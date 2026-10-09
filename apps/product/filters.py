import django_filters
from django.db.models import OuterRef, Exists, Subquery, QuerySet, Q
from django_filters import OrderingFilter
from eav.models import Attribute, Value

from apps.car.models.Model import ModelCar
from apps.category.models import Category
from apps.product.models import Product
from apps.product.models.Price import Price


class CharInFilter(django_filters.BaseInFilter, django_filters.CharFilter):
    pass


class NumberInFilter(django_filters.BaseInFilter, django_filters.NumberFilter):
    """Список чисел через запятую.

    Нужен вместо CharInFilter там, где значение уходит в запрос по id: фронт
    присылал `manufacturer=NaN` (результат parseInt без проверки), строка
    доходила до базы и давала 500 «Field 'id' expected a number but got 'NaN'».
    Теперь такой запрос отсекается валидацией фильтра с кодом 400.
    """
    pass


class DynamicProductFilterSet(django_filters.FilterSet):
    """Оптимизированный FilterSet для продуктов с динамическими EAV фильтрами.

    Рекомендуемые индексы для оптимальной производительности:
    - Product: name, category_id, status, created_at
    - Category: name
    - Price: (product_id, created_at)
    - Value: (attribute_id, entity_id, generic_value_id)
    - ModelCar: (startDate, endDate, manufacturer_id)
    """
    id = django_filters.NumberFilter(field_name='id')
    price = django_filters.RangeFilter(field_name='price__cost')
    category = NumberInFilter(field_name='category__id', lookup_expr='in')
    search = django_filters.CharFilter(method='filter_by_product_or_category_name')
    modification = NumberInFilter(field_name='modification__id', lookup_expr='in')
    status = NumberInFilter(field_name='status', lookup_expr='in')

    sort = OrderingFilter(
        fields=(
            ('id', 'id'),
            ('created_at', 'created_at'),
            ('status', 'status')
        )
    )

    class Meta:
        model = Product
        fields = '__all__'

    @staticmethod
    def filter_by_product_or_category_name(queryset: QuerySet, name, value):
        # Оптимизация: используем Q объекты для объединения условий в один запрос
        # вместо множественных запросов и объединения через списки ID

        # Находим категории, совпадающие по имени
        matching_categories = Category.objects.filter(name__icontains=value)

        # Собираем все категории (включая потомков) в один список
        all_category_ids = set()
        for category in matching_categories:
            all_category_ids.add(category.id)
            all_category_ids.update(
                category.get_all_descendants()
            )

        # Объединяем все условия через Q объекты
        return queryset.filter(
            Q(name__icontains=value) |           # Поиск по имени продукта
            Q(id__icontains=value) |             # Поиск по ID продукта
            Q(category__in=all_category_ids)  # Поиск по категориям
        ).distinct()

    @classmethod
    def get_filters(cls):
        """Получает все фильтры, включая динамические EAV фильтры.

        Примечание: Этот метод вызывается ОДИН РАЗ при загрузке класса,
        а не при каждом запросе, поэтому кэширование здесь не требуется.
        Оптимизация через select_related/prefetch_related достаточна.
        """
        # Получаем существующие фильтры
        filters = super().get_filters()

        # Добавляем фильтры для EAV атрибутов
        try:
            # Оптимизируем запрос с prefetch для избежания N+1 проблемы.
            # list() обязателен: queryset ленивый, и без него запрос уходил бы
            # в базу уже за пределами try — при переборе ниже. Из-за этого
            # приложение не импортировалось без живой БД, хотя except как раз
            # и писался ради такого случая.
            attributes = list(
                Attribute.objects.select_related('enum_group').prefetch_related('enum_group__values').all()
            )
        except Exception:
            # Таблица ещё не создана (во время миграций) или БД недоступна
            return filters

        # Создаем динамические фильтры на основе EAV атрибутов
        for attribute in attributes:
            if attribute.datatype == Attribute.TYPE_OBJECT:
                if attribute.slug == 'modelCar':
                    filters['year_start'] = django_filters.NumberFilter(
                        method='filter_year_start'
                    )
                    filters['year_end'] = django_filters.NumberFilter(
                        method='filter_year_end'
                    )
                    filters['manufacturer'] = NumberInFilter(
                        method='filter_manufacturer', lookup_expr='in'
                    )
                    filters['modelCar'] = NumberInFilter(
                        method='filter_modelCar', lookup_expr='in'
                    )
                else:
                    filters[attribute.name] = django_filters.ModelChoiceFilter(
                        field_name=f'eav__{attribute.slug}', queryset=ModelCar.objects.all()
                    )
            elif attribute.datatype == Attribute.TYPE_ENUM:
                filters[attribute.name] = django_filters.ChoiceFilter(
                    field_name=f'eav__{attribute.slug}', choices=[
                        (choice.value, choice.value) for choice in attribute.enum_group.values.all()
                    ]
                )
            else:
                filters[attribute.name] = django_filters.CharFilter(
                    field_name=f'eav__{attribute.slug}', lookup_expr='icontains'
                )

        return filters

    @staticmethod
    def get_eav_subquery(model_car_queryset):
        """Оптимизированный подзапрос для EAV значений.

        Принимает queryset вместо списка ID для избежания лишних запросов.
        """
        return Exists(Value.objects.filter(
            attribute__slug='modelCar',
            generic_value_id__in=Subquery(model_car_queryset.values('id')),
            entity_id=OuterRef('pk')
        ))

    def filter_year_start(self, queryset, name, value):
        """Фильтр по году начала производства модели автомобиля."""
        model_cars = ModelCar.objects.filter(startDate__year__gte=value)
        return queryset.filter(self.get_eav_subquery(model_cars))

    def filter_year_end(self, queryset, name, value):
        """Фильтр по году окончания производства модели автомобиля."""
        model_cars = ModelCar.objects.filter(endDate__year__lte=value)
        return queryset.filter(self.get_eav_subquery(model_cars))

    def filter_manufacturer(self, queryset, name, value):
        """Фильтр по производителю автомобиля."""
        model_cars = ModelCar.objects.filter(manufacturer_id__in=value)
        return queryset.filter(self.get_eav_subquery(model_cars))

    def filter_modelCar(self, queryset, name, value):
        """Фильтр по конкретной модели автомобиля."""
        model_cars = ModelCar.objects.filter(id__in=value)
        return queryset.filter(self.get_eav_subquery(model_cars))

    def filter_by_latest_price(self, queryset, name, value):
        """Оптимизированный фильтр по последней цене продукта.

        Использует один подзапрос вместо двух для лучшей производительности.
        """
        # Один подзапрос для получения последней цены
        latest_price_subquery = Price.objects.filter(
            product=OuterRef('pk')
        ).order_by('-created_at').values('cost')[:1]

        # Аннотируем queryset последней ценой
        queryset = queryset.annotate(latest_price=Subquery(latest_price_subquery))

        # Применяем фильтр диапазона
        if value.start is not None:
            queryset = queryset.filter(latest_price__gte=value.start)
        if value.stop is not None:
            queryset = queryset.filter(latest_price__lte=value.stop)

        return queryset.distinct()
