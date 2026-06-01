#!/usr/bin/env bash
# tree-diff.sh — Exhaustive per-element CSS diff between ref and impl
#
# Walks every visible element on impl (≥ MIN_SIZE px, ranked by area),
# pairs each with its true ref counterpart by CONTENT + STRUCTURE (text
# similarity → tag/role/src/alt/box/path identity → section-relative position
# as a final tiebreaker), then runs computed-style diff per pair. Pairing is
# done in ui_clone.tree_diff_pairing (unit-tested) — NOT by screen coordinate,
# because a clone in progress has a different layout/height than the ref, so
# same-coordinate pairing mis-pairs (anchor to content, not the y-coordinate).
#
# Catches mismatches that pixel-AE misses:
#   - Wrong font-family that renders identically (both fonts available)
#   - Two elements with same text/box but different style overrides
#   - Same effect via different DOM (button vs anchor with onClick)
#
# Usage: bash tree-diff.sh <session> <orig-url> <impl-url> [out-dir]
#
# Env:
#   VIEW_W=1440 VIEW_H=900    Viewport
#   WAIT_MS=4000              Initial page settle time
#   SCROLL_SETTLE_MS=350      Per-frame settle after each scroll step
#   MIN_SIZE=16               Skip elements smaller than NxN px
#   MAX_ELEMENTS=400          Cap whole-page walk (top N by area). Raised from
#                             200 because the walk now covers the FULL page
#                             (every section, not just the top viewport), so a
#                             200-cap would arbitrarily discard the newly-reached
#                             deep-section coverage. Coverage knob only — no
#                             severity threshold is touched.
#   PAIR_TOLERANCE=10         Legacy (no longer gates pairing — pairing is by
#                             content+structure in ui_clone.tree_diff_pairing)
#
# Coverage: the walk scrolls the page in viewport-height steps (15% overlap) and
# walks the elements in view at each step, so below-the-fold sections are walked
# too — a single top-viewport walk was structurally blind to them, leaving deep
# sections perpetually "unpaired". Elements seen in overlapping frames are
# de-duplicated by a stable key (tag + DOM path + page-absolute top/left + text).
# This ONLY extends coverage; the per-pair computed-style diff, severity buckets,
# and thresholds are unchanged.
#
# Output:
#   <dir>/tree-diff.md   — Markdown table (severity-sorted)
#   <dir>/tree-diff.json — Raw pair data
# Exit 0 if no critical/major mismatches; 1 otherwise.

set -uo pipefail

if ! command -v agent-browser &>/dev/null; then
  echo "ERROR: agent-browser not found"; exit 2
fi
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found"; exit 2
fi

SESSION="${1:?Usage: tree-diff.sh <session> <orig-url> <impl-url> [out-dir]}"
ORIG_URL="${2:?Missing orig-url}"
IMPL_URL="${3:?Missing impl-url}"
OUT_DIR="${4:-tmp/tree-diff}"

VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"
WAIT_MS="${WAIT_MS:-4000}"
SCROLL_SETTLE_MS="${SCROLL_SETTLE_MS:-350}"
MIN_SIZE="${MIN_SIZE:-16}"
MAX_ELEMENTS="${MAX_ELEMENTS:-400}"
PAIR_TOLERANCE="${PAIR_TOLERANCE:-10}"

mkdir -p "$OUT_DIR"

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPTS_DIR/../../.." && pwd)"

REF_SESS="${SESSION}-tree-ref"
IMPL_SESS="${SESSION}-tree-impl"

TMP_IMPL=$(mktemp "${TMPDIR:-/tmp}/tree-diff-impl.XXXXXX") || {
  echo "ERROR: failed to create impl temp file"
  exit 2
}
TMP_REF=$(mktemp "${TMPDIR:-/tmp}/tree-diff-ref.XXXXXX") || {
  echo "ERROR: failed to create ref temp file"
  exit 2
}
TMP_REF_FULL=$(mktemp "${TMPDIR:-/tmp}/tree-diff-ref-full.XXXXXX") || {
  echo "ERROR: failed to create ref-full temp file"
  exit 2
}

cleanup() {
  agent-browser --session "$REF_SESS" close >/dev/null 2>&1 || true
  agent-browser --session "$IMPL_SESS" close >/dev/null 2>&1 || true
  rm -f "$TMP_IMPL" "$TMP_REF" "$TMP_REF_FULL"
}
trap cleanup EXIT

echo "═══ Tree Diff (per-element CSS pairing) ═══"
echo "  orig: $ORIG_URL"
echo "  impl: $IMPL_URL"
echo "  viewport: ${VIEW_W}x${VIEW_H}, min size: ${MIN_SIZE}px, top: $MAX_ELEMENTS"
echo ""

# ── Open both sessions ──
agent-browser --session "$REF_SESS" open "$ORIG_URL" >/dev/null 2>&1
agent-browser --session "$IMPL_SESS" open "$IMPL_URL" >/dev/null 2>&1
agent-browser --session "$REF_SESS"  set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1 || true
agent-browser --session "$IMPL_SESS" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1 || true
agent-browser --session "$REF_SESS"  wait "$WAIT_MS" >/dev/null 2>&1
agent-browser --session "$IMPL_SESS" wait "$WAIT_MS" >/dev/null 2>&1

# ── Step 1: walk impl tree ──
echo "  ▸ Walking impl tree..."
WALK_JS=$(cat <<JSEOF
(async () => {
  const props = ['fontFamily','fontSize','fontWeight','fontStyle','letterSpacing',
                 'lineHeight','textTransform','textAlign','color','backgroundColor',
                 'display','position','padding','margin','borderRadius',
                 'borderTopWidth','borderTopColor',
                 // Fidelity props extract-dom.sh captures but tree-diff used to
                 // skip — without them a freehanded shadow / wrong alignment /
                 // missing radius / z-order pairs as "ok". ADDITIVE: compare
                 // more props. Severity buckets below are UNCHANGED (these land
                 // in "minor"), so no threshold is loosened.
                 'borderRightWidth','borderRightColor','borderBottomWidth',
                 'borderBottomColor','borderLeftWidth','borderLeftColor',
                 'borderTopStyle','boxShadow','transform','overflow','zIndex',
                 'justifyContent','alignItems','flexDirection','gap',
                 'gridTemplateColumns','gridTemplateRows','opacity'];
  const SKIP_TAGS = new Set(['SCRIPT','STYLE','META','LINK','HEAD','TITLE','NOSCRIPT','BR','HR']);
  const minSize = ${MIN_SIZE};
  const maxN    = ${MAX_ELEMENTS};
  const settle  = ${SCROLL_SETTLE_MS};
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  // De-dup across overlapping frames. Page-absolute top/left is stable for a
  // given element regardless of scroll position, so this key identifies one
  // DOM element uniquely; an element straddling two frames is captured once.
  const seen = new Map();
  // DOM structural path: nth-of-type chain up to 6 ancestors. Used to pair
  // text-less elements (wrappers/img/svg) by structural position, not coords.
  const pathOf = (el) => {
    const parts = []; let node = el, depth = 0;
    while (node && node.nodeType === 1 && node.tagName !== 'BODY' && depth < 6) {
      let i = 1, sib = node;
      while ((sib = sib.previousElementSibling)) { if (sib.tagName === node.tagName) i++; }
      parts.unshift(node.tagName.toLowerCase() + ':' + i);
      node = node.parentElement; depth++;
    }
    return parts.join('>');
  };
  // Walk every element currently in the viewport at the present scroll position.
  // Coordinates are recorded PAGE-ABSOLUTE (rect + scroll offset) so deltas and
  // section-relative pairing stay correct across frames; at scrollY=0 (the only
  // frame a single-viewport walk ever saw) this is identical to before.
  const captureVisible = () => {
    const sx = window.scrollX, sy = window.scrollY;
    const vw = window.innerWidth, vh = window.innerHeight;
    const all = document.querySelectorAll('body *');
    for (const el of all) {
      if (SKIP_TAGS.has(el.tagName)) continue;
      const r = el.getBoundingClientRect();
      // Allow thin separators/borders (1-3px tall, wide) — important for layout diff
      const isThin = (r.height >= 0.5 && r.height < 4 && r.width >= 80) ||
                     (r.width  >= 0.5 && r.width  < 4 && r.height >= 80);
      if (!isThin && (r.width < minSize || r.height < minSize)) continue;
      // In-view gate for THIS frame (viewport-relative rect). Unchanged from the
      // original walk — but now re-applied at every scroll step, so below-fold
      // sections become visible in a later frame instead of never at all.
      if (r.bottom < 0 || r.top > vh) continue;
      if (r.right  < 0 || r.left > vw) continue;
      const s = getComputedStyle(el);
      if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) continue;
      const pageTop = r.top + sy, pageLeft = r.left + sx;
      const txt = (el.textContent || '').trim().replace(/\s+/g,' ').slice(0, 120);
      const path = pathOf(el);
      const key = el.tagName + '|' + path + '|' + Math.round(pageTop) + '|' +
                  Math.round(pageLeft) + '|' + txt.slice(0, 40);
      if (seen.has(key)) continue;
      const cx = pageLeft + r.width  / 2;
      const cy = pageTop  + r.height / 2;
      const styleObj = {};
      props.forEach(p => styleObj[p] = s[p]);
      seen.set(key, {
        tag: el.tagName,
        cls: (el.className && el.className.toString) ? el.className.toString().slice(0, 60) : '',
        txt,
        role: el.getAttribute('role') || '',
        src: (el.currentSrc || el.getAttribute('src') || el.getAttribute('xlink:href') || '').slice(0, 200),
        alt: (el.getAttribute('alt') || el.getAttribute('aria-label') || el.getAttribute('title') || '').slice(0, 80),
        path,
        x: +cx.toFixed(1), y: +cy.toFixed(1),
        top: +pageTop.toFixed(1), left: +pageLeft.toFixed(1),
        w: +r.width.toFixed(1), h: +r.height.toFixed(1),
        area: +(r.width * r.height).toFixed(0),
        thin: isThin,
        style: styleObj,
      });
    }
  };
  // Step through the full page in viewport-height frames with 15% overlap so an
  // element straddling a frame boundary is still fully walked in an adjacent
  // frame. Covers the entire scrollHeight, so EVERY section is reached.
  const vh = window.innerHeight;
  const maxScroll = Math.max(0, document.documentElement.scrollHeight - vh);
  const step = Math.max(100, Math.floor(vh * 0.85));
  let top = 0;
  while (true) {
    window.scrollTo({ top, behavior: 'instant' });
    await sleep(settle);
    captureVisible();
    if (top >= maxScroll) break;
    top = Math.min(maxScroll, top + step);
  }
  window.scrollTo({ top: 0, behavior: 'instant' });
  const out = Array.from(seen.values());
  out.sort((a,b) => b.area - a.area);
  return JSON.stringify(out.slice(0, maxN));
})()
JSEOF
)
agent-browser --session "$IMPL_SESS" eval "$WALK_JS" > "$TMP_IMPL" 2>&1
if [ ! -s "$TMP_IMPL" ]; then
  echo "ERROR: impl walk returned empty"; exit 2
fi

# ── Step 2a: walk the FULL ref tree (same walk as impl) ──
echo "  ▸ Walking ref tree..."
agent-browser --session "$REF_SESS" eval "$WALK_JS" > "$TMP_REF_FULL" 2>&1
if [ ! -s "$TMP_REF_FULL" ]; then
  echo "ERROR: ref walk returned empty"; exit 2
fi

# ── Step 2b: pair impl ↔ ref by CONTENT + STRUCTURE ──
# Pairing lives in ui_clone.tree_diff_pairing (unit-tested) so it can be
# verified independently. It anchors each impl element to its ref counterpart
# by text similarity → structural identity (tag/role/src/alt/box/path) →
# section-relative position (final tiebreaker) — NEVER by absolute screen
# coordinate, which mis-pairs across differently-tall pages. Output is tagged
# with the impl index `i`, the exact shape the diff below already consumes.
echo "  ▸ Pairing impl ↔ ref by content + structure..."
PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m ui_clone.tree_diff_pairing pair \
  "$TMP_IMPL" "$TMP_REF_FULL" "$TMP_REF" 2>&1
if [ ! -s "$TMP_REF" ]; then
  echo "ERROR: ref pairing returned empty"; exit 2
fi

# ── Step 3: diff each pair ──
echo "  ▸ Diffing pairs..."
echo ""

python3 - "$TMP_IMPL" "$TMP_REF" "$OUT_DIR" "$PAIR_TOLERANCE" <<'PYEOF'
import json, sys, os

def parse(path):
    with open(path) as f:
        raw = f.read().strip()
    if raw.startswith('"') and raw.endswith('"'):
        return json.loads(json.loads(raw))
    return json.loads(raw)

impl = parse(sys.argv[1])
ref  = parse(sys.argv[2])
out_dir = sys.argv[3]
tol = float(sys.argv[4])
ref_by_i = {r["i"]: r for r in ref}

# Property severity buckets
CRITICAL = {"fontFamily", "fontWeight", "color", "display"}
MAJOR    = {"fontSize", "lineHeight", "letterSpacing", "textTransform",
            "backgroundColor", "padding", "margin", "borderRadius"}
ADVISORY: set[str] = set()
# everything else = minor

try:
    _subs_path = os.path.join(out_dir, "asset-substitution.json")
    if os.path.exists(_subs_path):
        _subs = json.loads(open(_subs_path).read())
        _patterns = _subs.get("structuralOnlySections") or []
        if _subs.get("fonts") and "*" in _patterns:
            # Wildcard substitution: font cascade is fully acknowledged.
            CRITICAL = CRITICAL - {"fontFamily", "fontWeight", "color"}
            MAJOR = MAJOR - {"fontFamily", "fontWeight", "color"}
            ADVISORY = ADVISORY | {"fontFamily", "fontWeight", "color",
                                   "fontSize", "lineHeight", "letterSpacing"}
except (OSError, ValueError):
    # Treat unreadable/malformed substitution as absent — keep strict mode.
    pass

# Bbox tolerance (px). Layout is paired-only, so it's relative to a successful pair.
LAYOUT_MINOR_PX = 1.5  # sub-pixel / anti-aliasing
LAYOUT_MAJOR_PX = 4.0  # visible shift

def norm(prop, v):
    if v is None: return ""
    v = str(v).strip()
    if prop == "fontFamily":
        return v.split(",")[0].strip().strip('"\'').lower()
    if prop in ("fontSize", "lineHeight", "letterSpacing"):
        # Drop trailing "px" and round to 0.5
        if v.endswith("px"):
            try:
                f = float(v[:-2])
                return f"{round(f * 2) / 2:.1f}px"
            except: pass
    return v

# Typographic props that norm() rounds to 0.5px for the PASS/FAIL decision.
TYPO_PX = ("fontSize", "lineHeight", "letterSpacing")

def _px(v):
    """Parse a 'NNpx' computed value to float, else None."""
    if v is None: return None
    v = str(v).strip()
    if v.endswith("px"):
        try: return float(v[:-2])
        except (TypeError, ValueError): return None
    return None

def subpx_drift(a, b):
    """Raw (unrounded) px deltas for typographic props the norm() rounding
    collapses to equal. REPORTING-ONLY: this never feeds severity_of() or the
    status sidecar, so the PASS/FAIL tolerance is unchanged — it only surfaces
    sub-pixel drift (e.g. 15.84px vs 16.0px) that rounding would otherwise hide.
    A token-system value like 15.84px must not silently vanish from the diff."""
    out = []
    for k in TYPO_PX:
        av, bv = a.get(k, ""), b.get(k, "")
        # Only the masked case: PASS/FAIL diff already dropped it (normed-equal)
        # but the raw values genuinely differ.
        if norm(k, av) != norm(k, bv): continue
        fa, fb = _px(av), _px(bv)
        if fa is None or fb is None: continue
        d = abs(fa - fb)
        if d > 0:
            out.append((k, av, bv, round(d, 4)))
    return out

def diff_styles(a, b):
    diffs = []
    for k in a:
        av, bv = a.get(k, ""), b.get(k, "")
        if norm(k, av) == norm(k, bv): continue
        # both unset
        if str(av) in ("", "none", "normal", "auto") and str(bv) in ("", "none", "normal", "auto"):
            continue
        diffs.append((k, av, bv))
    return diffs

def diff_layout(impl_el, ref_el):
    """Return list of (axis, impl_v, ref_v, delta) for bbox axes that differ beyond LAYOUT_MINOR_PX."""
    out = []
    for axis in ("top", "left", "w", "h"):
        iv = impl_el.get(axis); rv = ref_el.get(axis)
        if iv is None or rv is None: continue
        d = abs(float(iv) - float(rv))
        if d > LAYOUT_MINOR_PX:
            out.append((axis, iv, rv, d))
    return out

def severity_of(diffs):
    if any(d[0] in CRITICAL for d in diffs): return "critical"
    if any(d[0] in MAJOR    for d in diffs): return "major"
    if any(d[0] in ADVISORY for d in diffs): return "advisory"
    if diffs:                                return "minor"
    return "ok"

def layout_severity(layout_diffs):
    if not layout_diffs: return "ok"
    if any(d[3] >= LAYOUT_MAJOR_PX for d in layout_diffs): return "layout-major"
    return "layout-minor"

SEV_RANK = {"critical": 5, "unpaired": 4, "layout-major": 3, "major": 2, "advisory": 1, "layout-minor": 1, "minor": 1, "ok": 0}

rows = []
for i, ie in enumerate(impl):
    re = ref_by_i.get(i)
    if not re or re.get("miss"):
        rows.append({
            "i": i, "sev": "unpaired",
            "impl_tag": ie["tag"], "impl_cls": ie["cls"], "txt": ie["txt"],
            "impl_xy": (ie["x"], ie["y"]),
            "ref_xy": None,
            "diffs": [], "layout_diffs": [], "subpx_drift": [],
        }); continue
    # Pairing is decided upstream by ui_clone.tree_diff_pairing (content +
    # structure). A non-miss `re` here is an accepted pair, so the per-pair
    # style + layout diff runs unconditionally — the screen-coordinate gate
    # that used to drop content-correct pairs across differently-tall pages is
    # gone. dx/dy are kept for reporting only (cross-page position delta).
    dx = abs(ie["x"] - re["x"]); dy = abs(ie["y"] - re["y"])
    diffs = diff_styles(ie["style"], re["style"])
    layout_diffs = diff_layout(ie, re)
    style_sev = severity_of(diffs)
    lay_sev   = layout_severity(layout_diffs)
    sev = style_sev if SEV_RANK[style_sev] >= SEV_RANK[lay_sev] else lay_sev
    rows.append({
        "i": i, "sev": sev,
        "impl_tag": ie["tag"], "impl_cls": ie["cls"], "txt": ie["txt"],
        "impl_xy": (ie["x"], ie["y"]),
        "impl_box": {"top": ie.get("top"), "left": ie.get("left"), "w": ie.get("w"), "h": ie.get("h")},
        "ref_tag": re["tag"], "ref_cls": re["cls"], "ref_txt": re["txt"],
        "ref_xy": (re["x"], re["y"]),
        "ref_box": {"top": re.get("top"), "left": re.get("left"), "w": re.get("w"), "h": re.get("h")},
        "dx": dx, "dy": dy,
        "diffs": diffs,
        "layout_diffs": layout_diffs,
        "subpx_drift": subpx_drift(ie["style"], re["style"]),
    })

rows.sort(key=lambda r: (-SEV_RANK[r["sev"]], -impl[r["i"]]["area"]))

# Counts
counts = {"critical": 0, "major": 0, "layout-major": 0, "advisory": 0, "minor": 0, "layout-minor": 0, "ok": 0, "unpaired": 0}
for r in rows: counts[r["sev"]] += 1

SEV_ICON = {"critical": "🔴", "major": "🟠", "layout-major": "🟣",
            "minor": "🟡", "layout-minor": "🟦", "unpaired": "⚪"}

def fmt_layout(lds):
    return "; ".join(f"`{ax}`: {iv}→{rv} Δ{d:.1f}" for ax, iv, rv, d in lds[:3])

# ── Markdown ──
md_path = os.path.join(out_dir, "tree-diff.md")
with open(md_path, "w") as f:
    f.write("# Tree Diff Report\n\n")
    f.write(f"**Walked**: {len(impl)} elements  ")
    f.write(f"**Critical**: {counts['critical']}  ")
    f.write(f"**Major**: {counts['major']}  ")
    f.write(f"**Layout-major**: {counts['layout-major']}  ")
    f.write(f"**Advisory**: {counts['advisory']}  ")
    f.write(f"**Minor**: {counts['minor']}  ")
    f.write(f"**Layout-minor**: {counts['layout-minor']}  ")
    f.write(f"**Unpaired**: {counts['unpaired']}  ")
    f.write(f"**Match**: {counts['ok']}\n\n")
    f.write("| # | Sev | Impl tag.cls | Text | xy (impl→ref) | Property diffs | Layout diffs |\n")
    f.write("|---|---|---|---|---|---|---|\n")
    for r in rows:
        if r["sev"] == "ok": continue
        sev_label = SEV_ICON[r["sev"]]
        impl_id = f"{r['impl_tag']}.{r['impl_cls'][:25]}".rstrip(".")
        txt = r["txt"][:24]
        if r["ref_xy"]:
            xy = f"({r['impl_xy'][0]},{r['impl_xy'][1]})→({r['ref_xy'][0]},{r['ref_xy'][1]}) Δ{r['dx']:.0f},{r['dy']:.0f}"
        else:
            xy = f"({r['impl_xy'][0]},{r['impl_xy'][1]}) ref miss"
        if r["diffs"]:
            d = "; ".join(f"`{p}`: {str(a)[:18]}→{str(b)[:18]}" for p, a, b in r["diffs"][:3])
            if len(r["diffs"]) > 3: d += f" (+{len(r['diffs'])-3})"
        else:
            d = "—" if r["sev"] != "unpaired" else "(unpaired)"
        ld = r.get("layout_diffs") or []
        ld_str = fmt_layout(ld) if ld else "—"
        if len(ld) > 3: ld_str += f" (+{len(ld)-3})"
        f.write(f"| {r['i']} | {sev_label} | `{impl_id}` | {txt} | {xy} | {d} | {ld_str} |\n")

    # ── Sub-pixel typographic drift (within PASS tolerance — reported, not failing) ──
    # norm() rounds fontSize/lineHeight/letterSpacing to 0.5px for the gate, so a
    # 15.84px-vs-16.0px difference is masked from the table above (sev "ok"). It is
    # still real token-system drift, so surface the RAW unrounded delta here. This
    # section is informational only — it does NOT change any PASS/FAIL count.
    drift_rows = [r for r in rows if r.get("subpx_drift")]
    if drift_rows:
        f.write("\n## Sub-pixel typographic drift (within tolerance — reported, not failing)\n\n")
        f.write("| # | Impl tag.cls | Text | Raw drift (impl→ref Δpx) |\n")
        f.write("|---|---|---|---|\n")
        for r in drift_rows:
            impl_id = f"{r['impl_tag']}.{r['impl_cls'][:25]}".rstrip(".")
            txt = (r["txt"] or "")[:24]
            ds = "; ".join(f"`{p}`: {a}→{b} Δ{d}" for p, a, b, d in r["subpx_drift"])
            f.write(f"| {r['i']} | `{impl_id}` | {txt} | {ds} |\n")

# ── JSON ──
json_path = os.path.join(out_dir, "tree-diff.json")
with open(json_path, "w") as f:
    json.dump(rows, f, indent=2, default=str)

# ── Status sidecar (gate-readable) ──
# tree-diff.json is raw-pair data; verification-plan gates need a top-level
# `status` field to decide pass/fail. Write a separate sidecar that the
# gate consumer reads. status=fail when any critical/major/layout-major
# pair exists — same definition as the exit code below.
status_path = os.path.join(out_dir, "tree-diff-status.json")
total_fail = counts["critical"] + counts["major"] + counts["layout-major"]
pairing_fail = counts["unpaired"] >= 3 and counts["unpaired"] > counts["ok"]
status = "fail" if total_fail > 0 or pairing_fail else "pass"
if total_fail > 0:
    reason = f"{total_fail} critical/major element mismatch(es)"
elif pairing_fail:
    reason = f"tree pairing failed: unpaired={counts['unpaired']} ok={counts['ok']}"
else:
    reason = f"All {len(impl)} elements within style + layout tolerance"
with open(status_path, "w") as f:
    json.dump({
        "schemaVersion": 1,
        "status": status,
        "elements_walked": len(impl),
        "counts": counts,
        "errorCount": total_fail + (counts["unpaired"] if pairing_fail else 0),
        "reason": reason,
    }, f, indent=2)

# ── Stdout ──
print(f"  Walked {len(impl)} elements")
print(f"  🔴 critical: {counts['critical']}   🟠 major: {counts['major']}   🟣 layout-major: {counts['layout-major']}   🔶 advisory: {counts['advisory']}   🟡 minor: {counts['minor']}   🟦 layout-minor: {counts['layout-minor']}   ⚪ unpaired: {counts['unpaired']}   ✓ ok: {counts['ok']}")
print(f"  Report: {md_path}")
print(f"  Raw:    {json_path}")
_drift_n = sum(1 for r in rows if r.get("subpx_drift"))
if _drift_n:
    print(f"  📐 sub-pixel typographic drift (within tolerance, reported): {_drift_n} element(s)")
print()
if counts["critical"] or counts["major"] or counts["layout-major"] or pairing_fail:
    print("Top critical/major/layout-major:")
    for r in rows[:8]:
        if r["sev"] in ("ok", "minor", "layout-minor"): continue
        sev_label = SEV_ICON[r["sev"]]
        impl_id = f"{r['impl_tag']}.{r['impl_cls'][:30]}"
        txt = (r["txt"] or "")[:20]
        bits = []
        for p, a, b in (r.get("diffs") or [])[:2]:
            bits.append(f"{p}: {str(a)[:18]}→{str(b)[:18]}")
        for ax, iv, rv, d in (r.get("layout_diffs") or [])[:2]:
            bits.append(f"{ax}:{iv}→{rv} Δ{d:.1f}")
        d = "; ".join(bits) or "(unpaired)"
        print(f"  {sev_label} #{r['i']}  {impl_id}  '{txt}'  | {d}")

sys.exit(1 if (counts["critical"] or counts["major"] or counts["layout-major"] or pairing_fail) else 0)
PYEOF
