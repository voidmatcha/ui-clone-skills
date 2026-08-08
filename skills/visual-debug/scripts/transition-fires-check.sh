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
#         "N/M transitions fire — fired only; trajectory fidelity is
#         scroll-coverage / video-motion".
#
# What the count does NOT prove: this gate measures a runtime delta — that
# something moved. It never compares the trajectory against the ref, so a
# reveal that completes before its section is on screen still "fires", as does
# a latched state replayed as a smeared curve. Read the count as liveness, not
# as motion parity.

set -uo pipefail

# W-4 (loop-ebpb-0): the reference follows prefers-color-scheme — a host
# OS theme flip (macOS auto-dark in the evening) silently captured the ref
# in dark mode and poisoned an entire compare cycle (footer dSSIM
# 0.0000065 -> 0.687 reading as catastrophic regression). Pin light unless
# the caller explicitly overrides.
: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME

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

# Inner-iteration scoping: UI_CLONE_FIRES_IDS="id1,id2" probes only those spec
# entries. Scoped runs write transition-fires.scoped.json so the CANONICAL
# artifact (read by rollups/closeout) is never clobbered by a partial
# measurement — closeout still requires a full run.
FIRES_IDS="${UI_CLONE_FIRES_IDS:-}"
if [ -n "$FIRES_IDS" ] && [ -z "$OUT" ]; then
  OUT="$REF_DIR/transition-fires.scoped.json"
fi
[ -n "$OUT" ] || OUT="$REF_DIR/transition-fires.json"

VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"
WAIT_MS="${WAIT_MS:-1600}"
SETTLE_MS="${SETTLE_MS:-1100}"

python_imports_ui_clone() {
  (cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" "$@" - <<'PY'
from ui_clone.gates.transition_fires import classify  # noqa: F401
PY
  ) >/dev/null 2>&1
}

PY_MODE=""
PYBIN=""
if [ -n "${VIRTUAL_ENV:-}" ] \
  && [ -x "$VIRTUAL_ENV/bin/python" ] \
  && python_imports_ui_clone "$VIRTUAL_ENV/bin/python"; then
  PY_MODE="direct"
  PYBIN="$VIRTUAL_ENV/bin/python"
elif command -v python3 >/dev/null 2>&1 && python_imports_ui_clone python3; then
  PY_MODE="direct"
  PYBIN="python3"
elif command -v uv >/dev/null 2>&1 \
  && (cd "$REPO_ROOT" && python_imports_ui_clone uv run python); then
  PY_MODE="uv"
else
  echo "ERROR: could not find a Python interpreter that can import ui_clone" >&2
  exit 2
fi

run_py() {
  if [ "$PY_MODE" = "uv" ]; then
    (cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" uv run python "$@")
  else
    (cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" "$PYBIN" "$@")
  fi
}

# Scoped mode: operate on a FILTERED copy of the spec so the entry list, the
# wheel re-probe, and the verdict module all see the same scoped world —
# un-probed entries must not surface as FAILED in the scoped artifact.
if [ -n "$FIRES_IDS" ]; then
  # L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
  SCOPED_SPEC="$(mktemp "${TMPDIR:-/tmp}/transition-spec-scoped.XXXXXX")"
  mv "$SCOPED_SPEC" "${SCOPED_SPEC}.json"
  SCOPED_SPEC="${SCOPED_SPEC}.json"
  UI_CLONE_FIRES_IDS="$FIRES_IDS" run_py - "$SPEC" "$SCOPED_SPEC" <<'PY'
import json, os, sys

spec = json.load(open(sys.argv[1]))
only = {s.strip() for s in os.environ.get("UI_CLONE_FIRES_IDS", "").split(",") if s.strip()}
spec["transitions"] = [
    t for t in (spec.get("transitions") or [])
    if isinstance(t, dict) and str(t.get("id", "")) in only
]
spec["scoped"] = True
spec["scopedIds"] = sorted(only)
json.dump(spec, open(sys.argv[2], "w"))
PY
  SPEC="$SCOPED_SPEC"
  echo "transition-fires: SCOPED run (${FIRES_IDS}) -> $OUT (canonical artifact untouched)"
fi

# ── Short-circuit: no spec / no entries → no checks → not a failure. ──────
if [ ! -e "$SPEC" ]; then
  ENTRY_COUNT=0
else
  ENTRY_COUNT=$(run_py - "$SPEC" <<'PY'
import json, sys

from ui_clone.gates.transition_fires import classify

try:
    with open(sys.argv[1]) as fh:
        spec = json.load(fh)
except Exception as exc:
    print(f"ERROR: cannot read/parse transition spec {sys.argv[1]}: {exc}", file=sys.stderr)
    sys.exit(2)

if not isinstance(spec, dict):
    print(f"ERROR: transition spec {sys.argv[1]} must be a JSON object", file=sys.stderr)
    sys.exit(2)

ts = spec.get("transitions") or []
if not isinstance(ts, list):
    print(f"ERROR: transition spec {sys.argv[1]} transitions must be a list", file=sys.stderr)
    sys.exit(2)

entries = [t for t in ts if isinstance(t, dict)]
for entry in entries:
    classify(entry)
print(len(entries))
PY
  )
  ENTRY_RC=$?
  if [ "$ENTRY_RC" -ne 0 ]; then
    exit 2
  fi
  ENTRY_COUNT="${ENTRY_COUNT:-0}"
fi

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
    anim = t.get("animation")
    prop = str(anim.get("property", "")) if isinstance(anim, dict) else ""
    rows.append({
        "id": str(t.get("id", "")),
        "kind": classify(t),
        "trigger": str(t.get("trigger", "")),
        "target": str(t.get("target", "")) or "body",
        "prop": prop,
        "durationMs": (
            anim.get("duration", 0) if isinstance(anim, dict) else 0
        ),
        "stickyRangeH": (
            anim.get("stickyRangeH", t.get("stickyRangeH"))
            if isinstance(anim, dict) else t.get("stickyRangeH")
        ),
        "stickyContainerH": (
            anim.get("stickyContainerH", t.get("stickyContainerH"))
            if isinstance(anim, dict) else t.get("stickyContainerH")
        ),
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
  if (e.kind === 'sticky') { s.position = cs.position; s.cssTop = cs.top; }
  if (e.kind === 'video') { const v = (el.tagName === 'VIDEO') ? el : el.querySelector('video'); s.currentTime = v ? v.currentTime : null; }
  if (e.kind === 'carousel') { const sc = el.querySelector('[class*=track],[class*=slides],[class*=wrapper]') || el; s.scrollLeft = sc.scrollLeft; }
  if (e.kind === 'webgl') { const ci = canvasInfo(el); s.canvasCount = ci.count; s.canvasNonBlank = ci.nonBlank; }
  if (e.kind === 'hover') {
    s.color = cs.color;
    s.backgroundColor = cs.backgroundColor;
    s.borderColor = cs.borderColor;
    s.outlineColor = cs.outlineColor;
    s.textDecorationColor = cs.textDecorationColor;
    s.boxShadow = cs.boxShadow;
    s.filter = cs.filter;
    s.backgroundImage = cs.backgroundImage;
    s.fontWeight = cs.fontWeight;
    const pseudoSig = (pseudo) => {
      const ps = getComputedStyle(el, pseudo);
      return [
        ps.opacity, ps.transform, ps.color,
        ps.backgroundColor, ps.borderColor, ps.width, ps.height,
      ].join('|');
    };
    s.pseudoBefore = pseudoSig('::before');
    s.pseudoAfter = pseudoSig('::after');
    s.width = r.width;
  }
  if (e.kind === 'hover' || e.kind === 'click' || e.kind === 'reveal' || e.kind === 'splash') { var ch = el.querySelectorAll('span,div,em,b,i,p,a'); var t = ''; var lim = Math.min(ch.length, 16); for (var ci2 = 0; ci2 < lim; ci2++){ var cc = getComputedStyle(ch[ci2]); t += cc.transform + '|' + cc.opacity + ';'; } s.childSig = t; }
  if (e.kind === 'timer') {
    s.color = cs.color;
    s.backgroundColor = cs.backgroundColor;
    var tv = el.querySelectorAll('span,div,em,b,i,p,a,svg,img');
    var ts = ''; var tlim = Math.min(tv.length, 24);
    for (var ti = 0; ti < tlim; ti++){
      var tc = getComputedStyle(tv[ti]);
      ts += tc.transform + '|' + tc.opacity + '|' + tc.color + '|' + tc.backgroundColor + ';';
    }
    s.childVisualSig = ts;
  }
  if (e.kind === 'reveal' || e.kind === 'splash') {
    // Fix M (loop-e2e-6): height/text channels for bar-grow + count-up
    // reveals. Heights come from the same non-replaced child set as childSig
    // (span/div/em/b/i/p/a — excludes img/video/iframe/svg/canvas so lazy
    // media cannot fake growth); textDigest is the digits of innerText.
    // The judge only consults these when the spec declares height/count.
    var hch = el.querySelectorAll('span,div,em,b,i,p,a'); var hh = []; var hlim = Math.min(hch.length, 16);
    for (var hci = 0; hci < hlim; hci++){ hh.push(Math.round(hch[hci].getBoundingClientRect().height * 100) / 100); }
    s.childHeights = hh;
    s.textDigest = (el.innerText || '').replace(/[^0-9]/g, '').slice(0, 64);
  }
  if (e.kind === 'reveal' || e.kind === 'splash') { var sp = document.querySelector('[data-tf-stroke-for=\"' + e.id + '\"]') || ((el.tagName && el.tagName.toLowerCase() === 'path') ? el : (el.querySelector('path[data-stroke-draw]') || el.querySelector('path[stroke-dasharray]'))); if (!sp && (e.prop || '').toLowerCase().replace(/-/g, '').indexOf('strokedashoffset') >= 0) { sp = document.querySelector('path[data-stroke-draw]') || document.querySelector('path[stroke-dasharray]'); } if (sp) { s.strokeDashoffset = getComputedStyle(sp).strokeDashoffset; } }
  return s;
}
function canvasInfo(el){
  let cvs = (el.tagName === 'CANVAS') ? [el] : Array.prototype.slice.call(el.querySelectorAll('canvas'));
  if (!cvs.length) cvs = Array.prototype.slice.call(document.querySelectorAll('canvas'));
  const sampleTiles = (width, height) => {
    const size = Math.max(1, Math.min(32, width, height));
    const points = [];
    for (const fy of [0, 0.25, 0.5, 0.75, 1]) {
      for (const fx of [0, 0.25, 0.5, 0.75, 1]) {
        points.push([
          Math.max(0, Math.round((width - size) * fx)),
          Math.max(0, Math.round((height - size) * fy)),
          size,
        ]);
      }
    }
    return points;
  };
  const tileHasPixels = (pixels) => {
    let mn = 255, mx = 0, nz = 0;
    for (let k = 0; k < pixels.length; k++){
      const v = pixels[k];
      if (v !== 0) nz++;
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
    return nz > 0 && (mx - mn) > 4;
  };
  let nonBlank = false;
  for (const c of cvs){
    if (!(c.width > 0 && c.height > 0)) continue;
    try {
      const gl = c.getContext('webgl') || c.getContext('webgl2');
      if (gl) {
        for (const [x, y, size] of sampleTiles(c.width, c.height)) {
          const px = new Uint8Array(size * size * 4);
          gl.readPixels(x, y, size, size, gl.RGBA, gl.UNSIGNED_BYTE, px);
          if (tileHasPixels(px)) { nonBlank = true; break; }
        }
        continue;
      }
      const c2 = c.getContext('2d');
      if (c2) {
        for (const [x, y, size] of sampleTiles(c.width, c.height)) {
          const pixels = c2.getImageData(x, y, size, size).data;
          if (tileHasPixels(pixels)) { nonBlank = true; break; }
        }
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
# Named browser contexts can inherit the daemon's last pointer coordinates.
# If the newly loaded page places a hover target under that coordinate, the
# PRE snapshot below starts in :hover and a working CSS transition reads
# hovered -> hovered. Move the pointer outside the viewport before the baseline
# settles so every real-pointer hover is measured from an unhovered state.
agent-browser --session "$SESSION" mouse move -100 -100 >/dev/null 2>&1 || true
sleep $(( (WAIT_MS + 999) / 1000 ))

unwrap() {
  # agent-browser JSON-encodes JavaScript string results. Decode that outer
  # layer structurally: quote-stripping with sed corrupts legitimate nested
  # JSON strings such as backgroundImage: url("x.png").
  run_py -c '
import json
import sys

raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit(0)
try:
    value = json.loads(raw)
except json.JSONDecodeError:
    sys.stdout.write(raw)
else:
    if isinstance(value, str):
        sys.stdout.write(value)
    else:
        sys.stdout.write(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        )
'
}

PHASE1="(async () => {
  const ENTRIES = JSON.parse(atob('$ENTRIES_B64'));
  $SNAP_JS
  // Mount sweep (Fix 78c) — React impls conditionally MOUNT animated content
  // ({inView && ...}), so at scroll-top the element does not exist in the DOM
  // at all and every class-selector probe reports 'element not found' (loop-3:
  // 15/17 fires failures; the ref renders everything upfront so this is an
  // impl-shape difference, not a selector bug). Scroll the document through
  // once so lazy/in-view content mounts, then return to top before marking.
  // Pre-sweep initial snapshot (loop-e2e-5/codex): the mount sweep below
  // FIRES IO reveals before the 'before' snapshot is taken, so always-mounted
  // reveal targets read final->final and false-negative as dead. Capture the
  // pristine state of every directly-resolvable target FIRST; the merge
  // prefers it as the true 'before'.
  const PRE = {};
  for (let i = 0; i < ENTRIES.length; i++){
    const e = ENTRIES[i];
    let pel = null;
    try { pel = document.querySelector(e.target); } catch (_) {}
    if (!pel && e.target && e.target.indexOf('.') >= 0) {
      try { pel = document.querySelector(e.target.replace(/\.([A-Za-z0-9_-]+)/g, '[class*=\"\$1\"]')); } catch (_) {}
      // F7 (Fix 78b parity): the PRE snapshot must resolve hash-only targets the
      // SAME way the main pass does, else a target that matches ONLY after the
      // CSS-module hash suffix is stripped (ref hash != impl hash) gets no
      // pristine before-state; before then falls back to the post-mount-sweep
      // snapshot and a one-shot reveal reads final->final and false-fails.
      if (!pel) {
        const stripped = e.target.replace(/\.([A-Za-z0-9_-]+?)(?:__[A-Za-z0-9_-]{4,})(?=[\s>:\[.]|$)/g, '[class*=\"\$1\"]').replace(/\.([A-Za-z0-9_-]+)/g, '[class*=\"\$1\"]');
        if (stripped !== e.target) { try { pel = document.querySelector(stripped); } catch (_) {} }
      }
    }
    if (pel) { try { PRE[i] = snap(pel, e); } catch (_) {} }
  }
  try {
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    const docH = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    const step = Math.max(400, Math.floor(window.innerHeight * 0.8));
    for (let y = 0; y <= docH; y += step) { window.scrollTo(0, y); await wait(120); }
    window.scrollTo(0, 0); await wait(400);
  } catch (_) {}
  const out = {};
  for (let i = 0; i < ENTRIES.length; i++){
    const e = ENTRIES[i];
    let el = null;
    try { el = document.querySelector(e.target); } catch (_) {}
    // Fix 78 — CSS-module impls hash class tokens (cta_button__bFacv), so a
    // spec selector's bare class (.cta_button) never matches. Retry each
    // comma-alternative with .token converted to [class*=token].
    if (!el && e.target && e.target.indexOf('.') >= 0) {
      const alts = e.target.split(',');
      for (let a2 = 0; a2 < alts.length && !el; a2++) {
        try { el = document.querySelector(alts[a2].trim().replace(/\.([A-Za-z0-9_-]+)/g, '[class*=\"\$1\"]')); } catch (_) {}
      }
      // Fix 78b — REF-side specs carry the REF's module hash
      // (.card_hero__aB3dX); the impl's hash differs (or is absent), so even
      // [class*=card_hero__aB3dX] never matches. Retry with the hash suffix
      // stripped to the stable base token: [class*=\"card_hero\"]. This was
      // 15/17 motion-fire failures on the loop-3 validation run.
      if (!el) {
        for (let a3 = 0; a3 < alts.length && !el; a3++) {
          const stripped = alts[a3].trim().replace(/\.([A-Za-z0-9_-]+?)(?:__[A-Za-z0-9_-]{4,})(?=[\s>:\[.]|$)/g, '[class*=\"\$1\"]')
            .replace(/\.([A-Za-z0-9_-]+)/g, '[class*=\"\$1\"]');
          if (stripped !== alts[a3].trim()) {
            try { el = document.querySelector(stripped); } catch (_) {}
          }
        }
      }
    }
    if (!el && e.kind === 'smooth-scroll') el = document.scrollingElement || document.body;
    // Fix 79 — prose reveal targets ('section content blocks, headings, cards')
    // resolve nothing on a regenerated build. The deterministic emitters stamp
    // their targets: state-fade elements carry data-scroll-fade and the emitted
    // ScrollReveal wrapper carries data-scroll-reveal. Prefer a BELOW-FOLD
    // instance so the drive (scrollIntoView) is what triggers the motion.
    if (!el && e.kind === 'reveal') {
      const hint = ((e.id || '') + ' ' + (e.target || '')).toLowerCase();
      const prefer = hint.indexOf('state') >= 0 || hint.indexOf('progress') >= 0
        ? '[data-scroll-fade]' : '[data-scroll-reveal]';
      const other = prefer === '[data-scroll-fade]' ? '[data-scroll-reveal]' : '[data-scroll-fade]';
      let cands = Array.prototype.slice.call(document.querySelectorAll(prefer));
      if (!cands.length) cands = Array.prototype.slice.call(document.querySelectorAll(other));
      el = cands.find(function(c){ return c.getBoundingClientRect().top > window.innerHeight; }) || cands[0] || null;
    }
    // Fix 78 — stroke-draw entries: spec targets are prose ('decorative SVG
    // strokes'), and draw paths often live in boxless <mask>/<defs>. Resolve to
    // the element whose viewport entry TRIGGERS the draw: the mask-referencing
    // element of a STAMPED path (a static dasharray path measures 0 -> 0),
    // else the path's boxed svg ancestor, else the path itself.
    const isStroke = (e.prop || '').toLowerCase().replace(/-/g, '').indexOf('strokedashoffset') >= 0;
    if (isStroke) {
      // Prefer an UNDRAWN stamped path: a trigger in view at load draws at
      // mount, so the first path reads 0 -> 0 even though the motion fired.
      // Pin the chosen path with a marker so both phases measure the same one.
      const paths = Array.prototype.slice.call(document.querySelectorAll('path[data-stroke-draw]'));
      let p = paths.find(function (x) { return Math.abs(parseFloat(getComputedStyle(x).strokeDashoffset) || 0) > 0.5; })
        || paths[0] || document.querySelector('path[stroke-dasharray]');
      if (p) {
        p.setAttribute('data-tf-stroke-for', e.id);
        let t2 = null;
        const holder = p.closest('mask, defs');
        if (holder && holder.id) t2 = document.querySelector('[mask*=\"#' + holder.id + '\"], [style*=\"#' + holder.id + '\"]');
        el = t2 || p.closest('svg') || p;
      }
    }
    if (!el) { out[i] = { found: false }; continue; }
    // Collision-safe multi-index tagging (loop-e2e-4): several spec entries
    // can resolve to the SAME element (e.g. width-scrub + autoplay on one
    // <video>); a single-value attribute lets the last writer win and the
    // before/after merge then misreports the earlier entries as 'element not
    // found'. Append instead of overwrite.
    const prevIdxs = el.getAttribute('data-tf-idxs');
    el.setAttribute('data-tf-idxs', prevIdxs ? prevIdxs + ',' + i : String(i));
    // Prefer the pre-sweep pristine snapshot as 'before' — the mount sweep
    // above may already have fired this entry's motion (IO reveals).
    out[i] = { found: true, before: (PRE[i] || snap(el, e)) };
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

# PHASE2 runs as CHUNKED evals (loop-e2e-4): a single async eval over ~20
# probes needs >20s of settle waits and hits the agent-browser eval budget
# (~25s hard error), silently losing every probe after the first few and
# misreporting them as 'element not found'. Each chunk drives only its
# entry-index subset; bash merges the chunk outputs. TF_CHUNK_SIZE env
# overrides the per-eval entry budget.
PHASE2_TEMPLATE="(async () => {
  const ENTRIES = JSON.parse(atob('$ENTRIES_B64'));
  $SNAP_JS
  const SETTLE = $SETTLE_MS;
  const CHUNK = new Set(__CHUNK_JSON__);
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const out = {};
  const resolveEntry = (e, fallback) => {
    let node = null;
    try { node = document.querySelector(e.target); } catch (_) {}
    if (!node && e.target && e.target.indexOf('.') >= 0) {
      const alts = e.target.split(',');
      for (let i = 0; i < alts.length && !node; i++) {
        const raw = alts[i].trim();
        try { node = document.querySelector(raw.replace(/\.([A-Za-z0-9_-]+)/g, '[class*=\\"\$1\\"]')); } catch (_) {}
        if (!node) {
          const stable = raw
            .replace(/\.([A-Za-z0-9_-]+?)(?:__[A-Za-z0-9_-]{4,})(?=[\s>:\[.]|$)/g, '[class*=\\"\$1\\"]')
            .replace(/\.([A-Za-z0-9_-]+)/g, '[class*=\\"\$1\\"]');
          try { node = document.querySelector(stable); } catch (_) {}
        }
      }
    }
    return node || (fallback && fallback.isConnected ? fallback : null);
  };
  // Resolve the Swiper instance for a carousel target, whether the entry points
  // at the .swiper root (el.swiper), the .swiper-wrapper (instance is on the
  // ANCESTOR .swiper — closest, not a descendant query), or a container that
  // holds a .swiper descendant. Present on ref AND clone.
  const swInst = (node) => {
    const host = (node.closest && node.closest('.swiper')) || node;
    return (host && host.swiper)
      || (node.querySelector && (node.querySelector('.swiper') || {}).swiper)
      || null;
  };
  // Cumulative carousel wait budget per chunk: adaptive per-carousel waits
  // (up to ~6.5s each) would blow the agent-browser ~25s evaluation budget on a
  // page with several carousels and time out the whole chunk (losing ALL its
  // verdicts). Cap the TOTAL carousel wait; once spent, later carousels in the
  // chunk fall back to a minimal settle (a bounded degradation far better than a
  // chunk timeout). Keep carousel chunks small (TF_CHUNK_SIZE) to avoid it.
  let carWaitBudget = 12000;
  const probes = Array.prototype.slice.call(
    document.querySelectorAll('[data-tf-idxs]')
  );
  // A timer may remount its target while earlier chunks are running, taking
  // the phase-1 marker with the detached node. Recover the live target for the
  // active chunk before iterating probes; other kinds keep marker-only routing.
  for (const i of CHUNK) {
    const e = ENTRIES[i];
    if (!e || e.kind !== 'timer') continue;
    const marked = probes.some((node) => (
      (node.getAttribute('data-tf-idxs') || '').split(',').includes(String(i))
    ));
    if (marked) continue;
    const live = resolveEntry(e, null);
    if (!live) continue;
    const prior = live.getAttribute('data-tf-idxs');
    live.setAttribute('data-tf-idxs', prior ? prior + ',' + i : String(i));
    if (probes.indexOf(live) < 0) probes.push(live);
  }
  for (const el of probes){
   const idxList = (el.getAttribute('data-tf-idxs') || '').split(',').map(s => parseInt(s, 10)).filter(n => !isNaN(n));
   for (const i of idxList){
    if (!CHUNK.has(i)) { continue; }
    const e = ENTRIES[i];
    if (!e) { continue; }
    const rec = { found: true };
    try {
      if (e.kind === 'sticky') {
        // CSS sticky is layout behavior: sample the same absolute page
        // fractions used by the scrub global sweep, but within the declared
        // sticky range. Reset first so base is the element's natural document
        // position rather than a currently-pinned viewport position left by a
        // previous probe.
        window.scrollTo(0, 0);
        await wait(SETTLE);
        const initial = snap(el, e);
        const cssTop = parseFloat(initial.cssTop);
        const containerRect = el.parentElement
          ? el.parentElement.getBoundingClientRect() : null;
        const stickyBoxH = el.getBoundingClientRect().height;
        const topInset = isNaN(cssTop) ? 0 : Math.max(0, cssTop);
        const docH = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
        const base = window.scrollY + el.getBoundingClientRect().top;
        const start = Math.min(docH, Math.max(0, base - topInset));
        const containerBottom = containerRect
          ? window.scrollY + containerRect.bottom : start + stickyBoxH;
        const achievableEnd = Math.max(
          start,
          containerBottom - stickyBoxH - topInset
        );
        const achievableRange = Math.max(0, achievableEnd - start);
        // A declared range is reference truth. The live achievable range is
        // only a probe fallback when older specs do not carry sticky geometry;
        // never shrink the reference expectation to fit a short impl container.
        const declaredRange = Number(e.stickyRangeH);
        const range = Number.isFinite(declaredRange) && declaredRange > 0
          ? declaredRange : achievableRange;
        const samples = [];
        for (const p of [0, 0.25, 0.5, 0.75, 1]) {
          window.scrollTo(0, Math.min(docH, Math.max(0, start + range * p)));
          await wait(SETTLE);
          const cs = getComputedStyle(el);
          const rr = el.getBoundingClientRect();
          samples.push({ position: cs.position, cssTop: cs.top, top: rr.top, scrollY: window.scrollY });
        }
        rec.samples = samples;
        rec.after = snap(el, e);
      } else if (e.kind === 'scrub') {
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
        // Fix K (loop-e2e-6): early positions (clamped >= 0) cover scrubs
        // whose useScroll offset ['start start','end start'] completes while
        // the element is still at/near the top of the page (hero width
        // scrub saturates by scrollY~300 — the old {0,.5,1} sweep sampled
        // only the saturated tail). Extra samples cannot false-pass a dead
        // scrub: variation still requires measured property change across
        // advanced scroll (test_dead_scrub_with_extended_early_samples).
        // Child signature + inline width per sample (loop-e2e-5): scrub
        // motion often animates DESCENDANTS (deck cards, word spans) or an
        // inline width track (hero video 80vw->100vw) while the target's
        // own transform stays identity. Same signal class snap() already
        // records for reveals — children's transform|opacity only.
        // Fix L (loop-e2e-6): color series for class-swap scrubs (word
        // reveals dim via COLOR; opacity stays 1). Judged only when the
        // spec declares a color-family property.
        // zIndex third field feeds the per-element scrub judge (deck
        // reshuffles evolve z-order per card); legacy two-field sigs stay
        // parseable.
        const takeSample = () => {
          const cs = getComputedStyle(el); const rr = el.getBoundingClientRect();
          var chs = el.querySelectorAll('span,div,em,b,i,p,a,img,video,svg,g,path');
          var sig = ''; var csig = ''; var lim = Math.min(chs.length, 48);
          for (var sci = 0; sci < lim; sci++){ var scc = getComputedStyle(chs[sci]); sig += scc.transform + '|' + scc.opacity + '|' + scc.zIndex + ';'; csig += scc.color + ';'; }
          var animRunning = (cs.animationName && cs.animationName !== 'none' && cs.animationPlayState !== 'paused' && cs.animationDuration && cs.animationDuration !== '0s');
          samples.push({ transform: cs.transform, opacity: parseFloat(cs.opacity), top: rr.top, width: rr.width, childSig: sig, childColorSig: csig, cls: (el.getAttribute('class') || ''), scrollY: window.scrollY, docH: docH, smoothEngine: smoothEngine, animRunning: animRunning });
        };
        for (const p of [-1, -0.5, 0, 0.5, 1]) {
          window.scrollTo(0, Math.min(docH, Math.max(0, base - window.innerHeight * 0.4 + span * p)));
          await wait(SETTLE);
          takeSample();
        }
        // D17a globalSweep fallback (loop-nvti-0): near-top elements clamp the
        // local sweep to ~0 (base - 0.4*viewport + span*p all negative), so
        // scrollY never advances and a LIVE scrub reads flat — the judge then
        // mislabeled it smooth-scroll-intercept on an engine-less page.
        // Whole-page tracks (anchor-nav progress bars) also saturate a short
        // local window. When the local sweep's scroll coverage is degenerate,
        // re-sample at absolute page fractions so every scrub gets a genuinely
        // driven scroll range. Extra samples cannot false-pass a dead scrub:
        // variation still requires a measured property change across advanced
        // scroll (same argument as Fix K).
        const sYs = samples.map(s => s.scrollY);
        const ySpan = Math.max.apply(null, sYs) - Math.min.apply(null, sYs);
        if (new Set(sYs.map(y => Math.round(y))).size < 2 || ySpan < Math.min(2000, docH * 0.5)) {
          for (const gp of [0, 0.25, 0.5, 0.75, 1]) {
            window.scrollTo(0, Math.round(docH * gp));
            await wait(SETTLE);
            takeSample();
          }
        }
        rec.samples = samples;
      } else if (e.kind === 'hover') {
        ['pointerover','mouseover','mouseenter','mousemove'].forEach(t => { try { el.dispatchEvent(new MouseEvent(t, { bubbles: true })); } catch (_) {} });
        await wait(SETTLE); rec.after = snap(el, e);
      } else if (e.kind === 'click') {
        const tgt = el.querySelector('summary,button,[aria-expanded]') || el;
        try { tgt.click(); } catch (_) {}
        await wait(SETTLE); rec.after = snap(el, e);
      } else if (e.kind === 'timer') {
        // Timer state can replace the probed node on every tick. Re-query the
        // selector for every sample so a React key remount cannot leave this
        // probe reading a detached, permanently frozen element.
        let current = resolveEntry(e, el);
        if (current) current.scrollIntoView({ block: 'center' });
        const samples = [];
        const duration = Number(e.durationMs) || 0;
        const offsets = [0, 120, 240, 360, 600, 900, 1200];
        if (duration > 1200) {
          offsets.push(Math.min(5000, Math.max(1400, duration - 200)));
          offsets.push(Math.min(5400, Math.max(1600, duration + 300)));
        }
        offsets.sort((a, b) => a - b);
        let elapsed = 0;
        for (const offset of Array.from(new Set(offsets))) {
          if (offset > elapsed) await wait(offset - elapsed);
          elapsed = offset;
          current = resolveEntry(e, current);
          if (current) samples.push(snap(current, e));
          if (
            samples.length > 1
            && (
              samples[samples.length - 1].transform !== samples[0].transform
              || samples[samples.length - 1].opacity !== samples[0].opacity
              || samples[samples.length - 1].color !== samples[0].color
              || samples[samples.length - 1].backgroundColor !== samples[0].backgroundColor
              || samples[samples.length - 1].childVisualSig !== samples[0].childVisualSig
            )
          ) break;
        }
        rec.samples = samples;
        current = resolveEntry(e, current);
        rec.after = current ? snap(current, e) : (samples[samples.length - 1] || {});
      } else if (e.kind === 'carousel') {
        // Effect-agnostic carousel fingerprint: a FADE carousel holds its
        // .swiper-wrapper transform at identity (the transform channel below
        // never varies), but the active slide index and the per-slide opacity
        // vector still change. Capture both before/after so a fade carousel is
        // measured by the channel it actually drives, not only wrapper translate.
        const inst0 = swInst(el);
        const carSig = () => {
          const slides = el.querySelectorAll('.swiper-slide, [class*=\"slide\"]');
          let ops = '';
          for (let k = 0; k < Math.min(slides.length, 12); k++) {
            ops += Math.round(parseFloat(getComputedStyle(slides[k]).opacity || '1') * 100) + ',';
          }
          const ai = (inst0 && typeof inst0.activeIndex === 'number')
            ? inst0.activeIndex
            : Array.prototype.findIndex.call(slides, (s) => /(^|\\s|-)active(\\s|$|-)/.test(s.className));
          const wrap = el.querySelector('.swiper-wrapper')
            || (el.closest && el.closest('.swiper') && el.closest('.swiper').querySelector('.swiper-wrapper'))
            || el;
          return ai + '|' + getComputedStyle(wrap).transform + '|' + ops;
        };
        const carBefore = carSig();
        const explicitSwiperNext = String(e.trigger || '').trim().toLowerCase() === 'swiper-next';
        if (explicitSwiperNext) {
          // A captured swiper-next obligation is an imperative interaction, not
          // an autoplay observation. Drive that exact public Swiper API so two
          // slow carousels in one chunk cannot consume the shared wait budget
          // and make the later entry appear dead.
          if (inst0 && typeof inst0.slideNext === 'function') {
            inst0.slideNext();
            const speed = Number(inst0.params && inst0.params.speed) || 0;
            await wait(Math.max(SETTLE, Math.min(2000, speed + 100)));
          } else {
            await wait(SETTLE);
          }
        } else {
          // Natural autoplay entries remain observational. Read the carousel's
          // own delay and wait one full cycle plus margin without forcing it.
          const apDelay = inst0 && inst0.params && inst0.params.autoplay && inst0.params.autoplay.delay;
          let carWait = Math.min(6500, Math.max(SETTLE, apDelay ? Math.round(apDelay * 1.3) : 2200));
          carWait = Math.max(SETTLE, Math.min(carWait, carWaitBudget));
          carWaitBudget -= carWait;
          await wait(carWait);
        }
        rec.after = snap(el, e);
        rec.carousel = { before: carBefore, after: carSig() };
      } else if (e.kind === 'video') {
        // F2: OBSERVE natural autoplay -- do NOT force playback or mute here.
        // Forcing playback advances currentTime even when the clone video carries
        // no autoplay/muted (or lacks the mount controller that works around the
        // React muted-attr bug), so a video that never plays for a real user would
        // still pass -- measuring whether the asset can decode, not whether the
        // impl autoplays like the ref. A faithful clone autoplays by itself; bring
        // the element on-screen (IO-gated play controllers) and let it run, then
        // read the currentTime delta the impl produced on its own.
        // NOTE: keep this comment free of double quotes -- PHASE2_TEMPLATE is a
        // double-quoted bash string and a stray quote truncates it (D21).
        const v = (el.tagName === 'VIDEO') ? el : el.querySelector('video');
        if (v) { try { v.scrollIntoView({ block: 'center' }); } catch (_) {} }
        await wait(Math.max(SETTLE, 1400)); rec.after = snap(el, e);
      } else if (e.kind === 'webgl') {
        // L-MEA-3 (loop-ebpb-0): an IO play/paused canvas (playground) is
        // legitimately static while offscreen, so probing canvasInfo without
        // bringing it on-screen reads dead. Scroll it into view and settle so
        // its RAF loop resumes BEFORE the after-snapshot canvas read. Mirrors
        // the else-branch scrollIntoView pattern.
        el.scrollIntoView({ block: 'center' }); await wait(Math.max(SETTLE, 1400)); rec.after = snap(el, e);
      } else if (e.kind === 'smooth-scroll') {
        window.scrollTo(0, Math.min(2000, Math.max(1, document.documentElement.scrollHeight - window.innerHeight)));
        await wait(SETTLE); rec.after = snap(el, e);
      } else {
        el.scrollIntoView({ block: 'center' }); await wait(SETTLE); rec.after = snap(el, e);
      }
    } catch (err) { rec.error = String(err); try { rec.after = snap(el, e); } catch (_) {} }
    out[i] = rec;
   }
  }
  return JSON.stringify(out);
})()"

ENTRY_COUNT=$(run_py - "$ENTRIES_B64" <<'PY'
import base64, json, sys
print(len(json.loads(base64.b64decode(sys.argv[1]))))
PY
)
# Default 1 (H4, loop-nvti-2/4): at 5, scrub-heavy chunks exhaust the ~25s
# eval budget and entries lose their global re-sweep samples — the SAME impl
# measured 2/7 -> 6/7 varying only chunk size. One entry per chunk keeps every
# probe inside budget; the cost is more (cheap) eval round-trips.
CHUNK_SIZE="${TF_CHUNK_SIZE:-1}"
AFTER_TMP="$(mktemp)"
printf '%s' '{}' > "$AFTER_TMP"
chunk_start=0
while [ "$chunk_start" -lt "$ENTRY_COUNT" ]; do
  chunk_end=$((chunk_start + CHUNK_SIZE))
  [ "$chunk_end" -gt "$ENTRY_COUNT" ] && chunk_end=$ENTRY_COUNT
  CHUNK_JSON="[$(seq -s, "$chunk_start" $((chunk_end - 1)))]"
  PHASE2_CHUNK="${PHASE2_TEMPLATE/__CHUNK_JSON__/$CHUNK_JSON}"
  CHUNK_ERR="$(mktemp)"
  CHUNK_STATUS=0
  CHUNK_RAW=$(agent-browser --session "$SESSION" eval "$PHASE2_CHUNK" 2>"$CHUNK_ERR") \
    || CHUNK_STATUS=$?
  if [ "$CHUNK_STATUS" -ne 0 ]; then
    CHUNK_ERR_TAIL=$(tail -n 5 "$CHUNK_ERR" | tr '\n' ' ')
    echo "ERROR: phase2 chunk eval failed for indices $CHUNK_JSON (rc=$CHUNK_STATUS): ${CHUNK_ERR_TAIL:-no stderr}" >&2
    rm -f "$CHUNK_ERR" "$AFTER_TMP" "$BEFORE_TMP" "$OUT"
    exit 2
  fi
  rm -f "$CHUNK_ERR"
  CHUNK_JSON_OUT=$(printf '%s' "$CHUNK_RAW" | unwrap)
  if [ -z "$CHUNK_JSON_OUT" ] || [ "$CHUNK_JSON_OUT" = "null" ]; then
    echo "ERROR: phase2 chunk returned no JSON for indices $CHUNK_JSON" >&2
    rm -f "$AFTER_TMP" "$BEFORE_TMP" "$OUT"
    exit 2
  fi
  CHUNK_TMP="$(mktemp)"
  printf '%s' "$CHUNK_JSON_OUT" > "$CHUNK_TMP"
  MERGE_STATUS=0
  run_py - "$AFTER_TMP" "$CHUNK_TMP" "$BEFORE_TMP" \
    "$chunk_start" "$chunk_end" "$ENTRIES_B64" <<'PY' || MERGE_STATUS=$?
import base64
import json
import sys

acc_path, chunk_path, before_path = sys.argv[1:4]
chunk_start, chunk_end = map(int, sys.argv[4:6])
entries = json.loads(base64.b64decode(sys.argv[6]))


def load_object(path):
    raw = open(path).read().strip()
    value = json.loads(raw)
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    return value


try:
    acc = load_object(acc_path)
    chunk = load_object(chunk_path)
    before = load_object(before_path)
except Exception as exc:
    print(f"phase2 chunk invalid JSON: {exc}", file=sys.stderr)
    raise SystemExit(2)

expected = [
    i for i in range(chunk_start, chunk_end)
    if bool((before.get(str(i)) or {}).get("found"))
]
missing = [i for i in expected if str(i) not in chunk]
if missing:
    labels = [
        str((entries[i] or {}).get("id") or i)
        for i in missing
        if i < len(entries)
    ]
    print(
        "phase2 chunk incomplete: "
        f"missing indices {missing} entries {labels}",
        file=sys.stderr,
    )
    raise SystemExit(2)

acc.update(chunk)
json.dump(acc, open(acc_path, "w"))
PY
  rm -f "$CHUNK_TMP"
  if [ "$MERGE_STATUS" -ne 0 ]; then
    echo "ERROR: phase2 chunk validation failed for indices $CHUNK_JSON" >&2
    rm -f "$AFTER_TMP" "$BEFORE_TMP" "$OUT"
    exit 2
  fi
  chunk_start=$chunk_end
done
agent-browser --session "$SESSION" eval "(() => { document.querySelectorAll('[data-tf-idxs]').forEach(el => el.removeAttribute('data-tf-idxs')); return 'ok'; })()" >/dev/null 2>&1
AFTER_JSON=$(cat "$AFTER_TMP")

# ── Real-pointer hover pass ────────────────────────────────────────────────
# CSS `:hover` only activates under a REAL pointer; synthetic MouseEvents
# dispatched inside a page eval cannot trigger it, so the in-eval hover branch
# false-negatives every CSS-only hover into "dead". For each hover entry, move
# the genuine CDP pointer over the target and re-snapshot (color fields
# included), then patch that entry's AFTER state with the measured result.
HOVER_ROWS=$(run_py - "$ENTRIES_B64" <<'PY'
import base64, json, re, sys
rows = json.loads(base64.b64decode(sys.argv[1]))


def _fallbacks(sel):
    # F6: mirror the main pass's selector fallbacks (Fix-78/78b) so a hashed
    # CSS-module hover target the CDP pointer pass would otherwise fail to resolve
    # still gets hovered — else a working CSS-only :hover is judged dead. Order:
    # raw first, then hash-strip + [class*=], then plain [class*=].
    out = [sel]
    hashstrip = re.sub(
        r'\.([A-Za-z0-9_-]+?)(?:__[A-Za-z0-9_-]{4,})(?=[\s>:\[.]|$)',
        r'[class*="\1"]', sel)
    hashstrip = re.sub(r'\.([A-Za-z0-9_-]+)', r'[class*="\1"]', hashstrip)
    if hashstrip not in out:
        out.append(hashstrip)
    star = re.sub(r'\.([A-Za-z0-9_-]+)', r'[class*="\1"]', sel)
    if star not in out:
        out.append(star)
    return out


for i, r in enumerate(rows):
    if r.get("kind") == "hover":
        raw = [p.strip() for p in (r.get("target") or "body").split(",") if p.strip()] or ["body"]
        candidates = []
        for c in raw:
            for v in _fallbacks(c):
                if v not in candidates:
                    candidates.append(v)
        encoded = base64.b64encode(json.dumps(candidates).encode()).decode()
        sys.stdout.write(str(i) + "\t" + encoded + "\n")
PY
)
if [ -n "$HOVER_ROWS" ]; then
  HOVER_PATCH="$(mktemp)"
  : > "$HOVER_PATCH"
  while IFS=$'\t' read -r HIDX HCANDS_B64; do
    [ -z "$HCANDS_B64" ] && continue
    HJSON='{"found":false}'
    while IFS= read -r HSEL; do
      [ -z "$HSEL" ] && continue
      HSEL_B64=$(printf '%s' "$HSEL" | base64 | tr -d '\n')
      HOWNER_JS="(() => {
        document.querySelectorAll('[data-tf-hover-owner]').forEach(n => n.removeAttribute('data-tf-hover-owner'));
        document.querySelectorAll('[data-tf-hover-target]').forEach(n => n.removeAttribute('data-tf-hover-target'));
        let matches = [];
        try { matches = Array.from(document.querySelectorAll(atob('$HSEL_B64'))); } catch (_) {}
        const visible = matches.filter((node) => {
          const cs = getComputedStyle(node);
          const r = node.getBoundingClientRect();
          return cs.display !== 'none' && cs.visibility !== 'hidden'
            && parseFloat(cs.opacity || '1') > 0 && r.width > 0 && r.height > 0;
        });
        visible.sort((left, right) => {
          const a = left.getBoundingClientRect();
          const b = right.getBoundingClientRect();
          return b.width * b.height - a.width * a.height;
        });
        const el = visible[0] || matches[0] || null;
        if (!el) return false;
        el.setAttribute('data-tf-hover-target', '$HIDX');
        let cur = el && el.parentElement;
        while (cur && cur !== document.body) {
          const cs = getComputedStyle(cur);
          const r = cur.getBoundingClientRect();
          const label = String(cur.className || '') + ' ' + (cur.id || '') + ' ' + (cur.getAttribute('role') || '') + ' ' + cur.tagName;
          const navLike = /nav|menu|gnb|lnb/i.test(label) || cur.matches('li,[role=menuitem]');
          const ownerVisible = cs.display !== 'none' && cs.visibility !== 'hidden' && parseFloat(cs.opacity || '1') > 0 && r.width > 0 && r.height > 0;
          if (navLike && ownerVisible) {
            cur.setAttribute('data-tf-hover-owner', '$HIDX');
            break;
          }
          cur = cur.parentElement;
        }
        return true;
      })()"
      agent-browser --session "$SESSION" eval "$HOWNER_JS" >/dev/null 2>&1 || true
      agent-browser --session "$SESSION" hover "[data-tf-hover-owner='$HIDX']" >/dev/null 2>&1 || true
      agent-browser --session "$SESSION" wait 100 >/dev/null 2>&1 || true
      # Keep the concrete candidate probe: besides opening hidden menus, this
      # exercises each CSS-module fallback instead of only carrying it inside
      # the marker-selection eval.
      agent-browser --session "$SESSION" scrollintoview "$HSEL" >/dev/null 2>&1 || true
      agent-browser --session "$SESSION" scrollintoview "[data-tf-hover-target='$HIDX']" >/dev/null 2>&1 || true
      # A sticky target can jump from its pinned viewport position back to its
      # natural document position during scrollIntoView. Give layout and the
      # automation locator a frame to converge before calculating the pointer
      # coordinates; an immediate hover can otherwise use the stale pinned box.
      agent-browser --session "$SESSION" wait 250 >/dev/null 2>&1 || true
      agent-browser --session "$SESSION" hover "$HSEL" >/dev/null 2>&1 || true
      agent-browser --session "$SESSION" hover "[data-tf-hover-target='$HIDX']" >/dev/null 2>&1 || true
      HSNAP_JS="(async () => { $SNAP_JS
        const wait = (ms) => new Promise(r => setTimeout(r, ms));
        const el = document.querySelector('[data-tf-hover-target=\"$HIDX\"]');
        if (!el) return JSON.stringify({ found: false });
        await wait($SETTLE_MS);
        // NOTE: do NOT echo the selector here. F6 fallbacks are [class*="…"]
        // selectors that contain double quotes; round-tripped through
        // agent-browser's double-JSON encoding + unwrap they arrive as \\\" and
        // make this blob invalid JSON, so the merge's json.loads() throws and
        // silently drops the hover patch (except: continue) — a hashed hover then
        // false-fails. The merge only needs found + after.
        return JSON.stringify({ found: true, after: snap(el, { kind: 'hover' }) });
      })()"
      HRAW=$(agent-browser --session "$SESSION" eval "$HSNAP_JS" 2>/dev/null)
      HJSON=$(printf '%s' "$HRAW" | unwrap)
      if run_py - "$HJSON" <<'PY' >/dev/null 2>&1
import json, sys
try:
    sys.exit(0 if json.loads(sys.argv[1]).get("found") else 1)
except Exception:
    sys.exit(1)
PY
      then
        break
      fi
    done < <(run_py - "$HCANDS_B64" <<'PY'
import base64, json, sys
try:
    rows = json.loads(base64.b64decode(sys.argv[1]))
except Exception:
    rows = []
for row in rows:
    print(str(row))
PY
)
    printf '%s\t%s\n' "$HIDX" "$HJSON" >> "$HOVER_PATCH"
  done <<< "$HOVER_ROWS"
  PATCHED_TMP="$(mktemp)"
  run_py - "$AFTER_TMP" "$HOVER_PATCH" "$PATCHED_TMP" "$BEFORE_TMP" <<'PY'
import json, sys
try:
    after = json.load(open(sys.argv[1]))
except Exception:
    after = {}
try:
    before = json.load(open(sys.argv[4]))
except Exception:
    before = {}

def style_changed(candidate, baseline):
    fields = (
        "color", "backgroundColor", "borderColor", "outlineColor",
        "textDecorationColor", "boxShadow", "filter", "backgroundImage",
        "fontWeight", "pseudoBefore", "pseudoAfter",
        "opacity", "transform", "width", "height",
    )
    return any(str((candidate or {}).get(k)) != str((baseline or {}).get(k)) for k in fields)

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
    baseline = (before.get(idx) or {}).get("before", {}) or {}
    current_after = cur.get("after", {}) or {}
    pointer_after = rec.get("after", {}) or {}
    # Keep the synthetic MouseEvent measurement when it already observed a
    # real style delta and the CDP pointer pass did not. This prevents the
    # fallback pass from erasing evidence produced by JS hover handlers while
    # still allowing true CSS :hover-only deltas to replace a flat synthetic
    # snapshot.
    if style_changed(current_after, baseline) and not style_changed(pointer_after, baseline):
        continue
    cur["found"] = True
    cur["after"] = pointer_after
    after[idx] = cur
json.dump(after, open(sys.argv[3], "w"))
PY
  mv "$PATCHED_TMP" "$AFTER_TMP"
fi

# ── Wheel re-probe for smooth-scroll-blocked scrubs ──────────────────────
# Smooth-scroll engines (Lenis/ScrollSmoother/locomotive) intercept
# programmatic window.scrollTo, so Phase-2 scrub samples come back flat and
# used to be classified "unmeasurable" (silently counted toward PASS).
# Wheel events DO drive these engines (animation-detection.md scroll-method
# table) — re-probe blocked scrubs with an `agent-browser scroll` sweep so
# they get a measured verdict either way: varied -> pass, flat while the
# element moved through the viewport -> dead, undriveable -> unmeasurable.
WHEEL_TARGETS="$(run_py - "$SPEC" "$AFTER_TMP" <<'PY'
import json, sys

spec = json.load(open(sys.argv[1]))
try:
    after = json.load(open(sys.argv[2]))
except Exception:
    after = {}
entries = [t for t in (spec.get("transitions") or []) if isinstance(t, dict)]


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def norm_t(v):
    s = "" if v is None else str(v)
    return "none" if s in ("", "none") else s


flagged = []
for i, t in enumerate(entries):
    rec = after.get(str(i)) or {}
    samples = rec.get("samples") or []
    if not samples or not any(s.get("smoothEngine") for s in samples):
        continue
    first = samples[0]
    varied = False
    for s in samples[1:]:
        if norm_t(s.get("transform")) != norm_t(first.get("transform")):
            varied = True
            break
        a, b = f(s.get("opacity")), f(first.get("opacity"))
        if a is not None and b is not None and abs(a - b) > 0.05:
            varied = True
            break
    if not varied and str(t.get("target", "")):
        flagged.append({"idx": i, "target": str(t.get("target"))})
print(json.dumps(flagged))
PY
)"

if [ -n "$WHEEL_TARGETS" ] && [ "$WHEEL_TARGETS" != "[]" ]; then
  WHEEL_B64=$(printf '%s' "$WHEEL_TARGETS" | base64 | tr -d '\n')
  WHEEL_SAMPLES="$(mktemp)"
  : > "$WHEEL_SAMPLES"
  # Reset to top: engine API when exposed, native scrollTo otherwise. The real
  # Lenis instance is window.__lenis (window.lenis is often a {version} decoy
  # with no scrollTo), so try it first, then any window.lenis that does expose
  # scrollTo, then native.
  agent-browser --session "$SESSION" eval '(() => { try { var L = window.__lenis || (window.lenis && typeof window.lenis.scrollTo === "function" ? window.lenis : null); if (L && typeof L.scrollTo === "function") L.scrollTo(0, {immediate: true, force: true}); } catch (_) {} window.scrollTo(0, 0); return 1; })()' >/dev/null 2>&1
  sleep 1
  DOC_H_RAW=$(agent-browser --session "$SESSION" eval '(() => Math.max(1, document.documentElement.scrollHeight - window.innerHeight))()' 2>/dev/null | unwrap | tr -dc '0-9')
  DOC_H=${DOC_H_RAW:-12000}
  [ "$DOC_H" -lt 1 ] 2>/dev/null && DOC_H=12000
  WHEEL_STEP_PX="${WHEEL_STEP_PX:-700}"
  WHEEL_STEPS=$(( DOC_H / WHEEL_STEP_PX + 1 ))
  [ "$WHEEL_STEPS" -gt 36 ] && WHEEL_STEPS=36
  WHEEL_SAMPLE_JS="(() => {
    const targets = JSON.parse(atob('$WHEEL_B64'));
    const out = {};
    // engineDriven: a real engine API is available to drive the VIRTUAL scroll
    // (Lenis __lenis.scrollTo / ScrollSmoother.scrollTo). smoothEngine: a smooth
    // engine is present at all. When a smooth engine is present but NOT drivable,
    // the per-step drive falls back to native window.scrollTo, which advances
    // scrollY WITHOUT driving the engine — so a flat scrub there is unmeasurable,
    // not dead. The gate uses these two flags to tell those cases apart.
    const __L = window.__lenis || (window.lenis && typeof window.lenis.scrollTo === 'function' ? window.lenis : null);
    const __sm = (window.ScrollSmoother && window.ScrollSmoother.get) ? window.ScrollSmoother.get() : null;
    const engineDriven = !!((__L && typeof __L.scrollTo === 'function') || (__sm && __sm.scrollTo));
    const smoothEngine = !!(window.__lenis || window.lenis || window.ScrollSmoother
      || /lenis|has-scroll-smooth/.test(document.documentElement.className)
      || document.querySelector('[data-scroll-container],#smooth-content,[class*=lenis]'));
    for (const t of targets) {
      let el = null;
      try { el = document.querySelector(t.target); } catch (_) {}
      if (!el && t.target && t.target.indexOf('.') >= 0) {
        try { el = document.querySelector(t.target.replace(/\.([A-Za-z0-9_-]+)/g, '[class*=\"\$1\"]')); } catch (_) {}
        if (!el) {
          try { el = document.querySelector(t.target.replace(/\.([A-Za-z0-9_-]+?)(?:__[A-Za-z0-9_-]{4,})(?=[\s>:\[.]|$)/g, '[class*=\"\$1\"]').replace(/\.([A-Za-z0-9_-]+)/g, '[class*=\"\$1\"]')); } catch (_) {}
        }
      }
      if (!el) { continue; }
      const cs = getComputedStyle(el); const rr = el.getBoundingClientRect();
      // childSig + width per wheel sample (loop-e2e-5): descendant motion and
      // inline width tracks are the dominant scrub styles this re-probe
      // previously read as flat.
      var chs = el.querySelectorAll('span,div,em,b,i,p,a,img,video,svg,g,path');
      var sig = ''; var csig = ''; var lim = Math.min(chs.length, 48);
      for (var wci = 0; wci < lim; wci++){ var wcc = getComputedStyle(chs[wci]); sig += wcc.transform + '|' + wcc.opacity + '|' + wcc.zIndex + ';'; csig += wcc.color + ';'; }
      var animRunning = (cs.animationName && cs.animationName !== 'none' && cs.animationPlayState !== 'paused' && cs.animationDuration && cs.animationDuration !== '0s');
      out[t.idx] = { transform: cs.transform, opacity: parseFloat(cs.opacity), top: rr.top, width: rr.width, childSig: sig, childColorSig: csig, cls: (el.getAttribute('class') || ''), scrollY: window.scrollY, wheelDriven: true, smoothEngine: smoothEngine, engineDriven: engineDriven, animRunning: animRunning };
    }
    return JSON.stringify(out);
  })()"
  # Fix K (loop-e2e-6): sample once at the top BEFORE the first wheel step —
  # early-page scrubs (hero width 80vw->100vw over scrollY 0..~300) saturate
  # before the first 700px step, so a sweep that only samples after stepping
  # reads a permanently-flat tail and false-fails a live scrub.
  WHEEL_ROW=$(agent-browser --session "$SESSION" eval "$WHEEL_SAMPLE_JS" 2>/dev/null | unwrap)
  [ -n "$WHEEL_ROW" ] && printf '%s\n' "$WHEEL_ROW" >> "$WHEEL_SAMPLES"
  # Drive to an ABSOLUTE target each step. Smooth-scroll engines intercept BOTH
  # wheel events AND programmatic window.scrollTo, so the old wheel-only sweep
  # left Lenis pages dead-flat (element never moved -> forced "unmeasurable").
  # Lenis exposes window.__lenis.scrollTo (the live instance; window.lenis is a
  # {version} decoy), ScrollSmoother exposes ScrollSmoother.get().scrollTo —
  # call the engine's own API so the virtual scroll actually advances. The wheel
  # scroll stays as a fallback for wheel-only engines (locomotive variants) and
  # is a harmless no-op under Lenis.
  WHEEL_TARGET_Y=0
  for _ in $(seq 1 "$WHEEL_STEPS"); do
    WHEEL_TARGET_Y=$(( WHEEL_TARGET_Y + WHEEL_STEP_PX ))
    agent-browser --session "$SESSION" eval "(() => { var y = $WHEEL_TARGET_Y; try { var L = window.__lenis || (window.lenis && typeof window.lenis.scrollTo === 'function' ? window.lenis : null); if (L && typeof L.scrollTo === 'function') { L.scrollTo(y, {immediate: true, force: true}); return 'lenis'; } if (window.ScrollSmoother && window.ScrollSmoother.get) { var s = window.ScrollSmoother.get(); if (s && s.scrollTo) { s.scrollTo(y, false); return 'smoother'; } } } catch (_) {} window.scrollTo(0, y); return 'native'; })()" >/dev/null 2>&1
    agent-browser --session "$SESSION" scroll down "$WHEEL_STEP_PX" >/dev/null 2>&1
    sleep 0.35
    WHEEL_ROW=$(agent-browser --session "$SESSION" eval "$WHEEL_SAMPLE_JS" 2>/dev/null | unwrap)
    [ -n "$WHEEL_ROW" ] && printf '%s\n' "$WHEEL_ROW" >> "$WHEEL_SAMPLES"
  done
  WHEEL_MERGED="$(mktemp)"
  run_py - "$AFTER_TMP" "$WHEEL_SAMPLES" "$WHEEL_MERGED" <<'PY'
import json, sys

after = json.load(open(sys.argv[1]))
series = {}
for line in open(sys.argv[2]):
    line = line.strip()
    if not line:
        continue
    try:
        row = json.loads(line)
    except Exception:
        continue
    if isinstance(row, str):
        try:
            row = json.loads(row)
        except Exception:
            continue
    if not isinstance(row, dict):
        continue
    for idx, sample in row.items():
        if isinstance(sample, dict):
            series.setdefault(str(idx), []).append(sample)
for idx, samples in series.items():
    if len(samples) >= 2:
        rec = after.get(idx) or {"found": True, "before": {}}
        rec["samples"] = samples
        rec["found"] = True
        after[idx] = rec
json.dump(after, open(sys.argv[3], "w"))
PY
  mv "$WHEEL_MERGED" "$AFTER_TMP"
  rm -f "$WHEEL_SAMPLES"
fi

# ── Load-phase probe (loop-e2e-5) ─────────────────────────────────────────
# Splash overlays, autoplay tracks and timer carousels fire BEFORE the
# post-sweep probes can observe them (the intro overlay settles ~2.45s after
# navigate; the carousel rotates content, not transforms). Fresh-navigate and
# sample those entries through the first seconds; the judge accepts this
# series ONLY for load-class triggers (splash/load/autoplay/timer) — never
# for IO/scroll reveals (codex review: bypass risk).
LOAD_IDXS=$(run_py - "$SPEC" <<'PY'
import json, sys
spec = json.load(open(sys.argv[1]))
idxs = []
for i, t in enumerate(spec.get("transitions") or []):
    if not isinstance(t, dict):
        continue
    trig = str(t.get("trigger") or "").lower()
    is_load = any(k in trig for k in ("load", "splash", "autoplay", "timer", "interval"))
    is_io = any(k in trig for k in ("scroll", "useinview", "io ", "io-", "intersection"))
    if is_load and not is_io:
        idxs.append(i)
print(",".join(str(i) for i in idxs))
PY
)
LOAD_TMP="$(mktemp)"
printf '%s' '{}' > "$LOAD_TMP"
if [ -n "$LOAD_IDXS" ]; then
  LOAD_JSON="[${LOAD_IDXS}]"
  agent-browser --session "$SESSION" navigate "$URL" >/dev/null 2>&1
  LOAD_RAW=$(agent-browser --session "$SESSION" eval "(async () => {
    const ENTRIES = JSON.parse(atob('$ENTRIES_B64'));
    const IDXS = $LOAD_JSON;
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    const out = {};
    const sample = () => {
      for (const i of IDXS) {
        const e = ENTRIES[i];
        let el = null;
        try { el = document.querySelector(e.target); } catch (_) {}
        if (!el && e.target && e.target.indexOf('.') >= 0) {
          try { el = document.querySelector(e.target.replace(/\.([A-Za-z0-9_-]+)/g, '[class*=\"\$1\"]')); } catch (_) {}
        }
        if (!el) { continue; }
        const cs = getComputedStyle(el);
        var chs = el.querySelectorAll('span,div,em,b,i,p,a');
        var sig = ''; var lim = Math.min(chs.length, 16);
        for (var lci = 0; lci < lim; lci++){ var lcc = getComputedStyle(chs[lci]); sig += lcc.transform + '|' + lcc.opacity + ';'; }
        var srcs = [];
        el.querySelectorAll('img').forEach(function (im) { srcs.push((im.currentSrc || im.src || '').split('/').pop()); });
        var row = { opacity: parseFloat(cs.opacity), transform: cs.transform, childSig: sig, imgSrcs: srcs };
        var sp = el.querySelector('path[stroke-dasharray], path[data-stroke-draw]');
        if (sp) { row.strokeDashoffset = getComputedStyle(sp).strokeDashoffset; }
        (out[i] = out[i] || []).push(row);
      }
    };
    for (let t = 0; t < 10; t++) { sample(); await wait(300); }
    await wait(1000); sample();
    return JSON.stringify(out);
  })()" 2>/dev/null)
  LOAD_JSON_OUT=$(printf '%s' "$LOAD_RAW" | unwrap)
  if [ -n "$LOAD_JSON_OUT" ] && [ "$LOAD_JSON_OUT" != "null" ]; then
    printf '%s' "$LOAD_JSON_OUT" > "$LOAD_TMP"
  fi
fi

# ── Fresh-context reveal re-probe (L-MEA-8) ───────────────────────────────
# A ONE-SHOT IO reveal (data-in-view flip; staggered card stack) completes
# during the PHASE1 mount sweep, so the main pass reads before==after==final
# and the reveal looks dead. For IO/inview/intersection reveal entries whose
# main-pass before/after are identical-final, re-navigate fresh: snapshot the
# target's pre-state OUT of view, then scroll it IN and sample the transition
# window (0/300/900ms). The judge accepts a pre->settled delta or in-flight
# variation; a flat re-probe keeps the honest fail. Distinct channel from
# loadSamples, which still excludes IO reveals (codex anti-bypass).
REVEAL_TARGETS=$(run_py - "$SPEC" "$BEFORE_TMP" "$AFTER_TMP" <<'PY'
import json, sys

from ui_clone.gates.transition_fires import classify

spec = json.load(open(sys.argv[1]))
try:
    before = json.load(open(sys.argv[2]))
except Exception:
    before = {}
try:
    after = json.load(open(sys.argv[3]))
except Exception:
    after = {}
entries = [t for t in (spec.get("transitions") or []) if isinstance(t, dict)]
IO = ("inview", "in-view", "in view", "intersection", "io ", "io-", "viewport")


def norm(v):
    s = "" if v is None else str(v)
    return "none" if s in ("", "none") else s


flagged = []
for i, t in enumerate(entries):
    if classify(t) != "reveal":
        continue
    trig = str(t.get("trigger") or "").lower()
    if not any(k in trig for k in IO):
        continue
    if not str(t.get("target", "")):
        continue
    b = (before.get(str(i)) or {}).get("before", {}) or {}
    a = (after.get(str(i)) or {}).get("after", {}) or {}
    same = (
        norm(b.get("transform")) == norm(a.get("transform"))
        and str(b.get("opacity")) == str(a.get("opacity"))
    )
    target = str(t.get("target"))
    strict_not_active = ":not(.active)" in "".join(target.lower().split())
    if same or strict_not_active:
        flagged.append({"idx": i, "target": target})
print(json.dumps(flagged))
PY
)
REVEAL_TMP="$(mktemp)"
printf '%s' '{}' > "$REVEAL_TMP"
if [ -n "$REVEAL_TARGETS" ] && [ "$REVEAL_TARGETS" != "[]" ]; then
  REVEAL_B64=$(printf '%s' "$REVEAL_TARGETS" | base64 | tr -d '\n')
  agent-browser --session "$SESSION" navigate "$URL" >/dev/null 2>&1
  sleep $(( (WAIT_MS + 999) / 1000 ))
  REVEAL_RAW=$(agent-browser --session "$SESSION" eval "(async () => {
    const TARGETS = JSON.parse(atob('$REVEAL_B64'));
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    const resolve = (t) => {
      let el = null;
      try { el = document.querySelector(t); } catch (_) {}
      if (!el && t && t.indexOf('.') >= 0) {
        try { el = document.querySelector(t.replace(/\.([A-Za-z0-9_-]+)/g, '[class*=\"\$1\"]')); } catch (_) {}
        if (!el) {
          try { el = document.querySelector(t.replace(/\.([A-Za-z0-9_-]+?)(?:__[A-Za-z0-9_-]{4,})(?=[\s>:\[.]|$)/g, '[class*=\"\$1\"]').replace(/\.([A-Za-z0-9_-]+)/g, '[class*=\"\$1\"]')); } catch (_) {}
        }
      }
      return el;
    };
    const snapR = (el) => {
      const cs = getComputedStyle(el);
      var chs = el.querySelectorAll('span,div,em,b,i,p,a'); var sig = ''; var childTd = ''; var childAd = ''; var lim = Math.min(chs.length, 16);
      for (var i = 0; i < lim; i++){
        var cc = getComputedStyle(chs[i]);
        sig += cc.transform + '|' + cc.opacity + ';';
        childTd += cc.transitionDuration + ';';
        childAd += cc.animationDuration + ';';
      }
      return {
        opacity: parseFloat(cs.opacity),
        transform: cs.transform,
        childSig: sig,
        transitionDuration: cs.transitionDuration,
        animationName: cs.animationName,
        animationDuration: cs.animationDuration,
        childTransitionDuration: childTd,
        childAnimationDuration: childAd
      };
    };
    const out = {};
    const startT = Date.now();
    for (const tg of TARGETS) {
      // C4: bound the total wall time so a many-target spec cannot blow the
      // ~25s eval-budget (fail-safe: stop probing, keep what we have).
      if (Date.now() - startT > 18000) { break; }
      window.scrollTo(0, 0); await wait(200);
      let el = resolve(tg.target);
      let pre = el ? snapR(el) : null;
      if (!el) {
        const docH = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
        const step = Math.max(300, Math.floor(window.innerHeight * 0.5));
        // C4: cap the mount hunt — on a very tall page an unbounded 120ms-per-
        // step sweep could exhaust the eval-budget. 40 steps is a hard ceiling.
        const maxHunt = 40; let hunt = 0;
        for (let y = 0; y <= docH && hunt < maxHunt; y += step, hunt++) {
          window.scrollTo(0, y); await wait(120);
          el = resolve(tg.target);
          if (el) { if (el.getBoundingClientRect().top > window.innerHeight * 0.9) { pre = snapR(el); } break; }
        }
      }
      if (!el) { out[tg.idx] = { pre: pre, samples: [] }; continue; }
      const samples = [];
      el.scrollIntoView({ block: 'center' });
      samples.push(snapR(el));
      await wait(300); samples.push(snapR(el));
      await wait(600); samples.push(snapR(el));
      out[tg.idx] = { pre: pre, samples: samples };
    }
    return JSON.stringify(out);
  })()" 2>/dev/null)
  REVEAL_JSON_OUT=$(printf '%s' "$REVEAL_RAW" | unwrap)
  if [ -n "$REVEAL_JSON_OUT" ] && [ "$REVEAL_JSON_OUT" != "null" ]; then
    printf '%s' "$REVEAL_JSON_OUT" > "$REVEAL_TMP"
  fi
fi

# ── Merge before+after (keyed by entry index) into observations keyed by id. ─
OBS_TMP="$(mktemp)"
run_py - "$SPEC" "$BEFORE_TMP" "$AFTER_TMP" "$OBS_TMP" "$LOAD_TMP" "$REVEAL_TMP" <<'PY'
import json, sys

spec_path, before_path, after_path, out_path, load_path, reveal_path = sys.argv[1:7]


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}


spec = load(spec_path)
before = load(before_path) or {}
after = load(after_path) or {}
load_series = load(load_path) or {}
reveal_series = load(reveal_path) or {}
entries = [t for t in (spec.get("transitions") or []) if isinstance(t, dict)]
obs = {}
for i, t in enumerate(entries):
    b = before.get(str(i)) or {}
    a = after.get(str(i)) or {}
    ls = load_series.get(str(i)) or []
    # Load-phase samples are their own evidence channel: an entry the
    # post-sweep probes never resolved can still be judged from the
    # fresh-navigate series (splash overlays unmount after settle).
    found = (bool(b.get("found")) and bool(a.get("found"))) or bool(ls)
    obs[str(t.get("id", ""))] = {
        "found": found,
        "before": b.get("before", {}) or {},
        "after": a.get("after", {}) or {},
        "samples": a.get("samples", []) or [],
        "loadSamples": ls,
        # Effect-agnostic carousel fingerprint (active index + slide opacity
        # vector) captured in the phase-2 probe. Without carrying it here the
        # verdict's fade-carousel channel is dead — a fade carousel holds its
        # wrapper transform at identity, so it would fail on the transform
        # channel alone (T-4: this field was dropped and the hero stayed fail).
        "carousel": a.get("carousel"),
        # Fresh-context reveal re-probe (L-MEA-8): pre-state + in-flight samples
        # for a one-shot IO reveal that completed during the settle mount sweep.
        "revealProbe": reveal_series.get(str(i)),
    }
json.dump(obs, open(out_path, "w"))
PY

# ── Decide + write artifact + exit code (pure module, unit-tested). ───────
run_py -m ui_clone.gates.transition_fires "$SPEC" "$OBS_TMP" "$ASSET_SUB" "$OUT" --impl-url "$URL"
RC=$?

rm -f "$BEFORE_TMP" "$AFTER_TMP" "$OBS_TMP" "$LOAD_TMP" "$REVEAL_TMP"
exit $RC
