from typing import List, Dict, Any, Optional, Iterable
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import make_aware
from ninja import Router
from ninja.errors import HttpError
from datetime import datetime, date as date_type
import hashlib
import json
from django.conf import settings
from django.core.mail import send_mail

from accounts.models import Company, User
from .models import (
    Availability,
    Demand,
    ScheduleShift,
    EventRule,
    SpecialDay,
    DayDemandIndex,
    DefaultDemand,
    CompanyLocation,
    EmployeeRole,
    EmployeeRoleAssignment,
    ShiftTransferRequest,
)
from .schemas import (
    CompanyLocationIn,
    CompanyLocationOut,
    BulkAvailabilityIn,
    AvailabilityOut,
    DemandDayIn,
    DemandDayOut,
    DemandSlotOut,
    DefaultDemandIn,
    DefaultDemandOut,
    DefaultDemandDayOut,
    DefaultDemandBulkIn,
    DefaultDemandWeekOut,
    DefaultDemandWeekDayOut,
    ScheduleFullOut,
    ScheduleShiftOut,
    ShiftUpdateIn,
    ShiftOut,
    EventRuleIn,
    EventRuleOut,
    SpecialDayIn,
    SpecialDayOut,
    GenerateDayIn,
    GenerateRangeIn,
    GenerateResultOut,
    AutoGenerateIn,
    EmployeeRoleIn,
    EmployeeRoleOut,
    EmployeeRoleAssignmentIn,
    EmployeeRoleAssignmentOut,
    ShiftTransferRequestIn,
    ShiftTransferRequestOut,
    ShiftTransferModerateIn,
    ShiftApproveIn,
)
from donkeybackend.security import DRFJWTAuth
from .solver import run_solver

api = Router(tags=["schedule"], auth=DRFJWTAuth())
#api = Router(tags=["schedule"])

def _build_emp_availability(date_from, date_to) -> List[Dict[str, Any]]:
    qs = Availability.objects.filter(date__gte=date_from, date__lte=date_to)
    out = []
    for a in qs:
        out.append(dict(
            employee_id=a.employee_id,
            employee_name=a.employee_name,
            date=a.date.isoformat(),
            experienced=bool(a.experienced),
            hours_min=int(a.hours_min or 0),
            hours_max=int(a.hours_max or 1_000_000_000),
            available_slots=list(a.available_slots or []),
            assigned_shift=a.assigned_shift or None,
        ))
    return out

def _norm_hhmm(s: str) -> str:
    if not s:
        return s
    s = s.strip().replace(" ", "").replace(".", ":")
    if ":" in s:
        hh, mm = s.split(":", 1)
        return f"{int(hh):02d}:{int(mm):02d}"
    return f"{int(s):02d}:00"

def _as_mapping(x) -> dict | None:
    """Return a dict-like mapping for Slot input x (dict or Pydantic model)."""
    if x is None:
        return None
    if isinstance(x, dict):
        return x
    # Pydantic v2 BaseModel
    if hasattr(x, "model_dump") and callable(getattr(x, "model_dump")):
        try:
            return x.model_dump()
        except Exception:
            pass
    # Pydantic v1 BaseModel
    if hasattr(x, "dict") and callable(getattr(x, "dict")):
        try:
            return x.dict()
        except Exception:
            pass
    # Fallback to attributes
    if hasattr(x, "__dict__"):
        return {
            k: getattr(x, k) for k in ("start", "end") if hasattr(x, k)
        }
    return None


def _coerce_slots(val) -> list[dict]:
    """
    Accepts:
      - None
      - {start,end} or SlotIn
      - [{start,end}, ...] or [SlotIn, ...]
    Returns list of normalized slot dicts.
    """
    if val is None:
        return []

    # Single object case (dict or model)
    m = _as_mapping(val)
    if isinstance(m, dict) and ("start" in m or "end" in m):
        start = _norm_hhmm(str(m.get("start", "")))
        end   = _norm_hhmm(str(m.get("end", "")))
        return [{"start": start, "end": end}] if start and end else []

    out = []
    # Iterable case
    try:
        for x in (val or []):
            mx = _as_mapping(x) or {}
            start = _norm_hhmm(str(mx.get("start", "")))
            end   = _norm_hhmm(str(mx.get("end", "")))
            if start and end:
                out.append({"start": start, "end": end})
    except TypeError:
        # Not iterable and not a single mapping -> treat as empty
        return []

    return out

def _validate_slots(slots: list[dict]) -> list[dict]:
    ok = []
    for s in slots:
        try:
            h1, m1 = map(int, s["start"].split(":"))
            h2, m2 = map(int, s["end"].split(":"))
        except Exception:
            continue
        t1, t2 = h1*60 + m1, h2*60 + m2
        if 0 <= t1 < t2 <= 1440:
            ok.append({"start": f"{h1:02d}:{m1:02d}", "end": f"{h2:02d}:{m2:02d}"})
    return ok



def _default_from_email() -> str:
    return getattr(settings, "DEFAULT_FROM_EMAIL", getattr(settings, "SERVER_EMAIL", "no-reply@donkey.local"))


def _collect_employee_emails(identifiers: Iterable[Any]) -> list[str]:
    emails: set[str] = set()
    if not identifiers:
        return []
    for raw in identifiers:
        if raw is None:
            continue
        ident = str(raw).strip()
        if not ident:
            continue
        try:
            user = User.objects.get(pk=int(ident))
        except (ValueError, User.DoesNotExist):
            user = None
        if user is None and "@" in ident:
            try:
                user = User.objects.get(email__iexact=ident)
            except User.DoesNotExist:
                user = None
        if user:
            if user.email:
                emails.add(user.email)
            continue
        if "@" in ident:
            emails.add(ident)
    return sorted(emails)


def _send_shift_notification(
    shift: ScheduleShift,
    action: str,
    actor: Optional[User] = None,
    note: str | None = None,
    extra_recipients: Optional[Iterable[Any]] = None,
) -> None:
    recipients = set(_collect_employee_emails(shift.assigned_employees or []))
    if extra_recipients:
        recipients.update(_collect_employee_emails(extra_recipients))
    recipient_list = sorted(recipients)
    if not recipient_list:
        return
    actor_label = actor.full_name if actor and getattr(actor, "full_name", None) else (actor.email if actor else "System")
    subject = f"Zmiana grafiku: {shift.date} {shift.start}-{shift.end}"
    message_lines = [
        f"Twoja zmiana {shift.date} {shift.start}-{shift.end} ({shift.location}) została {action}.",
        "",
    ]
    if actor:
        message_lines.append(f"Akcja wykonana przez: {actor_label}")
    if note:
        message_lines.extend(["", f"Notatka: {note}"])
    message_lines.extend([
        "",
        "Pamiętaj, aby sprawdzić grafik w aplikacji.",
    ])
    try:
        send_mail(subject, "\n".join(message_lines), _default_from_email(), recipient_list, fail_silently=True)
    except Exception:
        # Notification issues should not break API flow
        pass


def _shift_payload(shift: ScheduleShift, include_user_edited: bool = False) -> dict[str, Any]:
    data = dict(
        id=shift.shift_uid,
        date=shift.date.isoformat(),
        location=shift.location,
        start=shift.start,
        end=shift.end,
        demand=shift.demand_count,
        assigned_employees=list(shift.assigned_employees or []),
        needs_experienced=bool(shift.needs_experienced),
        missing_minutes=int(shift.missing_minutes or 0),
        confirmed=bool(shift.confirmed),
        approved_by=shift.approved_by_id,
        approved_at=shift.approved_at.isoformat() if shift.approved_at else None,
    )
    if include_user_edited:
        data["user_edited"] = bool(shift.user_edited)
    return data


def _transfer_payload(req: ShiftTransferRequest) -> dict[str, Any]:
    requested_name = getattr(req.requested_by, "full_name", None) or req.requested_by.email
    target_user = req.target_employee
    target_name = None
    if target_user:
        target_name = getattr(target_user, "full_name", None) or target_user.email
    return dict(
        id=req.id,
        shift_id=req.shift.shift_uid,
        action=req.action,
        status=req.status,
        requested_by=req.requested_by_id,
        requested_by_name=requested_name,
        target_employee_id=req.target_employee_id,
        target_employee_name=target_name,
        note=req.note,
        manager_note=req.manager_note,
        approved_by=req.approved_by_id,
        approved_at=req.approved_at.isoformat() if req.approved_at else None,
        created_at=req.created_at.isoformat(),
        updated_at=req.updated_at.isoformat(),
    )


def _employee_role_payload(role: EmployeeRole) -> dict[str, Any]:
    return dict(
        id=role.id,
        name=role.name,
        requires_experience=bool(role.requires_experience),
        description=role.description,
        created_at=role.created_at.isoformat(),
        updated_at=role.updated_at.isoformat(),
    )


def _employee_role_assignment_payload(assignment: EmployeeRoleAssignment) -> dict[str, Any]:
    user = assignment.user
    role = assignment.role
    assigned_by = assignment.assigned_by
    user_name = getattr(user, "full_name", None) or user.email
    return dict(
        id=assignment.id,
        role_id=role.id,
        role_name=role.name,
        user_id=user.id,
        user_name=user_name,
        active=bool(assignment.active),
        notes=assignment.notes,
        assigned_by=assigned_by.id if assigned_by else None,
        created_at=assignment.created_at.isoformat(),
        updated_at=assignment.updated_at.isoformat(),
    )



@api.post(
    "/availability/bulk",
    response=List[AvailabilityOut],
    openapi_extra={
        "summary": "Upsert availability for one employee (bulk by days)",
        "description": "Request body is a single object with employee context and an 'availabilities' array. The legacy doc that showed a root-level array is incorrect.",
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "example": {
                        "employee_id": 9,
                        "employee_name": "test2",
                        "experienced": False,
                        "hours_min": 0,
                        "hours_max": 1000000000,
                        "availabilities": [
                            {"date": "2025-10-20", "available_slots": {"start": "03:00", "end": "16:00"}},
                            {"date": "2025-10-21", "available_slots": None},
                            {"date": "2025-10-24", "available_slots": {"start": "14:00", "end": "20:30"}}
                        ]
                    }
                }
            }
        }
    }
)
@transaction.atomic
def upsert_availability_bulk(request, payload: BulkAvailabilityIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    emp_id = str(payload.employee_id)
    emp_name = payload.employee_name or ""

    saved: list[Availability] = []
    for day in payload.availabilities:
        slots = _validate_slots(_coerce_slots(day.available_slots))
        obj, _ = Availability.objects.update_or_create(
            employee_id=emp_id,
            date=day.date,
            defaults=dict(
                employee_name=emp_name,
                experienced=bool(payload.experienced),
                hours_min=int(payload.hours_min or 0),
                hours_max=int(payload.hours_max or 1_000_000_000),
                available_slots=slots,
            )
        )
        saved.append(obj)

    return [
        dict(
            employee_id=o.employee_id,
            employee_name=o.employee_name,
            date=o.date.isoformat(),
            experienced=o.experienced,
            hours_min=o.hours_min,
            hours_max=o.hours_max,
            available_slots=o.available_slots,
        ) for o in saved
    ]

# ------- GET: lista z filtrami + paginacja -------
@api.get("/availability")
def list_availability(
    request,
    employee_id: str,
    date_from: date_type | None = None,
    date_to: date_type | None = None,
    only_with_slots: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    limit = max(1, min(limit, 200))
    qs = Availability.objects.all().order_by("date", "employee_id")

    qs = qs.filter(employee_id=str(employee_id))
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if only_with_slots:
        qs = qs.exclude(available_slots=[])

    count = qs.count()
    items = list(qs[offset: offset + limit])

    results = [
        dict(
            employee_id=o.employee_id,
            employee_name=o.employee_name,
            date=o.date.isoformat(),
            experienced=o.experienced,
            hours_min=o.hours_min,
            hours_max=o.hours_max,
            available_slots=o.available_slots,
        ) for o in items
    ]

    next_off = offset + limit if offset + limit < count else None
    prev_off = offset - limit if offset > 0 else None
    return {"count": count, "next": next_off, "previous": prev_off, "results": results}


# ===================== DEMAND & SCHEDULE =====================

def _hash_payload(obj: Any) -> str:
    try:
        s = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# ---- Day-level helpers ----
_DEF_LOC_ATTRS = ("location", "default_location", "restaurant", "org_location")

def _get_company_for_request(request) -> Company:
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        raise HttpError(401, "Unauthorized")
    company = getattr(user, "company", None)
    if company:
        return company
    raise HttpError(403, "Brak przypisanej firmy dla użytkownika")


def _get_company_location(company: Company, location: str, create: bool = False) -> CompanyLocation:
    loc = (location or "").strip()
    if not loc:
        raise HttpError(400, "Missing location")
    obj = CompanyLocation.objects.filter(company=company, name=loc).first()
    if obj:
        return obj
    if create:
        return CompanyLocation.objects.create(company=company, name=loc)
    raise HttpError(404, "Lokalizacja nie należy do Twojej firmy")


def _infer_location(request, location_param: Optional[str], *, create_if_missing: bool = False) -> str:
    loc = (location_param or "").strip()
    if not loc:
        # Try to infer from user if available (best-effort; can be extended later)
        user = getattr(request, "user", None)
        if user is not None:
            for attr in _DEF_LOC_ATTRS:
                if hasattr(user, attr):
                    v = getattr(user, attr)
                    if v:
                        loc = str(v)
                        break
            if not loc:
                # JWT claims via DRFJWTAuth could be stored on request.auth
                auth = getattr(request, "auth", None)
                if isinstance(auth, dict):
                    for k in ("location", "loc", "restaurant"):
                        if auth.get(k):
                            loc = str(auth[k])
                            break
    if not loc:
        raise HttpError(400, "Missing location: provide 'location' or ensure user has a default location")
    company = _get_company_for_request(request)
    _get_company_location(company, loc, create=create_if_missing)
    return loc


def _normalize_weekday(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        num = int(value)
    except Exception:
        raise HttpError(400, "weekday musi być liczbą 0-6")
    if num < 0 or num > 6:
        raise HttpError(400, "weekday musi być w zakresie 0-6 (pon=0)")
    return num


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


@api.get(
    "/roles",
    response=List[EmployeeRoleOut],
    openapi_extra={
        "summary": "Lista ról pracowniczych",
        "description": "Zwraca role dostępne w firmie wraz z wymaganiami dotyczącymi doświadczenia.",
    },
)
def list_employee_roles(request) -> List[dict]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    company = _get_company_for_request(request)
    roles = EmployeeRole.objects.filter(company=company).order_by("name")
    return [_employee_role_payload(role) for role in roles]


@api.post(
    "/roles",
    response=EmployeeRoleOut,
    openapi_extra={
        "summary": "Dodaj nową rolę pracownika",
        "description": "Role pozwalają na filtrowanie zmian ze względu na funkcję lub doświadczenie.",
    },
)
@transaction.atomic
def create_employee_role(request, payload: EmployeeRoleIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    if request.user.role not in ("manager", "owner"):
        raise HttpError(403, "Tylko manager lub właściciel może tworzyć role")
    company = _get_company_for_request(request)
    name = (payload.name or "").strip()
    if not name:
        raise HttpError(400, "Nazwa roli jest wymagana")
    role, created = EmployeeRole.objects.get_or_create(
        company=company,
        name=name,
        defaults=dict(
            requires_experience=bool(payload.requires_experience),
            description=payload.description or "",
        ),
    )
    if not created:
        # Update description/experience flag if role already exists
        role.requires_experience = bool(payload.requires_experience)
        role.description = payload.description or role.description
        role.save(update_fields=["requires_experience", "description", "updated_at"])
    return _employee_role_payload(role)


@api.get(
    "/roles/assignments",
    response=List[EmployeeRoleAssignmentOut],
    openapi_extra={
        "summary": "Lista przypisań ról",
        "description": "Pokazuje aktywne przypisania ról dla pracowników w firmie.",
    },
)
def list_role_assignments(request) -> List[dict]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    company = _get_company_for_request(request)
    assignments = (
        EmployeeRoleAssignment.objects.select_related("role", "user", "assigned_by")
        .filter(role__company=company)
        .order_by("-created_at")
    )
    return [_employee_role_assignment_payload(a) for a in assignments]


@api.post(
    "/roles/assign",
    response=EmployeeRoleAssignmentOut,
    openapi_extra={
        "summary": "Przypisz rolę pracownikowi",
        "description": "Pozwala aktywować lub dezaktywować rolę dla wybranego pracownika.",
    },
)
@transaction.atomic
def assign_employee_role(request, payload: EmployeeRoleAssignmentIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    if request.user.role not in ("manager", "owner"):
        raise HttpError(403, "Tylko manager lub właściciel może zarządzać rolami")
    company = _get_company_for_request(request)
    try:
        role = EmployeeRole.objects.get(id=payload.role_id, company=company)
    except EmployeeRole.DoesNotExist:
        raise HttpError(404, "Rola nie istnieje")
    try:
        user = User.objects.get(id=payload.user_id, company=company)
    except User.DoesNotExist:
        raise HttpError(404, "Pracownik nie istnieje")

    active = True if payload.active is None else bool(payload.active)
    notes = payload.notes or ""

    assignment, created = EmployeeRoleAssignment.objects.update_or_create(
        role=role,
        user=user,
        defaults=dict(
            active=active,
            notes=notes,
            assigned_by=request.user,
        ),
    )

    if not active:
        # Ensure we persist deactivation timestamp
        assignment.active = False
        assignment.notes = notes
        assignment.assigned_by = request.user
        assignment.save(update_fields=["active", "notes", "assigned_by", "updated_at"])

    return _employee_role_assignment_payload(assignment)


def _canonicalize_day_items(items: List[Dict[str, Any]], date_s: str, location: str) -> List[Dict[str, Any]]:
    canon = []
    for it in (items or []):
        start = _norm_hhmm(str(it.get("start", "")))
        end   = _norm_hhmm(str(it.get("end", "")))
        if not (start and end):
            # skip invalid entries silently
            continue
        dmd = int(it.get("demand", 0) or 0)
        ne  = bool(it.get("needs_experienced", False))
        canon.append({
            "date": date_s,
            "location": location,
            "start": start,
            "end": end,
            "demand": dmd,
            "needs_experienced": ne,
        })
    # stable sort for hash
    canon.sort(key=lambda x: (x["start"], x["end"], x["demand"], x.get("needs_experienced", False)))
    return canon


def _canonicalize_template_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    canon = []
    for it in (items or []):
        start = _norm_hhmm(str(it.get("start", "")))
        end = _norm_hhmm(str(it.get("end", "")))
        if not (start and end):
            continue
        dmd = int(it.get("demand", 0) or 0)
        ne = bool(it.get("needs_experienced", False))
        canon.append({
            "start": start,
            "end": end,
            "demand": dmd,
            "needs_experienced": ne,
        })
    canon.sort(key=lambda x: (x["start"], x["end"], x["demand"], x.get("needs_experienced", False)))
    return canon


def _strip_day_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items or []:
        out.append({
            "start": it.get("start"),
            "end": it.get("end"),
            "demand": int(it.get("demand", 0) or 0),
            "needs_experienced": bool(it.get("needs_experienced", False)),
        })
    return out


def _day_hash(date_s: str, location: str, items: List[Dict[str, Any]]) -> str:
    # items should already be canonicalized and sorted
    return _hash_payload({"date": date_s, "location": location, "items": items})


def _group_payload_by_day_location(items: List[Dict[str, Any]]) -> Dict[tuple, List[Dict[str, Any]]]:
    mp: Dict[tuple, List[Dict[str, Any]]] = {}
    for it in (items or []):
        d = str(it.get("date"))
        loc = str(it.get("location", ""))
        if not d:
            # skip
            continue
        mp.setdefault((d, loc), []).append(it)
    return mp


def _populate_day_index_for_demand(demand: Demand):
    try:
        groups = _group_payload_by_day_location(demand.raw_payload or [])
        for (d, loc), items in groups.items():
            canon = _canonicalize_day_items(items, d, loc)
            h = _day_hash(d, loc, canon)
            DayDemandIndex.objects.get_or_create(
                date=d, location=loc, day_hash=h, defaults={"demand": demand}
            )
    except Exception:
        # best-effort; do not fail API if indexing fails
        pass


def _shift_uid(demand_id: int, a: Dict[str, Any]) -> str:
    return f"D{demand_id}|{a['date']}|{a['location']}|{a['start']}-{a['end']}"


def _with_ids(demand_id: int, assignments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for a in assignments or []:
        b = dict(a)
        b["id"] = _shift_uid(demand_id, a)
        out.append(b)
    return out


def _assignments_from_db(demand: Demand) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in demand.shifts.all().order_by("date", "location", "start", "end"):
        payload = _shift_payload(s)
        # ScheduleShiftOut schema does not expose user_edited so do not include it
        out.append(payload)
    return out


def _assignments_for_day_from_db(demand: Demand, day: str, location: Optional[str] = None) -> List[Dict[str, Any]]:
    qs = demand.shifts.filter(date=day)
    if location:
        qs = qs.filter(location=location)
    out: List[Dict[str, Any]] = []
    for s in qs.order_by("location", "start"):
        payload = _shift_payload(s)
        out.append(payload)
    return out


def _get_or_build_day_index(day: str, location: str) -> DayDemandIndex | None:
    # Try existing index first
    idx = DayDemandIndex.objects.filter(date=day, location=location).order_by("-id").first()
    if idx:
        return idx
    # Lazy backfill by scanning existing demands in range
    from datetime import date as _date
    try:
        day_dt = _date.fromisoformat(day)
    except Exception:
        return None
    candidates = Demand.objects.filter(date_from__lte=day_dt, date_to__gte=day_dt).order_by("-created_at")
    for d in candidates:
        groups = _group_payload_by_day_location(d.raw_payload or [])
        items = groups.get((day, location))
        if not items:
            continue
        canon = _canonicalize_day_items(items, day, location)
        h = _day_hash(day, location, canon)
        try:
            idx, _ = DayDemandIndex.objects.get_or_create(date=day, location=location, day_hash=h, defaults={"demand": d})
            return idx
        except Exception:
            # may race; retry fetch
            idx = DayDemandIndex.objects.filter(date=day, location=location, day_hash=h).order_by("-id").first()
            if idx:
                return idx
    return None


def _get_default_template(company: Company, location: str, weekday: Optional[int] = None) -> List[Dict[str, Any]]:
    base_qs = DefaultDemand.objects.filter(company=company, location=location)
    if weekday is not None:
        obj = base_qs.filter(weekday=weekday).order_by("-updated_at").first()
        if obj:
            return _canonicalize_template_items(obj.items or [])
    obj = base_qs.filter(weekday__isnull=True).order_by("-updated_at").first()
    if obj:
        return _canonicalize_template_items(obj.items or [])
    return []


def _list_default_days(company: Company, location: str) -> List[Dict[str, Any]]:
    defaults = DefaultDemand.objects.filter(company=company, location=location).order_by("weekday", "id")
    out: List[Dict[str, Any]] = []
    for obj in defaults:
        canon = _canonicalize_template_items(obj.items or [])
        out.append(dict(
            weekday=obj.weekday,
            items=canon,
            updated_at=timezone.localtime(obj.updated_at).isoformat(),
        ))
    return out


def _build_default_week(company: Company, location: str) -> List[Dict[str, Any]]:
    qs = DefaultDemand.objects.filter(company=company, location=location).order_by("-updated_at", "-id")
    by_weekday: dict[int, Dict[str, Any]] = {}
    fallback: Optional[Dict[str, Any]] = None

    for obj in qs:
        canon = _canonicalize_template_items(obj.items or [])
        entry = dict(
            items=canon,
            updated_at=timezone.localtime(obj.updated_at).isoformat() if obj.updated_at else None,
        )
        if obj.weekday is None:
            if fallback is None:
                fallback = entry
        else:
            if obj.weekday not in by_weekday:
                by_weekday[obj.weekday] = entry

    def _serialize_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [DemandSlotOut(**item).dict() for item in (items or [])]

    out: List[Dict[str, Any]] = []
    for weekday in range(7):
        if weekday in by_weekday:
            data = by_weekday[weekday]
            out.append(
                DefaultDemandWeekDayOut(
                    weekday=weekday,
                    items=_serialize_items(data["items"]),
                    updated_at=data["updated_at"],
                    inherited=False,
                ).dict()
            )
        elif fallback is not None:
            out.append(
                DefaultDemandWeekDayOut(
                    weekday=weekday,
                    items=_serialize_items(fallback["items"]),
                    updated_at=fallback["updated_at"],
                    inherited=True,
                ).dict()
            )
        else:
            out.append(
                DefaultDemandWeekDayOut(
                    weekday=weekday,
                    items=[],
                    updated_at=None,
                    inherited=False,
                ).dict()
            )

    return out


def _upsert_default_day(company: Company, location: str, weekday: Optional[int], canon_items: List[Dict[str, Any]]) -> DefaultDemand:
    obj, created = DefaultDemand.objects.get_or_create(
        company=company,
        location=location,
        weekday=weekday,
        defaults={"items": canon_items},
    )
    if not created:
        obj.items = canon_items
        update_fields = ["items", "updated_at"]
        if obj.company_id != company.id:
            obj.company = company
            update_fields.append("company")
        if obj.weekday != weekday:
            obj.weekday = weekday
            update_fields.append("weekday")
        obj.save(update_fields=update_fields)
    return obj


def _extract_location_from_payload(items: List[Dict[str, Any]]) -> str:
    for it in items or []:
        loc = (it or {}).get("location")
        if loc:
            return str(loc)
    return ""


@api.post(
    "/demand/day",
    response=DemandDayOut,
    openapi_extra={
        "summary": "Zapisz zapotrzebowanie na dzień",
        "description": "Tworzy albo nadpisuje zapotrzebowanie dla wskazanej daty. Lokalizację bierzemy z użytkownika, ale można ją"
        " podać w polu location. Jeśli nie przekażesz listy zmian użyjemy domyślnego wzoru restauracji.",
    },
)
@transaction.atomic
def save_day_demand(request, payload: DemandDayIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    loc = _infer_location(request, payload.location, create_if_missing=True)
    day = (payload.date or "").strip()
    if not day:
        raise HttpError(400, "Missing 'date'")

    try:
        from datetime import date as _date

        day_dt = _date.fromisoformat(day)
    except Exception:
        raise HttpError(400, "Invalid date format. Użyj YYYY-MM-DD")

    company = _get_company_for_request(request)
    weekday = day_dt.weekday()

    raw_items = [dict(x) for x in (payload.items or [])]
    if raw_items:
        canon_items = _canonicalize_day_items(raw_items, day, loc)
    else:
        template = _get_default_template(company, loc, weekday)
        if not template:
            raise HttpError(400, "Brak listy zmian i brak domyślnego zapotrzebowania dla tej restauracji")
        canon_items = _canonicalize_day_items(template, day, loc)

    if not canon_items:
        raise HttpError(400, "Lista zmian jest pusta")

    content_hash = _day_hash(day, loc, canon_items)

    idx = DayDemandIndex.objects.filter(date=day, location=loc).order_by("-id").first()
    if idx:
        obj = idx.demand
    else:
        obj = None

    if obj is None:
        obj, created = Demand.objects.get_or_create(
            content_hash=content_hash,
            defaults=dict(name="", raw_payload=canon_items, date_from=day_dt, date_to=day_dt),
        )
        if not created:
            DayDemandIndex.objects.filter(demand=obj).delete()
    else:
        DayDemandIndex.objects.filter(demand=obj).delete()

    obj.raw_payload = canon_items
    obj.date_from = day_dt
    obj.date_to = day_dt
    obj.content_hash = content_hash
    obj.name = ""
    obj.schedule_generated = False
    obj.save(update_fields=[
        "raw_payload",
        "date_from",
        "date_to",
        "content_hash",
        "name",
        "schedule_generated",
        "updated_at",
    ])
    obj.shifts.all().delete()

    _populate_day_index_for_demand(obj)

    return dict(
        date=day,
        location=loc,
        items=[DemandSlotOut(**item).dict() for item in _strip_day_items(canon_items)],
        content_hash=obj.content_hash,
    )


@api.get(
    "/demand/day",
    response=DemandDayOut,
    openapi_extra={
        "summary": "Pobierz zapotrzebowanie na dzień",
        "description": "Zwraca zapotrzebowanie dla daty i restauracji. Jeśli brak rekordu zwracamy domyślne zapotrzebowanie lub pustą listę.",
    },
)
def get_day_demand(request, date: str, location: Optional[str] = None) -> Dict[str, Any]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    loc = _infer_location(request, location)
    day = (date or "").strip()
    if not day:
        raise HttpError(400, "Missing 'date'")

    try:
        from datetime import date as _date

        day_dt = _date.fromisoformat(day)
    except Exception:
        raise HttpError(400, "Invalid date format. Użyj YYYY-MM-DD")

    company = _get_company_for_request(request)
    weekday = day_dt.weekday()

    idx = _get_or_build_day_index(day, loc)
    if idx:
        demand = idx.demand
        groups = _group_payload_by_day_location(demand.raw_payload or [])
        day_items = _canonicalize_day_items(groups.get((day, loc), []), day, loc)
        return dict(
            date=day,
            location=loc,
            items=[DemandSlotOut(**item).dict() for item in _strip_day_items(day_items)],
            content_hash=_day_hash(day, loc, day_items) if day_items else demand.content_hash,
        )

    template = _get_default_template(company, loc, weekday)
    if template:
        return dict(
            date=day,
            location=loc,
            items=[DemandSlotOut(**item).dict() for item in template],
            content_hash=None,
        )

    return dict(date=day, location=loc, items=[], content_hash=None)


@api.post(
    "/demand/default",
    response=DefaultDemandOut,
    openapi_extra={
        "summary": "Ustaw domyślne zapotrzebowanie",
        "description": "Zapisuje listę zmian jako domyślne zapotrzebowanie restauracji. Wykorzystujemy je gdy nie podasz własnej listy dla dnia.",
    },
)
@transaction.atomic
def save_default_demand(request, payload: DefaultDemandIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    loc = _infer_location(request, payload.location, create_if_missing=True)
    company = _get_company_for_request(request)
    weekday = _normalize_weekday(getattr(payload, "weekday", None))
    raw_items = [dict(x) for x in (payload.items or [])]
    canon = _canonicalize_template_items(raw_items)
    if not canon:
        raise HttpError(400, "Lista zmian jest pusta")

    _upsert_default_day(company, loc, weekday, canon)

    defaults = _list_default_days(company, loc)
    return dict(
        location=loc,
        defaults=[
            DefaultDemandDayOut(
                weekday=entry["weekday"],
                items=[DemandSlotOut(**item).dict() for item in entry["items"]],
                updated_at=entry["updated_at"],
            ).dict()
            for entry in defaults
        ],
    )


@api.post(
    "/demand/default/bulk",
    response=DefaultDemandOut,
    openapi_extra={
        "summary": "Ustaw domyślne zapotrzebowania dla wielu dni tygodnia",
        "description": "Przyjmuje listę dni tygodnia wraz z zestawami zmian i zapisuje je jako domyślny wzór.",
    },
)
@transaction.atomic
def save_default_demand_bulk(request, payload: DefaultDemandBulkIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    loc = _infer_location(request, payload.location, create_if_missing=True)
    company = _get_company_for_request(request)

    if not payload.defaults:
        raise HttpError(400, "Przekaż przynajmniej jeden dzień tygodnia")

    for day in payload.defaults:
        weekday = _normalize_weekday(day.weekday)
        raw_items = [dict(x) for x in (day.items or [])]
        canon = _canonicalize_template_items(raw_items)
        if not canon:
            raise HttpError(400, f"Lista zmian dla dnia tygodnia {weekday if weekday is not None else '*'} jest pusta")
        _upsert_default_day(company, loc, weekday, canon)

    defaults = _list_default_days(company, loc)
    return dict(
        location=loc,
        defaults=[
            DefaultDemandDayOut(
                weekday=entry["weekday"],
                items=[DemandSlotOut(**item).dict() for item in entry["items"]],
                updated_at=entry["updated_at"],
            ).dict()
            for entry in defaults
        ],
    )


@api.get(
    "/demand/default/week",
    response=DefaultDemandWeekOut,
    openapi_extra={
        "summary": "Pobierz pełny tygodniowy szablon zapotrzebowania",
        "description": (
            "Zwraca listę siedmiu dni (poniedziałek=0 … niedziela=6) wraz z domyślnymi zmianami. "
            "Jeżeli dla dnia nie ma indywidualnego zapisu, zostaną użyte zmiany z szablonu ogólnego (weekday=null)."
        ),
    },
)
def get_default_demand_week(request, location: Optional[str] = None):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    loc = _infer_location(request, location)
    company = _get_company_for_request(request)
    defaults = _build_default_week(company, loc)

    return dict(
        location=loc,
        defaults=defaults,
    )


@api.get(
    "/demand/default",
    response=DefaultDemandOut,
    openapi_extra={
        "summary": "Pobierz domyślne zapotrzebowanie",
        "description": "Zwraca aktualny domyślny zestaw zmian dla restauracji. Możesz ograniczyć odpowiedź do wybranego dnia tygodnia.",
    },
)
def get_default_demand(request, location: Optional[str] = None, weekday: Optional[int] = None):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")

    loc = _infer_location(request, location)
    company = _get_company_for_request(request)
    weekday_norm = _normalize_weekday(weekday) if weekday is not None else None

    defaults = _list_default_days(company, loc)
    if weekday_norm is not None:
        defaults = [entry for entry in defaults if entry["weekday"] == weekday_norm]

    return dict(
        location=loc,
        defaults=[
            DefaultDemandDayOut(
                weekday=entry["weekday"],
                items=[DemandSlotOut(**item).dict() for item in entry["items"]],
                updated_at=entry["updated_at"],
            ).dict()
            for entry in defaults
        ],
    )


@api.get("/demand/{demand_id}")
def get_demand(request, demand_id: int) -> Dict[str, Any]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        d = Demand.objects.get(id=demand_id)
    except Demand.DoesNotExist:
        raise HttpError(404, "Demand not found")
    return dict(
        id=d.id,
        location=_extract_location_from_payload(d.raw_payload or []),
        date_from=str(d.date_from),
        date_to=str(d.date_to),
        schedule_generated=bool(d.schedule_generated),
        count=len(d.raw_payload or []),
        payload=d.raw_payload,
    )


@api.get("/demands")
def list_demands(request, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    limit = max(1, min(200, limit))
    qs = Demand.objects.all().order_by("-created_at")
    count = qs.count()
    items = list(qs[offset: offset + limit])
    out = [
        dict(
            id=o.id,
            location=_extract_location_from_payload(o.raw_payload or []),
            date_from=str(o.date_from),
            date_to=str(o.date_to),
            schedule_generated=o.schedule_generated,
            created_at=o.created_at.isoformat(),
        )
        for o in items
    ]
    next_off = offset + limit if offset + limit < count else None
    prev_off = offset - limit if offset > 0 else None
    return {"count": count, "next": next_off, "previous": prev_off, "results": out}


def _apply_special_rules_to_demand(items: List[Dict[str, Any]], date_from, date_to) -> List[Dict[str, Any]]:
    """Apply SpecialDay/EventRule transformations to incoming demand items without mutating originals.
    - Exact (date+location) rules take precedence; then wildcard (date+"") rules are applied if present.
    - Multiple rules for the same key are applied in creation order.
    """
    import math
    # Preload active special days + rules within range
    sd_qs = SpecialDay.objects.select_related("rule").filter(
        active=True,
        date__gte=date_from,
        date__lte=date_to,
        rule__active=True,
    ).order_by("created_at", "id")

    # Build mapping: (date_iso, location) -> [rule,...]; wildcard stored with location ""
    map_by_key: dict[tuple[str, str], list] = {}
    for sd in sd_qs:
        k = (sd.date.isoformat(), (sd.location or ""))
        map_by_key.setdefault(k, []).append(sd.rule)

    def apply_rules_for(date_s: str, location: str, base_demand: int, needs_exp: bool) -> tuple[int, bool]:
        # Gather rules: exact first, then wildcard
        rules = []
        exact = map_by_key.get((date_s, location or ""), [])
        wildcard = map_by_key.get((date_s, ""), [])
        # exact should override wildcard precedence; we apply wildcard first, then exact
        rules.extend(wildcard)
        rules.extend(exact)
        d_val = int(base_demand)
        nexp = bool(needs_exp)
        for r in rules:
            if r.mode == r.MODE_OVERRIDE:
                try:
                    d_val = int(round(r.value))
                except Exception:
                    d_val = int(r.value)
            else:  # multiplier
                try:
                    d_val = int(math.ceil(d_val * float(r.value)))
                except Exception:
                    d_val = d_val
            if r.min_demand is not None:
                d_val = max(d_val, int(r.min_demand))
            if r.max_demand is not None:
                d_val = min(d_val, int(r.max_demand))
            if r.needs_experienced_default:
                nexp = True
        d_val = max(0, int(d_val))
        return d_val, nexp

    out: List[Dict[str, Any]] = []
    for it in (items or []):
        a = dict(it)
        a_date = a.get("date")
        a_loc = a.get("location", "")
        d0 = int(a.get("demand", 0) or 0)
        n0 = bool(a.get("needs_experienced", False))
        d_new, n_new = apply_rules_for(str(a_date), str(a_loc), d0, n0)
        a["demand"] = d_new
        if n_new:
            a["needs_experienced"] = True
        out.append(a)
    return out


def _ensure_schedule_for_demand(d: Demand, force: bool = False) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Ensures schedule is generated for given demand; returns (assignments, summary) where
    summary contains uncovered/hours_summary when generation occurred. If schedule already existed,
    summary may be None.
    """
    # If shifts already exist and not forcing, return from DB
    if not force and d.shifts.exists():
        # ensure index exists (safety)
        _populate_day_index_for_demand(d)
        return _assignments_from_db(d), None

    # When forcing, clear existing
    if force and d.shifts.exists():
        d.shifts.all().delete()

    # Build availability input from DB for the demand date range
    avail_qs = Availability.objects.filter(date__gte=d.date_from, date__lte=d.date_to)
    emp_avail = []
    for a in avail_qs:
        emp_avail.append(dict(
            employee_id=a.employee_id,
            employee_name=a.employee_name,
            date=a.date.isoformat(),
            experienced=bool(a.experienced),
            hours_min=int(a.hours_min or 0),
            hours_max=int(a.hours_max or 1_000_000_000),
            available_slots=list(a.available_slots or []),
            assigned_shift=a.assigned_shift or None,
        ))

    # Apply special rules (holidays/events) before solving
    demand_payload = _apply_special_rules_to_demand(d.raw_payload or [], d.date_from, d.date_to)

    res = run_solver(emp_availability=emp_avail, demand=demand_payload)

    # Persist assignments per day/shift
    ass = res.get("assignments", []) or []
    to_create = []
    from datetime import date as _date
    for a in ass:
        uid = _shift_uid(d.id, a)
        to_create.append(ScheduleShift(
            demand=d,
            shift_uid=uid,
            date=_date.fromisoformat(a["date"]),
            location=a["location"],
            start=a["start"],
            end=a["end"],
            demand_count=int(a.get("demand", 1)),
            needs_experienced=bool(a.get("needs_experienced", False)),
            assigned_employees=list(a.get("assigned_employees", []) or []),
            missing_minutes=int(a.get("missing_minutes", 0) or 0),
            meta=dict(uncovered=res.get("uncovered", []), hours_summary=res.get("hours_summary", [])),
        ))
    if to_create:
        ScheduleShift.objects.bulk_create(to_create, ignore_conflicts=True)
    # Mark generated
    if to_create:
        d.schedule_generated = True
        try:
            from django.utils import timezone
            d.solved_at = timezone.now()
        except Exception:
            d.solved_at = None
        d.save(update_fields=["schedule_generated", "solved_at", "updated_at"]) 

    return _assignments_from_db(d), {
        "uncovered": res.get("uncovered", []),
        "hours_summary": res.get("hours_summary", []),
    } if to_create else None


@api.get("/schedule/{demand_id}")
@transaction.atomic
def get_or_generate_schedule(request, demand_id: int, force: bool = False) -> List[ScheduleShiftOut]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        d = Demand.objects.get(id=demand_id)
    except Demand.DoesNotExist:
        raise HttpError(404, "Demand not found")

    assignments, _summary = _ensure_schedule_for_demand(d, force=force)
    return assignments


# ===== Rules (EventRule) =====
@api.post("/rules", response=EventRuleOut)
@transaction.atomic
def create_rule(request, payload: EventRuleIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    if payload.mode not in (EventRule.MODE_OVERRIDE, EventRule.MODE_MULTIPLIER):
        raise HttpError(400, "Invalid mode: must be 'override' or 'multiplier'")
    obj = EventRule.objects.create(
        name=payload.name,
        mode=payload.mode,
        value=float(payload.value),
        needs_experienced_default=bool(payload.needs_experienced_default or False),
        min_demand=payload.min_demand,
        max_demand=payload.max_demand,
        active=bool(payload.active if payload.active is not None else True),
    )
    return dict(
        id=obj.id,
        name=obj.name,
        mode=obj.mode,
        value=float(obj.value),
        needs_experienced_default=bool(obj.needs_experienced_default),
        min_demand=obj.min_demand,
        max_demand=obj.max_demand,
        active=bool(obj.active),
    )


@api.get("/rules")
def list_rules(request) -> List[EventRuleOut]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    qs = EventRule.objects.all().order_by("name", "id")
    return [
        dict(
            id=o.id,
            name=o.name,
            mode=o.mode,
            value=float(o.value),
            needs_experienced_default=bool(o.needs_experienced_default),
            min_demand=o.min_demand,
            max_demand=o.max_demand,
            active=bool(o.active),
        ) for o in qs
    ]


@api.get("/rules/{rule_id}", response=EventRuleOut)
def get_rule(request, rule_id: int):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        o = EventRule.objects.get(id=rule_id)
    except EventRule.DoesNotExist:
        raise HttpError(404, "Rule not found")
    return dict(
        id=o.id,
        name=o.name,
        mode=o.mode,
        value=float(o.value),
        needs_experienced_default=bool(o.needs_experienced_default),
        min_demand=o.min_demand,
        max_demand=o.max_demand,
        active=bool(o.active),
    )


# ===== Special days =====
@api.post("/special-days", response=SpecialDayOut)
@transaction.atomic
def create_special_day(request, payload: SpecialDayIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        rule = EventRule.objects.get(id=payload.rule_id)
    except EventRule.DoesNotExist:
        raise HttpError(400, "Invalid rule_id")
    obj, created = SpecialDay.objects.get_or_create(
        date=payload.date,
        location=(payload.location or ""),
        rule=rule,
        defaults=dict(note=payload.note or "", active=bool(payload.active if payload.active is not None else True))
    )
    # If exists, update note/active
    if not created:
        obj.note = payload.note or obj.note
        if payload.active is not None:
            obj.active = bool(payload.active)
        obj.save(update_fields=["note", "active", "updated_at"])
    return dict(
        id=obj.id,
        date=obj.date.isoformat(),
        location=obj.location or "",
        rule_id=obj.rule_id,
        rule_name=obj.rule.name,
        note=obj.note,
        active=bool(obj.active),
    )


@api.get("/special-days")
def list_special_days(request, date_from: date_type | None = None, date_to: date_type | None = None, location: str | None = None) -> List[SpecialDayOut]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    qs = SpecialDay.objects.select_related("rule").all()
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if location is not None:
        qs = qs.filter(location=(location or ""))
    qs = qs.order_by("-date", "location")
    return [
        dict(
            id=o.id,
            date=o.date.isoformat(),
            location=o.location or "",
            rule_id=o.rule_id,
            rule_name=o.rule.name,
            note=o.note,
            active=bool(o.active),
        ) for o in qs
    ]


# ===== Day-level convenience endpoints =====
@api.get("/days/{day}")
@transaction.atomic
def get_day_schedule(request, day: str, location: Optional[str] = None) -> List[ScheduleShiftOut]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    loc = _infer_location(request, location)
    # If we already have persisted shifts for that date/location across any demand, return them
    shifts_qs = ScheduleShift.objects.filter(date=day, location=loc)
    if shifts_qs.exists():
        return [_shift_payload(s) for s in shifts_qs.order_by("start", "end")]
    # No persisted shifts — try to find a weekly demand through DayDemandIndex
    idx = _get_or_build_day_index(day, loc)
    if not idx:
        # Nothing known for this date/location
        return []
    d = idx.demand
    assignments, _summary = _ensure_schedule_for_demand(d, force=False)
    # filter to that day/location
    return _assignments_for_day_from_db(d, day, location=loc)


@api.post(
    "/generate-day",
    response=GenerateResultOut,
    openapi_extra={
        "summary": "Ułóż grafik na jeden dzień",
        "description": "Uruchamia solver dla podanej daty. Gdy lista zmian nie jest przesłana korzystamy z domyślnego zapotrzebowania.",
    },
)
@transaction.atomic
def generate_day(request, payload: GenerateDayIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    loc = _infer_location(request, payload.location, create_if_missing=True)
    company = _get_company_for_request(request)
    day = payload.date
    from datetime import date as _date

    try:
        day_dt = _date.fromisoformat(day)
    except Exception:
        raise HttpError(400, "Invalid date format. Użyj YYYY-MM-DD")

    weekday = day_dt.weekday()
    # Canonicalize items – if provided
    if payload.items:
        raw_items = [dict(x) for x in payload.items]
        # allow items missing date/location; enforce both
        canon_items = _canonicalize_day_items(raw_items, day, loc)
    else:
        template = _get_default_template(company, loc, weekday)
        if not template:
            raise HttpError(400, "Brak zapotrzebowania: podaj items albo ustaw domyślną listę zmian")
        canon_items = _canonicalize_day_items(template, day, loc)

    h = _day_hash(day, loc, canon_items)

    # Try reuse existing weekly demand via DayDemandIndex
    idx = DayDemandIndex.objects.filter(date=day, location=loc, day_hash=h).order_by("-id").first()
    if idx:
        d = idx.demand
        if payload.persist is False:
            # compute ad-hoc for this day, do not persist
            emp_avail = _build_emp_availability(d.date_from, d.date_to)
            # Apply special rules only to day items
            demand_payload = _apply_special_rules_to_demand(canon_items, day_dt, day_dt)
            res = run_solver(emp_availability=emp_avail, demand=demand_payload)
            return dict(demand_id=d.id, assignments=_with_ids(d.id, res.get("assignments", [])), summary={"uncovered": res.get("uncovered", []), "hours_summary": res.get("hours_summary", [])})
        # ensure schedule persisted
        assignments, summary = _ensure_schedule_for_demand(d, force=bool(payload.force))
        # return only that day/location
        return dict(demand_id=d.id, assignments=_assignments_for_day_from_db(d, day, location=loc), summary=summary)

    # No index found — create a dedicated one-day Demand (idempotent via hash of canon_items)
    content_hash = _hash_payload(canon_items)
    d, created = Demand.objects.get_or_create(
        content_hash=content_hash,
        defaults=dict(name=f"{day} {loc}", raw_payload=canon_items, date_from=day_dt, date_to=day_dt)
    )
    _populate_day_index_for_demand(d)

    if payload.persist is False:
        emp_avail = _build_emp_availability(day_dt, day_dt)
        demand_payload = _apply_special_rules_to_demand(canon_items, day_dt, day_dt)
        res = run_solver(emp_availability=emp_avail, demand=demand_payload)
        return dict(demand_id=d.id, assignments=_with_ids(d.id, res.get("assignments", [])), summary={"uncovered": res.get("uncovered", []), "hours_summary": res.get("hours_summary", [])})

    assignments, summary = _ensure_schedule_for_demand(d, force=bool(payload.force))
    return dict(demand_id=d.id, assignments=_assignments_for_day_from_db(d, day, location=loc), summary=summary)


def _generate_range_internal(request, payload: GenerateRangeIn) -> tuple[Dict[str, Any], Demand, date_type, date_type, str]:
    loc = _infer_location(request, payload.location, create_if_missing=True)
    company = _get_company_for_request(request)
    if payload.items and len(payload.items) > 0:
        template_items = [dict(x) for x in payload.items]
    else:
        template_items = None
    # Expand items for each day in range
    from datetime import date as _date, timedelta
    try:
        start = _date.fromisoformat(payload.date_from)
        end = _date.fromisoformat(payload.date_to)
    except Exception:
        raise HttpError(400, "Invalid date_from/date_to format")
    if end < start:
        raise HttpError(400, "date_to must be >= date_from")
    full_items: List[Dict[str, Any]] = []
    cur = start
    while cur <= end:
        day_s = cur.isoformat()
        # build canon for the day using templates (they may omit needs_experienced)
        if template_items is not None:
            source_items = template_items
        else:
            source_items = _get_default_template(company, loc, cur.weekday())
            if not source_items:
                raise HttpError(400, f"Brak domyślnego zapotrzebowania dla dnia {day_s}")
        day_canon = _canonicalize_day_items(source_items, day_s, loc)
        if not day_canon:
            raise HttpError(400, f"Lista zmian dla dnia {day_s} jest pusta")
        full_items.extend(day_canon)
        cur += timedelta(days=1)

    # Create/find Demand for full range (idempotent via content hash)
    content_hash = _hash_payload(full_items)
    d, _created = Demand.objects.get_or_create(
        content_hash=content_hash,
        defaults=dict(name=f"{start.isoformat()}..{end.isoformat()} {loc}", raw_payload=full_items, date_from=start, date_to=end)
    )
    # Populate day index mapping
    _populate_day_index_for_demand(d)

    if payload.persist is False:
        # Compute ad-hoc without persisting
        emp_avail = _build_emp_availability(start, end)
        demand_payload = _apply_special_rules_to_demand(full_items, start, end)
        res = run_solver(emp_availability=emp_avail, demand=demand_payload)
        result = dict(
            demand_id=d.id,
            assignments=res.get("assignments", []),
            summary={"uncovered": res.get("uncovered", []), "hours_summary": res.get("hours_summary", [])},
        )
        return result, d, start, end, loc

    assignments, summary = _ensure_schedule_for_demand(d, force=bool(payload.force))
    result = dict(demand_id=d.id, assignments=assignments, summary=summary)
    return result, d, start, end, loc


@api.post(
    "/generate-range",
    response=GenerateResultOut,
    openapi_extra={
        "summary": "Ułóż grafik na kilka dni",
        "description": "Buduje grafik dla zakresu dat korzystając z podanej listy zmian lub domyślnego zapotrzebowania restauracji.",
    },
)
@transaction.atomic
def generate_range(request, payload: GenerateRangeIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    result, _demand, _start, _end, _loc = _generate_range_internal(request, payload)
    return result


@api.post(
    "/schedule/auto-generate",
    response=GenerateResultOut,
    openapi_extra={
        "summary": "Automatycznie ułóż grafik na podstawie dyspozycyjności",
        "description": (
            "Buduje grafik w podanym zakresie dat korzystając z dyspozycyjności pracowników oraz domyślnych szablonów. "
            "Może wysłać powiadomienia mailowe do pracowników przydzielonych do zmian."
        ),
    },
)
@transaction.atomic
def auto_generate_schedule(request, payload: AutoGenerateIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    result, demand, start, end, loc = _generate_range_internal(request, payload)
    if payload.persist is False:
        return result
    if payload.send_notifications:
        shifts_qs = demand.shifts.filter(date__gte=start, date__lte=end, location=loc)
        for shift in shifts_qs:
            _send_shift_notification(shift, "zaplanuowana automatycznie", actor=request.user)
    return result


@api.get("/schedule/{demand_id}/day/{day}")
def get_schedule_day(request, demand_id: int, day: str) -> List[ScheduleShiftOut]:
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        d = Demand.objects.get(id=demand_id)
    except Demand.DoesNotExist:
        raise HttpError(404, "Demand not found")
    dsh = d.shifts.filter(date=day)
    return [_shift_payload(s) for s in dsh.order_by("location", "start")]


@api.get("/schedule/shift/{shift_id}", response=ShiftOut)
def get_shift(request, shift_id: str):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        s = ScheduleShift.objects.get(shift_uid=shift_id)
    except ScheduleShift.DoesNotExist:
        raise HttpError(404, "Shift not found")
    data = _shift_payload(s, include_user_edited=True)
    return data


@api.post("/schedule/shift", response=ShiftOut)
@transaction.atomic
def upsert_shift(request, payload: ShiftUpdateIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    # Find existing shift by id
    try:
        s = ScheduleShift.objects.get(shift_uid=payload.id)
    except ScheduleShift.DoesNotExist:
        raise HttpError(404, "Shift not found")

    # Update allowed fields
    fields = []
    if payload.date:
        s.date = payload.date
        fields.append("date")
    if payload.location:
        s.location = payload.location
        fields.append("location")
    if payload.start:
        s.start = payload.start
        fields.append("start")
    if payload.end:
        s.end = payload.end
        fields.append("end")
    if payload.demand is not None:
        s.demand_count = int(payload.demand)
        fields.append("demand_count")
    if payload.assigned_employees is not None:
        s.assigned_employees = [str(emp) for emp in payload.assigned_employees]
        fields.append("assigned_employees")
    if payload.needs_experienced is not None:
        s.needs_experienced = bool(payload.needs_experienced)
        fields.append("needs_experienced")
    if payload.missing_minutes is not None:
        s.missing_minutes = int(payload.missing_minutes)
        fields.append("missing_minutes")
    if payload.confirmed is not None:
        s.confirmed = bool(payload.confirmed)
        fields.append("confirmed")

    # Manual edits invalidate previous approvals
    s.approved_by = None
    s.approved_at = None
    fields.extend(["approved_by", "approved_at"])

    s.user_edited = True
    fields += ["user_edited", "updated_at"]
    s.save(update_fields=list(set(fields)))

    _send_shift_notification(s, "zaktualizowana", actor=request.user)

    data = _shift_payload(s, include_user_edited=True)
    return data


@api.post("/schedule/shift/{shift_id}/approve", response=ShiftOut)
@transaction.atomic
def approve_shift(request, shift_id: str, payload: ShiftApproveIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    if request.user.role not in ("manager", "owner"):
        raise HttpError(403, "Tylko manager lub właściciel może zatwierdzać zmiany")
    try:
        s = ScheduleShift.objects.get(shift_uid=shift_id)
    except ScheduleShift.DoesNotExist:
        raise HttpError(404, "Shift not found")

    s.confirmed = True
    s.approved_by = request.user
    s.approved_at = timezone.now()
    s.user_edited = True
    s.save(update_fields=["confirmed", "approved_by", "approved_at", "user_edited", "updated_at"])

    _send_shift_notification(s, "zatwierdzona", actor=request.user, note=payload.note)

    return _shift_payload(s, include_user_edited=True)


@api.post("/schedule/shift-transfer", response=ShiftTransferRequestOut)
@transaction.atomic
def create_shift_transfer_request(request, payload: ShiftTransferRequestIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    try:
        shift = ScheduleShift.objects.select_related("demand").get(shift_uid=payload.shift_id)
    except ScheduleShift.DoesNotExist:
        raise HttpError(404, "Shift not found")

    action = (payload.action or "").lower()
    if action not in (ShiftTransferRequest.ACTION_DROP, ShiftTransferRequest.ACTION_CLAIM):
        raise HttpError(400, "Unsupported action")

    requester = request.user
    assigned_raw = list(shift.assigned_employees or [])
    assigned_tokens = {str(x) for x in assigned_raw}
    assigned_lower = {token.lower() for token in assigned_tokens}
    requester_identifiers = {str(requester.id)}
    if requester.email:
        requester_identifiers.add(requester.email)
        requester_identifiers.add(requester.email.lower())

    target_user = None
    if payload.target_employee_id:
        try:
            target_user = User.objects.get(id=payload.target_employee_id, company=requester.company)
        except User.DoesNotExist:
            raise HttpError(400, "Nie znaleziono wskazanego pracownika")

    if action == ShiftTransferRequest.ACTION_DROP:
        if requester_identifiers.isdisjoint(assigned_tokens | assigned_lower):
            raise HttpError(400, "Pracownik nie jest przypisany do tej zmiany")
    elif action == ShiftTransferRequest.ACTION_CLAIM:
        if requester_identifiers & (assigned_tokens | assigned_lower):
            raise HttpError(400, "Pracownik już jest przypisany do tej zmiany")

    req = ShiftTransferRequest.objects.create(
        shift=shift,
        requested_by=requester,
        target_employee=target_user,
        action=action,
        note=payload.note or "",
    )

    extra = [addr for addr in [requester.email, target_user.email if target_user else None] if addr]
    _send_shift_notification(shift, "zgłoszona do akceptacji", actor=request.user, note=payload.note, extra_recipients=extra)

    return _transfer_payload(req)


@api.post("/schedule/shift-transfer/{request_id}/approve", response=ShiftTransferRequestOut)
@transaction.atomic
def approve_shift_transfer(request, request_id: int, payload: ShiftTransferModerateIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    if request.user.role not in ("manager", "owner"):
        raise HttpError(403, "Tylko manager lub właściciel może akceptować zmiany")
    try:
        req = ShiftTransferRequest.objects.select_related("shift", "requested_by", "target_employee").get(id=request_id)
    except ShiftTransferRequest.DoesNotExist:
        raise HttpError(404, "Request not found")
    if req.status != ShiftTransferRequest.STATUS_PENDING:
        raise HttpError(400, "Wniosek został już rozpatrzony")

    shift = req.shift
    assigned = [str(x) for x in (shift.assigned_employees or [])]

    requester_id_str = str(req.requested_by_id)
    requester_email = req.requested_by.email.lower()
    assigned = [a for a in assigned if str(a) not in {requester_id_str, requester_email}]

    if req.action == ShiftTransferRequest.ACTION_DROP:
        if req.target_employee_id:
            assigned.append(str(req.target_employee_id))
    elif req.action == ShiftTransferRequest.ACTION_CLAIM:
        assigned.append(str(req.requested_by_id))

    # De-duplicate while preserving order
    seen = set()
    deduped: list[str] = []
    for a in assigned:
        key = str(a)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)

    shift.assigned_employees = deduped
    shift.confirmed = True
    shift.approved_by = request.user
    shift.approved_at = timezone.now()
    shift.user_edited = True
    shift.save(update_fields=["assigned_employees", "confirmed", "approved_by", "approved_at", "user_edited", "updated_at"])

    req.status = ShiftTransferRequest.STATUS_APPROVED
    req.manager_note = payload.manager_note or ""
    req.approved_by = request.user
    req.approved_at = timezone.now()
    req.save(update_fields=["status", "manager_note", "approved_by", "approved_at", "updated_at"])

    extra_recipients = [addr for addr in [req.requested_by.email, req.target_employee.email if req.target_employee else None] if addr]
    _send_shift_notification(shift, "zatwierdzona (zmiana obsady)", actor=request.user, note=req.manager_note, extra_recipients=extra_recipients)

    return _transfer_payload(req)


@api.post("/schedule/shift-transfer/{request_id}/reject", response=ShiftTransferRequestOut)
@transaction.atomic
def reject_shift_transfer(request, request_id: int, payload: ShiftTransferModerateIn):
    if not request.user or not request.user.is_authenticated:
        raise HttpError(401, "Unauthorized")
    if request.user.role not in ("manager", "owner"):
        raise HttpError(403, "Tylko manager lub właściciel może akceptować zmiany")
    try:
        req = ShiftTransferRequest.objects.select_related("shift", "requested_by", "target_employee").get(id=request_id)
    except ShiftTransferRequest.DoesNotExist:
        raise HttpError(404, "Request not found")
    if req.status != ShiftTransferRequest.STATUS_PENDING:
        raise HttpError(400, "Wniosek został już rozpatrzony")

    req.status = ShiftTransferRequest.STATUS_REJECTED
    req.manager_note = payload.manager_note or ""
    req.approved_by = request.user
    req.approved_at = timezone.now()
    req.save(update_fields=["status", "manager_note", "approved_by", "approved_at", "updated_at"])

    shift = req.shift
    extra_recipients = [addr for addr in [req.requested_by.email, req.target_employee.email if req.target_employee else None] if addr]
    _send_shift_notification(shift, "odrzucona prośba o zmianę", actor=request.user, note=req.manager_note, extra_recipients=extra_recipients)

    return _transfer_payload(req)