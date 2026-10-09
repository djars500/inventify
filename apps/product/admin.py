import re

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.widgets import AdminFileWidget, AdminURLFieldWidget
from django.db.models import JSONField
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from django_json_widget.widgets import JSONEditorWidget
from base.admin import SafeDjangoQLSearchMixin
from eav.forms import BaseDynamicEntityForm

from apps.car.models import ModelCar, Engine
from apps.product.actions import ImportProductAction, RecarProductSyncAction
from apps.product.models import ImportProductData
from apps.product.models.Price import Price
from apps.product.models.Product import *
from apps.product.tasks import import_pictures_from_recar


class ProductAdminForm(BaseDynamicEntityForm):
    modelCar = forms.ModelChoiceField(
        queryset=ModelCar.objects.all(),
        required=False,
        label='Модель машины',
        widget=AdminURLFieldWidget(attrs={'class': 'vForeignKeyRawIdAdminField'})
    )

    engine = forms.ModelChoiceField(
        queryset=Engine.objects.all(),
        required=False,
        label='Двигатель',
        widget=AdminURLFieldWidget(attrs={'class': 'vForeignKeyRawIdAdminField'})
    )

    class Meta:
        model = Product
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Убедитесь, что поля корректно настроены для отображения
        if self.instance:
            model_car = getattr(self.instance.eav, 'modelCar', None)
            engine = getattr(self.instance.eav, 'engine', None)
            self.fields['modelCar'].initial = model_car
            self.fields['engine'].initial = engine

            if model_car:
                url = reverse('admin:car_modelcar_change', args=[model_car.pk])
                self.fields['modelCar'].help_text = format_html('<a href="{}">Перейти к модели машины</a>', url)

            if engine:
                url = reverse('admin:car_modelcar_change', args=[engine.pk])
                self.fields['engine'].help_text = format_html('<a href="{}">Перейти к двигателю</a>', url)

    def clean(self):
        cleaned_data = super().clean()
        # Обработка данных полей EAV при сохранении
        model_car = cleaned_data.get('modelCar')
        engine = cleaned_data.get('engine')
        if model_car:
            self.instance.eav.modelCar = model_car
        if engine:
            self.instance.eav.engine = engine
        return cleaned_data


class AdminImageWidget(AdminFileWidget):
    def render(self, name, value, attrs=None, renderer=None):
        result = []
        if hasattr(value, "url"):
            result.append(
                f'''<a href="{value.url}" target="_blank">
                      <img 
                        src="{value.url}" alt="{value}" 
                        width="100" height="100"
                        style="object-fit: cover;"
                      />
                    </a>'''
            )
        result.append(super().render(name, value, attrs, renderer))
        return format_html("".join(result))


class ProductImageTabularInline(admin.StackedInline):
    model = ProductImage
    extra = 0
    formfield_overrides = {
        models.ImageField: {'widget': AdminImageWidget}
    }


class ProductDetailTabularInline(admin.TabularInline):
    model = ProductDetail
    extra = 0


class PriceTabularInline(admin.TabularInline):
    model = Price
    extra = 0


class ProductComponents(admin.TabularInline):
    model = Product
    extra = 0
    raw_id_fields = ('modification', 'parent',)
    fields = ('name', 'category', 'status', 'warehouse',)


@admin.action(description='Импортировать фото')
def import_photos_from_recar(modeladmin, request, queryset: Product):
    for obj in queryset:
        import_pictures_from_recar.delay(obj.id)
        

class ProductAdmin(SafeDjangoQLSearchMixin, admin.ModelAdmin):
    actions = [import_photos_from_recar]
    form = ProductAdminForm
    search_fields = ('name',)
    list_display = ('id', 'name', 'status')
    raw_id_fields = ('modification', 'parent',)
    list_filter = ('status',)
    inlines = [ProductComponents, ProductImageTabularInline, ProductDetailTabularInline, PriceTabularInline]

    def get_last_price(self, product):
        return product.price.last()


@admin.action(description='Импортировать в основную базу продуктов')
def import_from_recar(modeladmin, request, queryset: ImportProductData):
    """Переливает уже сохранённый снапшот в основную базу, без обращения в Recar."""
    for obj in queryset:
        try:
            ImportProductAction().upsert(obj.data)
        except Exception as exc:  # noqa: BLE001 — причину показываем в админке
            modeladmin.message_user(request, f'{obj.product_id}: ошибка — {exc}', messages.ERROR)
        else:
            modeladmin.message_user(request, f'{obj.product_id}: перелит в основную базу', messages.SUCCESS)


@admin.action(description='Обновить снапшот из Recar')
def refresh_drafts_from_recar(modeladmin, request, queryset: ImportProductData):
    """Тянет свежие данные из Recar для выбранных строк, сам товар не трогает."""
    action = RecarProductSyncAction()
    results = [action.sync_safe(obj.product_id, update_product=False) for obj in queryset]
    modeladmin.report_recar_results(request, results)


# Каждый ID — отдельный синхронный запрос в Recar, поэтому за раз берём немного:
# иначе запрос в админке упрётся в таймаут nginx
MAX_MANUAL_IMPORT_IDS = 50
DEFAULT_BATCH_LIMIT = 20


class RecarImportForm(forms.Form):
    """Два режима: точечно по списку ID либо всё, что новее указанного ID."""

    product_ids = forms.CharField(
        label='ID товаров в Recar',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4, 'cols': 60}),
        help_text=f'Через запятую, пробел или с новой строки. Не больше {MAX_MANUAL_IMPORT_IDS} за раз.'
    )
    from_product_id = forms.IntegerField(
        label='Либо: все товары с ID больше указанного',
        required=False,
        min_value=0,
        help_text='Берутся реальные ID из Recar по возрастанию, а не подряд идущие числа.'
    )
    limit = forms.IntegerField(
        label='Сколько взять за раз',
        required=False,
        min_value=1,
        max_value=MAX_MANUAL_IMPORT_IDS,
        initial=DEFAULT_BATCH_LIMIT,
        help_text='Только для режима «с ID больше указанного».'
    )
    update_product = forms.BooleanField(
        label='Обновить и товары в основной базе',
        required=False,
        initial=True
    )

    def clean_product_ids(self):
        raw = self.cleaned_data['product_ids'].strip()
        if not raw:
            return []

        product_ids = []
        invalid = []
        for chunk in re.split(r'[\s,;]+', raw):
            if not chunk:
                continue
            if not chunk.isdigit():
                invalid.append(chunk)
                continue
            product_id = int(chunk)
            if product_id not in product_ids:
                product_ids.append(product_id)

        if invalid:
            raise forms.ValidationError('Это не похоже на ID: %s' % ', '.join(invalid))
        if len(product_ids) > MAX_MANUAL_IMPORT_IDS:
            raise forms.ValidationError(
                f'За раз не больше {MAX_MANUAL_IMPORT_IDS} ID: запрос в Recar синхронный, '
                f'иначе страница отвалится по таймауту'
            )
        return product_ids

    def clean(self):
        cleaned_data = super().clean()
        product_ids = cleaned_data.get('product_ids')
        from_product_id = cleaned_data.get('from_product_id')

        if product_ids and from_product_id is not None:
            raise forms.ValidationError('Заполните что-то одно: либо список ID, либо «с ID больше указанного»')
        if not product_ids and from_product_id is None:
            raise forms.ValidationError('Укажите список ID или ID, с которого продолжить')

        cleaned_data['limit'] = cleaned_data.get('limit') or DEFAULT_BATCH_LIMIT
        return cleaned_data


class ImportProductAdmin(admin.ModelAdmin):
    actions = [import_from_recar, refresh_drafts_from_recar]
    search_fields = ('product_id',)
    list_display = ('product_id', 'id', 'has_category', 'updated_at')
    change_list_template = 'admin/product/importproductdata/change_list.html'
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }

    @admin.display(description='Есть категория', boolean=True)
    def has_category(self, obj: ImportProductData):
        """Без категории снапшот в основную базу не переливается."""
        return bool((obj.data or {}).get('category'))

    def get_urls(self):
        custom_urls = [
            path(
                'import-from-recar/',
                self.admin_site.admin_view(self.recar_import_view),
                name='product_importproductdata_recar_import',
            ),
        ]
        return custom_urls + super().get_urls()

    def recar_import_view(self, request):
        """Импорт из Recar по ID — синхронно, чтобы сразу видеть результат и ошибку."""
        form = RecarImportForm(request.POST or None)

        if request.method == 'POST' and form.is_valid():
            self.run_recar_import(request, form.cleaned_data)
            return redirect(reverse('admin:product_importproductdata_changelist'))

        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'title': 'Импорт из Recar',
            'form': form,
        }
        return render(request, 'admin/product/importproductdata/recar_import.html', context)

    def run_recar_import(self, request, data):
        action = RecarProductSyncAction()
        update_product = data['update_product']

        if data['product_ids']:
            results = [
                action.sync_safe(product_id, update_product=update_product)
                for product_id in data['product_ids']
            ]
            self.report_recar_results(request, results)
            return

        from_product_id = data['from_product_id']
        batch = action.sync_after(from_product_id, data['limit'], update_product=update_product)

        if not batch['results']:
            self.message_user(request, f'В Recar нет товаров с ID больше {from_product_id}', messages.WARNING)
            return

        self.report_recar_results(request, batch['results'])
        self.message_user(
            request,
            f'Остановились на ID {batch["last_product_id"]}. '
            f'Осталось необработанных: {batch["remaining"]} из {batch["total"]}. '
            f'Чтобы продолжить, укажите этот ID в поле «все товары с ID больше указанного».',
            messages.INFO,
        )

    def report_recar_results(self, request, results):
        for result in results:
            if result['error']:
                self.message_user(request, f'{result["product_id"]}: ошибка — {result["error"]}', messages.ERROR)
                continue

            draft_state = 'снапшот создан' if result['draft_created'] else 'снапшот обновлён'
            if result['product_created'] is None:
                product_state = 'товар не трогали'
            elif result['product_created']:
                product_state = 'товар создан'
            else:
                product_state = 'товар обновлён'

            self.message_user(request, f'{result["product_id"]}: {draft_state}, {product_state}', messages.SUCCESS)


class ProductImageAdmin(admin.ModelAdmin):
    list_display = ('product_id', 'id')
    raw_id_fields = ('product',)


class PriceAdmin(SafeDjangoQLSearchMixin, admin.ModelAdmin):
    raw_id_fields = ('product',)
    list_display = ('cost', 'product')


admin.site.register(Price, PriceAdmin)
admin.site.register(Product, ProductAdmin)
admin.site.register(ProductView)
admin.site.register(ImportProductData, ImportProductAdmin)
admin.site.register(ProductImage, ProductImageAdmin)
admin.site.register(ProductDetail)
