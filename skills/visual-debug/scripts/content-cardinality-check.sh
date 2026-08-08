#!/usr/bin/env bash
# content-cardinality-check.sh — repeated-group count parity against ref
# ground truth (omx postmortem: 9 hardcoded storyCards shipped where the ref
# rendered the full list; no gate counted rendered group members).
#
# Signatures derive from <ref-dir>/dom-scaffold.json (sibling groups >=3
# sharing tag+class under one parent — ui_clone.content_cardinality). Expected
# and actual counts come from the ref and impl RENDERED runtime DOM at the same
# viewport with a visible-box filter:
# source arrays, metadata strings, or hidden duplicate DOM never satisfy the
# count. Duplication (impl > ref: looping carousels, virtualized clones) is
# an advisory note, not a fail. Tolerance: UI_CLONE_CARDINALITY_TOLERANCE
# (default 0).
#
# Usage: bash content-cardinality-check.sh <session> <impl-url> <ref-dir>
# Output: <ref-dir>/content-cardinality.json with "status": "pass" | "fail"
# Exit: 0 on completed comparison (gate reads status), 2 on setup errors.

set -uo pipefail

if ! command -v agent-browser &>/dev/null; then
  echo "ERROR: agent-browser not found. Install: npm i -g agent-browser"
  exit 2
fi

SESSION="${1:?Usage: content-cardinality-check.sh <session> <impl-url> <ref-dir>}"
IMPL_URL="${2:?Missing impl-url}"
REF_DIR="${3:?Missing ref-dir}"
TOLERANCE="${UI_CLONE_CARDINALITY_TOLERANCE:-0}"

if [ ! -d "$REF_DIR" ]; then
  echo "ERROR: ref-dir does not exist: $REF_DIR"
  exit 2
fi
if [ ! -f "$REF_DIR/dom-scaffold.json" ]; then
  echo "ERROR: $REF_DIR/dom-scaffold.json missing — run dom-scaffold.sh first"
  exit 2
fi

REF_SESSION="${SESSION}-ref"
cleanup() {
  agent-browser --session "$SESSION" close >/dev/null 2>&1
  agent-browser --session "$REF_SESSION" close >/dev/null 2>&1
}
trap cleanup EXIT

SIGS_JSON=$(python3 - "$REF_DIR/dom-scaffold.json" <<'PY'
import json, sys
from ui_clone.content_cardinality import repeated_group_signatures
scaffold = json.load(open(sys.argv[1]))
print(json.dumps(repeated_group_signatures(scaffold)))
PY
)
if [ -z "$SIGS_JSON" ]; then
  echo "ERROR: signature derivation failed"
  exit 2
fi

# Count VISIBLE rendered members per signature in each runtime DOM. The
# visible-box filter (like image-fidelity) excludes display:none clones,
# zero-size stubs, and detached templates.
COUNT_JS="(() => {
  const sigs = ${SIGS_JSON};
  const esc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/([^a-zA-Z0-9_-])/g, '\\\\$1');
  const out = {};
  for (const sig of sigs) {
    const parentSel = sig.parentClass ? '.' + esc(sig.parentClass) : 'body';
    const childSel = sig.childTag + '.' + esc(sig.childClass);
    let count = 0;
    let parents = [];
    try { parents = Array.from(document.querySelectorAll(parentSel)); } catch (e) { parents = []; }
    if (parents.length === 0) parents = [document.body];
    const seen = new Set();
    for (const p of parents) {
      let members = [];
      try { members = Array.from(p.querySelectorAll(childSel)); } catch (e) { members = []; }
      for (const m of members) {
        if (seen.has(m)) continue;
        seen.add(m);
        const r = m.getBoundingClientRect();
        const st = getComputedStyle(m);
        if (st.display === 'none' || st.visibility === 'hidden') continue;
        // Thin-by-design elements (hr/divider class, boxHeight<=2) can never
        // satisfy a height floor — the e2e-9 live ref failed its OWN scaffold
        // truth on 1px <hr> dividers (4 -> 0). For them, presence = visible
        // computed style + real width; hidden/zero-width stubs stay excluded.
        const boxOk = r.width > 2 && r.height > 2;
        const thinOk = r.height <= 2 && r.width > 2;
        if (boxOk || thinOk) count++;
      }
    }
    out[sig.parentClass + '|' + sig.childTag + '|' + sig.childClass] = count;
  }
  return JSON.stringify(out);
})()"

REF_URL=$(python3 - "$REF_DIR/head.json" <<'PY'
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
if path.is_file():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("url") or data.get("sourceUrl") or ""
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            print(value)
    except (OSError, json.JSONDecodeError):
        pass
PY
)
REF_RAW=""
if [ -n "$REF_URL" ]; then
  agent-browser --session "$REF_SESSION" open "$REF_URL" >/dev/null 2>&1
  agent-browser --session "$REF_SESSION" set viewport 1280 800 >/dev/null 2>&1
  agent-browser --session "$REF_SESSION" wait 2500 >/dev/null 2>&1
  REF_RAW=$(agent-browser --session "$REF_SESSION" eval "$COUNT_JS" 2>/dev/null | tail -1)
fi

agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1
agent-browser --session "$SESSION" set viewport 1280 800 >/dev/null 2>&1
agent-browser --session "$SESSION" wait 2500 >/dev/null 2>&1
RAW=$(agent-browser --session "$SESSION" eval "$COUNT_JS" 2>/dev/null | tail -1)
if [ -z "$RAW" ]; then
  echo "ERROR: runtime count eval returned nothing"
  exit 2
fi

OUT="$REF_DIR/content-cardinality.json"
python3 - "$OUT" "$TOLERANCE" "$SIGS_JSON" "$REF_RAW" "$RAW" <<'PY'
import json, sys
from ui_clone.content_cardinality import cardinality_verdict, with_live_reference_counts
out_path, tolerance, sigs_raw, ref_counts_raw, counts_raw = sys.argv[1:6]
sigs = json.loads(sigs_raw)
reference_count_source = "dom-scaffold"
if ref_counts_raw:
    ref_counts = json.loads(ref_counts_raw)
    if isinstance(ref_counts, str):
        ref_counts = json.loads(ref_counts)
    sigs = with_live_reference_counts(sigs, ref_counts)
    reference_count_source = "live-reference"
counts = json.loads(counts_raw)
if isinstance(counts, str):
    counts = json.loads(counts)
res = cardinality_verdict(sigs, counts, tolerance=int(tolerance))
res["referenceCountSource"] = reference_count_source
with open(out_path, "w") as f:
    json.dump(res, f, indent=2)
print(f"content-cardinality: {res['status']} "
      f"({len(res['groups'])} group(s), {res['failedGroups']} short)")
for g in res["groups"]:
    if g["status"] == "fail":
        print(f"  ❌ {g['parentClass']} > {g['childTag']}.{g['childClass']}: "
              f"impl {g['implCount']} < ref {g['refCount']}")
PY
