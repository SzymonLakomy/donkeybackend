"""Testy regul solvera wprowadzonych w modelu v2 (reguly twarde, sprawiedliwosc, stabilnosc).

Nie wymagaja bazy danych - `run_solver` jest czysta funkcja.
"""
from datetime import date, timedelta

from django.test import SimpleTestCase

from schedule.solver import SolverError, run_solver


def _av(emp, day, slots, experienced=False, hours_min=0, hours_max=10 ** 9, **extra):
    rec = {
        "employee_id": emp,
        "employee_name": emp,
        "date": day,
        "experienced": experienced,
        "hours_min": hours_min,
        "hours_max": hours_max,
        "available_slots": [{"start": s, "end": e} for s, e in slots],
    }
    rec.update(extra)
    return rec


def _dem(day, start, end, demand=1, location="A", needs_experienced=False):
    return {
        "date": day,
        "location": location,
        "start": start,
        "end": end,
        "demand": demand,
        "needs_experienced": needs_experienced,
    }


def _signature(result):
    return sorted(
        (a["date"], a["start"], a["end"], tuple(a["assigned_employees"]))
        for a in result["assignments"]
    )


class MinShiftLengthTests(SimpleTestCase):
    def test_short_availability_is_not_used(self):
        """30 minut dostepnosci w srodku 8-godzinnej zmiany nie moze byc osobnym 'grafikiem'."""
        availability = [_av("krotki", "2025-11-03", [("12:00", "12:30")])]
        demand = [_dem("2025-11-03", "12:00", "20:00", demand=3)]

        result = run_solver(availability, demand)

        self.assertEqual(result["assignments"][0]["assigned_employees"], [])
        self.assertGreater(result["assignments"][0]["missing_minutes"], 0)

    def test_option_can_reenable_short_blocks(self):
        """Opcja min_shift_minutes=0 przywraca stare zachowanie (30 min)."""
        availability = [_av("krotki", "2025-11-03", [("12:00", "12:30")])]
        demand = [_dem("2025-11-03", "12:00", "20:00", demand=3)]

        result = run_solver(availability, demand, options={"min_shift_minutes": 0})

        details = result["assignments"][0]["assigned_employees_detail"]
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["minutes"], 30)

    def test_full_shift_is_assigned(self):
        """Dostepnosc pokrywajaca cala zmiane konczy sie pelnym przypisaniem."""
        availability = [_av("pelny", "2025-11-03", [("10:00", "14:00")])]
        demand = [_dem("2025-11-03", "10:00", "14:00")]

        result = run_solver(availability, demand)

        assignment = result["assignments"][0]
        self.assertEqual(assignment["assigned_employees"], ["pelny"])
        self.assertEqual(assignment["missing_minutes"], 0)
        self.assertEqual(assignment["assigned_employees_detail"][0]["segments"],
                         [{"start": "10:00", "end": "14:00", "minutes": 240}])


class NoSplitShiftTests(SimpleTestCase):
    def test_two_disjoint_shifts_are_not_assigned_to_one_person(self):
        availability = [_av("rozdarty", "2025-11-03", [("08:00", "12:00"), ("16:00", "20:00")])]
        demand = [
            _dem("2025-11-03", "08:00", "12:00"),
            _dem("2025-11-03", "16:00", "20:00"),
        ]

        result = run_solver(availability, demand)

        staffed = [a for a in result["assignments"] if a["assigned_employees"]]
        self.assertEqual(len(staffed), 1, "jedna osoba nie moze pracowac dwoch rozdzielonych zmian")

    def test_adjacent_shifts_stay_allowed(self):
        """Zmiany przylegajace tworza jeden ciagly blok - regula ich nie blokuje."""
        availability = [_av("ciagly", "2025-11-03", [("08:00", "16:00")])]
        demand = [
            _dem("2025-11-03", "08:00", "12:00"),
            _dem("2025-11-03", "12:00", "16:00"),
        ]

        result = run_solver(availability, demand)

        staffed = [a for a in result["assignments"] if a["assigned_employees"]]
        self.assertEqual(len(staffed), 2)


class RestBetweenDaysTests(SimpleTestCase):
    def test_short_rest_between_days_is_blocked(self):
        """Koniec 23:30 i start 06:00 to 6.5 h - ponizej 11 h, wiec nie dla tej samej osoby."""
        availability = [
            _av("nocny", "2025-11-03", [("16:00", "23:30")]),
            _av("nocny", "2025-11-04", [("06:00", "14:00")]),
        ]
        demand = [
            _dem("2025-11-03", "16:00", "23:30"),
            _dem("2025-11-04", "06:00", "14:00"),
        ]

        result = run_solver(availability, demand)

        staffed = [a for a in result["assignments"] if a["assigned_employees"]]
        self.assertEqual(len(staffed), 1, "11 h odpoczynku musi byc zachowane")

    def test_rest_rule_can_be_disabled(self):
        availability = [
            _av("nocny", "2025-11-03", [("16:00", "23:30")]),
            _av("nocny", "2025-11-04", [("06:00", "14:00")]),
        ]
        demand = [
            _dem("2025-11-03", "16:00", "23:30"),
            _dem("2025-11-04", "06:00", "14:00"),
        ]

        result = run_solver(availability, demand, options={"min_rest_minutes": 0})

        staffed = [a for a in result["assignments"] if a["assigned_employees"]]
        self.assertEqual(len(staffed), 2)


class OneLocationPerDayTests(SimpleTestCase):
    def test_employee_does_not_change_location_within_a_day(self):
        availability = [_av("wedrujacy", "2025-11-03", [("08:00", "16:00")])]
        demand = [
            _dem("2025-11-03", "08:00", "12:00", location="A"),
            _dem("2025-11-03", "12:00", "16:00", location="B"),
        ]

        result = run_solver(availability, demand)

        staffed = [a for a in result["assignments"] if a["assigned_employees"]]
        self.assertEqual(len(staffed), 1)

    def test_rule_can_be_disabled(self):
        availability = [_av("wedrujacy", "2025-11-03", [("08:00", "16:00")])]
        demand = [
            _dem("2025-11-03", "08:00", "12:00", location="A"),
            _dem("2025-11-03", "12:00", "16:00", location="B"),
        ]

        result = run_solver(availability, demand, options={"one_location_per_day": False})

        staffed = [a for a in result["assignments"] if a["assigned_employees"]]
        self.assertEqual(len(staffed), 2)


class WeeklyHoursTests(SimpleTestCase):
    def test_hours_max_is_a_weekly_limit(self):
        """14 dni = dwa tygodnie; 40 h/tydz. daje maksymalnie 80 h na osobe."""
        start = date(2025, 11, 3)
        days = [(start + timedelta(days=i)).isoformat() for i in range(14)]

        availability = []
        demand = []
        for day in days:
            for emp in ("ala", "bolek"):
                availability.append(_av(emp, day, [("08:00", "22:00")], hours_max=40))
            demand.append(_dem(day, "10:00", "20:00"))

        result = run_solver(availability, demand, time_limit_sec=20.0)

        for summary in result["hours_summary"]:
            self.assertLessEqual(summary["total_hours"], 80.0 + 0.01,
                                 f"{summary['employee_id']} przekroczyl 2 x 40 h")
            self.assertEqual(summary["over_hours"], 0.0,
                             f"{summary['employee_id']} ma nadgodziny mimo limitu tygodniowego")


class ExperienceTests(SimpleTestCase):
    def test_missing_senior_is_soft_and_flagged(self):
        availability = [_av("junior", "2025-11-03", [("08:00", "12:00")])]
        demand = [_dem("2025-11-03", "08:00", "12:00", needs_experienced=True)]

        result = run_solver(availability, demand)

        assignment = result["assignments"][0]
        self.assertEqual(assignment["assigned_employees"], ["junior"], "pokrycie ma priorytet")
        self.assertTrue(assignment["experience_violated"])
        self.assertEqual(result["metrics"]["experience_violations"], 1)

    def test_senior_is_preferred_when_available(self):
        availability = [
            _av("junior", "2025-11-03", [("08:00", "12:00")]),
            _av("senior", "2025-11-03", [("08:00", "12:00")], experienced=True),
        ]
        demand = [_dem("2025-11-03", "08:00", "12:00", needs_experienced=True)]

        result = run_solver(availability, demand)

        assignment = result["assignments"][0]
        self.assertEqual(assignment["assigned_employees"], ["senior"])
        self.assertFalse(assignment["experience_violated"])


class FairnessTests(SimpleTestCase):
    def test_work_is_spread_when_coverage_is_saturated(self):
        """1 osoba na zmiane, 5 chetnych: godziny powinny byc wyrownane, a nie 'kto pierwszy'."""
        start = date(2025, 11, 3)
        days = [(start + timedelta(days=i)).isoformat() for i in range(7)]

        availability = []
        demand = []
        for day in days:
            for emp in ("a", "b", "c", "d", "e"):
                availability.append(_av(emp, day, [("08:00", "22:00")]))
            demand.append(_dem(day, "12:00", "16:00"))

        result = run_solver(availability, demand)

        hours = [h["total_hours"] for h in result["hours_summary"]]
        self.assertEqual(sum(hours), 28.0, "caly popyt (7 x 4 h) musi byc pokryty")
        self.assertLessEqual(max(hours) - min(hours), 4.0,
                             f"godziny nie sa wyrownane: {sorted(hours, reverse=True)}")

    def test_weekend_days_are_balanced(self):
        availability = [
            _av("a", "2025-11-08", [("08:00", "16:00")]),
            _av("a", "2025-11-09", [("08:00", "16:00")]),
            _av("b", "2025-11-08", [("08:00", "16:00")]),
            _av("b", "2025-11-09", [("08:00", "16:00")]),
        ]
        demand = [
            _dem("2025-11-08", "08:00", "12:00"),
            _dem("2025-11-09", "08:00", "12:00"),
        ]

        result = run_solver(availability, demand)

        weekend = {h["employee_id"]: h["weekend_days"] for h in result["hours_summary"]}
        self.assertEqual(sum(weekend.values()), 2)
        self.assertEqual(max(weekend.values()) - min(weekend.values()), 0,
                         f"weekendy nie sa rozlozone: {weekend}")


class DeterminismTests(SimpleTestCase):
    def test_same_input_gives_identical_schedule(self):
        availability = []
        demand = []
        start = date(2025, 11, 3)
        for i in range(3):
            day = (start + timedelta(days=i)).isoformat()
            for emp in ("1", "2", "3", "4"):
                availability.append(_av(emp, day, [("08:00", "12:00"), ("12:00", "20:00")]))
            demand.append(_dem(day, "08:00", "12:00", demand=2))
            demand.append(_dem(day, "12:00", "20:00", demand=1))

        first = run_solver(availability, demand, time_limit_sec=5.0)
        second = run_solver(availability, demand, time_limit_sec=5.0)

        self.assertEqual(_signature(first), _signature(second))


class StabilityTests(SimpleTestCase):
    def test_previous_assignments_are_kept_when_coverage_allows(self):
        availability = []
        demand = []
        start = date(2025, 11, 3)
        for i in range(2):
            day = (start + timedelta(days=i)).isoformat()
            for emp in ("1", "2", "3"):
                availability.append(_av(emp, day, [("08:00", "16:00")]))
            demand.append(_dem(day, "10:00", "14:00"))

        first = run_solver(availability, demand)

        previous = [
            {
                "date": a["date"],
                "location": a["location"],
                "start": a["start"],
                "end": a["end"],
                "assigned_employees": a["assigned_employees"],
            }
            for a in first["assignments"]
        ]

        second = run_solver(availability, demand, previous=previous)

        self.assertEqual(_signature(first), _signature(second),
                         "przy identycznym pokryciu solver powinien utrzymac poprzednie przypisania")


class SolverContractTests(SimpleTestCase):
    def test_status_and_metrics_are_reported(self):
        availability = [_av("1", "2025-11-03", [("08:00", "12:00")])]
        demand = [_dem("2025-11-03", "08:00", "12:00")]

        result = run_solver(availability, demand)

        self.assertIn(result["status"], {"OPTIMAL", "FEASIBLE"})
        metrics = result["metrics"]
        for key in ("coverage_pct", "assigned_minutes", "required_minutes",
                    "unassigned_shifts", "experience_violations"):
            self.assertIn(key, metrics)
        self.assertEqual(metrics["coverage_pct"], 100.0)

    def test_infeasible_model_raises_solver_error(self):
        """Dwie potwierdzone, nakladajace sie zmiany dla jednej osoby sa sprzeczne."""
        availability = [
            _av("1", "2025-11-03", [("08:00", "16:00")],
                assigned_shift={"location": "A", "start": "08:00", "end": "12:00", "confirmed": True}),
            _av("1", "2025-11-03", [("08:00", "16:00")],
                assigned_shift={"location": "A", "start": "10:00", "end": "14:00", "confirmed": True}),
        ]
        demand = [
            _dem("2025-11-03", "08:00", "12:00"),
            _dem("2025-11-03", "10:00", "14:00"),
        ]

        with self.assertRaises(SolverError) as ctx:
            run_solver(availability, demand)
        self.assertEqual(ctx.exception.status, "INFEASIBLE")
