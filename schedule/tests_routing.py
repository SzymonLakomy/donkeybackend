"""Test tras: dokumentacja (schedule/ENDPOINTS.md) musi odpowiadac rzeczywistym URL-om.

Ten test istnieje, bo w API jest podwojny prefiks (`/api/schedule/schedule/...`).
Bez niego ktos "poprawi" dokumentacje albo doda trase w zlym miejscu i nikt tego
nie zauwazy do pierwszego 404 u klienta.
"""
from django.test import SimpleTestCase
from django.urls import Resolver404, resolve

# sciezka -> dopuszczalne nazwy widokow (dwie metody na tej samej sciezce = dwa wpisy)
ROUTES = {
    # --- schedule ---
    "/api/schedule/locations": {"list_locations", "create_location"},
    "/api/schedule/availability": {"list_availability"},
    "/api/schedule/availability/bulk": {"upsert_availability_bulk"},
    "/api/schedule/availability/mobile": {"get_availability_mobile"},
    "/api/schedule/demand/day": {"save_day_demand", "get_day_demand"},
    "/api/schedule/demand/default": {"save_default_demand", "get_default_demand"},
    "/api/schedule/demand/default/bulk": {"save_default_demand_bulk"},
    "/api/schedule/demand/default/week": {"get_default_demand_week"},
    "/api/schedule/demand/12": {"get_demand"},
    "/api/schedule/demands": {"list_demands"},
    "/api/schedule/rules": {"create_rule", "list_rules"},
    "/api/schedule/rules/5": {"get_rule"},
    "/api/schedule/special-days": {"create_special_day", "list_special_days"},
    "/api/schedule/generate-day": {"generate_day"},
    "/api/schedule/generate-range": {"generate_range"},
    "/api/schedule/days/2026-11-02": {"get_day_schedule"},
    # podwojny prefiks - swiadomy stan faktyczny
    "/api/schedule/schedule/12": {"get_or_generate_schedule"},
    "/api/schedule/schedule/12/day/2026-11-02": {"get_schedule_day"},
    "/api/schedule/schedule/shift": {"upsert_shift"},
    "/api/schedule/schedule/shift/D1|2026-11-02|Main|10:00-14:00": {"get_shift"},
    # --- calendars ---
    "/api/calendar/events": {"create_calendar_event", "list_calendar_events"},
    "/api/calendar/medical": {"create_medical_event", "list_medical_events"},
    "/api/calendar/sources": {"create_external_calendar", "list_external_calendars"},
    "/api/calendar/sources/5/sync": {"mark_calendar_synced"},
    # --- accounts (DRF) ---
    "/api/accounts/workplace/config/": {"workplace-config"},
    "/api/accounts/employees/": {"company-users-list"},
    "/api/accounts/attendance/correction/review/": {"attendance-correction-review"},
    "/api/accounts/attendance/status/": {"attendance-status"},
}

# Tak NIE wolno wolac API: brak podwojnego prefiksu albo zbedny ukosnik na koncu
# (APPEND_SLASH=False, wiec Django nie doreguje automatycznie).
NOT_ROUTED = [
    "/api/schedule/shift",
    "/api/schedule/shift/D1|2026-11-02|Main|10:00-14:00",
    "/api/schedule/12",
    "/api/schedule/locations/",
]


class RouteContractTests(SimpleTestCase):
    def test_documented_paths_resolve_to_expected_views(self):
        for path, expected in ROUTES.items():
            with self.subTest(path=path):
                match = resolve(path)
                self.assertIn(
                    match.url_name,
                    expected,
                    f"{path} trafia w '{match.url_name}', oczekiwano {sorted(expected)}",
                )

    def test_old_paths_are_not_routed(self):
        for path in NOT_ROUTED:
            with self.subTest(path=path):
                with self.assertRaises(Resolver404):
                    resolve(path)
