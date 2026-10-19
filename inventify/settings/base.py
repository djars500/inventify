import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv('.env.dev')

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY')

DEBUG = int(os.environ.get('DEBUG', 1))

ALLOWED_HOSTS = ['*']

DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'corsheaders',
    'rest_framework',
    'rest_framework_simplejwt',
    'gunicorn',
    'drf_yasg',
    'django_celery_beat',
    'django_celery_results',
    'django_json_widget',
    'eav',
    'djangoql',
    'drf_api_logger',
]

LOCAL_APPS = [
    'base',
    'handbook',
    'users',
    'history',
    'apps.category',
    'apps.stock',
    'apps.supplier',
    'apps.product',
    'apps.order',
    'apps.car',
    'apps.address',
    'apps.feedback'
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'base.middlewares.RequestMiddleware.RequestMiddleware',
    'drf_api_logger.middleware.api_logger_middleware.APILoggerMiddleware'
]

# django-silk (профилировщик запросов) — только при DEBUG, в проде не подключается
if DEBUG:
    INSTALLED_APPS += ['silk']
    MIDDLEWARE += ['silk.middleware.SilkyMiddleware']

# django-cleanup удаляет файлы из хранилища при удалении/замене объектов.
# Должен быть последним в INSTALLED_APPS.
INSTALLED_APPS += ['django_cleanup.apps.CleanupConfig']

ROOT_URLCONF = 'inventify.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates']
        ,
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'inventify.wsgi.application'

# Database
# https://docs.djangoproject.com/en/4.1/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": os.environ.get("SQL_ENGINE", "django.db.backends.postgresql"),
        "NAME": os.environ.get("SQL_DATABASE", "inventify"),
        "USER": os.environ.get("SQL_USER", "inventify"),
        "PASSWORD": os.environ.get("SQL_PASSWORD", "123"),
        "HOST": os.environ.get("SQL_HOST", "localhost"),
        "PORT": os.environ.get("SQL_PORT", "5432"),
        # Переиспользуем соединения, чтобы не открывать новое на каждый запрос
        "CONN_MAX_AGE": int(os.environ.get("SQL_CONN_MAX_AGE", 60)),
    },
}

# Быстрый таймаут подключения для postgres (не виснуть на недоступной БД).
# Только для postgres — sqlite/др. движки не знают connect_timeout.
if "postgresql" in DATABASES["default"]["ENGINE"]:
    DATABASES["default"]["OPTIONS"] = {
        "connect_timeout": int(os.environ.get("SQL_CONNECT_TIMEOUT", 5)),
    }

# Password validation
# https://docs.djangoproject.com/en/4.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/4.1/topics/i18n/

LANGUAGE_CODE = 'ru'

TIME_ZONE = 'Asia/Oral'

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.1/howto/static-files/

STATIC_URL = 'static/'

STATIC_ROOT = BASE_DIR / 'staticfiles'

# Default primary key field type
# https://docs.djangoproject.com/en/4.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'base.paginations.CustomPageNumberPagination',
    'PAGE_SIZE': 100,
    "DEFAULT_AUTHENTICATION_CLASSES": (
        # "rest_framework_simplejwt.authentication.JWTAuthentication",
        "base.middlewares.BaseAuthMiddleware.CustomJWTAuthentication",
    ),
    'DEFAULT_FILTER_BACKENDS': (
        'rest_framework.filters.OrderingFilter',
        'rest_framework.filters.SearchFilter'
    ),
    # 'DEFAULT_PERMISSION_CLASSES': [
    #     'rest_framework.permissions.AllowAny',
    # ],
    'DEFAULT_PERMISSION_CLASSES': [
        'inventify.permissions.InventifyAPIPermission',
    ],
    'EXCEPTION_HANDLER': 'base.exception_handler.custom_exception_handler'

}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
}

# --- Media: локальный диск или S3-совместимое хранилище PS.KZ ---
# Главный переключатель: при 1 все FileField/ImageField пишут и читают из S3-бакета
USE_S3_MEDIA = int(os.environ.get('USE_S3_MEDIA', 0))

AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME', 'kaynar')
AWS_S3_ENDPOINT_URL = os.environ.get('AWS_S3_ENDPOINT_URL', 'https://object.pscloud.io')
AWS_S3_REGION_NAME = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
AWS_S3_FILE_OVERWRITE = False
# Фото публичные: чистые URL без подписей
AWS_DEFAULT_ACL = 'public-read'
AWS_QUERYSTRING_AUTH = False
# path-style, чтобы URL имел вид https://object.pscloud.io/kaynar/<key>
AWS_S3_ADDRESSING_STYLE = 'path'

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

if USE_S3_MEDIA:
    from botocore.config import Config as BotoConfig

    # PS.KZ не поддерживает aws-chunked загрузку с CRC-чексуммами,
    # которую botocore >= 1.36 включает по умолчанию (MissingContentLength)
    AWS_S3_CLIENT_CONFIG = BotoConfig(
        s3={'addressing_style': AWS_S3_ADDRESSING_STYLE},
        request_checksum_calculation='when_required',
        response_checksum_validation='when_required',
    )
    STORAGES['default'] = {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'}
    MEDIA_URL = f'{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/'
else:
    MEDIA_URL = '/media/'

# MEDIA_ROOT нужен и при S3 — из него читает команда migrate_media_to_s3
MEDIA_ROOT = BASE_DIR / 'media'
PREPEND_MEDIA_URL = True
AUTH_USER_MODEL = "users.User"

CORS_ALLOW_ALL_ORIGINS = True
CORS_ORIGIN_ALLOW_ALL = True
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:8000", "http://213.171.4.132:8000", "https://back-kaynar.kz",
                        "https://kaynaravto.kz/"]

CELERY_TIMEZONE = "Asia/Oral"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_RESULT_BACKEND = 'django-db'
# Результаты задач старше 7 дней удаляются задачей celery.backend_cleanup
CELERY_RESULT_EXPIRES = 60 * 60 * 24 * 7
CELERY_CACHE_BACKEND = 'django-cache'
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL')
# CELERY_IMPORTS = (
#     'apps.product.celery.import_product_task',
# )

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.environ.get('CELERY_CACHE_URL', ),  # Убедитесь, что Redis запущен на этом хосте и порту
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}

EAV2_PRIMARY_KEY_FIELD = "django.db.models.BigAutoField"  # as example
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# Email
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = int(os.environ.get('EMAIL_USE_TLS', 1))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER)

LOG_DIR = os.path.join(BASE_DIR, 'logs')

# Если директория логов не существует, создаём её
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'ERROR',  # Записываем только ошибки
            'class': 'logging.FileHandler',
            'filename': os.path.join(BASE_DIR, 'logs', 'errors.log'),  # Путь к файлу логов
            'formatter': 'verbose',  # Используем verbose-форматтер
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file'],
            'level': 'ERROR',  # Логируем ошибки уровня ERROR и выше
            'propagate': True,
        },
    },
}

# Отправка кодов подтверждения через smsc.kz
# Имена sms_login/sms_psw оставлены как в окружении на проде
OTP_SMSC_LOGIN = os.environ.get('sms_login')
OTP_SMSC_PASSWORD = os.environ.get('sms_psw')

# Канал доставки кода: 'sms' или 'whatsapp'. WhatsApp дешевле примерно в десять
# раз (1,7 тг против 17-21 тг), но требует подключённого номера бота.
OTP_CHANNEL = os.environ.get('OTP_CHANNEL', 'sms')
# Номер WhatsApp-бота, который выдаёт smsc после регистрации в Meta
OTP_WHATSAPP_BOT = os.environ.get('OTP_WHATSAPP_BOT', '')
# Каскад: если сообщение в WhatsApp не доставлено, smsc сам отправит SMS.
# Без этого клиент без WhatsApp просто не получит код и не сможет войти.
OTP_WHATSAPP_FALLBACK_SMS = int(os.environ.get('OTP_WHATSAPP_FALLBACK_SMS', 1))

# Лог запросов в Recar (base.models.RecarRequestLog)
RECAR_REQUEST_LOG_ENABLED = int(os.environ.get('RECAR_REQUEST_LOG_ENABLED', 1))
# Ответы больше лимита пишутся усечёнными: FetchParts на 200 000 записей
# иначе положит и память процесса, и таблицу логов
RECAR_REQUEST_LOG_MAX_RESPONSE_BYTES = int(os.environ.get('RECAR_REQUEST_LOG_MAX_RESPONSE_BYTES', 100 * 1024))
RECAR_REQUEST_LOG_RETENTION_DAYS = int(os.environ.get('RECAR_REQUEST_LOG_RETENTION_DAYS', 14))

DRF_API_LOGGER_DATABASE = True
# Чувствительные ключи в теле запроса/ответа маскируются в логах
DRF_API_LOGGER_EXCLUDE_KEYS = ['password', 'token', 'access', 'refresh', 'authorization']