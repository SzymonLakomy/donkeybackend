from rest_framework import serializers
from .models import User, Company
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class CompanyRegisterSerializer(serializers.Serializer):
    company_name = serializers.CharField()
    email        = serializers.EmailField()
    first_name   = serializers.CharField()
    last_name    = serializers.CharField()
    nip          = serializers.CharField(required=False, allow_blank=True)
    password     = serializers.CharField(write_only=True)

    def create(self, validated_data):
        comp = Company.objects.create(name=validated_data['company_name'])
        user = User.objects.create_user(
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            password=validated_data['password'],
            role='owner',
            company=comp,
            is_staff=True,
            is_superuser=True
        )
        return user

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

