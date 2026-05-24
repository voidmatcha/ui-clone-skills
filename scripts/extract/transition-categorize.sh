#!/usr/bin/env bash
# transition-categorize.sh — Step H: enrich transition-spec.json with a
# categorical layer (fingerprint, feel) alongside the existing exact
# layer (from/to/duration/easing).
#
# Rationale: exact values drive impl generation, but agents and human
# readers ALSO need the human-readable shape ("fade-up", "scroll-pin",
# "spring-bounce") to write educational content + match designlang-style
# motion-token grammar.
#
# This script is purely derivative — it reads transition-spec.json and
# writes the same file back enriched with two new fields per transition
# (`fingerprint`, `feel`) plus a top-level `motion_signature` aggregate.
# Idempotent: existing fingerprint/feel values are preserved unless
# `--rebuild` is passed.
#
# Usage:
#   bash transition-categorize.sh <ref-dir> [--rebuild]
#
# Exit: 0 success / no-op, 1 enrichment failure, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
REBUILD=""
if [ "${2:-}" = "--rebuild" ]; then
  REBUILD="1"
fi

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: transition-categorize.sh <ref-dir> [--rebuild]" >&2
  exit 2
fi

SPEC="$REF_DIR/transition-spec.json"
if [ ! -f "$SPEC" ]; then
  echo "transition-categorize: skip (no transition-spec.json)" >&2
  exit 0
fi

python3 - "$SPEC" "$REBUILD" <<'PY'
import json
import re
import sys
from pathlib import Path

spec_path = Path(sys.argv[1])
rebuild = bool(sys.argv[2])

try:
    data = json.loads(spec_path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"transition-categorize: parse failed: {exc}", file=sys.stderr)
    sys.exit(1)

CUBIC_BEZIER = re.compile(r"cubic-bezier\(\s*([\d.\-]+)\s*,\s*([\d.\-]+)\s*,\s*([\d.\-]+)\s*,\s*([\d.\-]+)\s*\)")
DURATION_MS = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s)\b", re.I)


def parse_duration_ms(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("scroll-tied", "loop", "n/a", "infinite"):
        return None
    m = DURATION_MS.search(s)
    if not m:
        return None
    n = float(m.group(1))
    return n if m.group(2) == "ms" else n * 1000


def feel_from_easing(easing: str | None, duration_ms: float | None) -> str:
    """Categorize feel from easing curve + duration.

    Output values (stable enum the receipt/skill docs key on):
      springy   — bezier overshoots (any control point > 1.0 in y)
      smooth    — near-linear bezier
      snappy    — late-ramp bezier with steep finish (ease-out-quart family)
      gentle    — ease-out with mid duration
      instant   — duration < 80ms
      scrubbed  — scroll-tied (no temporal feel — duration is None)
      unknown   — non-bezier / unparseable
    """
    if duration_ms is None:
        return "scrubbed"
    if duration_ms < 80:
        return "instant"

    if not easing:
        return "unknown"
    e = easing.strip().lower()

    # Canonical keyword easings
    if e in ("linear",):
        return "smooth"
    if e in ("ease-out", "ease",):
        return "gentle"
    if e in ("ease-in-out",):
        return "smooth"

    m = CUBIC_BEZIER.search(e)
    if not m:
        return "unknown"
    x1, y1, x2, y2 = (float(g) for g in m.groups())
    # Overshoot ⇒ springy
    if y1 > 1.0 or y2 > 1.0 or y1 < 0.0 or y2 < 0.0:
        return "springy"
    # Sharp finish (ease-out-quart-like)
    if x2 > 0.6 and y2 > 0.95:
        return "snappy"
    # Near-linear
    if abs(x1 + x2 - 1.0) < 0.15 and abs(y1 + y2 - 1.0) < 0.15:
        return "smooth"
    return "gentle"


def fingerprint_from_animation(transition: dict) -> str:
    """Derive a human-readable shape label from trigger + from/to delta."""
    trigger = (transition.get("trigger") or "").strip().lower()
    anim = transition.get("animation") or {}
    if not isinstance(anim, dict):
        return "unknown"
    prop = (anim.get("property") or "").lower()
    a_from = anim.get("from") or {}
    a_to = anim.get("to") or {}
    if not isinstance(a_from, dict) or not isinstance(a_to, dict):
        return "unknown"

    has_opacity = "opacity" in a_from or "opacity" in a_to
    has_translate_y = any("translatey" in str(k).lower() for k in a_from) or any(
        "translatey" in str(k).lower() for k in a_to
    )
    has_translate_x = any("translatex" in str(k).lower() for k in a_from) or any(
        "translatex" in str(k).lower() for k in a_to
    )
    has_scale = any("scale" in str(k).lower() for k in a_from) or any(
        "scale" in str(k).lower() for k in a_to
    )
    has_rotate = any("rotate" in str(k).lower() for k in a_from) or any(
        "rotate" in str(k).lower() for k in a_to
    )
    # video play() shape
    if "paused" in a_from or "playing" in a_to:
        return "video-autoplay"

    duration = anim.get("duration")
    scrubbed = str(duration or "").strip().lower() == "scroll-tied"

    # Compose categorical name.
    direction = ""
    if has_translate_y:
        direction = "-up"  # default; rough — we don't disambiguate up vs down
    elif has_translate_x:
        direction = "-right"

    if trigger == "scroll":
        if has_opacity and has_translate_y:
            base = f"scroll-fade{direction}"
        elif has_opacity and has_scale:
            base = "scroll-scale-fade"
        elif has_scale:
            base = "scroll-scale"
        elif has_opacity:
            base = "scroll-fade"
        else:
            base = "scroll-transform"
        return f"{base}-scrubbed" if scrubbed else base

    if trigger == "load":
        if has_opacity and has_translate_y:
            return f"page-load-fade{direction}"
        if has_opacity and has_scale:
            return "page-load-scale-fade"
        if has_opacity:
            return "page-load-fade"
        return "page-load-transform"

    if trigger in ("hover", "mouseenter", "mouseleave"):
        if has_scale:
            return "hover-pop"
        if has_opacity:
            return "hover-fade"
        if has_translate_y or has_translate_x:
            return "hover-shift"
        if has_rotate:
            return "hover-rotate"
        return "hover-transform"

    if trigger in ("click", "mousedown", "mouseup"):
        if has_scale:
            return "click-pulse"
        return "click-transform"

    if trigger in ("intersection", "io", "in-view", "intersectionobserver"):
        if has_opacity and has_translate_y:
            return f"intersection-fade{direction}"
        if has_opacity:
            return "intersection-fade"
        return "intersection-transform"

    return "unknown"


# ── Enrich ──
transitions = data.get("transitions")
if not isinstance(transitions, list):
    print("transition-categorize: skip (no transitions array)", file=sys.stderr)
    sys.exit(0)

enriched_count = 0
all_fingerprints: list[str] = []
all_feels: list[str] = []
for tr in transitions:
    if not isinstance(tr, dict):
        continue
    anim = tr.get("animation") or {}
    duration_ms = parse_duration_ms(anim.get("duration") if isinstance(anim, dict) else None)
    easing = anim.get("easing") if isinstance(anim, dict) else None

    fingerprint = tr.get("fingerprint")
    feel = tr.get("feel")
    if rebuild or not fingerprint:
        fingerprint = fingerprint_from_animation(tr)
        tr["fingerprint"] = fingerprint
        enriched_count += 1
    if rebuild or not feel:
        feel = feel_from_easing(easing, duration_ms)
        tr["feel"] = feel

    all_fingerprints.append(fingerprint)
    all_feels.append(feel)

# Top-level signature
if all_feels:
    # Most common non-unknown feel
    feel_counts: dict[str, int] = {}
    for f in all_feels:
        if f and f != "unknown":
            feel_counts[f] = feel_counts.get(f, 0) + 1
    dominant_feel = (
        sorted(feel_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        if feel_counts else "unknown"
    )
    has_scrubbed = "scrubbed" in all_feels
    has_springy = "springy" in all_feels
    data["motion_signature"] = {
        "dominant_feel": dominant_feel,
        "scroll_linked": has_scrubbed,
        "has_spring": has_springy,
        "fingerprint_summary": sorted(set(all_fingerprints)),
        "transitions_count": len(transitions),
    }

spec_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(
    f"transition-categorize: enriched {enriched_count} transition(s); "
    f"dominant_feel={data.get('motion_signature', {}).get('dominant_feel')}"
)
PY
EXIT=$?
exit $EXIT
