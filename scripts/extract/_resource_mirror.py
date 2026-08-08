"""Mirror browser-observed resources into a reference artifact directory.

This helper backs ``scripts/extract/resource-mirror.sh``. It intentionally
uses only Python stdlib so the core plugin does not gain a Playwright/runtime
dependency. The shell wrapper gathers URLs from a live agent-browser session;
this module deduplicates, filters, downloads bounded bodies, and writes a
schema-stable ``resource-manifest.json``.

The mirror is extraction evidence only. Downstream implementation code should
still use the normal asset-transfer gates and rendered parity checks rather than
serve the mirrored tree as a proxy/static copy of the reference site.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

JsonObject = dict[str, Any]

_SOURCE = "scripts/extract/resource-mirror.sh"
_RESOURCE_ROOT = "resources"
_DEFAULT_MAX_BYTES = 25 * 1024 * 1024
_DEFAULT_MAX_RESOURCES = 300


def _manifest_status(
    rows: list[dict], attempted: int
) -> tuple[str, list[str]]:
    """Decide the mirror's status from its own rows.

    `max-resources-reached` is a coverage hole, not a policy decision: the
    candidates behind the cap are ordinary same-origin assets that simply lost a
    race against an arbitrary constant. Reporting `pass` there hides the loss
    until it resurfaces as an image-fidelity defect several phases downstream,
    where the cap is no longer visible. Policy skips (external-origin,
    non-mirrorable-type, unsupported-content-type) are deliberate and stay clean.
    """
    status = "pass"
    reasons: list[str] = []
    if attempted == 0:
        status = "warn"
        reasons.append("no mirrorable resources attempted")
    failed = [row for row in rows if row.get("status") == "failed"]
    if failed:
        status = "warn"
        reasons.append(f"{len(failed)} resource(s) failed to download")
    capped = [row for row in rows if row.get("reason") == "max-resources-reached"]
    if capped:
        status = "warn"
        reasons.append(
            f"{len(capped)} resource(s) dropped at the maxResources cap — raise "
            "UI_CLONE_RESOURCE_MIRROR_MAX_RESOURCES to mirror them"
        )
    return status, reasons
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_CONTENT_TYPE_EXTENSIONS: dict[str, str] = {
    "text/css": ".css",
    "text/javascript": ".js",
    "application/javascript": ".js",
    "application/x-javascript": ".js",
    "application/json": ".json",
    "application/manifest+json": ".json",
    "application/wasm": ".wasm",
    "image/avif": ".avif",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
    "font/otf": ".otf",
    "font/ttf": ".ttf",
    "font/woff": ".woff",
    "font/woff2": ".woff2",
    "application/font-woff": ".woff",
    "application/font-woff2": ".woff2",
    "application/vnd.ms-fontobject": ".eot",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
}

_EXTENSION_KIND: dict[str, str] = {
    ".css": "stylesheet",
    ".js": "script",
    ".mjs": "script",
    ".cjs": "script",
    ".json": "data",
    ".lottie": "data",
    ".wasm": "wasm",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".avif": "image",
    ".svg": "image",
    ".ico": "image",
    ".woff": "font",
    ".woff2": "font",
    ".ttf": "font",
    ".otf": "font",
    ".eot": "font",
    ".mp4": "media",
    ".webm": "media",
    ".mov": "media",
    ".m4v": "media",
    ".mp3": "media",
    ".ogg": "media",
    ".wav": "media",
}

_MIRRORABLE_KINDS = {"stylesheet", "script", "image", "font", "media", "data", "wasm"}
_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def unwrap_agent_browser(raw: Any) -> Any:
    """Unwrap the common agent-browser JSON result envelopes.

    agent-browser builds vary: some print a raw JSON string, some print
    ``{"data":{"result": ...}}``, and ``eval`` often double-encodes returned
    strings. We unwrap conservatively so tests and shell callers can pass either
    shape.
    """
    value: Any = raw.strip() if isinstance(raw, str) else raw
    if not value:
        raise ValueError("empty resource payload")
    for _ in range(8):
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("empty resource payload")
            try:
                value = json.loads(stripped)
                continue
            except json.JSONDecodeError:
                return value
        if isinstance(value, dict):
            data = value.get("data")
            if isinstance(data, dict) and "result" in data:
                value = data["result"]
                continue
            if "result" in value and len(value) <= 3:
                value = value["result"]
                continue
        return value
    return value


def load_payload(path: Path) -> Any:
    raw = path.read_text(encoding="utf-8", errors="replace")
    payload = unwrap_agent_browser(raw)
    if isinstance(payload, str):
        payload = unwrap_agent_browser(payload)
    return payload


def _strip_fragment(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def _origin(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return parsed.scheme.lower(), parsed.netloc.lower()


def is_same_origin(source_url: str, resource_url: str) -> bool:
    if not source_url:
        return True
    return _origin(source_url) == _origin(resource_url)


def _content_type_base(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _extension_from_url(url: str) -> str:
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    return suffix if suffix in _EXTENSION_KIND else ""


def resource_kind(url: str, initiator_type: str = "", content_type: str = "") -> str:
    """Classify a browser resource into the mirror's evidence buckets."""
    ct = _content_type_base(content_type)
    if ct:
        if ct in _CONTENT_TYPE_EXTENSIONS:
            ext = _CONTENT_TYPE_EXTENSIONS[ct]
            return _EXTENSION_KIND.get(ext, "data")
        if ct.startswith("image/"):
            return "image"
        if ct.startswith("font/"):
            return "font"
        if ct.startswith(("video/", "audio/")):
            return "media"
        if "javascript" in ct:
            return "script"
        if ct.endswith("+json"):
            return "data"

    ext = _extension_from_url(url)
    if ext:
        return _EXTENSION_KIND[ext]

    initiator = initiator_type.lower()
    if initiator in {"script"}:
        return "script"
    if initiator in {"css", "link"}:
        return "stylesheet"
    if initiator in {"img", "image", "css-image", "favicon"}:
        return "image"
    if initiator in {"video", "audio", "source"}:
        return "media"
    if initiator in {"fetch", "xmlhttprequest"} and re.search(r"lottie|bodymovin|\.json", url, re.I):
        return "data"
    return "other"


def is_mirrorable(url: str, initiator_type: str = "", content_type: str = "") -> bool:
    return resource_kind(url, initiator_type, content_type) in _MIRRORABLE_KINDS


def normalize_candidates(payload: Any, source_url: str = "") -> list[JsonObject]:
    """Return deduplicated URL candidates from a wrapper payload."""
    resources: Any
    if isinstance(payload, dict):
        resources = payload.get("resources", payload.get("entries", payload.get("urls", [])))
        if source_url == "" and isinstance(payload.get("sourceUrl"), str):
            source_url = payload["sourceUrl"]
    else:
        resources = payload
    if not isinstance(resources, list):
        return []

    seen: set[str] = set()
    out: list[JsonObject] = []
    for index, item in enumerate(resources):
        if isinstance(item, str):
            raw_url = item
            entry: JsonObject = {"source": "list"}
        elif isinstance(item, dict):
            raw = item.get("url") or item.get("name") or item.get("href") or item.get("src")
            raw_url = raw if isinstance(raw, str) else ""
            entry = dict(item)
        else:
            continue
        if not raw_url:
            continue
        absolute = urljoin(source_url, raw_url)
        absolute = _strip_fragment(absolute)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        initiator = str(entry.get("initiatorType") or entry.get("type") or entry.get("source") or "")
        content_type = str(entry.get("contentType") or "")
        out.append(
            {
                "url": absolute,
                "initiatorType": initiator,
                "contentTypeHint": content_type,
                "kind": resource_kind(absolute, initiator, content_type),
                "source": str(entry.get("source") or "browser-resource"),
                "index": index,
            }
        )
    return out


def payload_source_url(payload: Any) -> str:
    """Return the browser page URL embedded in a resource payload, if present."""
    if not isinstance(payload, dict):
        return ""
    value = payload.get("sourceUrl") or payload.get("url") or payload.get("pageUrl")
    return value.strip() if isinstance(value, str) else ""


def _safe_segment(raw: str, fallback: str) -> str:
    value = _SAFE_SEGMENT_RE.sub("-", raw).strip(".-_")
    if not value:
        value = fallback
    if value.upper() in _WINDOWS_RESERVED:
        value = f"_{value}"
    return value[:96]


def _extension_for(url: str, content_type: str) -> str:
    ext = _extension_from_url(url)
    if ext:
        return ext
    return _CONTENT_TYPE_EXTENSIONS.get(_content_type_base(content_type), "")


def local_resource_path(
    resource_url: str,
    content_type: str,
    used_paths: set[str] | None = None,
) -> Path:
    """Map a URL to ``resources/<host>/<path>`` safely and deterministically."""
    parsed = urlparse(resource_url)
    host = _safe_segment(parsed.netloc.replace(":", "_"), "unknown-host")
    raw_path = unquote(parsed.path or "/")
    normalized = posixpath.normpath(raw_path)
    if normalized in {".", "/"}:
        parts: list[str] = []
    else:
        parts = [p for p in normalized.strip("/").split("/") if p and p != ".."]

    safe_parts = [_safe_segment(part, "segment") for part in parts]
    filename = safe_parts.pop() if safe_parts else "index"
    ext = _extension_for(resource_url, content_type)
    current_ext = Path(filename).suffix.lower()
    if ext and current_ext != ext:
        filename = f"{filename}{ext}" if not current_ext else f"{Path(filename).stem}{ext}"
    if parsed.query:
        digest = hashlib.sha1(parsed.query.encode("utf-8")).hexdigest()[:8]
        stem = Path(filename).stem or "resource"
        suffix = Path(filename).suffix
        filename = f"{stem}-{digest}{suffix}"

    rel = Path(_RESOURCE_ROOT) / host / Path(*safe_parts) / filename
    if used_paths is None:
        return rel

    candidate = rel
    counter = 2
    while candidate.as_posix() in used_paths:
        stem = candidate.stem
        suffix = candidate.suffix
        candidate = candidate.with_name(f"{stem}-{counter}{suffix}")
        counter += 1
    used_paths.add(candidate.as_posix())
    return candidate


def _failure_record(candidate: JsonObject, status: str, reason: str) -> JsonObject:
    return {
        "url": candidate.get("url", ""),
        "kind": candidate.get("kind", "other"),
        "initiatorType": candidate.get("initiatorType", ""),
        "status": status,
        "reason": reason,
    }


def download_candidate(
    candidate: JsonObject,
    ref_dir: Path,
    used_paths: set[str],
    *,
    timeout: float = 20.0,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> JsonObject:
    """Download one candidate and return its manifest row."""
    url = str(candidate.get("url") or "")
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-supplied research URL
            status_code = int(getattr(response, "status", 200) or 200)
            headers = response.headers
            content_type = headers.get("Content-Type", "")
            kind = resource_kind(url, str(candidate.get("initiatorType") or ""), content_type)
            if kind not in _MIRRORABLE_KINDS:
                return _failure_record(candidate, "skipped", "unsupported-content-type")
            content_length_raw = headers.get("Content-Length", "")
            try:
                content_length = int(content_length_raw) if content_length_raw else 0
            except ValueError:
                content_length = 0
            if content_length > max_bytes:
                row = _failure_record(candidate, "skipped", "content-length-over-limit")
                row["contentLength"] = content_length
                return row

            rel_path = local_resource_path(url, content_type, used_paths)
            dest = ref_dir / rel_path
            tmp_dest = dest.with_suffix(dest.suffix + ".tmp")
            dest.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            try:
                with tmp_dest.open("wb") as fh:
                    while True:
                        chunk = response.read(1024 * 64)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("body-over-limit")
                        fh.write(chunk)
                tmp_dest.replace(dest)
            except Exception:
                tmp_dest.unlink(missing_ok=True)
                raise
            return {
                "url": url,
                "kind": kind,
                "initiatorType": candidate.get("initiatorType", ""),
                "status": "downloaded",
                "httpStatus": status_code,
                "contentType": _content_type_base(content_type),
                "bytes": total,
                "path": rel_path.as_posix(),
            }
    except HTTPError as exc:
        row = _failure_record(candidate, "failed", "http-error")
        row["httpStatus"] = exc.code
        row["error"] = str(exc)
        return row
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        row = _failure_record(candidate, "failed", type(exc).__name__)
        row["error"] = str(exc)
        return row


def write_manifest(ref_dir: Path, payload: JsonObject) -> None:
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "resource-manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def mirror_resources(
    payload: Any,
    ref_dir: Path,
    *,
    source_url: str = "",
    include_external: bool = True,
    max_resources: int = _DEFAULT_MAX_RESOURCES,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    timeout: float = 20.0,
    captured_at: str | None = None,
) -> JsonObject:
    """Download mirrorable resources and write ``resource-manifest.json``."""
    ref_dir = Path(ref_dir)
    effective_source_url = source_url or payload_source_url(payload)
    candidates = normalize_candidates(payload, effective_source_url)
    used_paths: set[str] = set()
    rows: list[JsonObject] = []
    attempted = 0

    for candidate in candidates:
        if len([r for r in rows if r.get("status") == "downloaded"]) >= max_resources:
            rows.append(_failure_record(candidate, "skipped", "max-resources-reached"))
            continue
        url = str(candidate.get("url") or "")
        if not include_external and not is_same_origin(effective_source_url, url):
            rows.append(_failure_record(candidate, "skipped", "external-origin"))
            continue
        if not is_mirrorable(
            url,
            str(candidate.get("initiatorType") or ""),
            str(candidate.get("contentTypeHint") or ""),
        ):
            rows.append(_failure_record(candidate, "skipped", "non-mirrorable-type"))
            continue
        attempted += 1
        rows.append(
            download_candidate(
                candidate,
                ref_dir,
                used_paths,
                timeout=timeout,
                max_bytes=max_bytes,
            )
        )

    downloaded = [row for row in rows if row.get("status") == "downloaded"]
    skipped = [row for row in rows if row.get("status") == "skipped"]
    failed = [row for row in rows if row.get("status") == "failed"]
    manifest_status, status_reasons = _manifest_status(rows, attempted)
    manifest: JsonObject = {
        "schemaVersion": 1,
        "source": _SOURCE,
        "status": manifest_status,
        "capturedAt": captured_at or _now_iso(),
        "sourceUrl": effective_source_url,
        "resourceRoot": _RESOURCE_ROOT,
        "includeExternal": include_external,
        "limits": {"maxResources": max_resources, "maxBytesPerResource": max_bytes},
        "summary": {
            "candidates": len(candidates),
            "attempted": attempted,
            "downloaded": len(downloaded),
            "skipped": len(skipped),
            "failed": len(failed),
            "bytes": sum(int(row.get("bytes") or 0) for row in downloaded),
        },
        "resources": rows,
        "statusReasons": status_reasons,
        "policy": {
            "defaultSeverity": "advisory",
            "requiredEnv": "UI_CLONE_RESOURCE_MIRROR_REQUIRED=1",
        },
        "note": (
            "Extraction evidence only; do not serve this resources/ tree as the implementation "
            "or as fidelity proof without rendered visual verification."
        ),
    }
    write_manifest(ref_dir, manifest)
    return manifest


def _env_bool(name: str, default: bool) -> bool:
    import os

    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    import os

    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    import os

    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirror browser-observed resources")
    parser.add_argument("ref_dir", help="Reference artifact directory")
    parser.add_argument("payload_file", help="agent-browser resource payload JSON file")
    parser.add_argument("source_url", nargs="?", default="", help="Reference page URL")
    parser.add_argument(
        "--same-origin-only",
        action="store_true",
        help="Skip resources outside source_url origin",
    )
    parser.add_argument(
        "--max-resources",
        type=int,
        default=_env_int("UI_CLONE_RESOURCE_MIRROR_MAX_RESOURCES", _DEFAULT_MAX_RESOURCES),
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=_env_int("UI_CLONE_RESOURCE_MIRROR_MAX_BYTES", _DEFAULT_MAX_BYTES),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_env_float("UI_CLONE_RESOURCE_MIRROR_TIMEOUT", 20.0),
    )
    parser.add_argument(
        "--required",
        action="store_true",
        default=_env_bool("UI_CLONE_RESOURCE_MIRROR_REQUIRED", False),
        help="Exit non-zero when the manifest status is not pass",
    )
    args = parser.parse_args(argv)

    try:
        payload = load_payload(Path(args.payload_file))
        include_external = _env_bool("UI_CLONE_RESOURCE_MIRROR_INCLUDE_EXTERNAL", True)
        if args.same_origin_only:
            include_external = False
        manifest = mirror_resources(
            payload,
            Path(args.ref_dir),
            source_url=args.source_url,
            include_external=include_external,
            max_resources=args.max_resources,
            max_bytes=args.max_bytes,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"resource-mirror: failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"status": manifest["status"], "phase": "resource-mirror", "data": manifest["summary"]}))
    if args.required and manifest["status"] != "pass":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
