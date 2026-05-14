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

TIER="${UI_CLONE_VERIFY_TIER:-comprehensive}"
REF_DIR=""
for arg in "$@"; do
  case "$arg" in
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

# Read a JSON path safely; return empty string if file or path missing.
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

BUNDLE_MAP="$REF_DIR/bundle-map.json"
INTERACTIONS="$REF_DIR/interactions-detected.json"
EXTERNAL_SDKS="$REF_DIR/external-sdks.json"
SCROLL_ENGINE="$REF_DIR/scroll-engine.json"
TRANSITION_SPEC="$REF_DIR/transition-spec.json"
CANVAS_DETECT="$REF_DIR/canvas-webgl-detection.json"
PAID_FEATURES="$REF_DIR/paid-features.json"

# ── Signal extraction ──
# Each signal is OR of multiple proxy evidence. Conservatively true: if any
# proxy hits, the signal is on. This makes the plan err on the side of
# *running* a check — a false-positive runs an extra check (cheap), a
# false-negative misses a bug class (expensive).

# hasScrollScrub: scroll-driven animation present in some form
HAS_SCROLL_SCRUB="false"
if contains_pattern "$EXTERNAL_SDKS" '"(useScroll|scrollYProgress|ScrollTrigger|scrubbed|scrub)"' \
   || contains_pattern "$SCROLL_ENGINE" '"library":\s*"(Lenis|Locomotive|ScrollSmoother)"' \
   || contains_pattern "$TRANSITION_SPEC" '"trigger":\s*"scroll' \
   || contains_pattern "$INTERACTIONS" '"engine":\s*"scroll"' \
   || contains_pattern "$BUNDLE_MAP" '"(framer-motion|motion-one|gsap-scrolltrigger)"'; then
  HAS_SCROLL_SCRUB="true"
fi

# hasIOReveal: IntersectionObserver-driven entry animations
HAS_IO_REVEAL="false"
if contains_pattern "$TRANSITION_SPEC" '"trigger":\s*"(intersection|inview|onView)"' \
   || contains_pattern "$INTERACTIONS" '"trigger":\s*"intersection"' \
   || contains_pattern "$BUNDLE_MAP" 'IntersectionObserver'; then
  HAS_IO_REVEAL="true"
fi

# hasHover — match any trigger value containing the word "hover" (covers
# variants: "hover", "css-hover", "css :hover", "scale-on-hover-target",
# "whileHover", etc.). interaction-detection.md / transition-spec-rules.md
# do not pin a single canonical string, so the regex must be lenient.
HAS_HOVER="false"
if contains_pattern "$INTERACTIONS" '"trigger":\s*"[^"]*[Hh]over[^"]*"' \
   || contains_pattern "$TRANSITION_SPEC" '"trigger":\s*"[^"]*[Hh]over[^"]*"' \
   || contains_pattern "$INTERACTIONS" '"whileHover"\s*:' \
   || contains_pattern "$INTERACTIONS" '"hoverDelta"\s*:'; then
  HAS_HOVER="true"
fi

# hasSplash — interactions-detected.json carries `hasPreloader` per the
# Preloader/Splash protocol in bundle-analysis.md. Fall back to the
# splash-extraction artifacts in case the agent set hasPreloader=false but
# splash-extraction.md still produced output.
HAS_SPLASH="false"
if contains_pattern "$INTERACTIONS" '"hasPreloader":\s*true' \
   || contains_pattern "$INTERACTIONS" '"hasSplash":\s*true' \
   || [ -f "$REF_DIR/dom-state-diff.json" ]; then
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
if contains_pattern "$REGIONS_JSON" '"triggerType":\s*"click-' \
   || contains_pattern "$INTERACTIONS" '"trigger":\s*"click"' \
   || contains_pattern "$INTERACTIONS" '"type":\s*"click-' \
   || contains_pattern "$TRANSITION_SPEC" '"trigger":\s*"click"'; then
  HAS_CLICK_STATE="true"
fi

# ── Required checks (dispatch table) ──
# Built incrementally as JSON array body.

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

CHECKS=""
# add_check id script produces reason severity [min_tier]
#   min_tier: quick | standard | comprehensive (default: standard)
#             — see tier table at top of file. A check is dispatched only when
#             active tier level ≥ min_tier level.
add_check() {
  local id="$1" script="$2" produces="$3" reason="$4" severity="$5"
  local min_tier="${6:-standard}"
  local check_level
  check_level=$(tier_level "$min_tier")
  if [ "$check_level" -eq 0 ]; then
    echo "ERROR: add_check '$id' got invalid min_tier '$min_tier'" >&2
    exit 2
  fi
  if [ "$check_level" -gt "$CURRENT_TIER_LEVEL" ]; then
    return 0
  fi
  local sep=""
  [ -n "$CHECKS" ] && sep=","
  CHECKS="${CHECKS}${sep}
    {
      \"id\": \"$id\",
      \"script\": \"$script\",
      \"produces\": \"$produces\",
      \"reason\": \"$reason\",
      \"severity\": \"$severity\",
      \"tier\": \"$min_tier\"
    }"
}

# Universal — always.
# Tier=quick: hydration-check launches a single agent-browser session and
# greps the console for known errors; runs in seconds and catches the most
# common "page didn't boot" regression class. Cheap enough to keep at quick.
add_check "hydration-check" \
          "skills/visual-debug/scripts/hydration-check.sh" \
          "hydration-check.json" \
          "Universal — every HTML page must hydrate cleanly" \
          "block" \
          "quick"

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
            "standard"
fi

# Tier=standard: same browser-one-shot shape as scroll-end-completion.
if [ "$HAS_IO_REVEAL" = "true" ]; then
  add_check "reveal-trigger" \
            "skills/visual-debug/scripts/reveal-trigger-check.sh" \
            "reveal-trigger.json" \
            "signals.hasIOReveal=true — initially-hidden elements must advance after IO fires" \
            "block" \
            "standard"
fi

# 60fps video motion compare. Fires whenever any motion signal is true.
# Closes the "right destination, wrong velocity-curve" failure class that the
# prior 5-point trajectory probe could not see — easeOutCubic vs easeOutQuint
# read identical at 0/25/50/75/100 % of scroll but feel different to a user.
# transition-trajectory-compare.sh is kept available for ad-hoc debug; it is
# no longer in dispatch. Splash mode adds page-load motion coverage that the
# rest of the verification pipeline (static screenshots, hover compare) misses.
# Tier=comprehensive: records ~5-10s of 60fps video per signal class and
# SSIMs every pair — the most expensive row in the dispatch.
if [ "$HAS_SCROLL_SCRUB" = "true" ] || [ "$HAS_IO_REVEAL" = "true" ] || [ "$HAS_SPLASH" = "true" ]; then
  add_check "video-motion-compare" \
            "skills/visual-debug/scripts/video-motion-compare.sh" \
            "transitions/video-motion-result.txt" \
            "any motion signal true — 60fps frame-by-frame match (catches different easing / threshold / splash timing)" \
            "block" \
            "comprehensive"
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
            "standard"
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
            "comprehensive"
fi

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
            "comprehensive"
fi

# Every site with a transition-spec.json should verify each entry is wired
# into the impl. Catches the "hover matched while intersection/scroll entries
# were never wired" failure class that transition-compare.sh can't see.
# Tier=quick: file-presence + grep over generated source — no browser.
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
fi

# Enforce transition-spec.json reflects animation-runtime-dump.json signal classes.
# Closes the gap where transition-spec-rules.md Rule 7 was advisory — an agent
# could author a spec with zero scroll entries while the live page is running
# 30 ScrollTrigger animations. With this row wired, that mismatch fails the
# post-implement gate.
# Tier=quick: pure JSON-vs-JSON comparison — instant.
ANIM_DUMP="$REF_DIR/animation-runtime-dump.json"
if [ -f "$ANIM_DUMP" ] && [ -f "$TRANSITION_SPEC" ]; then
  add_check "runtime-spec-coverage" \
            "skills/visual-debug/scripts/runtime-spec-coverage.sh" \
            "runtime-spec-coverage.json" \
            "animation-runtime-dump.json signal classes (ScrollTrigger / IX2 timelines) must be reflected in transition-spec.json" \
            "block" \
            "quick"
fi

# Tier=standard: one-shot browser load + document.fonts.check() in both
# sessions. Fast but requires real browser context, so not at quick.
if [ "$HAS_COMMERCIAL_FONT" = "true" ]; then
  add_check "font-parity" \
            "skills/visual-debug/scripts/font-parity-check.sh" \
            "font-parity.json" \
            "signals.hasCommercialFont=true — declared substitution must be honored" \
            "block" \
            "standard"
fi

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
fi

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

# tree-diff — primary STATIC-phase convergence loop. Walks every visible
# impl element, pairs with ref via elementFromPoint, diffs computed style +
# layout. Becomes the gate that the iter loop has to drive to zero
# critical/major mismatches. Block severity so section-compare PASS isn't
# enough to declare done — the per-element diff must also converge.
# Runtime: ~200 element pairs × 2 sides ≈ 400 browser eval calls, so
# min_tier=standard (skip in quick smoke).
add_check "tree-diff" \
          "skills/visual-debug/scripts/tree-diff.sh" \
          "tree-diff-status.json" \
          "Every paired impl ↔ ref element must match style + layout within tolerance (zero critical/major/layout-major mismatches)" \
          "block" \
          "standard"

# scroll-coverage — revives the previously-orphan batch-scroll + batch-compare
# pair as a dispatchable check. Catches the "section-compare collapsed to N
# sections" coverage gap (d19e28d benchmark only matched 2 of 16 sections)
# by sweeping AE every 10% of page scroll on both sides — orthogonal to
# the DOM-section enumeration.
REGIONS_JSON="$REF_DIR/regions.json"
if [ -f "$REGIONS_JSON" ]; then
  add_check "scroll-coverage" \
            "skills/visual-debug/scripts/scroll-coverage-check.sh" \
            "scroll-coverage.json" \
            "≥70% of sampled scroll positions must match within AE/Mpx threshold (catches section-compare's enumeration blind spots)" \
            "warn" \
            "standard"
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
            "standard"
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
cat > "$OUT" <<JSON
{
  "schemaVersion": 1,
  "generatedAt": "$NOW",
  "component": "$COMPONENT",
  "tier": "$TIER",
  "signals": {
    "hasScrollScrub": $HAS_SCROLL_SCRUB,
    "hasIOReveal": $HAS_IO_REVEAL,
    "hasHover": $HAS_HOVER,
    "hasSplash": $HAS_SPLASH,
    "hasCanvas": $HAS_CANVAS,
    "hasCustomScroll": $HAS_CUSTOM_SCROLL,
    "hasCommercialFont": $HAS_COMMERCIAL_FONT,
    "hasClickStateTransition": $HAS_CLICK_STATE
  },
  "requiredChecks": [$CHECKS
  ],
  "viewports": [
    { "w": 375,  "h": 812,  "label": "mobile" },
    { "w": 1280, "h": 800,  "label": "laptop" },
    { "w": 1600, "h": 900,  "label": "desktop-mid" },
    { "w": 1920, "h": 1080, "label": "desktop-large" }
  ]
}
JSON

echo "Wrote $OUT"
echo "Tier: $TIER"
echo "Signals: scrollScrub=$HAS_SCROLL_SCRUB ioReveal=$HAS_IO_REVEAL hover=$HAS_HOVER splash=$HAS_SPLASH canvas=$HAS_CANVAS customScroll=$HAS_CUSTOM_SCROLL commercialFont=$HAS_COMMERCIAL_FONT clickState=$HAS_CLICK_STATE"

# Validate JSON
if [ "$HAS_JQ" -eq 1 ]; then
  if ! jq empty "$OUT" 2>/dev/null; then
    echo "ERROR: produced file is not valid JSON" >&2
    exit 2
  fi
fi

exit 0
