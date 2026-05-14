#!/usr/bin/env bash
# tree-diff.sh — Exhaustive per-element CSS diff between ref and impl
#
# Walks every visible element on impl (≥ MIN_SIZE px, ranked by area),
# pairs each with the ref element at the same screen-center via
# elementFromPoint, and runs computed-style diff per pair.
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
#   WAIT_MS=4000              Settle time
#   MIN_SIZE=16               Skip elements smaller than NxN px
#   MAX_ELEMENTS=200          Cap per-page walk (top N by area)
#   PAIR_TOLERANCE=10         Max center-distance for valid pair (px)
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
MIN_SIZE="${MIN_SIZE:-16}"
MAX_ELEMENTS="${MAX_ELEMENTS:-200}"
PAIR_TOLERANCE="${PAIR_TOLERANCE:-10}"

mkdir -p "$OUT_DIR"

REF_SESS="${SESSION}-tree-ref"
IMPL_SESS="${SESSION}-tree-impl"

TMP_IMPL=$(mktemp /tmp/tree-diff-impl-XXXXXX.json)
TMP_REF=$(mktemp /tmp/tree-diff-ref-XXXXXX.json)

cleanup() {
  agent-browser --session "$REF_SESS" close >/dev/null 2>&1 || true
  agent-browser --session "$IMPL_SESS" close >/dev/null 2>&1 || true
  rm -f "$TMP_IMPL" "$TMP_REF"
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
(() => {
  const props = ['fontFamily','fontSize','fontWeight','fontStyle','letterSpacing',
                 'lineHeight','textTransform','textAlign','color','backgroundColor',
                 'display','position','padding','margin','borderRadius',
                 'borderTopWidth','borderTopColor','opacity'];
  const SKIP_TAGS = new Set(['SCRIPT','STYLE','META','LINK','HEAD','TITLE','NOSCRIPT','BR','HR']);
  const minSize = ${MIN_SIZE};
  const maxN    = ${MAX_ELEMENTS};
  const out = [];
  const all = document.querySelectorAll('body *');
  for (const el of all) {
    if (SKIP_TAGS.has(el.tagName)) continue;
    const r = el.getBoundingClientRect();
    // Allow thin separators/borders (1-3px tall, wide) — important for layout diff
    const isThin = (r.height >= 0.5 && r.height < 4 && r.width >= 80) ||
                   (r.width  >= 0.5 && r.width  < 4 && r.height >= 80);
    if (!isThin && (r.width < minSize || r.height < minSize)) continue;
    if (r.bottom < 0 || r.top > window.innerHeight) continue;
    if (r.right  < 0 || r.left > window.innerWidth)  continue;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) continue;
    const cx = Math.max(1, Math.min(window.innerWidth  - 1, r.left + r.width  / 2));
    const cy = Math.max(1, Math.min(window.innerHeight - 1, r.top  + r.height / 2));
    const txt = (el.textContent || '').trim().replace(/\s+/g,' ').slice(0, 30);
    const styleObj = {};
    props.forEach(p => styleObj[p] = s[p]);
    out.push({
      tag: el.tagName,
      cls: (el.className && el.className.toString) ? el.className.toString().slice(0, 60) : '',
      txt,
      x: +cx.toFixed(1), y: +cy.toFixed(1),
      top: +r.top.toFixed(1), left: +r.left.toFixed(1),
      w: +r.width.toFixed(1), h: +r.height.toFixed(1),
      area: +(r.width * r.height).toFixed(0),
      thin: isThin,
      style: styleObj,
    });
  }
  out.sort((a,b) => b.area - a.area);
  return JSON.stringify(out.slice(0, maxN));
})()
JSEOF
)
agent-browser --session "$IMPL_SESS" eval "$WALK_JS" > "$TMP_IMPL" 2>&1
if [ ! -s "$TMP_IMPL" ]; then
  echo "ERROR: impl walk returned empty"; exit 2
fi

# ── Step 2: pair each impl element with ref via elementFromPoint ──
echo "  ▸ Pairing on ref via elementFromPoint..."
# Pass impl JSON to ref-side eval via window var (encoded as JSON literal in JS)
PAIR_JS=$(python3 - "$TMP_IMPL" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    raw = f.read().strip()
# agent-browser wraps string results in extra JSON quotes
if raw.startswith('"') and raw.endswith('"'):
    impl_list = json.loads(json.loads(raw))
else:
    impl_list = json.loads(raw)
points = [{"i": i, "x": e["x"], "y": e["y"]} for i, e in enumerate(impl_list)]
points_json = json.dumps(points)
js = """
(() => {
  const points = %s;
  const props = ['fontFamily','fontSize','fontWeight','fontStyle','letterSpacing',
                 'lineHeight','textTransform','textAlign','color','backgroundColor',
                 'display','position','padding','margin','borderRadius',
                 'borderTopWidth','borderTopColor','opacity'];
  const out = [];
  for (const p of points) {
    let el = document.elementFromPoint(p.x, p.y);
    if (!el) { out.push({ i: p.i, miss: true }); continue; }
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    const styleObj = {};
    props.forEach(k => styleObj[k] = s[k]);
    const cx = r.left + r.width / 2;
    const cy = r.top  + r.height / 2;
    out.push({
      i: p.i,
      tag: el.tagName,
      cls: (el.className && el.className.toString) ? el.className.toString().slice(0, 60) : '',
      txt: (el.textContent || '').trim().replace(/\\s+/g,' ').slice(0, 30),
      x: +cx.toFixed(1), y: +cy.toFixed(1),
      top: +r.top.toFixed(1), left: +r.left.toFixed(1),
      w: +r.width.toFixed(1), h: +r.height.toFixed(1),
      style: styleObj,
    });
  }
  return JSON.stringify(out);
})()
""" % points_json
print(js)
PYEOF
)
agent-browser --session "$REF_SESS" eval "$PAIR_JS" > "$TMP_REF" 2>&1
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
# everything else = minor

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
    if diffs:                                return "minor"
    return "ok"

def layout_severity(layout_diffs):
    if not layout_diffs: return "ok"
    if any(d[3] >= LAYOUT_MAJOR_PX for d in layout_diffs): return "layout-major"
    return "layout-minor"

SEV_RANK = {"critical": 5, "unpaired": 4, "layout-major": 3, "major": 2, "layout-minor": 1, "minor": 1, "ok": 0}

rows = []
for i, ie in enumerate(impl):
    re = ref_by_i.get(i)
    if not re or re.get("miss"):
        rows.append({
            "i": i, "sev": "unpaired",
            "impl_tag": ie["tag"], "impl_cls": ie["cls"], "txt": ie["txt"],
            "impl_xy": (ie["x"], ie["y"]),
            "ref_xy": None,
            "diffs": [], "layout_diffs": [],
        }); continue
    # confidence by center distance
    dx = abs(ie["x"] - re["x"]); dy = abs(ie["y"] - re["y"])
    pair_ok = (dx <= tol and dy <= tol)
    diffs = diff_styles(ie["style"], re["style"]) if pair_ok else []
    layout_diffs = diff_layout(ie, re) if pair_ok else []
    if not pair_ok:
        sev = "unpaired"
    else:
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
    })

rows.sort(key=lambda r: (-SEV_RANK[r["sev"]], -impl[r["i"]]["area"]))

# Counts
counts = {"critical": 0, "major": 0, "layout-major": 0, "minor": 0, "layout-minor": 0, "ok": 0, "unpaired": 0}
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
with open(status_path, "w") as f:
    json.dump({
        "schemaVersion": 1,
        "status": "fail" if total_fail > 0 else "pass",
        "elements_walked": len(impl),
        "counts": counts,
        "errorCount": total_fail,
        "reason": (
            f"{total_fail} critical/major element mismatch(es)"
            if total_fail > 0
            else f"All {len(impl)} elements within style + layout tolerance"
        ),
    }, f, indent=2)

# ── Stdout ──
print(f"  Walked {len(impl)} elements")
print(f"  🔴 critical: {counts['critical']}   🟠 major: {counts['major']}   🟣 layout-major: {counts['layout-major']}   🟡 minor: {counts['minor']}   🟦 layout-minor: {counts['layout-minor']}   ⚪ unpaired: {counts['unpaired']}   ✓ ok: {counts['ok']}")
print(f"  Report: {md_path}")
print(f"  Raw:    {json_path}")
print()
if counts["critical"] or counts["major"] or counts["layout-major"]:
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

sys.exit(1 if (counts["critical"] or counts["major"] or counts["layout-major"]) else 0)
PYEOF
