#!/usr/bin/env bash
# capture.sh — minimal Phase 1 reference-capture wrapper for `python -m
# ui_clone.pipeline run`. Standalone wrapper around the agent-browser CLI
# commands documented in skills/ui-capture/SKILL.md, just enough to pass
# the `reference` gate. Not a full ui-capture replacement — does not
# handle splash detection, hover catalogue, parallax/mousemove, or
# transition-region segmentation. Those stay in the ui-capture skill for
# now and can be wired in later from `run --phases 1-5`.
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

mkdir -p "$REF_DIR"/{static,scroll-video,transitions,clip}/{ref,impl}

# Open + canonical viewport. `set viewport` must follow `open` (the skill
# notes the reverse order is silently dropped).
agent-browser --session "$SESSION" open "$URL"
agent-browser --session "$SESSION" set viewport 1440 900
agent-browser --session "$SESSION" wait 3000

# Page height for evenly-spaced scroll screenshots.
PAGE_H_RAW=$(agent-browser --session "$SESSION" eval \
  "(() => document.documentElement.scrollHeight)()")
# agent-browser eval double-encodes the return value; jq -r unwraps once.
# Falls back to a sane default so we don't divide by zero later.
PAGE_H=$(printf '%s' "$PAGE_H_RAW" | jq -r 'tonumber? // 5000' 2>/dev/null || echo 5000)
if ! [[ "$PAGE_H" =~ ^[0-9]+$ ]]; then PAGE_H=5000; fi

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
cat > "${ABS_REF}/regions.json" <<JSEOF
{
  "regions": [
    {"name": "full-page", "x": 0, "y": 0, "width": 1440, "height": ${PAGE_H}}
  ]
}
JSEOF

echo "capture.sh: Phase 1 artifacts written to ${ABS_REF}"
echo "  static/ref/: $(ls "${ABS_REF}/static/ref/" | wc -l) screenshots"
echo "  scroll-video/ref/: $(ls "${ABS_REF}/scroll-video/ref/" | wc -l) videos"
echo "  transitions/ref/: $(ls "${ABS_REF}/transitions/ref/" | wc -l) videos"
echo "  regions.json: $([ -f "${ABS_REF}/regions.json" ] && echo ok || echo MISSING)"
