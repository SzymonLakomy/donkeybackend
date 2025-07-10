from django.urls import path
from .views import LoginView, RegisterUserView, RegisterCompanyView
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)


urlpatterns = [
    path("register-company", RegisterCompanyView.as_view(), name="register-company"),
    path("register",         RegisterUserView.as_view(),    name="register-user"),
    path("login",            LoginView.as_view(),           name="token_obtain_pair"),
    path("token/refresh", TokenRefreshView.as_view(), name="token_refresh"),
    path("token/pair", TokenObtainPairView.as_view(), name="token_obtain_pair"),


]
