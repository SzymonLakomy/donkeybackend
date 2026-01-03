# Create your models here.
import uuid

from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin


def gen_company_code():
    return uuid.uuid4().hex[:8]


class Company(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=8, unique=True, default=gen_company_code)
    nip = models.CharField(max_length=20, blank=True, null=True)

    # Konfiguracja lokalizacji (Workplace Config)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    radius = models.IntegerField(default=150, help_text="Promień w metrach")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.code})"


class Position(models.Model):
    name = models.CharField(max_length=100)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="positions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("name", "company")

    def __str__(self):
        return f"{self.name} ({self.company.code})"


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra_fields)

class User(AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = [('owner', 'Owner'), ('manager', 'Manager'), ('employee', 'Employee')]

    position = models.ForeignKey(Position, on_delete=models.SET_NULL, null=True, blank=True, related_name="users")
    experience_years = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True, null=True)

    email = models.EmailField(unique=True)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name", "role"]

    objects = UserManager()

    def __str__(self):
        return self.email
        
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class AttendanceEvent(models.Model):
    EVENT_TYPE_CHOICES = [
        ('check_in', 'Check In'),
        ('check_out', 'Check Out'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='attendance_events')
    type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES)
    timestamp = models.DateTimeField()
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    is_valid = models.BooleanField(default=False, help_text="Czy zdarzenie jest w zasięgu miejsca pracy")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} - {self.type} at {self.timestamp}"
