import logging

from django.contrib.auth.password_validation import validate_password
from django.db.models import Q
from django.core.exceptions import ValidationError as DjangoValidationError
from django_filters.rest_framework import DjangoFilterBackend
from drf_yasg.utils import swagger_auto_schema
from rest_framework import permissions
from rest_framework import status
from rest_framework import viewsets
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.order.actions import OrderAction
from apps.order.models import Order
from apps.order.serializers import OrderSerializer
from base.enums import StatusEnum
from inventify.permissions import IsDirector
from users.services.roles import is_management, is_real_user
from users import serializers
from users.actions import CreateUserAction
from users.filters import UserFilter
from users.models.User import User, Role
from users.otp.actions import GetStatusUserCodeAction
from users.otp.enums import SmsStatus
from users.serializers import (
    ChangePasswordSerializer,
    PhoneChangeRequestSerializer,
    PhoneChangeConfirmSerializer,
    ResetPasswordRequestSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
)
from users.services.change_phone import PhoneChangeService
from users.services.reset_password import ResetPasswordService, SmsPasswordResetService


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by('id')
    serializer_class = serializers.UserSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = UserFilter

    def get_queryset(self):
        qs = User.objects.all().order_by('id')
        user = self.request.user
        if is_management(user):
            return qs

        # Обычный сотрудник видит клиентов и себя, но не других сотрудников.
        # Себя — обязательно: иначе он не может открыть и поправить даже свой
        # профиль, запрос упирался в 404.
        if is_real_user(user):
            return qs.filter(Q(is_staff=False) | Q(pk=user.pk))

        return qs.filter(is_staff=False)

    def get_permissions(self):
        """
        Добавляем кастомные разрешения для действий, таких как удаление.
        """
        if self.request.method == 'DELETE':
            return [IsDirector()]
        if self.action in ('orders', 'phone_change_request', 'phone_change_confirm'):
            return [IsAuthenticated()]
        return super().get_permissions()

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = serializers.UserUpdateSerializer(instance, data=request.data, partial=partial,
                                                      context={'request': request})
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}
        response_data = self.serializer_class(self.get_object()).data
        return Response(response_data)

    def create(self, request, *args, **kwargs):
        serializer = serializers.UserRegisterSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = CreateUserAction(data=serializer.validated_data).run()
        response_data = self.serializer_class(user).data
        headers = self.get_success_headers(response_data)
        return Response(response_data, status=status.HTTP_201_CREATED, headers=headers)

    def roles(self, request, *args, **kwargs):
        return Response(Role.objects.all().values('id', 'name'), status=status.HTTP_200_OK)

    def orders(self, request, *args, **kwargs):
        self.check_permissions(request)

        instance = request.user
        orders = Order.objects.filter(user=instance).order_by('-created_at')
        page = self.paginate_queryset(orders)
        if page is not None:
            serializer = OrderSerializer(page, many=True, context={"request": request})
            return self.get_paginated_response(serializer.data)

        serializer = OrderSerializer(orders, many=True)
        return Response(serializer.data)

    def cancel(self, request, *args, **kwargs):
        self.check_permissions(request)
        instance = request.user
        order = get_object_or_404(Order, user=instance, id=self.kwargs.get('pk'))
        OrderAction().delete(order)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def bulk_delete(self, request):
        """ Массовое удаление пользователей """
        self.check_permissions(request)

        user_ids = request.data.get("ids", [])
        users = User.objects.filter(id__in=user_ids, status=StatusEnum.ACTIVE.value)
        if users.exists() is False:
            return Response({"error": "Не переданы ID пользователей или они были удалены"}, status=status.HTTP_400_BAD_REQUEST)

        deleted_count = users.update(status=StatusEnum.DELETED.value)
        return Response({"deleted": deleted_count}, status=status.HTTP_204_NO_CONTENT)

    @swagger_auto_schema(request_body=PhoneChangeRequestSerializer,
                         operation_id='Смена номера: запрос кода',
                         tags=['Клиент/Пользователи'],
                         )
    def phone_change_request(self, request):
        """Отправляет код подтверждения на новый номер."""
        self.check_permissions(request)

        serializer = PhoneChangeRequestSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        new_phone = serializer.validated_data['phone']
        try:
            PhoneChangeService.send_code(request.user, new_phone)
        except Exception as e:
            logging.exception(e)
            return Response({'error': 'Не удалось отправить код, попробуйте позже'},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response({'message': f'Код отправлен на {new_phone}'})

    @swagger_auto_schema(request_body=PhoneChangeConfirmSerializer,
                         operation_id='Смена номера: подтверждение кода',
                         tags=['Клиент/Пользователи'],
                         )
    def phone_change_confirm(self, request):
        """Подтверждает код и сохраняет новый номер — он же логин."""
        self.check_permissions(request)

        serializer = PhoneChangeConfirmSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        PhoneChangeService.confirm(
            request.user,
            serializer.validated_data['phone'],
            serializer.validated_data['otp'],
        )
        return Response(self.serializer_class(request.user, context={'request': request}).data)

    @swagger_auto_schema(request_body=ChangePasswordSerializer,
                         operation_id='Смена пароля',
                         tags=['Админ/Пользователи', 'Клиент/Пользователи'],
                         )
    def change_password(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        old_password = serializer.validated_data["old_password"]
        new_password = serializer.validated_data["new_password"]

        if not user.check_password(old_password):
            return Response({"error": "Неправильный пароль"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as e:
            return Response({"error": e.messages}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new_password)
        user.save()

        return Response({"message": "Пароль успешно изменен"})

    @swagger_auto_schema(request_body=ResetPasswordRequestSerializer,
                         operation_id='Сброс пароля на email (сотрудники)',
                         tags=['Админ/Пользователи'],
                         )
    def reset_password(self, request):
        serializer = ResetPasswordRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone = serializer.validated_data["phone"]
        user = User.objects.filter(phone=phone).first()
        if user is None:
            return Response({"error": "Пользователь не найден"}, status=status.HTTP_404_NOT_FOUND)
        if not user.email:
            return Response({"error": "У пользователя не указан email"},
                            status=status.HTTP_400_BAD_REQUEST)

        ResetPasswordService.reset_random_and_email(user)
        return Response({"message": "Новый пароль отправлен на email"})

    @swagger_auto_schema(request_body=PasswordResetRequestSerializer,
                         operation_id='Сброс пароля по SMS: запрос кода (клиенты)',
                         tags=['Клиент/Пользователи'],
                         )
    def password_reset_request(self, request):
        """Клиент: запрос SMS-кода для сброса пароля."""
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone = serializer.validated_data["phone"]
        user = User.objects.filter(phone=phone).first()
        if user is not None:
            try:
                SmsPasswordResetService.send_code(user)
            except Exception as e:
                logging.exception(e)

        # Нейтральный ответ — не раскрываем, зарегистрирован ли номер
        return Response({"message": "Если номер зарегистрирован, на него отправлен код"})

    @swagger_auto_schema(request_body=PasswordResetConfirmSerializer,
                         operation_id='Сброс пароля по SMS: подтверждение (клиенты)',
                         tags=['Клиент/Пользователи'],
                         )
    def password_reset_confirm(self, request):
        """Клиент: подтверждение сброса пароля по SMS-коду."""
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone = serializer.validated_data["phone"]
        otp = serializer.validated_data["otp"]
        new_password = serializer.validated_data["new_password"]

        invalid_code_response = Response(
            {"detail": "Неправильный код, пожалуйста попробуйте еще"},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

        user = User.objects.filter(phone=phone).first()
        if user is None:
            return invalid_code_response

        code_status = GetStatusUserCodeAction.run(user=user, otp=otp)
        if code_status == SmsStatus.SUCCESS:
            try:
                validate_password(new_password, user=user)
            except DjangoValidationError as e:
                return Response({"error": e.messages}, status=status.HTTP_400_BAD_REQUEST)
            SmsPasswordResetService.confirm(user, new_password)
            return Response({"message": "Пароль успешно изменён"})
        elif code_status == SmsStatus.TIMEOUT:
            return Response({"detail": "Время кода истекло"},
                            status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        return invalid_code_response


class UsersMe(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @staticmethod
    def get(request, *args, **kwargs):
        user = request.user
        serializer = serializers.UserSerializer(user)
        return Response(serializer.data, status=status.HTTP_200_OK)
