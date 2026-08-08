#!/usr/bin/env bash
# extract-animation-runtime.sh — Dump runtime-only animation parameters.
#
# Bundle-grep (Step 4) catches *literal* values present in source — durations,
# numeric ease coefficients, string ease names. It misses anything computed at
# runtime: ScrollTrigger.start expressions like "top 80%" resolved to pixel
# offsets, custom cubic-bezier functions defined as arrow bodies, Webflow IX2
# timeline IDs only known after the runtime mounts, Lenis instance config
# composed by user code.
#
# This script runs ONCE against the live ref page and dumps whatever animation
# runtimes are present into a single JSON sidecar. The spec gate should consult
# it when authoring transition-spec.json so easing/threshold values aren't
# silently lost between extraction and generation.
#
# Usage:
#   bash extract-animation-runtime.sh <session> <output-dir>
#
# Output: <output-dir>/animation-runtime-dump.json
#         { gsap:{...}, scrollTrigger:[...], webAnimations:[...],
#           lenis:{...}, ix2:{...}, scrollLinkedStyles:[...], generatedAt:"<ISO8601>" }
#
# scrollLinkedStyles captures JS-driven scroll-scrub motion that has NO global
# registry (framer-motion useScroll/useTransform, or any rAF code that writes
# inline styles per tick) by sampling inline transform/opacity/size across the
# scroll sweep and keeping only elements whose value varies — the residue is
# the scroll-progress-to-value curve. This is the one channel bundle-grep and
# ScrollTrigger capture both miss on framer sites.
#
# Missing-runtime fields are emitted as null (not omitted) so downstream code
# can do a single shape check.

set -euo pipefail

SESSION="${1:?Usage: extract-animation-runtime.sh <session> <output-dir>}"
DIR="${2:?Usage: extract-animation-runtime.sh <session> <output-dir>}"

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "ERROR: agent-browser CLI not on PATH" >&2
  exit 2
fi

mkdir -p "$DIR"

OUT="$DIR/animation-runtime-dump.json"

# The eval IIFE must be defensive: ScrollTrigger / Lenis / IX2 may be absent.
# Each branch returns null when the runtime isn't there; null in JSON means
# "we looked, it wasn't running" — distinguishable from "we didn't look".
#
# Scroll-walk: ScrollTrigger entries for below-fold sections are registered
# LAZY (when the section actually mounts during scroll). A single dump at
# page-load default scroll misses them. We walk N scroll fractions, capture
# at each, dedupe by trigger key, and merge — same idea as section sweep but
# for animation runtime state.
#
# Token discipline: serialize INSIDE the page so the agent-browser bridge
# returns a compact JSON string instead of a giant object graph.
#
# The extraction IIFE lives beside this wrapper as a standalone JS helper.
# Feed it over stdin so the shell never materializes the 13KB program inside
# a command substitution or exposes it to shell quoting.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL_JS="$SCRIPT_DIR/extract-animation-runtime.js"
if [ ! -r "$EVAL_JS" ]; then
  echo "ERROR: animation runtime JS helper not found: $EVAL_JS" >&2
  exit 2
fi
RESULT=$(agent-browser --session "$SESSION" eval --stdin < "$EVAL_JS" 2>/dev/null || echo "")

if [ -z "$RESULT" ]; then
  echo "WARN: agent-browser eval returned empty; writing minimal dump" >&2
  printf '%s\n' '{"gsap":null,"scrollTrigger":null,"webAnimations":null,"lenis":null,"ix2":null,"scrollLinkedStyles":null,"generatedAt":null,"note":"eval returned empty"}' > "$OUT"
  exit 0
fi

# Validate JSON before writing. The eval returns a JSON STRING literal (the
# IIFE called JSON.stringify), so the raw response is `"{...}"`. python -m
# json.tool parses the outer string, and we then re-emit just the inner
# object so the artifact is the dict, not a quoted string.
printf '%s' "$RESULT" | python3 -c "
import json, sys
raw = sys.stdin.read().strip()
try:
    payload = json.loads(raw)
    if isinstance(payload, str):
        # Double-encoded: agent-browser wrapped our stringify result.
        payload = json.loads(payload)
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
except Exception as e:
    sys.stderr.write(f'extract-animation-runtime: JSON parse failed: {e}\n')
    sys.exit(2)
" > "$OUT"

echo "Wrote $OUT"
exit 0
