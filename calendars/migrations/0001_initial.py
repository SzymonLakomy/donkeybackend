from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="CalendarEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("employee_id", models.CharField(db_index=True, max_length=128)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("start_at", models.DateTimeField()),
                ("end_at", models.DateTimeField()),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("schedule", "Schedule"),
                            ("leave", "Leave"),
                            ("training", "Training"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                ("location", models.CharField(blank=True, default="", max_length=255)),
                ("color", models.CharField(blank=True, default="", max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["start_at", "employee_id"],
            },
        ),
        migrations.CreateModel(
            name="MedicalCheckEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("employee_id", models.CharField(db_index=True, max_length=128)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("exam_type", models.CharField(blank=True, default="", max_length=128)),
                ("start_at", models.DateTimeField()),
                ("end_at", models.DateTimeField()),
                ("location", models.CharField(blank=True, default="", max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("planned", "Planned"),
                            ("confirmed", "Confirmed"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="planned",
                        max_length=20,
                    ),
                ),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["start_at", "employee_id"],
            },
        ),
        migrations.CreateModel(
            name="ExternalCalendarConnection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                (
                    "provider",
                    models.CharField(
                        choices=[
                            ("ics", "ICS"),
                            ("google", "Google"),
                            ("outlook", "Outlook"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        default="other",
                        max_length=20,
                    ),
                ),
                (
                    "employee_id",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Optional owner of the connection",
                        max_length=128,
                    ),
                ),
                ("external_id", models.CharField(blank=True, default="", max_length=255)),
                ("sync_token", models.CharField(blank=True, default="", max_length=255)),
                ("settings", models.JSONField(blank=True, default=dict)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-updated_at", "name"],
            },
        ),
        migrations.AddIndex(
            model_name="calendarevent",
            index=models.Index(fields=["start_at", "end_at"], name="calendar_event_time"),
        ),
        migrations.AddIndex(
            model_name="calendarevent",
            index=models.Index(fields=["category", "start_at"], name="calendar_event_category"),
        ),
        migrations.AddIndex(
            model_name="medicalcheckevent",
            index=models.Index(fields=["status", "start_at"], name="medical_status_start"),
        ),
        migrations.AddIndex(
            model_name="medicalcheckevent",
            index=models.Index(fields=["employee_id", "start_at"], name="medical_employee_start"),
        ),
        migrations.AddIndex(
            model_name="externalcalendarconnection",
            index=models.Index(fields=["provider", "active"], name="calendar_provider_active"),
        ),
    ]
