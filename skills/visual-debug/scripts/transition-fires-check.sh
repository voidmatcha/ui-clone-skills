#!/usr/bin/env bash
# transition-fires-check.sh — Drive each transition-spec entry's trigger in a
# real browser and assert a MEASURED runtime delta on the target.
#
# Why it matters:
#   Motion fidelity used to be enforced by STATIC string-matching
#   (transition-spec-coverage / spec-implementation-coverage): "class name
#   present in the JSX → covered". That is decoupled from whether the animation
#   actually RUNS. So an unimplemented scroll-reveal PASSED (the class string was
#   in the source) and a working FAQ accordion FAILED (its spec id was not a
#   substring of the source). This gate closes that hole: PASS requires measured
#   runtime motion at the entry's trigger — it cannot be earned by a class name
#   or a `transition-` token.
#
#   It does NOT false-fail a genuinely static page: only entries present in
#   transition-spec.json are checked; a page with no spec → no checks → exit 0.
#
# Usage:
#   bash transition-fires-check.sh <session> <impl-url> <ref-dir> [--out <json>]
#
# Exit: 0 = all spec entries fire (or are documented KNOWN-SKIP),
#       1 = at least one entry did not fire,
#       2 = setup error (no agent-browser, dead page, etc.)
#
# Output: <ref-dir>/transition-fires.json (per-entry id / trigger / expected /
#         observed delta / pass-fail-degraded-known-skip) + a summary line
#         "N/M transitions fire".

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Resolve the repo/plugin root so `ui_clone` is importable for the pure
# decision module (stdlib-only, no install needed). Prefer explicit plugin
# roots, else derive from the script location (skills/visual-debug/scripts).
REPO_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
if [ -z "$REPO_ROOT" ] || [ ! -d "$REPO_ROOT/ui_clone" ]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "ERROR: agent-browser not found. Install: npm i -g agent-browser" >&2
  exit 2
fi

SESSION="${1:?Usage: transition-fires-check.sh <session> <impl-url> <ref-dir> [--out <json>]}"
URL="${2:?Missing impl-url}"
REF_DIR="${3:?Missing ref-dir}"
shift 3 || true

OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="${2:-}"; shift 2 ;;
    --out=*) OUT="${1#--out=}"; shift ;;
    *) shift ;;
  esac
done

SPEC="$REF_DIR/transition-spec.json"
ASSET_SUB="$REF_DIR/asset-substitution.json"
[ -f "$ASSET_SUB" ] || ASSET_SUB="$REF_DIR/asset-substitutions.json"
[ -n "$OUT" ] || OUT="$REF_DIR/transition-fires.json"

VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"
WAIT_MS="${WAIT_MS:-1600}"
SETTLE_MS="${SETTLE_MS:-1100}"

PYBIN="python3"
run_py() { PYTHONPATH="$REPO_ROOT" "$PYBIN" "$@"; }

# ── Short-circuit: no spec / no entries → no checks → not a failure. ──────
ENTRY_COUNT=$(run_py - "$SPEC" <<'PY' 2>/dev/null
import json, sys
try:
    spec = json.load(open(sys.argv[1]))
    ts = spec.get("transitions") or []
    print(len([t for t in ts if isinstance(t, dict)]))
except Exception:
    print(0)
PY
)
ENTRY_COUNT="${ENTRY_COUNT:-0}"

if [ "$ENTRY_COUNT" -eq 0 ]; then
  run_py - "$OUT" "$URL" <<'PY'
import json, sys
out, url = sys.argv[1], sys.argv[2]
json.dump({
    "schemaVersion": 1, "status": "pass", "implUrl": url,
    "total": 0, "fired": 0, "known_skip": 0, "failed": 0, "entries": [],
}, open(out, "w"), indent=2)
PY
  echo "0/0 transitions fire (no transition-spec entries — nothing to check)"
  exit 0
fi

cleanup() { agent-browser --session "$SESSION" close >/dev/null 2>&1 || true; }
trap cleanup EXIT

# ── Build the compact ENTRIES the browser loop drives (id/kind/target). ──
# `kind` comes from the SAME classify() the decision module uses — single
# source of truth, no drift between driver and judge.
ENTRIES_B64=$(run_py - "$SPEC" <<'PY'
import base64, json, sys
from ui_clone.gates.transition_fires import classify
spec = json.load(open(sys.argv[1]))
rows = []
for t in spec.get("transitions") or []:
    if not isinstance(t, dict):
        continue
    rows.append({
        "id": str(t.get("id", "")),
        "kind": classify(t),
        "target": str(t.get("target", "")) or "body",
    })
sys.stdout.write(base64.b64encode(json.dumps(rows).encode()).decode())
PY
)
if [ -z "$ENTRIES_B64" ]; then
  echo "ERROR: could not build entry list from $SPEC" >&2
  exit 2
fi

# ── Shared measurement snapshot — identical in the before and after passes. ─
read -r -d '' SNAP_JS <<'JSEOF' || true
function snap(el, e){
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const s = { opacity: parseFloat(cs.opacity), transform: cs.transform, height: r.height };
  if (e.kind === 'smooth-scroll') { s.top = -window.scrollY; } else { s.top = r.top; }
  if (e.kind === 'video') { const v = (el.tagName === 'VIDEO') ? el : el.querySelector('video'); s.currentTime = v ? v.currentTime : null; }
  if (e.kind === 'carousel') { const sc = el.querySelector('[class*=track],[class*=slides],[class*=wrapper]') || el; s.scrollLeft = sc.scrollLeft; }
  if (e.kind === 'webgl') { const ci = canvasInfo(el); s.canvasCount = ci.count; s.canvasNonBlank = ci.nonBlank; }
  if (e.kind === 'hover') { s.color = cs.color; s.backgroundColor = cs.backgroundColor; s.borderColor = cs.borderColor; }
  if (e.kind === 'click' || e.kind === 'reveal' || e.kind === 'splash') { var ch = el.querySelectorAll('span,div,em,b,i,p,a'); var t = ''; var lim = Math.min(ch.length, 16); for (var ci2 = 0; ci2 < lim; ci2++){ var cc = getComputedStyle(ch[ci2]); t += cc.transform + '|' + cc.opacity + ';'; } s.childSig = t; }
  return s;
}
function canvasInfo(el){
  let cvs = (el.tagName === 'CANVAS') ? [el] : Array.prototype.slice.call(el.querySelectorAll('canvas'));
  if (!cvs.length) cvs = Array.prototype.slice.call(document.querySelectorAll('canvas'));
  let nonBlank = false;
  for (const c of cvs){
    if (!(c.width > 0 && c.height > 0)) continue;
    try {
      const gl = c.getContext('webgl') || c.getContext('webgl2');
      if (gl) {
        const w = Math.min(c.width, 40), h = Math.min(c.height, 40);
        const px = new Uint8Array(w * h * 4);
        gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
        let mn = 255, mx = 0, nz = 0;
        for (let k = 0; k < px.length; k++){ const v = px[k]; if (v !== 0) nz++; if (v < mn) mn = v; if (v > mx) mx = v; }
        if (nz > 0 && (mx - mn) > 4) nonBlank = true;
        continue;
      }
      const c2 = c.getContext('2d');
      if (c2) {
        const w = Math.min(c.width, 40), h = Math.min(c.height, 40);
        const d = c2.getImageData(0, 0, w, h).data;
        let mn = 255, mx = 0, nz = 0;
        for (let k = 0; k < d.length; k++){ const v = d[k]; if (v !== 0) nz++; if (v < mn) mn = v; if (v > mx) mx = v; }
        if (nz > 0 && (mx - mn) > 4) nonBlank = true;
        continue;
      }
    } catch (_) { /* unreadable (tainted / context lost) — NOT proven non-blank */ }
  }
  return { count: cvs.length, nonBlank };
}
JSEOF

# ── Browser session: navigate, capture BEFORE, drive triggers, capture AFTER ─
agent-browser --session "$SESSION" set viewport "$VIEW_W" "$VIEW_H" >/dev/null 2>&1
agent-browser --session "$SESSION" navigate "$URL" >/dev/null 2>&1
sleep $(( (WAIT_MS + 999) / 1000 ))

unwrap() { sed 's/^"//;s/"$//' | sed 's/\\"/"/g'; }

PHASE1="(() => {
  const ENTRIES = JSON.parse(atob('$ENTRIES_B64'));
  $SNAP_JS
  const out = {};
  for (let i = 0; i < ENTRIES.length; i++){
    const e = ENTRIES[i];
    let el = null;
    try { el = document.querySelector(e.target); } catch (_) {}
    if (!el && e.kind === 'smooth-scroll') el = document.scrollingElement || document.body;
    if (!el) { out[i] = { found: false }; continue; }
    el.setAttribute('data-tf-idx', String(i));
    out[i] = { found: true, before: snap(el, e) };
  }
  return JSON.stringify(out);
})()"

BEFORE_RAW=$(agent-browser --session "$SESSION" eval "$PHASE1" 2>/dev/null)
BEFORE_JSON=$(printf '%s' "$BEFORE_RAW" | unwrap)
BEFORE_TMP="$(mktemp)"
printf '%s' "$BEFORE_JSON" > "$BEFORE_TMP"

if [ -z "$BEFORE_JSON" ] || [ "$BEFORE_JSON" = "null" ]; then
  echo "ERROR: page returned no measurable DOM at $URL (dead page / wrong port?)" >&2
  rm -f "$BEFORE_TMP"
  exit 2
fi

PHASE2="(async () => {
  const ENTRIES = JSON.parse(atob('$ENTRIES_B64'));
  $SNAP_JS
  const SETTLE = $SETTLE_MS;
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const out = {};
  const probes = document.querySelectorAll('[data-tf-idx]');
  for (const el of probes){
    const i = parseInt(el.getAttribute('data-tf-idx'), 10);
    const e = ENTRIES[i];
    if (!e) { continue; }
    const rec = { found: true };
    try {
      if (e.kind === 'scrub') {
        const samples = [];
        const docH = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
        const base = window.scrollY + el.getBoundingClientRect().top;
        const span = el.offsetHeight || window.innerHeight;
        // A smooth-scroll engine (Lenis/ScrollSmoother/locomotive) binds the
        // scrub to a VIRTUAL scroll position that programmatic window.scrollTo
        // does not drive — so a flat transform here is unmeasurable, not dead.
        const smoothEngine = !!(window.lenis || window.Lenis || window.ScrollSmoother
          || /lenis|has-scroll-smooth/.test(document.documentElement.className)
          || document.querySelector('[data-scroll-container],#smooth-content,[class*=lenis]'));
        for (const p of [0, 0.5, 1]) {
          window.scrollTo(0, Math.min(docH, Math.max(0, base - window.innerHeight * 0.4 + span * p)));
          await wait(SETTLE);
          const cs = getComputedStyle(el); const rr = el.getBoundingClientRect();
          samples.push({ transform: cs.transform, opacity: parseFloat(cs.opacity), top: rr.top, scrollY: window.scrollY, docH: docH, smoothEngine: smoothEngine });
        }
        rec.samples = samples;
      } else if (e.kind === 'hover') {
        ['pointerover','mouseover','mouseenter','mousemove'].forEach(t => { try { el.dispatchEvent(new MouseEvent(t, { bubbles: true })); } catch (_) {} });
        await wait(SETTLE); rec.after = snap(el, e);
      } else if (e.kind === 'click') {
        const tgt = el.querySelector('summary,button,[aria-expanded]') || el;
        try { tgt.click(); } catch (_) {}
        await wait(SETTLE); rec.after = snap(el, e);
      } else if (e.kind === 'carousel') {
        await wait(Math.max(SETTLE, 2200)); rec.after = snap(el, e);
      } else if (e.kind === 'video') {
        const v = (el.tagName === 'VIDEO') ? el : el.querySelector('video');
        if (v) { try { v.muted = true; const pr = v.play(); if (pr && pr.catch) pr.catch(() => {}); } catch (_) {} }
        await wait(Math.max(SETTLE, 1400)); rec.after = snap(el, e);
      } else if (e.kind === 'webgl') {
        await wait(Math.max(SETTLE, 1400)); rec.after = snap(el, e);
      } else if (e.kind === 'smooth-scroll') {
        window.scrollTo(0, Math.min(2000, Math.max(1, document.documentElement.scrollHeight - window.innerHeight)));
        await wait(SETTLE); rec.after = snap(el, e);
      } else {
        el.scrollIntoView({ block: 'center' }); await wait(SETTLE); rec.after = snap(el, e);
      }
    } catch (err) { rec.error = String(err); try { rec.after = snap(el, e); } catch (_) {} }
    out[i] = rec;
  }
  document.querySelectorAll('[data-tf-idx]').forEach(el => el.removeAttribute('data-tf-idx'));
  return JSON.stringify(out);
})()"

AFTER_RAW=$(agent-browser --session "$SESSION" eval "$PHASE2" 2>/dev/null)
AFTER_JSON=$(printf '%s' "$AFTER_RAW" | unwrap)
AFTER_TMP="$(mktemp)"
printf '%s' "$AFTER_JSON" > "$AFTER_TMP"

# ── Real-pointer hover pass ────────────────────────────────────────────────
# CSS `:hover` only activates under a REAL pointer; synthetic MouseEvents
# dispatched inside a page eval cannot trigger it, so the in-eval hover branch
# false-negatives every CSS-only hover into "dead". For each hover entry, move
# the genuine CDP pointer over the target and re-snapshot (color fields
# included), then patch that entry's AFTER state with the measured result.
HOVER_ROWS=$(run_py - "$ENTRIES_B64" <<'PY'
import base64, json, sys
rows = json.loads(base64.b64decode(sys.argv[1]))
for i, r in enumerate(rows):
    if r.get("kind") == "hover":
        sys.stdout.write(str(i) + "\t" + (r.get("target") or "body") + "\n")
PY
)
if [ -n "$HOVER_ROWS" ]; then
  HOVER_PATCH="$(mktemp)"
  : > "$HOVER_PATCH"
  while IFS=$'\t' read -r HIDX HSEL; do
    [ -z "$HSEL" ] && continue
    HSEL_B64=$(printf '%s' "$HSEL" | base64 | tr -d '\n')
    agent-browser --session "$SESSION" scrollintoview "$HSEL" >/dev/null 2>&1 || true
    agent-browser --session "$SESSION" hover "$HSEL" >/dev/null 2>&1 || true
    HSNAP_JS="(async () => { $SNAP_JS
      const wait = (ms) => new Promise(r => setTimeout(r, ms));
      const el = document.querySelector(atob('$HSEL_B64'));
      if (!el) return JSON.stringify({ found: false });
      await wait($SETTLE_MS);
      return JSON.stringify({ found: true, after: snap(el, { kind: 'hover' }) });
    })()"
    HRAW=$(agent-browser --session "$SESSION" eval "$HSNAP_JS" 2>/dev/null)
    HJSON=$(printf '%s' "$HRAW" | unwrap)
    printf '%s\t%s\n' "$HIDX" "$HJSON" >> "$HOVER_PATCH"
  done <<< "$HOVER_ROWS"
  PATCHED_TMP="$(mktemp)"
  run_py - "$AFTER_TMP" "$HOVER_PATCH" "$PATCHED_TMP" <<'PY'
import json, sys
try:
    after = json.load(open(sys.argv[1]))
except Exception:
    after = {}
for line in open(sys.argv[2]):
    line = line.rstrip("\n")
    if "\t" not in line:
        continue
    idx, blob = line.split("\t", 1)
    try:
        rec = json.loads(blob)
    except Exception:
        continue
    if not rec.get("found"):
        continue
    cur = after.get(idx) or {}
    cur["found"] = True
    cur["after"] = rec.get("after", {}) or {}
    after[idx] = cur
json.dump(after, open(sys.argv[3], "w"))
PY
  mv "$PATCHED_TMP" "$AFTER_TMP"
fi

# ── Merge before+after (keyed by entry index) into observations keyed by id. ─
OBS_TMP="$(mktemp)"
run_py - "$SPEC" "$BEFORE_TMP" "$AFTER_TMP" "$OBS_TMP" <<'PY'
import json, sys

spec_path, before_path, after_path, out_path = sys.argv[1:5]


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}


spec = load(spec_path)
before = load(before_path) or {}
after = load(after_path) or {}
entries = [t for t in (spec.get("transitions") or []) if isinstance(t, dict)]
obs = {}
for i, t in enumerate(entries):
    b = before.get(str(i)) or {}
    a = after.get(str(i)) or {}
    found = bool(b.get("found")) and bool(a.get("found"))
    obs[str(t.get("id", ""))] = {
        "found": found,
        "before": b.get("before", {}) or {},
        "after": a.get("after", {}) or {},
        "samples": a.get("samples", []) or [],
    }
json.dump(obs, open(out_path, "w"))
PY

# ── Decide + write artifact + exit code (pure module, unit-tested). ───────
run_py -m ui_clone.gates.transition_fires "$SPEC" "$OBS_TMP" "$ASSET_SUB" "$OUT" --impl-url "$URL"
RC=$?

rm -f "$BEFORE_TMP" "$AFTER_TMP" "$OBS_TMP"
exit $RC
