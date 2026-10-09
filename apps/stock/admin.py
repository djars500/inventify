from django.contrib import admin
from django.db.models import JSONField
from django_json_widget.widgets import JSONEditorWidget
from base.admin import SafeDjangoQLSearchMixin

# Register your models here.
from apps.stock import models


class StockTabularInline(admin.TabularInline):
    model = models.Stock
    readonly_fields = ('product',)
    extra = 0

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if not self.has_view_or_change_permission(request):
            queryset = queryset.none()
        return queryset.filter(quantity__gt=0)


class WarehouseAdmin(SafeDjangoQLSearchMixin, admin.ModelAdmin):
    list_display = ('id', 'name', 'min_stock_level', 'get_stock')
    search_fields = ('name', 'product',)
    filter_horizontal = ('products_category',)
    inlines = [StockTabularInline, ]

    def get_stock(self, warehouse: models.Warehouse):
        return warehouse.get_stock()


admin.site.register(models.Warehouse, WarehouseAdmin)


class StockAdmin(SafeDjangoQLSearchMixin, admin.ModelAdmin):
    list_display = ('id', 'product', 'product_id', 'warehouse', 'quality', 'quantity')
    search_fields = ('product_id',)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    raw_id_fields = ('product', 'warehouse',)


class StockMovementAdmin(admin.ModelAdmin):
    raw_id_fields = ('product', 'warehouse',)


class WarehouseDraftsAdmin(admin.ModelAdmin):
    search_fields = ('warehouse_id',)
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }


admin.site.register(models.Stock, StockAdmin)
admin.site.register(models.Quality)
admin.site.register(models.StockMovement, StockMovementAdmin)
admin.site.register(models.WarehouseDrafts, WarehouseDraftsAdmin)
