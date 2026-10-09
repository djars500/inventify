import re

from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.validators import UniqueValidator

from base.enums import StatusEnum
from handbook.models import City
from handbook.serializers import CitySerializer
from users.fields import PhoneField
from users.models.User import User, Role
from users.services.roles import is_director, is_management, is_real_user, is_staff_member

# Сообщения пользователю, а не разработчику: по умолчанию DRF отдаёт
# «user с таким phone уже существует», и этот текст уходил прямо в форму
PHONE_TAKEN_MESSAGE = 'Этот номер телефона уже зарегистрирован'
PHONE_REQUIRED_MESSAGE = 'Укажите номер телефона'
PASSWORDS_DO_NOT_MATCH_MESSAGE = 'Пароли не совпадают'

# Валидаторы перечислены явно: передача `validators` в extra_kwargs заменяет
# набор, собранный DRF по модели, поэтому проверку формата нужно вернуть руками
PHONE_FIELD_KWARGS = {
    'validators': [
        PhoneField.phone_regex,
        UniqueValidator(queryset=User.objects.all(), message=PHONE_TAKEN_MESSAGE),
    ],
    'error_messages': {
        'blank': PHONE_REQUIRED_MESSAGE,
        'required': PHONE_REQUIRED_MESSAGE,
    },
}


class NormalizedPhoneField(serializers.CharField):
    """Номер телефона, приведённый к виду +77001234567.

    Телефон — это логин, и при входе он сравнивается строкой. Регулярка
    допускает пробелы и дефисы, поэтому номер, сохранённый из админки как
    «+7 708 953 17 92», потом не совпадал с тем, что отправляет форма входа
    («+77089531792»), и человек не мог войти вообще. Приводим к одному виду
    на входе: убираем разделители и заменяем ведущую 8 на +7.
    """

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        digits = re.sub(r'[^\d+]', '', value)

        if digits.startswith('8') and len(digits) == 11:
            digits = f'+7{digits[1:]}'
        elif digits.startswith('7') and len(digits) == 11:
            digits = f'+{digits}'

        return digits


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = '__all__'


class UserSerializer(serializers.ModelSerializer):
    city = CitySerializer(read_only=True)
    roles = RoleSerializer(many=True, read_only=True)
    phone = NormalizedPhoneField(**PHONE_FIELD_KWARGS)

    class Meta:
        model = User
        extra_kwargs = {
            "password": {"write_only": True, 'required': False},
        }
        fields = ('__all__')
        # exclude = ('user_permissions', )


class UserUpdateSerializer(UserSerializer):
    roles = serializers.ListSerializer(required=False,
                                       child=serializers.PrimaryKeyRelatedField(queryset=Role.objects.all())
                                       )
    # allow_null: в модели город необязателен, а в формах его можно очистить —
    # без этого сохранение падало с «Это поле не может быть пустым»
    city = serializers.PrimaryKeyRelatedField(queryset=City.objects.all(), required=False, allow_null=True)
    status = serializers.ChoiceField(choices=StatusEnum.choices)
    # Свой номер менять можно, он же логин — проверяем формат и занятость
    phone = NormalizedPhoneField(required=False, **PHONE_FIELD_KWARGS)

    class Meta(UserSerializer.Meta):
        extra_kwargs = {
            "first_name": {'required': False},
            "last_name": {'required': False},
            "password": {'required': False},
        }

    def update(self, instance, validated_data):
        # Обновляем роли
        roles_data = validated_data.pop('roles', None)
        if roles_data is not None:
            instance.roles.set(roles_data)  # Устанавливаем новые роли

        # Обновляем остальные поля
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # Сохраняем изменения
        instance.save()
        return instance

    # Контакты чужой учётной записи правит только руководство
    PROTECTED_CONTACT_FIELDS = ('phone', 'email')

    def validate(self, attrs):
        request = self.context['request']
        user = request.user

        # Менять роли и признак сотрудника (is_staff) может только директор/суперпользователь
        if ('roles' in attrs or 'is_staff' in attrs) and not is_director(user):
            raise PermissionDenied("Менять роли и признак сотрудника может только директор.")

        # is_superuser=True — только суперпользователь
        if attrs.get('is_superuser') and not (is_real_user(user) and user.is_superuser):
            raise PermissionDenied("Только суперпользователь может назначить is_superuser=True.")

        # Телефон и почту меняет либо сам владелец, либо директор/руководитель отдела
        changed_contacts = [field for field in self.PROTECTED_CONTACT_FIELDS if field in attrs]
        is_self = (
            is_real_user(user)
            and self.instance is not None
            and self.instance.pk == user.pk
        )
        if changed_contacts and not is_self and not is_management(user):
            raise PermissionDenied(
                "Менять телефон и почту другого сотрудника может только директор "
                "или руководитель отдела."
            )

        # Клиент меняет свой номер только с подтверждением кодом из SMS, иначе
        # обычным PATCH можно было бы обойти проверку: номер — это логин.
        # Сотрудникам прямое изменение оставляем: они работают в админке, и
        # ошибку поправит руководство.
        if (
            'phone' in attrs
            and not is_management(user)
            and not is_staff_member(user)
        ):
            raise PermissionDenied(
                "Смена номера подтверждается кодом из SMS: запросите код "
                "на новый номер и подтвердите его."
            )

        return super().validate(attrs)


class UserRegisterSerializer(UserSerializer):
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True)
    roles = serializers.ListSerializer(required=False,
                                       child=serializers.PrimaryKeyRelatedField(queryset=Role.objects.all()),
                                       allow_empty=True)
    city = serializers.PrimaryKeyRelatedField(queryset=City.objects.all(), required=True)

    def validate(self, attrs):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        # Регистрируется аноним — у AnonymousUser нет `roles`, поэтому роль
        # проверяем через хелпер, иначе запрос падал с 500 и зарегистрироваться
        # не мог никто с незанятым телефоном
        if (attrs.get('roles') or attrs.get('is_staff')) and not is_director(user):
            raise PermissionDenied("Создавать сотрудников и назначать роли может только директор.")
        if attrs.get('is_superuser') and not (is_real_user(user) and user.is_superuser):
            raise PermissionDenied("Только суперпользователь может назначить is_superuser=True.")

        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": PASSWORDS_DO_NOT_MATCH_MESSAGE})
        attrs['password'] = make_password(attrs['password'])
        attrs.pop('password2')
        return attrs


class PhoneChangeRequestSerializer(serializers.Serializer):
    """Запрос кода на новый номер. Номер проверяем до отправки SMS."""

    phone = NormalizedPhoneField(
        validators=[PhoneField.phone_regex],
        error_messages={
            'blank': PHONE_REQUIRED_MESSAGE,
            'required': PHONE_REQUIRED_MESSAGE,
        },
    )

    def validate_phone(self, value):
        user = self.context['request'].user
        if value == user.phone:
            raise serializers.ValidationError('Это ваш текущий номер телефона')
        if User.objects.filter(phone=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError(PHONE_TAKEN_MESSAGE)
        return value


class PhoneChangeConfirmSerializer(PhoneChangeRequestSerializer):
    otp = serializers.CharField(error_messages={
        'blank': 'Введите код из SMS',
        'required': 'Введите код из SMS',
    })


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)


class ResetPasswordRequestSerializer(serializers.Serializer):
    phone = serializers.CharField(required=True)


class PasswordResetRequestSerializer(serializers.Serializer):
    phone = serializers.CharField(required=True)


class PasswordResetConfirmSerializer(serializers.Serializer):
    phone = serializers.CharField(required=True)
    otp = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)