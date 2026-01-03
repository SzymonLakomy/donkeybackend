from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from .models import User, Company, Position, AttendanceEvent
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class CompanyCreateSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='name')
    nip          = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(
        validators=[
            UniqueValidator(
                queryset=User.objects.all(),
                message="Użytkownik z takim e-mailem już istnieje."
            )
        ]
    )

    first_name   = serializers.CharField(write_only=True)
    last_name    = serializers.CharField(write_only=True)
    password     = serializers.CharField(write_only=True)

    class Meta:
        model = Company
        fields = [
            'company_name',
            'nip',
            'email',       # dane właściciela
            'first_name',
            'last_name',
            'password'
        ]

    def create(self, validated_data):
        # wyciągamy dane użytkownika
        user_data = {
            'email':      validated_data.pop('email'),
            'first_name': validated_data.pop('first_name'),
            'last_name':  validated_data.pop('last_name'),
            'password':   validated_data.pop('password'),
            'role':       'owner',
            'is_staff':   True,
            'is_superuser': True,
        }
        # tworzymy Company (ModelSerializer zadba o name←company_name i nip)
        company = super().create(validated_data)

        # tworzymy właściciela
        User.objects.create_user(company=company, **user_data)

        return company


# 2. Tylko do OUTPUT: prosty serializer modelowy, zwracający kod etc.
class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ['id', 'name', 'code', 'nip', 'created_at']
        read_only_fields = fields


class CompanyCodeSerializer(serializers.Serializer):
    company_code = serializers.CharField()


class UserRegisterSerializer(serializers.ModelSerializer):
    company_code = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "password", "company_code"]
        extra_kwargs = {"password": {"write_only": True}}

    def validate_company_code(self, code):
        try:
            return Company.objects.get(code=code)
        except Company.DoesNotExist:
            raise serializers.ValidationError("Nieprawidłowy kod firmy.")

    def create(self, validated_data):
        company = validated_data.pop("company_code")
        return User.objects.create_user(
            company=company,
            role='employee',
            **validated_data
        )

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["email"]      = user.email
        token["role"]       = user.role
        token["company_id"] = user.company_id
        return token

    def validate(self, attrs):
        # Use the default validation to get tokens first
        data = super().validate(attrs)
        user = self.user
        data["user"] = {
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "role": user.role,
            "company_id": user.company_id,
        }
        return data



class WorkplaceConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ['latitude', 'longitude', 'radius']


class AttendanceEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceEvent
        fields = ['type', 'latitude', 'longitude', 'timestamp']

    def validate(self, data):
        # Walidacja odległości będzie w widoku lub tutaj
        # Ale tutaj nie mamy dostępu do request.user.company łatwo, chyba że przez context
        return data


class AttendanceStatusSerializer(serializers.Serializer):
    is_working = serializers.BooleanField()
    last_event_time = serializers.DateTimeField(allow_null=True)
    last_event_type = serializers.CharField(allow_null=True)


class AttendanceHistorySerializer(serializers.ModelSerializer):
    """Serializer dla historii zdarzeń obecności"""
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceEvent
        fields = ['id', 'user', 'user_name', 'type', 'timestamp', 'latitude',
                  'longitude', 'is_valid', 'created_at']
        read_only_fields = fields

    def get_user_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}"


class AttendanceCorrectionSerializer(serializers.Serializer):
    """Serializer dla ręcznych korekt obecności"""
    user_id = serializers.IntegerField(required=True)
    type = serializers.ChoiceField(choices=AttendanceEvent.EVENT_TYPE_CHOICES, required=True)
    timestamp = serializers.DateTimeField(required=True)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate_user_id(self, value):
        """Sprawdza czy użytkownik istnieje i należy do tej samej firmy"""
        request = self.context.get('request')
        if not request or not request.user.company:
            raise serializers.ValidationError("Brak przypisanej firmy.")

        try:
            user = User.objects.get(id=value, company=request.user.company)
        except User.DoesNotExist:
            raise serializers.ValidationError("Użytkownik nie istnieje lub nie należy do Twojej firmy.")

        return value


# Nowe serializery dla menedżera

class PositionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Position
        fields = ['id', 'name', 'created_at']
        read_only_fields = ['created_at']

    def validate(self, data):
        request = self.context.get('request')
        name = data.get('name')

        if name and request and request.user and not request.user.is_anonymous and request.user.company:
            # Check if a position with this name already exists in the company
            # Exclude the current instance if updating
            queryset = Position.objects.filter(company=request.user.company, name=name)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)

            if queryset.exists():
                raise serializers.ValidationError({"name": "Stanowisko o tej nazwie już istnieje w Twojej firmie."})
        return data

class UserListSerializer(serializers.ModelSerializer):
    position_name = serializers.CharField(source='position.name', read_only=True)
    
    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 
            'role', 'is_active', 'is_staff', 'created_at',
            'position', 'position_name', 'experience_years', 'notes'
        ]
        read_only_fields = ['id', 'created_at', 'position_name']

class UserDetailSerializer(serializers.ModelSerializer):
    position_id = serializers.PrimaryKeyRelatedField(
        source='position',
        queryset=Position.objects.all(),
        required=False,
        allow_null=True
    )
    
    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 
            'role', 'is_active', 'is_staff', 'created_at',
            'position_id', 'experience_years', 'notes'
        ]
        read_only_fields = ['id', 'created_at']
    
    def validate_position_id(self, position):
        # Sprawdza czy stanowisko należy do tej samej firmy
        if position and position.company != self.context['request'].user.company:
            raise serializers.ValidationError("To stanowisko nie należy do Twojej firmy.")
        return position
    
    def validate_role(self, role):
        # Menedżer nie może nadać roli właściciela
        if role == 'owner':
            raise serializers.ValidationError("Nie można przypisać roli właściciela.")
        # Menedżer może tylko awansować pracownika na menedżera
        if self.instance and self.instance.role == 'employee' and role == 'manager':
            return role
        # Pozostawienie obecnej roli
        if self.instance and self.instance.role == role:
            return role
        # Inne przypadki nie są dozwolone
        raise serializers.ValidationError("Niedozwolona zmiana roli.")
