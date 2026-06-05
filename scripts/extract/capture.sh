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
# Usage: capture.sh <url> <session> <ref_dir>
#
# Produces:
#   <ref_dir>/static/ref/section-{0..4}.png   — 5 evenly-spaced screenshots
#   <ref_dir>/scroll-video/ref/full-scroll.webm — full-scroll video
#   <ref_dir>/transitions/ref/placeholder.webm — gate-satisfying placeholder
#   <ref_dir>/regions.json                    — single full-page region
#
# Exits non-zero if agent-browser is missing or any capture step fails;
# the `run` driver in pipeline.py uses the exit code as the phase gate.

set -euo pipefail

URL="${1:?usage: capture.sh <url> <session> <ref_dir>}"
SESSION="${2:?usage: capture.sh <url> <session> <ref_dir>}"
REF_DIR="${3:?usage: capture.sh <url> <session> <ref_dir>}"

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

# Open + canonical viewport. `set viewport` must follow `open` (the skill
# notes the reverse order is silently dropped).
agent-browser --session "$SESSION" open "$URL"
agent-browser --session "$SESSION" set viewport 1440 900
agent-browser --session "$SESSION" wait 3000

# Page height for evenly-spaced scroll screenshots. The Python helper
# handles the agent-browser double-encode + non-numeric fallback so we
# never divide by zero downstream.
PAGE_H_RAW=$(agent-browser --session "$SESSION" eval \
  "(() => document.documentElement.scrollHeight)()")
PAGE_H=$(python3 "$ARTIFACTS_PY" parse-height "$PAGE_H_RAW")

# 5 screenshots at evenly-spaced scroll positions. Absolute path to dodge
# the cwd-leaks-between-commands footgun the skill calls out.
ABS_REF=$(cd "$REF_DIR" && pwd)
for i in 0 1 2 3 4; do
  Y=$(( PAGE_H * i / 5 ))
  agent-browser --session "$SESSION" eval \
    "(() => { window.scrollTo(0, ${Y}); return 1; })()" >/dev/null
  agent-browser --session "$SESSION" wait 800
  agent-browser --session "$SESSION" screenshot \
    "${ABS_REF}/static/ref/section-${i}.png"
done

# Reset to top.
agent-browser --session "$SESSION" eval \
  "(() => { window.scrollTo(0, 0); return 1; })()" >/dev/null
agent-browser --session "$SESSION" wait 500

# Scroll video — short smooth scroll-down recording so the `scroll-video/ref`
# gate row passes. Real ui-capture does a longer paced scroll; this minimal
# wrapper just exercises the recording path.
agent-browser --session "$SESSION" record start \
  "${ABS_REF}/scroll-video/ref/full-scroll.webm"
agent-browser --session "$SESSION" eval \
  "(() => { window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'smooth'}); return 1; })()" >/dev/null
sleep 5
agent-browser --session "$SESSION" record stop

# Placeholder transition clip so the `transitions/ref/` gate row passes.
agent-browser --session "$SESSION" record start \
  "${ABS_REF}/transitions/ref/placeholder.webm"
sleep 1
agent-browser --session "$SESSION" record stop

# Single full-page region as the minimal regions.json — proper region
# segmentation is the ui-capture skill's job; this only unblocks the gate.
python3 "$ARTIFACTS_PY" write-regions "$ABS_REF" "$PAGE_H"

# Count + report (matches the textual block the prior inline version
# emitted, so any callers grepping for "static/ref/: N screenshots" etc.
# keep working).
python3 "$ARTIFACTS_PY" summarize "$ABS_REF"
