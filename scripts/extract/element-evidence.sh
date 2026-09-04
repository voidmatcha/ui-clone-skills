#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: element-evidence.sh <agent-browser-session> <css-selector> <output-json>" >&2
  exit 2
fi

SESSION="$1"
SELECTOR="$2"
OUT="$3"

SELECTOR_JSON="$(python3 - "$SELECTOR" <<'PY'
import json
import sys

print(json.dumps(sys.argv[1]))
PY
)"

EVAL_JS=""
read -r -d '' EVAL_JS <<JS || true
(() => {
  const selector = ${SELECTOR_JSON};
  const escapeCss = (value) => {
    const text = String(value);
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(text);
    }
    return text.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  };
  const attrSelector = (name, value) => "[" + name + "=\"" + escapeCss(value) + "\"]";
  const truncateValue = (value, limit = 180) => {
    const text = String(value || "");
    return text.length > limit ? text.slice(0, limit) + "…" : text;
  };
  const selectorCandidatesFor = (node) => {
    const candidates = [selector];
    const id = node.getAttribute("id");
    if (id) {
      candidates.push("#" + escapeCss(id));
    }
    for (const name of ["data-testid", "data-test", "data-qa", "data-component", "data-ui", "data-id"]) {
      const value = node.getAttribute(name);
      if (value && value.length <= 80) {
        candidates.push(attrSelector(name, value));
      }
    }
    const role = node.getAttribute("role");
    const label = node.getAttribute("aria-label");
    if (role) {
      candidates.push(attrSelector("role", role));
      if (label) {
        if (label.length <= 120) {
          candidates.push(attrSelector("role", role) + attrSelector("aria-label", label));
        }
      }
    }

    const parts = [];
    let current = node;
    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.documentElement) {
      let part = current.tagName.toLowerCase();
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((child) => child.tagName === current.tagName);
        if (siblings.length > 1) {
          part += ":nth-of-type(" + (siblings.indexOf(current) + 1) + ")";
        }
      }
      parts.unshift(part);
      current = parent;
      if (parts.length >= 8) {
        break;
      }
    }
    if (parts.length > 0) {
      candidates.push(parts.join(" > "));
    }
    return Array.from(new Set(candidates)).slice(0, 8);
  };
  const styleAllowlist = [
    "display", "position", "inset", "top", "right", "bottom", "left",
    "width", "height", "minWidth", "minHeight", "maxWidth", "maxHeight",
    "margin", "padding", "boxSizing", "overflow", "zIndex",
    "flex", "flexDirection", "alignItems", "justifyContent", "gap",
    "gridTemplateColumns", "gridTemplateRows", "gridColumn", "gridRow",
    "fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing",
    "textAlign", "color", "background", "backgroundColor", "backgroundImage",
    "border", "borderRadius", "boxShadow", "opacity", "transform",
    "transformOrigin", "filter", "mixBlendMode",
    "transitionProperty", "transitionDuration", "transitionTimingFunction",
    "transitionDelay", "animationName", "animationDuration",
    "animationTimingFunction", "animationDelay", "animationIterationCount",
    "animationFillMode"
  ];
  const element = document.querySelector(selector);
  if (!element) {
    return {
      schemaVersion: 1,
      ok: false,
      url: location.href,
      selector,
      error: "selector not found"
    };
  }
  const rect = element.getBoundingClientRect();
  const computed = getComputedStyle(element);
  const computedStyle = {};
  for (const key of styleAllowlist) {
    computedStyle[key] = truncateValue(computed[key] || "", 180);
  }
  const attributes = {};
  for (const attr of Array.from(element.attributes || [])) {
    if (
      ["id", "class", "role", "aria-label", "href", "src", "alt", "title"].includes(attr.name) ||
      attr.name.startsWith("data-")
    ) {
      attributes[attr.name] = truncateValue(attr.value, attr.name === "class" ? 240 : 180);
    }
  }
  const animations = document.getAnimations()
    .filter((animation) => {
      const target = animation.effect && animation.effect.target;
      return target === element || (target instanceof Element && (element.contains(target) || target.contains(element)));
    })
    .slice(0, 20)
    .map((animation) => ({
      playState: animation.playState,
      currentTime: animation.currentTime,
      playbackRate: animation.playbackRate,
      effectTiming: animation.effect && animation.effect.getTiming ? animation.effect.getTiming() : null
    }));
  return {
    schemaVersion: 1,
    ok: true,
    url: location.href,
    annotation: {
      id: "element-probe",
      selector,
      selectorCandidates: selectorCandidatesFor(element),
      text: (element.innerText || element.textContent || "").trim().slice(0, 500),
      bbox: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height)
      },
      attributes,
      computedStyle,
      timeline: animations.length > 0 ? [{ phase: "idle", changed: true }] : [],
      animations
    }
  };
})()
JS

RAW_FILE="$(mktemp)"
trap 'rm -f "$RAW_FILE"' EXIT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORIGIN_VALIDATOR="$SCRIPT_DIR/validate-agent-browser-origin.py"

if ! agent-browser --session "$SESSION" eval --json "$EVAL_JS" >"$RAW_FILE"; then
  echo "element-evidence: agent-browser eval failed (session=$SESSION)" >&2
  exit 3
fi
if ! python3 "$ORIGIN_VALIDATOR" < "$RAW_FILE"; then
  echo "element-evidence: agent-browser eval returned a non-page origin (session=$SESSION)" >&2
  exit 3
fi

python3 - "$RAW_FILE" "$OUT" <<'PY'
import json
import sys
from pathlib import Path

raw_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
raw = raw_path.read_text(encoding="utf-8").strip()

try:
    payload = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit(f"element-evidence: invalid JSON from agent-browser: {exc}") from exc

if isinstance(payload, dict) and "data" in payload:
    data = payload.get("data")
    if isinstance(data, dict) and "result" in data:
        payload = data["result"]

if isinstance(payload, str):
    payload = json.loads(payload)

if not isinstance(payload, dict):
    raise SystemExit("element-evidence: browser result must be a JSON object")

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(out_path)
PY
