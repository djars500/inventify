import json

from django.core.management.base import BaseCommand

from base.requests import RecarRequest

# Типы, которые нужны, чтобы построить инкрементальную синхронизацию товаров:
# состав фильтров выборки и допустимые колонки сортировки
DEFAULT_TYPES = ('GetPartsInput', 'PartSort', 'Part')


class Command(BaseCommand):
    help = (
        'Печатает описание типов схемы Recar GraphQL. '
        'Нужна, чтобы не угадывать имена фильтров и колонок сортировки: '
        'poetry run python manage.py recar_introspect GetPartsInput PartSort'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'types',
            nargs='*',
            default=list(DEFAULT_TYPES),
            help=f'Имена типов (по умолчанию: {", ".join(DEFAULT_TYPES)})',
        )
        parser.add_argument(
            '--raw',
            action='store_true',
            help='Вывести ответ Recar как есть, без форматирования',
        )
        parser.add_argument(
            '--list-types',
            action='store_true',
            help='Список всех типов схемы вместо описания конкретных',
        )

    def handle(self, *args, **options):
        request = RecarRequest()

        if options['list_types']:
            self.list_types(request, raw=options['raw'])
            return

        for type_name in options['types']:
            self.stdout.write(self.style.MIGRATE_HEADING(f'\n=== {type_name} ==='))
            try:
                type_data = request.introspect_type(type_name)
            except Exception as exc:  # noqa: BLE001 — команда диагностическая
                self.stdout.write(self.style.ERROR(f'{type_name}: {exc}'))
                continue

            if not type_data:
                self.stdout.write(self.style.WARNING(f'Тип {type_name} в схеме не найден'))
                continue

            if options['raw']:
                self.stdout.write(json.dumps(type_data, ensure_ascii=False, indent=2))
                continue

            self.stdout.write(f'kind: {type_data.get("kind")}')
            self._print_fields('inputFields', type_data.get('inputFields'))
            self._print_fields('fields', type_data.get('fields'))

            enum_values = type_data.get('enumValues') or []
            if enum_values:
                self.stdout.write('enumValues:')
                for value in enum_values:
                    self.stdout.write(f'  - {value["name"]}')

    def list_types(self, request, raw=False):
        """Печатает типы схемы, сгруппированные по виду, служебные __-типы скрываем."""
        schema = request.introspect_types()

        if raw:
            self.stdout.write(json.dumps(schema, ensure_ascii=False, indent=2))
            return

        query_type = (schema.get('queryType') or {}).get('name')
        mutation_type = (schema.get('mutationType') or {}).get('name')
        self.stdout.write(
            f'Все запросы: recar_introspect {query_type}; '
            f'все мутации: recar_introspect {mutation_type}\n'
        )

        grouped = {}
        for type_data in schema.get('types') or []:
            name = type_data.get('name') or ''
            if name.startswith('__'):
                continue
            grouped.setdefault(type_data.get('kind'), []).append(name)

        for kind, names in sorted(grouped.items()):
            self.stdout.write(self.style.MIGRATE_HEADING(f'\n=== {kind} ({len(names)}) ==='))
            self.stdout.write(', '.join(sorted(names)))

    def _print_fields(self, title, fields):
        if not fields:
            return
        self.stdout.write(f'{title}:')
        for field in fields:
            self.stdout.write(f'  - {field["name"]}: {self._type_name(field.get("type"))}')

    def _type_name(self, type_data):
        """Разворачивает вложенные NON_NULL/LIST до имени типа."""
        if not type_data:
            return '?'
        name = type_data.get('name')
        if name:
            return name
        inner = self._type_name(type_data.get('ofType'))
        kind = type_data.get('kind')
        if kind == 'LIST':
            return f'[{inner}]'
        if kind == 'NON_NULL':
            return f'{inner}!'
        return inner
