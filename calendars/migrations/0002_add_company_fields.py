from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("calendars", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="calendarevent",
            name="company",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="calendar_events",
                to="accounts.company",
            ),
        ),
        migrations.AddField(
            model_name="medicalcheckevent",
            name="company",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="medical_events",
                to="accounts.company",
            ),
        ),
        migrations.AddField(
            model_name="externalcalendarconnection",
            name="company",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="external_calendars",
                to="accounts.company",
            ),
        ),
    ]
