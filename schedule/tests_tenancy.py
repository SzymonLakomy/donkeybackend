"""Izolacja danych po firmie (BACKLOG P0-3): dwie firmy nie moga widziec swoich grafikow.

Testy ida po HTTP, bo to w endpointach sa filtry - jesli ktos usunie filtr, test padnie.
"""
import json
from datetime import date, timedelta

from django.test import TestCase, override_settings

LOCATION = "Main"
START = date(2026, 11, 2)
DAYS = [(START + timedelta(days=i)).isoformat() for i in range(7)]


@override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
class TenantIsolationTests(TestCase):
    def _post(self, path, payload, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(
            path, data=json.dumps(payload), content_type="application/json", **headers
        )

    def _get(self, path, token):
        return self.client.get(path, HTTP_AUTHORIZATION=f"Bearer {token}")

    def _register(self, company_name, email):
        response = self._post("/api/accounts/register-company", {
            "company_name": company_name,
            "nip": "1234567890",
            "email": email,
            "first_name": "Owner",
            "last_name": company_name,
            "password": "haslo-testowe-123",
        })
        self.assertEqual(response.status_code, 201, response.content[:300])

        response = self._post("/api/accounts/login", {"email": email, "password": "haslo-testowe-123"})
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        return {"access": body["access"], "user_id": str(body["user"]["id"])}

    def _prepare_tenant(self, owner):
        token = owner["access"]
        self.assertEqual(self._post("/api/schedule/locations", {"name": LOCATION}, token).status_code, 200)

        response = self._post("/api/schedule/availability/bulk", {
            "employee_id": owner["user_id"],
            "employee_name": "Owner",
            "experienced": True,
            "hours_min": 0,
            "hours_max": 60,
            "availabilities": [
                {"date": day, "available_slots": {"start": "08:00", "end": "20:00"}} for day in DAYS
            ],
        }, token)
        self.assertEqual(response.status_code, 200, response.content[:300])

        response = self._post("/api/schedule/demand/default/bulk", {
            "location": LOCATION,
            "defaults": [{
                "weekday": weekday,
                "items": [{"start": "10:00", "end": "14:00", "demand": 1, "needs_experienced": False}],
            } for weekday in range(7)],
        }, token)
        self.assertEqual(response.status_code, 200, response.content[:300])

        response = self._post("/api/schedule/generate-range", {
            "date_from": DAYS[0],
            "date_to": DAYS[-1],
            "location": LOCATION,
            "persist": True,
            "force": True,
            "items": None,
        }, token)
        self.assertEqual(response.status_code, 200, response.content[:500])
        return response.json()

    def setUp(self):
        self.tenant_a = self._register("Firma A", "owner-a@test.local")
        self.tenant_b = self._register("Firma B", "owner-b@test.local")
        self.generated_a = self._prepare_tenant(self.tenant_a)
        self.generated_b = self._prepare_tenant(self.tenant_b)

    def test_each_tenant_gets_own_demand_even_for_identical_payload(self):
        self.assertNotEqual(self.generated_a["demand_id"], self.generated_b["demand_id"],
                            "ten sam payload dwoch firm nie moze wskazywac jednego Demand")

    def test_solver_uses_only_employees_of_the_requesting_company(self):
        for generated, owner in ((self.generated_a, self.tenant_a),
                                 (self.generated_b, self.tenant_b)):
            staffed = [a for a in generated["assignments"] if a["assigned_employees"]]
            self.assertTrue(staffed)
            for assignment in staffed:
                self.assertEqual(assignment["assigned_employees"], [owner["user_id"]],
                                 "w grafiku pojawil sie pracownik innej firmy")

    def test_foreign_schedule_is_not_readable(self):
        response = self._get(f"/api/schedule/{self.generated_a['demand_id']}", self.tenant_b["access"])
        self.assertEqual(response.status_code, 404, response.content[:300])

        response = self._get(
            f"/api/schedule/{self.generated_a['demand_id']}/day/{DAYS[0]}",
            self.tenant_b["access"],
        )
        self.assertEqual(response.status_code, 404)

    def test_demand_list_is_scoped(self):
        response = self._get("/api/schedule/demands", self.tenant_b["access"])
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.json()["results"]}
        self.assertIn(self.generated_b["demand_id"], ids)
        self.assertNotIn(self.generated_a["demand_id"], ids)

    def test_availability_is_scoped(self):
        response = self._get(
            f"/api/schedule/availability?employee_id={self.tenant_a['user_id']}",
            self.tenant_b["access"],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0, "firma B widzi dostepnosc firmy A")

        response = self._get(
            f"/api/schedule/availability?employee_id={self.tenant_b['user_id']}",
            self.tenant_b["access"],
        )
        self.assertEqual(response.json()["count"], len(DAYS))

    def test_mobile_availability_does_not_leak_other_company(self):
        response = self._get(
            f"/api/schedule/availability/mobile?employee_id={self.tenant_a['user_id']}"
            f"&date_from={DAYS[0]}&date_to={DAYS[-1]}",
            self.tenant_b["access"],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [], "IDOR w /availability/mobile (BACKLOG P0-4)")

    def test_shift_cannot_be_edited_from_another_company(self):
        shift = self.generated_a["assignments"][0]
        response = self._post("/api/schedule/schedule/shift", {
            "id": shift["id"],
            "assigned_employees": [],
        }, self.tenant_b["access"])
        self.assertEqual(response.status_code, 404, response.content[:300])

    def _create_rule_and_special_day(self, tenant, value=3):
        response = self._post("/api/schedule/rules", {
            "name": f"Reguła {value}",
            "mode": "override",
            "value": value,
        }, tenant["access"])
        self.assertEqual(response.status_code, 200, response.content[:300])
        rule = response.json()

        response = self._post("/api/schedule/special-days", {
            "date": DAYS[0],
            "location": "",
            "rule_id": rule["id"],
        }, tenant["access"])
        self.assertEqual(response.status_code, 200, response.content[:300])
        return rule

    def test_event_rules_and_special_days_are_scoped(self):
        self._create_rule_and_special_day(self.tenant_a)

        self.assertEqual(self._get("/api/schedule/rules", self.tenant_b["access"]).json(), [])
        self.assertEqual(self._get("/api/schedule/special-days", self.tenant_b["access"]).json(), [])

        self.assertEqual(len(self._get("/api/schedule/rules", self.tenant_a["access"]).json()), 1)
        self.assertEqual(len(self._get("/api/schedule/special-days", self.tenant_a["access"]).json()), 1)

    def test_special_day_changes_only_own_company_demand(self):
        self._create_rule_and_special_day(self.tenant_a, value=3)

        payload = {
            "date_from": DAYS[0],
            "date_to": DAYS[-1],
            "location": LOCATION,
            "persist": True,
            "force": True,
            "items": None,
        }
        generated_a = self._post("/api/schedule/generate-range", payload, self.tenant_a["access"]).json()
        day_a = [a for a in generated_a["assignments"] if a["date"] == DAYS[0]]
        self.assertTrue(day_a)
        self.assertTrue(all(a["demand"] == 3 for a in day_a),
                        f"reguła firmy A nie zadziałała: {[a['demand'] for a in day_a]}")

        generated_b = self._post("/api/schedule/generate-range", payload, self.tenant_b["access"]).json()
        day_b = [a for a in generated_b["assignments"] if a["date"] == DAYS[0]]
        self.assertTrue(day_b)
        self.assertTrue(all(a["demand"] == 1 for a in day_b),
                        f"reguła firmy A wpłynęła na grafik firmy B: {[a['demand'] for a in day_b]}")
