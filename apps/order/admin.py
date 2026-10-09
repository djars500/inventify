from django.contrib import admin
from django.db.models import JSONField
from django_json_widget.widgets import JSONEditorWidget
from base.admin import SafeDjangoQLSearchMixin

from apps.order import models
from apps.order.actions import ImportOrderAction
from apps.order.models import OrderItem


class OrderItemTabularInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    raw_id_fields = ('product',)
    fields = ('product', 'product_status', 'quantity', 'is_returning',)
    readonly_fields = ('product_status',)  # Поле только для чтения


class OrderAdmin(SafeDjangoQLSearchMixin, admin.ModelAdmin):
    list_display = ('id', 'total', 'payment_type', 'payment_status', 'status', 'created_at', )
    list_filter = ('payment_type', 'payment_status', 'status')
    inlines = [OrderItemTabularInline]
    raw_id_fields = ('warehouse', 'refund_order',)


@admin.action(description='Импортировать в основную базу заказов')
def import_from_recar(modeladmin, request, queryset: models.ImportOrderData):
    for obj in queryset:
        ImportOrderAction().run(obj.data)


class ImportOrderDataAdmin(admin.ModelAdmin):
    actions = [import_from_recar]
    search_fields = ('id',)
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }


admin.site.register(models.Order, OrderAdmin)
admin.site.register(models.OrderItem)
admin.site.register(models.ImportOrderData, ImportOrderDataAdmin)
