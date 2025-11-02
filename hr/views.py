from __future__ import annotations

from datetime import date

from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Contract, Employee, LeaveRecord, PayrollRecord, Reminder, WorkHourEntry
from .serializers import (
    ContractSerializer,
    EmployeeSerializer,
    LeaveRecordSerializer,
    PayrollRecordSerializer,
    ReminderSerializer,
    WorkHourEntrySerializer,
)
from .services import PayrollSummary, calculate_payroll, store_payroll


class EmployeeViewSet(viewsets.ModelViewSet):
    queryset = Employee.objects.all().order_by("last_name")
    serializer_class = EmployeeSerializer


class ContractViewSet(viewsets.ModelViewSet):
    queryset = Contract.objects.select_related("employee").all()
    serializer_class = ContractSerializer


class LeaveRecordViewSet(viewsets.ModelViewSet):
    queryset = LeaveRecord.objects.select_related("employee").all()
    serializer_class = LeaveRecordSerializer


class WorkHourEntryViewSet(viewsets.ModelViewSet):
    queryset = WorkHourEntry.objects.select_related("employee").all()
    serializer_class = WorkHourEntrySerializer


class ReminderViewSet(viewsets.ModelViewSet):
    queryset = Reminder.objects.select_related("employee").all()
    serializer_class = ReminderSerializer

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        reminder = self.get_object()
        reminder.acknowledged = True
        reminder.save(update_fields=["acknowledged"])
        return Response({"status": "ok"})


class PayrollRecordViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PayrollRecord.objects.select_related("employee").all()
    serializer_class = PayrollRecordSerializer


class PayrollPreviewView(APIView):
    """Return payroll data without storing it in the database."""

    def get(self, request):
        month_param = request.query_params.get("month")
        if not month_param:
            return Response(
                {"detail": "Dodaj parametr month w formacie RRRR-MM."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            month_date = parse_month(month_param)
        except ValueError:
            return Response(
                {"detail": "Błędny format miesiąca. Użyj np. 2024-05."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        summaries = calculate_payroll(month_date)
        data = [summary_to_dict(summary) for summary in summaries]
        return Response(data)


class PayrollStoreView(APIView):
    """Calculate and store payroll data for the provided month."""

    def post(self, request):
        month_param = request.data.get("month")
        if not month_param:
            return Response(
                {"detail": "Dodaj pole month w formacie RRRR-MM."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            month_date = parse_month(month_param)
        except ValueError:
            return Response(
                {"detail": "Błędny format miesiąca. Użyj np. 2024-05."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        records = store_payroll(month_date)
        serializer = PayrollRecordSerializer(records, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


def parse_month(value: str) -> date:
    parsed = parse_date(f"{value}-01")
    if not parsed:
        raise ValueError("invalid month")
    return parsed.replace(day=1)


def summary_to_dict(summary: PayrollSummary) -> dict:
    return {
        "employee_id": summary.employee_id,
        "employee_name": summary.employee_name,
        "contract_type": summary.contract_type,
        "hours_worked": str(summary.hours_worked),
        "expected_hours": summary.expected_hours,
        "amount": str(summary.amount),
        "note": summary.note,
    }
