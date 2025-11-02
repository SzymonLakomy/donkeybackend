from rest_framework import serializers

from .models import Contract, ContractType, Employee, LeaveRecord, PayrollRecord, Reminder, WorkHourEntry


class ContractSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        contract_type = attrs.get("contract_type") or getattr(self.instance, "contract_type", None)
        monthly_salary = attrs.get("monthly_salary") if "monthly_salary" in attrs else getattr(self.instance, "monthly_salary", None)
        hourly_rate = attrs.get("hourly_rate") if "hourly_rate" in attrs else getattr(self.instance, "hourly_rate", None)

        if contract_type == ContractType.UOP and monthly_salary is None:
            raise serializers.ValidationError("Dla umowy o pracę podaj miesięczne wynagrodzenie.")
        if contract_type == ContractType.ZLECENIE and hourly_rate is None:
            raise serializers.ValidationError("Dla umowy zlecenie podaj stawkę godzinową.")
        return attrs

    class Meta:
        model = Contract
        fields = "__all__"


class EmployeeSerializer(serializers.ModelSerializer):
    contract = ContractSerializer(read_only=True)

    class Meta:
        model = Employee
        fields = (
            "id",
            "first_name",
            "last_name",
            "email",
            "hire_date",
            "contract_type",
            "position",
            "medical_check_valid_until",
            "bhp_training_valid_until",
            "id_card_valid_until",
            "contractor_document_valid_until",
            "contract",
        )


class LeaveRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveRecord
        fields = "__all__"


class WorkHourEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkHourEntry
        fields = "__all__"


class ReminderSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)

    class Meta:
        model = Reminder
        fields = (
            "id",
            "employee",
            "employee_name",
            "reminder_type",
            "message",
            "due_date",
            "created_at",
            "sent_at",
            "acknowledged",
        )


class PayrollRecordSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)

    class Meta:
        model = PayrollRecord
        fields = (
            "id",
            "employee",
            "employee_name",
            "month",
            "hours_worked",
            "amount",
            "created_at",
        )
