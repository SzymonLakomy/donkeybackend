"""Walidator grafiku: reguly kalendarza niezalezne od tego, KTO wygenerowal grafik.

Powstal, bo AI (LLM) potrafi zwrocic ladny JSON, ktory jest fizycznie niemozliwy:
ten sam pracownik w dwoch zachodzacych na siebie zmianach. Walidator jest tania,
deterministyczna siatka bezpieczenstwa - mozna nim sprawdzic wynik solvera, reczna
korekte managera i odpowiedz dowolnego modelu.

Reguły (kazde naruszenie to jeden czytelny string):
1. kazda zmiana w grafiku istnieje w zapotrzebowaniu,
2. liczba osob na zmianie nie przekracza `demand`,
3. segmenty mieszcza sie w granicach zmiany i maja dodatnia dlugosc,
4. segmenty mieszcza sie w zadeklarowanej dostepnosci pracownika,
5. pracownik nie ma nakladajacych sie segmentow tego samego dnia,
6. opcjonalnie: minimalna dlugosc bloku pracy,
7. opcjonalnie: zmiana obsadzona musi byc pokryta od poczatku do konca.

Moduł nie dotyka bazy i nie zna Django - czysta funkcja na dictach.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Interval = Tuple[int, int]


def _to_min(hhmm: Any) -> Optional[int]:
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _bounds(start: Any, end: Any) -> Optional[Interval]:
    s, e = _to_min(start), _to_min(end)
    if s is None or e is None:
        return None
    if e <= s:
        e += 24 * 60
    return s, e


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def build_availability_index(emp_availability: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], List[Interval]]:
    """(employee_id, data) -> lista przedzialow dostepnosci w minutach."""
    index: Dict[Tuple[str, str], List[Interval]] = defaultdict(list)
    for rec in emp_availability or []:
        bounds = _bounds(rec.get("start"), rec.get("end"))
        if bounds:  # pojedynczy slot podany plasko
            index[(str(rec.get("employee_id")), str(rec.get("date")))].append(bounds)
        for slot in rec.get("available_slots") or []:
            b = _bounds(slot.get("start"), slot.get("end"))
            if b:
                index[(str(rec.get("employee_id")), str(rec.get("date")))].append(b)
    return index


def _segments_of(assignment: Dict[str, Any], shift: Interval) -> List[Tuple[str, int, int]]:
    """Zwraca (employee_id, start_min, end_min); brak detaili => cala zmiana."""
    out: List[Tuple[str, int, int]] = []
    details = assignment.get("assigned_employees_detail") or []
    if details:
        for detail in details:
            emp = str(detail.get("employee_id"))
            for seg in detail.get("segments") or []:
                b = _bounds(seg.get("start"), seg.get("end"))
                if b:
                    out.append((emp, b[0], b[1]))
            if not detail.get("segments"):
                b = _bounds(detail.get("start"), detail.get("end"))
                if b:
                    out.append((emp, b[0], b[1]))
    else:
        for emp in assignment.get("assigned_employees") or []:
            out.append((str(emp), shift[0], shift[1]))
    return out


def validate_schedule(assignments: Sequence[Dict[str, Any]],
                      emp_availability: Iterable[Dict[str, Any]],
                      demand: Optional[Sequence[Dict[str, Any]]] = None,
                      min_block_minutes: int = 0,
                      require_full_shift: bool = False) -> List[str]:
    """Zwraca liste naruszen; pusta lista = grafik przechodzi walidacje."""
    violations: List[str] = []
    avail = build_availability_index(emp_availability)

    demand_keys = set()
    for row in demand or []:
        b = _bounds(row.get("start"), row.get("end"))
        if b:
            demand_keys.add((str(row.get("date")), str(row.get("location")),
                             b[0], b[1], int(row.get("demand", 0) or 0)))

    per_employee_day: Dict[Tuple[str, str], List[Tuple[int, int, str]]] = defaultdict(list)

    for idx, assignment in enumerate(assignments or []):
        shift = _bounds(assignment.get("start"), assignment.get("end"))
        price = f"zmiana #{idx} {assignment.get('date')} {assignment.get('start')}-{assignment.get('end')}"
        if shift is None:
            violations.append(f"{price}: niepoprawny format godziny")
            continue

        date = str(assignment.get("date"))
        location = str(assignment.get("location"))
        demand_count = int(assignment.get("demand", 0) or 0)

        if demand_keys and (date, location, shift[0], shift[1], demand_count) not in demand_keys:
            violations.append(f"{price}: nie ma takiej zmiany w zapotrzebowaniu")

        segments = _segments_of(assignment, shift)
        people = {emp for emp, _s, _e in segments}
        if len(people) > demand_count:
            violations.append(
                f"{price}: {len(people)} osob przy zapotrzebowaniu {demand_count} "
                f"({', '.join(sorted(people))})"
            )

        covered: List[Tuple[int, int]] = []
        for emp, start, end in segments:
            if start >= end:
                violations.append(f"{price}: {emp} ma segment o niedodatniej dlugosci")
                continue
            if start < shift[0] or end > shift[1]:
                violations.append(
                    f"{price}: {emp} poza granicami zmiany "
                    f"({_fmt(start)}-{_fmt(end)})"
                )
            if end - start < min_block_minutes:
                violations.append(
                    f"{price}: {emp} ma blok {end - start} min, minimum to {min_block_minutes} min"
                )
            slots = avail.get((emp, date), [])
            if not any(s <= start and end <= e for s, e in slots):
                declared = ", ".join(f"{_fmt(s)}-{_fmt(e)}" for s, e in slots) or "brak"
                violations.append(
                    f"{price}: {emp} przypisany {_fmt(start)}-{_fmt(end)}, "
                    f"a zadeklarowal {declared}"
                )
            per_employee_day[(emp, date)].append((start, end, price))
            covered.append((start, end))

        if require_full_shift and covered:
            covered_sorted = sorted(covered)
            merged = [list(covered_sorted[0])]
            for s, e in covered_sorted[1:]:
                if s <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])
            if merged[0][0] > shift[0] or merged[-1][1] < shift[1] or len(merged) > 1:
                violations.append(f"{price}: zmiana nie jest pokryta w calosci")

    for (emp, date), spans in per_employee_day.items():
        spans_sorted = sorted(spans)
        for (s1, e1, p1), (s2, e2, p2) in zip(spans_sorted, spans_sorted[1:]):
            if s2 < e1:
                violations.append(
                    f"{date} {emp}: nakladka {_fmt(s1)}-{_fmt(e1)} i {_fmt(s2)}-{_fmt(e2)}"
                )

    return violations
