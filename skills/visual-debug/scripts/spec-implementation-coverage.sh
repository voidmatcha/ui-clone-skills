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

UNCOVERED=0
PRESENCE_ONLY=0
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
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    for m in "${MOTION_NEEDLES[@]}"; do
      if grep -qF "$m" "$f" 2>/dev/null; then
        motion_hit="\`$m\`"
        break 2
      fi
    done
  done <<< "$matched_files"

  file_count=$(echo "$matched_files" | wc -l | tr -d ' ')
  if [ -n "$motion_hit" ]; then
    echo "| $i | ✅ $id | $trigger | $type | $file_count file(s) | $motion_hit |"
  else
    echo "| $i | ❌ $id | $trigger | $type | $file_count file(s) | — |"
    PRESENCE_ONLY=$((PRESENCE_ONLY + 1))
    UNCOVERED=$((UNCOVERED + 1))
  fi
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
  "presenceOnly": $PRESENCE_ONLY
}
JSON

if [ "$UNCOVERED" -gt 0 ]; then
  echo "⛔ $UNCOVERED spec entr$([ "$UNCOVERED" -eq 1 ] && echo "y" || echo "ies") matched in impl source but have no motion declaration."
  echo ""
  echo "   This is the bug class where the generated component renders the"
  echo "   selector but never animates it — same end markup, missing motion."
  echo "   Fix: open each entry's matched file and wire the declared trigger /"
  echo "   easing / duration. Do NOT mark verification PASS until this table"
  echo "   is all ✅."
  exit 1
fi

echo "✅ Every covered spec entry has a motion declaration in its matched files."
exit 0
