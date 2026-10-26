"""Backfill `company` dla Availability i Demand po dodaniu FK (P0-3).

Availability da sie jednoznacznie przypisac: `employee_id` to string z `User.id`,
wiec bierzemy firme uzytkownika.
Demand nie ma zadnego powiazania z firma, wiec przypisujemy go tylko wtedy, gdy
w bazie istnieje dokladnie jedna firma (przypadek tej instalacji). W przeciwnym
razie rekordy zostaja z `company = NULL` - sa wtedy niewidoczne przez API
(filtrujemy po firmie), co jest bezpieczniejsze niz zgadywanie.
"""
from django.db import migrations


def backfill_company(apps, schema_editor):
    Company = apps.get_model("accounts", "Company")
    User = apps.get_model("accounts", "User")
    Availability = apps.get_model("schedule", "Availability")
    Demand = apps.get_model("schedule", "Demand")

    by_employee = {
        str(user_id): company_id
        for user_id, company_id in User.objects.exclude(company_id=None).values_list("id", "company_id")
    }
    for row in Availability.objects.filter(company__isnull=True).iterator():
        company_id = by_employee.get(str(row.employee_id))
        if company_id:
            Availability.objects.filter(pk=row.pk).update(company_id=company_id)

    if Company.objects.count() == 1:
        only_company = Company.objects.values_list("id", flat=True).first()
        Demand.objects.filter(company__isnull=True).update(company_id=only_company)


def noop(apps, schema_editor):
    """Cofniecie nie odtwarza poprzedniego stanu - nie kasujemy danych."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("schedule", "0004_availability_company_demand_company_and_more"),
        ("accounts", "0007_attendanceevent_correction_reason_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_company, noop),
    ]
