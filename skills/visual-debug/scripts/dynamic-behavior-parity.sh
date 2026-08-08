#!/usr/bin/env bash
# dynamic-behavior-parity.sh — unpinned dynamic-BEHAVIOR parity for dynamic regions.
#
# The clone pipeline FREEZES dynamic content (feeds, carousels, tickers) so the
# static AE/dSSIM pass measures layout without motion noise. That freezing hides
# a real fidelity axis: does the impl actually REPRODUCE the ref's dynamic
# behavior (a carousel that rotates, a ticker that advances), or did the clone
# bake a single frozen frame that never changes at runtime?
#
# This check runs a SEPARATE, UNPINNED pass. On fresh live sessions (nothing
# frozen, no animation pausing) it fingerprints each declared dynamic region on
# BOTH the ref and the impl at T0 and again at T0+Δ, and asks a behavior-only
# question: did the region CHANGE over the window, and does the impl change too?
# Content equality is NOT required — a frozen feed's data legitimately differs
# from the live ref; only the BEHAVIOR (mechanism + period) must match.
#
# Region discovery (priority order):
#   1. <ref-dir>/dynamic-regions.json  — curated {regions:[{selector,label?,periodMs?}]}
#   2. transition-spec.json entries whose trigger contains autoplay/timer/
#      interval/carousel/ticker, or whose animation type contains "carousel".
#   3. transition-spec.json dynamic:true masked selectors (same discovery the
#      masked-region-static gate uses).
#   No regions found → pass artifact with empty regions (a static page must not
#   false-fail).
#
# Cadence (period) is fail-closed: if a region has a KNOWN reference period
# (declared periodMs in dynamic-regions.json, or a detected ref Swiper autoplay
# delay) but the impl's cadence is undetectable, the verdict is
# behavior-match-period-unverified (fail-level) rather than a match. Curators
# who do NOT care about cadence should OMIT periodMs; a region with no declared
# or detected period keeps plain behavior-match semantics.
#
# Infra vs. measurement: a command-level probe failure (agent-browser nonzero
# exit / timeout / empty eval output, or a failed page open) is an INFRA error —
# status "error", exit 2, never a green pass. honest-unmeasurable is reserved
# strictly for probes that executed successfully but whose measurement is
# semantically impossible (with the cited reason).
#
# Curated regions (from dynamic-regions.json) are ASSERTED-TO-EXIST: if such a
# selector is not found on the ref, that is a probe-timing / selector-drift
# problem, NOT evidence the ref has no dynamics — the verdict is
# honest-unmeasurable and the region counts toward the artifact's
# `unverifiedCurated` tally. If EVERY curated region ends up unverified and no
# region produced a real measurement, the whole run is status "error" + exit 2
# (a parity run that measured nothing is not parity evidence). Spec-discovered
# (non-curated) regions keep missing-on-ref → no-dynamics-in-window semantics.
# Before fingerprinting, each selector is retried up to 3 times (1.5s apart) on
# both sessions, scrolling to reveal it, so a viewport-triggered lazy mount
# (hero <video>) is not missed by a single immediate querySelector.
#
# Usage:
#   dynamic-behavior-parity.sh <session> <ref-url> <impl-url> <ref-dir>
#   dynamic-behavior-parity.sh --judge <measurements-json> <ref-dir>
#   dynamic-behavior-parity.sh --discover <ref-dir>
#
# The --judge mode skips the browser and computes verdicts+artifact from a
# pre-collected measurements file (same schema this script writes internally);
# it lets the verdict logic be tested without a live browser. The --discover
# mode reports which regions discovery found (and from which source) without
# touching the browser.
#
# Writes: <ref-dir>/dynamic-behavior-parity.json  (always — emit-or-fail).
# Exit: 0 pass, 1 fail, 2 setup error. English-only.

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

if [ "${1:-}" = "--judge" ]; then
  MODE="judge"
  MEAS_FILE="${2:?Usage: dynamic-behavior-parity.sh --judge <measurements-json> <ref-dir>}"
  REF_DIR="${3:?Usage: dynamic-behavior-parity.sh --judge <measurements-json> <ref-dir>}"
  SESSION=""; REF_URL=""; IMPL_URL=""
elif [ "${1:-}" = "--discover" ]; then
  MODE="discover"
  REF_DIR="${2:?Usage: dynamic-behavior-parity.sh --discover <ref-dir>}"
  SESSION=""; REF_URL=""; IMPL_URL=""; MEAS_FILE=""
else
  MODE="collect"
  SESSION="${1:?Usage: dynamic-behavior-parity.sh <session> <ref-url> <impl-url> <ref-dir>}"
  REF_URL="${2:?Usage: dynamic-behavior-parity.sh <session> <ref-url> <impl-url> <ref-dir>}"
  IMPL_URL="${3:?Usage: dynamic-behavior-parity.sh <session> <ref-url> <impl-url> <ref-dir>}"
  REF_DIR="${4:?Usage: dynamic-behavior-parity.sh <session> <ref-url> <impl-url> <ref-dir>}"
  MEAS_FILE=""
fi

[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 \
  "$SCRIPTS_DIR/lib/dynamic_behavior_parity.py" \
  "$MODE" "$SESSION" "$REF_URL" "$IMPL_URL" "$REF_DIR" "$MEAS_FILE"
