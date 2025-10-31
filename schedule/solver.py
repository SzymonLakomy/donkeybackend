from __future__ import annotations
from typing import List, Dict, Any, Tuple
from ortools.sat.python import cp_model
import itertools

SLICE_MIN = 30


def _to_minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return not (a_end <= b_start or b_end <= a_start)


def _contains(slot_start, slot_end, s_start, s_end) -> bool:
    return slot_start <= s_start and s_end <= slot_end


def _shift_key(sh: dict) -> str:
    return f"{sh['date']}|{sh['location']}|{sh['start']}-{sh['end']}"


def run_solver(emp_availability: List[Dict[str, Any]], demand: List[Dict[str, Any]],
               time_limit_sec: float = 10.0, workers: int = 8) -> Dict[str, Any]:
    """
    Compute schedule using the same logic as schedule/or_tools_test.py but as a pure function.

    Inputs must match donkey_ai JSON structures:
    - emp_availability: list of dicts with keys: employee_id, date, experienced, hours_min, hours_max, available_slots
    - demand: list of dicts with keys: date, location, start, end, demand, needs_experienced (optional)

    Returns dict with keys:
    - assignments: list[dict] with keys: date, location, start, end, demand, assigned_employees, needs_experienced, missing_minutes
    - uncovered: list[dict]
    - hours_summary: list[dict]
    """
    # Prepare original shifts
    orig_shifts: List[Dict[str, Any]] = []
    for sh in demand:
        s = dict(sh)
        s["start_min"] = _to_minutes(s["start"])
        s["end_min"] = _to_minutes(s["end"])
        s["dur_min"] = s["end_min"] - s["start_min"]
        s["id"] = _shift_key(s)
        s["needs_experienced"] = bool(s.get("needs_experienced", False))
        orig_shifts.append(s)

    # Build slices (30 min)
    slices: List[Dict[str, Any]] = []
    slices_by_orig: Dict[str, List[Dict[str, Any]]] = {}
    for s in orig_shifts:
        t = s["start_min"]
        while t < s["end_min"]:
            t2 = min(t + SLICE_MIN, s["end_min"])
            sl = {
                "date": s["date"],
                "location": s["location"],
                "start": f"{t//60:02d}:{t%60:02d}",
                "end": f"{t2//60:02d}:{t2%60:02d}",
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
    emps: List[str] = []
    employees: Dict[str, Dict[str, Any]] = {}
    availability: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
    preassign_orig: Dict[Tuple[str, str], bool] = {}

    # Aggregate availability per employee/date; keep tightest hour bounds
    for rec in emp_availability:
        emp = rec["employee_id"]
        if emp not in employees:
            employees[emp] = {
                "experienced": bool(rec.get("experienced", False)),
                "hours_min": int(rec.get("hours_min", 0)),
                "hours_max": int(rec.get("hours_max", 10**9)),
            }
        employees[emp]["hours_min"] = max(employees[emp]["hours_min"], int(rec.get("hours_min", 0)))
        employees[emp]["hours_max"] = min(employees[emp]["hours_max"], int(rec.get("hours_max", 10**9)))

        key = (emp, rec["date"])  # (employee_id, date)
        availability.setdefault(key, [])
        for slot in rec.get("available_slots", []) or []:
            try:
                availability[key].append((_to_minutes(slot["start"]), _to_minutes(slot["end"])))
            except Exception:
                continue

        # Handle preassigned confirmed shift if present and identical to an original shift
        asg = (rec.get("assigned_shift") or {})
        if isinstance(asg, dict) and asg.get("confirmed", False):
            asg_norm = {
                "date": rec["date"],
                "location": asg.get("location"),
                "start": asg.get("start"),
                "end": asg.get("end"),
            }
            for s0 in orig_shifts:
                if (
                    s0["date"] == asg_norm["date"]
                    and s0["location"] == asg_norm["location"]
                    and s0["start"] == asg_norm["start"]
                    and s0["end"] == asg_norm["end"]
                ):
                    preassign_orig[(emp, s0["id"])] = True

    emps = sorted(employees.keys())
    shift_ids = [s["id"] for s in shifts]
    idx_by_id = {s["id"]: i for i, s in enumerate(shifts)}

    # Expand preassign to slices
    preassign_slices: Dict[Tuple[str, str], bool] = {}
    for (e, orig_id), val in list(preassign_orig.items()):
        if val:
            for sl in slices_by_orig.get(orig_id, []):
                preassign_slices[(e, sl["id"])] = True

    # Allowed matrix
    allowed: Dict[Tuple[str, str], bool] = {(e, s["id"]): False for e in emps for s in shifts}
    for e in emps:
        for s in shifts:
            slots = availability.get((e, s["date"]), [])
            ok = any(_contains(a, b, s["start_min"], s["end_min"]) for (a, b) in slots)
            if preassign_slices.get((e, s["id"])):
                ok = True
            allowed[(e, s["id"])] = ok

    # Build CP-SAT model — identical constraints as in or_tools_test
    m = cp_model.CpModel()

    # decision x[e,s]
    x: Dict[Tuple[str, str], Any] = {}
    for e in emps:
        for s in shifts:
            if allowed[(e, s["id"])]:
                x[(e, s["id"])] = m.NewBoolVar(f"x_{e}_{s['id']}")
            else:
                x[(e, s["id"])] = m.NewConstant(0)

    # Preassign slices
    for (e, sid), val in preassign_slices.items():
        if val:
            m.Add(x[(e, sid)] == 1)

    # per-original shift selection
    allowed_orig: Dict[Tuple[str, str], bool] = {}
    for e in emps:
        for s0 in orig_shifts:
            orig_id = s0["id"]
            any_ok = preassign_orig.get((e, orig_id), False)
            if not any_ok:
                for sl in slices_by_orig.get(orig_id, []):
                    if allowed[(e, sl["id"])]:
                        any_ok = True
                        break
            allowed_orig[(e, orig_id)] = any_ok

    y: Dict[Tuple[str, str], Any] = {}
    for e in emps:
        for s0 in orig_shifts:
            orig_id = s0["id"]
            if allowed_orig[(e, orig_id)]:
                y[(e, orig_id)] = m.NewBoolVar(f"y_{e}_{orig_id}")
            else:
                y[(e, orig_id)] = m.NewConstant(0)

    # Preassign y for confirmed originals
    for (e, orig_id), val in preassign_orig.items():
        if val:
            m.Add(y[(e, orig_id)] == 1)

    # At most demand distinct employees for an original shift
    for s0 in orig_shifts:
        orig_id = s0["id"]
        m.Add(sum(y[(e, orig_id)] for e in emps) <= int(s0["demand"]))

    # Link x and y
    for e in emps:
        for sl in slices:
            m.Add(x[(e, sl["id"])] <= y[(e, sl["orig_id"])])

    # Tighten: if selected for original, must cover >= 1 of its slices
    for e in emps:
        for s0 in orig_shifts:
            orig_id = s0["id"]
            sl_list = slices_by_orig.get(orig_id, [])
            if sl_list:
                m.Add(sum(x[(e, sl["id"])] for sl in sl_list) >= y[(e, orig_id)])

    # Coverage per slice; experienced when staffed
    under: Dict[str, Any] = {}
    is_staffed: Dict[str, Any] = {}
    for s in shifts:
        sid = s["id"]
        assigned_sum = sum(x[(e, sid)] for e in emps)
        m.Add(assigned_sum <= int(s["demand"]))
        under[sid] = m.NewIntVar(0, int(s["demand"]), f"under_{sid}")
        m.Add(under[sid] >= int(s["demand"]) - assigned_sum)
        is_staffed[sid] = m.NewBoolVar(f"staffed_{sid}")
        m.Add(assigned_sum >= is_staffed[sid])
        if s.get("needs_experienced", False):
            exp_sum = sum(x[(e, sid)] for e in emps if employees[e]["experienced"] and allowed[(e, sid)])
            m.Add(exp_sum >= is_staffed[sid])

    # No overlapping per employee per day
    for e in emps:
        by_date: Dict[str, List[Dict[str, Any]]] = {}
        for s in shifts:
            by_date.setdefault(s["date"], []).append(s)
        for date, slist in by_date.items():
            for s1, s2 in itertools.combinations(slist, 2):
                if _overlaps(s1["start_min"], s1["end_min"], s2["start_min"], s2["end_min"]):
                    m.Add(x[(e, s1["id"]) ] + x[(e, s2["id"]) ] <= 1)

    # Hours per employee
    over: Dict[str, Any] = {}
    under_hours: Dict[str, Any] = {}
    for e in emps:
        tot = m.NewIntVar(0, 7*24*60, f"totmin_{e}")
        m.Add(tot == sum(shifts[idx_by_id[sid]]["dur_min"] * x[(e, sid)] for sid in shift_ids))
        max_min = int(employees[e]["hours_max"]) * 60
        min_min = int(employees[e]["hours_min"]) * 60
        over[e] = m.NewIntVar(0, 7*24*60, f"over_{e}")
        under_hours[e] = m.NewIntVar(0, 7*24*60, f"underh_{e}")
        m.Add(tot <= max_min + over[e])
        m.Add(tot + under_hours[e] >= min_min)

    # Objective
    obj = []
    obj += [1000 * under[sid] for sid in shift_ids]
    obj += [10 * over[e] for e in emps]
    obj += [1 * under_hours[e] for e in emps]
    m.Minimize(sum(obj))

    # Solve
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_sec)
    solver.parameters.num_search_workers = int(workers)
    status = solver.Solve(m)

    # Build result
    result: Dict[str, Any] = {"assignments": [], "uncovered": [], "hours_summary": []}

    for s0 in orig_shifts:
        sid0 = s0["id"]
        sl_list = slices_by_orig.get(sid0, [])
        assigned_set = set()
        covered_person_min = 0
        for sl in sl_list:
            sid = sl["id"]
            assigned_here = [e for e in emps if solver.Value(x[(e, sid)]) == 1]
            for e in assigned_here:
                assigned_set.add(e)
            covered_person_min += min(len(assigned_here), int(s0["demand"])) * sl["dur_min"]
        total_needed_min = s0["dur_min"] * int(s0["demand"])
        missing_minutes = max(0, total_needed_min - covered_person_min)
        result["assignments"].append({
            "date": s0["date"],
            "location": s0["location"],
            "start": s0["start"],
            "end": s0["end"],
            "demand": int(s0["demand"]),
            "assigned_employees": sorted(list(assigned_set)),
            "needs_experienced": bool(s0.get("needs_experienced", False)),
            "missing_minutes": int(missing_minutes),
        })
        if missing_minutes > 0:
            result["uncovered"].append({"shift_id": sid0, "missing_minutes": int(missing_minutes)})

    for e in emps:
        tot_minutes = 0
        for s in shifts:
            if solver.Value(x[(e, s["id"])]) == 1:
                tot_minutes += s["dur_min"]
        result["hours_summary"].append({
            "employee_id": e,
            "experienced": bool(employees[e]["experienced"]),
            "total_hours": round(tot_minutes / 60.0, 2),
            "hours_min": int(employees[e]["hours_min"]),
            "hours_max": int(employees[e]["hours_max"]),
            "over_hours": solver.Value(over[e]) / 60.0,
            "under_hours": solver.Value(under_hours[e]) / 60.0,
        })

    return result
