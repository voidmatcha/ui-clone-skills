#!/usr/bin/env bash
# scaffold-warn-check.sh — fail when impl source carries
# scaffold-to-jsx subtree-not-found placeholders.
#
# Signal 1 (Common cheat pattern). scaffold-to-jsx.sh emits a sentinel
# `<section data-scaffold-warn="subtree-not-found-for-{name}" />`
# whenever it can't locate the dom-scaffold subtree for a generated
# section. The intent was for Phase-5b visual-judge to surface the
# gap, but in practice the placeholders ship to production-state
# without anyone catching them — the impl renders empty sections and
# section-compare blames CSS while the real cause is missing subtree
# locations.
#
# Static scan of impl source rejects any file containing
# `data-scaffold-warn` so the agent has to either (a) re-run the
# scaffold extractor with corrected subtree resolution or (b) author
# the section by hand. Either way, an unaddressed placeholder is a
# block-severity FAIL.
#
# Usage:
#   scaffold-warn-check.sh <ref-dir> [<impl-root>]
#
# Output: <ref-dir>/scaffold-warn.json
#
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: scaffold-warn-check.sh <ref-dir> [<impl-root>]" >&2
  exit 2
fi

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

OUT_PATH="$REF_DIR/scaffold-warn.json"

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl_root not found",
  "warnings": []
}
JSON
  echo "scaffold-warn: skip (no impl)"
  exit 0
fi

python3 - "$IMPL_ROOT" "$OUT_PATH" <<'PY'
import json
import re
import sys
from pathlib import Path

impl_root = Path(sys.argv[1])
out_path = Path(sys.argv[2])

SCAN_SUFFIXES = {".tsx", ".jsx", ".ts", ".js", ".html", ".vue", ".svelte"}
EXCLUDE = {"node_modules", ".next", ".svelte-kit", "dist", "build",
           ".turbo", ".cache", ".git"}

WARN_RE = re.compile(
    r'data-scaffold-warn\s*=\s*["\']?'
    r'(?P<value>subtree-not-found-for-[^"\'\s>]+)'
)

warnings: list[dict] = []
scanned = 0

for sub in ("src", "app", "pages"):
    base = impl_root / sub
    if not base.is_dir():
        continue
    for p in base.rglob("*"):
        if not p.is_file() or p.suffix not in SCAN_SUFFIXES:
            continue
        if any(part in EXCLUDE for part in p.parts):
            continue
        scanned += 1
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in WARN_RE.finditer(text):
            warnings.append({
                "file": str(p.relative_to(impl_root)),
                "marker": m.group("value"),
                "section": m.group("value")[
                    len("subtree-not-found-for-"):
                ],
            })

# Dedup by (file, section).
seen = set()
deduped = []
for w in warnings:
    key = (w["file"], w["section"])
    if key in seen:
        continue
    seen.add(key)
    deduped.append(w)


status = "fail" if deduped else "pass"
result = {
    "schemaVersion": 1,
    "status": status,
    "implRoot": str(impl_root),
    "scannedFiles": scanned,
    "warningCount": len(deduped),
    "warnings": deduped[:50],
    "rule": (
        "Impl source must not carry `data-scaffold-warn=\"subtree-not-"
        "found-for-*\"` placeholders left by scaffold-to-jsx.sh. Each "
        "placeholder is an unresolved section subtree and the impl will "
        "render that section empty. Re-run scaffold extraction with "
        "corrected subtree resolution or author the section by hand."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"scaffold-warn: {len(deduped)} placeholder(s) / "
    f"{scanned} file(s) scanned → {status} → {out_path}"
)
sys.exit(0 if status == "pass" else 1)
PY
