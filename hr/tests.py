from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from . import celery_tasks
from .models import Contract, ContractType, Employee, Reminder, ReminderType, WorkHourEntry
from .services import calculate_payroll


class PayrollServiceTests(TestCase):
    def setUp(self):
        self.employee_uop = Employee.objects.create(
            first_name="Jan",
            last_name="Kowalski",
            email="jan@example.com",
            hire_date=date(2022, 1, 1),
            contract_type=ContractType.UOP,
        )
        Contract.objects.create(
            employee=self.employee_uop,
            start_date=date(2022, 1, 1),
            contract_type=ContractType.UOP,
            monthly_salary=Decimal("5000.00"),
            expected_hours_per_month=160,
        )

        self.employee_zlec = Employee.objects.create(
            first_name="Anna",
            last_name="Nowak",
            email="anna@example.com",
            hire_date=date(2023, 3, 1),
            contract_type=ContractType.ZLECENIE,
        )
        Contract.objects.create(
            employee=self.employee_zlec,
            start_date=date(2023, 3, 1),
            contract_type=ContractType.ZLECENIE,
            hourly_rate=Decimal("30.00"),
        )

    def test_calculate_payroll_handles_both_contract_types(self):
        WorkHourEntry.objects.create(
            employee=self.employee_uop,
            work_date=date(2024, 5, 2),
            hours=Decimal("150"),
        )
        WorkHourEntry.objects.create(
            employee=self.employee_zlec,
            work_date=date(2024, 5, 2),
            hours=Decimal("40"),
        )

        summaries = calculate_payroll(date(2024, 5, 1))
        self.assertEqual(len(summaries), 2)

        uop_summary = next(item for item in summaries if item.employee_id == self.employee_uop.id)
        self.assertEqual(uop_summary.amount, Decimal("5000.00"))
        self.assertIn("Poniżej", uop_summary.note)

        zlec_summary = next(item for item in summaries if item.employee_id == self.employee_zlec.id)
        self.assertEqual(zlec_summary.amount, Decimal("1200.00"))
        self.assertIn("Zlecenie", zlec_summary.note)


class ReminderTasksTests(TestCase):
    def test_generate_reminders_creates_entries(self):
        employee = Employee.objects.create(
            first_name="Piotr",
            last_name="Lis",
            email="piotr@example.com",
            hire_date=date(2021, 6, 1),
            contract_type=ContractType.UOP,
            medical_check_valid_until=date.today() + timedelta(days=5),
            bhp_training_valid_until=date.today() + timedelta(days=5),
        )

        created = celery_tasks.generate_reminders()
        self.assertEqual(created, 2)
        self.assertEqual(Reminder.objects.count(), 2)

        celery_tasks.send_reminder_emails()
        self.assertEqual(Reminder.objects.filter(sent_at__isnull=False).count(), 2)
        self.assertTrue(all(reminder.sent_at for reminder in Reminder.objects.all()))

    def test_generate_reminders_for_contractors(self):
        employee = Employee.objects.create(
            first_name="Ewa",
            last_name="Kruk",
            email="ewa@example.com",
            hire_date=date(2024, 1, 1),
            contract_type=ContractType.ZLECENIE,
            id_card_valid_until=date.today() + timedelta(days=2),
            contractor_document_valid_until=date.today() + timedelta(days=3),
        )

        created = celery_tasks.generate_reminders()
        self.assertEqual(created, 2)
        reminders = Reminder.objects.filter(employee=employee, reminder_type=ReminderType.DOCUMENT)
        self.assertEqual(reminders.count(), 2)
