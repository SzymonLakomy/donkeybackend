from __future__ import annotations

from datetime import date as date_type
from typing import List, Optional

from django.db import transaction
from ninja.errors import HttpError

from ..models import EventRule, SpecialDay
from ..schemas import SpecialDayIn, SpecialDayOut
from .router import api


@api.post(
    "/special-days",
    response=SpecialDayOut,
    openapi_extra={
        "summary": "Dodaj dzień specjalny",
        "description": "Łączy wskazany dzień i opcjonalną lokalizację z regułą wpływającą na zapotrzebowanie.",
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
        defaults=dict(
            note=payload.note or "",
            active=bool(payload.active if payload.active is not None else True),
        ),
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
        "summary": "Lista dni specjalnych",
        "description": "Zwraca aktywne i nieaktywne dni specjalne z możliwością filtrowania po dacie oraz lokalizacji.",
    },
)
def list_special_days(
    request,
    date_from: date_type | None = None,
    date_to: date_type | None = None,
    location: Optional[str] = None,
) -> List[SpecialDayOut]:
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
        )
        for o in qs
    ]
