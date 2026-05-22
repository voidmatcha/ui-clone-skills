#!/usr/bin/env bash
# asset-download.sh — actually download every image referenced in
# visible-images.json. Pipeline DEFAULT philosophy: this is a local-use
# clone tool (research / benchmark / personal study) — NOT a publication
# pipeline. License flags from paid-features-detect.sh are advisory, not
# blockers. Always attempt download; substitute ONLY when HTTP request
# genuinely fails.
#
#
# Input:  tmp/ref/<component>/ — must contain visible-images.json
# Output: tmp/ref/<component>/download-log.json + populated impl/public/
#
# Usage: asset-download.sh <ref-dir> <impl-public-dir>
set -euo pipefail

REF_DIR="${1:-}"
IMPL_PUBLIC="${2:-}"
if [ -z "$REF_DIR" ] || [ -z "$IMPL_PUBLIC" ]; then
  echo "Usage: $0 <ref-dir> <impl-public-dir>" >&2
  exit 2
fi
if [ ! -d "$REF_DIR" ]; then
  echo "ERROR: ref-dir not found: $REF_DIR" >&2
  exit 2
fi

VIS_IMG="$REF_DIR/visible-images.json"
if [ ! -f "$VIS_IMG" ]; then
  echo "▸ asset-download: SKIP — no visible-images.json in $REF_DIR"
  exit 0
fi

mkdir -p "$IMPL_PUBLIC"

LOG="$REF_DIR/download-log.json"

python3 - "$VIS_IMG" "$IMPL_PUBLIC" "$LOG" <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

vis_img_path = Path(sys.argv[1])
impl_public = Path(sys.argv[2])
log_path = Path(sys.argv[3])

try:
    data = json.loads(vis_img_path.read_text())
except (OSError, json.JSONDecodeError) as e:
    print(f"ERROR: cannot read {vis_img_path}: {e}", file=sys.stderr)
    sys.exit(2)

# visible-images.json can be either a list of {src} dicts or a wrapped object
if isinstance(data, dict):
    items = data.get("images", []) or data.get("items", []) or []
elif isinstance(data, list):
    items = data
else:
    items = []

attempts = []
succeeded = 0
failed = 0
skipped = 0
unique_urls = set()

for item in items:
    if isinstance(item, dict):
        url = item.get("src") or item.get("url") or ""
    elif isinstance(item, str):
        url = item
    else:
        continue
    if not url or not url.startswith(("http://", "https://", "//")):
        continue
    if url.startswith("//"):
        url = "https:" + url
    if url in unique_urls:
        continue
    unique_urls.add(url)

    parsed = urlparse(url)
    # Path component → local file under impl/public/, stripped of /cdn-cgi/image/ prefix
    raw_path = unquote(parsed.path)
    # Strip Cloudflare image-resize prefix if present
    if "/cdn-cgi/image/" in raw_path:
        idx = raw_path.index("/cdn-cgi/image/") + len("/cdn-cgi/image/")
        # skip the resize spec segment (everything up to the next /)
        slash = raw_path.find("/", idx)
        raw_path = raw_path[slash:] if slash >= 0 else raw_path
    raw_path = raw_path.lstrip("/")
    if not raw_path:
        raw_path = parsed.netloc.replace(":", "_") + "/root"
    dest = impl_public / raw_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size > 0:
        attempts.append({"url": url, "dest": str(dest.relative_to(impl_public.parent)),
                         "status": "skipped-exists", "bytes": dest.stat().st_size})
        skipped += 1
        continue

    try:
        # Two-pass: -w writes status code to stderr, capture for log.
        result = subprocess.run(
            ["curl", "-sSL", "--fail-with-body",
             "--max-time", "15", "--retry", "3", "--retry-delay", "1",
             "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
             "-w", "%{http_code}",
             "-o", str(dest), url],
            capture_output=True, text=True, timeout=30
        )
        http_code = (result.stdout or "").strip()
        if result.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            attempts.append({"url": url, "dest": str(dest.relative_to(impl_public.parent)),
                             "status": "ok", "httpCode": http_code, "via": "curl", "bytes": dest.stat().st_size})
            succeeded += 1
        else:
            # agent-browser fallback for bot-protected sites (Cloudflare challenge,
            # hotlink protection, etc.) — curl gets blocked but real Chrome via
            # agent-browser inherits cookies + JS-handshake passes.
            ab_session = "asset-dl"
            try:
                ab_result = subprocess.run(
                    ["agent-browser", "--session", ab_session, "fetch", url, "--out", str(dest)],
                    capture_output=True, text=True, timeout=30
                )
                if ab_result.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                    attempts.append({"url": url, "dest": str(dest.relative_to(impl_public.parent)),
                                     "status": "ok", "httpCode": http_code, "via": "agent-browser",
                                     "bytes": dest.stat().st_size})
                    succeeded += 1
                else:
                    err_curl = (result.stderr or "").strip()[:100]
                    err_ab = (ab_result.stderr or "").strip()[:100]
                    attempts.append({"url": url, "dest": str(dest.relative_to(impl_public.parent)),
                                     "status": "failed", "httpCode": http_code,
                                     "errorCurl": err_curl, "errorAgentBrowser": err_ab})
                    failed += 1
                    if dest.exists():
                        dest.unlink()
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                err = (result.stderr or "").strip()[:200]
                attempts.append({"url": url, "dest": str(dest.relative_to(impl_public.parent)),
                                 "status": "failed", "httpCode": http_code,
                                 "errorCurl": err, "errorAgentBrowser": f"unavailable: {e}"})
                failed += 1
                if dest.exists():
                    dest.unlink()
    except (subprocess.TimeoutExpired, OSError) as e:
        attempts.append({"url": url, "status": "timeout", "error": str(e)[:200]})
        failed += 1
        if dest.exists():
            dest.unlink()

total = len(attempts)
success_rate = (succeeded / total * 100) if total else 0.0

log = {
    "schemaVersion": 1,
    "totalAttempted": total,
    "succeeded": succeeded,
    "failed": failed,
    "skippedExisting": skipped,
    "successRate": round(success_rate, 1),
    "attempts": attempts,
}
log_path.write_text(json.dumps(log, indent=2) + "\n")
print(f"✓ asset-download: {succeeded} ok / {failed} failed / {skipped} skipped (existing) — {success_rate:.0f}% success rate")
print(f"  log → {log_path}")
print(f"  files → {impl_public}")
if failed and not succeeded:
    print(f"  ⚠ all attempts failed — check network, paid-CDN, or 4xx/5xx in download-log.json")
elif success_rate < 80 and total >= 5:
    print(f"  ⚠ low success rate ({success_rate:.0f}%) — substitution may be justified for failed entries")
PY
