import os, json, itertools
from datetime import timedelta
from typing import Dict, Any, List
from jsonschema import validate, Draft7Validator, ValidationError

# --- OpenAI SDK ---
# pip install openai
from openai import OpenAI

# ====== ŚCIEŻKI ======
_BASE_DIR = os.path.dirname(__file__)
EMP_AVAIL_JSON = os.path.join(_BASE_DIR, "osiol_dost.json")
DEMAND_JSON    = os.path.join(_BASE_DIR, "osiol_zap.json")

# ====== USTAWIENIA MODELU ======
# Rekomendowany szybki model do structured outputs; możesz podmienić na inny wspierany model.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1")  # np. gpt-4o, gpt-4.1-mini itd.
API_KEY = os.environ.get("OPENAI_API_KEY")
# Kontrola trybu:
#  "whole"  -> przydzielamy całe zmiany (najprościej)
#  "segments" -> dopuszczamy podział jednej zmiany na kilka spójnych segmentów (min. 120 min)
ASSIGN_MODE = os.environ.get("ASSIGN_MODE", "segments")

# Minimalna długość segmentu przy ASSIGN_MODE="segments"
MIN_SEGMENT_MIN = int(os.environ.get("MIN_SEGMENT_MIN", "120"))

# ====== NARZĘDZIA ======
def to_minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h*60 + m

def overlaps(a_start, a_end, b_start, b_end) -> bool:
    return not (a_end <= b_start or b_end <= a_start)

def contains(slot_start, slot_end, s_start, s_end) -> bool:
    return slot_start <= s_start and s_end <= slot_end

def shift_key(sh):
    return f"{sh['date']}|{sh['location']}|{sh['start']}-{sh['end']}"

def same_shift(a, b):
    return a["date"] == b["date"] and a["location"] == b["location"] and a["start"] == b["start"] and a["end"] == b["end"]

def load_json_file(path):
    if not os.path.exists(path):
        raise SystemExit(f"Brak pliku: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Błąd JSON w pliku {path}: {e}")

# ====== SCHEMATY WYJŚCIOWE (STRUCTURED OUTPUTS) ======
SCHEDULE_SCHEMA_WHOLE: Dict[str, Any] = {
    "name": "ScheduleWholeOutput",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "shift_id":  {"type": "string"},
                        "date":      {"type": "string"},
                        "location":  {"type": "string"},
                        "start":     {"type": "string"},
                        "end":       {"type": "string"},
                        "demand":    {"type": "integer", "minimum": 1},
                        "assigned_employees": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "needs_experienced": {"type": "boolean"}
                    },
                    "required": ["shift_id","date","location","start","end","demand","assigned_employees","needs_experienced"]
                }
            },
            "uncovered": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "shift_id": {"type": "string"},
                        "missing":  {"type": "integer", "minimum": 0}
                    },
                    "required": ["shift_id", "missing"]
                }
            }
        },
        "required": ["assignments", "uncovered"]
    }
}

SCHEDULE_SCHEMA_SEGMENTS: Dict[str, Any] = {
    "name": "ScheduleSegmentedOutput",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "shift_id":  {"type": "string"},
                        "date":      {"type": "string"},
                        "location":  {"type": "string"},
                        "start":     {"type": "string"},
                        "end":       {"type": "string"},
                        "demand":    {"type": "integer", "minimum": 1},
                        "needs_experienced": {"type": "boolean"},
                        "segments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "employee_id": {"type":"string"},
                                    "seg_start":   {"type":"string"},
                                    "seg_end":     {"type":"string"}
                                },
                                "required": ["employee_id","seg_start","seg_end"]
                            }
                        }
                    },
                    "required": ["shift_id","date","location","start","end","demand","needs_experienced","segments"]
                }
            },
            "uncovered": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "shift_id": {"type": "string"},
                        "missing_minutes":  {"type": "integer", "minimum": 0}
                    },
                    "required": ["shift_id", "missing_minutes"]
                }
            }
        },
        "required": ["assignments", "uncovered"]
    }
}

# ====== PROMPTY (SYSTEM + USER) ======
SYSTEM_PROMPT = """\
Jesteś ekspertem od układania grafików zmianowych (restauracje). Twoim zadaniem jest zaproponować przydziały pracowników do zmian tak, aby:
1) Spełnić popyt (demand) – nie przekraczać go na żadnym przedziale.
2) Respektować dostępność czasową (pracownik może pracować tylko, gdy cały przedział mieści się w jego dostępności).
3) Nie nakładać pracownikowi nakładających się w czasie zmian/segmentów.
4) Gdy 'needs_experienced' = true, w każdej obsadzonej zmianie musi być przynajmniej 1 pracownik doświadczony.
5) Respektować tygodniowe limity: total_hours ∈ [hours_min, hours_max], jeśli to możliwe; jeżeli nie, dopuszczalne minimalne naruszenia (lepiej niedowyrabiać niż przekraczać maks).
6) Pre-assigny (assigned_shift.confirmed=true) są obowiązkowe.
7) Preferencje: rozkładaj obciążenie równo; nie rozbijaj zmiany na segmenty, chyba że tryb 'segments' jest włączony i brak prostej obsady.
Zwróć DOKŁADNIE JSON w podanym schemacie. Bez komentarzy, bez dodatkowego tekstu.
"""

# Uwaga: dołączamy komplet danych w JSON – AI robi tylko przydział.
def build_user_prompt(mode: str, employees: dict, avail_records: list, shifts: list) -> str:
    payload = {
        "assign_mode": mode,
        "min_segment_minutes": MIN_SEGMENT_MIN,
        "employees": employees,        # {employee_id: {experienced, hours_min, hours_max}}
        "availability": avail_records, # [{employee_id,date,available_slots:[{start,end}], assigned_shift?}]
        "shifts": shifts               # [{shift_id,date,location,start,end,demand,needs_experienced}]
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)

# ====== WEJŚCIE: CZYTANIE DANYCH ======
EMP_AVAIL = load_json_file(EMP_AVAIL_JSON)
WEEK_DEMAND = load_json_file(DEMAND_JSON)

# Preprocess: oryginalne zmiany z ID, czasem w minutach itd.
orig_shifts: List[Dict[str, Any]] = []
for sh in WEEK_DEMAND:
    s = dict(sh)
    s["start_min"] = to_minutes(s["start"])
    s["end_min"] = to_minutes(s["end"])
    s["dur_min"] = s["end_min"] - s["start_min"]
    s["id"] = shift_key(s)
    orig_shifts.append(s)

# Zbierz dane pracowników (sklej limity tygodniowe, doświadczony)
employees: Dict[str, Any] = {}
availability = {}  # (emp,date) -> list[(start_min,end_min)]
preassign_orig = {}

for rec in EMP_AVAIL:
    emp = rec["employee_id"]
    if emp not in employees:
        employees[emp] = {
            "experienced": bool(rec.get("experienced", False)),
            "hours_min": int(rec.get("hours_min", 0)),
            "hours_max": int(rec.get("hours_max", 10**9))
        }
    else:
        # zaciśnij limity, jeśli różne w rekordach dnia
        employees[emp]["experienced"] = employees[emp]["experienced"] or bool(rec.get("experienced", False))
        employees[emp]["hours_min"] = max(employees[emp]["hours_min"], int(rec.get("hours_min", 0)))
        employees[emp]["hours_max"] = min(employees[emp]["hours_max"], int(rec.get("hours_max", 10**9)))

    key = (emp, rec["date"])
    availability.setdefault(key, [])
    for slot in rec.get("available_slots", []):
        availability[key].append((to_minutes(slot["start"]), to_minutes(slot["end"])))

    if "assigned_shift" in rec and rec["assigned_shift"].get("confirmed", False):
        asg = rec["assigned_shift"]
        asg_norm = {"date": rec["date"], "location": asg["location"], "start": asg["start"], "end": asg["end"]}
        for s in orig_shifts:
            if same_shift(asg_norm, s):
                preassign_orig[(emp, s["id"])] = True

# Uproszczone rekordy dostępności na potrzeby promptu (bez minut)
avail_records_prompt = []
for (emp, date), slots in availability.items():
    slots_h = []
    for a,b in slots:
        slots_h.append({"start": f"{a//60:02d}:{a%60:02d}", "end": f"{b//60:02d}:{b%60:02d}"})
    item = {
        "employee_id": emp,
        "date": date,
        "available_slots": slots_h
    }
    # jeśli są preassigny tego dnia — dołącz
    for s in orig_shifts:
        if s["date"] == date and preassign_orig.get((emp, s["id"]), False):
            item["assigned_shift"] = {
                "start": s["start"], "end": s["end"], "location": s["location"], "confirmed": True
            }
    avail_records_prompt.append(item)

# Zmiany do promptu
shifts_prompt = []
for s in orig_shifts:
    shifts_prompt.append({
        "shift_id": s["id"],
        "date": s["date"],
        "location": s["location"],
        "start": s["start"],
        "end": s["end"],
        "demand": int(s["demand"]),
        "needs_experienced": bool(s.get("needs_experienced", False))
    })

# ====== WOŁANIE OPENAI (STRUCTURED OUTPUTS) ======
def ask_openai_for_schedule(assign_mode: str) -> dict:
    client = OpenAI(api_key="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")

    schema = SCHEDULE_SCHEMA_WHOLE if assign_mode == "whole" else SCHEDULE_SCHEMA_SEGMENTS
    user_prompt = build_user_prompt(assign_mode, employees, avail_records_prompt, shifts_prompt)

    # 1) PRÓBA: structured outputs na Chat Completions (json_schema)
    try:
        chat = client.chat.completions.create(
            model=OPENAI_MODEL,  # np. "gpt-4o-mini"
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            response_format={
                "type": "json_schema",
                "json_schema": schema
            },
            #temperature=0
        )
        content = chat.choices[0].message.content
        return json.loads(content)

    except Exception as e_schema:
        # 2) FALLBACK: JSON mode (gwarantuje poprawny JSON, ale bez twardego egzekwowania schematu)
        chat = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            #temperature=0
        )
        content = chat.choices[0].message.content
        parsed = json.loads(content)
        # Walidacja lokalna pod nasz schemat
        validate(instance=parsed, schema=schema["schema"])
        return parsed

# ====== WALIDACJA REGUŁ BIZNESOWYCH (po AI) ======
def post_validate_and_normalize(data: Dict[str, Any], mode: str) -> Dict[str, Any]:
    schema = SCHEDULE_SCHEMA_WHOLE if mode == "whole" else SCHEDULE_SCHEMA_SEGMENTS
    schema_only = schema["schema"]
    # 1) walidacja schematu
    v = Draft7Validator(schema_only)
    errors = sorted(v.iter_errors(data), key=lambda e: e.path)
    if errors:
        msgs = "\n".join([f"- {'/'.join([str(p) for p in err.path])}: {err.message}" for err in errors])
        raise ValueError(f"Invalid AI JSON against schema:\n{msgs}")

    # 2) uzupełnij brakujące pozycje w 'uncovered' jeśli AI nie pokrył w pełni
    if mode == "whole":
        covered_by_shift = {a["shift_id"]: len(a["assigned_employees"]) for a in data["assignments"]}
        # zbuduj mapę demand
        demand_map = {s["id"]: s["demand"] for s in orig_shifts}
        missing = []
        for sid, dem in demand_map.items():
            cov = covered_by_shift.get(sid, 0)
            if cov < dem:
                missing.append({"shift_id": sid, "missing": int(dem - cov)})
        # połącz z AI 'uncovered' (unikaj duplikowania shift_id)
        existing = {u["shift_id"] for u in data["uncovered"]}
        for m in missing:
            if m["shift_id"] not in existing:
                data["uncovered"].append(m)

    else:  # segments
        # policz braki w minutach
        demand_map = {s["id"]: s for s in orig_shifts}
        missing_map = {}
        for a in data["assignments"]:
            s0 = demand_map[a["shift_id"]]
            needed = s0["dur_min"] * s0["demand"]
            covered = 0
            for seg in a["segments"]:
                covered += max(0, to_minutes(seg["seg_end"]) - to_minutes(seg["seg_start"]))
            miss = max(0, needed - covered)
            if miss > 0:
                missing_map[a["shift_id"]] = miss
        existing = {u["shift_id"] for u in data["uncovered"]}
        for sid, miss in missing_map.items():
            if sid not in existing:
                data["uncovered"].append({"shift_id": sid, "missing_minutes": int(miss)})

    return data

# ====== PODSUMOWANIA GODZIN + HTML ======
def compute_hours_summary(assign_data: Dict[str, Any], mode: str) -> List[Dict[str, Any]]:
    # policz minuty per pracownik
    minutes_by_emp = {e: 0 for e in employees.keys()}

    if mode == "whole":
        # dla całych zmian: każdy przydzielony pracuje całą długość zmiany
        shift_by_id = {s["id"]: s for s in orig_shifts}
        for a in assign_data["assignments"]:
            s0 = shift_by_id[a["shift_id"]]
            dur = s0["dur_min"]
            for emp in a["assigned_employees"]:
                if emp in minutes_by_emp:
                    minutes_by_emp[emp] += dur
    else:
        # dla segmentów: zliczamy faktycznie przydzielone minuty segmentów
        for a in assign_data["assignments"]:
            for seg in a["segments"]:
                dur = max(0, to_minutes(seg["seg_end"]) - to_minutes(seg["seg_start"]))
                emp = seg["employee_id"]
                if emp in minutes_by_emp:
                    minutes_by_emp[emp] += dur

    out = []
    for e, mins in minutes_by_emp.items():
        out.append({
            "employee_id": e,
            "experienced": bool(employees[e]["experienced"]),
            "total_hours": round(mins/60.0, 2),
            "hours_min": employees[e]["hours_min"],
            "hours_max": employees[e]["hours_max"]
        })
    return out

def build_result_object(assign_data: Dict[str, Any], mode: str) -> Dict[str, Any]:
    hours = compute_hours_summary(assign_data, mode)
    return {
        "assignments": assign_data["assignments"],
        "uncovered": assign_data["uncovered"],
        "hours_summary": hours
    }

# ====== HTML wizualizacje (jak u Ciebie) ======
def _generate_html_viz(assignments):
    def _min(hhmm):
        h, m = map(int, hhmm.split(":"))
        return h * 60 + m
    grouped = {}
    for a in assignments:
        grouped.setdefault(a["date"], {}).setdefault(a["location"], []).append(a)

    head = (
        "<!DOCTYPE html><html lang='pl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Wizualizacja grafiku</title>"
        "<style>"
        "body{font-family:Arial,sans-serif;margin:16px;background:#f8f9fb;color:#222}"
        ".day{margin-bottom:28px;padding:12px;background:#fff;border:1px solid #e6e8ef;border-radius:8px;box-shadow:0 1px 2px rgba(0,0,0,.03)}"
        ".day h2{margin:0 0 12px 0;font-size:18px}"
        ".row{margin-bottom:12px}.loc{font-weight:600;margin-bottom:6px}"
        ".track{position:relative;background:linear-gradient(90deg,#fafbff 0,#fafbff 50%,#f2f4f8 50%,#f2f4f8 100%);background-size:120px 100%;border:1px solid #e6e8ef;border-radius:6px;overflow:hidden}"
        ".block{position:absolute;border-radius:4px;padding:2px 6px;font-size:12px;display:flex;align-items:center;gap:6px;color:#0b3d2e;background:#b8e5cc;border:1px solid #8fd4b4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        ".block.small{font-size:11px;padding:1px 4px}.legend{font-size:12px;color:#555;margin-top:6px}"
        ".axis{display:flex;justify-content:space-between;font-size:11px;color:#666;margin:4px 2px 10px 2px}"
        "</style></head><body>"
    )
    body = ["<h1>Wizualizacja grafiku (00:00–24:00)</h1>"]
    lane_height, lane_gap, track_padding = 32, 4, 4

    for date in sorted(grouped.keys()):
        body.append(f"<div class='day'><h2>{date}</h2>")
        body.append("<div class='axis'><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>")
        for loc, arr in sorted(grouped[date].items()):
            body.append("<div class='row'>")
            body.append(f"<div class='loc'>Lokalizacja: {loc}</div>")
            # znormalizuj wpisy na wspólną strukturę (obsługa obu trybów)
            items = []
            for a in sorted(arr, key=lambda x: _min(x["start"])):
                start, end = _min(a["start"]), _min(a["end"])
                if "assigned_employees" in a:  # whole
                    label_emp = ", ".join(a["assigned_employees"]) or "(brak)"
                else:  # segments
                    segs = a.get("segments", [])
                    uniq = []
                    for s in segs:
                        if s["employee_id"] not in uniq:
                            uniq.append(s["employee_id"])
                    label_emp = ", ".join(uniq) or "(brak)"
                items.append({"data": a, "start": start, "end": end})

            lane_ends = []
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

            lanes_count = (max([it["lane"] for it in items]) + 1) if items else 1
            track_height = track_padding*2 + lanes_count*lane_height + (lanes_count-1)*lane_gap
            body.append(f"<div class='track' style='height:{track_height}px;'>")

            total = 1440
            for it in items:
                a = it["data"]; start = it["start"]; end = it["end"]
                left_pct = 100.0 * start / total
                width_pct = max(0.8, 100.0 * (end - start) / total)
                top_px = track_padding + it["lane"] * (lane_height + lane_gap)
                emp_txt = ", ".join(a.get("assigned_employees", [])) if "assigned_employees" in a else ", ".join(sorted({s["employee_id"] for s in a.get("segments", [])}))
                need_exp = a.get("needs_experienced", False)
                demand = a.get("demand", "?")
                label = f"{a['start']}-{a['end']} | {emp_txt} ({demand}{' • EXP' if need_exp else ''})"
                body.append(f"<div class='block' style='left:{left_pct:.2f}%;width:{width_pct:.2f}%;top:{top_px}px;height:{lane_height}px;'>{label}</div>")
            body.append("</div></div>")
        body.append("<div class='legend'>Zielone bloki = przydzielone zmiany (lista osób / demand). EXP = w zmianie wymagana osoba doświadczona.</div>")
        body.append("</div>")
    return head + "".join(body) + "</body></html>"

def _generate_availability_html(emp_avail_records):
    def _min(hhmm):
        h, m = map(int, hhmm.split(":"))
        return h * 60 + m
    grouped = {}
    for rec in emp_avail_records:
        grouped.setdefault(rec["date"], {}).setdefault(rec["employee_id"], []).extend(rec.get("available_slots", []))

    head = (
        "<!DOCTYPE html><html lang='pl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Dostępność pracowników</title>"
        "<style>"
        "body{font-family:Arial,sans-serif;margin:16px;background:#f8f9fb;color:#222}"
        ".day{margin-bottom:28px;padding:12px;background:#fff;border:1px solid #e6e8ef;border-radius:8px;box-shadow:0 1px 2px rgba(0,0,0,.03)}"
        ".day h2{margin:0 0 12px 0;font-size:18px}.row{margin-bottom:12px}.loc{font-weight:600;margin-bottom:6px}"
        ".track{position:relative;height:46px;background:linear-gradient(90deg,#fafbff 0,#fafbff 50%,#f2f4f8 50%,#f2f4f8 100%);background-size:120px 100%;border:1px solid #e6e8ef;border-radius:6px;overflow:hidden}"
        ".block{position:absolute;top:4px;bottom:4px;border-radius:4px;padding:2px 6px;font-size:12px;display:flex;align-items:center;gap:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        ".block.avail{color:#0b2a3d;background:#cfe8ff;border:1px solid #9cc6f5}"
        ".axis{display:flex;justify-content:space-between;font-size:11px;color:#666;margin:4px 2px 10px 2px}"
        "</style></head><body>"
    )
    body = ["<h1>Dostępność pracowników (00:00–24:00)</h1>"]
    for date in sorted(grouped.keys()):
        body.append(f"<div class='day'><h2>{date}</h2>")
        body.append("<div class='axis'><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>")
        for emp in sorted(grouped[date].keys()):
            body.append("<div class='row'>")
            body.append(f"<div class='loc'>Pracownik: {emp}</div>")
            body.append("<div class='track'>")
            for slot in sorted(grouped[date][emp], key=lambda s: _min(s["start"])):
                start = _min(slot["start"]); end = _min(slot["end"])
                total = 1440
                left_pct = 100.0 * start / total
                width_pct = max(0.8, 100.0 * (end - start) / total)
                label = f"{slot['start']}-{slot['end']} • dostępny"
                body.append(f"<div class='block avail' style='left:{left_pct:.2f}%;width:{width_pct:.2f}%;'>{label}</div>")
            body.append("</div></div>")
        body.append("</div>")
    return head + "".join(body) + "</body></html>"

# ====== MAIN ======
def main():
    # 1) AI → harmonogram
    ai_raw = ask_openai_for_schedule(ASSIGN_MODE)
    ai_ok = post_validate_and_normalize(ai_raw, ASSIGN_MODE)
    result = build_result_object(ai_ok, ASSIGN_MODE)

    # 2) Zapis wyników
    out_json = os.path.join(_BASE_DIR, "schedule_ai.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"✅ Zapisano JSON grafiku: {out_json}")

    # 3) HTML (grafik)
    html_sched = _generate_html_viz(result["assignments"])
    html_sched_path = os.path.join(_BASE_DIR, "schedule_viz.html")
    with open(html_sched_path, "w", encoding="utf-8") as f:
        f.write(html_sched)
    print(f"🖼  Zapisano wizualizację grafiku: {html_sched_path}")

    # 4) HTML (dostępność)
    html_avail = _generate_availability_html(EMP_AVAIL)
    html_avail_path = os.path.join(_BASE_DIR, "availability_viz.html")
    with open(html_avail_path, "w", encoding="utf-8") as f:
        f.write(html_avail)
    print(f"🖼  Zapisano wizualizację dostępności: {html_avail_path}")

if __name__ == "__main__":
    main()
