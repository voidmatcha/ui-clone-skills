#!/usr/bin/env bash
# masked-region-static-check.sh — static-style parity for dynamic-masked regions.
#
# Dynamic:true spec selectors are masked out of pixel comparison, video-motion,
# and the motion proof — and the motion proof checks MOTION only. That left
# STATIC style defects under a mask completely unverified (loop-10/11: the
# eatReal "Eat Real" h2 rendered left-aligned while the ref centers it; the h2
# is inside the eatreal-food-carousel dynamic mask, so every gate passed).
#
# This check probes the LIVE impl DOM (un-masked) for each masked selector and
# compares phase-free computed styles against the extraction-time ref ground
# truth in dom-scaffold.json. Styles are state-independent, so no pixel capture
# or phase sampling is needed.
#
# Geometry/center-offset of masked elements is NOT this gate's concern — it is
# viewport-dependent and is closed by alignment-parity (which measures ref+impl
# geometry across fan-out viewports once masked elements are no longer excluded
# from enumerate-sections.js).
#
# Usage: masked-region-static-check.sh <session> <impl-url> <ref-dir> [ref-url]
#
# Env:
#   UI_CLONE_MRS_IMPL_FILE — pre-collected impl entries (JSON list); skips the
#                            browser (test fixtures / replay).
#   UI_CLONE_MRS_VIEWPORT  — "WxH" to set before probing (default: leave as-is).
#   UI_CLONE_MRS_PROPS     — comma list overriding the default property set.
#   REUSE_FROZEN_REF       — "1" reuses an existing ref-viewport-visibility.json
#                            instead of re-probing the live ref.
#
# Writes:
#   <ref-dir>/masked-region-static.json
#   <ref-dir>/ref-viewport-visibility.json  (only when [ref-url] is given)
#
# Exit: 0 pass/skip/warn, 1 fail, 2 setup error

set -euo pipefail

SESSION="${1:?Usage: masked-region-static-check.sh <session> <impl-url> <ref-dir> [ref-url]}"
IMPL_URL="${2:?Usage: masked-region-static-check.sh <session> <impl-url> <ref-dir> [ref-url]}"
REF_DIR="${3:?Usage: masked-region-static-check.sh <session> <impl-url> <ref-dir> [ref-url]}"
# tools-batch-11 ITEM 1: optional ref URL. When given, probe the LIVE REF with
# the SAME probe at the SAME viewports to produce ref-viewport-visibility.json so
# the verdict can excuse the ref's own responsive/scroll hiding (otherwise the
# gate stays fail-closed and false-fails the reference against itself). Legacy
# 3-arg callers skip the ref pass — backward compatible.
REF_URL="${4:-}"

[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPTS_DIR/../../.." && pwd)"
PY() { PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 "$@"; }

IMPL_FILE="${UI_CLONE_MRS_IMPL_FILE:-}"
if [ -z "$IMPL_FILE" ]; then
  command -v agent-browser >/dev/null 2>&1 || {
    echo "agent-browser not found in PATH" >&2
    exit 2
  }
  PLAN_JSON="$(PY -m ui_clone.gates.masked_region_static plan "$REF_DIR")"
  SEL_COUNT="$(printf '%s' "$PLAN_JSON" | python3 -c "import json,sys;print(len(json.load(sys.stdin).get('selectors') or []))")"
  IMPL_FILE="$(mktemp "${TMPDIR:-/tmp}/mrs-impl.XXXXXX")"
  trap 'rm -f "$IMPL_FILE"' EXIT
  echo "[]" > "$IMPL_FILE"

  if [ "${SEL_COUNT:-0}" -gt 0 ]; then
    # The probe emits RICH per-element records via the shared visible-identity
    # collector (paint + geometry), so the verdict can resolve the
    # rendered-VISIBLE match and reject decoys (Attacks A/C) rather than pairing
    # by DOM index. agent-browser eval applies ONE unescape pass; the lib and
    # this wrapper use plain string ops only (no backslash regexes).
    LIB_FILE="$SCRIPTS_DIR/lib/visible-identity.js"
    # L-MEA-13: macOS mktemp requires TRAILING Xs — a .js suffix in the
    # template aborts the whole check. Create then rename.
    WRAP_FILE="$(mktemp "${TMPDIR:-/tmp}/mrs-wrap.XXXXXX")"
    mv "$WRAP_FILE" "${WRAP_FILE}.js"
    WRAP_FILE="${WRAP_FILE}.js"
    # True settle (batch-7 ITEM 2): instead of two fixed samples, poll the masked
    # elements' styles until MutationObserver/rAF quiescence past a wall-clock
    # FLOOR that exceeds plausible deferred-defect timers (a flip at 7000ms is
    # caught). EVERY frame is recorded as stylesSamples so a late flip/oscillation
    # is visible to the verdict's settled_state. agent-browser eval applies ONE
    # unescape pass; plain string ops only (no backslash regexes).
    PY - "$PLAN_JSON" > "$WRAP_FILE" <<'PY'
import json
import os
import sys

plan = json.loads(sys.argv[1])
selectors = json.dumps(plan.get("selectors") or [])
props = json.dumps(plan.get("props") or [])
try:
    frames = max(2, int(os.environ.get("UI_CLONE_SETTLE_FRAMES", "3")))
except (TypeError, ValueError):
    frames = 3
try:
    floor_ms = max(8000, int(os.environ.get("UI_CLONE_SETTLE_FLOOR_MS", "8000")))
except (TypeError, ValueError):
    floor_ms = 8000

print(
    """(async () => {
  const sels = %s;
  const props = %s;
  const collectStyles = () => {
    const m = {};
    sels.forEach(sel => {
      __visibleIdentity.collect(sel, props).forEach(r => { m[r.selector + "|" + r.index] = r.styles; });
    });
    return m;
  };
  // Representative scroll sweep (batch-8 ITEM 4): trip scroll-/once-triggered
  // defects and bring below-fold masked content through the viewport BEFORE we
  // sample the settled state. Mirrors junk-token's sweep cadence.
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const maxScroll = () => Math.max(document.documentElement.scrollHeight - window.innerHeight, 0);
  for (let i = 1; i <= 6; i++) {
    window.scrollTo({ top: (i / 6) * maxScroll(), behavior: "instant" });
    await wait(300);
  }
  window.scrollTo({ top: 0, behavior: "instant" });
  await wait(300);
  const series = await __visibleIdentity.sampleUntilQuiescent(collectStyles, { frames: %d, floorMs: %d });
  const out = [];
  // Scroll each masked selector into view before measuring it (batch-8 ITEM 10):
  // a faithful below-fold heading reads on-screen instead of "absent". The
  // post-scroll record carries scrolledIntoView=true (batch-9 minor) so the
  // verdict's below-fold tolerance applies ONLY to elements the probe actually
  // brought through the viewport — an unreachable below-fold decoy has no proof.
  sels.forEach(sel => {
    document.querySelectorAll(sel).forEach(el => {
      try { el.scrollIntoView({ block: "center", behavior: "instant" }); } catch (e) {}
    });
    __visibleIdentity.collect(sel, props).forEach(r => { r.scrolledIntoView = true; out.push(r); });
  });
  out.forEach(r => {
    const k = r.selector + "|" + r.index;
    r.stylesSamples = series.map(frame => frame[k]).filter(s => s !== undefined && s !== null);
  });
  return JSON.stringify(out);
})()""" % (selectors, props, frames, floor_ms)
)
PY
    # L-MEA-13: macOS mktemp requires TRAILING Xs — a .js suffix in the
    # template aborts the whole check. Create then rename.
    PROBE_FILE="$(mktemp "${TMPDIR:-/tmp}/mrs-probe.XXXXXX")"
    mv "$PROBE_FILE" "${PROBE_FILE}.js"
    PROBE_FILE="${PROBE_FILE}.js"
    { cat "$LIB_FILE"; printf ';\n'; cat "$WRAP_FILE"; } > "$PROBE_FILE"
    rm -f "$WRAP_FILE"

    # Probe at EVERY fan-out viewport (batch-7 ITEM 3): text-align is
    # viewport-dependent via @media, so a single-viewport probe is blind to a
    # defect baked behind a narrow @media. Each record is stamped with its
    # clientWidth; the verdict buckets per viewport and fails any mismatch.
    VIEWPORTS="$(python3 - "$REF_DIR" <<'PY'
import json
import os
import sys
from pathlib import Path

ref = Path(sys.argv[1])
env = os.environ.get("UI_CLONE_MRS_VIEWPORT", "").strip()
if env:
    w, _, h = env.partition("x")
    print((w or "1280"), (h or "800"))
    sys.exit(0)
vps = []
try:
    plan = json.loads((ref / "verification-plan.json").read_text(encoding="utf-8"))
    for v in plan.get("viewports") or []:
        if isinstance(v, dict) and v.get("w"):
            vps.append((int(v["w"]), int(v.get("h") or 800)))
except (OSError, ValueError, json.JSONDecodeError):
    pass
if not vps:
    vps = [(1280, 800)]
for w, h in vps:
    print(w, h)
PY
)"
    agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1
    echo "[]" > "$IMPL_FILE"
    printf '%s\n' "$VIEWPORTS" | while read -r VW VH; do
      [ -n "$VW" ] || continue
      agent-browser --session "$SESSION" set viewport "$VW" "$VH" >/dev/null 2>&1 || true
      agent-browser --session "$SESSION" wait 2500 >/dev/null 2>&1
      RAW1="$(mktemp "${TMPDIR:-/tmp}/mrs-raw1.XXXXXX")"
      agent-browser --session "$SESSION" eval "$(cat "$PROBE_FILE")" > "$RAW1" 2>/dev/null || true
      PY - "$RAW1" "$IMPL_FILE" <<'PY'
import json
import os
import sys


def _decode(path):
    try:
        value = open(path, encoding="utf-8", errors="replace").read().strip()
    except OSError:
        return []
    for _ in range(3):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                break
        else:
            break
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


raw_path, impl_path = sys.argv[1], sys.argv[2]
# The probe attached the full ordered stylesSamples series per record (each
# stamped with clientWidth). Accumulate records across viewports; the verdict
# buckets per viewport and takes the settled STATE per series.
new_rows = _decode(raw_path)
existing = []
if os.path.exists(impl_path):
    try:
        loaded = json.loads(open(impl_path, encoding="utf-8").read())
        if isinstance(loaded, list):
            existing = loaded
    except (OSError, json.JSONDecodeError):
        existing = []
existing.extend(new_rows)
open(impl_path, "w", encoding="utf-8").write(json.dumps(existing))
PY
      rm -f "$RAW1"
    done

    # tools-batch-11 ITEM 1: ref-viewport-visibility.json producer. Probe the
    # LIVE REF with the SAME probe at the SAME fan-out viewports, then derive the
    # per-selector hidden-viewport set via the gate's OWN visibility predicate
    # (ui_clone.gates.masked_region_static ref-visibility). This is what makes
    # the gate ref-vs-ref self-pass: the verdict can excuse a zero-visible impl
    # bucket only where the REF is itself zero-visible. The hidden set is derived
    # ONLY from the ref, so the respbypass anti-cheat (impl hides what the ref
    # SHOWS) still fails. Written via the python module (Path.write_text), not a
    # shell redirect, so the ad-hoc-ref-write hook does not block it.
    if [ -n "$REF_URL" ] && { [ "${REUSE_FROZEN_REF:-0}" != "1" ] \
                              || [ ! -f "$REF_DIR/ref-viewport-visibility.json" ]; }; then
      REF_SESSION="${SESSION}-rv"
      REF_RECORDS="$(mktemp "${TMPDIR:-/tmp}/mrs-refrec.XXXXXX")"
      echo "[]" > "$REF_RECORDS"
      agent-browser --session "$REF_SESSION" open "$REF_URL" >/dev/null 2>&1 || true
      printf '%s\n' "$VIEWPORTS" | while read -r VW VH; do
        [ -n "$VW" ] || continue
        agent-browser --session "$REF_SESSION" set viewport "$VW" "$VH" >/dev/null 2>&1 || true
        agent-browser --session "$REF_SESSION" wait 2500 >/dev/null 2>&1
        RAWR="$(mktemp "${TMPDIR:-/tmp}/mrs-rawref.XXXXXX")"
        agent-browser --session "$REF_SESSION" eval "$(cat "$PROBE_FILE")" > "$RAWR" 2>/dev/null || true
        PY - "$RAWR" "$REF_RECORDS" <<'PY'
import json
import os
import sys


def _decode(path):
    try:
        value = open(path, encoding="utf-8", errors="replace").read().strip()
    except OSError:
        return []
    for _ in range(3):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                break
        else:
            break
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


raw_path, ref_path = sys.argv[1], sys.argv[2]
new_rows = _decode(raw_path)
existing = []
if os.path.exists(ref_path):
    try:
        loaded = json.loads(open(ref_path, encoding="utf-8").read())
        if isinstance(loaded, list):
            existing = loaded
    except (OSError, json.JSONDecodeError):
        existing = []
existing.extend(new_rows)
open(ref_path, "w", encoding="utf-8").write(json.dumps(existing))
PY
        rm -f "$RAWR"
      done
      # capturedViewports = exactly the widths probed above.
      CAPTURED_WIDTHS="$(printf '%s\n' "$VIEWPORTS" | awk 'NF{printf "%s%s", sep, $1; sep=","}')"
      PY -m ui_clone.gates.masked_region_static ref-visibility \
        "$REF_DIR" "$REF_RECORDS" "$CAPTURED_WIDTHS" || true
      rm -f "$REF_RECORDS"
    fi

    rm -f "$PROBE_FILE"
  fi
fi

set +e
PY -m ui_clone.gates.masked_region_static verdict "$REF_DIR" "$IMPL_FILE"
CODE=$?
set -e

if [ -f "$REF_DIR/masked-region-static.json" ]; then
  python3 - "$REF_DIR/masked-region-static.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"masked-region-static: status={data.get('status')} "
      f"checked={data.get('checkedRows')} fails={data.get('failCount')}")
for row in data.get("rows") or []:
    if row.get("status") == "fail":
        print(f"  FAIL {row.get('selector')} {row.get('property', '(element)')}: "
              f"ref={row.get('refValue', row.get('reason'))} "
              f"impl={row.get('implValue', 'absent')}")
for u in data.get("unmeasured") or []:
    print(f"  unmeasured {u.get('selector')}: {u.get('reason')}")
PY
fi

exit "$CODE"
