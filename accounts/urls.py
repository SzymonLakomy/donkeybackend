from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    LoginView, RegisterUserView, RegisterCompanyView, 
    CompanyCodeView, CompanyCodeResetView,
    PositionViewSet, CompanyUserListView, CompanyUserDetailView,
    WorkplaceConfigView, AttendanceEventView, AttendanceStatusView
)
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

# Router dla ViewSetów
router = DefaultRouter()
router.register(r'positions', PositionViewSet, basename='position')

urlpatterns = [
    path("register-company", RegisterCompanyView.as_view(), name="register-company"),
    path("register",         RegisterUserView.as_view(),    name="register-user"),
    path("login",            LoginView.as_view(),           name="token_obtain_pair"),
    path("token/refresh", TokenRefreshView.as_view(), name="token_refresh"),
    path("token/pair", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("companycode/", CompanyCodeView.as_view(), name="company-code"),
    path("companycode/reset/", CompanyCodeResetView.as_view(), name="company-code-reset"),
    
    # Endpoints dla menedżera
    path("", include(router.urls)),
    path("employees/", CompanyUserListView.as_view(), name="company-users-list"),
    path("employees/<int:pk>/", CompanyUserDetailView.as_view(), name="company-user-detail"),

    # Attendance endpoints
    path("workplace/config/", WorkplaceConfigView.as_view(), name="workplace-config"),
    path("attendance/event/", AttendanceEventView.as_view(), name="attendance-event"),
    path("attendance/status/", AttendanceStatusView.as_view(), name="attendance-status"),
]
