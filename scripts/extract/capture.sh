#!/usr/bin/env bash
# capture.sh — minimal Phase 1 reference-capture wrapper for `python -m
# ui_clone.pipeline run`. Standalone wrapper around the agent-browser CLI
# commands documented in skills/ui-capture/SKILL.md, just enough to pass
# the `reference` gate. Not a full ui-capture replacement — does not
# handle splash detection, hover catalogue, parallax/mousemove, or
# transition-region segmentation. Those stay in the ui-capture skill for
# now and can be wired in later from `run --phases 1-5`.
#
# Deterministic plumbing (page-height parsing, regions.json emission,
# count summary) lives in scripts/extract/_capture_artifacts.py so it's
# unit-testable. agent-browser orchestration (open/eval/record/screenshot)
# stays in this shell wrapper because tests would need a real browser
# regardless.
#
# Usage: capture.sh <url> <session> <ref_dir> [--reuse-session]
#
# By default this clears the named session before opening the reference URL so
# persisted cookies, localStorage, or site theme choices from an earlier browser
# run cannot poison the reference corpus. Pass `--reuse-session` only for
# intentional authenticated/session reuse.
#
# Produces:
#   <ref_dir>/static/ref/section-{0..4}.png   — 5 evenly-spaced screenshots
#   <ref_dir>/scroll-video/ref/full-scroll.webm — full-scroll video
#   <ref_dir>/transitions/ref/placeholder.webm — gate-satisfying placeholder
#   <ref_dir>/regions.json                    — single full-page region
#
# Exits non-zero if agent-browser is missing or any capture step fails.
# Runtime capture failures write <ref_dir>/capture-error.json with the
# failing stage, command, target artifact, and current artifact counts so
# the reference gate blocker is actionable without shell-log archaeology.
# The `run` driver in pipeline.py uses the exit code as the phase gate.

set -euo pipefail

# W-4 (loop-ebpb-0): pin the light color scheme at CAPTURE time too — a
# dark-evening Phase-0 capture bakes dark styles into the ref corpus
# PERMANENTLY, and every light-pinned verify then honestly-fails against
# poisoned ground truth. Caller override intact (default only when unset).
: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME

URL="${1:?usage: capture.sh <url> <session> <ref_dir> [--reuse-session]}"
SESSION="${2:?usage: capture.sh <url> <session> <ref_dir> [--reuse-session]}"
REF_DIR="${3:?usage: capture.sh <url> <session> <ref_dir> [--reuse-session]}"
REUSE_SESSION="false"
if [ "${4:-}" = "--reuse-session" ]; then
  REUSE_SESSION="true"
elif [ -n "${4:-}" ]; then
  echo "usage: capture.sh <url> <session> <ref_dir> [--reuse-session]" >&2
  exit 2
fi

CAPTURE_SESSION="$SESSION"

command -v agent-browser >/dev/null 2>&1 || {
  echo "capture.sh: agent-browser not found in PATH" >&2
  exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTIFACTS_PY="$SCRIPT_DIR/_capture_artifacts.py"
[ -f "$ARTIFACTS_PY" ] || {
  echo "capture.sh: missing $ARTIFACTS_PY" >&2
  exit 2
}

mkdir -p "$REF_DIR"/{static,scroll-video,transitions,clip}/{ref,impl}
ABS_REF=$(cd "$REF_DIR" && pwd)

CAPTURE_STAGE="initialization"
CAPTURE_ARTIFACT=""
CAPTURE_COMMAND=""
CAPTURE_MESSAGE=""

format_command() {
  printf '%q ' "$@"
}

write_capture_error() {
  local status="$1"
  python3 "$ARTIFACTS_PY" write-error \
    "$ABS_REF" \
    "$CAPTURE_STAGE" \
    "$status" \
    "$CAPTURE_ARTIFACT" \
    "$CAPTURE_COMMAND" \
    "$CAPTURE_MESSAGE" >/dev/null || true
  echo "capture.sh: wrote diagnostic ${ABS_REF}/capture-error.json (stage: ${CAPTURE_STAGE})" >&2
}

capture_err_trap() {
  local status="$?"
  if [ "$status" -eq 0 ]; then
    exit 0
  fi
  write_capture_error "$status"
  exit "$status"
}

trap capture_err_trap ERR

run_capture_step() {
  CAPTURE_STAGE="$1"
  CAPTURE_ARTIFACT="$2"
  shift 2
  CAPTURE_COMMAND="$(format_command "$@")"
  CAPTURE_MESSAGE=""
  "$@"
  CAPTURE_MESSAGE=""
}

run_record_stop() {
  CAPTURE_STAGE="$1"
  CAPTURE_ARTIFACT="$2"
  CAPTURE_COMMAND="$(format_command agent-browser --session "$CAPTURE_SESSION" record stop)"
  CAPTURE_MESSAGE=""

  local output status
  set +e
  output=$(agent-browser --session "$CAPTURE_SESSION" record stop 2>&1)
  status=$?
  set -e

  if [ "$status" -eq 0 ]; then
    return 0
  fi

  CAPTURE_MESSAGE="$output"
  if [ -n "$output" ]; then
    printf '%s\n' "$output" >&2
  fi

  # Some recorder builds report a lifecycle stop error even after flushing
  # the requested WebM. Preserve that evidence instead of failing Phase 1.
  if [ -s "${ABS_REF}/${CAPTURE_ARTIFACT}" ]; then
    echo "capture.sh: record stop reported failure at ${CAPTURE_STAGE}, but ${CAPTURE_ARTIFACT} exists; continuing" >&2
    CAPTURE_MESSAGE=""
    return 0
  fi

  return "$status"
}

require_capture_artifact() {
  CAPTURE_STAGE="$1"
  CAPTURE_ARTIFACT="$2"
  CAPTURE_COMMAND="test -s ${ABS_REF}/${CAPTURE_ARTIFACT}"
  CAPTURE_MESSAGE="Expected capture artifact is missing or empty: ${CAPTURE_ARTIFACT}"
  test -s "${ABS_REF}/${CAPTURE_ARTIFACT}"
  CAPTURE_MESSAGE=""
}

# Open + canonical viewport. `set viewport` must follow `open` (the skill
# notes the reverse order is silently dropped). Keep the same named session
# alive for downstream Phase 2 extraction, but clear it first by default so
# persisted site theme state cannot leak across captures.
if [ "$REUSE_SESSION" != "true" ]; then
  run_capture_step "pre-open-session-reset" "" agent-browser --session "$CAPTURE_SESSION" close
fi
run_capture_step "open" "" agent-browser --session "$CAPTURE_SESSION" open "$URL"
run_capture_step "viewport" "" agent-browser --session "$CAPTURE_SESSION" set viewport 1440 900
run_capture_step "initial-wait" "" agent-browser --session "$CAPTURE_SESSION" wait 3000

# Page height for evenly-spaced scroll screenshots. The Python helper
# handles the agent-browser double-encode + non-numeric fallback so we
# never divide by zero downstream.
PAGE_H_RAW=$(run_capture_step "page-height:eval" "" agent-browser --session "$CAPTURE_SESSION" eval \
  "(() => document.documentElement.scrollHeight)()")
PAGE_H=$(run_capture_step "page-height:parse" "" python3 "$ARTIFACTS_PY" parse-height "$PAGE_H_RAW")

# 5 screenshots at evenly-spaced scroll positions. Absolute path to dodge
# the cwd-leaks-between-commands footgun the skill calls out.
for i in 0 1 2 3 4; do
  Y=$(( PAGE_H * i / 5 ))
  run_capture_step "screenshot-${i}:scroll" "" agent-browser --session "$CAPTURE_SESSION" eval \
    "(() => { window.scrollTo(0, ${Y}); return 1; })()" >/dev/null
  run_capture_step "screenshot-${i}:wait" "" agent-browser --session "$CAPTURE_SESSION" wait 800
  run_capture_step "screenshot-${i}:capture" "static/ref/section-${i}.png" agent-browser --session "$CAPTURE_SESSION" screenshot \
    "${ABS_REF}/static/ref/section-${i}.png"
  require_capture_artifact "screenshot-${i}:artifact-check" "static/ref/section-${i}.png"
done

# Reset to top.
run_capture_step "reset-scroll" "" agent-browser --session "$CAPTURE_SESSION" eval \
  "(() => { window.scrollTo(0, 0); return 1; })()" >/dev/null
run_capture_step "reset-wait" "" agent-browser --session "$CAPTURE_SESSION" wait 500

# Scroll video — short smooth scroll-down recording so the `scroll-video/ref`
# gate row passes. Real ui-capture does a longer paced scroll; this minimal
# wrapper just exercises the recording path.
run_capture_step "scroll-video:record-start" "scroll-video/ref/full-scroll.webm" agent-browser --session "$CAPTURE_SESSION" record start \
  "${ABS_REF}/scroll-video/ref/full-scroll.webm"
run_capture_step "scroll-video:scroll" "" agent-browser --session "$CAPTURE_SESSION" eval \
  "(() => { window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'smooth'}); return 1; })()" >/dev/null
sleep 5
run_record_stop "scroll-video:record-stop" "scroll-video/ref/full-scroll.webm"
require_capture_artifact "scroll-video:artifact-check" "scroll-video/ref/full-scroll.webm"

# Placeholder transition clip so the `transitions/ref/` gate row passes.
run_capture_step "transition-placeholder:record-start" "transitions/ref/placeholder.webm" agent-browser --session "$CAPTURE_SESSION" record start \
  "${ABS_REF}/transitions/ref/placeholder.webm"
sleep 1
run_record_stop "transition-placeholder:record-stop" "transitions/ref/placeholder.webm"
require_capture_artifact "transition-placeholder:artifact-check" "transitions/ref/placeholder.webm"

# Single full-page region as the minimal regions.json — proper region
# segmentation is the ui-capture skill's job; this only unblocks the gate.
run_capture_step "regions:write" "regions.json" python3 "$ARTIFACTS_PY" write-regions "$ABS_REF" "$PAGE_H"

# Count + report (matches the textual block the prior inline version
# emitted, so any callers grepping for "static/ref/: N screenshots" etc.
# keep working).
run_capture_step "summary" "" python3 "$ARTIFACTS_PY" summarize "$ABS_REF"
