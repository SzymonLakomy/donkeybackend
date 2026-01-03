# Create your views here.
from rest_framework import generics, status, viewsets, filters
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.views import TokenObtainPairView
from django.db.models import Q
from drf_spectacular.utils import extend_schema
import math

from .models import gen_company_code, User, Position, AttendanceEvent
from .permissions import IsManager, IsManagerForOwnCompany, CannotPromoteToOwner

from .serializers import (
    CompanyCreateSerializer, CompanySerializer,
    UserRegisterSerializer,
    CustomTokenObtainPairSerializer,
    PositionSerializer,
    UserListSerializer,
    UserDetailSerializer,
    CompanyCodeSerializer,
    WorkplaceConfigSerializer,
    AttendanceEventSerializer,
    AttendanceStatusSerializer,
    AttendanceHistorySerializer,
    AttendanceCorrectionSerializer,
)

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c

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
    serializer_class = CompanyCodeSerializer

    @extend_schema(responses=CompanyCodeSerializer)
    def get(self, request):
        company = request.user.company
        return Response({"company_code": company.code})


class CompanyCodeResetView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CompanyCodeSerializer

    @extend_schema(request=None, responses=CompanyCodeSerializer)
    def post(self, request):
        company = request.user.company
        # Wygeneruj nowy, unikalny kod
        new_code = gen_company_code()
        company.code = new_code
        company.save(update_fields=["code"])
        return Response({"company_code": new_code})


class WorkplaceConfigView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = WorkplaceConfigSerializer

    @extend_schema(responses=WorkplaceConfigSerializer)
    def get(self, request):
        company = request.user.company
        if not company:
             return Response({"detail": "User has no company"}, status=status.HTTP_400_BAD_REQUEST)

        serializer = WorkplaceConfigSerializer(company)
        return Response(serializer.data)

    @extend_schema(request=WorkplaceConfigSerializer, responses=WorkplaceConfigSerializer)
    def post(self, request):
        company = request.user.company
        if not company:
             return Response({"detail": "User has no company"}, status=status.HTTP_400_BAD_REQUEST)

        # Allow partial updates (e.g. just radius)
        serializer = WorkplaceConfigSerializer(company, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class AttendanceEventView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AttendanceEventSerializer

    @extend_schema(request=AttendanceEventSerializer, responses=AttendanceEventSerializer)
    def post(self, request):
        serializer = AttendanceEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        company = user.company

        if not company:
             return Response({"detail": "User has no company"}, status=status.HTTP_400_BAD_REQUEST)

        lat = serializer.validated_data['latitude']
        lon = serializer.validated_data['longitude']

        # Validate distance
        if company.latitude is None or company.longitude is None:
             # If company has no location set, we mark as invalid but maybe still save?
             # Or reject? The requirement says "Backend powinien ponownie zweryfikować odległość"
             # If config is missing, we can't verify. Let's reject.
             return Response({"detail": "Company location not configured."}, status=status.HTTP_400_BAD_REQUEST)

        distance = haversine_distance(
            float(lat), float(lon),
            float(company.latitude), float(company.longitude)
        )
        is_valid = distance <= company.radius

        event = AttendanceEvent.objects.create(
            user=user,
            type=serializer.validated_data['type'],
            timestamp=serializer.validated_data['timestamp'],
            latitude=lat,
            longitude=lon,
            is_valid=is_valid
        )

        if not is_valid:
             return Response(
                 {
                     "detail": "Location is outside of workplace radius.",
                     "distance": distance,
                     "radius": company.radius
                 },
                 status=status.HTTP_400_BAD_REQUEST
             )

        return Response(AttendanceEventSerializer(event).data, status=status.HTTP_201_CREATED)


class AttendanceStatusView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AttendanceStatusSerializer

    @extend_schema(responses=AttendanceStatusSerializer)
    def get(self, request):
        user = request.user
        last_event = AttendanceEvent.objects.filter(user=user).order_by('-timestamp').first()

        is_working = False
        last_event_time = None
        last_event_type = None

        if last_event:
            is_working = (last_event.type == 'check_in')
            last_event_time = last_event.timestamp
            last_event_type = last_event.type

        data = {
            "is_working": is_working,
            "last_event_time": last_event_time,
            "last_event_type": last_event_type
        }
        return Response(data)


# Widoki dla menedżera do zarządzania pracownikami

class PositionViewSet(viewsets.ModelViewSet):
    """
    API endpoint dla zarządzania stanowiskami w firmie.
    Tylko menedżerowie i właściciele mają dostęp.
    """
    queryset = Position.objects.all()
    serializer_class = PositionSerializer
    permission_classes = [IsAuthenticated, IsManager]
    filter_backends = [filters.SearchFilter]
    search_fields = ['name']

    def get_queryset(self):
        # Zwraca tylko stanowiska z firmy zalogowanego użytkownika
        if not self.request.user.company:
            return Position.objects.none()
        return Position.objects.filter(company=self.request.user.company)

    def perform_create(self, serializer):
        # Automatycznie przypisuje company z zalogowanego użytkownika
        company = self.request.user.company
        if not company:
            raise ValidationError({"detail": "User does not belong to any company."})
        serializer.save(company=company)

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


class AttendanceHistoryView(generics.ListAPIView):
    """
    API endpoint do wyświetlania historii zdarzeń obecności.
    Menedżerowie widzą wszystkich pracowników firmy, pracownicy tylko swoje zdarzenia.
    """
    serializer_class = AttendanceHistorySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['user__first_name', 'user__last_name', 'user__email', 'type']
    ordering_fields = ['timestamp', 'created_at', 'type']
    ordering = ['-timestamp']  # Domyślne sortowanie po timestamp malejąco

    def get_queryset(self):
        user = self.request.user

        # Menedżerowie i właściciele widzą wszystkich pracowników swojej firmy
        if user.role in ['manager', 'owner']:
            queryset = AttendanceEvent.objects.filter(user__company=user.company)
        else:
            # Zwykli pracownicy widzą tylko swoje zdarzenia
            queryset = AttendanceEvent.objects.filter(user=user)

        # Filtrowanie po user_id jeśli podano w query params
        user_id = self.request.query_params.get('user_id', None)
        if user_id and user.role in ['manager', 'owner']:
            queryset = queryset.filter(user__id=user_id)

        # Filtrowanie po dacie jeśli podano w query params
        date_from = self.request.query_params.get('date_from', None)
        date_to = self.request.query_params.get('date_to', None)

        if date_from:
            queryset = queryset.filter(timestamp__gte=date_from)
        if date_to:
            queryset = queryset.filter(timestamp__lte=date_to)

        return queryset


class AttendanceCorrectionView(APIView):
    """
    API endpoint do ręcznego dodawania korekt obecności.
    Tylko menedżerowie i właściciele mają dostęp.
    """
    permission_classes = [IsAuthenticated, IsManager]
    serializer_class = AttendanceCorrectionSerializer

    @extend_schema(request=AttendanceCorrectionSerializer, responses=AttendanceEventSerializer)
    def post(self, request):
        serializer = AttendanceCorrectionSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        user_id = serializer.validated_data['user_id']
        event_type = serializer.validated_data['type']
        timestamp = serializer.validated_data['timestamp']

        # Pobierz użytkownika
        try:
            user = User.objects.get(id=user_id, company=request.user.company)
        except User.DoesNotExist:
            return Response(
                {"detail": "Użytkownik nie istnieje lub nie należy do Twojej firmy."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Utwórz zdarzenie korekcyjne
        # Dla korekt manualnych ustawiamy latitude i longitude na 0 lub null,
        # i is_valid na True (bo to korekta manualna zatwierdzona przez menedżera)
        event = AttendanceEvent.objects.create(
            user=user,
            type=event_type,
            timestamp=timestamp,
            latitude=0,
            longitude=0,
            is_valid=True  # Korekty manualne są domyślnie ważne
        )

        return Response(
            AttendanceEventSerializer(event).data,
            status=status.HTTP_201_CREATED
        )

