from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.utils import timezone
from .models import User, Company, AttendanceEvent


class AttendanceHistoryTestCase(APITestCase):
    def setUp(self):
        # Tworzymy firmę
        self.company = Company.objects.create(
            name="Test Company",
            code="TEST123",
            latitude=52.229676,
            longitude=21.012229,
            radius=100.0
        )

        # Tworzymy menedżera
        self.manager = User.objects.create_user(
            email="manager@test.com",
            password="testpass123",
            first_name="Manager",
            last_name="Test",
            company=self.company,
            role="manager"
        )

        # Tworzymy pracownika
        self.employee = User.objects.create_user(
            email="employee@test.com",
            password="testpass123",
            first_name="Employee",
            last_name="Test",
            company=self.company,
            role="employee"
        )

        # Tworzymy zdarzenia obecności
        self.event1 = AttendanceEvent.objects.create(
            user=self.employee,
            type="check_in",
            timestamp=timezone.now(),
            latitude=52.229676,
            longitude=21.012229,
            is_valid=True
        )

        self.event2 = AttendanceEvent.objects.create(
            user=self.employee,
            type="check_out",
            timestamp=timezone.now(),
            latitude=52.229676,
            longitude=21.012229,
            is_valid=True
        )

    def test_employee_can_see_own_history(self):
        """Pracownik może zobaczyć swoją historię obecności"""
        self.client.force_authenticate(user=self.employee)
        url = reverse('attendance-history')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_manager_can_see_all_history(self):
        """Menedżer może zobaczyć historię wszystkich pracowników"""
        self.client.force_authenticate(user=self.manager)
        url = reverse('attendance-history')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 2)

    def test_unauthenticated_cannot_access_history(self):
        """Niezalogowany użytkownik nie ma dostępu"""
        url = reverse('attendance-history')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AttendanceCorrectionTestCase(APITestCase):
    def setUp(self):
        # Tworzymy firmę
        self.company = Company.objects.create(
            name="Test Company",
            code="TEST123",
            latitude=52.229676,
            longitude=21.012229,
            radius=100.0
        )

        # Tworzymy menedżera
        self.manager = User.objects.create_user(
            email="manager@test.com",
            password="testpass123",
            first_name="Manager",
            last_name="Test",
            company=self.company,
            role="manager"
        )

        # Tworzymy pracownika
        self.employee = User.objects.create_user(
            email="employee@test.com",
            password="testpass123",
            first_name="Employee",
            last_name="Test",
            company=self.company,
            role="employee"
        )

    def test_manager_can_add_correction(self):
        """Menedżer może dodać korektę obecności"""
        self.client.force_authenticate(user=self.manager)
        url = reverse('attendance-correction')

        data = {
            "user_id": self.employee.id,
            "type": "check_in",
            "timestamp": timezone.now().isoformat(),
            "notes": "Korekta testowa"
        }

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(AttendanceEvent.objects.count(), 1)

        event = AttendanceEvent.objects.first()
        self.assertEqual(event.user, self.employee)
        self.assertEqual(event.type, "check_in")
        self.assertTrue(event.is_valid)

    def test_employee_cannot_add_correction(self):
        """Pracownik nie może dodać korekty"""
        self.client.force_authenticate(user=self.employee)
        url = reverse('attendance-correction')

        data = {
            "user_id": self.employee.id,
            "type": "check_in",
            "timestamp": timezone.now().isoformat(),
        }

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_cannot_add_correction(self):
        """Niezalogowany użytkownik nie może dodać korekty"""
        url = reverse('attendance-correction')

        data = {
            "user_id": self.employee.id,
            "type": "check_in",
            "timestamp": timezone.now().isoformat(),
        }

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

