#!/usr/bin/env bash
# visual-judge-escape.sh — operator-triggered visual-judge dispatch for stuck
# fix-loops. Reads sections/result.txt, picks worst-N failing rows (uncached
# first), dispatches visual-judge.sh via the python dispatcher (cache + lock),
# and summarizes findings to stdout.
#
# Why this exists:
#   E1 (post_implement._check_bundle_grep_context_inject, commit 9eb7c3e)
#   injects free ref-source snippets after fail count >= 2. When that cheap
#   text grep still doesn't unstick the loop, the operator runs THIS to get
#   real multimodal LLM findings for the worst-AE sections. Cached results
#   are reused across iterations.
#
#   Auto-dispatch from goal.py or
#   post_implement.py is RISKY — this stays driver-territory, invoked by
#   a human (or a higher-level orchestrator) only when the fix loop has
#   genuinely stalled.
#
# Usage:
#   bash scripts/loop/visual-judge-escape.sh <ref-dir> [worst-N=3]
#
# Output: stdout JSON array, one entry per dispatched section:
#   [{"label": "...", "ae_per_mpx": N, "cache_hit": bool, "findings": {...}}]
# Exit codes:
#   0 — all dispatches succeeded (or cache hits)
#   1 — at least one dispatch failed (VisualJudgeError raised, partial output)
#   2 — setup error (missing ref-dir, missing result.txt, etc.)
set -uo pipefail

REF_DIR="${1:-}"
WORST_N="${2:-3}"

if [[ -z "$REF_DIR" ]]; then
  echo "usage: visual-judge-escape.sh <ref-dir> [worst-N=3]" >&2
  exit 2
fi
if [[ ! -d "$REF_DIR" ]]; then
  echo "visual-judge-escape: ref-dir not found: $REF_DIR" >&2
  exit 2
fi
if [[ ! -f "$REF_DIR/sections/result.txt" ]]; then
  echo "visual-judge-escape: $REF_DIR/sections/result.txt not found — run section-compare first" >&2
  exit 2
fi
if [[ ! -d "$REF_DIR/sections/ref" ]] || [[ ! -d "$REF_DIR/sections/impl" ]]; then
  echo "visual-judge-escape: $REF_DIR/sections/{ref,impl}/ missing — capture+compare first" >&2
  exit 2
fi

PLUGIN_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}}"

# Delegate selection + dispatch to a python one-shot. Keeping the
# selection logic in python lets it share parse helpers with
# post_implement._check_bundle_grep_context_inject (worst-N by AE/Mpx,
# include 🌑 saturated rows, prefer uncached) without re-implementing
# in bash.
exec python3 - "$REF_DIR" "$WORST_N" "$PLUGIN_ROOT" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
worst_n = int(sys.argv[2])
plugin_root = sys.argv[3]

# Ensure the dispatcher module is importable. Plugin root may be outside
# the python path under direct `bash` invocations.
sys.path.insert(0, plugin_root)
from ui_clone import visual_judge_dispatcher as vjd

# Reuse the parsing logic from post_implement so selection stays consistent.
from ui_clone.gates import post_implement as pi

result_text = (ref_dir / "sections" / "result.txt").read_text(
    encoding="utf-8", errors="replace"
)
failing = pi._parse_failing_section_rows(result_text)
if not failing:
    print("[]")
    sys.exit(0)

# Sort worst-first by AE/Mpx
failing.sort(key=lambda row: row[1], reverse=True)

# Prefer uncached: separate cached vs uncached, prepend uncached
ref_pngs = ref_dir / "sections" / "ref"
impl_pngs = ref_dir / "sections" / "impl"

uncached = []
cached = []
for label, ae in failing:
    ref_png = ref_pngs / f"{label}.png"
    impl_png = impl_pngs / f"{label}.png"
    if not ref_png.is_file() or not impl_png.is_file():
        continue
    if vjd.load_cached(ref_dir, label, ref_png, impl_png) is not None:
        cached.append((label, ae, ref_png, impl_png))
    else:
        uncached.append((label, ae, ref_png, impl_png))

selected = (uncached + cached)[:worst_n]

out = []
any_failure = False
for label, ae, ref_png, impl_png in selected:
    cache_hit = vjd.load_cached(ref_dir, label, ref_png, impl_png) is not None
    try:
        findings = vjd.dispatch_visual_judge(ref_dir, label, ref_png, impl_png)
        out.append({
            "label": label,
            "ae_per_mpx": ae,
            "cache_hit": cache_hit,
            "findings": findings,
        })
    except vjd.VisualJudgeError as exc:
        any_failure = True
        out.append({
            "label": label,
            "ae_per_mpx": ae,
            "cache_hit": cache_hit,
            "error": {
                "returncode": exc.returncode,
                "stderr": exc.stderr[:200],
            },
        })

print(json.dumps(out, ensure_ascii=False, indent=2))
sys.exit(1 if any_failure else 0)
PY
