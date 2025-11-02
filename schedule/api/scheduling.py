from __future__ import annotations

from typing import Any, Dict, List, Optional

from django.db import transaction
from ninja.errors import HttpError

from ..models import Availability, DayDemandIndex, Demand, ScheduleShift
from ..schemas import (
    GenerateDayIn,
    GenerateRangeIn,
    GenerateResultOut,
    ScheduleShiftOut,
    ShiftOut,
    ShiftUpdateIn,
)
from ..solver import run_solver
from .router import api
from .utils import (
    _apply_special_rules_to_demand,
    _assignments_for_day_from_db,
    _assignments_from_db,
    _build_emp_availability,
    _canonicalize_day_items,
    _day_hash,
    _get_company_for_request,
    _get_default_template,
    _get_or_build_day_index,
    _hash_payload,
    _infer_location,
    _populate_day_index_for_demand,
    _shift_base_dict,
    _shift_uid,
    _with_ids,
)


def _ensure_schedule_for_demand(
    d: Demand, force: bool = False
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Ensures schedule is generated for given demand; returns (assignments, summary)"""
    # If shifts already exist and not forcing, return from DB
    if not force and d.shifts.exists():
        # ensure index exists (safety)
        _populate_day_index_for_demand(d)
        return _assignments_from_db(d), None

    # When forcing, clear existing
    if force and d.shifts.exists():
        d.shifts.all().delete()

    # Build availability input from DB for the demand date range
    avail_qs = Availability.objects.filter(date__gte=d.date_from, date__lte=d.date_to)
    emp_avail = []
    for a in avail_qs:
        emp_avail.append(
            dict(
                employee_id=a.employee_id,
                employee_name=a.employee_name,
                date=a.date.isoformat(),
                experienced=bool(a.experienced),
                hours_min=int(a.hours_min or 0),
                hours_max=int(a.hours_max or 1_000_000_000),
                available_slots=list(a.available_slots or []),
                assigned_shift=a.assigned_shift or None,
            )
        )

    # Apply special rules (holidays/events) before solving
    demand_payload = _apply_special_rules_to_demand(d.raw_payload or [], d.date_from, d.date_to)

    res = run_solver(emp_availability=emp_avail, demand=demand_payload)

    # Persist assignments per day/shift
    ass = res.get("assignments", []) or []
    to_create = []
    from datetime import date as _date

    for a in ass:
        uid = _shift_uid(d.id, a)
        meta_payload = {
            "uncovered": res.get("uncovered", []),
            "hours_summary": res.get("hours_summary", []),
        }
        if a.get("assigned_employees_detail") is not None:
            meta_payload["assigned_employees_detail"] = list(a.get("assigned_employees_detail") or [])
        if a.get("missing_segments") is not None:
            meta_payload["missing_segments"] = list(a.get("missing_segments") or [])

        to_create.append(
            ScheduleShift(
                demand=d,
                shift_uid=uid,
                date=_date.fromisoformat(a["date"]),
                location=a["location"],
                start=a["start"],
                end=a["end"],
                demand_count=int(a.get("demand", 1)),
                needs_experienced=bool(a.get("needs_experienced", False)),
                assigned_employees=list(a.get("assigned_employees", []) or []),
                missing_minutes=int(a.get("missing_minutes", 0) or 0),
                meta=meta_payload,
            )
        )
    if to_create:
        ScheduleShift.objects.bulk_create(to_create, ignore_conflicts=True)
    # Mark generated
    if to_create:
        d.schedule_generated = True
        try:
            from django.utils import timezone

            d.solved_at = timezone.now()
        except Exception:
            d.solved_at = None
        d.save(update_fields=["schedule_generated", "solved_at", "updated_at"])

    return _assignments_from_db(d), {
        "uncovered": res.get("uncovered", []),
        "hours_summary": res.get("hours_summary", []),
    } if to_create else None


@api.get(
    "/schedule/{demand_id}",
    openapi_extra={
        "summary": "Pobierz lub wygeneruj grafik",
        "description": "Jeżeli dla zapotrzebowania istnieją zapisane zmiany zwracamy je, w przeciwnym razie wywołujemy solver i zapisujemy wynik.",
    },
)
@transaction.atomic
def get_or_generate_schedule(request, demand_id: int, force: bool = False) -> List[ScheduleShiftOut]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        d = Demand.objects.get(id=demand_id)
    except Demand.DoesNotExist:
        raise HttpError(404, "Demand not found")

    assignments, _summary = _ensure_schedule_for_demand(d, force=force)
    return assignments


@api.get(
    "/days/{day}",
    openapi_extra={
        "summary": "Grafik dla konkretnego dnia",
        "description": "Zwraca zapisane zmiany dla dnia i restauracji lub generuje je na podstawie ostatniego zapotrzebowania.",
    },
)
@transaction.atomic
def get_day_schedule(request, day: str, location: Optional[str] = None) -> List[ScheduleShiftOut]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    loc = _infer_location(request, location)
    # If we already have persisted shifts for that date/location across any demand, return them
    shifts_qs = ScheduleShift.objects.filter(date=day, location=loc)
    if shifts_qs.exists():
        return [
            dict(
                id=s.shift_uid,
                date=s.date.isoformat(),
                location=s.location,
                start=s.start,
                end=s.end,
                demand=s.demand_count,
                assigned_employees=list(s.assigned_employees or []),
                needs_experienced=bool(s.needs_experienced),
                missing_minutes=int(s.missing_minutes or 0),
            )
            for s in shifts_qs.order_by("start", "end")
        ]
    # No persisted shifts — try to find a weekly demand through DayDemandIndex
    idx = _get_or_build_day_index(day, loc)
    if not idx:
        # Nothing known for this date/location
        return []
    d = idx.demand
    assignments, _summary = _ensure_schedule_for_demand(d, force=False)
    # filter to that day/location
    return _assignments_for_day_from_db(d, day, location=loc)


@api.post(
    "/generate-day",
    response=GenerateResultOut,
    openapi_extra={
        "summary": "Ułóż grafik na jeden dzień",
        "description": "Uruchamia solver dla podanej daty. Gdy lista zmian nie jest przesłana korzystamy z domyślnego zapotrzebowania.",
    },
)
@transaction.atomic
def generate_day(request, payload: GenerateDayIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    loc = _infer_location(request, payload.location, create_if_missing=True)
    company = _get_company_for_request(request)
    day = payload.date
    from datetime import date as _date

    try:
        day_dt = _date.fromisoformat(day)
    except Exception:
        raise HttpError(400, "Invalid date format. Użyj YYYY-MM-DD")

    weekday = day_dt.weekday()
    # Canonicalize items – if provided
    if payload.items:
        raw_items = [dict(x) for x in payload.items]
        # allow items missing date/location; enforce both
        canon_items = _canonicalize_day_items(raw_items, day, loc)
    else:
        template = _get_default_template(company, loc, weekday)
        if not template:
            raise HttpError(400, "Brak zapotrzebowania: podaj items albo ustaw domyślną listę zmian")
        canon_items = _canonicalize_day_items(template, day, loc)

    h = _day_hash(day, loc, canon_items)

    # Try reuse existing weekly demand via DayDemandIndex
    idx = DayDemandIndex.objects.filter(date=day, location=loc, day_hash=h).order_by("-id").first()
    if idx:
        d = idx.demand
        if payload.persist is False:
            # compute ad-hoc for this day, do not persist
            emp_avail = _build_emp_availability(d.date_from, d.date_to)
            # Apply special rules only to day items
            demand_payload = _apply_special_rules_to_demand(canon_items, day_dt, day_dt)
            res = run_solver(emp_availability=emp_avail, demand=demand_payload)
            return dict(
                demand_id=d.id,
                assignments=_with_ids(d.id, res.get("assignments", [])),
                summary={
                    "uncovered": res.get("uncovered", []),
                    "hours_summary": res.get("hours_summary", []),
                },
            )
        # ensure schedule persisted
        assignments, summary = _ensure_schedule_for_demand(d, force=bool(payload.force))
        # return only that day/location
        return dict(
            demand_id=d.id,
            assignments=_assignments_for_day_from_db(d, day, location=loc),
            summary=summary,
        )

    # No index found — create a dedicated one-day Demand (idempotent via hash of canon_items)
    content_hash = _hash_payload(canon_items)
    d, created = Demand.objects.get_or_create(
        content_hash=content_hash,
        defaults=dict(name=f"{day} {loc}", raw_payload=canon_items, date_from=day_dt, date_to=day_dt),
    )
    _populate_day_index_for_demand(d)

    if payload.persist is False:
        emp_avail = _build_emp_availability(day_dt, day_dt)
        demand_payload = _apply_special_rules_to_demand(canon_items, day_dt, day_dt)
        res = run_solver(emp_availability=emp_avail, demand=demand_payload)
        return dict(
            demand_id=d.id,
            assignments=_with_ids(d.id, res.get("assignments", [])),
            summary={
                "uncovered": res.get("uncovered", []),
                "hours_summary": res.get("hours_summary", []),
            },
        )

    assignments, summary = _ensure_schedule_for_demand(d, force=bool(payload.force))
    return dict(
        demand_id=d.id,
        assignments=_assignments_for_day_from_db(d, day, location=loc),
        summary=summary,
    )


@api.post(
    "/generate-range",
    response=GenerateResultOut,
    openapi_extra={
        "summary": "Ułóż grafik na kilka dni",
        "description": "Buduje grafik dla zakresu dat korzystając z podanej listy zmian lub domyślnego zapotrzebowania restauracji.",
    },
)
@transaction.atomic
def generate_range(request, payload: GenerateRangeIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    loc = _infer_location(request, payload.location, create_if_missing=True)
    company = _get_company_for_request(request)
    if payload.items and len(payload.items) > 0:
        template_items = [dict(x) for x in payload.items]
    else:
        template_items = None
    # Expand items for each day in range
    from datetime import date as _date, timedelta

    try:
        start = _date.fromisoformat(payload.date_from)
        end = _date.fromisoformat(payload.date_to)
    except Exception:
        raise HttpError(400, "Invalid date_from/date_to format")
    if end < start:
        raise HttpError(400, "date_to must be >= date_from")
    full_items: List[Dict[str, Any]] = []
    cur = start
    while cur <= end:
        day_s = cur.isoformat()
        # build canon for the day using templates (they may omit needs_experienced)
        if template_items is not None:
            source_items = template_items
        else:
            source_items = _get_default_template(company, loc, cur.weekday())
            if not source_items:
                raise HttpError(400, f"Brak domyślnego zapotrzebowania dla dnia {day_s}")
        day_canon = _canonicalize_day_items(source_items, day_s, loc)
        if not day_canon:
            raise HttpError(400, f"Lista zmian dla dnia {day_s} jest pusta")
        full_items.extend(day_canon)
        cur += timedelta(days=1)

    # Create/find Demand for full range (idempotent via content hash)
    content_hash = _hash_payload(full_items)
    d, created = Demand.objects.get_or_create(
        content_hash=content_hash,
        defaults=dict(
            name=f"{start.isoformat()}..{end.isoformat()} {loc}",
            raw_payload=full_items,
            date_from=start,
            date_to=end,
        ),
    )
    # Populate day index mapping
    _populate_day_index_for_demand(d)

    if payload.persist is False:
        # Compute ad-hoc without persisting
        emp_avail = _build_emp_availability(start, end)
        demand_payload = _apply_special_rules_to_demand(full_items, start, end)
        res = run_solver(emp_availability=emp_avail, demand=demand_payload)
        return dict(
            demand_id=d.id,
            assignments=res.get("assignments", []),
            summary={
                "uncovered": res.get("uncovered", []),
                "hours_summary": res.get("hours_summary", []),
            },
        )

    assignments, summary = _ensure_schedule_for_demand(d, force=bool(payload.force))
    return dict(demand_id=d.id, assignments=assignments, summary=summary)


@api.get(
    "/schedule/{demand_id}/day/{day}",
    openapi_extra={
        "summary": "Zmiany grafiku dla dnia",
        "description": "Zwraca wszystkie zmiany zapisane w grafiku dla wybranego zapotrzebowania i dnia.",
    },
)
def get_schedule_day(request, demand_id: int, day: str) -> List[ScheduleShiftOut]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        d = Demand.objects.get(id=demand_id)
    except Demand.DoesNotExist:
        raise HttpError(404, "Demand not found")
    dsh = d.shifts.filter(date=day).order_by("location", "start")
    return [_shift_base_dict(s) for s in dsh]


@api.get(
    "/schedule/shift/{shift_id}",
    response=ShiftOut,
    openapi_extra={
        "summary": "Szczegóły pojedynczej zmiany",
        "description": "Zwraca pełne informacje o zmianie z grafiku, w tym status potwierdzenia i ręcznej edycji.",
    },
)
def get_shift(request, shift_id: str):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        s = ScheduleShift.objects.get(shift_uid=shift_id)
    except ScheduleShift.DoesNotExist:
        raise HttpError(404, "Shift not found")
    data = _shift_base_dict(s)
    data.update(
        confirmed=bool(s.confirmed),
        user_edited=bool(s.user_edited),
    )
    return data


@api.post(
    "/schedule/shift",
    response=ShiftOut,
    openapi_extra={
        "summary": "Aktualizuj istniejącą zmianę",
        "description": "Pozwala zmodyfikować parametry zapisanej zmiany grafiku, np. liczbę osób czy przypisanych pracowników.",
    },
)
@transaction.atomic
def upsert_shift(request, payload: ShiftUpdateIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    # Find existing shift by id
    try:
        s = ScheduleShift.objects.get(shift_uid=payload.id)
    except ScheduleShift.DoesNotExist:
        raise HttpError(404, "Shift not found")

    # Update allowed fields
    fields = []
    if payload.date:
        s.date = payload.date
        fields.append("date")
    if payload.location:
        s.location = payload.location
        fields.append("location")
    if payload.start:
        s.start = payload.start
        fields.append("start")
    if payload.end:
        s.end = payload.end
        fields.append("end")
    if payload.demand is not None:
        s.demand_count = int(payload.demand)
        fields.append("demand_count")
    if payload.assigned_employees is not None:
        s.assigned_employees = list(payload.assigned_employees)
        fields.append("assigned_employees")
    if payload.needs_experienced is not None:
        s.needs_experienced = bool(payload.needs_experienced)
        fields.append("needs_experienced")
    if payload.missing_minutes is not None:
        s.missing_minutes = int(payload.missing_minutes)
        fields.append("missing_minutes")
    if payload.confirmed is not None:
        s.confirmed = bool(payload.confirmed)
        fields.append("confirmed")

    meta_raw = s.meta or {}
    meta = dict(meta_raw) if isinstance(meta_raw, dict) else {}
    meta_changed = False
    if payload.assigned_employees_detail is not None:
        try:
            meta["assigned_employees_detail"] = list(payload.assigned_employees_detail or [])
        except Exception:
            meta["assigned_employees_detail"] = []
        meta_changed = True
    if payload.missing_segments is not None:
        try:
            meta["missing_segments"] = list(payload.missing_segments or [])
        except Exception:
            meta["missing_segments"] = []
        meta_changed = True
    if meta_changed:
        s.meta = meta
        fields.append("meta")

    s.user_edited = True
    fields += ["user_edited", "updated_at"]
    s.save(update_fields=list(set(fields)))

    data = _shift_base_dict(s)
    data.update(
        confirmed=bool(s.confirmed),
        user_edited=bool(s.user_edited),
    )
    return data


__all__ = [
    "generate_day",
    "generate_range",
    "get_day_schedule",
    "get_or_generate_schedule",
    "get_schedule_day",
    "get_shift",
    "upsert_shift",
]
