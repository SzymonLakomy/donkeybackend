from __future__ import annotations

from datetime import date

from django.core.validators import MinValueValidator
from django.db import models


class ContractType(models.TextChoices):
    UOP = "uop", "Umowa o pracę"
    ZLECENIE = "zlecenie", "Umowa zlecenie"


class Employee(models.Model):
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    email = models.EmailField(unique=True)
    hire_date = models.DateField()
    contract_type = models.CharField(
        max_length=20,
        choices=ContractType.choices,
    )
    position = models.CharField(max_length=120, blank=True)

    # Medical and training validity (required for UoP contracts)
    medical_check_valid_until = models.DateField(null=True, blank=True)
    bhp_training_valid_until = models.DateField(null=True, blank=True)

    # Documents for contractors (zlecenie)
    id_card_valid_until = models.DateField(null=True, blank=True)
    contractor_document_valid_until = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Contract(models.Model):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="contract")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    contract_type = models.CharField(
        max_length=20,
        choices=ContractType.choices,
    )

    # Payroll info
    monthly_salary = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        null=True,
        blank=True,
        help_text="Wymagane dla umów o pracę.",
    )
    hourly_rate = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        null=True,
        blank=True,
        help_text="Wymagane dla umów zlecenie.",
    )
    expected_hours_per_month = models.PositiveIntegerField(
        default=160,
        help_text="Liczba godzin kontrolowanych dla umowy o pracę.",
    )

    class Meta:
        verbose_name = "Contract"
        verbose_name_plural = "Contracts"

    def clean(self) -> None:
        super().clean()
        if self.contract_type == ContractType.UOP and self.monthly_salary is None:
            raise models.ValidationError("Dla umowy o pracę podaj miesięczne wynagrodzenie.")
        if self.contract_type == ContractType.ZLECENIE and self.hourly_rate is None:
            raise models.ValidationError("Dla umowy zlecenie podaj stawkę godzinową.")

    def __str__(self) -> str:
        return f"{self.employee.full_name} - {self.get_contract_type_display()}"


class LeaveType(models.TextChoices):
    VACATION = "vacation", "Urlop wypoczynkowy"
    SICK_LEAVE = "sick", "Zwolnienie lekarskie"


class LeaveRecord(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leaves")
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self) -> str:
        return f"{self.employee.full_name} ({self.get_leave_type_display()})"

    @property
    def days_taken(self) -> int:
        return (self.end_date - self.start_date).days + 1


class WorkHourEntry(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="work_hours")
    work_date = models.DateField()
    hours = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0)])

    class Meta:
        ordering = ["-work_date"]
        unique_together = ("employee", "work_date")

    def __str__(self) -> str:
        return f"{self.employee.full_name} - {self.work_date}: {self.hours}h"


class ReminderType(models.TextChoices):
    MEDICAL = "medical", "Badania okresowe"
    BHP = "bhp", "Szkolenie BHP"
    DOCUMENT = "document", "Wygasający dokument"


class Reminder(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="reminders")
    reminder_type = models.CharField(max_length=20, choices=ReminderType.choices)
    message = models.CharField(max_length=255)
    due_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    acknowledged = models.BooleanField(default=False)

    class Meta:
        ordering = ["due_date", "reminder_type"]
        unique_together = ("employee", "reminder_type", "due_date")

    def __str__(self) -> str:
        return f"{self.employee.full_name} - {self.get_reminder_type_display()}"

    @property
    def is_overdue(self) -> bool:
        return date.today() > self.due_date


class PayrollRecord(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="payrolls")
    month = models.DateField(help_text="Pierwszy dzień miesiąca rozliczeniowego")
    hours_worked = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(0)])
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-month", "employee__last_name"]
        unique_together = ("employee", "month")

    def __str__(self) -> str:
        month_display = self.month.strftime("%Y-%m")
        return f"{self.employee.full_name} - {month_display}"
