"""Prosty zestaw zadań Celery do przypomnień HR.

Plik jest napisany w możliwie czytelny sposób tak, aby junior mógł go łatwo prześledzić.
"""

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import ContractType, Employee, Reminder, ReminderType

REMINDER_WINDOW_DAYS = 14


def _should_send(reminder: Reminder) -> bool:
    """Nie wysyłamy ponownie jeżeli wiadomość została już wysłana."""

    return reminder.sent_at is None


def _send_email(reminder: Reminder) -> None:
    subject = f"Przypomnienie: {reminder.get_reminder_type_display()}"
    message = (
        f"Cześć {reminder.employee.full_name}!\n\n"
        f"{reminder.message}\n"
        f"Termin: {reminder.due_date}."
    )
    send_mail(
        subject,
        message,
        getattr(settings, "DEFAULT_FROM_EMAIL", "hr@example.com"),
        [reminder.employee.email],
        fail_silently=True,
    )


@shared_task
def generate_reminders() -> int:
    """Sprawdź terminy badań, szkoleń i dokumentów."""

    today = timezone.now().date()
    window_end = today + timedelta(days=REMINDER_WINDOW_DAYS)
    created = 0

    # Badania oraz BHP tylko dla UoP
    uop_employees = Employee.objects.filter(contract_type=ContractType.UOP)
    for employee in uop_employees:
        if employee.medical_check_valid_until and employee.medical_check_valid_until <= window_end:
            reminder, was_created = Reminder.objects.get_or_create(
                employee=employee,
                reminder_type=ReminderType.MEDICAL,
                due_date=employee.medical_check_valid_until,
                defaults={"message": "Badania okresowe wymagają odnowienia."},
            )
            if was_created:
                created += 1
        if employee.bhp_training_valid_until and employee.bhp_training_valid_until <= window_end:
            reminder, was_created = Reminder.objects.get_or_create(
                employee=employee,
                reminder_type=ReminderType.BHP,
                due_date=employee.bhp_training_valid_until,
                defaults={"message": "Szkolenie BHP zbliża się do końca ważności."},
            )
            if was_created:
                created += 1

    # Dokumenty dla zleceniobiorców
    contractors = Employee.objects.filter(contract_type=ContractType.ZLECENIE)
    for employee in contractors:
        for due_date, message in [
            (employee.id_card_valid_until, "Legitymacja traci ważność."),
            (employee.contractor_document_valid_until, "Dokumenty zleceniobiorcy wygasają."),
        ]:
            if due_date and due_date <= window_end:
                reminder, was_created = Reminder.objects.get_or_create(
                    employee=employee,
                    reminder_type=ReminderType.DOCUMENT,
                    due_date=due_date,
                    defaults={"message": message},
                )
                if was_created:
                    created += 1

    return created


@shared_task
def send_reminder_emails() -> int:
    """Wyślij e-maile dla świeżo utworzonych przypomnień."""

    count = 0
    for reminder in Reminder.objects.filter(sent_at__isnull=True):
        if not _should_send(reminder):
            continue
        _send_email(reminder)
        reminder.sent_at = timezone.now()
        reminder.save(update_fields=["sent_at"])
        count += 1
    return count
