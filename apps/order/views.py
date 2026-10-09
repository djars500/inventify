from django.db import transaction
from django.db.models import Prefetch
from django.utils.translation import gettext as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, generics, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from django.contrib.auth.models import AnonymousUser

from apps.order import models, serializers
from apps.order.actions import OrderAction
from apps.order.enums import OrderStatusChoices, PaymentStatusChoices
from apps.order.filters import OrderFilter
from apps.product.models.Price import Price
from apps.product.models.Product import ProductImage


class OrderViewSet(viewsets.ModelViewSet):
    queryset = models.Order.objects.all().order_by('-created_at')
    serializer_class = serializers.OrderSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = OrderFilter

    def get_serializer_class(self):
        # В списке отдаём облегчённый заказ: полный ProductSerializer на каждую
        # позицию при PAGE_SIZE=100 превращал страницу в тысячи запросов
        if self.action == 'list':
            return serializers.OrderListSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        queryset = super().get_queryset().select_related('warehouse', 'user', 'address')

        if self.action == 'list':
            # Порядок в prefetch задан явно: цену и фото берём последними
            # элементами списка, а не отдельным запросом через .last()
            return queryset.prefetch_related(
                'goods__product',
                Prefetch('goods__product__price', queryset=Price.objects.order_by('id')),
                Prefetch('goods__product__pictures', queryset=ProductImage.objects.order_by('id')),
            )

        return queryset.prefetch_related(
            'goods__product__category',
            'goods__product__code',
            'goods__product__pictures',
            'goods__product__price',
            'goods__product__detail',
            'goods__product__warehouse',
            'goods__product__modification',
        )

    @swagger_auto_schema(responses={200: serializers.OrderSerializer},
                         request_body=serializers.OrderSerializer,
                         operation_id='Создание заказа',
                         tags=['Заказы'],
                         )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user if not isinstance(request.user, AnonymousUser) else None
        serializer.validated_data['user'] = user
        with transaction.atomic():
            order = OrderAction(serializer.validated_data).create()

        return Response(self.serializer_class(order).data, status=status.HTTP_201_CREATED)

    @swagger_auto_schema(responses={200: serializers.OrderSerializer},
                         operation_id='Удалить',
                         tags=['Заказы'],
                         )
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status == OrderStatusChoices.CANCELED:
            raise ValidationError(_('Заказ уже отменен'))
        if instance.status == OrderStatusChoices.COMPLETED:
            raise ValidationError(_('Вы не можете удалить проведенный заказ'))

        with transaction.atomic():
            OrderAction().delete(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @swagger_auto_schema(responses={200: serializers.OrderSerializer},
                         request_body=serializers.OrderUpdateSerializer,
                         operation_id='Обновить',
                         tags=['Заказы'],
                         )
    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            partial = kwargs.pop('partial', False)
            instance = self.get_object()
            serializer = serializers.OrderUpdateSerializer(instance, data=request.data, partial=partial)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)

            if getattr(instance, '_prefetched_objects_cache', None):
                instance._prefetched_objects_cache = {}

            return Response(self.serializer_class(instance).data)

    @swagger_auto_schema(responses={200: serializers.OrderSerializer},
                         request_body=serializers.OrderRefundSerializer,
                         operation_id='Возврат',
                         tags=['Заказы'],
                         )
    @action(detail=False, methods=['post'], url_path='refund')
    def refund(self, request, *args, **kwargs):
        serializer = serializers.OrderRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data['refund_order'].status != OrderStatusChoices.COMPLETED:
            raise ValidationError(_('Вы не можете вернуть заказ, который не завершен'))
        with transaction.atomic():
            order = OrderAction(data).refund()
            return Response(self.serializer_class(order).data)

    @action(detail=False, methods=['delete'], url_path='bulk-delete')
    def bulk_delete(self, request):
        order_ids = request.data.get("ids", [])
        orders = models.Order.objects.filter(id__in=order_ids).exclude(status=OrderStatusChoices.DELETED.value)
        if orders.exists() is False:
            return Response({"error": "Не переданы ID заказов или удалены"}, status=status.HTTP_400_BAD_REQUEST)

        deleted_count = orders.update(status=OrderStatusChoices.DELETED.value)
        return Response({"deleted": deleted_count}, status=status.HTTP_204_NO_CONTENT)


class OrderConfirmView(generics.GenericAPIView):
    queryset = models.Order.objects.filter(status=OrderStatusChoices.PROCESSING)
    serializer_class = serializers.OrderSerializer

    @swagger_auto_schema(responses={200: serializers.OrderSerializer},
                         operation_id='Завершение заказа',
                         tags=['Заказы'],
                         )
    def post(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = self.get_object()
            if instance.payment_status != PaymentStatusChoices.PAID:
                raise ValidationError(_('Вы не можете завершить заказ, который не оплачен'))
            order, write_off_skipped = OrderAction().confirm(instance)

        data = self.serializer_class(order).data
        # Товары, которые уже были списаны (например проданы в Recar), завершению
        # заказа больше не мешают — но о них сообщаем, чтобы это не было тихо
        data['write_off_skipped'] = write_off_skipped
        return Response(data)
