from django.db import models

# Create your models here.

from django.db import models
from accounts.models import Company

BIG_MAX = 1_000_000_000

class Availability(models.Model):
    employee_id   = models.CharField(max_length=128, db_index=True)
    employee_name = models.CharField(max_length=255, blank=True, default="")
    date          = models.DateField(db_index=True)

    experienced   = models.BooleanField(default=False)
    hours_min     = models.PositiveIntegerField(default=0)
    hours_max     = models.PositiveIntegerField(default=BIG_MAX)

    # lista slotów: [{"start":"HH:MM","end":"HH:MM"}, ...]
    available_slots = models.JSONField(default=list)
    assigned_shift  = models.JSONField(null=True, blank=True)

    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("employee_id", "date")
        ordering = ["employee_id", "date"]

    def __str__(self):
        return f"{self.employee_id} @ {self.date}"


class Demand(models.Model):
    name          = models.CharField(max_length=255, blank=True, default="")
    # Original list of demand shifts in donkey_ai format
    raw_payload   = models.JSONField(default=list)
    content_hash  = models.CharField(max_length=64, unique=True, db_index=True)
    date_from     = models.DateField(db_index=True)
    date_to       = models.DateField(db_index=True)

    schedule_generated = models.BooleanField(default=False)
    solved_at     = models.DateTimeField(null=True, blank=True)

    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Demand#{self.id} {self.name} [{self.date_from}..{self.date_to}]"


class ScheduleShift(models.Model):
    demand        = models.ForeignKey(Demand, related_name="shifts", on_delete=models.CASCADE)
    # Stable unique id to edit a single change easily. Includes demand id to avoid cross-demand collisions.
    shift_uid     = models.CharField(max_length=512, unique=True, db_index=True)

    date          = models.DateField(db_index=True)
    location      = models.CharField(max_length=255)
    start         = models.CharField(max_length=5)
    end           = models.CharField(max_length=5)

    demand_count  = models.PositiveIntegerField(default=1)
    needs_experienced = models.BooleanField(default=False)

    assigned_employees = models.JSONField(default=list)
    missing_minutes    = models.PositiveIntegerField(default=0)

    user_edited   = models.BooleanField(default=False)
    confirmed     = models.BooleanField(default=False)

    meta          = models.JSONField(null=True, blank=True)

    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("demand", "date", "location", "start", "end")
        ordering = ["date", "location", "start", "end"]

    def __str__(self):
        return f"Shift[{self.date} {self.location} {self.start}-{self.end}] @Demand {self.demand_id}"


# ===== Special events (holidays/festivals/etc.) =====
class EventRule(models.Model):
    MODE_OVERRIDE = "override"
    MODE_MULTIPLIER = "multiplier"
    MODE_CHOICES = [
        (MODE_OVERRIDE, "Override"),
        (MODE_MULTIPLIER, "Multiplier"),
    ]

    name = models.CharField(max_length=255)
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default=MODE_OVERRIDE)
    value = models.FloatField(help_text="If override: target demand (int). If multiplier: multiply incoming demand.")

    needs_experienced_default = models.BooleanField(default=False)
    min_demand = models.PositiveIntegerField(null=True, blank=True)
    max_demand = models.PositiveIntegerField(null=True, blank=True)

    active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return f"Rule#{self.id} {self.name} ({self.mode}={self.value})"


class SpecialDay(models.Model):
    date = models.DateField(db_index=True)
    location = models.CharField(max_length=255, blank=True, default="", db_index=True)
    rule = models.ForeignKey(EventRule, related_name="special_days", on_delete=models.PROTECT)
    note = models.CharField(max_length=255, blank=True, default="")
    active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("date", "location", "rule")
        indexes = [
            models.Index(fields=["date", "location"]),
        ]
        ordering = ["-date", "location"]

    def __str__(self):
        loc = self.location or "*"
        return f"SpecialDay[{self.date} {loc}] -> Rule#{self.rule_id}"


# ===== Day-level idempotency index (map day/location to a Demand by canonical day hash) =====
class DayDemandIndex(models.Model):
    demand = models.ForeignKey(Demand, related_name="day_indexes", on_delete=models.CASCADE)
    date = models.DateField(db_index=True)
    location = models.CharField(max_length=255, db_index=True)
    day_hash = models.CharField(max_length=64, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("date", "location", "day_hash")
        indexes = [
            models.Index(fields=["date", "location"]),
            models.Index(fields=["day_hash"]),
        ]
        ordering = ["-date", "location", "-id"]

    def __str__(self):
        return f"DayIndex[{self.date} {self.location} #{self.day_hash[:8]}] -> Demand {self.demand_id}"


class CompanyLocation(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="schedule_locations")
    name = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("company", "name")
        ordering = ["name"]

    def __str__(self):
        return f"{self.company.name}::{self.name}"


class DefaultDemand(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="default_demands", null=True, blank=True)
    location = models.CharField(max_length=255)
    weekday = models.PositiveSmallIntegerField(null=True, blank=True)
    items = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location", "weekday"]
        unique_together = ("company", "location", "weekday")

    def __str__(self):
        day = "*" if self.weekday is None else str(self.weekday)
        company_code = self.company.code if self.company_id else "?"
        return f"DefaultDemand[{company_code}:{self.location}:{day}]"
