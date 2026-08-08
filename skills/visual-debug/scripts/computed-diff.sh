#!/usr/bin/env bash
# computed-diff.sh — Compare getComputedStyle between original and implementation
# Usage: bash computed-diff.sh <session> <orig-url> <impl-url> <selector1> [selector2] ...
#
# For each selector, extracts key CSS properties from both sites and reports differences.
# Catches sub-pixel mismatches that AE/SSIM might miss (wrong font-size, padding, etc.)
#
# Options (env vars):
#   VIEW_W=1440          Viewport width (default: 1440)
#   VIEW_H=900           Viewport height (default: 900)
#   WAIT_MS=4000         Page settle time in ms (default: 4000)
#   IGNORE_FONT_SIZE=1   Skip fontSize/lineHeight diffs caused by OS text scaling (default: 0)
#
# Output: Markdown table of mismatches. Empty = perfect match.
# Exit: 0 = no mismatches, 1 = mismatches found, 2 = setup error

set -uo pipefail

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not installed"
  exit 2
fi

if ! command -v agent-browser &>/dev/null; then
  echo "ERROR: agent-browser not found. Install: npm i -g agent-browser"
  exit 2
fi

SESSION="${1:?Usage: computed-diff.sh <session> <orig-url> <impl-url> <selector1> ...}"
ORIG_URL="${2:?Missing orig-url}"
IMPL_URL="${3:?Missing impl-url}"
VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"
WAIT_MS="${WAIT_MS:-4000}"
IGNORE_FONT_SIZE="${IGNORE_FONT_SIZE:-0}"
shift 3

if [ $# -eq 0 ]; then
  echo "ERROR: provide at least one CSS selector"
  exit 1
fi

SELECTORS=("$@")

# Build JSON array of selectors using Python (avoids jq pipe + bash array expansion issues)
SELECTORS_JSON=$(python3 -c "import json, sys; print(json.dumps(sys.argv[1:]))" "${SELECTORS[@]}")

PROPS='["display","position","width","height","padding","margin","fontSize","fontWeight","fontFamily","lineHeight","letterSpacing","color","backgroundColor","borderRadius","border","boxShadow","opacity","transform","zIndex","gap","flexDirection","alignItems","justifyContent","gridTemplateColumns"]'

# JS that extracts computed styles for all selectors
EXTRACT_JS="(() => {
  const props = ${PROPS};
  const selectors = ${SELECTORS_JSON};
  const result = {};
  selectors.forEach(sel => {
    const el = document.querySelector(sel);
    if (!el) { result[sel] = null; return; }
    const s = getComputedStyle(el);
    const vals = {};
    props.forEach(p => { vals[p] = s[p]; });
    result[sel] = vals;
  });
  return JSON.stringify(result);
})()"

echo "═══ Computed Style Diff ═══"
echo "  orig: $ORIG_URL"
echo "  impl: $IMPL_URL"
echo "  selectors: ${#SELECTORS[@]}"
echo ""

SESSION_ORIG="${SESSION}-orig"
SESSION_IMPL="${SESSION}-impl"

# Temp files for style JSON (avoids heredoc interpolation issues with special chars)
# L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
TMP_ORIG="$(mktemp /tmp/computed-diff-orig-XXXXXX)"
mv "$TMP_ORIG" "${TMP_ORIG}.json"
TMP_ORIG="${TMP_ORIG}.json"
# L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
TMP_IMPL="$(mktemp /tmp/computed-diff-impl-XXXXXX)"
mv "$TMP_IMPL" "${TMP_IMPL}.json"
TMP_IMPL="${TMP_IMPL}.json"

cleanup() {
  agent-browser --session "$SESSION_ORIG" close >/dev/null 2>&1 || true
  agent-browser --session "$SESSION_IMPL" close >/dev/null 2>&1 || true
  rm -f "$TMP_ORIG" "$TMP_IMPL"
}
trap cleanup EXIT

# Open both in parallel sessions
agent-browser --session "$SESSION_ORIG" open "$ORIG_URL" >/dev/null 2>&1
agent-browser --session "$SESSION_IMPL" open "$IMPL_URL" >/dev/null 2>&1

agent-browser --session "$SESSION_ORIG" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1 || true
agent-browser --session "$SESSION_IMPL" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1 || true

# Wait for JS/CSS to settle
agent-browser --session "$SESSION_ORIG" wait "$WAIT_MS" >/dev/null 2>&1
agent-browser --session "$SESSION_IMPL" wait "$WAIT_MS" >/dev/null 2>&1

echo "  ▸ Extracting orig styles..."
agent-browser --session "$SESSION_ORIG" eval "$EXTRACT_JS" > "$TMP_ORIG" 2>&1

echo "  ▸ Extracting impl styles..."
agent-browser --session "$SESSION_IMPL" eval "$EXTRACT_JS" > "$TMP_IMPL" 2>&1

if [ ! -s "$TMP_ORIG" ] || [ ! -s "$TMP_IMPL" ]; then
  echo "ERROR: empty response from agent-browser eval (page not loaded?)"
  exit 2
fi

# Guard: if response is a JS error (SyntaxError, TypeError, etc.) rather than JSON, bail early
_check_js_error() {
  local f="$1" label="$2"
  local first
  first=$(head -1 "$f" 2>/dev/null || echo "")
  if echo "$first" | grep -qE '^(SyntaxError|TypeError|ReferenceError|Error:|Uncaught)'; then
    echo "ERROR: browser eval returned JS error for $label:"
    cat "$f"
    echo ""
    echo "  Hint: wrap eval in (async () => { ... })() if using await"
    return 1
  fi
  return 0
}
_check_js_error "$TMP_ORIG" "orig" || exit 2
_check_js_error "$TMP_IMPL" "impl" || exit 2

echo ""

# Resolve diagnosis.md path for context injection on mismatch
_SKILL_DIR="$(cd "$(dirname "$0")/../../ui-reverse-engineering" && pwd 2>/dev/null || echo "")"
export _COMPUTED_DIFF_DIAGNOSIS="$_SKILL_DIR/diagnosis.md"

# Compare with Python — reads style JSON from temp files (no shell interpolation of style data)
python3 - "$IGNORE_FONT_SIZE" "$TMP_ORIG" "$TMP_IMPL" << 'PYEOF'
import json, sys

ignore_font_size = sys.argv[1] == "1"
orig_path = sys.argv[2]
impl_path = sys.argv[3]

def parse_file(path):
    with open(path) as f:
        raw = f.read().strip()
    # agent-browser wraps string results in extra JSON quotes
    if raw.startswith('"') and raw.endswith('"'):
        return json.loads(json.loads(raw))
    return json.loads(raw)

try:
    orig = parse_file(orig_path)
except Exception as e:
    with open(orig_path) as f:
        snippet = f.read()[:200]
    print(f"ERROR: failed to parse orig styles: {e}")
    print(f"  Raw: {repr(snippet)}")
    sys.exit(2)

try:
    impl = parse_file(impl_path)
except Exception as e:
    with open(impl_path) as f:
        snippet = f.read()[:200]
    print(f"ERROR: failed to parse impl styles: {e}")
    print(f"  Raw: {repr(snippet)}")
    sys.exit(2)

# Properties where OS-level font scaling causes spurious diffs
FONT_SIZE_PROPS = {"fontSize", "lineHeight", "width", "height", "letterSpacing"}

import importlib, os

# Try to import severity from ui_clone.metrics (available when plugin is installed)
_severity_fn = None
_plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
if _plugin_root:
    sys.path.insert(0, _plugin_root)
try:
    from ui_clone.metrics import severity as _severity_fn
except ImportError:
    pass

def _sev(prop, ov, iv):
    if _severity_fn is None:
        return ""
    try:
        return _severity_fn(prop, ov, iv)
    except Exception:
        return ""

SEV_RANK = {"ok": 0, "minor": 0, "warn": 1, "critical": 2}
SEV_LABEL = {"ok": "", "warn": "🟡 minor", "critical": "🔴 critical"}

mismatches = 0
critical_count = 0
warn_count = 0
per_selector_worst = {}
print("| Selector | Property | Original | Implementation | Severity |")
print("|----------|----------|----------|----------------|----------|")

for sel in orig:
    if orig[sel] is None:
        if impl.get(sel) is None:
            continue
        print(f"| `{sel}` | — | NOT FOUND on orig | found on impl | |")
        mismatches += 1
        continue
    if impl.get(sel) is None:
        print(f"| `{sel}` | — | found on orig | NOT FOUND on impl | 🔴 critical |")
        mismatches += 1
        critical_count += 1
        per_selector_worst[sel] = "critical"
        continue

    for prop in orig[sel]:
        ov = orig[sel][prop]
        iv = impl.get(sel, {}).get(prop, "")
        if ov == iv:
            continue

        # Skip when both are semantically "unset"
        if ov in ("", "none", "normal", "auto") and iv in ("", "none", "normal", "auto"):
            continue

        # Skip border differences where only style keyword differs but width=0 (invisible either way)
        # e.g. "0px none rgb(...)" vs "0px solid rgb(...)" — both render as no border
        if prop == "border" and ov.startswith("0px") and iv.startswith("0px"):
            continue

        # Skip font-family ordering differences (first family matches)
        if prop == "fontFamily":
            ov_first = ov.split(",")[0].strip().strip('"\'')
            iv_first = iv.split(",")[0].strip().strip('"\'')
            if ov_first == iv_first:
                continue

        # Optionally skip font-size related diffs (OS text-scaling artifact)
        if ignore_font_size and prop in FONT_SIZE_PROPS:
            continue

        sev = _sev(prop, ov, iv)
        sev_label = SEV_LABEL.get(sev, "")
        print(f"| `{sel}` | {prop} | `{ov[:60]}` | `{iv[:60]}` | {sev_label} |")
        mismatches += 1
        if sev == "critical":
            critical_count += 1
        elif sev == "warn":
            warn_count += 1

        # Track worst severity per selector
        prev = per_selector_worst.get(sel, "ok")
        if SEV_RANK.get(sev, 0) > SEV_RANK.get(prev, 0):
            per_selector_worst[sel] = sev

# Summary
print(f"\n**{mismatches} mismatches found**")
if _severity_fn and mismatches > 0:
    print(f"  Severity: {critical_count} critical, {warn_count} warn, {mismatches - critical_count - warn_count} ok")
    if per_selector_worst:
        crit_sels = [s for s, v in per_selector_worst.items() if v == "critical"]
        if crit_sels:
            print(f"  ⛔ Fix these first: {', '.join(crit_sels[:5])}")
if mismatches > 0:
    print("")
    print("Fix priority:")
    print("  1. fontWeight  — Tailwind preflight resets h1-h6 to inherit (add `h1,h2,h3,h4,h5,h6 { font-weight: bold }` to global CSS)")
    print("  2. display     — Tailwind preflight sets `img { display: block }` (override with `display: inline !important`)")
    print("  3. height      — Tailwind sets `img { height: auto }` overriding HTML attribute (use inline style or `!important`)")
    print("  4. fontSize    — run with IGNORE_FONT_SIZE=1 to check if it is an OS text-scaling artifact")
    print("")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("▶ Root cause of computed-style diff is almost always one of:")
    print("  color/weight wrong  → Root Cause B (CSS Cascade) — grep host.css for ^button,^a,^select")
    print("  display/height diff → Root Cause C (Missing Wrapper) or Root Cause A (DOM Mismatch)")
    print("  font-family wrong   → Root Cause D (body scoping missing) or @theme file location")
    print("")

    import os, sys as _sys
    diagnosis_path = os.environ.get('_COMPUTED_DIFF_DIAGNOSIS', '')
    if diagnosis_path and os.path.exists(diagnosis_path):
        print("▶ ROOT CAUSE DIAGNOSIS COMMANDS:")
        with open(diagnosis_path) as f:
            content = f.read()
        # Print sections matching "## Root Cause"
        in_section = False
        lines_printed = 0
        for line in content.splitlines():
            if line.startswith('## Root Cause'):
                in_section = True
            elif line == '---' and in_section:
                break
            if in_section and lines_printed < 50:
                print(line)
                lines_printed += 1
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
sys.exit(0 if mismatches == 0 else 1)
PYEOF
