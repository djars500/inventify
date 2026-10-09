from rest_framework.permissions import BasePermission

from users.enums import RoleEnum
from users.services.roles import has_role, is_real_user


class IsManager(BasePermission):
    """
    Разрешение для Менеджера компании
    """

    allowed_roles = [RoleEnum.DEPARTMENT_DIRECTOR.value, RoleEnum.DIRECTOR.value]

    def has_permission(self, request, view):
        return has_role(request.user, *self.allowed_roles)


class IsDirector(BasePermission):
    """
    Разрешение только для Директора.
    """

    allowed_roles = [RoleEnum.DIRECTOR.value]

    def has_permission(self, request, view):
        return has_role(request.user, *self.allowed_roles)


class IsStaff(BasePermission):
    """
        Разрешение только для сотрудников.
    """

    allowed_roles = [role.value for role in (RoleEnum)]

    def has_permission(self, request, view):
        return has_role(request.user, *self.allowed_roles)


class InventifyAPIPermission(BasePermission):
    """
    Дает доступ только сотрудникам (через IsStaff) для URL, начинающихся с /api/admin.
    Остальные URL доступны для всех.
    """
    def has_permission(self, request, view):
        # Проверяем, начинается ли URL с /api/admin
        if request.path.startswith('/api/admin'):
            user = request.user
            if not is_real_user(user):
                return False
            # Доступ в админку — только сотрудникам (is_staff) и суперпользователям
            return bool(user.is_superuser or user.is_staff)
        # Для всех остальных URL доступ разрешен
        return True
