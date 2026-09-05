import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'inventify.settings.dev')
app = Celery('inventify')
# Set the default Django settings module for the 'celery' program.

app.config_from_object('django.conf:settings', namespace='CELERY')
app.conf.update(result_extended=True)
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'Обновление продуктов и их модификаций': {
        'task': 'apps.car.tasks.import_car_data_recar',
        'schedule': crontab(hour=00, minute=00)
    },

    'Обновление складов': {
        'task': 'apps.stock.tasks.import_warehouses_from_recar',
        'schedule': crontab(hour=1, minute=00)
    },

    'Импорт моделей машины': {
        'task': 'apps.car.tasks.create_car_models',
        'schedule': crontab(day_of_week=0, hour=0, minute=0),
    },

    'Импорт модификаций': {
        'task': 'apps.car.tasks.import_modification_recar',
        'schedule': crontab(day_of_week=0, hour=1, minute=0),
    },

    'Импорт двигателей': {
        'task': 'apps.car.tasks.create_engines',
        'schedule': crontab(day_of_week=0, hour=2, minute=0),
    },

    'Обновление статуса продуктов': {
        'task': 'apps.product.tasks.update_status_products',
        'schedule': crontab(hour=00, minute=30)
    },

    # Обновление цен отключено: задача не нужна, а её реализация
    # (apps.product.tasks.update_price) не зарегистрирована как celery-задача,
    # поэтому beat каждую ночь ронял её с NotRegistered.

    'Импорт заказов': {
        'task': 'apps.order.tasks.import_orders_from_recar',
        'schedule': crontab(hour=2, minute=00)
    },

    # Товары, изменённые в Recar за последнюю неделю: обход выборки по сортировке
    # updated_at, потому что фильтра по дате изменения в GetPartsInput нет.
    # Ловит и новые, и отредактированные.
    'Обновление изменённых товаров': {
        'task': 'apps.product.tasks.sync_recent_products',
        'schedule': crontab(hour=5, minute=0),
    },

    'Очистка логов запросов в Recar': {
        'task': 'base.tasks.clean_recar_request_logs',
        'schedule': crontab(hour=3, minute=15),
    },

    'Очистка логов drf-api-logger': {
        'task': 'base.tasks.clean_drf_api_logs',
        'schedule': crontab(hour=3, minute=0),
    },

    # Задачи 'celery.backend_cleanup' здесь намеренно нет: celery заводит её сам
    # (Scheduler.install_default_entries) на 4:00, раз задан CELERY_RESULT_EXPIRES.
    # Своя копия дублировала встроенную, и чистка результатов шла дважды.

    'Очистка истёкших сессий': {
        'task': 'base.tasks.clear_expired_sessions',
        'schedule': crontab(day_of_week=0, hour=3, minute=30),
    },
}

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.

# Load task modules from all registered Django apps.
# app.conf.timezone = settings.TIME_ZONE
