#!/usr/bin/env bash
# transition-spec-coverage.sh — Static gate: every transition-spec.json entry
# must be wired into DOM-producing impl source (not just present in copied CSS).
#
# Why it matters:
#   It is common for the agent to declare "transitions matched" after
#   transition-compare.sh passes — but transition-compare only verifies
#   hover/idle-state diffs. Intersection-driven or scroll-driven entries can be
#   entirely missing from the impl while the hover sweep stays green.
#
#   A subtler failure: the CSS mirror (src/styles/from-ref/) reproduces every
#   ref selector verbatim, so an earlier grep over the whole impl tree scored a
#   hover entry "covered" the moment its class appeared in that copied CSS —
#   even when the TARGET was never emitted into the JSX (dead CSS acting on no
#   node). On the ebay benchmark that scored a false 16/16 while 6 of 8 hover
#   targets had zero DOM presence.
#
#   So this script is DOM-aware: for an entry with a resolvable class/id target,
#   the target must appear in DOM-producing source (JSX/TSX/HTML/JS — the CSS
#   mirror and pure stylesheets are excluded) or the entry is UNCOVERED with
#   reason `target-not-in-dom`. Bundle-mined entries without a resolvable target
#   stay on the behavior-text path (grep the impl for `id` / `type` / `trigger`
#   derived hooks). FAIL if any entry is uncovered.
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
# id|type|trigger|selector|runtime_hook|runtime_type
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
  const runtimeHook = (e.runtime_hook || '_').toString();
  const runtimeType = (e.runtime_type || '_').toString();
  if (!id) continue;
  console.log([id, type, trigger, selector, runtimeHook, runtimeType].join('|'));
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

# ─── DOM-producing source universe ───────────────────────────────────────
# Coverage must be counted from DOM-producing source only. The verbatim CSS
# mirror (src/styles/from-ref/, plus any UI_CLONE_GENERATED_EVIDENCE_DIRS dirs)
# carries every ref selector by construction, so a class present ONLY there is
# dead CSS, not a wired impl node — matching it there produced the false
# 16/16 pass. Pure stylesheets are excluded for the same reason: a bare
# `.foo{}` rule is not DOM presence.
GREP_EXCLUDES=(
  --exclude-dir=node_modules --exclude-dir=dist --exclude-dir=build
  --exclude-dir=.git --exclude-dir=.next --exclude-dir=coverage
  --exclude-dir=from-ref --exclude-dir=ref-css
  --exclude="*.css" --exclude="*.scss" --exclude="*.sass"
  --exclude="*.less" --exclude="*.styl" --exclude="*.pcss"
)
# Honor the same env override the sibling coverage scripts use for the mirror
# dir name (spec-implementation-coverage.sh, asset-utilization-check.sh).
if [ -n "${UI_CLONE_GENERATED_EVIDENCE_DIRS:-}" ]; then
  for d in $(printf '%s' "$UI_CLONE_GENERATED_EVIDENCE_DIRS" | tr ',:' '  '); do
    [ -n "$d" ] && GREP_EXCLUDES+=(--exclude-dir="$d")
  done
fi

src_grep() {
  # Print up to 3 DOM-producing source files containing the literal needle,
  # mirror dirs + pure stylesheets excluded. Empty output = needle absent.
  grep -r -l -F "$1" "$IMPL_DIR" "${GREP_EXCLUDES[@]}" 2>/dev/null | head -3 || true
}

src_id_grep() {
  # Match an actual JSX/HTML id attribute, not arbitrary prose containing the
  # same short word. CSS identifiers are restricted before reaching here, so
  # interpolating the token into this ERE is safe.
  grep -r -l -E "id[[:space:]]*=[[:space:]]*(\\{[[:space:]]*)?['\"]$1['\"]([[:space:]]*\\})?" \
    "$IMPL_DIR" "${GREP_EXCLUDES[@]}" 2>/dev/null | head -3 || true
}

is_runtime_token() {
  # Library-injected selectors are legitimately absent from static source, so a
  # target made only of these must not be flagged target-not-in-dom. Exact- or
  # prefix-match (name followed by `-`/`_`) so `.canvasThing` is NOT exempted.
  printf '%s\n' "$1" | grep -qE \
    '^(swiper|splide|slick|flickity|embla|keen-slider|glide|lottie|bodymovin|canvas|lenis|locomotive|data-scroll|data-lottie|data-lenis|data-smooth|data-pseudo)([_-].*)?$'
}

UNCOVERED=0
TOTAL=0
TSV="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/tsc.$$.tsv")"
: > "$TSV"
trap 'rm -f "$TSV"' EXIT

echo "| # | id | trigger | type | path | hits | reason |"
echo "|---|----|---------|------|------|------|--------|"

i=0
while IFS='|' read -r id type trigger selector runtime_hook runtime_type; do
  # Restore empty fields from the `_` placeholder so downstream needle generation
  # doesn't grep for the literal underscore.
  [ "$type" = "_" ] && type=""
  [ "$trigger" = "_" ] && trigger=""
  [ "$selector" = "_" ] && selector=""
  [ "$runtime_hook" = "_" ] && runtime_hook=""
  [ "$runtime_type" = "_" ] && runtime_type=""
  TOTAL=$((TOTAL + 1))

  # ── Selector-derived DOM needles (class/id tokens only) ────────────────
  # Strip attribute selectors ([data-state=x]) and pseudo-classes/elements
  # (:hover, :not(:disabled), ::before) so the raw class/id token is clean,
  # then match ONLY the full CSS-Modules token and its hash-stripped base.
  # The aggressive local-name/kebab expansion of the old needle path is
  # deliberately NOT used here: generic tails like `share`/`play` collide with
  # unrelated DOM text and would falsely rescue a dead target.
  clean_sel=$(printf '%s' "$selector" \
    | sed -E 's/\[[^]]*\]//g; s/::?[A-Za-z][A-Za-z0-9_-]*(\([^)]*\))?//g')
  dom_needles=()
  dom_id_needles=()
  # JSX motion primitives render normal DOM tags (`<motion.g id="even">` ->
  # `g#even`). Record ID tokens separately so coverage requires a real id
  # attribute instead of accepting an unrelated occurrence of the word.
  for raw in $(printf '%s\n' "$clean_sel" | grep -oE '#[A-Za-z_][A-Za-z0-9_-]*' | sed 's/^#//'); do
    [ ${#raw} -ge 3 ] && dom_id_needles+=("$raw")
  done
  class_sel=$(printf '%s' "$clean_sel" | sed -E 's/#[A-Za-z_][A-Za-z0-9_-]*//g')
  for raw in $(printf '%s\n' "$class_sel" | tr ' ' '\n' | sed 's/^\.//' | tr '.' '\n' | grep -v '^$'); do
    case "$raw" in
      ">"|"+"|"~"|"*"|":"*) continue ;;
      *) ;;
    esac
    [ ${#raw} -lt 3 ] && continue
    is_runtime_token "$raw" && continue
    dom_needles+=("$raw")
    base=$(printf '%s' "$raw" | sed 's/__[A-Za-z0-9_-]\{3,\}$//')
    if [ -n "$base" ] && [ "$base" != "$raw" ] && [ ${#base} -ge 3 ]; then
      dom_needles+=("$base")
    fi
  done

  # ── Behavior needles (id + type/trigger hints) ─────────────────────────
  # Used only for entries without a resolvable DOM target (bundle-mined scroll
  # entries, tag-only or purely runtime-injected targets).
  behavior_needles=()
  behavior_needles+=("$id")
  camel=$(echo "$id" | awk -F'-' '{ for (i=1;i<=NF;i++) { if (i==1) printf "%s",$i; else printf "%s%s", toupper(substr($i,1,1)), substr($i,2) } }')
  pascal=$(echo "$id" | awk -F'-' '{ for (i=1;i<=NF;i++) printf "%s%s", toupper(substr($i,1,1)), substr($i,2) }')
  [ -n "$camel" ] && [ "$camel" != "$id" ] && behavior_needles+=("$camel")
  [ -n "$pascal" ] && [ "$pascal" != "$id" ] && [ "$pascal" != "$camel" ] && behavior_needles+=("$pascal")
  case "$type" in
    intersection-fade-up|fade-up|reveal-rise) behavior_needles+=("RevealRise" "RevealLetters" "RevealWords" "useScrollTrigger" "IntersectionObserver") ;;
    scroll-driven|scroll-driven-scale|scroll-scale|scroll-parallax|scroll-scrub) behavior_needles+=("useScroll" "useTransform" "ScrollScale" "scroll(") ;;
    hover) behavior_needles+=("onMouseEnter" "onMouseLeave" "onPointerEnter" ":hover") ;;
    css-class-toggle|css-hover) behavior_needles+=(":hover" "hover:" "@media (hover") ;;
    timer|loop|cycle|auto-timer) behavior_needles+=("setInterval" "setTimeout" "requestAnimationFrame" "useAnimationFrame") ;;
    canvas-webgl-shader|canvas-raf) behavior_needles+=("getContext" "createShader" "WebGL" "useFrame" "<canvas") ;;
    raf-position-follow) behavior_needles+=("requestAnimationFrame" "lerp" "translate3d" "transform") ;;
    scroll-engine) behavior_needles+=("Lenis" "ReactLenis" "useLenis" "lenis") ;;
  esac
  case "$type" in
    *state*) behavior_needles+=("useState" "useScroll" "useTransform" "scrollYProgress") ;;
  esac
  case "$trigger" in
    intersection|inview|enter-viewport) behavior_needles+=("useScrollTrigger" "IntersectionObserver") ;;
    scroll*) behavior_needles+=("useScroll" "scroll(" "Lenis") ;;
    hover|css-hover) behavior_needles+=("onMouseEnter" "onPointerEnter" ":hover" "hover:") ;;
    mousemove) behavior_needles+=("mousemove" "onMouseMove" "pointermove" "onPointerMove") ;;
    auto-timer|raf*) behavior_needles+=("setInterval" "requestAnimationFrame" "useFrame") ;;
  esac
  # A Swiper target is injected by the library, so selector-only DOM coverage is
  # impossible. Runtime capture records the concrete library/action instead;
  # accept only executable Swiper construction/control hooks, not a bare import
  # or copied `.swiper` class string.
  if [ "$runtime_type" = "Swiper" ] \
    || [ "$type" = "swiper" ] \
    || printf '%s' "$runtime_hook" | grep -qE '(^|[.])swiper([.]|$)'; then
    behavior_needles+=("new Swiper(" "new Swiper (" "<Swiper " "<Swiper>" "useSwiper(" ".slideNext(")
  fi

  # ── Coverage decision ──────────────────────────────────────────────────
  # An entry WITH a resolvable class/id target is covered only when that target
  # appears in DOM-producing source; a target present only in the mirrored CSS
  # is dead. An entry WITHOUT a target stays on the behavior-text path.
  hits=0
  matched=""
  if [ ${#dom_needles[@]} -gt 0 ] || [ ${#dom_id_needles[@]} -gt 0 ]; then
    coverage_path="dom"
    # macOS Bash 3.2 treats expansion of a declared-but-empty array as an
    # unbound variable under `set -u`. Guard each loop independently because a
    # selector can have classes but no ids (or vice versa).
    if [ "${#dom_id_needles[@]}" -gt 0 ]; then
      for n in "${dom_id_needles[@]}"; do
        [ -z "$n" ] && continue
        if [ -n "$(src_id_grep "$n")" ]; then
          hits=$((hits + 1))
          [ -z "$matched" ] && matched="$n"
        fi
      done
    fi
    if [ "${#dom_needles[@]}" -gt 0 ]; then
      for n in "${dom_needles[@]}"; do
        [ -z "$n" ] && continue
        if [ -n "$(src_grep "$n")" ]; then
          hits=$((hits + 1))
          [ -z "$matched" ] && matched="$n"
        fi
      done
    fi
    if [ "$hits" -gt 0 ]; then reason="target-in-dom"; else reason="target-not-in-dom"; fi
  else
    coverage_path="behavior"
    for n in "${behavior_needles[@]}"; do
      [ -z "$n" ] && continue
      if [ -n "$(src_grep "$n")" ]; then
        hits=$((hits + 1))
        [ -z "$matched" ] && matched="$n"
      fi
    done
    if [ "$hits" -gt 0 ]; then reason="behavior-hit"; else reason="no-impl-hook"; fi
  fi

  if [ "$hits" -eq 0 ]; then
    status_icon="❌"
    covered_bool="false"
    UNCOVERED=$((UNCOVERED + 1))
  else
    status_icon="✅"
    covered_bool="true"
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$id" "$selector" "$coverage_path" "$covered_bool" "$reason" "${matched:-}" >> "$TSV"
  echo "| $i | $status_icon $id | ${trigger:-—} | ${type:-—} | $coverage_path | $hits | $reason |"
  i=$((i + 1))
done <<< "$ENTRIES"

echo ""
echo "Coverage: $((TOTAL - UNCOVERED)) / $TOTAL"
echo ""

# JSON sidecar for gate_post_implement (verification-plan dispatch reads this).
STATUS="pass"
[ "$UNCOVERED" -gt 0 ] && STATUS="fail"
node -e '
const fs = require("fs");
const [tsvPath, outPath, status, total, covered, uncovered] = process.argv.slice(1);
const rows = fs.readFileSync(tsvPath, "utf8").split("\n").filter(Boolean).map((line) => {
  const [id, target, path, coveredBool, reason, matched] = line.split("\t");
  return {
    id: id || "",
    target: target || "",
    path: path || "",
    covered: coveredBool === "true",
    reason: reason || "",
    matched: matched || null,
  };
});
const out = {
  schemaVersion: 2,
  status,
  total: Number(total),
  covered: Number(covered),
  uncovered: Number(uncovered),
  entries: rows,
};
fs.writeFileSync(outPath, JSON.stringify(out, null, 2) + "\n");
' "$TSV" "$COMP_DIR/transition-spec-coverage.json" "$STATUS" "$TOTAL" "$((TOTAL - UNCOVERED))" "$UNCOVERED"

if [ "$UNCOVERED" -gt 0 ]; then
  echo "⛔ $UNCOVERED spec entr$([ "$UNCOVERED" -eq 1 ] && echo "y" || echo "ies") have no matching impl node."
  echo "   'target-not-in-dom' means the entry's target class/id lives only in the"
  echo "   mirrored ref CSS (src/styles/from-ref/) — dead CSS with no rendered node."
  echo "   This is the bug class where 'hover transitions matched' was reported while"
  echo "   the hover TARGETS were never emitted into the JSX."
  echo ""
  echo "   Fix: emit the missing target node into the impl DOM (JSX/TSX/HTML) so the"
  echo "   mirrored hover/transition CSS has something to act on, OR delete the entry"
  echo "   from the spec if it was over-extracted. Do NOT mark verification PASS until"
  echo "   this table is all ✅."
  exit 1
fi

echo "✅ Every spec entry's target is present in the impl DOM (or wired via a hook)."
exit 0
