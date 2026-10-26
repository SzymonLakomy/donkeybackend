"""Testy walidatora grafiku (schedule/validation.py).

Walidator jest niezalezny od solvera: sprawdza KAZDY grafik, takze reczna korekte
albo odpowiedz modelu jezykowego. Tu testujemy go na grafikach zepsutych celowo
oraz na wyniku solvera (ktory musi przechodzic zawsze).
"""
from django.test import SimpleTestCase

from schedule.solver import run_solver
from schedule.validation import build_availability_index, validate_schedule


def _av(emp, day, slots):
    return {
        "employee_id": emp,
        "date": day,
        "experienced": False,
        "hours_min": 0,
        "hours_max": 1000,
        "available_slots": [{"start": s, "end": e} for s, e in slots],
    }


def _assignment(day, start, end, demand, segments):
    """segments: [(employee_id, start, end), ...]"""
    return {
        "date": day,
        "location": "A",
        "start": start,
        "end": end,
        "demand": demand,
        "assigned_employees": sorted({emp for emp, _s, _e in segments}),
        "assigned_employees_detail": [
            {"employee_id": emp, "segments": [{"start": s, "end": e, "minutes": None}]}
            for emp, s, e in segments
        ],
    }


def _demand(day, start, end, demand):
    return {"date": day, "location": "A", "start": start, "end": end, "demand": demand}


class AvailabilityIndexTests(SimpleTestCase):
    def test_index_builds_slots_per_employee_and_day(self):
        index = build_availability_index([_av("a", "2026-01-05", [("08:00", "12:00"), ("14:00", "18:00")])])
        self.assertEqual(index[("a", "2026-01-05")], [(480, 720), (840, 1080)])


class ValidScheduleTests(SimpleTestCase):
    def test_correct_schedule_has_no_violations(self):
        assignments = [_assignment("2026-01-05", "10:00", "14:00", 1, [("a", "10:00", "14:00")])]
        violations = validate_schedule(
            assignments,
            [_av("a", "2026-01-05", [("08:00", "18:00")])],
            [_demand("2026-01-05", "10:00", "14:00", 1)],
        )
        self.assertEqual(violations, [])

    def test_solver_output_passes_validation(self):
        availability = [
            _av(emp, f"2026-01-{day:02d}", [("08:00", "20:00")])
            for emp in ("a", "b", "c")
            for day in (5, 6, 7)
        ]
        demand = [
            _demand(f"2026-01-{day:02d}", "10:00", "14:00", 1)
            for day in (5, 6, 7)
        ] + [
            _demand(f"2026-01-{day:02d}", "14:00", "18:00", 2)
            for day in (5, 6, 7)
        ]

        result = run_solver(availability, demand, time_limit_sec=5.0)

        violations = validate_schedule(result["assignments"], availability, demand,
                                       min_block_minutes=180)
        self.assertEqual(violations, [], f"wynik solvera nie przechodzi walidacji: {violations[:5]}")


class BrokenScheduleTests(SimpleTestCase):
    def test_detects_overlaps_of_same_employee(self):
        assignments = [
            _assignment("2026-01-05", "10:00", "14:00", 1, [("a", "10:00", "14:00")]),
            _assignment("2026-01-05", "12:00", "16:00", 1, [("a", "12:00", "16:00")]),
        ]
        violations = validate_schedule(assignments, [_av("a", "2026-01-05", [("08:00", "20:00")])])
        self.assertTrue(any("nakladka" in v for v in violations), violations)

    def test_detects_assignment_outside_availability(self):
        assignments = [_assignment("2026-01-05", "10:00", "14:00", 1, [("a", "10:00", "14:00")])]
        violations = validate_schedule(assignments, [_av("a", "2026-01-05", [("10:00", "12:00")])])
        self.assertTrue(any("zadeklarowal" in v for v in violations), violations)

    def test_detects_more_people_than_demand(self):
        assignments = [_assignment("2026-01-05", "10:00", "14:00", 1,
                                   [("a", "10:00", "14:00"), ("b", "10:00", "14:00")])]
        availability = [_av("a", "2026-01-05", [("08:00", "20:00")]),
                        _av("b", "2026-01-05", [("08:00", "20:00")])]
        violations = validate_schedule(assignments, availability)
        self.assertTrue(any("osob przy zapotrzebowaniu" in v for v in violations), violations)

    def test_detects_segment_outside_shift_bounds(self):
        assignments = [_assignment("2026-01-05", "10:00", "14:00", 1, [("a", "09:00", "11:00")])]
        violations = validate_schedule(assignments, [_av("a", "2026-01-05", [("08:00", "20:00")])])
        self.assertTrue(any("poza granicami zmiany" in v for v in violations), violations)

    def test_detects_shift_missing_from_demand(self):
        assignments = [_assignment("2026-01-05", "10:00", "14:00", 1, [("a", "10:00", "14:00")])]
        violations = validate_schedule(
            assignments,
            [_av("a", "2026-01-05", [("08:00", "20:00")])],
            [_demand("2026-01-05", "11:00", "15:00", 1)],
        )
        self.assertTrue(any("zapotrzebowaniu" in v for v in violations), violations)

    def test_min_block_minutes_is_checked(self):
        assignments = [_assignment("2026-01-05", "10:00", "14:00", 1, [("a", "10:00", "11:00")])]
        availability = [_av("a", "2026-01-05", [("08:00", "20:00")])]
        self.assertEqual(validate_schedule(assignments, availability), [])
        violations = validate_schedule(assignments, availability, min_block_minutes=180)
        self.assertTrue(any("minimum to 180 min" in v for v in violations), violations)

    def test_require_full_shift_flags_partial_coverage(self):
        assignments = [_assignment("2026-01-05", "10:00", "14:00", 1, [("a", "10:00", "12:00")])]
        availability = [_av("a", "2026-01-05", [("08:00", "20:00")])]
        self.assertEqual(validate_schedule(assignments, availability), [])
        violations = validate_schedule(assignments, availability, require_full_shift=True)
        self.assertTrue(any("nie jest pokryta w calosci" in v for v in violations), violations)

    def test_ai_style_schedule_is_rejected(self):
        """Odtworzenie bledu modelu jezykowego: trzy zachodzace na siebie zmiany, ta sama osoba."""
        assignments = [
            _assignment("2026-01-05", "11:00", "18:00", 1, [("karina", "11:00", "18:00")]),
            _assignment("2026-01-05", "11:00", "20:00", 1, [("karina", "11:00", "20:00")]),
            _assignment("2026-01-05", "12:00", "20:30", 1, [("karina", "12:00", "20:30")]),
        ]
        availability = [_av("karina", "2026-01-05", [("10:00", "22:00")])]

        violations = validate_schedule(assignments, availability)

        overlaps = [v for v in violations if "nakladka" in v]
        # walidator raportuje nakladki parami sasiadujacymi po czasie; kazda taka para
        # wystarcza, zeby odrzucic grafik (i tak jest tansza niz porownywanie wszystkich par).
        self.assertGreaterEqual(len(overlaps), 2,
                                f"AI-owe nakladki nie zostaly wykryte: {violations}")
        self.assertTrue(all("karina" in v for v in overlaps), overlaps)


class ConfirmedShiftTests(SimpleTestCase):
    def test_confirmed_shift_may_exceed_declared_availability(self):
        """Potwierdzona zmiana (assigned_shift.confirmed) jest decyzja managera - nie jest bledem."""
        availability = [{
            "employee_id": "a",
            "date": "2026-01-05",
            "available_slots": [{"start": "10:00", "end": "12:00"}],
            "assigned_shift": {"location": "A", "start": "14:00", "end": "18:00", "confirmed": True},
        }]
        assignments = [_assignment("2026-01-05", "14:00", "18:00", 1, [("a", "14:00", "18:00")])]

        self.assertEqual(validate_schedule(assignments, availability), [])

    def test_unconfirmed_shift_outside_availability_is_flagged(self):
        availability = [{
            "employee_id": "a",
            "date": "2026-01-05",
            "available_slots": [{"start": "10:00", "end": "12:00"}],
            "assigned_shift": {"location": "A", "start": "14:00", "end": "18:00", "confirmed": False},
        }]
        assignments = [_assignment("2026-01-05", "14:00", "18:00", 1, [("a", "14:00", "18:00")])]

        violations = validate_schedule(assignments, availability)
        self.assertTrue(any("zadeklarowal" in v for v in violations), violations)
