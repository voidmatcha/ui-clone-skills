#!/usr/bin/env bash
# masked-region-motion-proof-check.sh — live motion proof for dynamic-masked
# timer/carousel regions.
#
# Dynamic:true spec entries are masked out of pixel comparison because their
# frame is timer-phase-dependent — which previously left them with NO
# compensating verification (specific regression: carousel timer ran, content swapped
# instantly, the spec-declared card motion never happened, every gate
# passed). This check samples the LIVE impl DOM per entry (~250ms cadence
# for >=1.5x the declared interval, retrying to ~2.2x when no change is
# seen — unlucky phase) and hands the samples to
# ui_clone.gates.masked_region_motion for phase-free verdicts:
# state-count, change cadence, declared-channel coverage, and bundle item
# sequence. Ref truth comes from spec params/bundle evidence only.
#
# Usage: masked-region-motion-proof-check.sh <session> <impl-url> <ref-dir>
#
# Env:
#   UI_CLONE_MRM_SAMPLES_FILE — pre-collected samples ({entryId: samples[]});
#                               skips the browser (test fixtures).
#
# Writes:
#   <ref-dir>/masked-region-motion.json
#
# Exit: 0 pass/skip, 1 fail, 2 setup error

set -euo pipefail

SESSION="${1:?Usage: masked-region-motion-proof-check.sh <session> <impl-url> <ref-dir>}"
IMPL_URL="${2:?Usage: masked-region-motion-proof-check.sh <session> <impl-url> <ref-dir>}"
REF_DIR="${3:?Usage: masked-region-motion-proof-check.sh <session> <impl-url> <ref-dir>}"

[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPTS_DIR/../../.." && pwd)"

SAMPLES_FILE="${UI_CLONE_MRM_SAMPLES_FILE:-}"
if [ -z "$SAMPLES_FILE" ]; then
  command -v agent-browser >/dev/null 2>&1 || {
    echo "agent-browser not found in PATH" >&2
    exit 2
  }
  PLAN_JSON="$(PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m ui_clone.gates.masked_region_motion plan "$REF_DIR")"
  SAMPLES_FILE="$(mktemp "${TMPDIR:-/tmp}/mrm-samples.XXXXXX")"
  trap 'rm -f "$SAMPLES_FILE"' EXIT
  echo "{}" > "$SAMPLES_FILE"

  ENTRY_COUNT="$(printf '%s' "$PLAN_JSON" | python3 -c "import json,sys;print(len(json.load(sys.stdin)))")"
  if [ "$ENTRY_COUNT" -gt 0 ]; then
    agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1
    agent-browser --session "$SESSION" wait 2500 >/dev/null 2>&1

    for IDX in $(seq 0 $((ENTRY_COUNT - 1))); do
      ENTRY="$(printf '%s' "$PLAN_JSON" | python3 -c "import json,sys;print(json.dumps(json.load(sys.stdin)[$IDX]))")"
      EID="$(printf '%s' "$ENTRY" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")"
      echo "▸ sampling $EID"

      for WINDOW_KEY in windowMs retryWindowMs; do
        WINDOW="$(printf '%s' "$ENTRY" | python3 -c "import json,sys;print(json.load(sys.stdin)['$WINDOW_KEY'])")"
        # L-MEA-13: macOS mktemp requires the Xs to be TRAILING — a .js suffix in
        # the template aborts the whole check. Create then rename.
        JS_FILE="$(mktemp "${TMPDIR:-/tmp}/mrm-sampler.XXXXXX")"
        mv "$JS_FILE" "${JS_FILE}.js"
        JS_FILE="${JS_FILE}.js"
        python3 - "$ENTRY" "$WINDOW" > "$JS_FILE" <<'PY'
import json
import sys

entry = json.loads(sys.argv[1])
window_ms = int(sys.argv[2])
selectors = json.dumps(entry.get("selectors") or [])
cadence = int(entry.get("cadenceMs") or 250)

# NOTE: no backslash regexes here — agent-browser eval applies one unescape
# pass; plain string ops keep the sampler escaping-proof.
print(
    """(async () => {
  const sels = %s;
  const cadence = %d;
  const windowMs = %d;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const firstEl = sels.map(s => document.querySelector(s)).find(Boolean);
  if (firstEl) { firstEl.scrollIntoView({block: "center"}); }
  await wait(600);
  const base = (src) => {
    const noQuery = String(src || "").split("?")[0].split("#")[0];
    const parts = noQuery.split("/");
    return parts[parts.length - 1];
  };
  // Visibility filter (review-2 finding 4): only PAINTED nodes feed the
  // digest — a hidden counter or off-screen transform mutation must not
  // satisfy the motion proof. Painted = nonzero rect, not display:none /
  // visibility:hidden / opacity 0, and intersecting the viewport band.
  const isPainted = (node) => {
    const r = node.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    if (r.bottom < -window.innerHeight || r.top > window.innerHeight * 2) return false;
    if (r.right < -window.innerWidth || r.left > window.innerWidth * 2) return false;
    const s = getComputedStyle(node);
    if (s.display === "none" || s.visibility === "hidden") return false;
    if (parseFloat(s.opacity || "1") === 0) return false;
    return true;
  };
  const snap = () => {
    const imgs = [];
    const texts = [];
    const cards = [];
    sels.forEach(s => Array.from(document.querySelectorAll(s)).forEach(el => {
      if (el.tagName.toLowerCase() === "img" && isPainted(el)) { imgs.push(base(el.currentSrc || el.src)); }
      Array.from(el.querySelectorAll("img")).forEach(im => { if (isPainted(im)) imgs.push(base(im.currentSrc || im.src)); });
      // innerText is render-aware (display:none / visibility:hidden text is
      // excluded by the engine) — but only when the TARGET itself paints.
      if (isPainted(el)) {
        texts.push(String(el.innerText || "").split(String.fromCharCode(10)).join(" ").trim());
      }
      const nodes = el.children.length ? Array.from(el.children) : [el];
      nodes.forEach(c => {
        if (!isPainted(c)) return;
        const cs = getComputedStyle(c);
        cards.push(cs.transform + "|" + cs.zIndex + "|" + cs.opacity);
      });
    }));
    return { t: Math.round(performance.now()), imgSrcs: imgs, text: texts.join(" :: "), cards: cards };
  };
  const samples = [];
  const start = performance.now();
  while (performance.now() - start < windowMs) {
    samples.push(snap());
    await wait(cadence);
  }
  samples.push(snap());
  return JSON.stringify(samples);
})()""" % (selectors, cadence, window_ms)
)
PY
        RAW_FILE="$(mktemp "${TMPDIR:-/tmp}/mrm-raw.XXXXXX")"
        agent-browser --session "$SESSION" eval "$(cat "$JS_FILE")" > "$RAW_FILE" 2>/dev/null || true
        rm -f "$JS_FILE"
        CHANGED="$(python3 - "$SAMPLES_FILE" "$EID" "$RAW_FILE" <<'PY'
import json
import sys

samples_path, eid, raw_path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    value = open(raw_path, encoding="utf-8", errors="replace").read().strip()
except OSError:
    value = ""
for _ in range(3):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            break
    else:
        break
rows = [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []
samples = json.loads(open(samples_path, encoding="utf-8").read())
samples[eid] = rows
open(samples_path, "w", encoding="utf-8").write(json.dumps(samples))
digests = [json.dumps({k: r.get(k) for k in ("imgSrcs", "text", "cards")}, sort_keys=True) for r in rows]
print("changed" if len(set(digests)) >= 2 else "static")
PY
)"
        rm -f "$RAW_FILE"
        # retry with the longer window only when the first pass saw no change
        # (unlucky timer phase); a changing region is already evidence.
        [ "$CHANGED" = "static" ] || break
      done
    done
  fi
fi

set +e
PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m ui_clone.gates.masked_region_motion verdict "$REF_DIR" "$SAMPLES_FILE"
CODE=$?
set -e

if [ -f "$REF_DIR/masked-region-motion.json" ]; then
  python3 - "$REF_DIR/masked-region-motion.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"masked-region-motion: status={data.get('status')}")
for entry in data.get("entries") or []:
    print(
        f"  {entry.get('id')}: {entry.get('status')} "
        f"states={entry.get('distinctStates')} "
        f"changed={entry.get('changedChannels')}"
    )
    for reason in entry.get("reasons") or []:
        print(f"    - {reason}")
PY
fi

exit "$CODE"
