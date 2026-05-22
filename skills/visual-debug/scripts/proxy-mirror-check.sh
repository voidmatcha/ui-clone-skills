#!/usr/bin/env bash
# proxy-mirror-check.sh — fail proxy/static mirrors of the original runtime.
#
# Usage:
#   proxy-mirror-check.sh <ref-dir> [<impl-dir>]
#
# A faithful ui-reverse-engineering result is source code that renders the
# captured DOM/assets/motion. Serving the original HTML/Next chunks through a
# local proxy can look perfect, but it is not a clone implementation and must
# not satisfy post-implement gates.
set -euo pipefail

REF_DIR="${1:?Usage: proxy-mirror-check.sh <ref-dir> [<impl-dir>]}"
IMPL_ARG="${2:-}"
OUT="$REF_DIR/proxy-mirror-check.json"

[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

IMPL_DIR="$IMPL_ARG"
if [ -z "$IMPL_DIR" ]; then
  for c in \
    "$(dirname "$REF_DIR")/../impl" \
    "$(dirname "$REF_DIR")/impl" \
    "scratch/$(basename "$REF_DIR")/impl" \
    "impl"; do
    if [ -d "$c" ]; then IMPL_DIR="$c"; break; fi
  done
fi

if [ -z "$IMPL_DIR" ] || [ ! -d "$IMPL_DIR" ]; then
  python3 - "$OUT" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "schemaVersion": 1,
    "status": "skip",
    "reason": "impl dir not found",
    "findings": [],
}, indent=2), encoding="utf-8")
PY
  echo "proxy-mirror-check: SKIP (impl dir not found)"
  exit 0
fi

python3 - "$OUT" "$IMPL_DIR" <<'PY'
import json
import re
import sys
from pathlib import Path

out = Path(sys.argv[1])
impl = Path(sys.argv[2])
findings = []

TEXT_EXTS = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".htm", ".json"}

def rel(path: Path) -> str:
    try:
        return str(path.relative_to(impl))
    except ValueError:
        return str(path)

def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

for path in impl.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in TEXT_EXTS:
        continue
    if any(part in {"node_modules", ".git", ".next", "dist", "build"} for part in path.parts):
        continue
    text = read(path)
    lower = text.lower()

    if path.name in {"server.js", "server.mjs", "proxy.js", "proxy.mjs"}:
        if (
            re.search(r'\bupstream\b\s*=\s*["\']https?://', text)
            and ("proxyandcache" in lower or "fetch(upstream" in lower or "http-proxy" in lower)
        ):
            findings.append({
                "kind": "proxy-runtime-server",
                "file": rel(path),
                "reason": "local server proxies/caches the original upstream runtime",
            })

    if path.name == "index.html" and ("public" in path.parts or path.parent == impl):
        has_full_doc = "<!doctype html" in lower or "<html" in lower
        has_next_runtime = "self.__next_f" in text or "__NEXT_DATA__" in text or "/_next/static/" in text
        if has_full_doc and has_next_runtime:
            findings.append({
                "kind": "next-ssr-document-mirror",
                "file": rel(path),
                "reason": "index.html contains a captured Next/React server document and original runtime chunks",
            })

    if path.name == "index.html" and "document.documentelement.outerhtml" in lower:
        findings.append({
            "kind": "outerhtml-document-mirror",
            "file": rel(path),
            "reason": "implementation stores a whole-document outerHTML mirror",
        })

status = "fail" if findings else "pass"
payload = {
    "schemaVersion": 1,
    "status": status,
    "implDir": str(impl),
    "findings": findings,
    "rule": (
        "The implementation must be generated source code. A local server that "
        "proxies/caches the original upstream HTML, RSC payloads, or _next chunks "
        "is a mirror and cannot satisfy clone-fidelity gates."
    ),
}
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
if findings:
    print(f"proxy-mirror-check: FAIL ({len(findings)} mirror signal(s))", file=sys.stderr)
    sys.exit(1)
print("proxy-mirror-check: PASS")
PY
