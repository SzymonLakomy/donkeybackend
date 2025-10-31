import os
from ortools.sat.python import cp_model
from datetime import datetime, timedelta
import itertools
import json


def to_minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m

def overlaps(a_start, a_end, b_start, b_end) -> bool:
    return not (a_end <= b_start or b_end <= a_start)

def contains(slot_start, slot_end, s_start, s_end) -> bool:
    return slot_start <= s_start and s_end <= slot_end

def shift_key(sh):
    return f"{sh['date']}|{sh['location']}|{sh['start']}-{sh['end']}"

def same_shift(a, b):
    return a["date"] == b["date"] and a["location"] == b["location"] and a["start"] == b["start"] and a["end"] == b["end"]

# ====== Example data (paste your JSON instead if you want) ======
_BASE_DIR = os.path.dirname(__file__)
EMP_AVAIL_JSON = os.path.join(_BASE_DIR, "osiol_dost.json")
#DEMAND_A_JSON   = os.path.join(_BASE_DIR, "balanced_zapotrzebowanie.json")
DEMAND_A_JSON   = os.path.join(_BASE_DIR, "osiol_zap.json")
DEMAND_B_JSON   = os.path.join(_BASE_DIR, "hard_zapotrzebowanie.json")

# For quick demo, embed a tiny subset consistent with above:
USE_VARIANT = "A"  # "A" albo "B"

# ====== PROSTE WCZYTYWANIE Z PLIKÓW ======
def load_json_file(path):
    if not os.path.exists(path):
        raise SystemExit(f" Brak pliku: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f" Błąd JSON w pliku {path}: {e}")

EMP_AVAIL = load_json_file(EMP_AVAIL_JSON)
WEEK_DEMAND = load_json_file(DEMAND_A_JSON if USE_VARIANT == "A" else DEMAND_B_JSON)

# ====== POMOCNICZE ======
def to_minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m

def overlaps(a_start, a_end, b_start, b_end) -> bool:
    return not (a_end <= b_start or b_end <= a_start)

def contains(slot_start, slot_end, s_start, s_end) -> bool:
    return slot_start <= s_start and s_end <= slot_end

def shift_key(sh):
    return f"{sh['date']}|{sh['location']}|{sh['start']}-{sh['end']}"

def same_shift(a, b):
    return a["date"] == b["date"] and a["location"] == b["location"] and a["start"] == b["start"] and a["end"] == b["end"]

# ====== PREPROCESS ======
orig_shifts = []
for sh in WEEK_DEMAND:
    s = dict(sh)
    s["start_min"] = to_minutes(s["start"])
    s["end_min"] = to_minutes(s["end"])
    s["dur_min"] = s["end_min"] - s["start_min"]
    s["id"] = shift_key(s)
    orig_shifts.append(s)

# Build 30-min slices from original shifts
SLICE_MIN = 30
slices = []
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
            "demand": s["demand"],
            "needs_experienced": bool(s.get("needs_experienced", False)),
            "orig_id": s["id"],
        }
        sl["id"] = f"{s['id']}#{t}-{t2}"
        slices.append(sl)
        t = t2

# Use slices as shifts for the model
shifts = slices

# Index slices by original shift id for quick aggregation
slices_by_orig = {}
for sl in slices:
    slices_by_orig.setdefault(sl["orig_id"], []).append(sl)

employees = {}
availability = {}
preassign_orig = {}
preassign_slices = {}

for rec in EMP_AVAIL:
    emp = rec["employee_id"]
    if emp not in employees:
        employees[emp] = {
            "experienced": bool(rec["experienced"]),
            "hours_min": int(rec.get("hours_min", 0)),
            "hours_max": int(rec.get("hours_max", 10**9)),  # very large if missing
        }
    # keep tightest bounds if repeated
    employees[emp]["hours_min"] = max(employees[emp]["hours_min"], int(rec.get("hours_min", 0)))
    employees[emp]["hours_max"] = min(employees[emp]["hours_max"], int(rec.get("hours_max", 10**9)))

    key = (emp, rec["date"])
    availability.setdefault(key, [])
    for slot in rec.get("available_slots", []):
        availability[key].append((to_minutes(slot["start"]), to_minutes(slot["end"])))

    if "assigned_shift" in rec and rec["assigned_shift"].get("confirmed", False):
        asg = rec["assigned_shift"]
        asg_norm = {
            "date": rec["date"],
            "location": asg["location"],
            "start": asg["start"],
            "end": asg["end"]
        }
        # mark preassign if this shift exists in demand (original shifts)
        for s in orig_shifts:
            if same_shift(asg_norm, s):
                preassign_orig[(emp, s["id"])] = True

emps = sorted(employees.keys())
shift_ids = [s["id"] for s in shifts]
idx_by_id = {s["id"]: i for i, s in enumerate(shifts)}

# Expand preassign from original shifts into per-slice preassign
for (e, orig_id), val in list(preassign_orig.items()):
    if val:
        for sl in slices_by_orig.get(orig_id, []):
            preassign_slices[(e, sl["id"])] = True

# Allowed matrix (availability)
allowed = {(e, s["id"]): False for e in emps for s in shifts}
for e in emps:
    for s in shifts:
        slots = availability.get((e, s["date"]), [])
        ok = any(contains(a, b, s["start_min"], s["end_min"]) for (a, b) in slots)
        # If preassigned, allow anyway
        if preassign_slices.get((e, s["id"]), False):
            ok = True
        allowed[(e, s["id"])] = ok

# ====== Build CP-SAT model ======
m = cp_model.CpModel()

# x[e,s] in {0,1} assignment
x = {}
for e in emps:
    for s in shifts:
        if allowed[(e, s["id"])]:
            x[(e, s["id"])] = m.NewBoolVar(f"x_{e}_{s['id']}")
        else:
            x[(e, s["id"])] = m.NewConstant(0)

# Preassign fixed to 1 for slices
for (e, sid), val in preassign_slices.items():
    if val:
        m.Add(x[(e, sid)] == 1)

# New: per-original-shift selection variables (y) to limit distinct employees per original shift
# Build allowed_orig indicating if employee e can work any slice of original shift orig_id (or is preassigned)
allowed_orig = {}
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

# Create y variables (or constants 0 when impossible)
y = {}
for e in emps:
    for s0 in orig_shifts:
        orig_id = s0["id"]
        if allowed_orig[(e, orig_id)]:
            y[(e, orig_id)] = m.NewBoolVar(f"y_{e}_{orig_id}")
        else:
            y[(e, orig_id)] = m.NewConstant(0)

# Preassign y for confirmed original shifts
for (e, orig_id), val in preassign_orig.items():
    if val:
        m.Add(y[(e, orig_id)] == 1)

# For each original shift: at most 'demand' distinct employees across its slices
for s0 in orig_shifts:
    orig_id = s0["id"]
    m.Add(sum(y[(e, orig_id)] for e in emps) <= s0["demand"]) 

# Link: assigning any slice implies the employee is selected for that original shift
for e in emps:
    for sl in slices:
        m.Add(x[(e, sl["id"])] <= y[(e, sl["orig_id"])])

# Tightening: if an employee is selected for an original shift, he/she must cover at least one of its slices
for e in emps:
    for s0 in orig_shifts:
        orig_id = s0["id"]
        sl_list = slices_by_orig.get(orig_id, [])
        if sl_list:
            m.Add(sum(x[(e, sl["id"]) ] for sl in sl_list) >= y[(e, orig_id)])

# For each shift: coverage and undercoverage
under = {}
is_staffed = {}
for s in shifts:
    sid = s["id"]
    assigned_sum = sum(x[(e, sid)] for e in emps)
    # can't exceed demand
    m.Add(assigned_sum <= s["demand"])
    # compute undercoverage: u >= demand - assigned_sum
    under[sid] = m.NewIntVar(0, s["demand"], f"under_{sid}")
    m.Add(under[sid] >= s["demand"] - assigned_sum)
    # is shift staffed at all?
    is_staffed[sid] = m.NewBoolVar(f"staffed_{sid}")
    m.Add(assigned_sum >= is_staffed[sid])

    # experienced requirement
    if s.get("needs_experienced", False):
        exp_sum = sum(x[(e, sid)] for e in emps if employees[e]["experienced"] and allowed[(e, sid)])
        # if staffed, at least 1 experienced
        m.Add(exp_sum >= is_staffed[sid])

# No overlapping shifts per employee
for e in emps:
    # group shifts per date for speed
    by_date = {}
    for s in shifts:
        by_date.setdefault(s["date"], []).append(s)
    for date, slist in by_date.items():
        for s1, s2 in itertools.combinations(slist, 2):
            if overlaps(s1["start_min"], s1["end_min"], s2["start_min"], s2["end_min"]):
                m.Add(x[(e, s1["id"])] + x[(e, s2["id"])] <= 1)

# Hours per employee
over = {}
under_hours = {}
for e in emps:
    tot = m.NewIntVar(0, 7*24*60, f"totmin_{e}")  # weekly minutes cap (safe upper bound)
    m.Add(tot == sum(shifts[idx_by_id[sid]]["dur_min"] * x[(e, sid)] for sid in shift_ids))
    max_min = employees[e]["hours_max"] * 60
    min_min = employees[e]["hours_min"] * 60

    over[e] = m.NewIntVar(0, 7*24*60, f"over_{e}")
    under_hours[e] = m.NewIntVar(0, 7*24*60, f"underh_{e}")

    # tot <= max + over
    m.Add(tot <= max_min + over[e])
    # tot + under >= min
    m.Add(tot + under_hours[e] >= min_min)


obj = []
obj += [1000 * under[sid] for sid in shift_ids]
obj += [10 * over[e] for e in emps]
obj += [1 * under_hours[e] for e in emps]
m.Minimize(sum(obj))


solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 10.0
solver.parameters.num_search_workers = 8

status = solver.Solve(m)
print("Status:", solver.StatusName(status))


result = {
    "assignments": [],
    "uncovered": [],
    "hours_summary": []
}


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
        covered_person_min += min(len(assigned_here), s0["demand"]) * sl["dur_min"]
    total_needed_min = s0["dur_min"] * s0["demand"]
    missing_minutes = max(0, total_needed_min - covered_person_min)
    result["assignments"].append({
        "date": s0["date"],
        "location": s0["location"],
        "start": s0["start"],
        "end": s0["end"],
        "demand": s0["demand"],
        "assigned_employees": sorted(list(assigned_set)),
        "needs_experienced": bool(s0.get("needs_experienced", False)),
        "missing_minutes": missing_minutes
    })
    if missing_minutes > 0:
        result["uncovered"].append({
            "shift_id": sid0,
            "missing_minutes": missing_minutes
        })

for e in emps:
    # reconstruct minutes
    tot_minutes = 0
    for s in shifts:
        if solver.Value(x[(e, s["id"])]) == 1:
            tot_minutes += s["dur_min"]
    result["hours_summary"].append({
        "employee_id": e,
        "experienced": employees[e]["experienced"],
        "total_hours": round(tot_minutes / 60.0, 2),
        "hours_min": employees[e]["hours_min"],
        "hours_max": employees[e]["hours_max"],
        "over_hours": solver.Value(over[e]) / 60.0,
        "under_hours": solver.Value(under_hours[e]) / 60.0
    })

print(json.dumps(result, ensure_ascii=False, indent=2))

# ====== Simple HTML visualization (Gantt-like) ======
# Creates a minimal HTML file visualizing shifts per date and location,
# with blocks spanning their time window and listing assigned employees.
# No external dependencies required.
def _generate_html_viz(assignments):
    # Group by date then location
    grouped = {}
    for a in assignments:
        date = a["date"]
        loc = a["location"]
        grouped.setdefault(date, {})
        grouped[date].setdefault(loc, [])
        grouped[date][loc].append(a)

    # Helper to convert HH:MM to minutes
    def _min(hhmm):
        h, m = map(int, hhmm.split(":"))
        return h * 60 + m

    # Build HTML
    head = (
        "<!DOCTYPE html><html lang=\"pl\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Wizualizacja grafiku</title>"
        "<style>"
        "body{font-family:Arial,sans-serif;margin:16px;background:#f8f9fb;color:#222}"
        ".day{margin-bottom:28px;padding:12px;background:#fff;border:1px solid #e6e8ef;border-radius:8px;box-shadow:0 1px 2px rgba(0,0,0,0.03)}"
        ".day h2{margin:0 0 12px 0;font-size:18px;color:#111}"
        ".row{margin-bottom:12px}"
        ".loc{font-weight:600;margin-bottom:6px}"
        ".track{position:relative;background:linear-gradient(90deg,#fafbff 0,#fafbff 50%,#f2f4f8 50%,#f2f4f8 100%);"
        "background-size:120px 100%;border:1px solid #e6e8ef;border-radius:6px;overflow:hidden}"
        ".block{position:absolute;border-radius:4px;padding:2px 6px;font-size:12px;"
        "display:flex;align-items:center;gap:6px;color:#0b3d2e;background:#b8e5cc;border:1px solid #8fd4b4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        ".block.small{font-size:11px;padding:1px 4px}"
        ".legend{font-size:12px;color:#555;margin-top:6px}"
        ".axis{display:flex;justify-content:space-between;font-size:11px;color:#666;margin:4px 2px 10px 2px}"
        "</style></head><body>"
    )

    body = ["<h1>Wizualizacja grafiku (00:00–24:00)</h1>"]

    lane_height = 32  # px height per overlapping lane
    lane_gap = 4      # px vertical gap between lanes
    track_padding = 4 # px top/bottom padding inside track

    # For each date, render all locations
    for date in sorted(grouped.keys()):
        body.append(f"<div class=\"day\"><h2>{date}</h2>")
        # Axis 0..24h
        body.append("<div class=\"axis\"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>")
        for loc in sorted(grouped[date].keys()):
            body.append(f"<div class=\"row\">")
            body.append(f"<div class=\"loc\">Lokalizacja: {loc}</div>")

            # Prepare items with start/end in minutes and assign lanes to avoid overlap
            items = []
            for a in sorted(grouped[date][loc], key=lambda x: _min(x["start"])):
                start = _min(a["start"])  # minutes from 0..1440
                end_raw = _min(a["end"]) if a.get("end") else _min(a["start"])  # guard
                end = end_raw if end_raw >= start else start
                start = max(0, min(1440, start))
                end = max(0, min(1440, end))
                items.append({"data": a, "start": start, "end": end})

            # Lane packing: assign the first lane where it doesn't overlap with the last block in that lane
            lane_ends = []  # last end time per lane
            for it in items:
                placed = False
                for i, last_end in enumerate(lane_ends):
                    if it["start"] >= last_end:
                        it["lane"] = i
                        lane_ends[i] = it["end"]
                        placed = True
                        break
                if not placed:
                    it["lane"] = len(lane_ends)
                    lane_ends.append(it["end"])

            lanes_count = max((it.get("lane", 0) for it in items), default=0) + 1 if items else 1
            track_height = track_padding*2 + lanes_count*lane_height + (lanes_count-1)*lane_gap

            # Render track container with computed height
            body.append(f"<div class=\"track\" style=\"height:{track_height}px;\">")

            total = 1440
            for it in items:
                a = it["data"]
                start = it["start"]
                end = it["end"]
                left_pct = 100.0 * start / total
                width_pct = max(0.8, 100.0 * (end - start) / total)  # ensure tiny visibility
                top_px = track_padding + it["lane"] * (lane_height + lane_gap)
                height_px = lane_height

                emp = ", ".join(map(str, a.get("assigned_employees", []))) or "(brak)"
                need_exp = a.get("needs_experienced", False)
                demand = a.get("demand", "?")
                missing_min = int(a.get("missing_minutes", 0) or 0)
                if missing_min > 0:
                    hrs = missing_min / 60.0
                    hrs_txt = f"{hrs:.2f}".rstrip("0").rstrip(".")
                    missing_txt = f" • brak {hrs_txt} h"
                else:
                    missing_txt = ""
                label = f"{a['start']}-{a['end']} | {emp} ({len(a.get('assigned_employees', []))}/{demand}{' • EXP' if need_exp else ''}){missing_txt}"
                base_cls = "block" if missing_min == 0 else "block small"
                style = f"left:{left_pct:.2f}%;width:{width_pct:.2f}%;top:{top_px}px;height:{height_px}px;"
                body.append(f"<div class=\"{base_cls}\" style=\"{style}\">{label}</div>")

            body.append("</div>")  # track
            body.append("</div>")  # row
        body.append("<div class=\"legend\">Zielone bloki oznaczają przydzielone zmiany; w nawiasie: liczba osób/demanda. EXP = wymagana osoba doświadczona.</div>")
        body.append("</div>")  # day

    html = head + "".join(body) + "</body></html>"
    return html

try:
    html_out = _generate_html_viz(result["assignments"])
    out_path = os.path.join(os.path.dirname(__file__), "schedule_viz.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"Wizualizacja zapisana: {out_path}")
except Exception as viz_err:
    print(f"Nie udało się utworzyć wizualizacji: {viz_err}")

# ====== Availability HTML visualization ======
# Shows availability of all employees per day on a 24h timeline, analogous style.
def _generate_availability_html(emp_avail_records):
    # Group by date then employee
    grouped = {}
    for rec in emp_avail_records:
        date = rec["date"]
        emp = rec["employee_id"]
        grouped.setdefault(date, {})
        grouped[date].setdefault(emp, [])
        # keep original strings for display
        for slot in rec.get("available_slots", []):
            grouped[date][emp].append({
                "start": slot.get("start"),
                "end": slot.get("end"),
            })
        # mark assigned (confirmed) if present
        if "assigned_shift" in rec and rec["assigned_shift"].get("confirmed", False):
            asg = rec["assigned_shift"]
            grouped[date][emp].append({
                "start": asg.get("start"),
                "end": asg.get("end"),
                "assigned": True,
                "location": asg.get("location")
            })

    def _min(hhmm):
        h, m = map(int, hhmm.split(":"))
        return h * 60 + m

    head = (
        "<!DOCTYPE html><html lang=\"pl\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Dostępność pracowników</title>"
        "<style>"
        "body{font-family:Arial,sans-serif;margin:16px;background:#f8f9fb;color:#222}"
        ".day{margin-bottom:28px;padding:12px;background:#fff;border:1px solid #e6e8ef;border-radius:8px;box-shadow:0 1px 2px rgba(0,0,0,0.03)}"
        ".day h2{margin:0 0 12px 0;font-size:18px;color:#111}"
        ".row{margin-bottom:12px}"
        ".loc{font-weight:600;margin-bottom:6px}"
        ".track{position:relative;height:46px;background:linear-gradient(90deg,#fafbff 0,#fafbff 50%,#f2f4f8 50%,#f2f4f8 100%);"
        "background-size:120px 100%;border:1px solid #e6e8ef;border-radius:6px;overflow:hidden}"
        ".block{position:absolute;top:4px;bottom:4px;border-radius:4px;padding:2px 6px;font-size:12px;"
        "display:flex;align-items:center;gap:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        ".block.avail{color:#0b2a3d;background:#cfe8ff;border:1px solid #9cc6f5}"
        ".block.assign{color:#3d0b15;background:#ffd6de;border:1px dashed #ff8da3}"
        ".legend{font-size:12px;color:#555;margin-top:6px}"
        ".axis{display:flex;justify-content:space-between;font-size:11px;color:#666;margin:4px 2px 10px 2px}"
        "</style></head><body>"
    )

    body = ["<h1>Dostępność pracowników (00:00–24:00)</h1>"]

    for date in sorted(grouped.keys()):
        body.append(f"<div class=\"day\"><h2>{date}</h2>")
        body.append("<div class=\"axis\"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>")
        for emp in sorted(grouped[date].keys()):
            body.append("<div class=\"row\">")
            body.append(f"<div class=\"loc\">Pracownik: {emp}</div>")
            body.append("<div class=\"track\">")
            # sort by start time
            for slot in sorted(grouped[date][emp], key=lambda s: _min(s["start"])):
                start = _min(slot["start"]) if slot.get("start") else 0
                end = _min(slot["end"]) if slot.get("end") else start
                start = max(0, min(1440, start))
                end = max(0, min(1440, end))
                total = 1440
                left_pct = 100.0 * start / total
                width_pct = max(0.8, 100.0 * (end - start) / total)
                label = f"{slot.get('start','??')}-{slot.get('end','??')}"
                if slot.get("assigned"):
                    loc = slot.get("location", "")
                    label += f" • potwierdzona zmiana {loc}" if loc else " • potwierdzona zmiana"
                    cls = "block assign"
                else:
                    label += " • dostępny"
                    cls = "block avail"
                body.append(f"<div class=\"{cls}\" style=\"left:{left_pct:.2f}%;width:{width_pct:.2f}%;\">{label}</div>")
            body.append("</div>")
            body.append("</div>")
        body.append("<div class=\"legend\">Niebieskie bloki = dostępność; Różowe = potwierdzona zmiana (z pliku dostępności).</div>")
        body.append("</div>")

    html = head + "".join(body) + "</body></html>"
    return html

try:
    avail_html = _generate_availability_html(EMP_AVAIL)
    avail_path = os.path.join(os.path.dirname(__file__), "availability_viz.html")
    with open(avail_path, "w", encoding="utf-8") as f:
        f.write(avail_html)
    print(f"Wizualizacja dostępności zapisana: {avail_path}")
except Exception as avail_err:
    print(f"Nie udało się utworzyć wizualizacji dostępności: {avail_err}")
