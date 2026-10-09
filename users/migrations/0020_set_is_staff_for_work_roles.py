from django.db import migrations

from users.enums import RoleEnum

# Роли сотрудников. «Гость» сюда не входит: эту роль получают клиенты при
# входе по SMS (users/otp/views.py), сотрудниками они не являются.
WORK_ROLES = [role.value for role in RoleEnum if role is not RoleEnum.GUEST]


def set_is_staff(apps, schema_editor):
    """Выставляет is_staff тем, у кого есть рабочая роль.

    Права сотрудника теперь требуют и роль, и is_staff (users.services.roles).
    До этого проверялась только роль, поэтому в базе накопились сотрудники с
    is_staff=False — без этой миграции они потеряли бы доступ.
    """
    User = apps.get_model('users', 'User')
    User.objects.filter(is_staff=False, roles__name__in=WORK_ROLES).distinct().update(is_staff=True)


def noop(apps, schema_editor):
    """Обратно не откатываем: кто именно был без is_staff, уже не восстановить."""


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0019_alter_user_status'),
    ]

    operations = [
        migrations.RunPython(set_is_staff, noop),
    ]
