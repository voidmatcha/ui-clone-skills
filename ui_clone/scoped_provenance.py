"""Implementation provenance for scoped clones.

The capture manifest proves which page an element-scope frame came from; it
does not prove that the page was rendered from generated source. A local
proxy of the reference site, an `<iframe>` of it, or a component that loads
the reference bundles renders pixel-identical frames from a non-reference
origin. The page-level pipeline has no-cheat gates for that
(`skills/visual-debug/scripts/proxy-mirror-check.sh`, `bundle-paste-check.sh`,
`html-paste-check.sh`, `css-mirror-check.sh`); this module brings the ones
that work without page-level artifacts to the scoped path:

* at capture time (`element-state-capture.sh` -> `ui_clone.element_capture`)
  the implementation page's loaded resources (Performance resource entries
  plus script/link/iframe/img/media element URLs) must not include the
  reference host or any of its subdomains; the origins are recorded per
  frame in `capture-manifest.json` and re-checked by `scoped_check`;
* at diff time (`python -m ui_clone.scoped_diff`) the page-level
  `proxy-mirror-check.sh` and `bundle-paste-check.sh` run against the
  implementation root (both work on implementation sources alone), and the
  fingerprinted sources plus the implementation entry files are scanned for
  reference-host loads, whole-document mirrors, raw HTML mounts, and
  upstream proxies. Results, output hashes, and findings go into
  `pixel-perfect-diff.json`; `scoped_check` re-hashes the outputs and re-runs
  the source scan.

`html-paste-check.sh` and `css-mirror-check.sh` are not run: their rules need
`dom-scaffold.json`, `bundle-map.json`, and `bundles/*.css`, which a scoped
run never produces, so they would pass vacuously.

Producer records: every manifest entry and the diff artifact carry
`producer` (`entry` cli|api, the argv the CLI saw, the sha256 of the producer
module, and for captures the driver script's sha256) and the diff carries a
self checksum. Threat model: a lazy or over-eager agent reaching for the
natural tools (a shell one-liner, an inline Python import, a hand-edited
JSON). None of this is a cryptographic proof; a determined agent that
rewrites the producers or forges the records with the scheme in hand is out
of scope, as it is for every other hook-protected artifact in this repo.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER_SCRIPT = REPO_ROOT / "scripts" / "extract" / "element-state-capture.sh"
DRIVER_ENV = "UI_CLONE_CAPTURE_DRIVER"
CHECKS_DIR = REPO_ROOT / "skills" / "visual-debug" / "scripts"
# Page-level no-cheat checks that judge implementation sources alone:
# check name -> output file the script writes under <ref-dir>/.
PAGE_LEVEL_CHECKS: dict[str, str] = {
    "proxy-mirror-check": "proxy-mirror-check.json",
    "bundle-paste-check": "bundle-paste-check.json",
}
# Implementation entry files scanned (and fingerprinted) in addition to the
# component sources: a reference `<iframe>` or `<script src>` is as likely to
# sit in the app shell as in the component named after the target.
ENTRY_FILES = (
    "index.html",
    "public/index.html",
    "src/index.html",
    "app/page.tsx",
    "app/page.jsx",
    "app/layout.tsx",
    "app/layout.jsx",
    "pages/index.tsx",
    "pages/index.jsx",
    "pages/_document.tsx",
    "pages/_app.tsx",
    "src/app/page.tsx",
    "src/app/layout.tsx",
    "src/App.tsx",
    "src/App.jsx",
    "src/App.vue",
    "src/App.svelte",
    "src/main.tsx",
    "src/main.ts",
    "src/main.jsx",
    "src/index.tsx",
    "src/index.jsx",
    "src/index.css",
    "src/globals.css",
    "app/globals.css",
    "server.js",
    "server.mjs",
    "proxy.js",
    "proxy.mjs",
    "vite.config.ts",
    "vite.config.js",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
)
_MAX_MATCH = 80


# ── reference-host matching ───────────────────────────────────────────────


def _host(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed.hostname.lower()


def reference_base_host(reference_origin: str) -> str:
    """`example.org` for `https://www.example.org:443` (a leading `www.` is dropped)."""
    host = _host(reference_origin) or ""
    return host[4:] if host.startswith("www.") else host


def host_matches_reference(host: str | None, reference_origin: str) -> bool:
    """True for the reference host itself and any subdomain of its base host."""
    if not host:
        return False
    base = reference_base_host(reference_origin)
    if not base:
        return False
    host = host.lower()
    return host == base or host.endswith("." + base)


# Resource kinds an implementation page may load from the reference host:
# media and fonts (AGENTS.md "Source fidelity" keeps the original asset URLs)
# and the CSS-initiated / preload / icon requests that fetch them. A code
# resource is recorded under `script` / `stylesheet` whatever initiated it
# (`resource_kind`), so a `<link rel=preload as=script>` or an `@import` of a
# reference bundle is never hidden behind one of these kinds.
MEDIA_KINDS = frozenset(
    {
        "img",
        "image",
        "picture",
        "video",
        "audio",
        "source",
        "track",
        "font",
        "css",
        "link",
        "preload",
        "prefetch",
        "icon",
        "apple-touch-icon",
        "dns-prefetch",
        "preconnect",
    }
)


def resource_kind(url: object, kind: object, content_type: object = None) -> str:
    """The kind recorded for a loaded resource: `script` / `stylesheet` for
    any JavaScript / CSS (by kind, extension, or response content type), else
    the reported kind."""
    raw = str(kind or "other").lower()
    if not is_code_resource(url, raw, content_type):
        return raw
    if raw == "stylesheet":
        return "stylesheet"
    if raw in CODE_KINDS:
        return "script"
    if _content_type_class(content_type) == "stylesheet":
        return "stylesheet"
    try:
        ext = os.path.splitext(urlparse(str(url)).path)[1].lower()
    except ValueError:
        ext = ""
    return "stylesheet" if ext == ".css" else "script"


def resource_origins(resources: object) -> dict[str, list[str]] | None:
    """Compact `{origin: [kinds]}` map of a probe's `resources` list, None when malformed."""
    if not isinstance(resources, list):
        return None
    out: dict[str, set[str]] = {}
    for item in resources:
        if not isinstance(item, dict):
            return None
        url = item.get("url")
        kind = item.get("kind")
        content_type = item.get("contentType")
        try:
            parsed = urlparse(url) if isinstance(url, str) else None
            port = parsed.port if parsed is not None else None
        except ValueError:
            continue
        if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        origin = f"{parsed.scheme}://{parsed.hostname.lower()}:{port}"
        out.setdefault(origin, set()).add(resource_kind(url, kind, content_type))
    return {origin: sorted(kinds) for origin, kinds in sorted(out.items())}


def reference_loads(origins: object, reference_origin: str) -> list[str]:
    """Reference-site origins in a `resourceOrigins` map that served anything
    other than media / fonts (a script, stylesheet, iframe, fetch/XHR, or
    document: the reference runtime rather than its assets)."""
    if not isinstance(origins, dict):
        return []
    hits: list[str] = []
    for origin, kinds in origins.items():
        if not host_matches_reference(_host(origin), reference_origin):
            continue
        if not isinstance(kinds, list):
            hits.append(f"{origin} (?)")
            continue
        runtime = [str(k) for k in kinds if str(k).lower() not in MEDIA_KINDS]
        if runtime:
            hits.append(f"{origin} ({','.join(runtime)})")
    return hits


# ── reference code (script / stylesheet) hotlinks ─────────────────────────
#
# AGENTS.md "Source fidelity" requires preserving the reference's asset URLs
# (images, video, Lottie, fonts), so media and font hotlinks from any host are
# allowed. Its JavaScript and CSS bundles are the reference runtime: an
# implementation that loads them — from the reference host or from the CDN
# that serves them (a Contentful / CloudFront / jsDelivr host) — is a proxy of
# the site, not a clone. The reference capture inventories the code resources
# its page loaded; an impl capture is refused, and scoped_check fails, when
# the impl page loads a code resource whose normalized URL is in that
# inventory or whose origin is a non-first-party origin that served
# reference code. Code resources that the reference never loaded (an
# analytics tag, a locally hosted library) are not affected.

# Resource kinds (Performance `initiatorType` or the DOM element/rel that
# collectResources tags) that are code regardless of the URL's extension.
CODE_KINDS = frozenset({"script", "stylesheet", "modulepreload"})
# Extensions that make any resource code (a `css`/`link`/`preload`/`fetch`/
# `other` entry may be a font, an image, or a bundle chunk).
CODE_EXTENSIONS = frozenset({".js", ".mjs", ".cjs", ".css"})
# Content hashes bundlers put into file names (`main.3f2a1b9c.js`,
# `chunk-A1b2C3d4.css`): collapsed so a re-deployed reference bundle still
# matches its inventory entry. Only long hex/base62 runs inside the file
# name are touched; `bootstrap.min.js` is not a hash.
_HASH_SEGMENT_RE = re.compile(
    r"(?<![^._-])(?:[0-9a-f]{8,}|(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{8,})(?![^._-])"
)


def normalize_code_url(url: object) -> str | None:
    """Canonical form of an http(s) resource URL for the code inventory:
    lowercase scheme/host, default port dropped, query and fragment stripped,
    content-hash segments of the file name collapsed to `[hash]`."""
    if not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    default = 443 if parsed.scheme == "https" else 80
    authority = host if port is None or port == default else f"{host}:{port}"
    path = parsed.path or "/"
    head, _, name = path.rpartition("/")
    name = _HASH_SEGMENT_RE.sub("[hash]", name)
    return f"{parsed.scheme}://{authority}{head}/{name}"


# Font services deliver @font-face stylesheets. Loading the reference's font
# CSS is how a clone keeps its original fonts (AGENTS.md "Source fidelity"),
# so these hosts are font assets, not the reference runtime.
FONT_STYLESHEET_HOSTS = frozenset(
    {
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        "use.typekit.net",
        "p.typekit.net",
        "fonts.bunny.net",
        "fast.fonts.net",
        "cloud.typography.com",
        "use.fontawesome.com",
    }
)


# Response content types that make a resource code whatever its URL or
# initiator: a bundle chunk the page pulled through `fetch()` / a dynamic
# `import()` (`initiatorType` fetch / other) from an extensionless URL. The
# capture eval reads `PerformanceResourceTiming.contentType` (Chromium; empty
# where unsupported) so no extra request is made and nothing is guessed.
_CONTENT_TYPE_CLASSES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:application|text)/(?:x-)?(?:javascript|ecmascript|jscript)$"), "script"),
    (re.compile(r"^application/(?:x-)?(?:wasm|es6|module)$"), "script"),
    (re.compile(r"^text/css$"), "stylesheet"),
)


def _content_type_class(content_type: object) -> str | None:
    if not isinstance(content_type, str) or not content_type:
        return None
    essence = content_type.split(";", 1)[0].strip().lower()
    for pattern, klass in _CONTENT_TYPE_CLASSES:
        if pattern.match(essence):
            return klass
    return None


def is_code_resource(url: object, kind: object, content_type: object = None) -> bool:
    """True when a loaded resource is JavaScript or CSS (by kind, extension,
    or the response content type the capture recorded).

    Stylesheets from font services are font assets and never count as code.
    """
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    ext = os.path.splitext(parsed.path)[1].lower()
    kind_name = kind.lower() if isinstance(kind, str) else ""
    by_type = _content_type_class(content_type)
    is_script = (
        kind_name in {"script", "modulepreload"}
        or ext in {".js", ".mjs", ".cjs"}
        or by_type == "script"
    )
    if (parsed.hostname or "").lower() in FONT_STYLESHEET_HOSTS and not is_script:
        return False
    if kind_name in CODE_KINDS or by_type is not None:
        return True
    return ext in CODE_EXTENSIONS


def code_resources(resources: object) -> dict[str, list[str]] | None:
    """`{normalized url: [kinds]}` of the code resources in a probe's
    `resources` list (`[{url, kind}]`), None when the list is malformed."""
    if not isinstance(resources, list):
        return None
    out: dict[str, set[str]] = {}
    for item in resources:
        if not isinstance(item, dict):
            return None
        url = item.get("url")
        kind = item.get("kind")
        if not is_code_resource(url, kind, item.get("contentType")):
            continue
        normalized = normalize_code_url(url)
        if normalized is None:
            continue
        out.setdefault(normalized, set()).add(str(kind or "other"))
    return {key: sorted(kinds) for key, kinds in sorted(out.items())}


def merge_code_resources(*maps: object) -> dict[str, list[str]]:
    """Union of code inventories (a side's manifest accumulates every capture)."""
    out: dict[str, set[str]] = {}
    for item in maps:
        if not isinstance(item, dict):
            continue
        for url, kinds in item.items():
            if not isinstance(url, str):
                continue
            bucket = out.setdefault(url, set())
            if isinstance(kinds, list):
                bucket.update(str(k) for k in kinds)
    return {key: sorted(kinds) for key, kinds in sorted(out.items())}


def _origin_of_normalized(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def code_origins(inventory: object) -> set[str]:
    """Origins (`scheme://authority`, default port dropped) of a code inventory."""
    if not isinstance(inventory, dict):
        return set()
    found: set[str] = set()
    for url in inventory:
        origin = _origin_of_normalized(str(url))
        if origin is not None:
            found.add(origin)
    return found


def _canonical_origin(origin: object) -> str | None:
    """`scheme://host[:port]` with the default port dropped (a `resourceOrigins`
    key always carries the port; a normalized code URL never carries a default one)."""
    if not isinstance(origin, str):
        return None
    return _origin_of_normalized(normalize_code_url(origin + "/") or "")


def reference_code_hits(
    impl_code: object,
    impl_page_url: object,
    reference_code: object,
    impl_origins: object = None,
) -> list[str]:
    """Resources an impl page loaded that belong to the reference runtime.

    A hit is a code resource whose normalized URL is in the reference
    inventory, a code resource served by an origin that served reference code
    and is not the impl page's own origin (its first party), or — given the
    page's `resourceOrigins` map — ANY non-media load (fetch/XHR, `other`,
    iframe, document) from such an origin: an extensionless bundle chunk the
    page pulls through `fetch()` or a dynamic `import()` has no code
    extension, so the origin that serves the reference code is the signal.
    Media and fonts never hit (images, video, fonts, and CSS-initiated asset
    requests are `MEDIA_KINDS`).
    """
    if not isinstance(impl_code, dict) or not isinstance(reference_code, dict):
        return []
    first_party = _origin_of_normalized(normalize_code_url(impl_page_url) or "")
    shared_origins = code_origins(reference_code) - ({first_party} if first_party else set())
    hits: list[str] = []
    for url, kinds in impl_code.items():
        if not isinstance(url, str):
            continue
        shown = ",".join(kinds) if isinstance(kinds, list) else "?"
        if url in reference_code:
            hits.append(f"{url} ({shown}; reference code)")
            continue
        origin = _origin_of_normalized(url)
        if origin is not None and origin in shared_origins:
            hits.append(f"{url} ({shown}; {origin} serves reference code)")
    if isinstance(impl_origins, dict):
        for raw_origin, kinds in impl_origins.items():
            origin = _canonical_origin(raw_origin)
            if origin is None or origin not in shared_origins or not isinstance(kinds, list):
                continue
            runtime = sorted(str(k) for k in kinds if str(k).lower() not in MEDIA_KINDS)
            if runtime:
                hits.append(
                    f"{origin} ({','.join(runtime)}; non-media load from an origin that serves reference code)"
                )
    return hits


# ── implementation source scan ────────────────────────────────────────────


def _url_rule(reference_origin: str) -> re.Pattern[str] | None:
    base = reference_base_host(reference_origin)
    if not base:
        return None
    host = rf"(?:[a-z0-9-]+\.)*{re.escape(base)}(?=[/:\"'`\s)?#]|$)"
    url = rf"https?://{host}"
    return re.compile(
        rf"(?:(?:src|href|action|srcset|poster|data)\s*=\s*[\"'{{]?\s*[\"'`]?{url}"
        rf"|url\(\s*[\"']?{url}"
        rf"|@import\s+(?:url\()?\s*[\"']?{url}"
        rf"|fetch\(\s*[\"'`]{url}"
        rf"|from\s+[\"']{url}"
        rf"|import\(\s*[\"'`]{url}"
        rf"|new\s+URL\(\s*[\"'`]{url})",
        re.IGNORECASE,
    )


_DOCUMENT_MIRROR_RE = re.compile(r"document\.documentElement\.outerHTML", re.IGNORECASE)
_RAW_HTML_RE = re.compile(r"\.html?\?raw[\"'`]", re.IGNORECASE)
_INNER_HTML_MOUNT_RE = re.compile(r"dangerouslySetInnerHTML|\.innerHTML\s*=", re.IGNORECASE)
_UPSTREAM_PROXY_RE = re.compile(
    r"http-proxy|createProxyMiddleware|proxyAndCache|fetch\(\s*upstream|\bupstream\s*=\s*[\"']https?://",
    re.IGNORECASE,
)
_SKIP_DIRS = frozenset(
    {"node_modules", ".git", ".next", "dist", "build", "out", ".turbo", ".cache"}
)


def entry_sources(impl_root: Path | None) -> list[Path]:
    """Existing ENTRY_FILES under the implementation root and under its
    conventional `impl/` subdirectory (the page-level resolver's first
    convention candidate when the project root holds `tmp/ref/`)."""
    if impl_root is None or not impl_root.is_dir():
        return []
    found: list[Path] = []
    for base in (impl_root, impl_root / "impl"):
        found.extend(base / rel for rel in ENTRY_FILES if (base / rel).is_file())
    return found


def scan_sources(paths: list[Path], reference_origin: str) -> list[dict[str, str]]:
    """Reference-runtime signals in implementation sources.

    Rules: `reference-load` (a load position — src/href/url()/@import/fetch/
    import — naming the reference host or a subdomain), `document-mirror`
    (`document.documentElement.outerHTML`), `raw-html-mount` (an `?raw` HTML
    import mounted through innerHTML/dangerouslySetInnerHTML), and
    `upstream-proxy` (http-proxy / createProxyMiddleware / `upstream =` URL).
    """
    url_rule = _url_rule(reference_origin)
    findings: list[dict[str, str]] = []
    for path in paths:
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path)
        if url_rule is not None:
            m = url_rule.search(text)
            if m:
                findings.append(
                    {"file": rel, "rule": "reference-load", "match": m.group(0)[:_MAX_MATCH]}
                )
        m = _DOCUMENT_MIRROR_RE.search(text)
        if m:
            findings.append(
                {"file": rel, "rule": "document-mirror", "match": m.group(0)[:_MAX_MATCH]}
            )
        raw = _RAW_HTML_RE.search(text)
        if raw and _INNER_HTML_MOUNT_RE.search(text):
            findings.append(
                {"file": rel, "rule": "raw-html-mount", "match": raw.group(0)[:_MAX_MATCH]}
            )
        m = _UPSTREAM_PROXY_RE.search(text)
        if m:
            findings.append(
                {"file": rel, "rule": "upstream-proxy", "match": m.group(0)[:_MAX_MATCH]}
            )
    return findings


# ── page-level no-cheat scripts ───────────────────────────────────────────


def run_page_level_checks(ref_dir: Path, impl_root: Path | None) -> dict[str, dict[str, Any]]:
    """Run the applicable page-level no-cheat scripts against `impl_root`.

    Each entry: `status` (`pass`|`fail`|`skip`|`error`), `output` (path
    relative to `ref_dir`), `findings` (count), `exit`.
    """
    results: dict[str, dict[str, Any]] = {}
    for name, output in PAGE_LEVEL_CHECKS.items():
        script = CHECKS_DIR / f"{name}.sh"
        entry: dict[str, Any] = {"output": output, "status": "error", "findings": 0, "exit": None}
        if impl_root is None or not impl_root.is_dir():
            entry["status"] = "skip"
            entry["reason"] = "impl root not found"
            results[name] = entry
            continue
        if not script.is_file():
            entry["reason"] = f"{script} missing"
            results[name] = entry
            continue
        try:
            proc = subprocess.run(
                ["bash", str(script), str(ref_dir), str(impl_root)],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                env={**os.environ, "PLUGIN_ROOT": str(REPO_ROOT)},
            )
            entry["exit"] = proc.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            entry["reason"] = str(exc)[:_MAX_MATCH]
            results[name] = entry
            continue
        data = _load_json(ref_dir / output)
        if data is None:
            entry["reason"] = "output missing or unreadable"
        else:
            status = data.get("status")
            entry["status"] = status if isinstance(status, str) else "error"
            items = (
                data.get("findings")
                if isinstance(data.get("findings"), list)
                else data.get("violations")
            )
            entry["findings"] = len(items) if isinstance(items, list) else 0
        results[name] = entry
    return results


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ── producer records ──────────────────────────────────────────────────────


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError:
        return None


def module_sha256(module: str) -> str | None:
    """sha256 of a `ui_clone` module's source file (the producer's own code)."""
    try:
        spec = importlib.import_module(module).__spec__
    except ImportError:
        return None
    origin = getattr(spec, "origin", None)
    return file_sha256(Path(origin)) if isinstance(origin, str) else None


def driver_script_sha256() -> str | None:
    """sha256 of the shipped element-state-capture.sh."""
    return file_sha256(DRIVER_SCRIPT)


def driver_record() -> dict[str, Any] | None:
    """`{path, sha256}` of the capture script that set `UI_CLONE_CAPTURE_DRIVER`."""
    raw = os.environ.get(DRIVER_ENV)
    if not raw:
        return None
    path = Path(raw)
    digest = file_sha256(path)
    if digest is None:
        return None
    return {"path": str(path), "sha256": digest}


def producer_record(module: str, entry: str, argv: list[str] | None) -> dict[str, Any]:
    return {
        "entry": entry,
        "argv": list(argv) if argv is not None else list(sys.argv[1:]),
        "moduleSha256": module_sha256(module),
    }


def record_checksum(data: dict[str, Any], key: str = "recordSha256") -> str:
    """Self checksum over the canonical JSON of `data` without `key`."""
    body = {k: v for k, v in data.items() if k != key}
    return sha256_bytes(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
