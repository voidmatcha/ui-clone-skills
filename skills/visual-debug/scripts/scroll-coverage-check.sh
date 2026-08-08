#!/usr/bin/env bash
# scroll-coverage-check.sh — verification-plan-dispatchable wrapper that
# revives the previously-orphan `batch-scroll.sh` + `batch-compare.sh` pair.
#
# Why this exists:
#   `section-compare.sh` matches sections by DOM enumeration → on sites whose
#   ref `<main>` contains only `<div>` children it collapses to 1 container
#   (the whole page). Coverage drops to 2 sections vs the 16+ that section-
#   map.json finds at extraction time. This wrapper captures section-aligned
#   anchors first (ref section N ↔ impl section N), including sticky/pinned
#   entry/mid/exit probes; it falls back to every-10%-of-scroll only when
#   semantic anchor planning cannot produce enough pairs.
#
# Why the prior scripts went unused:
#   `batch-scroll.sh` + `batch-compare.sh` already exist but were never
#   wired into verification-plan dispatch — `scripts/verify/auto-verify.sh`
#   was the sole caller. Operator demanded fix.
#
# Usage: scroll-coverage-check.sh <ref-dir> [<orig-url> <impl-url> <session>]
#   ref-dir         the canonical ref dir
#   orig-url        ref site URL (defaults from regions.json `sourceUrl` if present)
#   impl-url        local impl URL (defaults to http://localhost:3000)
#   session         agent-browser session (defaults `scroll-coverage-check`)
#
# Writes:
#   <ref-dir>/scroll-coverage.json      — schemaVersion 1, status, points, fail_count
#   <ref-dir>/static/scroll-anchors.json — section/sticky anchor plan when available
#   <ref-dir>/static/{ref,impl}/*.png    — section-anchor or fallback pct captures
#
# Pass criteria:
#   pass  — fewer than 30% of sampled points exceed AE/Mpx threshold
#           (configurable via SCROLL_COVERAGE_FAIL_PCT, default 30)
#   fail  — 30%+ of points exceed threshold
#   skip  — no regions.json/section-map.json coverage source OR fewer than
#           5 regions/sections (small page) OR impl URL not reachable

set -uo pipefail

# Source the timeout shim so macOS gets a working `timeout` cmd. See
# scripts/lib/timeout-shim.sh.
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SHIM="$_SCRIPT_DIR/../../../scripts/lib/timeout-shim.sh"
[ -f "$_SHIM" ] && . "$_SHIM" || true

REF_DIR="${1:?Usage: scroll-coverage-check.sh <ref-dir> [<orig-url> <impl-url> <session>]}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

ORIG_URL="${2:-}"
IMPL_URL="${3:-http://localhost:3000}"
SESSION="${4:-scroll-coverage-check}"

REGIONS="$REF_DIR/regions.json"
SECTION_MAP="$REF_DIR/section-map.json"
OUT="$REF_DIR/scroll-coverage.json"
THRESHOLD="${SCROLL_COVERAGE_THRESHOLD:-4000}"   # AE/Mpx per scroll point
FAIL_PCT="${SCROLL_COVERAGE_FAIL_PCT:-30}"      # tolerated fail-rate

# skip_class (5th arg) machine-classifies WHY a skip happened so the consumer
# gate can tell apart a legitimate page-shape skip (short/static page — stays a
# pass) from an infra skip (impl unreachable / no rows / capture failed — fails
# closed when scroll-scrub motion was declared). Empty on pass/fail records.
write_status() {
  local status="$1" points="$2" failed="$3" reason="$4" skip_class="${5:-}"
  python3 - "$OUT" "$status" "$points" "$failed" "$THRESHOLD" "$FAIL_PCT" "$reason" "$skip_class" <<'PY'
import json, sys
out_path, status, points, failed, threshold, fail_pct, reason, skip_class = sys.argv[1:]
payload = {
    "schemaVersion": 1,
    "status": status,
    "points": int(points),
    "failed_points": int(failed),
    "threshold": int(threshold),
    "fail_pct_tolerated": int(fail_pct),
    "reason": reason,
}
if skip_class:
    payload["skipClass"] = skip_class
with open(out_path, "w") as fh:
    json.dump(payload, fh, indent=2)
PY
}

# Skip-paths first. regions.json can legitimately collapse to one full-page
# region when DOM enumeration misses semantic sections; section-map.json is the
# extraction-time source of truth for page section count in that case.
if [ ! -f "$REGIONS" ] && [ ! -f "$SECTION_MAP" ]; then
  write_status skip 0 0 "regions.json and section-map.json absent — extraction not complete" page-shape
  echo "▸ scroll-coverage: SKIP (no regions.json or section-map.json)"
  exit 0
fi

REGION_COUNT=$(python3 -c "
import json
from pathlib import Path

def count_regions(path):
    if not path.is_file():
        return 0
    try:
        d = json.load(open(path))
    except Exception:
        return 0
    if isinstance(d, dict):
        rows = d.get('regions') or d.get('sections') or []
        if isinstance(rows, list):
            return len(rows)
        if isinstance(d.get('totalCount'), int):
            return int(d.get('totalCount'))
        return 0
    if isinstance(d, list):
        return len(d)
    return 0

try:
    regions = count_regions(Path('$REGIONS'))
    sections = count_regions(Path('$SECTION_MAP'))
    print(max(regions, sections))
except Exception:
    print(0)
" 2>/dev/null || echo "0")

if [ "${REGION_COUNT:-0}" -lt 5 ]; then
  write_status skip 0 0 "regions/section-map have only ${REGION_COUNT} regions/sections — coverage redundant for short pages" page-shape
  echo "▸ scroll-coverage: SKIP (only ${REGION_COUNT} regions/sections)"
  exit 0
fi

# Default ORIG_URL from regions.json if not provided
if [ -z "$ORIG_URL" ] && [ -f "$REGIONS" ]; then
  ORIG_URL=$(python3 -c "
import json
try:
    d = json.load(open('$REGIONS'))
    print(d.get('sourceUrl', '') if isinstance(d, dict) else '')
except Exception:
    print('')
" 2>/dev/null)
fi
if [ -z "$ORIG_URL" ]; then
  write_status skip 0 0 "no orig-url available — pass explicitly or set sourceUrl in regions.json" config
  echo "▸ scroll-coverage: SKIP (no orig-url)"
  exit 0
fi

# Impl URL reachable? Follow redirects and accept any 2xx/3xx (matches
# runtime-env tolerance) — a healthy impl that 301/302s "/"→"/en" or adds a
# trailing slash is reachable, not an infra failure.
IMPL_CODE=$(curl -sL --connect-timeout 5 --max-time 15 -o /dev/null -w "%{http_code}" "$IMPL_URL" 2>/dev/null || echo "000")
case "$IMPL_CODE" in
  2*|3*) ;;
  *)
    write_status skip 0 0 "impl URL not reachable: $IMPL_URL (http $IMPL_CODE)" infra
    echo "▸ scroll-coverage: SKIP (impl unreachable, http $IMPL_CODE)"
    exit 0
    ;;
esac

# Drive the pair
SCRIPT_DIR="$_SCRIPT_DIR"
echo "▸ scroll-coverage: capture (batch-scroll.sh)..."
BS_OUT=$(bash "$SCRIPT_DIR/batch-scroll.sh" "$ORIG_URL" "$IMPL_URL" "$SESSION" "$REF_DIR" 2>&1)
BS_RC=$?
echo "$BS_OUT" | tail -10
if [ "$BS_RC" -ne 0 ]; then
  write_status fail 0 0 "batch-scroll capture failed or produced missing screenshots"
  echo "✗ scroll-coverage: FAIL (batch-scroll capture failed)" >&2
  exit 1
fi
echo "▸ scroll-coverage: compare (batch-compare.sh)..."
BC_OUT=$(bash "$SCRIPT_DIR/batch-compare.sh" "$REF_DIR" "$THRESHOLD" 2>&1)
echo "$BC_OUT" | tail -25

# Parse batch-compare table: count data rows + fails. Rows may be legacy
# "25pct" names or section/sticky anchor names such as "hero__mid".
POINTS=$(echo "$BC_OUT" | awk -F'|' '
  /^\|/ && $2 !~ /Position/ && $2 !~ /---/ {
    status=$5
    if (status ~ /✅|❌|⚠️/) count++
  }
  END { print count + 0 }
' || true)
FAILED=$(echo "$BC_OUT" | awk -F'|' '
  /^\|/ && $2 !~ /Position/ && $2 !~ /---/ {
    status=$5
    if (status ~ /❌|⚠️/) count++
  }
  END { print count + 0 }
' || true)

if [ "${POINTS:-0}" -eq 0 ]; then
  write_status skip 0 0 "batch-compare produced no rows — capture failed?" infra
  echo "✗ scroll-coverage: SKIP (no compare rows)" >&2
  exit 0
fi

# Compute fail rate
ACTUAL_FAIL_PCT=$(python3 -c "print(int(100 * ${FAILED:-0} / max(1, ${POINTS:-1})))")

if [ "${ACTUAL_FAIL_PCT:-0}" -le "${FAIL_PCT:-30}" ]; then
  write_status pass "$POINTS" "$FAILED" "$FAILED of $POINTS scroll points failed (${ACTUAL_FAIL_PCT}% ≤ ${FAIL_PCT}%)"
  echo "✓ scroll-coverage: PASS ($FAILED/$POINTS = ${ACTUAL_FAIL_PCT}%)"
  exit 0
else
  write_status fail "$POINTS" "$FAILED" "$FAILED of $POINTS scroll points failed (${ACTUAL_FAIL_PCT}% > ${FAIL_PCT}%)"
  echo "✗ scroll-coverage: FAIL ($FAILED/$POINTS = ${ACTUAL_FAIL_PCT}%)" >&2
  exit 1
fi
