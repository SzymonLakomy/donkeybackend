from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ContractViewSet,
    EmployeeViewSet,
    LeaveRecordViewSet,
    PayrollPreviewView,
    PayrollRecordViewSet,
    PayrollStoreView,
    ReminderViewSet,
    WorkHourEntryViewSet,
)

router = DefaultRouter()
router.register(r"employees", EmployeeViewSet)
router.register(r"contracts", ContractViewSet)
router.register(r"leaves", LeaveRecordViewSet)
router.register(r"work-hours", WorkHourEntryViewSet)
router.register(r"reminders", ReminderViewSet)
router.register(r"payroll-records", PayrollRecordViewSet, basename="payroll-record")

urlpatterns = [
    path("", include(router.urls)),
    path("payroll/preview/", PayrollPreviewView.as_view(), name="payroll-preview"),
    path("payroll/store/", PayrollStoreView.as_view(), name="payroll-store"),
]
