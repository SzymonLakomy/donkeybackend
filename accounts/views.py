from django.shortcuts import render

# Create your views here.
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import gen_company_code

from .serializers import (
    CompanyCreateSerializer, CompanySerializer,
    UserRegisterSerializer,
    CustomTokenObtainPairSerializer,
)

class RegisterCompanyView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class   = CompanyCreateSerializer

    def create(self, request, *args, **kwargs):
        # 1. walidacja i utworzenie firmy + ownera
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        company = serializer.save()

        # 2. przygotowanie danych wyjściowych
        out_ser = CompanySerializer(company, context=self.get_serializer_context())
        return Response(out_ser.data, status=status.HTTP_201_CREATED)



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
