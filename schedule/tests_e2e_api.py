"""Test end-to-end przeplywu managera przez HTTP (bez przegladarki).

Odwzorowuje to, co robi front:
  rejestracja firmy -> login JWT -> lokalizacja -> dostepnosc -> szablon zapotrzebowania
  -> wygenerowanie grafiku -> odczyt grafiku -> ponowne wygenerowanie.

Test jest dowodem, ze API sklada sie w dzialajaca calosc, a nie tylko w izolowane endpointy.
Uruchamiany na bazie testowej (TestCase), nie dotyka danych produkcyjnych.
"""
import json
from datetime import date, timedelta
from urllib.parse import quote

from django.test import TestCase, override_settings

LOCATION = "Lokal Główny"


@override_settings(DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
class ManagerFlowE2ETests(TestCase):
    def _post(self, path, payload, token=None):
        headers = {}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def _get(self, path, token):
        return self.client.get(path, HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_manager_can_generate_and_read_a_schedule(self):
        # 1. Firma + konto ownera przez DRF
        response = self._post("/api/accounts/register-company", {
            "company_name": "Restauracja Testowa",
            "nip": "1234567890",
            "email": "owner@test.local",
            "first_name": "Maja",
            "last_name": "Owner",
            "password": "haslo-testowe-123",
        })
        self.assertEqual(response.status_code, 201, response.content[:500])

        # 2. Login: token + dane uzytkownika
        response = self._post("/api/accounts/login", {
            "email": "owner@test.local",
            "password": "haslo-testowe-123",
        })
        self.assertEqual(response.status_code, 200, response.content[:500])
        body = response.json()
        self.assertIn("access", body)
        self.assertEqual(body["user"]["role"], "owner")
        access = body["access"]
        employee_id = str(body["user"]["id"])

        # 3. Lokalizacja (Ninja)
        response = self._post("/api/schedule/locations", {"name": LOCATION}, access)
        self.assertEqual(response.status_code, 200, response.content[:500])

        response = self._get("/api/schedule/locations", access)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([loc["name"] for loc in response.json()], [LOCATION])

        # 4. Tydzien od poniedzialku
        start = date(2026, 11, 2)
        days = [(start + timedelta(days=i)).isoformat() for i in range(7)]

        # 5. Dostepnosc pracownika (10:00-20:00 codziennie)
        response = self._post("/api/schedule/availability/bulk", {
            "employee_id": employee_id,
            "employee_name": "Maja Owner",
            "experienced": True,
            "hours_min": 0,
            "hours_max": 60,
            "availabilities": [
                {"date": day, "available_slots": {"start": "10:00", "end": "20:00"}}
                for day in days
            ],
        }, access)
        self.assertEqual(response.status_code, 200, response.content[:500])
        self.assertEqual(len(response.json()), 7)

        # 6. Szablon zapotrzebowania na kazdy dzien tygodnia: 10-14 i 14-18, po 1 osobie
        response = self._post("/api/schedule/demand/default/bulk", {
            "location": LOCATION,
            "defaults": [
                {
                    "weekday": weekday,
                    "items": [
                        {"start": "10:00", "end": "14:00", "demand": 1, "needs_experienced": False},
                        {"start": "14:00", "end": "18:00", "demand": 1, "needs_experienced": False},
                    ],
                }
                for weekday in range(7)
            ],
        }, access)
        self.assertEqual(response.status_code, 200, response.content[:500])

        # 7. Generowanie grafiku na tydzien (tak jak front: items=None -> szablon)
        payload = {
            "date_from": days[0],
            "date_to": days[-1],
            "location": LOCATION,
            "persist": True,
            "force": True,
            "items": None,
        }
        response = self._post("/api/schedule/generate-range", payload, access)
        self.assertEqual(response.status_code, 200, response.content[:800])
        generated = response.json()

        self.assertTrue(generated["assignments"], "brak zmian w odpowiedzi")
        self.assertEqual(len(generated["assignments"]), 14)
        self.assertIn("summary", generated)
        self.assertIn("uncovered", generated["summary"])

        staffed = [a for a in generated["assignments"] if a["assigned_employees"]]
        self.assertEqual(len(staffed), 14, "kazda zmiana powinna byc obsadzona")
        for assignment in staffed:
            self.assertEqual(assignment["assigned_employees"], [employee_id])
            self.assertEqual(assignment["missing_minutes"], 0)

        # 8. Odczyt grafiku dnia z bazy (sciezka uzywana przez widok tygodnia)
        response = self._get(f"/api/schedule/days/{days[0]}?location={quote(LOCATION)}", access)
        self.assertEqual(response.status_code, 200, response.content[:500])
        day_shifts = response.json()
        self.assertEqual(len(day_shifts), 2)
        self.assertEqual({s["start"] for s in day_shifts}, {"10:00", "14:00"})

        # 9. Ponowne wygenerowanie (force=True) nie moze wywrocic sie na istniejacych zmianach
        response = self._post("/api/schedule/generate-range", payload, access)
        self.assertEqual(response.status_code, 200, response.content[:800])
        regenerated = response.json()
        self.assertEqual(len(regenerated["assignments"]), 14)
        self.assertEqual(
            len([a for a in regenerated["assignments"] if a["assigned_employees"]]), 14
        )
