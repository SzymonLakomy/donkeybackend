from types import SimpleNamespace
from datetime import date

from django.test import TestCase, override_settings

from accounts.models import Company, User
from schedule.models import DefaultDemand, CompanyLocation, Availability, Demand
from schedule.api import (
    _get_default_template,
    save_default_demand,
    save_default_demand_bulk,
    list_locations,
    create_location,
    get_default_demand_week,
    _ensure_schedule_for_demand,
)
from schedule.solver import run_solver
from schedule.schemas import (
    DemandShiftTemplateIn,
    DefaultDemandIn,
    DefaultDemandBulkIn,
    DefaultDemandDayIn,
    CompanyLocationIn,
)


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


@override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
class SolverResultTests(TestCase):
    def test_run_solver_provides_assignment_details_and_missing_segments(self):
        emp_availability = [
            {
                "employee_id": "1",
                "employee_name": "Jan",
                "date": "2025-01-01",
                "experienced": False,
                "hours_min": 0,
                "hours_max": 600,
                "available_slots": [{"start": "08:00", "end": "12:00"}],
            },
            {
                "employee_id": "2",
                "employee_name": "Ola",
                "date": "2025-01-01",
                "experienced": False,
                "hours_min": 0,
                "hours_max": 600,
                "available_slots": [{"start": "09:00", "end": "12:00"}],
            },
        ]
        demand = [
            {
                "date": "2025-01-01",
                "location": "Main",
                "start": "08:00",
                "end": "12:00",
                "demand": 2,
                "needs_experienced": False,
            }
        ]

        result = run_solver(emp_availability=emp_availability, demand=demand)

        # Ola jest dostępna od 09:00, więc pierwsza godzina zmiany zostaje nieobsadzona.
        self.assertEqual(len(result["assignments"]), 1)
        assignment = result["assignments"][0]
        self.assertEqual(assignment["missing_minutes"], 60)
        self.assertTrue(assignment["missing_segments"])
        missing = assignment["missing_segments"][0]
        self.assertEqual(missing["start"], "08:00")
        self.assertEqual(missing["end"], "09:00")
        self.assertEqual(missing["missing"], 1)

        details = {entry["employee_id"]: entry for entry in assignment["assigned_employees_detail"]}
        self.assertIn("1", details)
        self.assertEqual(details["1"]["start"], "08:00")
        self.assertEqual(details["1"]["end"], "12:00")
        self.assertEqual(details["1"]["minutes"], 240)
        self.assertTrue(details["1"]["segments"])
        self.assertEqual(details["2"]["start"], "09:00")
        self.assertEqual(details["2"]["end"], "12:00")
        self.assertEqual(details["2"]["minutes"], 180)

        self.assertTrue(result["uncovered"])
        uncovered = result["uncovered"][0]
        self.assertIn("missing_segments", uncovered)
        self.assertEqual(uncovered["missing_segments"][0]["end"], "09:00")


@override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
class ScheduleDetailPersistenceTests(TestCase):
    def test_generated_shifts_store_assignment_details(self):
        shift_date = date(2025, 1, 1)
        company = Company.objects.create(name="Test Co", code="TST00001")
        demand = Demand.objects.create(
            company=company,
            name="Test",
            raw_payload=[
                {
                    "date": shift_date.isoformat(),
                    "location": "Main",
                    "start": "08:00",
                    "end": "10:00",
                    "demand": 2,
                    "needs_experienced": False,
                }
            ],
            content_hash="persist-detail-test",
            date_from=shift_date,
            date_to=shift_date,
        )

        Availability.objects.create(
            company=company,
            employee_id="1",
            employee_name="Jan",
            date=shift_date,
            available_slots=[{"start": "08:00", "end": "10:00"}],
        )
        Availability.objects.create(
            company=company,
            employee_id="2",
            employee_name="Ola",
            date=shift_date,
            available_slots=[{"start": "09:00", "end": "10:00"}],
        )

        assignments, summary = _ensure_schedule_for_demand(demand, force=True)

        self.assertTrue(assignments)
        first = assignments[0]
        self.assertTrue(first["assigned_employees_detail"])
        self.assertTrue(first["missing_segments"])

        shift = demand.shifts.first()
        self.assertIsNotNone(shift)
        self.assertIn("assigned_employees_detail", shift.meta)
        self.assertIn("missing_segments", shift.meta)
        meta_details = {entry["employee_id"]: entry for entry in shift.meta["assigned_employees_detail"]}
        self.assertEqual(meta_details["1"]["employee_name"], "Jan")
        self.assertEqual(shift.meta["missing_segments"][0]["missing"], 1)


@override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
class SolverErrorHandlingTests(TestCase):
    """Kontrakt API: SolverError nie moze wyciec jako 500 (BACKLOG P1-3)."""

    def test_safe_run_solver_maps_solver_error_to_422(self):
        from unittest.mock import patch

        from ninja.errors import HttpError
        from schedule.api import _safe_run_solver
        from schedule.solver import SolverError

        with patch("schedule.api.run_solver", side_effect=SolverError("INFEASIBLE")):
            with self.assertRaises(HttpError) as ctx:
                _safe_run_solver([], [])

        self.assertEqual(ctx.exception.status_code, 422)


@override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
class ScheduleRegenerationTests(TestCase):
    """Regeneracja z `force=True` zapisuje metadane solvera i nie wywraca sie na `previous`."""

    def test_force_regeneration_records_solver_metadata(self):
        shift_date = date(2025, 1, 1)
        company = Company.objects.create(name="Regen Co", code="RGN00001")
        demand = Demand.objects.create(
            company=company,
            name="Regen",
            raw_payload=[
                {
                    "date": shift_date.isoformat(),
                    "location": "Main",
                    "start": "08:00",
                    "end": "12:00",
                    "demand": 1,
                    "needs_experienced": False,
                }
            ],
            content_hash="regeneration-metadata-test",
            date_from=shift_date,
            date_to=shift_date,
        )
        for emp in ("1", "2"):
            Availability.objects.create(
                company=company,
                employee_id=emp,
                employee_name=f"Pracownik {emp}",
                date=shift_date,
                available_slots=[{"start": "08:00", "end": "12:00"}],
            )

        _ensure_schedule_for_demand(demand, force=True)

        shift = demand.shifts.first()
        self.assertIsNotNone(shift)
        self.assertIn(shift.meta.get("solver_status"), {"OPTIMAL", "FEASIBLE"})
        self.assertIn("experience_violated", shift.meta)

        # Druga regeneracja czyta poprzedni grafik (stabilnosc) i musi zakonczyc sie poprawnie.
        assignments, summary = _ensure_schedule_for_demand(demand, force=True)

        self.assertTrue(assignments)
        self.assertIsNotNone(summary)
        self.assertIn("status", summary)
        self.assertEqual(len(assignments), 1)
        self.assertEqual(len(assignments[0]["assigned_employees"]), 1,
                         "popyt 1 powinien byc pokryty przez dokladnie jedna osobe")


@override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
class SolverValidationGateTests(TestCase):
    """Niespojny wynik solvera nie moze trafic do bazy (siatka bezpieczenstwa w API)."""

    def test_inconsistent_solver_output_is_rejected_and_not_persisted(self):
        from unittest.mock import patch

        from ninja.errors import HttpError
        from schedule.api import _ensure_schedule_for_demand

        shift_date = date(2025, 1, 1)
        company = Company.objects.create(name="Gate Co", code="GAT00001")
        demand = Demand.objects.create(
            company=company,
            name="Gate",
            raw_payload=[
                {
                    "date": shift_date.isoformat(),
                    "location": "Main",
                    "start": "08:00",
                    "end": "12:00",
                    "demand": 1,
                    "needs_experienced": False,
                }
            ],
            content_hash="validation-gate-test",
            date_from=shift_date,
            date_to=shift_date,
        )
        Availability.objects.create(
            company=company,
            employee_id="1",
            employee_name="Jan",
            date=shift_date,
            available_slots=[{"start": "08:00", "end": "12:00"}],
        )

        def _detail(emp):
            return {
                "employee_id": emp,
                "employee_name": emp,
                "start": "08:00",
                "end": "12:00",
                "minutes": 240,
                "segments": [{"start": "08:00", "end": "12:00", "minutes": 240}],
            }

        # Dwie osoby na zmiane z popytem 1 -> grafik niemozliwy do przyjecia.
        broken = {
            "assignments": [{
                "date": shift_date.isoformat(),
                "location": "Main",
                "start": "08:00",
                "end": "12:00",
                "demand": 1,
                "assigned_employees": ["1", "2"],
                "needs_experienced": False,
                "missing_minutes": 0,
                "assigned_employees_detail": [_detail("1"), _detail("2")],
                "missing_segments": [],
                "experience_violated": False,
            }],
            "uncovered": [],
            "hours_summary": [],
            "status": "OPTIMAL",
            "metrics": {},
        }

        with patch("schedule.api._safe_run_solver", return_value=broken):
            with self.assertRaises(HttpError) as ctx:
                _ensure_schedule_for_demand(demand, force=True)

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertFalse(demand.shifts.exists(), "niespojny grafik nie moze trafic do bazy")
