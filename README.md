# Część Serwerowa -  praca inżynierska

Backend systemu wspomagającego planowanie grafików pracy w gastronomii. Projekt zrealizowano w oparciu o Django oraz warstwę API udostępnianą przez Django REST Framework i `django-ninja`. Repozytorium zawiera logikę kont i firm, moduł ewidencji obecności i konfiguracji stanowisk oraz moduł planowania (z solverem opartym o OR-Tools).



## 1. Wymagania
- Python 3.x
- PostgreSQL (lokalnie lub zewnętrznie, podawany przez `DATABASE_URL`)

Zależności Pythona są opisane w `requirements.txt`.

## 2. Uruchomienie lokalne
Poniższe kroki są opisane dla Windows (PowerShell). Na Linux/macOS przebieg jest analogiczny, z użyciem `python3` zamiast `python`.

### 2.1. Konfiguracja środowiska
1) Utworzenie i aktywacja wirtualnego środowiska:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2) Instalacja zależności:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

3) Konfiguracja zmiennych środowiskowych:
- skopiowano plik `.env.example` do `.env`, a następnie uzupełniono wartości.

```powershell
Copy-Item .env.example .env
```

### 2.2. Migracje i konto administratora
Migracje bazy:

```powershell
python manage.py migrate
```

Utworzenie superużytkownika (panel admina Django):

```powershell
python manage.py createsuperuser
```

### 2.3. Start serwera

```powershell
python manage.py runserver
```

Domyślnie serwer uruchamia się pod adresem `http://127.0.0.1:8000/`.


## 3. Zmienne środowiskowe
Projekt używa pliku `.env` (ładowanego przez `python-dotenv`). Do repo dołączono `.env.example` bez sekretów.

Wymagane/typowe zmienne:
- `DATABASE_URL` – połączenie do PostgreSQL w formacie URL (np. `postgresql://user:pass@host:5432/dbname?sslmode=require`).
- `OPENAI_API_KEY` – opcjonalnie; wymagane tylko, jeżeli są wywoływane elementy integracji AI.
- `TEST_TOKEN_ENABLED` – flaga pomocnicza dla trybu testowego (zależnie od implementacji w module autoryzacji).

## 4. Dokumentacja API
Zdefiniowane są równolegle dwie warstwy routingu:
- endpointy DRF (m.in. `accounts/`) pod prefiksem `/api/accounts/`,
- endpointy Ninja (m.in. `schedule/`, `calendars/`) pod prefiksem `/api/`.

Po uruchomieniu serwera dostępne są:
- DRF schema: `GET /api/drf/schema/`
- DRF Swagger UI: `GET /api/drf/docs/`
- Combined schema: `GET /api/schema/combined/`
- Combined Swagger UI: `GET /api/docs/combined/`


## 5. Fragment pracy inżynierskiej (opis uruchomienia i architektury)

### 5.1. Cel i kontekst implementacyjny
W ramach pracy zrealizowano backend aplikacji, której celem jest wsparcie układania grafików dla zespołów pracowniczych w gastronomii. Założono, że system będzie obsługiwał kilka firm (np. sieć lokali), a dane będą rozdzielane na poziomie organizacji. W praktyce oznacza to konieczność utrzymania spójnej autoryzacji, powiązań użytkowników z firmą oraz modelu danych umożliwiającego zapis dostępności pracowników i wymagań kadrowych.

Przyjęto implementację w Django, ponieważ framework dostarcza stabilny ORM i mechanizmy migracji, a jednocześnie upraszcza pracę nad autoryzacją i administracją. Z kolei dla warstwy REST zastosowano DRF oraz `django-ninja`. Taki układ pozwolił rozdzielić część typowo „zasobową” (np. zarządzanie pracownikami, stanowiskami) od bardziej funkcjonalnych endpointów związanych z generowaniem grafiku.

### 5.2. Organizacja warstwy API
Zaimplementowano dwie ścieżki dokumentowania i udostępniania API. Dla endpointów w Django REST Framework wykorzystano generowanie schematu OpenAPI, co ułatwia weryfikację kontraktów danych w trakcie integracji z frontendem. Dla modułów planowania użyto routera Ninja, ponieważ w tym miejscu częściej występują operacje „akcyjne” (np. generowanie grafiku dla zakresu dat), a nie tylko CRUD.

W strukturze projektu logiczny podział na aplikacje (`accounts/`, `schedule/`, `calendars/`) okazał się wygodny. Pozwala to utrzymać modele i widoki bliżej siebie, a także w naturalny sposób ograniczać zakres uprawnień na poziomie modułu. W rzeczywistych zastosowaniach często spotyka się sytuację, w której harmonogram i logika kont rozwijają się niezależnie; tutaj ten podział został utrzymany od początku.

### 5.3. Konfiguracja środowiska i uruchomienie
Konfigurację oparto o plik `.env`, a zmienne wczytywane są przy starcie aplikacji. Zastosowano pojedynczą zmienną `DATABASE_URL`, co uprościło przenoszenie konfiguracji między środowiskami, szczególnie w przypadku bazy PostgreSQL uruchamianej jako usługa zewnętrzna.

Proces uruchomieniowy sprowadza się do utworzenia środowiska wirtualnego, instalacji zależności i wykonania migracji. Migracje wygenerowane przez ORM traktowane są jako część procesu wdrożenia, bo w praktyce to one „spajają” kod aplikacji z aktualnym schematem bazy. Dopiero po tej operacji uruchomiono serwer deweloperski Django.

Na rysunku 2.3 (w części dokumentacyjnej pracy) przedstawiono uproszczony przepływ: konfiguracja środowiska → migracje → uruchomienie API → konsumowanie endpointów z poziomu klienta. Tabela 3.1 zawiera zestawienie kluczowych endpointów użytych w scenariuszu testowym wraz z formatem odpowiedzi.

### 5.4. Uwierzytelnianie i autoryzacja
Uwierzytelnianie użytkowników zrealizowano w oparciu o JWT (pakiet `rest_framework_simplejwt`). W praktyce oznacza to, że klient otrzymuje krótkożyjący token dostępu oraz token odświeżania, a następnie dołącza nagłówek `Authorization: Bearer <token>` do żądań wymagających zalogowania.

Zastosowane podejście jest typowe dla aplikacji SPA i mobilnych, bo nie wymaga utrzymywania sesji po stronie serwera. Dalsze rozważania dotyczą tego, że część endpointów jest „firmowa” (np. lista pracowników), więc kluczowe stało się sprawdzanie powiązania użytkownika z firmą przy każdym żądaniu, a nie tylko na etapie logowania.
