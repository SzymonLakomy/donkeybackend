"""Testy HTTP modulu kalendarzy (BACKLOG P1-8: calendars/api.py mialo 23% pokrycia)."""
import json
from datetime import date, datetime, time, timedelta

from django.test import TestCase, override_settings


def _dt(day: date, hour: int) -> str:
    return datetime.combine(day, time(hour=hour)).isoformat()


@override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
class CalendarApiTests(TestCase):
    def setUp(self):
        response = self.client.post("/api/accounts/register-company", data=json.dumps({
            "company_name": "Kalendarz Co",
            "nip": "1234567890",
            "email": "cal@test.local",
            "first_name": "Cal",
            "last_name": "Owner",
            "password": "haslo-testowe-123",
        }), content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content[:300])

        response = self.client.post("/api/accounts/login", data=json.dumps({
            "email": "cal@test.local", "password": "haslo-testowe-123",
        }), content_type="application/json")
        body = response.json()
        self.token = body["access"]
        self.employee_id = str(body["user"]["id"])
        self.day = date(2027, 2, 1)

    def _post(self, path, payload, auth=True):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"} if auth else {}
        return self.client.post(path, data=json.dumps(payload), content_type="application/json", **headers)

    def _get(self, path, auth=True):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"} if auth else {}
        return self.client.get(path, **headers)

    def test_create_and_list_calendar_event(self):
        response = self._post("/api/calendar/events", {
            "title": "Urlop",
            "start_at": _dt(self.day, 9),
            "end_at": _dt(self.day, 17),
            "category": "leave",
            "employee_id": self.employee_id,
        })
        self.assertEqual(response.status_code, 200, response.content[:400])
        event = response.json()
        self.assertEqual(event["title"], "Urlop")
        self.assertEqual(event["category"], "leave")
        self.assertEqual(str(event["company_id"]), str(event["company_id"]))  # pole istnieje

        response = self._get("/api/calendar/events")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

        response = self._get("/api/calendar/events?category=leave")
        self.assertEqual(len(response.json()), 1)
        response = self._get("/api/calendar/events?category=training")
        self.assertEqual(response.json(), [])

    def test_event_rejects_inverted_range(self):
        response = self._post("/api/calendar/events", {
            "title": "Zle daty",
            "start_at": _dt(self.day, 17),
            "end_at": _dt(self.day, 9),
        })
        self.assertEqual(response.status_code, 400, response.content[:300])

    def test_event_without_dates_gets_default_duration(self):
        response = self._post("/api/calendar/events", {"title": "Bez dat"})
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertIsNotNone(response.json()["start_at"])
        self.assertIsNotNone(response.json()["end_at"])

    def test_medical_event_lifecycle(self):
        response = self._post("/api/calendar/medical", {
            "title": "Badanie okresowe",
            "start_at": _dt(self.day, 10),
            "end_at": _dt(self.day, 11),
            "exam_type": "sanepid",
            "status": "confirmed",
        })
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertEqual(response.json()["status"], "confirmed")

        response = self._get("/api/calendar/medical")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

    def test_external_calendar_create_list_and_sync(self):
        response = self._post("/api/calendar/sources", {
            "name": "Google firmowy",
            "provider": "google",
            "external_id": "cal-1",
        })
        self.assertEqual(response.status_code, 200, response.content[:400])
        source_id = response.json()["id"]

        response = self._get("/api/calendar/sources")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)

        response = self._post(f"/api/calendar/sources/{source_id}/sync", {
            "sync_token": "token-123",
            "last_synced_at": _dt(self.day, 12),
        })
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertEqual(response.json()["sync_token"], "token-123")

    def test_unauthenticated_is_401(self):
        self.assertEqual(self._get("/api/calendar/events", auth=False).status_code, 401)
        self.assertEqual(self._post("/api/calendar/events", {"title": "X"}, auth=False).status_code, 401)
