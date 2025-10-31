from django.db import models


class CalendarEvent(models.Model):
    CATEGORY_SCHEDULE = "schedule"
    CATEGORY_LEAVE = "leave"
    CATEGORY_TRAINING = "training"

    CATEGORY_CHOICES = [
        (CATEGORY_SCHEDULE, "Schedule"),
        (CATEGORY_LEAVE, "Leave"),
        (CATEGORY_TRAINING, "Training"),
    ]

    employee_id = models.CharField(max_length=128, db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, db_index=True)
    location = models.CharField(max_length=255, blank=True, default="")
    color = models.CharField(max_length=32, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_at", "employee_id"]
        indexes = [
            models.Index(fields=["start_at", "end_at"], name="calendar_event_time"),
            models.Index(fields=["category", "start_at"], name="calendar_event_category"),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.title} ({self.category})"


class MedicalCheckEvent(models.Model):
    STATUS_PLANNED = "planned"
    STATUS_CONFIRMED = "confirmed"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PLANNED, "Planned"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    employee_id = models.CharField(max_length=128, db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    exam_type = models.CharField(max_length=128, blank=True, default="")
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    location = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PLANNED, db_index=True)
    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_at", "employee_id"]
        indexes = [
            models.Index(fields=["status", "start_at"], name="medical_status_start"),
            models.Index(fields=["employee_id", "start_at"], name="medical_employee_start"),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.title} ({self.status})"


class ExternalCalendarConnection(models.Model):
    PROVIDER_ICS = "ics"
    PROVIDER_GOOGLE = "google"
    PROVIDER_OUTLOOK = "outlook"
    PROVIDER_OTHER = "other"

    PROVIDER_CHOICES = [
        (PROVIDER_ICS, "ICS"),
        (PROVIDER_GOOGLE, "Google"),
        (PROVIDER_OUTLOOK, "Outlook"),
        (PROVIDER_OTHER, "Other"),
    ]

    name = models.CharField(max_length=255)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default=PROVIDER_OTHER, db_index=True)
    employee_id = models.CharField(max_length=128, blank=True, default="", help_text="Optional owner of the connection")
    external_id = models.CharField(max_length=255, blank=True, default="")
    sync_token = models.CharField(max_length=255, blank=True, default="")
    settings = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "name"]
        indexes = [
            models.Index(fields=["provider", "active"], name="calendar_provider_active"),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.name} ({self.provider})"
