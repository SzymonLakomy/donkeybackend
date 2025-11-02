from __future__ import annotations

from typing import List

from django.db import transaction
from ninja.errors import HttpError

from ..models import EventRule
from ..schemas import EventRuleIn, EventRuleOut
from .router import api


@api.post(
    "/rules",
    response=EventRuleOut,
    openapi_extra={
        "summary": "Utwórz regułę zdarzenia",
        "description": "Dodaje regułę modyfikującą zapotrzebowanie (nadpisanie lub mnożnik) stosowaną dla dni specjalnych.",
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
        "summary": "Lista reguł zdarzeń",
        "description": "Zwraca wszystkie reguły wykorzystywane przy modyfikowaniu zapotrzebowania przez dni specjalne.",
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
        )
        for o in qs
    ]


@api.get(
    "/rules/{rule_id}",
    response=EventRuleOut,
    openapi_extra={
        "summary": "Szczegóły reguły zdarzenia",
        "description": "Zwraca konfigurację wybranej reguły wykorzystywanej przy dniach specjalnych.",
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
