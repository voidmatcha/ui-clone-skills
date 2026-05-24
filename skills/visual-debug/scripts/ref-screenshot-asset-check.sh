#!/usr/bin/env bash
# ref-screenshot-asset-check.sh — fail when impl uses ref's captured
# screenshots as background images or assets.
#
#
# Detection: scan impl/src/ and impl/public/ for any reference to
# the ref's screenshot artifacts:
#   - tmp/ref/<component>/sections/{ref,impl}/*.png  (per-section crops)
#   - tmp/ref/<component>/static/{ref,impl}/*.png    (full-page screenshots)
#   - tmp/ref/<component>/sections/diff/*.png        (AE diff images)
#   - tmp/ref/<component>/transitions/*.{png,webp,mp4}
#
# Also: scan impl/public/ for files byte-identical to anything under
# the ref's screenshot dirs (catches the "copy-and-rename" variant).
#
# Usage:
#   ref-screenshot-asset-check.sh <ref-dir> [<impl-root>]
#   ref-dir       canonical ref dir (tmp/ref/<component>)
#   impl-root     impl/ — auto-detected via find-impl-root.sh if omitted
#
# Output: <ref-dir>/ref-screenshot-asset.json
#   { schemaVersion: 1, status: "pass"|"fail", scanned, violations:[...] }
#
# Exit: 0 = pass, 1 = at least one violation, 2 = setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: ref-screenshot-asset-check.sh <ref-dir> [<impl-root>]" >&2
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

OUT_PATH="$REF_DIR/ref-screenshot-asset.json"

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl_root not found",
  "scanned": 0,
  "violations": []
}
JSON
  echo "ref-screenshot-asset: skip (no impl)"
  exit 0
fi

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT_PATH" <<'PY'
# Compat note: this embedded Python uses PEP 604 union syntax (`X | Y`)
# which needs Python 3.10+. macOS dev environments ship 3.9.6 by default;
# without this future-import the script raises SyntaxError before writing
# ref-screenshot-asset.json, blocking the dispatcher. Future-import defers
# annotation evaluation so 3.9 accepts the modern syntax as strings.
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_root = Path(sys.argv[2])
out_path = Path(sys.argv[3])

# 1. Build the forbidden-path set from ref's captured artifacts.
# These are paths the impl must never reference.
forbidden_substrings: set[str] = set()
ref_screenshot_files: dict[str, str] = {}  # sha256 → relative path

REF_SCREENSHOT_DIRS = [
    ref_dir / "sections" / "ref",
    ref_dir / "sections" / "impl",
    ref_dir / "sections" / "diff",
    ref_dir / "static" / "ref",
    ref_dir / "static" / "impl",
    ref_dir / "transitions",
    ref_dir / "scroll-video",
    ref_dir / "clip",
]

# Always-forbidden substrings — even literal references to these dirs in
# impl source counts as cheat.
forbidden_substrings.update({
    "tmp/ref/",
    "/sections/ref/",
    "/sections/impl/",
    "/sections/diff/",
    "/static/ref/",
    "/static/impl/",
    "/scroll-video/",
})


def sha256_of(p: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# Index ref screenshots by hash.
SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm")
for d in REF_SCREENSHOT_DIRS:
    if not d.is_dir():
        continue
    for p in d.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUFFIXES:
            h = sha256_of(p)
            if h:
                ref_screenshot_files[h] = str(p.relative_to(ref_dir))


# 2. Scan impl source tree for forbidden references.
SCAN_EXCLUDE = {
    "node_modules", ".next", ".nuxt", ".svelte-kit", "dist", "build",
    ".turbo", ".cache", "coverage", ".git", ".vite",
}
TEXT_SUFFIXES = {".tsx", ".jsx", ".ts", ".js", ".mjs", ".cjs",
                 ".css", ".scss", ".sass", ".less",
                 ".module.css", ".html", ".htm", ".vue", ".svelte",
                 ".json", ".md", ".mdx"}

violations = []
scanned_text = 0
scanned_binary = 0

for p in impl_root.rglob("*"):
    if not p.is_file():
        continue
    try:
        rel_parts = p.relative_to(impl_root).parts
    except ValueError:
        continue
    if any(part in SCAN_EXCLUDE for part in rel_parts):
        continue
    if p.suffix in TEXT_SUFFIXES:
        scanned_text += 1
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for needle in forbidden_substrings:
            if needle in text:
                violations.append({
                    "file": str(p.relative_to(impl_root)),
                    "kind": "ref-path-reference",
                    "needle": needle,
                })
    elif p.suffix.lower() in SUFFIXES:
        scanned_binary += 1
        h = sha256_of(p)
        if h and h in ref_screenshot_files:
            violations.append({
                "file": str(p.relative_to(impl_root)),
                "kind": "byte-identical-copy",
                "refSource": ref_screenshot_files[h],
                "sha256": h[:12],
            })

# Dedup by (file, kind, needle/refSource).
seen = set()
deduped = []
for v in violations:
    key = (v["file"], v["kind"], v.get("needle") or v.get("refSource", ""))
    if key in seen:
        continue
    seen.add(key)
    deduped.append(v)

status = "fail" if deduped else "pass"
result = {
    "schemaVersion": 1,
    "status": status,
    "implRoot": str(impl_root),
    "scannedTextFiles": scanned_text,
    "scannedBinaryFiles": scanned_binary,
    "refScreenshotCount": len(ref_screenshot_files),
    "violationCount": len(deduped),
    "violations": deduped[:50],
    "rule": (
        "Impl must not reference or contain copies of reference screenshot "
        "artifacts (tmp/ref/*/sections/, tmp/ref/*/static/, transitions, "
        "scroll-video, clip). Using ref screenshots as impl backgrounds "
        "fakes pixel-diff agreement without implementing the actual UI."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(f"ref-screenshot-asset: {len(deduped)} violation(s) / "
      f"{scanned_text}T+{scanned_binary}B files scanned → {out_path}")
sys.exit(0 if status == "pass" else 1)
PY
