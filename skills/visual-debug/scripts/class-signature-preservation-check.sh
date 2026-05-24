#!/usr/bin/env bash
# class-signature-preservation-check.sh — fail when impl source ignores
# distinctive class-name signatures from the ref's compiled CSS.
#
# Failure mode this catches ("invented design" / extracted-data ignoring):
#   Many production sites ship CSS Modules or component-scoped CSS where the
#   rendered HTML uses class names with a deterministic compiler signature —
#   typically `<componentName>__<hashSuffix>` (Next/Webpack CSS Modules) or
#   shorter `<prefix>_<hash>` forms. Examples in the wild include
#   `Header_logo__abcd12`, `prefix_pyramid_kI8Lh`, `hero__O1Pp9`. These names
#   are fingerprints of the ref's component graph.
#
#   When an LLM clone agent invents fresh Tailwind utility classes / handmade
#   vanilla CSS and discards the captured class names from <ref-dir>/html/*
#   or dom-scaffold.json, the resulting JSX/TSX has ZERO ref signatures. The
#   visual output may still resemble the ref by coincidence of layout, but
#   no captured DOM grounding is preserved — the implementation is effectively
#   freehanded. Root-cause pattern: zero captured CSS-Modules class
#   refs in impl source vs 100+ in the reference.
#
# What this check does:
#   1. Scan <ref-dir>/html/*.json (the captured per-section DOM) and
#      <ref-dir>/dom-scaffold.json for class-attribute tokens matching
#      the CSS-Modules signature regex /[A-Za-z_][A-Za-z0-9_]*__[A-Za-z0-9_-]{4,8}/
#      (component__hash) or /[A-Za-z]{2,}_[A-Za-z0-9]{4,8}/ (prefix_hash,
#      e.g. `prefix_kI8Lh`). Deduplicate.
#   2. Scan <impl>/src/**/*.{tsx,ts,jsx,js,css,module.css,scss} for the same
#      regex tokens; deduplicate.
#   3. Compute coverage = (|ref ∩ impl| / |ref|).
#   4. Status:
#         - skip   when |ref| < 10  (not enough signal to judge)
#         - pass   when coverage ≥ 0.30  (≥ 30% preserved — see handover)
#         - fail   when coverage <  0.30
#
# Universal anti-cheat — runs on every clone, regardless of framework, as
# long as the ref's captured DOM contains class tokens at all. Sites that
# render entirely without classes (rare) gracefully skip.
#
# Inputs:
#   <ref-dir>/html/*.json          — captured per-section DOM (preferred source)
#   <ref-dir>/dom-scaffold.json    — fallback DOM source if html/ is sparse
#
# Outputs: <ref-dir>/class-signature-preservation.json
#   {
#     schemaVersion: 1,
#     status: "pass" | "fail" | "skip",
#     reason?: string,
#     refSignatureCount: int,
#     implSignatureCount: int,
#     preservedCount: int,
#     coverage: float,
#     threshold: 0.30,
#     missingSample: [string, ...]    — up to 12 ref signatures absent in impl
#   }
#
# Exit: 0 pass/skip, 1 fail, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ARG="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: class-signature-preservation-check.sh <ref-dir> [<impl-root>]" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/class-signature-preservation.json"

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
    "refSignatureCount": 0,
    "implSignatureCount": 0,
    "preservedCount": 0,
    "coverage": 0.0,
    "threshold": 0.30,
    "missingSample": [],
}, indent=2), encoding="utf-8")
PY
  echo "class-signature-preservation: skip (no impl)"
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

# Two complementary signature shapes:
#   component__hash  (CSS Modules canonical, e.g. Header_logo__abc12)
#   prefix_hash      (single-underscore short form, e.g. prefix_kI8Lh)
SIG_DOUBLE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*__[A-Za-z0-9_-]{4,8})\b")
SIG_SINGLE = re.compile(r"\b([A-Za-z]{2,}_[A-Za-z0-9]{4,8})\b")
# Reject single-underscore matches that are obvious generic identifiers
# (variable names, css custom prop fragments, etc).
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
        lower = tok.lower()
        if any(lower.startswith(b) for b in SINGLE_BLOCKLIST):
            continue
        # Skip if it looks like camelCase JS identifier (no digits in hash half).
        prefix, _, suffix = tok.partition("_")
        if not any(c.isdigit() for c in suffix) and suffix.islower():
            # Plain `foo_bar` is too noisy without a digit/mixed-case hash.
            continue
        sigs.add(tok)
    return sigs


# ── Harvest ref signatures from captured DOM JSON ──
ref_sigs: set[str] = set()
html_dir = ref_dir / "html"
if html_dir.is_dir():
    for path in sorted(html_dir.glob("*.json")):
        try:
            ref_sigs |= harvest(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue

scaffold_path = ref_dir / "dom-scaffold.json"
if scaffold_path.is_file():
    try:
        ref_sigs |= harvest(scaffold_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        pass

# Fallback: also try extracted.json (sometimes captured DOM lands here for
# sites with a single primary section).
extracted_path = ref_dir / "extracted.json"
if extracted_path.is_file() and not ref_sigs:
    try:
        ref_sigs |= harvest(extracted_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        pass

# ── Harvest impl signatures ──
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
ref_count = len(ref_sigs)
impl_count = len(impl_sigs)
preserved_count = len(preserved)
coverage = (preserved_count / ref_count) if ref_count else 0.0
threshold = 0.30

# Status logic — order matters.
#
# Theft asymmetry first: if the ref capture has zero CSS-Modules signatures
# but the impl has many, that's NOT "this site has no CSS Modules". It's
# evidence that impl wholesale-pasted compiled CSS bundles (the L41/L44
# cheat) whose bundles contain hundreds of ref-side signatures we never
# captured. The original skip branch would silently let this through —
# fail it loudly.
THEFT_FLOOR = 10
if ref_count == 0 and impl_count >= THEFT_FLOOR:
    status = "fail"
    reason = (
        f"impl has {impl_count} CSS-Modules signature(s) but ref has none. "
        "Either ref extraction was skipped or impl bulk-pasted compiled CSS "
        "bundles whose internal class names are now appearing in impl source. "
        "Both are non-clones."
    )
elif ref_count < THEFT_FLOOR:
    status = "skip"
    reason = (
        f"ref signature count below floor ({ref_count} < {THEFT_FLOOR}) — "
        "site likely does not use CSS-Modules or has no captured class tokens"
    )
elif coverage >= threshold:
    status = "pass"
    reason = (
        f"{preserved_count}/{ref_count} ref signatures preserved "
        f"(coverage={coverage:.2f} ≥ {threshold:.2f})"
    )
else:
    status = "fail"
    reason = (
        f"only {preserved_count}/{ref_count} ref class-name signatures appear "
        f"in impl source (coverage={coverage:.2f} < {threshold:.2f}). Implementation "
        f"likely freehands utility classes / vanilla CSS while ignoring captured "
        f"CSS-Modules class names from <ref-dir>/html/*."
    )

missing_sample = sorted(ref_sigs - impl_sigs)[:12]

out_path.write_text(json.dumps({
    "schemaVersion": 1,
    "status": status,
    "reason": reason,
    "refSignatureCount": ref_count,
    "implSignatureCount": impl_count,
    "preservedCount": preserved_count,
    "coverage": round(coverage, 4),
    "threshold": threshold,
    "missingSample": missing_sample,
}, indent=2), encoding="utf-8")

short = f"class-signature-preservation: {status} ({preserved_count}/{ref_count} preserved, coverage={coverage:.2f})"
print(short)
sys.exit(0 if status != "fail" else 1)
PY

EXIT=$?
exit $EXIT
