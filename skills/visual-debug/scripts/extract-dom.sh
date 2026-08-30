#!/usr/bin/env bash
# extract-dom.sh — invoke the Fix 13 DOM extraction eval as a real callable.
#
# Bash 4+ self-relaunch: this script uses heredoc shapes / array syntax that
if [ -z "${BASH_VERSION:-}" ] || [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
  for _bashcand in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    [ -x "$_bashcand" ] && exec "$_bashcand" "$0" "$@"
  done
  echo "extract-dom.sh: bash 4+ required (current ${BASH_VERSION:-unknown}); install via 'brew install bash'" >&2
  exit 1
fi
#
# Replaces the dom-extraction.md prose guide as the canonical entry point.
# Across V5–V10 the prose guide was repeatedly ignored — agents wrote their
# own variant of the eval that lost the per-node `text` field (Fix 6 v1)
# and the per-node `styles` field (Fix 13). With this as a script, the
# eval is invoked verbatim; downstream gates (pre-generate) check the
# resulting structure.json schema.
#
# Usage:
#   extract-dom.sh <ref-dir> <session-name> <target-selector>
#
# Writes <ref-dir>/structure.json with this schema:
#   { tag, class, display, position, text?, styles?, children: [...] }
#
# Where:
#   - text     present only if the node has its own (non-descendant) text
#   - styles   present only if at least one LAYOUT_PROPS value diverges
#              from the user-agent default. Subset of ~25 props that
#              materially affect rendered layout (display/position/box-
#              model/typography/color/transform/flex/grid/box-shadow/...).
#
# This is the deterministic primitive that Phase 4's scaffold-to-jsx.sh
# transpiler consumes. The LLM does NOT write this schema — Phase 2 does.
set -euo pipefail

# DOM snapshots are reference evidence. Pin light by default so a host
# auto-dark flip or reused browser daemon cannot change computed theme styles
# unless the caller explicitly overrides the environment.
: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME

REF_DIR=""
SESSION=""
TARGET=""
VIEWPORT=""   # Fix 17 — optional WxH (e.g. 375x812). If set, agent-browser
              # resizes the session before extracting; output written to
              # structure_<W>x<H>.json instead of structure.json so a
              # multi-viewport sweep can keep both desktop and mobile
              # structures on disk for the transpiler / agent to diff.

while [[ $# -gt 0 ]]; do
  case "$1" in
    --viewport) VIEWPORT="$2"; shift 2;;
    -h|--help) sed -n '2,22p' "$0"; exit 0;;
    *)
      if [[ -z "$REF_DIR" ]]; then REF_DIR="$1"
      elif [[ -z "$SESSION" ]]; then SESSION="$1"
      elif [[ -z "$TARGET" ]]; then TARGET="$1"
      else echo "extract-dom: unexpected arg: $1" >&2; exit 2
      fi
      shift
      ;;
  esac
done

if [[ -z "$REF_DIR" || -z "$SESSION" || -z "$TARGET" ]]; then
  echo "usage: extract-dom.sh <ref-dir> <session-name> <target-selector> [--viewport WxH]" >&2
  exit 2
fi
if [[ ! -d "$REF_DIR" ]]; then
  echo "extract-dom: ref-dir not found: $REF_DIR" >&2; exit 2
fi
if ! command -v agent-browser >/dev/null 2>&1; then
  echo "extract-dom: agent-browser not found on PATH" >&2; exit 3
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/idle-reset.sh
. "$SCRIPT_DIR/lib/idle-reset.sh"

OUT_PATH="$REF_DIR/structure.json"
if [[ -n "$VIEWPORT" ]]; then
  # Validate WxH form so a typo doesn't silently produce desktop styles.
  if [[ ! "$VIEWPORT" =~ ^[0-9]+x[0-9]+$ ]]; then
    echo "extract-dom: --viewport must be WIDTHxHEIGHT (e.g. 375x812), got: $VIEWPORT" >&2
    exit 2
  fi
  OUT_PATH="$REF_DIR/structure_${VIEWPORT}.json"
  W="${VIEWPORT%x*}"; H="${VIEWPORT#*x}"
  # Resize before extracting. Failure here is fatal — extracting at the
  # wrong viewport silently produces desktop styles which is worse than
  # erroring.
  # agent-browser CLI uses `set viewport <w> <h>`, not `resize --width`.
  if ! agent-browser --session "$SESSION" set viewport "$W" "$H" >/dev/null 2>&1; then
    echo "extract-dom: agent-browser set viewport ${W}x${H} failed" >&2
    exit 6
  fi
fi

EXTRACT_JS_SOURCE="$SCRIPT_DIR/lib/extract-dom.js"
if [[ ! -r "$EXTRACT_JS_SOURCE" ]]; then
  echo "extract-dom: JS helper not found: $EXTRACT_JS_SOURCE" >&2
  exit 7
fi

# Hydrated/translated pages can expose the layout before their visible copy is
# complete. A single immediate snapshot then preserves empty headings even
# though the same session contains the real text a moment later. Wait for two
# consecutive text signatures and no visible empty heading, with a bounded
# fallback for intentionally-empty or continuously-animated pages.
# shellcheck disable=SC2016  # JavaScript template interpolation is intentional.
TEXT_SETTLE_JS='(() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 &&
      style.display !== "none" && style.visibility !== "hidden";
  };
  const emptyHeadings = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")]
    .filter((el) => visible(el) && !(el.innerText || "").trim()).length;
  const textLength = (document.body?.innerText || "")
    .normalize("NFC").replace(/\s+/g, " ").trim().length;
  return `${textLength}:${emptyHeadings}`;
})()'
PREVIOUS_TEXT_SIGNATURE=""
for _settle_attempt in 1 2 3 4 5 6 7 8; do
  TEXT_SIGNATURE="$(
    agent-browser --session "$SESSION" eval "$TEXT_SETTLE_JS" 2>/dev/null |
      tr -d '"\r\n[:space:]'
  )"
  if [[ "$TEXT_SIGNATURE" =~ ^[0-9]+:0$ &&
        "$TEXT_SIGNATURE" == "$PREVIOUS_TEXT_SIGNATURE" ]]; then
    break
  fi
  PREVIOUS_TEXT_SIGNATURE="$TEXT_SIGNATURE"
  if [[ "$_settle_attempt" -lt 8 ]]; then
    agent-browser --session "$SESSION" wait 350 >/dev/null 2>&1 || true
  fi
done

# Inject the selector — must be a JS string literal. Escape single-quotes,
# then substitute into a temporary script. Keeping the large IIFE in a helper
# avoids command-substitution size limits while preserving the existing eval.
SELECTOR_LITERAL=$(printf '%s' "$TARGET" | sed "s/'/\\\\'/g")
EVAL_JS=$(mktemp)
TMP_OUT=$(mktemp)
trap 'rm -f "$EVAL_JS" "$TMP_OUT"' EXIT
sed "s|SELECTOR_PLACEHOLDER|'${SELECTOR_LITERAL}'|" "$EXTRACT_JS_SOURCE" > "$EVAL_JS"

CAPTURED_IDLE="$(ab_idle_reset "$SESSION")"

# Run via agent-browser and capture.
agent-browser --session "$SESSION" eval --stdin < "$EVAL_JS" > "$TMP_OUT" 2>&1 || {
  echo "extract-dom: agent-browser eval failed:" >&2
  cat "$TMP_OUT" >&2
  rm -f "$TMP_OUT"
  exit 4
}

# Validate JSON shape: must have `tag` (root node) and `children`.
# Newer agent-browser versions JSON-encode the eval return value, so a function
# that already calls JSON.stringify(...) yields a double-encoded string on disk.
# Unwrap once if the top-level is a string, then re-write the canonical form.
if ! UI_CLONE_CAPTURED_IDLE="$CAPTURED_IDLE" python3 -c "
import json, os, sys
d = json.load(open('$TMP_OUT'))
if isinstance(d, str):
    d = json.loads(d)
if not isinstance(d, dict): raise ValueError('top-level must be object')
if 'tag' not in d: raise ValueError('missing tag — schema mismatch (Fix 14)')
if 'children' not in d: raise ValueError('missing children — schema mismatch')
ci = os.environ.get('UI_CLONE_CAPTURED_IDLE')
if ci:
    try:
        d['capturedIdle'] = json.loads(ci)
    except Exception:
        d['capturedIdle'] = {'reset': False, 'idle': None, 'note': 'provenance-parse-failed'}
json.dump(d, open('$OUT_PATH', 'w'), indent=2)
" 2>&1; then
  echo "extract-dom: output failed schema validation" >&2
  head -c 500 "$TMP_OUT" >&2
  rm -f "$TMP_OUT"
  exit 5
fi
rm -f "$TMP_OUT"
NODE_COUNT=$(python3 -c "
import json
d = json.load(open('$OUT_PATH'))
def cnt(n, c=0):
  if isinstance(n, dict):
    c += 1
    for k in n.get('children', []): c = cnt(k, c)
  return c
print(cnt(d))
")
STYLE_COUNT=$(python3 -c "
import json
d = json.load(open('$OUT_PATH'))
def cnt(n, c=0):
  if isinstance(n, dict):
    if n.get('styles'): c += 1
    for k in n.get('children', []): c = cnt(k, c)
  return c
print(cnt(d))
")
echo "extract-dom: wrote $OUT_PATH"
echo "  nodes: $NODE_COUNT, with styles: $STYLE_COUNT"
