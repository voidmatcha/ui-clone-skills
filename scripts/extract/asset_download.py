#!/usr/bin/env python3
# mypy: ignore-errors
# ruff: noqa: E402, F401, F541, I001, UP038
"""Download captured and runtime-required media into an implementation tree."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote, urljoin

ref_dir = Path(sys.argv[1])
impl_public = Path(sys.argv[2])
log_path = Path(sys.argv[3])
vis_img_path = ref_dir / "visible-images.json"
required_media_path = ref_dir / "required-media.json"

data = []
if vis_img_path.exists():
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


def _add_required_media_items() -> None:
    """Promote required runtime media (Lottie/video/SVG) into the download set.

    required-media.json is produced from bundle/runtime analysis, so those files
    may never appear as visible DOM images. If we only mirror visible images, a
    clone can build while its animation/runtime proof fails because the JSON or
    video assets were never transferred to public/.
    """
    if not required_media_path.exists():
        return
    try:
        payload = json.loads(required_media_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: cannot read {required_media_path}: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(payload, dict):
        return

    def add(bucket: str, keys: tuple[str, ...], media_kind: str) -> None:
        entries = payload.get(bucket) or []
        if not isinstance(entries, list):
            return
        for entry in entries:
            if isinstance(entry, str):
                src = entry
            elif isinstance(entry, dict):
                src = ""
                for key in keys:
                    value = entry.get(key)
                    if isinstance(value, str) and value:
                        src = value
                        break
            else:
                src = ""
            if src:
                item = {"src": src, "mediaKind": media_kind, "requiredMedia": True}
                if media_kind == "video":
                    video_name = Path(unquote(urlparse(src).path)).name
                    if video_name:
                        item["destPath"] = f"videos/{video_name}"
                items.append(item)

    add("videos", ("src", "url", "path", "poster"), "video")
    add("lottie", ("path", "src", "url"), "lottie")
    add("svgs", ("src", "url", "path"), "svg")


_add_required_media_items()


def _safe_dest_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    dest_path = Path(value.strip())
    if dest_path.is_absolute() or any(part in ("", ".", "..") for part in dest_path.parts):
        return None
    return dest_path


def _add_vimeo_oembed_thumbnails() -> None:
    """Mirror Vimeo oEmbed thumbnails for localized <video poster=...> output."""
    for path in sorted((ref_dir / "resources" / "vimeo.com" / "api").glob("oembed-*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        thumbnail_url = payload.get("thumbnail_url")
        video_id = payload.get("video_id")
        video_id_text = str(video_id) if isinstance(video_id, (int, str)) else ""
        if (
            isinstance(thumbnail_url, str)
            and thumbnail_url.startswith(("http://", "https://", "//"))
            and video_id_text.isdigit()
        ):
            items.append({
                "src": thumbnail_url,
                "mediaKind": "video-poster",
                "requiredMedia": True,
                "source": "vimeo-oembed",
                "destPath": f"videos/vimeo-{video_id_text}.jpg",
            })


_add_vimeo_oembed_thumbnails()

# Harvest image URLs from the captured DOM (structure.json / dom-scaffold.json).
# visible-images.json only records images that were in-viewport at capture time;
# lazy-loaded / off-screen images (e.g. realfood's intro/ section) appear in the
# DOM but not there, so without this they never download and 404 in the clone.
import re as _re
# Match absolute (https://…) and root-relative (/cdn-cgi/…, /images/…) image
# URLs. realfood stores DOM srcs as root-relative cdn-cgi paths; those are
# resolved against the site origin in the download loop below.
_ASSET_URL_RE = _re.compile(
    r"(?:https?://[^\"'\s)]+|/[^\"'\s)]+)\.(?:webp|png|jpe?g|avif|gif|svg|mp4|webm|mov|m4v|json|lottie)\b",
    _re.IGNORECASE,
)
_VIMEO_PROGRESSIVE_RE = _re.compile(
    r"https?://player\.vimeo\.com/progressive_redirect/playback/(?P<video_id>\d+)/[^\"'\s)]+\.mp4(?:\?[^\"'\s)]*)?",
    _re.IGNORECASE,
)


def _decode_captured_url(value: str) -> str:
    decoded = value.replace("\\/", "/")
    decoded = _re.sub(r"\\+u0026", "&", decoded)
    return decoded.rstrip("\\")


def _is_vimeo_progressive_url(value: str) -> bool:
    return "player.vimeo.com/progressive_redirect/playback/" in _decode_captured_url(value)


def _add_vimeo_progressive_videos(text: str) -> None:
    for match in _VIMEO_PROGRESSIVE_RE.finditer(_decode_captured_url(text)):
        video_id = match.group("video_id")
        items.append({
            "src": match.group(0).rstrip("\\"),
            "mediaKind": "video",
            "requiredMedia": True,
            "source": "vimeo-progressive",
            "destPath": f"videos/vimeo-{video_id}.mp4",
        })


for _src_name in ("structure.json", "dom-scaffold.json"):
    _p = ref_dir / _src_name
    if not _p.exists():
        continue
    try:
        _text = _p.read_text()
    except OSError:
        continue
    _add_vimeo_progressive_videos(_text)
    for _u in _ASSET_URL_RE.findall(_text):
        if _is_vimeo_progressive_url(_u):
            continue
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
DEFAULT_ACCEPT = "image/avif,image/webp,image/apng,image/svg+xml,image/*,application/json,video/*,*/*;q=0.8"
JPEG_ACCEPT = "image/jpeg,image/*;q=0.9,*/*;q=0.8"


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
    accept_header = JPEG_ACCEPT if dest.suffix.lower() in (".jpg", ".jpeg") else DEFAULT_ACCEPT
    command = [
        "curl", "-sSL", "--fail-with-body",
        "--max-time", "15", "--retry", "3", "--retry-delay", "1",
        "-A", USER_AGENT,
        "-H", f"Accept: {accept_header}",
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
        custom_dest = _safe_dest_path(item.get("destPath"))
    elif isinstance(item, str):
        url = item
        custom_dest = None
    else:
        continue
    # Resolve root-relative DOM srcs (/cdn-cgi/…, /images/…) against the origin.
    if url.startswith("/") and not url.startswith("//") and referrer_url:
        url = urljoin(referrer_url, url)
    if not url or not url.startswith(("http://", "https://", "//")):
        continue
    if url.startswith("//"):
        url = "https:" + url
    unique_key = f"{url}\n{custom_dest.as_posix() if custom_dest else ''}"
    if unique_key in unique_urls:
        continue
    unique_urls.add(unique_key)

    parsed = urlparse(url)
    # Path component → local file under impl/public/, stripped of /cdn-cgi/image/ prefix
    if custom_dest:
        raw_path = custom_dest.as_posix()
    else:
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
