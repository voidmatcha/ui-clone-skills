#!/usr/bin/env bash
# extract-asset-metadata.sh — deterministic Step 2.5 metadata extraction.
#
# Usage: bash extract-asset-metadata.sh <session> <ref-dir> [url]
#
# Produces canonical extraction-gate artifacts from the live reference page:
#   <ref-dir>/head.json
#   <ref-dir>/fonts.json
#   <ref-dir>/visible-images.json
#   <ref-dir>/css/*.css                 (best-effort stylesheet downloads)
#   <ref-dir>/css/variables.txt
#
# This script is intentionally metadata-focused. Asset file transfer to an
# implementation public/ directory remains the job of asset-download.sh and
# extract-assets.sh after an impl root exists.

set -euo pipefail

SESSION="${1:?usage: extract-asset-metadata.sh <session> <ref-dir> [url]}"
REF_DIR="${2:?usage: extract-asset-metadata.sh <session> <ref-dir> [url]}"
SOURCE_URL="${3:-}"

command -v agent-browser >/dev/null 2>&1 || {
  echo "extract-asset-metadata.sh: agent-browser not found in PATH" >&2
  exit 2
}

mkdir -p "$REF_DIR/css"

JS=$(cat <<'JS'
(() => {
  const absUrl = (value) => {
    if (!value || typeof value !== 'string') return '';
    try { return new URL(value, location.href).href; } catch (_) { return value; }
  };
  const className = (el) => {
    const raw = typeof el.className === 'string' ? el.className : (el.className && el.className.baseVal) || '';
    return String(raw || '').trim();
  };
  const firstClassSelector = (el) => {
    const first = className(el).split(/\s+/).find(Boolean);
    return el.tagName.toLowerCase() + (first ? '.' + first.replace(/[^a-zA-Z0-9_-]/g, '') : '');
  };
  const rectPayload = (rect) => ({
    top: Math.round(rect.top + window.scrollY),
    left: Math.round(rect.left + window.scrollX),
    width: Math.round(rect.width),
    height: Math.round(rect.height)
  });
  const resourceNames = performance.getEntriesByType('resource')
    .map((entry) => entry && entry.name)
    .filter((name) => typeof name === 'string' && name);
  const stylesheetUrls = new Set();
  document.querySelectorAll('link[rel~="stylesheet"], link[as="style"]').forEach((link) => {
    const href = absUrl(link.href || link.getAttribute('href'));
    if (href) stylesheetUrls.add(href);
  });
  resourceNames.forEach((name) => {
    if (/\.css(?:[?#].*)?$/i.test(name)) stylesheetUrls.add(absUrl(name));
  });

  const favicon = (() => {
    const link = document.querySelector('link[rel*="icon"], link[rel="shortcut icon"]');
    return link ? absUrl(link.href || link.getAttribute('href')) : '';
  })();
  const viewport = (() => {
    const meta = document.querySelector('meta[name="viewport"]');
    return meta ? String(meta.content || '') : '';
  })();
  const head = {
    schemaVersion: 1,
    source: 'scripts/extract/extract-asset-metadata.sh',
    url: location.href,
    title: document.title || '',
    lang: document.documentElement ? (document.documentElement.lang || '') : '',
    favicon,
    viewport,
    stylesheets: [...stylesheetUrls]
  };

  const imageMap = new Map();
  const addImage = (entry) => {
    const src = absUrl(entry.src || '');
    if (!src || !/^(https?:|data:image\/|blob:)/i.test(src)) return;
    const key = `${entry.type || 'image'} ${src} ${entry.top || 0} ${entry.left || 0}`;
    if (!imageMap.has(key)) imageMap.set(key, { ...entry, src });
  };
  document.querySelectorAll('img').forEach((img) => {
    const rect = img.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return;
    const src = img.currentSrc || img.src || img.getAttribute('src') || img.dataset.src || img.dataset.lazy || '';
    addImage({
      type: 'image',
      element: firstClassSelector(img),
      alt: img.alt || '',
      originalSrc: img.getAttribute('src') || '',
      src,
      srcset: img.currentSrc ? (img.getAttribute('srcset') || '') : '',
      ...rectPayload(rect)
    });
  });
  const bgUrlRe = /url\(["']?([^"')]+)["']?\)/g;
  document.querySelectorAll('*').forEach((el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 50 || rect.height < 50) return;
    const bg = getComputedStyle(el).backgroundImage || '';
    if (!bg || bg === 'none' || !bg.includes('url(')) return;
    let match;
    while ((match = bgUrlRe.exec(bg)) !== null) {
      addImage({
        type: 'bg-image',
        element: firstClassSelector(el),
        src: match[1],
        ...rectPayload(rect)
      });
    }
  });

  const fontFaces = [];
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules || []) {
        if (rule.type !== CSSRule.FONT_FACE_RULE) continue;
        const src = rule.style.getPropertyValue('src') || '';
        const urls = [];
        let match;
        while ((match = bgUrlRe.exec(src)) !== null) urls.push(absUrl(match[1]));
        fontFaces.push({
          family: (rule.style.getPropertyValue('font-family') || '').replace(/["']/g, ''),
          weight: rule.style.getPropertyValue('font-weight') || 'normal',
          style: rule.style.getPropertyValue('font-style') || 'normal',
          display: rule.style.getPropertyValue('font-display') || '',
          urls
        });
      }
    } catch (_) {}
  }
  const loadedFonts = [];
  if (document.fonts && typeof document.fonts.forEach === 'function') {
    document.fonts.forEach((font) => {
      loadedFonts.push({
        family: font.family || '',
        weight: font.weight || '',
        style: font.style || '',
        stretch: font.stretch || '',
        status: font.status || ''
      });
    });
  }
  const fontResourceUrls = resourceNames
    .filter((name) => /\.(?:woff2?|ttf|otf|eot)(?:[?#].*)?$/i.test(name))
    .map(absUrl);

  return JSON.stringify({
    schemaVersion: 1,
    source: 'scripts/extract/extract-asset-metadata.sh',
    capturedAt: new Date().toISOString(),
    head,
    visibleImages: [...imageMap.values()],
    fonts: {
      schemaVersion: 1,
      source: 'scripts/extract/extract-asset-metadata.sh',
      faces: fontFaces,
      loadedFonts,
      resourceUrls: [...new Set(fontResourceUrls)]
    }
  });
})()
JS
)

RAW_FILE="$(mktemp "${TMPDIR:-/tmp}/ui-clone-asset-metadata.XXXXXX")"
trap 'rm -f "$RAW_FILE"' EXIT

if ! agent-browser --session "$SESSION" eval "$JS" > "$RAW_FILE"; then
  echo "extract-asset-metadata.sh: agent-browser eval failed for session '$SESSION'" >&2
  exit 1
fi

python3 - "$REF_DIR" "$SOURCE_URL" "$RAW_FILE" <<'PY'
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ref_dir = Path(sys.argv[1])
source_url = sys.argv[2]
raw_path = Path(sys.argv[3])
css_dir = ref_dir / "css"
css_dir.mkdir(parents=True, exist_ok=True)


def _unwrap_agent_browser(raw: str):
    value = raw.strip()
    if not value:
        raise ValueError("empty agent-browser eval output")
    for _ in range(5):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        if isinstance(parsed, dict):
            data = parsed.get("data")
            if isinstance(data, dict) and "result" in data:
                parsed = data["result"]
            elif "result" in parsed:
                parsed = parsed["result"]
            else:
                return parsed
        if isinstance(parsed, str):
            stripped = parsed.strip()
            if stripped.startswith(("{", "[", '"')):
                value = stripped
                continue
            return parsed
        return parsed
    return parsed


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _safe_css_name(url: str, index: int, used: set[str]) -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name or f"stylesheet-{index}.css"
    if not name.lower().endswith(".css"):
        name = f"{name}.css"
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or f"stylesheet-{index}.css"
    stem = name[:-4]
    candidate = name
    suffix = 2
    while candidate in used:
        candidate = f"{stem}-{suffix}.css"
        suffix += 1
    used.add(candidate)
    return candidate


def _download_css(urls: list[str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    used = {p.name for p in css_dir.glob("*.css")}
    timeout = os.environ.get("UI_CLONE_ASSET_METADATA_CSS_TIMEOUT", "20")
    curl = os.environ.get("UI_CLONE_CURL", "curl")
    user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    for index, url in enumerate(urls, start=1):
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        filename = _safe_css_name(url, index, used)
        dest = css_dir / filename
        command = [curl, "-fsSL", "--max-time", timeout, "-A", user_agent, "-o", str(dest), url]
        try:
            proc = subprocess.run(command, capture_output=True, timeout=float(timeout) + 5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            dest.unlink(missing_ok=True)
            records.append({"url": url, "path": f"css/{filename}", "status": "failed", "error": str(exc)})
            continue
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            records.append({"url": url, "path": f"css/{filename}", "status": "downloaded", "bytes": dest.stat().st_size})
        else:
            dest.unlink(missing_ok=True)
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            records.append({"url": url, "path": f"css/{filename}", "status": "failed", "error": stderr})
    return records


def _extract_variables(css_files: list[Path]) -> list[tuple[str, str]]:
    css_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in css_files if path.is_file()
    )
    css_text = re.sub(r"/\*.*?\*/", "", css_text, flags=re.DOTALL)
    found = {
        name: value.strip()
        for name, value in re.findall(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;{}]+)", css_text)
    }
    return sorted(found.items())


raw = raw_path.read_text(encoding="utf-8", errors="replace")
payload = _unwrap_agent_browser(raw)
if isinstance(payload, str):
    payload = _unwrap_agent_browser(payload)
if not isinstance(payload, dict):
    raise SystemExit(f"extract-asset-metadata.sh: expected object payload, got {type(payload).__name__}")

head = payload.get("head") if isinstance(payload.get("head"), dict) else {}
if source_url and not head.get("sourceUrl"):
    head["sourceUrl"] = source_url
stylesheets = [url for url in head.get("stylesheets", []) if isinstance(url, str)]
css_downloads = _download_css(stylesheets)
head["cssDownloads"] = css_downloads
_write_json(ref_dir / "head.json", head)

fonts = payload.get("fonts") if isinstance(payload.get("fonts"), dict) else {}
fonts.setdefault("schemaVersion", 1)
fonts.setdefault("source", "scripts/extract/extract-asset-metadata.sh")
fonts.setdefault("faces", [])
fonts.setdefault("loadedFonts", [])
fonts.setdefault("resourceUrls", [])
_write_json(ref_dir / "fonts.json", fonts)

images = payload.get("visibleImages")
if not isinstance(images, list):
    images = []
visible_payload = {
    "schemaVersion": 1,
    "source": "scripts/extract/extract-asset-metadata.sh",
    "images": images,
    "summary": {"count": len(images)},
}
if not images:
    visible_payload["note"] = "No visible <img> or CSS background-image assets observed during Step 2.5 extraction."
_write_json(ref_dir / "visible-images.json", visible_payload)

variables = _extract_variables(sorted(css_dir.glob("*.css")))
variables_txt = css_dir / "variables.txt"
if variables:
    variables_txt.write_text(
        "\n".join(f"{name}: {value}" for name, value in variables) + "\n",
        encoding="utf-8",
    )
else:
    variables_txt.write_text(
        "/* ui-clone: no CSS custom properties observed in downloaded CSS */\n",
        encoding="utf-8",
    )
_write_json(
    css_dir / "variables.json",
    {
        "schemaVersion": 1,
        "source": "scripts/extract/extract-asset-metadata.sh",
        "observation": "css-custom-properties" if variables else "no-css-custom-properties",
        "count": len(variables),
        "variables": [{"name": name, "value": value} for name, value in variables],
        "derivedFrom": [f"css/{path.name}" for path in sorted(css_dir.glob("*.css"))],
    },
)

summary = {
    "status": "pass",
    "phase": "asset-metadata",
    "data": {
        "head": "head.json",
        "fonts": "fonts.json",
        "visibleImages": len(images),
        "stylesheets": len(stylesheets),
        "cssDownloaded": sum(1 for row in css_downloads if row.get("status") == "downloaded"),
        "cssVariables": len(variables),
    },
    "defects": [],
    "errors": [],
}
print(json.dumps(summary, indent=2))
PY
