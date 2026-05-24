#!/usr/bin/env bash
# class-signature-css-coverage-check.sh — fail when impl preserves
# class-name signatures but doesn't define styles for them.
#
# Failure mode this catches (L64 root cause):
#   class-signature-preservation gate passes at 95%+ because the impl
#   JSX/TSX includes all the ref's distinctive class signatures
#   (opaque-hashed CSS-Modules names like `componentName__hashSuffix`).
#   But the impl's CSS file has zero rules for any of those classes —
#   the components just emit className="..." with no matching style.
#   The page renders as an unstyled layout (often just the background
#   color of the transition-signal anchor section, looking like a
#   single-color visual fail). Class-signature gate sees this as PASS,
#   but the visual is broken.
#
#   Decouple: class-signature gate verifies NAME presence. This new
#   gate verifies STYLE presence for those names.
#
# What this check does:
#   1. Load <ref-dir>/class-signature-preservation.json (Step E artifact).
#      If absent or status=skip, this check also skips.
#   2. Take the set of preserved signatures (ref ∩ impl).
#   3. For each preserved signature, search impl's CSS files (any
#      *.css / *.scss / *.module.css under impl/) for a rule starting
#      with `.<signature>` or `[class~="<signature>"]` (Tailwind-arbitrary)
#      or attribute selector targeting that class.
#   4. Compute style_coverage = (signatures with CSS rule) / preserved.
#   5. Status:
#         - skip   when preserved < 5  (low signal floor)
#         - pass   when style_coverage ≥ 0.30
#         - fail   when style_coverage <  0.30
#
# Why 0.30 threshold:
#   Same as class-signature-preservation — be lenient enough that
#   utility-class-first impls (which legitimately don't need a CSS
#   rule per signature) pass, but strict enough that "named but
#   unstyled" patterns fail.
#
# Universal anti-cheat — runs whenever class-signature-preservation
# fired (impl had >= 5 preserved signatures).
#
# Output: <ref-dir>/class-signature-css-coverage.json
# Exit: 0 pass/skip, 1 fail, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ARG="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: class-signature-css-coverage-check.sh <ref-dir> [<impl-root>]" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/class-signature-css-coverage.json"

IMPL_ROOT="$IMPL_ARG"
if [ -z "$IMPL_ROOT" ]; then
  PLUGIN_ROOT_CAND="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
  for cand_root in "$PLUGIN_ROOT_CAND" "$(cd "$(dirname "$0")/../../.." && pwd)"; do
    [ -z "$cand_root" ] && continue
    RESOLVER="$cand_root/scripts/extract/find-impl-root.sh"
    if [ -f "$RESOLVER" ]; then
      IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
      [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT" ] && break
    fi
  done
fi

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  python3 - "$OUT_PATH" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "schemaVersion": 1,
    "status": "skip",
    "reason": "impl_root not found",
    "preservedCount": 0,
    "styledCount": 0,
    "styleCoverage": 0.0,
    "threshold": 0.30,
    "unstyledSample": [],
}, indent=2), encoding="utf-8")
PY
  echo "class-signature-css-coverage: skip (no impl)"
  exit 0
fi

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT_PATH" <<'PY'
import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_root = Path(sys.argv[2])
out_path = Path(sys.argv[3])

# ── Load class-signature-preservation artifact ──
sig_artifact_path = ref_dir / "class-signature-preservation.json"
if not sig_artifact_path.is_file():
    out_path.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
        "reason": "class-signature-preservation.json not found — run that gate first",
        "preservedCount": 0,
        "styledCount": 0,
        "styleCoverage": 0.0,
        "threshold": 0.30,
        "unstyledSample": [],
    }, indent=2), encoding="utf-8")
    print("class-signature-css-coverage: skip (prerequisite artifact missing)")
    sys.exit(0)

try:
    sig_data = json.loads(sig_artifact_path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"class-signature-css-coverage: parse failed: {exc}", file=sys.stderr)
    sys.exit(2)

# The class-signature gate doesn't currently emit the preserved set.
# Recompute it here from html + impl using the same harvest logic.
# Cheap (the gate just ran).
SIG_DOUBLE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*__[A-Za-z0-9_-]{4,8})\b")
SIG_SINGLE = re.compile(r"\b([A-Za-z]{2,}_[A-Za-z0-9]{4,8})\b")
SINGLE_BLOCKLIST = {
    "data_v", "px_to", "rem_to", "min_w", "max_w", "min_h", "max_h",
    "var_", "calc_", "url_",
}


def harvest(text: str) -> set[str]:
    sigs: set[str] = set()
    for m in SIG_DOUBLE.finditer(text):
        sigs.add(m.group(1))
    for m in SIG_SINGLE.finditer(text):
        tok = m.group(1)
        if any(tok.lower().startswith(b) for b in SINGLE_BLOCKLIST):
            continue
        prefix, _, suffix = tok.partition("_")
        if not any(c.isdigit() for c in suffix) and suffix.islower():
            continue
        sigs.add(tok)
    return sigs


ref_sigs: set[str] = set()
html_dir = ref_dir / "html"
if html_dir.is_dir():
    for path in sorted(html_dir.glob("*.json")):
        try:
            ref_sigs |= harvest(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
scaffold = ref_dir / "dom-scaffold.json"
if scaffold.is_file():
    try:
        ref_sigs |= harvest(scaffold.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        pass

impl_sigs: set[str] = set()
SRC_EXTS = {".tsx", ".ts", ".jsx", ".js", ".css", ".scss", ".sass", ".module.css"}
SKIP_PARTS = {"node_modules", ".git", ".next", "dist", "build", ".turbo", "coverage"}
for path in impl_root.rglob("*"):
    if not path.is_file():
        continue
    if any(p in SKIP_PARTS for p in path.parts):
        continue
    suffix = "".join(path.suffixes[-2:]) if path.name.endswith(".module.css") else path.suffix
    if suffix.lower() not in SRC_EXTS:
        continue
    try:
        impl_sigs |= harvest(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        continue

preserved = ref_sigs & impl_sigs
preserved_count = len(preserved)

# ── Floor: skip if preserved set is too small to evaluate ──
if preserved_count < 5:
    out_path.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
        "reason": f"preserved set too small ({preserved_count} < 5) — class-signature gate already handled this",
        "preservedCount": preserved_count,
        "styledCount": 0,
        "styleCoverage": 0.0,
        "threshold": 0.30,
        "unstyledSample": [],
    }, indent=2), encoding="utf-8")
    print(f"class-signature-css-coverage: skip (preserved={preserved_count} < 5)")
    sys.exit(0)

# ── Scan impl CSS for rules matching each preserved signature ──
CSS_EXTS = {".css", ".scss", ".sass"}
css_text_chunks: list[str] = []
for path in impl_root.rglob("*"):
    if not path.is_file():
        continue
    if any(p in SKIP_PARTS for p in path.parts):
        continue
    suffix = "".join(path.suffixes[-2:]) if path.name.endswith(".module.css") else path.suffix
    if suffix.lower() not in CSS_EXTS and not path.name.endswith(".module.css"):
        continue
    try:
        css_text_chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        continue

all_css = "\n".join(css_text_chunks)

# Match a class rule for the signature anywhere in the CSS.
# Patterns we accept as "styled":
#   .signature      (plain class selector)
#   .signature.X    (compound)
#   .signature > Y  (descendant from)
#   .X .signature   (nested)
#   [class*="signature"]  (attribute substring)
#   :where(.signature), :is(...)
def is_styled(sig: str) -> bool:
    # Use word-boundary on the signature character itself; signatures
    # are alphanumeric + _ + - so they're regex-safe.
    pattern = re.compile(
        rf"(?:\.{re.escape(sig)}\b|\[class\s*[*~|^$]?=\s*['\"]{re.escape(sig)}\b)"
    )
    return bool(pattern.search(all_css))


styled = [s for s in preserved if is_styled(s)]
styled_count = len(styled)
style_coverage = styled_count / preserved_count
threshold = 0.30

unstyled = sorted(preserved - set(styled))[:12]

if style_coverage >= threshold:
    status = "pass"
    reason = (
        f"{styled_count}/{preserved_count} preserved signatures have CSS rules "
        f"(style_coverage={style_coverage:.2f} ≥ {threshold:.2f})"
    )
else:
    status = "fail"
    reason = (
        f"only {styled_count}/{preserved_count} preserved class signatures "
        f"have a CSS rule in impl (style_coverage={style_coverage:.2f} < "
        f"{threshold:.2f}). Impl preserves the ref's class NAMES but doesn't "
        f"define styles for them — components render unstyled. See unstyledSample."
    )

out_path.write_text(json.dumps({
    "schemaVersion": 1,
    "status": status,
    "reason": reason,
    "preservedCount": preserved_count,
    "styledCount": styled_count,
    "styleCoverage": round(style_coverage, 4),
    "threshold": threshold,
    "unstyledSample": unstyled,
}, indent=2), encoding="utf-8")
print(
    f"class-signature-css-coverage: {status} "
    f"({styled_count}/{preserved_count} styled, coverage={style_coverage:.2f})"
)
sys.exit(0 if status != "fail" else 1)
PY
EXIT=$?
exit $EXIT
