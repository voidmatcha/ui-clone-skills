# Python 3.9 compat for PEP 604 unions used below — defer
# annotation evaluation so `X | Y` is parsed as a string.
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ref_dir = Path(sys.argv[1])
out_path = Path(sys.argv[2])

JsonObject = dict[str, Any]

videos: list[JsonObject] = []
lottie: list[JsonObject] = []
svgs: list[JsonObject] = []


def read_safe(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def url_ext(u: str) -> str:
    """Return the lowercase file extension of a URL, stripping any
    `?` querystring or `#` fragment first. Audit FN #12 — prior
    extension checks used `str.endswith(".svg")` which failed on
    cache-busted URLs like `asset.svg?v=1` or hash-anchored
    `icons.svg#search`.
    """
    if not isinstance(u, str):
        return ""
    path = u.split("?", 1)[0].split("#", 1)[0]
    dot = path.rfind(".")
    if dot < 0:
        return ""
    return path[dot:].lower()


def url_ext_in(u: str, exts: tuple[str, ...]) -> bool:
    return url_ext(u) in exts


VIDEO_EXT_RE = re.compile(r"\.(?:mp4|webm|mov|m4v|m3u8|mpd)(?:$|[?#])", re.I)


def is_video_url(u: str) -> bool:
    if not isinstance(u, str):
        return False
    if u.startswith(("blob:", "data:")):
        return False
    return bool(VIDEO_EXT_RE.search(u))


# 1. Pull video sources from html/<name>.json.media[].
html_dir = ref_dir / "html"
if html_dir.is_dir():
    for json_path in sorted(html_dir.glob("*.json")):
        section_name = json_path.stem
        try:
            section_data = json.loads(read_safe(json_path))
        except ValueError:
            continue
        # html/<name>.json shape varies by producer:
        #   - dict with "media" key (most extract-section-html.sh outputs)
        #   - bare list of media entries (some producers emit the array
        #     directly; the dict-only path hit AttributeError: 'list'
        #     object has no attribute 'get' before this fallback existed)
        # Accept both. Anything else (None, string, int) → skip the file.
        if isinstance(section_data, dict):
            media_arr = section_data.get("media") or []
        elif isinstance(section_data, list):
            media_arr = section_data
        else:
            continue
        if not isinstance(media_arr, list):
            continue
        # Track video/source pairs so we don't double-emit when an inner
        # <source> appears alongside its parent <video src="">.
        current_video: JsonObject | None = None
        for m in media_arr:
            if not isinstance(m, dict):
                continue
            tag = (m.get("tag") or "").lower()
            src = (m.get("src") or "").strip()
            if tag == "video":
                if src:
                    videos.append({
                        "section": section_name,
                        "src": src,
                        "type": m.get("type") or None,
                        "poster": m.get("poster") or None,
                        "w": m.get("width"),
                        "h": m.get("height"),
                        "autoplay": m.get("autoplay"),
                        "loop": m.get("loop"),
                        "muted": m.get("muted"),
                    })
                current_video = {
                    "section": section_name,
                    "poster": m.get("poster") or None,
                    "w": m.get("width"),
                    "h": m.get("height"),
                    "autoplay": m.get("autoplay"),
                    "loop": m.get("loop"),
                    "muted": m.get("muted"),
                }
            elif tag == "source" and src:
                entry = {
                    "section": section_name,
                    "src": src,
                    "type": m.get("type") or None,
                }
                if current_video:
                    entry.update({
                        "poster": current_video.get("poster"),
                        "w": current_video.get("w"),
                        "h": current_video.get("h"),
                        "autoplay": current_video.get("autoplay"),
                        "loop": current_video.get("loop"),
                        "muted": current_video.get("muted"),
                    })
                videos.append(entry)


# 1b. Pull runtime-created video sources from runtime-media.json.
# Some modern landing pages create <video> nodes only after the JS runtime
# mounts or after scroll/lazy triggers. Section HTML snapshots and static DOM
# captures can legitimately show zero videos while the live reference has
# autoplaying background video. runtime-media.sh writes those live nodes here.
runtime_media_path = ref_dir / "runtime-media.json"
if runtime_media_path.exists():
    try:
        runtime_media = json.loads(read_safe(runtime_media_path))
    except ValueError:
        runtime_media = None
    if isinstance(runtime_media, dict):
        runtime_videos = runtime_media.get("videos") or []
    elif isinstance(runtime_media, list):
        runtime_videos = runtime_media
    else:
        runtime_videos = []
    if isinstance(runtime_videos, list):
        for idx, rv in enumerate(runtime_videos):
            if not isinstance(rv, dict):
                continue
            section = (
                rv.get("section")
                or rv.get("sectionHint")
                or f"runtime-video-{idx}"
            )
            rect_value = rv.get("rect")
            rect: JsonObject = rect_value if isinstance(rect_value, dict) else {}
            base = {
                "section": section,
                "type": rv.get("type") or None,
                "poster": rv.get("poster") or None,
                "w": rv.get("videoWidth") or rect.get("w") or rv.get("w"),
                "h": rv.get("videoHeight") or rect.get("h") or rv.get("h"),
                "autoplay": rv.get("autoplay"),
                "loop": rv.get("loop"),
                "muted": rv.get("muted"),
                "playsInline": rv.get("playsInline"),
                "evidenceFile": "runtime-media.json",
                "evidenceKind": "runtime-video",
            }

            candidates: list[tuple[str, str | None]] = []
            for key in ("currentSrc", "src", "url"):
                value = rv.get(key)
                if isinstance(value, str) and value:
                    candidates.append((value, rv.get("type") or None))
            sources = rv.get("sources") or []
            if isinstance(sources, list):
                for source in sources:
                    if not isinstance(source, dict):
                        continue
                    src = source.get("src")
                    if isinstance(src, str) and src:
                        candidates.append((src, source.get("type") or rv.get("type") or None))
            data_sources = rv.get("dataSources") or []
            if isinstance(data_sources, list):
                for source in data_sources:
                    if not isinstance(source, dict):
                        continue
                    src = source.get("src") or source.get("value")
                    if isinstance(src, str) and src:
                        candidates.append((src, rv.get("type") or None))

            seen_runtime_sources: set[str] = set()
            for src, source_type in candidates:
                src = src.strip()
                if not src or src in seen_runtime_sources or not is_video_url(src):
                    continue
                seen_runtime_sources.add(src)
                entry = dict(base)
                entry["src"] = src
                entry["type"] = source_type or entry.get("type")
                videos.append(entry)


# 2. Lottie path extraction from bundles/*.js.
# Universality audit HIGH FN: prior pattern assumed
# bodymovin/lottie.loadAnimation({ path: "..." }). Webpack-mangled
# bundles obscure the method call, and modern Lottie usage includes
# animationData (inline JSON), .lottie containers (dotLottie format),
# and <dotlottie-player src=...> Web Components. Broaden to all
# common forms.
LOTTIE_RE = re.compile(
    r"(?is)\b(?:bodymovin|lottie|Lottie)\s*\.\s*loadAnimation\s*\("
    r"\s*\{(?:(?!\}\s*\)).){0,4000}?"
    r"\b[\"']?path[\"']?\s*:\s*"
    r"([\"'`])"
    r"(?P<url>[^\"'`]+?\.json(?:\?[^\"'`]*)?)"
    r"\1"
)

# Also detect direct Lottie URL references like
#   "/img/lottie/foo.json" or 'https://.../lottie/bar.json'
# inside the bundle (lazier than the full loadAnimation pattern but
# catches obfuscated bundlers that pre-strip the path key).
DIRECT_LOTTIE_RE = re.compile(
    r"[\"']([^\"']*?/(?:lottie|animations|motion)/[^\"']+?\.(?:json|lottie)(?:\?[^\"']*)?)[\"']",
    re.IGNORECASE,
)

# dotLottie format URLs (.lottie binary archive).
DOTLOTTIE_RE = re.compile(
    r"[\"']([^\"']+?\.lottie(?:\?[^\"']*)?)[\"']",
    re.IGNORECASE,
)

# <dotlottie-player src="..." /> or <lottie-player src="..." />
# Web Components — when the bundle stamps these out as strings.
LOTTIE_PLAYER_SRC_RE = re.compile(
    r"(?:dotlottie-player|lottie-player)[^>]{0,200}?\bsrc\s*=\s*"
    r"[\"']([^\"']+?\.(?:json|lottie)(?:\?[^\"']*)?)[\"']",
    re.IGNORECASE,
)

# Webpack-mangled / minified loadAnimation calls — best-effort:
# look for any function call passing an object literal containing
# `path:"<json>"` near a `lottie`/`bodymovin` symbol within 200
# chars (window before the property).
WEBPACK_MANGLED_RE = re.compile(
    r"(?is)(?:lottie|bodymovin|Lottie)\b[^;{}]{0,200}?"
    r"\bpath\s*:\s*[\"']([^\"']+?\.json(?:\?[^\"']*)?)[\"']"
)

bundles_dir = ref_dir / "bundles"
if bundles_dir.is_dir():
    for js_path in sorted(bundles_dir.glob("*.js")):
        text = read_safe(js_path)
        if not text:
            continue
        rel = str(js_path.relative_to(ref_dir))
        seen: set[str] = set()
        for m in LOTTIE_RE.finditer(text):
            url = m.group("url").strip()
            if url and url not in seen:
                lottie.append({
                    "path": url,
                    "evidenceFile": rel,
                    "evidenceKind": "loadAnimation-path",
                    "offset": m.start(),
                })
                seen.add(url)
        for m in DIRECT_LOTTIE_RE.finditer(text):
            url = m.group(1).strip()
            if url and url not in seen and url_ext_in(url, (".json", ".lottie")):
                lottie.append({
                    "path": url,
                    "evidenceFile": rel,
                    "evidenceKind": "direct-lottie-url",
                    "offset": m.start(),
                })
                seen.add(url)
        # Universality audit HIGH FN — webpack-mangled and
        # Web-Component-form Lottie evidence.
        for m in WEBPACK_MANGLED_RE.finditer(text):
            url = m.group(1).strip()
            if url and url not in seen and url_ext_in(url, (".json", ".lottie")):
                lottie.append({
                    "path": url,
                    "evidenceFile": rel,
                    "evidenceKind": "webpack-mangled-loadAnimation",
                    "offset": m.start(),
                })
                seen.add(url)
        for m in LOTTIE_PLAYER_SRC_RE.finditer(text):
            url = m.group(1).strip()
            if url and url not in seen:
                lottie.append({
                    "path": url,
                    "evidenceFile": rel,
                    "evidenceKind": "lottie-player-src",
                    "offset": m.start(),
                })
                seen.add(url)
        for m in DOTLOTTIE_RE.finditer(text):
            url = m.group(1).strip()
            # Only count .lottie URLs that look path-shaped (have /).
            if url and url not in seen and "/" in url and not url.startswith("data:"):
                lottie.append({
                    "path": url,
                    "evidenceFile": rel,
                    "evidenceKind": "dotlottie-archive",
                    "offset": m.start(),
                })
                seen.add(url)


CSS_URL_SVG_RE = re.compile(
    r"url\(\s*[\"']?([^\"'\)\s]+?\.svg(?:\?[^\"'\)\s]*)?)[\"']?\s*\)",
    re.IGNORECASE,
)


def add_svg(*, section: str | None, src: str, kind: str, evidence_file: str | None) -> None:
    if not src:
        return
    src = src.strip()
    if not src or src.startswith("data:"):
        return
    if url_ext(src) != ".svg":
        return
    svgs.append({
        "section": section,
        "src": src,
        "kind": kind,
        "evidenceFile": evidence_file,
    })


if html_dir.is_dir():
    for json_path in sorted(html_dir.glob("*.json")):
        section_name = json_path.stem
        rel = str(json_path.relative_to(ref_dir))
        try:
            section_data = json.loads(read_safe(json_path))
        except ValueError:
            continue

        # 3a. <img src="...svg"> + <use href="...svg"> in media[].
        # Same shape tolerance as the video/lottie scan above —
        # accept dict with "media" key OR a bare media list.
        if isinstance(section_data, dict):
            media_arr = section_data.get("media") or []
            section_styles = section_data.get("styles") or {}
            section_text = section_data.get("html") or ""
        elif isinstance(section_data, list):
            media_arr = section_data
            section_styles = {}
            section_text = ""
        else:
            continue
        if isinstance(media_arr, list):
            for m in media_arr:
                if not isinstance(m, dict):
                    continue
                src = (m.get("src") or "").strip()
                href = (m.get("href") or m.get("xlink:href") or "").strip()
                tag = (m.get("tag") or "").lower()
                if url_ext(src) == ".svg":
                    add_svg(
                        section=section_name, src=src,
                        kind=f"{tag}-src" if tag else "img-src",
                        evidence_file=rel,
                    )
                if url_ext(href) == ".svg":
                    add_svg(
                        section=section_name,
                        src=href.split("#", 1)[0],
                        kind="use-href",
                        evidence_file=rel,
                    )

        # 3b. background-image / mask-image / list-style-image url(...svg)
        # in section + descendant style captures.
        def walk_styles(node: JsonObject, section: str, evidence: str) -> None:
            if not isinstance(node, dict):
                return
            for blob_key in ("styles", "section", "before_styles", "after_styles"):
                style_dict = node.get(blob_key)
                if not isinstance(style_dict, dict):
                    continue
                for css_prop, value in style_dict.items():
                    if not isinstance(value, str) or "url(" not in value:
                        continue
                    for url in CSS_URL_SVG_RE.findall(value):
                        add_svg(
                            section=section, src=url,
                            kind=f"css-{css_prop}",
                            evidence_file=evidence,
                        )
            for child in node.get("children") or []:
                walk_styles(child, section, evidence)

        walk_styles(section_data, section_name, rel)


# 3c. Global CSS bundles — any url(...svg) inside <ref>/bundles/*.css.
bundles_css_dir = ref_dir / "bundles"
if bundles_css_dir.is_dir():
    for css_path in sorted(bundles_css_dir.glob("*.css")):
        text = read_safe(css_path)
        if not text:
            continue
        rel_css = str(css_path.relative_to(ref_dir))
        for url in CSS_URL_SVG_RE.findall(text):
            add_svg(
                section=None, src=url,
                kind="bundle-css-url",
                evidence_file=rel_css,
            )


# Dedupe videos by (section, src) — html captures can repeat the same
# entry across re-runs.
dedup_videos: list[JsonObject] = []
seen_v: set[tuple[str, str]] = set()
for v in videos:
    video_key = (str(v.get("section") or ""), str(v.get("src") or ""))
    if video_key in seen_v:
        continue
    seen_v.add(video_key)
    dedup_videos.append(v)


# Dedupe lottie by path globally.
dedup_lottie: list[JsonObject] = []
seen_l: set[str] = set()
for lottie_entry in lottie:
    path = str(lottie_entry["path"])
    if path in seen_l:
        continue
    seen_l.add(path)
    dedup_lottie.append(lottie_entry)


# Dedupe svgs by (section, src) — same icon URL legitimately appears in
# multiple sections so we keep per-section bindings, but a section
# shouldn't list the same URL twice from two evidence sources.
dedup_svgs: list[JsonObject] = []
seen_s: set[tuple[str, str]] = set()
for s in svgs:
    svg_key = (str(s.get("section") or ""), str(s.get("src") or ""))
    if svg_key in seen_s:
        continue
    seen_s.add(svg_key)
    dedup_svgs.append(s)


# D2 (loop-nvti-0): bundle JS is often shared site-wide — a static string
# scan promotes assets OTHER pages load (homepage-only Lottie) into THIS
# page's requirements, and the coverage gate then fails against media the
# reference page never requests. When the capture recorded the page's actual
# network census (resource-manifest.json, includes failed/skipped candidates
# — observation proves the request), demote bundle-evidenced entries with no
# runtime request to `bundleOnlyUnrequested` (kept for transparency, excluded
# from requirements). DOM/html-evidenced entries are page-truth and never
# demoted; captures without a manifest keep legacy strict behavior.
requested_paths: list[str] = []
manifest_usable = False
manifest_file = ref_dir / "resource-manifest.json"
if manifest_file.is_file():
    try:
        manifest_data = json.loads(read_safe(manifest_file))
        manifest_resources = (
            manifest_data.get("resources")
            if isinstance(manifest_data, dict)
            else None
        )
        if isinstance(manifest_resources, list) and manifest_resources:
            for r in manifest_resources:
                u = (r.get("url") or "").strip() if isinstance(r, dict) else ""
                if u:
                    requested_paths.append(u.split("?", 1)[0])
            manifest_usable = bool(requested_paths)
    except ValueError:
        manifest_usable = False


def _runtime_requested(path: str) -> bool:
    p = (path or "").split("?", 1)[0]
    if not p:
        return True
    for u in requested_paths:
        if u == p or u.endswith(p):
            return True
    return False


def _bundle_only(entry: JsonObject) -> bool:
    return str(entry.get("evidenceFile") or "").startswith("bundles/")


demoted_lottie: list[JsonObject] = []
demoted_videos: list[JsonObject] = []
if manifest_usable:
    kept_l: list[JsonObject] = []
    for lottie_entry in dedup_lottie:
        if _bundle_only(lottie_entry) and not _runtime_requested(
            str(lottie_entry.get("path") or "")
        ):
            demoted_lottie.append(lottie_entry)
        else:
            kept_l.append(lottie_entry)
    dedup_lottie = kept_l
    kept_v: list[JsonObject] = []
    for v in dedup_videos:
        if _bundle_only(v) and not _runtime_requested(str(v.get("src") or "")):
            demoted_videos.append(v)
        else:
            kept_v.append(v)
    dedup_videos = kept_v

result = {
    "schemaVersion": 1,
    "videos": dedup_videos,
    "lottie": dedup_lottie,
    "svgs": dedup_svgs,
    "bundleOnlyUnrequested": {
        "lottie": demoted_lottie,
        "video": demoted_videos,
        "manifestConsulted": manifest_usable,
    },
    "totals": {
        "video": len(dedup_videos),
        "lottie": len(dedup_lottie),
        "svg": len(dedup_svgs),
    },
    "sources": {
        "extractor": "required-media.sh",
        "htmlSectionsScanned": (
            len(list(html_dir.glob("*.json"))) if html_dir.is_dir() else 0
        ),
        "runtimeMediaScanned": runtime_media_path.exists(),
        "bundlesScanned": (
            len(list(bundles_dir.glob("*.js"))) if bundles_dir.is_dir() else 0
        ),
    },
    "note": (
        "Video URLs are promoted from per-section html/*.json captures "
        "(media[] arrays) and runtime-media.json live DOM captures; "
        "Lottie URLs are extracted from bundles/*.js "
        "via bodymovin.loadAnimation({path:...}) PCRE plus direct URL "
        "literal fallback. The required-media-coverage gate verifies "
        "every entry was transferred to impl/public/ and referenced in "
        "impl source."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
demoted_note = ""
if demoted_lottie or demoted_videos:
    demoted_note = (
        f" (demoted bundle-only-unrequested: {len(demoted_lottie)} lottie, "
        f"{len(demoted_videos)} video — no runtime request in resource-manifest)"
    )
print(
    f"required-media: {len(dedup_videos)} video(s), "
    f"{len(dedup_lottie)} Lottie path(s), "
    f"{len(dedup_svgs)} SVG URL(s) → {out_path}{demoted_note}"
)
sys.exit(0)
