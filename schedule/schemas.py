from datetime import date
from typing import List, Optional, Union, Any
from ninja import Schema

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

class AvailabilityOut(Schema):
    employee_id: str
    employee_name: str
    date: str
    experienced: bool
    hours_min: int
    hours_max: int
    available_slots: List[SlotOut]
