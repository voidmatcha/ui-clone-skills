#!/usr/bin/env bash
# visual-fidelity-judge-check.sh — the "automated eyeball": a VLM judge that
# scores ref-vs-impl visual fidelity across STATIC section crops AND a LIVE
# scroll-motion sweep.
#
# Why this exists:
#   The verification stack measures per-section pixels (AE/SSIM), transition
#   coverage, and runtime motion oracles — but it repeatedly certified clones
#   whose scroll choreography was DEAD (elements that move/scale/reveal in the
#   ref sit still in the impl) because no gate looks at the whole motion arc the
#   way a human eye does. The loop-nvti-2 denominator-capture incident is the
#   canonical miss: gates green, the eye caught it instantly. Web research on
#   clone/diff fidelity converges on a VLM-as-judge as the SOTA fix for exactly
#   this axis, and it is the one signal class our stack lacks. This check adds
#   it as an ADVISORY (severity=warn) automated eyeball — non-gating for now,
#   but always honest: a browser/CLI failure is status=error + exit 2, never a
#   silent pass.
#
# Usage:
#   visual-fidelity-judge-check.sh <session> <ref-url> <impl-url> <ref-dir>
#   visual-fidelity-judge-check.sh --judge-artifact <measurements-json> <ref-dir>
#   visual-fidelity-judge-check.sh --print-settle <ref-dir>        (J-2 probe)
#   visual-fidelity-judge-check.sh --print-static-plan <ref-dir>   (J-1 probe)
#
# The --judge-artifact mode skips the browser and the claude CLI entirely: it
# assembles the verdict + artifact from a pre-collected measurements JSON (the
# same shape collect mode builds internally), so the verdict math is testable
# without a live browser or a paid VLM call. The two --print-* modes are
# filesystem-only probes (no dispatch) that expose the derived settle window and
# the static crop plan for testing.
#
# Static pass (J-1): choose the FRESHEST of sections/{ref,impl}/ and
#   sections/viewports/1440x900/{ref,impl}/, then for each <name>.png pair whose
#   crops are NOT older than the newest impl-source change (a crop older than the
#   impl is stale — left by a previous loop, describes a tree that no longer
#   exists — and is excluded + recorded in staticSkipped), dispatch the cached
#   visual-judge (ui_clone.visual_judge_dispatcher) and derive a 0-10 score from
#   its findings severities. All-stale → motion-only (never judge a dead tree).
#
# Artifact schema (v1): overall = {score: MEAN of axes+section scores (J-3, so
#   one wall section can't zero the headline), min: <old min>, worstAxis,
#   worstSection}; staticSkipped[], staticCropSet, motion.settleMs (J-2 derived),
#   motion.samples[], motion.unpairedSamples[].
# Motion pass: drive two live sessions, sample N scroll depths over the ref
#   docHeight. At each depth, scroll the ref, settle, and READ BACK where it
#   actually landed (a snap-back / smooth-scroll engine like Lenis moves it
#   during the settle), then RE-TARGET the impl to the ref's actual position and
#   read back the impl's settled position too. Frames are PAIRED by the ref's
#   landing spot; a sample whose impl still diverges beyond PAIR_TOLERANCE is
#   marked unpaired, EXCLUDED from the VLM frames (so mismatched frames can't
#   fabricate phantom motion findings) but RECORDED in motion.unpairedSamples.
#   ONE claude --print call (motion-judge.md prompt) then scores
#   layout/text/color/animation 0-10 across the paired sequences. If EVERY
#   sample diverges, the run is a hard error (no paired frames = no motion
#   evidence), never a pass.
#
# Writes: <ref-dir>/visual-fidelity-judge.json (ALWAYS — emit-or-fail).
# Exit: 0 pass, 1 fail, 2 setup/infra error. English-only.
set -uo pipefail

# W-4 (loop-ebpb-0): the reference follows prefers-color-scheme — a host
# OS theme flip (macOS auto-dark in the evening) silently captured the ref
# in dark mode and poisoned an entire compare cycle (footer dSSIM
# 0.0000065 -> 0.687 reading as catastrophic regression). Pin light unless
# the caller explicitly overrides.
: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPTS_DIR/../../.." && pwd)"

if [ "${1:-}" = "--judge-artifact" ]; then
  MODE="judge"
  MEAS_FILE="${2:?Usage: visual-fidelity-judge-check.sh --judge-artifact <measurements-json> <ref-dir>}"
  REF_DIR="${3:?Usage: visual-fidelity-judge-check.sh --judge-artifact <measurements-json> <ref-dir>}"
  SESSION=""; REF_URL=""; IMPL_URL=""
elif [ "${1:-}" = "--print-settle" ]; then
  # Testability probe: print the derived post-scroll settle window (ms) for a
  # ref-dir and exit. No browser, no artifact.
  MODE="print-settle"
  REF_DIR="${2:?Usage: visual-fidelity-judge-check.sh --print-settle <ref-dir>}"
  SESSION=""; REF_URL=""; IMPL_URL=""; MEAS_FILE=""
elif [ "${1:-}" = "--print-static-plan" ]; then
  # Testability probe: print the static crop plan (chosen crop set + which
  # pairs are judged vs stale-skipped) as JSON and exit. Filesystem-only (no
  # dispatch), so crop-staleness logic is testable with controlled mtimes.
  MODE="print-static-plan"
  REF_DIR="${2:?Usage: visual-fidelity-judge-check.sh --print-static-plan <ref-dir>}"
  SESSION=""; REF_URL=""; IMPL_URL=""; MEAS_FILE=""
elif [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
else
  MODE="collect"
  SESSION="${1:?Usage: visual-fidelity-judge-check.sh <session> <ref-url> <impl-url> <ref-dir>}"
  REF_URL="${2:?Usage: visual-fidelity-judge-check.sh <session> <ref-url> <impl-url> <ref-dir>}"
  IMPL_URL="${3:?Usage: visual-fidelity-judge-check.sh <session> <ref-url> <impl-url> <ref-dir>}"
  REF_DIR="${4:?Usage: visual-fidelity-judge-check.sh <session> <ref-url> <impl-url> <ref-dir>}"
  MEAS_FILE=""
fi

[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

# Keep the Python driver in a standalone file so shell parsing never touches
# embedded JS or prompt strings (the D21 quoting-regression pattern).
PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 "$SCRIPTS_DIR/visual_fidelity_judge.py" \
  "$MODE" "$SESSION" "$REF_URL" "$IMPL_URL" "$REF_DIR" "$MEAS_FILE" "$REPO_ROOT"
