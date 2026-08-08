#!/usr/bin/env bash
# runtime-media.sh — capture media nodes that only exist after the page JS runs.
#
# Static HTML/section captures miss sites that create background videos from
# client-side code. This extractor opens the live reference, samples a few
# scroll positions to trigger lazy media, and writes runtime-media.json for
# required-media.sh to merge.
#
# Usage:
#   runtime-media.sh <url> <session> <ref-dir>
#
# Output:
#   <ref-dir>/runtime-media.json
set -euo pipefail

URL="${1:-}"
SESSION_BASE="${2:-}"
REF_DIR="${3:-}"

if [[ -z "$URL" || -z "$SESSION_BASE" || -z "$REF_DIR" ]]; then
  echo "Usage: runtime-media.sh <url> <session> <ref-dir>" >&2
  exit 2
fi
if [[ ! -d "$REF_DIR" ]]; then
  echo "runtime-media: ref-dir not found: $REF_DIR" >&2
  exit 2
fi
if ! command -v agent-browser >/dev/null 2>&1; then
  echo "runtime-media: agent-browser CLI missing" >&2
  exit 2
fi

SESSION="${SESSION_BASE}-runtime-media"
OUT="$REF_DIR/runtime-media.json"
RAW="$(mktemp "${TMPDIR:-/tmp}/runtime-media.XXXXXX")"
trap 'rm -f "$RAW"; agent-browser --session "$SESSION" close >/dev/null 2>&1 || true' EXIT

agent-browser --session "$SESSION" open "$URL" >/dev/null
agent-browser --session "$SESSION" set viewport 1440 900 >/dev/null 2>&1 || true
agent-browser --session "$SESSION" wait "${RUNTIME_MEDIA_INITIAL_WAIT_MS:-2000}" >/dev/null 2>&1 || true

agent-browser --session "$SESSION" eval "
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const abs = (value) => {
    if (!value || typeof value !== 'string') return '';
    if (value.startsWith('blob:') || value.startsWith('data:')) return value;
    try { return new URL(value, location.href).href; } catch (_) { return value; }
  };
  const textOf = (value) => (value == null ? '' : String(value));
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height),
    };
  };
  const sectionHint = (el, index) => {
    const kwMap = [
      ['hero','hero'], ['banner','banner'], ['main','main'], ['visual','visual'],
      ['showcase','showcase'], ['product','product'], ['feature','features'],
      ['partner','partners'], ['client','clients'], ['about','about'],
      ['team','team'], ['gallery','gallery'], ['portfolio','portfolio'],
      ['contact','contact'], ['footer','footer'],
    ];
    let parent = el;
    while (parent && parent !== document.body) {
      const cls = textOf(parent.className).toLowerCase();
      const id = textOf(parent.id).toLowerCase();
      for (const [kw, name] of kwMap) {
        if (cls.includes(kw) || id.includes(kw)) return name;
      }
      if (parent.tagName === 'SECTION') return parent.id || textOf(parent.className).split(/\\s+/)[0] || ('section-' + index);
      parent = parent.parentElement;
    }
    return 'runtime-video-' + index;
  };
  const collect = (phase) => Array.from(document.querySelectorAll('video')).map((v, index) => {
    const attrs = Array.from(v.attributes).map((a) => ({ name: a.name, value: a.value }));
    const dataSources = attrs
      .filter((a) => /(?:src|source|video|media|url)$/i.test(a.name) && /\.(mp4|webm|mov|m4v|m3u8|mpd)(?:$|[?#])/i.test(a.value || ''))
      .map((a) => ({ attr: a.name, src: abs(a.value) }));
    const sources = Array.from(v.querySelectorAll('source')).map((s) => ({
      src: abs(s.getAttribute('src') || s.src || ''),
      type: s.getAttribute('type') || s.type || '',
      media: s.getAttribute('media') || '',
    })).filter((s) => s.src);
    const srcAttr = v.getAttribute('src') || '';
    return {
      index,
      phase,
      section: sectionHint(v, index),
      src: abs(srcAttr || ''),
      currentSrc: abs(v.currentSrc || ''),
      sources,
      dataSources,
      attrs,
      poster: abs(v.getAttribute('poster') || v.poster || ''),
      type: v.getAttribute('type') || '',
      autoplay: v.autoplay || v.hasAttribute('autoplay'),
      loop: v.loop || v.hasAttribute('loop'),
      muted: v.muted || v.hasAttribute('muted'),
      playsInline: v.playsInline || v.hasAttribute('playsinline'),
      preload: v.getAttribute('preload') || '',
      readyState: v.readyState,
      videoWidth: v.videoWidth || null,
      videoHeight: v.videoHeight || null,
      rect: rectOf(v),
    };
  });

  const snapshots = [];
  snapshots.push(...collect('initial'));
  const maxScroll = Math.max(
    0,
    (document.documentElement.scrollHeight || document.body.scrollHeight || 0) - window.innerHeight
  );
  for (const pct of [0.25, 0.5, 0.75, 1]) {
    window.scrollTo(0, Math.round(maxScroll * pct));
    await sleep(700);
    snapshots.push(...collect('scroll-' + pct));
  }
  window.scrollTo(0, 0);
  await sleep(300);

  const byKey = new Map();
  for (const item of snapshots) {
    const key = item.currentSrc || item.src || (item.sources[0] && item.sources[0].src) || (item.dataSources[0] && item.dataSources[0].src) || item.poster || ('index-' + item.index);
    if (!byKey.has(key)) byKey.set(key, item);
  }
  const videos = Array.from(byKey.values());
  return JSON.stringify({
    schemaVersion: 1,
    url: location.href,
    capturedAt: new Date().toISOString(),
    videos,
    totals: { video: videos.length },
    sources: { extractor: 'runtime-media.sh', scrollSamples: 5 },
  });
})()
" > "$RAW"

python3 - "$RAW" "$OUT" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

raw_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])


def unwrap(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        value = line.strip()
        if not value:
            continue
        try:
            parsed = json.loads(value)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    raise ValueError("agent-browser eval did not return a JSON object")


payload = unwrap(raw_path.read_text(encoding="utf-8", errors="replace"))
out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"runtime-media: {len(payload.get('videos') or [])} video(s) → {out_path}")
PY
