from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.address.models import Address
from apps.order import models
from apps.order.enums import PaymentTypeChoices, DeliveryTypeChoices, PaymentStatusChoices, OrderStatusChoices
from apps.product.enums import StatusChoices
from apps.product.models import Product
from apps.product.serializers import ProductSerializer
from apps.stock.models import Warehouse
from apps.stock.serializers import QualitySerializer, WareHouseSerializer
from users.models.User import User
from users.serializers import UserSerializer


class OrderItemSerializer(serializers.ModelSerializer):
    product_id = serializers.PrimaryKeyRelatedField(required=True,
                                                    queryset=Product.objects.filter(status=StatusChoices.IN_STOCK),
                                                    source='product',
                                                    write_only=True)
    product = ProductSerializer(read_only=True)
    quality = QualitySerializer(read_only=True)
    quantity = serializers.IntegerField(required=True)

    class Meta:
        exclude = ('order',)
        model = models.OrderItem

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Динамически обновляем queryset на основе контекста
        if self.context.get('refund_mode', False):
            self.fields['product_id'].queryset = Product.objects.filter(status=StatusChoices.SOLD)
        else:
            self.fields['product_id'].queryset = Product.objects.filter(status=StatusChoices.IN_STOCK)


class OrderSerializer(serializers.ModelSerializer):
    uuid = serializers.UUIDField(read_only=True)
    goods = OrderItemSerializer(many=True, required=True, allow_empty=False)
    total = serializers.DecimalField(read_only=True, max_digits=10, decimal_places=2)
    delivery_type_id = serializers.ChoiceField(write_only=True,
                                               choices=DeliveryTypeChoices.choices,
                                               source='delivery_type')
    delivery_type = serializers.CharField(read_only=True, source='get_delivery_type_display')
    payment_type_id = serializers.ChoiceField(write_only=True,
                                              choices=PaymentTypeChoices.choices,
                                              source='payment_type')
    payment_type = serializers.CharField(read_only=True, source='get_payment_type_display')
    payment_status = serializers.CharField(read_only=True, source='get_payment_status_display')

    status = serializers.CharField(read_only=True, source='get_status_display')
    warehouse_id = serializers.PrimaryKeyRelatedField(required=False,
                                                      queryset=Warehouse.objects.all(),
                                                      source='warehouse',
                                                      allow_null=True)
    warehouse = WareHouseSerializer(read_only=True)
    user = UserSerializer(read_only=True)

    class Meta:
        fields = '__all__'
        model = models.Order


class OrderProductListSerializer(serializers.ModelSerializer):
    """Минимум о товаре для списка заказов.

    Полный ProductSerializer тянет категорию, модификацию, EAV, остатки и
    цену отдельными запросами на каждую позицию: при PAGE_SIZE=100 страница
    заказов превращалась в тысячи запросов.
    """
    status = serializers.CharField(source='get_status_display', read_only=True)
    price = serializers.SerializerMethodField()
    pictures = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ('id', 'name', 'status', 'price', 'pictures')

    @staticmethod
    def get_price(product: Product):
        # Берём из prefetch'а, а не через .last(): иначе на каждую позицию
        # уходит отдельный запрос
        prices = list(product.price.all())
        return prices[-1].cost if prices else None

    def get_pictures(self, product: Product):
        from apps.product.serializers import ProductImageSerializer
        return ProductImageSerializer(product.pictures.all(), many=True, context=self.context).data


class OrderItemListSerializer(serializers.ModelSerializer):
    product = OrderProductListSerializer(read_only=True)

    class Meta:
        model = models.OrderItem
        fields = ('id', 'quantity', 'is_returning', 'product')


class OrderWarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ('id', 'name')


class OrderListSerializer(serializers.ModelSerializer):
    """Заказ для списка: те же поля, что и раньше, но без тяжёлых вложенностей."""
    goods = OrderItemListSerializer(many=True, read_only=True)
    warehouse = OrderWarehouseSerializer(read_only=True)
    delivery_type = serializers.CharField(read_only=True, source='get_delivery_type_display')
    payment_type = serializers.CharField(read_only=True, source='get_payment_type_display')
    payment_status = serializers.CharField(read_only=True, source='get_payment_status_display')
    status = serializers.CharField(read_only=True, source='get_status_display')

    class Meta:
        model = models.Order
        fields = (
            'id', 'uuid', 'total', 'discount', 'comment', 'created_at', 'updated_at',
            'status', 'payment_status', 'payment_type', 'delivery_type',
            'client', 'first_name', 'last_name', 'phone_number', 'email',
            'warehouse', 'user', 'address', 'refund_order', 'goods',
        )


class OrderUpdateSerializer(OrderSerializer):
    payment_status = serializers.ChoiceField(choices=PaymentStatusChoices.choices, required=False)
    delivery_type_id = serializers.ChoiceField(required=False,
                                               choices=DeliveryTypeChoices.choices,
                                               source='delivery_type')

    payment_type_id = serializers.ChoiceField(required=False,
                                              choices=PaymentTypeChoices.choices,
                                              source='payment_type')
    warehouse_id = serializers.PrimaryKeyRelatedField(required=False,
                                                      queryset=Warehouse.objects.all(),
                                                      source='warehouse',
                                                      allow_null=True)
    user_id = serializers.PrimaryKeyRelatedField(required=False,
                                                 queryset=User.objects.all(),
                                                 source='user',
                                                 allow_null=True)
    address_id = serializers.PrimaryKeyRelatedField(required=False,
                                                    queryset=Address.objects.all(),
                                                    source='address',
                                                    allow_null=True)

    class Meta(OrderSerializer.Meta):
        # Состав заказа через это обновление не меняется: смена товаров тянет
        # пересчёт суммы и возврат статусов товаров, это отдельная операция
        fields = (
            'payment_type_id',
            'delivery_type_id',
            'warehouse_id',
            'comment',
            'payment_status',
            'user_id',
            'address_id',
            'client',
            'first_name',
            'last_name',
            'phone_number',
            'email',
        )

    def validate(self, attrs):
        order_status = attrs.get('status', None)
        payment_status = attrs.get('payment_status', None)
        if order_status == OrderStatusChoices.COMPLETED and payment_status != PaymentStatusChoices.PAID:
            raise ValidationError(_('Для завершения заказа, оплатите сумму'))

        return attrs


class OrderRefundSerializer(serializers.ModelSerializer):
    warehouse_id = serializers.PrimaryKeyRelatedField(required=False,
                                                      queryset=Warehouse.objects.all(),
                                                      source='warehouse',
                                                      allow_null=True)
    goods = OrderItemSerializer(many=True, context={'refund_mode': True}, required=True, allow_empty=False)
    refund_order_id = serializers.PrimaryKeyRelatedField(required=True,
                                                         queryset=models.Order.objects.all(),
                                                         source='refund_order',
                                                         allow_null=False)

    class Meta:
        fields = ('warehouse_id', 'comment', 'goods', 'refund_order_id')
        model = models.Order
