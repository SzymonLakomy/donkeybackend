from django.shortcuts import render

# Create your views here.
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView
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
