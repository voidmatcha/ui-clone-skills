#!/usr/bin/env bash
# blank-viewport-check.sh — fail when the impl has DOM/text but renders invisible.
#
# Catches copied-loader / forensic-CSS failures such as `body { opacity: 0 }`
# where selectors still find elements but the viewport is blank because the
# original site's JS/class unlock was not reproduced.
#
# Usage:
#   blank-viewport-check.sh <session> <impl-url> <ref-dir>
#
# Output: <ref-dir>/blank-viewport.json
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

SESSION="${1:-}"
IMPL_URL="${2:-}"
REF_DIR="${3:-}"

if [ -z "$SESSION" ] || [ -z "$IMPL_URL" ] || [ -z "$REF_DIR" ]; then
  echo "Usage: blank-viewport-check.sh <session> <impl-url> <ref-dir>" >&2
  exit 2
fi
if [ ! -d "$REF_DIR" ]; then
  echo "ref-dir not found: $REF_DIR" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/blank-viewport.json"
# L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
EVAL_OUT="$(mktemp -t blank-viewport-XXXXXX)"
mv "$EVAL_OUT" "${EVAL_OUT}.json"
EVAL_OUT="${EVAL_OUT}.json"
trap 'rm -f "$EVAL_OUT"' EXIT

write_browser_fail() {
  local kind="$1" detail="$2"
  python3 - "$OUT_PATH" "$IMPL_URL" "$kind" "$detail" <<'PY'
import json
import sys
from pathlib import Path
out_path = Path(sys.argv[1])
impl_url = sys.argv[2]
kind = sys.argv[3]
detail = sys.argv[4]
payload = {
    "schemaVersion": 1,
    "status": "fail",
    "implUrl": impl_url,
    "reasons": [{"kind": kind, "detail": detail}],
    "rule": "Impl must render visible content; browser probe could not verify first paint.",
}
out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"blank-viewport: fail ({kind}) -> {out_path}")
PY
}

agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1 || {
  write_browser_fail "browser-open-failed" "$IMPL_URL"
  exit 1
}
agent-browser --session "$SESSION" wait 2500 >/dev/null 2>&1 || true

EVAL_JS='(() => {
  const ROOT_SELECTORS = ["html", "body", "#root", "#__next", "#app", "[data-reactroot]"];
  const SKIP_TEXT = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "META", "LINK"]);
  const stateFor = (el, selector) => {
    if (!el) return { selector, present: false };
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return {
      selector,
      present: true,
      tag: el.tagName.toLowerCase(),
      id: el.id || "",
      className: String(el.className || "").slice(0, 120),
      display: cs.display,
      visibility: cs.visibility,
      opacity: Number.parseFloat(cs.opacity || "1"),
      width: Math.round(r.width),
      height: Math.round(r.height),
    };
  };
  const roots = ROOT_SELECTORS
    .map((sel) => stateFor(document.querySelector(sel), sel))
    .filter((s, idx, arr) => s.present && arr.findIndex((o) => o.present && o.tag === s.tag && o.id === s.id) === idx);

  const hiddenOwnReasons = (el, selector) => {
    if (!el) return [];
    const cs = getComputedStyle(el);
    const out = [];
    if (cs.display === "none") out.push({ selector, kind: "display-none" });
    if (cs.visibility === "hidden" || cs.visibility === "collapse") {
      out.push({ selector, kind: "visibility-hidden", value: cs.visibility });
    }
    const opacity = Number.parseFloat(cs.opacity || "1");
    if (Number.isFinite(opacity) && opacity <= 0.01) {
      out.push({ selector, kind: "opacity-zero", value: opacity });
    }
    return out;
  };

  const topLevelHidden = [];
  for (const sel of ROOT_SELECTORS) {
    const el = document.querySelector(sel);
    for (const r of hiddenOwnReasons(el, sel)) topLevelHidden.push(r);
  }

  const effective = (el) => {
    let cur = el;
    const chain = [];
    let opacityProduct = 1;
    while (cur && cur.nodeType === 1) {
      const cs = getComputedStyle(cur);
      const selector = cur === document.documentElement ? "html" : cur === document.body ? "body" : (cur.id ? `#${cur.id}` : cur.tagName.toLowerCase());
      if (cs.display === "none") return { visible: false, reason: "ancestor-display-none", chain: [...chain, selector] };
      if (cs.visibility === "hidden" || cs.visibility === "collapse") return { visible: false, reason: "ancestor-visibility-hidden", chain: [...chain, selector] };
      const op = Number.parseFloat(cs.opacity || "1");
      if (Number.isFinite(op)) opacityProduct *= op;
      if (opacityProduct <= 0.01) return { visible: false, reason: "ancestor-opacity-zero", chain: [...chain, selector], opacityProduct };
      chain.push(selector);
      cur = cur.parentElement;
    }
    return { visible: true, opacityProduct };
  };

  const body = document.body || document.documentElement;
  const allElements = [...body.querySelectorAll("*")];
  const tw = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      const text = (n.nodeValue || "").trim();
      if (!text) return NodeFilter.FILTER_REJECT;
      const parent = n.parentElement;
      if (!parent || SKIP_TEXT.has(parent.tagName)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let rawTextNodes = 0;
  let rawTextChars = 0;
  let visibleTextNodes = 0;
  let visibleTextChars = 0;
  const invisibleTextSamples = [];
  while (tw.nextNode()) {
    const n = tw.currentNode;
    const text = (n.nodeValue || "").trim();
    rawTextNodes += 1;
    rawTextChars += text.length;
    const parent = n.parentElement;
    const eff = effective(parent);
    const pr = parent.getBoundingClientRect();
    const visible = eff.visible && pr.width >= 2 && pr.height >= 2;
    if (visible) {
      visibleTextNodes += 1;
      visibleTextChars += text.length;
    } else if (invisibleTextSamples.length < 6) {
      invisibleTextSamples.push({
        text: text.slice(0, 80),
        tag: parent.tagName.toLowerCase(),
        id: parent.id || "",
        className: String(parent.className || "").slice(0, 80),
        reason: eff.reason || "zero-rect",
        chain: eff.chain || [],
      });
    }
  }

  let paintableElements = 0;
  const paintableSamples = [];
  const viewportArea = Math.max(1, innerWidth * innerHeight);
  const isTransparent = (color) => {
    if (!color || color === "transparent") return true;
    const m = color.match(/rgba?\(([^)]+)\)/i);
    if (!m) return false;
    const parts = m[1].split(",").map((p) => p.trim());
    return parts.length === 4 && Number.parseFloat(parts[3]) <= 0.01;
  };
  for (const el of allElements) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const eff = effective(el);
    if (!eff.visible) continue;
    const cs = getComputedStyle(el);
    const hasMedia = /^(IMG|SVG|CANVAS|VIDEO|IFRAME)$/.test(el.tagName);
    const hasBg = !isTransparent(cs.backgroundColor) || (cs.backgroundImage && cs.backgroundImage !== "none");
    const hasBorder = [cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth]
      .some((v) => Number.parseFloat(v || "0") > 0);
    const hasText = (el.textContent || "").trim().length > 0;
    if (hasMedia || hasBg || hasBorder || hasText) {
      paintableElements += 1;
      if (paintableSamples.length < 6) {
        paintableSamples.push({
          tag: el.tagName.toLowerCase(),
          id: el.id || "",
          className: String(el.className || "").slice(0, 80),
          areaRatio: Math.round(((r.width * r.height) / viewportArea) * 1000) / 1000,
        });
      }
    }
  }

  const bg = getComputedStyle(body).backgroundColor || getComputedStyle(document.documentElement).backgroundColor || "";
  return JSON.stringify({
    url: location.href,
    title: document.title,
    viewport: { width: innerWidth, height: innerHeight },
    rootStates: roots,
    topLevelHidden,
    domNodeCount: allElements.length,
    rawTextNodes,
    rawTextChars,
    visibleTextNodes,
    visibleTextChars,
    paintableElements,
    paintableSamples,
    invisibleTextSamples,
    bodyBackground: bg,
  });
})()'

agent-browser --session "$SESSION" eval "$EVAL_JS" > "$EVAL_OUT" 2>/dev/null || {
  write_browser_fail "browser-eval-failed" "$IMPL_URL"
  exit 1
}

python3 - "$EVAL_OUT" "$OUT_PATH" "$IMPL_URL" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

eval_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
impl_url = sys.argv[3]


def parse_eval_result(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not raw:
        return {"error": "empty browser output"}
    try:
        first = json.loads(raw)
        if isinstance(first, str):
            try:
                return json.loads(first)
            except ValueError:
                return {"error": "non-json browser result", "raw": first[:500]}
        if isinstance(first, dict):
            return first
        return {"error": "unexpected browser result shape", "raw": raw[:500]}
    except ValueError:
        stripped = raw.strip("'\"")
        try:
            return json.loads(stripped)
        except ValueError:
            return {"error": "unparseable browser output", "raw": raw[:500]}


data = parse_eval_result(eval_path)
reasons: list[dict] = []
if "error" in data:
    reasons.append({"kind": "browser-eval-unparseable", "detail": data.get("error"), "raw": data.get("raw", "")})
else:
    dom_nodes = int(data.get("domNodeCount") or 0)
    raw_text_nodes = int(data.get("rawTextNodes") or 0)
    raw_text_chars = int(data.get("rawTextChars") or 0)
    visible_text_nodes = int(data.get("visibleTextNodes") or 0)
    visible_text_chars = int(data.get("visibleTextChars") or 0)
    paintable = int(data.get("paintableElements") or 0)
    top_hidden = data.get("topLevelHidden") or []
    has_content_dom = dom_nodes >= 10 or raw_text_chars >= 40 or raw_text_nodes >= 3

    if top_hidden and has_content_dom:
        reasons.append({
            "kind": "top-level-hidden-with-dom",
            "detail": "html/body/root is hidden while DOM/text exists; likely copied loader CSS without runtime unlock",
            "topLevelHidden": top_hidden,
            "domNodeCount": dom_nodes,
            "rawTextChars": raw_text_chars,
        })

    if raw_text_chars >= 120 and visible_text_chars == 0:
        reasons.append({
            "kind": "all-text-invisible",
            "detail": "Text nodes exist but none are effectively visible after ancestor opacity/display/visibility checks",
            "rawTextNodes": raw_text_nodes,
            "rawTextChars": raw_text_chars,
            "visibleTextNodes": visible_text_nodes,
            "visibleTextChars": visible_text_chars,
            "samples": data.get("invisibleTextSamples", []),
        })

    if has_content_dom and paintable == 0:
        reasons.append({
            "kind": "no-paintable-elements",
            "detail": "DOM exists but no effectively visible paintable elements were found in the first viewport",
            "domNodeCount": dom_nodes,
            "rawTextChars": raw_text_chars,
        })

status = "fail" if reasons else "pass"
payload = {
    "schemaVersion": 1,
    "status": status,
    "implUrl": impl_url,
    "metrics": data,
    "reasons": reasons,
    "rule": (
        "The implementation must produce a visible first paint. If DOM/text exists, "
        "html/body/#root/#__next/#app must not remain display:none, visibility:hidden, "
        "or opacity<=0.01; visible text accounting includes ancestor opacity so copied "
        "loader CSS such as body{opacity:0} fails until the original ready/unlock state "
        "is reproduced locally."
    ),
}
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"blank-viewport: {status} reasons={len(reasons)} visibleText={data.get('visibleTextChars') if isinstance(data, dict) else '?'} -> {out_path}")
sys.exit(0 if status == "pass" else 1)
PY
