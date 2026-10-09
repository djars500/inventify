import logging
import time

from celery import shared_task

from apps.order.actions import ImportOrderAction
from apps.order.enums import OrderStatusChoices
from apps.order.models import ImportOrderData, Order
from base.requests import RecarRequest

logger = logging.getLogger('django')


@shared_task
def create_orders_draft():
    orders_recar = RecarRequest().get_orders()
    order_ids = list(map(lambda x: int(x['id']), orders_recar))
    orders = ImportOrderData.objects.filter(id__in=order_ids)
    difference_orders = set(order_ids).difference(orders.values_list('id', flat=True))
    for order_id in difference_orders:
        import_order_draft.delay(order_id)


@shared_task
def import_order_draft(order_id: int):
    request = RecarRequest()
    order_data = request.get_order(order_id)
    ImportOrderData.objects.create(id=order_id, data=order_data)


@shared_task
def import_order_task(id: int):
    order = ImportOrderData.objects.get(id=id)
    ImportOrderAction().run(order.data)


@shared_task
def create_orders():
    order_ids_recar = ImportOrderData.objects.values_list('id', flat=True)
    orders = Order.objects.filter(id__in=order_ids_recar).values_list('id', flat=True)
    difference_order_ids = set(order_ids_recar).difference(orders)
    batch_size = 100

    for start in range(0, len(difference_order_ids), batch_size):
        end = start + batch_size
        batch_ids = list(difference_order_ids)[start:end]
        remains_recar_orders = ImportOrderData.objects.filter(id__in=batch_ids)

        for order in remains_recar_orders:
            import_order_task.delay(order.id)


@shared_task
def import_orders_from_recar():
    create_orders_draft()
    time.sleep(300)
    create_orders()


@shared_task
def sync_order_from_recar(order_id: int):
    """Перетягивает один заказ из Recar: снапшот и сам заказ с позициями."""
    order_data = RecarRequest().get_order(order_id)
    if not order_data:
        logger.warning('Recar не вернул заказ %s — синхронизация пропущена', order_id)
        return {'order_id': order_id, 'synced': False}

    ImportOrderData.objects.update_or_create(id=order_id, defaults={'data': order_data})
    ImportOrderAction().run(order_data)
    return {'order_id': order_id, 'synced': True}


@shared_task
def sync_unfinished_orders():
    """Обновляет из Recar заказы, которые ещё в работе.

    Нужна, чтобы заказ завершался по актуальным данным: статус оплаты и состав
    могли измениться на стороне Recar, а у нас оставались прежними.

    Синхронизируем только заказы, пришедшие из Recar (у них есть снапшот в
    ImportOrderData). Созданные у нас вручную не трогаем: их id выдаёт наша
    последовательность и может совпасть с чужим заказом в Recar.
    """
    recar_order_ids = ImportOrderData.objects.values_list('id', flat=True)
    order_ids = list(
        Order.objects
        .filter(status=OrderStatusChoices.PROCESSING, id__in=recar_order_ids)
        .values_list('id', flat=True)
    )

    for order_id in order_ids:
        sync_order_from_recar.delay(order_id)

    return {'orders': len(order_ids)}
