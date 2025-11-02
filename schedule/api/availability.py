from __future__ import annotations

from datetime import date as date_type
from typing import Any, Dict, List

from django.db import transaction
from ninja.errors import HttpError

from ..models import Availability
from ..schemas import AvailabilityOut, BulkAvailabilityIn
from .router import api
from .utils import _coerce_slots, _validate_slots


@api.post(
    "/availability/bulk",
    response=List[AvailabilityOut],
    openapi_extra={
        "summary": "Upsert availability for one employee (bulk by days)",
        "description": (
            "Request body is a single object with employee context and an 'availabilities' array. "
            "The legacy doc that showed a root-level array is incorrect."
        ),
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
                            {
                                "date": "2025-10-20",
                                "available_slots": {"start": "03:00", "end": "16:00"},
                            },
                            {"date": "2025-10-21", "available_slots": None},
                            {
                                "date": "2025-10-24",
                                "available_slots": {"start": "14:00", "end": "20:30"},
                            },
                        ],
                    }
                }
            },
        },
    },
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
            ),
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
        )
        for o in saved
    ]


@api.get(
    "/availability",
    openapi_extra={
        "summary": "Lista dostępności pracownika",
        "description": (
            "Zwraca listę dostępności dla wskazanego pracownika wraz z paginacją i filtrami daty. "
            "Parametr only_with_slots usuwa dni bez zdefiniowanych przedziałów."
        ),
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
    items = list(qs[offset : offset + limit])

    results = [
        dict(
            employee_id=o.employee_id,
            employee_name=o.employee_name,
            date=o.date.isoformat(),
            experienced=o.experienced,
            hours_min=o.hours_min,
            hours_max=o.hours_max,
            available_slots=o.available_slots,
        )
        for o in items
    ]

    next_off = offset + limit if offset + limit < count else None
    prev_off = offset - limit if offset > 0 else None
    return {"count": count, "next": next_off, "previous": prev_off, "results": results}
