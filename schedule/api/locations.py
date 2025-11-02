from __future__ import annotations

from typing import List

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from ..models import CompanyLocation
from ..schemas import CompanyLocationIn, CompanyLocationOut
from .router import api
from .utils import _get_company_for_request


@api.get(
    "/locations",
    response=List[CompanyLocationOut],
    openapi_extra={
        "summary": "Lista lokalizacji powiązanych z firmą",
        "description": "Zwraca listę lokalizacji (restauracji) przypisanych do firmy zalogowanego użytkownika.",
    },
)
def list_locations(request) -> List[dict]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    company = _get_company_for_request(request)
    locations = CompanyLocation.objects.filter(company=company).order_by("name")
    return [
        CompanyLocationOut(
            id=loc.id,
            name=loc.name,
            created_at=timezone.localtime(loc.created_at).isoformat(),
        ).dict()
        for loc in locations
    ]


@api.post(
    "/locations",
    response=CompanyLocationOut,
    openapi_extra={
        "summary": "Dodaj nową lokalizację",
        "description": "Tworzy nową lokalizację (restaurację) przypisaną do firmy zalogowanego użytkownika.",
    },
)
@transaction.atomic
def create_location(request, payload: CompanyLocationIn) -> dict:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    company = _get_company_for_request(request)
    name = (payload.name or "").strip()
    if not name:
        raise HttpError(400, "Nazwa lokalizacji jest wymagana")

    existing = CompanyLocation.objects.filter(company=company, name=name).first()
    if existing:
        raise HttpError(409, "Taka lokalizacja już istnieje")

    location = CompanyLocation.objects.create(company=company, name=name)
    return CompanyLocationOut(
        id=location.id,
        name=location.name,
        created_at=timezone.localtime(location.created_at).isoformat(),
    ).dict()
