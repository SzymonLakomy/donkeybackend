from types import SimpleNamespace

from django.test import TestCase, override_settings

from accounts.models import Company, User
from schedule.models import DefaultDemand, CompanyLocation
from schedule.api import (
    _get_default_template,
    save_default_demand,
    save_default_demand_bulk,
    list_locations,
    create_location,
    get_default_demand_week,
)
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
