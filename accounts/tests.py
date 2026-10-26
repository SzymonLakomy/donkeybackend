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


class WorkplaceConfigPermissionTestCase(APITestCase):
    """BACKLOG P0-2: geofencje zmienia tylko manager/owner, nie kazdy pracownik."""

    def setUp(self):
        self.company = Company.objects.create(
            name="Geo Company",
            code="GEO12345",
            latitude=52.229676,
            longitude=21.012229,
            radius=100,
        )
        self.owner = User.objects.create_user(
            email="owner@geo.test", password="testpass123",
            first_name="Owner", last_name="Geo", company=self.company, role="owner",
        )
        self.manager = User.objects.create_user(
            email="manager@geo.test", password="testpass123",
            first_name="Manager", last_name="Geo", company=self.company, role="manager",
        )
        self.employee = User.objects.create_user(
            email="employee@geo.test", password="testpass123",
            first_name="Employee", last_name="Geo", company=self.company, role="employee",
        )

    def test_employee_cannot_change_geofence(self):
        self.client.force_authenticate(user=self.employee)
        response = self.client.post(
            reverse("workplace-config"),
            {"latitude": 10.0, "longitude": 10.0, "radius": 50000},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.company.refresh_from_db()
        self.assertEqual(float(self.company.latitude), 52.229676)
        self.assertEqual(self.company.radius, 100)

    def test_manager_can_change_geofence(self):
        self.client.force_authenticate(user=self.manager)
        response = self.client.post(
            reverse("workplace-config"),
            {"latitude": 51.107883, "longitude": 17.038538, "radius": 250},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.company.refresh_from_db()
        self.assertEqual(self.company.radius, 250)

    def test_employee_can_read_geofence(self):
        self.client.force_authenticate(user=self.employee)
        response = self.client.get(reverse("workplace-config"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["radius"], 100)


class AttendanceGeofenceTestCase(APITestCase):
    """BACKLOG P1-7: serwer liczy odleglosc i odrzuca check-in poza strefa."""

    def setUp(self):
        self.company = Company.objects.create(
            name="Geo Attendance",
            code="GEOATT01",
            latitude=52.229676,
            longitude=21.012229,
            radius=150,
        )
        self.employee = User.objects.create_user(
            email="worker@geo.test", password="testpass123",
            first_name="Worker", last_name="Geo", company=self.company, role="employee",
        )

    def _event(self, **overrides):
        payload = {
            "type": "check_in",
            "latitude": 52.229676,
            "longitude": 21.012229,
            "timestamp": timezone.now().isoformat(),
        }
        payload.update(overrides)
        return self.client.post(reverse("attendance-event"), payload, format="json")

    def test_check_in_inside_radius_is_accepted(self):
        self.client.force_authenticate(user=self.employee)

        response = self._event(latitude=52.229700, longitude=21.012300)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        event = AttendanceEvent.objects.get()
        self.assertTrue(event.is_valid)
        self.assertEqual(event.status, "approved")

    def test_check_in_outside_radius_is_rejected(self):
        self.client.force_authenticate(user=self.employee)

        # ~1.1 km na polnoc od lokalu przy promieniu 150 m
        response = self._event(latitude=52.239676, longitude=21.012229)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("outside of workplace radius", response.json()["detail"])
        self.assertGreater(response.json()["distance"], 150)
        self.assertFalse(AttendanceEvent.objects.exists())

    def test_missing_company_location_rejects_coordinates(self):
        self.company.latitude = None
        self.company.longitude = None
        self.company.save(update_fields=["latitude", "longitude"])
        self.client.force_authenticate(user=self.employee)

        response = self._event()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not configured", response.json()["detail"])
        self.assertFalse(AttendanceEvent.objects.exists())

    def test_event_without_coordinates_documents_remote_work_compromise(self):
        """P1-5 (decyzja produktowa): brak wspolrzednych = zdarzenie poprawne, omija geofencje."""
        self.client.force_authenticate(user=self.employee)

        response = self.client.post(reverse("attendance-event"), {
            "type": "check_in",
            "timestamp": timezone.now().isoformat(),
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        event = AttendanceEvent.objects.get()
        self.assertTrue(event.is_valid)
        self.assertIsNone(event.latitude)

