#!/usr/bin/env bash
# bundle-paste-check.sh — fail when impl bulk-pastes the ref's compiled
# CSS bundles, Next.js compiled chunks, or rendered HTML into its own
# public/src tree.
#
# Failure mode this catches:
#   Agent skips extraction + component synthesis, downloads the ref's
#   compiled CSS bundle filenames into impl/public/css/ (or any wrapper
#   directory), pastes the ref's _next/static/ chunks into impl/public/,
#   and/or dumps the ref's rendered HTML into a markup.html that the
#   single-file impl imports via `?raw` and renders with
#   dangerouslySetInnerHTML. The result looks pixel-perfect because it
#   IS the ref runtime, with a React shell around it.
#
# Detection rules (any single violation → FAIL):
#   R1: any directory under impl/public/ that contains >=3 files matching
#       /^[0-9a-f]{8,}\.css$/ (Webpack/Next CSS-Modules content-hash naming).
#       Same pattern caught by html-paste-check at <style>-inline level —
#       this catches the static-file form.
#   R2: any directory under impl/public/_next/ (full Next runtime mirror).
#   R3: any *.html file under impl/src/ or impl/app/ imported with `?raw`
#       (Vite raw-import shape) AND mounted via dangerouslySetInnerHTML.
#   R4: impl/public/<anywhere> contains a file whose first 200 bytes
#       contain a JSON-encoded `self.__next_f` push (rendered Next/React
#       server document payload).
#
# Output: <ref-dir>/bundle-paste-check.json
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ARG="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: bundle-paste-check.sh <ref-dir> [<impl-root>]" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/bundle-paste-check.json"

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
    "violations": [],
}, indent=2), encoding="utf-8")
PY
  echo "bundle-paste-check: skip (no impl)"
  exit 0
fi

python3 - "$IMPL_ROOT" "$OUT_PATH" <<'PY'
import json
import re
import sys
from pathlib import Path

impl_root = Path(sys.argv[1])
out_path = Path(sys.argv[2])

violations: list[dict] = []
HEXHASH_CSS = re.compile(r"^[0-9a-f]{8,}\.css$")

def rel(p: Path) -> str:
    try:
        return str(p.relative_to(impl_root))
    except ValueError:
        return str(p)

# R1: hex-hash CSS bundles dumped into impl/public/<anywhere>/
public_dir = impl_root / "public"
if public_dir.is_dir():
    by_parent: dict[str, list[str]] = {}
    for path in public_dir.rglob("*.css"):
        if not HEXHASH_CSS.match(path.name):
            continue
        parent_rel = rel(path.parent)
        by_parent.setdefault(parent_rel, []).append(path.name)
    for parent_rel, names in by_parent.items():
        if len(names) >= 3:
            violations.append({
                "rule": "R1",
                "kind": "hex-hash-css-bundle-paste",
                "dir": parent_rel,
                "fileCount": len(names),
                "sample": sorted(names)[:5],
                "reason": (
                    f"{len(names)} content-hashed CSS bundle(s) found under "
                    f"{parent_rel}/. This is the shape of a bulk paste of the "
                    f"ref's compiled CSS-Modules bundles (Webpack/Next naming)."
                ),
            })

# R2: _next/ runtime mirror anywhere under public/
for cand in (public_dir / "_next", impl_root / "_next"):
    if cand.is_dir() and cand.exists():
        # Count any files within
        count = sum(1 for _ in cand.rglob("*") if _.is_file())
        if count > 0:
            violations.append({
                "rule": "R2",
                "kind": "next-runtime-mirror",
                "dir": rel(cand),
                "fileCount": count,
                "reason": (
                    f"impl contains _next/ directory with {count} file(s). "
                    f"Pasting ref's Next.js compiled chunks into impl is a runtime "
                    f"mirror, not a clone."
                ),
            })

# R3 + R4: scan src/ + app/ for `?raw` HTML imports and Next push payloads.
SRC_ROOTS = [impl_root / "src", impl_root / "app", impl_root]
RAW_HTML_IMPORT = re.compile(r'''import\s+\w+\s+from\s+["']([^"']+\.html)\?raw["']''')
DANGEROUSLY = re.compile(r"dangerouslySetInnerHTML\s*=\s*\{[^}]*__html")
NEXT_PUSH = re.compile(r'self\.__next_f\.push')

raw_imports: list[str] = []
dangerously_files: list[str] = []
for src_root in SRC_ROOTS:
    if not src_root.is_dir():
        continue
    for path in src_root.rglob("*"):
        if not path.is_file():
            continue
        if any(p in {"node_modules", ".git", ".next", "dist", "build"} for p in path.parts):
            continue
        if path.suffix.lower() not in {".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in RAW_HTML_IMPORT.finditer(text):
            raw_imports.append(rel(path))
            break
        if DANGEROUSLY.search(text):
            dangerously_files.append(rel(path))

if raw_imports and dangerously_files:
    intersection = set(raw_imports) & set(dangerously_files)
    if intersection:
        violations.append({
            "rule": "R3",
            "kind": "raw-html-import-mounted",
            "files": sorted(intersection),
            "reason": (
                "Impl source imports a .html file via Vite's `?raw` suffix AND "
                "mounts it via dangerouslySetInnerHTML in the same file. This "
                "is the shape of dumping the ref's rendered HTML and rendering "
                "it inside a React shell."
            ),
        })

# R4: scan public/ for files whose head contains self.__next_f.push
if public_dir.is_dir():
    for path in public_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            head = path.read_bytes()[:512]
        except OSError:
            continue
        try:
            head_text = head.decode("utf-8", errors="ignore")
        except Exception:
            continue
        if NEXT_PUSH.search(head_text):
            violations.append({
                "rule": "R4",
                "kind": "next-server-payload-paste",
                "file": rel(path),
                "reason": (
                    "File contains a self.__next_f.push() server-document "
                    "payload — paste of the ref's rendered React server output."
                ),
            })

status = "fail" if violations else "pass"
reason = (
    f"{len(violations)} bundle-paste violation(s) detected"
    if violations
    else "no bulk-paste of ref CSS bundles, _next runtime, raw-HTML, or server payloads"
)

out_path.write_text(json.dumps({
    "schemaVersion": 1,
    "status": status,
    "reason": reason,
    "implRoot": str(impl_root),
    "violationCount": len(violations),
    "violations": violations,
}, indent=2), encoding="utf-8")

print(f"bundle-paste-check: {status} ({len(violations)} violation(s))")
sys.exit(0 if status == "pass" else 1)
PY
EXIT=$?
exit $EXIT
