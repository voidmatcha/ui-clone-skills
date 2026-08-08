#!/usr/bin/env bash
# desktop-band-fluidity-check.sh — does the impl reflow across the DESKTOP BAND,
# or is it a frozen capture-time snapshot?
#
# Why it matters:
#   Cloned sites bake capture-time widths inline, so they do not reflow when the
#   browser is resized within the desktop band — the range of widths ABOVE the
#   breakpoint where the reference serves a wholesale-different (tablet/mobile)
#   UI. resize-behavior-probe inspects the CSS cascade mechanism per selector;
#   this check instead measures ref-vs-impl REFLOW PARITY at several widths
#   inside that band: at each width it compares document height and horizontal
#   overflow, and it flags the classic baked-width symptom — the ref's document
#   height moves across widths while the impl's stays frozen.
#
#   NOTE: agent-browser eval output is often double-JSON-encoded (an outer string
#   wrap); the parser below json.loads twice with a fallback, matching the
#   sibling geometry-sanity / resize-behavior probes.
#
# Usage:
#   desktop-band-fluidity-check.sh <session> <ref-url> <impl-url> <ref-dir>
#   desktop-band-fluidity-check.sh --judge <measurements-json> <out-artifact>
#
# Env:
#   DESKTOP_BAND_WIDTHS   comma list of WxH (default: derived from REF_DIR's
#                         detected-breakpoints.json / impl-detected-breakpoints.json,
#                         plus one probe above the widest declared breakpoint)
#   FLUIDITY_DOCH_TOL_PCT per-width docH delta tolerance percent (default 8)
#
# Output: <ref-dir>/desktop-band-fluidity.json
# Exit: 0 pass, 1 fail (a width out of tolerance / impl-only overflow / widthBaked),
#       2 setup error (bad args / browser unreachable — an "error" artifact is
#       still written when a destination is known).

set -uo pipefail

TOL_PCT="${FLUIDITY_DOCH_TOL_PCT:-8}"

# ── Shared verdict: measurements JSON -> artifact JSON. ────────────────────
# Consumed by both the live browser path and --judge mode so the verdict math
# is exercised identically in tests (no live browser) and in production.
run_judge() {
  # run_judge <measurements-json-path> <out-artifact-path>
  python3 - "$1" "$2" "$TOL_PCT" <<'PY'
import json
import sys
from datetime import datetime, timezone

meas_path, out_path, tol_arg = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    tol = float(tol_arg)
except (TypeError, ValueError):
    tol = 8.0

with open(meas_path) as fh:
    meas = json.load(fh)
rows_in = meas.get("widths") or []


def _vp_width(vp):
    try:
        return int(str(vp).lower().split("x", 1)[0])
    except (ValueError, AttributeError):
        return None


widths = []
any_fail = not rows_in
for row in rows_in:
    vp = row.get("viewport")
    ref_h = row.get("refDocH")
    impl_h = row.get("implDocH")
    ref_ox = bool(row.get("refOverflowX"))
    impl_ox = bool(row.get("implOverflowX"))
    delta_pct = None
    if isinstance(ref_h, (int, float)) and ref_h and isinstance(impl_h, (int, float)):
        delta_pct = abs(impl_h - ref_h) / ref_h * 100.0
    doch_fail = delta_pct is not None and delta_pct > tol
    # impl overflows horizontally while the ref does not — a baked-width tell.
    overflow_fail = impl_ox and not ref_ox
    # Fail-closed: a width whose measurement is MISSING (eval flake, dead
    # session) must not read as green — silent pass on unmeasured rows is
    # the same hole the dispatcher's emit-or-fail invariant closes.
    unmeasured = not (
        isinstance(ref_h, (int, float)) and ref_h
        and isinstance(impl_h, (int, float))
    )
    row_pass = not (doch_fail or overflow_fail or unmeasured)
    if not row_pass:
        any_fail = True
    widths.append({
        "viewport": vp,
        "refDocH": ref_h,
        "implDocH": impl_h,
        "dochDeltaPct": round(delta_pct, 3) if delta_pct is not None else None,
        "refOverflowX": ref_ox,
        "implOverflowX": impl_ox,
        "refBodyWidth": row.get("refBodyWidth"),
        "implBodyWidth": row.get("implBodyWidth"),
        "pass": row_pass,
    })

# Cross-width fluidity: if the ref's docH moves >2% from the widest to the
# narrowest band width but the impl's moves <0.5x that relative change, the impl
# is frozen to its capture width while the ref reflows -> baked px widths.
width_baked = False
measured = [
    w for w in widths
    if _vp_width(w["viewport"]) is not None
    and isinstance(w["refDocH"], (int, float)) and w["refDocH"]
    and isinstance(w["implDocH"], (int, float))
]
if len(measured) >= 2:
    measured.sort(key=lambda w: _vp_width(w["viewport"]))
    narrow, wide = measured[0], measured[-1]
    if _vp_width(narrow["viewport"]) != _vp_width(wide["viewport"]):
        ref_rel = abs(narrow["refDocH"] - wide["refDocH"]) / wide["refDocH"]
        impl_rel = abs(narrow["implDocH"] - wide["implDocH"]) / wide["implDocH"] \
            if wide["implDocH"] else 0.0
        if ref_rel > 0.02 and impl_rel < 0.5 * ref_rel:
            width_baked = True

status = "fail" if (any_fail or width_baked) else "pass"
artifact = {
    "schemaVersion": 1,
    "status": status,
    "widths": widths,
    "widthBaked": width_baked,
    "tolerancePct": tol,
    "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
with open(out_path, "w") as fh:
    json.dump(artifact, fh, indent=2)
print(f"desktop-band-fluidity: {status} "
      f"({sum(1 for w in widths if not w['pass'])}/{len(widths)} widths fail, "
      f"widthBaked={str(width_baked).lower()})")
sys.exit(0 if status == "pass" else 1)
PY
}

# ── --judge mode: skip the browser, judge pre-collected measurements. ──────
if [ "${1:-}" = "--judge" ]; then
  MEAS="${2:?Usage: desktop-band-fluidity-check.sh --judge <measurements-json> <out-artifact>}"
  OUT="${3:?Usage: desktop-band-fluidity-check.sh --judge <measurements-json> <out-artifact>}"
  if [ ! -f "$MEAS" ]; then
    echo "desktop-band-fluidity: measurements file not found: $MEAS" >&2
    exit 2
  fi
  run_judge "$MEAS" "$OUT"
  exit $?
fi

# ── Live browser path. ─────────────────────────────────────────────────────
SESSION="${1:?Usage: desktop-band-fluidity-check.sh <session> <ref-url> <impl-url> <ref-dir>}"
REF_URL="${2:?Usage: desktop-band-fluidity-check.sh <session> <ref-url> <impl-url> <ref-dir>}"
IMPL_URL="${3:?Usage: desktop-band-fluidity-check.sh <session> <ref-url> <impl-url> <ref-dir>}"
REF_DIR="${4:?Usage: desktop-band-fluidity-check.sh <session> <ref-url> <impl-url> <ref-dir>}"

OUT="$REF_DIR/desktop-band-fluidity.json"
REF_SESSION="${SESSION}-fl-ref"
IMPL_SESSION="${SESSION}-fl-impl"
# Probe widths. An explicit DESKTOP_BAND_WIDTHS always wins; otherwise derive
# them from the reference's own breakpoints. A fixed ceiling silently skips the
# band a wide reference actually defines, so a clone that only breaks above the
# ceiling used to pass unmeasured. The widest breakpoint is a lower bound, not
# a ceiling, so one probe is also placed above it.
DEFAULT_WIDTHS="1440x900,1280x800,1024x800"
if [ -n "${DESKTOP_BAND_WIDTHS:-}" ]; then
  WIDTHS="$DESKTOP_BAND_WIDTHS"
else
  WIDTHS="$(python3 - "$REF_DIR" <<'PY'
import json
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
DESKTOP_MIN = 1024      # below this the reference serves a different UI
ABOVE_MAX_MARGIN = 320  # one probe inside the open-ended band above the max
MAX_PROBES = 6          # each probe costs a settle + two evals per side

values = {1024, 1280, 1440}
for name in ("detected-breakpoints.json", "impl-detected-breakpoints.json"):
    path = ref_dir / name
    if not path.is_file():
        continue
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    if not isinstance(document, dict):
        continue
    for raw in document.get("breakpoints") or []:
        text = str(raw).strip().lower()
        if text.endswith("px"):
            text = text[:-2]
        if text.isdigit():
            values.add(int(text))

widths = sorted(v for v in values if v >= DESKTOP_MIN)
widths.append(max(widths) + ABOVE_MAX_MARGIN)
widths = sorted(set(widths))
if len(widths) > MAX_PROBES:
    step = (len(widths) - 1) / (MAX_PROBES - 1)
    widths = sorted({widths[round(i * step)] for i in range(MAX_PROBES)})
print(",".join(f"{w}x900" for w in reversed(widths)))
PY
)"
  [ -z "$WIDTHS" ] && WIDTHS="$DEFAULT_WIDTHS"
fi

write_error() {
  # write_error <reason> — always emit an artifact so the dispatcher's
  # emit-or-fail invariant holds even on setup failure.
  python3 - "$OUT" "$1" "$TOL_PCT" <<'PY'
import json
import sys
from datetime import datetime, timezone

out_path, reason, tol_arg = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    tol = float(tol_arg)
except (TypeError, ValueError):
    tol = 8.0
with open(out_path, "w") as fh:
    json.dump({
        "schemaVersion": 1,
        "status": "error",
        "widths": [],
        "widthBaked": False,
        "tolerancePct": tol,
        "reason": reason,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, fh, indent=2)
PY
}

: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME

MEAS="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/fluidity.$$.meas")"
TMPD="$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/fluidity.$$.d")"
mkdir -p "$TMPD"
trap 'rm -rf "$TMPD" "$MEAS"; \
  agent-browser --session "$REF_SESSION" close >/dev/null 2>&1 || true; \
  agent-browser --session "$IMPL_SESSION" close >/dev/null 2>&1 || true' EXIT

# Open each session once; widths are applied by set viewport in the loop.
if ! agent-browser --session "$REF_SESSION" open "$REF_URL" >/dev/null 2>&1; then
  write_error "ref URL not reachable: $REF_URL"
  echo "desktop-band-fluidity: error (ref not reachable)" >&2
  exit 2
fi
if ! agent-browser --session "$IMPL_SESSION" open "$IMPL_URL" >/dev/null 2>&1; then
  write_error "impl URL not reachable: $IMPL_URL"
  echo "desktop-band-fluidity: error (impl not reachable)" >&2
  exit 2
fi

MEASURE_JS='(() => {
  const de = document.documentElement;
  return JSON.stringify({
    docH: de.scrollHeight,
    overflowX: de.scrollWidth > window.innerWidth + 1,
    bodyWidth: document.body.getBoundingClientRect().width
  });
})()'

i=0
IFS=',' read -ra _WLIST <<< "$WIDTHS"
for WH in "${_WLIST[@]}"; do
  WH="$(printf '%s' "$WH" | tr -d '[:space:]')"
  [ -z "$WH" ] && continue
  W="${WH%%x*}"
  H="${WH#*x}"
  [ "$H" = "$WH" ] && H=900
  agent-browser --session "$REF_SESSION" set viewport "$W" "$H" >/dev/null 2>&1
  agent-browser --session "$IMPL_SESSION" set viewport "$W" "$H" >/dev/null 2>&1
  sleep 1.5
  agent-browser --session "$REF_SESSION" eval "$MEASURE_JS" > "$TMPD/ref.$i" 2>/dev/null || true
  agent-browser --session "$IMPL_SESSION" eval "$MEASURE_JS" > "$TMPD/impl.$i" 2>/dev/null || true
  printf '%s' "$WH" > "$TMPD/vp.$i"
  i=$((i + 1))
done

# Assemble measurements from the per-width raw evals (double-decode aware).
python3 - "$TMPD" "$i" "$MEAS" "$TOL_PCT" <<'PY'
import json
import sys
from pathlib import Path

tmpd, count, meas_path, tol_arg = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
try:
    tol = float(tol_arg)
except (TypeError, ValueError):
    tol = 8.0


def unwrap(path):
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        v = json.loads(raw)
        if isinstance(v, str):
            v = json.loads(v)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


widths = []
for idx in range(count):
    vp = (tmpd / f"vp.{idx}").read_text().strip() if (tmpd / f"vp.{idx}").is_file() else None
    ref = unwrap(tmpd / f"ref.{idx}") or {}
    impl = unwrap(tmpd / f"impl.{idx}") or {}
    widths.append({
        "viewport": vp,
        "refDocH": ref.get("docH"),
        "implDocH": impl.get("docH"),
        "refOverflowX": bool(ref.get("overflowX")),
        "implOverflowX": bool(impl.get("overflowX")),
        "refBodyWidth": ref.get("bodyWidth"),
        "implBodyWidth": impl.get("bodyWidth"),
    })

with open(meas_path, "w") as fh:
    json.dump({"tolerancePct": tol, "widths": widths}, fh)
PY

run_judge "$MEAS" "$OUT"
exit $?
