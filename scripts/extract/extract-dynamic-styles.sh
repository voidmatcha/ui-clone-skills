#!/usr/bin/env bash
# extract-dynamic-styles.sh — Classify inline styles as layout vs animation
# Usage: bash extract-dynamic-styles.sh <session> <output-dir>
#
# Captures all inline styles from the page and classifies them:
# - LAYOUT: height, width, min-height (svh/vh/px units) — must be preserved
# - ANIMATION: transform, opacity, visibility, translate, rotate, scale — should be removed
#
# Output: <dir>/dynamic-styles.json with classification per element

set -euo pipefail

SESSION="${1:?Usage: extract-dynamic-styles.sh <session> <output-dir>}"
DIR="${2:?Usage: extract-dynamic-styles.sh <session> <output-dir>}"
_RESULT_TMP=""
_cleanup() {
  [ -n "$_RESULT_TMP" ] && rm -f "$_RESULT_TMP" 2>/dev/null
  agent-browser --session "$SESSION" close 2>/dev/null || true
}
trap _cleanup EXIT

mkdir -p "$DIR"

echo "═══ Extract Dynamic Styles ═══"

RESULT=$(agent-browser --session "$SESSION" eval "(() => {
  const elements = [];
  document.querySelectorAll('[style]').forEach(el => {
    const style = el.getAttribute('style');
    if (!style || style.trim() === '') return;

    const cn = typeof el.className === 'string' ? el.className : el.className?.baseVal || '';
    const id = el.id || '';
    const selector = id ? '#' + id : el.tagName.toLowerCase() + '.' + cn.trim().split(/\s+/).slice(0, 2).join('.');

    // Parse individual properties
    const props = {};
    style.split(';').forEach(decl => {
      const [prop, ...valParts] = decl.split(':');
      if (!prop || !valParts.length) return;
      const p = prop.trim();
      const v = valParts.join(':').trim();
      if (!p || !v) return;
      props[p] = v;
    });

    // Classify each property
    const layout = {};
    const animation = {};
    const unknown = {};

    Object.entries(props).forEach(([prop, val]) => {
      // Layout properties (KEEP)
      if (/^(height|width|min-height|min-width|max-height|max-width)$/.test(prop) &&
          /\d+(svh|vh|vw|px|rem|em|%)/.test(val)) {
        layout[prop] = val;
      }
      // Animation properties (REMOVE — will be re-set by JS; translate|rotate|scale are GSAP shorthand, already covered here)
      else if (/^(transform|opacity|visibility|translate|rotate|scale)$/.test(prop)) {
        animation[prop] = val;
      }
      // transform-origin is usually layout (KEEP)
      else if (prop === 'transform-origin') {
        layout[prop] = val;
      }
      // pointer-events set by JS (sometimes layout, sometimes animation)
      else if (prop === 'pointer-events') {
        animation[prop] = val;
      }
      // z-index is layout (KEEP)
      else if (prop === 'z-index') {
        layout[prop] = val;
      }
      else {
        unknown[prop] = val;
      }
    });

    if (Object.keys(layout).length > 0 || Object.keys(animation).length > 0) {
      elements.push({
        selector: selector.slice(0, 100),
        id: id || undefined,
        layout: Object.keys(layout).length > 0 ? layout : undefined,
        animation: Object.keys(animation).length > 0 ? animation : undefined,
        unknown: Object.keys(unknown).length > 0 ? unknown : undefined,
      });
    }
  });

  return JSON.stringify({
    total: elements.length,
    layoutCount: elements.filter(e => e.layout).length,
    animationCount: elements.filter(e => e.animation).length,
    elements: elements,
  });
})()" 2>&1)

# Write raw result to temp file to avoid heredoc injection from page content
_RESULT_TMP=$(mktemp)
printf '%s' "$RESULT" > "$_RESULT_TMP"

# Parse and save
python3 - "$_RESULT_TMP" "$DIR/dynamic-styles.json" << 'PYEOF'
import json, sys

raw_file, out_file = sys.argv[1], sys.argv[2]
with open(raw_file, encoding="utf-8", errors="replace") as f:
    raw = f.read().strip()

try:
    data = json.loads(json.loads(raw)) if raw.startswith('"') else json.loads(raw)
except Exception:
    data = {"error": "parse failed", "raw": raw[:500]}

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)

print(f'Total elements with inline styles: {data.get("total", 0)}')
print(f'  Layout values (KEEP): {data.get("layoutCount", 0)}')
print(f'  Animation values (REMOVE): {data.get("animationCount", 0)}')
print()
print("Layout values to preserve:")
for el in data.get("elements", [])[:10]:
    if el.get("layout"):
        print(f'  {el["selector"]}: {el["layout"]}')
PYEOF

rm -f "$_RESULT_TMP"

echo ""
echo "Saved to $DIR/dynamic-styles.json"
echo "Use this to decide which inline styles to keep vs remove when cleaning GSAP artifacts."
