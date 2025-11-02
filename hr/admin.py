from django.contrib import admin

from .models import Contract, Employee, LeaveRecord, PayrollRecord, Reminder, WorkHourEntry


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "email",
        "contract_type",
        "hire_date",
    )
    search_fields = ("first_name", "last_name", "email")
    list_filter = ("contract_type",)


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "contract_type",
        "start_date",
        "end_date",
        "monthly_salary",
        "hourly_rate",
    )
    list_filter = ("contract_type",)


@admin.register(LeaveRecord)
class LeaveRecordAdmin(admin.ModelAdmin):
    list_display = ("employee", "leave_type", "start_date", "end_date")
    list_filter = ("leave_type",)
    search_fields = ("employee__first_name", "employee__last_name")


@admin.register(WorkHourEntry)
class WorkHourEntryAdmin(admin.ModelAdmin):
    list_display = ("employee", "work_date", "hours")
    list_filter = ("work_date",)
    search_fields = ("employee__first_name", "employee__last_name")


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ("employee", "reminder_type", "due_date", "acknowledged", "sent_at")
    list_filter = ("reminder_type", "acknowledged")
    search_fields = ("employee__first_name", "employee__last_name")


@admin.register(PayrollRecord)
class PayrollRecordAdmin(admin.ModelAdmin):
    list_display = ("employee", "month", "hours_worked", "amount")
    date_hierarchy = "month"
    search_fields = ("employee__first_name", "employee__last_name")
