from django.urls import path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from users import views
from users.otp import views as otp_views

jwt_url = [
    path('token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('me/', views.UsersMe.as_view(), name='me'),
    path('otp/', otp_views.UserOTPView.as_view({'post': 'register'}), name='user_otp_register'),
    path('otp/token/', otp_views.UserOTPView.as_view({'post': 'verify'}), name='user_otp_verify')
]

user_url = [

    path('', views.UserViewSet.as_view(
        {
            'post': 'create',
            'get': 'list',
        })),

    path('<int:pk>/', views.UserViewSet.as_view(
        {
            'delete': 'destroy',
            # partial_update, а не update: PATCH вёл себя как полная замена и
            # требовал присылать `status` даже при правке одного поля
            'patch': 'partial_update',
            'get': 'retrieve'
        })),
    path('bulk-delete/', views.UserViewSet.as_view({"delete": "bulk_delete"})),
    path('roles/', views.UserViewSet.as_view({'get': 'roles'})),
    path('change-password/', views.UserViewSet.as_view({'post': 'change_password'})),
    # Смена номера — он же логин, поэтому в два шага с кодом из SMS
    path('phone-change/request/', views.UserViewSet.as_view({'post': 'phone_change_request'})),
    path('phone-change/confirm/', views.UserViewSet.as_view({'post': 'phone_change_confirm'})),
    path('reset-password/', views.UserViewSet.as_view({'post': 'reset_password'})),
    path('password-reset/request/', views.UserViewSet.as_view({'post': 'password_reset_request'})),
    path('password-reset/confirm/', views.UserViewSet.as_view({'post': 'password_reset_confirm'})),
    path('orders/', views.UserViewSet.as_view({'get': 'orders'})),
    path('orders/<int:pk>/cancel/', views.UserViewSet.as_view({'post': 'cancel'}))
]

urlpatterns = jwt_url + user_url
