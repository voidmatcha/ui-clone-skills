#!/usr/bin/env bash
# verification-plan.sh — Synthesize tmp/ref/<c>/verification-plan.json from extraction
# and bundle-analysis artifacts. No browser work; pure JSON reading.
#
# Why this exists:
#   Running every downstream verification on every site is slow and noisy.
#   Skipping verifications by gut feel is how regressions ship. This script
#   produces a tiny manifest declaring *which classes of bug to look for on
#   this specific site*, based on what bundle/extraction artifacts already say.
#
# Usage:
#   bash verification-plan.sh tmp/ref/<component> [--tier=quick|standard|comprehensive]
#
# Tier (cost-tier filtering — env: UI_CLONE_VERIFY_TIER, default comprehensive):
#   quick         — static + JSON-comparison checks only (~10s total).
#                   Use during fast iteration loops where running the full
#                   browser sweep on every change is wasteful.
#   standard      — quick + one-shot browser interactions (~1min total).
#                   No 60fps video recording.
#   comprehensive — standard + 60fps frame-by-frame motion compares
#                   (video-motion / hover-state / click-state, ~5min+).
#                   Default — preserves prior unconditional behavior.
#
# Exit: 0 = wrote plan, 2 = setup error. Always tries to write — partial inputs
# still produce a useful plan with empty signals (hydration check still
# included since it is universal).
#
# Output: tmp/ref/<component>/verification-plan.json

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
VERIFICATION_PLAN_HELPER="$SCRIPT_DIR/lib/verification_plan_helpers.py"

TIER="${UI_CLONE_VERIFY_TIER:-comprehensive}"
REF_DIR=""
# --amend: after generation-plan.sh derives plan-specific rows (signatureEffects
# / scrollScrub), re-evaluate ONLY those plan-conditional add_check rows and
# append any missing ones to the EXISTING plan, leaving every other row frozen.
# Closes the ordering hole where the plan is minted at Step 5d (before
# generation-plan.json exists) so signature-effects-coverage never registered.
AMEND=0
for arg in "$@"; do
  case "$arg" in
    --amend) AMEND=1 ;;
    --tier=*) TIER="${arg#--tier=}" ;;
    --tier)
      echo "ERROR: --tier requires =value form (e.g. --tier=quick)" >&2
      exit 2
      ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      if [ -z "$REF_DIR" ]; then
        REF_DIR="$arg"
      else
        echo "ERROR: unexpected argument: $arg" >&2
        exit 2
      fi
      ;;
  esac
done

if [ -z "$REF_DIR" ]; then
  echo "Usage: verification-plan.sh <ref-dir> [--tier=quick|standard|comprehensive]" >&2
  exit 2
fi

case "$TIER" in
  quick|standard|comprehensive) ;;
  *)
    echo "ERROR: invalid --tier value: '$TIER' (use quick|standard|comprehensive)" >&2
    exit 2
    ;;
esac

# Check ids whose add_check condition reads generation-plan.json (Step 7-pre
# output). These are the ONLY rows --amend may append to an existing plan; every
# other row stays frozen so the closed-list property is preserved. Keep in sync
# with the generation-plan.json-gated add_check blocks below.
PLAN_DERIVED_CHECK_IDS="signature-effects-coverage"

# --amend snapshot: capture the existing plan (and adopt its tier) BEFORE the
# staleness block can delete it. With no existing plan, amend degrades to a
# normal full generation (which now naturally includes the plan-derived rows
# because generation-plan.json exists by Step 7-pre).
AMEND_BASE=""
if [ "$AMEND" = "1" ]; then
  if [ -f "$REF_DIR/verification-plan.json" ]; then
    AMEND_BASE="$(mktemp "${TMPDIR:-/tmp}/verification-plan-amend-base.XXXXXX")" || {
      echo "ERROR: cannot create amend snapshot file" >&2
      exit 2
    }
    cp "$REF_DIR/verification-plan.json" "$AMEND_BASE"
    BASE_TIER="$(python3 -c "import json,sys
try:
    print(json.load(open('$AMEND_BASE')).get('tier',''))
except Exception:
    pass" 2>/dev/null || true)"
    case "$BASE_TIER" in
      quick|standard|comprehensive) TIER="$BASE_TIER" ;;
    esac
  else
    echo "verification-plan.sh --amend: no existing plan at $REF_DIR/verification-plan.json — generating a fresh plan." >&2
  fi
fi

# Benchmark hardening: when the ref dir is under benchmark/work/, force
# comprehensive tier regardless of UI_CLONE_VERIFY_TIER / --tier. The 077d8c3
# benchmark exposed a gaming pattern where the agent set tier=quick to drop
# `asset-transfer`, `image-fidelity`, `transition-compare`, and `font-parity`
# from `requiredChecks` — the verification surface shrank while the
# implementation stayed monolithic. Benchmarks measure the FULL surface; the
# agent does not get to pick which checks fire.
case "$REF_DIR" in
  *benchmark/work/*)
    if [ "$TIER" != "comprehensive" ]; then
      echo "verification-plan.sh: benchmark context detected (ref under benchmark/work/) — forcing tier=comprehensive (was: $TIER)" >&2
      TIER="comprehensive"
    fi
    ;;
esac

# Tier ordering: a check tagged at min_tier=X runs only when active tier ≥ X.
tier_level() {
  case "$1" in
    quick) echo 1 ;;
    standard) echo 2 ;;
    comprehensive) echo 3 ;;
    *) echo 0 ;;
  esac
}
CURRENT_TIER_LEVEL=$(tier_level "$TIER")
case "${UI_CLONE_STRICT_WARNINGS:-}" in
  1|true|TRUE|yes|YES|on|ON) STRICT_WARNINGS_JSON=true ;;
  *) STRICT_WARNINGS_JSON=false ;;
esac

if [ ! -d "$REF_DIR" ]; then
  echo "ERROR: ref-dir not found: $REF_DIR" >&2
  exit 2
fi

COMPONENT=$(basename "$REF_DIR")

# Required: jq for clean JSON reading. node fallback if jq is missing.
HAS_JQ=0
if command -v jq >/dev/null 2>&1; then
  HAS_JQ=1
fi

# Resolve the concrete Python interpreter once. Version-manager shims (pyenv,
# asdf, etc.) can add seconds to every tiny `python3 -c` call; this script runs
# several JSON probes, so repeated shim startup makes even the quick tier slow
# enough to trip CI smoke timeouts. Callers such as bench-verification.sh may
# export PYTHON_BIN to avoid even this one resolution call.
if [ -z "${PYTHON_BIN:-}" ]; then
  if [ -x "$ROOT_DIR/.venv/bin/python3" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python3"
  elif [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=$(python3 -c 'import sys; print(sys.executable)' 2>/dev/null || command -v python3)
  else
    echo "ERROR: python3 required for verification-plan JSON probes" >&2
    exit 2
  fi
fi
[ -n "$PYTHON_BIN" ] || { echo "ERROR: cannot resolve python3 executable" >&2; exit 2; }

# Read a JSON path safely; return empty string if file or path missing.
# shellcheck disable=SC2329 # Kept as a local helper for plan extensions.
read_json() {
  local file="$1"
  local path="$2"
  [ -f "$file" ] || { echo ""; return 0; }
  if [ "$HAS_JQ" -eq 1 ]; then
    jq -r "$path // empty" "$file" 2>/dev/null
  else
    node -e "
      try {
        const d = require('fs').readFileSync(process.argv[1], 'utf8');
        const j = JSON.parse(d);
        const get = (o, p) => p.replace(/^\\./, '').split('.').reduce((a,k) => (a && a[k] !== undefined) ? a[k] : undefined, o);
        const v = get(j, process.argv[2]);
        if (v === undefined || v === null) process.exit(0);
        if (typeof v === 'object') process.stdout.write(JSON.stringify(v));
        else process.stdout.write(String(v));
      } catch (e) {}
    " "$file" "$path" 2>/dev/null
  fi
}

# Returns 1 if any line of $1 (a JSON array of strings, or a JSON object's
# stringified body) contains the regex in $2.
contains_pattern() {
  local file="$1"
  local pattern="$2"
  [ -f "$file" ] || return 1
  grep -Eq "$pattern" "$file" 2>/dev/null
}

contains_ref_pattern() {
  local pattern="$1"
  if grep -R -Eiq "$pattern" \
    "$REF_DIR/bundles" \
    "$REF_DIR/scroll-engine.json" \
    "$REF_DIR/animation-runtime-dump.json" \
    2>/dev/null; then
    return 0
  fi
  [ "${TRANSITION_SPEC_SIGNAL_ELIGIBLE:-false}" = "true" ] \
    && grep -Eiq "$pattern" "$TRANSITION_SPEC" 2>/dev/null
}

contains_ref_source_pattern() {
  local pattern="$1"
  if grep -R -Eiq "$pattern" \
    "$REF_DIR/bundles" \
    "$REF_DIR/scroll-engine.json" \
    2>/dev/null; then
    return 0
  fi
  [ "${TRANSITION_SPEC_SIGNAL_ELIGIBLE:-false}" = "true" ] \
    && grep -Eiq "$pattern" "$TRANSITION_SPEC" 2>/dev/null
}

runtime_scroll_trigger_signal() {
  "$PYTHON_BIN" -c 'import json, sys
from pathlib import Path
path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(1)
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
if not isinstance(data, dict):
    raise SystemExit(1)
value = data.get("scrollTrigger") or data.get("scrollTriggers")
raise SystemExit(0 if bool(value) else 1)' \
    "$REF_DIR/animation-runtime-dump.json" 2>/dev/null
}

transition_spec_scroll_scrub_signal() {
  "$PYTHON_BIN" "$VERIFICATION_PLAN_HELPER" \
    transition-spec-scroll-scrub "$TRANSITION_SPEC" 2>/dev/null
}

BUNDLE_MAP="$REF_DIR/bundle-map.json"
INTERACTIONS="$REF_DIR/interactions-detected.json"
EXTERNAL_SDKS="$REF_DIR/external-sdks.json"
SCROLL_ENGINE="$REF_DIR/scroll-engine.json"
TRANSITION_SPEC="$REF_DIR/transition-spec.json"
CANVAS_DETECT="$REF_DIR/canvas-webgl-detection.json"
PAID_FEATURES="$REF_DIR/paid-features.json"
TRANSITION_COVERAGE="$REF_DIR/transition-coverage.json"
ANIMATIONS_DETECTED="$REF_DIR/animations-detected.json"
ELEMENT_TRACKING="$REF_DIR/element-tracking.json"

# Auto-finalized placeholder specs describe a conservative implementation floor,
# not independently observed reference behavior. They must not feed motion
# signals back into the next plan and re-mint themselves indefinitely.
TRANSITION_SPEC_SIGNAL_ELIGIBLE="true"
if [ -f "$TRANSITION_SPEC" ]; then
  TRANSITION_SPEC_SOURCE=$(read_json "$TRANSITION_SPEC" ".source")
  TRANSITION_SPEC_PLACEHOLDER=$(read_json "$TRANSITION_SPEC" ".placeholder")
  if [ "$TRANSITION_SPEC_PLACEHOLDER" = "true" ] \
     || [ "$TRANSITION_SPEC_SOURCE" = "ui_clone.extraction_artifacts" ]; then
    TRANSITION_SPEC_SIGNAL_ELIGIBLE="false"
  fi
fi

# transition-coverage synthesized by the extraction finalizer is only a
# projection of transition-spec.json, not an observed-motion artifact. Keep
# real captured coverage eligible, but do not count the auto projection as an
# independent observed scroll signal.
TRANSITION_COVERAGE_SIGNAL="$TRANSITION_COVERAGE"
if [ "$(read_json "$TRANSITION_COVERAGE" ".source")" = "ui_clone.extraction_artifacts" ]; then
  TRANSITION_COVERAGE_SIGNAL="$REF_DIR/.no-observed-transition-coverage"
fi

# observed_motion_signal MODE — library-agnostic, closed-form detection of
# motion that was actually OBSERVED during extraction, independent of any
# library/token allowlist. Prints "true" / "false".
#   MODE=scroll  → page moved under scroll (scroll-scrub / parallax / sticky-pin)
#   MODE=reveal  → element entered an on-state as it scrolled into the viewport
# Python logic lives in a helper file instead of a Bash heredoc. Homebrew Bash
# can block in heredoc_write on macOS before the child interpreter ever runs.
observed_motion_signal() {
  local mode="$1"
  "$PYTHON_BIN" "$VERIFICATION_PLAN_HELPER" observed-motion \
    "$mode" "$TRANSITION_COVERAGE_SIGNAL" "$ANIMATIONS_DETECTED" "$ELEMENT_TRACKING"
}

OBSERVED_MOTION_FIELDS=$(observed_motion_signal all 2>/dev/null || echo "false false false false")
# shellcheck disable=SC2086 # split the four fixed boolean fields.
set -- $OBSERVED_MOTION_FIELDS
OBSERVED_SCROLL_SIGNAL="${1:-false}"
OBSERVED_REVEAL_SIGNAL="${2:-false}"
OBSERVED_CAROUSEL_SIGNAL="${3:-false}"
OBSERVED_VECTOR_SIGNAL="${4:-false}"

observed_motion_signal() {
  case "$1" in
    scroll) echo "$OBSERVED_SCROLL_SIGNAL" ;;
    reveal) echo "$OBSERVED_REVEAL_SIGNAL" ;;
    carousel) echo "$OBSERVED_CAROUSEL_SIGNAL" ;;
    vector) echo "$OBSERVED_VECTOR_SIGNAL" ;;
    *) echo "false" ;;
  esac
}

BOOLEAN_CSS_REVEAL_SIGNAL=$("$PYTHON_BIN" "$VERIFICATION_PLAN_HELPER" \
  boolean-css-reveal "$REF_DIR" 2>/dev/null || echo "false")

file_mtime_epoch() {
  local path="$1"
  stat -f %m "$path" 2>/dev/null \
    || stat -c %Y "$path" 2>/dev/null \
    || "$PYTHON_BIN" -c 'import os, sys; print(int(os.path.getmtime(sys.argv[1])))' "$path"
}

plan_generated_epoch() {
  "$PYTHON_BIN" "$VERIFICATION_PLAN_HELPER" plan-generated-epoch "$1"
}

PLAN_PATH="$REF_DIR/verification-plan.json"
if [ -f "$PLAN_PATH" ]; then
  NEWEST_EXTRACTION_MTIME=0
  # Motion-site review: expand staleness inputs to include
  # the v0.7.0 multi-snapshot capture artifacts AND the Phase 0 runtime
  # dump. Without these, motion-rich runs can regenerate states/*/ AND
  # animation-runtime-dump.json post-plan while the plan stays stale →
  # hasSplash/hasHover false-negatives + runtime-spec-coverage never
  # added. Listing them here forces re-derivation whenever they refresh.
  for EXTRACTION_ARTIFACT in \
    "$REF_DIR/extracted.json" \
    "$REF_DIR/structure.json" \
    "$TRANSITION_SPEC" \
    "$REF_DIR/animation-runtime-dump.json" \
    "$REF_DIR/state-structure-spec.json" \
    "$REF_DIR/states/splash/summary.json" \
    "$REF_DIR/states/scroll/summary.json" \
    "$REF_DIR/states/hover/summary.json" \
    "$REF_DIR/states/hover/manifest.json"; do
    if [ -f "$EXTRACTION_ARTIFACT" ]; then
      ARTIFACT_MTIME=$(file_mtime_epoch "$EXTRACTION_ARTIFACT" || echo 0)
      if [ "$ARTIFACT_MTIME" -gt "$NEWEST_EXTRACTION_MTIME" ]; then
        NEWEST_EXTRACTION_MTIME="$ARTIFACT_MTIME"
      fi
    fi
  done
  if [ "$NEWEST_EXTRACTION_MTIME" -gt 0 ]; then
    PLAN_GENERATED_AT=$(plan_generated_epoch "$PLAN_PATH" || echo 0)
    if [ "$PLAN_GENERATED_AT" -lt "$NEWEST_EXTRACTION_MTIME" ]; then
      echo "verification-plan.json is stale (generated before latest extraction OR Phase A/B/C/0 capture); regenerating." >&2
      rm -f "$PLAN_PATH"
      if [ -n "$AMEND_BASE" ]; then
        rm -f "$AMEND_BASE"
        AMEND_BASE=""
      fi
    fi
  fi
fi

# ── Signal extraction ──
# Each signal is OR of multiple proxy evidence. Conservatively true: if any
# proxy hits, the signal is on. This makes the plan err on the side of
# *running* a check — a false-positive runs an extra check (cheap), a
# false-negative misses a bug class (expensive).

# hasScrollScrub: scroll-driven animation present in some form.
# The allowlist greps below are param-extraction *hints* (one OR-input each).
# Observed motion (observed_motion_signal scroll) is an authoritative,
# library-agnostic OR-input: if pixels actually moved under scroll during
# extraction, dispatch the motion checks regardless of which library (or none)
# drove it. Catches unknown / hand-rolled motion that no token grep sees.
HAS_SCROLL_SCRUB="false"
if contains_pattern "$EXTERNAL_SDKS" '"(useScroll|scrollYProgress|ScrollTrigger|scrubbed|scrub)"' \
   || contains_pattern "$SCROLL_ENGINE" '"library":\s*"(Lenis|Locomotive|ScrollSmoother)"' \
   || contains_pattern "$SCROLL_ENGINE" '"(motion|useScroll|scrollYProgress)":\s*\{[^}]*"matches":\s*[1-9]' \
   || { [ "$TRANSITION_SPEC_SIGNAL_ELIGIBLE" = "true" ] \
        && [ "$(transition_spec_scroll_scrub_signal)" = "true" ]; } \
   || contains_pattern "$INTERACTIONS" '"engine":\s*"scroll"' \
   || contains_pattern "$BUNDLE_MAP" '"(useScroll|scrollYProgress|ScrollTrigger|scrub|gsap-scrolltrigger)"' \
   || [ "$(observed_motion_signal scroll)" = "true" ]; then
  HAS_SCROLL_SCRUB="true"
fi

# hasScrollStateMachine: scroll-driven motion that does work after scroll stop
# (smooth return, snap-back, section snap). This is stricter than
# hasScrollScrub: require both a progress signal and a controller signal. The
# runtime check self-skips if the reference does not actually auto-move at the
# tested viewport, so false positives cost one browser probe instead of hiding
# a missing settled/returned phase.
HAS_SCROLL_STATE_MACHINE="false"
if { contains_ref_source_pattern 'scrollYProgress|(^|[^[:alnum:]_])useScroll([^[:alnum:]_]|$)|ScrollTrigger|scrollY\.on|scroll[^[:alnum:]]*progress' \
     || runtime_scroll_trigger_signal; } \
   && contains_ref_source_pattern 'window\.scrollTo|[^[:alnum:]_]scrollTo[[:space:]]*\(|scrollIntoView|(^|[^[:alnum:]_])(setTimeout|clearTimeout|getVelocity|velocity|guardRef|autoReturning|isScrolling)([^[:alnum:]_]|$)'; then
  HAS_SCROLL_STATE_MACHINE="true"
elif contains_pattern "$SCROLL_ENGINE" 'ScrollTrigger|gsap-scrolltrigger|GSAP' \
   && contains_pattern "$SCROLL_ENGINE" '"(pin|scrub)":\s*true|\b(sticky-scrub|scroll-scrub|scroll-pin)\b'; then
  HAS_SCROLL_STATE_MACHINE="true"
elif [ "$TRANSITION_SPEC_SIGNAL_ELIGIBLE" = "true" ] \
   && contains_pattern "$TRANSITION_SPEC" '"trigger":\s*"(sticky-scrub|scroll-scrub|scroll-pin)"' \
   && contains_pattern "$SCROLL_ENGINE" 'ScrollTrigger|gsap-scrolltrigger|GSAP|Lenis'; then
  HAS_SCROLL_STATE_MACHINE="true"
fi

# hasIOReveal: viewport-entry animation dispatch signal. This classifier decides
# whether reveal-trigger must run; it is NOT transition-spec truth. In particular,
# BOOLEAN_CSS_REVEAL_SIGNAL is conservative structure+CSS evidence that a runtime
# probe is required. Step 5d must still add an evidence-backed transitions[] entry
# or a structured skipped[] reason before the spec gate can pass.
# As with
# hasScrollScrub, observed reveal-on-enter motion (observed_motion_signal
# reveal) is an authoritative OR-input — an element that went off-state→on-state
# as it entered the viewport, or a non-empty textReveals/reveals list, fires the
# reveal-trigger check even when no IO token is present.
HAS_IO_REVEAL="false"
if { [ "$TRANSITION_SPEC_SIGNAL_ELIGIBLE" = "true" ] \
     && contains_pattern "$TRANSITION_SPEC" '"trigger":\s*"(intersection|inview|onView)"'; } \
   || contains_pattern "$INTERACTIONS" '"trigger":\s*"intersection"' \
   || contains_pattern "$SCROLL_ENGINE" '"IntersectionObserver":\s*\{[^}]*"matches":\s*[1-9]' \
   || contains_pattern "$BUNDLE_MAP" 'IntersectionObserver' \
   || [ "$(observed_motion_signal reveal)" = "true" ] \
   || [ "$BOOLEAN_CSS_REVEAL_SIGNAL" = "true" ]; then
  HAS_IO_REVEAL="true"
fi

# hasHover — match any trigger value containing the word "hover" (covers
# variants: "hover", "css-hover", "css :hover", "scale-on-hover-target",
# "whileHover", etc.). interaction-detection.md / transition-spec-rules.md
# do not pin a single canonical string, so the regex must be lenient.
#
# Motion-site review: also derive from Phase C capture
# (states/hover/manifest.json) — that artifact records actual hover
# candidates discovered at runtime. Without this, hover plan flag stayed
# false even when capture-hover.sh found 13 candidates.
HAS_HOVER="false"
HOVER_MANIFEST="$REF_DIR/states/hover/manifest.json"
if contains_pattern "$INTERACTIONS" '"trigger":\s*"[^"]*[Hh]over[^"]*"' \
   || contains_pattern "$INTERACTIONS" '"whileHover"\s*:' \
   || contains_pattern "$INTERACTIONS" '"hoverDelta"\s*:'; then
  HAS_HOVER="true"
elif [ -f "$HOVER_MANIFEST" ]; then
  # entries length > 0 means capture-hover.sh found at least one hover
  # target — set HAS_HOVER even if upstream extraction missed it.
  HOVER_ENTRIES=$("$PYTHON_BIN" -c "
import json, sys
try:
    data = json.loads(open('$HOVER_MANIFEST').read())
    entries = data.get('entries', []) if isinstance(data, dict) else []
    print(len(entries))
except Exception:
    print(0)
" 2>/dev/null || echo 0)
  if [ "$HOVER_ENTRIES" -gt 0 ]; then
    HAS_HOVER="true"
  fi
fi

# hasSplash — interactions-detected.json carries `hasPreloader` per the
# Preloader/Splash protocol in bundle-analysis.md. Fall back to the
# splash-extraction artifacts in case the agent set hasPreloader=false but
# splash-extraction.md still produced output.
#
# Motion-site review: also derive from Phase A capture
# (states/splash/summary.json polls > 1). Without this, splash plan flag
# stayed false even when capture-states.sh found 2+ transitions —
# some GSAP splash signatures never set hasSplash:true, so no splash check
# was scheduled. Fall-through to transition-spec entries with page-load
# trigger keeps a third signal path.
DOM_STATE_DIFF="$REF_DIR/dom-state-diff.json"
SPLASH_CONTRACT="$REF_DIR/states/splash/contract.json"
SPLASH_SUMMARY="$REF_DIR/states/splash/summary.json"
HAS_SPLASH="false"
SPLASH_CONTRACT_SIGNAL="fallthrough"
if [ -f "$SPLASH_CONTRACT" ]; then
  SPLASH_CONTRACT_SIGNAL=$(SPLASH_CONTRACT_PATH="$SPLASH_CONTRACT" "$PYTHON_BIN" -c "
import json, os
try:
    data = json.loads(open(os.environ['SPLASH_CONTRACT_PATH']).read())
    if isinstance(data, dict) and data.get('detected') is True:
        print('true')
    elif isinstance(data, dict) and data.get('schemaVersion') is not None and data.get('detected') is False:
        if data.get('captureMode') == 'reuse-session':
            print('fallthrough')
        else:
            overlay = data.get('overlay') if isinstance(data.get('overlay'), dict) else None
            capture = data.get('capture') if isinstance(data.get('capture'), dict) else None
            has_overlay_metadata = overlay is not None and 'everVisible' in overlay
            has_capture_metadata = capture is not None
            if not has_overlay_metadata and not has_capture_metadata:
                print('false')
            elif capture is not None and capture.get('authoritativeNegative') is False:
                print('fallthrough')
            elif capture is not None and capture.get('authoritativeNegative') is True:
                print('false')
            else:
                ever_visible = bool(overlay.get('everVisible')) if overlay is not None else False
                state_count = capture.get('stateCount') if capture is not None else None
                timed_out = bool(capture.get('timedOut')) if capture is not None else False
                print('false' if (not ever_visible and state_count == 1 and not timed_out) else 'fallthrough')
    else:
        print('fallthrough')
except Exception:
    print('fallthrough')
" 2>/dev/null || echo fallthrough)
  if [ "$SPLASH_CONTRACT_SIGNAL" = "true" ] || [ "$SPLASH_CONTRACT_SIGNAL" = "false" ]; then
    HAS_SPLASH="$SPLASH_CONTRACT_SIGNAL"
  fi
fi
if [ "$SPLASH_CONTRACT_SIGNAL" != "false" ] && { contains_pattern "$INTERACTIONS" '"hasPreloader":\s*true' \
   || contains_pattern "$INTERACTIONS" '"hasSplash":\s*true' \
   || contains_pattern "$DOM_STATE_DIFF" '"(dom_changes|splashElements|changes|preloaderRemoved)":\s*\[?[^][}{]'; }; then
  HAS_SPLASH="true"
elif [ "$SPLASH_CONTRACT_SIGNAL" != "false" ] && [ -f "$SPLASH_SUMMARY" ]; then
  # polls > 1 = capture-states.sh recorded at least one class transition
  # during the splash window (loading → loaded). Treat as splash present.
  SPLASH_POLLS=$("$PYTHON_BIN" -c "
import json, sys
try:
    data = json.loads(open('$SPLASH_SUMMARY').read())
    print(int(data.get('polls') or 0) if isinstance(data, dict) else 0)
except Exception:
    print(0)
" 2>/dev/null || echo 0)
  if [ "$SPLASH_POLLS" -gt 1 ]; then
    HAS_SPLASH="true"
  fi
elif [ "$SPLASH_CONTRACT_SIGNAL" != "false" ] && contains_pattern "$TRANSITION_SPEC" '"trigger":\s*"(page-?load|onLoad|load)"'; then
  HAS_SPLASH="true"
fi

# hasCanvas
HAS_CANVAS="false"
if contains_pattern "$CANVAS_DETECT" '"hasCanvas":\s*true'; then
  HAS_CANVAS="true"
fi

# hasCustomScroll
HAS_CUSTOM_SCROLL="false"
if contains_pattern "$SCROLL_ENGINE" '"hasTransformScroll":\s*true' \
   || contains_pattern "$SCROLL_ENGINE" '"nativeScrollDisabled":\s*true'; then
  HAS_CUSTOM_SCROLL="true"
fi

# hasCommercialFont
HAS_COMMERCIAL_FONT="false"
if [ -f "$PAID_FEATURES" ]; then
  # Any paidFonts[] entry implies commercial font involvement.
  if contains_pattern "$PAID_FEATURES" '"paidFonts":\s*\[\s*\{'; then
    HAS_COMMERCIAL_FONT="true"
  fi
fi

# hasClickStateTransition — captures landing in regions.json under any
# triggerType starting with "click-" (click-toggle, click-cycle,
# click-content-swap), or interactions-detected.json with trigger=="click".
# Click-driven UI (tabs/accordions/modals/menu toggles) has its own motion
# arc that hover-compare + section-compare never exercise; this signal gates
# whether click-state-compare.sh is required.
HAS_CLICK_STATE="false"
REGIONS_JSON="$REF_DIR/regions.json"
STATE_STRUCTURE_SPEC="$REF_DIR/state-structure-spec.json"
if contains_pattern "$REGIONS_JSON" '"triggerType":\s*"click-' \
   || contains_pattern "$INTERACTIONS" '"trigger":\s*"click"' \
   || contains_pattern "$INTERACTIONS" '"type":\s*"click-' \
   || contains_pattern "$STATE_STRUCTURE_SPEC" '"phase":\s*"click"' \
   || contains_pattern "$TRANSITION_SPEC" '"trigger":\s*"click"'; then
  HAS_CLICK_STATE="true"
fi

# hasLottie — Lottie/bodymovin/dotlottie needs a real runtime + local JSON,
# not generic CSS/GSAP motion. Evidence can come from bundle resources,
# transition specs, runtime dumps, or canvas/WebGL detection.
HAS_LOTTIE="false"
LOTTIE_SIGNAL_PATTERN='[Ll]ottie[[:space:]]*\.|[Ll]ottie["[:space:]]*:[[:space:]]*(\{|\[|")|lottie-web|lottie-react|bodymovin|dotlottie|lottie-player|\.lottie'
for LOTTIE_FILE in \
  "$BUNDLE_MAP" \
  "$TRANSITION_SPEC" \
  "$REF_DIR/animation-runtime-dump.json" \
  "$REF_DIR/canvas-webgl-detection.json" \
  "$REF_DIR/external-sdks.json" \
  "$REF_DIR/interactions-detected.json" \
  "$REF_DIR/assets.json" \
  "$REF_DIR/extracted.json"; do
  if contains_pattern "$LOTTIE_FILE" "$LOTTIE_SIGNAL_PATTERN"; then
    HAS_LOTTIE="true"
    break
  fi
done
if [ "$HAS_LOTTIE" != "true" ] && contains_ref_pattern "$LOTTIE_SIGNAL_PATTERN"; then
  HAS_LOTTIE="true"
fi
# Fix B (additive OR-input): a <canvas> surface OBSERVED advancing frames at
# runtime is a vector/canvas player (Rive .riv, custom JSON-on-canvas) even with
# no lottie/bodymovin name token. hasCanvas + observed continuous frame motion →
# dispatch the runtime gate. The name-grep above stays as a hint; this only makes
# MORE real cases dispatch. A static canvas (no observed motion) does NOT trigger.
if [ "$HAS_LOTTIE" != "true" ] && [ "$HAS_CANVAS" = "true" ] \
   && [ "$(observed_motion_signal vector)" = "true" ]; then
  HAS_LOTTIE="true"
fi

# hasSwiper — card rails/sliders whose spacing and translate are runtime-owned.
HAS_SWIPER="false"
if contains_ref_pattern '\bSwiper\b|swiper-wrapper|swiper-slide|swiper\.bundle|swiper/css'; then
  HAS_SWIPER="true"
fi
# Fix B (additive OR-input): an OBSERVED auto-rotating carousel/slideshow
# (animations-detected autoTimers) is runtime-owned card motion regardless of
# library — Embla/Splide/keen-slider/hand-rolled all surface here, not just
# Swiper. The Swiper class grep above stays as a hint; this only widens dispatch.
# A page with no observed auto-rotation does NOT trigger.
if [ "$HAS_SWIPER" != "true" ] && [ "$(observed_motion_signal carousel)" = "true" ]; then
  HAS_SWIPER="true"
fi

# ── Required checks (dispatch table) ──
# Built incrementally as JSON array body.

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

CHECKS_FILE="$(mktemp "${TMPDIR:-/tmp}/verification-plan-checks.XXXXXX")" || {
  echo "ERROR: cannot create temporary checks file" >&2
  exit 2
}
CHECK_COUNT=0
DEFERRED_FILE="$(mktemp "${TMPDIR:-/tmp}/verification-plan-deferred.XXXXXX")" || {
  echo "ERROR: cannot create temporary deferred-checks file" >&2
  exit 2
}
DEFERRED_COUNT=0
trap 'rm -f "$CHECKS_FILE" "$DEFERRED_FILE"' EXIT

# add_check id script produces reason severity [min_tier]
#   min_tier: quick | standard | comprehensive (default: standard)
#             — see tier table at top of file. A check is dispatched only when
#             active tier level ≥ min_tier level.
add_check() {
  local id="$1" script="$2" produces="$3" reason="$4" severity="$5"
  local min_tier="${6:-standard}"
  local depends_on="${7:-}"
  # Optional 8th arg: argsRecipe — a dispatch template using {ref_dir},
  # {impl_url}, {ref_url}, {session} placeholders (same vocabulary as
  # run-required-checks.sh SIGNATURES). When present, the dispatcher uses
  # it directly and the check needs NO SIGNATURES entry — new checks
  # self-describe instead of requiring the second hand-synced table.
  local args_recipe="${8:-}"
  local check_level
  case "$min_tier" in
    quick) check_level=1 ;;
    standard) check_level=2 ;;
    comprehensive) check_level=3 ;;
    *)
      echo "ERROR: add_check '$id' got invalid min_tier '$min_tier'" >&2
      exit 2
      ;;
  esac
  if [ "$check_level" -gt "$CURRENT_TIER_LEVEL" ]; then
    # Tier-dropped checks are recorded, not silently discarded — the plan
    # must say what was NOT verified at this tier so closeout consumers can
    # treat it as tracked debt instead of reading absence as coverage.
    if [ "$DEFERRED_COUNT" -gt 0 ]; then
      printf ',\n' >> "$DEFERRED_FILE"
    fi
    printf '    { "id": "%s", "minTier": "%s", "severity": "%s" }' \
      "$id" "$min_tier" "$severity" >> "$DEFERRED_FILE"
    DEFERRED_COUNT=$((DEFERRED_COUNT + 1))
    return 0
  fi
  local recipe_field=""
  if [ -n "$args_recipe" ]; then
    recipe_field=",
      \"argsRecipe\": \"$args_recipe\""
  fi
  local depends_field=""
  if [ -n "$depends_on" ]; then
    local deps_json="["
    local first=1
    for dep in $depends_on; do
      if [ "$first" = "1" ]; then
        deps_json="${deps_json}\"$dep\""
        first=0
      else
        deps_json="${deps_json}, \"$dep\""
      fi
    done
    deps_json="${deps_json}]"
    depends_field=",
      \"dependsOn\": $deps_json"
  fi
  if [ "$CHECK_COUNT" -gt 0 ]; then
    printf ',\n' >> "$CHECKS_FILE"
  fi
  {
    printf '    {\n'
    printf '      "id": "%s",\n' "$id"
    printf '      "script": "%s",\n' "$script"
    printf '      "produces": "%s",\n' "$produces"
    printf '      "reason": "%s",\n' "$reason"
    printf '      "severity": "%s",\n' "$severity"
    printf '      "tier": "%s"%s%s\n' "$min_tier" "$recipe_field" "$depends_field"
    printf '    }'
  } >> "$CHECKS_FILE"
  CHECK_COUNT=$((CHECK_COUNT + 1))
}

# Universal local-port guard — must run before any agent-browser comparison
# that opens <impl-url>. runtime-env still performs deeper browser checks, but
# this cheap lsof/cwd guard prevents stale dev servers from making direct
# live-parity/compare runs inspect the wrong impl tree.
add_check "impl-url-guard" \
          "scripts/verify/impl-url-guard.sh" \
          "impl-url-guard.json" \
          "Local impl-url ports must be served by the canonical implRoot recorded for this ref; stale/orphan dev servers are hard failures before browser comparison" \
          "block" \
          "standard"

# Capacity probe — materializes conservative local concurrency guidance for
# browser-heavy verification. The dispatcher is sequential today, but the
# artifact gives Team/ultrawork/agent loops a repo-native source of truth for
# safe wave sizing instead of guessing from machine class.
add_check "capacity-probe" \
          "scripts/verify/capacity-check.sh" \
          "capacity-report.json" \
          "Estimate safe browser verification wave size before heavy visual checks" \
          "block" \
          "quick"

# Universal — runtime-env MUST be near the top in the dispatch order. Every
# browser-probe gate downstream declares dependsOn:runtime-env, but
# scripts/verify/run-required-checks.sh only cascades skips for deps
# that have already failed (in array iteration order). So runtime-env
# must run before its dependents — registering it here ensures it gets
# dispatched first. A prior bottom-of-file placement made the dependsOn
# cascade a silent no-op.
add_check "runtime-env" \
          "skills/visual-debug/scripts/runtime-env-check.sh" \
          "runtime-env.json" \
          "Impl-url must serve current iteration's impl-root AND render without env traps (Vite preamble missing, hydration mismatch, port-routing mismatch)" \
          "block" \
          "standard" \
          "impl-url-guard"

add_check "preview-runtime-health" \
            "skills/visual-debug/scripts/preview-runtime-health-check.sh" \
            "preview-runtime-health.json" \
            "Preview runtime must keep impl build assets same-origin, avoid mobile/tablet horizontal overflow, and match reference scroll-state/header mutations" \
            "block" \
            "standard" \
            "runtime-env"

# Universal first-paint visibility. This is a cheap browser probe, but it is
# intentionally standard-tier (not quick) because it opens a page. It catches a
# class that hydration/runtime-env can miss: DOM exists and selectors work, but
# copied loader CSS such as `body { opacity: 0 }` keeps the entire viewport
# blank because the original ready/unlock JS was not reproduced.
add_check "blank-viewport" \
          "skills/visual-debug/scripts/blank-viewport-check.sh" \
          "blank-viewport.json" \
          "Impl first paint must be visible; DOM/text with html/body/root opacity:0, visibility:hidden, display:none, or no paintable content is a hard failure" \
          "block" \
          "standard" \
          "runtime-env"

# Universal — always.
# Tier=quick: hydration-check launches a single agent-browser session and
# greps the console for known errors; runs in seconds and catches the most
# common "page didn't boot" regression class. Cheap enough to keep at quick.
# Universal — the generated tree must be able to resolve its own imports.
# Nothing in the pipeline checked this, which is how a 35/35-PASS reference
# shipped a scaffold that could not build (unresolvable ./lib/ScrollLatchDriver
# from a driver whose two gate predicates disagreed). Static and cheap: pure
# Python over src/**/*.tsx, no node_modules and no build required, so it belongs
# at the quick tier where the inner loop actually runs it.
add_check "unresolved-imports" \
          "skills/visual-debug/scripts/lib/regate_unresolved_imports.py" \
          "unresolved-imports.json" \
          "Universal — an emitted import with no file behind it cannot build" \
          "block" \
          "quick" \
          "" \
          "{impl_root} --check --report {ref_dir}/unresolved-imports.json"

add_check "hydration-check" \
          "skills/visual-debug/scripts/hydration-check.sh" \
          "hydration-check.json" \
          "Universal — every HTML page must hydrate cleanly" \
          "block" \
          "quick" \
          "runtime-env"

# Universal — Tailwind v3↔v4 transform conflict. Script writes `status: pass`
# on hosts that don't use Tailwind (or use a single version) so this is safe
# to require unconditionally. Catches Root Cause I in diagnosis.md.
# Tier=quick: pure static analysis over downloaded CSS — no browser, no IO.
add_check "tailwind-transform-conflict" \
          "skills/visual-debug/scripts/tailwind-transform-conflict-check.sh" \
          "tailwind-conflict.json" \
          "Universal — elements with both \`transform:\` and individual \`translate:\`/\`rotate:\`/\`scale:\` stack twice (v3↔v4 conflict)" \
          "block" \
          "quick"

# Universal anti-cheat baseline — these are not signal-gated. They reject
# direct HTML theft, screenshot-backed fake parity, and proxy/cache mirrors on
# every site before advisory/static fidelity rows are considered.
add_check "html-paste" \
          "skills/visual-debug/scripts/html-paste-check.sh" \
          "html-paste.json" \
          "Impl entry HTML must not mirror ref dom-scaffold (>=70% similarity), load ref bundle scripts, or inline ref CSS bundles" \
          "block" \
          "quick"

add_check "ref-screenshot-asset" \
          "skills/visual-debug/scripts/ref-screenshot-asset-check.sh" \
          "ref-screenshot-asset.json" \
          "Impl must not reference or copy reference screenshot artifacts (sections/, static/, transitions/) — using them as backgrounds fakes pixel-diff parity" \
          "block" \
          "quick"

add_check "proxy-mirror-check" \
          "skills/visual-debug/scripts/proxy-mirror-check.sh" \
          "proxy-mirror-check.json" \
          "Universal — local impl must be generated source, not a proxy/cache of the original HTML, RSC payloads, or _next runtime" \
          "block" \
          "quick"

# Universal — bundle-paste anti-cheat. Catches the cheat shape where impl
# bulk-pastes the ref's compiled CSS bundles into public/css/ (or any
# hex-hash-filename dir), mirrors the ref's _next/static/ runtime, or imports
# the ref's rendered HTML via Vite ?raw + dangerouslySetInnerHTML. Pure
# filesystem + regex (no browser), so tier=quick.
add_check "bundle-paste" \
          "skills/visual-debug/scripts/bundle-paste-check.sh" \
          "bundle-paste-check.json" \
          "Universal — impl must not bulk-paste ref's compiled CSS bundles, _next runtime, or rendered HTML (?raw + dangerouslySetInnerHTML)" \
          "block" \
          "quick"

# Universal — Fix 8 anti-fabrication gates. Both are pure static analysis
# (no LLM, no browser) so they're tier=quick. They compare the generated
# Phase-4 components against <ref-dir>/dom-scaffold.json:
#   text-fidelity-check  — block JSX text-position strings not in scaffold
#   dom-mirror-check     — advisory JSX tag-multiset divergence signal
add_check "text-fidelity-check" \
          "skills/visual-debug/scripts/text-fidelity-check.sh" \
          "text-fidelity-check.json" \
          "Universal — JSX text-position strings must be verbatim from ref (dom-scaffold.json allowlist)" \
          "block" \
          "quick"

# Cross-run measurement: LLM-generated clones often produced 80%+
# tag-multiset divergence by collapsing ref's deeply-nested div soup
# (~1063 nodes) into clean React components (~200 nodes). The signal is
# real (catches eviscerated impls) but the strict threshold made block
# severity unreachable for legitimate React component patterns. Downgraded
# to warn so the divergence number stays informational without blocking
# section-compare-PASS impls. The "hero composite must exist" enforcement
# moved to hero-composite-check.sh which spot-checks the specific 4-element
# pattern (video + button + h1/h2 + label) the LLM consistently drops.
# Operators with non-React targets (1:1 HTML clones) can re-tighten via
# UI_CLONE_DOM_MIRROR_THRESHOLD env var on dom-mirror-check.sh directly.
add_check "dom-mirror-check" \
          "skills/visual-debug/scripts/dom-mirror-check.sh" \
          "dom-mirror-check.json" \
          "Advisory — JSX tag-multiset divergence vs ref (informational; legit React composition produces 80%+ divergence)" \
          "warn" \
          "quick"

# Hero composite spot-check — replaces dom-mirror's role as the structural
# enforcer. Verifies the impl contains every element kind present in ref's
# hero region (video, button, h1/h2, label span). LLMs drop the 4-layer
# composite into 1-2 layers consistently; this check catches that
# without the noise of full-tree divergence.
add_check "hero-composite-check" \
          "skills/visual-debug/scripts/hero-composite-check.sh" \
          "hero-composite.json" \
          "Impl hero region must contain every element kind present in ref hero (video, button, h1/h2, label span)" \
          "block" \
          "quick" \
          "runtime-env"

if [ "$HAS_LOTTIE" = "true" ]; then
  add_check "lottie-runtime" \
            "skills/visual-debug/scripts/lottie-runtime-check.sh" \
            "lottie-runtime.json" \
            "Lottie/bodymovin/dotlottie detected — impl must use a real runtime package and downloaded animation JSON" \
            "block" \
            "quick"
  # Static slot-identity gate: bundle-impl-coverage/required-media pass on the
  # mere presence of the JSON string, but the navercorp clone mounted only 2 of
  # 5 slots with inverted loop/autoplay and one asset in an invented container.
  # This parses the actual loadAnimation()/mount call sites against the spec's
  # container->asset map. Self-describing argsRecipe -> no SIGNATURES entry (the
  # script takes <ref-dir> and resolves impl via find-impl-root).
  add_check "lottie-slot-identity" \
            "skills/visual-debug/scripts/lottie-slot-identity-check.sh" \
            "lottie-slot-identity.json" \
            "Lottie detected — every spec slot must mount its exact container/asset with matching loop/autoplay" \
            "block" \
            "quick" \
            "" \
            "{ref_dir} {impl_root}"
fi

# Unconditional.
# Tier=standard: geometry-sanity renders the impl at the CAPTURE viewport and
# compares docH + major-section heights against the ref capture geometry
# (orig-layout totalHeight + section-map heights). Catches the class pixel
# metrics structurally miss: a build scoring its best dSSIM while the document
# is 2x the ref height (ballooned/collapsed pages). One-shot browser measure.
add_check "geometry-sanity" \
          "skills/visual-debug/scripts/geometry-sanity-check.sh" \
          "geometry-sanity.json" \
          "rendered docH + major section heights must track the ref capture (docH >15% or any major section >25% off = fail; warn band below)" \
          "block" \
          "standard"

# Unconditional.
# Tier=quick: junk-token is a static impl-source scan plus ONE cheap DOM
# eval (same cost class as hydration-check, which is also quick). Catches
# serialization junk — 'undefined'/'null'/'NaN'/'[object Object]' as
# standalone tokens in className/id/src/href/alt/style — in source and in
# the live DOM (template-string junk only materializes at runtime).
add_check "junk-token" \
          "skills/visual-debug/scripts/junk-token-check.sh" \
          "junk-token.json" \
          "serialization junk (undefined/null/NaN/[object Object]) must never appear as a standalone token in className/id/src/href/alt/style — impl source or runtime DOM" \
          "block" \
          "quick" \
          "" \
          "{ref_dir} {impl_src} {session}-junk {impl_url}"

# Unconditional.
# Tier=quick: alignment-parity is pure file IO over the section-compare
# enumeration artifacts (matches.json already records both sides' rects,
# contentBox and contentGroups per fan-out viewport — zero extra browser
# time). Catches the loop-9 class AE crops are structurally blind to: a
# full-bleed section whose rect is IDENTICAL ref-vs-impl while the inner
# content column / a card group is horizontally off-center (pixel constants
# baked for one design width). Frozen refs without contentBox surface as
# warn (unmeasurable + recapture remediation), never a silent pass.
add_check "alignment-parity" \
          "skills/visual-debug/scripts/alignment-parity-check.sh" \
          "alignment-parity.json" \
          "matched sections must keep the ref's horizontal alignment: section-center offset, contentBox gap asymmetry, and per-container group asymmetry are compared ref-relative per fan-out viewport" \
          "block" \
          "quick" \
          "" \
          "{ref_dir}"

# Conditional.
# Tier=standard: scroll-end-completion opens ref + impl in agent-browser,
# scrolls to maxScroll once per viewport, and reads computed styles. One-shot
# browser interaction — fits standard tier.
if [ "$HAS_SCROLL_SCRUB" = "true" ]; then
  add_check "scroll-end-completion" \
            "skills/visual-debug/scripts/scroll-end-completion-check.sh" \
            "scroll-completion.json" \
            "signals.hasScrollScrub=true — scroll-scrub reveals must settle by maxScroll across all viewports" \
            "block" \
            "standard" \
            "runtime-env"
fi

# Tier=standard: compares ref and impl at initial, active/expanded, and
# settled/returned phases when the bundle/spec suggests scroll-stop logic
# such as smooth return, snap-back, timers, velocity checks, or guard refs.
if [ "$HAS_SCROLL_STATE_MACHINE" = "true" ]; then
  add_check "scroll-state-machine" \
            "skills/visual-debug/scripts/scroll-state-machine-check.sh" \
            "scroll-state-machine.json" \
            "signals.hasScrollStateMachine=true — scroll state-machine transitions require initial → active/expanded → settled/returned runtime proof" \
            "block" \
            "standard" \
            "runtime-env"
fi

# Tier=standard: same browser-one-shot shape as scroll-end-completion.
if [ "$HAS_IO_REVEAL" = "true" ]; then
  add_check "reveal-trigger" \
            "skills/visual-debug/scripts/reveal-trigger-check.sh" \
            "reveal-trigger.json" \
            "signals.hasIOReveal=true — initially-hidden elements must advance after IO fires" \
            "block" \
            "standard" \
          "runtime-env"
fi

if [ "$HAS_SPLASH" = "true" ]; then
  add_check "splash-lifecycle" \
            "skills/visual-debug/scripts/splash-lifecycle-check.sh" \
            "splash-lifecycle.json" \
            "signals.hasSplash=true — first-load splash overlays must mount, change phase, and exit on both ref and impl; static screenshots and background video motion are not lifecycle proof" \
            "block" \
            "standard" \
            "runtime-env" \
            "{session}-splash {ref_url} {impl_url} {ref_dir}"
fi

add_check "svg-provenance" \
          "skills/visual-debug/scripts/svg-provenance-check.sh" \
          "svg-provenance.json" \
          "Impl <svg> geometry must trace back to ref (catches LLM-invented icons satisfying svg-dom-parity count only)" \
          "block" \
          "standard" \
          "runtime-env"

# Tier 1-5 composite enforcement: runtime-proof.json is a roll-up validator over every
# existing runtime-measurement artifact. Does NOT run new probes —
# instead checks that each constituent gate produced an artifact AND
# the artifact contains real measurement (not a measurement-free
# status=pass). tier=quick because it's pure file IO.
add_check "runtime-proof" \
          "skills/visual-debug/scripts/runtime-proof-rollup.sh" \
          "runtime-proof.json" \
          "Composite roll-up: every runtime/state/no-cheat gate must produce a measurement-bearing artifact (catches measurement-free status=pass and missing source artifacts)" \
          "block" \
          "quick"

# Tier 3 composite: transition-proof.json rolls up spec-coverage + spec-implementation +
# transition-coverage runtime probe + reveal + scroll-end + keyframes +
# video-motion. Fails on partial coverage and empty runtime probes that
# the individual gates didn't themselves fail.
add_check "transition-proof" \
          "skills/visual-debug/scripts/transition-proof-rollup.sh" \
          "transition-proof.json" \
          "Composite roll-up: every transition-spec entry must have impl file + motion declaration + runtime probe evidence (catches partial coverage and empty runtime probes)" \
          "block" \
          "quick"

# Dynamic final-state anti-cheat — self-skips when the reference has no
# scroll/intersection/state-class evidence. Blocks reveal-all patches such as
# hardcoded is-active/is-visible/is-show plus transition:none/transform:none.
add_check "forced-state-class" \
          "skills/visual-debug/scripts/forced-state-class-check.sh" \
          "forced-state-class.json" \
          "Impl must not force dynamic reveal/state classes or final styles at load; scroll/intersection transitions need runtime triggers" \
          "block" \
          "quick"

# When sanitize-ref-css.sh flags a first-paint root lock (body{opacity:0} et
# al.) the impl must release it at runtime, or the whole page renders invisible
# and every visual compare degrades into blank-vs-content noise. Skips itself
# when no sanitize report / no lock exists.
add_check "body-opacity-unlock" \
          "skills/visual-debug/scripts/body-opacity-unlock-check.sh" \
          "body-opacity-unlock.json" \
          "Ref CSS first-paint root lock (e.g. body{opacity:0}) must be released by impl runtime or a local override" \
          "block" \
          "quick"

# Dual-session live sweep — catches the defect classes section-compare's
# dynamic-region masks can hide (missing/extra images incl. percent-encoded
# filenames, stray oversized text such as visibly-rendered pseudo content,
# global geometry drift). Deterministic DOM census findings block; the
# full-frame AE/dssim depths remain advisory inside live-parity.json.
add_check "live-parity-sweep" \
          "skills/visual-debug/scripts/live-parity-sweep.sh" \
          "live-parity.json" \
          "Live dual-session DOM census must not reveal mask-hidden generic defects: missing images, visible pseudo duplication, broken assets, or global geometry/count drift" \
          "block" \
          "standard" \
          "runtime-env"

if [ "$HAS_LOTTIE" = "true" ] && { [ "$HAS_SCROLL_SCRUB" = "true" ] || [ "$HAS_SCROLL_STATE_MACHINE" = "true" ] || contains_ref_pattern 'ScrollTrigger|scrollYProgress|scrub\s*:\s*true|useScroll'; }; then
  add_check "lottie-scroll-scrub" \
            "skills/visual-debug/scripts/lottie-scroll-scrub-check.sh" \
            "lottie-scroll-scrub.json" \
            "Scroll-scrubbed Lottie must bind frames to scroll progress and match visible/active containers at 0/25/50/75/100% scroll; autoplay/loop or one-container-for-many fails" \
            "block" \
            "standard" \
            "runtime-env"
fi

if [ "$HAS_SWIPER" = "true" ]; then
  add_check "swiper-runtime" \
            "skills/visual-debug/scripts/swiper-runtime-check.sh" \
            "swiper-runtime.json" \
            "Swiper card rails must use the real runtime or extracted sizing/translate; copied swiper-wrapper/swiper-slide classes are insufficient" \
            "block" \
            "quick"
fi

# Tier 5 anti-cheat: the existing gates catch screenshot/HTML cheats; this catches the
# remaining big cheat — loading ref's compiled JS bundle directly via
# <script src>, dynamic import(), or fetch() from impl source or
# runtime. Scans impl source tree for ref-host references and (with
# impl-url) inspects performance.getEntriesByType("resource") for
# cross-origin requests to the ref. tier=quick (filesystem only when
# no impl-url; one viewport when impl-url present).
add_check "ref-js-loader" \
          "skills/visual-debug/scripts/ref-js-loader-check.sh" \
          "ref-js-loader.json" \
          "Impl must not load ref site's JavaScript at build or runtime (catches the documented Tier 5 'load ref bundle to fake runtime' cheat)" \
          "block" \
          "quick"

add_check "video-play-proof" \
          "skills/visual-debug/scripts/video-play-proof-check.sh" \
          "video-play-proof.json" \
          "Impl <video> must advance currentTime at runtime, not just exist (catches static-poster cheats and missing autoplay/playsinline)" \
          "block" \
          "standard" \
          "runtime-env"

add_check "impl-scope" \
          "skills/visual-debug/scripts/impl-scope-check.sh" \
          "impl-scope.json" \
          "Impl iteration must only modify files under the impl root; editing plugin tooling (skills/, scripts/, ui_clone/, tests/) is the documented gate-cheat pattern" \
          "block" \
          "quick"

add_check "color-token-grounding" \
          "skills/visual-debug/scripts/color-token-grounding-check.sh" \
          "color-token-grounding.json" \
          "Every impl color literal must trace to ref's extracted color palette (blocks 'invent plausible color' failures)" \
          "block" \
          "quick"

add_check "duration-easing-grounding" \
          "skills/visual-debug/scripts/duration-easing-grounding-check.sh" \
          "duration-easing-grounding.json" \
          "Impl transition durations/easings must come from ref artifacts, not guessed values" \
          "block" \
          "quick"

add_check "mobile-viewport-parity" \
          "skills/visual-debug/scripts/mobile-viewport-parity-check.sh" \
          "mobile-viewport-parity.json" \
          "Impl must render at mobile viewport (375x812) with no h-overflow, working mobile nav, vertical content stacking matching ref" \
          "block" \
          "standard" \
          "runtime-env"

add_check "runtime-frame-proof" \
          "skills/visual-debug/scripts/runtime-frame-proof-check.sh" \
          "runtime-frame-proof.json" \
          "Animation surfaces (canvas/WebGL/Lottie instance) must advance frames at runtime (stricter than DOM mutation heuristic)" \
          "block" \
          "standard" \
          "runtime-env"

#
# Gate fires unconditionally — every clone has a header/nav. The gate
# self-skips when the ref's own header is static (single-page apps with
# no scroll-driven nav). tier=standard because it requires two viewport
# loads (ref + impl) and a 1.5s settle each — heavier than tier=quick
# static rows but cheaper than the 60fps video comparisons.
add_check "header-state-runtime" \
          "skills/visual-debug/scripts/header-state-runtime-check.sh" \
          "header-state-runtime.json" \
          "Impl header must mutate className/data-* on scroll when ref does (prove runtime controller, not static HTML paste)" \
          "block" \
          "standard" \
          "runtime-env"

# Cheap 5-point scroll-trajectory pre-check at STANDARD tier. A trajectory
# FAIL is a reliable gross-scroll-motion mismatch signal during inner
# iteration loops (seconds, not the 5min+ 60fps sweep); a PASS is
# INCONCLUSIVE on easing — video-motion-compare below stays the authority.
# Self-describing argsRecipe -> no SIGNATURES entry needed.
if [ "$HAS_SCROLL_SCRUB" = "true" ] || [ "$HAS_IO_REVEAL" = "true" ]; then
  add_check "transition-trajectory" \
            "skills/visual-debug/scripts/transition-trajectory-compare.sh" \
            "transitions/trajectory-result.txt" \
            "scroll motion present — 5-point scroll-trajectory AE pre-check; FAIL = gross scroll-motion mismatch (easing verdict stays with video-motion-compare)" \
            "block" \
            "standard" \
            "runtime-env" \
            "{ref_url} {impl_url} {session} {ref_dir}"
fi

# 60fps video motion compare. Fires whenever any motion signal is true.
# Closes the "right destination, wrong velocity-curve" failure class that the
# 5-point trajectory probe above cannot see — easeOutCubic vs easeOutQuint
# read identical at 0/25/50/75/100 % of scroll but feel different to a user.
# transition-trajectory now runs as the standard-tier pre-filter row above;
# video-motion-compare also invokes it internally as its pre-filter. Splash mode adds page-load motion coverage that the
# rest of the verification pipeline (static screenshots, hover compare) misses.
# Tier=comprehensive: records ~5-10s of 60fps video per signal class and
# SSIMs every pair — the most expensive row in the dispatch.
if [ "$HAS_SCROLL_SCRUB" = "true" ] || [ "$HAS_IO_REVEAL" = "true" ] || [ "$HAS_SPLASH" = "true" ]; then
  add_check "video-motion-compare" \
            "skills/visual-debug/scripts/video-motion-compare.sh" \
            "transitions/video-motion-result.txt" \
            "any motion signal true — 60fps frame-by-frame match (catches different easing / threshold / splash timing)" \
            "block" \
            "comprehensive" \
            "runtime-env"
fi

if [ "$HAS_HOVER" = "true" ]; then
  # Tier=standard: idle/hover end-state diff — single screenshot pair, no
  # video. Skipping at quick is fine because hover end-states usually appear
  # in the static section-compare sweep anyway.
  add_check "transition-compare" \
            "skills/visual-debug/scripts/transition-compare.sh" \
            "transitions/result.txt" \
            "signals.hasHover=true" \
            "block" \
            "standard" \
            "runtime-env"
  # Motion-arc check for hover. transition-compare above verifies idle/hover
  # END-STATE diffs; hover-state-compare verifies the easing/duration ARC
  # between them. Same bug class as video-motion-compare for scroll motion —
  # easeOutCubic vs easeOutQuint resolves to identical resting frames.
  # Tier=comprehensive: 60fps recording per hover target, MAX_HOVER_TARGETS=5.
  add_check "hover-state-compare" \
            "skills/visual-debug/scripts/hover-state-compare.sh" \
            "transitions/hover-state-result.txt" \
            "signals.hasHover=true — hover motion arc must match (catches different easing / duration on entry transition)" \
            "block" \
            "comprehensive" \
          "runtime-env"
  # Exhaustive impl-side hover sweep. The two rows above start from ref hover
  # targets; this one starts from impl hover candidates and fails extra motion
  # not present in the ref (e.g. invented header rotation or hover opacity
  # disappearance on static elements).
  add_check "hover-tree-diff" \
            "skills/visual-debug/scripts/hover-tree-diff.sh" \
            "hover-tree-diff.md" \
            "signals.hasHover=true — impl must not invent hover transforms/opacity on elements that are static in ref" \
            "block" \
            "comprehensive" \
            "runtime-env"
fi

# Unconditional advisory — the VLM "automated eyeball". Every other fidelity row
# above measures ONE axis (pixels, transition coverage, runtime oracles); none
# looks at the whole scroll-motion arc the way a human eye does. The
# loop-nvti-2 denominator-capture incident is the canonical miss: gates green
# while the eye caught dead scroll choreography instantly. Web research on
# clone/diff fidelity converges on a VLM-as-judge for exactly this gap, which is
# the one signal class our stack lacks. severity=warn keeps normal iteration
# advisory; strictWarnings promotes it to blocking for release/closeout. The
# check itself is fail-closed (browser/CLI failure => status=error, never a
# silent pass).
# Self-describing argsRecipe -> no SIGNATURES entry needed.
add_check "visual-fidelity-judge" \
          "skills/visual-debug/scripts/visual-fidelity-judge-check.sh" \
          "visual-fidelity-judge.json" \
          "Advisory VLM eyeball — impl must reproduce the ref's static section fidelity AND its scroll-driven motion arc (layout/text/color/animation axes); catches dead scroll choreography that per-axis gates miss" \
          "warn" \
          "standard" \
          "runtime-env" \
          "ENV:ROW_TIMEOUT_SEC=600 -- {session} {ref_url} {impl_url} {ref_dir}"
# ^ own row budget (codex P1): the motion pass waits up to ~330s on the VLM
# call; under the default 180s row budget the dispatcher would process-group-
# kill it BEFORE the artifact is written, turning a warn-only row into a hard
# dispatcher failure (advisory rows are non-blocking only when an artifact
# exists).

# Unconditional blocking check — source/static text checks can match while the
# rendered DOM exposes the wrong visible text or document order after runtime
# and scroll effects. Compare reference and implementation in a dedicated
# browser session. Bounded phase variance remains accepted only when the
# runtime artifact contains the comparator's recurrence proof.
# Self-describing argsRecipe keeps plan dispatch independent of SIGNATURES.
add_check "runtime-text-sequence" \
          "skills/visual-debug/scripts/runtime-text-sequence-check.sh" \
          "runtime-text-sequence.json" \
          "Rendered reference and implementation text must match in document order after runtime and scroll effects" \
          "block" \
          "standard" \
          "runtime-env" \
          "{session}-rts {ref_url} {impl_url} {ref_dir}"

# Click-state video compare. Catches the failure class where tabs / accordions /
# modals / hamburger menus open with the wrong motion arc — same end-state but
# different timing, easing, stacking-order swap. Static screenshot compares
# verify only the resting frames.
# Tier=comprehensive: same 60fps recording shape as hover-state-compare.
if [ "$HAS_CLICK_STATE" = "true" ]; then
  add_check "click-state-compare" \
            "skills/visual-debug/scripts/click-state-compare.sh" \
            "transitions/click-state-result.txt" \
            "signals.hasClickStateTransition=true — click-driven state transitions must match motion arc" \
            "block" \
            "comprehensive" \
            "runtime-env"
fi

if contains_pattern "$REGIONS_JSON" '"triggerType":\s*"'; then
  add_check "capture-artifact-inventory" \
            "skills/visual-debug/scripts/capture-artifact-inventory-check.sh" \
            "capture-artifact-inventory.json" \
            "Every ui-capture regions.json triggerType entry must enumerate the concrete ref clip/video artifacts it produced" \
            "block" \
            "quick"
fi

# Every site with a transition-spec.json should verify each entry is wired
# into the impl. Do not dispatch transition-compare just because the spec
# exists: transition-compare is a hover/end-state checker, while scroll,
# splash, IO, and click arcs are covered by transition-fires plus their
# dedicated temporal gates.
# Tier=quick: file-presence + grep over generated source — no browser.
# Deterministically-detected signature effects (per-character scroll scrub,
# etc.) declared in generation-plan.signatureEffects must be wired in impl.
# Dispatched when signatureEffects is a non-empty list OR scrollScrub declares a
# scale band (the #3 zoom): a scrollScrub-only ref has empty signatureEffects but
# still must wire the scroll-bound scale, so keying on signatureEffects alone left
# the scrub-scale contract silently unenforced. Mirror signature-effects-coverage-
# check.sh's scrub_has_scale logic so registration and validation agree.
if [ -f "$REF_DIR/generation-plan.json" ] && "$PYTHON_BIN" -c "import json,sys
d=json.load(open('$REF_DIR/generation-plan.json'))
se=d.get('signatureEffects')
ss=d.get('scrollScrub') if isinstance(d.get('scrollScrub'), dict) else {}
has_se=isinstance(se, list) and bool(se)
scrub_scale=bool(ss.get('required')) and any(
    (t.get('property') or '').startswith('scale')
    for s in (ss.get('sites') or []) if isinstance(s, dict)
    for t in (s.get('transforms') or []) if isinstance(t, dict))
sys.exit(0 if (has_se or scrub_scale) else 1)" 2>/dev/null; then
  add_check "signature-effects-coverage" \
            "skills/visual-debug/scripts/signature-effects-coverage-check.sh" \
            "signature-effects-coverage.json" \
            "Declared signatureEffects + scrollScrub scale must be wired in impl (scroll binding + per-character split + scroll-bound scale)" \
            "block" \
            "quick"
fi

# Mobile responsiveness density (ADVISORY/warn): a responsive ref needs a
# responsive impl. The browser mobile-viewport-parity gate only checks overflow,
# so a generic non-responsive rebuild (≈no media queries / fluid sizing) passes
# it while looking broken on mobile. Warn-only — surfaces the gap without
# blocking convergence. Dispatched only when the ref has detected breakpoints.
# Unconditional (review-1 MINOR 5: the plan always declares multiple desktop
# viewports, and a responsive ref without detected-breakpoints.json must not
# lose intermediate-width coverage — breakpoints are an optional extra input
# to the sweep-width derivation, not the dispatch condition).
# Tier=standard: one impl-only browser session sweeping intermediate widths
# (midpoints between plan viewports + breakpoints ±1px when available) and
# asserting ref-classified alignment invariants (centered / fixed-gutter)
# via DOM rects — no screenshots. Self-skips when no per-viewport ref
# enumeration data exists to classify from. Blocks only on two adjacent
# sweep-width violations or one enforced-width violation.
add_check "alignment-sweep" \
          "skills/visual-debug/scripts/alignment-sweep-check.sh" \
          "alignment-sweep.json" \
          "ref-centered / fixed-gutter sections must keep their invariant at intermediate viewport widths (impl-only DOM-rect sweep)" \
          "block" \
          "standard" \
          "runtime-env" \
          "{session}-align {impl_url} {ref_dir}"

if [ -f "$REF_DIR/detected-breakpoints.json" ]; then
  # Static source-signal density is diagnostic only: generated/dummy media
  # queries can satisfy this lexical check without proving that the rendered
  # layout responds. Runtime resize-behavior and desktop-band-fluidity remain
  # the blocking responsive contracts below.
  add_check "mobile-responsive-coverage" \
            "skills/visual-debug/scripts/mobile-responsive-coverage-check.sh" \
            "mobile-responsive-coverage.json" \
            "Diagnostic source coverage for responsive CSS/JS signals (runtime responsive checks decide blocking status)" \
            "warn" \
            "quick"
fi

# resize-behavior — live resize probe. A ref with >=2 breakpoints must have an
# impl whose layout actually RESPONDS to viewport resize (fluid / @media per key
# selector + a JS resize handler), not a one-shot matchMedia snapshot read at
# init. Static coverage can't see this; the probe resizes the served impl across
# the detected breakpoints. Tier=standard (one browser session). Self-describing
# argsRecipe -> no SIGNATURES entry ({impl_url} is a supported placeholder).
if [ -f "$REF_DIR/detected-breakpoints.json" ] && "$PYTHON_BIN" -c "import json,sys
try:
    d=json.load(open('$REF_DIR/detected-breakpoints.json'))
except Exception:
    sys.exit(1)
bps=d.get('breakpoints') if isinstance(d, dict) else d
n=len(bps) if isinstance(bps, list) else (
    (d.get('summary') or {}).get('count', 0) if isinstance(d, dict) else 0)
sys.exit(0 if isinstance(n, int) and n >= 2 else 1)" 2>/dev/null; then
  add_check "resize-behavior" \
            "skills/visual-debug/scripts/resize-behavior-probe.sh" \
            "resize-behavior.json" \
            "Ref responsive — impl layout must respond to resize (fluid/@media per key selector + a JS resize handler, not a one-shot matchMedia read)" \
            "block" \
            "standard" \
            "runtime-env" \
            "{impl_url} {ref_dir}"
  # Desktop-band fluidity (loop-nvti-0 briefing): capture-time widths baked
  # inline keep the clone frozen while the ref reflows across the desktop
  # band (widths above the wholesale-UI breakpoint). Compares ref-vs-impl
  # docHeight parity + horizontal overflow at several in-band widths and
  # flags the width-baked signature (ref reflows, impl constant). Same
  # responsive gating as resize-behavior. Self-describing argsRecipe ->
  # no SIGNATURES entry.
  add_check "desktop-band-fluidity" \
            "skills/visual-debug/scripts/desktop-band-fluidity-check.sh" \
            "desktop-band-fluidity.json" \
            "Ref responsive — impl must reflow like the ref across the desktop band (docHeight parity per width, no impl-only horizontal overflow, no width-baked freeze)" \
            "block" \
            "standard" \
            "runtime-env" \
            "{session} {ref_url} {impl_url} {ref_dir}"
fi

# Dynamic-behavior parity (loop-nvti-0 briefing): dynamic content is part of
# transition fidelity — freezing it for static AE/dSSIM is a measurement
# technique, not a scope reduction. On fresh UNPINNED sessions, verify each
# declared dynamic region still CHANGES over time in the impl with a
# compatible mechanism/period (content equality not required). Self-skips
# to pass when the page declares no dynamic regions, so static pages are
# unaffected. Self-describing argsRecipe -> no SIGNATURES entry.
add_check "dynamic-behavior-parity" \
          "skills/visual-debug/scripts/dynamic-behavior-parity.sh" \
          "dynamic-behavior-parity.json" \
          "Declared dynamic regions must stay dynamic in the impl: unpinned T0/T0+delta fingerprints show ref-and-impl both changing with compatible period — a frozen-in-impl region is a transition-fidelity defect" \
          "block" \
          "standard" \
          "runtime-env" \
          "{session} {ref_url} {impl_url} {ref_dir}"

# Conditional: dynamic:true timer/carousel spec entries. These regions are
# dynamic-masked out of pixel comparison (frame is timer-phase-dependent),
# which previously left them with NO compensating verification — loop-9's
# carousel timer ran and swapped content instantly while the spec-declared
# card motion never happened, and every gate passed. The motion proof
# samples the live impl DOM per entry and asserts phase-free properties
# (state count, cadence, channel coverage, bundle item sequence) from spec/
# bundle truth only. Tier=standard: one browser session, ~5-8s per entry.
HAS_DYNAMIC_TIMER="false"
if [ -f "$TRANSITION_SPEC" ]; then
  _mrm_entries="$(PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m ui_clone.gates.masked_region_motion plan "$REF_DIR" 2>/dev/null \
    | "$PYTHON_BIN" -c 'import json,sys;print(len(json.load(sys.stdin)))' 2>/dev/null \
    || echo 0)"
  if [ "${_mrm_entries:-0}" -gt 0 ] 2>/dev/null; then
    HAS_DYNAMIC_TIMER="true"
  fi
fi
if [ "$HAS_DYNAMIC_TIMER" = "true" ]; then
  add_check "masked-region-motion" \
            "skills/visual-debug/scripts/masked-region-motion-proof-check.sh" \
            "masked-region-motion.json" \
            "dynamic:true timer/carousel entries must prove live motion (state count, cadence, declared-channel coverage, item sequence) — masked regions get no pixel verdict, so this is their only verification" \
            "block" \
            "standard" \
            "runtime-env" \
            "{session}-mrm {impl_url} {ref_dir}"
fi

# Conditional: any dynamic:true masked selector. The motion proof checks MOTION
# only; the dynamic mask (visibility:hidden) also hides the region's STATIC
# style/geometry from section-compare, video-motion, and the motion proof. A
# static style defect under a mask (loop-11 eatReal "Eat Real" h2 lost
# text-align:center) thus passed every gate. This check probes the live impl DOM
# (un-masked) and compares phase-free computed styles to the extraction-time ref
# ground truth (dom-scaffold.json). Pure artifact-vs-DOM, no pixel capture.
HAS_DYNAMIC_MASK="false"
if [ -f "$TRANSITION_SPEC" ]; then
  _mrs_sels="$(PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m ui_clone.gates.masked_region_static plan "$REF_DIR" 2>/dev/null \
    | "$PYTHON_BIN" -c 'import json,sys;p=json.load(sys.stdin);print(len(p.get("selectors") or [])+len(p.get("unresolvableSelectors") or []))' 2>/dev/null \
    || echo 0)"
  if [ "${_mrs_sels:-0}" -gt 0 ] 2>/dev/null; then
    HAS_DYNAMIC_MASK="true"
  fi
fi
if [ "$HAS_DYNAMIC_MASK" = "true" ]; then
  add_check "masked-region-static" \
            "skills/visual-debug/scripts/masked-region-static-check.sh" \
            "masked-region-static.json" \
            "dynamic:true masked selectors must keep the ref's static computed styles (text-align, justify-content, align-items, font-family/weight, color) — the mask absorbs motion only, so a static style defect under a mask must still fail" \
            "block" \
            "standard" \
            "runtime-env" \
            "{session}-mrs {impl_url} {ref_dir} {ref_url}"
fi

# Conditional: bundle declares an active-state width reveal (nav active-section
# label expansion). The hover-fallback gate only covers hover-triggered reveals;
# an active-state (scroll) reveal had no compensating verification (loop-11: the
# newly-active nav button's label stayed width:0 on scroll). This drives the
# active state on the live impl and asserts the bundle-declared reveal fires.
HAS_ACTIVE_REVEAL="false"
if [ -f "$REF_DIR/bundle-extraction.json" ]; then
  _sr_sels="$(PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m ui_clone.gates.state_reveal plan "$REF_DIR" 2>/dev/null \
    | "$PYTHON_BIN" -c 'import json,sys;print(len(json.load(sys.stdin).get("selectors") or []))' 2>/dev/null \
    || echo 0)"
  if [ "${_sr_sels:-0}" -gt 0 ] 2>/dev/null; then
    HAS_ACTIVE_REVEAL="true"
  fi
fi
if [ "$HAS_ACTIVE_REVEAL" = "true" ]; then
  add_check "state-reveal" \
            "skills/visual-debug/scripts/state-reveal-proof-check.sh" \
            "state-reveal.json" \
            "bundle-declared active-state reveals (nav active-section label width 0->auto) must fire on the live impl: driving the state change (scroll) must expand the active element's label to show its text — a label that stays collapsed when active is the loop-11 defect" \
            "block" \
            "standard" \
            "runtime-env" \
            "{session}-sr {impl_url} {ref_dir}"
fi

if [ -f "$TRANSITION_SPEC" ]; then
  add_check "transition-spec-coverage" \
            "skills/visual-debug/scripts/transition-spec-coverage.sh" \
            "transition-spec-coverage.json" \
            "Every transition-spec entry must have at least one matching impl artifact" \
            "block" \
            "quick"
  # Closes the silent-killer "selector matched but no motion declared" gap.
  # transition-spec-coverage above answers "does the impl mention this entry?";
  # this row answers "and does it actually animate it?". The two are split so
  # the presence check stays at quick tier (cheap pre-generate sanity), while
  # the stronger declaration check fires at standard tier and reports a fail
  # severity on impls that render the selector with zero motion plumbing.
  add_check "spec-implementation-coverage" \
            "skills/visual-debug/scripts/spec-implementation-coverage.sh" \
            "spec-implementation-coverage.json" \
            "Every matched spec entry must have a motion declaration in its impl file (catches generated components that render the selector without animating it)" \
            "block" \
            "standard"
  # RUNTIME source-of-truth for motion fidelity. The two coverage rows above
  # are STATIC string-matching (selector/class present in source = "covered"),
  # decoupled from whether the animation actually runs — so an unimplemented
  # scroll-reveal passes on the class name while a working FAQ fails because its
  # spec id is not a substring. This row drives each spec entry's trigger in a
  # real browser and asserts a MEASURED runtime delta on the target
  # (opacity/transform/rect/scroll-progress/currentTime/canvas-pixels); it
  # cannot be satisfied by a class name or a transition- token. Kept ALONGSIDE
  # (not replacing) the cheap static pre-filters above. Tier=standard: one
  # browser session, before/after measurement per entry. Already gated on
  # transition-spec.json having entries (the enclosing `if`).
  add_check "transition-fires" \
            "skills/visual-debug/scripts/transition-fires-check.sh" \
            "transition-fires.json" \
            "Every transition-spec entry must produce a MEASURED runtime delta when its trigger is driven in the impl — class-name presence is not motion" \
            "block" \
            "standard" \
            "runtime-env"
fi

# Enforce transition-spec.json reflects animation-runtime-dump.json signal classes.
# Closes the gap where transition-spec-rules.md Rule 7 was advisory — an agent
# could author a spec with zero scroll entries while the live page is running
# 30 ScrollTrigger animations. With this row wired, that mismatch fails the
# post-implement gate.
#
# Motion-site review: drop the `&& -f TRANSITION_SPEC`
# guard. A stale transition-spec can under-report runtime ScrollTrigger
# entries — both files may exist, but more
# critically the gap is REAL when runtime-dump exists but transition-spec
# is empty/missing. The check script handles both branches; gating its
# REGISTRATION on transition-spec presence let agents silence the gap by
# deleting transition-spec.json. Now: presence of animation-runtime-dump
# alone forces the row, and the script itself reports spec absent/empty.
# Tier=quick: pure JSON-vs-JSON comparison — instant.
ANIM_DUMP="$REF_DIR/animation-runtime-dump.json"
if [ -f "$ANIM_DUMP" ]; then
  add_check "runtime-spec-coverage" \
            "skills/visual-debug/scripts/runtime-spec-coverage.sh" \
            "runtime-spec-coverage.json" \
            "animation-runtime-dump.json signal classes (ScrollTrigger / IX2 timelines) must be reflected in transition-spec.json" \
            "block" \
            "quick"
fi

# Canonical boundary and font-parity gates require these artifacts for every
# implementation, regardless of detected signals. Both checks are standard
# tier because they use a live browser; the dispatcher supplies their existing
# script signatures (including REF_DIR for breakpoint-collision).
add_check "breakpoint-collision" \
          "skills/visual-debug/scripts/breakpoint-collision-check.sh" \
          "responsive/boundary-collisions.json" \
          "canonical boundary gate requires a live breakpoint collision sweep" \
          "block" \
          "standard" \
          "runtime-env"

add_check "font-parity" \
          "skills/visual-debug/scripts/font-parity-check.sh" \
          "font-parity.json" \
          "canonical font-parity gate requires measured ref-vs-impl font evidence" \
          "block" \
          "standard" \
          "runtime-env"

# font-binaries-present — root-relative @font-face binaries must actually be
# delivered to impl/public, not just referenced in mirrored CSS (navercorp
# shipped 0 files in impl/public/font while asset-transfer reported 44/44).
# Reads transfer-fonts.sh's font-transfer.json + verifies files on disk;
# self-skips (pass) when the transfer report is absent. Unconditional because
# transfer-fonts.sh runs after this plan is minted, so the report may not yet
# exist at mint time — the check resolves it at run time. Self-describing
# argsRecipe -> no SIGNATURES entry.
add_check "font-binaries-present" \
          "skills/visual-debug/scripts/font-binaries-present-check.sh" \
          "font-binaries-present.json" \
          "root-relative font binaries referenced by ref CSS must be present under impl/public (not just referenced) — else custom fonts 404 to system fallbacks" \
          "block" \
          "quick" \
          "" \
          "{ref_dir}"

# Content cardinality — repeated-group count parity against ref ground
# truth (omx postmortem: 9 hardcoded storyCards shipped where the ref
# rendered the full list; AE/section masks can hide a short repeated list
# and nothing counted rendered members). Signatures from dom-scaffold.json
# sibling groups >=3; counts from rendered runtime DOM with a visible-box
# filter. Tier=standard (one impl browser load), severity=block.
if [ -f "$REF_DIR/dom-scaffold.json" ]; then
  add_check "content-cardinality" \
            "skills/visual-debug/scripts/content-cardinality-check.sh" \
            "content-cardinality.json" \
            "dom-scaffold.json present — every ref repeated group (>=3 siblings) must render at full count in the impl runtime DOM" \
            "block" \
            "standard"
fi

# Typography parity — per-element font-weight / letter-spacing + global
# body-rule diff. font-parity only compares the primary font FAMILY; this
# row catches the silent class where the family matches but the impl
# dropped a global tracking rule (body letter-spacing -0.5px) or generated
# headings at 400/600 where the ref uses 800/900 (omx navercorp evidence).
# Universal: every page renders text. Tier=standard (one ref+impl browser
# pair, same cost class as font-parity); severity=block — a dropped global
# rule or wrong weight is a generation defect, not a style choice.
add_check "typography-parity" \
          "skills/visual-debug/scripts/typography-parity-check.sh" \
          "typography-parity.json" \
          "universal — font-weight/letter-spacing/body-rule parity (family parity alone misses tracking/weight divergence)" \
          "block" \
          "standard"

# Image fidelity — closes the "impl dropped or swapped a ref image" failure
# class statically. AE/SSIM catches the pixel diff but buries the cause; this
# row names the specific URL that's missing from impl source. Severity warn
# (not block) because image swaps are sometimes intentional (placeholder for
# DRM/auth-gated CDN, asset-substitutions.json declared swap). The agent
# inspects the artifact and decides; severity=block would force every
# legitimate substitution to bypass the gate.
# Tier=standard: grep over impl source — same cost class as
# spec-implementation-coverage.
VISIBLE_IMAGES="$REF_DIR/visible-images.json"
if [ -f "$VISIBLE_IMAGES" ]; then
  # Severity upgraded warn → block. Observed failure mode: agent extracts
  # visible-images.json (cataloged) but skips actual download — impl renders
  # gradient placeholders, AE explodes 1M+. warn-severity let this pass; block
  # forces the agent to address before declaring done.
  add_check "image-fidelity" \
            "skills/visual-debug/scripts/image-fidelity-check.sh" \
            "image-fidelity.json" \
            "Every visible-images.json entry must be referenced in impl source; declared dimensions must be within tolerance" \
            "block" \
            "quick"
  # Companion check at a STRONGER position: the impl source can reference a URL,
  # but if the actual file is missing from impl/public/, Next.js serves a 404
  # for it. asset-transfer-check looks for the real files, not just code refs.
  add_check "asset-transfer" \
            "skills/visual-debug/scripts/asset-transfer-check.sh" \
            "asset-transfer.json" \
            "Non-substituted visible-images.json entries must exist as real files under impl/public/" \
            "block" \
            "quick"
  # Third asset check at a YET STRONGER position: the file may exist on disk
  # AND a URL may resolve, but is the impl source actually referencing it?
  # The c9b638d benchmark shipped 45 downloaded images of which only 2 were
  # referenced in src — orphan ratio ~95%, AE stayed catastrophic because the
  # impl rendered placeholders for 43 expected images. This check fails when
  # the referenced/downloaded ratio drops below 0.6.
  add_check "asset-utilization" \
            "skills/visual-debug/scripts/asset-utilization-check.sh" \
            "asset-utilization.json" \
            "At least 60% of non-substituted visible-images.json entries must be referenced in impl/src/ source" \
            "block" \
            "quick"
  if [ -f "$REF_DIR/section-map.json" ] && [ -f "$REF_DIR/component-map.json" ]; then
    add_check "asset-placement" \
              "skills/visual-debug/scripts/asset-placement-check.sh" \
              "asset-placement.json" \
              "Section-mappable visible assets must be referenced by the component mapped to that original section" \
              "block" \
              "quick"
  fi
  add_check "runtime-image-validity" \
            "skills/visual-debug/scripts/runtime-image-validity-check.sh" \
            "runtime-image-validity.json" \
            "Runtime <img> elements must load with nonzero naturalWidth and not resolve to HTML fallback responses" \
            "block" \
            "standard" \
          "runtime-env"
  add_check "remote-asset-ref" \
            "skills/visual-debug/scripts/remote-asset-ref-check.sh" \
            "remote-asset-ref.json" \
            "Impl source must not hot-link the reference CDN — use locally-downloaded /public/ assets" \
            "block" \
            "quick"
fi

# Common cheat pattern A2/A3 — entry coherence. Vite+React must render from
# src/main.{jsx,tsx}; Next App from app/page.tsx. Coexisting entry
# points (src/main.* + app/page.* both present), mixed Vite+Next
# dependencies, or raw ref markup pasted into index.html all indicate
# the agent is gaming gates via scaffold residue. tier=quick (pure
# filesystem + package.json read).
add_check "entry-coherence" \
          "skills/visual-debug/scripts/entry-coherence-check.sh" \
          "entry-coherence.json" \
          "Impl must have ONE coherent entry path matching declared stack; no coexisting Vite/Next entries; index.html must be a mount file, not pasted ref markup" \
          "block" \
          "quick"

# Common cheat pattern A3 — scaffold residue (orphan components). PascalCase
# components exported from impl/src/ (excluding entry files main.*/
# App.*/index.{tsx,jsx}) must appear as JSX usage somewhere. ≥3 orphans
# OR ≥40% orphan ratio = the agent shipped scaffold files without
# actually wiring them into the render tree. tier=quick (regex scan).
add_check "scaffold-residue" \
          "skills/visual-debug/scripts/scaffold-residue-check.sh" \
          "scaffold-residue.json" \
          "PascalCase components defined in impl/src/ must be referenced as JSX (<Name>) or createElement(Name) somewhere; ≥3 orphans = scaffold residue" \
          "block" \
          "quick"

add_check "required-media-coverage" \
          "skills/visual-debug/scripts/required-media-coverage-check.sh" \
          "required-media-coverage.json" \
          "Every required video/Lottie/SVG asset (from html/*.json + bundles/*.js + CSS url(...)) must be downloaded to impl/public AND referenced in impl source; Lottie URLs require a Lottie runtime package" \
          "block" \
          "quick"

# Common cheat pattern A5 — CSS mirror. Reject @import to ref CSS hosts/
# filenames, byte-identical copies of <ref>/bundles/*.css in impl CSS,
# and impl CSS files with >=70% difflib quick_ratio similarity to a
# ref CSS bundle. Per-section snippets are allowed under
# impl/src/styles/from-ref/. tier=quick (filesystem + difflib).
add_check "css-mirror" \
          "skills/visual-debug/scripts/css-mirror-check.sh" \
          "css-mirror.json" \
          "Impl CSS must not @import the reference CSS host/filename, byte-copy any ref CSS bundle, or be >=70% similar to one (snippets allowed under src/styles/from-ref/)" \
          "block" \
          "quick"

add_check "hidden-children" \
          "skills/visual-debug/scripts/hidden-children-check.sh" \
          "hidden-children.json" \
          "Major sections (area>20000) must not have ALL non-trivial direct children permanently hidden after animations finish" \
          "block" \
          "standard" \
          "runtime-env"
# Common cheat pattern A4 — positive-parity runtime gate. All other gates
# are NEGATIVE assertions (don't cheat with X). This one is POSITIVE:
# the impl runtime DOM must match the ref runtime DOM along four
# axes — node count within ±30%, >= max(10, sectionCount*2) visible
# text nodes, no single image/video/background element covering >90%
# of viewport, and >=1 Lottie container mounted when ref has Lottie
# evidence. tier=standard (one browser session per side, scroll
# walk, eval).
add_check "runtime-dom-parity" \
          "skills/visual-debug/scripts/runtime-dom-parity-check.sh" \
          "runtime-dom-parity.json" \
          "Impl runtime DOM must match ref along node count (±30%), text-node count, no-single-dominant-element, and Lottie-container parity" \
          "block" \
          "standard" \
          "runtime-env"
add_check "svg-dom-parity" \
          "skills/visual-debug/scripts/svg-dom-parity-check.sh" \
          "svg-dom-parity.json" \
          "Impl runtime SVG inventory must match ref (page total >=50%, inline <svg> must have geometry children, no per-section SVG dropout)" \
          "block" \
          "standard" \
          "runtime-env"

add_check "invalidation" \
          "skills/visual-debug/scripts/invalidation-check.sh" \
          "invalidation.json" \
          "Ref must not carry an .invalidated stamp; remove the stamp only after fixing the underlying cheat that triggered it" \
          "block" \
          "quick"

# Common failure pattern — monolithic-impl detection. When the agent packs
# the entire UI into a single App.jsx/App.tsx/page.tsx without any
# components, scaffold-residue passes (0 orphans because 0 components
# defined). The monolithic shape breaks per-section iteration: every
# visual-debug fix touches the same file. Catches that pattern by
# requiring expected-component-count when entry file is large.
# tier=quick (filesystem only).
add_check "monolithic-impl" \
          "skills/visual-debug/scripts/monolithic-impl-check.sh" \
          "monolithic-impl.json" \
          "Impl must componentize: entry file >= 8KB AND component count < max(3, sections/3) = fail" \
          "block" \
          "quick"

# Common failure pattern — motion coverage. When ref's bundle-map/
# transition-spec/external-sdks evidence motion lib usage but impl
# source has zero motion imports, hooks, IntersectionObserver, or
# GSAP calls — fail. bundle-impl-coverage only checks package.json;
# this checks ACTUAL motion code presence. tier=quick (grep over
# impl source + JSON reads).
add_check "scroll-engine-parity" \
          "skills/visual-debug/scripts/scroll-engine-parity-check.sh" \
          "scroll-engine-parity.json" \
          "Impl must implement an equivalent scroll-engine class to ref (gsap-scrolltrigger / lenis-smooth-scroll / scroll-pin / scroll-scrub / framer-motion / native-scroll-timeline). Bare IntersectionObserver + CSS transitions cannot replicate progress-bound scrub or sticky-pin." \
          "block" \
          "quick"

add_check "motion-coverage" \
          "skills/visual-debug/scripts/motion-coverage-check.sh" \
          "motion-coverage.json" \
          "Impl source must show motion implementation matching ref bundle/spec evidence (imports, hooks, IntersectionObserver, GSAP calls)" \
          "block" \
          "quick" \
          "runtime-env"

# Signal 1 — scaffold-warn placeholders. scaffold-to-jsx.sh emits
# `<section data-scaffold-warn="subtree-not-found-for-<name>" />`
# when it cannot resolve a section's subtree in dom-scaffold.json.
# Those sentinels were meant for Phase-5b visual-judge to flag, but
# in practice they ship to impl/src/ untouched and render empty
# sections — section-compare then blames CSS while the real cause
# is missing subtree resolution. Static scan of impl source rejects
# any file containing the placeholder.
add_check "scaffold-warn" \
          "skills/visual-debug/scripts/scaffold-warn-check.sh" \
          "scaffold-warn.json" \
          "Impl source must not carry scaffold-to-jsx subtree-not-found placeholders — re-run scaffold extraction or author the section by hand" \
          "block" \
          "quick"

# bundle-impl-coverage — if bundle-map.json detected runtime libs (Lenis,
# GSAP, Framer Motion), require impl/package.json to actually depend on
# them. The c9b638d benchmark exposed this dead-wire pattern: bundle-map
# correctly identified gsap-like + motion-like + lenis-on-<html>, the
# Next.js scaffold shipped with only next/react/react-dom, and the impl
# had zero runtime motion against rich ref motion.
BUNDLE_MAP="$REF_DIR/bundle-map.json"
if [ -f "$BUNDLE_MAP" ]; then
  add_check "bundle-impl-coverage" \
            "skills/visual-debug/scripts/bundle-impl-coverage-check.sh" \
            "bundle-impl-coverage.json" \
            "Every library signature detected in bundle-map.json must have a matching install in impl/package.json (dependencies or devDependencies)" \
            "block" \
            "quick"
fi

# library-usage — the import-level counterpart to bundle-impl-coverage. A
# package.json install proves nothing runs: on the ebay run framer-motion was
# declared but never imported, and the entire useScroll/useTransform surface was
# faked with a requestAnimationFrame shim + scale(). This row fails when a ref
# animation library (bundle-map.json / external-sdks.json) has ZERO import/require
# hits in impl source. Self-describing argsRecipe -> no SIGNATURES entry needed.
if [ -f "$BUNDLE_MAP" ] || [ -f "$REF_DIR/external-sdks.json" ]; then
  add_check "library-usage" \
            "skills/visual-debug/scripts/library-usage-check.sh" \
            "library-usage.json" \
            "Every ref-detected animation library (bundle-map.json / external-sdks.json) must be actually imported in impl source — package.json presence alone is the rAF-shim loophole" \
            "block" \
            "quick" \
            "" \
            "{ref_dir}"
fi

# tree-diff — element pairing via elementFromPoint, per-element style+layout
# diff. 17-iteration measurement (2026-05-22): hero-area elements
# (BUTTON.hero-video, VIDEO, SPAN.hero-video__label, H1) were unpaired in
# EVERY iteration because LLMs flatten ref's deeply-nested hero composite
# into a clean React tree — the impl's BUTTON center now lands on the
# parent SECTION instead of resolving to BUTTON via elementFromPoint.
# Forcing block severity made section-compare-PASS impls fail forever on
# a structural pattern the LLM cannot un-abstract. Downgraded to warn:
# results stay informational (still useful when AE diff is mysterious),
# but the dispatcher doesn't count them in the FAIL tally. The actual
# "hero composite must exist" signal moved to hero-composite-check.sh,
# which spot-checks the 4-element pattern (video + button + h1/h2 +
# label) regardless of how impl wraps them.
add_check "tree-diff" \
          "skills/visual-debug/scripts/tree-diff.sh" \
          "tree-diff-status.json" \
          "Element-pairing diff (advisory) — informational only; hero composite enforced by hero-composite-check" \
          "warn" \
          "standard" \
          "runtime-env"

# scroll-coverage — revives the previously-orphan batch-scroll + batch-compare
# pair as a dispatchable check. Catches the "section-compare collapsed to N
# sections" coverage gap (d19e28d benchmark only matched 2 of 16 sections)
# by sweeping section-aligned anchors (plus sticky/scroll-transition phase
# probes) on both sides; legacy every-10% scroll capture remains a fallback.
REGIONS_JSON="$REF_DIR/regions.json"
SECTION_MAP_JSON="$REF_DIR/section-map.json"
if [ -f "$REGIONS_JSON" ] || [ -f "$SECTION_MAP_JSON" ]; then
  add_check "scroll-coverage" \
            "skills/visual-debug/scripts/scroll-coverage-check.sh" \
            "scroll-coverage.json" \
            "≥70% of section-aligned scroll/sticky probes must match within AE/Mpx threshold (fallback: percent scroll probes)" \
            "warn" \
            "standard" \
            "runtime-env"
fi

# keyframes-diff — when extracted CSS declares @keyframes rules, verify
# the impl carries the same set with matching steps. Catches missing
# entrance animations and wrong timing curves baked into keyframes —
# scenarios that transition-compare can miss when the impl never reaches
# the entrance state during its visit window.
EXTRACTED="$REF_DIR/extracted.json"
HAS_KEYFRAMES="false"
if [ -d "$REF_DIR/css" ] && grep -lq "@keyframes" "$REF_DIR/css/"*.css 2>/dev/null; then
  HAS_KEYFRAMES="true"
elif [ -f "$EXTRACTED" ] && grep -q "@keyframes" "$EXTRACTED" 2>/dev/null; then
  HAS_KEYFRAMES="true"
fi
if [ "$HAS_KEYFRAMES" = "true" ]; then
  add_check "keyframes-diff" \
            "skills/visual-debug/scripts/keyframes-diff.sh" \
            "transitions/keyframes-diff-result.txt" \
            "@keyframes declarations in ref must match impl (catches missing entrance animations + wrong steps)" \
            "warn" \
            "standard" \
            "runtime-env"
fi

# scroll-anim-temporal-diff — when transition-spec declares scroll-driven
# motion on repeating elements (marquees, parallax tile grids, number
# stacks). Same-amplitude same-phase-step = single traveling wave (smooth
# interlock); different phase steps = per-row phase chaos (irregular
# gaps). Both pass AE on any frozen frame but feel completely different
# in motion.
TRANSITION_SPEC="$REF_DIR/transition-spec.json"
HAS_REPEATING="false"
if [ -f "$TRANSITION_SPEC" ] && command -v jq >/dev/null 2>&1; then
  if jq -e '[.transitions[]? | select(.pattern == "repeating" or .repeating == true)] | length > 0' "$TRANSITION_SPEC" >/dev/null 2>&1; then
    HAS_REPEATING="true"
  fi
fi
if [ "$HAS_REPEATING" = "true" ]; then
  add_check "scroll-anim-temporal" \
            "skills/visual-debug/scripts/scroll-anim-temporal-diff.sh" \
            "transitions/temporal-result.txt" \
            "Scroll-driven repeating animations must match phase/frequency family (single-wave vs per-row)" \
            "warn" \
            "comprehensive"
fi

OUT="$REF_DIR/verification-plan.json"
# Build the fresh plan to a temp first. In --amend mode we splice only the
# plan-derived rows from this fresh build into the frozen base; otherwise the
# fresh plan IS the output.
FRESH_PLAN="$(mktemp "${TMPDIR:-/tmp}/verification-plan-fresh.XXXXXX")" || {
  echo "ERROR: cannot create temporary plan file" >&2
  exit 2
}
{
  printf '{\n'
  printf '  "schemaVersion": 1,\n'
  printf '  "generatedAt": "%s",\n' "$NOW"
  printf '  "component": "%s",\n' "$COMPONENT"
  printf '  "tier": "%s",\n' "$TIER"
  printf '  "strictWarnings": %s,\n' "$STRICT_WARNINGS_JSON"
  printf '  "signals": {\n'
  printf '    "hasScrollScrub": %s,\n' "$HAS_SCROLL_SCRUB"
  printf '    "hasScrollStateMachine": %s,\n' "$HAS_SCROLL_STATE_MACHINE"
  printf '    "hasIOReveal": %s,\n' "$HAS_IO_REVEAL"
  printf '    "hasHover": %s,\n' "$HAS_HOVER"
  printf '    "hasSplash": %s,\n' "$HAS_SPLASH"
  printf '    "hasCanvas": %s,\n' "$HAS_CANVAS"
  printf '    "hasCustomScroll": %s,\n' "$HAS_CUSTOM_SCROLL"
  printf '    "hasCommercialFont": %s,\n' "$HAS_COMMERCIAL_FONT"
  printf '    "hasClickStateTransition": %s,\n' "$HAS_CLICK_STATE"
  printf '    "hasLottie": %s,\n' "$HAS_LOTTIE"
  printf '    "hasSwiper": %s\n' "$HAS_SWIPER"
  printf '  },\n'
  printf '  "requiredChecks": [\n'
  cat "$CHECKS_FILE"
  printf '\n  ],\n'
  printf '  "deferredChecks": [\n'
  cat "$DEFERRED_FILE"
  printf '\n  ],\n'
  printf '  "viewports": [\n'
  printf '    { "w": 375,  "h": 812,  "label": "mobile" },\n'
  printf '    { "w": 1280, "h": 800,  "label": "laptop" },\n'
  printf '    { "w": 1440, "h": 900,  "label": "capture" },\n'
  printf '    { "w": 1600, "h": 900,  "label": "desktop-mid" },\n'
  printf '    { "w": 1920, "h": 1080, "label": "desktop-large" }\n'
  printf '  ]\n'
  printf '}\n'
} > "$FRESH_PLAN"

if [ "$AMEND" = "1" ] && [ -n "$AMEND_BASE" ]; then
  # Append-only merge: keep the base plan authoritative for every existing row;
  # add only PLAN_DERIVED_CHECK_IDS rows that the fresh build now emits (because
  # generation-plan.json exists) and the base lacks. Preserves tier/deferred
  # placement from the fresh build and the closed list for everything else.
  "$PYTHON_BIN" "$VERIFICATION_PLAN_HELPER" amend-plan \
    "$AMEND_BASE" "$FRESH_PLAN" "$OUT" "$PLAN_DERIVED_CHECK_IDS"
  rm -f "$FRESH_PLAN" "$AMEND_BASE"
else
  mv "$FRESH_PLAN" "$OUT"
fi

echo "Wrote $OUT"
echo "Tier: $TIER"
echo "Signals: scrollScrub=$HAS_SCROLL_SCRUB scrollStateMachine=$HAS_SCROLL_STATE_MACHINE ioReveal=$HAS_IO_REVEAL hover=$HAS_HOVER splash=$HAS_SPLASH canvas=$HAS_CANVAS customScroll=$HAS_CUSTOM_SCROLL commercialFont=$HAS_COMMERCIAL_FONT clickState=$HAS_CLICK_STATE lottie=$HAS_LOTTIE swiper=$HAS_SWIPER"

# Validate JSON. Use the already-resolved Python interpreter instead of
# spawning jq here: repeated CI invocations exposed a slow jq cold-start/tail
# hang after the plan had already been written, causing otherwise-valid plans to
# exceed the 30s smoke-test timeout. Python is already required above for the
# signal probes, so this keeps validation deterministic without adding a second
# tool startup path.
if ! "$PYTHON_BIN" -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$OUT" 2>/dev/null; then
  echo "ERROR: produced file is not valid JSON" >&2
  exit 2
fi

exit 0
