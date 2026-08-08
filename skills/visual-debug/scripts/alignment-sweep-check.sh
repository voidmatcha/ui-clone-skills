#!/usr/bin/env bash
# alignment-sweep-check.sh — alignment invariant transfer at intermediate widths.
#
# Plan viewports have ref geometry (alignment-parity's matches.json data);
# intermediate widths have none. Sections/groups the ref keeps centered (or
# fixed-gutter) at EVERY enforced desktop viewport must keep that invariant
# at midpoint/breakpoint widths — pixel constants baked for one design width
# (the loop-9 footer carousel class) drift everywhere except the width they
# were authored for. The sweep is IMPL-ONLY: one browser session, viewport
# set per width, DOM rects via the shared enumerator, no screenshots.
#
# Blocking requires a violation at two ADJACENT sweep widths or at one
# enforced plan width (single mid-width wobbles are advisory).
#
# Usage: alignment-sweep-check.sh <session> <impl-url> <ref-dir>
#
# Env:
#   UI_CLONE_SWEEP_SAMPLES_FILE — pre-collected samples JSON ({width: rows});
#                                 skips the browser (test fixtures).
#
# Reads:
#   <ref-dir>/verification-plan.json        (plan viewports)
#   <ref-dir>/detected-breakpoints.json     (breakpoint widths)
#   <ref-dir>/sections/viewports/*/sections/matches.json  (ref classification)
#
# Writes:
#   <ref-dir>/alignment-sweep.json
#
# Exit: 0 pass/warn/skip, 1 fail, 2 setup error

set -euo pipefail

SESSION="${1:?Usage: alignment-sweep-check.sh <session> <impl-url> <ref-dir>}"
IMPL_URL="${2:?Usage: alignment-sweep-check.sh <session> <impl-url> <ref-dir>}"
REF_DIR="${3:?Usage: alignment-sweep-check.sh <session> <impl-url> <ref-dir>}"

[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPTS_DIR/../../.." && pwd)"
ENUM_JS="$SCRIPTS_DIR/lib/enumerate-sections.js"
[ -f "$ENUM_JS" ] || { echo "missing $ENUM_JS" >&2; exit 2; }

SAMPLES_FILE="${UI_CLONE_SWEEP_SAMPLES_FILE:-}"
TMP_SAMPLES=""
if [ -z "$SAMPLES_FILE" ]; then
  command -v agent-browser >/dev/null 2>&1 || {
    echo "agent-browser not found in PATH" >&2
    exit 2
  }
  TMP_SAMPLES="$(mktemp "${TMPDIR:-/tmp}/alignment-sweep-samples.XXXXXX")"
  trap 'rm -f "$TMP_SAMPLES"' EXIT
  SAMPLES_FILE="$TMP_SAMPLES"

  # Merge the IMPL's own @media boundaries into the sweep width set (batch-7
  # ITEM 3): a defect baked behind an @media the ref never had (a 1380-1500px
  # window) is invisible unless the sweep samples the impl's breakpoints too.
  IMPL_ROOT="$(bash "$REPO_ROOT/scripts/extract/find-impl-root.sh" "$REF_DIR" 2>/dev/null | head -1 || true)"
  if [ -n "${IMPL_ROOT:-}" ] && [ -d "$IMPL_ROOT" ]; then
    python3 - "$IMPL_ROOT" "$REF_DIR/impl-detected-breakpoints.json" <<'PY' || true
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
out = Path(sys.argv[2])
vals: set[str] = set()
for css in list(root.rglob("*.css")) + list(root.rglob("*.scss")):
    if "node_modules" in css.parts or "dist" in css.parts or ".next" in css.parts:
        continue
    try:
        text = css.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for m in re.findall(r"@media[^{}]*\((?:min|max)-width\s*:\s*([^)]+)\)", text):
        vals.add(m.strip())
out.write_text(json.dumps({
    "schemaVersion": 1,
    "breakpoints": sorted(vals),
    "source": "impl-css-media-scan",
}), encoding="utf-8")
PY
  fi

  WIDTH_ROWS="$(PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m ui_clone.alignment_sweep --emit-widths "$REF_DIR")"

  echo "{}" > "$SAMPLES_FILE"
  if [ -n "$WIDTH_ROWS" ]; then
    agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1
    agent-browser --session "$SESSION" wait 2500 >/dev/null 2>&1
    while read -r W H; do
      [ -n "$W" ] || continue
      echo "▸ sweep ${W}x${H}"
      agent-browser --session "$SESSION" set viewport "$W" "$H" >/dev/null 2>&1
      # full scroll cycle so scroll-latched reveals mount before measuring
      agent-browser --session "$SESSION" eval '(() => { document.documentElement.style.scrollBehavior = "auto"; window.scrollTo(0, document.documentElement.scrollHeight); return window.scrollY; })()' >/dev/null 2>&1
      agent-browser --session "$SESSION" wait 900 >/dev/null 2>&1
      agent-browser --session "$SESSION" eval '(() => { document.documentElement.style.scrollBehavior = "auto"; window.scrollTo(0, 0); return window.scrollY; })()' >/dev/null 2>&1
      agent-browser --session "$SESSION" wait 700 >/dev/null 2>&1
      RAW_FILE="$(mktemp "${TMPDIR:-/tmp}/alignment-sweep-raw.XXXXXX")"
      agent-browser --session "$SESSION" eval "$(cat "$ENUM_JS")" > "$RAW_FILE" 2>/dev/null || true
      python3 - "$SAMPLES_FILE" "$W" "$RAW_FILE" <<'PY' || true
import json
import sys

samples_path, width, raw_path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    value = open(raw_path, encoding="utf-8", errors="replace").read().strip()
except OSError:
    value = ""
for _ in range(3):  # agent-browser eval output may be double-JSON-encoded
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            break
    else:
        break
rows = [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []
samples = json.loads(open(samples_path, encoding="utf-8").read())
samples[width] = rows
open(samples_path, "w", encoding="utf-8").write(json.dumps(samples))
PY
      rm -f "$RAW_FILE"
    done <<< "$WIDTH_ROWS"
  fi
fi

set +e
PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m ui_clone.alignment_sweep "$REF_DIR" "$SAMPLES_FILE"
CODE=$?
set -e

if [ -f "$REF_DIR/alignment-sweep.json" ]; then
  python3 - "$REF_DIR/alignment-sweep.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    f"alignment-sweep: status={data.get('status')} "
    f"widths={data.get('sweptWidths')} "
    f"transferable={data.get('transferableCount')} "
    f"unclassifiable={len(data.get('unclassifiable') or [])}"
)
if data.get("diagnostic"):
    print("  " + str(data["diagnostic"]))
PY
fi

exit "$CODE"
