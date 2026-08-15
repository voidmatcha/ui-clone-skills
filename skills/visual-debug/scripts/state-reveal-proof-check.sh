#!/usr/bin/env bash
# state-reveal-proof-check.sh — active-state reveal end-state proof.
#
# Loop-10/11: scrolling swaps the nav pill's active state but the newly-active
# button's label never reveals (container baked width:0). The hover-fallback
# gate only covers HOVER reveals; this drives the ACTIVE-state change (scrolling
# the page so each section becomes active) and asserts the bundle-declared reveal
# (width 0 -> auto on the active flag, from bundle-extraction activeStateExpansions)
# actually occurs: the revealed element must expand past the collapsed width at
# least once across the sweep. A faithful impl reveals the active label; the
# loop-11 impl keeps every label at width:0.
#
# Usage: state-reveal-proof-check.sh <session> <impl-url> <ref-dir>
#
# Env:
#   UI_CLONE_STATE_REVEAL_OBSERVED_FILE — pre-collected {selector: maxWidth};
#                                         skips the browser (test fixtures).
#   UI_CLONE_STATE_REVEAL_COLLAPSED_PX  — collapsed-width threshold (default 4).
#
# Writes:
#   <ref-dir>/state-reveal.json
#
# Exit: 0 pass/skip/warn, 1 fail, 2 setup error

set -euo pipefail

SESSION="${1:?Usage: state-reveal-proof-check.sh <session> <impl-url> <ref-dir>}"
IMPL_URL="${2:?Usage: state-reveal-proof-check.sh <session> <impl-url> <ref-dir>}"
REF_DIR="${3:?Usage: state-reveal-proof-check.sh <session> <impl-url> <ref-dir>}"

[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPTS_DIR/../../.." && pwd)"
PY() { PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 "$@"; }

OBSERVED_FILE="${UI_CLONE_STATE_REVEAL_OBSERVED_FILE:-}"
# batch-9 ITEM 3: provenance — 1 only when a LIVE browser state-sweep actually
# ran below (not a pre-collected observed-file). The verdict marks
# runtimeScanned=true only when this flag AND a written receipt both hold.
RUNTIME_SCANNED=0
if [ -z "$OBSERVED_FILE" ]; then
  command -v agent-browser >/dev/null 2>&1 || { echo "agent-browser not found in PATH" >&2; exit 2; }
  PLAN_JSON="$(PY -m ui_clone.gates.state_reveal plan "$REF_DIR")"
  SEL_COUNT="$(printf '%s' "$PLAN_JSON" | python3 -c "import json,sys;print(len(json.load(sys.stdin).get('selectors') or []))")"
  OBSERVED_FILE="$(mktemp "${TMPDIR:-/tmp}/state-reveal-obs.XXXXXX")"
  trap 'rm -f "$OBSERVED_FILE"' EXIT
  echo "[]" > "$OBSERVED_FILE"

  if [ "${SEL_COUNT:-0}" -gt 0 ]; then
    agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1
    agent-browser --session "$SESSION" wait 2500 >/dev/null 2>&1
    # L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
    JS_FILE="$(mktemp "${TMPDIR:-/tmp}/state-reveal-probe.XXXXXX")"
    mv "$JS_FILE" "${JS_FILE}.js"
    JS_FILE="${JS_FILE}.js"
    PY - "$PLAN_JSON" > "$JS_FILE" <<'PY'
import json
import sys

plan = json.loads(sys.argv[1])
selectors = json.dumps(plan.get("selectors") or [])
samples = int(plan.get("scrollSamples") or 8)

# NOTE: no backslash regexes — agent-browser eval applies one unescape pass.
# Paint + on-screen fields (colorAlpha/opacity/fontSizePx/onScreen) let the
# verdict reject an empty pill (color:transparent / font-size:0) and an
# off-screen decoy that geometry alone would pass. parseAlpha comes from the
# injected visible-identity lib (no-regex string parsing).
print(
    """(async () => {
  const sels = %s;
  const samples = %d;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const maxScroll = () => Math.max(document.documentElement.scrollHeight - window.innerHeight, 0);
  // The label's nav button/item carries the active flag (data-active / aria-current
  // / an "active" class). Only the ACTIVE button's label is expected to reveal.
  const isActive = (el) => {
    let n = el;
    for (let d = 0; d < 5 && n && n.getAttribute; d++, n = n.parentElement) {
      const a = (n.getAttribute("data-active") || n.getAttribute("aria-current") || "").toLowerCase();
      if (a === "true" || a === "page" || a === "section" || a === "step") return true;
      const cls = String(n.className || "");
      if (/(?:^|[ _-])active(?:$|[ _-])/i.test(cls)) return true;
    }
    return false;
  };
  const out = [];
  for (let i = 0; i <= samples; i++) {
    window.scrollTo({top: (i / samples) * maxScroll(), behavior: "instant"});
    await wait(450);
    const pct = Math.round((100 * i) / samples);
    const vpW = document.documentElement.clientWidth;
    const vpH = document.documentElement.clientHeight;
    sels.forEach(s => {
      let els = [];
      try { els = Array.from(document.querySelectorAll(s)); } catch (e) { els = []; }
      els.forEach(el => {
        if (!isActive(el)) return;
        const r = el.getBoundingClientRect();
        // Full pixel-truth record via the shared collector: color +
        // effectiveBgColor (contrast / white-on-white), checkVisibility, clip /
        // filter / ancestor-clip / hit-test (batch-7 ITEM 1), merged with the
        // reveal-specific box/content/pct fields. Width belongs to the declared
        // reveal container, while paint truth belongs to its actual text-bearing
        // descendant when CSS overrides inherited colour on that child.
        const base = __visibleIdentity.describeTextPaint(el, s, 0, [], vpW, vpH);
        const paintRect = base.rect || {};
        const paintOnScreen = (
          Number(paintRect.left || 0) + Number(paintRect.width || 0) > 0 &&
          Number(paintRect.left || 0) < vpW &&
          Number(paintRect.top || 0) + Number(paintRect.height || 0) > 0 &&
          Number(paintRect.top || 0) < vpH
        );
        out.push(Object.assign(base, {
          pct: pct,
          text: String(el.innerText || "").split(String.fromCharCode(10)).join(" ").trim().slice(0, 40),
          box: r.width,
          content: el.scrollWidth,
          onScreen: (
            r.right > 0 && r.left < vpW && r.bottom > 0 && r.top < vpH &&
            paintOnScreen
          ),
        }));
      });
    });
  }
  return JSON.stringify(out);
})()""" % (selectors, samples)
)
PY
    # L-MEA-13 class: macOS mktemp needs TRAILING Xs — create then rename.
    PROBE_FILE="$(mktemp "${TMPDIR:-/tmp}/state-reveal-combined.XXXXXX")"
    mv "$PROBE_FILE" "${PROBE_FILE}.js"
    PROBE_FILE="${PROBE_FILE}.js"
    { cat "$SCRIPTS_DIR/lib/visible-identity.js"; printf ';\n'; cat "$JS_FILE"; } > "$PROBE_FILE"
    rm -f "$JS_FILE"
    RAW_FILE="$(mktemp "${TMPDIR:-/tmp}/state-reveal-raw.XXXXXX")"
    agent-browser --session "$SESSION" eval "$(cat "$PROBE_FILE")" > "$RAW_FILE" 2>/dev/null || true
    rm -f "$PROBE_FILE"
    RUNTIME_SCANNED=1
    PY - "$RAW_FILE" "$OBSERVED_FILE" <<'PY'
import json
import sys

raw_path, obs_path = sys.argv[1], sys.argv[2]
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
obs = [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []
open(obs_path, "w", encoding="utf-8").write(json.dumps(obs))
PY
    rm -f "$RAW_FILE"
  fi
fi

# Provenance receipt (batch-9 ITEM 3, mirror hover-fallback): when a live scan
# actually ran, drop a receipt INSIDE the impl tree. The verdict marks
# runtimeScanned=true only when the env flag AND this receipt file both exist,
# and the consumer binds the receipt to impl_root + mtime (PATH_CHECK) — so a
# hand-authored observed-file (or a self-attested env flag with no browser) can
# no longer mint a pass.
SCAN_RECEIPT=""
if [ "${RUNTIME_SCANNED:-0}" = "1" ]; then
  IMPL_ROOT="$(bash "$REPO_ROOT/scripts/extract/find-impl-root.sh" "$REF_DIR" 2>/dev/null | head -1 || true)"
  if [ -n "${IMPL_ROOT:-}" ] && [ -d "$IMPL_ROOT" ]; then
    SCAN_RECEIPT="$IMPL_ROOT/.state-reveal-scan-receipt.json"
    python3 - "$SCAN_RECEIPT" "$IMPL_ROOT" "$IMPL_URL" <<'PY' || true
import json
import sys
import time

receipt, impl_root, impl_url = sys.argv[1], sys.argv[2], sys.argv[3]
with open(receipt, "w", encoding="utf-8") as fh:
    json.dump({
        "schemaVersion": 1,
        "scannedAt": int(time.time()),
        "implRoot": impl_root,
        "implUrl": impl_url,
        "by": "state-reveal-proof-check.sh",
    }, fh)
PY
  fi
fi

set +e
UI_CLONE_STATE_REVEAL_RUNTIME_SCANNED="$RUNTIME_SCANNED" \
UI_CLONE_STATE_REVEAL_SCAN_RECEIPT="$SCAN_RECEIPT" \
PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m ui_clone.gates.state_reveal verdict "$REF_DIR" "$OBSERVED_FILE"
CODE=$?
set -e

if [ -f "$REF_DIR/state-reveal.json" ]; then
  python3 - "$REF_DIR/state-reveal.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"state-reveal: status={data.get('status')}")
for row in data.get("rows") or []:
    print(f"  {row.get('selector')} [{row.get('activeText')}] @{row.get('pct')}%: "
          f"box={row.get('boxPx')}px content={row.get('contentPx')}px "
          f"ratio={row.get('revealRatio')} -> {row.get('status')}")
    if row.get("reason"):
        print(f"    - {row['reason']}")
for u in data.get("unmeasured") or []:
    print(f"  unmeasured {u.get('selector')}: {u.get('reason')}")
PY
fi

exit "$CODE"
