#!/usr/bin/env bash
# capture-runtime-text.sh — capture the FINAL text of JS-injected elements that
# are empty/animated in the static DOM (e.g. count-up stat numbers), so the
# deterministic transpiler can inject real values instead of blank elements.
#
# Scrolls the live page to trigger scroll-driven count-ups, then records, per
# class token, the final standalone-number text in document order.
#
# Usage: capture-runtime-text.sh <url> <session> <ref-dir>
# Writes <ref-dir>/runtime-text.json : {"byClass": {"<token>": ["50%", ...]}}
set -euo pipefail

URL="${1:?Usage: capture-runtime-text.sh <url> <session> <ref-dir>}"
SESSION="${2:?Usage: capture-runtime-text.sh <url> <session> <ref-dir>}"
REF_DIR="${3:?Usage: capture-runtime-text.sh <url> <session> <ref-dir>}"
OUT="$REF_DIR/runtime-text.json"

agent-browser open "$URL" --session "$SESSION" >/dev/null 2>&1 || true
sleep 2
# Accumulator init + per-step collector. Scroll-driven count-ups animate while
# their section is in view, so we slow-scroll and keep the MAX value seen per
# element (keyed by class token + document position) across the whole pass.
agent-browser eval --session "$SESSION" 'window.__rt = {}; "init"' >/dev/null 2>&1 || true
COLLECT='(() => {
  const numRe = /^\s*\d[\d.,]*\s*%?\s*$/;
  const num = s => parseFloat(String(s).replace(/[^\d.]/g, "")) || 0;
  for (const el of document.querySelectorAll("*")) {
    if (el.children.length > 1) continue;
    const t = (el.textContent || "").trim();
    if (!t || !numRe.test(t)) continue;
    const cls = (el.className || "").toString().trim();
    if (!cls) continue;
    const token = cls.split(/\s+/)[0].split("__")[0];
    if (!token) continue;
    const r = el.getBoundingClientRect();
    // round absolute Y to 150px buckets so mid-animation frames of the SAME
    // element (which drift a little) merge to one entry, keeping the max value.
    const ay = Math.round((r.top + window.scrollY) / 150) * 150;
    const key = token + "|" + ay;
    const prev = window.__rt[key];
    if (!prev || num(t) > num(prev.t)) window.__rt[key] = { token, t, ay };
  }
  return "ok";
})()'
# Slow pass top->bottom. Dwell >= the count-up animation duration (~1.5s) at
# each step so a scroll-triggered count-up FINISHES while in view before we move
# on; we keep the max value seen per element, capturing the settled final.
for i in $(seq 0 12); do
  agent-browser eval --session "$SESSION" \
    "window.scrollTo(0, document.body.scrollHeight*($i/12)); 'ok'" >/dev/null 2>&1 || true
  sleep 0.6
  agent-browser eval --session "$SESSION" "$COLLECT" >/dev/null 2>&1 || true
  sleep 0.9
  agent-browser eval --session "$SESSION" "$COLLECT" >/dev/null 2>&1 || true
done

RESULT="$(agent-browser eval --session "$SESSION" '(() => {
  const byPos = Object.values(window.__rt || {}).sort((a,b) => a.ay - b.ay);
  const byClass = {};
  for (const e of byPos) (byClass[e.token] = byClass[e.token] || []).push(e.t);
  return JSON.stringify({ byClass });
})()' 2>/dev/null || echo '{"byClass":{}}')"

# agent-browser may wrap the eval result in quotes — unwrap to raw JSON.
python3 - "$OUT" "$RESULT" <<'PY'
import json, sys
out_path, raw = sys.argv[1], sys.argv[2]
raw = raw.strip()
# Unwrap a JSON-string-encoded payload if needed.
try:
    val = json.loads(raw)
    if isinstance(val, str):
        val = json.loads(val)
except Exception:
    val = {"byClass": {}}
if not isinstance(val, dict) or "byClass" not in val:
    val = {"byClass": {}}
with open(out_path, "w") as f:
    f.write(json.dumps(val, indent=2) + "\n")
n = sum(len(v) for v in val.get("byClass", {}).values())
print(f"✓ capture-runtime-text: {n} value(s) across {len(val['byClass'])} class(es) → {out_path}")
PY
