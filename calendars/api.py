from datetime import datetime
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from donkeybackend.security import DRFJWTAuth

from .models import CalendarEvent, ExternalCalendarConnection, MedicalCheckEvent
from .schemas import (
    CalendarEventIn,
    CalendarEventOut,
    ExternalCalendarIn,
    ExternalCalendarOut,
    ExternalCalendarSyncIn,
    MedicalEventIn,
    MedicalEventOut,
)

api = Router(tags=["calendar"], auth=DRFJWTAuth())


_ALLOWED_EVENT_CATEGORIES = {choice[0] for choice in CalendarEvent.CATEGORY_CHOICES}
_ALLOWED_MEDICAL_STATUSES = {choice[0] for choice in MedicalCheckEvent.STATUS_CHOICES}
_ALLOWED_PROVIDERS = {choice[0] for choice in ExternalCalendarConnection.PROVIDER_CHOICES}


def _ensure_authenticated(request) -> None:
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")


def _normalize_dt(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return value
    current_tz = timezone.get_current_timezone()
    if timezone.is_naive(value):
        return timezone.make_aware(value, current_tz)
    return value.astimezone(current_tz)


def _validate_range(start, end) -> None:
    if end <= start:
        raise HttpError(400, "end_at must be later than start_at")


def _serialize_calendar_event(obj: CalendarEvent) -> Dict[str, Any]:
    return {
        "id": obj.id,
        "employee_id": obj.employee_id,
        "title": obj.title,
        "start_at": timezone.localtime(obj.start_at),
        "end_at": timezone.localtime(obj.end_at),
        "category": obj.category,
        "description": obj.description or None,
        "location": obj.location or None,
        "color": obj.color or None,
        "created_at": timezone.localtime(obj.created_at),
        "updated_at": timezone.localtime(obj.updated_at),
    }


def _serialize_medical_event(obj: MedicalCheckEvent) -> Dict[str, Any]:
    return {
        "id": obj.id,
        "employee_id": obj.employee_id,
        "title": obj.title,
        "start_at": timezone.localtime(obj.start_at),
        "end_at": timezone.localtime(obj.end_at),
        "exam_type": obj.exam_type or None,
        "description": obj.description or None,
        "location": obj.location or None,
        "status": obj.status,
        "notes": obj.notes or None,
        "created_at": timezone.localtime(obj.created_at),
        "updated_at": timezone.localtime(obj.updated_at),
    }


def _serialize_external_calendar(obj: ExternalCalendarConnection) -> Dict[str, Any]:
    return {
        "id": obj.id,
        "name": obj.name,
        "provider": obj.provider,
        "employee_id": obj.employee_id or None,
        "external_id": obj.external_id or None,
        "sync_token": obj.sync_token or None,
        "settings": obj.settings or {},
        "active": obj.active,
        "last_synced_at": timezone.localtime(obj.last_synced_at) if obj.last_synced_at else None,
        "created_at": timezone.localtime(obj.created_at),
        "updated_at": timezone.localtime(obj.updated_at),
    }


@api.post("/events", response=CalendarEventOut)
@transaction.atomic
def create_calendar_event(request, payload: CalendarEventIn):
    _ensure_authenticated(request)

    if payload.category not in _ALLOWED_EVENT_CATEGORIES:
        raise HttpError(400, "Unsupported event category")

    start = _normalize_dt(payload.start_at)
    end = _normalize_dt(payload.end_at)
    _validate_range(start, end)

    event = CalendarEvent.objects.create(
        employee_id=payload.employee_id.strip(),
        title=payload.title.strip(),
        description=(payload.description or "").strip(),
        start_at=start,
        end_at=end,
        category=payload.category,
        location=(payload.location or "").strip(),
        color=(payload.color or "").strip(),
    )
    return _serialize_calendar_event(event)


@api.get("/events", response=List[CalendarEventOut])
def list_calendar_events(
    request,
    employee_id: Optional[str] = None,
    category: Optional[str] = None,
    start_from: Optional[datetime] = None,
    end_to: Optional[datetime] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    _ensure_authenticated(request)

    limit = max(1, min(limit, 500))
    qs = CalendarEvent.objects.all().order_by("start_at", "id")

    if employee_id:
        qs = qs.filter(employee_id=employee_id)

    if category:
        if category not in _ALLOWED_EVENT_CATEGORIES:
            raise HttpError(400, "Unsupported event category")
        qs = qs.filter(category=category)

    if start_from:
        qs = qs.filter(end_at__gte=_normalize_dt(start_from))
    if end_to:
        qs = qs.filter(start_at__lte=_normalize_dt(end_to))

    events = list(qs[:limit])
    return [_serialize_calendar_event(event) for event in events]


@api.post("/medical", response=MedicalEventOut)
@transaction.atomic
def create_medical_event(request, payload: MedicalEventIn):
    _ensure_authenticated(request)

    if payload.status not in _ALLOWED_MEDICAL_STATUSES:
        raise HttpError(400, "Unsupported medical event status")

    start = _normalize_dt(payload.start_at)
    end = _normalize_dt(payload.end_at)
    _validate_range(start, end)

    medical_event = MedicalCheckEvent.objects.create(
        employee_id=payload.employee_id.strip(),
        title=payload.title.strip(),
        description=(payload.description or "").strip(),
        exam_type=(payload.exam_type or "").strip(),
        start_at=start,
        end_at=end,
        location=(payload.location or "").strip(),
        status=payload.status,
        notes=(payload.notes or "").strip(),
    )
    return _serialize_medical_event(medical_event)


@api.get("/medical", response=List[MedicalEventOut])
def list_medical_events(
    request,
    employee_id: Optional[str] = None,
    status: Optional[str] = None,
    start_from: Optional[datetime] = None,
    end_to: Optional[datetime] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    _ensure_authenticated(request)

    limit = max(1, min(limit, 500))
    qs = MedicalCheckEvent.objects.all().order_by("start_at", "id")

    if employee_id:
        qs = qs.filter(employee_id=employee_id)

    if status:
        if status not in _ALLOWED_MEDICAL_STATUSES:
            raise HttpError(400, "Unsupported medical event status")
        qs = qs.filter(status=status)

    if start_from:
        qs = qs.filter(end_at__gte=_normalize_dt(start_from))
    if end_to:
        qs = qs.filter(start_at__lte=_normalize_dt(end_to))

    return [_serialize_medical_event(item) for item in qs[:limit]]


@api.post("/sources", response=ExternalCalendarOut)
@transaction.atomic
def create_external_calendar(request, payload: ExternalCalendarIn):
    _ensure_authenticated(request)

    if payload.provider not in _ALLOWED_PROVIDERS:
        raise HttpError(400, "Unsupported provider")

    last_synced_at = _normalize_dt(payload.last_synced_at) if payload.last_synced_at else None

    connection = ExternalCalendarConnection.objects.create(
        name=payload.name.strip(),
        provider=payload.provider,
        employee_id=(payload.employee_id or "").strip(),
        external_id=(payload.external_id or "").strip(),
        sync_token=(payload.sync_token or "").strip(),
        settings=payload.settings or {},
        active=payload.active,
        last_synced_at=last_synced_at,
    )
    return _serialize_external_calendar(connection)


@api.get("/sources", response=List[ExternalCalendarOut])
def list_external_calendars(
    request,
    provider: Optional[str] = None,
    active: Optional[bool] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    _ensure_authenticated(request)

    limit = max(1, min(limit, 200))
    qs = ExternalCalendarConnection.objects.all().order_by("-updated_at", "id")

    if provider:
        if provider not in _ALLOWED_PROVIDERS:
            raise HttpError(400, "Unsupported provider")
        qs = qs.filter(provider=provider)

    if active is not None:
        qs = qs.filter(active=bool(active))

    connections = list(qs[:limit])
    return [_serialize_external_calendar(conn) for conn in connections]


@api.post("/sources/{source_id}/sync", response=ExternalCalendarOut)
@transaction.atomic
def mark_calendar_synced(request, source_id: int, payload: ExternalCalendarSyncIn):
    _ensure_authenticated(request)

    connection = get_object_or_404(ExternalCalendarConnection, pk=source_id)

    updated = False
    if payload.sync_token is not None:
        connection.sync_token = payload.sync_token.strip()
        updated = True

    if payload.metadata:
        current_settings = dict(connection.settings or {})
        current_settings.update(payload.metadata)
        connection.settings = current_settings
        updated = True

    if payload.last_synced_at:
        connection.last_synced_at = _normalize_dt(payload.last_synced_at)
        updated = True
    elif not connection.last_synced_at:
        connection.last_synced_at = timezone.now()
        updated = True

    if updated:
        connection.save(update_fields=["sync_token", "settings", "last_synced_at", "updated_at"])

    return _serialize_external_calendar(connection)
