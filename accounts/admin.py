from django.contrib import admin

from .models import AttendanceEvent, Company, Position, User


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "nip", "radius", "latitude", "longitude", "created_at")
    search_fields = ("name", "code", "nip")
    readonly_fields = ("created_at",)


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "created_at")
    list_filter = ("company",)
    search_fields = ("name",)


@admin.register(User)
class DonkeyUserAdmin(admin.ModelAdmin):
    """Uwaga: to nie jest django.contrib.auth.admin.UserAdmin - model ma email jako USERNAME_FIELD."""

    list_display = ("email", "first_name", "last_name", "role", "company", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff", "company")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)


@admin.register(AttendanceEvent)
class AttendanceEventAdmin(admin.ModelAdmin):
    list_display = ("user", "type", "timestamp", "status", "is_valid", "is_correction", "correction_reason")
    list_filter = ("type", "status", "is_valid", "is_correction")
    search_fields = ("user__email",)
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
