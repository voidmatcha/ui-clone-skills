#!/usr/bin/env bash
# Capture one deterministic scroll replay track and bind it to the exact
# recorder/validator sources that produced it.

set -euo pipefail

if [ "$#" -lt 5 ]; then
  echo "usage: capture-replay-track.sh <url> <selector> <out.json> <start-px> <end-px> [baseline-sha] [--mode scroll-progress|scroll-action] [--driver animation-pause|virtual-clock] [--transport native|lenis-wheel] [--ready-wait-ms N] [--denominator-ms N] [--anchor-ms N]" >&2
  exit 2
fi

URL="$1"
SELECTOR="$2"
OUT="$3"
START_PX="$4"
END_PX="$5"
shift 5
BASELINE_SHA=""
MODE="scroll-progress"
DRIVER="animation-pause"
TRANSPORT="native"
READY_WAIT_MS=""
DENOMINATOR_MS=""
ANCHOR_MS=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode)
      [ "$#" -ge 2 ] || { echo "capture-replay-track: --mode requires a value" >&2; exit 2; }
      MODE="$2"
      shift 2
      ;;
    --mode=*)
      MODE="${1#--mode=}"
      shift
      ;;
    --driver)
      [ "$#" -ge 2 ] || { echo "capture-replay-track: --driver requires a value" >&2; exit 2; }
      DRIVER="$2"
      shift 2
      ;;
    --driver=*)
      DRIVER="${1#--driver=}"
      shift
      ;;
    --transport)
      [ "$#" -ge 2 ] || { echo "capture-replay-track: --transport requires a value" >&2; exit 2; }
      TRANSPORT="$2"
      shift 2
      ;;
    --transport=*)
      TRANSPORT="${1#--transport=}"
      shift
      ;;
    --ready-wait-ms)
      [ "$#" -ge 2 ] || { echo "capture-replay-track: --ready-wait-ms requires a value" >&2; exit 2; }
      READY_WAIT_MS="$2"
      shift 2
      ;;
    --ready-wait-ms=*)
      READY_WAIT_MS="${1#--ready-wait-ms=}"
      shift
      ;;
    --denominator-ms)
      [ "$#" -ge 2 ] || { echo "capture-replay-track: --denominator-ms requires a value" >&2; exit 2; }
      DENOMINATOR_MS="$2"
      shift 2
      ;;
    --denominator-ms=*)
      DENOMINATOR_MS="${1#--denominator-ms=}"
      shift
      ;;
    --anchor-ms)
      [ "$#" -ge 2 ] || { echo "capture-replay-track: --anchor-ms requires a value" >&2; exit 2; }
      ANCHOR_MS="$2"
      shift 2
      ;;
    --anchor-ms=*)
      ANCHOR_MS="${1#--anchor-ms=}"
      shift
      ;;
    [0-9a-f][0-9a-f]*)
      [ -z "$BASELINE_SHA" ] || { echo "capture-replay-track: duplicate baseline sha" >&2; exit 2; }
      BASELINE_SHA="$1"
      shift
      ;;
    *)
      echo "capture-replay-track: unexpected argument: $1" >&2
      exit 2
      ;;
  esac
done

case "$MODE" in
  scroll-progress|scroll-action) ;;
  *) echo "capture-replay-track: invalid mode: $MODE" >&2; exit 2 ;;
esac
case "$DRIVER" in
  animation-pause|virtual-clock) ;;
  *) echo "capture-replay-track: invalid driver: $DRIVER" >&2; exit 2 ;;
esac
case "$TRANSPORT" in
  native|lenis-wheel) ;;
  *) echo "capture-replay-track: invalid transport: $TRANSPORT" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RECORDER="$SCRIPT_DIR/capture-replay-track.mjs"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

command -v node >/dev/null 2>&1 || { echo "capture-replay-track: node not found" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "capture-replay-track: python3 not found" >&2; exit 2; }
[ -f "$RECORDER" ] || { echo "capture-replay-track: recorder missing: $RECORDER" >&2; exit 2; }

OUT_PARENT="$(dirname "$OUT")"
mkdir -p "$OUT_PARENT"
OUT_PARENT="$(cd "$OUT_PARENT" && pwd)"
OUT_BASENAME="$(basename "$OUT")"
case "$OUT_BASENAME" in
  *.json) MANIFEST_BASENAME="${OUT_BASENAME%.json}.manifest.json" ;;
  *) MANIFEST_BASENAME="${OUT_BASENAME}.manifest.json" ;;
esac
FINAL_OUT="$OUT_PARENT/$OUT_BASENAME"
FINAL_MANIFEST="$OUT_PARENT/$MANIFEST_BASENAME"
WORK_DIR="$(mktemp -d "$OUT_PARENT/.replay-track.XXXXXX")"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

TRACK_TMP="$WORK_DIR/$OUT_BASENAME"
SUMMARY_TMP="$WORK_DIR/summary.json"
MANIFEST_TMP="$WORK_DIR/$MANIFEST_BASENAME"

RECORDER_ARGS=(
  --url "$URL"
  --selector "$SELECTOR"
  --out "$TRACK_TMP"
  --start-px "$START_PX"
  --end-px "$END_PX"
  --mode "$MODE"
  --driver "$DRIVER"
  --transport "$TRANSPORT"
)
if [ -n "$DENOMINATOR_MS" ]; then
  RECORDER_ARGS+=(--denominator-ms "$DENOMINATOR_MS")
fi
if [ -n "$READY_WAIT_MS" ]; then
  RECORDER_ARGS+=(--ready-wait-ms "$READY_WAIT_MS")
fi
if [ -n "$ANCHOR_MS" ]; then
  RECORDER_ARGS+=(--anchor-ms "$ANCHOR_MS")
fi
if [ -n "$BASELINE_SHA" ]; then
  RECORDER_ARGS+=(--baseline-sha "$BASELINE_SHA")
fi
if [ -n "${UI_CLONE_CHROMIUM_PATH:-}" ]; then
  RECORDER_ARGS+=(--executable-path "$UI_CLONE_CHROMIUM_PATH")
elif [ -n "${UI_CLONE_BROWSER_CHANNEL:-}" ]; then
  RECORDER_ARGS+=(--channel "$UI_CLONE_BROWSER_CHANNEL")
fi

node "$RECORDER" "${RECORDER_ARGS[@]}" > "$SUMMARY_TMP"
VALIDATION_RESULT=""
if ! VALIDATION_RESULT="$(python3 -m ui_clone.replay_track validate "$TRACK_TMP")"; then
  echo "$VALIDATION_RESULT" >&2
  exit 1
fi

read -r BROWSER_VERSION TOOL_VERSION < <(
  python3 - "$SUMMARY_TMP" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    summary = json.load(handle)
browser = str(summary.get("browserVersion", "")).replace("\n", " ").strip()
tool = str(summary.get("playwrightVersion", "")).replace("\n", " ").strip()
if not browser or not tool:
    raise SystemExit("capture-replay-track: recorder summary lacks tool versions")
print(browser, f"playwright-core/{tool}")
PY
)

(
  cd "$REPO_ROOT"
  python3 -m ui_clone.replay_track manifest-build \
    "$REPO_ROOT" "$MANIFEST_TMP" \
    scripts/extract/capture-replay-track.mjs \
    scripts/extract/capture-replay-track.sh \
    ui_clone/replay_track.py \
    package.json \
    --browser-version "$BROWSER_VERSION" \
    --tool-version "$TOOL_VERSION" >/dev/null
  python3 -m ui_clone.replay_track manifest-verify "$REPO_ROOT" "$MANIFEST_TMP" >/dev/null
)

mv "$TRACK_TMP" "$FINAL_OUT"
mv "$MANIFEST_TMP" "$FINAL_MANIFEST"
python3 - "$SUMMARY_TMP" "$FINAL_OUT" "$FINAL_MANIFEST" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    summary = json.load(handle)
summary["out"] = sys.argv[2]
summary["manifest"] = sys.argv[3]
print(json.dumps(summary, separators=(",", ":")))
PY
