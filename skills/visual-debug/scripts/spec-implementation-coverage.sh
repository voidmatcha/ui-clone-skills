#!/usr/bin/env bash
# spec-implementation-coverage.sh — Post-implement gate: every transition-spec
# entry whose selector/id is matched in the impl source must also have a motion
# declaration in that source. Closes the silent-killer failure class where:
#   1. The agent produced a clean transition-spec.json,
#   2. transition-spec-coverage passed (selector hits ≥1 per entry),
#   3. but the generated component renders the selector with zero animation
#      hooks — fade-in is missing, easing wrong, or scroll-driven entry never
#      wired to IntersectionObserver / useScroll.
#
# Why a separate script instead of folding into transition-spec-coverage:
#   transition-spec-coverage answers "does the impl mention this entry?" —
#   useful as a pre-generate sanity check or a quick presence audit. This
#   script answers "and does the impl actually animate it?" — meaningful only
#   after generation, with a strictly stronger pass bar. Splitting keeps the
#   cheaper presence check usable at quick tier while gating the expensive
#   declaration check at standard tier.
#
# What counts as a "motion declaration":
#   - CSS transition / animation properties (transition:, animation:,
#     @keyframes, transition-property:)
#   - React motion libraries (framer-motion / motion, react-spring,
#     @react-spring, react-use-gesture)
#   - Scroll/intersection hooks (useScroll, useTransform, useSpring,
#     useScrollTrigger, IntersectionObserver, react-intersection-observer)
#   - GSAP / Lenis / ScrollMagic (gsap.to, gsap.from, gsap.timeline, Lenis,
#     ReactLenis, ScrollMagic)
#   - Tailwind animation utilities (animate-, transition-, ease-, duration-,
#     hover:, focus:, group-hover:) — captured by the `transition-` prefix
#   - Trigger-specific runtime wiring. Generic motion keywords are not enough:
#     hover entries need hover handlers/CSS, click/accordion entries need click
#     or expansion state, smooth-scroll entries need real smooth-scroll wiring,
#     and load reveals need mount/load reveal wiring.
#
# The matcher is intentionally permissive — a false positive here means an
# implementation passes that should have failed (rare in practice given the
# entry must also be selector-matched); a false negative means a real impl
# with custom motion plumbing fails. Permissive errs toward fewer false
# negatives, which is the right trade-off for an additive gate (the
# transition-spec-coverage row catches the missing-entirely case separately).
#
# Usage:
#   bash spec-implementation-coverage.sh <component-dir> <impl-src-dir>
#
#   <component-dir>: path containing transition-spec.json (e.g. tmp/ref/<c>)
#   <impl-src-dir>:  path to the impl source root for the component
#                    (e.g. apps/<app>/src/projects/<c>)
#
# Exit: 0 = every covered entry has a motion declaration in its matched files,
#       1 = entries with selector hit but no motion declaration,
#       2 = setup error / missing files

set -uo pipefail

COMP_DIR="${1:?Usage: spec-implementation-coverage.sh <component-dir> <impl-src-dir>}"
IMPL_DIR="${2:?Missing impl-src-dir}"
SPEC="$COMP_DIR/transition-spec.json"

if [ ! -f "$SPEC" ]; then
  echo "ERROR: transition-spec.json not found at $SPEC"
  exit 2
fi
if [ ! -d "$IMPL_DIR" ]; then
  echo "ERROR: impl source dir not found at $IMPL_DIR"
  exit 2
fi
if ! command -v node &>/dev/null; then
  echo "ERROR: node not found"
  exit 2
fi

# Same parse shape as transition-spec-coverage.sh — keep the field order
# (id|type|trigger|selector) so reading either script is easy. The `_`
# placeholder preserves empty fields against bash `read` IFS collapsing.
ENTRIES=$(node -e "
const fs = require('fs');
const spec = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const list = Array.isArray(spec) ? spec
  : (Array.isArray(spec.transitions) ? spec.transitions
    : (Array.isArray(spec.entries) ? spec.entries : []));
for (const e of list) {
  const id = (e.id || e.name || '').toString();
  const type = (e.type || (e.animation && e.animation.type) || '_').toString();
  const trigger = (e.trigger || '_').toString();
  const selector = (e.selector || e.target || '_').toString();
  if (!id) continue;
  console.log([id, type, trigger, selector].join('|'));
}
" "$SPEC")

if [ -z "$ENTRIES" ]; then
  echo "ERROR: spec has no entries (or schema not recognised)."
  exit 2
fi

echo "═══ Spec Implementation Coverage ═══"
echo "Spec:        $SPEC"
echo "Impl source: $IMPL_DIR"
echo ""

# Motion-declaration needles. Order roughly by frequency in modern impls so
# the inner grep loop short-circuits sooner on the common cases.
MOTION_NEEDLES=(
  # CSS
  "transition:"
  "transition-property"
  "scroll-behavior"
  "animation:"
  "@keyframes"
  # Tailwind utilities
  "transition-"
  "animate-"
  "duration-"
  "ease-"
  "hover:"
  "group-hover:"
  "focus:"
  # framer-motion / motion
  "framer-motion"
  "from 'motion"
  "from \"motion"
  "<motion."
  "useMotionValue"
  "useTransform"
  "useScroll"
  "useSpring"
  "AnimatePresence"
  # GSAP / Lenis / ScrollMagic
  "gsap.to"
  "gsap.from"
  "gsap.timeline"
  "ScrollTrigger"
  "Lenis"
  "ReactLenis"
  # IntersectionObserver
  "IntersectionObserver"
  "useInView"
  "useIntersection"
  "useScrollTrigger"
  # react-spring
  "react-spring"
  "useSprings"
  "useChain"
  # Webflow IX2 markers
  "data-w-id"
  "w-mod"
)

MARKER_SCAFFOLD_RE='data-transition-hooks|data-transition=|data-scroll-hook|data-hover-hook|data-click-hook|data-motion-hook|hidden[[:space:]][^>]*data-'
MARKER_HOOK_FILE_RE='data-transition-hooks'

has_marker_scaffold() {
  local files="$1"
  local f
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if grep -Eq "$MARKER_SCAFFOLD_RE" "$f" 2>/dev/null; then
      return 0
    fi
  done <<< "$files"
  return 1
}

has_motion_needle() {
  local needle="$1"
  local files="$2"
  local f
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if grep -Eq "$MARKER_HOOK_FILE_RE" "$f" 2>/dev/null; then
      continue
    fi
    if grep -Ev "$MARKER_SCAFFOLD_RE" "$f" 2>/dev/null | grep -qF "$needle"; then
      return 0
    fi
  done <<< "$files"
  return 1
}

has_code_match() {
  local regex="$1"
  local files="$2"
  local f
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if grep -Eq "$MARKER_HOOK_FILE_RE" "$f" 2>/dev/null; then
      continue
    fi
    if grep -Ev "$MARKER_SCAFFOLD_RE" "$f" 2>/dev/null | grep -Eq "$regex"; then
      return 0
    fi
  done <<< "$files"
  return 1
}

trigger_static_reason() {
  local id="$1"
  local type="$2"
  local trigger="$3"
  local files="$4"
  local key
  key=$(printf '%s %s %s' "$id" "$type" "$trigger")

  if echo "$key" | grep -Eiq 'hover|mouseenter|mouseover|pointerenter'; then
    if ! has_code_match '(^|[^A-Za-z0-9_-])(:hover|hover:|group-hover:|onMouseEnter|onMouseLeave|onPointerEnter|onPointerLeave|whileHover|useHover|addEventListener[[:space:]]*\([[:space:]]*["'\''](mouseenter|mouseover|pointerenter))' "$files"; then
      echo "hover trigger missing handler/css"
      return 0
    fi
  fi

  if echo "$key" | grep -Eiq 'click|accordion|toggle|expanded'; then
    if ! has_code_match '(onClick|addEventListener[[:space:]]*\([[:space:]]*["'\'']click|aria-expanded|useState|useReducer|<details|<summary|[[:space:]]open[=}]|data-state=|set[A-Z][A-Za-z0-9_]*)' "$files"; then
      echo "click/accordion trigger missing handler/state"
      return 0
    fi
  fi

  if echo "$key" | grep -Eiq 'smooth-scroll|smooth[[:space:]_-]*scroll|lenis'; then
    if ! has_code_match '(new[[:space:]]+Lenis|ReactLenis|from[[:space:]]+["'\'']lenis["'\'']|Lenis[[:space:]]*\(|scroll-behavior[[:space:]]*:[[:space:]]*smooth|scrollBehavior[[:space:]]*:[[:space:]]*["'\'']?smooth)' "$files"; then
      echo "smooth scroll missing Lenis/native smooth-scroll wiring"
      return 0
    fi
  elif echo "$key" | grep -Eiq '(^|[[:space:]_-])scroll([[:space:]_-]|$)|scroll-driven|scrolltrigger'; then
    if ! has_code_match '(useScroll|scrollYProgress|useTransform|ScrollTrigger|scrollTrigger|addEventListener[[:space:]]*\([[:space:]]*["'\'']scroll|onscroll|requestAnimationFrame|getBoundingClientRect|ScrollTimeline|animationTimeline)' "$files"; then
      echo "scroll trigger missing scroll progress/listener wiring"
      return 0
    fi
  fi

  if echo "$key" | grep -Eiq 'page-load|(^|[[:space:]_-])load([[:space:]_-]|$)|mount-reveal|load-reveal'; then
    if ! has_code_match '(@keyframes|animation:|animate-|<motion\.|initial=|animate=|useEffect|requestAnimationFrame|setTimeout|onLoad|data-loaded|isLoaded|loaded)' "$files"; then
      echo "load reveal missing mount/load animation wiring"
      return 0
    fi
  fi

  return 1
}

UNCOVERED=0
PRESENCE_ONLY=0
SCROLL_SCRUB_STATIC=0
INTERSECTION_STATIC=0
TRIGGER_STATIC=0
MARKER_ONLY=0
TOTAL=0

echo "| # | id | trigger | type | matched file(s) | motion |"
echo "|---|----|---------|------|-----------------|--------|"

i=0
while IFS='|' read -r id type trigger selector; do
  [ "$type" = "_" ] && type=""
  [ "$trigger" = "_" ] && trigger=""
  [ "$selector" = "_" ] && selector=""
  TOTAL=$((TOTAL + 1))

  # Build needles for finding matched impl files. Same logic as
  # transition-spec-coverage.sh — keep selector/id family lookups identical
  # so an entry covered there is the same entry inspected here.
  needles=()
  needles+=("$id")
  camel=$(echo "$id" | awk -F'-' '{ for (i=1;i<=NF;i++) { if (i==1) printf "%s",$i; else printf "%s%s", toupper(substr($i,1,1)), substr($i,2) } }')
  pascal=$(echo "$id" | awk -F'-' '{ for (i=1;i<=NF;i++) printf "%s%s", toupper(substr($i,1,1)), substr($i,2) }')
  [ -n "$camel" ] && [ "$camel" != "$id" ] && needles+=("$camel")
  [ -n "$pascal" ] && [ "$pascal" != "$id" ] && [ "$pascal" != "$camel" ] && needles+=("$pascal")
  for raw in $(echo "$selector" | tr ' ' '\n' | sed 's/^[\.#]//' | tr '.' '\n' | grep -v '^$'); do
    case "$raw" in
      ">"|"+"|"~"|"*"|":"*) continue ;;
      *) ;;
    esac
    [ ${#raw} -lt 3 ] && continue
    needles+=("$raw")
    base=$(echo "$raw" | sed 's/__[A-Za-z0-9_-]\{3,\}$//')
    if [ -n "$base" ] && [ "$base" != "$raw" ] && [ ${#base} -ge 3 ]; then
      needles+=("$base")
      local_name=$(echo "$base" | sed 's/^[a-z]*_//')
      if [ -n "$local_name" ] && [ "$local_name" != "$base" ] && [ ${#local_name} -ge 3 ]; then
        needles+=("$local_name")
      fi
    fi
  done

  # Find matched files (uniqued).
  matched_files=""
  for n in "${needles[@]}"; do
    [ -z "$n" ] && continue
    found=$(grep -r -l -F "$n" "$IMPL_DIR" 2>/dev/null || true)
    if [ -n "$found" ]; then
      matched_files="$matched_files
$found"
    fi
  done
  matched_files=$(echo "$matched_files" | sort -u | grep -v '^$' || true)

  if [ -z "$matched_files" ]; then
    # Not matched at all — transition-spec-coverage handles this case.
    # This script is specifically the "matched but unanimated" gate, so
    # missing-entirely entries get a separate icon to make that visible
    # without double-counting against this script's exit code.
    echo "| $i | ⚠️ $id | $trigger | $type | (none — see transition-spec-coverage) | — |"
    i=$((i + 1))
    continue
  fi

  # Search the matched files for any motion-declaration needle. Stop on
  # first hit to keep the loop fast on large impls.
  motion_hit=""
  for m in "${MOTION_NEEDLES[@]}"; do
    if has_motion_needle "$m" "$matched_files"; then
      motion_hit="\`$m\`"
      break
    fi
  done

  file_count=$(echo "$matched_files" | wc -l | tr -d ' ')
  if has_marker_scaffold "$matched_files"; then
    MARKER_ONLY=$((MARKER_ONLY + 1))
  fi

  # Stronger bar for pinned / scrubbed scroll storytelling. A CSS transition
  # proves an element can animate after some state changes; it does NOT prove
  # the page implements the reference pattern where scroll progress is sampled
  scroll_scrub_entry=0
  if echo "$id $type $trigger" | grep -Eiq 'scroll-scrub|scroll-driven.*pin|pin.*scroll|sticky-pin'; then
    scroll_scrub_entry=1
  fi
  if [ "$scroll_scrub_entry" -eq 1 ]; then
    has_progress=0
    has_pin=0
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      if grep -Eq 'useScroll|scrollYProgress|useTransform|ScrollTrigger|scrollTrigger|requestAnimationFrame|getBoundingClientRect|ScrollTimeline|animationTimeline' "$f" 2>/dev/null; then
        has_progress=1
      fi
      if grep -Eq "position:[[:space:]]*['\"]?sticky|position:[[:space:]]*sticky|className=.*sticky|pin:[[:space:]]*true|pin:[[:space:]]*[^,}]+|ScrollTrigger" "$f" 2>/dev/null; then
        has_pin=1
      fi
      [ "$has_progress" -eq 1 ] && [ "$has_pin" -eq 1 ] && break
    done <<< "$matched_files"
    if [ "$has_progress" -eq 0 ] || [ "$has_pin" -eq 0 ]; then
      echo "| $i | ❌ $id | $trigger | $type | $file_count file(s) | scroll-scrub missing progress=$has_progress pin=$has_pin |"
      SCROLL_SCRUB_STATIC=$((SCROLL_SCRUB_STATIC + 1))
      UNCOVERED=$((UNCOVERED + 1))
      i=$((i + 1))
      continue
    fi
    motion_hit="${motion_hit:-\`scroll-scrub progress+pin\`}"
  fi

  # Stronger bar for in-view / intersection reveals. A CSS transition proves
  # the element can animate, but it does not prove the implementation observes
  intersection_entry=0
  if echo "$id $type $trigger" | grep -Eiq 'intersection|in-view|inview|viewport|while-in-view|whileInView'; then
    intersection_entry=1
  fi
  if [ "$intersection_entry" -eq 1 ]; then
    has_observer=0
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      if grep -Eq 'IntersectionObserver|useInView|whileInView|viewport[[:space:]]*=|viewport:|onViewportEnter|onViewportLeave|useIntersection|react-intersection-observer' "$f" 2>/dev/null; then
        has_observer=1
        break
      fi
    done <<< "$matched_files"
    if [ "$has_observer" -eq 0 ]; then
      echo "| $i | ❌ $id | $trigger | $type | $file_count file(s) | intersection reveal missing observer |"
      INTERSECTION_STATIC=$((INTERSECTION_STATIC + 1))
      UNCOVERED=$((UNCOVERED + 1))
      i=$((i + 1))
      continue
    fi
    motion_hit="${motion_hit:-\`intersection observer\`}"
  fi

  if [ -z "$motion_hit" ]; then
    echo "| $i | ❌ $id | $trigger | $type | $file_count file(s) | — |"
    PRESENCE_ONLY=$((PRESENCE_ONLY + 1))
    UNCOVERED=$((UNCOVERED + 1))
    i=$((i + 1))
    continue
  fi

  trigger_reason=$(trigger_static_reason "$id" "$type" "$trigger" "$matched_files" || true)
  if [ -n "$trigger_reason" ]; then
    echo "| $i | ❌ $id | $trigger | $type | $file_count file(s) | $trigger_reason |"
    TRIGGER_STATIC=$((TRIGGER_STATIC + 1))
    UNCOVERED=$((UNCOVERED + 1))
    i=$((i + 1))
    continue
  fi

  echo "| $i | ✅ $id | $trigger | $type | $file_count file(s) | $motion_hit |"
  i=$((i + 1))
done <<< "$ENTRIES"

echo ""
echo "Coverage: $((TOTAL - UNCOVERED)) / $TOTAL with motion declared"
echo ""

# JSON sidecar for gate_post_implement (verification-plan dispatch reads this).
STATUS="pass"
[ "$UNCOVERED" -gt 0 ] && STATUS="fail"
cat > "$COMP_DIR/spec-implementation-coverage.json" <<JSON
{
  "schemaVersion": 1,
  "status": "$STATUS",
  "total": $TOTAL,
  "withMotion": $((TOTAL - UNCOVERED)),
  "presenceOnly": $PRESENCE_ONLY,
  "scrollScrubStatic": $SCROLL_SCRUB_STATIC,
  "intersectionStatic": $INTERSECTION_STATIC,
  "triggerStatic": $TRIGGER_STATIC,
  "markerOnly": $MARKER_ONLY
}
JSON

if [ "$UNCOVERED" -gt 0 ]; then
  echo "⛔ $UNCOVERED spec entr$([ "$UNCOVERED" -eq 1 ] && echo "y" || echo "ies") matched in impl source but have no motion declaration."
  echo ""
  echo "   This is the bug class where the generated component renders the"
  echo "   selector but never animates it — same end markup, missing motion."
  echo "   For scroll-scrub / pinned sections, CSS transition alone is not enough:"
  echo "   matched source must include a scroll progress source and sticky/pin"
  echo "   structure."
  echo "   For intersection / in-view reveals, CSS transition alone is not enough:"
  echo "   matched source must include viewport observer wiring such as"
  echo "   IntersectionObserver, useInView, whileInView, or onViewportEnter."
  echo "   For trigger-specific entries, marker strings are not enough:"
  echo "   matched source must include non-marker hover/click/load/scroll wiring"
  echo "   appropriate to the spec trigger."
  echo "   Fix: open each entry's matched file and wire the declared trigger /"
  echo "   easing / duration. Do NOT mark verification PASS until this table"
  echo "   is all ✅."
  exit 1
fi

echo "✅ Every covered spec entry has a motion declaration in its matched files."
exit 0
