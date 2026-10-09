import logging
from typing import List

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.db.utils import DataError
from django.utils.translation import gettext as _

from apps.order.enums import *
from apps.order.models import Order, OrderItem
from apps.product.enums import StatusChoices
from apps.product.models.Product import Product
from apps.stock.actions import StockAction
from apps.stock.models import Stock, Warehouse


logger = logging.getLogger(__name__)


def _map_recar_value(mapping, raw_value, default, field_name, order_id):
    """Переводит значение Recar в наше перечисление.

    Recar заводит новые статусы и типы оплаты, не предупреждая. Неизвестное
    значение не должно ронять импорт всего заказа: подставляем значение по
    умолчанию и пишем предупреждение, чтобы расхождение было видно в логах.
    """
    try:
        return mapping[raw_value]
    except KeyError:
        logger.warning(
            'Recar прислал неизвестный %s=%r у заказа %s — подставлено %s',
            field_name, raw_value, order_id, default.label,
        )
        return default


class ImportOrderAction:
    def run(self, order_data: dict):
        order_id = order_data['id']

        order = Order(
            id=order_id,
            payment_type=_map_recar_value(
                RECAR_PAYMENT_TYPE_MAP, order_data['paymentType'],
                PaymentTypeChoices.CASH, 'paymentType', order_id,
            ),
            payment_status=PaymentStatusChoices.PAID if order_data['paymentCompleted'] else PaymentStatusChoices.FAILED,
            status=
            _map_recar_value(
                RECAR_ORDER_STATUS_MAP, order_data['status'],
                OrderStatusChoices.PROCESSING, 'status', order_id,
            )
            if order_data['returning'] is False
            else OrderStatusChoices.REFUNDED,
            total=order_data['totalPrice'],
            comment=order_data['comment'],
            warehouse_id=order_data['location']['id'],
            refund_order_id=None if order_data.get('parentOrder', None) is None else order_data['parentOrder']['id'],
            created_at=order_data['createdAt'],
            updated_at=order_data['updatedAt']
        )
        try:
            order.save()
        except DataError as e:
            order.total = int(order_data['totalPrice'] / 100)
            order.save()

        self.sync_items(order, order_data)

    @staticmethod
    def sync_items(order: Order, order_data: dict):
        """Приводит позиции заказа к составу из Recar.

        Повторный импорт того же заказа (ночная синхронизация незавершённых)
        раньше плодил дубли позиций, потому что items всегда создавались
        заново. Теперь позиции обновляются, а исчезнувшие из Recar удаляются.
        """
        snapshot = [
            item for item in (order_data.get('partsSnapshot') or [])
            if item.get('nearestParentId') is None
        ]
        product_ids = [int(item['id']) for item in snapshot]

        # Товара может не быть в нашей базе (ещё не импортирован) — такую
        # позицию пропускаем, иначе весь заказ не импортируется по FK
        known_ids = set(
            Product.objects.filter(id__in=product_ids).values_list('id', flat=True)
        )
        missing = [product_id for product_id in product_ids if product_id not in known_ids]
        if missing:
            logger.warning('Заказ %s: товары %s не найдены — позиции пропущены', order.id, missing)

        for item in snapshot:
            product_id = int(item['id'])
            if product_id not in known_ids:
                continue
            OrderItem.objects.update_or_create(
                order=order,
                product_id=product_id,
                defaults={'is_returning': item.get('returning') is True},
            )

        order.goods.exclude(product_id__in=known_ids).delete()


class OrderAction:

    def __init__(self, data=None):
        if data is None:
            data = {}
        self.data = data
        self.goods = self.data.pop('goods', [])

    def create(self):
        self.set_total()
        order = Order.objects.create(**self.data)
        self.__create_order_items(order)
        products = [item['product'] for item in self.goods]
        self._update_status_products(StatusChoices.RESERVED, products)
        return order

    def outgoing_order(self, products: List[Product]) -> List[dict]:
        """Списывает товары заказа со склада, не падая на уже списанных.

        Товар могли продать мимо нас — например в Recar, и тогда остатка на
        складе нет. Раньше такой заказ нельзя было завершить вообще: первый же
        товар ронял всю операцию ValidationError'ом из StockAction. Теперь
        движение по складу для таких товаров не создаётся (в минус остатки не
        уводим), а список пропущенных возвращается наверх, чтобы его было
        видно в ответе и в логах.
        """
        stock = StockAction()
        skipped = []
        for product in products:
            reason = self.write_off_blocker(product)
            if reason:
                logger.warning('Заказ: списание товара %s пропущено — %s', product.id, reason)
                skipped.append({'product_id': product.id, 'name': product.name, 'reason': reason})
                continue
            stock.process_outgoing(product, product.warehouse, 1)
        return skipped

    @staticmethod
    def write_off_blocker(product: Product):
        """Причина, по которой товар нельзя списать со склада, иначе None."""
        if product.warehouse_id is None:
            return 'товар не привязан к складу'
        if product.parent_id is not None:
            return 'товар входит в состав разобранной детали'

        quantity = Stock.objects.filter(
            product=product, warehouse_id=product.warehouse_id
        ).aggregate(total=Sum('quantity', default=0))['total']
        if quantity < 1:
            return 'на складе нет остатка'

        return None

    def ingoing_order(self, products: List[Product], warehouse: Warehouse):
        stock = StockAction()
        for product in products:
            stock.process_ingoing(product, warehouse, 1)

    def delete(self, order: Order):
        self.__update_order_status_failure(order)
        products = list(Product.objects.filter(order_item__order=order))
        self._update_status_products(StatusChoices.IN_STOCK, products)
        order.save()

    def refund(self):
        self.set_total()
        order = Order.objects.create(**self.data)
        self.__create_order_items(order)
        products = Product.objects.filter(order_item__order=order)
        self.__set_is_returning_products(order.refund_order)
        self._update_status_products(StatusChoices.IN_STOCK, list(products))
        self.__update_order_status_refunded(order)
        self.ingoing_order(products, order.warehouse)
        return order

    def confirm(self, order: Order):
        """Завершает заказ. Возвращает заказ и список товаров без списания."""
        self.__update_order_status_success(order)
        products = list(Product.objects.filter(order_item__order=order))
        skipped = self.outgoing_order(products)
        self._update_status_products(StatusChoices.SOLD, products)
        return order, skipped

    def set_total(self) -> None:
        self.data['total'] = sum(list(
            map(
                lambda x: getattr(x['product'].price.last(), 'cost', 0) * x['quantity'], self.goods
            )
        ))

    def _update_status_products(self, status: StatusChoices, items: List[Product]):
        products = []
        for item in items:
            item.status = status
            products.append(item)

        Product.objects.bulk_update(products, ['status'])

    def __create_order_items(self, order: Order):
        for item in self.goods:
            item['order_id'] = order.id
            OrderItem.objects.create(**item)

    @staticmethod
    def __update_order_status_success(order: Order):
        if order.payment_status == PaymentStatusChoices.PAID \
                and order.payment_type == PaymentTypeChoices.INTERNET_PAYMENT:
            order.status = OrderStatusChoices.COMPLETED
            order.save()

        elif order.payment_type == PaymentTypeChoices.CASH:
            order.status = OrderStatusChoices.COMPLETED
            order.payment_status = PaymentStatusChoices.PAID
            order.save()
        else:
            raise ValidationError(_('Заказ не оплачен, либо отклонен'))

    @staticmethod
    def __update_order_status_failure(order: Order):
        order.status = OrderStatusChoices.CANCELED
        order.payment_status = PaymentStatusChoices.FAILED
        order.save()

    @staticmethod
    def __update_order_status_refunded(order: Order):
        order.status = OrderStatusChoices.REFUNDED
        order.payment_status = PaymentStatusChoices.PAID
        order.save()

    def __set_is_returning_products(self, order: Order):
        products = [item['product'] for item in self.goods]
        order_items = OrderItem.objects.filter(product__in=products, order=order)
        order_items.update(is_returning=True)
