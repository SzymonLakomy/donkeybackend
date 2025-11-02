from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, Iterable, List

from django.db.models import Sum

from .models import ContractType, Employee, PayrollRecord, WorkHourEntry


@dataclass
class PayrollSummary:
    employee_id: int
    employee_name: str
    contract_type: str
    hours_worked: Decimal
    expected_hours: int | None
    amount: Decimal
    note: str


def month_start(month: date) -> date:
    """Return the first day of the month for the provided date."""

    return month.replace(day=1)


def month_end(month: date) -> date:
    """Return the last day of the month for the provided date."""

    first_day = month_start(month)
    # Go to the next month and step back one day.
    next_month = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return next_month - timedelta(days=1)


def collect_hours(employees: Iterable[Employee], month: date) -> Dict[int, Decimal]:
    """Return a mapping employee_id -> hours worked in the selected month."""

    first_day = month_start(month)
    last_day = month_end(month)

    hours = (
        WorkHourEntry.objects.filter(
            employee__in=employees,
            work_date__gte=first_day,
            work_date__lte=last_day,
        )
        .values("employee")
        .annotate(total=Sum("hours"))
    )
    return {item["employee"]: item["total"] or Decimal("0") for item in hours}


def calculate_payroll(month: date) -> List[PayrollSummary]:
    """Prepare payroll summaries without touching the database."""

    employees = Employee.objects.select_related("contract").all()
    hour_totals = collect_hours(employees, month)

    results: List[PayrollSummary] = []
    for employee in employees:
        contract = getattr(employee, "contract", None)
        hours = hour_totals.get(employee.id, Decimal("0"))
        note = ""
        expected_hours = None
        amount = Decimal("0")

        if contract and contract.contract_type == ContractType.ZLECENIE:
            rate = contract.hourly_rate or Decimal("0")
            amount = (hours * rate).quantize(Decimal("0.01"))
            note = "Zlecenie rozliczane godzinowo."
        elif contract and contract.contract_type == ContractType.UOP:
            expected_hours = contract.expected_hours_per_month
            if expected_hours and hours < Decimal(expected_hours):
                note = "Poniżej normy godzin w miesiącu."
            elif expected_hours and hours > Decimal(expected_hours):
                note = "Przekroczono normę godzin."
            amount = contract.monthly_salary or Decimal("0")
        else:
            note = "Brak skonfigurowanej umowy."

        results.append(
            PayrollSummary(
                employee_id=employee.id,
                employee_name=employee.full_name,
                contract_type=employee.contract_type,
                hours_worked=hours,
                expected_hours=expected_hours,
                amount=amount,
                note=note,
            )
        )
    return results


def store_payroll(month: date) -> List[PayrollRecord]:
    """Persist payroll summaries for the provided month."""

    month = month_start(month)
    summaries = calculate_payroll(month)
    stored_records: List[PayrollRecord] = []
    for summary in summaries:
        payroll, _ = PayrollRecord.objects.update_or_create(
            employee_id=summary.employee_id,
            month=month,
            defaults={
                "hours_worked": summary.hours_worked,
                "amount": summary.amount,
            },
        )
        stored_records.append(payroll)
    return stored_records
