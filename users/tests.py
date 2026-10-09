from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from handbook.models import City, Country
from users.enums import RoleEnum
from users.models.User import User, Role
from users.otp.actions import CreateUserCodeAction
from users.otp.models import UserCode
from users.serializers import PHONE_TAKEN_MESSAGE
from users.services.reset_password import (
    DEFAULT_PASSWORD,
    ResetPasswordService,
    SmsPasswordResetService,
)
from users.services.roles import has_role, is_management


class ResetPasswordServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+77770000001", password="oldpass123", email="u1@example.com"
        )

    def test_reset_to_default_sets_fixed_password_and_sends_no_email(self):
        ResetPasswordService.reset_to_default(self.user)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(DEFAULT_PASSWORD))
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_random_and_email_changes_password_and_sends_email(self):
        ResetPasswordService.reset_random_and_email(self.user)
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password("oldpass123"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.user.email, mail.outbox[0].to)


class SmsPasswordResetServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone="+77770000010", password="oldpass123", email=None
        )

    @mock.patch("users.services.reset_password.SmsService.send_sms")
    def test_send_code_creates_usercode_and_sends_sms(self, send_sms):
        SmsPasswordResetService.send_code(self.user)

        codes = UserCode.objects.filter(user=self.user)
        self.assertEqual(codes.count(), 1)
        send_sms.assert_called_once_with(phone=self.user.phone, sms=codes.first().otp)

    def test_confirm_sets_new_password(self):
        SmsPasswordResetService.confirm(self.user, "BrandNew987")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("BrandNew987"))
        self.assertFalse(self.user.check_password("oldpass123"))


class AnonymousRegistrationTest(TestCase):
    """Регистрация клиента с сайта.

    Раньше любой запрос с незанятым телефоном падал 500: UserRegisterSerializer
    обращался к `user.roles`, которого у AnonymousUser нет.
    """

    def setUp(self):
        self.client = APIClient()
        country = Country.objects.create(name='Казахстан')
        self.city = City.objects.create(name='Уральск', country=country)
        self.payload = {
            'phone': '+77013550525',
            'password': 'Bmw123455a',
            'password2': 'Bmw123455a',
            'first_name': 'Артем',
            'last_name': 'Сабитов',
            'city': self.city.id,
        }

    def test_anonymous_can_register(self):
        response = self.client.post('/api/users/', self.payload, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(User.objects.filter(phone=self.payload['phone']).exists())

    def test_registered_user_is_not_staff(self):
        self.client.post('/api/users/', self.payload, format='json')

        user = User.objects.get(phone=self.payload['phone'])
        self.assertFalse(user.is_staff)
        self.assertFalse(user.roles.exists())

    def test_duplicate_phone_returns_readable_message(self):
        """Фронт показывает текст из `message`, поэтому формат ответа закрепляем."""
        User.objects.create_user(phone=self.payload['phone'], password='whatever123')

        response = self.client.post('/api/users/', self.payload, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('phone', response.data['message'])
        self.assertTrue(response.data['message']['phone'])

    def test_anonymous_cannot_register_staff_with_roles(self):
        role = Role.objects.create(name=RoleEnum.SALEPERSON.value)

        response = self.client.post(
            '/api/users/', {**self.payload, 'roles': [role.id], 'is_staff': True}, format='json'
        )

        self.assertEqual(response.status_code, 403, response.data)


class PhoneChangeTest(TestCase):
    """Смена своего номера с подтверждением кодом из SMS.

    Номер — это логин, поэтому код уходит на новый номер: опечатка не отрезает
    вход, а чужой номер нельзя присвоить.
    """

    def setUp(self):
        self.client = APIClient()
        cache.clear()
        self.user = User.objects.create_user(phone='+77770000301', password='pass123456')
        self.other = User.objects.create_user(phone='+77770000302', password='pass123456')
        self.client.force_authenticate(user=self.user)
        self.new_phone = '+77770000399'

    @mock.patch('users.services.change_phone.SmsService.send_sms')
    def _request_code(self, send_sms, phone=None):
        response = self.client.post(
            '/api/users/phone-change/request/', {'phone': phone or self.new_phone}, format='json'
        )
        return response, send_sms

    def test_code_goes_to_the_new_number(self):
        response, send_sms = self._request_code()

        self.assertEqual(response.status_code, 200, response.data)
        send_sms.assert_called_once()
        self.assertEqual(send_sms.call_args.kwargs['phone'], self.new_phone)
        # Пока код не подтверждён, номер не меняется
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '+77770000301')

    def test_confirm_saves_new_number(self):
        self._request_code()
        otp = UserCode.objects.filter(user=self.user).latest('created_at').otp

        response = self.client.post(
            '/api/users/phone-change/confirm/',
            {'phone': self.new_phone, 'otp': str(otp)},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, self.new_phone)

    def test_wrong_code_keeps_old_number(self):
        self._request_code()

        response = self.client.post(
            '/api/users/phone-change/confirm/',
            {'phone': self.new_phone, 'otp': '0000'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '+77770000301')

    def test_confirm_without_request_is_rejected(self):
        otp = CreateUserCodeAction.run(self.user).otp

        response = self.client.post(
            '/api/users/phone-change/confirm/',
            {'phone': self.new_phone, 'otp': str(otp)},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('phone', response.data['message'])

    def test_taken_number_is_rejected_before_sms(self):
        response, send_sms = self._request_code(phone=self.other.phone)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['message']['phone'], [PHONE_TAKEN_MESSAGE])
        send_sms.assert_not_called()

    def test_bad_format_is_rejected_before_sms(self):
        response, send_sms = self._request_code(phone='8777')

        self.assertEqual(response.status_code, 400)
        send_sms.assert_not_called()

    def test_client_cannot_change_phone_by_plain_patch(self):
        """Иначе подтверждение по SMS обходится обычным PATCH."""
        response = self.client.patch(
            f'/api/users/{self.user.id}/', {'phone': self.new_phone}, format='json'
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '+77770000301')

    def test_client_can_still_change_email_by_patch(self):
        response = self.client.patch(
            f'/api/users/{self.user.id}/', {'email': 'me@example.com'}, format='json'
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'me@example.com')

    def test_anonymous_cannot_request_code(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(
            '/api/users/phone-change/request/', {'phone': self.new_phone}, format='json'
        )

        self.assertIn(response.status_code, (401, 403))


class PhoneNormalizationTest(TestCase):
    """Номер всегда сохраняется как +77001234567.

    Вход сравнивает номер строкой, а формы входа отправляют номер без
    разделителей. Номер, сохранённый как «+7 708 953 17 92», делал вход
    невозможным: формат проходил проверку, но не совпадал при логине.
    """

    def setUp(self):
        self.client = APIClient()
        self.director_role = Role.objects.create(name=RoleEnum.DIRECTOR.value)
        self.director = User.objects.create_user(
            phone='+77770000501', password='pass123456', is_staff=True
        )
        self.director.roles.add(self.director_role)
        self.client.force_authenticate(user=self.director)

    def _patch_phone(self, value):
        return self.client.patch(
            f'/api/users/{self.director.id}/', {'phone': value}, format='json'
        )

    def test_spaces_and_dashes_are_stripped(self):
        for value in ('+7 777 000 05 02', '+7-777-000-05-02'):
            with self.subTest(value=value):
                response = self._patch_phone(value)

                self.assertEqual(response.status_code, 200, response.data)
                self.director.refresh_from_db()
                self.assertEqual(self.director.phone, '+77770000502')

    def test_leading_eight_becomes_plus_seven(self):
        response = self._patch_phone('87770000503')

        self.assertEqual(response.status_code, 200, response.data)
        self.director.refresh_from_db()
        self.assertEqual(self.director.phone, '+77770000503')

    def test_number_without_plus_is_accepted(self):
        response = self._patch_phone('77770000504')

        self.assertEqual(response.status_code, 200, response.data)
        self.director.refresh_from_db()
        self.assertEqual(self.director.phone, '+77770000504')

    def test_garbage_is_still_rejected(self):
        response = self._patch_phone('8777')

        self.assertEqual(response.status_code, 400)
        self.director.refresh_from_db()
        self.assertEqual(self.director.phone, '+77770000501')

    def test_normalized_number_collides_with_existing(self):
        """После приведения к одному виду занятость тоже ловится."""
        User.objects.create_user(phone='+77770000505', password='pass123456')

        response = self._patch_phone('8 777 000 05 05')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['message']['phone'], [PHONE_TAKEN_MESSAGE])

    def test_saved_number_allows_login(self):
        """Главное: по сохранённому номеру можно войти."""
        self._patch_phone('+7 777 000 05 06')
        self.client.force_authenticate(user=None)

        response = self.client.post(
            '/api/users/token/',
            {'phone': '+77770000506', 'password': 'pass123456'},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('access', response.data)


class StaffRightsRequireIsStaffTest(TestCase):
    """Права сотрудника требуют и роль, и признак is_staff.

    Роли и is_staff в базе независимы: раньше роль «Гость» (входит в список
    разрешённых у IsStaff) открывала служебные эндпоинты, а руководитель без
    is_staff правил чужие контакты.
    """

    def setUp(self):
        self.director_role = Role.objects.create(name=RoleEnum.DIRECTOR.value)
        self.guest_role = Role.objects.create(name=RoleEnum.GUEST.value)

    def _user(self, phone, role, is_staff):
        user = User.objects.create_user(phone=phone, password='pass123456', is_staff=is_staff)
        user.roles.add(role)
        return user

    def test_role_without_is_staff_is_not_management(self):
        user = self._user('+77770000401', self.director_role, is_staff=False)

        self.assertFalse(is_management(user))
        self.assertFalse(has_role(user, RoleEnum.DIRECTOR.value))

    def test_role_with_is_staff_is_management(self):
        user = self._user('+77770000402', self.director_role, is_staff=True)

        self.assertTrue(is_management(user))

    def test_guest_role_is_not_staff_member(self):
        user = self._user('+77770000403', self.guest_role, is_staff=False)

        self.assertFalse(has_role(user, *[role.value for role in RoleEnum]))

    def test_superuser_passes_without_role(self):
        user = User.objects.create_user(phone='+77770000404', password='pass123456')
        user.is_superuser = True
        user.save(update_fields=['is_superuser'])

        self.assertTrue(is_management(user))

    def test_client_without_roles_has_no_rights(self):
        user = User.objects.create_user(phone='+77770000405', password='pass123456')

        self.assertFalse(is_management(user))


class StaffContactsPermissionTest(TestCase):
    """Телефон и почту чужой учётной записи правит только руководство."""

    def setUp(self):
        self.client = APIClient()
        self.director_role = Role.objects.create(name=RoleEnum.DIRECTOR.value)
        self.seller_role = Role.objects.create(name=RoleEnum.SALEPERSON.value)

        self.seller = self._staff('+77770000101', self.seller_role)
        self.colleague = self._staff('+77770000102', self.seller_role)
        self.director = self._staff('+77770000103', self.director_role)
        self.client_user = User.objects.create_user(phone='+77770000104', password='pass123456')

    @staticmethod
    def _staff(phone, role):
        user = User.objects.create_user(phone=phone, password='pass123456', is_staff=True)
        user.roles.add(role)
        return user

    def _patch(self, actor, target, **data):
        self.client.force_authenticate(user=actor)
        return self.client.patch(f'/api/admin/users/{target.id}/', data, format='json')

    def test_employee_can_change_own_contacts(self):
        response = self._patch(self.seller, self.seller, phone='+77770000111', email='me@example.com')

        self.assertEqual(response.status_code, 200, response.data)
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.email, 'me@example.com')
        self.assertEqual(self.seller.phone, '+77770000111')

    def test_employee_can_save_own_profile_fields(self):
        """Страница профиля в админке отправляет только свои поля."""
        response = self._patch(
            self.seller, self.seller,
            first_name='Иван', last_name='Петров', middle_name='Сергеевич',
            phone='+77770000121', email='ivan@example.com',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.first_name, 'Иван')
        self.assertEqual(self.seller.phone, '+77770000121')

    def test_employee_cannot_send_roles_with_own_profile(self):
        """Роли и is_staff меняет только директор — профиль их не отправляет."""
        response = self._patch(self.seller, self.seller, first_name='Иван', is_staff=True)

        self.assertEqual(response.status_code, 403, response.data)

    def test_empty_city_is_allowed(self):
        """Город необязателен в модели, и в форме его можно очистить."""
        response = self._patch(self.seller, self.seller, city=None)

        self.assertEqual(response.status_code, 200, response.data)

    def test_employee_cannot_change_client_contacts(self):
        response = self._patch(self.seller, self.client_user, phone='+77770000112')

        self.assertEqual(response.status_code, 403, response.data)
        self.client_user.refresh_from_db()
        self.assertEqual(self.client_user.phone, '+77770000104')

    def test_employee_can_change_client_non_contact_fields(self):
        """Ограничение только на телефон и почту, остальное не ломаем."""
        response = self._patch(self.seller, self.client_user, first_name='Пётр')

        self.assertEqual(response.status_code, 200, response.data)
        self.client_user.refresh_from_db()
        self.assertEqual(self.client_user.first_name, 'Пётр')

    def test_employee_does_not_see_staff_colleagues(self):
        """Чужие учётные записи сотрудников обычному сотруднику не видны вовсе."""
        response = self._patch(self.seller, self.colleague, first_name='Пётр')

        self.assertEqual(response.status_code, 404, response.data)

    def test_director_can_change_staff_contacts(self):
        response = self._patch(
            self.director, self.colleague, phone='+77770000113', email='new@example.com'
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.colleague.refresh_from_db()
        self.assertEqual(self.colleague.email, 'new@example.com')

    def test_department_director_can_change_staff_contacts(self):
        head_role = Role.objects.create(name=RoleEnum.DEPARTMENT_DIRECTOR.value)
        head = self._staff('+77770000105', head_role)

        response = self._patch(head, self.colleague, email='head@example.com')

        self.assertEqual(response.status_code, 200, response.data)
        self.colleague.refresh_from_db()
        self.assertEqual(self.colleague.email, 'head@example.com')
