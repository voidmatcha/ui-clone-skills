#!/usr/bin/env bash
# required-media.sh — produce <ref-dir>/required-media.json by merging
# video/Lottie evidence from per-section HTML captures + bundle JS scan.
#
#
# This artifact promotes:
#   - <video src=...> and <video><source src=...> URLs collected per
#     section by extract-section-html.sh (html/<name>.json.media[])
#   - Lottie/bodymovin path strings inside bundles/*.js using the
#     loadAnimation({ path: "..." }) PCRE pattern.
#
# Section binding: media entries from html/<name>.json are already
# scoped to a section name; Lottie paths from the bundle are global
# (no section info) until the impl side maps them by usage. Both
# kinds are emitted; the coverage gate checks transfer + reference.
#
# Inputs:
#   <ref-dir>/html/*.json       — per-section captures with media[]
#   <ref-dir>/bundles/*.js      — downloaded JS bundles (extract-bundles)
#   <ref-dir>/external-sdks.json — fallback Lottie evidence
#
# Output: <ref-dir>/required-media.json
#   {
#     schemaVersion: 1,
#     videos: [{section, src, type?, poster?, w?, h?}],
#     lottie: [{path, evidenceFile, line}],
#     totals: {video, lottie}
#   }
#
# Exit: 0 success, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: required-media.sh <ref-dir>" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/required-media.json"

python3 - "$REF_DIR" "$OUT_PATH" <<'PY'
# Python 3.9 compat for PEP 604 unions used below — defer
# annotation evaluation so `X | Y` is parsed as a string.
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
out_path = Path(sys.argv[2])

videos: list[dict] = []
lottie: list[dict] = []
svgs: list[dict] = []


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
        current_video: dict | None = None
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
        def walk_styles(node: dict, section: str, evidence: str) -> None:
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
dedup_videos = []
seen_v: set[tuple[str, str]] = set()
for v in videos:
    key = (v.get("section", ""), v.get("src", ""))
    if key in seen_v:
        continue
    seen_v.add(key)
    dedup_videos.append(v)


# Dedupe lottie by path globally.
dedup_lottie = []
seen_l: set[str] = set()
for l in lottie:
    if l["path"] in seen_l:
        continue
    seen_l.add(l["path"])
    dedup_lottie.append(l)


# Dedupe svgs by (section, src) — same icon URL legitimately appears in
# multiple sections so we keep per-section bindings, but a section
# shouldn't list the same URL twice from two evidence sources.
dedup_svgs = []
seen_s: set[tuple[str, str]] = set()
for s in svgs:
    key = (s.get("section") or "", s.get("src", ""))
    if key in seen_s:
        continue
    seen_s.add(key)
    dedup_svgs.append(s)


result = {
    "schemaVersion": 1,
    "videos": dedup_videos,
    "lottie": dedup_lottie,
    "svgs": dedup_svgs,
    "totals": {
        "video": len(dedup_videos),
        "lottie": len(dedup_lottie),
        "svg": len(dedup_svgs),
    },
    "sources": {
        "htmlSectionsScanned": (
            len(list(html_dir.glob("*.json"))) if html_dir.is_dir() else 0
        ),
        "bundlesScanned": (
            len(list(bundles_dir.glob("*.js"))) if bundles_dir.is_dir() else 0
        ),
    },
    "note": (
        "Video URLs are promoted from per-section html/*.json captures "
        "(media[] arrays); Lottie URLs are extracted from bundles/*.js "
        "via bodymovin.loadAnimation({path:...}) PCRE plus direct URL "
        "literal fallback. The required-media-coverage gate verifies "
        "every entry was transferred to impl/public/ and referenced in "
        "impl source."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"required-media: {len(dedup_videos)} video(s), "
    f"{len(dedup_lottie)} Lottie path(s), "
    f"{len(dedup_svgs)} SVG URL(s) → {out_path}"
)
sys.exit(0)
PY
