#!/usr/bin/env bash
# live-parity-sweep.sh — dual-session live comparison of ref vs impl.
#
# Opens BOTH sites side by side and sweeps matched scroll depths, collecting a
# deterministic DOM census diff plus advisory full-frame AE/dssim numbers.
# Before browser work, local impl URLs are guarded against stale-port/cwd
# mismatches. During browser work, dynamic carousels/animations are pinned by
# default so repeated live-current compares are deterministic; set
# UI_CLONE_LIVE_CURRENT_MODE=snapshot to disable pinning.
# Complements section-compare: cropped+masked section verdicts can hide real
# defects under dynamic-region masks (mask covers carousel noise AND layout
# breakage alike); this sweep catches generic masking-hidden defects —
# missing/extra images (incl. percent-encoded filenames), oversized stray
# text, accessibility-only copy rendered visibly, synthetic/native pseudo
# duplication, and global geometry drift.
#
# Deterministic DOM census findings are gate evidence: missing/extra images,
# broken images, stray/accessibility-only text, pseudo duplication, and
# geometry/count drift exit non-zero.
# Full-frame AE/dssim depths remain advisory because live-site dynamics make
# those thresholds unreliable as hard gates.
#
# Usage:
#   bash live-parity-sweep.sh <ref-url> <impl-url> <session> [output-dir] [depths]
#     depths: comma-separated scroll Y list (default "0,1500,3000,4500,6000")
#
# Output:
#   <output-dir>/live-parity.json  (+ screenshot pairs under <output-dir>/live-parity/)

set -uo pipefail

REF_URL="${1:?Usage: live-parity-sweep.sh <ref-url> <impl-url> <session> [output-dir] [depths]}"
IMPL_URL="${2:?Missing impl-url}"
SESSION="${3:?Missing session}"
DIR="${4:-tmp/ref/visual-debug}"
DEPTHS="${5:-0,1500,3000,4500,6000}"
VIEW_W="${VIEW_W:-1280}"
VIEW_H="${VIEW_H:-800}"
LIVE_CURRENT_MODE="${UI_CLONE_LIVE_CURRENT_MODE:-pin}"

case "$REF_URL" in http://*|https://*) ;; *) echo "live-parity-sweep: <ref-url> must be http(s)://… (got: $REF_URL)" >&2; exit 2 ;; esac
case "$IMPL_URL" in http://*|https://*) ;; *) echo "live-parity-sweep: <impl-url> must be http(s)://… (got: $IMPL_URL)" >&2; exit 2 ;; esac
case "$SESSION" in *[!A-Za-z0-9._-]*|"") echo "live-parity-sweep: <session> must be a slug (got: $SESSION)" >&2; exit 2 ;; esac

if [[ "$DIR" != /* ]]; then DIR="$(pwd)/$DIR"; fi
SHOTS="$DIR/live-parity"
mkdir -p "$SHOTS"
S_REF="${SESSION}-lp-ref"
S_IMPL="${SESSION}-lp-impl"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}}}"
IMPL_URL_GUARD="$REPO_ROOT/scripts/verify/impl-url-guard.sh"
if [ -f "$IMPL_URL_GUARD" ]; then
  if ! bash "$IMPL_URL_GUARD" "$DIR" "$IMPL_URL"; then
    echo "live-parity-sweep: impl-url guard failed; refusing to compare against a potentially stale local server" >&2
    exit 1
  fi
fi

cleanup() {
  agent-browser --session "$S_REF" close 2>/dev/null || true
  agent-browser --session "$S_IMPL" close 2>/dev/null || true
}
trap cleanup EXIT

agent-browser --session "$S_REF" open "$REF_URL" 2>&1 | head -1 || true
agent-browser --session "$S_IMPL" open "$IMPL_URL" 2>&1 | head -1 || true
agent-browser --session "$S_REF" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1
agent-browser --session "$S_IMPL" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1
for s in "$S_REF" "$S_IMPL"; do
  if ! agent-browser --session "$s" eval '(() => 1)()' >/dev/null 2>&1; then
    echo "live-parity-sweep: session '$s' failed to start" >&2
    exit 1
  fi
done
sleep 2

DYNAMIC_PIN_JS='(() => {
  const actions = [];
  const addStyle = () => {
    if (document.getElementById("__ui_clone_dynamic_pin_style")) return;
    const style = document.createElement("style");
    style.id = "__ui_clone_dynamic_pin_style";
    style.textContent = `
      *, *::before, *::after {
        animation-play-state: paused !important;
        transition-duration: 0s !important;
        scroll-behavior: auto !important;
      }
      video { animation-play-state: paused !important; }
    `;
    document.head.appendChild(style);
    actions.push("css-animation-transition-freeze");
  };
  const pauseMedia = () => {
    document.querySelectorAll("video, audio").forEach((el) => {
      try { el.pause(); actions.push(`${el.tagName.toLowerCase()}:pause`); } catch (_) {}
    });
  };
  const pinSwiper = () => {
    document.querySelectorAll(".swiper, .swiper-container, .swiper-wrapper").forEach((el) => {
      const swiper = el.swiper || el.closest(".swiper, .swiper-container")?.swiper;
      if (!swiper) return;
      try { swiper.autoplay?.stop?.(); actions.push("swiper:autoplay-stop"); } catch (_) {}
      try { swiper.slideToLoop?.(0, 0, false); actions.push("swiper:slideToLoop0"); return; } catch (_) {}
      try { swiper.slideTo?.(0, 0, false); actions.push("swiper:slideTo0"); } catch (_) {}
    });
  };
  const pinKnownJQueryCarousels = () => {
    const jq = window.jQuery || window.$;
    if (!jq) return;
    document.querySelectorAll(".slick-slider").forEach((el) => {
      try { jq(el).slick("slickPause").slick("slickGoTo", 0, true); actions.push("slick:pause-go0"); } catch (_) {}
    });
    document.querySelectorAll(".owl-carousel").forEach((el) => {
      try { jq(el).trigger("stop.owl.autoplay").trigger("to.owl.carousel", [0, 0, true]); actions.push("owl:pause-go0"); } catch (_) {}
    });
  };
  const pinEmblaLike = () => {
    for (const key of Object.keys(window)) {
      const value = window[key];
      if (!value || typeof value !== "object") continue;
      try {
        if (typeof value.scrollTo === "function" && typeof value.selectedScrollSnap === "function") {
          value.scrollTo(0, true);
          actions.push(`embla-like:${key}:scrollTo0`);
        }
      } catch (_) {}
    }
  };
  addStyle();
  pauseMedia();
  pinSwiper();
  pinKnownJQueryCarousels();
  pinEmblaLike();
  document.documentElement.setAttribute("data-ui-clone-live-current-mode", "pin");
  return JSON.stringify({
    mode: "pin",
    url: location.href,
    actions,
    activeElement: document.activeElement ? document.activeElement.tagName : null,
    imageFiles: [...document.images].slice(0, 12).map((i) => decodeURIComponent((i.currentSrc || i.src || "").split("/").pop().split("?")[0] || "")),
  });
})()'

if [ "$LIVE_CURRENT_MODE" = "pin" ]; then
  agent-browser --session "$S_REF" eval "$DYNAMIC_PIN_JS" 2>/dev/null > "$SHOTS/dynamic-ref.raw" || true
  agent-browser --session "$S_IMPL" eval "$DYNAMIC_PIN_JS" 2>/dev/null > "$SHOTS/dynamic-impl.raw" || true
else
  printf '{"mode":"%s","actions":[]}\n' "$LIVE_CURRENT_MODE" > "$SHOTS/dynamic-ref.raw"
  printf '{"mode":"%s","actions":[]}\n' "$LIVE_CURRENT_MODE" > "$SHOTS/dynamic-impl.raw"
fi

python3 - "$DIR/live-dynamic-state.json" "$SHOTS/dynamic-ref.raw" "$SHOTS/dynamic-impl.raw" "$LIVE_CURRENT_MODE" <<'PY' >/dev/null 2>&1 || true
from __future__ import annotations
import json
import sys
from pathlib import Path

def load(path: str) -> dict:
    raw = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        if isinstance(value, str):
            value = json.loads(value)
        return value if isinstance(value, dict) else {"raw": value}
    except Exception as exc:
        return {"parseError": str(exc), "raw": raw[:200]}

out, ref_raw, impl_raw, mode = sys.argv[1:5]
payload = {
    "schemaVersion": 1,
    "source": "skills/visual-debug/scripts/live-parity-sweep.sh",
    "mode": mode,
    "ref": load(ref_raw),
    "impl": load(impl_raw),
    "note": "Pin mode pauses common carousel/media/animation APIs before live parity so repeated live-current comparisons are deterministic. Use UI_CLONE_LIVE_CURRENT_MODE=snapshot for raw live behavior.",
}
Path(out).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

CENSUS_JS='(() => {
  const d = document;
  const normalizeText = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const a11yClass = /(?:^|[\s_-])(?:sr-only|screen-reader(?:-only)?|visually-hidden|blind|clipped|a11y|accessibility)(?:$|[\s_-])/i;
  const hiddenCache = new WeakMap();
  const stronglyHidden = (el) => {
    if (hiddenCache.has(el)) return hiddenCache.get(el);
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const opacity = parseFloat(cs.opacity || "1");
    const clipped = String(cs.clip || "auto") !== "auto";
    const clipPath = String(cs.clipPath || cs.webkitClipPath || "none");
    const insetHidden = /inset\(\s*(?:50|100)%/i.test(clipPath);
    const overflowHidden = [cs.overflow, cs.overflowX, cs.overflowY]
      .some((value) => value === "hidden" || value === "clip");
    const tinyClipped = rect.width <= 2 && rect.height <= 2 && overflowHidden;
    const offCanvas = rect.right < -10 || rect.bottom < -10
      || rect.left > d.documentElement.scrollWidth + 10;
    const parent = el.parentElement;
    const hidden = cs.display === "none" || cs.visibility === "hidden" || opacity <= 0.01
      || clipped || insetHidden || tinyClipped || offCanvas
      || Boolean(parent && parent !== d.documentElement && stronglyHidden(parent));
    hiddenCache.set(el, hidden);
    return hidden;
  };
  const textNodeVisible = (node) => {
    const parent = node.parentElement;
    if (!parent || stronglyHidden(parent)) return false;
    const range = d.createRange();
    range.selectNodeContents(node);
    return [...range.getClientRects()].some((rect) => rect.width > 1 && rect.height > 1);
  };
  const visibleTextParts = [];
  const walker = d.createTreeWalker(d.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = normalizeText(node.nodeValue);
    if (text && textNodeVisible(node)) visibleTextParts.push(text);
  }
  const visibleBodyText = normalizeText(visibleTextParts.join(" ")).slice(0, 250000);
  const accessibilityOnlyText = [];
  const candidateKeys = new Set();
  const addCandidate = (text, source, el) => {
    const normalized = normalizeText(text);
    if (normalized.length < 40) return;
    const key = `${source}\u0000${normalized}`;
    if (candidateKeys.has(key)) return;
    candidateKeys.add(key);
    accessibilityOnlyText.push({
      text: normalized.slice(0, 500),
      source,
      tag: el.tagName,
      class: String(el.className || "").slice(0, 160),
    });
  };
  [...d.querySelectorAll("body *")].forEach((el) => {
    const cls = String(el.className || "");
    const roleImage = el.closest("[role=\"img\"],[role^=\"graphics-\"]");
    if (a11yClass.test(cls) || (roleImage && stronglyHidden(el))) {
      addCandidate(el.textContent, "hidden-text", el);
    }
    for (const attr of ["alt", "aria-label", "aria-description"]) {
      if (el.hasAttribute(attr)) addCandidate(el.getAttribute(attr), attr, el);
    }
  });
  const leafTextOversized = [...d.querySelectorAll("body *")].filter(e =>
    e.children.length === 0 && e.textContent && e.textContent.trim().length > 0 &&
    e.textContent.trim().length <= 4 &&
    (parseFloat(getComputedStyle(e).fontSize) >= 48 || e.getBoundingClientRect().height >= 100)
  ).slice(0, 30).map(e => ({
    tag: e.tagName, cls: (e.className || "").toString().slice(0, 60),
    txt: e.textContent.trim().slice(0, 8),
    font: getComputedStyle(e).fontSize,
    pseudo: e.getAttribute("data-pseudo") || null,
  }));
  const activePseudo = (el, which) => {
    const ps = getComputedStyle(el, which);
    const rect = el.getBoundingClientRect();
    const content = String(ps.content || "");
    const display = String(ps.display || "");
    const bg = String(ps.backgroundImage || "");
    const opacity = parseFloat(ps.opacity || "1");
    const hasPaint = bg !== "none" || !["none", "normal", "\"\"", "''", ""].includes(content);
    return display !== "none" && opacity > 0.01 && hasPaint && rect.width > 0 && rect.height > 0;
  };
  const activeSyntheticPseudo = (el) => {
    const ps = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const bg = String(ps.backgroundImage || "");
    const opacity = parseFloat(ps.opacity || "1");
    const hasPaint = bg !== "none" || (el.textContent || "").trim().length > 0;
    return ps.display !== "none" && ps.visibility !== "hidden" && opacity > 0.01
      && hasPaint && rect.width > 0 && rect.height > 0;
  };
  const pseudoDuplicates = [...d.querySelectorAll("body *")].filter(el => {
    const children = [...el.children];
    return (
      children.some(child => child.getAttribute("data-pseudo") === "before" && activeSyntheticPseudo(child)) && activePseudo(el, "::before")
    ) || (
      children.some(child => child.getAttribute("data-pseudo") === "after" && activeSyntheticPseudo(child)) && activePseudo(el, "::after")
    );
  }).slice(0, 30).map(el => ({
    tag: el.tagName,
    cls: (el.className || "").toString().slice(0, 80),
    text: (el.textContent || "").trim().slice(0, 40),
    before: activePseudo(el, "::before"),
    after: activePseudo(el, "::after"),
  }));
  return JSON.stringify({
    scrollHeight: d.documentElement.scrollHeight,
    headerHeight: (d.querySelector("header") || {}).offsetHeight || 0,
    imgCount: [...d.images].length,
    imgFiles: [...d.images].map(i => decodeURIComponent((i.currentSrc || i.src).split("/").pop().split("?")[0])).sort(),
    brokenImgs: [...d.images].filter(i => i.complete && i.naturalWidth === 0).length,
    fonts: [...new Set([...d.fonts].map(f => f.family))].sort(),
    accessibilityOnlyText,
    visibleBodyText,
    oversizedLeafText: leafTextOversized,
    pseudoDuplicates,
  });
})()'

agent-browser --session "$S_REF" eval "$CENSUS_JS" 2>/dev/null > "$SHOTS/census-ref.raw"
agent-browser --session "$S_IMPL" eval "$CENSUS_JS" 2>/dev/null > "$SHOTS/census-impl.raw"

DEPTH_RESULTS="$SHOTS/depths.tsv"
: > "$DEPTH_RESULTS"
IFS=',' read -ra YS <<< "$DEPTHS"
for y in "${YS[@]}"; do
  for s in "$S_REF" "$S_IMPL"; do
    agent-browser --session "$s" eval "(() => { window.scrollTo(0, $y); return $y })()" >/dev/null 2>&1
  done
  sleep 1.5
  agent-browser --session "$S_REF" screenshot "$SHOTS/ref-$y.png" >/dev/null 2>&1
  agent-browser --session "$S_IMPL" screenshot "$SHOTS/impl-$y.png" >/dev/null 2>&1
  AE=$(magick compare -metric AE "$SHOTS/ref-$y.png" "$SHOTS/impl-$y.png" null: 2>&1 | awk '{print $1}')
  DS=$(dssim "$SHOTS/ref-$y.png" "$SHOTS/impl-$y.png" 2>/dev/null | awk '{print $1}')
  printf '%s\t%s\t%s\n' "$y" "${AE:-NA}" "${DS:-NA}" >> "$DEPTH_RESULTS"
done

python3 - "$DIR" "$SHOTS" <<'PY'
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

out_dir = Path(sys.argv[1])
shots = Path(sys.argv[2])


def load_census(name: str) -> dict:
    raw = (shots / name).read_text(encoding="utf-8").strip()
    if raw.startswith('"'):
        raw = json.loads(raw)
    return json.loads(raw)


try:
    ref = load_census("census-ref.raw")
    impl = load_census("census-impl.raw")
except Exception as exc:  # noqa: BLE001 — diagnostic tool, report and bail soft
    (out_dir / "live-parity.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "error",
        "reasons": [f"census unreadable: {exc}"],
    }, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "error"}))
    raise SystemExit(0)

findings: list[dict] = []
advisory_findings: list[dict] = []

ref_imgs = ref.get("imgFiles") or []
impl_imgs = impl.get("imgFiles") or []
ref_img_counts = Counter(ref_imgs)
impl_img_counts = Counter(impl_imgs)
missing = sorted(set(ref_imgs) - set(impl_imgs))
extra = sorted(set(impl_imgs) - set(ref_imgs))
if missing:
    findings.append({
        "kind": "missing-images", "count": len(missing),
        "files": missing[:20],
        "note": "ref renders these <img> files, impl never does (encode/decode normalized)",
    })
if extra:
    findings.append({"kind": "extra-images", "count": len(extra), "files": extra[:20]})
if abs(len(ref_imgs) - len(impl_imgs)) > 0:
    findings.append({
        "kind": "image-count-drift",
        "ref": len(ref_imgs), "impl": len(impl_imgs),
        "note": "duplicate-instance drift (carousel clones) beyond filename diff",
    })
duplicate_drift = [
    {"file": name, "ref": ref_img_counts[name], "impl": impl_img_counts[name]}
    for name in sorted(set(ref_img_counts) | set(impl_img_counts))
    if ref_img_counts[name] != impl_img_counts[name]
]
if duplicate_drift:
    from ui_clone.gates.live_parity import classify_image_drift

    drift_row = {
        "kind": "image-file-count-drift",
        "count": len(duplicate_drift),
        "files": duplicate_drift[:20],
        "note": "same total image count can still hide wrong carousel/proof duplicates",
    }
    # Timer-carousel phase noise (loop-e2e-4): same filename vocabulary on both
    # sides with per-file delta <= 1 is rotation phase, not a missing asset —
    # the pin hooks cannot reach custom setInterval carousels and the ref
    # rotates while off-screen. Anything beyond stays blocking.
    if classify_image_drift(dict(ref_img_counts), dict(impl_img_counts)) == "advisory":
        drift_row["advisory"] = True
        drift_row["note"] += " (advisory: matching vocabulary, per-file delta <= 1 — timer-carousel phase)"
        advisory_findings.append(drift_row)
    else:
        findings.append(drift_row)

impl_oversized = impl.get("oversizedLeafText") or []
ref_oversized_txt = {(e.get("txt"), e.get("tag")) for e in (ref.get("oversizedLeafText") or [])}
stray = [e for e in impl_oversized if (e.get("txt"), e.get("tag")) not in ref_oversized_txt]
if stray:
    findings.append({
        "kind": "stray-oversized-text", "count": len(stray), "elements": stray[:10],
        "note": "large text leaves in impl with no ref counterpart "
                "(classic case: pseudo-element content emitted visibly)",
    })

from ui_clone.gates.live_parity import find_accessibility_text_leaks

accessibility_leaks = find_accessibility_text_leaks(
    ref.get("accessibilityOnlyText") or [],
    str(ref.get("visibleBodyText") or ""),
    str(impl.get("visibleBodyText") or ""),
)
if accessibility_leaks:
    findings.append({
        "kind": "visible-accessibility-copy-leak",
        "count": len(accessibility_leaks),
        "elements": accessibility_leaks[:10],
        "note": (
            "long copy that is accessibility-only in ref appears in the impl's "
            "rendered visible text"
        ),
    })

pseudo_duplicates = impl.get("pseudoDuplicates") or []
if pseudo_duplicates:
    findings.append({
        "kind": "synthetic-native-pseudo-duplicates",
        "count": len(pseudo_duplicates),
        "elements": pseudo_duplicates[:10],
        "note": "impl materializes ::before/::after as [data-pseudo] spans but native CSS pseudo still paints too",
    })


from ui_clone.gates.live_parity import scrollheight_within_tolerance

for key in ("scrollHeight", "headerHeight", "brokenImgs"):
    rv, iv = ref.get(key), impl.get(key)
    if rv == iv:
        continue
    if key == "scrollHeight" and scrollheight_within_tolerance(rv, iv):
        # Pages with scroll-activated content legally oscillate (realfood erf
        # +-180px); exact equality across two sessions is luck. Balloons are
        # still caught here (max(0.5%, 200px)) and by geometry-sanity (15%).
        advisory_findings.append(
            {"kind": f"{key}-mismatch", "ref": rv, "impl": iv, "advisory": True}
        )
        continue
    findings.append({"kind": f"{key}-mismatch", "ref": rv, "impl": iv})

ref_fonts = set(ref.get("fonts") or [])
impl_fonts = set(impl.get("fonts") or [])
if ref_fonts - impl_fonts:
    findings.append({"kind": "missing-fonts", "fonts": sorted(ref_fonts - impl_fonts)})

depths = []
for line in (shots / "depths.tsv").read_text(encoding="utf-8").splitlines():
    parts = line.split("\t")
    if len(parts) == 3:
        depths.append({"y": parts[0], "ae": parts[1], "dssim": parts[2]})

payload = {
    "schemaVersion": 1,
    "status": "fail" if findings else "pass",
    "liveCurrentMode": "pin",
    "censusStatus": "findings" if findings else "clean",
    "findingCount": len(findings),
    "findings": findings,
    "advisoryFindings": advisory_findings,
    "advisoryDepths": depths,
    "censusRef": {k: ref.get(k) for k in ("scrollHeight", "headerHeight", "imgCount", "brokenImgs")},
    "censusImpl": {k: impl.get(k) for k in ("scrollHeight", "headerHeight", "imgCount", "brokenImgs")},
    "note": (
        "Diagnostic sweep — full-frame AE/dssim are advisory only (live-site "
        "dynamics); DOM census findings are deterministic and actionable."
    ),
}
dynamic_state = out_dir / "live-dynamic-state.json"
if dynamic_state.is_file():
    try:
        payload["dynamicState"] = json.loads(dynamic_state.read_text(encoding="utf-8"))
        payload["liveCurrentMode"] = payload["dynamicState"].get("mode", payload["liveCurrentMode"])
    except Exception:
        payload["dynamicState"] = {"unreadable": True}
(out_dir / "live-parity.json").write_text(
    json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
)
print(json.dumps({"status": payload["status"], "findings": len(findings)}))
PY
echo "✅ live-parity-sweep: report at $DIR/live-parity.json"
if ! STATUS=$(python3 - "$DIR/live-parity.json" 2>/dev/null <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
print(str(data.get("status") or "error").lower())
PY
); then
  STATUS="error"
fi
case "$STATUS" in
  pass)
    exit 0
    ;;
  *)
    echo "✗ live-parity-sweep: FAIL (status=$STATUS; deterministic DOM census finding)" >&2
    exit 1
    ;;
esac
