#!/usr/bin/env bash
# remote-asset-ref-check.sh — fail when impl src references the ref's
# CDN / live domain directly instead of using the locally-downloaded
# /images/ /videos/ public/ assets.
#
#
# Failure pattern this closes:
#   const A = 'https://navercorp.com';
#   <img src={`${A}/img/pc/main_visual.webp`} />
#
# Why this matters:
#   - asset-transfer.json passes ("files downloaded to /public/") but the
#     impl never references them — instead it hot-links the ref's CDN.
#   - Browser renders the ref's actual images (so AE looks fine) but
#     the clone is a fake — it depends on the live ref staying online
#     and the ref's CDN authoring policy.
#   - asset-utilization counts the URL string as a reference but doesn't
#     reject remote URLs, so the orphan ratio passes too.
#
# This check scans impl/src/**/*.{tsx,jsx,ts,js,css,scss} for:
#   1. Absolute URL string literals in JSX attribute positions that
#      look like asset references (src=, poster=, href= on link[rel=icon],
#      url(...) in CSS).
#   2. Template literal patterns: src={`${HOST}/...`}, where HOST is a
#      string literal in the same file pointing to an http(s) origin.
#
# Whitelisted: localhost, 127.0.0.1, data: URLs, blob: URLs, well-known
# CDN libraries (unpkg, jsdelivr, cdnjs) that are legitimately used.
#
# Usage:
#   remote-asset-ref-check.sh <ref-dir> [<impl-src-dir>]
#   ref-dir       canonical ref dir (tmp/ref/<component>)
#   impl-src-dir  impl/src/ — auto-detected via find-impl-root.sh if omitted
#
# Output: <ref-dir>/remote-asset-ref.json
#   { schemaVersion: 1, status: "pass"|"fail", scanned, violations:[...] }
#
# Exit: 0 = pass, 1 = at least one violation, 2 = setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_SRC_DIR="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: remote-asset-ref-check.sh <ref-dir> [<impl-src-dir>]" >&2
  exit 2
fi

if [ -z "$IMPL_SRC_DIR" ]; then
  # Use shared resolver (find-impl-root.sh) for cross-loop safety.
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

if [ -z "$IMPL_SRC_DIR" ] || [ ! -d "$IMPL_SRC_DIR" ]; then
  OUT_PATH="$REF_DIR/remote-asset-ref.json"
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl/src not found — Phase 3 has not produced impl tree yet",
  "scanned": 0,
  "violations": []
}
JSON
  echo "remote-asset-ref: skip (no impl/src)"
  exit 0
fi

OUT_PATH="$REF_DIR/remote-asset-ref.json"

python3 - "$REF_DIR" "$IMPL_SRC_DIR" "$OUT_PATH" <<'PY'
import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_src = Path(sys.argv[2])
out_path = Path(sys.argv[3])

# Whitelisted hosts — legitimately remote.
WHITELIST_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0",
    # Public free CDNs commonly imported for libraries (not assets).
    "unpkg.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com",
    # Google Fonts — handled by font-parity, not asset-utilization.
    "fonts.googleapis.com", "fonts.gstatic.com",
}

# Patterns that count as asset references in impl source.
# Each captures the URL group; we then host-check the URL.
ASSET_REF_PATTERNS = [
    # <img src="https://..." />
    re.compile(r'\bsrc\s*=\s*[\"\']([^\"\'\s]+)'),
    # <video poster="https://..." />
    re.compile(r'\bposter\s*=\s*[\"\']([^\"\'\s]+)'),
    # <source src="..."> inside <video>/<audio>
    # (covered by src= above)
    # <link rel="icon" href="https://..."> — icons too
    re.compile(r'\bhref\s*=\s*[\"\'](https?://[^\"\'\s]+\.(?:ico|png|svg|webp|jpg|jpeg)[^\"\'\s]*)'),
    # CSS url(...) — match url("https://..."), url('https://...'), url(https://...)
    re.compile(r'\burl\(\s*[\"\']?(https?://[^\"\'\s\)]+)'),
    # JSX template literal asset refs: src={`${HOST}/path/...`} or
    # src={HOST + "/path/..."} — caught by separate template-literal pass.
]

# JSX template literal pattern — catches the heavy-motion site case:
#   const A = 'https://navercorp.com';
#   <img src={`${A}/img/pc/x.webp`} />
HOST_CONST_PATTERN = re.compile(
    r'const\s+\w+\s*=\s*[\"\'](https?://[^\"\'\s]+?)[\"\']'
)

violations = []
scanned = 0


def host_of(url: str) -> str:
    m = re.match(r'https?://([^/\:\?\#]+)', url)
    return m.group(1).lower() if m else ""


def is_violating(url: str) -> bool:
    host = host_of(url)
    if not host:
        return False
    if host in WHITELIST_HOSTS:
        return False
    return True


for path in impl_src.rglob("*"):
    if not path.is_file():
        continue
    if path.suffix not in {".tsx", ".jsx", ".ts", ".js", ".css", ".scss", ".module.css"}:
        continue
    if any(part in {"node_modules", ".next", "dist", "build", ".turbo"} for part in path.parts):
        continue
    scanned += 1
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    # Strip block comments only. Skipping line-comment strip — the
    # naive `//[^\n]*` also eats URL substrings starting at `//` (e.g.
    # "https://example.com/..." → "https:" + "//example.com/..."
    # consumed as if it were a comment). False-positives in comment-
    # bodies are acceptable; missing URLs is not.
    text_no_comment = re.sub(r"/\*[\s\S]*?\*/", "", text)

    # Direct asset URL violations.
    for pat in ASSET_REF_PATTERNS:
        for m in pat.finditer(text_no_comment):
            url = m.group(1)
            if not url.startswith(("http://", "https://")):
                continue
            if not is_violating(url):
                continue
            violations.append({
                "file": str(path.relative_to(impl_src)),
                "url": url[:200],
                "kind": "direct",
            })

    # Host-const pattern — defining `const X = 'https://violating-host'`
    # in impl source is itself the smell. heavy-motion site's exact pattern.
    # Simpler to flag the const definition than chase template-literal
    # usage downstream; the const has no legitimate reason to exist in
    # a clone whose assets should all be local.
    for m in HOST_CONST_PATTERN.finditer(text_no_comment):
        host_url = m.group(1)
        if not is_violating(host_url):
            continue
        var_match = re.search(
            r'const\s+(\w+)\s*=\s*[\"\']' + re.escape(host_url) + r'[\"\']',
            text_no_comment,
        )
        var = var_match.group(1) if var_match else "?"
        violations.append({
            "file": str(path.relative_to(impl_src)),
            "url": host_url[:200],
            "kind": "host-const-definition",
            "var": var,
        })


status = "fail" if violations else "pass"

# Dedup by (file, url).
seen = set()
deduped = []
for v in violations:
    key = (v["file"], v["url"])
    if key in seen:
        continue
    seen.add(key)
    deduped.append(v)

result = {
    "schemaVersion": 1,
    "status": status,
    "scanned": scanned,
    "implSrcDir": str(impl_src),
    "violationCount": len(deduped),
    "violations": deduped[:50],
    "rule": (
        "Impl source must not hot-link the reference site's CDN. "
        "Use the locally-downloaded /public/ assets via /images/ /videos/ paths. "
        "Whitelisted: localhost, 127.0.0.1, unpkg/jsdelivr/cdnjs (library CDNs), "
        "Google Fonts (handled by font-parity)."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(f"remote-asset-ref: {len(deduped)} violation(s) / {scanned} file(s) scanned → {out_path}")
sys.exit(0 if status == "pass" else 1)
PY
