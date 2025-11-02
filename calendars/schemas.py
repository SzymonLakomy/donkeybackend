from datetime import datetime
from typing import Any, Dict, Literal, Optional

from ninja import Schema


class CalendarEventIn(Schema):
    employee_id: Optional[str] = None
    title: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    category: Optional[Literal["schedule", "leave", "training"]] = None
    description: Optional[str] = None
    location: Optional[str] = None
    color: Optional[str] = None


class CalendarEventOut(CalendarEventIn):
    id: int
    company_id: Optional[int]
    created_at: datetime
    updated_at: datetime


class MedicalEventIn(Schema):
    employee_id: Optional[str] = None
    title: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    exam_type: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    status: Optional[Literal["planned", "confirmed", "completed", "cancelled"]] = "planned"
    notes: Optional[str] = None


class MedicalEventOut(MedicalEventIn):
    id: int
    company_id: Optional[int]
    created_at: datetime
    updated_at: datetime


class ExternalCalendarIn(Schema):
    name: Optional[str] = None
    provider: Optional[Literal["ics", "google", "outlook", "other"]] = "other"
    employee_id: Optional[str] = None
    external_id: Optional[str] = None
    sync_token: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    active: bool = True
    last_synced_at: Optional[datetime] = None


class ExternalCalendarOut(ExternalCalendarIn):
    id: int
    company_id: Optional[int]
    created_at: datetime
    updated_at: datetime


class ExternalCalendarSyncIn(Schema):
    sync_token: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None
