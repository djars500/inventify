"""Проверка ролей пользователя.

Вынесено в одно место, потому что `user.roles` есть только у настоящего
пользователя: у `AnonymousUser` такого атрибута нет, и прямое обращение
роняло запрос с 500 `'AnonymousUser' object has no attribute 'roles'`.
Из-за этого, в частности, не работала регистрация — аноним доходил до
проверки роли директора в UserRegisterSerializer.
"""

from users.enums import RoleEnum

# Роли, которым можно править чужие учётные записи
MANAGEMENT_ROLES = (RoleEnum.DIRECTOR.value, RoleEnum.DEPARTMENT_DIRECTOR.value)


def is_real_user(user) -> bool:
    """True, если это аутентифицированный пользователь, а не аноним."""
    return bool(user and getattr(user, 'is_authenticated', False))


def is_staff_member(user) -> bool:
    """Сотрудник: признак is_staff либо суперпользователь.

    Роли и is_staff в базе живут независимо, и раньше права сотрудника
    выдавались по одной роли. Из-за этого роль «Гость» (а она входит в
    список разрешённых у IsStaff) открывала доступ к служебным эндпоинтам,
    а руководители без is_staff правили чужие контакты, хотя в админку их
    не пускало. Теперь любое право сотрудника требует обоих условий.
    """
    return is_real_user(user) and bool(user.is_superuser or user.is_staff)


def has_role(user, *role_names) -> bool:
    """Есть ли у сотрудника хотя бы одна из перечисленных ролей.

    Суперпользователь считается обладателем любой роли. Аноним и клиент без
    признака сотрудника — никакой.
    """
    if not is_staff_member(user):
        return False
    if user.is_superuser:
        return True
    if not role_names:
        return False
    return user.roles.filter(name__in=role_names).exists()


def is_director(user) -> bool:
    return has_role(user, RoleEnum.DIRECTOR.value)


def is_management(user) -> bool:
    """Директор или руководитель отдела — те, кто правит чужие учётные записи."""
    return has_role(user, *MANAGEMENT_ROLES)
