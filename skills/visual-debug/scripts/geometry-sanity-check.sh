#!/usr/bin/env bash
# geometry-sanity-check.sh — rendered geometry must track the ref capture.
#
# Whole-page dSSIM / per-section AE structurally miss a failure class: a build
# can score its best dSSIM while the document is 2x the ref height (loop-129:
# best 0.1156 with docH ballooned), because pixel metrics compare what IS
# rendered, not how much page exists. This check renders the impl AT THE
# CAPTURE VIEWPORT (orig-layout.json viewportWidth/Height — apples-to-apples
# with the captured px, including vh-authored tracks) and compares:
#   - document scrollHeight vs orig-layout totalHeight
#   - each major section's rendered height vs section-map's captured height
#
# Verdict bands (env-tunable):
#   UI_CLONE_GEOM_DOCH_FAIL_PCT     (default 15)  UI_CLONE_GEOM_DOCH_WARN_PCT    (default 10)
#   UI_CLONE_GEOM_SECTION_FAIL_PCT  (default 25)  UI_CLONE_GEOM_SECTION_WARN_PCT (default 16)
#   UI_CLONE_GEOM_MIN_SECTION_PX    (default 200)
#
# Usage: geometry-sanity-check.sh <session> <impl-url> <ref-dir>
# Output: <ref-dir>/geometry-sanity.json  (+ verdict lines on stdout)
# Exit: 0 pass/warn, 1 fail, 2 usage/setup error
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
if [ -z "$REPO_ROOT" ] || [ ! -d "$REPO_ROOT/ui_clone" ]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi
if ! command -v agent-browser >/dev/null 2>&1; then
  echo "geometry-sanity: agent-browser not found" >&2
  exit 2
fi

SESSION="${1:?Usage: geometry-sanity-check.sh <session> <impl-url> <ref-dir>}"
URL="${2:?Missing impl-url}"
REF_DIR="${3:?Missing ref-dir}"

if [ ! -d "$REF_DIR" ]; then
  echo "geometry-sanity: ref-dir not found: $REF_DIR" >&2
  exit 2
fi
SECMAP="$REF_DIR/section-map.json"
if [ ! -f "$SECMAP" ]; then
  echo "geometry-sanity: SKIP — no section-map.json in $REF_DIR"
  exit 0
fi

PYBIN="python3"
run_py() { PYTHONPATH="$REPO_ROOT" "$PYBIN" "$@"; }

# ── Ref geometry + capture viewport (orig-layout.json optional). ─────────
REF_GEOM_B64=$(run_py - "$REF_DIR" <<'PY'
import base64, json, os, sys
ref = sys.argv[1]
out = {"docH": None, "vpW": 1280, "vpH": 800, "sections": []}
try:
    ol = json.load(open(os.path.join(ref, "orig-layout.json")))
    if isinstance(ol, dict):
        if isinstance(ol.get("totalHeight"), (int, float)):
            out["docH"] = ol["totalHeight"]
        if isinstance(ol.get("viewportWidth"), (int, float)) and ol["viewportWidth"] > 0:
            out["vpW"] = int(ol["viewportWidth"])
        if isinstance(ol.get("viewportHeight"), (int, float)) and ol["viewportHeight"] > 0:
            out["vpH"] = int(ol["viewportHeight"])
except (OSError, json.JSONDecodeError):
    pass
try:
    sm = json.load(open(os.path.join(ref, "section-map.json")))
    secs = sm.get("sections") if isinstance(sm, dict) else sm
    for s in secs or []:
        if not isinstance(s, dict):
            continue
        h = s.get("height")
        if not isinstance(h, (int, float)):
            continue
        cls = str(s.get("className") or s.get("cls") or "")
        out["sections"].append({
            "name": (s.get("id") or (cls.split()[0] if cls.split() else "")) or f"idx{s.get('index')}",
            "id": s.get("id") or "",
            "cls": cls.split()[0] if cls.split() else "",
            "refH": h,
            "refTop": s.get("top") if isinstance(s.get("top"), (int, float)) else None,
        })
    # docH fallback: furthest section extent
    if out["docH"] is None and out["sections"]:
        tops = [(s.get("top"), s.get("height")) for s in secs if isinstance(s, dict)]
        ext = [t + h for t, h in tops if isinstance(t, (int, float)) and isinstance(h, (int, float))]
        if ext:
            out["docH"] = max(ext)
except (OSError, json.JSONDecodeError):
    pass
sys.stdout.write(base64.b64encode(json.dumps(out).encode()).decode())
PY
)
if [ -z "$REF_GEOM_B64" ]; then
  echo "geometry-sanity: could not read ref geometry" >&2
  exit 2
fi

VP_W=$(run_py - "$REF_GEOM_B64" <<'PY'
import base64, json, sys
print(json.loads(base64.b64decode(sys.argv[1]))["vpW"])
PY
)
VP_H=$(run_py - "$REF_GEOM_B64" <<'PY'
import base64, json, sys
print(json.loads(base64.b64decode(sys.argv[1]))["vpH"])
PY
)

# ── Measure the impl at the capture viewport. ────────────────────────────
agent-browser --session "$SESSION" set viewport "$VP_W" "$VP_H" >/dev/null 2>&1
agent-browser --session "$SESSION" navigate "$URL" >/dev/null 2>&1
sleep 3

unwrap() { sed 's/^"//;s/"$//' | sed 's/\\"/"/g'; }

MEASURE_JS="(() => {
  const GEOM = JSON.parse(atob('$REF_GEOM_B64'));
  // Fix 96 (B2) — impl docH must capture Lenis / overflow-hidden balloons. The
  // document scrollbar may live on documentElement or an inner wrapper, so
  // body.scrollHeight alone under-measures a ballooned page (the 2.2x-tall
  // loop-129 case). Take the max of the document and the tallest inner scroll
  // container (same detection as section-compare's DETECT_SCROLLER_JS).
  let __maxInner = 0;
  document.querySelectorAll('*').forEach((el) => {
    const cs = getComputedStyle(el);
    if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll' || cs.overflowY === 'hidden')
        && el.scrollHeight > el.clientHeight + 100
        && el.scrollHeight > __maxInner) {
      __maxInner = el.scrollHeight;
    }
  });
  const out = { docH: Math.max(
    document.documentElement.scrollHeight,
    document.body.scrollHeight,
    __maxInner
  ), sections: [] };
  const pick = (cands, refTop) => {
    if (!cands.length) return null;
    if (refTop == null || cands.length === 1) return cands[0];
    // repeated classes (container/section reused across the page): choose the
    // instance whose page position is nearest the ref's captured top.
    let best = cands[0], bd = Infinity;
    for (const c of cands) {
      const d = Math.abs(c.getBoundingClientRect().top + window.scrollY - refTop);
      if (d < bd) { bd = d; best = c; }
    }
    return best;
  };
  for (const sec of GEOM.sections) {
    let el = null;
    if (sec.id) {
      el = pick(Array.prototype.slice.call(document.querySelectorAll('[id=\"' + sec.id + '\"]')), sec.refTop);
    }
    if (!el && sec.cls) {
      let cands = [];
      try { cands = Array.prototype.slice.call(document.querySelectorAll('.' + CSS.escape(sec.cls))); } catch (_) {}
      if (!cands.length) { try { cands = Array.prototype.slice.call(document.querySelectorAll('[class*=\"' + sec.cls + '\"]')); } catch (_) {} }
      el = pick(cands, sec.refTop);
    }
    out.sections.push({ name: sec.name, implH: el ? el.getBoundingClientRect().height : null });
  }
  return JSON.stringify(out);
})()"

IMPL_RAW=$(agent-browser --session "$SESSION" eval "$MEASURE_JS" 2>/dev/null)
IMPL_JSON=$(printf '%s' "$IMPL_RAW" | unwrap)
if [ -z "$IMPL_JSON" ] || [ "$IMPL_JSON" = "null" ]; then
  echo "geometry-sanity: impl page returned no measurable DOM at $URL" >&2
  exit 2
fi
IMPL_TMP="$(mktemp)"
printf '%s' "$IMPL_JSON" > "$IMPL_TMP"

# ── Judge + write artifact. ───────────────────────────────────────────────
OUT_JSON="$REF_DIR/geometry-sanity.json"
run_py - "$REF_GEOM_B64" "$IMPL_TMP" "$OUT_JSON" <<'PY'
import base64, json, os, sys
from ui_clone.gates.geometry_sanity import evaluate

geom = json.loads(base64.b64decode(sys.argv[1]))
raw = open(sys.argv[2]).read().strip()
if raw.startswith('"') and raw.endswith('"'):
    impl = json.loads(json.loads(raw))
else:
    impl = json.loads(raw)

impl_by_name = {s.get("name"): s.get("implH") for s in impl.get("sections") or []}
sections = [
    {"name": s["name"], "refH": s["refH"], "implH": impl_by_name.get(s["name"])}
    for s in geom.get("sections") or []
]


def _envf(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


res = evaluate(
    geom.get("docH"), impl.get("docH"), sections,
    doch_fail_pct=_envf("UI_CLONE_GEOM_DOCH_FAIL_PCT", 15.0),
    doch_warn_pct=_envf("UI_CLONE_GEOM_DOCH_WARN_PCT", 10.0),
    section_fail_pct=_envf("UI_CLONE_GEOM_SECTION_FAIL_PCT", 25.0),
    section_warn_pct=_envf("UI_CLONE_GEOM_SECTION_WARN_PCT", 16.0),
    min_section_px=_envf("UI_CLONE_GEOM_MIN_SECTION_PX", 200.0),
)
with open(sys.argv[3], "w") as f:
    json.dump(res, f, indent=2)

d = res["docH"]
print(f"geometry-sanity: docH ref={d.get('refH')} impl={d.get('implH')} "
      f"off={d.get('pctOff', '?')}% -> {d['status']}")
worst = sorted(
    (r for r in res["sections"] if isinstance(r.get("pctOff"), (int, float))),
    key=lambda r: r["pctOff"], reverse=True,
)
for r in worst[:5]:
    print(f"  section {r['name']}: ref={r['refH']} impl={r['implH']} off={r['pctOff']}% -> {r['status']}")
print(f"geometry-sanity: {res['status']} -> {sys.argv[3]}")
sys.exit(1 if res["status"] == "fail" else 0)
PY
RC=$?
rm -f "$IMPL_TMP"
exit $RC
