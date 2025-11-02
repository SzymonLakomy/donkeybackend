from datetime import date
from typing import List, Optional, Union, Any, Dict
from ninja import Schema


class CompanyLocationIn(Schema):
    name: str


class CompanyLocationOut(Schema):
    id: int
    name: str
    created_at: str


class SlotIn(Schema):
    start: str
    end: str

class DailyAvailabilityIn(Schema):
    date: date
    # z mobilki: null | {start,end} | [{start,end}, ...]
    available_slots: Optional[Union[SlotIn, List[SlotIn], None]] = None

class BulkAvailabilityIn(Schema):
    employee_id: Any                  # liczba/tekst – rzutujemy na str
    employee_name: Optional[str] = ""
    experienced: Optional[bool] = False
    hours_min: Optional[int] = 0
    hours_max: Optional[int] = 1_000_000_000
    availabilities: List[DailyAvailabilityIn]

class SlotOut(Schema):
    start: str
    end: str


class ShiftEmployeeSegmentOut(Schema):
    start: str
    end: str
    minutes: int


class ShiftAssignedEmployeeOut(Schema):
    employee_id: str
    employee_name: Optional[str] = ""
    start: Optional[str] = None
    end: Optional[str] = None
    minutes: int
    segments: List[ShiftEmployeeSegmentOut]


class ShiftMissingSegmentOut(Schema):
    start: str
    end: str
    missing: int
    missing_minutes: int

class AvailabilityOut(Schema):
    employee_id: str
    employee_name: str
    date: str
    experienced: bool
    hours_min: int
    hours_max: int
    available_slots: List[SlotOut]

# ---- Demand / Schedule ----
class DemandSlotOut(Schema):
    start: str
    end: str
    demand: int
    needs_experienced: bool


class DemandShiftTemplateIn(Schema):
    start: str
    end: str
    demand: int
    needs_experienced: Optional[bool] = False


class DemandDayIn(Schema):
    date: str
    location: Optional[str] = None
    items: Optional[List[DemandShiftTemplateIn]] = None


class DemandDayOut(Schema):
    date: str
    location: str
    items: List[DemandSlotOut]
    content_hash: Optional[str] = None


class ShiftEmployeeSegmentIn(Schema):
    start: str
    end: str
    minutes: Optional[int] = None


class ShiftAssignedEmployeeIn(Schema):
    employee_id: str
    employee_name: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    minutes: Optional[int] = None
    segments: Optional[List[ShiftEmployeeSegmentIn]] = None


class ShiftMissingSegmentIn(Schema):
    start: str
    end: str
    missing: int
    missing_minutes: Optional[int] = None


class ScheduleShiftOut(Schema):
    id: str
    date: str
    location: str
    start: str
    end: str
    demand: int
    assigned_employees: List[str]
    needs_experienced: bool
    missing_minutes: int
    assigned_employees_detail: Optional[List[ShiftAssignedEmployeeOut]] = None
    missing_segments: Optional[List[ShiftMissingSegmentOut]] = None

class ScheduleFullOut(Schema):
    assignments: List[ScheduleShiftOut]
    uncovered: List[dict]
    hours_summary: List[dict]

class ShiftUpdateIn(Schema):
    id: str
    date: Optional[str] = None
    location: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    demand: Optional[int] = None
    assigned_employees: Optional[List[str]] = None
    needs_experienced: Optional[bool] = None
    missing_minutes: Optional[int] = None
    confirmed: Optional[bool] = None
    assigned_employees_detail: Optional[List[ShiftAssignedEmployeeIn]] = None
    missing_segments: Optional[List[ShiftMissingSegmentIn]] = None

class ShiftOut(Schema):
    id: str
    date: str
    location: str
    start: str
    end: str
    demand: int
    assigned_employees: List[str]
    needs_experienced: bool
    missing_minutes: int
    confirmed: bool
    user_edited: bool
    assigned_employees_detail: Optional[List[ShiftAssignedEmployeeOut]] = None
    missing_segments: Optional[List[ShiftMissingSegmentOut]] = None

# ---- Special events (rules + special days) ----
class EventRuleIn(Schema):
    name: str
    mode: str  # "override" | "multiplier"
    value: float
    needs_experienced_default: Optional[bool] = False
    min_demand: Optional[int] = None
    max_demand: Optional[int] = None
    active: Optional[bool] = True

class EventRuleOut(Schema):
    id: int
    name: str
    mode: str
    value: float
    needs_experienced_default: bool
    min_demand: Optional[int]
    max_demand: Optional[int]
    active: bool

class SpecialDayIn(Schema):
    date: date
    location: Optional[str] = ""
    rule_id: int
    note: Optional[str] = ""
    active: Optional[bool] = True

class SpecialDayOut(Schema):
    id: int
    date: str
    location: str
    rule_id: int
    rule_name: str
    note: str
    active: bool

# ---- Generation helpers ----


class DefaultDemandIn(Schema):
    location: Optional[str] = None
    weekday: Optional[int] = None
    items: List[DemandShiftTemplateIn]


class DefaultDemandDayIn(Schema):
    weekday: Optional[int] = None
    items: List[DemandShiftTemplateIn]


class DefaultDemandBulkIn(Schema):
    location: Optional[str] = None
    defaults: List[DefaultDemandDayIn]


class DefaultDemandDayOut(Schema):
    weekday: Optional[int] = None
    items: List[DemandSlotOut]
    updated_at: Optional[str] = None


class DefaultDemandOut(Schema):
    location: str
    defaults: List[DefaultDemandDayOut]


class DefaultDemandWeekDayOut(DefaultDemandDayOut):
    inherited: bool = False


class DefaultDemandWeekOut(Schema):
    location: str
    defaults: List[DefaultDemandWeekDayOut]

class GenerateDayIn(Schema):
    date: str
    location: Optional[str] = None
    persist: Optional[bool] = True
    force: Optional[bool] = False
    items: Optional[List[DemandShiftTemplateIn]] = None  # if omitted, backend may use template or return 400

class GenerateRangeIn(Schema):
    date_from: str
    date_to: str
    location: Optional[str] = None
    persist: Optional[bool] = True
    force: Optional[bool] = False
    items: Optional[List[DemandShiftTemplateIn]] = None  # template repeated for each day

class GenerateResultOut(Schema):
    demand_id: int
    assignments: List[ScheduleShiftOut]
    summary: Optional[Dict[str, Any]] = None
