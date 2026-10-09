from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.category.models import Category
from apps.order.actions import ImportOrderAction, OrderAction
from apps.order.enums import OrderStatusChoices, PaymentStatusChoices, PaymentTypeChoices
from apps.order.models import Order, OrderItem
from apps.product.enums import StatusChoices
from apps.product.models import Product
from apps.product.models.Price import Price
from apps.stock.models import Stock, Warehouse
from users.enums import RoleEnum
from users.models.User import User, Role


def make_product(name, status=StatusChoices.IN_STOCK, warehouse=None, category=None, cost=1000):
    product = Product.objects.create(name=name, status=status, category=category, warehouse=warehouse)
    Price.objects.create(product=product, cost=cost)
    return product


class OrderConfirmTest(TestCase):
    """Завершение заказа.

    Товар могли продать мимо нас (например в Recar), остатка на складе нет —
    раньше такой заказ не завершался вообще: StockAction ронял операцию
    ошибкой «Недостаточно товара на складе».
    """

    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name='Двигатель')
        self.warehouse = Warehouse.objects.create(name='Склад 1')

        staff_role = Role.objects.create(name=RoleEnum.SALEPERSON.value)
        self.staff = User.objects.create_user(phone='+77770000201', password='pass123456', is_staff=True)
        self.staff.roles.add(staff_role)
        self.client.force_authenticate(user=self.staff)

    def _order(self, products, payment_status=PaymentStatusChoices.PAID):
        order = Order.objects.create(
            total=1000,
            warehouse=self.warehouse,
            payment_type=PaymentTypeChoices.CASH,
            payment_status=payment_status,
            status=OrderStatusChoices.PROCESSING,
        )
        for product in products:
            OrderItem.objects.create(order=order, product=product, quantity=1)
        return order

    def test_confirm_writes_off_product_in_stock(self):
        product = make_product('Фара', warehouse=self.warehouse, category=self.category)
        Stock.objects.create(product=product, warehouse=self.warehouse, quantity=1)
        order = self._order([product])

        response = self.client.post(f'/api/admin/orders/{order.id}/confirm/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['write_off_skipped'], [])
        order.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(order.status, OrderStatusChoices.COMPLETED)
        self.assertEqual(product.status, StatusChoices.SOLD)
        self.assertEqual(Stock.objects.get(product=product, warehouse=self.warehouse).quantity, 0)

    def test_confirm_completes_order_when_product_has_no_stock(self):
        product = make_product('Блок ECU', warehouse=self.warehouse, category=self.category)
        Stock.objects.create(product=product, warehouse=self.warehouse, quantity=0)
        order = self._order([product])

        response = self.client.post(f'/api/admin/orders/{order.id}/confirm/')

        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatusChoices.COMPLETED)

        skipped = response.data['write_off_skipped']
        self.assertEqual([item['product_id'] for item in skipped], [product.id])
        self.assertEqual(skipped[0]['reason'], 'на складе нет остатка')
        # В минус остаток не уводим
        self.assertEqual(Stock.objects.get(product=product, warehouse=self.warehouse).quantity, 0)

    def test_confirm_completes_order_when_product_has_no_warehouse(self):
        product = make_product('Зеркало', status=StatusChoices.SOLD, category=self.category)
        order = self._order([product])

        response = self.client.post(f'/api/admin/orders/{order.id}/confirm/')

        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatusChoices.COMPLETED)
        self.assertEqual(
            response.data['write_off_skipped'][0]['reason'], 'товар не привязан к складу'
        )

    def test_confirm_writes_off_only_available_products(self):
        available = make_product('Дверь', warehouse=self.warehouse, category=self.category)
        Stock.objects.create(product=available, warehouse=self.warehouse, quantity=1)
        sold = make_product('Капот', warehouse=self.warehouse, category=self.category)
        Stock.objects.create(product=sold, warehouse=self.warehouse, quantity=0)
        order = self._order([available, sold])

        response = self.client.post(f'/api/admin/orders/{order.id}/confirm/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [item['product_id'] for item in response.data['write_off_skipped']], [sold.id]
        )
        self.assertEqual(Stock.objects.get(product=available, warehouse=self.warehouse).quantity, 0)

    def test_confirm_rejects_unpaid_order(self):
        """Проверку оплаты не ослабляем."""
        product = make_product('Бампер', warehouse=self.warehouse, category=self.category)
        Stock.objects.create(product=product, warehouse=self.warehouse, quantity=1)
        order = self._order([product], payment_status=PaymentStatusChoices.PENDING)

        response = self.client.post(f'/api/admin/orders/{order.id}/confirm/')

        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatusChoices.PROCESSING)


class ImportOrderSyncTest(TestCase):
    """Повторный импорт заказа из Recar — ночная синхронизация незавершённых."""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='Склад 1')
        self.category = Category.objects.create(name='Двигатель')
        self.first = make_product('Фара', warehouse=self.warehouse, category=self.category)
        self.second = make_product('Капот', warehouse=self.warehouse, category=self.category)

    def _recar_order(self, product_ids, status='processing', paid=False):
        return {
            'id': 500100,
            'paymentType': 'cash',
            'paymentCompleted': paid,
            'status': status,
            'returning': False,
            'totalPrice': 1500,
            'comment': None,
            'location': {'id': self.warehouse.id},
            'parentOrder': None,
            'createdAt': '2026-02-24T12:11:22+00:00',
            'updatedAt': '2026-02-24T12:46:52+00:00',
            'partsSnapshot': [
                {'id': str(product_id), 'nearestParentId': None, 'returning': False}
                for product_id in product_ids
            ],
        }

    def test_repeated_import_does_not_duplicate_items(self):
        data = self._recar_order([self.first.id, self.second.id])

        ImportOrderAction().run(data)
        ImportOrderAction().run(data)

        order = Order.objects.get(id=data['id'])
        self.assertEqual(order.goods.count(), 2)

    def test_repeated_import_applies_new_status_and_payment(self):
        ImportOrderAction().run(self._recar_order([self.first.id]))
        ImportOrderAction().run(self._recar_order([self.first.id], status='done', paid=True))

        order = Order.objects.get(id=500100)
        self.assertEqual(order.status, OrderStatusChoices.COMPLETED)
        self.assertEqual(order.payment_status, PaymentStatusChoices.PAID)

    def test_repeated_import_removes_items_gone_from_recar(self):
        ImportOrderAction().run(self._recar_order([self.first.id, self.second.id]))
        ImportOrderAction().run(self._recar_order([self.first.id]))

        order = Order.objects.get(id=500100)
        self.assertEqual([item.product_id for item in order.goods.all()], [self.first.id])

    def test_unknown_product_does_not_break_import(self):
        """Товара может не быть в нашей базе — заказ всё равно импортируется."""
        data = self._recar_order([self.first.id, 999999999])

        ImportOrderAction().run(data)

        order = Order.objects.get(id=data['id'])
        self.assertEqual([item.product_id for item in order.goods.all()], [self.first.id])


class OrderListQueriesTest(TestCase):
    """Список заказов не должен делать запросы пропорционально числу заказов."""

    def setUp(self):
        self.client = APIClient()
        self.warehouse = Warehouse.objects.create(name='Склад 1')
        self.category = Category.objects.create(name='Двигатель')

        staff_role = Role.objects.create(name=RoleEnum.SALEPERSON.value)
        self.staff = User.objects.create_user(phone='+77770000202', password='pass123456', is_staff=True)
        self.staff.roles.add(staff_role)
        self.client.force_authenticate(user=self.staff)

    def _make_orders(self, count):
        for index in range(count):
            order = Order.objects.create(
                total=1000,
                warehouse=self.warehouse,
                payment_status=PaymentStatusChoices.PAID,
                status=OrderStatusChoices.PROCESSING,
                first_name=f'Клиент {index}',
            )
            product = make_product(f'Товар {index}', warehouse=self.warehouse, category=self.category)
            OrderItem.objects.create(order=order, product=product, quantity=1)

    # Запросы профилировщиков считать нельзя: django-silk пишет свои
    # silk_request и периодически подчищает старые записи, поэтому их число
    # скачет от запроса к запросу и к нашим выборкам отношения не имеет
    FOREIGN_TABLES = ('silk_', 'drf_api_logs')

    def _count_queries(self, count):
        self._make_orders(count)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/api/admin/orders/')
        self.assertEqual(response.status_code, 200, response.data)

        own_queries = [
            query for query in queries.captured_queries
            if not any(table in query['sql'] for table in self.FOREIGN_TABLES)
        ]
        return len(own_queries), response

    def test_query_count_does_not_grow_with_orders(self):
        few_queries, _ = self._count_queries(2)
        Order.objects.all().delete()
        many_queries, response = self._count_queries(10)

        self.assertEqual(len(response.data['results']), 10)
        # Число запросов не зависит от числа заказов: при N+1 было бы +8 и больше
        self.assertEqual(
            few_queries, many_queries,
            f'список заказов делает запросы на каждую строку: {few_queries} -> {many_queries}'
        )

    def test_list_keeps_fields_used_by_admin_table(self):
        self._make_orders(1)

        response = self.client.get('/api/admin/orders/')

        row = response.data['results'][0]
        for field in ('id', 'status', 'payment_status', 'delivery_type', 'total',
                      'created_at', 'comment', 'first_name', 'warehouse', 'goods'):
            self.assertIn(field, row)
        self.assertIn('name', row['goods'][0]['product'])
        self.assertIn('pictures', row['goods'][0]['product'])
        self.assertEqual(row['goods'][0]['product']['price'], 1000)


class OrderWriteOffBlockerTest(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='Склад 1')

    def test_component_of_disassembled_part_is_skipped(self):
        parent = make_product('Двигатель', warehouse=self.warehouse)
        component = Product.objects.create(
            name='Форсунка', status=StatusChoices.IN_STOCK, warehouse=self.warehouse, parent=parent
        )
        Stock.objects.create(product=component, warehouse=self.warehouse, quantity=1)

        reason = OrderAction().write_off_blocker(component)

        self.assertEqual(reason, 'товар входит в состав разобранной детали')

    def test_available_product_has_no_blocker(self):
        product = make_product('Фара', warehouse=self.warehouse)
        Stock.objects.create(product=product, warehouse=self.warehouse, quantity=1)

        self.assertIsNone(OrderAction().write_off_blocker(product))
