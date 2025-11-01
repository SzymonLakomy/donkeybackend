# Schedule API – szybki przegląd

_Podstawowy prefiks wszystkich ścieżek to `/schedule`._

## Lokalizacje
- `GET /schedule/locations` – zwraca listę lokalizacji przypisanych do firmy zalogowanego użytkownika (identyfikator, nazwa, data utworzenia). Tylko te lokalizacje, które należą do danej firmy.
- `POST /schedule/locations` – tworzy nową lokalizację (restaurację) powiązaną z firmą zalogowanego użytkownika.

## Dostępności pracowników
- `POST /schedule/availability/bulk` – zapisuje dostępność jednego pracownika dla wielu dni w jednym żądaniu.
- `GET /schedule/availability` – pobiera listę zapisanych dostępności (filtrowanie po pracowniku i zakresie dat).

## Zapotrzebowania dzienne
- `POST /schedule/demand/day` – zapisuje zapotrzebowanie na konkretny dzień; jeżeli nie podasz listy zmian, używany jest domyślny szablon dla dnia tygodnia.
- `GET /schedule/demand/day` – pobiera zapotrzebowanie na konkretny dzień; gdy brak rekordu, zwraca domyślne zmiany dla dnia tygodnia.

## Domyślne zapotrzebowanie (szablony)
- `POST /schedule/demand/default` – zapisuje szablon zmian dla jednego dnia tygodnia (0=poniedziałek … 6=niedziela).
- `POST /schedule/demand/default/bulk` – zapisuje jednocześnie kilka dni tygodnia wraz z ich listami zmian.
- `GET /schedule/demand/default` – zwraca wszystkie domyślne szablony dla lokalizacji; można filtrować po `weekday`.
- `GET /schedule/demand/default/week` – zwraca listę siedmiu dni z domyślnymi zmianami (łącznie z dziedziczeniem ze wzoru ogólnego).

## Przegląd zapotrzebowań
- `GET /schedule/demand/{demand_id}` – szczegóły pojedynczego zapotrzebowania (daty, lokalizacja, lista zmian).
- `GET /schedule/demands` – lista ostatnio zapisanych zapotrzebowań z paginacją.

## Zasady specjalne (opcjonalne funkcje) *
- `POST /schedule/rules` – dodaje regułę modyfikującą zapotrzebowanie na podstawie wydarzeń specjalnych. *
- `GET /schedule/rules` – lista reguł. *
- `GET /schedule/rules/{rule_id}` – szczegóły reguły. *

## Dni specjalne (wykorzystują reguły) *
- `POST /schedule/special-days` – przypisuje regułę do konkretnego dnia/lokalizacji. *
- `GET /schedule/special-days` – lista dni z przypisanymi regułami (filtry po dacie i lokalizacji). *

## Generowanie grafiku *
- `POST /schedule/generate-day` – uruchamia solver i buduje grafik na jeden dzień. *
- `POST /schedule/generate-range` – jak wyżej, ale dla zakresu dat. *
- `POST /schedule/auto-generate` – układa grafik dla zakresu dat na podstawie zapisanych dyspozycyjności i szablonów; opcjonalnie wysyła powiadomienia mailowe do pracowników.

## Grafik i zmiany
- `GET /schedule/days/{day}` – szybki podgląd zmian zaplanowanych na konkretny dzień (zwraca również dane zapisane wcześniej przez solver).
- `GET /schedule/{demand_id}` – wymusza wygenerowanie i zwraca wszystkie zmiany dla wskazanego zapotrzebowania.
- `GET /schedule/{demand_id}/day/{day}` – zwraca tylko zmiany z danego grafiku dla jednej daty.
- `GET /schedule/shift/{shift_id}` – szczegóły pojedynczej zmiany.
- `POST /schedule/shift` – aktualizuje istniejącą zmianę (np. przypisanie pracowników).
- `POST /schedule/shift/{shift_id}/approve` – zatwierdza zmianę przez managera/właściciela i wysyła powiadomienia do pracowników.
- `POST /schedule/shift-transfer` – zgłoszenie oddania/przejęcia zmiany przez pracownika (oczekuje na akceptację managera).
- `POST /schedule/shift-transfer/{request_id}/approve` – akceptuje zgłoszenie zmiany obsady i aktualizuje grafik.
- `POST /schedule/shift-transfer/{request_id}/reject` – odrzuca zgłoszenie zmiany obsady.

`*` – funkcje bardziej złożone, prawdopodobnie do uproszczenia lub usunięcia, jeżeli nie są potrzebne w podstawowej konfiguracji.

## Role i doświadczenie
- `GET /schedule/roles` – lista ról dostępnych w firmie (np. Barista, Kierownik sali) wraz z informacją czy wymagają doświadczenia.
- `POST /schedule/roles` – dodaje lub aktualizuje rolę (wymagane uprawnienia managera/właściciela).
- `GET /schedule/roles/assignments` – aktywne przypisania ról do pracowników.
- `POST /schedule/roles/assign` – przypisuje lub dezaktywuje rolę dla pracownika.
