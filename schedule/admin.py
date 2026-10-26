from django.contrib import admin

from .models import (
    Availability,
    CompanyLocation,
    DayDemandIndex,
    DefaultDemand,
    Demand,
    EventRule,
    ScheduleShift,
    SpecialDay,
)


@admin.register(Demand)
class DemandAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "name", "date_from", "date_to", "schedule_generated", "solved_at")
    list_filter = ("company", "schedule_generated")
    search_fields = ("name", "content_hash")
    date_hierarchy = "date_from"
    readonly_fields = ("created_at", "updated_at")


@admin.register(ScheduleShift)
class ScheduleShiftAdmin(admin.ModelAdmin):
    list_display = ("date", "location", "start", "end", "demand_count", "missing_minutes",
                    "confirmed", "user_edited")
    list_filter = ("confirmed", "user_edited", "location")
    search_fields = ("shift_uid", "location")
    date_hierarchy = "date"
    readonly_fields = ("created_at", "updated_at")


@admin.register(Availability)
class AvailabilityAdmin(admin.ModelAdmin):
    list_display = ("employee_id", "employee_name", "date", "company", "experienced",
                    "hours_min", "hours_max")
    list_filter = ("company", "experienced")
    search_fields = ("employee_id", "employee_name")
    date_hierarchy = "date"


@admin.register(DefaultDemand)
class DefaultDemandAdmin(admin.ModelAdmin):
    list_display = ("location", "company", "weekday", "updated_at")
    list_filter = ("company", "weekday")
    search_fields = ("location",)


@admin.register(CompanyLocation)
class CompanyLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "created_at")
    list_filter = ("company",)
    search_fields = ("name",)


@admin.register(EventRule)
class EventRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "mode", "value", "active", "needs_experienced_default")
    list_filter = ("company", "mode", "active")
    search_fields = ("name",)


@admin.register(SpecialDay)
class SpecialDayAdmin(admin.ModelAdmin):
    list_display = ("date", "location", "company", "rule", "active")
    list_filter = ("company", "active", "rule")
    search_fields = ("location", "note")
    date_hierarchy = "date"


@admin.register(DayDemandIndex)
class DayDemandIndexAdmin(admin.ModelAdmin):
    """Indeks idempotencji dziennej - zwykle tylko do podgladu, nie do edycji."""

    list_display = ("date", "location", "demand", "day_hash")
    list_filter = ("location",)
    search_fields = ("day_hash",)
    date_hierarchy = "date"

    def has_add_permission(self, request):
        return False
