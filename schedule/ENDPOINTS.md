# Schedule API – szybki przegląd

_Podstawowy prefiks wszystkich ścieżek to `/schedule`._

## Lokalizacje
- `GET /schedule/locations` – zwraca listę nazw lokalizacji przypisanych do firmy zalogowanego użytkownika. Tylko te lokalizacje, które należą do danej firmy.

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

## Grafik i zmiany
- `GET /schedule/days/{day}` – szybki podgląd zmian zaplanowanych na konkretny dzień (zwraca również dane zapisane wcześniej przez solver).
- `GET /schedule/{demand_id}` – wymusza wygenerowanie i zwraca wszystkie zmiany dla wskazanego zapotrzebowania.
- `GET /schedule/{demand_id}/day/{day}` – zwraca tylko zmiany z danego grafiku dla jednej daty.
- `GET /schedule/shift/{shift_id}` – szczegóły pojedynczej zmiany.
- `POST /schedule/shift` – aktualizuje istniejącą zmianę (np. przypisanie pracowników).

`*` – funkcje bardziej złożone, prawdopodobnie do uproszczenia lub usunięcia, jeżeli nie są potrzebne w podstawowej konfiguracji.
