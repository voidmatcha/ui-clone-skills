#!/usr/bin/env bash
# resource-mirror.sh — Capture browser-observed JS/CSS/assets into ref resources/
#
# Usage: bash scripts/extract/resource-mirror.sh <session> <ref-dir> [url] [--same-origin-only]
#
# Produces:
#   <ref-dir>/resources/<host>/<path>        downloaded resource bodies
#   <ref-dir>/resource-manifest.json         URL → local path/status manifest
#
# This is extraction evidence only. Do not serve resources/ as the implementation
# or treat a mirror as fidelity proof; rendered visual verification remains the
# source of truth.

set -euo pipefail

SESSION="${1:?usage: resource-mirror.sh <session> <ref-dir> [url] [--same-origin-only]}"
REF_DIR="${2:?usage: resource-mirror.sh <session> <ref-dir> [url] [--same-origin-only]}"
SOURCE_URL="${3:-}"
SAME_ORIGIN_FLAG="${4:-}"

command -v agent-browser >/dev/null 2>&1 || {
  echo "resource-mirror.sh: agent-browser not found in PATH" >&2
  exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/_resource_mirror.py"
[ -f "$HELPER" ] || {
  echo "resource-mirror.sh: missing $HELPER" >&2
  exit 2
}

mkdir -p "$REF_DIR"
RAW_FILE="$(mktemp "${TMPDIR:-/tmp}/ui-clone-resource-mirror.XXXXXX")"
trap 'rm -f "$RAW_FILE"' EXIT

if [ -n "$SOURCE_URL" ]; then
  agent-browser --session "$SESSION" open "$SOURCE_URL" >/dev/null
  agent-browser --session "$SESSION" wait "${UI_CLONE_RESOURCE_MIRROR_WAIT_MS:-3000}" >/dev/null
fi

JS=$(cat <<'JS'
(() => {
  const absUrl = (value) => {
    if (!value || typeof value !== 'string') return '';
    try { return new URL(value, location.href).href; } catch (_) { return value; }
  };
  const add = (map, entry) => {
    const url = absUrl(entry.url || entry.name || entry.href || entry.src || '');
    if (!url || !/^https?:\/\//i.test(url)) return;
    const key = url.replace(/#.*/, '');
    if (!map.has(key)) map.set(key, { ...entry, url: key });
  };
  const resources = new Map();

  performance.getEntriesByType('resource').forEach((entry) => {
    add(resources, {
      source: 'performance',
      url: entry.name,
      initiatorType: entry.initiatorType || '',
      transferSize: entry.transferSize || 0,
      encodedBodySize: entry.encodedBodySize || 0,
      decodedBodySize: entry.decodedBodySize || 0
    });
  });

  document.querySelectorAll('script[src]').forEach((el) => add(resources, {
    source: 'dom-script', initiatorType: 'script', url: el.src || el.getAttribute('src')
  }));
  document.querySelectorAll('link[href]').forEach((el) => add(resources, {
    source: 'dom-link',
    initiatorType: (el.rel || el.getAttribute('as') || 'link'),
    url: el.href || el.getAttribute('href')
  }));
  document.querySelectorAll('img[src], img[srcset], source[src], source[srcset], video[src], audio[src]').forEach((el) => {
    const tag = el.tagName.toLowerCase();
    const current = el.currentSrc || el.src || el.getAttribute('src') || '';
    add(resources, { source: `dom-${tag}`, initiatorType: tag, url: current });
    const srcset = el.getAttribute('srcset') || '';
    srcset.split(',').map((part) => part.trim().split(/\s+/)[0]).filter(Boolean).forEach((url) => {
      add(resources, { source: `dom-${tag}-srcset`, initiatorType: tag, url });
    });
  });

  const urlRe = /url\(["']?([^"')]+)["']?\)/g;
  document.querySelectorAll('*').forEach((el) => {
    let bg = '';
    try { bg = getComputedStyle(el).backgroundImage || ''; } catch (_) { bg = ''; }
    let match;
    while ((match = urlRe.exec(bg)) !== null) {
      add(resources, { source: 'computed-background', initiatorType: 'css-image', url: match[1] });
    }
  });

  return JSON.stringify({
    schemaVersion: 1,
    source: 'scripts/extract/resource-mirror.sh',
    sourceUrl: location.href,
    capturedAt: new Date().toISOString(),
    resources: [...resources.values()]
  });
})()
JS
)

if ! agent-browser --session "$SESSION" eval "$JS" > "$RAW_FILE"; then
  echo "resource-mirror.sh: agent-browser eval failed for session '$SESSION'" >&2
  exit 1
fi

ARGS=("$REF_DIR" "$RAW_FILE" "$SOURCE_URL")
if [ "$SAME_ORIGIN_FLAG" = "--same-origin-only" ]; then
  ARGS+=("--same-origin-only")
fi

python3 "$HELPER" "${ARGS[@]}"
