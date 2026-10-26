# Schedule API — szybki przegląd

Prefiks bazowy: **`/api/schedule`**. Każdy endpoint wymaga `Authorization: Bearer <access>` i działa **w granicach firmy** z tokenu (izolacja `company`).

> **Podwójny prefiks:** router jest montowany pod `/schedule`, a trasy dotyczące konkretnego zapotrzebowania/zmiany mają w sobie własny człon `schedule`. Dlatego część ścieżek to `/api/schedule/schedule/...`, a część `/api/schedule/...` — to stan faktyczny, nie literówka.

## Lokalizacje
- `GET /api/schedule/locations` — lokalizacje firmy (id, nazwa, data utworzenia).
- `POST /api/schedule/locations` — nowa lokalizacja; `409` gdy nazwa już istnieje, `400` gdy pusta.

## Dostępności
- `POST /api/schedule/availability/bulk` — zapis dostępności jednego pracownika na wiele dni.
- `GET /api/schedule/availability?employee_id=&date_from=&date_to=` — lista z paginacją (tylko firma z tokenu).
- `GET /api/schedule/availability/mobile?employee_id=&date_from=&date_to=` — uproszczony format dla mobile (IDOR zamknięty: `employee_id` spoza firmy → pusta lista).

## Zapotrzebowanie dzienne
- `POST /api/schedule/demand/day` — zapis na dzień; bez `items` bierze szablon `DefaultDemand` dla weekday.
- `GET /api/schedule/demand/day?date=&location=` — odczyt dnia (z fallbackiem do szablonu).

## Szablony tygodniowe
- `POST /api/schedule/demand/default` — szablon jednego weekday (`weekday=null` = wzór ogólny).
- `POST /api/schedule/demand/default/bulk` — kilka weekdayów naraz.
- `GET /api/schedule/demand/default?location=&weekday=` — zapisane szablony.
- `GET /api/schedule/demand/default/week?location=` — 7 dni z `inherited` (dziedziczenie z wzoru ogólnego).

## Przegląd zapotrzebowań
- `GET /api/schedule/demand/{demand_id}` — szczegóły; `404` dla obcej firmy.
- `GET /api/schedule/demands` — lista z paginacją (tylko firma z tokenu).

## Reguły i dni specjalne (per firma)
- `POST /api/schedule/rules` — reguła (`override`/`multiplier`, `min_demand`/`max_demand`, `needs_experienced_default`).
- `GET /api/schedule/rules`, `GET /api/schedule/rules/{rule_id}` — lista / szczegóły; obca firma → `404`.
- `POST /api/schedule/special-days` — przypisanie reguły do daty i lokalizacji (`rule_id` musi należeć do firmy).
- `GET /api/schedule/special-days?date_from=&date_to=&location=` — lista.

## Generowanie grafiku
- `POST /api/schedule/generate-day` — solver dla jednego dnia (`items` lub szablon); `400` gdy brak obu.
- `POST /api/schedule/generate-range` — solver dla zakresu dat; `persist=false` liczy bez zapisu (wynik ma `id`, ale nic nie trafia do bazy).
- `422` gdy solver zwróci `SolverError` albo wynik nie przejdzie `validate_schedule`; w drugim przypadku **nic nie jest zapisywane**.

## Grafik i zmiany
- `GET /api/schedule/days/{day}?location=` — podgląd dnia (zapisane zmiany, a jeśli brak — próba wygenerowania z indeksu dnia).
- `GET /api/schedule/schedule/{demand_id}` — wymusza wygenerowanie i zwraca wszystkie zmiany zapotrzebowania.
- `GET /api/schedule/schedule/{demand_id}/day/{day}` — zmiany jednego dnia w zapotrzebowaniu.
- `GET /api/schedule/schedule/shift/{shift_uid}` — szczegóły zmiany (`shift_uid` z odpowiedzi generowania).
- `POST /api/schedule/schedule/shift` — ręczna korekta (`user_edited=true`); `missing_segments`/`assigned_employees_detail` są zapisywane w `meta`.

## Kontrakt odpowiedzi grafiku
`ScheduleShiftOut`: `id`, `date`, `location`, `start`, `end`, `demand`, `assigned_employees`, `needs_experienced`, `missing_minutes`, opcjonalnie `assigned_employees_detail` (segmenty), `missing_segments`, `experience_violated`.

Testy kontraktu: `schedule/tests_api_endpoints.py`, `schedule/tests_tenancy.py`, `schedule/tests_e2e_api.py`.
