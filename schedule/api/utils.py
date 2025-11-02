from __future__ import annotations

import hashlib
import json
import math
from datetime import date as date_type
from typing import Any, Dict, List, Optional

from django.utils import timezone
from ninja.errors import HttpError

from accounts.models import Company
from ..models import (
    Availability,
    CompanyLocation,
    DayDemandIndex,
    Demand,
    DefaultDemand,
    EventRule,
    ScheduleShift,
    SpecialDay,
)
from ..schemas import DemandSlotOut, DefaultDemandWeekDayOut


def _build_emp_availability(date_from, date_to) -> List[Dict[str, Any]]:
    qs = Availability.objects.filter(date__gte=date_from, date__lte=date_to)
    out = []
    for a in qs:
        out.append(
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
        return {k: getattr(x, k) for k in ("start", "end") if hasattr(x, k)}
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
        end = _norm_hhmm(str(m.get("end", "")))
        return [{"start": start, "end": end}] if start and end else []

    out = []
    # Iterable case
    try:
        for x in (val or []):
            mx = _as_mapping(x) or {}
            start = _norm_hhmm(str(mx.get("start", "")))
            end = _norm_hhmm(str(mx.get("end", "")))
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
        t1, t2 = h1 * 60 + m1, h2 * 60 + m2
        if 0 <= t1 < t2 <= 1440:
            ok.append({"start": f"{h1:02d}:{m1:02d}", "end": f"{h2:02d}:{m2:02d}"})
    return ok


def _hash_payload(obj: Any) -> str:
    try:
        s = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


_DEF_LOC_ATTRS = ("location", "default_location", "restaurant", "org_location")


def _get_company_for_request(request) -> Company:
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        raise HttpError(401, "Unauthorized")
    company = getattr(user, "company", None)
    if company:
        return company
    raise HttpError(403, "Brak przypisanej firmy dla użytkownika")


def _get_company_location(company: Company, location: str, create: bool = False) -> CompanyLocation:
    loc = (location or "").strip()
    if not loc:
        raise HttpError(400, "Missing location")
    obj = CompanyLocation.objects.filter(company=company, name=loc).first()
    if obj:
        return obj
    if create:
        return CompanyLocation.objects.create(company=company, name=loc)
    raise HttpError(404, "Lokalizacja nie należy do Twojej firmy")


def _infer_location(request, location_param: Optional[str], *, create_if_missing: bool = False) -> str:
    loc = (location_param or "").strip()
    if not loc:
        # Try to infer from user if available (best-effort; can be extended later)
        user = getattr(request, "user", None)
        if user is not None:
            for attr in _DEF_LOC_ATTRS:
                if hasattr(user, attr):
                    v = getattr(user, attr)
                    if v:
                        loc = str(v)
                        break
            if not loc:
                # JWT claims via DRFJWTAuth could be stored on request.auth
                auth = getattr(request, "auth", None)
                if isinstance(auth, dict):
                    for k in ("location", "loc", "restaurant"):
                        if auth.get(k):
                            loc = str(auth[k])
                            break
    if not loc:
        raise HttpError(400, "Missing location: provide 'location' or ensure user has a default location")
    company = _get_company_for_request(request)
    _get_company_location(company, loc, create=create_if_missing)
    return loc


def _normalize_weekday(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        num = int(value)
    except Exception:
        raise HttpError(400, "weekday musi być liczbą 0-6")
    if num < 0 or num > 6:
        raise HttpError(400, "weekday musi być w zakresie 0-6 (pon=0)")
    return num


def _canonicalize_day_items(items: List[Dict[str, Any]], date_s: str, location: str) -> List[Dict[str, Any]]:
    canon = []
    for it in (items or []):
        start = _norm_hhmm(str(it.get("start", "")))
        end = _norm_hhmm(str(it.get("end", "")))
        if not (start and end):
            # skip invalid entries silently
            continue
        dmd = int(it.get("demand", 0) or 0)
        ne = bool(it.get("needs_experienced", False))
        canon.append(
            {
                "date": date_s,
                "location": location,
                "start": start,
                "end": end,
                "demand": dmd,
                "needs_experienced": ne,
            }
        )
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
        canon.append(
            {
                "start": start,
                "end": end,
                "demand": dmd,
                "needs_experienced": ne,
            }
        )
    canon.sort(key=lambda x: (x["start"], x["end"], x["demand"], x.get("needs_experienced", False)))
    return canon


def _strip_day_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items or []:
        out.append(
            {
                "start": it.get("start"),
                "end": it.get("end"),
                "demand": int(it.get("demand", 0) or 0),
                "needs_experienced": bool(it.get("needs_experienced", False)),
            }
        )
    return out


def _day_hash(date_s: str, location: str, items: List[Dict[str, Any]]) -> str:
    # items should already be canonicalized and sorted
    return _hash_payload({"date": date_s, "location": location, "items": items})


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
            h = _day_hash(d, loc, canon)
            DayDemandIndex.objects.get_or_create(
                date=d, location=loc, day_hash=h, defaults={"demand": demand}
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


def _shift_base_dict(s: ScheduleShift) -> Dict[str, Any]:
    meta_raw = s.meta or {}
    if isinstance(meta_raw, dict):
        meta_dict = dict(meta_raw)
    else:
        meta_dict = {}
    return {
        "id": s.shift_uid,
        "date": s.date.isoformat(),
        "location": s.location,
        "start": s.start,
        "end": s.end,
        "demand": s.demand_count,
        "assigned_employees": list(s.assigned_employees or []),
        "needs_experienced": bool(s.needs_experienced),
        "missing_minutes": int(s.missing_minutes or 0),
        "assigned_employees_detail": list(meta_dict.get("assigned_employees_detail", [])),
        "missing_segments": list(meta_dict.get("missing_segments", [])),
    }


def _assignments_from_db(demand: Demand) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in demand.shifts.all().order_by("date", "location", "start", "end"):
        out.append(_shift_base_dict(s))
    return out


def _assignments_for_day_from_db(
    demand: Demand, day: str, location: Optional[str] = None
) -> List[Dict[str, Any]]:
    qs = demand.shifts.filter(date=day)
    if location:
        qs = qs.filter(location=location)
    out: List[Dict[str, Any]] = []
    for s in qs.order_by("location", "start"):
        out.append(_shift_base_dict(s))
    return out


def _get_or_build_day_index(day: str, location: str) -> DayDemandIndex | None:
    idx = DayDemandIndex.objects.filter(date=day, location=location).order_by("-id").first()
    if idx:
        return idx
    # Lazy backfill by scanning existing demands in range
    from datetime import date as _date

    try:
        day_dt = _date.fromisoformat(day)
    except Exception:
        return None
    candidates = Demand.objects.filter(date_from__lte=day_dt, date_to__gte=day_dt).order_by("-created_at")
    for d in candidates:
        groups = _group_payload_by_day_location(d.raw_payload or [])
        items = groups.get((day, location))
        if not items:
            continue
        canon = _canonicalize_day_items(items, day, location)
        h = _day_hash(day, location, canon)
        try:
            idx, _ = DayDemandIndex.objects.get_or_create(
                date=day, location=location, day_hash=h, defaults={"demand": d}
            )
            return idx
        except Exception:
            # may race; retry fetch
            idx = (
                DayDemandIndex.objects.filter(date=day, location=location, day_hash=h)
                .order_by("-id")
                .first()
            )
            if idx:
                return idx
    return None


def _get_default_template(
    company: Company, location: str, weekday: Optional[int] = None
) -> List[Dict[str, Any]]:
    base_qs = DefaultDemand.objects.filter(company=company, location=location)
    if weekday is not None:
        obj = base_qs.filter(weekday=weekday).order_by("-updated_at").first()
        if obj:
            return _canonicalize_template_items(obj.items or [])
    obj = base_qs.filter(weekday__isnull=True).order_by("-updated_at").first()
    if obj:
        return _canonicalize_template_items(obj.items or [])
    return []


def _list_default_days(company: Company, location: str) -> List[Dict[str, Any]]:
    defaults = DefaultDemand.objects.filter(company=company, location=location).order_by("weekday", "id")
    out: List[Dict[str, Any]] = []
    for obj in defaults:
        canon = _canonicalize_template_items(obj.items or [])
        out.append(
            dict(
                weekday=obj.weekday,
                items=canon,
                updated_at=timezone.localtime(obj.updated_at).isoformat(),
            )
        )
    return out


def _build_default_week(company: Company, location: str) -> List[Dict[str, Any]]:
    qs = DefaultDemand.objects.filter(company=company, location=location).order_by("-updated_at", "-id")
    by_weekday: dict[int, Dict[str, Any]] = {}
    fallback: Optional[Dict[str, Any]] = None

    for obj in qs:
        canon = _canonicalize_template_items(obj.items or [])
        entry = dict(
            items=canon,
            updated_at=timezone.localtime(obj.updated_at).isoformat() if obj.updated_at else None,
        )
        if obj.weekday is None:
            if fallback is None:
                fallback = entry
        else:
            if obj.weekday not in by_weekday:
                by_weekday[obj.weekday] = entry

    def _serialize_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [DemandSlotOut(**item).dict() for item in (items or [])]

    out: List[Dict[str, Any]] = []
    for weekday in range(7):
        if weekday in by_weekday:
            data = by_weekday[weekday]
            out.append(
                DefaultDemandWeekDayOut(
                    weekday=weekday,
                    items=_serialize_items(data["items"]),
                    updated_at=data["updated_at"],
                    inherited=False,
                ).dict()
            )
        elif fallback is not None:
            out.append(
                DefaultDemandWeekDayOut(
                    weekday=weekday,
                    items=_serialize_items(fallback["items"]),
                    updated_at=fallback["updated_at"],
                    inherited=True,
                ).dict()
            )
        else:
            out.append(
                DefaultDemandWeekDayOut(
                    weekday=weekday,
                    items=[],
                    updated_at=None,
                    inherited=False,
                ).dict()
            )

    return out


def _upsert_default_day(
    company: Company, location: str, weekday: Optional[int], canon_items: List[Dict[str, Any]]
) -> DefaultDemand:
    obj, created = DefaultDemand.objects.get_or_create(
        company=company,
        location=location,
        weekday=weekday,
        defaults={"items": canon_items},
    )
    if not created:
        obj.items = canon_items
        update_fields = ["items", "updated_at"]
        if obj.company_id != company.id:
            obj.company = company
            update_fields.append("company")
        if obj.weekday != weekday:
            obj.weekday = weekday
            update_fields.append("weekday")
        obj.save(update_fields=update_fields)
    return obj


def _extract_location_from_payload(items: List[Dict[str, Any]]) -> str:
    for it in items or []:
        loc = (it or {}).get("location")
        if loc:
            return str(loc)
    return ""


def _apply_special_rules_to_demand(
    items: List[Dict[str, Any]], date_from, date_to
) -> List[Dict[str, Any]]:
    """Apply SpecialDay/EventRule transformations to incoming demand items without mutating originals.
    - Exact (date+location) rules take precedence; then wildcard (date+"") rules are applied if present.
    - Multiple rules for the same key are applied in creation order.
    """
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


__all__ = [
    "_apply_special_rules_to_demand",
    "_as_mapping",
    "_assignments_for_day_from_db",
    "_assignments_from_db",
    "_build_default_week",
    "_build_emp_availability",
    "_canonicalize_day_items",
    "_canonicalize_template_items",
    "_coerce_slots",
    "_day_hash",
    "_extract_location_from_payload",
    "_get_company_for_request",
    "_get_company_location",
    "_get_default_template",
    "_get_or_build_day_index",
    "_group_payload_by_day_location",
    "_hash_payload",
    "_infer_location",
    "_list_default_days",
    "_normalize_weekday",
    "_populate_day_index_for_demand",
    "_shift_base_dict",
    "_shift_uid",
    "_strip_day_items",
    "_upsert_default_day",
    "_validate_slots",
    "_with_ids",
]
