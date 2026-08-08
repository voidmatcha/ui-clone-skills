#!/usr/bin/env bash
# responsive-sweep.sh — deterministic Step 4-C2 multi-viewport sizing sweep.
#
# responsive-detection.md Step 4-C2 recovers original CSS expressions
# (calc()/vw/%/breakpoint-switch) that getComputedStyle flattens to resolved px.
# Today that sweep is agent diligence (hand-run browser evals); it gets skipped,
# so responsive/sizing-expressions.json stays the single-viewport finalizer
# sentinel and every clone renders desktop-frozen. This makes the sweep
# deterministic: it opens the ref at 768/1280/1440, samples the tracked layout
# elements' computed metrics at each width, and classifies each property into
# fixed-px / calc / vw / linear / breakpoint-jump / switched (see
# _responsive_classify.py), writing the real selector-keyed
# responsive/sizing-expressions.json the pre-generate gate requires.
#
# Usage: responsive-sweep.sh <ref_url> <ref_dir> [--session <name>]
#
# Output:
#   <ref_dir>/responsive/sizing-<vp>.json       (raw per-viewport samples)
#   <ref_dir>/responsive/sizing-expressions.json (bare selector-keyed map)
#   <ref_dir>/responsive/sizing-sweep.json        (provenance + type histogram)
#
# Exit: 0 on a clean run (sweep done, or gracefully skipped when agent-browser
# is unavailable / the URL will not open); 2 on setup error. It never writes a
# sentinel and never clobbers an existing real sweep with an empty result.

set -uo pipefail

# W-4 (loop-ebpb-0): pin the light color scheme at CAPTURE time too — a
# dark-evening Phase-0 capture bakes dark styles into the ref corpus
# PERMANENTLY, and every light-pinned verify then honestly-fails against
# poisoned ground truth. Caller override intact (default only when unset).
: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME

REF_URL="${1:-}"
REF_DIR="${2:-}"
SESSION=""
shift 2 2>/dev/null || true
while [ "$#" -gt 0 ]; do
  case "$1" in
    --session) SESSION="${2:-}"; shift 2 ;;
    --session=*) SESSION="${1#--session=}"; shift ;;
    *) shift ;;
  esac
done

if [ -z "$REF_URL" ] || [ -z "$REF_DIR" ]; then
  echo "Usage: responsive-sweep.sh <ref_url> <ref_dir> [--session <name>]" >&2
  exit 2
fi
if [ ! -d "$REF_DIR" ]; then
  echo "ERROR: ref-dir not found: $REF_DIR" >&2
  exit 2
fi
if [ -z "$SESSION" ]; then
  SESSION="$(basename "$REF_DIR")-rsweep"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

python3 - "$REF_URL" "$REF_DIR" "$SESSION" "$SCRIPT_DIR" <<'PY'
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ref_url = sys.argv[1]
ref_dir = Path(sys.argv[2])
session = sys.argv[3]
script_dir = sys.argv[4]

sys.path.insert(0, script_dir)
import _responsive_classify as rc  # noqa: E402

VIEWPORTS = rc.VIEWPORTS
resp_dir = ref_dir / "responsive"
resp_dir.mkdir(parents=True, exist_ok=True)

# The measurement IIFE (responsive-detection.md Step 4-C2 selector set, plus the
# computed display/position the classifier uses to detect breakpoint switches).
MEASURE_JS = r"""
(() => {
  const selectors = [
    'body', 'main', 'header', 'footer', 'nav',
    ...Array.from(document.querySelectorAll('section')).map((el, i) => {
      const cn = typeof el.className === 'string' ? el.className : '';
      const first = cn.trim().split(/\s+/)[0];
      return el.id ? '#' + el.id : 'section' + (first ? '.' + first : ':nth-of-type(' + (i + 1) + ')');
    }),
    '.container', '.wrapper', '.hero', '.grid', '.inner',
    '[class*=container]', '[class*=wrapper]', '[class*=inner]',
  ];
  const results = {};
  const seen = new Set();
  selectors.forEach((sel) => {
    if (seen.has(sel)) return;
    seen.add(sel);
    let el = null;
    try { el = document.querySelector(sel); } catch (e) { return; }
    if (!el) return;
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    results[sel] = {
      width: r.width,
      height: r.height,
      paddingLeft: parseFloat(s.paddingLeft) || 0,
      paddingRight: parseFloat(s.paddingRight) || 0,
      fontSize: parseFloat(s.fontSize) || null,
      display: s.display,
      position: s.position,
      maxWidth: s.maxWidth,
      minWidth: s.minWidth,
      marginLeft: s.marginLeft,
      marginRight: s.marginRight,
      gap: s.gap,
      lineHeight: s.lineHeight,
    };
  });
  return JSON.stringify({ viewport: window.innerWidth, elements: results });
})()
"""

TIMEOUT = float(os.environ.get("UI_CLONE_SWEEP_TIMEOUT", "20"))


def run_ab(args: list[str], timeout: float) -> dict[str, Any]:
    if shutil.which("agent-browser") is None:
        return {"status": "skipped", "reason": "agent-browser not found"}
    try:
        proc = subprocess.run(
            ["agent-browser", "--session", session, *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    return {
        "status": "pass" if proc.returncode == 0 else "fail",
        "stdout": proc.stdout,
        "stderr": proc.stderr[-300:],
        "returncode": proc.returncode,
    }


def unwrap(raw: str) -> Any:
    """Unwrap agent-browser's {data:{result}} / {result} envelope and the
    double-JSON string the agent-browser eval subcommand returns."""
    value: Any = json.loads(raw)
    for _ in range(3):
        if isinstance(value, dict) and isinstance(value.get("data"), dict) and "result" in value["data"]:
            value = value["data"]["result"]
        elif isinstance(value, dict) and "result" in value:
            value = value["result"]
        else:
            break
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith(("{", "[")):
                value = json.loads(stripped)
    return value


def skip(reason: str) -> None:
    print(f"responsive-sweep: SKIP — {reason}")
    sys.exit(0)


if shutil.which("agent-browser") is None:
    skip("agent-browser not found (sweep needs a live browser)")

open_res = run_ab(["open", ref_url], min(TIMEOUT, 15))
if open_res.get("status") != "pass":
    skip(f"could not open {ref_url} ({open_res.get('status')})")
time.sleep(1.5)  # 'open --wait' is not supported; settle explicitly.

per_viewport: dict[int, dict[str, Any]] = {}
for vp in VIEWPORTS:
    # 'open' must precede 'set viewport'; a viewport set before open is dropped.
    vp_res = run_ab(["set", "viewport", str(vp), "900"], min(TIMEOUT, 10))
    if vp_res.get("status") != "pass":
        continue
    time.sleep(0.4)  # let @media rules + reflow settle at the new width.
    eval_res = run_ab(["eval", "--json", MEASURE_JS], TIMEOUT)
    if eval_res.get("status") != "pass":
        continue
    try:
        payload = unwrap(eval_res.get("stdout") or "")
    except (ValueError, TypeError):
        continue
    elements = payload.get("elements") if isinstance(payload, dict) else None
    measured_vp = payload.get("viewport") if isinstance(payload, dict) else None
    if not isinstance(elements, dict) or not elements:
        continue
    per_viewport[int(vp)] = elements
    (resp_dir / f"sizing-{vp}.json").write_text(
        json.dumps({"viewport": measured_vp or vp, "elements": elements}, indent=2) + "\n",
        encoding="utf-8",
    )

if len(per_viewport) < 2:
    skip(f"only {len(per_viewport)} viewport(s) measured — need >=2 to derive expressions")

expressions = rc.build_expressions(per_viewport)
resp_path, meta_path = rc.write_outputs(
    ref_dir, expressions, VIEWPORTS, sorted(per_viewport.keys())
)
hist = rc.type_histogram(expressions)
hist_str = ", ".join(f"{t}={n}" for t, n in hist.items() if n)
print(
    f"responsive-sweep: {len(expressions)} selectors, "
    f"{sum(hist.values())} expressions ({hist_str or 'none'}) "
    f"from {len(per_viewport)} viewport(s) → {resp_path}"
)
sys.exit(0)
PY
