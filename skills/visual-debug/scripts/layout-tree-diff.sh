#!/usr/bin/env bash
# layout-tree-diff.sh — Geometry diff via signature-based pairing
#
# Walks both DOMs, builds a stable "signature" per element (text + tag + class hash + size class),
# then pairs impl ↔ ref by best signature match. Reports geometry deltas (top, left, w, h)
# regardless of where the element moved on screen.
#
# Catches what tree-diff misses:
#   - Right element, wrong Y (e.g., footer border line shifted by 7px)
#   - Right element, wrong width/height (container that should be 50% but is 100%)
#   - Element in different stacking position (z-order shifted)
#
# Usage: bash layout-tree-diff.sh <session> <orig-url> <impl-url> [out-dir]
#
# Env:
#   VIEW_W=1440 VIEW_H=900    Viewport
#   WAIT_MS=4000              Settle time
#   MIN_SIZE=8                Skip tinier than NxN px (1px lines auto-included via thin filter)
#   MAX_ELEMENTS=300          Cap walk
#   MINOR_PX=1.5              Δ within → minor (sub-pixel)
#   MAJOR_PX=4.0              Δ above → major
#
# Output:
#   <dir>/layout-tree-diff.md   — Markdown report
#   <dir>/layout-tree-diff.json — Raw pairs
# Exit 0 if no major mismatches; 1 otherwise.

set -uo pipefail

if ! command -v agent-browser &>/dev/null; then echo "ERROR: agent-browser not found"; exit 2; fi
if ! command -v python3 &>/dev/null;       then echo "ERROR: python3 not found";       exit 2; fi

SESSION="${1:?Usage: layout-tree-diff.sh <session> <orig-url> <impl-url> [out-dir]}"
ORIG_URL="${2:?Missing orig-url}"
IMPL_URL="${3:?Missing impl-url}"
OUT_DIR="${4:-tmp/layout-tree-diff}"

VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"
WAIT_MS="${WAIT_MS:-4000}"
MIN_SIZE="${MIN_SIZE:-8}"
MAX_ELEMENTS="${MAX_ELEMENTS:-300}"
MINOR_PX="${MINOR_PX:-1.5}"
MAJOR_PX="${MAJOR_PX:-4.0}"

mkdir -p "$OUT_DIR"

REF_SESS="${SESSION}-ltd-ref"
IMPL_SESS="${SESSION}-ltd-impl"

# L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
TMP_IMPL="$(mktemp /tmp/ltd-impl-XXXXXX)"
mv "$TMP_IMPL" "${TMP_IMPL}.json"
TMP_IMPL="${TMP_IMPL}.json"
# L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
TMP_REF="$(mktemp /tmp/ltd-ref-XXXXXX)"
mv "$TMP_REF" "${TMP_REF}.json"
TMP_REF="${TMP_REF}.json"

cleanup() {
  agent-browser --session "$REF_SESS"  close >/dev/null 2>&1 || true
  agent-browser --session "$IMPL_SESS" close >/dev/null 2>&1 || true
  rm -f "$TMP_IMPL" "$TMP_REF"
}
trap cleanup EXIT

echo "═══ Layout Tree Diff (signature pairing, geometry-only) ═══"
echo "  orig: $ORIG_URL"
echo "  impl: $IMPL_URL"
echo "  viewport: ${VIEW_W}x${VIEW_H}, max: $MAX_ELEMENTS, minor=${MINOR_PX}px major=${MAJOR_PX}px"
echo ""

agent-browser --session "$REF_SESS"  open "$ORIG_URL" >/dev/null 2>&1
agent-browser --session "$IMPL_SESS" open "$IMPL_URL" >/dev/null 2>&1
agent-browser --session "$REF_SESS"  set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1 || true
agent-browser --session "$IMPL_SESS" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1 || true
agent-browser --session "$REF_SESS"  wait "$WAIT_MS" >/dev/null 2>&1
agent-browser --session "$IMPL_SESS" wait "$WAIT_MS" >/dev/null 2>&1

# ── Walk: collect signature + bbox for each side ──
WALK_JS=$(cat <<JSEOF
(() => {
  const SKIP = new Set(['SCRIPT','STYLE','META','LINK','HEAD','TITLE','NOSCRIPT','BR','HR']);
  const minSize = ${MIN_SIZE};
  const maxN = ${MAX_ELEMENTS};
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    if (SKIP.has(el.tagName)) continue;
    const r = el.getBoundingClientRect();
    const isThin = (r.height >= 0.5 && r.height < 4 && r.width >= 80) ||
                   (r.width  >= 0.5 && r.width  < 4 && r.height >= 80);
    if (!isThin && (r.width < minSize || r.height < minSize)) continue;
    if (r.bottom < 0 || r.top > document.documentElement.scrollHeight) continue;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) continue;

    const txt = (el.textContent || '').trim().replace(/\s+/g,' ').slice(0, 50);
    const cls = (el.className && el.className.toString) ? el.className.toString().slice(0, 80) : '';
    // Size bucket: log2 of area (rough class for tie-breaking)
    const area = r.width * r.height;
    const sizeBucket = Math.floor(Math.log2(Math.max(1, area)));
    out.push({
      tag: el.tagName,
      cls,
      txt,
      sizeBucket,
      thin: isThin,
      top: +r.top.toFixed(1), left: +r.left.toFixed(1),
      w:   +r.width.toFixed(1), h:    +r.height.toFixed(1),
      area: +area.toFixed(0),
      // Anchor inside parent: helps disambiguate identical leaves in different lists
      parentTag: el.parentElement ? el.parentElement.tagName : '',
      parentCls: (el.parentElement && el.parentElement.className && el.parentElement.className.toString) ? el.parentElement.className.toString().slice(0, 50) : '',
      childIndex: el.parentElement ? Array.from(el.parentElement.children).indexOf(el) : -1,
    });
  }
  out.sort((a,b) => b.area - a.area);
  return JSON.stringify(out.slice(0, maxN));
})()
JSEOF
)

echo "  ▸ Walking impl..."
agent-browser --session "$IMPL_SESS" eval "$WALK_JS" > "$TMP_IMPL" 2>&1
echo "  ▸ Walking ref..."
agent-browser --session "$REF_SESS"  eval "$WALK_JS" > "$TMP_REF"  2>&1

if [ ! -s "$TMP_IMPL" ] || [ ! -s "$TMP_REF" ]; then
  echo "ERROR: walk returned empty"; exit 2
fi

echo "  ▸ Pairing by signature + diffing geometry..."
echo ""

python3 - "$TMP_IMPL" "$TMP_REF" "$OUT_DIR" "$MINOR_PX" "$MAJOR_PX" <<'PYEOF'
import json, sys, os, re

def parse(path):
    with open(path) as f: raw = f.read().strip()
    if raw.startswith('"') and raw.endswith('"'):
        return json.loads(json.loads(raw))
    return json.loads(raw)

impl = parse(sys.argv[1])
ref  = parse(sys.argv[2])
out_dir   = sys.argv[3]
MINOR_PX  = float(sys.argv[4])
MAJOR_PX  = float(sys.argv[5])

# Signature: (tag, normalized class set, text fingerprint, parent ref).
# Class normalization: sort tokens, drop volatile state classes (is-*, hover, focus).
VOLATILE = re.compile(r'^(is-|has-|hover|focus|active|open|visible|hidden)')

def cls_sig(cls):
    if not cls: return ""
    toks = [t for t in cls.split() if not VOLATILE.match(t)]
    toks.sort()
    return " ".join(toks[:6])  # top 6 tokens

def text_sig(txt):
    # Compact: lowercase, alphanumerics + length signature
    if not txt: return ""
    t = re.sub(r'\W+', '', txt.lower())[:40]
    return t

def signature(el):
    """Class names diverge between ref and impl (Webflow vs Tailwind), so don't lean on them.
    For elements with stable text content → (tag, text). For structural elements → (tag, parentTag, sizeBucket)."""
    txt = text_sig(el.get("txt", ""))
    if txt and len(txt) >= 3:
        return ("text", el["tag"], txt[:30])
    # Structural: pair by hierarchy + size bucket (rough)
    return ("struct", el["tag"], el.get("parentTag", ""), el.get("sizeBucket", 0))

# Build signature → list of indices for ref (allow multi-match resolved by best-fit)
ref_by_sig = {}
for j, r in enumerate(ref):
    ref_by_sig.setdefault(signature(r), []).append(j)

def best_fit(impl_el, ref_indices):
    """Pick the ref whose bbox is most similar to impl in size (prevents matching
    sibling repeats by area). For thin elements, also weight position."""
    if not ref_indices: return None
    iw, ih = impl_el["w"], impl_el["h"]
    iy = impl_el["top"]
    best = None; best_score = float("inf")
    for j in ref_indices:
        r = ref[j]
        sz_score = abs(r["w"] - iw) + abs(r["h"] - ih)
        # If text is empty (structural element), favor closer y to disambiguate stacks
        pos_score = abs(r["top"] - iy) * 0.05 if not impl_el.get("txt") else 0
        score = sz_score + pos_score
        if score < best_score:
            best_score = score; best = j
    return best

used_ref = set()
rows = []
for i, ie in enumerate(impl):
    sig = signature(ie)
    candidates = [j for j in ref_by_sig.get(sig, []) if j not in used_ref]
    j = best_fit(ie, candidates)
    if j is None:
        rows.append({
            "i": i, "sev": "unpaired",
            "tag": ie["tag"], "cls": ie["cls"], "txt": ie["txt"],
            "impl_box": {k: ie[k] for k in ("top","left","w","h")},
            "ref_box": None, "deltas": [],
        }); continue
    used_ref.add(j)
    re_ = ref[j]
    deltas = []
    for axis in ("top", "left", "w", "h"):
        d = abs(ie[axis] - re_[axis])
        if d > MINOR_PX:
            deltas.append((axis, ie[axis], re_[axis], d))
    if not deltas:
        sev = "ok"
    elif any(d[3] >= MAJOR_PX for d in deltas):
        sev = "major"
    else:
        sev = "minor"
    rows.append({
        "i": i, "sev": sev,
        "tag": ie["tag"], "cls": ie["cls"], "txt": ie["txt"],
        "impl_box": {k: ie[k] for k in ("top","left","w","h")},
        "ref_box":  {k: re_[k] for k in ("top","left","w","h")},
        "deltas": deltas,
        "ref_idx": j,
    })

SEV_RANK = {"unpaired": 3, "major": 2, "minor": 1, "ok": 0}
counts = {"unpaired": 0, "major": 0, "minor": 0, "ok": 0}
for r in rows: counts[r["sev"]] += 1
rows.sort(key=lambda r: -SEV_RANK[r["sev"]])

SEV_ICON = {"major": "🟣", "minor": "🟦", "unpaired": "⚪"}

# ── Markdown ──
md = os.path.join(out_dir, "layout-tree-diff.md")
with open(md, "w") as f:
    f.write("# Layout Tree Diff Report (signature pairing)\n\n")
    f.write(f"**Walked impl**: {len(impl)}  ")
    f.write(f"**Walked ref**: {len(ref)}  ")
    f.write(f"**Major shifts**: {counts['major']}  ")
    f.write(f"**Minor shifts**: {counts['minor']}  ")
    f.write(f"**Unpaired**: {counts['unpaired']}  ")
    f.write(f"**Match**: {counts['ok']}\n\n")
    f.write(f"Tolerance: minor ≥ {MINOR_PX}px, major ≥ {MAJOR_PX}px\n\n")
    f.write("| # | Sev | tag.cls | Text | Impl box (t,l,w,h) | Ref box | Deltas |\n")
    f.write("|---|---|---|---|---|---|---|\n")
    for r in rows:
        if r["sev"] == "ok": continue
        sev_label = SEV_ICON[r["sev"]]
        ident = f"{r['tag']}.{r['cls'][:30]}".rstrip(".")
        txt = (r["txt"] or "")[:24]
        ib = r["impl_box"]
        i_box = f"{ib['top']},{ib['left']},{ib['w']},{ib['h']}"
        if r["ref_box"]:
            rb = r["ref_box"]
            r_box = f"{rb['top']},{rb['left']},{rb['w']},{rb['h']}"
            d = "; ".join(f"`{ax}` Δ{delta:.1f}" for ax, _, _, delta in r["deltas"][:4])
        else:
            r_box = "—"; d = "(no signature match)"
        f.write(f"| {r['i']} | {sev_label} | `{ident}` | {txt} | {i_box} | {r_box} | {d} |\n")

# ── JSON ──
js = os.path.join(out_dir, "layout-tree-diff.json")
with open(js, "w") as f:
    json.dump(rows, f, indent=2, default=str)

print(f"  Walked impl={len(impl)} ref={len(ref)}")
print(f"  🟣 major: {counts['major']}   🟦 minor: {counts['minor']}   ⚪ unpaired: {counts['unpaired']}   ✓ ok: {counts['ok']}")
print(f"  Report: {md}")
print(f"  Raw:    {js}")
print()
if counts["major"]:
    print("Top major shifts:")
    for r in rows[:8]:
        if r["sev"] != "major": continue
        ident = f"{r['tag']}.{r['cls'][:30]}"
        txt = (r["txt"] or "")[:20]
        d = "; ".join(f"{ax}:{i}→{rv} Δ{delta:.1f}" for ax, i, rv, delta in r["deltas"][:3])
        print(f"  🟣 #{r['i']}  {ident}  '{txt}'  | {d}")

sys.exit(1 if counts["major"] else 0)
PYEOF
