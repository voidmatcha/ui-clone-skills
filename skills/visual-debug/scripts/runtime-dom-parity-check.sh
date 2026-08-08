#!/usr/bin/env bash
# runtime-dom-parity-check.sh — positive-parity runtime gate.
#
#
# Four positive assertions:
#
#   1. DOM node count — impl within ±30% of ref (matches existing
#      30% precedent in dom-mirror-check / section-compare).
#
#   2. Visible text-node count — impl must have >= max(10, sections*2)
#      non-empty text nodes. A screenshot-overlay impl has ~0 because
#      the DOM is all hidden chrome + a single img/canvas/svg painting
#      the ref capture.
#
#   3. No single image / picture / video covers more than 90% of the
#      viewport area. A single full-viewport <img> = the screenshot
#      overlay cheat in its purest form.
#
#   4. Lottie-container parity — if dom-scaffold / external-sdks
#      shows ref has Lottie evidence, the impl must have at least one
#      mounted Lottie container (a div with `<svg>` or `<canvas>`
#      child injected by lottie-web) visible at runtime.
#
# Usage:
#   runtime-dom-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>
#
# Output: <ref-dir>/runtime-dom-parity.json
#
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

SESSION="${1:-}"
REF_URL="${2:-}"
IMPL_URL="${3:-}"
REF_DIR="${4:-}"

if [ -z "$SESSION" ] || [ -z "$REF_URL" ] || [ -z "$IMPL_URL" ] || [ -z "$REF_DIR" ]; then
  echo "Usage: runtime-dom-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>" >&2
  exit 2
fi

if [ ! -d "$REF_DIR" ]; then
  echo "ref-dir not found: $REF_DIR" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/runtime-dom-parity.json"

# Detect ref Lottie evidence — used as a positive-assertion gate
# trigger.
HAS_LOTTIE=0
if [ -d "$REF_DIR/bundles" ]; then
  if grep -Eiq 'lottie|bodymovin|dotlottie|lottie-player' "$REF_DIR"/bundles/*.js 2>/dev/null; then
    HAS_LOTTIE=1
  fi
fi
if [ -f "$REF_DIR/external-sdks.json" ]; then
  if grep -Eiq 'lottie|bodymovin|dotlottie' "$REF_DIR/external-sdks.json" 2>/dev/null; then
    HAS_LOTTIE=1
  fi
fi
if [ -f "$REF_DIR/required-media.json" ]; then
  if grep -q '"path"' "$REF_DIR/required-media.json" 2>/dev/null; then
    if python3 -c '
import json,sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if d.get("lottie") else 1)
' "$REF_DIR/required-media.json" 2>/dev/null; then
      HAS_LOTTIE=1
    fi
  fi
fi


# L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
REF_TMP="$(mktemp -t ref-dom-parity-XXXXXX)"
mv "$REF_TMP" "${REF_TMP}.json"
REF_TMP="${REF_TMP}.json"
# L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
IMPL_TMP="$(mktemp -t impl-dom-parity-XXXXXX)"
mv "$IMPL_TMP" "${IMPL_TMP}.json"
IMPL_TMP="${IMPL_TMP}.json"
trap 'rm -f "$REF_TMP" "$IMPL_TMP"' EXIT

ANALYSIS_JS='(() => {
  const root = document.body || document.documentElement;
  const allNodes = root.querySelectorAll("*");
  const skipText = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "META", "LINK"]);
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      const t = (n.nodeValue || "").trim();
      if (!t) return NodeFilter.FILTER_REJECT;
      const par = n.parentElement;
      if (par && skipText.has(par.tagName)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let textNodes = 0;
  while (tw.nextNode()) textNodes++;
  const vw = innerWidth * innerHeight;
  let maxArea = 0;
  let maxTag = "";
  let maxSrc = "";
  const checkArea = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    const area = r.width * r.height;
    if (area > maxArea) {
      maxArea = area;
      maxTag = el.tagName.toLowerCase();
      maxSrc = el.currentSrc || el.src || el.getAttribute("poster") || el.getAttribute("data-src") || "";
    }
  };
  document.querySelectorAll("img, picture img, video, canvas").forEach(checkArea);
  document.querySelectorAll("*").forEach((el) => {
    const bg = getComputedStyle(el).backgroundImage;
    if (bg && bg !== "none" && bg.includes("url(")) {
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return;
      const area = r.width * r.height;
      if (area > maxArea) {
        maxArea = area;
        maxTag = el.tagName.toLowerCase() + "[bg]";
        const m = bg.match(/url\(["\x27]?([^)"\x27]+)/);
        maxSrc = m ? m[1] : "";
      }
    }
  });
  let lottieMounted = 0;
  document.querySelectorAll("[data-lottie], .lottie, lottie-player, dotlottie-player").forEach(() => lottieMounted++);
  document.querySelectorAll("div").forEach((el) => {
    const id = (el.id || "").toLowerCase();
    const cls = (el.className && el.className.baseVal !== undefined) ? "" : String(el.className || "").toLowerCase();
    if (id.includes("lottie") || cls.includes("lottie")) {
      if (el.querySelector("svg") || el.querySelector("canvas")) lottieMounted++;
    }
  });
  // Universality audit FN: text-node count without visibility
  // check let a screenshot-overlay impl satisfy the floor by stuffing
  // hidden text. Re-count text inside ELEMENTS that have nonzero
  // rects and non-hidden style.
  const visTw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      const t = (n.nodeValue || "").trim();
      if (!t) return NodeFilter.FILTER_REJECT;
      const par = n.parentElement;
      if (!par || skipText.has(par.tagName)) return NodeFilter.FILTER_REJECT;
      const cs = getComputedStyle(par);
      if (cs.display === "none" || cs.visibility === "hidden" || parseFloat(cs.opacity || "1") <= 0.01) {
        return NodeFilter.FILTER_REJECT;
      }
      const r = par.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let visibleTextNodes = 0;
  while (visTw.nextNode()) visibleTextNodes++;
  // Opaque-overlay detection. A splash class can be preserved and styled yet
  // render as an opaque overlay covering everything; class-signature gates pass,
  // visual output is a solid color. Find fixed/absolute elements with high z-index,
  // covering >= 70% of viewport, opaque background, no media descendants.
  // If present, the page is rendering through a visual blocker.
  let opaqueOverlayCount = 0;
  let opaqueOverlaySample = [];
  const Z_FLOOR = 50;
  const VIEWPORT_FRACTION_FLOOR = 0.7;
  document.querySelectorAll("*").forEach((el) => {
    const cs = getComputedStyle(el);
    const pos = cs.position;
    if (pos !== "fixed" && pos !== "absolute") return;
    const z = parseInt(cs.zIndex || "0", 10);
    if (!Number.isFinite(z) || z < Z_FLOOR) return;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    const area = r.width * r.height;
    if (vw === 0 || area / vw < VIEWPORT_FRACTION_FLOOR) return;
    // Check opacity: own opacity * computed alpha of background-color
    const ownOpacity = parseFloat(cs.opacity || "1");
    if (ownOpacity < 0.85) return;
    const bgc = cs.backgroundColor || "";
    // Reject transparent backgrounds (they do not occlude)
    const rgbaMatch = bgc.match(/rgba?\(([^)]+)\)/i);
    let alpha = 1;
    if (rgbaMatch) {
      const parts = rgbaMatch[1].split(",").map(s => s.trim());
      if (parts.length === 4) alpha = parseFloat(parts[3]);
    } else if (bgc === "" || bgc === "transparent" || bgc === "rgba(0, 0, 0, 0)") {
      alpha = 0;
    }
    if (alpha < 0.85) return;
    // Reject if it contains media (real content, not overlay)
    if (el.querySelector("video, canvas, img, picture, iframe")) return;
    opaqueOverlayCount++;
    if (opaqueOverlaySample.length < 5) {
      opaqueOverlaySample.push({
        tag: el.tagName.toLowerCase(),
        id: (el.id || "").slice(0, 40),
        cls: String(el.className || "").slice(0, 80),
        zIndex: z,
        viewportFraction: Math.round((area / vw) * 1000) / 1000,
        bgc: bgc.slice(0, 60),
        opacity: ownOpacity,
      });
    }
  });
  // Universality fix: section count selector was too narrow (semantic-
  // only). Add nav/aside/header/footer plus large container div fallback
  // when semantic count < 3 (div-only layouts with no semantic tags).
  const semSecSel = "main,section,header,footer,article,nav,aside,[role=region],[role=banner],[role=contentinfo]";
  let semSec = [...document.querySelectorAll(semSecSel)];
  if (semSec.length < 3) {
    const seenS = new Set(semSec);
    document.querySelectorAll("body > div, main > div").forEach((d) => {
      if (seenS.has(d)) return;
      const r = d.getBoundingClientRect();
      if (r.width * r.height >= vw * 0.15 && d.children.length >= 2) {
        semSec.push(d);
      }
    });
  }
  return JSON.stringify({
    nodeCount: allNodes.length,
    textNodeCount: textNodes,
    visibleTextNodeCount: visibleTextNodes,
    viewportArea: vw,
    maxElementArea: Math.round(maxArea),
    maxElementRatio: vw ? Math.round((maxArea / vw) * 1000) / 1000 : 0,
    maxElementTag: maxTag,
    maxElementSrc: maxSrc.slice(0, 200),
    lottieMounted,
    sectionCount: semSec.length,
    opaqueOverlayCount,
    opaqueOverlaySample,
  });
})()'


run_capture() {
  local url="$1" out="$2" sess="$3"
  local open_status=0
  # Some reference pages keep network activity alive long enough for
  # agent-browser `open` to exit with a timeout even when the document has
  # loaded and is scriptable. Verify the page state instead of treating that
  # timeout as an automatic browser failure.
  agent-browser --session "$sess" open "$url" >/dev/null 2>&1 || open_status=$?
  agent-browser --session "$sess" wait 2500 >/dev/null 2>&1 || true
  local href=""
  href="$(agent-browser --session "$sess" eval '(() => location.href)()' 2>/dev/null || true)"
  if [ -z "$href" ] || printf '%s' "$href" | grep -Eiq 'about:blank'; then
    echo "{\"error\": \"open failed: $url\", \"openStatus\": $open_status}" > "$out"
    return 1
  fi
  # Scroll mid + bottom + top to surface lazily-mounted Lottie / IO
  # reveals.
  agent-browser --session "$sess" eval 'window.scrollTo(0, document.body.scrollHeight/2)' >/dev/null 2>&1 || true
  agent-browser --session "$sess" wait 600 >/dev/null 2>&1 || true
  agent-browser --session "$sess" eval 'window.scrollTo(0, document.body.scrollHeight)' >/dev/null 2>&1 || true
  agent-browser --session "$sess" wait 600 >/dev/null 2>&1 || true
  agent-browser --session "$sess" eval 'window.scrollTo(0, 0)' >/dev/null 2>&1 || true
  agent-browser --session "$sess" wait 400 >/dev/null 2>&1 || true
  agent-browser --session "$sess" eval "$ANALYSIS_JS" > "$out" 2>/dev/null || {
    echo "{\"error\": \"eval failed: $url\"}" > "$out"
    return 1
  }
  return 0
}


run_capture "$REF_URL"  "$REF_TMP"  "${SESSION}-ref"  || true
run_capture "$IMPL_URL" "$IMPL_TMP" "${SESSION}-impl" || true


python3 - "$REF_TMP" "$IMPL_TMP" "$OUT_PATH" "$REF_URL" "$IMPL_URL" "$HAS_LOTTIE" <<'PY'
import json
import re
import sys
from pathlib import Path

ref_path = Path(sys.argv[1])
impl_path = Path(sys.argv[2])
out_path = Path(sys.argv[3])
ref_url = sys.argv[4]
impl_url = sys.argv[5]
has_lottie = sys.argv[6] == "1"


def parse_eval_result(p: Path) -> dict:
    text = p.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return {"error": "empty"}
    # agent-browser eval often returns a JSON-encoded JSON string —
    # peel the outer string layer when present.
    try:
        first = json.loads(text)
        if isinstance(first, str):
            try:
                return json.loads(first)
            except ValueError:
                return {"error": "non-json browser result", "raw": first[:300]}
        if isinstance(first, dict):
            return first
        return {"error": "unexpected browser result shape", "raw": text[:300]}
    except ValueError:
        # Sometimes the runner wraps in `'"{...}"'` — try one strip.
        stripped = text.strip("'\"")
        try:
            return json.loads(stripped)
        except ValueError:
            return {"error": "unparseable", "raw": text[:300]}


ref_data = parse_eval_result(ref_path)
impl_data = parse_eval_result(impl_path)


violations: list[dict] = []
if "error" in ref_data:
    violations.append({"kind": "ref-eval-failed", "detail": ref_data["error"]})
if "error" in impl_data:
    violations.append({"kind": "impl-eval-failed", "detail": impl_data["error"]})


if "error" not in ref_data and "error" not in impl_data:
    ref_nodes = int(ref_data.get("nodeCount") or 0)
    impl_nodes = int(impl_data.get("nodeCount") or 0)
    if ref_nodes > 0:
        ratio = impl_nodes / ref_nodes
        if ratio < 0.70 or ratio > 1.30:
            violations.append({
                "kind": "dom-node-count-outside-tolerance",
                "ref": ref_nodes,
                "impl": impl_nodes,
                "ratio": round(ratio, 3),
                "tolerance": "70%-130%",
            })

    section_count = int(impl_data.get("sectionCount") or 0)
    min_text_nodes = max(10, section_count * 2)
    # Universality audit FN: count VISIBLE text nodes (style+
    # geometry filtered), not the raw walker count. Falls back to the
    # raw count if the visibility-filtered field is absent (older
    # artifact format).
    impl_text = int(
        impl_data.get("visibleTextNodeCount")
        or impl_data.get("textNodeCount")
        or 0
    )
    if impl_text < min_text_nodes:
        violations.append({
            "kind": "insufficient-visible-text-nodes",
            "impl": impl_text,
            "required": min_text_nodes,
            "detail": (
                "impl has near-zero rendered text — likely a "
                "screenshot/canvas overlay instead of real DOM"
            ),
        })

    max_ratio = float(impl_data.get("maxElementRatio") or 0.0)
    if max_ratio > 0.90:
        # Universality audit FP: a legitimate full-viewport hero
        # image / video / background-image can cover >90% by design.
        # Require AT LEAST ONE corroborating signal before failing:
        #   (a) DOM node count well below ref (<=50% of ref), OR
        #   (b) visible text-node count below the floor, OR
        #   (c) ref node count is high but impl is tiny
        # If max_ratio fails alone, downgrade to "warn" by not adding
        # the violation; the gate.py status=warn honor logic surfaces
        # it but doesn't block.
        ref_nodes_for_corr = int(ref_data.get("nodeCount") or 0)
        impl_nodes_for_corr = int(impl_data.get("nodeCount") or 0)
        node_dropout = (
            ref_nodes_for_corr > 0
            and impl_nodes_for_corr < ref_nodes_for_corr * 0.50
        )
        text_dropout = impl_text < min_text_nodes
        corroborated = node_dropout or text_dropout
        if corroborated:
            violations.append({
                "kind": "single-element-dominates-viewport",
                "ratio": max_ratio,
                "tag": impl_data.get("maxElementTag"),
                "src": impl_data.get("maxElementSrc"),
                "corroboration": {
                    "nodeDropout": node_dropout,
                    "textDropout": text_dropout,
                },
                "detail": (
                    "one element covers >90% of viewport AND impl has "
                    "low DOM/text density — screenshot-overlay cheat"
                ),
            })

    if has_lottie and int(ref_data.get("lottieMounted") or 0) > 0:
        impl_lottie = int(impl_data.get("lottieMounted") or 0)
        if impl_lottie < 1:
            violations.append({
                "kind": "ref-has-lottie-impl-has-no-lottie-container",
                "impl": impl_lottie,
                "detail": (
                    "ref evidence shows Lottie/bodymovin but impl has zero "
                    "mounted Lottie containers (no [data-lottie], no "
                    "lottie-player, no .lottie div with svg/canvas child)"
                ),
            })

    # Opaque-overlay occlusion. Splash classes can be preserved and styled
    # while rendering as an opaque div covering the viewport, producing a
    # solid-color visual despite healthy class metrics. Pair the
    # browser-eval count with a corroborator (impl text dropout vs ref)
    # so we don't false-positive on intentional modals / cookie banners.
    impl_overlay_count = int(impl_data.get("opaqueOverlayCount") or 0)
    if impl_overlay_count > 0:
        ref_overlay_count = int(ref_data.get("opaqueOverlayCount") or 0)
        # Corroborate by checking whether impl text density is well
        # below ref. An overlay is "blocking content" if ref has lots
        # of visible text and impl has near-none.
        ref_visible_text = int(
            ref_data.get("visibleTextNodeCount")
            or ref_data.get("textNodeCount")
            or 0
        )
        impl_visible_text = int(
            impl_data.get("visibleTextNodeCount")
            or impl_data.get("textNodeCount")
            or 0
        )
        text_dropout_severe = (
            ref_visible_text >= 30
            and impl_visible_text < ref_visible_text * 0.4
        )
        # Or: impl has overlay but ref has zero (asymmetric — impl
        # invented a blocker the ref doesn't have)
        asymmetric = impl_overlay_count > 0 and ref_overlay_count == 0
        if text_dropout_severe or asymmetric:
            violations.append({
                "kind": "opaque-overlay-occludes-content",
                "implOverlayCount": impl_overlay_count,
                "refOverlayCount": ref_overlay_count,
                "implVisibleText": impl_visible_text,
                "refVisibleText": ref_visible_text,
                "sample": impl_data.get("opaqueOverlaySample", []),
                "corroboration": {
                    "textDropoutSevere": text_dropout_severe,
                    "asymmetricVsRef": asymmetric,
                },
                "detail": (
                    f"impl renders {impl_overlay_count} opaque overlay(s) "
                    f"(position:fixed/absolute, z-index>=50, >=70% of viewport, "
                    f"opacity>=0.85, no media descendants). "
                    + (
                        f"Ref has none — impl invented a blocker. "
                        if asymmetric
                        else f"Ref visible text={ref_visible_text}, impl visible text={impl_visible_text} (<40% of ref). "
                    )
                    + "Class signatures are preserved and styled, but "
                    "the splash/intro overlay covers content. Sample below."
                ),
            })


status = "fail" if violations else "pass"
result = {
    "schemaVersion": 1,
    "status": status,
    "refUrl": ref_url,
    "implUrl": impl_url,
    "hasLottieEvidence": has_lottie,
    "refMetrics": ref_data,
    "implMetrics": impl_data,
    "violations": violations,
    "rule": (
        "Impl runtime DOM must match ref runtime DOM along five axes: "
        "(1) node count within ±30%, (2) >= max(10, sectionCount*2) "
        "visible text nodes, (3) no single image/video/background element "
        "covering >90% of viewport, (4) >= 1 Lottie container mounted when "
        "ref mounts a Lottie container at runtime, (5) no opaque fixed/absolute overlay "
        "(z-index>=50, >=70% viewport, opacity>=0.85, no media descendants) "
        "occluding content (corroborated by impl-text-dropout or ref-has-none-impl-has-some)."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"runtime-dom-parity: {len(violations)} violation(s) → {status} "
    f"({out_path})"
)
sys.exit(0 if status == "pass" else 1)
PY
