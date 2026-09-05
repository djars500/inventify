import logging
import os
from io import BytesIO

import requests
from PIL import Image
from django.core.files.base import ContentFile
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.car.models import ModificationDraft
from apps.car.tasks import update_eav_attr
from apps.category.models import Category
from apps.product.enums import StatusChoicesRecar, StatusChoices
from apps.product.models.ImportProductData import ImportProductData
from apps.product.models.Price import Price
from apps.product.models.Product import ProductDetail, ProductImage, Product
from apps.product.repository import ProductRepository
from apps.stock.actions import StockAction
from apps.stock.models import Warehouse
from base.requests import RecarRequest

logger = logging.getLogger('django')


class ProductAction:

    def create(self, data):
        with transaction.atomic():
            product = ProductRepository.create(**data)
            return product

    def update(self, product: Product, data):
        with transaction.atomic():
            instance = ProductRepository.update(product, **data)
            return instance

    def assign_to_warehouse(self, product: Product, warehouse: Warehouse):
        # Проверяем, что фотографии уже загружены
        # if not product.pictures.exists():
        #     raise ValidationError("Сначала необходимо загрузить фотографии")

        # Проверяем что у товара нету привязки к складу
        if product.warehouse:
            raise ValidationError(f"Товар уже присвоен к складу: {product.warehouse.name}" )

        # Привязываем продукт к складу
        stock = StockAction().process_ingoing(product, warehouse, 1)

        # Меняем статус на "в наличии"
        product.status = StatusChoices.IN_STOCK.value
        self.save_product(product)

        return stock

    @staticmethod
    def save_product(product: Product):
        """
        Сохраняет продукт и проверяет возможность изменения статуса.

        :param product: Продукт для сохранения.
        :raises ValidationError: Если статус изменен неправильно.
        """

        if product:  # Если продукт уже существует
            original = Product.objects.get(pk=product.pk)
            if original.status == StatusChoices.IN_STOCK.value and product.status != StatusChoices.IN_STOCK.value:
                raise ValidationError("Нельзя изменить статус обратно после установки 'в наличии'")

        product.save()

    @staticmethod
    def add_component(product: Product, category: Category):
        pass

    @staticmethod
    def remove_component(product: Product, category: Category):
        pass


class ImportProductAction:

    @staticmethod
    def save_image(product):
        """Перезагружает фотографии товара из Recar.

        Идемпотентна: старые фото заменяются, а не добавляются рядом. Раньше
        повторный вызов (действие «Импортировать фото» в админке) плодил копии —
        при AWS_S3_FILE_OVERWRITE=False каждая загрузка создаёт объект с новым
        именем, и прежний навсегда оставался в бакете.

        Новые файлы сначала целиком готовятся в памяти и только потом заменяют
        старые: если Recar не ответит на середине, товар не останется без фото.
        """
        pictures = RecarRequest().get_photos_by_product(product_id=product.id)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36'
        }

        prepared = []
        for product_image in pictures:
            image_url = product_image['host'] + '1024/768/' + product_image['url']
            response = requests.get(image_url, headers=headers, timeout=60)
            image = Image.open(BytesIO(response.content))

            if image.mode == 'RGBA':
                image = image.convert('RGB')

            output_io = BytesIO()
            quality = 70  # Начальное качество
            max_size = 250 * 1024

            while True:
                output_io.truncate(0)
                output_io.seek(0)
                image.save(output_io, format='JPEG', quality=quality)
                output_io.seek(0)
                if len(output_io.getvalue()) <= max_size or quality < 10:
                    break
                quality -= 5  # Уменьшение качества на 5%

            prepared.append((image_url.split("/")[-1], output_io.getvalue()))
            output_io.close()

        if not prepared:
            return

        # Файлы из хранилища удалит django-cleanup по post_delete
        product.pictures.all().delete()

        for file_name, content in prepared:
            product_image_instance = ProductImage(product=product)
            product_image_instance.image.save(file_name, ContentFile(content))

    def run(self, product_data: dict):
        """Совместимость с прежними вызовами: создаёт товар либо обновляет существующий."""
        return self.upsert(product_data)

    @transaction.atomic()
    def upsert(self, product_data: dict) -> Product:
        """Переливает снапшот Recar в Product: создаёт новый товар или обновляет существующий.

        Ошибки не глушатся: если в снапшоте нет категории (в Recar товар ещё
        не распарсен), поднимаем понятную ValidationError вместо TypeError
        на product_data['category']['name'].
        """
        product_id = int(product_data['id'])
        category = product_data.get('category') or {}
        if not category.get('id'):
            raise ValidationError(
                f'В снапшоте товара {product_id} нет категории — '
                f'в Recar товар ещё не распарсен, импортировать нечего'
            )

        fields = {
            'name': category['name'],
            'category_id': category['id'],
            'market_price': self.map_market_price(product_data.get('suggestedPrice')),
            'defect': product_data.get('defectComment'),
            'comment': product_data.get('comment'),
            'status': self.map_status(product_data.get('status')),
        }

        product = Product.objects.filter(id=product_id).first()
        if product is None:
            product = Product.objects.create(id=product_id, **fields)
        else:
            for field, value in fields.items():
                setattr(product, field, value)
            product.save()

        self.input_parent(product_data)

        ProductDetail.objects.update_or_create(
            product=product,
            defaults={
                'height': product_data.get('height'),
                'width': product_data.get('width'),
                'length': product_data.get('length'),
                'weight': product_data.get('weight'),
            }
        )

        self.sync_price(product, product_data)
        self.sync_modification_attrs(product)

        if os.environ.get('APP_ENV', 'production') == 'local':
            return product

        # На обновлении фото заново не тянем — только если их ещё нет
        if not product.pictures.exists():
            try:
                self.save_image(product)
            except Exception:
                logger.error(f"Ошибка загрузки фото продукта {product.id}")

        return product

    @staticmethod
    def map_market_price(suggested_price):
        """Рыночная цена из snapshot'а Recar.

        В схеме Recar suggestedPrice сейчас Float, а в старых снапшотах лежит
        объект с currentPrice — поддерживаем оба варианта, иначе прошлые
        снапшоты перестанут переливаться.
        """
        if suggested_price is None:
            return None
        if isinstance(suggested_price, dict):
            suggested_price = suggested_price.get('currentPrice')
            if suggested_price is None:
                return None
        try:
            return int(float(suggested_price))
        except (TypeError, ValueError):
            logger.error(f"Не удалось разобрать suggestedPrice: {suggested_price!r}")
            return None

    @staticmethod
    def map_status(recar_status, default=StatusChoices.RAW.value):
        """Статус Recar -> статус товара; неизвестное значение не роняет импорт."""
        try:
            return StatusChoicesRecar[recar_status].value
        except KeyError:
            logger.error(f"Неизвестный статус товара в Recar: {recar_status}")
            return default

    @staticmethod
    def sync_price(product: Product, product_data: dict):
        """Обновляет последнюю цену товара, не плодя новую запись на каждый импорт."""
        cost = 0 if product_data.get('price') is None else int(product_data['price'])
        price = product.price.order_by('-id').first()

        if price is None:
            Price.objects.create(product=product, cost=cost)
            return

        if price.cost != cost:
            price.cost = cost
            price.save(update_fields=['cost', 'updated_at'])

    @staticmethod
    def sync_modification_attrs(product: Product):
        """Заполняет EAV-атрибуты модификации, если для товара есть черновик."""
        modification_attr = ModificationDraft.objects.filter(product_id=product.id).first()
        if modification_attr is None:
            logger.warning(f"Для товара {product.id} нет ModificationDraft — атрибуты не заполнены")
            return

        update_eav_attr(modification_attr.data, product.id)

    @staticmethod
    def input_parent(product_data: dict):
        product = Product.objects.get(id=product_data['id'])
        parent_id = product_data.get('nearestParentId')

        if parent_id is not None:
            parent_id = int(parent_id)
            if not Product.objects.filter(id=parent_id).exists():
                # Родителя ещё не импортировали. Раньше это валило всю транзакцию
                # по IntegrityError, теперь просто оставляем товар без родителя.
                logger.warning(f"Родитель {parent_id} товара {product.id} не найден — связь не установлена")
                return

        product.parent_id = parent_id
        product.save()


class RecarProductSyncAction:
    """Синхронизация товара с Recar: снапшот ImportProductData и перелив его в Product."""

    @staticmethod
    def refresh_draft(product_id: int):
        """Перезаписывает снапшот свежим ответом Recar. Возвращает (снапшот, создан ли он)."""
        product_data = RecarRequest().get_product(product_id)
        if not product_data:
            raise ValidationError(f'Recar не вернул данные по товару {product_id}')

        draft = ImportProductData.objects.filter(product_id=product_id).order_by('-id').first()
        if draft is None:
            return ImportProductData.objects.create(product_id=product_id, data=product_data), True

        draft.data = product_data
        draft.save(update_fields=['data', 'updated_at'])
        return draft, False

    def sync(self, product_id: int, update_product: bool = True) -> dict:
        """Обновляет снапшот и, если нужно, сам товар. Возвращает, что именно изменилось."""
        draft, draft_created = self.refresh_draft(product_id)
        result = {
            'product_id': product_id,
            'draft_created': draft_created,
            'product_created': None,
        }

        if update_product:
            product_existed = Product.objects.filter(id=product_id).exists()
            ImportProductAction().upsert(draft.data)
            result['product_created'] = not product_existed

        return result

    def sync_safe(self, product_id: int, update_product: bool = True) -> dict:
        """Как sync, но ошибка по одному товару не прерывает обработку остальных."""
        try:
            result = self.sync(product_id, update_product=update_product)
            result['error'] = None
            return result
        except Exception as exc:  # noqa: BLE001 — причину показываем пользователю и идём дальше
            logger.error(f"Не удалось синхронизировать товар {product_id} с Recar: {exc}")
            return {
                'product_id': product_id,
                'draft_created': None,
                'product_created': None,
                'error': str(exc) or type(exc).__name__,
            }

    def sync_after(self, from_product_id: int, limit: int, update_product: bool = True) -> dict:
        """Синхронизирует товары Recar с ID больше указанного, не больше limit за раз.

        Возвращает и last_product_id — ID, на котором остановились, чтобы
        продолжить с него следующим запуском.
        """
        product_ids = RecarRequest().get_product_ids_after(from_product_id)
        batch = product_ids[:limit]
        results = [self.sync_safe(product_id, update_product=update_product) for product_id in batch]

        return {
            'results': results,
            'last_product_id': batch[-1] if batch else from_product_id,
            'remaining': max(len(product_ids) - len(batch), 0),
            'total': len(product_ids),
        }