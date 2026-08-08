#!/usr/bin/env bash
# container-context-check.sh -- verify the implementation preserves the
# reference page's CSS container-query context.
#
# Why it matters:
#   CSS container queries size a subtree against the WIDTH of the nearest
#   ancestor with `container-type` -- not the viewport. Two silent failure modes
#   make a clone "recognizable but ~15% off":
#     1. Dropped container: the transpiler flattens away a `container-type`
#        ancestor, so `@container` descendants resolve against the wrong element.
#     2. Wrong container width: the container survives but renders at the wrong
#        width (e.g. a product grid that reflows 4 columns -> 2, doubling each
#        cell), so every `@container` utility snaps to a larger breakpoint and
#        the whole subtree mis-sizes.
#   Neither raises a console error and both are easy to miss in a single AE frame.
#   This is the static/structural counterpart to section-compare for container
#   layout -- it runs against a live impl and diffs the container inventory
#   against <ref-dir>/container-context.json (produced by
#   scripts/extract/extract-container-context.sh).
#
# Usage:
#   container-context-check.sh <session> <impl-url> <ref-dir> [w] [h]
#
# Exit: 0 = context preserved, 1 = divergence found, 2 = setup error

set -uo pipefail

SESSION="${1:?Usage: container-context-check.sh <session> <impl-url> <ref-dir> [w] [h]}"
URL="${2:?Missing impl-url}"
REF_DIR="${3:?Missing ref-dir}"
VIEW_W="${4:-${VIEW_W:-1280}}"
VIEW_H="${5:-${VIEW_H:-800}}"
WAIT_MS="${WAIT_MS:-3000}"

REF_JSON="$REF_DIR/container-context.json"
OUT_JSON="$REF_DIR/container-context-parity.json"

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "container-context-check: agent-browser not found on PATH" >&2
  exit 2
fi
if [ ! -f "$REF_JSON" ]; then
  echo "container-context-check: $REF_JSON missing -- run extract-container-context.sh on the ref first" >&2
  exit 2
fi

agent-browser --session "$SESSION" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1
agent-browser --session "$SESSION" navigate "$URL" >/dev/null 2>&1
sleep $((WAIT_MS / 1000))

EVAL_JS=$(cat <<'JSEOF'
(() => {
  const SKIP = new Set(['SCRIPT','STYLE','META','LINK','HEAD','TITLE','NOSCRIPT']);
  const items = [];
  document.querySelectorAll('*').forEach(el => {
    if (SKIP.has(el.tagName)) return;
    const cs = getComputedStyle(el);
    const ct = cs.containerType;
    if (!ct || ct === 'normal') return;
    const r = el.getBoundingClientRect();
    if (r.width < 1 && r.height < 1) return;
    const cls = (el.className && el.className.toString ? el.className.toString() : '').trim();
    const first = cls ? cls.split(/\s+/)[0] : '';
    const name = (cs.containerName && cs.containerName !== 'none') ? cs.containerName : '';
    items.push({ sig: name || first || el.tagName.toLowerCase(), width: Math.round(r.width) });
  });
  const bySig = {};
  items.forEach(it => {
    const g = bySig[it.sig] || (bySig[it.sig] = { sig: it.sig, count: 0, widths: [] });
    g.count += 1; g.widths.push(it.width);
  });
  const groups = Object.values(bySig).map(g => {
    const ws = g.widths.slice().sort((a, b) => a - b);
    return { sig: g.sig, count: g.count, medianWidth: ws[Math.floor(ws.length / 2)] };
  });
  return JSON.stringify({ totalContainers: items.length, groups: groups });
})()
JSEOF
)

TMP_OUT=$(mktemp)
agent-browser --session "$SESSION" eval "$EVAL_JS" > "$TMP_OUT" 2>&1 || {
  echo "container-context-check: agent-browser eval failed:" >&2
  head -c 500 "$TMP_OUT" >&2
  rm -f "$TMP_OUT"
  exit 2
}

python3 - "$REF_JSON" "$TMP_OUT" "$OUT_JSON" <<'PYEOF'
import json, sys

ref_path, impl_raw_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

ref = json.load(open(ref_path))
impl = json.load(open(impl_raw_path))
if isinstance(impl, str):
    impl = json.loads(impl)

# Drop threshold: fail if the impl is missing > 20% of the ref's containers.
# Width threshold: on a repeated module (ref count >= 3) a median width off by
# more than 25% means the container snapped to a different @container breakpoint.
DROP_FRAC = 0.20
WIDTH_FRAC = 0.25
REPEAT_MIN = 3

ref_total = ref.get("totalContainers", 0)
impl_total = impl.get("totalContainers", 0)
ref_groups = {g["sig"]: g for g in ref.get("groups", [])}
impl_groups = {g["sig"]: g for g in impl.get("groups", [])}

findings = []
for sig, rg in ref_groups.items():
    ig = impl_groups.get(sig)
    rc = rg.get("count", 0)
    rw = rg.get("medianWidth", 0) or 0
    if ig is None or ig.get("count", 0) == 0:
        findings.append({"sig": sig, "kind": "dropped-container",
                         "refCount": rc, "implCount": 0, "refWidth": rw})
        continue
    ic = ig.get("count", 0)
    iw = ig.get("medianWidth", 0) or 0
    if rc >= REPEAT_MIN and rw > 0 and abs(iw - rw) / rw > WIDTH_FRAC:
        findings.append({"sig": sig, "kind": "width-divergence",
                         "refCount": rc, "implCount": ic,
                         "refWidth": rw, "implWidth": iw,
                         "deltaPct": round(abs(iw - rw) / rw * 100)})

dropped = [f for f in findings if f["kind"] == "dropped-container"]
width = [f for f in findings if f["kind"] == "width-divergence"]
total_drop = ref_total - impl_total
fail = (ref_total > 0 and total_drop > ref_total * DROP_FRAC) or bool(width) or bool(dropped)

payload = {
    "schemaVersion": 1,
    "source": "container-context-check.sh",
    "status": "fail" if fail else "pass",
    "refTotal": ref_total,
    "implTotal": impl_total,
    "droppedContainerSignatures": len(dropped),
    "widthDivergences": len(width),
    "findings": findings,
}
json.dump(payload, open(out_path, "w"), indent=2, ensure_ascii=False)

if fail:
    print("container-context-check: FAIL")
    print("  ref containers=%d  impl containers=%d  (dropped %d)"
          % (ref_total, impl_total, total_drop))
    for f in findings[:12]:
        if f["kind"] == "dropped-container":
            print("  DROPPED   %-32s ref x%d (w=%d) -> impl x0"
                  % (f["sig"][:32], f["refCount"], f["refWidth"]))
        else:
            print("  WIDTH     %-32s ref w=%d -> impl w=%d (%d%% off)"
                  % (f["sig"][:32], f["refWidth"], f["implWidth"], f["deltaPct"]))
    sys.exit(1)

print("container-context-check: PASS (%d container contexts preserved)" % impl_total)
sys.exit(0)
PYEOF
STATUS=$?
rm -f "$TMP_OUT"
exit $STATUS
