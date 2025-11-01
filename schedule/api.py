from typing import List, Dict, Any, Optional
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import make_aware
from ninja import Router
from ninja.errors import HttpError
from datetime import datetime, date as date_type
import hashlib
import json

from accounts.models import Company
from .models import Availability, Demand, ScheduleShift, EventRule, SpecialDay, DayDemandIndex, DefaultDemand
from .schemas import (
    BulkAvailabilityIn,
    AvailabilityOut,
    DemandDayIn,
    DemandDayOut,
    DemandSlotOut,
    DemandBulkSlotIn,
    DefaultDemandIn,
    DefaultDemandOut,
    ScheduleFullOut,
    ScheduleShiftOut,
    ShiftUpdateIn,
    ShiftOut,
    EventRuleIn,
    EventRuleOut,
    SpecialDayIn,
    SpecialDayOut,
    GenerateDayIn,
    GenerateRangeIn,
    GenerateResultOut,
)
from donkeybackend.security import DRFJWTAuth
from .solver import run_solver

api = Router(tags=["schedule"], auth=DRFJWTAuth())
#api = Router(tags=["schedule"])

def _build_emp_availability(date_from, date_to) -> List[Dict[str, Any]]:
    qs = Availability.objects.filter(date__gte=date_from, date__lte=date_to)
    out = []
    for a in qs:
        out.append(dict(
            employee_id=a.employee_id,
            employee_name=a.employee_name,
            date=a.date.isoformat(),
            experienced=bool(a.experienced),
            hours_min=int(a.hours_min or 0),
            hours_max=int(a.hours_max or 1_000_000_000),
            available_slots=list(a.available_slots or []),
            assigned_shift=a.assigned_shift or None,
        ))
    return out

def _norm_hhmm(s: str) -> str:
    if not s:
        return s
    s = s.strip().replace(" ", "").replace(".", ":")
    if ":" in s:
        hh, mm = s.split(":", 1)
        return f"{int(hh):02d}:{int(mm):02d}"
    return f"{int(s):02d}:00"

def _as_mapping(x) -> dict | None:
    """Return a dict-like mapping for Slot input x (dict or Pydantic model)."""
    if x is None:
        return None
    if isinstance(x, dict):
        return x
    # Pydantic v2 BaseModel
    if hasattr(x, "model_dump") and callable(getattr(x, "model_dump")):
        try:
            return x.model_dump()
        except Exception:
            pass
    # Pydantic v1 BaseModel
    if hasattr(x, "dict") and callable(getattr(x, "dict")):
        try:
            return x.dict()
        except Exception:
            pass
    # Fallback to attributes
    if hasattr(x, "__dict__"):
        return {
            k: getattr(x, k) for k in ("start", "end") if hasattr(x, k)
        }
    return None


def _coerce_slots(val) -> list[dict]:
    """
    Accepts:
      - None
      - {start,end} or SlotIn
      - [{start,end}, ...] or [SlotIn, ...]
    Returns list of normalized slot dicts.
    """
    if val is None:
        return []

    # Single object case (dict or model)
    m = _as_mapping(val)
    if isinstance(m, dict) and ("start" in m or "end" in m):
        start = _norm_hhmm(str(m.get("start", "")))
        end   = _norm_hhmm(str(m.get("end", "")))
        return [{"start": start, "end": end}] if start and end else []

    out = []
    # Iterable case
    try:
        for x in (val or []):
            mx = _as_mapping(x) or {}
            start = _norm_hhmm(str(mx.get("start", "")))
            end   = _norm_hhmm(str(mx.get("end", "")))
            if start and end:
                out.append({"start": start, "end": end})
    except TypeError:
        # Not iterable and not a single mapping -> treat as empty
        return []

    return out

def _validate_slots(slots: list[dict]) -> list[dict]:
    ok = []
    for s in slots:
        try:
            h1, m1 = map(int, s["start"].split(":"))
            h2, m2 = map(int, s["end"].split(":"))
        except Exception:
            continue
        t1, t2 = h1*60 + m1, h2*60 + m2
        if 0 <= t1 < t2 <= 1440:
            ok.append({"start": f"{h1:02d}:{m1:02d}", "end": f"{h2:02d}:{m2:02d}"})
    return ok



@api.post(
    "/availability/bulk",
    response=List[AvailabilityOut],
    openapi_extra={
        "summary": "Zapisuję dostępność pracownika",
        "description": "Przekaż dane pracownika i listę dni. Każdy dzień może mieć okno godzin lub być pusty.",
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "example": {
                        "employee_id": 9,
                        "employee_name": "test2",
                        "experienced": False,
                        "hours_min": 0,
                        "hours_max": 1000000000,
                        "availabilities": [
                            {"date": "2025-10-20", "available_slots": {"start": "03:00", "end": "16:00"}},
                            {"date": "2025-10-21", "available_slots": None},
                            {"date": "2025-10-24", "available_slots": {"start": "14:00", "end": "20:30"}}
                        ]
                    }
                }
            }
        }
    }
)
@transaction.atomic
def upsert_availability_bulk(request, payload: BulkAvailabilityIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    emp_id = str(payload.employee_id)
    emp_name = payload.employee_name or ""

    saved: list[Availability] = []
    for day in payload.availabilities:
        slots = _validate_slots(_coerce_slots(day.available_slots))
        obj, _ = Availability.objects.update_or_create(
            employee_id=emp_id,
            date=day.date,
            defaults=dict(
                employee_name=emp_name,
                experienced=bool(payload.experienced),
                hours_min=int(payload.hours_min or 0),
                hours_max=int(payload.hours_max or 1_000_000_000),
                available_slots=slots,
            )
        )
        saved.append(obj)

    return [
        dict(
            employee_id=o.employee_id,
            employee_name=o.employee_name,
            date=o.date.isoformat(),
            experienced=o.experienced,
            hours_min=o.hours_min,
            hours_max=o.hours_max,
            available_slots=o.available_slots,
        ) for o in saved
    ]

# ------- GET: lista z filtrami + paginacja -------
@api.get(
    "/availability",
    openapi_extra={
        "summary": "Pobieram dostępność pracownika",
        "description": "Podaj ID pracownika i zakres dat. Dostaniesz listę dni z zapisanymi godzinami.",
    },
)
def list_availability(
    request,
    employee_id: str,
    date_from: date_type | None = None,
    date_to: date_type | None = None,
    only_with_slots: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    limit = max(1, min(limit, 200))
    qs = Availability.objects.all().order_by("date", "employee_id")

    qs = qs.filter(employee_id=str(employee_id))
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if only_with_slots:
        qs = qs.exclude(available_slots=[])

    count = qs.count()
    items = list(qs[offset: offset + limit])

    results = [
        dict(
            employee_id=o.employee_id,
            employee_name=o.employee_name,
            date=o.date.isoformat(),
            experienced=o.experienced,
            hours_min=o.hours_min,
            hours_max=o.hours_max,
            available_slots=o.available_slots,
        ) for o in items
    ]

    next_off = offset + limit if offset + limit < count else None
    prev_off = offset - limit if offset > 0 else None
    return {"count": count, "next": next_off, "previous": prev_off, "results": results}


# ===================== DEMAND & SCHEDULE =====================

def _hash_payload(obj: Any) -> str:
    try:
        s = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# ---- Day-level helpers ----
_DEF_LOC_ATTRS = ("location", "default_location", "restaurant", "org_location")


def _require_company(request) -> Company:
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        raise HttpError(401, "Brak uprawnień")

    company = getattr(user, "company", None)
    if company:
        return company

    auth = getattr(request, "auth", None)
    if isinstance(auth, dict):
        for key in ("company_id", "company", "companyId"):
            comp_val = auth.get(key)
            if comp_val:
                try:
                    return Company.objects.get(id=comp_val)
                except Company.DoesNotExist:
                    break

    raise HttpError(400, "Użytkownik nie ma przypisanej firmy")

def _infer_location(request, location_param: Optional[str]) -> str:
    loc = (location_param or "").strip()
    if loc:
        return loc
    # Try to infer from user if available (best-effort; can be extended later)
    user = getattr(request, "user", None)
    if user is not None:
        for attr in _DEF_LOC_ATTRS:
            if hasattr(user, attr):
                v = getattr(user, attr)
                if v:
                    return str(v)
        # JWT claims via DRFJWTAuth could be stored on request.auth
        auth = getattr(request, "auth", None)
        if isinstance(auth, dict):
            for k in ("location", "loc", "restaurant"):
                if auth.get(k):
                    return str(auth[k])
    raise HttpError(400, "Missing location: provide 'location' or ensure user has a default location")


def _canonicalize_day_items(items: List[Dict[str, Any]], date_s: str, location: str) -> List[Dict[str, Any]]:
    canon = []
    for it in (items or []):
        start = _norm_hhmm(str(it.get("start", "")))
        end   = _norm_hhmm(str(it.get("end", "")))
        if not (start and end):
            # skip invalid entries silently
            continue
        dmd = int(it.get("demand", 0) or 0)
        ne  = bool(it.get("needs_experienced", False))
        canon.append({
            "date": date_s,
            "location": location,
            "start": start,
            "end": end,
            "demand": dmd,
            "needs_experienced": ne,
        })
    # stable sort for hash
    canon.sort(key=lambda x: (x["start"], x["end"], x["demand"], x.get("needs_experienced", False)))
    return canon


def _canonicalize_template_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    canon = []
    for it in (items or []):
        start = _norm_hhmm(str(it.get("start", "")))
        end = _norm_hhmm(str(it.get("end", "")))
        if not (start and end):
            continue
        dmd = int(it.get("demand", 0) or 0)
        ne = bool(it.get("needs_experienced", False))
        canon.append({
            "start": start,
            "end": end,
            "demand": dmd,
            "needs_experienced": ne,
        })
    canon.sort(key=lambda x: (x["start"], x["end"], x["demand"], x.get("needs_experienced", False)))
    return canon


def _strip_day_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items or []:
        out.append({
            "start": it.get("start"),
            "end": it.get("end"),
            "demand": int(it.get("demand", 0) or 0),
            "needs_experienced": bool(it.get("needs_experienced", False)),
        })
    return out


def _day_hash(company_id: Optional[int], date_s: str, location: str, items: List[Dict[str, Any]]) -> str:
    # items should already be canonicalized and sorted
    return _hash_payload({"company": company_id or 0, "date": date_s, "location": location, "items": items})


def _company_payload_hash(company_id: Optional[int], items: List[Dict[str, Any]]) -> str:
    return _hash_payload({"company": company_id or 0, "items": items})


def _group_payload_by_day_location(items: List[Dict[str, Any]]) -> Dict[tuple, List[Dict[str, Any]]]:
    mp: Dict[tuple, List[Dict[str, Any]]] = {}
    for it in (items or []):
        d = str(it.get("date"))
        loc = str(it.get("location", ""))
        if not d:
            # skip
            continue
        mp.setdefault((d, loc), []).append(it)
    return mp


def _populate_day_index_for_demand(demand: Demand):
    try:
        groups = _group_payload_by_day_location(demand.raw_payload or [])
        for (d, loc), items in groups.items():
            canon = _canonicalize_day_items(items, d, loc)
            h = _day_hash(demand.company_id, d, loc, canon)
            DayDemandIndex.objects.get_or_create(
                company=demand.company,
                date=d,
                location=loc,
                day_hash=h,
                defaults={"demand": demand},
            )
    except Exception:
        # best-effort; do not fail API if indexing fails
        pass


def _shift_uid(demand_id: int, a: Dict[str, Any]) -> str:
    return f"D{demand_id}|{a['date']}|{a['location']}|{a['start']}-{a['end']}"


def _with_ids(demand_id: int, assignments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for a in assignments or []:
        b = dict(a)
        b["id"] = _shift_uid(demand_id, a)
        out.append(b)
    return out


def _assignments_from_db(demand: Demand) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in demand.shifts.all().order_by("date", "location", "start", "end"):
        out.append({
            "id": s.shift_uid,
            "date": s.date.isoformat(),
            "location": s.location,
            "start": s.start,
            "end": s.end,
            "demand": s.demand_count,
            "assigned_employees": list(s.assigned_employees or []),
            "needs_experienced": bool(s.needs_experienced),
            "missing_minutes": int(s.missing_minutes or 0),
        })
    return out


def _assignments_for_day_from_db(demand: Demand, day: str, location: Optional[str] = None) -> List[Dict[str, Any]]:
    qs = demand.shifts.filter(date=day)
    if location:
        qs = qs.filter(location=location)
    out: List[Dict[str, Any]] = []
    for s in qs.order_by("location", "start"):
        out.append({
            "id": s.shift_uid,
            "date": s.date.isoformat(),
            "location": s.location,
            "start": s.start,
            "end": s.end,
            "demand": s.demand_count,
            "assigned_employees": list(s.assigned_employees or []),
            "needs_experienced": bool(s.needs_experienced),
            "missing_minutes": int(s.missing_minutes or 0),
        })
    return out


def _get_or_build_day_index(company: Company, day: str, location: str) -> DayDemandIndex | None:
    # Try existing index first
    idx = (
        DayDemandIndex.objects.filter(company=company, date=day, location=location)
        .order_by("-id")
        .first()
    )
    if idx:
        return idx
    # Lazy backfill by scanning existing demands in range
    from datetime import date as _date

    try:
        day_dt = _date.fromisoformat(day)
    except Exception:
        return None

    candidates = (
        Demand.objects.filter(company=company, date_from__lte=day_dt, date_to__gte=day_dt)
        .order_by("-created_at")
    )
    for d in candidates:
        groups = _group_payload_by_day_location(d.raw_payload or [])
        items = groups.get((day, location))
        if not items:
            continue
        canon = _canonicalize_day_items(items, day, location)
        h = _day_hash(company.id if company else None, day, location, canon)
        try:
            idx, _ = DayDemandIndex.objects.get_or_create(
                company=company,
                date=day,
                location=location,
                day_hash=h,
                defaults={"demand": d},
            )
            return idx
        except Exception:
            # may race; retry fetch
            idx = (
                DayDemandIndex.objects.filter(
                    company=company, date=day, location=location, day_hash=h
                )
                .order_by("-id")
                .first()
            )
            if idx:
                return idx
    return None


def _collect_default_week(company: Company, location: str) -> Dict[int, List[Dict[str, Any]]]:
    week: Dict[int, List[Dict[str, Any]]] = {}
    qs = DefaultDemand.objects.filter(company=company, location=location)
    for obj in qs:
        try:
            weekday = int(obj.weekday)
        except (TypeError, ValueError):
            continue
        week[weekday] = _canonicalize_template_items(obj.items or [])
    return week


def _get_default_template(company: Company, location: str, day: Optional[str] = None, weekday: Optional[int] = None) -> List[Dict[str, Any]]:
    if weekday is None and day:
        from datetime import date as _date

        try:
            weekday = _date.fromisoformat(day).weekday()
        except Exception:
            weekday = None

    if weekday is None:
        return []

    week = _collect_default_week(company, location)
    return week.get(int(weekday), [])


def _build_default_week_payload(company: Company, location: str, weekdays: Optional[List[int]] = None) -> Dict[str, Any]:
    week = _collect_default_week(company, location)
    selected = weekdays if weekdays is not None else list(range(7))
    normalized: List[int] = []
    for w in selected:
        try:
            normalized.append(int(w))
        except (TypeError, ValueError):
            continue
    normalized = sorted({w for w in normalized if 0 <= w <= 6})
    if not normalized:
        normalized = list(range(7))
    days_payload: List[Dict[str, Any]] = []
    for weekday in normalized:
        items = week.get(weekday, [])
        days_payload.append(
            dict(
                weekday=weekday,
                items=[DemandSlotOut(**item).dict() for item in items],
            )
        )

    last_update = (
        DefaultDemand.objects.filter(company=company, location=location)
        .order_by("-updated_at")
        .values_list("updated_at", flat=True)
        .first()
    )
    if not last_update:
        last_update = timezone.now()

    return dict(
        location=location,
        days=days_payload,
        updated_at=timezone.localtime(last_update).isoformat(),
    )


def _get_company_demand_or_404(company: Company, demand_id: int) -> Demand:
    try:
        return Demand.objects.get(id=demand_id, company=company)
    except Demand.DoesNotExist:
        raise HttpError(404, "Nie znaleziono grafiku dla tej firmy")


def _update_demand_span(demand: Demand, items: List[Dict[str, Any]]):
    from datetime import date as _date

    dates: List[_date] = []
    for it in items:
        ds = str(it.get("date", "")).strip()
        if not ds:
            continue
        try:
            dates.append(_date.fromisoformat(ds))
        except Exception:
            continue
    if dates:
        demand.date_from = min(dates)
        demand.date_to = max(dates)


def _clear_day_overrides(company: Company, day: str, location: str) -> bool:
    indexes = list(DayDemandIndex.objects.filter(company=company, date=day, location=location))
    extra_idx = _get_or_build_day_index(company, day, location)
    if extra_idx and all(idx.id != extra_idx.id for idx in indexes if idx.id is not None):
        indexes.append(extra_idx)
    if not indexes:
        return False

    touched = False
    demand_ids = {idx.demand_id for idx in indexes if idx.demand_id}
    index_ids = [idx.id for idx in indexes if idx.id]
    if index_ids:
        DayDemandIndex.objects.filter(id__in=index_ids).delete()

    for demand in Demand.objects.filter(id__in=demand_ids, company=company):
        original = list(demand.raw_payload or [])
        keep = [
            item
            for item in original
            if str(item.get("date", "")) != day or str(item.get("location", "")) != location
        ]

        if len(keep) == len(original):
            continue

        touched = True
        demand.shifts.filter(date=day, location=location).delete()

        if not keep:
            demand.delete()
            continue

        demand.raw_payload = keep
        _update_demand_span(demand, keep)
        demand.content_hash = _company_payload_hash(demand.company_id, keep)
        demand.schedule_generated = False
        demand.save(
            update_fields=[
                "raw_payload",
                "date_from",
                "date_to",
                "content_hash",
                "schedule_generated",
                "updated_at",
            ]
        )
        DayDemandIndex.objects.filter(demand=demand).delete()
        _populate_day_index_for_demand(demand)

    return touched


def _save_day_payload(company: Company, day: str, location: str, raw_items: List[Dict[str, Any]], allow_template: bool) -> Dict[str, Any]:
    from datetime import date as _date

    if raw_items:
        canon_items = _canonicalize_day_items(raw_items, day, location)
    elif allow_template:
        template = _get_default_template(company, location, day)
        if not template:
            raise HttpError(400, "Brak listy zmian i brak domyślnego zapotrzebowania dla tej restauracji")
        canon_items = _canonicalize_day_items(template, day, location)
    else:
        canon_items = []

    if not canon_items:
        raise HttpError(400, "Lista zmian jest pusta")

    try:
        day_dt = _date.fromisoformat(day)
    except Exception:
        raise HttpError(400, "Invalid date format. Użyj YYYY-MM-DD")

    _clear_day_overrides(company, day, location)

    content_hash = _day_hash(company.id if company else None, day, location, canon_items)
    demand, _created = Demand.objects.get_or_create(
        content_hash=content_hash,
        defaults=dict(
            company=company,
            name="",
            raw_payload=canon_items,
            date_from=day_dt,
            date_to=day_dt,
        ),
    )

    if demand.company_id != (company.id if company else None):
        demand.company = company
        demand.save(update_fields=["company"])

    demand.raw_payload = canon_items
    demand.date_from = day_dt
    demand.date_to = day_dt
    demand.content_hash = content_hash
    demand.name = ""
    demand.schedule_generated = False
    demand.save(
        update_fields=[
            "company",
            "raw_payload",
            "date_from",
            "date_to",
            "content_hash",
            "name",
            "schedule_generated",
            "updated_at",
        ]
    )
    demand.shifts.all().delete()
    DayDemandIndex.objects.filter(demand=demand).delete()
    _populate_day_index_for_demand(demand)

    return dict(
        date=day,
        location=location,
        items=[DemandSlotOut(**item).dict() for item in _strip_day_items(canon_items)],
        content_hash=content_hash,
        uses_default=False,
    )


def _build_day_response(company: Company, day: str, location: str) -> Dict[str, Any]:
    idx = _get_or_build_day_index(company, day, location)
    if idx:
        demand = idx.demand
        groups = _group_payload_by_day_location(demand.raw_payload or [])
        day_items = _canonicalize_day_items(groups.get((day, location), []), day, location)
        if day_items:
            return dict(
                date=day,
                location=location,
                items=[DemandSlotOut(**item).dict() for item in _strip_day_items(day_items)],
                content_hash=_day_hash(company.id if company else None, day, location, day_items),
                uses_default=False,
            )

    template = _get_default_template(company, location, day)
    if template:
        return dict(
            date=day,
            location=location,
            items=[DemandSlotOut(**item).dict() for item in template],
            content_hash=None,
            uses_default=True,
        )

    return dict(date=day, location=location, items=[], content_hash=None, uses_default=True)


def _extract_location_from_payload(items: List[Dict[str, Any]]) -> str:
    for it in items or []:
        loc = (it or {}).get("location")
        if loc:
            return str(loc)
    return ""


@api.post(
    "/demand/day",
    response=DemandDayOut,
    openapi_extra={
        "summary": "Zapisuję zapotrzebowanie na jeden dzień",
        "description": "Podajesz datę i listę zmian. Gdy lista jest pusta, korzystam z domyślnego wzoru restauracji.",
    },
)
@transaction.atomic
def save_day_demand(request, payload: DemandDayIn):
    company = _require_company(request)
    loc = _infer_location(request, payload.location)
    day = (payload.date or "").strip()
    if not day:
        raise HttpError(400, "Missing 'date'")

    raw_items = [dict(x) for x in (payload.items or [])]
    return _save_day_payload(company, day, loc, raw_items, allow_template=True)


@api.get(
    "/demand/day",
    response=DemandDayOut,
    openapi_extra={
        "summary": "Pobieram zapotrzebowanie na jeden dzień",
        "description": "Podajesz datę i (opcjonalnie) lokalizację. Gdy brak własnych zmian zwracam domyślne zapotrzebowanie.",
    },
)
def get_day_demand(request, date: str, location: Optional[str] = None) -> Dict[str, Any]:
    company = _require_company(request)
    loc = _infer_location(request, location)
    day = (date or "").strip()
    if not day:
        raise HttpError(400, "Missing 'date'")

    return _build_day_response(company, day, loc)


@api.post(
    "/demand/many",
    response=List[DemandDayOut],
    openapi_extra={
        "summary": "Zapisuję kilka dni na raz",
        "description": "Przesyłasz listę zmian z datami. Grupuję je dniami i zapisuję zapotrzebowanie dla każdej daty.",
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "example": [
                        {
                            "date": "2025-10-06",
                            "location": "Restauracja A",
                            "start": "10:30",
                            "end": "19:30",
                            "demand": 1,
                            "needs_experienced": False,
                        }
                    ]
                }
            },
        },
    },
)
@transaction.atomic
def save_many_days(request, payload: List[DemandBulkSlotIn]):
    company = _require_company(request)
    if not payload:
        raise HttpError(400, "Lista zmian jest pusta")

    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    order: List[tuple[str, str]] = []

    for slot in payload:
        day = (slot.date or "").strip()
        if not day:
            raise HttpError(400, "Każdy wpis musi mieć datę")
        loc = _infer_location(request, slot.location)
        key = (day, loc)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(
            {
                "start": slot.start,
                "end": slot.end,
                "demand": slot.demand,
                "needs_experienced": slot.needs_experienced,
            }
        )

    results: List[Dict[str, Any]] = []
    for day, loc in order:
        results.append(_save_day_payload(company, day, loc, grouped[(day, loc)], allow_template=False))

    return results


@api.get(
    "/demand/range",
    response=List[DemandDayOut],
    openapi_extra={
        "summary": "Pobieram zapotrzebowanie dla zakresu dat",
        "description": "Wystarczy podać datę od i do. Zwracam każdy dzień, wskazując czy to własne czy domyślne zmiany.",
    },
)
def get_demand_range(request, date_from: str, date_to: str, location: Optional[str] = None) -> List[Dict[str, Any]]:
    company = _require_company(request)
    loc = _infer_location(request, location)
    from datetime import date as _date, timedelta

    try:
        start = _date.fromisoformat((date_from or "").strip())
        end = _date.fromisoformat((date_to or "").strip())
    except Exception:
        raise HttpError(400, "Invalid date range. Użyj formatu YYYY-MM-DD")

    if end < start:
        raise HttpError(400, "date_to must be >= date_from")

    out: List[Dict[str, Any]] = []
    cur = start
    while cur <= end:
        out.append(_build_day_response(company, cur.isoformat(), loc))
        cur += timedelta(days=1)

    return out


@api.delete(
    "/demand/day",
    response=DemandDayOut,
    openapi_extra={
        "summary": "Usuwam zapotrzebowanie na dzień",
        "description": "Czyści zapisane zmiany dla dnia i wraca do domyślnego wzoru restauracji.",
    },
)
@transaction.atomic
def delete_day_demand(request, date: str, location: Optional[str] = None) -> Dict[str, Any]:
    company = _require_company(request)
    loc = _infer_location(request, location)
    day = (date or "").strip()
    if not day:
        raise HttpError(400, "Missing 'date'")

    _clear_day_overrides(company, day, loc)
    return _build_day_response(company, day, loc)


@api.post(
    "/demand/default",
    response=DefaultDemandOut,
    openapi_extra={
        "summary": "Ustawiam tygodniowe domyślne zapotrzebowanie",
        "description": "Podajesz lokalizację i dni tygodnia (0=pn ... 6=nd). Każdy dzień może mieć kilka zmian. Pusta lista zmian usuwa domyślne wpisy.",
    },
)
@transaction.atomic
def save_default_demand(request, payload: DefaultDemandIn):
    company = _require_company(request)
    loc = _infer_location(request, payload.location)
    if payload.days is None:
        raise HttpError(400, "Podaj listę dni tygodnia")

    for day_cfg in payload.days:
        try:
            weekday = int(day_cfg.weekday)
        except (TypeError, ValueError):
            raise HttpError(400, "weekday musi być liczbą od 0 do 6")
        if weekday < 0 or weekday > 6:
            raise HttpError(400, "weekday musi być w zakresie 0-6")

        raw_items = [dict(x) for x in (day_cfg.items or [])]
        canon = _canonicalize_template_items(raw_items)
        qs = DefaultDemand.objects.filter(company=company, location=loc, weekday=weekday)
        if not canon:
            qs.delete()
            continue

        obj, created = DefaultDemand.objects.get_or_create(
            company=company,
            location=loc,
            weekday=weekday,
            defaults={"items": canon},
        )
        if not created:
            obj.items = canon
            obj.save(update_fields=["items", "updated_at"])

    # If list empty -> clear all for location
    if not payload.days:
        DefaultDemand.objects.filter(company=company, location=loc).delete()

    return _build_default_week_payload(company, loc)


@api.get(
    "/demand/default",
    response=DefaultDemandOut,
    openapi_extra={
        "summary": "Pobieram domyślne zapotrzebowanie",
        "description": "Zwracam cały tydzień (0-6). Możesz podać weekday, aby dostać tylko wybrany dzień.",
    },
)
def get_default_demand(request, location: Optional[str] = None, weekday: Optional[int] = None):
    company = _require_company(request)
    loc = _infer_location(request, location)

    weekdays = None
    if weekday is not None:
        try:
            weekdays = [int(weekday)]
        except (TypeError, ValueError):
            raise HttpError(400, "weekday musi być liczbą od 0 do 6")
        if weekdays[0] < 0 or weekdays[0] > 6:
            raise HttpError(400, "weekday musi być w zakresie 0-6")

    return _build_default_week_payload(company, loc, weekdays)


@api.get(
    "/demand/{demand_id}",
    openapi_extra={
        "summary": "Podglądam zapisane zapotrzebowanie",
        "description": "Zwracam surową listę zmian i zakres dat dla jednego grafiku. * Starszy endpoint, raczej tylko do podglądu.",
    },
)
def get_demand(request, demand_id: int) -> Dict[str, Any]:
    company = _require_company(request)
    d = _get_company_demand_or_404(company, demand_id)
    return dict(
        id=d.id,
        location=_extract_location_from_payload(d.raw_payload or []),
        date_from=str(d.date_from),
        date_to=str(d.date_to),
        schedule_generated=bool(d.schedule_generated),
        count=len(d.raw_payload or []),
        payload=d.raw_payload,
    )


@api.get(
    "/demands",
    openapi_extra={
        "summary": "Lista zapisanych grafików",
        "description": "Paginuje wszystkie zapotrzebowania firmy. * Przy większej liczbie wpisów może być do uproszczenia.",
    },
)
def list_demands(request, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    company = _require_company(request)
    limit = max(1, min(200, limit))
    qs = Demand.objects.filter(company=company).order_by("-created_at")
    count = qs.count()
    items = list(qs[offset: offset + limit])
    out = [
        dict(
            id=o.id,
            location=_extract_location_from_payload(o.raw_payload or []),
            date_from=str(o.date_from),
            date_to=str(o.date_to),
            schedule_generated=o.schedule_generated,
            created_at=o.created_at.isoformat(),
        )
        for o in items
    ]
    next_off = offset + limit if offset + limit < count else None
    prev_off = offset - limit if offset > 0 else None
    return {"count": count, "next": next_off, "previous": prev_off, "results": out}


def _apply_special_rules_to_demand(items: List[Dict[str, Any]], date_from, date_to) -> List[Dict[str, Any]]:
    """Apply SpecialDay/EventRule transformations to incoming demand items without mutating originals.
    - Exact (date+location) rules take precedence; then wildcard (date+"") rules are applied if present.
    - Multiple rules for the same key are applied in creation order.
    """
    import math
    # Preload active special days + rules within range
    sd_qs = SpecialDay.objects.select_related("rule").filter(
        active=True,
        date__gte=date_from,
        date__lte=date_to,
        rule__active=True,
    ).order_by("created_at", "id")

    # Build mapping: (date_iso, location) -> [rule,...]; wildcard stored with location ""
    map_by_key: dict[tuple[str, str], list] = {}
    for sd in sd_qs:
        k = (sd.date.isoformat(), (sd.location or ""))
        map_by_key.setdefault(k, []).append(sd.rule)

    def apply_rules_for(date_s: str, location: str, base_demand: int, needs_exp: bool) -> tuple[int, bool]:
        # Gather rules: exact first, then wildcard
        rules = []
        exact = map_by_key.get((date_s, location or ""), [])
        wildcard = map_by_key.get((date_s, ""), [])
        # exact should override wildcard precedence; we apply wildcard first, then exact
        rules.extend(wildcard)
        rules.extend(exact)
        d_val = int(base_demand)
        nexp = bool(needs_exp)
        for r in rules:
            if r.mode == r.MODE_OVERRIDE:
                try:
                    d_val = int(round(r.value))
                except Exception:
                    d_val = int(r.value)
            else:  # multiplier
                try:
                    d_val = int(math.ceil(d_val * float(r.value)))
                except Exception:
                    d_val = d_val
            if r.min_demand is not None:
                d_val = max(d_val, int(r.min_demand))
            if r.max_demand is not None:
                d_val = min(d_val, int(r.max_demand))
            if r.needs_experienced_default:
                nexp = True
        d_val = max(0, int(d_val))
        return d_val, nexp

    out: List[Dict[str, Any]] = []
    for it in (items or []):
        a = dict(it)
        a_date = a.get("date")
        a_loc = a.get("location", "")
        d0 = int(a.get("demand", 0) or 0)
        n0 = bool(a.get("needs_experienced", False))
        d_new, n_new = apply_rules_for(str(a_date), str(a_loc), d0, n0)
        a["demand"] = d_new
        if n_new:
            a["needs_experienced"] = True
        out.append(a)
    return out


def _ensure_schedule_for_demand(d: Demand, force: bool = False) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Ensures schedule is generated for given demand; returns (assignments, summary) where
    summary contains uncovered/hours_summary when generation occurred. If schedule already existed,
    summary may be None.
    """
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
        emp_avail.append(dict(
            employee_id=a.employee_id,
            employee_name=a.employee_name,
            date=a.date.isoformat(),
            experienced=bool(a.experienced),
            hours_min=int(a.hours_min or 0),
            hours_max=int(a.hours_max or 1_000_000_000),
            available_slots=list(a.available_slots or []),
            assigned_shift=a.assigned_shift or None,
        ))

    # Apply special rules (holidays/events) before solving
    demand_payload = _apply_special_rules_to_demand(d.raw_payload or [], d.date_from, d.date_to)

    res = run_solver(emp_availability=emp_avail, demand=demand_payload)

    # Persist assignments per day/shift
    ass = res.get("assignments", []) or []
    to_create = []
    from datetime import date as _date
    for a in ass:
        uid = _shift_uid(d.id, a)
        to_create.append(ScheduleShift(
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
            meta=dict(uncovered=res.get("uncovered", []), hours_summary=res.get("hours_summary", [])),
        ))
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
        "summary": "Pobieram gotowy grafik",
        "description": "Zwracam listę zmian dla wybranego zapotrzebowania. Użyj force=true, aby wymusić przeliczenie.",
    },
)
@transaction.atomic
def get_or_generate_schedule(request, demand_id: int, force: bool = False) -> List[ScheduleShiftOut]:
    company = _require_company(request)
    d = _get_company_demand_or_404(company, demand_id)

    assignments, _summary = _ensure_schedule_for_demand(d, force=force)
    return assignments


# ===== Rules (EventRule) =====
@api.post(
    "/rules",
    response=EventRuleOut,
    openapi_extra={
        "summary": "Dodaję regułę specjalną",
        "description": "Tworzę regułę override lub mnożnik na wyjątkowe dni. * Zaawansowane, używaj tylko jeśli naprawdę potrzebujesz.",
    },
)
@transaction.atomic
def create_rule(request, payload: EventRuleIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    if payload.mode not in (EventRule.MODE_OVERRIDE, EventRule.MODE_MULTIPLIER):
        raise HttpError(400, "Invalid mode: must be 'override' or 'multiplier'")
    obj = EventRule.objects.create(
        name=payload.name,
        mode=payload.mode,
        value=float(payload.value),
        needs_experienced_default=bool(payload.needs_experienced_default or False),
        min_demand=payload.min_demand,
        max_demand=payload.max_demand,
        active=bool(payload.active if payload.active is not None else True),
    )
    return dict(
        id=obj.id,
        name=obj.name,
        mode=obj.mode,
        value=float(obj.value),
        needs_experienced_default=bool(obj.needs_experienced_default),
        min_demand=obj.min_demand,
        max_demand=obj.max_demand,
        active=bool(obj.active),
    )


@api.get(
    "/rules",
    openapi_extra={
        "summary": "Lista reguł specjalnych",
        "description": "Pokazuje wszystkie reguły override/mnożnik. * Raczej opcjonalne narzędzie administracyjne.",
    },
)
def list_rules(request) -> List[EventRuleOut]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    qs = EventRule.objects.all().order_by("name", "id")
    return [
        dict(
            id=o.id,
            name=o.name,
            mode=o.mode,
            value=float(o.value),
            needs_experienced_default=bool(o.needs_experienced_default),
            min_demand=o.min_demand,
            max_demand=o.max_demand,
            active=bool(o.active),
        ) for o in qs
    ]


@api.get(
    "/rules/{rule_id}",
    response=EventRuleOut,
    openapi_extra={
        "summary": "Pobieram jedną regułę",
        "description": "Zwracam szczegóły reguły specjalnej. * Starszy endpoint zostawiony głównie do panelu admina.",
    },
)
def get_rule(request, rule_id: int):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        o = EventRule.objects.get(id=rule_id)
    except EventRule.DoesNotExist:
        raise HttpError(404, "Rule not found")
    return dict(
        id=o.id,
        name=o.name,
        mode=o.mode,
        value=float(o.value),
        needs_experienced_default=bool(o.needs_experienced_default),
        min_demand=o.min_demand,
        max_demand=o.max_demand,
        active=bool(o.active),
    )


# ===== Special days =====
@api.post(
    "/special-days",
    response=SpecialDayOut,
    openapi_extra={
        "summary": "Dodaję wyjątkowy dzień",
        "description": "Przypinam regułę do konkretnej daty i lokalu. * Bardziej zaawansowana opcja, nie dla codziennych zmian.",
    },
)
@transaction.atomic
def create_special_day(request, payload: SpecialDayIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        rule = EventRule.objects.get(id=payload.rule_id)
    except EventRule.DoesNotExist:
        raise HttpError(400, "Invalid rule_id")
    obj, created = SpecialDay.objects.get_or_create(
        date=payload.date,
        location=(payload.location or ""),
        rule=rule,
        defaults=dict(note=payload.note or "", active=bool(payload.active if payload.active is not None else True))
    )
    # If exists, update note/active
    if not created:
        obj.note = payload.note or obj.note
        if payload.active is not None:
            obj.active = bool(payload.active)
        obj.save(update_fields=["note", "active", "updated_at"])
    return dict(
        id=obj.id,
        date=obj.date.isoformat(),
        location=obj.location or "",
        rule_id=obj.rule_id,
        rule_name=obj.rule.name,
        note=obj.note,
        active=bool(obj.active),
    )


@api.get(
    "/special-days",
    openapi_extra={
        "summary": "Lista wyjątkowych dni",
        "description": "Pokazuje wszystkie dni z przypiętymi regułami. * Do rozważenia czy zostawić w UI.",
    },
)
def list_special_days(request, date_from: date_type | None = None, date_to: date_type | None = None, location: str | None = None) -> List[SpecialDayOut]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    qs = SpecialDay.objects.select_related("rule").all()
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if location is not None:
        qs = qs.filter(location=(location or ""))
    qs = qs.order_by("-date", "location")
    return [
        dict(
            id=o.id,
            date=o.date.isoformat(),
            location=o.location or "",
            rule_id=o.rule_id,
            rule_name=o.rule.name,
            note=o.note,
            active=bool(o.active),
        ) for o in qs
    ]


# ===== Day-level convenience endpoints =====
@api.get(
    "/days/{day}",
    openapi_extra={
        "summary": "Szybki podgląd dnia",
        "description": "Zwracam wygenerowane zmiany dla daty i lokalu. Jeśli nic nie zapisano, próbuję użyć wzorca.",
    },
)
@transaction.atomic
def get_day_schedule(request, day: str, location: Optional[str] = None) -> List[ScheduleShiftOut]:
    company = _require_company(request)
    loc = _infer_location(request, location)
    # If we already have persisted shifts for that date/location across any demand, return them
    shifts_qs = ScheduleShift.objects.filter(date=day, location=loc, demand__company=company)
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
            ) for s in shifts_qs.order_by("start", "end")
        ]
    # No persisted shifts — try to find a weekly demand through DayDemandIndex
    idx = _get_or_build_day_index(company, day, loc)
    if not idx:
        # Nothing known for this date/location
        return []
    d = idx.demand
    if d.company_id != (company.id if company else None):
        return []
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
    company = _require_company(request)
    loc = _infer_location(request, payload.location)
    day = payload.date
    # Canonicalize items – if provided
    if payload.items:
        raw_items = [dict(x) for x in payload.items]
        # allow items missing date/location; enforce both
        canon_items = _canonicalize_day_items(raw_items, day, loc)
    else:
        template = _get_default_template(company, loc, day)
        if not template:
            raise HttpError(400, "Brak zapotrzebowania: podaj items albo ustaw domyślną listę zmian")
        canon_items = _canonicalize_day_items(template, day, loc)

    h = _day_hash(company.id if company else None, day, loc, canon_items)

    # Try reuse existing weekly demand via DayDemandIndex
    idx = (
        DayDemandIndex.objects.filter(company=company, date=day, location=loc, day_hash=h)
        .order_by("-id")
        .first()
    )
    if idx:
        d = idx.demand
        if d.company_id != (company.id if company else None):
            raise HttpError(404, "Brak grafiku dla tej firmy")
        if payload.persist is False:
            # compute ad-hoc for this day, do not persist
            emp_avail = _build_emp_availability(d.date_from, d.date_to)
            # Apply special rules only to day items
            from datetime import date as _date
            d_from = _date.fromisoformat(day)
            d_to = _date.fromisoformat(day)
            demand_payload = _apply_special_rules_to_demand(canon_items, d_from, d_to)
            res = run_solver(emp_availability=emp_avail, demand=demand_payload)
            return dict(demand_id=d.id, assignments=_with_ids(d.id, res.get("assignments", [])), summary={"uncovered": res.get("uncovered", []), "hours_summary": res.get("hours_summary", [])})
        # ensure schedule persisted
        assignments, summary = _ensure_schedule_for_demand(d, force=bool(payload.force))
        # return only that day/location
        return dict(demand_id=d.id, assignments=_assignments_for_day_from_db(d, day, location=loc), summary=summary)

    # No index found — create a dedicated one-day Demand (idempotent via hash of canon_items)
    content_hash = _company_payload_hash(company.id if company else None, canon_items)
    from datetime import date as _date
    d_from = _date.fromisoformat(day)
    d_to = _date.fromisoformat(day)
    d, created = Demand.objects.get_or_create(
        content_hash=content_hash,
        defaults=dict(
            company=company,
            name=f"{day} {loc}",
            raw_payload=canon_items,
            date_from=d_from,
            date_to=d_to,
        ),
    )
    if d.company_id != (company.id if company else None):
        d.company = company
        d.save(update_fields=["company"])
    _populate_day_index_for_demand(d)

    if payload.persist is False:
        emp_avail = _build_emp_availability(d_from, d_to)
        demand_payload = _apply_special_rules_to_demand(canon_items, d_from, d_to)
        res = run_solver(emp_availability=emp_avail, demand=demand_payload)
        return dict(demand_id=d.id, assignments=_with_ids(d.id, res.get("assignments", [])), summary={"uncovered": res.get("uncovered", []), "hours_summary": res.get("hours_summary", [])})

    assignments, summary = _ensure_schedule_for_demand(d, force=bool(payload.force))
    return dict(demand_id=d.id, assignments=_assignments_for_day_from_db(d, day, location=loc), summary=summary)


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
    company = _require_company(request)
    loc = _infer_location(request, payload.location)
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
        base_items = template_items
        if base_items is None:
            base_items = _get_default_template(company, loc, day_s)
            if not base_items:
                raise HttpError(400, f"Brak domyślnego zapotrzebowania dla dnia {day_s}")
        day_canon = _canonicalize_day_items(base_items, day_s, loc)
        if not day_canon:
            raise HttpError(400, f"Lista zmian dla {day_s} jest pusta")
        full_items.extend(day_canon)
        cur += timedelta(days=1)

    if not full_items:
        raise HttpError(400, "Brak zmian do zapisania")

    # Create/find Demand for full range (idempotent via content hash)
    content_hash = _company_payload_hash(company.id if company else None, full_items)
    d, created = Demand.objects.get_or_create(
        content_hash=content_hash,
        defaults=dict(
            company=company,
            name=f"{start.isoformat()}..{end.isoformat()} {loc}",
            raw_payload=full_items,
            date_from=start,
            date_to=end,
        ),
    )
    if d.company_id != (company.id if company else None):
        d.company = company
        d.save(update_fields=["company"])
    # Populate day index mapping
    _populate_day_index_for_demand(d)

    if payload.persist is False:
        # Compute ad-hoc without persisting
        emp_avail = _build_emp_availability(start, end)
        demand_payload = _apply_special_rules_to_demand(full_items, start, end)
        res = run_solver(emp_availability=emp_avail, demand=demand_payload)
        return dict(demand_id=d.id, assignments=res.get("assignments", []), summary={"uncovered": res.get("uncovered", []), "hours_summary": res.get("hours_summary", [])})

    assignments, summary = _ensure_schedule_for_demand(d, force=bool(payload.force))
    return dict(demand_id=d.id, assignments=assignments, summary=summary)


@api.get(
    "/schedule/{demand_id}/day/{day}",
    openapi_extra={
        "summary": "Pobieram grafik konkretnego dnia",
        "description": "Zwracam wszystkie zmiany wygenerowane w danym grafiku dla tej daty.",
    },
)
def get_schedule_day(request, demand_id: int, day: str) -> List[ScheduleShiftOut]:
    company = _require_company(request)
    d = _get_company_demand_or_404(company, demand_id)
    dsh = d.shifts.filter(date=day)
    return [
        {
            "id": s.shift_uid,
            "date": s.date.isoformat(),
            "location": s.location,
            "start": s.start,
            "end": s.end,
            "demand": s.demand_count,
            "assigned_employees": list(s.assigned_employees or []),
            "needs_experienced": bool(s.needs_experienced),
            "missing_minutes": int(s.missing_minutes or 0),
        }
        for s in dsh.order_by("location", "start")
    ]


@api.get(
    "/schedule/shift/{shift_id}",
    response=ShiftOut,
    openapi_extra={
        "summary": "Podglądam jedną zmianę",
        "description": "Zwracam szczegóły zapisanej zmiany według ID.",
    },
)
def get_shift(request, shift_id: str):
    company = _require_company(request)
    try:
        s = ScheduleShift.objects.select_related("demand").get(
            shift_uid=shift_id, demand__company=company
        )
    except ScheduleShift.DoesNotExist:
        raise HttpError(404, "Shift not found")
    return dict(
        id=s.shift_uid,
        date=s.date.isoformat(),
        location=s.location,
        start=s.start,
        end=s.end,
        demand=s.demand_count,
        assigned_employees=list(s.assigned_employees or []),
        needs_experienced=bool(s.needs_experienced),
        missing_minutes=int(s.missing_minutes or 0),
        confirmed=bool(s.confirmed),
        user_edited=bool(s.user_edited),
    )


@api.post(
    "/schedule/shift",
    response=ShiftOut,
    openapi_extra={
        "summary": "Aktualizuję zmianę",
        "description": "Pozwala poprawić datę, godziny, liczbę osób i potwierdzenie wybranej zmiany.",
    },
)
@transaction.atomic
def upsert_shift(request, payload: ShiftUpdateIn):
    company = _require_company(request)
    # Find existing shift by id
    try:
        s = ScheduleShift.objects.select_related("demand").get(
            shift_uid=payload.id, demand__company=company
        )
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

    s.user_edited = True
    fields += ["user_edited", "updated_at"]
    s.save(update_fields=list(set(fields)))

    return dict(
        id=s.shift_uid,
        date=s.date.isoformat(),
        location=s.location,
        start=s.start,
        end=s.end,
        demand=s.demand_count,
        assigned_employees=list(s.assigned_employees or []),
        needs_experienced=bool(s.needs_experienced),
        missing_minutes=int(s.missing_minutes or 0),
        confirmed=bool(s.confirmed),
        user_edited=bool(s.user_edited),
    )