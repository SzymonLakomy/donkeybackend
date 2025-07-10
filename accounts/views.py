from django.shortcuts import render

# Create your views here.
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import gen_company_code

from .serializers import (
    CompanyRegisterSerializer,
    UserRegisterSerializer,
    CustomTokenObtainPairSerializer,
)

class RegisterCompanyView(generics.CreateAPIView):
    serializer_class   = CompanyRegisterSerializer
    permission_classes = [AllowAny]

class RegisterUserView(generics.CreateAPIView):
    serializer_class   = UserRegisterSerializer
    permission_classes = [AllowAny]

class LoginView(TokenObtainPairView):
    serializer_class   = CustomTokenObtainPairSerializer
    permission_classes = [AllowAny]


class CompanyCodeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company = request.user.company
        return Response({"company_code": company.code})


class CompanyCodeResetView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        company = request.user.company
        # Wygeneruj nowy, unikalny kod
        new_code = gen_company_code()
        company.code = new_code
        company.save(update_fields=["code"])
        return Response({"company_code": new_code})
