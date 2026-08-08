#!/usr/bin/env bash
# svg-dom-parity-check.sh — runtime SVG inventory parity gate.
#
#
# Algorithm:
#   1. Open ref and impl URLs via agent-browser
#   2. For each runtime: enumerate per major section the SVG inventory
#      - inline <svg> count (with path count, viewBox set, bbox)
#      - <img src$=".svg"> count
#      - elements with computed background-image: url(...svg)
#      - elements with ::before/::after computed bg url(...svg)
#      - <use href="...svg"> count
#   3. Compare ref vs impl totals + per-section deltas
#   4. Fail if:
#      - Ref has inline SVGs but impl has 0
#      - Ref totals are ≥ 2 and impl totals < 50% of ref
#      - Any section that has SVG in ref has 0 in impl
#      - Impl inline <svg> has 0 path/use/circle children when ref's
#        counterpart had ≥ 1 (catches the empty-<svg> scaffold bug)
#
# Usage:
#   svg-dom-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>
#
# Output: <ref-dir>/svg-dom-parity.json
#
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

SESSION="${1:-}"
REF_URL="${2:-}"
IMPL_URL="${3:-}"
REF_DIR="${4:-}"

if [ -z "$SESSION" ] || [ -z "$REF_URL" ] || [ -z "$IMPL_URL" ] || [ -z "$REF_DIR" ]; then
  echo "Usage: svg-dom-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>" >&2
  exit 2
fi

if [ ! -d "$REF_DIR" ]; then
  echo "ref-dir not found: $REF_DIR" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/svg-dom-parity.json"
# L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
REF_TMP="$(mktemp -t svg-parity-ref-XXXXXX)"
mv "$REF_TMP" "${REF_TMP}.json"
REF_TMP="${REF_TMP}.json"
# L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
IMPL_TMP="$(mktemp -t svg-parity-impl-XXXXXX)"
mv "$IMPL_TMP" "${IMPL_TMP}.json"
IMPL_TMP="${IMPL_TMP}.json"
trap 'rm -f "$REF_TMP" "$IMPL_TMP"' EXIT

INVENTORY_JS='(() => {
  const isSvgUrl = (u) => {
    if (!u || typeof u !== "string") return false;
    const m = u.match(/url\(\s*["\x27]?([^"\x27)]+?\.svg(?:\?[^"\x27)]*)?)\s*/i);
    return !!m;
  };
  const extractSvgUrls = (bgString) => {
    if (!bgString || bgString === "none") return [];
    const out = [];
    const re = /url\(\s*["\x27]?([^"\x27)]+?\.svg(?:\?[^"\x27)]*)?)["\x27]?\s*\)/gi;
    let m;
    while ((m = re.exec(bgString)) !== null) out.push(m[1]);
    return out;
  };
  const sectionElements = [...document.querySelectorAll("main,section,header,footer,article,nav,aside,[role=region],[role=banner],[role=contentinfo]")]
    .filter((s) => {
      const r = s.getBoundingClientRect();
      return r.width >= 60 && r.height >= 40;
    });

  const inventory = (rootEl, rootName) => {
    // Common cheat pattern: agents pad SVG inventory with 2px / opacity:0
    // / display:none pseudo seeds to satisfy count gates. Add a
    // visibility filter so only RENDERED SVG counts.
    //
    const isVisible = (el) => {
      try {
        if (typeof el.checkVisibility === "function") {
          if (!el.checkVisibility({
            checkOpacity: true, checkVisibilityCSS: true,
          })) return false;
        }
        const cs = getComputedStyle(el);
        if (cs.display === "none" || cs.visibility === "hidden") return false;
        if (parseFloat(cs.opacity || "1") <= 0.05) return false;
        let p = el.parentElement; let hops = 0;
        while (p && hops < 12) {
          const pcs = getComputedStyle(p);
          if (pcs.display === "none" || pcs.visibility === "hidden") return false;
          if (parseFloat(pcs.opacity || "1") <= 0.05) return false;
          p = p.parentElement; hops++;
        }
        const r = el.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) return false;
      } catch (e) { /* ignore */ }
      return true;
    };
    const all = rootEl.querySelectorAll("*");
    let inlineSvg = 0;
    let svgWithPath = 0;
    let inlineSvgInvisible = 0;
    let imgSvg = 0;
    let useHref = 0;
    let bgSvg = 0;
    let pseudoBgSvg = 0;
    const viewBoxes = [];
    const pathCounts = [];
    rootEl.querySelectorAll("svg").forEach((s) => {
      if (!isVisible(s)) { inlineSvgInvisible++; return; }
      inlineSvg++;
      const vb = s.getAttribute("viewBox") || "";
      viewBoxes.push(vb);
      const paths = s.querySelectorAll("path, circle, rect, line, polygon, polyline, ellipse, use");
      pathCounts.push(paths.length);
      if (paths.length > 0) svgWithPath++;
    });
    rootEl.querySelectorAll("img").forEach((i) => {
      const src = i.getAttribute("src") || "";
      if (!src.toLowerCase().split("?")[0].endsWith(".svg")) return;
      if (!isVisible(i)) return;
      imgSvg++;
    });
    rootEl.querySelectorAll("use").forEach((u) => {
      const h = u.getAttribute("href") || u.getAttribute("xlink:href") || "";
      if (!h || !h.toLowerCase().split("#")[0].endsWith(".svg")) return;
      // <use> visibility follows the parent <svg> visibility.
      const svgParent = u.closest("svg");
      if (svgParent && !isVisible(svgParent)) return;
      useHref++;
    });
    const hasSvgBg = (bg) => bg && bg.includes("url(") && /\.svg\b/i.test(bg);
    all.forEach((el) => {
      const isSyntheticPseudo = el.matches && el.matches("[data-pseudo]");
      if (isSyntheticPseudo) {
        // Scaffolded clones materialize captured ::before/::after as
        // <span data-pseudo>. Native pseudo inventory is counted from the
        // visible parent even when the pseudo layer itself is opacity:0
        // (for hover/state crossfades). Mirror that behavior here so the
        // verifier does not treat a duplicate-prevention guard as SVG loss.
        const parent = el.parentElement;
        if (parent && isVisible(parent)) {
          const bg = getComputedStyle(el).backgroundImage;
          if (hasSvgBg(bg)) pseudoBgSvg++;
        }
        return;
      }
      if (!isVisible(el)) return;
      const bg = getComputedStyle(el).backgroundImage;
      if (hasSvgBg(bg)) bgSvg++;
      try {
        for (const which of ["::before", "::after"]) {
          const ps = getComputedStyle(el, which);
          const psBg = ps.getPropertyValue("background-image");
          if (hasSvgBg(psBg)) pseudoBgSvg++;
        }
      } catch (e) { /* ignore */ }
    });
    // Universality audit HIGH FP: section name was synthesized
    // from tag/id/class, so impls that legitimately re-classed
    // would appear missing. Emit geometry (top, height, index) so
    // the Python comparator can match by bbox/order rather than
    // brittle synthesized names.
    let bbox = { top: 0, height: 0, width: 0 };
    try {
      const r = rootEl.getBoundingClientRect ? rootEl.getBoundingClientRect() : null;
      if (r) bbox = {
        top: Math.round(r.top + window.scrollY),
        height: Math.round(r.height),
        width: Math.round(r.width),
      };
    } catch (e) { /* ignore */ }
    return {
      name: rootName,
      bbox,
      inlineSvg,
      inlineSvgInvisible,
      svgWithPath,
      imgSvg,
      useHref,
      bgSvg,
      pseudoBgSvg,
      total: inlineSvg + imgSvg + useHref + bgSvg + pseudoBgSvg,
      viewBoxes: viewBoxes.slice(0, 12),
      pathCounts: pathCounts.slice(0, 12),
    };
  };

  const pageRoot = document.body || document.documentElement;
  const pageTotal = inventory(pageRoot, "__page__");
  const sections = sectionElements.map((s, i) => {
    const name = s.tagName.toLowerCase() + (s.id ? "#" + s.id : "") +
      (s.className && s.className.baseVal === undefined ?
        "." + String(s.className).split(/\s+/).filter(Boolean).slice(0, 2).join(".") : "");
    const inv = inventory(s, name.slice(0, 60) || `sec${i}`);
    inv.index = i;  // preserve document order for the bbox comparator
    return inv;
  });
  return JSON.stringify({ page: pageTotal, sections });
})()'

run_capture() {
  local url="$1" out="$2" sess="$3"
  local open_status=0
  # agent-browser can return a timeout for pages that keep network activity
  # alive even though the document is already usable. Treat the open command
  # as a navigation attempt, then verify the loaded document directly instead
  # of failing on the CLI exit code alone.
  agent-browser --session "$sess" open "$url" >/dev/null 2>&1 || open_status=$?
  agent-browser --session "$sess" wait 2500 >/dev/null 2>&1 || true
  local href=""
  href="$(agent-browser --session "$sess" eval '(() => location.href)()' 2>/dev/null || true)"
  if [ -z "$href" ] || printf '%s' "$href" | grep -Eiq 'about:blank'; then
    echo "{\"error\": \"open failed\", \"openStatus\": $open_status}" > "$out"
    return 1
  fi
  agent-browser --session "$sess" eval 'window.scrollTo(0, document.body.scrollHeight/2)' >/dev/null 2>&1 || true
  agent-browser --session "$sess" wait 400 >/dev/null 2>&1 || true
  agent-browser --session "$sess" eval 'window.scrollTo(0, document.body.scrollHeight)' >/dev/null 2>&1 || true
  agent-browser --session "$sess" wait 400 >/dev/null 2>&1 || true
  agent-browser --session "$sess" eval 'window.scrollTo(0, 0)' >/dev/null 2>&1 || true
  agent-browser --session "$sess" wait 300 >/dev/null 2>&1 || true
  agent-browser --session "$sess" eval "$INVENTORY_JS" > "$out" 2>/dev/null || {
    echo "{\"error\": \"browser result failed\"}" > "$out"
    return 1
  }
  return 0
}

run_capture "$REF_URL"  "$REF_TMP"  "${SESSION}-ref"  || true
run_capture "$IMPL_URL" "$IMPL_TMP" "${SESSION}-impl" || true

python3 - "$REF_TMP" "$IMPL_TMP" "$OUT_PATH" "$REF_URL" "$IMPL_URL" <<'PY'
import json
import sys
from pathlib import Path

ref_path = Path(sys.argv[1])
impl_path = Path(sys.argv[2])
out_path = Path(sys.argv[3])
ref_url = sys.argv[4]
impl_url = sys.argv[5]


def parse(p: Path) -> dict:
    text = p.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return {"error": "empty"}
    try:
        outer = json.loads(text)
        if isinstance(outer, str):
            return json.loads(outer)
        if isinstance(outer, dict):
            return outer
        return {"error": "unexpected shape"}
    except ValueError:
        try:
            return json.loads(text.strip("'\""))
        except ValueError:
            return {"error": "unparseable", "raw": text[:300]}


ref_data = parse(ref_path)
impl_data = parse(impl_path)

violations: list[dict] = []
if "error" in ref_data:
    violations.append({"kind": "ref-browser-failed", "detail": ref_data["error"]})
if "error" in impl_data:
    violations.append({"kind": "impl-browser-failed", "detail": impl_data["error"]})

ref_page = ref_data.get("page", {}) if "error" not in ref_data else {}
impl_page = impl_data.get("page", {}) if "error" not in impl_data else {}

ref_total = int(ref_page.get("total") or 0)
impl_total = int(impl_page.get("total") or 0)
ref_inline = int(ref_page.get("inlineSvg") or 0)
impl_inline = int(impl_page.get("inlineSvg") or 0)
ref_with_path = int(ref_page.get("svgWithPath") or 0)
impl_with_path = int(impl_page.get("svgWithPath") or 0)

# Rule 1: page-total dropout
if ref_total >= 2 and impl_total < ref_total * 0.5:
    violations.append({
        "kind": "page-total-svg-dropout",
        "ref": ref_total,
        "impl": impl_total,
        "ratio": round(impl_total / ref_total, 3) if ref_total else 0,
        "detail": "impl is missing >=50% of the ref's SVG inventory",
    })

# Rule 2: empty inline SVGs (the scaffold bug)
if ref_with_path >= 1 and impl_inline >= 1 and impl_with_path == 0:
    violations.append({
        "kind": "inline-svg-empty",
        "refInline": ref_inline, "refWithPath": ref_with_path,
        "implInline": impl_inline, "implWithPath": impl_with_path,
        "detail": (
            "impl ships <svg> elements but none contain any "
            "path/circle/rect/use children — extract-dom didn't preserve "
            "SVG geometry attrs, or scaffold-to-jsx dropped them"
        ),
    })

# Common cheat pattern: SVG count-gaming. When impl ships many invisible
# SVG seeds (2px / opacity:0 / display:none / hidden) to satisfy a
# count gate without rendering anything, the visible inventory will
# drop to ~0 while the invisible inventory inflates. Flag when impl
# has >= 2 invisible inline SVGs AND visible inline count is < 50%
# of ref's visible inline count.
impl_inline_invisible = int(impl_page.get("inlineSvgInvisible") or 0)
if (
    impl_inline_invisible >= 2
    and ref_inline >= 2
    and impl_inline < ref_inline * 0.5
):
    violations.append({
        "kind": "svg-count-gaming",
        "refInlineVisible": ref_inline,
        "implInlineVisible": impl_inline,
        "implInlineInvisible": impl_inline_invisible,
        "detail": (
            f"impl ships {impl_inline_invisible} invisible inline SVG "
            f"seeds (2px / opacity:0 / display:none / hidden) while only "
            f"{impl_inline} visible — pattern matches count-gaming to "
            "satisfy SVG inventory without rendering. Make the SVGs "
            "actually visible or remove the seeds."
        ),
    })

# Rule 3: per-section dropout — Universality audit HIGH FP fix.
# Section matching is now bbox/order-based: walk ref + impl section
# lists in document order, pair by index; if the impl has fewer
# sections, the unmatched tail counts as missing. For the matched
# pairs, additionally verify bbox overlap (top distance < 30% of ref
# section height) before treating the pair as equivalent.
ref_secs_list = ref_data.get("sections") or []
impl_secs_list = impl_data.get("sections") or []


def _bbox_overlap_ratio(a: dict, b: dict) -> float:
    """Vertical overlap fraction between two bboxes (0..1).

    The two pages may scale differently, but the SECTION-RELATIVE
    height should be similar; we score by min/max of height ratio
    plus top-distance normalized by ref height.
    """
    a_bb = a.get("bbox") or {}
    b_bb = b.get("bbox") or {}
    a_h = float(a_bb.get("height") or 0)
    b_h = float(b_bb.get("height") or 0)
    if a_h <= 0 or b_h <= 0:
        return 0.0
    height_ratio = min(a_h, b_h) / max(a_h, b_h)
    a_top = float(a_bb.get("top") or 0)
    b_top = float(b_bb.get("top") or 0)
    # Both tops have to be roughly proportional to page height — use
    # |a_top - b_top| / max(a_h, b_h) as a coarse vertical-distance
    # signal.
    top_dist_norm = abs(a_top - b_top) / max(a_h, b_h)
    return max(0.0, height_ratio - min(1.0, top_dist_norm * 0.5))


for i, ref_s in enumerate(ref_secs_list):
    if not isinstance(ref_s, dict):
        continue
    rt = int(ref_s.get("total") or 0)
    if rt < 1:
        continue
    impl_s = impl_secs_list[i] if i < len(impl_secs_list) else {}
    if not isinstance(impl_s, dict):
        impl_s = {}
    it = int(impl_s.get("total") or 0)
    # Bbox sanity: if the paired impl section is at the wrong page
    # location, fall back to "first impl section with non-zero total
    # whose bbox overlaps the ref section best" search.
    if it == 0 or _bbox_overlap_ratio(ref_s, impl_s) < 0.3:
        best = None
        best_score = 0.3  # require >= 0.3 overlap
        for j, candidate in enumerate(impl_secs_list):
            if not isinstance(candidate, dict):
                continue
            score = _bbox_overlap_ratio(ref_s, candidate)
            if score > best_score and int(candidate.get("total") or 0) > 0:
                best, best_score = candidate, score
        if best is not None:
            impl_s = best
            it = int(impl_s.get("total") or 0)
    name = ref_s.get("name") or f"sec{i}"
    if it == 0:
        violations.append({
            "kind": "section-svg-missing",
            "section": name,
            "refIndex": i,
            "refBbox": ref_s.get("bbox"),
            "refTotal": rt,
            "implTotal": it,
            "refBreakdown": {
                k: ref_s.get(k) for k in
                ("inlineSvg", "imgSvg", "useHref", "bgSvg", "pseudoBgSvg")
            },
        })

status = "fail" if violations else "pass"
result = {
    "schemaVersion": 1,
    "status": status,
    "refUrl": ref_url,
    "implUrl": impl_url,
    "refPage": ref_page,
    "implPage": impl_page,
    "violations": violations[:30],
    "rule": (
        "Impl runtime SVG inventory must match ref along: (1) page-total "
        ">= 50% of ref when ref has >=2 SVGs, (2) impl inline <svg> "
        "must have geometry children when ref's do, (3) any major section "
        "with SVG in ref must have at least 1 SVG (inline/img/use/bg/"
        "pseudo-bg) in impl."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"svg-dom-parity: ref={ref_total} impl={impl_total} "
    f"(inline {ref_inline}→{impl_inline}, with-path {ref_with_path}→{impl_with_path}) "
    f"→ {status} ({len(violations)} violation(s))"
)
sys.exit(0 if status == "pass" else 1)
PY
