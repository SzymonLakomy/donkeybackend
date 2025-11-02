from __future__ import annotations

from datetime import date as date_type
from typing import Any, Dict, List, Optional

from django.db import transaction
from ninja.errors import HttpError

from ..models import DayDemandIndex, Demand
from ..schemas import (
    DemandDayIn,
    DemandDayOut,
    DemandSlotOut,
    DefaultDemandBulkIn,
    DefaultDemandDayOut,
    DefaultDemandIn,
    DefaultDemandOut,
    DefaultDemandWeekOut,
)
from .router import api
from .utils import (
    _canonicalize_day_items,
    _canonicalize_template_items,
    _day_hash,
    _extract_location_from_payload,
    _get_company_for_request,
    _get_default_template,
    _get_or_build_day_index,
    _group_payload_by_day_location,
    _infer_location,
    _list_default_days,
    _normalize_weekday,
    _populate_day_index_for_demand,
    _strip_day_items,
    _upsert_default_day,
    _build_default_week,
)


@api.post(
    "/demand/day",
    response=DemandDayOut,
    openapi_extra={
        "summary": "Zapisz zapotrzebowanie na dzień",
        "description": (
            "Tworzy albo nadpisuje zapotrzebowanie dla wskazanej daty. Lokalizację bierzemy z użytkownika, "
            "ale można ją podać w polu location. Jeśli nie przekażesz listy zmian użyjemy domyślnego wzoru restauracji."
        ),
    },
)
@transaction.atomic
def save_day_demand(request, payload: DemandDayIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    loc = _infer_location(request, payload.location, create_if_missing=True)
    day = (payload.date or "").strip()
    if not day:
        raise HttpError(400, "Missing 'date'")

    try:
        from datetime import date as _date

        day_dt = _date.fromisoformat(day)
    except Exception:
        raise HttpError(400, "Invalid date format. Użyj YYYY-MM-DD")

    company = _get_company_for_request(request)
    weekday = day_dt.weekday()

    raw_items = [dict(x) for x in (payload.items or [])]
    if raw_items:
        canon_items = _canonicalize_day_items(raw_items, day, loc)
    else:
        template = _get_default_template(company, loc, weekday)
        if not template:
            raise HttpError(400, "Brak listy zmian i brak domyślnego zapotrzebowania dla tej restauracji")
        canon_items = _canonicalize_day_items(template, day, loc)

    if not canon_items:
        raise HttpError(400, "Lista zmian jest pusta")

    content_hash = _day_hash(day, loc, canon_items)

    idx = DayDemandIndex.objects.filter(date=day, location=loc).order_by("-id").first()
    if idx:
        obj = idx.demand
    else:
        obj = None

    if obj is None:
        obj, created = Demand.objects.get_or_create(
            content_hash=content_hash,
            defaults=dict(name="", raw_payload=canon_items, date_from=day_dt, date_to=day_dt),
        )
        if not created:
            DayDemandIndex.objects.filter(demand=obj).delete()
    else:
        DayDemandIndex.objects.filter(demand=obj).delete()

    obj.raw_payload = canon_items
    obj.date_from = day_dt
    obj.date_to = day_dt
    obj.content_hash = content_hash
    obj.name = ""
    obj.schedule_generated = False
    obj.save(
        update_fields=[
            "raw_payload",
            "date_from",
            "date_to",
            "content_hash",
            "name",
            "schedule_generated",
            "updated_at",
        ]
    )
    obj.shifts.all().delete()

    _populate_day_index_for_demand(obj)

    return dict(
        date=day,
        location=loc,
        items=[DemandSlotOut(**item).dict() for item in _strip_day_items(canon_items)],
        content_hash=obj.content_hash,
    )


@api.get(
    "/demand/day",
    response=DemandDayOut,
    openapi_extra={
        "summary": "Pobierz zapotrzebowanie na dzień",
        "description": (
            "Zwraca zapotrzebowanie dla daty i restauracji. Jeśli brak rekordu zwracamy domyślne zapotrzebowanie lub pustą listę."
        ),
    },
)
def get_day_demand(request, date: str, location: Optional[str] = None) -> Dict[str, Any]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    loc = _infer_location(request, location)
    day = (date or "").strip()
    if not day:
        raise HttpError(400, "Missing 'date'")

    try:
        from datetime import date as _date

        day_dt = _date.fromisoformat(day)
    except Exception:
        raise HttpError(400, "Invalid date format. Użyj YYYY-MM-DD")

    company = _get_company_for_request(request)
    weekday = day_dt.weekday()

    idx = _get_or_build_day_index(day, loc)
    if idx:
        demand = idx.demand
        groups = _group_payload_by_day_location(demand.raw_payload or [])
        day_items = _canonicalize_day_items(groups.get((day, loc), []), day, loc)
        return dict(
            date=day,
            location=loc,
            items=[DemandSlotOut(**item).dict() for item in _strip_day_items(day_items)],
            content_hash=_day_hash(day, loc, day_items) if day_items else demand.content_hash,
        )

    template = _get_default_template(company, loc, weekday)
    if template:
        return dict(
            date=day,
            location=loc,
            items=[DemandSlotOut(**item).dict() for item in template],
            content_hash=None,
        )

    return dict(date=day, location=loc, items=[], content_hash=None)


@api.post(
    "/demand/default",
    response=DefaultDemandOut,
    openapi_extra={
        "summary": "Ustaw domyślne zapotrzebowanie",
        "description": (
            "Zapisuje listę zmian jako domyślne zapotrzebowanie restauracji. "
            "Wykorzystujemy je gdy nie podasz własnej listy dla dnia."
        ),
    },
)
@transaction.atomic
def save_default_demand(request, payload: DefaultDemandIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    loc = _infer_location(request, payload.location, create_if_missing=True)
    company = _get_company_for_request(request)
    weekday = _normalize_weekday(getattr(payload, "weekday", None))
    raw_items = [dict(x) for x in (payload.items or [])]
    canon = _canonicalize_template_items(raw_items)
    if not canon:
        raise HttpError(400, "Lista zmian jest pusta")

    _upsert_default_day(company, loc, weekday, canon)

    defaults = _list_default_days(company, loc)
    return dict(
        location=loc,
        defaults=[
            DefaultDemandDayOut(
                weekday=entry["weekday"],
                items=[DemandSlotOut(**item).dict() for item in entry["items"]],
                updated_at=entry["updated_at"],
            ).dict()
            for entry in defaults
        ],
    )


@api.post(
    "/demand/default/bulk",
    response=DefaultDemandOut,
    openapi_extra={
        "summary": "Ustaw domyślne zapotrzebowania dla wielu dni tygodnia",
        "description": "Przyjmuje listę dni tygodnia wraz z zestawami zmian i zapisuje je jako domyślny wzór.",
    },
)
@transaction.atomic
def save_default_demand_bulk(request, payload: DefaultDemandBulkIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    loc = _infer_location(request, payload.location, create_if_missing=True)
    company = _get_company_for_request(request)

    if not payload.defaults:
        raise HttpError(400, "Przekaż przynajmniej jeden dzień tygodnia")

    for day in payload.defaults:
        weekday = _normalize_weekday(day.weekday)
        raw_items = [dict(x) for x in (day.items or [])]
        canon = _canonicalize_template_items(raw_items)
        if not canon:
            raise HttpError(
                400,
                f"Lista zmian dla dnia tygodnia {weekday if weekday is not None else '*'} jest pusta",
            )
        _upsert_default_day(company, loc, weekday, canon)

    defaults = _list_default_days(company, loc)
    return dict(
        location=loc,
        defaults=[
            DefaultDemandDayOut(
                weekday=entry["weekday"],
                items=[DemandSlotOut(**item).dict() for item in entry["items"]],
                updated_at=entry["updated_at"],
            ).dict()
            for entry in defaults
        ],
    )


@api.get(
    "/demand/default/week",
    response=DefaultDemandWeekOut,
    openapi_extra={
        "summary": "Pobierz pełny tygodniowy szablon zapotrzebowania",
        "description": (
            "Zwraca listę siedmiu dni (poniedziałek=0 … niedziela=6) wraz z domyślnymi zmianami. "
            "Jeżeli dla dnia nie ma indywidualnego zapisu, zostaną użyte zmiany z szablonu ogólnego (weekday=null)."
        ),
    },
)
def get_default_demand_week(request, location: Optional[str] = None):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    loc = _infer_location(request, location)
    company = _get_company_for_request(request)
    defaults = _build_default_week(company, loc)

    return dict(
        location=loc,
        defaults=defaults,
    )


@api.get(
    "/demand/default",
    response=DefaultDemandOut,
    openapi_extra={
        "summary": "Pobierz domyślne zapotrzebowanie",
        "description": (
            "Zwraca aktualny domyślny zestaw zmian dla restauracji. Możesz ograniczyć odpowiedź do wybranego dnia tygodnia."
        ),
    },
)
def get_default_demand(request, location: Optional[str] = None, weekday: Optional[int] = None):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    loc = _infer_location(request, location)
    company = _get_company_for_request(request)
    weekday_norm = _normalize_weekday(weekday) if weekday is not None else None

    defaults = _list_default_days(company, loc)
    if weekday_norm is not None:
        defaults = [entry for entry in defaults if entry["weekday"] == weekday_norm]

    return dict(
        location=loc,
        defaults=[
            DefaultDemandDayOut(
                weekday=entry["weekday"],
                items=[DemandSlotOut(**item).dict() for item in entry["items"]],
                updated_at=entry["updated_at"],
            ).dict()
            for entry in defaults
        ],
    )


@api.get(
    "/demand/{demand_id}",
    openapi_extra={
        "summary": "Szczegóły zapotrzebowania",
        "description": "Zwraca pełny opis zapotrzebowania wraz z listą zmian i informacją o wygenerowanym grafiku.",
    },
)
def get_demand(request, demand_id: int) -> Dict[str, Any]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        d = Demand.objects.get(id=demand_id)
    except Demand.DoesNotExist:
        raise HttpError(404, "Demand not found")
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
        "summary": "Lista zapotrzebowań",
        "description": "Zwraca zapotrzebowania posortowane malejąco po dacie utworzenia wraz z paginacją.",
    },
)
def list_demands(request, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    limit = max(1, min(200, limit))
    qs = Demand.objects.all().order_by("-created_at")
    count = qs.count()
    items = list(qs[offset : offset + limit])
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
