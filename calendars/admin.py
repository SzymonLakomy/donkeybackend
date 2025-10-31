from django.contrib import admin

from .models import CalendarEvent, ExternalCalendarConnection, MedicalCheckEvent


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ("title", "employee_id", "category", "start_at", "end_at")
    list_filter = ("category", "start_at")
    search_fields = ("title", "employee_id")
    ordering = ("-start_at",)


@admin.register(MedicalCheckEvent)
class MedicalCheckEventAdmin(admin.ModelAdmin):
    list_display = ("title", "employee_id", "status", "start_at", "end_at")
    list_filter = ("status", "start_at")
    search_fields = ("title", "employee_id", "exam_type")
    ordering = ("-start_at",)


@admin.register(ExternalCalendarConnection)
class ExternalCalendarConnectionAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "employee_id", "active", "last_synced_at")
    list_filter = ("provider", "active")
    search_fields = ("name", "employee_id", "external_id")
    ordering = ("-updated_at",)
