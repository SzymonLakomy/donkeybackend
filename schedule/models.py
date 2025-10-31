from django.db import models

# Create your models here.

from django.db import models

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
