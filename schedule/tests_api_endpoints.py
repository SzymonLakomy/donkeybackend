"""Testy HTTP endpointow grafiku, ktore nie mialy pokrycia (BACKLOG P1-8).

Sprawdzaja kontrakt odpowiedzi i efekty uboczne (co trafia do bazy), a nie tylko to,
ze funkcja nie rzuca wyjatku.
"""
import json
from datetime import date, timedelta

from django.test import TestCase, override_settings

from schedule.models import ScheduleShift

LOCATION = "Main"
START = date(2027, 1, 4)  # poniedzialek
DAYS = [(START + timedelta(days=i)).isoformat() for i in range(3)]


@override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
class ScheduleApiEndpointsTests(TestCase):
    def setUp(self):
        response = self.client.post("/api/accounts/register-company", data=json.dumps({
            "company_name": "API Co",
            "nip": "1234567890",
            "email": "api@test.local",
            "first_name": "Api",
            "last_name": "Owner",
            "password": "haslo-testowe-123",
        }), content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content[:300])

        response = self.client.post("/api/accounts/login", data=json.dumps({
            "email": "api@test.local",
            "password": "haslo-testowe-123",
        }), content_type="application/json")
        body = response.json()
        self.token = body["access"]
        self.employee_id = str(body["user"]["id"])

        self.assertEqual(self._post("/api/schedule/locations", {"name": LOCATION}, auth=True).status_code, 200)

        response = self._post("/api/schedule/availability/bulk", {
            "employee_id": self.employee_id,
            "employee_name": "Api Owner",
            "experienced": True,
            "hours_min": 0,
            "hours_max": 60,
            "availabilities": [
                {"date": day, "available_slots": {"start": "08:00", "end": "20:00"}} for day in DAYS
            ],
        }, auth=True)
        self.assertEqual(response.status_code, 200, response.content[:300])

    def _post(self, path, payload, auth=True):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"} if auth else {}
        return self.client.post(path, data=json.dumps(payload), content_type="application/json", **headers)

    def _get(self, path, auth=True):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"} if auth else {}
        return self.client.get(path, **headers)

    def _items(self):
        return [{"start": "10:00", "end": "14:00", "demand": 1, "needs_experienced": False}]

    # ---------- generate-day / generate-range ----------
    def test_generate_day_persists_schedule_and_day_view(self):
        response = self._post("/api/schedule/generate-day", {
            "date": DAYS[0], "location": LOCATION, "persist": True, "force": True, "items": self._items(),
        })
        self.assertEqual(response.status_code, 200, response.content[:500])
        body = response.json()
        self.assertEqual(len(body["assignments"]), 1)
        self.assertEqual(body["assignments"][0]["assigned_employees"], [self.employee_id])

        response = self._get(f"/api/schedule/days/{DAYS[0]}?location={LOCATION}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["start"], "10:00")

    def test_generate_day_without_items_and_template_is_400(self):
        response = self._post("/api/schedule/generate-day", {
            "date": DAYS[1], "location": LOCATION, "persist": True, "force": True, "items": None,
        })
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("Brak zapotrzebowania", response.json()["detail"])

    def test_generate_range_with_explicit_items(self):
        response = self._post("/api/schedule/generate-range", {
            "date_from": DAYS[0], "date_to": DAYS[-1], "location": LOCATION,
            "persist": True, "force": True, "items": self._items(),
        })
        self.assertEqual(response.status_code, 200, response.content[:500])
        body = response.json()
        self.assertEqual(len(body["assignments"]), len(DAYS))
        self.assertTrue(all(a["assigned_employees"] for a in body["assignments"]))

    def test_generate_range_persist_false_writes_nothing(self):
        response = self._post("/api/schedule/generate-range", {
            "date_from": DAYS[0], "date_to": DAYS[-1], "location": LOCATION,
            "persist": False, "force": False, "items": self._items(),
        })
        self.assertEqual(response.status_code, 200, response.content[:500])
        self.assertTrue(response.json()["assignments"])
        self.assertEqual(ScheduleShift.objects.count(), 0, "persist=False nie moze zapisywac zmian")

    # ---------- demand / defaults ----------
    def test_demand_day_save_and_read(self):
        response = self._post("/api/schedule/demand/day", {
            "date": DAYS[0], "location": LOCATION, "items": self._items(),
        })
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertEqual(len(response.json()["items"]), 1)

        response = self._get(f"/api/schedule/demand/day?date={DAYS[0]}&location={LOCATION}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["date"], DAYS[0])
        self.assertEqual(body["items"][0]["start"], "10:00")
        self.assertEqual(body["items"][0]["demand"], 1)

    def test_default_demand_save_read_and_week(self):
        response = self._post("/api/schedule/demand/default", {
            "location": LOCATION, "weekday": 0, "items": self._items(),
        })
        self.assertEqual(response.status_code, 200, response.content[:400])

        # wzor ogolny (weekday=None) jest fallbackiem dla pozostalych dni
        response = self._post("/api/schedule/demand/default", {
            "location": LOCATION, "weekday": None, "items": self._items(),
        })
        self.assertEqual(response.status_code, 200, response.content[:400])

        response = self._get(f"/api/schedule/demand/default?location={LOCATION}&weekday=0")
        self.assertEqual(response.status_code, 200)
        weekdays = [entry["weekday"] for entry in response.json()["defaults"]]
        self.assertIn(0, weekdays)

        response = self._get(f"/api/schedule/demand/default/week?location={LOCATION}")
        self.assertEqual(response.status_code, 200)
        week = response.json()["defaults"]
        self.assertEqual(len(week), 7)
        self.assertFalse(week[0]["inherited"], "poniedzialek ma wlasny szablon")
        self.assertTrue(week[1]["inherited"], "wtorek dziedziczy wzor ogolny")
        self.assertEqual(len(week[1]["items"]), 1)

    # ---------- shift get/edit ----------
    def test_shift_get_and_manual_edit(self):
        generated = self._post("/api/schedule/generate-day", {
            "date": DAYS[0], "location": LOCATION, "persist": True, "force": True, "items": self._items(),
        }).json()
        shift_uid = generated["assignments"][0]["id"]

        response = self._get(f"/api/schedule/schedule/shift/{shift_uid}")
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertFalse(response.json()["confirmed"])

        # Uwaga: trasy zmiany/zapotrzebowania maja podwojny prefiks `/schedule/schedule/...`
        # (router jest montowany pod `/schedule`, a sciezki w nim zaczynaja sie od `/schedule`).
        response = self._post("/api/schedule/schedule/shift", {
            "id": shift_uid,
            "demand": 2,
            "assigned_employees": [self.employee_id],
            "confirmed": True,
            "missing_minutes": 240,
            "assigned_employees_detail": [{
                "employee_id": self.employee_id,
                "employee_name": "Api Owner",
                "start": "10:00",
                "end": "14:00",
                "minutes": 240,
                "segments": [{"start": "10:00", "end": "14:00", "minutes": 240}],
            }],
            "missing_segments": [{"start": "14:00", "end": "18:00", "missing": 1, "missing_minutes": 240}],
        })
        self.assertEqual(response.status_code, 200, response.content[:500])
        body = response.json()
        self.assertTrue(body["user_edited"])
        self.assertTrue(body["confirmed"])
        self.assertEqual(body["demand"], 2)
        self.assertEqual(len(body["assigned_employees_detail"]), 1)
        self.assertEqual(len(body["missing_segments"]), 1)

    # ---------- rules / special days ----------
    def test_rules_crud_and_validation(self):
        response = self._post("/api/schedule/rules", {"name": "Weekend", "mode": "override", "value": 2})
        self.assertEqual(response.status_code, 200, response.content[:300])
        rule_id = response.json()["id"]

        response = self._get(f"/api/schedule/rules/{rule_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Weekend")

        self.assertEqual(self._get("/api/schedule/rules/999999").status_code, 404)
        self.assertEqual(self._post("/api/schedule/rules", {"name": "X", "mode": "zly", "value": 1}).status_code, 400)

    def test_special_day_requires_existing_rule(self):
        response = self._post("/api/schedule/special-days", {
            "date": DAYS[0], "location": "", "rule_id": 999999,
        })
        self.assertEqual(response.status_code, 400, response.content[:300])

    # ---------- walidacja i auth ----------
    def test_locations_validation(self):
        self.assertEqual(self._post("/api/schedule/locations", {"name": LOCATION}).status_code, 409)
        self.assertEqual(self._post("/api/schedule/locations", {"name": "   "}).status_code, 400)

    def test_unauthenticated_requests_are_401(self):
        self.assertEqual(self._get("/api/schedule/locations", auth=False).status_code, 401)
        self.assertEqual(self._get("/api/schedule/demands", auth=False).status_code, 401)

    def test_unknown_demand_is_404(self):
        self.assertEqual(self._get("/api/schedule/demand/999999").status_code, 404)
        self.assertEqual(self._get("/api/schedule/schedule/999999").status_code, 404)
        self.assertEqual(self._get(f"/api/schedule/schedule/999999/day/{DAYS[0]}").status_code, 404)