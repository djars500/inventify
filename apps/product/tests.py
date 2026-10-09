from django.test import TestCase
from eav.models import Attribute
from rest_framework.test import APIClient

from apps.category.models import Category
from apps.product.enums import StatusChoices
from apps.product.filters import DynamicProductFilterSet
from apps.product.models import Product
from apps.product.models.Price import Price


class ProductFilterValidationTest(TestCase):
    """Фильтры клиентского списка товаров.

    Фронт присылал `manufacturer=NaN` (результат parseInt по пустому
    параметру), строка доходила до базы и давала 500
    «Field 'id' expected a number but got 'NaN'».
    """

    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name='Двигатель')
        product = Product.objects.create(
            name='Блок ECU', status=StatusChoices.IN_STOCK, category=self.category
        )
        Price.objects.create(product=product, cost=1000)

    def test_nan_in_category_returns_400(self):
        response = self.client.get('/api/v2/product/', {'category': 'NaN'})

        self.assertEqual(response.status_code, 400, response.data)

    def test_nan_in_modification_returns_400(self):
        response = self.client.get('/api/v2/product/', {'modification': 'NaN'})

        self.assertEqual(response.status_code, 400, response.data)

    def test_text_in_status_returns_400(self):
        response = self.client.get('/api/v2/product/', {'status': 'abc'})

        self.assertEqual(response.status_code, 400, response.data)

    def test_empty_filters_are_ignored(self):
        """Пустые значения фильтров запрос не ломают — так ходит сайт."""
        response = self.client.get(
            '/api/v2/product/', {'category': '', 'modelCar': '', 'manufacturer': '', 'status': 2}
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 1)

    def test_valid_numeric_filter_works(self):
        response = self.client.get('/api/v2/product/', {'category': self.category.id})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 1)

    def test_product_without_eav_attribute_does_not_break_list(self):
        """У товара может не быть атрибута modelCar — это не 500."""
        response = self.client.get('/api/v2/product/')

        self.assertEqual(response.status_code, 200, response.data)


class DynamicCarFilterValidationTest(TestCase):
    """Фильтры по машине строятся из EAV-атрибутов.

    Состав этих фильтров django-filter вычисляет один раз при создании класса,
    поэтому в тесте создаём атрибуты и пересобираем класс наследованием —
    иначе в пустой тестовой базе динамических фильтров просто нет.
    """

    @classmethod
    def setUpTestData(cls):
        Attribute.objects.get_or_create(
            name='modelCar', slug='modelCar', defaults={'datatype': Attribute.TYPE_OBJECT}
        )

    def setUp(self):
        self.filter_set = type('CarFilterSetForTest', (DynamicProductFilterSet,), {})
        self.assertIn('manufacturer', self.filter_set.base_filters)

    def _validate(self, data):
        filter_set = self.filter_set(data=data, queryset=Product.objects.all())
        return filter_set.is_valid(), filter_set.errors

    def test_nan_manufacturer_is_rejected(self):
        is_valid, errors = self._validate({'manufacturer': 'NaN'})

        self.assertFalse(is_valid)
        self.assertIn('manufacturer', errors)

    def test_nan_model_car_is_rejected(self):
        is_valid, errors = self._validate({'modelCar': 'NaN'})

        self.assertFalse(is_valid)
        self.assertIn('modelCar', errors)

    def test_numeric_manufacturer_is_accepted(self):
        is_valid, errors = self._validate({'manufacturer': '5,7'})

        self.assertTrue(is_valid, errors)
