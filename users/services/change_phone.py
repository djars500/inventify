"""Смена номера телефона с подтверждением по SMS.

Телефон — это логин, поэтому меняем его только после подтверждения кодом,
отправленным на НОВЫЙ номер: так опечатка не отрежет вход, а чужой номер
нельзя присвоить себе.

Переиспользует OTP-инфраструктуру (users/otp): код живёт 5 минут, проверку
делает GetStatusUserCodeAction.
"""

from django.core.cache import cache
from rest_framework.exceptions import ValidationError

from users.otp.actions import CreateUserCodeAction, GetStatusUserCodeAction, SmsService
from users.otp.enums import SmsStatus

# Столько же, сколько живёт код в GetStatusUserCodeAction
PENDING_TTL_SECONDS = 300
PENDING_CACHE_PREFIX = 'phone_change'

CODE_NOT_REQUESTED_MESSAGE = 'Сначала запросите код на новый номер'
CODE_EXPIRED_MESSAGE = 'Код устарел, запросите новый'
CODE_INVALID_MESSAGE = 'Неверный код'


class PhoneChangeService:

    @staticmethod
    def cache_key(user) -> str:
        return f'{PENDING_CACHE_PREFIX}:{user.pk}'

    @classmethod
    def send_code(cls, user, new_phone: str) -> None:
        """Отправляет код на новый номер и запоминает, какой номер подтверждают."""
        otp_object = CreateUserCodeAction.run(user)
        SmsService.send_sms(phone=new_phone, sms=otp_object.otp)
        cache.set(cls.cache_key(user), new_phone, timeout=PENDING_TTL_SECONDS)

    @classmethod
    def confirm(cls, user, new_phone: str, otp: str) -> None:
        """Проверяет код и сохраняет новый номер."""
        pending_phone = cache.get(cls.cache_key(user))
        if not pending_phone or pending_phone != new_phone:
            raise ValidationError({'phone': [CODE_NOT_REQUESTED_MESSAGE]})

        code_status = GetStatusUserCodeAction.run(user=user, otp=otp)
        if code_status == SmsStatus.TIMEOUT:
            raise ValidationError({'otp': [CODE_EXPIRED_MESSAGE]})
        if code_status != SmsStatus.SUCCESS:
            raise ValidationError({'otp': [CODE_INVALID_MESSAGE]})

        user.phone = new_phone
        user.save(update_fields=['phone'])
        cache.delete(cls.cache_key(user))
