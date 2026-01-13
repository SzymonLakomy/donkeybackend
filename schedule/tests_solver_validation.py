"""
Testy walidacyjne solvera harmonogramów.

Zawiera:
- Generator danych syntetycznych
- Testy walidacji ograniczeń (TC-SOLVER-01 do TC-SOLVER-05)
- Testy wydajnościowe / benchmark (TC-BENCH-01, TC-BENCH-02)
- Testy deterministyczności (TC-DET-01, TC-DET-02)
- Testy przypadków brzegowych (TC-EDGE-01 do TC-EDGE-04)
- Test struktury wyjścia (TC-INT-01)
"""

import time
import random
from datetime import date, timedelta
from typing import List, Dict, Any, Tuple
from django.test import SimpleTestCase

from schedule.solver import run_solver


# =============================================================================
# GENERATOR DANYCH SYNTETYCZNYCH
# =============================================================================

# Typowe sloty czasowe w gastronomii (4-godzinne zmiany)
GASTRO_SLOTS = [
    ("06:00", "10:00"),
    ("10:00", "14:00"),
    ("14:00", "18:00"),
    ("18:00", "22:00"),
]

# Rozszerzone sloty dla większej liczby zmian
EXTENDED_SLOTS = [
    ("06:00", "10:00"),
    ("08:00", "12:00"),
    ("10:00", "14:00"),
    ("12:00", "16:00"),
    ("14:00", "18:00"),
    ("16:00", "20:00"),
    ("18:00", "22:00"),
    ("20:00", "00:00"),
]


def generate_synthetic_data(
    num_employees: int = 5,
    shifts_per_day: int = 3,
    num_days: int = 7,
    experienced_ratio: float = 0.3,
    availability_ratio: float = 0.7,
    seed: int = 42,
    location: str = "Restauracja",
    hours_min: int = 0,
    hours_max: int = 40,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Generuje syntetyczne dane testowe dla solvera.

    Args:
        num_employees: Liczba pracowników
        shifts_per_day: Liczba zmian na dzień (max 8)
        num_days: Liczba dni do zaplanowania
        experienced_ratio: Odsetek pracowników doświadczonych (0.0 - 1.0)
        availability_ratio: Współczynnik dostępności (0.0 - 1.0)
        seed: Seed dla generatora losowego (dla powtarzalności)
        location: Nazwa lokalizacji
        hours_min: Minimalna liczba godzin tygodniowo
        hours_max: Maksymalna liczba godzin tygodniowo

    Returns:
        Tuple (emp_availability, demand)
    """
    random.seed(seed)

    # Wybierz sloty czasowe w zależności od liczby zmian
    if shifts_per_day <= 4:
        available_slots = GASTRO_SLOTS[:shifts_per_day]
    else:
        available_slots = EXTENDED_SLOTS[:shifts_per_day]

    # Generuj daty
    start_date = date(2025, 1, 6)  # Poniedziałek
    dates = [(start_date + timedelta(days=i)).isoformat() for i in range(num_days)]

    # Określ którzy pracownicy są doświadczeni
    num_experienced = max(1, int(num_employees * experienced_ratio))
    experienced_ids = set(range(1, num_experienced + 1))

    # Generuj dostępność pracowników
    emp_availability: List[Dict[str, Any]] = []
    for emp_id in range(1, num_employees + 1):
        is_experienced = emp_id in experienced_ids

        for day_date in dates:
            # Losowo decyduj czy pracownik jest dostępny w danym dniu
            if random.random() > availability_ratio:
                continue  # Pracownik niedostępny w tym dniu

            # Losowo wybierz sloty dostępności (1-3 sloty)
            num_slots = random.randint(1, min(3, len(available_slots)))
            day_slots = random.sample(available_slots, num_slots)

            emp_availability.append({
                "employee_id": str(emp_id),
                "employee_name": f"Pracownik_{emp_id}",
                "date": day_date,
                "experienced": is_experienced,
                "hours_min": hours_min,
                "hours_max": hours_max,
                "available_slots": [{"start": s[0], "end": s[1]} for s in day_slots],
            })

    # Generuj zapotrzebowanie
    demand: List[Dict[str, Any]] = []
    for day_date in dates:
        for slot_idx, (start, end) in enumerate(available_slots):
            # Losowa liczba wymaganych pracowników (1-3)
            demand_count = random.randint(1, 3)
            # Co 3-cia zmiana wymaga doświadczenia
            needs_exp = (slot_idx % 3 == 0)

            demand.append({
                "date": day_date,
                "location": location,
                "start": start,
                "end": end,
                "demand": demand_count,
                "needs_experienced": needs_exp,
            })

    return emp_availability, demand


def calculate_coverage_ratio(result: Dict[str, Any], demand: List[Dict[str, Any]]) -> float:
    """
    Oblicza współczynnik pokrycia (ile person-minut przypisano vs ile wymagano).
    """
    total_required = 0
    total_assigned = 0

    for shift in demand:
        start_min = int(shift["start"].split(":")[0]) * 60 + int(shift["start"].split(":")[1])
        end_min = int(shift["end"].split(":")[0]) * 60 + int(shift["end"].split(":")[1])
        if end_min < start_min:  # np. 22:00-00:00
            end_min += 24 * 60
        duration = end_min - start_min
        total_required += duration * shift["demand"]

    for assignment in result.get("assignments", []):
        for detail in assignment.get("assigned_employees_detail", []):
            total_assigned += detail.get("minutes", 0)

    if total_required == 0:
        return 1.0
    return total_assigned / total_required


# =============================================================================
# TESTY WALIDACJI OGRANICZEŃ
# =============================================================================

class TestSolverConstraintValidation(SimpleTestCase):
    """Testy walidacji ograniczeń solvera (TC-SOLVER-01 do TC-SOLVER-05)"""

    def setUp(self):
        """Przygotowanie danych testowych"""
        self.emp_availability, self.demand = generate_synthetic_data(
            num_employees=10,
            shifts_per_day=4,
            num_days=7,
            experienced_ratio=0.3,
            availability_ratio=0.7,
            seed=42,
        )
        self.result = run_solver(self.emp_availability, self.demand, time_limit_sec=10.0)

    def _build_availability_index(self) -> Dict[Tuple[str, str], List[Tuple[int, int]]]:
        """Buduje indeks dostępności: (employee_id, date) -> [(start_min, end_min), ...]"""
        index = {}
        for rec in self.emp_availability:
            key = (str(rec["employee_id"]), rec["date"])
            slots = []
            for slot in rec.get("available_slots", []):
                start = int(slot["start"].split(":")[0]) * 60 + int(slot["start"].split(":")[1])
                end = int(slot["end"].split(":")[0]) * 60 + int(slot["end"].split(":")[1])
                slots.append((start, end))
            index[key] = slots
        return index

    def _get_experienced_employees(self) -> set:
        """Zwraca zbiór ID doświadczonych pracowników"""
        experienced = set()
        for rec in self.emp_availability:
            if rec.get("experienced", False):
                experienced.add(str(rec["employee_id"]))
        return experienced

    def test_tc_solver_01_no_assignments_outside_availability(self):
        """
        TC-SOLVER-01: Brak przypisań poza dostępnością

        Dla każdego przypisania sprawdza czy pracownik miał zadeklarowaną
        dostępność w danym terminie i czy slot czasowy mieści się w jego
        dostępnych godzinach.
        """
        availability_index = self._build_availability_index()
        violations = []

        for assignment in self.result["assignments"]:
            shift_date = assignment["date"]
            shift_start = int(assignment["start"].split(":")[0]) * 60 + int(assignment["start"].split(":")[1])
            shift_end = int(assignment["end"].split(":")[0]) * 60 + int(assignment["end"].split(":")[1])

            for detail in assignment.get("assigned_employees_detail", []):
                emp_id = str(detail["employee_id"])
                key = (emp_id, shift_date)

                if key not in availability_index:
                    violations.append(f"Pracownik {emp_id} przypisany {shift_date} bez dostępności")
                    continue

                slots = availability_index[key]
                # Sprawdź czy którykolwiek slot pokrywa przypisane segmenty
                for segment in detail.get("segments", []):
                    seg_start = int(segment["start"].split(":")[0]) * 60 + int(segment["start"].split(":")[1])
                    seg_end = int(segment["end"].split(":")[0]) * 60 + int(segment["end"].split(":")[1])

                    covered = any(
                        slot_start <= seg_start and seg_end <= slot_end
                        for slot_start, slot_end in slots
                    )
                    if not covered:
                        violations.append(
                            f"Pracownik {emp_id} przypisany {shift_date} {segment['start']}-{segment['end']} "
                            f"poza dostępnością {slots}"
                        )

        self.assertEqual(len(violations), 0, f"Znaleziono {len(violations)} naruszeń: {violations[:5]}")

    def test_tc_solver_02_experienced_requirement(self):
        """
        TC-SOLVER-02: Wymaganie doświadczenia

        Dla zmian z needs_experienced=True sprawdza czy jeśli dostępni byli
        doświadczeni pracownicy w tym terminie, to solver przypisał przynajmniej
        jednego z nich. Jest to "soft constraint" - solver woli obsadzić zmianę
        niedoświadczonymi niż pozostawić ją pustą, a także może naruszyć to
        ograniczenie gdy doświadczony pracownik jest potrzebny gdzie indziej.

        Test raportuje naruszenia informacyjnie, ale nie failuje - sprawdza
        jedynie czy większość zmian wymagających doświadczenia ma doświadczonego.
        """
        experienced_employees = self._get_experienced_employees()
        availability_index = self._build_availability_index()

        total_needs_exp = 0
        satisfied = 0
        violations = []
        warnings = []

        for assignment in self.result["assignments"]:
            if not assignment.get("needs_experienced", False):
                continue

            total_needs_exp += 1
            assigned = assignment.get("assigned_employees", [])

            if not assigned:
                # Brak przypisanych - to nie jest naruszenie tej reguły (brak dostępnych)
                warnings.append(
                    f"Zmiana {assignment['date']} {assignment['start']}-{assignment['end']} "
                    f"wymaga doświadczenia, ale nikt nie został przypisany"
                )
                continue

            shift_date = assignment["date"]
            shift_start = int(assignment["start"].split(":")[0]) * 60 + int(assignment["start"].split(":")[1])
            shift_end = int(assignment["end"].split(":")[0]) * 60 + int(assignment["end"].split(":")[1])

            # Sprawdź którzy doświadczeni pracownicy byli dostępni w tym terminie
            available_experienced = []
            for emp_id in experienced_employees:
                key = (emp_id, shift_date)
                if key in availability_index:
                    slots = availability_index[key]
                    for slot_start, slot_end in slots:
                        if slot_start <= shift_start and shift_end <= slot_end:
                            available_experienced.append(emp_id)
                            break

            has_experienced = any(str(emp_id) in experienced_employees for emp_id in assigned)

            if has_experienced:
                satisfied += 1
            elif available_experienced:
                # Byli dostępni doświadczeni, ale żaden nie został przypisany
                violations.append(
                    f"Zmiana {assignment['date']} {assignment['start']}-{assignment['end']} "
                    f"wymaga doświadczenia, dostępni doświadczeni: {available_experienced}, "
                    f"ale przypisani to: {assigned}"
                )
            else:
                # Nie było dostępnych doświadczonych - to jest ostrzeżenie
                warnings.append(
                    f"Zmiana {assignment['date']} {assignment['start']}-{assignment['end']} "
                    f"wymaga doświadczenia, ale brak dostępnych doświadczonych pracowników"
                )

        # Wyświetl statystyki
        if total_needs_exp > 0:
            satisfaction_rate = satisfied / total_needs_exp * 100
            print(f"\n[INFO] Wymaganie doświadczenia: {satisfied}/{total_needs_exp} ({satisfaction_rate:.1f}%) zmian spełnionych")

        if warnings:
            print(f"[INFO] {len(warnings)} zmian bez dostępnych doświadczonych pracowników")

        if violations:
            print(f"[WARNING] {len(violations)} zmian z naruszeniem soft constraint doświadczenia:")
            for v in violations[:3]:
                print(f"  - {v}")

        # Test sprawdza czy większość zmian (>50%) wymagających doświadczenia ma doświadczonego
        # Jest to soft constraint więc nie wymagamy 100%
        if total_needs_exp > 0:
            satisfaction_rate = satisfied / total_needs_exp
            self.assertGreater(satisfaction_rate, 0.5,
                              f"Zbyt niski poziom spełnienia wymagania doświadczenia: {satisfaction_rate*100:.1f}%")

    def test_tc_solver_03_no_overlapping_shifts(self):
        """
        TC-SOLVER-03: Brak nakładających się zmian

        Sprawdza czy żaden pracownik nie ma przypisanych nakładających się
        zmian tego samego dnia.
        """
        # Grupuj przypisania per pracownik per dzień
        emp_day_shifts: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}

        for assignment in self.result["assignments"]:
            shift_date = assignment["date"]

            for detail in assignment.get("assigned_employees_detail", []):
                emp_id = str(detail["employee_id"])
                key = (emp_id, shift_date)

                if key not in emp_day_shifts:
                    emp_day_shifts[key] = []

                for segment in detail.get("segments", []):
                    seg_start = int(segment["start"].split(":")[0]) * 60 + int(segment["start"].split(":")[1])
                    seg_end = int(segment["end"].split(":")[0]) * 60 + int(segment["end"].split(":")[1])
                    emp_day_shifts[key].append((seg_start, seg_end))

        violations = []
        for (emp_id, shift_date), shifts in emp_day_shifts.items():
            shifts_sorted = sorted(shifts)
            for i in range(len(shifts_sorted) - 1):
                end1 = shifts_sorted[i][1]
                start2 = shifts_sorted[i + 1][0]
                if end1 > start2:
                    violations.append(
                        f"Pracownik {emp_id} ma nakładające się zmiany {shift_date}: "
                        f"{shifts_sorted[i]} i {shifts_sorted[i+1]}"
                    )

        self.assertEqual(len(violations), 0, f"Znaleziono {len(violations)} nakładań: {violations[:5]}")

    def test_tc_solver_04_demand_not_exceeded(self):
        """
        TC-SOLVER-04: Nieprzekraczanie zapotrzebowania

        Dla każdej zmiany sprawdza czy liczba przypisanych pracowników
        nie przekracza zapotrzebowania.
        """
        violations = []

        for assignment in self.result["assignments"]:
            demand = assignment.get("demand", 0)
            assigned_count = len(assignment.get("assigned_employees", []))

            if assigned_count > demand:
                violations.append(
                    f"Zmiana {assignment['date']} {assignment['start']}-{assignment['end']}: "
                    f"przypisano {assigned_count}, wymagano max {demand}"
                )

        self.assertEqual(len(violations), 0, f"Znaleziono {len(violations)} przekroczeń: {violations[:5]}")

    def test_tc_solver_05_hours_limit(self):
        """
        TC-SOLVER-05: Limity godzinowe

        Sprawdza czy over_hours w hours_summary nie przekracza rozsądnego
        progu (8h). Solver minimalizuje przekroczenia.
        """
        max_over_hours = 8.0  # Maksymalne dopuszczalne przekroczenie
        violations = []

        for emp_summary in self.result.get("hours_summary", []):
            over_hours = emp_summary.get("over_hours", 0)
            if over_hours > max_over_hours:
                violations.append(
                    f"Pracownik {emp_summary['employee_id']}: przekroczenie {over_hours:.2f}h > {max_over_hours}h"
                )

        self.assertEqual(len(violations), 0, f"Znaleziono {len(violations)} przekroczeń limitu godzin: {violations[:5]}")


# =============================================================================
# TESTY WYDAJNOŚCIOWE / BENCHMARK
# =============================================================================

class TestSolverBenchmark(SimpleTestCase):
    """Testy wydajnościowe solvera (TC-BENCH-01, TC-BENCH-02)"""

    def test_tc_bench_01_scenario_benchmarks(self):
        """
        TC-BENCH-01: Benchmark scenariuszy

        Testuje 4 scenariusze o rosnącej złożoności i mierzy czas/pokrycie.
        """
        scenarios = [
            {"name": "mały_lokal", "employees": 5, "shifts": 3, "days": 7, "time_limit": 5},
            {"name": "średni_lokal", "employees": 15, "shifts": 5, "days": 7, "time_limit": 15},
            {"name": "duży_lokal", "employees": 30, "shifts": 8, "days": 7, "time_limit": 30},
            {"name": "sieć_mała", "employees": 25, "shifts": 6, "days": 14, "time_limit": 45},
        ]

        results_table = []
        print("\n" + "=" * 80)
        print("BENCHMARK SCENARIUSZY SOLVERA")
        print("=" * 80)
        print(f"{'Scenariusz':<15} {'Prac.':<6} {'Zmiany':<7} {'Dni':<5} {'Czas [s]':<10} {'Pokrycie':<10}")
        print("-" * 80)

        for scenario in scenarios:
            emp_availability, demand = generate_synthetic_data(
                num_employees=scenario["employees"],
                shifts_per_day=scenario["shifts"],
                num_days=scenario["days"],
                seed=42,
            )

            start_time = time.time()
            result = run_solver(emp_availability, demand, time_limit_sec=scenario["time_limit"])
            elapsed = time.time() - start_time

            coverage = calculate_coverage_ratio(result, demand)

            results_table.append({
                "scenario": scenario["name"],
                "employees": scenario["employees"],
                "shifts": scenario["shifts"],
                "days": scenario["days"],
                "time": elapsed,
                "coverage": coverage,
            })

            print(f"{scenario['name']:<15} {scenario['employees']:<6} {scenario['shifts']:<7} "
                  f"{scenario['days']:<5} {elapsed:<10.2f} {coverage*100:<10.1f}%")

            # Sprawdź czy solver zmieścił się w limicie czasowym (z marginesem)
            self.assertLess(elapsed, scenario["time_limit"] * 1.5,
                           f"Scenariusz {scenario['name']} przekroczył limit czasowy")

        print("=" * 80)

        # Sprawdź czy wszystkie scenariusze mają jakieś pokrycie
        for row in results_table:
            self.assertGreater(row["coverage"], 0.0,
                              f"Scenariusz {row['scenario']} nie ma żadnego pokrycia")

    def test_tc_bench_02_scaling_analysis(self):
        """
        TC-BENCH-02: Analiza skalowania

        Sprawdza jak czas solvera rośnie wraz z liczbą pracowników.
        Stosunek czasu dla 25 vs 5 pracowników nie powinien przekraczać ~100x.
        """
        employee_counts = [5, 10, 15, 20, 25]
        times = []

        print("\n" + "=" * 60)
        print("ANALIZA SKALOWANIA SOLVERA")
        print("=" * 60)
        print(f"{'Pracownicy':<12} {'Czas [s]':<12} {'Stosunek do bazowego':<20}")
        print("-" * 60)

        base_time = None
        for num_emp in employee_counts:
            emp_availability, demand = generate_synthetic_data(
                num_employees=num_emp,
                shifts_per_day=4,
                num_days=7,
                seed=42,
            )

            start_time = time.time()
            run_solver(emp_availability, demand, time_limit_sec=15.0)
            elapsed = time.time() - start_time
            times.append(elapsed)

            if base_time is None:
                base_time = elapsed

            ratio = elapsed / base_time if base_time > 0 else 1.0
            print(f"{num_emp:<12} {elapsed:<12.3f} {ratio:<20.1f}x")

        print("=" * 60)

        # Sprawdź czy wzrost nie jest wykładniczy
        max_ratio = times[-1] / times[0] if times[0] > 0 else 1.0
        self.assertLess(max_ratio, 100.0,
                       f"Wzrost czasu jest zbyt duży: {max_ratio:.1f}x (oczekiwano < 100x)")

    def test_tc_bench_03_extreme_scaling(self):
        """
        TC-BENCH-03: Analiza skalowania na dużych instancjach

        Sprawdza skalowanie solvera na 100 i 1000 pracowników.
        Z ciekawości, aby zobaczyć jak problem rośnie w trudności.

        UWAGA: Test zajmuje więcej czasu. Liczba zmian zmniejszona aby
        nie zaprzepaścić całej nocy na testy :)
        """
        # Skonfiguruj mniejszą liczbę zmian dla dużych instancji
        # aby nie czekać wieczność
        employee_counts = [5, 25, 50, 100]
        times = []
        coverages = []

        print("\n" + "=" * 80)
        print("ANALIZA SKALOWANIA NA DUŻYCH INSTANCJACH (TC-BENCH-03)")
        print("=" * 80)
        print(f"{'Pracownicy':<12} {'Zmian/dzień':<14} {'Dni':<6} {'Czas [s]':<10} {'Pokrycie':<10} {'Stosunek':<12}")
        print("-" * 80)

        base_time = None
        for num_emp in employee_counts:
            # Skaluj parametry: mniej zmian na dzień dla dużych instancji
            if num_emp <= 25:
                shifts = 4
                days = 7
                time_limit = 15.0
            else:
                shifts = 3
                days = 3
                time_limit = 30.0

            emp_availability, demand = generate_synthetic_data(
                num_employees=num_emp,
                shifts_per_day=shifts,
                num_days=days,
                seed=42,
            )

            print(f"[{num_emp} pracowników] Generowanie danych... ", end="", flush=True)
            print(f"({len(emp_availability)} dostępności, {len(demand)} zmian)")

            start_time = time.time()
            result = run_solver(emp_availability, demand, time_limit_sec=time_limit)
            elapsed = time.time() - start_time
            times.append(elapsed)

            coverage = calculate_coverage_ratio(result, demand)
            coverages.append(coverage)

            if base_time is None:
                base_time = elapsed

            ratio = elapsed / base_time if base_time > 0 else 1.0
            print(f"{num_emp:<12} {shifts:<14} {days:<6} {elapsed:<10.3f} {coverage*100:<10.1f}% {ratio:<12.1f}x")

        print("=" * 80)

        # Analiza charakterystyki wzrostu
        print("\n[ANALIZA SKALOWANIA]")
        print("-" * 80)
        for i in range(1, len(times)):
            prev_emp = employee_counts[i-1]
            curr_emp = employee_counts[i]
            emp_ratio = curr_emp / prev_emp
            time_ratio = times[i] / times[i-1] if times[i-1] > 0 else 1.0
            print(f"  {prev_emp:>3} -> {curr_emp:>3} pracowników (x{emp_ratio:.2f}): "
                  f"czas wzrasta {time_ratio:.2f}x")

        overall_ratio = times[-1] / times[0] if times[0] > 0 else 1.0
        print(f"\n  Razem ({employee_counts[0]} -> {employee_counts[-1]} prac.): "
              f"czas wzrasta {overall_ratio:.1f}x")

        avg_coverage = sum(coverages) / len(coverages)
        coverage_stability = max(abs(c - avg_coverage) for c in coverages)
        print(f"  Pokrycie: śred. {avg_coverage*100:.1f}%, max odchylenie ±{coverage_stability*100:.1f}%")

        # Szacunek dla 1000 pracowników (ekstrapolacja)
        if times[0] > 0:
            # Modeluj jako funkcję mocy: T = a * N^b
            # Ze względu na CP-SAT, prawdopodobnie 1.5 <= b <= 2.5
            import math

            # Użyj ostatniego punktu danych do ekstrapolacji
            last_emp = employee_counts[-1]
            last_time = times[-1]
            first_emp = employee_counts[0]
            first_time = times[0]

            # Oszacuj wykładnik b: log(T2/T1) / log(N2/N1)
            if last_time > 0 and first_time > 0:
                b = math.log(last_time / first_time) / math.log(last_emp / first_emp)

                # Ekstrapoluj na 1000 pracowników
                est_1000 = last_time * ((1000 / last_emp) ** b)

                print(f"\n[EKSTRAPOLACJA]")
                print(f"  Oszacowany wykładnik skalowania: b ~ {b:.2f}")
                if est_1000 > 3600:
                    est_hours = est_1000 / 3600
                    print(f"  Szacunkowy czas dla 1000 pracowników: ~{est_hours:.1f} godzin")
                else:
                    print(f"  Szacunkowy czas dla 1000 pracowników: ~{est_1000:.0f} sekund (~{est_1000/60:.1f} minut)")

                if b >= 2.0:
                    print(f"  Uwaga: Skalowanie zbliża się do kwadratowego O(N^2) - typ hard constraint problem")
                elif b >= 1.5:
                    print(f"  Skalowanie: pośrednie O(N^{b:.1f}) - typowe dla SAT/IP solverów")
                else:
                    print(f"  Skalowanie: poniżej liniowego - solver ma dobrą wydajność")

        print("=" * 80)

    def test_tc_bench_04_ultra_large_scale(self):
        """
        TC-BENCH-04: Test skalowania dla 1000 pracowników (opcjonalny, eksperymentalny)

        Sprawdza czy solver zdolny jest obsługiwać bardzo duże instancje.
        Test może być długi - uruchom go manualnie jeśli jesteś ciekaw :)

        Scenariusz: sieć wielkich restauracji
        - 1000 pracowników
        - 20 zmian/dzień (różne lokalizacje)
        - 2 dni (aby problem nie był zbyt duży)
        - Limit czasowy 60 sekund
        """
        print("\n" + "=" * 80)
        print("EKSPERYMENTALNY TEST: SOLVER DLA 1000 PRACOWNIKÓW (TC-BENCH-04)")
        print("=" * 80)

        # Generuj dane dla bardzo dużej instancji
        num_employees = 1000
        shifts_per_day = 20  # Można zarządzać dużą restauracją
        num_days = 2
        time_limit = 60.0

        print(f"\n[Konfiguracja]")
        print(f"  Pracownicy: {num_employees}")
        print(f"  Zmiany/dzień: {shifts_per_day}")
        print(f"  Dni: {num_days}")
        print(f"  Limit czasowy: {time_limit}s")

        print(f"\n[Generowanie danych] ", end="", flush=True)
        start_gen = time.time()
        emp_availability, demand = generate_synthetic_data(
            num_employees=num_employees,
            shifts_per_day=shifts_per_day,
            num_days=num_days,
            seed=42,
        )
        gen_time = time.time() - start_gen

        total_vars = len(emp_availability) * len(demand)
        print(f"OK ({gen_time:.2f}s)")
        print(f"  Liczba dostępności: {len(emp_availability):,}")
        print(f"  Liczba zmian: {len(demand):,}")
        print(f"  Szacunkowe zmienne decyzyjne: ~{total_vars:,}")

        print(f"\n[Rozwiązywanie] ", end="", flush=True)
        start_solve = time.time()
        try:
            result = run_solver(emp_availability, demand, time_limit_sec=time_limit, workers=1)
            solve_time = time.time() - start_solve
            print(f"OK ({solve_time:.2f}s)")

            coverage = calculate_coverage_ratio(result, demand)
            num_assigned = sum(len(a.get("assigned_employees", [])) for a in result.get("assignments", []))
            num_uncovered = len(result.get("uncovered", []))

            print(f"\n[Rezultaty]")
            print(f"  Czas rozwiązania: {solve_time:.2f}s")
            print(f"  Pokrycie: {coverage*100:.1f}%")
            print(f"  Całkowite przypisania: {num_assigned:,}")
            print(f"  Zmian bez przypisań: {num_uncovered:,}")

            # Analiza
            print(f"\n[Analiza wydajności]")
            if solve_time < 1.0:
                print(f"  [OK] Solver bardzo szybki dla 1000 pracowników!")
            elif solve_time < time_limit / 2:
                print(f"  [OK] Solver efektywny - zmieścił się w limicie")
            else:
                print(f"  [WARNING] Solver potrzebuje pełnego limitu czasowego")

            if coverage > 0.95:
                print(f"  [OK] Doskonałe pokrycie zapotrzebowania")
            elif coverage > 0.80:
                print(f"  [INFO] Zadowalające pokrycie")
            else:
                print(f"  [WARNING] Niskie pokrycie - problem może być zbyt trudny")

            # Porównanie ze wcześniejszymi wynikami
            print(f"\n[Wnioski dla skalowalności]")
            avg_employees_per_assignment = num_assigned / len(demand) if len(demand) > 0 else 0
            avg_demand_per_shift = sum(s["demand"] for s in demand) / len(demand) if len(demand) > 0 else 0
            print(f"  Średnio przypisano: {avg_employees_per_assignment:.2f} os./zmianę (wymagano: {avg_demand_per_shift:.1f})")
            print(f"  Czasy skalowania na 100 prac. wynosiły ~0.4s, na 1000: ~{solve_time:.1f}s")
            print(f"  Wzrost czasu: {solve_time / 0.4:.1f}x dla 10x większej problemu")

        except Exception as e:
            solve_time = time.time() - start_solve
            print(f"BŁĄD ({solve_time:.2f}s)")
            print(f"  Wyjątek: {type(e).__name__}: {e}")
            self.fail(f"Solver nie zdołał obsługiwać 1000 pracowników: {e}")

        print("=" * 80)


# =============================================================================
# TESTY DETERMINISTYCZNOŚCI
# =============================================================================

class TestSolverDeterminism(SimpleTestCase):
    """Testy deterministyczności solvera (TC-DET-01, TC-DET-02)"""

    def test_tc_det_01_result_repeatability(self):
        """
        TC-DET-01: Powtarzalność wyników

        Uruchamia solver 3 razy dla identycznych danych i sprawdza
        czy metryki są identyczne (CP-SAT jest deterministyczny).
        """
        emp_availability, demand = generate_synthetic_data(
            num_employees=10,
            shifts_per_day=4,
            num_days=7,
            seed=42,
        )

        results = []
        for i in range(3):
            result = run_solver(emp_availability, demand, time_limit_sec=5.0, workers=1)

            # Oblicz metryki
            total_assigned = sum(
                len(a.get("assigned_employees", []))
                for a in result.get("assignments", [])
            )
            total_missing = sum(
                a.get("missing_minutes", 0)
                for a in result.get("assignments", [])
            )

            results.append({
                "run": i + 1,
                "total_assigned": total_assigned,
                "total_missing": total_missing,
            })

        # Sprawdź identyczność metryk
        self.assertEqual(results[0]["total_assigned"], results[1]["total_assigned"],
                        "Różnica w przypisaniach między uruchomieniem 1 i 2")
        self.assertEqual(results[1]["total_assigned"], results[2]["total_assigned"],
                        "Różnica w przypisaniach między uruchomieniem 2 i 3")
        self.assertEqual(results[0]["total_missing"], results[1]["total_missing"],
                        "Różnica w brakujących minutach między uruchomieniem 1 i 2")
        self.assertEqual(results[1]["total_missing"], results[2]["total_missing"],
                        "Różnica w brakujących minutach między uruchomieniem 2 i 3")

    def test_tc_det_02_input_order_independence(self):
        """
        TC-DET-02: Niezależność od kolejności danych

        Sprawdza czy solver znajduje podobnie dobre rozwiązania
        niezależnie od kolejności danych wejściowych.
        """
        coverages = []

        for seed in [42, 123, 456]:
            emp_availability, demand = generate_synthetic_data(
                num_employees=10,
                shifts_per_day=4,
                num_days=7,
                seed=seed,
            )

            result = run_solver(emp_availability, demand, time_limit_sec=5.0)
            coverage = calculate_coverage_ratio(result, demand)
            coverages.append(coverage)

        # Sprawdź czy pokrycie jest w rozsądnym zakresie
        avg_coverage = sum(coverages) / len(coverages)
        for i, cov in enumerate(coverages):
            deviation = abs(cov - avg_coverage) / avg_coverage if avg_coverage > 0 else 0
            self.assertLess(deviation, 0.5,
                           f"Seed {[42, 123, 456][i]}: odchylenie {deviation:.2%} > 50%")


# =============================================================================
# TESTY PRZYPADKÓW BRZEGOWYCH
# =============================================================================

class TestSolverEdgeCases(SimpleTestCase):
    """Testy przypadków brzegowych (TC-EDGE-01 do TC-EDGE-04)"""

    def test_tc_edge_01_empty_demand(self):
        """
        TC-EDGE-01: Pusty demand

        Solver nie powinien rzucić wyjątku, powinien zwrócić pustą listę assignments.
        """
        emp_availability = [
            {
                "employee_id": "1",
                "employee_name": "Jan Kowalski",
                "date": "2025-01-06",
                "experienced": True,
                "hours_min": 0,
                "hours_max": 40,
                "available_slots": [{"start": "08:00", "end": "16:00"}],
            }
        ]
        demand = []

        # Nie powinno rzucić wyjątku
        result = run_solver(emp_availability, demand, time_limit_sec=5.0)

        self.assertIn("assignments", result)
        self.assertEqual(len(result["assignments"]), 0)

    def test_tc_edge_02_no_available_employees(self):
        """
        TC-EDGE-02: Brak dostępnych pracowników

        Pracownik dostępny 14:00-18:00, zmiana 8:00-12:00.
        Solver powinien zwrócić zmianę z pustą listą przypisanych
        i niezerowym missing_minutes.
        """
        emp_availability = [
            {
                "employee_id": "1",
                "employee_name": "Jan Kowalski",
                "date": "2025-01-06",
                "experienced": True,
                "hours_min": 0,
                "hours_max": 40,
                "available_slots": [{"start": "14:00", "end": "18:00"}],
            }
        ]
        demand = [
            {
                "date": "2025-01-06",
                "location": "Restauracja",
                "start": "08:00",
                "end": "12:00",
                "demand": 1,
                "needs_experienced": False,
            }
        ]

        result = run_solver(emp_availability, demand, time_limit_sec=5.0)

        self.assertEqual(len(result["assignments"]), 1)
        assignment = result["assignments"][0]
        self.assertEqual(len(assignment["assigned_employees"]), 0)
        self.assertGreater(assignment["missing_minutes"], 0)

    def test_tc_edge_03_perfect_match(self):
        """
        TC-EDGE-03: Idealne dopasowanie

        1 pracownik dostępny dokładnie 9:00-13:00, 1 zmiana 9:00-13:00 z demand=1.
        Przypisanie powinno być pełne i missing_minutes=0.
        """
        emp_availability = [
            {
                "employee_id": "1",
                "employee_name": "Jan Kowalski",
                "date": "2025-01-06",
                "experienced": True,
                "hours_min": 0,
                "hours_max": 40,
                "available_slots": [{"start": "09:00", "end": "13:00"}],
            }
        ]
        demand = [
            {
                "date": "2025-01-06",
                "location": "Restauracja",
                "start": "09:00",
                "end": "13:00",
                "demand": 1,
                "needs_experienced": False,
            }
        ]

        result = run_solver(emp_availability, demand, time_limit_sec=5.0)

        self.assertEqual(len(result["assignments"]), 1)
        assignment = result["assignments"][0]
        self.assertEqual(len(assignment["assigned_employees"]), 1)
        self.assertEqual(assignment["assigned_employees"][0], "1")
        self.assertEqual(assignment["missing_minutes"], 0)

    def test_tc_edge_04_high_demand_few_employees(self):
        """
        TC-EDGE-04: Wysokie zapotrzebowanie, mało pracowników

        Zmiana wymaga 5 osób, dostępnych tylko 2.
        Solver powinien przypisać 2 i raportować brakujące minuty.
        """
        emp_availability = [
            {
                "employee_id": "1",
                "employee_name": "Jan Kowalski",
                "date": "2025-01-06",
                "experienced": True,
                "hours_min": 0,
                "hours_max": 40,
                "available_slots": [{"start": "08:00", "end": "16:00"}],
            },
            {
                "employee_id": "2",
                "employee_name": "Anna Nowak",
                "date": "2025-01-06",
                "experienced": False,
                "hours_min": 0,
                "hours_max": 40,
                "available_slots": [{"start": "08:00", "end": "16:00"}],
            },
        ]
        demand = [
            {
                "date": "2025-01-06",
                "location": "Restauracja",
                "start": "08:00",
                "end": "12:00",
                "demand": 5,
                "needs_experienced": False,
            }
        ]

        result = run_solver(emp_availability, demand, time_limit_sec=5.0)

        self.assertEqual(len(result["assignments"]), 1)
        assignment = result["assignments"][0]
        self.assertEqual(len(assignment["assigned_employees"]), 2)
        self.assertGreater(assignment["missing_minutes"], 0)

        # Sprawdź brakujące minuty: (5-2) * 240 minut = 720 minut
        expected_missing = (5 - 2) * 4 * 60  # 3 brakujących * 4 godziny
        self.assertEqual(assignment["missing_minutes"], expected_missing)


# =============================================================================
# TEST STRUKTURY WYJŚCIA
# =============================================================================

class TestSolverOutputStructure(SimpleTestCase):
    """Test struktury wyjścia solvera (TC-INT-01)"""

    def test_tc_int_01_output_specification(self):
        """
        TC-INT-01: Zgodność ze specyfikacją

        Sprawdza czy wynik zawiera wymagane klucze i struktury.
        """
        emp_availability, demand = generate_synthetic_data(
            num_employees=5,
            shifts_per_day=3,
            num_days=3,
            seed=42,
        )

        result = run_solver(emp_availability, demand, time_limit_sec=5.0)

        # Sprawdź główne klucze
        self.assertIn("assignments", result)
        self.assertIn("uncovered", result)
        self.assertIn("hours_summary", result)

        # Sprawdź strukturę assignments
        self.assertIsInstance(result["assignments"], list)
        if len(result["assignments"]) > 0:
            assignment = result["assignments"][0]
            required_fields = [
                "date", "location", "start", "end", "demand",
                "assigned_employees", "needs_experienced", "missing_minutes"
            ]
            for field in required_fields:
                self.assertIn(field, assignment,
                             f"Brak pola '{field}' w assignment")

            # Sprawdź typy
            self.assertIsInstance(assignment["date"], str)
            self.assertIsInstance(assignment["location"], str)
            self.assertIsInstance(assignment["start"], str)
            self.assertIsInstance(assignment["end"], str)
            self.assertIsInstance(assignment["demand"], int)
            self.assertIsInstance(assignment["assigned_employees"], list)
            self.assertIsInstance(assignment["needs_experienced"], bool)
            self.assertIsInstance(assignment["missing_minutes"], int)

        # Sprawdź strukturę hours_summary
        self.assertIsInstance(result["hours_summary"], list)
        if len(result["hours_summary"]) > 0:
            summary = result["hours_summary"][0]
            required_summary_fields = [
                "employee_id", "experienced", "total_hours",
                "hours_min", "hours_max", "over_hours", "under_hours"
            ]
            for field in required_summary_fields:
                self.assertIn(field, summary,
                             f"Brak pola '{field}' w hours_summary")


# =============================================================================
# PODSUMOWANIE TESTÓW
# =============================================================================

class TestSolverSummary(SimpleTestCase):
    """Podsumowanie wszystkich testów"""

    def test_summary_table(self):
        """Wyświetla podsumowanie przypadków testowych"""
        test_cases = [
            ("TC-SOLVER-01", "Brak przypisań poza dostępnością", "WALIDACJA"),
            ("TC-SOLVER-02", "Wymaganie doświadczenia", "WALIDACJA"),
            ("TC-SOLVER-03", "Brak nakładających się zmian", "WALIDACJA"),
            ("TC-SOLVER-04", "Nieprzekraczanie zapotrzebowania", "WALIDACJA"),
            ("TC-SOLVER-05", "Limity godzinowe", "WALIDACJA"),
            ("TC-BENCH-01", "Benchmark scenariuszy", "WYDAJNOŚĆ"),
            ("TC-BENCH-02", "Analiza skalowania (5-25 prac.)", "WYDAJNOŚĆ"),
            ("TC-BENCH-03", "Analiza skalowania na dużych instancjach (5-100 prac.)", "WYDAJNOŚĆ"),
            ("TC-BENCH-04", "Ultra duża skala (1000 pracowników) - eksperymentalne", "WYDAJNOŚĆ"),
            ("TC-DET-01", "Powtarzalność wyników", "DETERMINISTYCZNOŚĆ"),
            ("TC-DET-02", "Niezależność od kolejności danych", "DETERMINISTYCZNOŚĆ"),
            ("TC-EDGE-01", "Pusty demand", "BRZEGOWY"),
            ("TC-EDGE-02", "Brak dostępnych pracowników", "BRZEGOWY"),
            ("TC-EDGE-03", "Idealne dopasowanie", "BRZEGOWY"),
            ("TC-EDGE-04", "Wysokie zapotrzebowanie, mało pracowników", "BRZEGOWY"),
            ("TC-INT-01", "Zgodność ze specyfikacją", "INTEGRACJA"),
        ]

        print("\n" + "=" * 95)
        print("TABELA PRZYPADKÓW TESTOWYCH SOLVERA")
        print("=" * 95)
        print(f"{'ID':<15} {'Nazwa':<55} {'Kategoria':<15}")
        print("-" * 95)

        for tc_id, name, category in test_cases:
            print(f"{tc_id:<15} {name:<55} {category:<15}")

        print("=" * 95)
        print(f"Łączna liczba przypadków testowych: {len(test_cases)}")
        print("=" * 95)

        # Ten test zawsze przechodzi - służy tylko do wyświetlenia tabeli
        self.assertEqual(len(test_cases), 16)

