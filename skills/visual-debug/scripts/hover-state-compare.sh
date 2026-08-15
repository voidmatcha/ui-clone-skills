#!/usr/bin/env bash
# hover-state-compare.sh — 60fps video compare per hover-driven motion arc.
#
# Why this exists:
#   transition-compare.sh captures idle/hover as two screenshots and AE-diffs
#   the resting frames. The motion arc between them — button-press scale curve,
#   icon-morph easing, color-transition velocity — is invisible to that check.
#   Same bug class as video-motion-compare for scroll/splash motion: an impl
#   with same end state but different easing or duration passes the static
#   check while still feeling wrong to a user.
#
# This script:
#   - Reads regions.json for entries with a hover triggerType.
#   - Caps targets at MAX_HOVER_TARGETS (capture-transitions.md already dedupes
#     before saving, so first-N in document order is a reasonable sample).
#   - Runs scripts/verify/video-transition-compare.sh in `hover:<selector>` mode
#     per target — real-mouse hover via agent-browser, recorded at 60fps,
#     frame-by-frame SSIM compare.
#
# Usage:
#   bash hover-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>
#
# Env:
#   MAX_HOVER_TARGETS=5    — cap on hover targets evaluated (default 5)
#   HOVER_EXIT_CAPTURE=0   — set to 1 to use `hover-and-out:<sel>` mode, which
#                            records entry AND exit arcs in one video (total
#                            duration ≈ 2 × RECORD_DURATION). Off by default
#                            because most hover designs are symmetric (the exit
#                            transition reverses the entry curve), so the extra
#                            recording time rarely surfaces new bugs. Enable
#                            when the site uses Webflow IX2 "On Mouse Leave"
#                            handlers, distinct exit easing in the CSS, or a
#                            group-hover unwind chain that the entry sweep
#                            cannot exercise.
#   VIEWPORTS=""           — comma-separated WxH list (e.g.
#                            "375x812,1280x800,1920x1080"). When set, the
#                            target loop runs once per viewport; results land
#                            in <ref-dir>/transitions/hover-state/<WxH>/<name>/.
#                            Default empty = single-viewport (back-compat); the
#                            inner script's VIEW_W/VIEW_H apply. Comprehensive-
#                            tier callers should pass the four
#                            verification-plan.json viewports to catch
#                            responsive hover regressions (mobile has no
#                            :hover; tablet collapses hover-to-tap; desktop
#                            fires the actual arc).
#
# Output: <ref-dir>/transitions/hover-state-result.txt (❌ on any failure)

set -uo pipefail

# W-4 (loop-ebpb-0): the reference follows prefers-color-scheme — a host
# OS theme flip (macOS auto-dark in the evening) silently captured the ref
# in dark mode and poisoned an entire compare cycle (footer dSSIM
# 0.0000065 -> 0.687 reading as catastrophic regression). Pin light unless
# the caller explicitly overrides.
: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME

# Source the timeout shim so macOS gets a working `timeout` cmd even when
# coreutils isn't installed. See scripts/lib/timeout-shim.sh.
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SHIM="$_SCRIPT_DIR/../../../scripts/lib/timeout-shim.sh"
[ -f "$_SHIM" ] && . "$_SHIM" || true

# Navigation watchdog: shadows `agent-browser` so the pre-flight
# `agent-browser --session X open <ref-url>` probe fails fast on a
# dead/unreachable URL (UI_CLONE_AB_OPEN_TIMEOUT, default 30s) instead of
# hanging this gate. See lib/ab-timeout.sh header.
# shellcheck source=lib/ab-timeout.sh
[ -f "$_SCRIPT_DIR/lib/ab-timeout.sh" ] && . "$_SCRIPT_DIR/lib/ab-timeout.sh" || true

MAX_HOVER_TARGETS="${MAX_HOVER_TARGETS:-5}"
HOVER_EXIT_CAPTURE="${HOVER_EXIT_CAPTURE:-0}"
VIEWPORTS="${VIEWPORTS:-}"

# Select hover action mode based on opt-in flag. video-transition-compare.sh
# accepts both `hover:<sel>` and `hover-and-out:<sel>`.
if [ "$HOVER_EXIT_CAPTURE" = "1" ]; then
  HOVER_MODE_PREFIX="hover-and-out"
else
  HOVER_MODE_PREFIX="hover"
fi

ORIG_URL="${1:?Usage: hover-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
IMPL_URL="${2:?Usage: hover-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
SESSION="${3:?Usage: hover-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"
REF_DIR="${4:?Usage: hover-state-compare.sh <orig-url> <impl-url> <session> <ref-dir>}"

if [[ "$REF_DIR" != /* ]]; then
  REF_DIR="$(pwd)/$REF_DIR"
fi

PROJECT_ROOT="${PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${UI_CLONE_ROOT:-}}}}"
if [ -z "$PROJECT_ROOT" ]; then
  PROJECT_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
fi
COMPARE="$PROJECT_ROOT/scripts/verify/video-transition-compare.sh"
CLEANUP_SESSIONS="$PROJECT_ROOT/scripts/verify/cleanup-sessions.sh"
FRAME_ALIGN="$PROJECT_ROOT/scripts/verify/lib/frame-align.sh"
REFERENCE_SELF_CALIBRATOR="${HOVER_REFERENCE_SELF_CALIBRATOR:-$PROJECT_ROOT/scripts/verify/lib/reference_self_calibration.py}"

# Keep selector-driven hover recordings focused on the control arc instead of
# a timer/video backdrop phase. The inner comparator applies the same bounded,
# symmetric dynamic mask as scroll-position capture and protects the hover
# target itself when it lives inside a declared dynamic region.
# shellcheck source=../../../scripts/verify/lib/position-compare.sh
. "$PROJECT_ROOT/scripts/verify/lib/position-compare.sh"
VIDEO_COMPARE_DYNAMIC_SELECTORS="$(dynamic_selectors_from_spec "$REF_DIR/transition-spec.json")"
export VIDEO_COMPARE_DYNAMIC_SELECTORS

TEMP_FILES=()
ACTIVE_HOVER_SESSION_PREFIXES=()

track_temp_file() {
  TEMP_FILES+=("$1")
}

register_hover_session() {
  local prefix="$1"
  local current
  if [ "${#ACTIVE_HOVER_SESSION_PREFIXES[@]}" -gt 0 ]; then
    for current in "${ACTIVE_HOVER_SESSION_PREFIXES[@]}"; do
      [ "$current" = "$prefix" ] && return 0
    done
  fi
  ACTIVE_HOVER_SESSION_PREFIXES+=("$prefix")
}

unregister_hover_session() {
  local prefix="$1"
  local current
  local remaining=()
  if [ "${#ACTIVE_HOVER_SESSION_PREFIXES[@]}" -gt 0 ]; then
    for current in "${ACTIVE_HOVER_SESSION_PREFIXES[@]}"; do
      [ "$current" = "$prefix" ] || remaining+=("$current")
    done
  fi
  if [ "${#remaining[@]}" -gt 0 ]; then
    ACTIVE_HOVER_SESSION_PREFIXES=("${remaining[@]}")
  else
    ACTIVE_HOVER_SESSION_PREFIXES=()
  fi
}

if [ ! -f "$COMPARE" ]; then
  echo "ERROR: video-transition-compare.sh not found at $COMPARE" >&2
  exit 2
fi

cleanup_hover_sessions() {  # <owned-session-prefix>
  if [ ! -f "$CLEANUP_SESSIONS" ]; then
    echo "hover-state: session cleanup helper not found: $CLEANUP_SESSIONS" >&2
    return 2
  fi
  local cleanup_attempt=1
  local cleanup_status=1
  while [ "$cleanup_attempt" -le 3 ]; do
    if bash "$CLEANUP_SESSIONS" "$1" >/dev/null 2>&1; then
      unregister_hover_session "$1"
      return 0
    else
      cleanup_status=$?
    fi
    cleanup_attempt=$((cleanup_attempt + 1))
    [ "$cleanup_attempt" -le 3 ] && sleep 0.2
  done
  return "$cleanup_status"
}

# shellcheck disable=SC2329  # invoked by the EXIT trap below
cleanup_hover_state_on_exit() {
  local status=$?
  local prefix
  trap - EXIT INT TERM
  if [ "${#ACTIVE_HOVER_SESSION_PREFIXES[@]}" -gt 0 ]; then
    for prefix in "${ACTIVE_HOVER_SESSION_PREFIXES[@]}"; do
      cleanup_hover_sessions "$prefix" >/dev/null 2>&1 || true
    done
  fi
  if [ "${#TEMP_FILES[@]}" -gt 0 ]; then
    rm -f -- "${TEMP_FILES[@]}"
  fi
  exit "$status"
}

trap cleanup_hover_state_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

capture_retry_reason() {  # <attempt-dir>
  python3 - "$1/capture-retry.json" <<'PY'
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
reason = payload.get("reason")
if payload.get("status") != "retryable-unmeasurable" or not isinstance(reason, str):
    raise SystemExit(1)
print(reason)
PY
}

validate_contiguous_frames() {  # <frame-dir> <offset> <expected-rows>
  local frame_dir="$1"
  local offset="$2"
  local expected_rows="$3"
  local row frame
  for ((row = 1; row <= expected_rows; row++)); do
    frame=$(printf "%s/f-%06d.png" "$frame_dir" $((row + offset)))
    [ -f "$frame" ] || return 1
  done
}

clear_attempt_evidence() {  # <attempt-dir>
  local attempt_dir="$1"
  mkdir -p "$attempt_dir"
  rm -f \
    "$attempt_dir/capture-retry.json" \
    "$attempt_dir/result.txt" \
    "$attempt_dir/target-rect-ref.json" \
    "$attempt_dir/target-rect-impl.json" \
    "$attempt_dir/reference-self-calibration.json" \
    "$attempt_dir/reference-self-complementary-calibration.json"
  rm -rf \
    "$attempt_dir/diff-frames" \
    "$attempt_dir/ref-frames" \
    "$attempt_dir/impl-frames" \
    "$attempt_dir/ref-delta-frames" \
    "$attempt_dir/impl-delta-frames" \
    "$attempt_dir/ref-video" \
    "$attempt_dir/impl-video"
}

complementary_provenance_allowed() {  # <trigger-type>
  [ "$1" = "css-hover" ] || [ "$1" = "synth-hover-css" ]
}

run_reference_self_calibration() {  # <first-dir> <retry-dir> [mode] [trigger-type] [provenance]
  local first_dir="$1"
  local retry_dir="$2"
  local mode="${3:-early}"
  local cal_trigger_type="${4:-}"
  local cal_provenance="${5:-$cal_trigger_type}"
  local out="$retry_dir/reference-self-calibration.json"
  local threshold="${SSIM_THRESHOLD:-0.90}"
  if [ "$mode" = "complementary" ]; then
    out="$retry_dir/reference-self-complementary-calibration.json"
  elif [ "$mode" = "static-discrete" ]; then
    out="$retry_dir/static-discrete-hover-state-calibration.json"
  fi
  rm -f "$out"
  [ -f "$FRAME_ALIGN" ] || return 2
  [ -f "$REFERENCE_SELF_CALIBRATOR" ] || return 2
  # shellcheck source=/dev/null
  source "$FRAME_ALIGN"
  local first_ref="$first_dir/ref-delta-frames"
  local retry_ref="$retry_dir/ref-delta-frames"
  local first_timing="$first_dir/ref-frames"
  local retry_timing="$retry_dir/ref-frames"
  local first_fc retry_fc first_total retry_total
  local self_series="$retry_dir/diff-frames/reference-self-ssim.txt"
  local first_cross_series="$first_dir/diff-frames/target-raw-ssim.txt"
  local cross_series="$retry_dir/diff-frames/target-raw-ssim.txt"
  first_fc=$(cat "$first_timing/.first-change" 2>/dev/null) || return 2
  retry_fc=$(cat "$retry_timing/.first-change" 2>/dev/null) || return 2
  [[ "$first_fc" =~ ^[0-9]+$ && "$retry_fc" =~ ^[0-9]+$ ]] || return 2
  CAL_FIRST_OFF=$((first_fc - 1))
  CAL_RETRY_OFF=$((retry_fc - 1))
  first_total=$(find "$first_ref" -maxdepth 1 -type f -name 'f-*.png' | wc -l | tr -d ' ')
  retry_total=$(find "$retry_ref" -maxdepth 1 -type f -name 'f-*.png' | wc -l | tr -d ' ')
  CAL_EXPECTED_ROWS=$((first_total - CAL_FIRST_OFF < retry_total - CAL_RETRY_OFF ? first_total - CAL_FIRST_OFF : retry_total - CAL_RETRY_OFF))
  [ "$CAL_EXPECTED_ROWS" -gt 0 ] || return 2
  validate_contiguous_frames "$first_ref" "$CAL_FIRST_OFF" "$CAL_EXPECTED_ROWS" || return 2
  validate_contiguous_frames "$retry_ref" "$CAL_RETRY_OFF" "$CAL_EXPECTED_ROWS" || return 2
  CAL_SELF_SERIES="$self_series"
  CAL_FIRST_CROSS_SERIES="$first_cross_series"
  CAL_CROSS_SERIES="$cross_series"
  CAL_FIRST_CAPTURE="$first_dir/capture-retry.json"
  CAL_RETRY_CAPTURE="$retry_dir/capture-retry.json"
  CAL_FIRST_REF_TARGET="$first_dir/ref-video/target-rect.raw.json"
  CAL_FIRST_IMPL_TARGET="$first_dir/impl-video/target-rect.raw.json"
  CAL_RETRY_REF_TARGET="$retry_dir/ref-video/target-rect.raw.json"
  CAL_RETRY_IMPL_TARGET="$retry_dir/impl-video/target-rect.raw.json"
  CAL_FIRST_REF_ACTION="$first_dir/ref-video/hover-action.raw.json"
  CAL_FIRST_IMPL_ACTION="$first_dir/impl-video/hover-action.raw.json"
  CAL_RETRY_REF_ACTION="$retry_dir/ref-video/hover-action.raw.json"
  CAL_RETRY_IMPL_ACTION="$retry_dir/impl-video/hover-action.raw.json"
  CAL_FIRST_REF_SOURCE="$first_dir/ref-video/source-metadata.json"
  CAL_FIRST_IMPL_SOURCE="$first_dir/impl-video/source-metadata.json"
  CAL_RETRY_REF_SOURCE="$retry_dir/ref-video/source-metadata.json"
  CAL_RETRY_IMPL_SOURCE="$retry_dir/impl-video/source-metadata.json"
  CAL_FIRST_REF_RAW="$first_dir/ref-video/raw.webm"
  CAL_FIRST_IMPL_RAW="$first_dir/impl-video/raw.webm"
  CAL_RETRY_REF_RAW="$retry_dir/ref-video/raw.webm"
  CAL_RETRY_IMPL_RAW="$retry_dir/impl-video/raw.webm"
  CAL_STANDARD_RECEIPT="$retry_dir/reference-self-calibration.json"
  CAL_FIRST_ATTEMPT="${first_dir##*/}"
  CAL_RETRY_ATTEMPT="${retry_dir##*/}"
  CAL_ACTION="${HOVER_MODE_PREFIX}:${SELECTOR}"
  CAL_TRIGGER_TYPE="$cal_trigger_type"
  CAL_PROVENANCE="$cal_provenance"
  mkdir -p "$(dirname "$self_series")"
  compute_ssim_series \
    "$first_ref" "$CAL_FIRST_OFF" \
    "$retry_ref" "$CAL_RETRY_OFF" \
    "$CAL_EXPECTED_ROWS" "${UI_CLONE_VMC_JITTER_FRAMES:-1}" "$threshold" \
    > "$self_series"
  local calibrator_args=(
    --reference-self-series "$self_series"
    --first-cross-series "$first_cross_series"
    --retry-cross-series "$cross_series"
    --threshold "$threshold"
    --expected-rows "$CAL_EXPECTED_ROWS"
    --first-offset "$CAL_FIRST_OFF"
    --retry-offset "$CAL_RETRY_OFF"
    --first-attempt "$CAL_FIRST_ATTEMPT"
    --retry-attempt "$CAL_RETRY_ATTEMPT"
    --action "$CAL_ACTION"
    --first-capture-retry "$first_dir/capture-retry.json"
    --retry-capture-retry "$retry_dir/capture-retry.json"
    --out "$out"
  )
  if [ "$mode" = "complementary" ]; then
    calibrator_args+=(
      --complementary
      --first-ref-target "$CAL_FIRST_REF_TARGET"
      --first-impl-target "$CAL_FIRST_IMPL_TARGET"
      --retry-ref-target "$CAL_RETRY_REF_TARGET"
      --retry-impl-target "$CAL_RETRY_IMPL_TARGET"
      --trigger-type "$CAL_TRIGGER_TYPE"
      --provenance "$CAL_PROVENANCE"
    )
  elif [ "$mode" = "static-discrete" ]; then
    calibrator_args+=(
      --static-discrete
      --first-ref-target "$CAL_FIRST_REF_TARGET"
      --first-impl-target "$CAL_FIRST_IMPL_TARGET"
      --retry-ref-target "$CAL_RETRY_REF_TARGET"
      --retry-impl-target "$CAL_RETRY_IMPL_TARGET"
      --first-ref-action "$CAL_FIRST_REF_ACTION"
      --first-impl-action "$CAL_FIRST_IMPL_ACTION"
      --retry-ref-action "$CAL_RETRY_REF_ACTION"
      --retry-impl-action "$CAL_RETRY_IMPL_ACTION"
      --first-ref-source-metadata "$CAL_FIRST_REF_SOURCE"
      --first-impl-source-metadata "$CAL_FIRST_IMPL_SOURCE"
      --retry-ref-source-metadata "$CAL_RETRY_REF_SOURCE"
      --retry-impl-source-metadata "$CAL_RETRY_IMPL_SOURCE"
      --standard-calibration-receipt "$CAL_STANDARD_RECEIPT"
    )
  fi
  if [ -n "${HOVER_REFERENCE_SELF_CALIBRATOR:-}" ]; then
    "$REFERENCE_SELF_CALIBRATOR" "${calibrator_args[@]}"
  else
    python3 "$REFERENCE_SELF_CALIBRATOR" "${calibrator_args[@]}"
  fi
}

valid_reference_self_calibration() {  # <receipt> [pass|clean-divergence]
  local expected="${2:-pass}"
  python3 - \
    "$1" "$expected" "$CAL_SELF_SERIES" "$CAL_CROSS_SERIES" \
    "$CAL_FIRST_CROSS_SERIES" \
    "$CAL_FIRST_CAPTURE" "$CAL_RETRY_CAPTURE" \
    "$CAL_FIRST_REF_TARGET" "$CAL_FIRST_IMPL_TARGET" \
    "$CAL_RETRY_REF_TARGET" "$CAL_RETRY_IMPL_TARGET" \
    "$CAL_FIRST_REF_ACTION" "$CAL_FIRST_IMPL_ACTION" \
    "$CAL_RETRY_REF_ACTION" "$CAL_RETRY_IMPL_ACTION" \
    "$CAL_FIRST_REF_SOURCE" "$CAL_FIRST_IMPL_SOURCE" \
    "$CAL_RETRY_REF_SOURCE" "$CAL_RETRY_IMPL_SOURCE" \
    "$CAL_STANDARD_RECEIPT" \
    "$CAL_FIRST_REF_RAW" "$CAL_FIRST_IMPL_RAW" \
    "$CAL_RETRY_REF_RAW" "$CAL_RETRY_IMPL_RAW" \
    "${SSIM_THRESHOLD:-0.90}" \
    "$CAL_EXPECTED_ROWS" "$CAL_FIRST_OFF" "$CAL_RETRY_OFF" \
    "$CAL_FIRST_ATTEMPT" "$CAL_RETRY_ATTEMPT" "$CAL_ACTION" \
    "${CAL_TRIGGER_TYPE:-}" "${CAL_PROVENANCE:-}" <<'PY'
import hashlib
import json
import math
import sys
from fractions import Fraction

def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def values(path):
    parsed = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                parsed.append(float(line))
    return parsed

def failure_rows(series, threshold):
    return [index + 1 for index, value in enumerate(series) if value < threshold]

try:
    (
        receipt, expected, self_series, cross_series, first_cross_series,
        first_capture, retry_capture, first_ref_target, first_impl_target,
        retry_ref_target, retry_impl_target, first_ref_action, first_impl_action,
        retry_ref_action, retry_impl_action, first_ref_source, first_impl_source,
        retry_ref_source, retry_impl_source, standard_receipt,
        first_ref_raw, first_impl_raw, retry_ref_raw, retry_impl_raw,
        threshold, expected_rows,
        first_offset, retry_offset, first_attempt, retry_attempt, action,
        trigger_type, provenance,
    ) = sys.argv[1:]
    payload = json.load(open(receipt, encoding="utf-8"))
    expected_rows = int(expected_rows)
    first_offset = int(first_offset)
    retry_offset = int(retry_offset)
    threshold = float(threshold)
    selector = action.split(":", 1)[1] if ":" in action else action
    if not selector:
        raise SystemExit(1)
    self_values = values(self_series)
    first_cross_values = values(first_cross_series)
    cross_values = values(cross_series)
    self_rows = len(self_values)
    first_cross_rows = len(first_cross_values)
    cross_rows = len(cross_values)
    first_failure_rows = failure_rows(first_cross_values, threshold)
    retry_failure_rows = failure_rows(cross_values, threshold)
    first_payload = json.load(open(first_capture, encoding="utf-8"))
    retry_payload = json.load(open(retry_capture, encoding="utf-8"))
    target_paths = {
        "firstRef": first_ref_target,
        "firstImpl": first_impl_target,
        "retryRef": retry_ref_target,
        "retryImpl": retry_impl_target,
    }
    target_payloads = {}
    if all(__import__("os").path.exists(path) for path in target_paths.values()):
        target_payloads = {
            name: json.load(open(path, encoding="utf-8"))
            for name, path in target_paths.items()
        }
    action_paths = {
        "firstRef": first_ref_action,
        "firstImpl": first_impl_action,
        "retryRef": retry_ref_action,
        "retryImpl": retry_impl_action,
    }
    source_paths = {
        "first": {"ref": first_ref_source, "impl": first_impl_source},
        "retry": {"ref": retry_ref_source, "impl": retry_impl_source},
    }
    standard_payload = json.load(open(standard_receipt, encoding="utf-8")) if __import__("os").path.exists(standard_receipt) else {}
except Exception:
    raise SystemExit(1)

def unwrap(value):
    for _ in range(4):
        if isinstance(value, dict) and isinstance(value.get("data"), dict) and "result" in value["data"]:
            value = value["data"]["result"]
            continue
        if isinstance(value, str):
            value = json.loads(value)
            continue
        break
    return value

def target_summary(payload):
    payload = unwrap(payload)
    rect = payload.get("rect") if isinstance(payload, dict) else None
    transition = payload.get("transition") if isinstance(payload, dict) else None
    return {
        "found": payload.get("found") if isinstance(payload, dict) else None,
        "selector": payload.get("selector") if isinstance(payload, dict) else None,
        "matchIndex": payload.get("matchIndex") if isinstance(payload, dict) else None,
        "matchCount": payload.get("matchCount") if isinstance(payload, dict) else None,
        "rect": {
            "x": rect.get("x") if isinstance(rect, dict) else None,
            "y": rect.get("y") if isinstance(rect, dict) else None,
            "width": rect.get("width") if isinstance(rect, dict) else None,
            "height": rect.get("height") if isinstance(rect, dict) else None,
        },
        "transition": {
            "property": transition.get("property") if isinstance(transition, dict) else None,
            "duration": transition.get("duration") if isinstance(transition, dict) else None,
            "delay": transition.get("delay") if isinstance(transition, dict) else None,
            "timingFunction": transition.get("timingFunction") if isinstance(transition, dict) else None,
        },
        "state": payload.get("state") if isinstance(payload.get("state"), dict) else None,
    }

def parse_number_list(raw):
    if not isinstance(raw, str) or not raw:
        return None
    values = []
    for part in raw.split(","):
        try:
            value = float(part.strip())
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        values.append(value)
    return values

def parse_css_list(raw):
    if not isinstance(raw, str) or not raw:
        return None
    parts = []
    depth = 0
    start = 0
    for index, char in enumerate(raw):
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return None
            depth -= 1
        elif char == "," and depth == 0:
            part = raw[start:index].strip()
            if not part:
                return None
            parts.append(part)
            start = index + 1
    if depth != 0:
        return None
    final = raw[start:].strip()
    if not final:
        return None
    parts.append(final)
    return tuple(parts)

def transition_contract_key(transition):
    if not isinstance(transition, dict):
        return None
    properties = parse_css_list(transition.get("property"))
    durations = parse_number_list(transition.get("duration"))
    delays = parse_number_list(transition.get("delay"))
    timings = parse_css_list(transition.get("timingFunction"))
    if not properties or not durations or not delays or not timings:
        return None
    count = len(properties)
    effective_durations = tuple(
        durations[index % len(durations)] for index in range(count)
    )
    if max(effective_durations) <= 0:
        return None
    return (
        properties,
        effective_durations,
        tuple(delays[index % len(delays)] for index in range(count)),
        tuple(timings[index % len(timings)] for index in range(count)),
    )

def hover_rect_delta(idle_rect, hover_rect):
    rects = []
    for rect in (idle_rect, hover_rect):
        if not isinstance(rect, dict):
            return None
        raw_values = tuple(rect.get(key) for key in ("x", "y", "width", "height"))
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in raw_values
        ):
            return None
        values = tuple(float(value) for value in raw_values)
        if values[2] <= 0 or values[3] <= 0:
            return None
        rects.append(values)
    idle, hover = rects
    epsilon = 1e-6
    if (
        max(idle[2], hover[2]) / min(idle[2], hover[2]) > 1.25 + epsilon
        or max(idle[3], hover[3]) / min(idle[3], hover[3]) > 1.25 + epsilon
        or abs(hover[0] - idle[0]) > max(idle[2], hover[2]) * 0.25 + epsilon
        or abs(hover[1] - idle[1]) > max(idle[3], hover[3]) * 0.25 + epsilon
    ):
        return None
    return tuple(hover[index] - idle[index] for index in range(4))

def hover_rect_deltas_match(deltas):
    if not deltas:
        return False
    expected = deltas[0]
    return all(
        all(abs(value - expected[index]) <= 1e-6 for index, value in enumerate(delta))
        for delta in deltas[1:]
    )

target_block = {}
if target_payloads:
    target_block = {
        name: {"sha256": sha256(path), "payload": target_summary(target_payloads[name])}
        for name, path in target_paths.items()
    }
target_payloads_ok = set(target_block) == {"firstRef", "firstImpl", "retryRef", "retryImpl"}
target_identity = None
target_transition_key = None
target_dimensions = []
for target_item in target_block.values():
    target_payload = target_item.get("payload")
    rect = target_payload.get("rect") if isinstance(target_payload, dict) else None
    transition = target_payload.get("transition") if isinstance(target_payload, dict) else None
    candidate_transition_key = transition_contract_key(transition)
    identity = {
        "selector": target_payload.get("selector") if isinstance(target_payload, dict) else None,
        "matchIndex": target_payload.get("matchIndex") if isinstance(target_payload, dict) else None,
        "matchCount": target_payload.get("matchCount") if isinstance(target_payload, dict) else None,
    }
    width = rect.get("width") if isinstance(rect, dict) else None
    height = rect.get("height") if isinstance(rect, dict) else None
    if (
        not isinstance(target_payload, dict)
        or target_payload.get("found") is not True
        or target_payload.get("selector") != selector
        or not isinstance(rect, dict)
        or isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, (int, float))
        or not isinstance(height, (int, float))
        or not math.isfinite(float(width))
        or not math.isfinite(float(height))
        or float(width) <= 0
        or float(height) <= 0
        or candidate_transition_key is None
    ):
        target_payloads_ok = False
        break
    target_identity = identity if target_identity is None else target_identity
    target_transition_key = (
        candidate_transition_key
        if target_transition_key is None
        else target_transition_key
    )
    if identity != target_identity or candidate_transition_key != target_transition_key:
        target_payloads_ok = False
        break
    target_dimensions.append((float(width), float(height)))
if target_payloads_ok and target_dimensions:
    target_widths = [item[0] for item in target_dimensions]
    target_heights = [item[1] for item in target_dimensions]
    target_payloads_ok = (
        min(target_widths) > 0
        and min(target_heights) > 0
        and max(target_widths) / min(target_widths) <= 1.25
        and max(target_heights) / min(target_heights) <= 1.25
    )
action_block = {}
if all(__import__("os").path.exists(path) for path in action_paths.values()):
    action_block = {
        name: {
            "sha256": sha256(path),
            "payload": target_summary(json.load(open(path, encoding="utf-8"))),
        }
        for name, path in action_paths.items()
    }
source_block = {}
if all(__import__("os").path.exists(path) for sides in source_paths.values() for path in sides.values()):
    source_block = {
        attempt: {
            side: {
                "sha256": sha256(path),
                "payload": json.load(open(path, encoding="utf-8")),
            }
            for side, path in sides.items()
        }
        for attempt, sides in source_paths.items()
    }
raw_paths = {
    "first": {"ref": first_ref_raw, "impl": first_impl_raw},
    "retry": {"ref": retry_ref_raw, "impl": retry_impl_raw},
}

WATCHED = (
    "color", "backgroundColor", "borderTopColor", "borderRightColor",
    "borderBottomColor", "borderLeftColor", "opacity", "transform", "filter",
    "boxShadow", "textDecorationLine", "textDecorationColor", "fontWeight",
    "letterSpacing",
)
CSS_MAP = {
    "color": ("color",),
    "background": ("backgroundColor",),
    "background-color": ("backgroundColor",),
    "border": ("borderTopColor", "borderRightColor", "borderBottomColor", "borderLeftColor"),
    "border-top-color": ("borderTopColor",),
    "border-right-color": ("borderRightColor",),
    "border-bottom-color": ("borderBottomColor",),
    "border-left-color": ("borderLeftColor",),
    "border-color": ("borderTopColor", "borderRightColor", "borderBottomColor", "borderLeftColor"),
    "opacity": ("opacity",),
    "transform": ("transform",),
    "filter": ("filter",),
    "box-shadow": ("boxShadow",),
    "text-decoration-line": ("textDecorationLine",),
    "text-decoration-color": ("textDecorationColor",),
    "font-weight": ("fontWeight",),
    "letter-spacing": ("letterSpacing",),
}

def declared_transition_duration_ms(transition, changed_keys, idle_style, hover_style):
    transition_key = transition_contract_key(transition)
    if transition_key is None or not changed_keys:
        return None
    properties, durations, delays, _ = transition_key
    effective = []
    covered_changed_keys = set()
    for prop, duration, delay in zip(properties, durations, delays):
        prop = prop.lower()
        if prop == "all":
            mapped = set(WATCHED)
        elif prop in CSS_MAP:
            mapped = set(CSS_MAP[prop])
        else:
            return None
        if prop == "color":
            for key in (
                "borderTopColor", "borderRightColor", "borderBottomColor",
                "borderLeftColor", "textDecorationColor",
            ):
                if idle_style[key] == idle_style["color"] and hover_style[key] == hover_style["color"]:
                    mapped.add(key)
        changed_for_property = changed_keys.intersection(mapped)
        if duration <= 0 or not changed_for_property:
            continue
        effective_ms = max(0.0, duration + delay) * 1000.0
        if effective_ms <= 0:
            return None
        effective.append(effective_ms)
        covered_changed_keys.update(changed_for_property)
    if covered_changed_keys != changed_keys or not effective:
        return None
    return max(effective)

def source_bins(rows, ratio, offset=0):
    return sorted({((offset + row - 1) // ratio) + 1 for row in rows})

def attempt_extracted_fps(attempt):
    sides = source_block.get(attempt, {})
    values = []
    for side in ("ref", "impl"):
        item = sides.get(side)
        payload = item.get("payload") if isinstance(item, dict) else None
        if not isinstance(payload, dict):
            return None
        value = payload.get("extractedFps")
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) <= 0
            or not float(value).is_integer()
        ):
            return None
        values.append(float(value))
    return values[0] if values[0] == values[1] else None

def normalize_ancestor_class_path(path):
    if (
        not isinstance(path, list)
        or not path
        or len(path) > 6
        or any(not isinstance(raw, str) or not raw for raw in path)
    ):
        return None
    normalized = []
    for raw in path:
        if raw.startswith("."):
            return None
        if "#" in raw:
            normalized.append(raw)
            continue
        tag, *classes = raw.split(".")
        if not tag:
            return None
        kept = [
            token
            for token in classes
            if not (token.startswith("h_") and token[2:].isdigit())
        ]
        normalized.append(".".join([tag, *kept]) if kept else tag)
    return normalized

def state_of(path):
    payload = unwrap(json.load(open(path, encoding="utf-8")))
    state = payload.get("state") if isinstance(payload, dict) else None
    watched = state.get("watchedStyle") if isinstance(state, dict) else None
    ancestors = normalize_ancestor_class_path(
        state.get("ancestorClassPath") if isinstance(state, dict) else None
    )
    if not isinstance(watched, dict) or ancestors is None:
        return None
    bounded = {key: watched.get(key) for key in WATCHED}
    if any(not isinstance(value, str) for value in bounded.values()):
        return None
    return {"watchedStyle": bounded, "ancestorClassPath": ancestors}

def static_validator_ok():
    if not source_block or not action_block or not target_payloads_ok:
        return False
    source_ratio = None
    for attempt, sides in source_block.items():
        for side, item in sides.items():
            source_payload = item.get("payload")
            if not isinstance(source_payload, dict):
                return False
            if source_payload.get("rawWebmSha256") != sha256(raw_paths[attempt][side]):
                return False
            ratio = source_payload.get("sourceToExtractedRatio")
            source_fps = source_payload.get("sourceFps")
            extracted_fps = source_payload.get("extractedFps")
            extracted_fps_number = (
                float(extracted_fps)
                if isinstance(extracted_fps, (int, float))
                and not isinstance(extracted_fps, bool)
                and math.isfinite(float(extracted_fps))
                and float(extracted_fps) > 0
                and float(extracted_fps).is_integer()
                else None
            )
            try:
                r_rate = Fraction(source_payload.get("rFrameRate"))
                avg_rate = Fraction(source_payload.get("avgFrameRate"))
            except Exception:
                return False
            if (
                source_payload.get("cfr") is not True
                or r_rate <= 0
                or avg_rate <= 0
                or r_rate != avg_rate
                or not isinstance(source_fps, (int, float))
                or isinstance(source_fps, bool)
                or not math.isfinite(float(source_fps))
                or abs(float(source_fps) - float(r_rate)) > 1e-6
                or not isinstance(ratio, int)
                or isinstance(ratio, bool)
                or ratio <= 0
                or extracted_fps_number is None
                or Fraction(int(extracted_fps_number), 1) / r_rate != ratio
            ):
                return False
            source_ratio = ratio if source_ratio is None else source_ratio
            if source_ratio != ratio:
                return False
    self_failure_rows = failure_rows(self_values, threshold)
    if any(row > expected_rows for row in self_failure_rows):
        return False
    self_clean_or_failures_inside_window = (
        not self_failure_rows
        or all(row <= expected_rows for row in self_failure_rows)
    )
    first_self_bins = source_bins(self_failure_rows, source_ratio, first_offset)
    retry_self_bins = source_bins(self_failure_rows, source_ratio, retry_offset)
    if self_failure_rows:
        if not first_self_bins or not retry_self_bins:
            return False
        if first_self_bins != list(range(first_self_bins[0], first_self_bins[-1] + 1)):
            return False
        if retry_self_bins != list(range(retry_self_bins[0], retry_self_bins[-1] + 1)):
            return False
    first_bins = source_bins(first_failure_rows, source_ratio, first_offset)
    retry_bins = source_bins(retry_failure_rows, source_ratio, retry_offset)
    if not first_bins or not retry_bins:
        return False
    source_subset_ok = set(first_bins).issubset(set(first_self_bins)) and set(retry_bins).issubset(set(retry_self_bins))
    tail_ok = True
    for index, value in enumerate(first_cross_values, start=1):
        if ((first_offset + index - 1) // source_ratio) + 1 not in first_self_bins and value < threshold:
            tail_ok = False
            break
    for index, value in enumerate(cross_values, start=1):
        if ((retry_offset + index - 1) // source_ratio) + 1 not in retry_self_bins and value < threshold:
            tail_ok = False
            break
    early_tail_ok = (
        isinstance(first_payload.get("earlyWindowRows"), int)
        and isinstance(retry_payload.get("earlyWindowRows"), int)
        and all(
            value >= threshold
            for index, value in enumerate(first_cross_values, start=1)
            if index > first_payload["earlyWindowRows"]
        )
        and all(
            value >= threshold
            for index, value in enumerate(cross_values, start=1)
            if index > retry_payload["earlyWindowRows"]
        )
    )
    def runtime_receipt_ok(receipt, values, rows, attempt):
        failures = failure_rows(values, threshold)
        early = receipt.get("earlyWindowRows")
        seconds = receipt.get("earlyWindowSeconds")
        fps = receipt.get("extractedFps")
        source_fps = attempt_extracted_fps(attempt)
        if (
            not isinstance(seconds, (int, float))
            or isinstance(seconds, bool)
            or not math.isfinite(float(seconds))
            or float(seconds) <= 0
            or float(seconds) > 0.5
            or not isinstance(fps, (int, float))
            or isinstance(fps, bool)
            or not math.isfinite(float(fps))
            or float(fps) <= 0
            or not float(fps).is_integer()
            or float(source_fps) != float(fps)
        ):
            return False
        expected_early = max(1, math.ceil(float(seconds) * float(fps)))
        return (
            receipt.get("schemaVersion") == 1
            and receipt.get("status") == "retryable-unmeasurable"
            and receipt.get("reason") == "early-window-capture-phase"
            and receipt.get("selector") == selector
            and receipt.get("threshold") == threshold
            and receipt.get("rows") == rows
            and receipt.get("failureRows") == failures
            and receipt.get("failures") == len(failures)
            and bool(failures)
            and receipt.get("lastFailureRow") == max(failures)
            and receipt.get("firstStablePassingRow") == max(failures) + 1
            and isinstance(early, int)
            and not isinstance(early, bool)
            and early == expected_early
            and rows > early
            and receipt.get("firstStablePassingRow") <= rows
            and max(failures) <= early
        )
    runtime_receipts_ok = runtime_receipt_ok(first_payload, first_cross_values, first_cross_rows, "first") and runtime_receipt_ok(retry_payload, cross_values, cross_rows, "retry")
    runtime_row_count_drift_ok = (
        isinstance(source_ratio, int)
        and not isinstance(source_ratio, bool)
        and source_ratio > 0
        and isinstance(first_payload.get("earlyWindowRows"), int)
        and not isinstance(first_payload.get("earlyWindowRows"), bool)
        and isinstance(retry_payload.get("earlyWindowRows"), int)
        and not isinstance(retry_payload.get("earlyWindowRows"), bool)
        and self_rows == expected_rows
        and first_cross_rows >= first_payload["earlyWindowRows"]
        and cross_rows >= retry_payload["earlyWindowRows"]
        and abs(first_cross_rows - expected_rows) <= source_ratio
        and abs(cross_rows - expected_rows) <= source_ratio
        and abs(first_cross_rows - cross_rows) <= 2 * source_ratio
    )
    idle_states = {name: state_of(path) for name, path in target_paths.items()}
    hover_states = {name: state_of(path) for name, path in action_paths.items()}
    if any(value is None for value in idle_states.values()) or any(value is None for value in hover_states.values()):
        return False
    if any(value != idle_states["firstRef"] for value in idle_states.values()):
        return False
    if any(value != hover_states["firstRef"] for value in hover_states.values()):
        return False
    idle_style = idle_states["firstRef"]["watchedStyle"]
    hover_style = hover_states["firstRef"]["watchedStyle"]
    delta = {key for key in WATCHED if idle_style[key] != hover_style[key]}
    if not delta:
        return False
    def runtime_proof_ok(state_change_mode):
        if state_change_mode not in {"static-discrete", "declared-transition"}:
            return False
        runtime_latencies = []
        for name in action_paths:
            raw_action = unwrap(json.load(open(action_paths[name], encoding="utf-8")))
            proof = raw_action.get("hoverProof") if isinstance(raw_action, dict) else None
            target_payload = target_block[name]["payload"]
            if not isinstance(proof, dict):
                return False
            times = [
                proof.get("armedAt"),
                proof.get("moveAt"),
                proof.get("firstPointerEvent"),
                proof.get("firstCommitRaf"),
                proof.get("firstHoverRaf"),
                proof.get("stableAt"),
            ]
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) for value in times):
                return False
            armed, move, pointer, first_commit, first_hover, stable = [float(value) for value in times]
            if not (armed < move <= pointer <= first_hover <= first_commit <= stable):
                return False
            latency = first_commit - pointer
            stable_count = proof.get("stableHoverRafCount")
            max_active_animation_count = proof.get("maxActiveAnimationCount")
            if (
                proof.get("schemaVersion") != 1
                or proof.get("selector") != target_payload.get("selector")
                or proof.get("matchIndex") != target_payload.get("matchIndex")
                or proof.get("matchCount") != target_payload.get("matchCount")
                or proof.get("pointerObserved") is not True
                or proof.get("rafObserved") is not True
                or proof.get("done") is not True
                or not isinstance(stable_count, int)
                or isinstance(stable_count, bool)
                or stable_count < 2
                or not isinstance(max_active_animation_count, int)
                or isinstance(max_active_animation_count, bool)
            ):
                return False
            initial = proof.get("initial")
            commit = proof.get("commit")
            final = proof.get("final")
            if not isinstance(initial, dict) or not isinstance(commit, dict) or not isinstance(final, dict):
                return False
            if (
                initial.get("watchedStyle") != idle_states[name]["watchedStyle"]
                or normalize_ancestor_class_path(initial.get("ancestorClassPath")) != idle_states[name]["ancestorClassPath"]
                or initial.get("activeAnimationCount") != 0
                or commit.get("watchedStyle") != hover_states[name]["watchedStyle"]
                or normalize_ancestor_class_path(commit.get("ancestorClassPath")) != hover_states[name]["ancestorClassPath"]
                or commit.get("hovered") is not True
                or final.get("watchedStyle") != hover_states[name]["watchedStyle"]
                or normalize_ancestor_class_path(final.get("ancestorClassPath")) != hover_states[name]["ancestorClassPath"]
                or final.get("hovered") is not True
                or sorted(proof.get("changedStyleKeys", [])) != sorted(delta)
            ):
                return False
            mutation_value = proof.get("firstMutation")
            mutation = (
                float(mutation_value)
                if isinstance(mutation_value, (int, float))
                and not isinstance(mutation_value, bool)
                and math.isfinite(float(mutation_value))
                else None
            )
            mutation_snapshot = proof.get("mutation")
            if state_change_mode == "static-discrete":
                if (
                    mutation is None
                    or not (pointer <= mutation <= first_commit)
                    or latency > 50
                    or proof.get("mutationObserved") is not True
                    or max_active_animation_count != 0
                    or not isinstance(mutation_snapshot, dict)
                    or mutation_snapshot.get("time") != proof.get("firstMutation")
                    or normalize_ancestor_class_path(mutation_snapshot.get("ancestorClassPath")) != hover_states[name]["ancestorClassPath"]
                ):
                    return False
            elif state_change_mode == "declared-transition":
                expected_duration_ms = declared_transition_duration_ms(
                    target_payload.get("transition"),
                    set(delta),
                    idle_states[name]["watchedStyle"],
                    hover_states[name]["watchedStyle"],
                )
                if (
                    max_active_animation_count < 1
                    or first_hover - pointer > 50
                    or expected_duration_ms is None
                    or abs(latency - expected_duration_ms) > 50
                ):
                    return False
                if mutation is None:
                    if proof.get("mutationObserved") is not False or mutation_snapshot is not None:
                        return False
                elif (
                    proof.get("mutationObserved") is not True
                    or not isinstance(mutation_snapshot, dict)
                    or not (pointer <= mutation <= first_commit)
                    or mutation_snapshot.get("time") != proof.get("firstMutation")
                    or normalize_ancestor_class_path(mutation_snapshot.get("ancestorClassPath")) != hover_states[name]["ancestorClassPath"]
                ):
                    return False
            runtime_latencies.append(latency)
        return bool(runtime_latencies) and max(runtime_latencies) - min(runtime_latencies) <= 25
    ancestor_delta = (
        idle_states["firstRef"]["ancestorClassPath"],
        hover_states["firstRef"]["ancestorClassPath"],
    )
    if any((idle_states[name]["ancestorClassPath"], hover_states[name]["ancestorClassPath"]) != ancestor_delta for name in idle_states):
        return False
    transition = target_block["firstRef"]["payload"].get("transition")
    target_identity = None
    static_target_transition_key = None
    hover_rect_deltas = []
    for name in target_paths:
        target_payload = target_block[name]["payload"]
        identity = (
            target_payload.get("found"),
            target_payload.get("selector"),
            target_payload.get("matchIndex"),
            target_payload.get("matchCount"),
        )
        candidate_transition_key = transition_contract_key(target_payload.get("transition"))
        if target_identity is None:
            target_identity = identity
            static_target_transition_key = candidate_transition_key
        if (
            identity != target_identity
            or candidate_transition_key is None
            or candidate_transition_key != static_target_transition_key
        ):
            return False
        action_payload = action_block[name]["payload"]
        raw_action = unwrap(json.load(open(action_paths[name], encoding="utf-8")))
        action_rect_delta = hover_rect_delta(
            target_payload.get("rect"),
            action_payload.get("rect"),
        )
        action_transition_key = transition_contract_key(action_payload.get("transition"))
        target_action_transition_key = transition_contract_key(target_payload.get("transition"))
        if (
            action_payload.get("found") is not True
            or raw_action.get("hovered") is not True
            or raw_action.get("pointerReachable") is not True
            or action_payload.get("selector") != target_payload.get("selector")
            or action_payload.get("matchIndex") != target_payload.get("matchIndex")
            or action_payload.get("matchCount") != target_payload.get("matchCount")
            or action_rect_delta is None
            or action_transition_key is None
            or action_transition_key != target_action_transition_key
        ):
            return False
        hover_rect_deltas.append(action_rect_delta)
    if not hover_rect_deltas_match(hover_rect_deltas):
        return False
    prop_raw = transition.get("property") if isinstance(transition, dict) else None
    if not isinstance(prop_raw, str) or not prop_raw.strip():
        return False
    transition_key = transition_contract_key(transition)
    if transition_key is None:
        return False
    declared = set()
    positive_duration_declared = set()
    properties = [part.strip().lower() for part in prop_raw.split(",") if part.strip()]
    for prop, duration in zip(properties, transition_key[1]):
        if prop in {"", "none"}:
            return False
        if prop == "all":
            mapped = WATCHED
        elif prop in CSS_MAP:
            mapped = CSS_MAP[prop]
        else:
            return False
        declared.update(mapped)
        if duration > 0:
            positive_duration_declared.update(mapped)
    if "all" in properties and delta.intersection(declared):
        return False
    covered = set(positive_duration_declared)
    if "color" in positive_duration_declared:
        for key in (
            "borderTopColor", "borderRightColor", "borderBottomColor",
            "borderLeftColor", "textDecorationColor",
        ):
            if idle_style[key] == idle_style["color"] and hover_style[key] == hover_style["color"]:
                covered.add(key)
    changed_declared = delta.intersection(covered)
    if changed_declared and not delta.issubset(covered):
        return False
    state_change_mode = "declared-transition" if changed_declared else "static-discrete"
    runtime_ok = runtime_proof_ok(state_change_mode)
    if standard_payload.get("status") != "reference-self-calibration-failed":
        return False
    metrics_ref = standard_payload.get("metrics", {}).get("referenceSelf", {})
    if metrics_ref.get("failureRows") != self_failure_rows:
        return False
    for receipt_payload in (first_payload, retry_payload):
        if receipt_payload.get("reason") != "early-window-capture-phase":
            return False
    first_ref_arc = first_payload.get("arc", {}).get("ref", {})
    retry_ref_arc = retry_payload.get("arc", {}).get("ref", {})
    if not isinstance(first_ref_arc.get("durationFrames"), int) or not isinstance(retry_ref_arc.get("durationFrames"), int):
        return False
    drift = abs(first_ref_arc["durationFrames"] - retry_ref_arc["durationFrames"])
    arc_ok = True
    for receipt_payload in (first_payload, retry_payload):
        arc = receipt_payload.get("arc", {})
        ref_duration = arc.get("ref", {}).get("durationFrames")
        impl_duration = arc.get("impl", {}).get("durationFrames")
        delta_frames = arc.get("deltaFrames")
        max_delta_frames = arc.get("maxDeltaFrames")
        if not isinstance(ref_duration, int) or not isinstance(impl_duration, int):
            return False
        if not isinstance(delta_frames, int) or not isinstance(max_delta_frames, int):
            return False
        if ref_duration <= 0 or impl_duration <= 0:
            arc_ok = False
            break
        if delta_frames != abs(ref_duration - impl_duration):
            return False
        if arc.get("withinTolerance") != (delta_frames <= max_delta_frames):
            arc_ok = False
            break
        if arc.get("withinTolerance") is not True and delta_frames > drift:
            arc_ok = False
            break
    metrics = payload.get("metrics", {})
    runtime_used = metrics.get("runtimeTimingRelaxationUsed") is True
    if metrics.get("state", {}).get("stateChangeMode") != state_change_mode:
        return False
    if metrics.get("runtimeTimingProofValid") is not runtime_ok:
        return False
    if metrics.get("sourceBinSubsetOfReferenceSelf") is not source_subset_ok:
        return False
    if metrics.get("tailRowsPassingOutsideReferenceSelfBins") is not tail_ok:
        return False
    if metrics.get("earlyWindowTailRowsPassing") is not early_tail_ok:
        return False
    if metrics.get("runtimeCaptureReceiptsValid") is not runtime_receipts_ok:
        return False
    if metrics.get("runtimeRowCountDriftValid") is not runtime_row_count_drift_ok:
        return False
    if metrics.get("referenceSelfCleanOrFailuresInsideExpectedWindow") is not self_clean_or_failures_inside_window:
        return False
    strict_ok = source_subset_ok and tail_ok and arc_ok
    runtime_expected = (
        self_clean_or_failures_inside_window
        and runtime_ok
        and not strict_ok
        and early_tail_ok
        and runtime_receipts_ok
        and runtime_row_count_drift_ok
    )
    if runtime_used != runtime_expected:
        return False
    return strict_ok or runtime_used
row_window_ok = (
    self_rows == expected_rows
    and first_cross_rows >= expected_rows
    and cross_rows >= expected_rows
    and all(row <= expected_rows for row in first_failure_rows)
    and all(row <= expected_rows for row in retry_failure_rows)
)
receipt_series_binding_ok = (
    first_payload.get("rows") == first_cross_rows
    and first_payload.get("failures") == len(first_failure_rows)
    and first_payload.get("failureRows") == first_failure_rows
    and retry_payload.get("rows") == cross_rows
    and retry_payload.get("failures") == len(retry_failure_rows)
    and retry_payload.get("failureRows") == retry_failure_rows
)
bound = (
    payload.get("schemaVersion") == 4
    and payload.get("rule") in {
        "retry-cross-early-window-subset-of-reference-self-capture-phase",
        "mixed-early-window-and-arc-only-capture-phase",
        "static-discrete-hover-state-source-bin-proof",
    }
    and payload.get("threshold") == threshold
    and payload.get("expectedRows") == expected_rows
    and payload.get("attempts") == {
        "first": {"id": first_attempt, "offset": first_offset},
        "retry": {"id": retry_attempt, "offset": retry_offset},
    }
    and payload.get("action") == action
    and payload.get("series") == {
        "referenceSelf": {"sha256": sha256(self_series), "rows": self_rows},
        "firstCross": {
            "sha256": sha256(first_cross_series),
            "rows": first_cross_rows,
        },
        "retryCross": {"sha256": sha256(cross_series), "rows": cross_rows},
    }
    and payload.get("receipts", {}).get("firstCaptureRetry") == {
        "sha256": sha256(first_capture),
        "payload": {
            "schemaVersion": first_payload.get("schemaVersion"),
            "status": first_payload.get("status"),
            "reason": first_payload.get("reason"),
            "selector": first_payload.get("selector"),
            "threshold": first_payload.get("threshold"),
            "rows": first_payload.get("rows"),
            "failures": first_payload.get("failures"),
            "failureRows": first_payload.get("failureRows"),
            "firstStablePassingRow": first_payload.get("firstStablePassingRow"),
            "lastFailureRow": first_payload.get("lastFailureRow"),
            "earlyWindowRows": first_payload.get("earlyWindowRows"),
            "earlyWindowSeconds": first_payload.get("earlyWindowSeconds"),
            "extractedFps": first_payload.get("extractedFps"),
            "arc": first_payload.get("arc"),
            "sourceMetadata": first_payload.get("sourceMetadata"),
        },
    }
    and payload.get("receipts", {}).get("retryCaptureRetry") == {
        "sha256": sha256(retry_capture),
        "payload": {
            "schemaVersion": retry_payload.get("schemaVersion"),
            "status": retry_payload.get("status"),
            "reason": retry_payload.get("reason"),
            "selector": retry_payload.get("selector"),
            "threshold": retry_payload.get("threshold"),
            "rows": retry_payload.get("rows"),
            "failures": retry_payload.get("failures"),
            "failureRows": retry_payload.get("failureRows"),
            "firstStablePassingRow": retry_payload.get("firstStablePassingRow"),
            "lastFailureRow": retry_payload.get("lastFailureRow"),
            "earlyWindowRows": retry_payload.get("earlyWindowRows"),
            "earlyWindowSeconds": retry_payload.get("earlyWindowSeconds"),
            "extractedFps": retry_payload.get("extractedFps"),
            "arc": retry_payload.get("arc"),
            "sourceMetadata": retry_payload.get("sourceMetadata"),
        },
    }
    and (
        payload.get("rule")
        not in {
            "mixed-early-window-and-arc-only-capture-phase",
            "static-discrete-hover-state-source-bin-proof",
        }
        or target_payloads_ok
    )
    and (
        payload.get("rule") != "mixed-early-window-and-arc-only-capture-phase"
        or payload.get("targets") == target_block
    )
    and (
        payload.get("rule") != "mixed-early-window-and-arc-only-capture-phase"
        or payload.get("provenance") == {
            "triggerType": trigger_type,
            "provenance": provenance or trigger_type,
        }
    )
    and (
        payload.get("rule") != "static-discrete-hover-state-source-bin-proof"
        or (
            payload.get("targets") == target_block
            and payload.get("actions") == action_block
            and payload.get("sourceMetadata") == source_block
            and payload.get("receipts", {}).get("referenceSelf") == {
                "sha256": sha256(standard_receipt),
                "status": standard_payload.get("status"),
                "rule": standard_payload.get("rule"),
            }
        )
    )
    and receipt_series_binding_ok
    and (
        payload.get("rule") != "static-discrete-hover-state-source-bin-proof"
        or payload.get("metrics", {}).get("rowCountsCoverExpectedWindow") is True
        or payload.get("metrics", {}).get("runtimeRowCountDriftValid") is True
    )
    and (
        payload.get("rule") == "static-discrete-hover-state-source-bin-proof"
        or (
            row_window_ok
            and payload.get("metrics", {}).get("rowCountsCoverExpectedWindow") is True
        )
    )
)
metrics = payload.get("metrics", {})
reference_self = metrics.get("referenceSelf", {})
retry_cross = metrics.get("retryCross", {})
if (
    bound
    and expected == "pass"
    and payload.get("rule") == "retry-cross-early-window-subset-of-reference-self-capture-phase"
    and payload.get("status") == "pass-after-reference-self-calibration"
    and metrics.get("rowCountsCoverExpectedWindow") is True
    and metrics.get("captureReceiptsValid") is True
):
    raise SystemExit(0)
if (
    bound
    and expected == "complementary"
    and payload.get("rule") == "mixed-early-window-and-arc-only-capture-phase"
    and payload.get("status") == "pass-after-complementary-reference-self-calibration"
    and metrics.get("rowCountsCoverExpectedWindow") is True
    and metrics.get("captureReceiptsValid") is True
    and metrics.get("targetPayloadsValid") is True
    and metrics.get("arcDriftWithinBounds") is True
    and metrics.get("earlyFailureRowsSubsetOfReferenceSelf") is True
    and metrics.get("earlyPostReferenceSelfBlockPassing") is True
    and metrics.get("exactlyMixedReceipts") is True
    and metrics.get("arcOnlyPixelsPassing") is True
    and metrics.get("arcTimingComplementary") is True
    and metrics.get("provenanceValid") is True
):
    raise SystemExit(0)
if (
    bound
    and expected == "static-discrete"
    and payload.get("rule") == "static-discrete-hover-state-source-bin-proof"
    and payload.get("status") == "pass-after-static-discrete-hover-state-calibration"
    and metrics.get("earlyWindowRowsBoundFailureRows") is True
    and metrics.get("earlyWindowTailRowsPassing") is True
    and metrics.get("sourceMetadataValid") is True
    and metrics.get("statePayloadsValid") is True
    and metrics.get("referenceSelfStandardFailed") is True
    and (
        (
            metrics.get("rowCountsCoverExpectedWindow") is True
            and metrics.get("captureReceiptsValid") is True
            and
            metrics.get("sourceBinSubsetOfReferenceSelf") is True
            and metrics.get("tailRowsPassingOutsideReferenceSelfBins") is True
            and metrics.get("arcExplained") is True
        )
        or (
            metrics.get("runtimeTimingRelaxationUsed") is True
            and metrics.get("runtimeCaptureReceiptsValid") is True
            and metrics.get("runtimeRowCountDriftValid") is True
        )
    )
    and static_validator_ok()
):
    raise SystemExit(0)
if (
    bound
    and expected == "clean-divergence"
    and payload.get("rule") == "retry-cross-early-window-subset-of-reference-self-capture-phase"
    and payload.get("status") == "reference-self-calibration-failed"
    and reference_self.get("failures") == 0
    and isinstance(retry_cross.get("failures"), int)
    and retry_cross.get("failures") > 0
    and metrics.get("captureReceiptsValid") is True
):
    raise SystemExit(0)
raise SystemExit(1)
PY
}

REGIONS="$REF_DIR/regions.json"
OUT_DIR="$REF_DIR/transitions/hover-state"
mkdir -p "$OUT_DIR"
RESULT="$REF_DIR/transitions/hover-state-result.txt"

# Scheduling-signal artifacts. verification-plan.sh adds this gate with
# severity=block whenever .signals.hasHover=true, but the realfood/Lenis
# regions.json producer emits a single full-page region with no triggerType,
# so the triggerType jq below finds nothing. Without a cross-check the gate
# would self-certify PASS while the site ships hover motion completely
# unverified. These artifacts independently prove hover exists and also carry
# real selectors we can synthesize targets from.
PLAN="$REF_DIR/verification-plan.json"
HOVER_CSS="$REF_DIR/hover-css-rules.json"
HOVER_CAND="$REF_DIR/hover-candidates.json"
HOVER_MANIFEST="$REF_DIR/states/hover/manifest.json"

# hover_expected — true when the scheduling signal (or any non-empty hover
# artifact) says hover is present, so an empty target list is a real failure
# rather than a legitimate "nothing to compare" skip. On hosts without jq we
# cannot read the JSON, so we return non-zero (no FAIL) — the gate's own
# measurement-row guard is the backstop there.
hover_expected() {
  command -v jq >/dev/null 2>&1 || return 1
  if [ -f "$PLAN" ]; then
    [ "$(jq -r '.signals.hasHover // false' "$PLAN" 2>/dev/null)" = "true" ] && return 0
  fi
  if [ -f "$HOVER_CSS" ]; then
    [ "$(jq -r 'if type=="array" then length else (.rules // [] | length) end' "$HOVER_CSS" 2>/dev/null || echo 0)" -gt 0 ] 2>/dev/null && return 0
  fi
  if [ -f "$HOVER_CAND" ]; then
    [ "$(jq -r 'if type=="array" then length else (.candidates // [] | length) end' "$HOVER_CAND" 2>/dev/null || echo 0)" -gt 0 ] 2>/dev/null && return 0
  fi
  if [ -f "$HOVER_MANIFEST" ]; then
    [ "$(jq -r '.entries // [] | length' "$HOVER_MANIFEST" 2>/dev/null || echo 0)" -gt 0 ] 2>/dev/null && return 0
  fi
  return 1
}

affected_selector_for_hover() {  # <activation-selector>
  python3 - "$REF_DIR/transition-spec.json" "$HOVER_CSS" "$1" <<'PY'
import json
import sys

spec_path, hover_css_path, activation = sys.argv[1:4]

def load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}

def selectors(raw):
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]

spec = load(spec_path)
for item in spec.get("transitions") or []:
    if not isinstance(item, dict):
        continue
    if activation not in selectors(item.get("target")):
        continue
    affected = str(item.get("affectedTarget") or "").strip()
    if affected and affected != activation:
        print(affected)
        raise SystemExit(0)

hover_css = load(hover_css_path)
rules = hover_css if isinstance(hover_css, list) else hover_css.get("rules") or []
for item in rules:
    if not isinstance(item, dict):
        continue
    affected = str(item.get("affected") or "").strip()
    if str(item.get("activation") or "").strip() == activation and affected and affected != activation:
        print(affected)
        raise SystemExit(0)

for item in rules:
    if not isinstance(item, dict):
        continue
    raw = str(item.get("selector") or "").split(",", 1)[0].strip()
    marker = f"{activation}:hover"
    if marker in raw:
        affected = raw.replace(marker, activation, 1).strip()
        if affected and affected != activation:
            print(affected)
            raise SystemExit(0)
PY
}

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
{
  echo "# hover-state-compare"
  echo "# generated: $NOW"
  echo "# max targets: $MAX_HOVER_TARGETS"
  echo "# exit capture: $HOVER_EXIT_CAPTURE (mode: $HOVER_MODE_PREFIX:<sel>)"
  echo
} > "$RESULT"

if [ ! -f "$REGIONS" ]; then
  if hover_expected; then
    echo "❌ regions.json missing but signals.hasHover=true / hover artifacts present — hover motion UNVERIFIED" >> "$RESULT"
    echo "Wrote $RESULT"
    exit 1
  fi
  echo "✅ no regions.json — hover-state compare skipped (verification-plan should not have required this row)" >> "$RESULT"
  echo "Wrote $RESULT"
  exit 0
fi

# Extract (name, triggerType, selector) tuples where triggerType matches the
# hover family. Lenient regex like the verification-plan hasHover signal —
# matches "hover", "css-hover", "scale-on-hover-target", etc.
TARGETS_FILE="$(mktemp)"
track_temp_file "$TARGETS_FILE"

if command -v jq >/dev/null 2>&1; then
  jq -r '
    [.. | objects | select(.triggerType? | type == "string") | select(.triggerType | test("[Hh]over"))]
    | unique_by(.selector)
    | .[0:'"$MAX_HOVER_TARGETS"']
    | .[]
    | "\(.name // .triggerType)\t\(.triggerType)\t\(.selector)"
  ' "$REGIONS" > "$TARGETS_FILE" 2>/dev/null || true
fi

# regions.json carried no triggerType-tagged hover entries (realfood/Lenis
# full-page-only shape). Try to recover real hover targets from the richer
# hover artifacts before deciding this is a skip. Priority: hover-css-rules
# (pure CSS selectors — agent-browser hover accepts them directly) →
# hover-candidates (carry .text/.rect but selectors may be Playwright-syntax
# like `button:text(...)`) → states/hover/manifest. Each branch caps at
# MAX_HOVER_TARGETS and emits the same `name<TAB>triggerType<TAB>selector`
# tuple the run loop below reads.
if [ ! -s "$TARGETS_FILE" ] && command -v jq >/dev/null 2>&1; then
  if [ -f "$HOVER_CSS" ]; then
    # [{selector:".a:hover .b", css, media}]. Reduce ".x:hover .y" /
    # ".x:focus-visible, .x:hover" → first hoverable base selector ".x"
    # (split on first comma, then first colon).
    jq -r '(if type=="array" then . else (.rules // []) end)
      | map(.selector | split(",")[0] | split(":")[0] | gsub("^\\s+|\\s+$";""))
      | map(select(length>0)) | unique | .[0:'"$MAX_HOVER_TARGETS"'] | .[]
      | "\(.)\tsynth-hover-css\t\(.)"' "$HOVER_CSS" 2>/dev/null >> "$TARGETS_FILE" || true
  fi
  if [ ! -s "$TARGETS_FILE" ] && [ -f "$HOVER_CAND" ]; then
    jq -r '(if type=="array" then . else (.candidates // []) end)
      | map(select(.selector!=null)) | unique_by(.selector)
      | .[0:'"$MAX_HOVER_TARGETS"'] | .[]
      | "\((.text // .selector)|gsub("[\\t\\n]";" "))\tsynth-hover-candidate\t\(.selector)"' \
      "$HOVER_CAND" 2>/dev/null >> "$TARGETS_FILE" || true
  fi
  if [ ! -s "$TARGETS_FILE" ] && [ -f "$HOVER_MANIFEST" ]; then
    jq -r '(.entries // []) | map(select(.selector!=null)) | unique_by(.selector)
      | .[0:'"$MAX_HOVER_TARGETS"'] | .[]
      | "\(.selector)\tsynth-hover-manifest\t\(.selector)"' "$HOVER_MANIFEST" 2>/dev/null >> "$TARGETS_FILE" || true
  fi
fi

if [ ! -s "$TARGETS_FILE" ]; then
  if hover_expected; then
    echo "❌ hover expected (signals.hasHover=true / non-empty hover-css-rules.json / hover-candidates.json) but no hover targets resolvable from regions.json or hover artifacts — hover motion UNVERIFIED" >> "$RESULT"
    echo "Wrote $RESULT"
    exit 1
  fi
  echo "✅ no hover regions found and no hasHover signal — nothing to compare" >> "$RESULT"
  echo "Wrote $RESULT"
  exit 0
fi

# ── Documented overlay-gated skips (loop-e2e-5 / codex review) ──
# A hover target that exists only after opening an overlay (lightbox controls,
# mobile-nav menu items) reads "Element not found" on the REF side and
# false-fails as divergence. transition-fires already honors these documented
# skips; consult the SAME narrow evidence here: ONLY exact selectors derived
# from transition-spec entries whose ids appear in asset-substitution.json
# skips[] with a conditionally-mounted reason. No wildcards, no broad
# substitution/origin-lock reuse (bypass risk). Skipped rows are recorded as
# known-skip verdicts, never silently dropped.
SKIP_SELECTORS_FILE="$(mktemp)"
track_temp_file "$SKIP_SELECTORS_FILE"
python3 - "$REF_DIR/transition-spec.json" "$REF_DIR/asset-substitution.json" > "$SKIP_SELECTORS_FILE" 2>/dev/null <<'PY' || true
import json, sys

def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}

spec = load(sys.argv[1])
sub = load(sys.argv[2])
skip_ids = set()
for item in sub.get("skips") or []:
    if not isinstance(item, dict):
        continue
    reason = str(item.get("reason") or "").lower()
    if "mount" in reason or "element not found" in reason or "element-not-found" in reason:
        if item.get("id"):
            skip_ids.add(str(item["id"]))
for t in spec.get("transitions") or []:
    if not isinstance(t, dict) or str(t.get("id", "")) not in skip_ids:
        continue
    for sel in str(t.get("target") or "").split(","):
        sel = sel.strip()
        if sel and "*" not in sel:
            print(sel)
PY
if [ -s "$SKIP_SELECTORS_FILE" ] && [ -s "$TARGETS_FILE" ]; then
  FILTERED_TARGETS="$(mktemp)"
  track_temp_file "$FILTERED_TARGETS"
  while IFS=$'\t' read -r T_NAME T_TYPE T_SEL; do
    if grep -Fxq "$T_SEL" "$SKIP_SELECTORS_FILE"; then
      echo "## $T_SEL ($T_TYPE) — known-skip: documented conditionally-mounted overlay target (asset-substitution skips[])" >> "$RESULT"
    else
      printf '%s\t%s\t%s\n' "$T_NAME" "$T_TYPE" "$T_SEL" >> "$FILTERED_TARGETS"
    fi
  done < "$TARGETS_FILE"
  mv "$FILTERED_TARGETS" "$TARGETS_FILE"
  if [ ! -s "$TARGETS_FILE" ]; then
    # Review-1 honesty pattern: an all-known-skip run measured NOTHING —
    # that is absence of evidence, not a pass (observed failure mode: 5/5
    # targets overlay-gated, 0 measured runs, gate passed while a bundle-
    # declared hover expansion was missing entirely). Every hoverable entry
    # gets a fallback probe verdict instead.
    echo "# coverage: 0 measured target-runs — all selected targets are documented known-skips" >> "$RESULT"
    echo "▸ all selected hover targets are known-skips — running per-entry fallback probe"
    _known_skip_fallback_status=0
    register_hover_session "${SESSION}-hfb"
    bash "$_SCRIPT_DIR/hover-fallback-probe.sh" "${SESSION}-hfb" "$IMPL_URL" "$REF_DIR" \
      >> "$RESULT" 2>&1 || _known_skip_fallback_status=$?
    _known_skip_cleanup_status=0
    cleanup_hover_sessions "${SESSION}-hfb" || _known_skip_cleanup_status=$?
    if [ "$_known_skip_cleanup_status" -ne 0 ]; then
      echo "⚠️ UNMEASURABLE: fallback session cleanup failed; no pass can be reported" >> "$RESULT"
      echo "Wrote $RESULT"
      exit 2
    fi
    if [ "$_known_skip_fallback_status" -eq 0 ]; then
      echo "✅ fallback probe covered all hoverable entries (hover-fallback.json)" >> "$RESULT"
      echo "Wrote $RESULT"
      exit 0
    elif [ "$_known_skip_fallback_status" -eq 1 ]; then
      echo "❌ fallback probe FAILED for at least one hoverable entry (hover-fallback.json)" >> "$RESULT"
      echo "Wrote $RESULT"
      exit 1
    fi
    echo "⚠️ UNMEASURABLE: fallback probe returned status $_known_skip_fallback_status" >> "$RESULT"
    echo "Wrote $RESULT"
    exit 2
  fi
fi

FAIL_COUNT=0
UNMEASURABLE_COUNT=0
RUN_COUNT=0
# Review-2 finding 2: per-entry coverage accounting. Every measured
# target-run records its selector; the fallback probe afterwards covers
# every PLANNED hover entry that got no measured run — one measured run
# must never suppress probing of the rest.
MEASURED_SEL_FILE="$(mktemp "${TMPDIR:-/tmp}/hover-measured.XXXXXX")"
track_temp_file "$MEASURED_SEL_FILE"

# Build viewport list. Empty VIEWPORTS = single iteration with no overrides
# (back-compat: VIEW_W/VIEW_H from caller environment, or video-transition-
# compare.sh defaults). Non-empty = comma-separated WxH; one outer-loop pass
# per entry with VIEW_W/VIEW_H exported so the inner compare uses it.
VP_LIST=()
if [ -n "$VIEWPORTS" ]; then
  IFS=',' read -ra VP_LIST <<< "$VIEWPORTS"
else
  VP_LIST=("")
fi

echo "# viewports: ${VIEWPORTS:-<single (caller VIEW_W/VIEW_H)>}" >> "$RESULT"
echo >> "$RESULT"

for VP in "${VP_LIST[@]}"; do
  VP_LABEL="single"
  VP_OUT_DIR="$OUT_DIR"
  VP_SESSION_SUFFIX=""
  if [ -n "$VP" ]; then
    VP_LABEL="$VP"
    VP_W="${VP%x*}"
    VP_H="${VP#*x}"
    if ! [[ "$VP_W" =~ ^[0-9]+$ ]] || ! [[ "$VP_H" =~ ^[0-9]+$ ]]; then
      echo "ERROR: malformed VIEWPORTS entry '$VP' (expected WxH)" >&2
      exit 2
    fi
    export VIEW_W="$VP_W" VIEW_H="$VP_H"
    VP_OUT_DIR="$OUT_DIR/$VP"
    VP_SESSION_SUFFIX="-${VP_W}x${VP_H}"
    mkdir -p "$VP_OUT_DIR"
    {
      echo "### viewport: ${VP_W}x${VP_H}"
      echo
    } >> "$RESULT"
  fi

  # ── Both-absent / both-hidden parity pre-filter (loop-e2e-6) ──
  # A hover target whose selector matches NOTHING on ref AND impl at idle is
  # mount-gated UI shipped in the ref's CSS (overlay controls, third-party
  # autocomplete rows). Recording it false-fails as "Element not found" on
  # both sides. Absence parity is parity — the mounted-state behavior is
  # covered by the click-state probes. One-sided absence (ref has it, impl
  # doesn't, or vice versa) is still a real divergence and stays in the run
  # list. Likewise, selectors whose matches are all CSS-hidden on both sides
  # cannot produce a valid pixel ROI; record them as known-skips and leave
  # their behavioral coverage to the unconditional per-entry fallback probe.
  VP_PROBE_JSON="$(mktemp)"
  track_temp_file "$VP_PROBE_JSON"
  python3 - "$TARGETS_FILE" <<'PY' > "$VP_PROBE_JSON" 2>/dev/null || printf '[]' > "$VP_PROBE_JSON"
import json, sys
sels = []
for line in open(sys.argv[1]):
    parts = line.rstrip("\n").split("\t")
    if len(parts) == 3 and parts[2]:
        sels.append(parts[2])
print(json.dumps(sels))
PY
  PROBE_B64=$(base64 < "$VP_PROBE_JSON" | tr -d '\n')
  PROBE_JS="(() => { const sels = JSON.parse(atob('$PROBE_B64')); const out = {}; const rendered = (el) => { const style = getComputedStyle(el); const rect = el.getBoundingClientRect(); if (typeof el.checkVisibility === 'function' && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false; return style.display !== 'none' && style.visibility !== 'hidden' && Number.parseFloat(style.opacity || '1') > 0 && style.pointerEvents !== 'none' && rect.width > 0 && rect.height > 0; }; for (const s of sels) { try { const matches = Array.from(document.querySelectorAll(s)); out[s] = matches.length === 0 ? false : (matches.some(rendered) ? 'rendered' : 'hidden'); } catch (_) { out[s] = null; } } return JSON.stringify(out); })()"
  PROBE_SESSION="$SESSION-hsprobe${VP_SESSION_SUFFIX}"
  register_hover_session "$PROBE_SESSION"
  REF_PRESENT="{}"
  IMPL_PRESENT="{}"
  if agent-browser --session "$PROBE_SESSION" open "$ORIG_URL" >/dev/null 2>&1; then
    [ -n "$VP" ] && agent-browser --session "$PROBE_SESSION" set viewport "$VP_W" "$VP_H" >/dev/null 2>&1
    sleep 4
    REF_PRESENT=$(agent-browser --session "$PROBE_SESSION" eval "$PROBE_JS" 2>/dev/null | python3 -c 'import sys,json
raw=sys.stdin.read().strip()
try:
    v=json.loads(raw)
    if isinstance(v,str): v=json.loads(v)
    print(json.dumps(v))
except Exception:
    print("{}")')
    agent-browser --session "$PROBE_SESSION" open "$IMPL_URL" >/dev/null 2>&1
    sleep 4
    IMPL_PRESENT=$(agent-browser --session "$PROBE_SESSION" eval "$PROBE_JS" 2>/dev/null | python3 -c 'import sys,json
raw=sys.stdin.read().strip()
try:
    v=json.loads(raw)
    if isinstance(v,str): v=json.loads(v)
    print(json.dumps(v))
except Exception:
    print("{}")')
  fi
  PROBE_CLEANUP_STATUS=0
  cleanup_hover_sessions "$PROBE_SESSION" || PROBE_CLEANUP_STATUS=$?
  if [ "$PROBE_CLEANUP_STATUS" -ne 0 ]; then
    echo "⚠️ UNMEASURABLE: presence-probe session cleanup failed [$VP_LABEL]; stopping before target capture" >> "$RESULT"
    echo "Wrote $RESULT"
    exit 2
  fi
  VP_TARGETS="$(mktemp)"
  track_temp_file "$VP_TARGETS"
  python3 - "$TARGETS_FILE" "$REF_PRESENT" "$IMPL_PRESENT" "$RESULT" "$VP_LABEL" > "$VP_TARGETS" <<'PY'
import json, sys
targets_path, ref_raw, impl_raw, result_path, vp_label = sys.argv[1:6]
def parse(raw):
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}
ref_p, impl_p = parse(ref_raw), parse(impl_raw)
kept, skipped = [], []
for line in open(targets_path):
    line = line.rstrip("\n")
    parts = line.split("\t")
    if len(parts) != 3 or not parts[2]:
        continue
    sel = parts[2]
    # Skip only definitive parity states: absent on both or all matches hidden
    # on both. Probe errors (None / missing key) keep the target in the run list.
    if ref_p.get(sel) is False and impl_p.get(sel) is False:
        skipped.append((parts[0], parts[1], sel, "selector absent on BOTH ref and impl at idle (mount-gated UI; absence parity verified by live probe)"))
    elif ref_p.get(sel) == "hidden" and impl_p.get(sel) == "hidden":
        skipped.append((parts[0], parts[1], sel, "all selector matches hidden on BOTH ref and impl at idle; delegated to per-entry fallback probe"))
    else:
        kept.append(line)
with open(result_path, "a") as r:
    for name, ttype, sel, reason in skipped:
        r.write(f"## {sel} ({ttype}) [{vp_label}] — known-skip: {reason}\n")
print("\n".join(kept))
PY
  while IFS=$'\t' read -r NAME TTYPE SELECTOR; do
    [ -z "$SELECTOR" ] && continue
    AFFECTED_SELECTOR="$(affected_selector_for_hover "$SELECTOR" 2>/dev/null || true)"
    RUN_COUNT=$((RUN_COUNT + 1))
    printf '%s\n' "$SELECTOR" >> "$MEASURED_SEL_FILE"
    SAFE_NAME="${NAME//[^A-Za-z0-9_-]/_}"
    TARGET_DIR="$VP_OUT_DIR/$SAFE_NAME"
    clear_attempt_evidence "$TARGET_DIR"
    {
      echo "## $NAME ($TTYPE) [$VP_LABEL]"
      echo "selector: $SELECTOR"
      [ -n "$AFFECTED_SELECTOR" ] && echo "affected-selector: $AFFECTED_SELECTOR"
      echo
    } >> "$RESULT"

    MEASURE_SESSION="$SESSION-hs${VP_SESSION_SUFFIX}-$RUN_COUNT"
    register_hover_session "$MEASURE_SESSION"
    MEASURE_STATUS=0
    if [ -n "$AFFECTED_SELECTOR" ]; then
      VIDEO_COMPARE_AFFECTED_SELECTOR="$AFFECTED_SELECTOR" \
        bash "$COMPARE" "$MEASURE_SESSION" "$ORIG_URL" "$IMPL_URL" \
        "$TARGET_DIR" "${HOVER_MODE_PREFIX}:$SELECTOR" \
        >> "$RESULT" 2>&1 || MEASURE_STATUS=$?
    else
      bash "$COMPARE" "$MEASURE_SESSION" "$ORIG_URL" "$IMPL_URL" \
        "$TARGET_DIR" "${HOVER_MODE_PREFIX}:$SELECTOR" \
        >> "$RESULT" 2>&1 || MEASURE_STATUS=$?
    fi
    MEASURE_CLEANUP_STATUS=0
    cleanup_hover_sessions "$MEASURE_SESSION" || MEASURE_CLEANUP_STATUS=$?
    if [ "$MEASURE_CLEANUP_STATUS" -ne 0 ]; then
      echo "⚠️ $NAME unmeasurable [$VP_LABEL] — first-attempt session cleanup failed; stopping before any further capture" >> "$RESULT"
      echo "Wrote $RESULT"
      exit 2
    fi
    case "$MEASURE_STATUS" in
      0)
        echo "✅ $NAME clean [$VP_LABEL]" >> "$RESULT"
        ;;
      1)
        echo "❌ $NAME divergence [$VP_LABEL] — inspect $TARGET_DIR/diff-frames/; no capture retry allowed" >> "$RESULT"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        ;;
      2)
        RETRY_SESSION="${MEASURE_SESSION}-retry1"
        RETRY_DIR="${TARGET_DIR}-retry-1"
        RETRY_STATUS=0
        clear_attempt_evidence "$RETRY_DIR"
        echo "↻ $NAME retryable-unmeasurable [$VP_LABEL] — confirming once with a fresh session" >> "$RESULT"
        register_hover_session "$RETRY_SESSION"
        if [ -n "$AFFECTED_SELECTOR" ]; then
          VIDEO_COMPARE_AFFECTED_SELECTOR="$AFFECTED_SELECTOR" \
            bash "$COMPARE" "$RETRY_SESSION" "$ORIG_URL" "$IMPL_URL" \
            "$RETRY_DIR" "${HOVER_MODE_PREFIX}:$SELECTOR" \
            >> "$RESULT" 2>&1 || RETRY_STATUS=$?
        else
          bash "$COMPARE" "$RETRY_SESSION" "$ORIG_URL" "$IMPL_URL" \
            "$RETRY_DIR" "${HOVER_MODE_PREFIX}:$SELECTOR" \
            >> "$RESULT" 2>&1 || RETRY_STATUS=$?
        fi
        RETRY_CLEANUP_STATUS=0
        cleanup_hover_sessions "$RETRY_SESSION" || RETRY_CLEANUP_STATUS=$?
        if [ "$RETRY_CLEANUP_STATUS" -ne 0 ]; then
          echo "⚠️ $NAME unmeasurable-after-retry [$VP_LABEL] — retry session cleanup failed; stopping before any further capture" >> "$RESULT"
          echo "Wrote $RESULT"
          exit 2
        fi
        case "$RETRY_STATUS" in
          0)
            echo "✅ $NAME pass-after-retry [$VP_LABEL] — capture-flake-confirmed; inspect $TARGET_DIR and $RETRY_DIR" >> "$RESULT"
            ;;
          1)
            echo "❌ $NAME divergence-after-retry [$VP_LABEL] — attempts: $TARGET_DIR and $RETRY_DIR" >> "$RESULT"
            FAIL_COUNT=$((FAIL_COUNT + 1))
            ;;
          *)
            FIRST_REASON=$(capture_retry_reason "$TARGET_DIR" 2>/dev/null || true)
            RETRY_REASON=$(capture_retry_reason "$RETRY_DIR" 2>/dev/null || true)
            CALIBRATION_RECEIPT="$RETRY_DIR/reference-self-calibration.json"
            COMPLEMENTARY_RECEIPT="$RETRY_DIR/reference-self-complementary-calibration.json"
            STATIC_DISCRETE_RECEIPT="$RETRY_DIR/static-discrete-hover-state-calibration.json"
            CALIBRATION_STATUS=2
            COMPLEMENTARY_STATUS=2
            STATIC_DISCRETE_STATUS=2
            if [ "$RETRY_STATUS" -eq 2 ] \
              && [ "$FIRST_REASON" = "early-window-capture-phase" ] \
              && [ "$RETRY_REASON" = "early-window-capture-phase" ]; then
              CALIBRATION_STATUS=0
              run_reference_self_calibration "$TARGET_DIR" "$RETRY_DIR" \
                || CALIBRATION_STATUS=$?
              if [ "$CALIBRATION_STATUS" -ne 0 ] \
                && [ -f "$CALIBRATION_RECEIPT" ]; then
                STATIC_DISCRETE_STATUS=0
                run_reference_self_calibration "$TARGET_DIR" "$RETRY_DIR" \
                  static-discrete \
                  || STATIC_DISCRETE_STATUS=$?
              fi
            elif [ "$RETRY_STATUS" -eq 2 ] \
              && { { [ "$FIRST_REASON" = "early-window-capture-phase" ] \
                     && [ "$RETRY_REASON" = "arc-only-capture-jitter" ]; } \
                   || { [ "$FIRST_REASON" = "arc-only-capture-jitter" ] \
                        && [ "$RETRY_REASON" = "early-window-capture-phase" ]; }; }; then
              if complementary_provenance_allowed "$TTYPE"; then
                COMPLEMENTARY_STATUS=0
                run_reference_self_calibration "$TARGET_DIR" "$RETRY_DIR" \
                  complementary "$TTYPE" "$TTYPE" \
                  || COMPLEMENTARY_STATUS=$?
              fi
            fi
            if [ "$CALIBRATION_STATUS" -eq 0 ] \
              && valid_reference_self_calibration "$CALIBRATION_RECEIPT"; then
              echo "✅ $NAME pass-after-reference-self-calibration [$VP_LABEL] — duplicate early capture phase confirmed; inspect $CALIBRATION_RECEIPT" >> "$RESULT"
            elif [ "$STATIC_DISCRETE_STATUS" -eq 0 ] \
              && valid_reference_self_calibration "$STATIC_DISCRETE_RECEIPT" static-discrete; then
              echo "✅ $NAME pass-after-static-discrete-hover-state-calibration [$VP_LABEL] — static discrete hover state proved against source-frame phase; inspect $STATIC_DISCRETE_RECEIPT" >> "$RESULT"
            elif [ "$COMPLEMENTARY_STATUS" -eq 0 ] \
              && valid_reference_self_calibration "$COMPLEMENTARY_RECEIPT" complementary; then
              echo "✅ $NAME pass-after-complementary-reference-self-calibration [$VP_LABEL] — early pixel noise and arc-only retry complement each other; inspect $COMPLEMENTARY_RECEIPT" >> "$RESULT"
            elif [ "$CALIBRATION_STATUS" -eq 1 ] \
              && valid_reference_self_calibration "$CALIBRATION_RECEIPT" clean-divergence; then
              echo "❌ $NAME divergence-after-clean-reference-self [$VP_LABEL] — both captures diverge while ref-vs-ref is clean; inspect $CALIBRATION_RECEIPT" >> "$RESULT"
              FAIL_COUNT=$((FAIL_COUNT + 1))
            else
              echo "⚠️ $NAME unmeasurable-after-retry [$VP_LABEL] — status $RETRY_STATUS; attempts: $TARGET_DIR and $RETRY_DIR" >> "$RESULT"
              UNMEASURABLE_COUNT=$((UNMEASURABLE_COUNT + 1))
            fi
            # Two sub-threshold captures pass only when both retry receipts and
            # ref-vs-ref prove the same bounded early capture phase. Calibration
            # can also prove a hard divergence when ref-vs-ref is clean.
            ;;
        esac
        ;;
      *)
        echo "⚠️ $NAME unmeasurable [$VP_LABEL] — hard comparator status $MEASURE_STATUS; no retry" >> "$RESULT"
        UNMEASURABLE_COUNT=$((UNMEASURABLE_COUNT + 1))
        ;;
    esac
    echo >> "$RESULT"
  done < "$VP_TARGETS"
done

# Review-2 finding 2: the fallback probe runs UNCONDITIONALLY with the
# measured-selector accounting — entries covered by a measured run are
# marked "measured"; every other planned entry gets a probe verdict. The
# old RUN_COUNT==0 gating let one unrelated measured run suppress probing
# of all remaining entries (the nav-pill width-0 defect survived behind
# partial coverage).
FALLBACK_FAILED=0
echo "▸ per-entry fallback probe (measured runs: $RUN_COUNT)"
_fallback_status=0
register_hover_session "${SESSION}-hfb"
UI_CLONE_HOVER_MEASURED_FILE="$MEASURED_SEL_FILE" \
  bash "$_SCRIPT_DIR/hover-fallback-probe.sh" "${SESSION}-hfb" "$IMPL_URL" "$REF_DIR" \
  >> "$RESULT" 2>&1 || _fallback_status=$?
_fallback_cleanup_status=0
cleanup_hover_sessions "${SESSION}-hfb" || _fallback_cleanup_status=$?
if [ "$_fallback_cleanup_status" -ne 0 ]; then
  echo "⚠️ UNMEASURABLE: fallback session cleanup failed; no pass can be reported" >> "$RESULT"
  UNMEASURABLE_COUNT=$((UNMEASURABLE_COUNT + 1))
elif [ "$_fallback_status" -eq 1 ]; then
  FALLBACK_FAILED=1
elif [ "$_fallback_status" -ne 0 ]; then
  echo "⚠️ UNMEASURABLE: fallback probe returned status $_fallback_status" >> "$RESULT"
  UNMEASURABLE_COUNT=$((UNMEASURABLE_COUNT + 1))
fi

{
  echo
  echo "# coverage: measured=$RUN_COUNT failed=$FAIL_COUNT unmeasurable=$UNMEASURABLE_COUNT fallbackFailed=$FALLBACK_FAILED"
  if [ "$FALLBACK_FAILED" -eq 1 ]; then
    echo "❌ fallback probe FAILED for at least one unmeasured hoverable entry (hover-fallback.json)"
  elif [ "$FAIL_COUNT" -gt 0 ]; then
    echo "❌ $FAIL_COUNT/$RUN_COUNT hover target-run(s) diverged"
  elif [ "$UNMEASURABLE_COUNT" -gt 0 ]; then
    echo "⚠️ $UNMEASURABLE_COUNT/$RUN_COUNT hover target-run(s) unmeasurable"
  else
    echo "✅ all $RUN_COUNT measured hover target-run(s) within SSIM threshold; fallback probe covered the rest"
  fi
} >> "$RESULT"

echo "Wrote $RESULT"
# Exit-code contract (mirror video-motion-compare / click-state-compare): a
# diverging MEASURED run must fail the gate too, not just a fallback-probe
# failure. Exiting 0 on FAIL_COUNT>0 left every exit-code consumer (the
# dispatcher) reporting clean while ❌ rows sat in the result text.
if [ "$FALLBACK_FAILED" -eq 1 ] || [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
elif [ "$UNMEASURABLE_COUNT" -gt 0 ]; then
  exit 2
fi
exit 0
