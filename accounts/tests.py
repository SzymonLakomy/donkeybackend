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

        # Tworzymy zdarzenia obecności dla pracownika
        self.event1 = AttendanceEvent.objects.create(
            user=self.employee,
            type="check_in",
            timestamp=timezone.now(),
            latitude=52.229676,
            longitude=21.012229,
            is_valid=True,
            is_correction=False,
            status='approved'
        )

        self.event2 = AttendanceEvent.objects.create(
            user=self.employee,
            type="check_out",
            timestamp=timezone.now(),
            latitude=52.229676,
            longitude=21.012229,
            is_valid=True,
            is_correction=False,
            status='approved'
        )

        # Tworzymy zdarzenie dla menedżera
        self.manager_event = AttendanceEvent.objects.create(
            user=self.manager,
            type="check_in",
            timestamp=timezone.now(),
            latitude=52.229676,
            longitude=21.012229,
            is_valid=True,
            is_correction=False,
            status='approved'
        )

    def test_employee_can_see_own_history(self):
        """Pracownik może zobaczyć swoją historię obecności"""
        self.client.force_authenticate(user=self.employee)
        url = reverse('attendance-history')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Pracownik widzi tylko swoje 2 zdarzenia
        self.assertEqual(len(response.data), 2)

        # Sprawdź czy odpowiedź zawiera wymagane pola
        for event in response.data:
            self.assertIn('id', event)
            self.assertIn('timestamp', event)
            self.assertIn('type', event)
            self.assertIn('latitude', event)
            self.assertIn('longitude', event)
            self.assertIn('is_correction', event)
            self.assertIn('correction_reason', event)
            self.assertIn('status', event)

    def test_manager_sees_only_own_history(self):
        """Menedżer widzi tylko swoją historię (zgodnie ze specyfikacją - endpoint zwraca zdarzenia zalogowanego użytkownika)"""
        self.client.force_authenticate(user=self.manager)
        url = reverse('attendance-history')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Menedżer widzi tylko swoje zdarzenie
        self.assertEqual(len(response.data), 1)

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

    def test_employee_can_add_correction(self):
        """Pracownik może dodać korektę obecności (zgodnie ze specyfikacją)"""
        self.client.force_authenticate(user=self.employee)
        url = reverse('attendance-correction')

        data = {
            "timestamp": timezone.now().isoformat(),
            "type": "check_in",
            "reason": "forgot",
            "latitude": 52.229676,
            "longitude": 21.012229
        }

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('id', response.data)
        self.assertEqual(response.data['status'], 'pending_approval')

        # Sprawdź czy zdarzenie zostało utworzone
        self.assertEqual(AttendanceEvent.objects.count(), 1)

        event = AttendanceEvent.objects.first()
        self.assertEqual(event.user, self.employee)
        self.assertEqual(event.type, "check_in")
        self.assertTrue(event.is_correction)
        self.assertEqual(event.correction_reason, "forgot")
        self.assertEqual(event.status, "pending_approval")

    def test_manager_can_add_correction(self):
        """Menedżer może dodać korektę obecności"""
        self.client.force_authenticate(user=self.manager)
        url = reverse('attendance-correction')

        data = {
            "timestamp": timezone.now().isoformat(),
            "type": "check_in",
            "reason": "gps_error"
        }

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'pending_approval')

    def test_unauthenticated_cannot_add_correction(self):
        """Niezalogowany użytkownik nie może dodać korekty"""
        url = reverse('attendance-correction')

        data = {
            "timestamp": timezone.now().isoformat(),
            "type": "check_in",
            "reason": "forgot"
        }

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_correction_without_coordinates(self):
        """Korekta może być dodana bez współrzędnych (np. remote work)"""
        self.client.force_authenticate(user=self.employee)
        url = reverse('attendance-correction')

        data = {
            "timestamp": timezone.now().isoformat(),
            "type": "check_in",
            "reason": "remote_work"
        }

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        event = AttendanceEvent.objects.first()
        self.assertIsNone(event.latitude)
        self.assertIsNone(event.longitude)

