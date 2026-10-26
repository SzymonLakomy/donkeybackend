from __future__ import annotations

from collections import defaultdict
from datetime import date as date_type
from typing import Any, Dict, List, Optional, Set, Tuple

import itertools
import time
from ortools.sat.python import cp_model

SLICE_MIN = 30

# =============================================================================
# Opcje modelu. Wszystkie mozna nadpisac przez argument `options` w run_solver().
# Wartosci domyslne = reguly obowiazujace w gastronomii (PL).
# =============================================================================
DEFAULT_OPTIONS: Dict[str, Any] = {
    # --- reguly twarde (struktura grafiku) ---
    "min_shift_minutes": 180,      # minimalny ciagly blok pracy; 0 = brak limitu
    "no_split_shifts": True,       # zakaz dwoch nieprzylegajacych zmian w jednym dniu
    "min_rest_minutes": 660,       # 11h odpoczynku miedzy kolejnymi dniami
    "one_location_per_day": True,  # zakaz pracy w dwoch lokalach tego samego dnia
    # --- godziny ---
    "weekly_hours": True,          # limity hours_min/max per tydzien (pro-rata)
    # --- wagi funkcji celu ---
    "understaff_penalty": 1000,    # za osobe-plaster (30 min) ponizej zapotrzebowania
    "overtime_penalty": 10,        # za minute powyzej hours_max
    "under_hours_penalty": 1,      # za minute ponizej hours_min
    "experience_penalty": 400,     # za zmiane "needs_experienced" bez seniora
    "fairness_weight": 1,          # za minute rozrzutu godzin (max-min) w zespole
    "weekend_weight": 60,          # za jednostke nierownosci weekendow
    # --- stabilnosc wzgledem poprzedniego grafiku ---
    "stability_keep_bonus": 150,   # bonus za utrzymanie wczesniejszego przypisania
    "stability_add_penalty": 5,    # kara za nowe przypisanie (anty-chaos)
    # --- solver ---
    "seed": 42,
    "deterministic": True,         # 1 worker + staly seed => powtarzalny wynik
}

_STATUS_NAMES = {
    cp_model.OPTIMAL: "OPTIMAL",
    cp_model.FEASIBLE: "FEASIBLE",
    cp_model.INFEASIBLE: "INFEASIBLE",
    cp_model.MODEL_INVALID: "MODEL_INVALID",
    cp_model.UNKNOWN: "UNKNOWN",
}


class SolverError(RuntimeError):
    """Solver nie zwrocil poprawnego rozwiazania (INFEASIBLE/UNKNOWN/MODEL_INVALID)."""

    def __init__(self, status: str, message: str = ""):
        self.status = status
        super().__init__(message or f"Solver zakonczyl prace ze statusem {status}")


# =============================================================================
# Pomocnicze
# =============================================================================

def _to_minutes(hhmm: str) -> int:
    h, m = map(int, str(hhmm).split(":"))
    return h * 60 + m


def _to_hhmm(total_minutes: int) -> str:
    total_minutes = max(0, int(total_minutes))
    h = (total_minutes // 60) % 24
    m = total_minutes % 60
    return f"{h:02d}:{m:02d}"


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return not (a_end <= b_start or b_end <= a_start)


def _contains(slot_start, slot_end, s_start, s_end) -> bool:
    return slot_start <= s_start and s_end <= slot_end


def _shift_key(sh: dict) -> str:
    return f"{sh['date']}|{sh['location']}|{sh['start']}-{sh['end']}"


def _shift_bounds(start: str, end: str) -> Tuple[int, int]:
    """Minuty od poczatku doby; zmiana konczaca sie '00:00' dostaje 24:00."""
    s = _to_minutes(start)
    e = _to_minutes(end)
    if e <= s:
        e += 24 * 60
    return s, e


def _week_key(day: str) -> Tuple[int, int]:
    y, w, _ = date_type.fromisoformat(day).isocalendar()
    return (int(y), int(w))


def _is_weekend(day: str) -> bool:
    return date_type.fromisoformat(day).weekday() >= 5


def _normalize_options(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    opts = dict(DEFAULT_OPTIONS)
    if options:
        unknown = set(options) - set(DEFAULT_OPTIONS)
        if unknown:
            raise ValueError(f"Nieznane opcje solvera: {sorted(unknown)}")
        opts.update(options)
    return opts


# =============================================================================
# Model
# =============================================================================

def run_solver(emp_availability: List[Dict[str, Any]],
               demand: List[Dict[str, Any]],
               time_limit_sec: float = 10.0,
               workers: Optional[int] = None,
               options: Optional[Dict[str, Any]] = None,
               previous: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Uklada grafik przy pomocy CP-SAT.

    Wejscia (jak dotad):
    - emp_availability: employee_id, date, experienced, hours_min, hours_max, available_slots
    - demand: date, location, start, end, demand, needs_experienced

    Nowe argumenty:
    - options: nadpisania DEFAULT_OPTIONS (patrz slownik na gorze pliku)
    - previous: poprzedni grafik (lista dictow z date/location/start/end/assigned_employees)
      -> model premiuje utrzymanie istniejacych przypisan (stabilnosc)

    Wynik: dict z assignments / uncovered / hours_summary + status i metrics.
    Rzuca SolverError, gdy solver nie znajdzie zadnego dopuszczalnego rozwiazania.
    """
    opts = _normalize_options(options)
    if workers is None:
        workers = 1 if opts["deterministic"] else 8

    # ---------- normalizacja zapotrzebowania ----------
    orig_shifts: List[Dict[str, Any]] = []
    for sh in demand:
        s = dict(sh)
        s["start_min"], s["end_min"] = _shift_bounds(s["start"], s["end"])
        s["dur_min"] = s["end_min"] - s["start_min"]
        s["id"] = _shift_key(s)
        s["needs_experienced"] = bool(s.get("needs_experienced", False))
        orig_shifts.append(s)

    if not orig_shifts:
        return {
            "assignments": [],
            "uncovered": [],
            "hours_summary": [],
            "status": "OPTIMAL",
            "metrics": {"coverage_pct": 100.0, "assigned_minutes": 0,
                        "required_minutes": 0, "unassigned_shifts": 0,
                        "experience_violations": 0},
        }

    # ---------- plastry 30-min ----------
    slices: List[Dict[str, Any]] = []
    slices_by_orig: Dict[str, List[Dict[str, Any]]] = {}
    for s in orig_shifts:
        t = s["start_min"]
        while t < s["end_min"]:
            t2 = min(t + SLICE_MIN, s["end_min"])
            sl = {
                "date": s["date"],
                "location": s["location"],
                "start": _to_hhmm(t),
                "end": _to_hhmm(t2),
                "start_min": t,
                "end_min": t2,
                "dur_min": t2 - t,
                "demand": int(s["demand"]),
                "needs_experienced": s["needs_experienced"],
                "orig_id": s["id"],
            }
            sl["id"] = f"{s['id']}#{t}-{t2}"
            slices.append(sl)
            slices_by_orig.setdefault(s["id"], []).append(sl)
            t = t2

    shifts = slices
    shift_ids = [s["id"] for s in shifts]

    # ---------- pracownicy ----------
    employees: Dict[str, Dict[str, Any]] = {}
    employee_names: Dict[str, str] = {}
    availability: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
    preassign_orig: Dict[Tuple[str, str], bool] = {}

    for rec in emp_availability:
        emp = str(rec["employee_id"])
        if emp not in employees:
            employees[emp] = {
                "experienced": bool(rec.get("experienced", False)),
                "hours_min": int(rec.get("hours_min", 0) or 0),
                "hours_max": int(rec.get("hours_max", 10 ** 9) or 10 ** 9),
            }
        if rec.get("employee_name") is not None:
            employee_names[emp] = rec.get("employee_name") or ""
        employees[emp]["hours_min"] = max(employees[emp]["hours_min"], int(rec.get("hours_min", 0) or 0))
        employees[emp]["hours_max"] = min(employees[emp]["hours_max"], int(rec.get("hours_max", 10 ** 9) or 10 ** 9))

        key = (emp, rec["date"])
        availability.setdefault(key, [])
        for slot in rec.get("available_slots", []) or []:
            try:
                _s, _e = _shift_bounds(slot["start"], slot["end"])
                availability[key].append((_s, _e))
            except Exception:
                continue

        asg = rec.get("assigned_shift") or {}
        if isinstance(asg, dict) and asg.get("confirmed", False):
            for s0 in orig_shifts:
                if (s0["date"] == rec["date"]
                        and s0["location"] == asg.get("location")
                        and s0["start"] == asg.get("start")
                        and s0["end"] == asg.get("end")):
                    preassign_orig[(emp, s0["id"])] = True

    emps = sorted(employees.keys())

    preassign_slices: Dict[Tuple[str, str], bool] = {}
    for (e, orig_id), val in list(preassign_orig.items()):
        if val:
            for sl in slices_by_orig.get(orig_id, []):
                preassign_slices[(e, sl["id"])] = True

    # macierz dozwolonych przypisan
    allowed: Dict[Tuple[str, str], bool] = {(e, s["id"]): False for e in emps for s in shifts}
    for e in emps:
        for s in shifts:
            slots = availability.get((e, s["date"]), [])
            ok = any(_contains(a, b, s["start_min"], s["end_min"]) for (a, b) in slots)
            if preassign_slices.get((e, s["id"])):
                ok = True
            allowed[(e, s["id"])] = ok

    m = cp_model.CpModel()

    # ---------- zmienne decyzyjne ----------
    x: Dict[Tuple[str, str], Any] = {}
    x_vars: Dict[Tuple[str, str], Any] = {}
    x_vars_by_emp: Dict[str, List[Tuple[Any, int, str]]] = defaultdict(list)
    for e in emps:
        for s in shifts:
            if allowed[(e, s["id"])]:
                v = m.NewBoolVar(f"x_{e}_{s['id']}")
                x[(e, s["id"])] = v
                x_vars[(e, s["id"])] = v
                x_vars_by_emp[e].append((v, int(s["dur_min"]), s["date"]))
            else:
                x[(e, s["id"])] = m.NewConstant(0)

    allowed_orig: Dict[Tuple[str, str], bool] = {}
    for e in emps:
        for s0 in orig_shifts:
            orig_id = s0["id"]
            ok = preassign_orig.get((e, orig_id), False)
            if not ok:
                ok = any(allowed[(e, sl["id"])] for sl in slices_by_orig.get(orig_id, []))
            allowed_orig[(e, orig_id)] = ok

    y: Dict[Tuple[str, str], Any] = {}
    y_vars: Dict[Tuple[str, str], Any] = {}
    for e in emps:
        for s0 in orig_shifts:
            orig_id = s0["id"]
            if allowed_orig[(e, orig_id)]:
                v = m.NewBoolVar(f"y_{e}_{orig_id}")
                y[(e, orig_id)] = v
                y_vars[(e, orig_id)] = v
            else:
                y[(e, orig_id)] = m.NewConstant(0)

    # potwierdzone zmiany: wymuszone
    for (e, sid), val in preassign_slices.items():
        if val:
            m.Add(x[(e, sid)] == 1)
    for (e, orig_id), val in preassign_orig.items():
        if val:
            m.Add(y[(e, orig_id)] == 1)

    # ---------- 1. ciaglosc i minimalna dlugosc bloku w ramach zmiany ----------
    # x[t] == licznik_startow(t) - licznik_koncow(t) => dokladnie jeden spojny blok
    min_slices = max(1, int(round(float(opts["min_shift_minutes"]) / SLICE_MIN)))
    for e in emps:
        for s0 in orig_shifts:
            orig_id = s0["id"]
            if (e, orig_id) not in y_vars:
                continue
            yv = y_vars[(e, orig_id)]
            sl_list = slices_by_orig.get(orig_id, [])
            n = len(sl_list)
            if n == 0:
                m.Add(yv == 0)
                continue
            ms = max(1, min(min_slices, n))

            starts = {t: m.NewBoolVar(f"st_{e}_{orig_id}_{t}") for t in range(0, n - ms + 1)}
            ends = {t: m.NewBoolVar(f"en_{e}_{orig_id}_{t}") for t in range(ms, n + 1)}
            m.Add(sum(starts.values()) == yv)
            m.Add(sum(ends.values()) == yv)

            cum_starts = 0
            cum_ends = 0
            for t, sl in enumerate(sl_list):
                if t in starts:
                    cum_starts = cum_starts + starts[t]
                if t in ends:
                    cum_ends = cum_ends + ends[t]
                m.Add(x[(e, sl["id"])] == cum_starts - cum_ends)

            # minimalna dlgugosc bloku: start w t => co najmniej ms plastrow od t
            for t, sv in starts.items():
                window = sl_list[t:t + ms]
                m.Add(sum(x[(e, sl["id"])] for sl in window) >= ms * sv)

    # co najwyzej `demand` roznych osob na jedna zmiane
    for s0 in orig_shifts:
        orig_id = s0["id"]
        m.Add(sum(y[(e, orig_id)] for e in emps) <= int(s0["demand"]))

    # ---------- 2. spojnosc x <-> y ----------
    for e in emps:
        for sl in slices:
            m.Add(x[(e, sl["id"])] <= y[(e, sl["orig_id"])])
    for e in emps:
        for s0 in orig_shifts:
            orig_id = s0["id"]
            sl_list = slices_by_orig.get(orig_id, [])
            if sl_list:
                m.Add(sum(x[(e, sl["id"])] for sl in sl_list) >= y[(e, orig_id)])

    # ---------- 3. pokrycie i doswiadczenie ----------
    under: Dict[str, Any] = {}
    is_staffed: Dict[str, Any] = {}
    exp_viol: Dict[str, Any] = {}
    for s in shifts:
        sid = s["id"]
        assigned_sum = sum(x[(e, sid)] for e in emps)
        m.Add(assigned_sum <= int(s["demand"]))
        under[sid] = m.NewIntVar(0, int(s["demand"]), f"under_{sid}")
        m.Add(under[sid] >= int(s["demand"]) - assigned_sum)

        staffed = m.NewBoolVar(f"staffed_{sid}")
        m.Add(assigned_sum >= staffed)
        m.Add(assigned_sum <= int(s["demand"]) * staffed)  # staffed == (assigned_sum > 0)
        is_staffed[sid] = staffed

        if s.get("needs_experienced", False):
            exp_sum = sum(x[(e, sid)] for e in emps
                          if employees[e]["experienced"] and allowed[(e, sid)])
            viol = m.NewBoolVar(f"expviol_{sid}")
            m.Add(viol >= staffed - exp_sum)  # kara tylko gdy zmiana obsadzona bez seniora
            exp_viol[sid] = viol

    # ---------- 4. zakaz nakladek ----------
    for e in emps:
        by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for s in shifts:
            by_date[s["date"]].append(s)
        for _date, slist in by_date.items():
            for s1, s2 in itertools.combinations(slist, 2):
                if _overlaps(s1["start_min"], s1["end_min"], s2["start_min"], s2["end_min"]):
                    m.Add(x[(e, s1["id"])] + x[(e, s2["id"])] <= 1)

    # ---------- 5. reguly dzienne: brak dzielonych zmian / jeden lokal ----------
    shifts_by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s0 in orig_shifts:
        shifts_by_day[s0["date"]].append(s0)

    if opts["no_split_shifts"] or opts["one_location_per_day"]:
        for e in emps:
            for day, day_shifts in shifts_by_day.items():
                cand = [s0 for s0 in day_shifts if (e, s0["id"]) in y_vars]
                for s1, s2 in itertools.combinations(sorted(cand, key=lambda z: z["start_min"]), 2):
                    same_location = s1["location"] == s2["location"]
                    if opts["one_location_per_day"] and not same_location:
                        m.Add(y[(e, s1["id"])] + y[(e, s2["id"])] <= 1)
                        continue
                    if not opts["no_split_shifts"] or not same_location:
                        continue
                    gap = s2["start_min"] - s1["end_min"]
                    if gap > 0:
                        m.Add(y[(e, s1["id"])] + y[(e, s2["id"])] <= 1)

    # ---------- 6. odpoczynek miedzy dniami ----------
    rest_min = int(opts["min_rest_minutes"])
    if rest_min > 0:
        by_emp_date: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for e in emps:
            for s0 in orig_shifts:
                if (e, s0["id"]) in y_vars:
                    by_emp_date[e][s0["date"]].append(s0)
        for e, per_day in by_emp_date.items():
            days = sorted(per_day)
            for d1, d2 in zip(days, days[1:]):
                if (date_type.fromisoformat(d2) - date_type.fromisoformat(d1)).days != 1:
                    continue
                for s1 in per_day[d1]:
                    for s2 in per_day[d2]:
                        gap = (24 * 60 - s1["end_min"]) + s2["start_min"]
                        if gap < rest_min:
                            m.Add(y[(e, s1["id"])] + y[(e, s2["id"])] <= 1)

    # ---------- 7. godziny: tygodniowo (pro-rata) albo dla calego zakresu ----------
    demand_days = sorted({s["date"] for s in orig_shifts})
    week_days: Dict[Tuple[int, int], Set[str]] = defaultdict(set)
    for d in demand_days:
        week_days[_week_key(d)].add(d)

    def _bucket(day: str):
        return _week_key(day) if opts["weekly_hours"] else ("ALL", 0)

    bucket_days: Dict[Any, int] = {}
    for d in demand_days:
        b = _bucket(d)
        bucket_days[b] = bucket_days.get(b, 0) + 1

    over_terms: List[Any] = []
    under_terms: List[Any] = []
    over_by_emp: Dict[str, List[Any]] = defaultdict(list)
    under_by_emp: Dict[str, List[Any]] = defaultdict(list)
    max_total_min = 31 * 24 * 60

    for e in emps:
        if not x_vars_by_emp[e]:
            continue
        per_bucket: Dict[Any, List[Tuple[Any, int]]] = defaultdict(list)
        for v, dur, day in x_vars_by_emp[e]:
            per_bucket[_bucket(day)].append((v, dur))

        h_max = int(employees[e]["hours_max"])
        h_min = int(employees[e]["hours_min"])
        for b, items in per_bucket.items():
            day_count = max(1, bucket_days.get(b, 7))
            tot = m.NewIntVar(0, max_total_min, f"tot_{e}_{b}")
            m.Add(tot == sum(dur * v for v, dur in items))

            if h_max < 10 ** 8:
                cap = int(round(h_max * 60 * day_count / 7))
                ov = m.NewIntVar(0, max_total_min, f"over_{e}_{b}")
                m.Add(ov >= tot - cap)
                over_terms.append(ov)
                over_by_emp[e].append(ov)
            if h_min > 0:
                floor_ = int(round(h_min * 60 * day_count / 7))
                un = m.NewIntVar(0, max_total_min, f"underh_{e}_{b}")
                m.Add(un >= floor_ - tot)
                under_terms.append(un)
                under_by_emp[e].append(un)

    # ---------- 8. faza 1: pokrycie + kontrakt (elementy twarde ekonomicznie) ----------
    base_obj: List[Any] = []
    base_obj += [int(opts["understaff_penalty"]) * under[sid] for sid in shift_ids]
    base_obj += [int(opts["overtime_penalty"]) * v for v in over_terms]
    base_obj += [int(opts["under_hours_penalty"]) * v for v in under_terms]
    base_obj += [int(opts["experience_penalty"]) * v for v in exp_viol.values()]

    # ---------- 9. faza 2: sprawiedliwosc, weekendy, stabilnosc ----------
    quality_obj: List[Any] = []
    total_hours_var: Dict[str, Any] = {}
    emps_with_work = [e for e in emps if x_vars_by_emp[e]]
    if len(emps_with_work) >= 2 and int(opts["fairness_weight"]) > 0:
        for e in emps_with_work:
            tot_all = m.NewIntVar(0, max_total_min, f"htot_{e}")
            m.Add(tot_all == sum(dur * v for v, dur, _d in x_vars_by_emp[e]))
            total_hours_var[e] = tot_all
        hmax = m.NewIntVar(0, max_total_min, "hmax")
        hmin = m.NewIntVar(0, max_total_min, "hmin")
        for e in emps_with_work:
            m.Add(hmax >= total_hours_var[e])
            m.Add(hmin <= total_hours_var[e])
        quality_obj.append(int(opts["fairness_weight"]) * (hmax - hmin))

    # ---------- 10. rotacja weekendow ----------
    weekend_days = [d for d in demand_days if _is_weekend(d)]
    if weekend_days and len(emps_with_work) >= 2 and int(opts["weekend_weight"]) > 0:
        wc_vars: List[Any] = []
        for e in emps_with_work:
            terms = []
            for day in weekend_days:
                day_y = [y[(e, s0["id"])] for s0 in shifts_by_day.get(day, [])
                         if (e, s0["id"]) in y_vars]
                if not day_y:
                    continue
                wd = m.NewBoolVar(f"wd_{e}_{day}")
                m.Add(wd <= sum(day_y))
                for yv in day_y:
                    m.Add(wd >= yv)
                terms.append(wd)
            if terms:
                wc = m.NewIntVar(0, len(terms), f"wc_{e}")
                m.Add(wc == sum(terms))
                wc_vars.append(wc)
        if len(wc_vars) >= 2:
            wmax = m.NewIntVar(0, len(weekend_days), "wmax")
            wmin = m.NewIntVar(0, len(weekend_days), "wmin")
            for v in wc_vars:
                m.Add(wmax >= v)
                m.Add(wmin <= v)
            quality_obj.append(int(opts["weekend_weight"]) * (wmax - wmin))

    # ---------- 11. stabilnosc wzgledem poprzedniego grafiku ----------
    if previous:
        prev_keys: Set[Tuple[str, str]] = set()
        for a in previous:
            key = _shift_key({
                "date": str(a.get("date")),
                "location": a.get("location"),
                "start": a.get("start"),
                "end": a.get("end"),
            })
            for emp in (a.get("assigned_employees") or []):
                prev_keys.add((str(emp), key))
        keep = int(opts["stability_keep_bonus"])
        add = int(opts["stability_add_penalty"])
        for (e, orig_id), yv in y_vars.items():
            if (e, orig_id) in prev_keys:
                quality_obj.append(-keep * yv)
            else:
                quality_obj.append(add * yv)

    # ---------- solve (leksykograficznie: najpierw pokrycie, potem jakosc) ----------
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = max(1, int(workers))
    solver.parameters.random_seed = int(opts["seed"])

    time_budget = max(0.5, float(time_limit_sec))
    started = time.monotonic()

    def _solve(seconds: float) -> Tuple[int, str]:
        solver.parameters.max_time_in_seconds = max(0.5, float(seconds))
        st = solver.Solve(m)
        return st, _STATUS_NAMES.get(st, str(st))

    m.Minimize(sum(base_obj))
    status, status_name = _solve(time_budget * (0.5 if quality_obj else 1.0))
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SolverError(status_name)

    if quality_obj:
        # zamroz osiagniete pokrycie/kontrakt, zeby faza 2 nie mogla go pogorszyc
        fixed_vars: List[Any] = [under[sid] for sid in shift_ids]
        fixed_vars += over_terms + under_terms + list(exp_viol.values())
        for v in fixed_vars:
            m.Add(v == solver.Value(v))
        # podpowiedz biezace przypisania jako punkt startowy
        for (e, sid), v in x_vars.items():
            m.AddHint(v, solver.Value(v))
        for (e, orig_id), v in y_vars.items():
            m.AddHint(v, solver.Value(v))

        m.Minimize(sum(quality_obj))
        remaining = time_budget - (time.monotonic() - started)
        st2, name2 = _solve(remaining)
        if st2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            status, status_name = st2, name2
        else:
            # awaryjnie wroc do rozwiazania z fazy 1
            m.Minimize(sum(base_obj))
            st3, name3 = _solve(time_budget - (time.monotonic() - started))
            if st3 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                raise SolverError(name3)
            status, status_name = st3, name3


    # ---------- wynik ----------
    result: Dict[str, Any] = {"assignments": [], "uncovered": [], "hours_summary": []}

    total_needed = 0
    covered_person_min = 0
    unassigned_shifts = 0
    experience_violations = 0

    for s0 in orig_shifts:
        sid0 = s0["id"]
        sl_list = slices_by_orig.get(sid0, [])
        assigned_set: Set[str] = set()
        per_emp_slices: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
        missing_slices: List[Tuple[int, int, int]] = []
        shift_violated = False
        covered_shift_min = 0

        for sl in sl_list:
            sid = sl["id"]
            assigned_here = [e for e in emps if solver.Value(x[(e, sid)]) == 1]
            for e in assigned_here:
                assigned_set.add(e)
                per_emp_slices[e].append((sl["start_min"], sl["end_min"]))
            covered_slice = min(len(assigned_here), int(s0["demand"])) * sl["dur_min"]
            covered_shift_min += covered_slice
            covered_person_min += covered_slice
            missing_count = max(0, int(s0["demand"]) - len(assigned_here))
            if missing_count > 0:
                missing_slices.append((sl["start_min"], sl["end_min"], missing_count))
            if sid in exp_viol and solver.Value(exp_viol[sid]) == 1:
                shift_violated = True

        total_needed_min = s0["dur_min"] * int(s0["demand"])
        total_needed += total_needed_min
        missing_minutes = max(0, total_needed_min - covered_shift_min)

        assigned_details: List[Dict[str, Any]] = []
        for emp in sorted(per_emp_slices.keys()):
            segments_raw = sorted(per_emp_slices[emp])
            merged: List[List[int]] = []
            for start_min, end_min in segments_raw:
                if not merged:
                    merged.append([start_min, end_min])
                    continue
                prev_start, prev_end = merged[-1]
                if start_min <= prev_end:
                    merged[-1][1] = max(prev_end, end_min)
                else:
                    merged.append([start_min, end_min])

            total_emp_min = 0
            segments_fmt: List[Dict[str, Any]] = []
            for start_min, end_min in merged:
                dur = max(0, end_min - start_min)
                total_emp_min += dur
                segments_fmt.append({
                    "start": _to_hhmm(start_min),
                    "end": _to_hhmm(end_min),
                    "minutes": dur,
                })

            if segments_fmt:
                assigned_details.append({
                    "employee_id": emp,
                    "employee_name": employee_names.get(emp, ""),
                    "start": segments_fmt[0]["start"],
                    "end": segments_fmt[-1]["end"],
                    "minutes": total_emp_min,
                    "segments": segments_fmt,
                })

        missing_segments: List[Dict[str, Any]] = []
        for start_min, end_min, missing_count in sorted(missing_slices):
            if not missing_segments:
                missing_segments.append({
                    "start_min": start_min, "end_min": end_min,
                    "missing": missing_count,
                    "missing_minutes": (end_min - start_min) * missing_count,
                })
                continue
            prev = missing_segments[-1]
            if prev["missing"] == missing_count and prev["end_min"] == start_min:
                prev["end_min"] = end_min
                prev["missing_minutes"] += (end_min - start_min) * missing_count
            else:
                missing_segments.append({
                    "start_min": start_min, "end_min": end_min,
                    "missing": missing_count,
                    "missing_minutes": (end_min - start_min) * missing_count,
                })

        for seg in missing_segments:
            seg["start"] = _to_hhmm(seg.pop("start_min"))
            seg["end"] = _to_hhmm(seg.pop("end_min"))

        if not assigned_set:
            unassigned_shifts += 1
        if shift_violated:
            experience_violations += 1

        result["assignments"].append({
            "date": s0["date"],
            "location": s0["location"],
            "start": s0["start"],
            "end": s0["end"],
            "demand": int(s0["demand"]),
            "assigned_employees": sorted(assigned_set),
            "needs_experienced": bool(s0.get("needs_experienced", False)),
            "missing_minutes": int(missing_minutes),
            "assigned_employees_detail": assigned_details,
            "missing_segments": missing_segments,
            "experience_violated": bool(shift_violated),
        })
        if missing_minutes > 0:
            result["uncovered"].append({
                "shift_id": sid0,
                "missing_minutes": int(missing_minutes),
                "missing_segments": missing_segments,
            })

    for e in emps:
        tot_minutes = 0
        for v, dur, _d in x_vars_by_emp[e]:
            if solver.Value(v) == 1:
                tot_minutes += dur
        result["hours_summary"].append({
            "employee_id": e,
            "experienced": bool(employees[e]["experienced"]),
            "total_hours": round(tot_minutes / 60.0, 2),
            "hours_min": int(employees[e]["hours_min"]),
            "hours_max": int(employees[e]["hours_max"]),
            "over_hours": round(sum(solver.Value(v) for v in over_by_emp.get(e, [])) / 60.0, 2),
            "under_hours": round(sum(solver.Value(v) for v in under_by_emp.get(e, [])) / 60.0, 2),
            "weekend_days": sum(
                1 for day in weekend_days
                if any(solver.Value(y[(e, s0["id"])]) == 1
                       for s0 in shifts_by_day.get(day, []) if (e, s0["id"]) in y_vars)
            ),
        })

    result["status"] = status_name
    result["metrics"] = {
        "coverage_pct": round(100.0 * covered_person_min / total_needed, 2) if total_needed else 100.0,
        "assigned_minutes": int(covered_person_min),
        "required_minutes": int(total_needed),
        "unassigned_shifts": int(unassigned_shifts),
        "experience_violations": int(experience_violations),
        "objective": (solver.ObjectiveValue()
                      if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None),
    }
    return result
