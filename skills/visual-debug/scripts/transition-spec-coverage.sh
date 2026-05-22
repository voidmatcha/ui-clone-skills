#!/usr/bin/env bash
# transition-spec-coverage.sh — Static gate: every transition-spec.json entry
# must have at least one matching artifact in the impl source tree.
#
# Why it matters:
#   It is common for the agent to declare "transitions matched" after
#   transition-compare.sh passes — but transition-compare only verifies
#   hover/idle-state diffs. Intersection-driven or scroll-driven entries can be
#   entirely missing from the impl while the hover sweep stays green. This
#   script makes that bookkeeping checkable: parse spec entries, grep the impl
#   source for hooks that match each entry's `id` / `selector` / `type`, and
#   FAIL if any entry has zero hits.
#
#   This is the static counterpart to reveal-trigger-check.sh:
#     - reveal-trigger-check  → runtime: do reveals actually trigger?
#     - transition-spec-coverage → static: are all spec entries even wired?
#   Both must pass; one without the other leaves the door open to the same
#   class of regression.
#
# Usage:
#   bash transition-spec-coverage.sh <component-dir> <impl-src-dir>
#
#   <component-dir>: path containing transition-spec.json (e.g. tmp/ref/<c>)
#   <impl-src-dir>:  path to the impl source root for the component
#                    (e.g. apps/<app>/src/projects/<c>)
#
# Exit: 0 = every spec entry has at least one impl hit, 1 = uncovered entries,
#       2 = setup error / missing files

set -uo pipefail

COMP_DIR="${1:?Usage: transition-spec-coverage.sh <component-dir> [<impl-src-dir>]}"
IMPL_DIR="${2:-}"
SPEC="$COMP_DIR/transition-spec.json"

if [ -z "$IMPL_DIR" ] || [ ! -d "$IMPL_DIR" ]; then
  RESOLVER="${PLUGIN_ROOT:-$(dirname "$(dirname "$(dirname "${BASH_SOURCE[0]}")")")}/scripts/extract/find-impl-root.sh"
  if [ -x "$RESOLVER" ]; then
    RESOLVED=$(bash "$RESOLVER" "$COMP_DIR" 2>/dev/null | sed -n '2p')
    if [ -n "$RESOLVED" ] && [ -d "$RESOLVED" ]; then
      IMPL_DIR="$RESOLVED"
    fi
  fi
fi

if [ ! -f "$SPEC" ]; then
  echo "ERROR: transition-spec.json not found at $SPEC"
  exit 2
fi
if [ -z "$IMPL_DIR" ] || [ ! -d "$IMPL_DIR" ]; then
  echo "ERROR: impl source dir not found (tried arg + find-impl-root.sh fallback)"
  exit 2
fi
if ! command -v node &>/dev/null; then
  echo "ERROR: node not found"
  exit 2
fi

# Parse the spec into shell-friendly pipe-separated lines:
# id|type|trigger|selector
# Use `|` not `\t` because bash `read` with IFS=$'\t' collapses consecutive
# tabs, eating empty fields — the literal substitution `_` keeps fields aligned.
ENTRIES=$(node -e "
const fs = require('fs');
const spec = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const list = Array.isArray(spec) ? spec
  : (Array.isArray(spec.transitions) ? spec.transitions
    : (Array.isArray(spec.entries) ? spec.entries : []));
for (const e of list) {
  const id = (e.id || e.name || '').toString();
  // Look for type at top-level OR nested under animation.type (transition-spec
  // schema variant — see ../ui-reverse-engineering/transition-spec-rules.md).
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

echo "═══ Transition Spec Coverage ═══"
echo "Spec:        $SPEC"
echo "Impl source: $IMPL_DIR"
echo ""

UNCOVERED=0
TOTAL=0
echo "| # | id | trigger | type | hits | matched on |"
echo "|---|----|---------|------|------|------------|"

i=0
while IFS='|' read -r id type trigger selector; do
  # Restore empty fields from the `_` placeholder so downstream needle generation
  # doesn't grep for the literal underscore.
  [ "$type" = "_" ] && type=""
  [ "$trigger" = "_" ] && trigger=""
  [ "$selector" = "_" ] && selector=""
  TOTAL=$((TOTAL + 1))
  # Build a list of search needles. Anything that's a meaningful identifier:
  # - the entry id (kebab/camel), with both forms
  # - selector tokens (class names without leading dot)
  # - synthesised hook names from the type ("intersection-fade-up" → RevealRise / useScrollTrigger)
  needles=()
  needles+=("$id")
  # camelCase + PascalCase forms of the id.
  camel=$(echo "$id" | awk -F'-' '{ for (i=1;i<=NF;i++) { if (i==1) printf "%s",$i; else printf "%s%s", toupper(substr($i,1,1)), substr($i,2) } }')
  pascal=$(echo "$id" | awk -F'-' '{ for (i=1;i<=NF;i++) printf "%s%s", toupper(substr($i,1,1)), substr($i,2) }')
  [ -n "$camel" ] && [ "$camel" != "$id" ] && needles+=("$camel")
  [ -n "$pascal" ] && [ "$pascal" != "$id" ] && [ "$pascal" != "$camel" ] && needles+=("$pascal")
  # Selector tokens: split on whitespace AND `.` so chained classes (`a.b.c`)
  # become individual class needles. Strip CSS-Modules hash suffixes
  # (`header_menuItem__unxJB` → `header_menuItem`, `menuItem`) so impls that
  # rename classes to plain semantic names still match.
  for raw in $(echo "$selector" | tr ' ' '\n' | sed 's/^[\.#]//' | tr '.' '\n' | grep -v '^$'); do
    case "$raw" in
      ">"|"+"|"~"|"*"|":"*) continue ;;
      *) ;;
    esac
    [ ${#raw} -lt 3 ] && continue
    needles+=("$raw")
    # Strip CSS-Modules hash suffix: `__xxxxx` at end.
    base=$(echo "$raw" | sed 's/__[A-Za-z0-9_-]\{3,\}$//')
    if [ -n "$base" ] && [ "$base" != "$raw" ] && [ ${#base} -ge 3 ]; then
      needles+=("$base")
      # If the stripped name is `prefix_localName`, also try `localName`
      # (CSS-Modules format) and `prefix-localName` (kebab equivalent).
      local_name=$(echo "$base" | sed 's/^[a-z]*_//')
      if [ -n "$local_name" ] && [ "$local_name" != "$base" ] && [ ${#local_name} -ge 3 ]; then
        needles+=("$local_name")
        # camelCase → kebab-case (menuItem → menu-item) for Tailwind/utility impls.
        kebab=$(echo "$local_name" | sed 's/\([A-Z]\)/-\L\1/g' | sed 's/^-//')
        [ "$kebab" != "$local_name" ] && needles+=("$kebab")
      fi
    fi
  done
  # Type-derived hook hints.
  case "$type" in
    intersection-fade-up|fade-up|reveal-rise) needles+=("RevealRise" "RevealLetters" "RevealWords" "useScrollTrigger" "IntersectionObserver") ;;
    scroll-driven|scroll-driven-scale|scroll-scale|scroll-parallax) needles+=("useScroll" "useTransform" "ScrollScale" "scroll(") ;;
    hover) needles+=("onMouseEnter" "onMouseLeave" "onPointerEnter" ":hover") ;;
    css-class-toggle|css-hover) needles+=(":hover" "hover:" "@media (hover") ;;
    timer|loop|cycle|auto-timer) needles+=("setInterval" "setTimeout" "requestAnimationFrame" "useAnimationFrame") ;;
    canvas-webgl-shader) needles+=("getContext" "createShader" "WebGL" "useFrame" "<canvas") ;;
    raf-position-follow) needles+=("requestAnimationFrame" "lerp" "translate3d" "transform") ;;
    scroll-engine) needles+=("Lenis" "ReactLenis" "useLenis" "lenis") ;;
  esac
  # Trigger-derived hints.
  case "$trigger" in
    intersection|inview|enter-viewport) needles+=("useScrollTrigger" "IntersectionObserver") ;;
    scroll) needles+=("useScroll" "scroll(" "Lenis") ;;
    hover|css-hover) needles+=("onMouseEnter" "onPointerEnter" ":hover" "hover:") ;;
    mousemove) needles+=("mousemove" "onMouseMove" "pointermove" "onPointerMove") ;;
    auto-timer) needles+=("setInterval" "requestAnimationFrame" "useFrame") ;;
  esac

  hits=0
  matched=""
  for n in "${needles[@]}"; do
    [ -z "$n" ] && continue
    # Use grep -r -l for filename listing; -F fixes literal match.
    found=$(grep -r -l -F "$n" "$IMPL_DIR" 2>/dev/null | head -3 || true)
    if [ -n "$found" ]; then
      hits=$((hits + 1))
      if [ -z "$matched" ]; then
        matched="\`$n\`"
      fi
    fi
  done

  status_icon="✅"
  if [ "$hits" -eq 0 ]; then
    status_icon="❌"
    UNCOVERED=$((UNCOVERED + 1))
  fi
  echo "| $i | $status_icon $id | $trigger | $type | $hits | ${matched:-—} |"
  i=$((i + 1))
done <<< "$ENTRIES"

echo ""
echo "Coverage: $((TOTAL - UNCOVERED)) / $TOTAL"
echo ""

# JSON sidecar for gate_post_implement (verification-plan dispatch reads this).
STATUS="pass"
[ "$UNCOVERED" -gt 0 ] && STATUS="fail"
cat > "$COMP_DIR/transition-spec-coverage.json" <<JSON
{
  "schemaVersion": 1,
  "status": "$STATUS",
  "total": $TOTAL,
  "covered": $((TOTAL - UNCOVERED)),
  "uncovered": $UNCOVERED
}
JSON

if [ "$UNCOVERED" -gt 0 ]; then
  echo "⛔ $UNCOVERED spec entr$([ "$UNCOVERED" -eq 1 ] && echo "y" || echo "ies") have no matching impl artifact."
  echo "   This is the bug class where 'hover transitions matched' was reported"
  echo "   while intersection/scroll-driven entries were never implemented."
  echo ""
  echo "   Fix: implement the missing entries OR delete them from the spec if"
  echo "   they were over-extracted. Do NOT mark verification PASS until this"
  echo "   table is all ✅."
  exit 1
fi

echo "✅ Every spec entry has at least one matching impl artifact."
exit 0
