#!/usr/bin/env bash
# capture-scroll.sh — Phase B scroll-progress snapshots
#
# Captures DOM state at fixed scroll percentages [0, 10, 25, 50, 75, 90, 100]
# so the impl can replicate scroll-driven state (sticky-navbar shrink,
# parallax position, IntersectionObserver-mounted sections, scroll-triggered
# reveals) instead of guessing from a single top-of-page snapshot.
#
# Design: docs/multi-snapshot-capture-design.md § Phase B. Pattern mirrors
# capture-states.sh (Phase A) — single in-page Promise loop with state
# capture across all 7 stops, no shell-side per-stop eval round-trips.
#
# Usage:
#   capture-scroll.sh <url> <session> <ref_dir> [--reuse-session]
#
# By default opens its own derived session `${session}-scroll`. Pass
# `--reuse-session` to use the caller's session directly (only safe when
# capture-scroll.sh is called sequentially from capture.sh on a quiet
# session, typically after capture-states.sh has settled the page).
#
# Output:
#   <ref_dir>/states/scroll/0pct.json … 100pct.json   — per-pct full DOM + visible-section index
#   <ref_dir>/states/scroll/trajectory.json           — compact per-pct entries (no outerHTML)
#   <ref_dir>/states/scroll/dom-mutations.json        — scroll-correlated DOM mutation trace
#   <ref_dir>/states/scroll/summary.json              — {checked, durationMs, scrollHeight, viewportHeight,
#                                                       finalScrollHeight, infiniteScroll, static, schemaVersion}
#
# Exit codes:
#   0  capture completed (may be static — single 0pct snapshot for short pages)
#   1  bad usage
#   2  agent-browser open failed
#   3  agent-browser eval returned unparseable / unexpected-shape response

set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <url> <session> <ref_dir> [--reuse-session]" >&2
  exit 1
fi

URL="$1"
SESSION="$2"
REF_DIR="$3"
REUSE_SESSION="false"
if [ "${4:-}" = "--reuse-session" ]; then
  REUSE_SESSION="true"
fi

SCROLL_SESSION="${SESSION}-scroll"
if [ "$REUSE_SESSION" = "true" ]; then
  SCROLL_SESSION="$SESSION"
fi

# A shared agent-browser daemon can be restarted by an unrelated concurrent
# capture and silently reopen this session at about:blank. Keep every capture
# run in a deterministic namespace while preserving an explicit caller choice.
if [ -z "${AGENT_BROWSER_NAMESPACE:-}" ]; then
  CAPTURE_NAMESPACE_ID="$(printf '%s' "$SESSION" | cksum | awk '{print $1}')"
  AGENT_BROWSER_NAMESPACE="ui-clone-${CAPTURE_NAMESPACE_ID}"
  export AGENT_BROWSER_NAMESPACE
fi

OUTDIR="${REF_DIR}/${STATES_PREFIX:-states}/scroll"
mkdir -p "$(dirname "$OUTDIR")"
RESPONSE_TMP=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORIGIN_VALIDATOR="$SCRIPT_DIR/validate-agent-browser-origin.py"
EVAL_JS_FILE="$SCRIPT_DIR/capture-scroll-eval.js"
INIT_JS_FILE="$SCRIPT_DIR/capture-scroll-init.js"
[ -f "$EVAL_JS_FILE" ] || {
  echo "capture-scroll: missing $EVAL_JS_FILE" >&2
  exit 2
}
[ -f "$INIT_JS_FILE" ] || {
  echo "capture-scroll: missing $INIT_JS_FILE" >&2
  exit 2
}

EVAL_ATTEMPTS="${CAPTURE_SCROLL_EVAL_ATTEMPTS:-3}"
EVAL_TIMEOUT_MS="${CAPTURE_SCROLL_TIMEOUT_MS:-25000}"
if ! [[ "$EVAL_ATTEMPTS" =~ ^[0-9]+$ ]] || [ "$EVAL_ATTEMPTS" -lt 1 ]; then
  EVAL_ATTEMPTS=1
fi
if ! [[ "$EVAL_TIMEOUT_MS" =~ ^[0-9]+$ ]] || [ "$EVAL_TIMEOUT_MS" -lt 1000 ]; then
  EVAL_TIMEOUT_MS=25000
fi
# agent-browser treats launch-affecting environment changes between commands
# as a reason to restart its daemon. Export before `open`; setting this only on
# `eval` discards the live page and replays the script against about:blank.
AGENT_BROWSER_DEFAULT_TIMEOUT="$EVAL_TIMEOUT_MS"
export AGENT_BROWSER_DEFAULT_TIMEOUT

derived_ready_wait_ms() {
  local splash_summary="${REF_DIR}/${STATES_PREFIX:-states}/splash/summary.json"
  # RealFood's first-load splash can remain active beyond 2.6s. A fresh
  # derived session has no shared lifecycle state, so keep a conservative
  # floor even when the measured splash summary is shorter.
  local fallback="${CAPTURE_DERIVED_READY_WAIT_MS:-3500}"
  local buffer="${CAPTURE_DERIVED_READY_BUFFER_MS:-500}"
  python3 - "$splash_summary" "$fallback" "$buffer" <<'PY'
import json
import sys
from pathlib import Path


def as_nonnegative_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


summary_path = Path(sys.argv[1])
fallback_ms = as_nonnegative_int(sys.argv[2], 3500)
buffer_ms = as_nonnegative_int(sys.argv[3], 500)
wait_ms = fallback_ms

if summary_path.is_file():
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        summary = {}
    if summary.get("checked") is True:
        duration_ms = as_nonnegative_int(summary.get("durationMs"), 0)
        wait_ms = max(wait_ms, duration_ms + buffer_ms)

print(wait_ms)
PY
}

wait_for_derived_readiness() {
  local wait_ms
  wait_ms="$(derived_ready_wait_ms)"
  if ! agent-browser --session "$SCROLL_SESSION" wait "$wait_ms" >/dev/null 2>&1; then
    echo "capture-scroll: agent-browser wait failed (session=$SCROLL_SESSION waitMs=$wait_ms)" >&2
    exit 2
  fi
}

cleanup() {
  rm -f "${RESPONSE_TMP:-}"
  if [ "$REUSE_SESSION" = "false" ]; then
    agent-browser --session "$SCROLL_SESSION" close >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT

# Open page in the derived session unless reusing the caller's session.
if [ "$REUSE_SESSION" = "false" ]; then
  if ! OPEN_OUTPUT="$(agent-browser --session "$SCROLL_SESSION" --init-script "$INIT_JS_FILE" open "$URL" 2>&1)"; then
    echo "capture-scroll: agent-browser open failed for $URL (session=$SCROLL_SESSION)" >&2
    printf '%s\n' "$OPEN_OUTPUT" >&2
    exit 2
  fi
  wait_for_derived_readiness
fi

# Keep the browser program in a real JavaScript file. Besides making it
# lintable, this avoids shell quoting drift and lets agent-browser consume the
# supported --stdin transport without echoing multi-megabyte DOM payloads.

RESPONSE_RAW=""
EVAL_OK="false"
for attempt in $(seq 1 "$EVAL_ATTEMPTS"); do
  if RESPONSE_RAW="$(agent-browser --session "$SCROLL_SESSION" eval --json --stdin < "$EVAL_JS_FILE" 2>&1)"; then
    if printf '%s' "$RESPONSE_RAW" | python3 "$ORIGIN_VALIDATOR"; then
      EVAL_OK="true"
      break
    fi
  fi

  echo "capture-scroll: agent-browser eval failed (session=$SCROLL_SESSION attempt=${attempt}/${EVAL_ATTEMPTS})" >&2
  echo "$RESPONSE_RAW" >&2

  if [ "$attempt" -lt "$EVAL_ATTEMPTS" ]; then
    # Long single-eval scroll sweeps occasionally lose the CDP response
    # channel on large animated pages. Re-opening the derived session gives
    # the next attempt a fresh page target without weakening capture
    # requirements or accepting partial scroll data.
    if [ "$REUSE_SESSION" = "false" ]; then
      agent-browser --session "$SCROLL_SESSION" --init-script "$INIT_JS_FILE" open "$URL" >/dev/null 2>&1 || true
    fi
    wait_for_derived_readiness
  fi
done

if [ "$EVAL_OK" != "true" ]; then
  echo "capture-scroll: agent-browser eval failed after ${EVAL_ATTEMPTS} attempt(s) (session=$SCROLL_SESSION)" >&2
  exit 3
fi

# Validate + split into trajectory / summary / per-pct files via python.
# Heredoc + stdin pipe conflict — write response to a temp file the python
# block reads via argv. Also handles multi-MB DOM blobs (7 stops × ~500KB).
RESPONSE_TMP="$(mktemp -t capture-scroll-resp.XXXX)"
printf '%s' "$RESPONSE_RAW" > "$RESPONSE_TMP"
python3 - "$OUTDIR" "$RESPONSE_TMP" <<'PY'
import atexit
import json
import shutil
import sys
import tempfile
from pathlib import Path

outdir = Path(sys.argv[1])
raw = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")

try:
    parsed = json.loads(raw)
except json.JSONDecodeError as e:
    print(f"capture-scroll: invalid JSON from agent-browser eval ({e}):\n{raw[:300]}", file=sys.stderr)
    sys.exit(3)

# Peel agent-browser eval envelope: {success, data: {origin, result: <inner>}}.
# Real `agent-browser eval --json` always wraps. Unit-test fake-browser emits
# the inner JSON bare, so this peel is a no-op there.
if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict) and "result" in parsed["data"]:
    parsed = parsed["data"]["result"]
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            pass

# Legacy single-key wrapper {"result": <inner>}. Kept so a future shim that
# pre-strips the envelope on the caller side keeps working without script edits.
if isinstance(parsed, dict) and "result" in parsed and isinstance(parsed["result"], (dict, str)):
    inner = parsed["result"]
    if isinstance(inner, str):
        try:
            parsed = json.loads(inner)
        except json.JSONDecodeError:
            pass
    else:
        parsed = inner

if not isinstance(parsed, dict) or "stops" not in parsed:
    print(f"capture-scroll: unexpected payload shape:\n{json.dumps(parsed)[:300]}", file=sys.stderr)
    sys.exit(3)

stops = parsed.get("stops", [])
summary = {
    "checked": True,
    "durationMs": parsed.get("durationMs", 0),
    "scrollHeight": parsed.get("scrollHeight", 0),
    "viewportHeight": parsed.get("viewportHeight", 0),
    "finalScrollHeight": parsed.get("finalScrollHeight", parsed.get("scrollHeight", 0)),
    "scrollHeightDeltaPct": parsed.get("scrollHeightDeltaPct", 0),
    "scrollHeightGrew": parsed.get("scrollHeightGrew", False),
    "infiniteScroll": parsed.get("infiniteScroll", False),
    "scrollEngine": parsed.get("scrollEngine", "native"),
    "scrollEngineReason": parsed.get("scrollEngineReason", "not reported"),
    "scrollTransportProven": parsed.get("scrollTransportProven", True),
    "scrollControlMethod": parsed.get("scrollControlMethod", "not reported"),
    "static": parsed.get("static", False),
    "domMutationCount": len(parsed.get("domMutations", [])),
    "domMutationTraceTruncated": parsed.get("domMutationTraceTruncated", False),
    "scanStepPx": parsed.get("scanStepPx", 0),
    "alignmentFailures": parsed.get("alignmentFailures", []),
    "schemaVersion": 2,
}

if not summary["scrollTransportProven"]:
    print(
        "capture-scroll: scroll transport is unproven: "
        f"{summary['scrollEngine']} ({summary['scrollEngineReason']})",
        file=sys.stderr,
    )
    sys.exit(3)

if summary["alignmentFailures"]:
    print(
        "capture-scroll: failed to align one or more scroll stops: "
        + json.dumps(summary["alignmentFailures"], ensure_ascii=False),
        file=sys.stderr,
    )
    sys.exit(3)

if not isinstance(stops, list) or not stops:
    print("capture-scroll: stops must be a non-empty list", file=sys.stderr)
    sys.exit(3)

staging = Path(tempfile.mkdtemp(prefix=f".{outdir.name}.tmp-", dir=outdir.parent))
atexit.register(shutil.rmtree, staging, ignore_errors=True)

# Trajectory entries — drop outerHTML; keep pct + scrollY + visibleSections + digest.
trajectory = []
for s in stops:
    entry = {k: v for k, v in s.items() if k != "outerHTML"}
    trajectory.append(entry)

(staging / "trajectory.json").write_text(
    json.dumps(trajectory, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
(staging / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
(staging / "dom-mutations.json").write_text(
    json.dumps(parsed.get("domMutations", []), ensure_ascii=False, indent=2),
    encoding="utf-8",
)

# Per-pct full DOM snapshots.
for s in stops:
    pct = s.get("pct")
    html = s.get("outerHTML")
    if pct is None or not html:
        continue
    (staging / f"{pct}pct.json").write_text(
        json.dumps({
            "pct": pct,
            "scrollY": s.get("scrollY", 0),
            "outerHTML": html,
            "visibleSections": s.get("visibleSections", []),
        }, ensure_ascii=False),
        encoding="utf-8",
    )

backup = None
try:
    if outdir.exists():
        backup = Path(tempfile.mkdtemp(prefix=f".{outdir.name}.previous-", dir=outdir.parent))
        backup.rmdir()
        outdir.rename(backup)
    staging.rename(outdir)
except Exception:
    if backup is not None and backup.exists() and not outdir.exists():
        backup.rename(outdir)
    shutil.rmtree(staging, ignore_errors=True)
    raise
else:
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)

print(f"capture-scroll: wrote {len(trajectory)} stop(s) to {outdir}/", file=sys.stderr)
PY

SPEC_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/state-structure-spec.py"
if [ "${STATE_STRUCTURE_SPEC:-1}" != "0" ] && [ -f "$SPEC_PY" ]; then
  python3 "$SPEC_PY" "$REF_DIR" >/dev/null
fi
