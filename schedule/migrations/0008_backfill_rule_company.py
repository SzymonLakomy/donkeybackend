"""Backfill `company` dla EventRule i SpecialDay (P1: reguly per firma).

Reguly nie maja zadnego powiazania z firma, wiec przypisujemy je tylko wtedy, gdy
w bazie istnieje dokladnie jedna firma. W przeciwnym razie zostaja z `company = NULL`
i sa niewidoczne przez API (filtruje po firmie) - bezpieczniej niz zgadywanie.
"""
from django.db import migrations


def backfill_company(apps, schema_editor):
    Company = apps.get_model("accounts", "Company")
    EventRule = apps.get_model("schedule", "EventRule")
    SpecialDay = apps.get_model("schedule", "SpecialDay")

    if Company.objects.count() != 1:
        return

    only_company = Company.objects.values_list("id", flat=True).first()
    EventRule.objects.filter(company__isnull=True).update(company_id=only_company)
    SpecialDay.objects.filter(company__isnull=True).update(company_id=only_company)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("schedule", "0007_eventrule_company_specialday_company"),
        ("accounts", "0007_attendanceevent_correction_reason_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_company, noop),
    ]
