from django.contrib import admin, messages
from django.core.exceptions import FieldError, ValidationError
from django.db.models import JSONField
from django.db.utils import DataError
from django_json_widget.widgets import JSONEditorWidget
from djangoql.admin import DjangoQLSearchMixin
from djangoql.exceptions import DjangoQLError
from djangoql.queryset import apply_search

from base.models import RecarRequestLog


class SafeDjangoQLSearchMixin(DjangoQLSearchMixin):
    """DjangoQL-поиск, не вызывающий queryset.explain().

    django-silk оборачивает каждый SELECT в свой EXPLAIN, а DjangoQL для
    проверки запроса вызывает .explain() — вместе это даёт
    «EXPLAIN EXPLAIN SELECT ...» и падение поиска в админке с
    ProgrammingError. Проверяем запрос обычной выборкой одной строки:
    смысл проверки (поймать ошибку сравнения inet в Postgres) сохраняется.
    """

    def get_search_results(self, request, queryset, search_term):
        if (
            self.search_mode_toggle_enabled() and
            not self.djangoql_search_enabled(request)
        ):
            return super().get_search_results(
                request=request,
                queryset=queryset,
                search_term=search_term,
            )

        use_distinct = False
        if not search_term:
            return queryset, use_distinct

        try:
            qs = apply_search(queryset, search_term, self.djangoql_schema)
        except (DjangoQLError, ValueError, FieldError, ValidationError) as e:
            messages.add_message(request, messages.WARNING, self.djangoql_error_message(e))
            return queryset.none(), use_distinct

        try:
            list(qs[:1])
        except DataError as e:
            if 'inet' not in str(e):
                raise
            messages.add_message(request, messages.WARNING, self.djangoql_error_message(e))
            qs = queryset.none()

        return qs, use_distinct


@admin.register(RecarRequestLog)
class RecarRequestLogAdmin(admin.ModelAdmin):
    """Только просмотр: строки пишет интеграция, руками их не создают и не правят."""

    list_display = ('created_at', 'operation_name', 'status_code', 'duration_ms', 'is_failed')
    list_filter = ('operation_name', 'status_code')
    search_fields = ('operation_name', 'error')
    date_hierarchy = 'created_at'
    readonly_fields = (
        'created_at', 'operation_name', 'status_code', 'duration_ms',
        'query', 'variables', 'response', 'error',
    )
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget},
    }

    @admin.display(description='Ошибка', boolean=True)
    def is_failed(self, obj: RecarRequestLog):
        return bool(obj.error) or (obj.status_code or 0) >= 400

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
