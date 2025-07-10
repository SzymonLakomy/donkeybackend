from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from .models import User, Company
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

