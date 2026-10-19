import logging

import requests
from django.conf import settings

from users.otp.enums import SmsStatus

logger = logging.getLogger('django')

SMSC_URL = "https://smsc.kz/sys/send.php"
MESSAGE_TEMPLATE = "Kaynaravto Ваш код: {code}"

# Каналы доставки кода
CHANNEL_SMS = 'sms'
CHANNEL_WHATSAPP = 'whatsapp'

# Флаг резервной отправки в smsc: если сообщение в мессенджер не доставлено,
# уходит SMS с текстом из mes2
SMSC_BACKUP_FLAG = 'fl[5]'

REQUEST_TIMEOUT = 30


class SmsRequest:
    """Отправка кода подтверждения через smsc.kz — SMS либо WhatsApp.

    Канал задаётся настройкой OTP_CHANNEL, чтобы переключать его переменной
    окружения, без выкатки кода. У WhatsApp есть каскад на SMS
    (OTP_WHATSAPP_FALLBACK_SMS): мессенджер есть не у всех, и без каскада
    часть людей просто не получила бы код и не смогла войти.
    """

    @staticmethod
    def build_params(phone: str, msg) -> dict:
        """Собирает параметры запроса к smsc под выбранный канал."""
        message = MESSAGE_TEMPLATE.format(code=msg)
        params = {
            "login": settings.OTP_SMSC_LOGIN,
            "psw": settings.OTP_SMSC_PASSWORD,
            # smsc ждёт номер без плюса
            "phones": phone.lstrip('+'),
            "mes": message,
        }

        if settings.OTP_CHANNEL != CHANNEL_WHATSAPP:
            return params

        if not settings.OTP_WHATSAPP_BOT:
            # Канал включили, а номер бота не задали. Отправляем SMS: иначе
            # вместо кода человек получит ошибку отправки и не сможет войти.
            logger.error('OTP_CHANNEL=whatsapp, но OTP_WHATSAPP_BOT пуст — отправляем SMS')
            return params

        params['bot'] = f'wa:{settings.OTP_WHATSAPP_BOT.lstrip("+")}'

        if settings.OTP_WHATSAPP_FALLBACK_SMS:
            params['mes2'] = message
            params[SMSC_BACKUP_FLAG] = 1

        return params

    @classmethod
    def send_sms(cls, phone: str, msg):
        params = cls.build_params(phone, msg)
        response = requests.post(url=SMSC_URL, params=params, timeout=REQUEST_TIMEOUT)

        answer = response.content.decode('utf-8')
        status = answer.split(' ')[0]

        if status != 'OK':
            # Без этого отказ smsc выглядел как «код просто не пришёл»
            logger.error(
                'smsc отклонил отправку кода (канал %s): %s',
                settings.OTP_CHANNEL, answer[:200],
            )

        return {
            "status": SmsStatus.SUCCESS if status == 'OK' else SmsStatus.NOT_CREATED,
        }
