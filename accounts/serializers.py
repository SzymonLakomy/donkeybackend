from rest_framework import serializers
from .models import User, Company
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class RegisterSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "password", "role", "company_name"]
        extra_kwargs = {"password": {"write_only": True}}

    def create(self, validated_data):
        company_name = validated_data.pop("company_name")
        company = Company.objects.create(name=company_name)
        user = User.objects.create_user(
            company=company,
            **validated_data
        )
        return user

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["email"] = user.email
        token["role"] = user.role
        token["company_id"] = user.company_id
        return token
