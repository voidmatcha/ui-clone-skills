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
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote, urljoin

vis_img_path = Path(sys.argv[1])
impl_public = Path(sys.argv[2])
log_path = Path(sys.argv[3])
ref_dir = vis_img_path.parent

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

# Harvest image URLs from the captured DOM (structure.json / dom-scaffold.json).
# visible-images.json only records images that were in-viewport at capture time;
# lazy-loaded / off-screen images (e.g. realfood's intro/ section) appear in the
# DOM but not there, so without this they never download and 404 in the clone.
import re as _re
# Match absolute (https://…) and root-relative (/cdn-cgi/…, /images/…) image
# URLs. realfood stores DOM srcs as root-relative cdn-cgi paths; those are
# resolved against the site origin in the download loop below.
_IMG_URL_RE = _re.compile(
    r"(?:https?://[^\"'\s)]+?|/[^\"'\s)]+?)\.(?:webp|png|jpe?g|avif|gif|svg)\b",
    _re.IGNORECASE,
)
for _src_name in ("structure.json", "dom-scaffold.json"):
    _p = ref_dir / _src_name
    if not _p.exists():
        continue
    try:
        _text = _p.read_text()
    except OSError:
        continue
    for _u in _IMG_URL_RE.findall(_text):
        items.append({"src": _u})

attempts = []
succeeded = 0
failed = 0
skipped = 0
unique_urls = set()

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def read_referrer_url() -> str:
    """Best-effort source page URL used for hotlink-protected image hosts."""
    for filename in ("head.json", "extracted.json", "canvas-webgl-detection.json"):
        path = ref_dir / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        candidates = [
            payload.get("url"),
            payload.get("sourceUrl"),
            payload.get("targetUrl"),
        ]
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            candidates.extend([
                metadata.get("url"),
                metadata.get("sourceUrl"),
                metadata.get("targetUrl"),
            ])
        for value in candidates:
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
    return ""


def curl_command(url: str, dest: Path, referrer_url: str) -> list[str]:
    command = [
        "curl", "-sSL", "--fail-with-body",
        "--max-time", "15", "--retry", "3", "--retry-delay", "1",
        "-A", USER_AGENT,
        "-H", "Accept: image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    ]
    if referrer_url:
        command.extend(["-e", referrer_url])
        command.extend([
            "-H", "Sec-Fetch-Dest: image",
            "-H", "Sec-Fetch-Mode: no-cors",
            "-H", "Sec-Fetch-Site: cross-site",
        ])
    command.extend(["-w", "%{http_code}", "-o", str(dest), url])
    return command


def unwrap_agent_browser_payload(raw: str) -> dict:
    parsed = json.loads(raw)
    if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict) and "result" in parsed["data"]:
        parsed = parsed["data"]["result"]
    if isinstance(parsed, dict) and "result" in parsed and isinstance(parsed["result"], (dict, str)):
        parsed = parsed["result"]
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    if not isinstance(parsed, dict):
        raise ValueError("agent-browser eval returned a non-object payload")
    return parsed


def agent_browser_download(url: str, dest: Path, referrer_url: str) -> tuple[bool, str, str, int]:
    session = "asset-dl"
    start_url = referrer_url or url
    open_result = subprocess.run(
        ["agent-browser", "--session", session, "open", start_url],
        capture_output=True, text=True, timeout=30,
    )
    if open_result.returncode != 0:
        err = (open_result.stderr or open_result.stdout or "").strip()[:200]
        return False, "open", err, 0

    if referrer_url:
        navigate_js = (
            "(() => { window.location.href = "
            + json.dumps(url)
            + "; return window.location.href; })()"
        )
        nav_result = subprocess.run(
            ["agent-browser", "--session", session, "eval", "--json", navigate_js],
            capture_output=True, text=True, timeout=15,
        )
        if nav_result.returncode != 0:
            err = (nav_result.stderr or nav_result.stdout or "").strip()[:200]
            return False, "navigate", err, 0
        subprocess.run(
            ["agent-browser", "--session", session, "wait", "1000"],
            capture_output=True, text=True, timeout=10,
        )

    fetch_js = """(async () => {
  const response = await fetch(window.location.href, { cache: "no-store" });
  if (!response.ok) {
    return { ok: false, status: response.status, statusText: response.statusText };
  }
  const buffer = await response.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return {
    ok: true,
    status: response.status,
    contentType: response.headers.get("content-type") || "",
    bodyBase64: btoa(binary),
  };
})()"""
    eval_result = subprocess.run(
        ["agent-browser", "--session", session, "eval", "--json", fetch_js],
        capture_output=True, text=True, timeout=45,
    )
    if eval_result.returncode != 0:
        err = (eval_result.stderr or eval_result.stdout or "").strip()[:200]
        return False, "eval", err, 0

    try:
        payload = unwrap_agent_browser_payload(eval_result.stdout.strip())
        status = int(payload.get("status") or 0)
        body_base64 = payload.get("bodyBase64")
        if payload.get("ok") is True and isinstance(body_base64, str) and body_base64:
            dest.write_bytes(base64.b64decode(body_base64))
            return True, "", "", status
        err = payload.get("statusText") or payload.get("error") or "empty body"
        return False, "eval", str(err)[:200], status
    except (ValueError, json.JSONDecodeError, OSError, base64.binascii.Error) as e:
        return False, "eval", str(e)[:200], 0


referrer_url = read_referrer_url()

for item in items:
    if isinstance(item, dict):
        url = item.get("src") or item.get("url") or ""
    elif isinstance(item, str):
        url = item
    else:
        continue
    # Resolve root-relative DOM srcs (/cdn-cgi/…, /images/…) against the origin.
    if url.startswith("/") and not url.startswith("//") and referrer_url:
        url = urljoin(referrer_url, url)
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
        # -w writes the status code to stdout for the attempt log.
        result = subprocess.run(
            curl_command(url, dest, referrer_url),
            capture_output=True, text=True, timeout=30
        )
        http_code = (result.stdout or "").strip()
        if result.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            attempts.append({"url": url, "dest": str(dest.relative_to(impl_public.parent)),
                             "status": "ok", "httpCode": http_code, "via": "curl", "bytes": dest.stat().st_size})
            succeeded += 1
        else:
            try:
                ok, phase, ab_error, ab_status = agent_browser_download(url, dest, referrer_url)
                if ok and dest.exists() and dest.stat().st_size > 0:
                    attempts.append({"url": url, "dest": str(dest.relative_to(impl_public.parent)),
                                     "status": "ok", "httpCode": str(ab_status or http_code),
                                     "via": "agent-browser-eval",
                                     "bytes": dest.stat().st_size})
                    succeeded += 1
                else:
                    err_curl = (result.stderr or "").strip()[:100]
                    err_ab = f"{phase}: {ab_error}"[:100]
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
# A skipped-existing file is already present on disk — count it as a success,
# not a shortfall. Otherwise width-variant duplicates (same local dest) deflate
# the rate and spuriously suggest asset substitution.
success_rate = ((succeeded + skipped) / total * 100) if total else 0.0

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
