from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.db.models import Q
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


def _get_request_company(request):
    _ensure_authenticated(request)
    company = getattr(request.user, "company", None)
    if not company:
        raise HttpError(400, "User is not assigned to a company")
    return company


def _company_scope(model_cls, company):
    company_id = getattr(company, "id", None)
    scope = Q(company=company)
    if company_id is not None:
        scope |= Q(company__isnull=True)
    return model_cls.objects.filter(scope)


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
        "company_id": obj.company_id,
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
        "company_id": obj.company_id,
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
        "company_id": obj.company_id,
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
    company = _get_request_company(request)

    category = payload.category or CalendarEvent.CATEGORY_SCHEDULE
    if category not in _ALLOWED_EVENT_CATEGORIES:
        raise HttpError(400, "Unsupported event category")

    normalized_start = _normalize_dt(payload.start_at)
    normalized_end = _normalize_dt(payload.end_at)

    if normalized_start and normalized_end:
        start = normalized_start
        end = normalized_end
    elif normalized_start and not normalized_end:
        start = normalized_start
        end = start + timedelta(hours=1)
    elif not normalized_start and normalized_end:
        end = normalized_end
        start = end - timedelta(hours=1)
    else:
        start = timezone.now()
        end = start + timedelta(hours=1)

    _validate_range(start, end)

    employee_id = (payload.employee_id or str(getattr(request.user, "id", "")) or "").strip()
    if not employee_id:
        raise HttpError(400, "Unable to determine employee context")

    title = (payload.title or "Wydarzenie").strip() or "Wydarzenie"
    event = CalendarEvent.objects.create(
        company=company,
        employee_id=employee_id,
        title=title,
        description=(payload.description or "").strip(),
        start_at=start,
        end_at=end,
        category=category,
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
    company = _get_request_company(request)

    limit = max(1, min(limit, 500))
    qs = _company_scope(CalendarEvent, company).order_by("start_at", "id")

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
    company = _get_request_company(request)

    status = payload.status or MedicalCheckEvent.STATUS_PLANNED
    if status not in _ALLOWED_MEDICAL_STATUSES:
        raise HttpError(400, "Unsupported medical event status")

    normalized_start = _normalize_dt(payload.start_at)
    normalized_end = _normalize_dt(payload.end_at)

    if normalized_start and normalized_end:
        start = normalized_start
        end = normalized_end
    elif normalized_start and not normalized_end:
        start = normalized_start
        end = start + timedelta(hours=1)
    elif not normalized_start and normalized_end:
        end = normalized_end
        start = end - timedelta(hours=1)
    else:
        start = timezone.now()
        end = start + timedelta(hours=1)

    _validate_range(start, end)

    employee_id = (payload.employee_id or str(getattr(request.user, "id", "")) or "").strip()
    if not employee_id:
        raise HttpError(400, "Unable to determine employee context")

    title = (payload.title or "Badanie").strip() or "Badanie"

    medical_event = MedicalCheckEvent.objects.create(
        company=company,
        employee_id=employee_id,
        title=title,
        description=(payload.description or "").strip(),
        exam_type=(payload.exam_type or "").strip(),
        start_at=start,
        end_at=end,
        location=(payload.location or "").strip(),
        status=status,
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
    company = _get_request_company(request)

    limit = max(1, min(limit, 500))
    qs = _company_scope(MedicalCheckEvent, company).order_by("start_at", "id")

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
    company = _get_request_company(request)

    provider = payload.provider or ExternalCalendarConnection.PROVIDER_OTHER
    if provider not in _ALLOWED_PROVIDERS:
        raise HttpError(400, "Unsupported provider")

    last_synced_at = _normalize_dt(payload.last_synced_at) if payload.last_synced_at else None

    name = (payload.name or provider.title()).strip() or provider.title()

    owner_id = (payload.employee_id or str(getattr(request.user, "id", "")) or "").strip()

    connection = ExternalCalendarConnection.objects.create(
        company=company,
        name=name,
        provider=provider,
        employee_id=owner_id,
        external_id=(payload.external_id or "").strip(),
        sync_token=(payload.sync_token or "").strip(),
        settings=dict(payload.settings or {}),
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
    company = _get_request_company(request)

    limit = max(1, min(limit, 200))
    qs = _company_scope(ExternalCalendarConnection, company).order_by("-updated_at", "id")

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
    company = _get_request_company(request)

    connection = get_object_or_404(
        _company_scope(ExternalCalendarConnection, company),
        pk=source_id,
    )

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
