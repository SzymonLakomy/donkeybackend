from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("schedule", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],  # nic nie zmieniamy w DB
            state_operations=[
                migrations.CreateModel(
                    name="Availability",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        # tu wstaw pola identyczne jak w modelu
                    ],
                    options={
                        "db_table": "schedule_availability",  # nazwa istniejącej tabeli
                    },
                ),
            ],
        ),
    ]
