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
#api = Router(tags=["schedule"])

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
        "summary": "Upsert availability for one employee (bulk by days)",
        "description": "Request body is a single object with employee context and an 'availabilities' array. The legacy doc that showed a root-level array is incorrect.",
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
@api.get("/availability")
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