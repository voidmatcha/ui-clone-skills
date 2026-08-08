#!/usr/bin/env bash
# reconcile-spec-targets.sh — reveal interaction-gated spec targets and splice
# them into a merged DOM scaffold so the transpiler can emit real nodes.
#
# Why it matters:
#   The single-state structure.json snapshot never sees interaction-revealed
#   elements (dropdown CTAs, tab panels, share popovers). Their classes exist in
#   the CSS bundle and transition-spec, so the mirrored hover CSS ships — but the
#   transpiler has no node to attach it to, spec-coverage honestly fails them, and
#   transition-fires later reports 'element not found'. This driver drives the live
#   ref with bounded stimulation (scroll, hover nav, click tabs/expanders — never
#   navigating <a href>), captures each revealed element's subtree in
#   structure.json's node shape, and hands it to the deterministic merge, which
#   splices it under its observed parent into structure.merged.json. structure.json
#   itself is never mutated (it is provenance-stamped). Targets that stay
#   unrevealable become missingSpecTargets[] for the Step-7 synthesis obligation.
#
# Usage:
#   reconcile-spec-targets.sh <ref_url> <ref_dir> [--session S]
#
# Output (all under <ref_dir>):
#   spec-targets-missing.json   classify result (present/missing)
#   revealed-targets.json       {targets:[{selector,foundVia,subtreeHtml,subtree,ancestors}]}
#   structure.merged.json       structure.json + spliced revealed subtrees
#   reconcile-report.json       {merged, mergedTargets[], missingSpecTargets[]}
#
# Exit: 0 (reconcile ran; unresolved targets are recorded, not fatal),
#       2 setup error (missing structure.json / helper).

set -uo pipefail

REF_URL="${1:?Usage: reconcile-spec-targets.sh <ref_url> <ref_dir> [--session S]}"
REF_DIR="${2:?Usage: reconcile-spec-targets.sh <ref_url> <ref_dir> [--session S]}"
SESSION="reconcile"
shift 2 || true
while [ $# -gt 0 ]; do
  case "$1" in
    --session) SESSION="$2"; shift 2 ;;
    *) shift ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/_reconcile_spec_targets.py"
if [ ! -f "$HELPER" ]; then
  echo "ERROR: helper not found: $HELPER" >&2
  exit 2
fi
if [ ! -f "$REF_DIR/structure.json" ]; then
  echo "ERROR: structure.json not found in $REF_DIR (run Phase 2 first)" >&2
  exit 2
fi

# ── 1. classify: which spec/hover targets are absent from structure.json ──────
if ! python3 "$HELPER" classify "$REF_DIR"; then
  echo "ERROR: classify failed" >&2
  exit 2
fi
MISSING="$REF_DIR/spec-targets-missing.json"
NMISS=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))['missing']))" "$MISSING" 2>/dev/null || echo 0)

REVEALED="$REF_DIR/revealed-targets.json"
if [ "$NMISS" -eq 0 ]; then
  echo "▸ reconcile: no missing targets — nothing to reveal"
  echo '{"targets":[]}' > "$REVEALED"
  python3 "$HELPER" merge "$REF_DIR" "$REVEALED"
  exit 0
fi

# ── 2. build the reveal eval (subject tokens injected) ────────────────────────
REVEAL_JS="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/reveal.$$.js")"
RAW_OUT="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/reveal.$$.out")"
trap 'rm -f "$REVEAL_JS" "$RAW_OUT"; agent-browser --session "$SESSION" close >/dev/null 2>&1 || true' EXIT

python3 - "$MISSING" "$REVEAL_JS" <<'PY'
import json
import sys

missing = json.load(open(sys.argv[1]))["missing"]
# Subject token = the target LEAF (last class/id token) of each missing selector.
tokens: list[str] = []
seen = set()
for m in missing:
    toks = m.get("tokens") or []
    if not toks:
        continue
    subject = toks[-1]
    if subject not in seen and len(subject) >= 3:
        seen.add(subject)
        tokens.append(subject)
tokens = tokens[:40]  # bound the reveal

body = """(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const TOKENS = __TOKENS__;
  const STYLE_KEYS = ["display","position","width","height","min-height","padding",
    "border","background-color","background-position","color","font-family",
    "font-size","font-weight","flex","flex-direction"];
  const found = {};
  const captureNode = (el, depth) => {
    const cs = getComputedStyle(el);
    const styles = {};
    for (const k of STYLE_KEYS) styles[k] = cs.getPropertyValue(k);
    const node = { tag: el.tagName.toLowerCase(), class: el.getAttribute("class") || "",
      display: styles.display, position: styles.position, styles, children: [] };
    if (depth < 6) {
      const kids = [...el.children].slice(0, 12);
      for (const k of kids) node.children.push(captureNode(k, depth + 1));
    }
    if (el.children.length === 0) {
      const txt = (el.textContent || "").trim().slice(0, 200);
      if (txt) node.text = txt;
    }
    return node;
  };
  const ancestorsOf = (el) => {
    const out = []; let p = el.parentElement; let d = 0;
    while (p && p.tagName !== "BODY" && p.tagName !== "HTML" && d < 14) {
      out.push({ id: p.id || undefined, classes: [...p.classList] });
      p = p.parentElement; d++;
    }
    return out;
  };
  const capture = (step) => {
    for (const t of TOKENS) {
      if (found[t]) continue;
      let el = null;
      try { el = document.querySelector('[class*="' + t + '"]'); } catch (e) { el = null; }
      if (el) found[t] = { token: t, selector: "." + t, foundVia: step,
        subtreeHtml: (el.outerHTML || "").slice(0, 4000),
        subtree: captureNode(el, 0), ancestors: ancestorsOf(el) };
    }
  };
  const remaining = () => TOKENS.some((t) => !found[t]);
  capture("rest");
  const H = document.body.scrollHeight;
  for (let y = 0; y <= H && remaining(); y += 500) { window.scrollTo(0, y); await sleep(200); capture("scroll"); }
  window.scrollTo(0, 0); await sleep(150);
  if (remaining()) {
    const navs = [...document.querySelectorAll("header *, nav *, [class*=nav_item]")].slice(0, 120);
    for (const el of navs) { if (!remaining()) break;
      try { el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
            el.dispatchEvent(new MouseEvent("mouseenter", { bubbles: true })); } catch (e) {}
      await sleep(30); capture("hover-nav"); }
  }
  if (remaining()) {
    const trigs = [...document.querySelectorAll("button, [role=tab], [aria-expanded], [data-state], [class*=trigger], [class*=tab], [class*=toggle], [class*=expand], [class*=share], [class*=more]")].slice(0, 120);
    for (const el of trigs) { if (!remaining()) break;
      const tag = el.tagName.toLowerCase();
      const href = el.getAttribute ? el.getAttribute("href") : null;
      if (tag === "a" && href && href !== "#" && !href.startsWith("#")) continue;
      try { el.click(); } catch (e) {}
      await sleep(60); capture("click"); }
  }
  return JSON.stringify({ found, revealed: Object.keys(found), missingAfter: TOKENS.filter((t) => !found[t]) });
})()"""
open(sys.argv[2], "w").write(body.replace("__TOKENS__", json.dumps(tokens)))
print(f"reveal tokens: {len(tokens)}", file=sys.stderr)
PY

# ── 3. drive the live ref ─────────────────────────────────────────────────────
: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME
if ! agent-browser --session "$SESSION" open "$REF_URL" >/dev/null 2>&1; then
  echo "⚠ reconcile: agent-browser open failed for $REF_URL — recording all targets as unresolved" >&2
  echo '{"targets":[]}' > "$REVEALED"
  python3 "$HELPER" merge "$REF_DIR" "$REVEALED"
  exit 0
fi
agent-browser --session "$SESSION" eval "$(cat "$REVEAL_JS")" > "$RAW_OUT" 2>/dev/null || true

# ── 4. parse the (double-JSON-encoded) eval output into revealed-targets.json ─
python3 - "$RAW_OUT" "$REVEALED" <<'PY'
import json
import sys

raw = open(sys.argv[1]).read().strip()
obj = None
if raw:
    try:
        obj = json.loads(raw)
        if isinstance(obj, str):
            obj = json.loads(obj)  # agent-browser double-encodes eval results
    except json.JSONDecodeError:
        obj = None
targets = []
if isinstance(obj, dict):
    for rec in (obj.get("found") or {}).values():
        if isinstance(rec, dict):
            targets.append(rec)
json.dump({"schemaVersion": 1, "targets": targets,
           "revealed": [t.get("token") for t in targets]},
          open(sys.argv[2], "w"), indent=2)
print(f"revealed: {len(targets)}", file=sys.stderr)
PY

# ── 5. deterministic merge → structure.merged.json + reconcile-report.json ────
python3 "$HELPER" merge "$REF_DIR" "$REVEALED"
exit 0
