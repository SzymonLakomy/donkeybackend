from typing import List, Dict, Any
from django.db import transaction
from django.utils.timezone import make_aware
from ninja import Router
from ninja.errors import HttpError
from datetime import datetime, date as date_type

from .models import Availability
from .schemas import BulkAvailabilityIn, AvailabilityOut
from donkeybackend.security import DRFJWTAuth

api = Router(tags=["schedule"], auth=DRFJWTAuth())


def _norm_hhmm(s: str) -> str:
    if not s:
        return s
    s = s.strip().replace(" ", "").replace(".", ":")
    if ":" in s:
        hh, mm = s.split(":", 1)
        return f"{int(hh):02d}:{int(mm):02d}"
    return f"{int(s):02d}:00"

def _coerce_slots(val) -> list[dict]:
    if val is None:
        return []
    if isinstance(val, dict):
        start = _norm_hhmm(val.get("start", ""))
        end   = _norm_hhmm(val.get("end", ""))
        return [{"start": start, "end": end}] if start and end else []
    out = []
    for x in val:
        start = _norm_hhmm(x.get("start", ""))
        end   = _norm_hhmm(x.get("end", ""))
        if start and end:
            out.append({"start": start, "end": end})
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



@api.post("/availability/bulk", response=List[AvailabilityOut])
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
@api.get("/availability")
def list_availability(
    request,
    employee_id: str | None = None,
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

    if employee_id:
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