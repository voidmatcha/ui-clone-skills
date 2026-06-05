#!/usr/bin/env bash
# benchmark-harvest.sh — parse a finished benchmark run and append metrics
# to benchmark/history.csv + benchmark/history/<timestamp>-<sha>.json.
#
# Invoked at the END of a benchmark run (see skills/benchmark/SKILL.md).
#
# Reads (each is optional — missing files yield NaN / 0 / null for that field):
#   <ref-dir>/.benchmark-start                        — wallclock start (epoch s)
#   <ref-dir>/pipeline-state.json                     — gates, fails, abort
#   <ref-dir>/sections/result.txt                     — per-section AE/SSIM, FAILs
#   <ref-dir>/transitions/result.txt                  — transition compare PASS/FAIL
#   <ref-dir>/hydration-check.json                    — hydration error count
#   <ref-dir>/font-parity.json                        — match / mismatch
#   <ref-dir>/responsive/boundary-collisions.json     — overflow zones
#   <ref-dir>/spec-implementation-coverage.json       — spec → impl coverage %
#   <ref-dir>/phase-e-review.json                     — Phase E advisory deductions
#
# Outputs:
#   benchmark/history/<UTC-iso>-<sha>.json (full record incl. quality dims)
#   benchmark/history.csv                  (one-row append with new schema)

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <ref-dir>" >&2
  exit 1
fi

REF_DIR="$1"
[[ -d "$REF_DIR" ]] || { echo "error: ref-dir not found: $REF_DIR" >&2; exit 2; }
[[ -f "$REF_DIR/pipeline-state.json" ]] || { echo "error: pipeline-state.json missing — benchmark never started?" >&2; exit 3; }

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HISTORY_DIR="$REPO_ROOT/benchmark/history"
HISTORY_CSV="$REPO_ROOT/benchmark/history.csv"
mkdir -p "$HISTORY_DIR"

SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
NOW_EPOCH="$(date -u +%s)"
NOW_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── Wallclock from start marker ──────────────────────────────────────────────
WALLCLOCK_S=0
if [[ -f "$REF_DIR/.benchmark-start" ]]; then
  START_EPOCH="$(head -1 "$REF_DIR/.benchmark-start" | tr -d '[:space:]')"
  [[ "$START_EPOCH" =~ ^[0-9]+$ ]] && WALLCLOCK_S=$(( NOW_EPOCH - START_EPOCH ))
fi

# ── All parsing done in one python pass for tidiness ─────────────────────────
JSON_FILE="$HISTORY_DIR/${NOW_ISO//:/-}-${SHA}.json"

python3 - "$REF_DIR" "$JSON_FILE" "$SHA" "$NOW_ISO" "$WALLCLOCK_S" <<'PY'
import json, os, re, sys
from pathlib import Path

ref_dir, json_out, sha, now_iso, wallclock_s = sys.argv[1:6]
ref = Path(ref_dir)

def load_json(rel):
    p = ref / rel
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

# ── pipeline-state ──────────────────────────────────────────────────────────
state = load_json("pipeline-state.json") or {}
current_gate     = state.get("current_gate", "")
gate_fail_counts = state.get("gate_fail_counts", {}) or {}
unclonable       = state.get("unclonable_reasons", []) or []
completed        = state.get("completed_steps", []) or []

if current_gate == "done":
    outcome = "DONE"
elif unclonable:
    outcome = "ABORT"
else:
    outcome = "INCOMPLETE"

gate_fail_total      = sum(int(v) for v in gate_fail_counts.values())
iterations           = gate_fail_total + len(completed)
unclonable_summary   = ";".join(f"{u.get('gate','?')}:{u.get('reason','?')}" for u in unclonable) or None

# ── sections/result.txt — visual fidelity (AE/SSIM per section) ─────────────
# Two formats observed in the wild:
#   legacy : "section_name: AE=1.23 SSIM=0.94 PASS"
#   table  : "| footer | 1065710 | 870678 | critical | ❌ |"  (AE in col 2)
ae_vals, ssim_vals, sections_failed = [], [], 0
sec_file = ref / "sections" / "result.txt"
if sec_file.is_file():
    for line in sec_file.read_text(encoding="utf-8").splitlines():
        m = re.search(r"AE\s*=\s*([0-9.]+)", line)
        if m:
            ae_vals.append(float(m.group(1)))
        else:
            mt = re.match(r"\|\s+\S+\s+\|\s+([0-9.]+)\s+\|\s+[0-9.]+\s+\|", line)
            if mt:
                ae_vals.append(float(mt.group(1)))
        m = re.search(r"SSIM\s*=\s*([0-9.]+)", line)
        if m: ssim_vals.append(float(m.group(1)))
        if "FAIL" in line or "❌" in line:
            sections_failed += 1

ae_avg  = round(sum(ae_vals)/len(ae_vals), 4)   if ae_vals   else None
ae_max  = round(max(ae_vals), 4)                 if ae_vals   else None
ssim_avg= round(sum(ssim_vals)/len(ssim_vals), 4) if ssim_vals else None

# ── transitions/result.txt — animation arc fidelity ─────────────────────────
trans_total, trans_passed = 0, 0
trans_file = ref / "transitions" / "result.txt"
if trans_file.is_file():
    for line in trans_file.read_text(encoding="utf-8").splitlines():
        if re.search(r"\bPASS\b|✅", line):
            trans_total += 1; trans_passed += 1
        elif re.search(r"\bFAIL\b|❌", line):
            trans_total += 1
transition_pass_rate = round(trans_passed / trans_total, 4) if trans_total else None

# ── hydration-check.json — SSR/console errors ───────────────────────────────
hyd = load_json("hydration-check.json")
hydration_errors = None
if isinstance(hyd, dict):
    hydration_errors = (
        len(hyd.get("errors", []))
        if isinstance(hyd.get("errors"), list)
        else int(hyd.get("errorCount", 0) or 0)
    )

# ── font-parity.json — font visual match ────────────────────────────────────
fp = load_json("font-parity.json")
font_parity = fp.get("parity") if isinstance(fp, dict) else None  # "match"/"mismatch"/None

# ── responsive/boundary-collisions.json — Tailwind ↔ project @media zones ──
bc = load_json("responsive/boundary-collisions.json")
boundary_collisions = len(bc) if isinstance(bc, list) else None

# ── spec-implementation-coverage.json — spec → impl coverage % ──────────────
sc = load_json("spec-implementation-coverage.json")
spec_coverage_pct = None
if isinstance(sc, dict):
    total = sc.get("total")
    withm = sc.get("withMotion")
    if isinstance(total, int) and total > 0 and isinstance(withm, int):
        spec_coverage_pct = round(withm / total, 4)

# ── phase-e-review.json — advisory deduction aggregate ──────────────────────
# Aggregates the Phase E reviewer's advisory {location, reason, penalty, label}
# entries (schemaVersion 2+). Maintainer trend signal only; never feeds a gate.
per = load_json("phase-e-review.json")
advisory_deductions = None
if isinstance(per, dict) and isinstance(per.get("positions"), list):
    by_label = {"completeness": 0, "visual-effect": 0, "icon-variant": 0}
    penalty_sum = 0.0
    deductions_total = 0
    for pos in per["positions"]:
        if not isinstance(pos, dict):
            continue
        for d in pos.get("deductions") or []:
            if not isinstance(d, dict):
                continue
            deductions_total += 1
            if d.get("label") in by_label:
                by_label[d["label"]] += 1
            if isinstance(d.get("penalty"), (int, float)):
                penalty_sum += d["penalty"]
    advisory_deductions = {
        "deductions_total": deductions_total,
        "penalty_sum": round(penalty_sum, 2),
        "by_label": by_label,
    }

# ── Capture depth — how thorough was Phase 1 / Phase 2? ─────────────────────
# Reveals whether ui-capture actually populated the reference set, vs the
# agent short-circuiting and going straight to extraction.
static_ref_dir = ref / "static" / "ref"
sections_captured = (
    sum(1 for p in static_ref_dir.iterdir() if p.suffix == ".png")
    if static_ref_dir.is_dir() else None
)
regions = load_json("regions.json")
regions_detected = len(regions) if isinstance(regions, list) else None
trigger_types_seen = None
if isinstance(regions, list):
    trigger_types_seen = len({r.get("triggerType") for r in regions if isinstance(r, dict) and r.get("triggerType")})

rec = {
    "timestamp": now_iso,
    "sha": sha,
    "site": "realfood.gov",
    "outcome": outcome,
    "pipeline_actions": iterations,
    "wallclock_s": int(wallclock_s),
    "sections_captured": sections_captured,
    "regions_detected": regions_detected,
    "trigger_types_seen": trigger_types_seen,
    "ae_avg": ae_avg,
    "ae_max": ae_max,
    "ssim_avg": ssim_avg,
    "sections_failed": sections_failed,
    "transition_pass_rate": transition_pass_rate,
    "hydration_errors": hydration_errors,
    "font_parity": font_parity,
    "boundary_collisions": boundary_collisions,
    "spec_coverage_pct": spec_coverage_pct,
    "advisory_deductions": advisory_deductions,
    "gate_fail_total": gate_fail_total,
    "unclonable_count": len(unclonable),
    "unclonable_reasons_summary": unclonable_summary,
    "completed_gates": completed,
}
Path(json_out).write_text(json.dumps(rec, indent=2), encoding="utf-8")
PY

# Capture the CSV row printed by python
CSV_ROW="$(python3 - "$REF_DIR" "$JSON_FILE" "$SHA" "$NOW_ISO" "$WALLCLOCK_S" <<'PY'
# Re-run with the same parsing, but only print the CSV row. Tiny duplication
# is fine because the per-pass cost is negligible and it keeps the JSON write
# and CSV append in one bash flow.
import json, re, sys
from pathlib import Path
ref_dir, json_out, sha, now_iso, wallclock_s = sys.argv[1:6]
ref = Path(ref_dir)
def load_json(rel):
    p = ref / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
state = load_json("pipeline-state.json") or {}
cg = state.get("current_gate", ""); gfc = state.get("gate_fail_counts", {}) or {}
uc = state.get("unclonable_reasons", []) or []; cs = state.get("completed_steps", []) or []
outcome = "DONE" if cg == "done" else ("ABORT" if uc else "INCOMPLETE")
gft = sum(int(v) for v in gfc.values()); iters = gft + len(cs)
ae_vals=[]; ssim_vals=[]; sf=0
sec = ref / "sections" / "result.txt"
if sec.is_file():
    for line in sec.read_text().splitlines():
        m = re.search(r"AE\s*=\s*([0-9.]+)", line)
        if m: ae_vals.append(float(m.group(1)))
        else:
            mt = re.match(r"\|\s+\S+\s+\|\s+([0-9.]+)\s+\|\s+[0-9.]+\s+\|", line)
            if mt: ae_vals.append(float(mt.group(1)))
        m = re.search(r"SSIM\s*=\s*([0-9.]+)", line); ssim_vals.append(float(m.group(1))) if m else None
        if "FAIL" in line or "❌" in line: sf += 1
ae_avg = round(sum(ae_vals)/len(ae_vals),4) if ae_vals else None
ae_max = round(max(ae_vals),4) if ae_vals else None
ssim_avg = round(sum(ssim_vals)/len(ssim_vals),4) if ssim_vals else None
tt=tp=0
tr = ref / "transitions" / "result.txt"
if tr.is_file():
    for line in tr.read_text().splitlines():
        if re.search(r"\bPASS\b|✅", line): tt+=1; tp+=1
        elif re.search(r"\bFAIL\b|❌", line): tt+=1
trpr = round(tp/tt,4) if tt else None
hyd = load_json("hydration-check.json")
he = (len(hyd.get("errors",[])) if isinstance(hyd, dict) and isinstance(hyd.get("errors"), list)
      else (int(hyd.get("errorCount",0) or 0) if isinstance(hyd, dict) else None))
fp = load_json("font-parity.json"); fpa = fp.get("parity") if isinstance(fp, dict) else None
bc = load_json("responsive/boundary-collisions.json"); bcc = len(bc) if isinstance(bc, list) else None
sc = load_json("spec-implementation-coverage.json")
scov = None
if isinstance(sc, dict):
    t=sc.get("total"); w=sc.get("withMotion")
    if isinstance(t,int) and t>0 and isinstance(w,int): scov = round(w/t, 4)
# Capture depth — how thorough was Phase 1 / 2?
static_ref_dir = ref / "static" / "ref"
sec_cap = sum(1 for p in static_ref_dir.iterdir() if p.suffix == ".png") if static_ref_dir.is_dir() else None
regs = load_json("regions.json")
reg_det = len(regs) if isinstance(regs, list) else None
trig_types = None
if isinstance(regs, list):
    trig_types = len({r.get("triggerType") for r in regs if isinstance(r, dict) and r.get("triggerType")})
def v(x): return "NaN" if x is None else str(x)
print(",".join(v(x) for x in [now_iso, sha, outcome, iters, wallclock_s,
    sec_cap, reg_det, trig_types,
    ae_avg, ae_max, ssim_avg, sf, trpr, he, fpa or "NaN", bcc, scov, gft, len(uc)]))
PY
)"

# ── Append CSV (create header if first run) ──────────────────────────────────
HEADER="timestamp,sha,outcome,pipeline_actions,wallclock_s,sections_captured,regions_detected,trigger_types_seen,ae_avg,ae_max,ssim_avg,sections_failed,transition_pass_rate,hydration_errors,font_parity,boundary_collisions,spec_coverage_pct,gate_fail_total,unclonable_count"
if [[ ! -f "$HISTORY_CSV" ]]; then
  echo "$HEADER" > "$HISTORY_CSV"
elif head -1 "$HISTORY_CSV" | grep -q "^timestamp,sha,outcome,iterations,"; then
  # Schema upgraded: iterations → pipeline_actions (clearer that it's a derived
  # gate_fails + completed_steps count, not the actual ralph-loop iter cap).
  TMP="${HISTORY_CSV}.tmp.$$"
  { echo "$HEADER"; tail -n +2 "$HISTORY_CSV"; } > "$TMP"
  mv "$TMP" "$HISTORY_CSV"
elif ! head -1 "$HISTORY_CSV" | grep -q "sections_captured"; then
  # Schema upgraded (added capture-depth columns). Rewrite header in place;
  # old rows keep their original column count — readers tolerant to ragged
  # CSV (pandas, awk -F,) handle them.
  TMP="${HISTORY_CSV}.tmp.$$"
  { echo "$HEADER"; tail -n +2 "$HISTORY_CSV"; } > "$TMP"
  mv "$TMP" "$HISTORY_CSV"
fi
echo "$CSV_ROW" >> "$HISTORY_CSV"

# ── Summary + delta ──────────────────────────────────────────────────────────
echo
echo "benchmark recorded: $JSON_FILE"
echo "  outcome              : $(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r["outcome"])' "$JSON_FILE")"
python3 - "$JSON_FILE" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
fields = [
    ("pipeline_actions",      r["pipeline_actions"]),
    ("wallclock_s",           r["wallclock_s"]),
    ("sections_captured",     r["sections_captured"]),
    ("regions_detected",      r["regions_detected"]),
    ("trigger_types_seen",    r["trigger_types_seen"]),
    ("ae_avg",                r["ae_avg"]),
    ("ae_max",                r["ae_max"]),
    ("ssim_avg",              r["ssim_avg"]),
    ("sections_failed",       r["sections_failed"]),
    ("transition_pass_rate",  r["transition_pass_rate"]),
    ("hydration_errors",      r["hydration_errors"]),
    ("font_parity",           r["font_parity"]),
    ("boundary_collisions",   r["boundary_collisions"]),
    ("spec_coverage_pct",     r["spec_coverage_pct"]),
    ("gate_fail_total",       r["gate_fail_total"]),
    ("unclonable_count",      r["unclonable_count"]),
]
for name, val in fields:
    print(f"  {name:<20} : {val}")
if r["unclonable_count"]:
    print(f"  ABORT reasons        : {r['unclonable_reasons_summary']}")
PY

# Delta vs previous run
PREV_LINE="$(tail -2 "$HISTORY_CSV" 2>/dev/null | head -1)"
LAST_LINE="$(tail -1 "$HISTORY_CSV" 2>/dev/null)"
if [[ -n "$PREV_LINE" && "$PREV_LINE" != "$LAST_LINE" && "$PREV_LINE" != timestamp,* ]]; then
  echo
  echo "delta vs previous run:"
  python3 - "$PREV_LINE" "$LAST_LINE" "$HEADER" <<'PY'
import sys
prev = sys.argv[1].split(",")
cur  = sys.argv[2].split(",")
hdr  = sys.argv[3].split(",")
# Ragged-row safe comparison. Only compare fields where BOTH sides have a
# numeric value at the SAME header position. If prev row was written under
# an older schema (fewer columns or different order), skip mismatched fields
# rather than emitting misleading "improvements" / "regressions".
lower_better = {"pipeline_actions","wallclock_s","ae_avg","ae_max","sections_failed",
                "hydration_errors","boundary_collisions","gate_fail_total","unclonable_count"}
higher_better = {"ssim_avg","transition_pass_rate","spec_coverage_pct",
                 "sections_captured","regions_detected","trigger_types_seen"}
skip_string_fields = {"timestamp","sha","outcome","font_parity",
                      "unclonable_reasons_summary","completed_gates"}
def num(x):
    if not isinstance(x, str): return None
    s = x.strip()
    if s in ("", "NaN", "None", "null"): return None
    try: return float(s)
    except ValueError: return None

shown = 0
for i, label in enumerate(hdr):
    if label in skip_string_fields: continue
    if i >= len(prev) or i >= len(cur): continue
    a, b = num(prev[i]), num(cur[i])
    if a is None or b is None: continue
    if a == b: continue
    arrow = "↓" if b < a else "↑"
    if label in lower_better:
        note = " good" if b < a else " BAD"
    elif label in higher_better:
        note = " good" if b > a else " BAD"
    else:
        note = ""
    print(f"  {label:<22} {a} → {b} {arrow}{note}")
    shown += 1
if shown == 0:
    print("  (no comparable numeric fields between rows — schema may differ)")
PY
fi
