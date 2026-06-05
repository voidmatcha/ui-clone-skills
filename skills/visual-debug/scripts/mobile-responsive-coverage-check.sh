#!/usr/bin/env bash
# mobile-responsive-coverage-check.sh — static, advisory (warn) check that the
# impl carries responsive CSS proportional to a responsive reference. The
# browser-probe mobile-viewport-parity gate only verifies "no horizontal
# overflow + renders", so a generic rebuild that ships the desktop layout with
# almost no media queries / fluid sizing passes it while looking broken on
# mobile. This surfaces that gap (non-blocking) by comparing responsive-signal
# density, ref vs impl.
#
# Usage: mobile-responsive-coverage-check.sh <ref-dir> [<impl-src-dir>]
# Output: <ref-dir>/mobile-responsive-coverage.json
# Exit: 0 pass/skip, 1 under-responsive (advisory), 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_SRC_DIR="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: mobile-responsive-coverage-check.sh <ref-dir> [<impl-src-dir>]" >&2
  exit 2
fi

if [ -z "$IMPL_SRC_DIR" ]; then
  PLUGIN_ROOT_CAND="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
  for cand_root in "$PLUGIN_ROOT_CAND" "$(cd "$(dirname "$0")/../../.." && pwd)"; do
    [ -z "$cand_root" ] && continue
    RESOLVER="$cand_root/scripts/extract/find-impl-root.sh"
    if [ -f "$RESOLVER" ]; then
      IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
      if [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT/src" ]; then
        IMPL_SRC_DIR="$IMPL_ROOT/src"
        break
      fi
    fi
  done
fi

OUT_PATH="$REF_DIR/mobile-responsive-coverage.json"

python3 - "$REF_DIR" "$IMPL_SRC_DIR" "$OUT_PATH" <<'PY'
import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_src = sys.argv[2]
out_path = Path(sys.argv[3])

_MEDIA = re.compile(r"@media")
_CLAMP = re.compile(r"clamp\(")
_VW = re.compile(r"\d+vw\b")
_UTIL = re.compile(r"\b(?:sm|md|lg|xl|2xl):")


def write(obj: dict, code: int) -> None:
    out_path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    print(f"mobile-responsive-coverage: {obj['status']}")
    sys.exit(code)


def signals(text: str, *, utilities: bool) -> int:
    n = len(_MEDIA.findall(text)) + len(_CLAMP.findall(text)) + len(_VW.findall(text))
    if utilities:
        n += len(_UTIL.findall(text))
    return n


# Reference responsive density: ref CSS chunks + styles.json.
ref_text_parts = []
css_dir = ref_dir / "css"
if css_dir.is_dir():
    for p in css_dir.glob("*.css"):
        try:
            ref_text_parts.append(p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
sj = ref_dir / "styles.json"
if sj.is_file():
    try:
        ref_text_parts.append(sj.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        pass
ref_text = "\n".join(ref_text_parts)
ref_signals = signals(ref_text, utilities=False)

# Is the ref actually responsive? Require either detected breakpoints or a
# meaningful responsive density — otherwise this check does not apply.
bp = ref_dir / "detected-breakpoints.json"
bp_count = 0
if bp.is_file():
    try:
        d = json.loads(bp.read_text(encoding="utf-8"))
        bp_count = len(d) if hasattr(d, "__len__") else 0
    except (OSError, json.JSONDecodeError):
        bp_count = 0
ref_responsive = bp_count >= 2 or ref_signals >= 8
if not ref_responsive:
    write({"schemaVersion": 1, "status": "skip",
           "reason": "ref is not responsive (no breakpoints / low density) — check N/A",
           "refSignals": ref_signals, "refBreakpoints": bp_count}, 0)

if not impl_src or not Path(impl_src).is_dir():
    write({"schemaVersion": 1, "status": "skip",
           "reason": "impl/src not found"}, 0)

impl_parts = []
for p in Path(impl_src).rglob("*"):
    if p.is_file() and p.suffix.lower() in {".css", ".scss", ".sass", ".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte"}:
        try:
            impl_parts.append(p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
impl_signals = signals("\n".join(impl_parts), utilities=True)

# Generous proportional floor — impl needs only ~10% of the ref's responsive
# density (min 3). A forensic-CSS clone clears this easily; a generic rebuild
# that ships ~no media queries / fluid sizing does not.
floor = max(3, int(ref_signals * 0.1))
status = "fail" if impl_signals < floor else "pass"
write({
    "schemaVersion": 1,
    "status": status,
    "refSignals": ref_signals,
    "refBreakpoints": bp_count,
    "implSignals": impl_signals,
    "floor": floor,
    "reason": (
        f"impl responsive density {impl_signals} < floor {floor} "
        f"(ref density {ref_signals}); the impl likely renders a fixed desktop "
        "layout at mobile widths — add media queries / fluid sizing / responsive "
        "utilities, or preserve the ref's responsive CSS."
    ) if status == "fail" else "impl carries responsive CSS proportional to ref",
}, 0 if status == "pass" else 1)
PY
