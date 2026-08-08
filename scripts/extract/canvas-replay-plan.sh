#!/usr/bin/env bash
# canvas-replay-plan.sh — AUTO-routing decision for the canvas-replay fallback.
#
# Decides whether a WebGL/canvas hero must fall back to a recorded <video>
# replay (because a live re-embed is origin-locked OR the impl renders 0
# canvases) and writes the deterministic plan artifact.
#
# Usage:
#   canvas-replay-plan.sh <ref-dir> [impl-url] [session]
#
# Inputs read from <ref-dir>:
#   canvas-webgl-detection.json   — ref render-surface detection (required)
#   asset-substitution.json,      — scene-src discovery (origin-lock heuristic)
#   external-sdks.json, script-urls.json
#   section-map.json              — hero section naming
#
# Writes:
#   <ref-dir>/canvas-replay-plan.json
#   (and merges a canvas-replay declaration into asset-substitution.json when
#    the decision routes to replay)
#
# Exit 0 always (a "none" decision is a valid, non-error outcome). Exit 2 on
# setup error (missing ref-dir / detection artifact).
set -uo pipefail

REF_DIR="${1:?usage: canvas-replay-plan.sh <ref-dir> [impl-url] [session]}"
IMPL_URL="${2:-}"
SESSION="${3:-canvas-replay-plan}"

[ -d "$REF_DIR" ] || { echo "canvas-replay-plan: ref dir not found: $REF_DIR" >&2; exit 2; }
DETECT="$REF_DIR/canvas-webgl-detection.json"
[ -f "$DETECT" ] || { echo "canvas-replay-plan: detection artifact missing: $DETECT" >&2; exit 2; }

# ── Discover the canvas-driving scene src + origin-lock host ──────────────
# Pull candidate URLs from artifacts that name external scenes/SDKs.
# ref-bound CDN hosts gate the scene/SDK by domain and are not publicly
# embeddable: Bunny CDN, Webflow site assets, the ref's own apex.
REF_BOUND_HOSTS='b-cdn\.net|website-files\.com|\.webflow\.io'
ARTS="asset-substitution.json external-sdks.json required-media.json script-urls.json bundle-map.json"

SCENE_SRC=""
REEMBED_BLOCKED="false"
for art in $ARTS; do
  p="$REF_DIR/$art"
  [ -f "$p" ] || continue
  # Prefer a precise scene-like URL (for the region/curl probe).
  if [ -z "$SCENE_SRC" ]; then
    cand="$(grep -oE 'https?://[^"[:space:]]+(unicorn[^"[:space:]]*|portrait[^"[:space:]]*|\.json|spline[^"[:space:]]*|scene[^"[:space:]]*)' "$p" 2>/dev/null | head -1)"
    [ -n "$cand" ] && SCENE_SRC="$cand"
  fi
  # Independent origin-lock signal: any ref-bound CDN host present.
  if grep -qiE "$REF_BOUND_HOSTS" "$p" 2>/dev/null; then
    REEMBED_BLOCKED="true"
    [ -z "$SCENE_SRC" ] && SCENE_SRC="$(grep -oE "https?://[^\"[:space:]]*($REF_BOUND_HOSTS)[^\"[:space:]]*" "$p" 2>/dev/null | head -1)"
  fi
done

# ── Cross-origin fetch probe (confirms a precise scene URL is origin-locked) ─
SCENE_STATUS="-1"
if [ -n "$SCENE_SRC" ] && printf '%s' "$SCENE_SRC" | grep -qiE '\.json($|\?)'; then
  SCENE_STATUS="$(curl -s -o /dev/null -m 8 -w '%{http_code}' \
    -H 'Origin: https://probe.example' -H 'Referer: https://probe.example/' \
    "$SCENE_SRC" 2>/dev/null || echo 0)"
fi

# ── Impl canvas count (the "blank hero" trigger) ──────────────────────────
IMPL_CANVAS_COUNT="-1"   # -1 == unknown (impl not probed)
if [ -n "$IMPL_URL" ] && command -v agent-browser >/dev/null 2>&1; then
  RAW="$(agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1; sleep 2; \
    agent-browser --session "$SESSION" eval '(() => document.querySelectorAll("canvas").length)()' 2>/dev/null || echo '')"
  agent-browser --session "$SESSION" close >/dev/null 2>&1 || true
  num="$(printf '%s' "$RAW" | grep -oE '[0-9]+' | head -1)"
  [ -n "$num" ] && IMPL_CANVAS_COUNT="$num"
fi

python3 - "$REF_DIR" "$SCENE_SRC" "$REEMBED_BLOCKED" "$SCENE_STATUS" "$IMPL_CANVAS_COUNT" <<'PY'
import json
import sys
from pathlib import Path

# Make ui_clone importable from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[0] if False else Path.cwd()))
try:
    from ui_clone.policies import canvas_replay_auto as cra
except ModuleNotFoundError:
    repo = Path(__file__).resolve()
    # Walk up to find the repo root containing ui_clone/.
    for parent in Path.cwd().resolve().parents:
        if (parent / "ui_clone").is_dir():
            sys.path.insert(0, str(parent))
            break
    from ui_clone.policies import canvas_replay_auto as cra

ref_dir = Path(sys.argv[1])
scene_src = sys.argv[2]
reembed_blocked_host = sys.argv[3] == "true"
scene_status = int(sys.argv[4])
impl_canvas_count_raw = int(sys.argv[5])

detection = json.loads((ref_dir / "canvas-webgl-detection.json").read_text(encoding="utf-8"))

# reembed blocked if the ref-bound-host heuristic fired OR the cross-origin
# probe returned a blocking status (>=0 means it ran).
reembed_blocked = reembed_blocked_host
if scene_status >= 0:
    reembed_blocked = reembed_blocked or cra.reembed_blocked_from_status(scene_status)

# impl canvas count: -1 means "not probed". When the impl was not probed we
# cannot assert the blank trigger, so treat as None (origin-lock can still route).
impl_canvas_count = None if impl_canvas_count_raw < 0 else impl_canvas_count_raw

# Region from the largest detected canvas.
canvases = detection.get("canvases") or []
if canvases:
    big = max(canvases, key=lambda c: c.get("area", c.get("width", 0) * c.get("height", 0)))
    region = {
        "x": 0,
        "y": 0,
        "width": int(big.get("width", 1440)),
        "height": int(big.get("height", 900)),
    }
else:
    region = {"x": 0, "y": 0, "width": 1440, "height": 900}

# Hero section name from section-map (skip nav/footer/transition shells).
section = "hero"
sm = ref_dir / "section-map.json"
if sm.is_file():
    try:
        sections = json.loads(sm.read_text(encoding="utf-8")).get("sections") or []
        for entry in sections:
            cls = str(entry.get("className", "")).lower()
            if any(k in cls for k in ("nav", "footer", "transition")):
                continue
            if int(entry.get("childCount", 0)) == 0 and not entry.get("textPreview"):
                continue
            section = entry.get("id") or entry.get("className") or "hero"
            break
    except Exception:
        pass

plan = cra.build_replay_plan(
    url=detection.get("url", ""),
    detection=detection,
    impl_canvas_count=impl_canvas_count,
    reembed_blocked=reembed_blocked,
    section=section,
    ref_canvas_selector="canvas",
    region=region,
    replay_asset="public/canvas-replay/hero.webm",
    poster="public/canvas-replay/hero-poster.png",
)
plan["sceneSrc"] = scene_src
plan["sceneStatus"] = scene_status

out = ref_dir / "canvas-replay-plan.json"
out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"canvas-replay-plan: decision={plan['decision']} reason={plan['reason']} -> {out}")

# Declare the replay asset in asset-substitution.json so anti-cheat understands
# it is the ref's OWN recorded motion (a declared substituted asset).
decl = cra.asset_substitution_entry(plan)
if decl is not None:
    asp = ref_dir / "asset-substitution.json"
    try:
        sub = json.loads(asp.read_text(encoding="utf-8")) if asp.is_file() else {}
    except Exception:
        sub = {}
    if not isinstance(sub, dict):
        sub = {}
    existing = sub.get("canvasReplay")
    if not isinstance(existing, list):
        existing = []
    # Replace any prior declaration for the same asset so it stays fresh.
    existing = [
        e for e in existing
        if not (isinstance(e, dict) and e.get("replacementSrc") == decl["replacementSrc"])
    ]
    existing.append(decl)
    sub["canvasReplay"] = existing
    asp.write_text(json.dumps(sub, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"canvas-replay-plan: declared replay asset in {asp}")
PY
