from types import SimpleNamespace

from django.test import TestCase, override_settings
from django.core import mail
from unittest.mock import patch
from django.utils import timezone

from accounts.models import Company, User
from schedule.models import (
    DefaultDemand,
    CompanyLocation,
    ScheduleShift,
    ShiftTransferRequest,
)
from schedule.api import (
    _get_default_template,
    save_default_demand,
    save_default_demand_bulk,
    list_locations,
    create_location,
    get_default_demand_week,
    auto_generate_schedule,
    approve_shift,
    create_shift_transfer_request,
    approve_shift_transfer,
    list_employee_roles,
    create_employee_role,
    assign_employee_role,
    list_role_assignments,
)
from schedule.schemas import (
    DemandShiftTemplateIn,
    DefaultDemandIn,
    DefaultDemandBulkIn,
    DefaultDemandDayIn,
    CompanyLocationIn,
    AutoGenerateIn,
    ShiftApproveIn,
    ShiftTransferRequestIn,
    ShiftTransferModerateIn,
    EmployeeRoleIn,
    EmployeeRoleAssignmentIn,
)
from schedule.models import Demand


@override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
class DefaultDemandTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme", code="ACME1234")
        self.user = User.objects.create_user(
            email="owner@acme.test",
            password="secret",
            first_name="Owner",
            last_name="User",
            role="owner",
            company=self.company,
        )
        self.request = SimpleNamespace(user=self.user, auth=None)

    def test_save_default_demand_stores_weekday(self):
        payload = DefaultDemandIn(
            location="Main",
            weekday=2,
            items=[DemandShiftTemplateIn(start="8", end="12", demand=2, needs_experienced=True)],
        )

        response = save_default_demand(self.request, payload)

        self.assertEqual(DefaultDemand.objects.count(), 1)
        stored = DefaultDemand.objects.first()
        self.assertEqual(stored.company, self.company)
        self.assertEqual(stored.weekday, 2)
        self.assertEqual(stored.items[0]["start"], "08:00")
        self.assertTrue(CompanyLocation.objects.filter(company=self.company, name="Main").exists())
        self.assertEqual(response["defaults"][0]["weekday"], 2)
        self.assertEqual(response["defaults"][0]["items"][0]["demand"], 2)

    def test_get_default_template_prefers_weekday(self):
        CompanyLocation.objects.create(company=self.company, name="HQ")
        DefaultDemand.objects.create(company=self.company, location="HQ", weekday=None, items=[{"start": "09:00", "end": "17:00", "demand": 1, "needs_experienced": False}])
        DefaultDemand.objects.create(company=self.company, location="HQ", weekday=0, items=[{"start": "06:00", "end": "14:00", "demand": 2, "needs_experienced": True}])

        monday_template = _get_default_template(self.company, "HQ", 0)
        tuesday_template = _get_default_template(self.company, "HQ", 1)

        self.assertEqual(monday_template[0]["start"], "06:00")
        self.assertEqual(monday_template[0]["demand"], 2)
        self.assertEqual(tuesday_template[0]["start"], "09:00")
        self.assertEqual(tuesday_template[0]["demand"], 1)

    def test_save_default_demand_bulk_creates_multiple_days(self):
        payload = DefaultDemandBulkIn(
            location="Warehouse",
            defaults=[
                DefaultDemandDayIn(
                    weekday=0,
                    items=[
                        DemandShiftTemplateIn(start="07:00", end="11:00", demand=1),
                        DemandShiftTemplateIn(start="12:00", end="16:00", demand=2),
                    ],
                ),
                DefaultDemandDayIn(
                    weekday=1,
                    items=[DemandShiftTemplateIn(start="10:00", end="14:00", demand=3)],
                ),
            ],
        )

        response = save_default_demand_bulk(self.request, payload)

        defaults = {entry["weekday"]: entry for entry in response["defaults"]}
        self.assertIn(0, defaults)
        self.assertEqual(len(defaults[0]["items"]), 2)
        self.assertIn(1, defaults)
        self.assertEqual(defaults[1]["items"][0]["demand"], 3)
        self.assertEqual(DefaultDemand.objects.filter(company=self.company, location="Warehouse").count(), 2)

    def test_list_locations_returns_only_company_entries(self):
        own = CompanyLocation.objects.create(company=self.company, name="Main")
        other_company = Company.objects.create(name="Other", code="OTHER001")
        CompanyLocation.objects.create(company=other_company, name="Shared")

        payload = list_locations(self.request)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["id"], own.id)
        self.assertEqual(payload[0]["name"], "Main")

    def test_create_location_creates_entry_for_company(self):
        payload = CompanyLocationIn(name="New Spot")

        response = create_location(self.request, payload)

        self.assertTrue(
            CompanyLocation.objects.filter(company=self.company, name="New Spot").exists()
        )
        self.assertEqual(response["name"], "New Spot")
        self.assertIn("created_at", response)

    def test_get_default_demand_week_returns_full_week(self):
        CompanyLocation.objects.create(company=self.company, name="HQ")
        DefaultDemand.objects.create(
            company=self.company,
            location="HQ",
            weekday=None,
            items=[{"start": "08:00", "end": "16:00", "demand": 2, "needs_experienced": False}],
        )
        DefaultDemand.objects.create(
            company=self.company,
            location="HQ",
            weekday=2,
            items=[{"start": "10:00", "end": "18:00", "demand": 5, "needs_experienced": True}],
        )

        response = get_default_demand_week(self.request, location="HQ")

        self.assertEqual(len(response["defaults"]), 7)
        self.assertEqual([entry["weekday"] for entry in response["defaults"]], list(range(7)))

        defaults = {entry["weekday"]: entry for entry in response["defaults"]}
        self.assertFalse(defaults[2]["inherited"])
        self.assertEqual(defaults[2]["items"][0]["demand"], 5)
        self.assertTrue(defaults[3]["inherited"])
        self.assertEqual(defaults[3]["items"][0]["demand"], 2)


@override_settings(
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@test.local",
)
class ScheduleWorkflowTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme", code="ACME1234")
        self.manager = User.objects.create_user(
            email="manager@acme.test",
            password="secret",
            first_name="Manager",
            last_name="User",
            role="manager",
            company=self.company,
        )
        self.employee = User.objects.create_user(
            email="employee@acme.test",
            password="secret",
            first_name="Employee",
            last_name="User",
            role="employee",
            company=self.company,
        )
        self.request = SimpleNamespace(user=self.manager, auth=None)
        self.employee_request = SimpleNamespace(user=self.employee, auth=None)
        self.location = CompanyLocation.objects.create(company=self.company, name="HQ")
        DefaultDemand.objects.create(
            company=self.company,
            location="HQ",
            weekday=None,
            items=[{"start": "08:00", "end": "12:00", "demand": 1, "needs_experienced": False}],
        )
        mail.outbox.clear()

    @patch("schedule.api.run_solver")
    def test_auto_generate_creates_shifts_and_sends_notifications(self, mock_solver):
        mock_solver.return_value = {
            "assignments": [
                {
                    "date": "2025-01-06",
                    "location": "HQ",
                    "start": "08:00",
                    "end": "12:00",
                    "demand": 1,
                    "needs_experienced": False,
                    "missing_minutes": 0,
                    "assigned_employees": ["employee@acme.test"],
                }
            ],
            "uncovered": [],
            "hours_summary": [],
        }

        payload = AutoGenerateIn(date_from="2025-01-06", date_to="2025-01-06", location="HQ", persist=True)
        response = auto_generate_schedule(self.request, payload)

        self.assertEqual(len(response["assignments"]), 1)
        shift = ScheduleShift.objects.get()
        self.assertEqual(shift.assigned_employees, ["employee@acme.test"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("employee@acme.test", mail.outbox[0].to)

    def test_manager_can_approve_shift(self):
        demand = Demand.objects.create(
            name="Test",
            raw_payload=[],
            content_hash="hash",
            date_from=timezone.now().date(),
            date_to=timezone.now().date(),
        )
        shift = ScheduleShift.objects.create(
            demand=demand,
            shift_uid="uid-1",
            date=timezone.now().date(),
            location="HQ",
            start="08:00",
            end="12:00",
            demand_count=1,
            assigned_employees=["employee@acme.test"],
        )

        payload = ShiftApproveIn(note="OK")
        response = approve_shift(self.request, shift.shift_uid, payload)

        shift.refresh_from_db()
        self.assertTrue(shift.confirmed)
        self.assertEqual(shift.approved_by, self.manager)
        self.assertIsNotNone(shift.approved_at)
        self.assertTrue(response["confirmed"])

    def test_shift_transfer_flow(self):
        demand = Demand.objects.create(
            name="Transfer",
            raw_payload=[],
            content_hash="hash2",
            date_from=timezone.now().date(),
            date_to=timezone.now().date(),
        )
        shift = ScheduleShift.objects.create(
            demand=demand,
            shift_uid="uid-2",
            date=timezone.now().date(),
            location="HQ",
            start="08:00",
            end="12:00",
            demand_count=1,
            assigned_employees=[str(self.employee.id)],
        )

        request_payload = ShiftTransferRequestIn(shift_id=shift.shift_uid, action="drop", note="Need day off")
        transfer = create_shift_transfer_request(self.employee_request, request_payload)
        self.assertEqual(transfer["status"], ShiftTransferRequest.STATUS_PENDING)

        approval_payload = ShiftTransferModerateIn(manager_note="OK")
        approved = approve_shift_transfer(self.request, transfer["id"], approval_payload)
        self.assertEqual(approved["status"], ShiftTransferRequest.STATUS_APPROVED)
        shift.refresh_from_db()
        self.assertEqual(shift.assigned_employees, [])

    def test_roles_management(self):
        create_payload = EmployeeRoleIn(name="Barista", requires_experience=True, description="Coffee expert")
        role = create_employee_role(self.request, create_payload)
        self.assertEqual(role["name"], "Barista")
        roles = list_employee_roles(self.request)
        self.assertEqual(len(roles), 1)

        assign_payload = EmployeeRoleAssignmentIn(
            role_id=role["id"],
            user_id=self.employee.id,
            notes="Starter",
            active=True,
        )
        assignment = assign_employee_role(self.request, assign_payload)
        self.assertTrue(assignment["active"])

        assignments = list_role_assignments(self.request)
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["role_name"], "Barista")
