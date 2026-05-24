#!/usr/bin/env bash
# transition-to-dtcg.sh — Step E: emit DTCG-format motion-tokens.json
# from transition-spec.json.
#
# Step H added `fingerprint` + `feel` per transition + a top-level
# `motion_signature` aggregate; this script reads those + the exact
# values (duration/easing) and writes a separate motion-tokens.json
# file in W3C Design Token Community Group (DTCG) format so
# designlang / Figma Variables / shadcn / Tailwind theme consumers can
# read our motion language verbatim.
#
# Not strategic per codex review — designlang already owns the DTCG
# format. We emit it as a compatibility surface so cross-tool interop
# is possible, but our value is the exact-values + verification layer
# under it.
#
# Usage:
#   bash transition-to-dtcg.sh <ref-dir> [<output-path>]
#
# Default output: <ref-dir>/motion-tokens.json
# Exit: 0 wrote tokens, 1 spec missing / parse failure, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
OUT_ARG="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: transition-to-dtcg.sh <ref-dir> [<output-path>]" >&2
  exit 2
fi

SPEC="$REF_DIR/transition-spec.json"
if [ ! -f "$SPEC" ]; then
  echo "transition-to-dtcg: skip (no transition-spec.json)" >&2
  exit 0
fi

OUT_PATH="${OUT_ARG:-$REF_DIR/motion-tokens.json}"

python3 - "$SPEC" "$OUT_PATH" <<'PY'
import json
import re
import sys
from pathlib import Path

spec_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])

try:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"transition-to-dtcg: parse failed: {exc}", file=sys.stderr)
    sys.exit(1)

DURATION_MS = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s)\b", re.I)


def parse_duration_ms(value) -> int | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("scroll-tied", "loop", "n/a", "infinite"):
        return None
    m = DURATION_MS.search(s)
    if not m:
        return None
    n = float(m.group(1))
    return int(round(n if m.group(2) == "ms" else n * 1000))


# ── Collect ──
transitions = spec.get("transitions") or []
durations: dict[int, str] = {}   # ms → token name
easings: dict[str, str] = {}      # cubic-bezier string → token name

for tr in transitions:
    if not isinstance(tr, dict):
        continue
    anim = tr.get("animation") or {}
    if not isinstance(anim, dict):
        continue
    ms = parse_duration_ms(anim.get("duration"))
    if ms is not None and ms not in durations:
        # Bucketed names for stable DTCG IDs across runs.
        if ms < 100:
            name = "instant"
        elif ms < 200:
            name = "xs"
        elif ms < 350:
            name = "sm"
        elif ms < 550:
            name = "md"
        elif ms < 900:
            name = "lg"
        else:
            name = "xl"
        # Disambiguate collisions deterministically by ms.
        if name in durations.values():
            name = f"{name}-{ms}"
        durations[ms] = name

    easing_raw = anim.get("easing")
    if not isinstance(easing_raw, str):
        continue
    e = easing_raw.strip().lower()
    if e in ("", "n/a", "linear (scroll-tied)"):
        continue
    if e not in easings:
        # Token name from the bezier shape or keyword.
        m = re.search(r"cubic-bezier\(\s*([\d.\-]+)\s*,\s*([\d.\-]+)\s*,\s*([\d.\-]+)\s*,\s*([\d.\-]+)\s*\)", e)
        if m:
            y1 = float(m.group(2))
            y2 = float(m.group(4))
            if y1 > 1.0 or y2 > 1.0 or y1 < 0.0 or y2 < 0.0:
                base = "spring"
            elif float(m.group(3)) > 0.6 and y2 > 0.95:
                base = "ease-out-quart"
            else:
                base = "ease-out"
        elif "ease-in-out" in e:
            base = "ease-in-out"
        elif "ease-out" in e:
            base = "ease-out"
        elif "ease-in" in e:
            base = "ease-in"
        elif "linear" in e:
            base = "linear"
        else:
            base = "custom"
        # Number collisions
        name = base
        idx = 2
        while name in easings.values():
            name = f"{base}-{idx}"
            idx += 1
        easings[e] = name

# ── Emit DTCG ──
tokens: dict = {
    "$description": (
        "Motion tokens extracted by ui-clone-skills (Step E DTCG export). "
        "Source: tmp/ref/<c>/transition-spec.json — see also "
        "motion_signature.dominant_feel for the categorical character."
    ),
    "duration": {
        name: {
            "$value": f"{ms}ms",
            "$type": "duration",
            "ms": ms,
        }
        for ms, name in sorted(durations.items(), key=lambda kv: kv[0])
    },
    "easing": {
        name: {
            "$value": value,
            "$type": "cubicBezier",
        }
        for value, name in easings.items()
    },
    "$meta": {
        "transitions_count": len(transitions),
        "motion_signature": spec.get("motion_signature", {}),
        "site": spec.get("site", ""),
    },
}

out_path.write_text(json.dumps(tokens, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(
    f"transition-to-dtcg: wrote {out_path} "
    f"(durations={len(durations)} easings={len(easings)})"
)
PY
EXIT=$?
exit $EXIT
