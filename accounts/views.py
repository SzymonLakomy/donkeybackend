from django.shortcuts import render

# Create your views here.
from rest_framework import generics, status, viewsets, filters
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from django.db.models import Q

from .models import gen_company_code, User, Position
from .permissions import IsManager, IsManagerForOwnCompany, CannotPromoteToOwner

from .serializers import (
    CompanyCreateSerializer, CompanySerializer,
    UserRegisterSerializer,
    CustomTokenObtainPairSerializer,
    PositionSerializer,
    UserListSerializer,
    UserDetailSerializer,
)

class RegisterCompanyView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class   = CompanyCreateSerializer

    def create(self, request, *args, **kwargs):
        # 1. walidacja i utworzenie firmy + ownera
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        company = serializer.save()
        print(company)

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

# Widoki dla menedżera do zarządzania pracownikami

class PositionViewSet(viewsets.ModelViewSet):
    """
    API endpoint dla zarządzania stanowiskami w firmie.
    Tylko menedżerowie i właściciele mają dostęp.
    """
    serializer_class = PositionSerializer
    permission_classes = [IsAuthenticated, IsManager]
    filter_backends = [filters.SearchFilter]
    search_fields = ['name']

    def get_queryset(self):
        # Zwraca tylko stanowiska z firmy zalogowanego użytkownika
        return Position.objects.filter(company=self.request.user.company)

class CompanyUserListView(generics.ListAPIView):
    """
    API endpoint do wyświetlania wszystkich użytkowników z firmy menedżera.
    """
    serializer_class = UserListSerializer
    permission_classes = [IsAuthenticated, IsManager]
    filter_backends = [filters.SearchFilter]
    search_fields = ['email', 'first_name', 'last_name']
    #no

    def get_queryset(self):
        # Zwraca tylko użytkowników z firmy zalogowanego użytkownika
        # Właściciel widzi wszystkich, menedżer nie widzi właścicieli
        if self.request.user.role == 'owner':
            return User.objects.filter(company=self.request.user.company)
        else:
            # Używamy Q() do utworzenia złożonego zapytania
            return User.objects.filter(
                Q(company=self.request.user.company) & ~Q(role='owner')  # Menedżer nie widzi właścicieli
            )

class CompanyUserDetailView(generics.RetrieveUpdateAPIView):
    """
    API endpoint do wyświetlania i aktualizacji szczegółów użytkownika.
    """
    serializer_class = UserDetailSerializer
    permission_classes = [IsAuthenticated, IsManager, IsManagerForOwnCompany, CannotPromoteToOwner]
    
    def get_queryset(self):
        # Menedżer widzi tylko pracowników swojej firmy
        if self.request.user.role == 'owner':
            return User.objects.filter(company=self.request.user.company)
        else:
            # Używamy Q() do utworzenia złożonego zapytania
            return User.objects.filter(
                Q(company=self.request.user.company) & ~Q(role='owner')  # Menedżer nie widzi właścicieli
            )
