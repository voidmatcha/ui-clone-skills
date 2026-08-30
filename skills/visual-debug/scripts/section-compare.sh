#!/usr/bin/env bash
# section-compare.sh — Compare original vs implementation by section
#
# Usage: bash section-compare.sh <orig-url> <impl-url> <session> [output-dir]
#
# Instead of full-page scroll screenshots, this script:
# 1. Enumerates semantic sections on both sites
# 2. Matches sections by text content similarity
# 3. Crops element-level screenshots per section
# 4. Runs AE comparison per section
# 5. Diffs computedStyle + DOM structure per section
#
# Output: <dir>/sections/{ref,impl,diff}/<section-name>.png
#         <dir>/sections/report.json
#
# This eliminates scroll-alignment noise from full-page comparisons.

set -euo pipefail

# W-4 (loop-ebpb-0): the reference follows prefers-color-scheme — a host
# OS theme flip (macOS auto-dark in the evening) silently captured the ref
# in dark mode and poisoned an entire compare cycle (footer dSSIM
# 0.0000065 -> 0.687 reading as catastrophic regression). Pin light unless
# the caller explicitly overrides.
: "${AGENT_BROWSER_COLOR_SCHEME:=light}"
export AGENT_BROWSER_COLOR_SCHEME

VIEW_W="${VIEW_W:-1440}"
VIEW_H="${VIEW_H:-900}"
NO_IMAGES="${NO_IMAGES:-0}"
WAIT_REF="${WAIT_REF:-8000}"
WAIT_IMPL="${WAIT_IMPL:-6000}"
WAIT_LAZY_LOAD="${WAIT_LAZY_LOAD:-2}"
# H9 (loop-nvti-3/4): a fixed 0.5s settle captured choreography-alive refs
# MID-TRANSITION (1.06s CSS transitions + rest re-eval) — transient ref crops
# overturned two eyeball observations. When the caller does not pin
# WAIT_SCROLL_SETTLE, it is DERIVED from the spec's longest transition
# duration (+0.4s rest margin, floor 0.5s, cap 4s) after DIR is known below.
WAIT_SCROLL_SETTLE_USER="${WAIT_SCROLL_SETTLE:-}"
WAIT_SCROLL_SETTLE="${WAIT_SCROLL_SETTLE:-0.5}"

# ── Frozen-ref mode (opt-in) ─────────────────────────────────────────
# RECATCH_REF=1 (DEFAULT): delete + re-capture the ref live every run —
#   byte-identical to historical behavior. Live GSAP idle-drift / lazy
#   media make AE & DSSIM bounce ±3-5% run-to-run on the ref side.
# RECATCH_REF=0: when frozen ref artifacts already exist (sections/ref/*.png
#   AND sections/ref-sections.json), REUSE them — skip ref-side delete,
#   browser open, enumeration, and capture. The impl is still captured
#   fresh and the per-section diff runs against the frozen crops. This
#   removes ref-side run-to-run noise so AE/DSSIM are deterministic.
#   Provenance (url/viewport/timestamp/crop count) is stamped to
#   sections/frozen-ref-provenance.json when frozen crops are reused.
RECATCH_REF="${RECATCH_REF:-1}"

# ── Perceptual-dense PASS (default ON; =0 is the strict escape hatch) ──
# Promoted to default-ON after a cross-site decision (scratch/perceptual-decide):
# refStd guard closes the blank-ref hole, and cross-site validation showed
# clean faithful/wrong separation with an independent vision judge. The content-based matcher
# (Fix 22) removed the mis-pairing confound.
# SECTION_PERCEPTUAL_DENSE=0: strict escape hatch — the strict
#   AE/Mpx + pass-by-dssim(<=0.015) ladder is the only pass path.
# SECTION_PERCEPTUAL_DENSE=1 (DEFAULT): a section may PASS as `pass-by-perceptual`
#   (a genuine ✅, counted like pass-by-dssim) IFF ALL hold:
#     1. it is DENSE by REF morphology (ref has a non-empty text
#        fingerprint or SVG-text) — classified from REF evidence only, so
#        deleting impl content can never earn relaxed scoring;
#     2. global dssim <= SECTION_DSSIM_DENSE_MAX and AE/Mpx < saturation;
#     3. ZERO Critical and ZERO Major structure deltas for the section —
#        this combines (a) the existing Step-5 DOM structure-mismatch
#        logic AND (b) a regional-locality check: a misplaced element
#        leaves a catastrophic local band (dssim >= SECTION_DSSIM_LOCAL_FAIL)
#        even when the global dssim passes. Uniform font-AA / idle-drift
#        noise does not. This is the gate that FAILS a section whose global
#        dssim is low but which has a real localized structural defect;
#     4. the ref-screenshot-asset scan is clean (no screenshot cheat).
#   Simple (non-dense) sections keep strict AE. Motion-critical
#   STRUCTURAL_ONLY protection and the 50% STRUCTURAL_ONLY ratio cap stay.
#
# Calibration (one <slug> reference, frozen crops): a faithful text-dense
# <navbar> section floors at global dssim 0.0999 with a worst 200px-band
# dssim of 0.0999; a buggy <about> section sits at a LOWER global dssim
# 0.0947, yet its single misplaced label element produces a worst band
# dssim of 0.677. DENSE_MAX=0.12 admits both as candidates (and excludes a
# <hero> at 0.169); LOCAL_FAIL=0.30 then FAILs <about> (band 0.677 >> 0.30)
# while passing <navbar> (band 0.0999 << 0.30). The buggy section's lower
# GLOBAL dssim is exactly why dssim-alone is unsafe and the localized band
# gate is required.
SECTION_PERCEPTUAL_DENSE="${SECTION_PERCEPTUAL_DENSE:-1}"
SECTION_DSSIM_DENSE_MAX="${SECTION_DSSIM_DENSE_MAX:-0.12}"
SECTION_DSSIM_LOCAL_FAIL="${SECTION_DSSIM_LOCAL_FAIL:-0.30}"
SECTION_LOCAL_BAND_PX="${SECTION_LOCAL_BAND_PX:-200}"
# Strict-dssim rescue ceiling (pass-by-dssim-strict): a responsive (un-baked)
# impl rasterizes glyphs at a different subpixel phase than a pixel-frozen ref
# crop, so AE saturates past dssim_cap and AE_SATURATION while dSSIM stays
# ~1e-6..0.02 (loop-ebpb-3: 8 rows <= 0.0212, byte-continuous across the
# un-bake; real defects measured 0.118/0.199 — ~5x separation). 0.03 sits in
# that gap, 4x tighter than SECTION_DSSIM_DENSE_MAX.
SECTION_DSSIM_STRICT_MAX="${SECTION_DSSIM_STRICT_MAX:-0.03}"

# ── Motion shift-search (Commit 2) ──
# A faithful scroll-reveal section can be caught at a different scroll sub-frame
# than the ref (identical content, uniformly translated tens-to-low-hundreds px).
# A wide NON-circular vertical shift-search realigns it; a broken impl does not
# (vetoed by motion_phase_verdict's collapse + structure + localized guards).
SECTION_MOTION_PHASE_MAX_PX="${SECTION_MOTION_PHASE_MAX_PX:-240}"   # widest shift searched (each direction)
SECTION_MOTION_PHASE_STEP="${SECTION_MOTION_PHASE_STEP:-8}"          # coarse sweep granularity in px
SECTION_MOTION_COLLAPSE_MIN="${SECTION_MOTION_COLLAPSE_MIN:-0.15}"   # min AE drop ratio to count as a phase
SECTION_MOTION_MIN_STRUCT="${SECTION_MOTION_MIN_STRUCT:-0.85}"       # shifted structure floor (1 - dssim)
SECTION_MOTION_PHASE="${SECTION_MOTION_PHASE:-1}"                    # master enable (1 default; 0 disables tier)

# ── Dynamic-content exclusion ──
# RAF-driven canvases (Three.js shaders, particles), <video>, and other
# auto-running animations produce per-frame pixel noise that AE can never
# match because ref and impl run on independent clocks. Hiding these
# elements via `visibility: hidden` (applied identically to ref + impl)
# removes the noise without affecting layout.
#
#   EXCLUDE_DYNAMIC=1                                     # DEFAULT (set 0 to opt out)
#   DYNAMIC_SELECTORS="canvas, video, .ticker"            # override defaults
#   transition-spec.json entries with `"dynamic": true`   # auto-augment
#
# EXCLUDE_DYNAMIC default flipped 0 → 1 after the c9b638d/d19e28d benchmarks:
# both runs measured AE against ref videos that hadn't paused at the same
# frame as impl videos (codec/scheduler variance), producing 1M+ AE on
# sections whose static layout was actually close to ref. The existing
# pause logic (animation-play-state, video.pause()+currentTime=0) handles
# CSS loops + <video> auto-restart, but a video element that simply
# renders a different decoded frame at currentTime=0 between captures
# still diffs catastrophically — masking it out is the only deterministic
# option. Motion fidelity is validated SEPARATELY by transition-compare /
# video-motion-compare, NOT by section-compare. Opt back into per-pixel
# motion-in-section by setting `EXCLUDE_DYNAMIC=0`.
EXCLUDE_DYNAMIC="${EXCLUDE_DYNAMIC:-1}"
DYNAMIC_SELECTORS="${DYNAMIC_SELECTORS:-canvas, video, iframe}"

ORIG_URL="${1:?Usage: section-compare.sh <orig-url> <impl-url> <session> [output-dir]}"
IMPL_URL="${2:?Usage: section-compare.sh <orig-url> <impl-url> <session> [output-dir]}"
SESSION="${3:?Usage: section-compare.sh <orig-url> <impl-url> <session> [output-dir]}"
DIR="${4:-tmp/ref/visual-debug}"
# ⚠️  IMPORTANT: Always pass $4 = absolute path to tmp/ref/<component-name>.
# The default (tmp/ref/visual-debug) is for standalone runs only.
# The Stop gate looks for sections/result.txt in the ACTIVE REF_DIR (which is absolute).
# If you use the default, the Stop gate will NEVER clear because result.txt is in the wrong place.
#
# Correct usage:
#   bash section-compare.sh <orig> <impl> <session> "$(pwd)/tmp/ref/<component>"
if [ "$DIR" = "tmp/ref/visual-debug" ]; then
  echo "⚠️  WARNING: Using default output-dir 'tmp/ref/visual-debug'." >&2
  echo "   The Stop gate hook won't find this result. Pass the component ref dir as \$4:" >&2
  echo "   bash section-compare.sh <orig> <impl> <session> \"\$(pwd)/tmp/ref/<component>\"" >&2
fi
# Convert to absolute path (if relative, resolve from PWD)
if [[ "$DIR" != /* ]]; then
  DIR="$(pwd)/$DIR"
fi

# Direct per-viewport reruns commonly point DIR at
# <ref>/sections/viewports/<WxH> without going through the fan-out wrapper that
# exports REF_ROOT_DIR. Infer that canonical ref root so transition-spec,
# section-map, substitution, and selector config fallbacks remain available.
if [ -z "${REF_ROOT_DIR:-}" ]; then
  case "$DIR" in
    */sections/viewports/*)
      _viewport_ref_root="${DIR%%/sections/viewports/*}"
      if [ -d "$_viewport_ref_root" ]; then
        REF_ROOT_DIR="$_viewport_ref_root"
        export REF_ROOT_DIR
      fi
      ;;
  esac
fi

# H9 settle derivation (see header note): only when the caller did not pin it.
_SETTLE_SPEC="$DIR/transition-spec.json"
if [ ! -f "$_SETTLE_SPEC" ] && [ -n "${REF_ROOT_DIR:-}" ] && [ -f "${REF_ROOT_DIR}/transition-spec.json" ]; then
  _SETTLE_SPEC="${REF_ROOT_DIR}/transition-spec.json"
fi
if [ -z "$WAIT_SCROLL_SETTLE_USER" ] && [ -f "$_SETTLE_SPEC" ]; then
  _derived_settle=$(PYTHONPATH="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"     python3 -m ui_clone.section_capture --print-settle "$_SETTLE_SPEC" 2>/dev/null || echo "")
  case "$_derived_settle" in
    ''|*[!0-9.]*) : ;; # non-numeric — keep the 0.5 default
    *) WAIT_SCROLL_SETTLE="$_derived_settle" ;;
  esac
fi

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
# AE ceiling for the dssim/perceptual leniency branches (+ visual-judge
# override path). See lib/dssim-cap.sh header.
# shellcheck source=lib/dssim-cap.sh
. "$SCRIPTS_DIR/lib/dssim-cap.sh"
# Navigation watchdog: shadows `agent-browser` so a dead/unreachable URL on the
# open/goto/navigate path fails fast (UI_CLONE_AB_OPEN_TIMEOUT, default 30s)
# instead of deadlocking this compare. See lib/ab-timeout.sh header.
# shellcheck source=lib/ab-timeout.sh
. "$SCRIPTS_DIR/lib/ab-timeout.sh"
# Frozen section-map provenance: advisory only, used to warn when reused ground
# truth was captured before the idle-reset rule or while a page state was open.
# shellcheck source=lib/idle-reset.sh
. "$SCRIPTS_DIR/lib/idle-reset.sh"
# AE unit normalization: ImageMagick 7.1.2-27 Q16 returns `compare -metric AE`
# as pixel_count * 65535 (QuantumRange), not the raw count. Every AE flows
# through _ae_at, which normalizes via _ae_normalize. See lib/ae-quantum.sh.
# shellcheck source=lib/ae-quantum.sh
. "$SCRIPTS_DIR/lib/ae-quantum.sh"
SECTION_DSSIM_AE_CAP_MULT="${SECTION_DSSIM_AE_CAP_MULT:-10}"
REPO_ROOT="$(cd "$SCRIPTS_DIR/../../.." && pwd)"

# ── Optional multi-viewport fan-out ─────────────────────────────────
# VIEWPORTS is intentionally opt-in. Unset keeps the historical single
# viewport path exactly as the inner runner. When set, the wrapper re-runs
# this script once per WxH value with VIEW_W/VIEW_H exported, stores each
# viewport's full artifact tree under sections/viewports/<WxH>/, then writes
# a canonical aggregate sections/result.txt for gates and completion reports.
if [ -n "${VIEWPORTS:-}" ] && [ "${SECTION_COMPARE_INNER:-0}" != "1" ]; then
  SECTION_COMPARE_INNER_CMD="${SECTION_COMPARE_INNER_CMD:-$0}"

  # Validate the full fan-out before touching canonical evidence. Previously the
  # wrapper truncated result.txt before discovering a malformed later entry,
  # leaving a header-only file that could not honestly represent either the last
  # complete sweep or the interrupted one.
  IFS=',' read -r -a RAW_VIEWPORT_LIST <<< "$VIEWPORTS"
  VIEWPORT_LIST=()
  VIEWPORT_COUNT=0
  for RAW_VIEWPORT in "${RAW_VIEWPORT_LIST[@]}"; do
    VP="$(printf '%s' "$RAW_VIEWPORT" | tr -d '[:space:]')"
    if [ -z "$VP" ]; then
      continue
    fi
    if [[ ! "$VP" =~ ^[0-9]+x[0-9]+$ ]]; then
      echo "ERROR: malformed VIEWPORTS entry '$RAW_VIEWPORT' (expected WIDTHxHEIGHT)" >&2
      exit 2
    fi
    VIEWPORT_LIST[$VIEWPORT_COUNT]="$VP"
    VIEWPORT_COUNT=$((VIEWPORT_COUNT + 1))
  done
  if [ "$VIEWPORT_COUNT" -eq 0 ]; then
    echo "ERROR: VIEWPORTS did not contain any WIDTHxHEIGHT entries" >&2
    exit 2
  fi

  mkdir -p "$DIR/sections/viewports"
  RESULT_FILE="$DIR/sections/result.txt"
  RESULT_JSON_FILE="$DIR/sections/result.json"
  RESULT_TMP="$(mktemp "$DIR/sections/.result.txt.tmp.XXXXXX")"
  RESULT_JSON_TMP=""
  # Invoked indirectly by the EXIT trap below.
  # shellcheck disable=SC2329
  _cleanup_result_stage() {
    [ -z "${RESULT_TMP:-}" ] || rm -f "$RESULT_TMP"
    [ -z "${RESULT_JSON_TMP:-}" ] || rm -f "$RESULT_JSON_TMP"
  }
  trap '_result_status=$?; _cleanup_result_stage; exit "$_result_status"' EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  RESULT_JSON_TMP="$(mktemp "$DIR/sections/.result.json.tmp.XXXXXX")"
  {
    echo "# section-compare multi-viewport result"
    echo "viewports: $VIEWPORTS"
    echo ""
  } > "$RESULT_TMP"

  OVERALL=0
  for VP in "${VIEWPORT_LIST[@]}"; do
    VP_W="${VP%x*}"
    VP_H="${VP#*x}"
    VP_DIR="$DIR/sections/viewports/$VP"
    mkdir -p "$VP_DIR"

    {
      echo "viewport: $VP"
      echo ""
    } >> "$RESULT_TMP"

    echo "▸ section-compare viewport $VP"
    set +e
    # REF_ROOT_DIR: per-viewport runs write artifacts under
    # sections/viewports/<WxH>/, but ref-root-level inputs
    # (transition-spec.json, asset-substitution.json) only exist at $DIR —
    # without this the inner run silently resolved an empty spec and every
    # dynamic:true mask dropped out (loop-e2e-9 viewport-fanout-mask-gap).
    VIEW_W="$VP_W" VIEW_H="$VP_H" VIEWPORTS="" SECTION_COMPARE_INNER=1 \
      REF_ROOT_DIR="$DIR" \
      WAIT_SCROLL_SETTLE="$WAIT_SCROLL_SETTLE" \
      bash "$SECTION_COMPARE_INNER_CMD" "$ORIG_URL" "$IMPL_URL" "${SESSION}-${VP}" "$VP_DIR" \
      > "$VP_DIR/section-compare.log" 2>&1
    COMPARE_CODE=$?
    CODE=$COMPARE_CODE
    if command -v agent-browser >/dev/null 2>&1 \
      && [ -f "$REPO_ROOT/scripts/verify/cleanup-sessions.sh" ]; then
      CLEANUP_OUTPUT="$(
        bash "$REPO_ROOT/scripts/verify/cleanup-sessions.sh" "${SESSION}-${VP}" 2>&1
      )"
      CLEANUP_CODE=$?
      if [ "$CLEANUP_CODE" -ne 0 ]; then
        echo "ERROR: section-compare cleanup failed for viewport $VP session prefix '${SESSION}-${VP}' (exit $CLEANUP_CODE)" >&2
        [ -z "$CLEANUP_OUTPUT" ] || printf '%s\n' "$CLEANUP_OUTPUT" >&2
        if [ "$COMPARE_CODE" -eq 0 ]; then
          CODE=2
        fi
      fi
    fi
    set -e

    echo "[$VP] exit: $CODE" >> "$RESULT_TMP"
    if [ -f "$VP_DIR/sections/result.txt" ]; then
      python3 - "$VP" "$VP_DIR/sections/result.txt" >> "$RESULT_TMP" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

vp, result_path = sys.argv[1:3]
for raw in Path(result_path).read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw.rstrip("\n")
    stripped = line.strip()
    if stripped.startswith("|"):
        cells = line.split("|")
        first = cells[1].strip() if len(cells) > 1 else ""
        if (
            len(cells) > 2
            and first.lower() != "section"
            and not (first and set(first) <= {"-"})
        ):
            cells[1] = f" [{vp}] {cells[1].strip()} "
            print("|".join(cells))
            continue
    print(f"[{vp}] {line}" if line else "")
PY
    else
      echo "[$VP] sections/result.txt missing; see $VP_DIR/section-compare.log" >> "$RESULT_TMP"
    fi
    echo "" >> "$RESULT_TMP"

    if [ "$COMPARE_CODE" -ne 0 ]; then
      OVERALL=1
    elif [ "$CODE" -eq 2 ] && [ "$OVERALL" -eq 0 ]; then
      OVERALL=2
    fi
  done

  # Each inner run writes its own result.json. Write the derived top-level
  # helper from the aggregate result.txt after every viewport has completed so
  # post-command report consumers see totals across the full responsive sweep.
  python3 - "$RESULT_TMP" "$RESULT_JSON_TMP" <<'PY'
import json
import re
import sys
from pathlib import Path

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
text = src.read_text(encoding="utf-8", errors="replace")
row_re = re.compile(
    r"^\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<ae>[^|]*?)\s*\|"
    r"\s*(?P<aempx>[^|]*?)\s*\|\s*(?P<sev>[^|]*?)\s*\|"
    r"\s*(?P<status>[^|]*?)\s*\|\s*$"
)
summary_re = re.compile(
    r"\*\*Result: (\d+) PASS, (\d+) FAIL, (\d+) SKIP, "
    r"(\d+) STRUCTURAL_ONLY(?:, (\d+) UNMEASURED)?\*\*"
)

def num(value):
    try:
        return int(float(value.replace(",", "").strip()))
    except ValueError:
        return None

rows = []
for line in text.splitlines():
    match = row_re.match(line)
    if not match:
        continue
    name = match.group("name")
    if name == "Section" or (name and set(name) <= {"-"}):
        continue
    status_raw = match.group("status")
    if "✅" in status_raw or "PASS" in status_raw:
        status = "pass"
    elif "MISSING" in status_raw or "missing" in status_raw:
        status = "missing"
    elif "STRUCTURAL" in status_raw:
        status = "structural-only"
    elif "🌑" in status_raw:
        status = "saturated"
    elif "❌" in status_raw or "FAIL" in status_raw:
        status = "fail"
    elif "UNMEASURED" in status_raw:
        # Distinct from "unknown": the row was deliberately not compared because
        # the reference crop carried no signal. The self-healing loop classifier
        # reads this file, and "unknown" reads as a parse artifact it can ignore.
        status = "unmeasured"
    else:
        status = "unknown"
    rows.append({
        "name": name,
        "ae": num(match.group("ae")),
        "aePerMpx": num(match.group("aempx")),
        "severity": match.group("sev") or None,
        "status": status,
        "statusRaw": status_raw,
        "diffCrop": None,
    })

summary = {"pass": 0, "fail": 0, "skip": 0, "structuralOnly": 0}
for match in summary_re.finditer(text):
    summary["pass"] += int(match.group(1))
    summary["fail"] += int(match.group(2))
    summary["skip"] += int(match.group(3))
    summary["structuralOnly"] += int(match.group(4))

dst.write_text(json.dumps({
    "schemaVersion": 1,
    "source": "section-compare.sh",
    "summary": summary,
    "sections": rows,
}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
  # POSIX cannot atomically rename this two-file pair. result.txt is therefore
  # the sole canonical commit marker read by gates and Stop hooks; result.json is
  # a derived helper that consumers may read only after publication completes.
  # Publish the helper first, then atomically replace the commit marker after the
  # complete responsive sweep has been assembled.
  mv -f "$RESULT_JSON_TMP" "$RESULT_JSON_FILE"
  RESULT_JSON_TMP=""
  mv -f "$RESULT_TMP" "$RESULT_FILE"
  RESULT_TMP=""
  trap - EXIT HUP INT TERM
  exit "$OVERALL"
fi

# ── --only-if-changed short-circuit ──
# When ONLY_IF_CHANGED=1 and IMPL_SRC_DIR points at the implementation source
# tree, skip the full comparison if no impl source file has changed since the
# last successful run. The previous result.txt stays in place so the Stop
# gate's verification passes against it.
#
# Hash strategy: SHA-256 of (sorted relative paths + content) of every *.tsx
# /*.jsx/*.ts/*.css/*.scss under IMPL_SRC_DIR. mtime alone is unreliable
# because editor saves bump it without changing bytes.
#
# Usage:
#   ONLY_IF_CHANGED=1 IMPL_SRC_DIR=~/projects/foo/src \
#     bash section-compare.sh <orig> <impl> <session> "$(pwd)/tmp/ref/<c>"
ONLY_IF_CHANGED="${ONLY_IF_CHANGED:-0}"
IMPL_SRC_DIR="${IMPL_SRC_DIR:-}"
HASH_FILE="$DIR/sections/.last-impl-hash"

compute_impl_hash() {
  # Args: $1 = src dir. Echo a single sha256.
  ( cd "$1" && find . \
      \( -name '*.tsx' -o -name '*.jsx' -o -name '*.ts' -o -name '*.js' \
         -o -name '*.css' -o -name '*.scss' \) \
      -not -path '*/node_modules/*' \
      -not -path '*/.next/*' \
      -not -path '*/dist/*' \
      -not -path '*/build/*' \
      -type f -print0 2>/dev/null \
    | sort -z \
    | xargs -0 cat 2>/dev/null \
    | shasum -a 256 \
    | awk '{print $1}'
  )
}

if [ "$ONLY_IF_CHANGED" = "1" ]; then
  if [ -z "$IMPL_SRC_DIR" ] || [ ! -d "$IMPL_SRC_DIR" ]; then
    echo "ERROR: ONLY_IF_CHANGED=1 requires IMPL_SRC_DIR to point at the impl source tree" >&2
    echo "  current IMPL_SRC_DIR='$IMPL_SRC_DIR' (must exist and be a directory)" >&2
    exit 2
  fi
  CURRENT_HASH=$(compute_impl_hash "$IMPL_SRC_DIR")
  if [ -z "$CURRENT_HASH" ]; then
    echo "WARNING: no source files found under $IMPL_SRC_DIR — proceeding with full run" >&2
  elif [ -f "$HASH_FILE" ] && [ -f "$DIR/sections/result.txt" ]; then
    PRIOR_HASH=$(cat "$HASH_FILE" 2>/dev/null || echo "")
    if [ "$CURRENT_HASH" = "$PRIOR_HASH" ]; then
      # The hash tracks impl source only, and is written regardless of the prior
      # run's verdict. Reusing a run that did not converge would report exit 0 for
      # a result.txt that says otherwise — and that is the *expected* path here,
      # because the remedy for an UNMEASURED row is capture-side and re-capturing
      # the reference does not change the impl hash.
      _prior_line=$(grep -E '^\*\*Result: ' "$DIR/sections/result.txt" 2>/dev/null | tail -1)
      _prior_fail=$(printf '%s\n' "$_prior_line" | sed -nE 's/^.*, ([0-9]+) FAIL,.*$/\1/p')
      _prior_unmeasured=$(printf '%s\n' "$_prior_line" | sed -nE 's/^.*, ([0-9]+) UNMEASURED\*\*$/\1/p')
      if [ -z "$_prior_unmeasured" ]; then
        # Artifact predates the UNMEASURED field — fall back to the table rows.
        _prior_unmeasured=$(grep -cE '^\|.*\| *unmeasured *\|' "$DIR/sections/result.txt" 2>/dev/null || true)
      fi
      if [ "${_prior_fail:-0}" -gt 0 ] || [ "${_prior_unmeasured:-0}" -gt 0 ]; then
        echo "▸ ONLY_IF_CHANGED: impl unchanged, but the prior run did not converge"
        echo "  (${_prior_fail:-0} FAIL, ${_prior_unmeasured:-0} UNMEASURED) — running full"
        echo "  compare rather than reusing it."
      else
        echo "▸ ONLY_IF_CHANGED: impl source hash matches prior run ($CURRENT_HASH)"
        echo "  → no source changes since last section-compare; reusing $DIR/sections/result.txt"
        echo "  (delete $HASH_FILE to force a full re-run)"
        exit 0
      fi
    else
      echo "▸ ONLY_IF_CHANGED: impl source changed (was ${PRIOR_HASH:0:12}..., now ${CURRENT_HASH:0:12}...) — running full compare"
    fi
  else
    echo "▸ ONLY_IF_CHANGED: no prior result.txt or hash file — running full compare"
  fi
fi

# Augment DYNAMIC_SELECTORS from transition-spec.json — entries with `dynamic: true`
# contribute their `target` selector. Ignored when EXCLUDE_DYNAMIC is off.
# Load optional project-local dynamic selector masks before validating and
# injecting DYNAMIC_SELECTORS. This keeps per-site dynamic/live chrome config
# data-only and avoids shell-sourcing repository files.
source "$SCRIPTS_DIR/lib/dynamic-selectors.sh"
load_section_dynamic_selectors_config

DYNAMIC_PAUSE_EXTRA=""
if [ "$EXCLUDE_DYNAMIC" = "1" ]; then
  TSPEC_FILE="$DIR/transition-spec.json"
  # Viewport fan-out: inner runs get DIR=sections/viewports/<WxH>/ which never
  # holds the spec — resolve from the ref root the wrapper passed instead of
  # silently masking nothing (loop-e2e-9 viewport-fanout-mask-gap).
  if [ ! -f "$TSPEC_FILE" ] && [ -n "${REF_ROOT_DIR:-}" ] && [ -f "${REF_ROOT_DIR}/transition-spec.json" ]; then
    TSPEC_FILE="${REF_ROOT_DIR}/transition-spec.json"
  fi
  if [ -f "$TSPEC_FILE" ]; then
    EXTRA_TARGETS=$(python3 -c "
import json
try:
    d = json.loads(open('$TSPEC_FILE').read())
    targets = [t.get('target','') for t in d.get('transitions', []) if t.get('dynamic') is True]
    print(', '.join(t for t in targets if t and '\"' not in t))
except Exception:
    print('')
" 2>/dev/null || echo "")
    if [ -n "$EXTRA_TARGETS" ]; then
      DYNAMIC_SELECTORS="$DYNAMIC_SELECTORS, $EXTRA_TARGETS"
    fi
  fi
  # Auto-augment with async-mounted WebGL-embed CONTAINERS. The default
  # `canvas` selector only hides a <canvas> that exists at snapshot time, but
  # Unicorn Studio ([data-us-project]), Spline (spline-viewer / [data-spline])
  # and Three.js/generic engines ([data-engine]) inject their <canvas> AFTER
  # init — so a faithfully re-embedded WebGL hero diffs catastrophically and
  # FAILs pixel-AE even when the hero was faithfully re-embedded. The container element
  # is present in the DOM early, so masking it hides the whole region
  # regardless of when the canvas paints. fix-not-loosen: this masks MORE
  # genuinely-dynamic regions; static sections are unaffected and the masked
  # region's motion is still verified by video-motion-compare. Symmetric on
  # ref + impl (the mask CSS is injected identically into both).
  DYNAMIC_SELECTORS="$DYNAMIC_SELECTORS, [data-us-project], spline-viewer, [data-spline], [data-engine]"
  # Selectors must not contain quote characters of either kind:
  #  - `"` would close the JS string inside the injected <style> textContent.
  #  - `'` would close the surrounding Python r'...' raw string in pause_js below.
  # Use bare attribute matchers (e.g. [data-canvas=hero]) or class/id selectors instead.
  if [[ "$DYNAMIC_SELECTORS" == *'"'* || "$DYNAMIC_SELECTORS" == *\'* ]]; then
    echo "ERROR: DYNAMIC_SELECTORS must not contain quote characters (\" or '). Use bare attribute matchers like [data-canvas=hero] or class/id selectors." >&2
    exit 1
  fi
DYNAMIC_PAUSE_EXTRA=" ${DYNAMIC_SELECTORS} { visibility: hidden !important; }"
if [ -n "${SECTION_FIXED_OVERLAY_SELECTORS:-}" ]; then
  DYNAMIC_PAUSE_EXTRA="${DYNAMIC_PAUSE_EXTRA} html[data-section-compare-scrolled=1] ${SECTION_FIXED_OVERLAY_SELECTORS} { visibility: hidden !important; }"
fi
if [ -n "${SECTION_IGNORE_SELECTORS:-}" ]; then
  DYNAMIC_PAUSE_EXTRA="${DYNAMIC_PAUSE_EXTRA} ${SECTION_IGNORE_SELECTORS} { display: none !important; }"
fi
  echo "▸ EXCLUDE_DYNAMIC=1 — masking: $DYNAMIC_SELECTORS"
fi

# Guard: spaces in DIR path break Python one-liners that embed $DIR in string literals
if [[ "$DIR" == *" "* ]]; then
  echo "ERROR: output-dir path contains spaces: '$DIR'" >&2
  echo "       Rename the directory to remove spaces before running section-compare.sh." >&2
  exit 1
fi

SESSION_REF="${SESSION}-sc-ref"
SESSION_IMPL="${SESSION}-sc-impl"
SESSION_REF_USED=0
SESSION_IMPL_USED=0

cleanup_browsers() {
  if [ "$SESSION_REF_USED" = "1" ]; then
    agent-browser --session "$SESSION_REF" close 2>/dev/null || true
    SESSION_REF_USED=0
  fi
  if [ "$SESSION_IMPL_USED" = "1" ]; then
    agent-browser --session "$SESSION_IMPL" close 2>/dev/null || true
    SESSION_IMPL_USED=0
  fi
}
trap cleanup_browsers EXIT

mkdir -p "$DIR/sections/ref" "$DIR/sections/impl" "$DIR/sections/diff"

# ── Frozen-ref reuse decision (Task A) ───────────────────────────────
# Must run BEFORE the stale-output cleanup below so frozen ref crops are
# preserved. REUSE_FROZEN_REF stays 0 on the default (RECATCH_REF=1) path,
# leaving every downstream branch unchanged.
REUSE_FROZEN_REF=0
if [ "$RECATCH_REF" != "1" ]; then
  shopt -s nullglob
  _frozen_ref_pngs=("$DIR/sections/ref/"*.png)
  shopt -u nullglob
  # Provenance guard (B2 interim): a prior run stamps frozen-ref-provenance.json
  # with the (url, viewport) the frozen crops belong to. That stamp used to be
  # write-only, so reusing another site's / another viewport's frozen crops was
  # silently possible. Read it back and REFUSE reuse (fall through to live
  # capture) when it disagrees with this run — a stale baseline directly
  # miscolors the verdict. Also warn (non-blocking) when the crops are older than
  # UI_CLONE_REF_FROZEN_TTL_SEC. No stamp (fresh PASS1 crops) → existence floor
  # decides as before. Entirely inside the non-default RECATCH_REF!=1 branch, so
  # the default live-capture path is byte-identical.
  _reuse_ok=1
  if [ -s "$DIR/sections/ref-sections.json" ] && [ "${#_frozen_ref_pngs[@]}" -gt 0 ] \
     && [ -f "$DIR/sections/frozen-ref-provenance.json" ]; then
    # `|| _prov_rc=$?` keeps the helper's deliberate `sys.exit(2)` (refuse) from
    # tripping `set -e` and aborting the whole script — exit 2 must FALL THROUGH
    # to live ref capture, not terminate the run.
    _prov_rc=0
    python3 - "$DIR/sections/frozen-ref-provenance.json" "$ORIG_URL" "$VIEW_W" "$VIEW_H" \
      "${UI_CLONE_REF_FROZEN_TTL_SEC:-86400}" <<'PY' || _prov_rc=$?
import calendar, json, sys, time
prov_path, url, vw, vh, ttl = sys.argv[1:6]
want_vp = f"{vw}x{vh}"
try:
    with open(prov_path) as fh:
        p = json.load(fh)
except Exception:
    sys.exit(0)  # unreadable/corrupt stamp -> don't block; existence floor applies
got_url, got_vp = p.get("refUrl"), p.get("viewport")
if (got_url and got_url != url) or (got_vp and got_vp != want_vp):
    sys.stderr.write(
        f"⚠ frozen-ref provenance mismatch: crops captured for {got_url!r} @ "
        f"{got_vp!r} but this run is {url!r} @ {want_vp!r} — refusing reuse, "
        f"re-capturing the ref live.\n"
    )
    sys.exit(2)  # refuse reuse
stamped = p.get("reusedAt") or p.get("capturedAt") or ""
try:
    age = time.time() - calendar.timegm(time.strptime(stamped[:19], "%Y-%m-%dT%H:%M:%S"))
    if int(ttl) > 0 and age > int(ttl):
        sys.stderr.write(
            f"⚠ frozen ref is ~{int(age // 3600)}h old (> TTL {ttl}s) — set "
            f"RECATCH_REF=1 to re-capture if the ref may have changed.\n"
        )
except Exception:
    pass
sys.exit(0)
PY
    [ "$_prov_rc" = "2" ] && _reuse_ok=0
  fi
  if [ "$_reuse_ok" = "1" ] \
     && [ -s "$DIR/sections/ref-sections.json" ] && [ "${#_frozen_ref_pngs[@]}" -gt 0 ]; then
    REUSE_FROZEN_REF=1
    echo "▸ RECATCH_REF=$RECATCH_REF — reusing ${#_frozen_ref_pngs[@]} frozen ref crop(s); skipping ref re-capture."
    python3 - "$DIR" "$ORIG_URL" "$VIEW_W" "$VIEW_H" "${#_frozen_ref_pngs[@]}" <<'PY' 2>/dev/null || true
import json, os, sys, time
d, url, vw, vh, n = sys.argv[1:6]
os.makedirs(os.path.join(d, "sections"), exist_ok=True)
stamp = {
    "mode": "frozen-ref-reuse",
    "refUrl": url,
    "viewport": f"{vw}x{vh}",
    "frozenRefCrops": int(n),
    "reusedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "note": "ref crops + ref-sections.json reused (RECATCH_REF=0); impl captured fresh.",
}
with open(os.path.join(d, "sections", "frozen-ref-provenance.json"), "w") as fh:
    json.dump(stamp, fh, indent=2)
PY
  elif [ "$_reuse_ok" = "1" ]; then
    echo "▸ RECATCH_REF=$RECATCH_REF set but no frozen ref artifacts (sections/ref/*.png + ref-sections.json) — falling back to live ref capture." >&2
  fi
fi

# Ref-side browser invocation. In frozen-ref mode every ref browser call is a
# no-op (cached crops are reused, so SESSION_REF is never opened). On the
# default path this is exactly `agent-browser --session "$SESSION_REF" "$@"`.
# Callers that redirect stdout to a FROZEN artifact (ref-sections.json) must
# NOT use this wrapper — the shell applies the redirect before the no-op runs
# and would truncate the file; those call sites are guarded explicitly instead.
ref_browser() {
  if [ "$REUSE_FROZEN_REF" = "1" ]; then return 0; fi
  agent-browser --session "$SESSION_REF" "$@"
}
# Dedicated wrapper for ref-side `eval` calls. Kept separate from ref_browser
# so the literal `eval` token stays inside this body (which contains
# `agent-browser` and is exempt from the bash-eval security scan) rather than
# appearing at every call site.
ref_eval() {
  if [ "$REUSE_FROZEN_REF" = "1" ]; then return 0; fi
  agent-browser --session "$SESSION_REF" eval "$@"
}

# Clean stale outputs from prior runs. Without this, deleted/renamed sections
# leave orphan PNGs that get picked up by the AE loop (REF_IMGS glob) and
# inflate the section count with stale entries that never re-render.
# Impl + diff are always cleaned; ref crops are preserved only when reusing
# frozen artifacts (RECATCH_REF=0).
rm -f "$DIR/sections/impl/"*.png "$DIR/sections/diff/"*.png 2>/dev/null || true
if [ "$REUSE_FROZEN_REF" != "1" ]; then
  rm -f "$DIR/sections/ref/"*.png 2>/dev/null || true
  # batch-13 ITEM 1: the ref-calib frames are part of the frozen ref baseline —
  # clean them only on a fresh ref capture, preserve them under RECATCH_REF=0 so
  # the dynamic classification survives into the verdict pass.
  rm -f "$DIR/sections/ref-calib/"*.png 2>/dev/null || true
  # The ref scroll-position manifest is rewritten on every fresh ref capture;
  # drop the stale one so a frozen impl never reuses a position from an older run.
  rm -f "$DIR/sections/ref-scroll-positions.json" 2>/dev/null || true
fi

# ── Asset substitution mode ──
# When the impl deliberately substitutes paid fonts / unlicensed images / videos
# with free replacements, AE pixel comparison is by-design meaningless for the
# affected sections — but layout/structure should still match the ref.
#
# Read $DIR/asset-substitution.json (if present) and build a list of section
# patterns to skip pixel comparison for. Schema:
#   {
#     "fonts":  [{ "original": "Exat", "replacement": "Roboto Flex", "reason": "..." }],
#     "images": [{ "originalSrc": "...", "replacementSrc": "...", "reason": "..." }],
#     "videos": [...],
#     "structuralOnlySections": ["main-hero", "*"]   // "*" matches every section
#   }
SUBSTITUTION_FILE="$DIR/asset-substitution.json"
# Same ref-root fallback as transition-spec.json above: per-viewport inner
# runs must not lose STRUCTURAL_ONLY switching (loop-e2e-9).
if [ ! -f "$SUBSTITUTION_FILE" ] && [ -n "${REF_ROOT_DIR:-}" ] && [ -f "${REF_ROOT_DIR}/asset-substitution.json" ]; then
  SUBSTITUTION_FILE="${REF_ROOT_DIR}/asset-substitution.json"
fi
SUBSTITUTION_PATTERNS=""
SUBSTITUTION_ALL=0
SUBSTITUTION_AUTO=0    # 1 if structuralOnlySections was auto-defaulted
if [ -f "$SUBSTITUTION_FILE" ]; then
  # Observed failure mode across benchmark baselines: the agent writes
  # asset-substitution.json with only `fonts`/`images` declared and
  # forgets the `structuralOnlySections` key — which is the actual toggle for
  # structural-only mode. Result: pixel diff still runs strict on every
  # section, AE explodes to 1M+, gate never clears. Forgiving fallback: when
  # fonts/images/videos are non-empty but structuralOnlySections is missing,
  # auto-default to ["*"] and log a hint so the agent can promote it next run.
  PARSED=$(python3 -c "
import json
try:
    d = json.loads(open('$SUBSTITUTION_FILE').read())
    pats = d.get('structuralOnlySections', [])
    if not isinstance(pats, list): pats = []
    has_subs = any(
        isinstance(d.get(k), list) and d.get(k)
        for k in ('fonts','images','videos')
    )
    auto = 0
    if has_subs and not pats:
        pats = ['*']
        auto = 1
    print(' '.join(p for p in pats if isinstance(p, str)))
    print(auto)
except Exception:
    print('')
    print('0')
" 2>/dev/null)
  SUBSTITUTION_PATTERNS="$(echo "$PARSED" | sed -n '1p')"
  SUBSTITUTION_AUTO="$(echo "$PARSED" | sed -n '2p')"
  case " $SUBSTITUTION_PATTERNS " in
    *" * "*) SUBSTITUTION_ALL=1 ;;
  esac
  if [ -n "$SUBSTITUTION_PATTERNS" ]; then
    echo "▸ Asset substitution mode active: pixel diff skipped for [$SUBSTITUTION_PATTERNS]"
    if [ "$SUBSTITUTION_AUTO" = "1" ]; then
      echo "  ⚠ structuralOnlySections key was MISSING — auto-defaulted to [\"*\"] because fonts/images/videos were declared."
      echo "  ⚠ Add \"structuralOnlySections\": [\"*\"] (or specific section names) to $SUBSTITUTION_FILE to make this explicit."
    fi
  fi
fi

# Template-mode escape valve restriction (Common cheat pattern): wildcard "*"
# only honored when paid-features.json has at least one finding (paid font /
# paid SDK / paid asset). Otherwise the wildcard is downgraded to "no
# substitution at all" — agent must declare per-section or download the
# real asset. This blocks the "declare wholesale substitution to skip
if [ "$SUBSTITUTION_ALL" = "1" ]; then
  PAID_FEATURES_FILE="$DIR/paid-features.json"
  has_paid=$(python3 -c "
import json, sys
try:
    d = json.loads(open('$PAID_FEATURES_FILE').read())
    fonts = d.get('paidFonts', []) or []
    sdks = d.get('paidSdks', []) or []
    assets = d.get('paidAssets', []) or []
    print(1 if (fonts or sdks or assets) else 0)
except Exception:
    print(0)
" 2>/dev/null)
  if [ "$has_paid" != "1" ]; then
    echo "  ⛔ Wildcard substitution [\"*\"] REJECTED — paid-features.json shows no paid font / SDK / asset."
    echo "    Wholesale substitution requires evidence (a paid foundry or commercial CDN). Without it,"
    echo "    agent must either (a) download the asset for real, or (b) declare per-section substitution"
    echo "    with concrete substitution targets (not 'emoji-or-gradient')."
    SUBSTITUTION_ALL=0
    SUBSTITUTION_PATTERNS=""
  fi
fi

# Motion-critical sections cannot be downgraded to STRUCTURAL_ONLY. Pixel AE can
# be noisy for substituted media, but sections that own scroll/pin/scrub,
# Lottie, Swiper/card rails, hover/click transitions, or required video/Lottie
# assets still need runtime/motion evidence instead of a settled-frame escape.
# Patterns are derived from transition-spec.json + required-media.json and can
# be extended with MOTION_STRUCTURAL_ONLY_PATTERNS="again finish" for one-off
# audits.
AUTO_MOTION_STRUCTURAL_ONLY_PATTERNS=$(python3 - "$DIR" <<'PY' 2>/dev/null || true
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
patterns: set[str] = set()

def read_json(name: str):
    try:
        return json.loads((ref_dir / name).read_text(encoding="utf-8"))
    except Exception:
        return None

def add_pattern(value: object) -> None:
    if not isinstance(value, str):
        return
    text = value.strip().lower()
    if not text:
        return
    text = re.sub(r"^[#.]", "", text)
    text = re.sub(r"[^a-z0-9_-]+", "-", text).strip("-")
    if len(text) >= 4:
        patterns.add(text)
    for token in re.split(r"[-_\s]+", text):
        if len(token) >= 5:
            patterns.add(token)

spec = read_json("transition-spec.json")
if isinstance(spec, dict):
    for item in spec.get("transitions") or spec.get("entries") or []:
        if not isinstance(item, dict):
            continue
        trigger = str(item.get("trigger") or item.get("type") or "").lower()
        blob = json.dumps(item, ensure_ascii=False).lower()
        if not re.search(r"scroll|scrub|pin|lottie|swiper|hover|click|motion|transition", trigger + " " + blob):
            continue
        for key in ("id", "name", "section", "sectionName", "target", "selector"):
            add_pattern(item.get(key))
        target = item.get("target")
        if isinstance(target, dict):
            for key in ("section", "sectionName", "selector", "id", "class"):
                add_pattern(target.get(key))

required = read_json("required-media.json")
if isinstance(required, dict):
    for key in ("videos", "lottie"):
        for item in required.get(key) or []:
            if isinstance(item, dict):
                add_pattern(item.get("section"))
                add_pattern(item.get("container"))
                add_pattern(item.get("containerId"))
                add_pattern(item.get("id"))

print(" ".join(sorted(patterns)))
PY
)
MOTION_STRUCTURAL_ONLY_PATTERNS="${MOTION_STRUCTURAL_ONLY_PATTERNS:-} ${AUTO_MOTION_STRUCTURAL_ONLY_PATTERNS:-}"
if [ -n "$(echo "$MOTION_STRUCTURAL_ONLY_PATTERNS" | tr -d '[:space:]')" ]; then
  echo "▸ Motion STRUCTURAL_ONLY protection active: [$MOTION_STRUCTURAL_ONLY_PATTERNS]"
fi

is_motion_structural_only_protected() {
  local name_lc
  name_lc=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
  for PAT in $MOTION_STRUCTURAL_ONLY_PATTERNS; do
    [ -z "$PAT" ] && continue
    PAT=$(printf '%s' "$PAT" | tr '[:upper:]' '[:lower:]')
    case "$name_lc" in
      *"$PAT"*) return 0 ;;
    esac
  done
  return 1
}

echo "═══ Section-Level Comparison ═══"
echo "Original: $ORIG_URL"
echo "Implementation: $IMPL_URL"
echo ""

# ── Open both sites ──
# Set viewport BEFORE opening so the page's init JS reads the correct
# window.innerWidth. Bundles that compute one-shot mobile/desktop branches at
# load time (e.g. `isMobile = window.innerWidth < 1024`) do not re-evaluate on
# resize — opening at the default viewport then resizing leaves the page in a
# broken half-mobile state that bears no resemblance to a real mobile load.
echo "▸ Opening both sites..."
if [ "$REUSE_FROZEN_REF" != "1" ]; then
  SESSION_REF_USED=1
fi
set +e
ref_browser set viewport "$VIEW_W" "$VIEW_H" > /dev/null
REF_VIEWPORT_RC=$?
set -e
if [ "$REF_VIEWPORT_RC" -ne 0 ]; then
  echo "ERROR: failed to set reference viewport ${VIEW_W}x${VIEW_H} for session '$SESSION_REF'" >&2
  exit 2
fi
SESSION_IMPL_USED=1
set +e
agent-browser --session "$SESSION_IMPL" set viewport "$VIEW_W" "$VIEW_H" > /dev/null
IMPL_VIEWPORT_RC=$?
set -e
if [ "$IMPL_VIEWPORT_RC" -ne 0 ]; then
  echo "ERROR: failed to set implementation viewport ${VIEW_W}x${VIEW_H} for session '$SESSION_IMPL'" >&2
  exit 2
fi

ref_browser open "$ORIG_URL" 2>&1 | head -1 || true
agent-browser --session "$SESSION_IMPL" open "$IMPL_URL" 2>&1 | head -1 || true

ref_browser wait "$WAIT_REF" > /dev/null 2>&1
agent-browser --session "$SESSION_IMPL" wait "$WAIT_IMPL" > /dev/null 2>&1

# Remove common overlays (cookie banners, newsletter popups)
DISMISS_OVERLAYS='(() => {
  // First sweep: vendor-specific consent/cookie SDKs that always render fixed UIs
  document.querySelectorAll("#iubenda-cs-banner, [id^=iubenda-], [class*=iubenda], [id^=onetrust-], [class*=onetrust], [id^=osano-], [class*=osano], [id^=cky-], [class*=cookieconsent], #cookie, [class~=cookie], [class*=cookie_]").forEach(el => el.remove());
  // Second sweep: heuristic match by class keywords for big fixed/absolute popups
  document.querySelectorAll("[class*=popup], [class*=modal], [class*=cookie], [class*=banner], [class*=overlay], [class*=signup]").forEach(el => {
    const s = getComputedStyle(el);
    if (s.position === "fixed" || s.position === "absolute") {
      if (el.offsetWidth > window.innerWidth * 0.3 && el.offsetHeight > window.innerHeight * 0.2) {
        el.remove();
      }
    }
  });
  document.body.style.overflow = "";
  document.documentElement.style.overflow = "";
  return "overlays dismissed";
})()'

ref_eval "$DISMISS_OVERLAYS" 2>&1 > /dev/null
agent-browser --session "$SESSION_IMPL" eval "$DISMISS_OVERLAYS" 2>&1 > /dev/null

# Pause carousels/sliders/auto-advancing animations to get a stable frame for comparison.
# This freezes CSS animations and stops Swiper/Splide autoplay — does NOT affect layout.
# Set SKIP_PAUSE_ANIMATIONS=1 to disable if your site relies on animation-based initial layout.
PAUSE_ANIMATIONS='(() => {
  // Freeze all CSS animations and transitions
  const style = document.createElement("style");
  style.id = "__sc-pause__";
  style.textContent = `
    *, *::before, *::after {
      animation-play-state: paused !important;
      transition-duration: 0s !important;
    }
    '"$DYNAMIC_PAUSE_EXTRA"'
  `;
  document.head.appendChild(style);

  // Stop Swiper autoplay AND pin to slide 0 — stopping alone freezes the
  // carousel at whichever slide it happened to reach, so ref and impl (and
  // ref across runs) freeze at DIFFERENT indices and AE diffs a moving
  // target (navercorp postmortem: middle-banner carousel state diverged
  // every compare). Pinning makes the frozen state deterministic.
  if (window.Swiper) {
    document.querySelectorAll(".swiper").forEach(el => {
      if (el.swiper) {
        try { el.swiper.autoplay && el.swiper.autoplay.stop(); } catch (e) {}
        try { el.swiper.slideTo(0, 0, false); } catch (e) {}
      }
    });
  }
  // Stop Splide autoplay and pin to slide 0
  if (window.Splide) {
    document.querySelectorAll(".splide").forEach(el => {
      if (el.splide) {
        try { el.splide.Components.Autoplay.pause(); } catch (e) {}
        try { el.splide.go(0); } catch (e) {}
      }
    });
  }
  // Generic carousel pinning: common track-class patterns get their scroll
  // reset so hand-rolled interval sliders land on a deterministic first
  // frame. (Class-name heuristic only — ARIA-based carousels are not
  // covered here.)
  document.querySelectorAll("[class*=carousel] ul, [class*=slider] ul, [class*=rolling]").forEach(el => {
    try {
      if (el.scrollLeft) el.scrollLeft = 0;
    } catch (e) {}
  });
  // Pause all <video> elements at frame 0 — autoplay videos otherwise produce
  // a different frame on every screenshot, dominating AE without representing
  // structural diffs. Reset currentTime so ref/impl land on the same frame
  // (poster image or 0:00 keyframe).
  document.querySelectorAll("video").forEach(v => {
    try {
      v.pause();
      v.autoplay = false;
      if (v.readyState >= 1) v.currentTime = 0;
    } catch (e) {}
  });
  // Stop any setInterval-based sliders (common pattern: stash interval IDs in data attributes)
  // We cannot enumerate all intervals, but freezing CSS transitions catches visual state.
  return "animations paused";
})()'

# Wait briefly for async-mounted canvas/WebGL embeds to attach BEFORE the mask
# snapshot. Unicorn Studio / Spline / Three.js inject their <canvas> after init,
# so applying the dynamic mask too early can miss a canvas that has not painted
# yet. Poll up to ~3s for a canvas (or a WebGL-embed container) on either side,
# then proceed. Only relevant when EXCLUDE_DYNAMIC is on (we are about to mask).
# Symmetric: the mask itself is injected identically into ref + impl below.
if [ "$EXCLUDE_DYNAMIC" = "1" ] && [ "${SKIP_WAIT_CANVAS:-0}" != "1" ]; then
  WAIT_CANVAS_JS='(() => {
    const el = document.querySelector("canvas") ||
      document.querySelector("[data-us-project], spline-viewer, [data-spline], [data-engine]");
    return el ? "1" : "0";
  })()'
  for _i in 1 2 3 4 5 6; do
    WC_REF=$(ref_eval "$WAIT_CANVAS_JS" 2>/dev/null | tail -1)
    WC_IMPL=$(agent-browser --session "$SESSION_IMPL" eval "$WAIT_CANVAS_JS" 2>/dev/null | tail -1)
    if [[ "$WC_REF" == *1* ]] || [[ "$WC_IMPL" == *1* ]]; then
      echo "▸ canvas/WebGL embed detected (ref:$WC_REF impl:$WC_IMPL) — masking after mount"
      break
    fi
    sleep 0.5
  done
fi

if [ "${SKIP_PAUSE_ANIMATIONS:-0}" != "1" ]; then
  ref_eval "$PAUSE_ANIMATIONS" 2>&1 > /dev/null
  agent-browser --session "$SESSION_IMPL" eval "$PAUSE_ANIMATIONS" 2>&1 > /dev/null
fi

# Force JS-driven entrance animations to their end state.
# CSS pause (above) does NOT stop libraries that mutate inline styles via RAF
# (GSAP, anime.js) or Web Animations API (Framer Motion, motion). When the ref
# site uses one of these, screenshots capture mid-flight frames (opacity 0.5,
# translate3d(20px, 0, 0)) producing huge AE that has nothing to do with
# structural correctness. We detect each library and jump its active
# animations to their final frame. No-op when none are present.
# Set SKIP_FINISH_ANIMATIONS=1 to disable.
FINISH_ANIMATIONS='(() => {
  const found = [];
  // Web Animations API — Framer Motion (when using waapi backend), CSS animations, motion
  try {
    if (typeof document.getAnimations === "function") {
      const anims = document.getAnimations();
      let n = 0;
      anims.forEach(a => { try { a.finish(); n++; } catch (e) {} });
      if (n) found.push("waapi:" + n);
    }
  } catch (e) {}
  // Webpack-bundled GSAP/ScrollTrigger — when ESM-imported and not exposed on window
  // (Next.js, Vite, modern bundlers). Probe webpack module factories for ScrollTrigger
  // and gsap source signatures, stash refs on window for the per-section finish_js.
  try {
    const wp = window.webpackChunk_N_E || window.webpackChunk;
    if (wp && Array.isArray(wp) && !window.__sc_wp_probed) {
      window.__sc_wp_probed = true;
      let cap = null;
      try { wp.push([["__sc_probe_" + Date.now()], {}, (r) => { cap = r; }]); } catch (e) {}
      if (cap && cap.m) {
        let stId = null, gsapId = null;
        for (const id of Object.keys(cap.m)) {
          try {
            const src = cap.m[id].toString();
            if (src.length < 1000) continue;
            if (!stId && /scrollerProxy/.test(src) && /ScrollTrigger/.test(src)) stId = id;
            if (!gsapId && /globalTimeline/.test(src) && /tweenLite/i.test(src)) gsapId = id;
            if (stId && gsapId) break;
          } catch (e) {}
        }
        if (stId) {
          try {
            const m = cap(stId);
            const ST = (m && m.default && typeof m.default.getAll === "function") ? m.default
                     : (m && typeof m.getAll === "function") ? m : null;
            if (ST) window.__sc_st = ST;
          } catch (e) {}
        }
        if (gsapId) {
          try {
            const m = cap(gsapId);
            const cands = [m && m.default, m && m.gsap, m];
            for (const c of cands) {
              if (c && c.globalTimeline && typeof c.globalTimeline.getChildren === "function") {
                window.__sc_gsap = c;
                break;
              }
            }
          } catch (e) {}
        }
      }
    }
  } catch (e) {}
  // GSAP ScrollTrigger — disable all triggers FIRST so progress(1) below
  // is not immediately re-synced to current scroll position by scrub:true.
  try {
    const ST = (window.ScrollTrigger) || window.__sc_st || (window.gsap && window.gsap.core && window.gsap.core.globals && window.gsap.core.globals().ScrollTrigger);
    if (ST && typeof ST.getAll === "function") {
      let n = 0;
      ST.getAll().forEach(st => {
        try {
          if (st.animation && typeof st.animation.progress === "function") st.animation.progress(1, false);
          if (typeof st.disable === "function") { st.disable(false, false); n++; }
        } catch (e) {}
      });
      if (n) found.push("scrollTrigger-disabled:" + n);
    }
  } catch (e) {}
  // GSAP — jump every active tween/timeline to its end
  try {
    const gs = window.gsap || window.__sc_gsap;
    if (gs && gs.globalTimeline && typeof gs.globalTimeline.getChildren === "function") {
      const items = gs.globalTimeline.getChildren(true, true, true);
      let n = 0;
      items.forEach(t => { try { if (typeof t.progress === "function") { t.progress(1, false); n++; } } catch (e) {} });
      found.push("gsap:" + n);
    }
  } catch (e) {}
  // anime.js v3 — running is an array of active instances
  try {
    if (window.anime && Array.isArray(window.anime.running)) {
      const list = window.anime.running.slice();
      let n = 0;
      list.forEach(a => { try { a.seek(a.duration); a.pause(); n++; } catch (e) {} });
      found.push("anime:" + n);
    }
  } catch (e) {}
  // Lottie — lottie-web (window.lottie.getRegisteredAnimations) + <lottie-player>/<dotlottie-player> elements
  try {
    let n = 0;
    if (window.lottie && typeof window.lottie.getRegisteredAnimations === "function") {
      window.lottie.getRegisteredAnimations().forEach(a => {
        try {
          const last = (typeof a.totalFrames === "number" ? a.totalFrames : 1) - 1;
          a.goToAndStop(Math.max(0, last), true);
          n++;
        } catch (e) {}
      });
    }
    document.querySelectorAll("lottie-player, dotlottie-player").forEach(el => {
      try { if (typeof el.seek === "function") el.seek("100%"); if (typeof el.pause === "function") el.pause(); n++; } catch (e) {}
    });
    if (n) found.push("lottie:" + n);
  } catch (e) {}
  return found.join(",") || "none";
})()'

if [ "${SKIP_FINISH_ANIMATIONS:-0}" != "1" ]; then
  REF_FIN=$(ref_eval "$FINISH_ANIMATIONS" 2>/dev/null | tail -1)
  IMPL_FIN=$(agent-browser --session "$SESSION_IMPL" eval "$FINISH_ANIMATIONS" 2>/dev/null | tail -1)
  if [ -n "$REF_FIN" ] && [ "$REF_FIN" != '"none"' ]; then
    echo "  ▸ Animation libs finished — ref: $REF_FIN, impl: $IMPL_FIN"
  fi
fi

# Hide images to reduce AE noise from dynamic content (thumbnails, ads, etc.)
HIDE_IMAGES_JS='(() => {
  const style = document.createElement("style");
  style.id = "__no_images__";
  style.textContent = "img, picture, video, iframe { visibility: hidden !important; }";
  document.head.appendChild(style);
  document.querySelectorAll("*").forEach(el => {
    if (el.style && el.style.backgroundImage) el.style.backgroundImage = "none";
  });
  new MutationObserver(muts => {
    muts.forEach(m => m.addedNodes.forEach(n => {
      if (n.style && n.style.backgroundImage) n.style.backgroundImage = "none";
      if (n.querySelectorAll) n.querySelectorAll("[style*=background-image]").forEach(el => { el.style.backgroundImage = "none"; });
    }));
  }).observe(document.body, { childList: true, subtree: true });
})()'

# Hide <canvas> elements (WebGL/Three.js/etc.) — their content is dynamic per-frame
# so it would dominate AE without representing real structural diffs.
HIDE_CANVAS_JS='(() => {
  const style = document.createElement("style");
  style.id = "__no_canvas__";
  style.textContent = "canvas { visibility: hidden !important; }";
  document.head.appendChild(style);
})()'

if [ "$NO_IMAGES" = "1" ]; then
  echo "▸ Hiding images (NO_IMAGES=1)..."
  ref_eval "$HIDE_IMAGES_JS" 2>/dev/null || true
  agent-browser --session "$SESSION_IMPL" eval "$HIDE_IMAGES_JS" 2>/dev/null || true
fi

if [ "${NO_CANVAS:-0}" = "1" ]; then
  echo "▸ Hiding canvases (NO_CANVAS=1)..."
  ref_eval "$HIDE_CANVAS_JS" 2>/dev/null || true
  agent-browser --session "$SESSION_IMPL" eval "$HIDE_CANVAS_JS" 2>/dev/null || true
fi

sleep 1

# ── Detect the actual scroll container ──
# Lenis / locomotive-scroll / overflow:hidden body sites move the document
# scrollbar to an inner wrapper; window.scrollTo silently no-ops on those,
# producing identical screenshots at every "scroll position". Detect once
# per session and reuse for all subsequent scroll commands.
DETECT_SCROLLER_JS='(() => {
  const dh = document.documentElement.scrollHeight;
  const dc = document.documentElement.clientHeight;
  if (dh > dc + 100) return "__document__";
  let best = null;
  document.querySelectorAll("*").forEach(el => {
    const cs = getComputedStyle(el);
    if ((cs.overflowY === "auto" || cs.overflowY === "scroll" || cs.overflowY === "hidden")
        && el.scrollHeight > el.clientHeight + 100) {
      if (!best || el.scrollHeight > best.sh) best = { el, sh: el.scrollHeight };
    }
  });
  if (!best) return "__document__";
  const cls = (typeof best.el.className === "string" ? best.el.className : "")
    .split(" ").find(c => c.startsWith("js-") || c.includes("lenis") || c.includes("scroll"));
  return best.el.tagName.toLowerCase() + (cls ? "." + cls : "");
})()'
_unwrap_scroller() {
  python3 -c "import sys, json; v=sys.argv[1]; print(json.loads(v) if v.startswith('\"') else v)" "$1" 2>/dev/null || echo "__document__"
}
# Validate the detected selector against a strict allow-list before it flows into
# downstream Python f-strings. Detection produces values like `div.js-foo`; anything
# else (special chars, malformed) falls back to __document__. Matches v0.4.2's
# transition-compare.sh hardening discipline.
_validate_scroller() {
  local sel="$1"
  if [ "$sel" = "__document__" ] || [[ "$sel" =~ ^[a-z][a-z0-9]*(#[a-zA-Z][a-zA-Z0-9_-]*)?(\.[a-zA-Z][a-zA-Z0-9_-]*)?$ ]]; then
    echo "$sel"
  else
    echo "__document__"
  fi
}
REF_SCROLLER_SEL=$(_unwrap_scroller "$(ref_eval "$DETECT_SCROLLER_JS" 2>&1 | tail -1)")
IMPL_SCROLLER_SEL=$(_unwrap_scroller "$(agent-browser --session "$SESSION_IMPL" eval "$DETECT_SCROLLER_JS" 2>&1 | tail -1)")
REF_SCROLLER_SEL=$(_validate_scroller "$REF_SCROLLER_SEL")
IMPL_SCROLLER_SEL=$(_validate_scroller "$IMPL_SCROLLER_SEL")
[ -z "$REF_SCROLLER_SEL" ] && REF_SCROLLER_SEL="__document__"
[ -z "$IMPL_SCROLLER_SEL" ] && IMPL_SCROLLER_SEL="__document__"
if [ "$REF_SCROLLER_SEL" != "__document__" ] || [ "$IMPL_SCROLLER_SEL" != "__document__" ]; then
  echo "  ▸ Inner scroll container detected (Lenis/locomotive-style)"
  echo "    ref:  $REF_SCROLLER_SEL"
  echo "    impl: $IMPL_SCROLLER_SEL"
fi

# Build per-session scroll JS — falls back to window.scrollTo when the scroller is __document__.
_scroll_js() {
  local sel="$1"; local y="$2"
  if [ "$sel" = "__document__" ]; then
    echo "(() => { window.scrollTo(0, $y); document.documentElement.setAttribute('data-section-compare-scrolled', ($y > 0 ? '1' : '0')); return $y; })()"
  else
    echo "(() => { const w = document.querySelector('$sel'); if (!w) { window.scrollTo(0, $y); document.documentElement.setAttribute('data-section-compare-scrolled', ($y > 0 ? '1' : '0')); return $y; } w.scrollTop = $y; document.documentElement.setAttribute('data-section-compare-scrolled', ($y > 0 ? '1' : '0')); w.dispatchEvent(new Event('scroll')); return w.scrollTop; })()"
  fi
}

# ── Pre-scroll: trigger lazy-loaded content before fingerprint extraction ──
# Sites with IntersectionObserver-based lazy loading will have empty innerText
# for off-screen sections at load time. Scrolling through the full page forces
# all lazy content to load before we build section fingerprints.
# This prevents MATCH_COUNT=0 on sites with aggressive lazy loading.
#
# Round-trip + settle: previous version did `scrollTo(0, total)` then instantly
# `scrollTo(0, 0)`. Sites whose scroll handler toggles body/progress classes
# based on visited scroll thresholds (e.g. `body.-postManifest`, `-active`,
# `--progress` CSS vars) often did NOT remove those classes when teleported
# back to top, because the scroll handler is RAF-debounced and the round-trip
# happened in a single frame. The captured ref then carried artifact state
# (cards display:none, color overrides) that the impl correctly cleared,
# producing huge AE that wasn't a real visual mismatch. New behavior:
# scroll DOWN in steps, scroll UP in the same steps (so each intermediate
# scroll position fires a handler tick), then a multi-frame settle dispatching
# scroll events at y=0 to give handlers full opportunity to reset.
echo "▸ Pre-scrolling to trigger lazy content..."
_pre_scroll_js() {
  local sel="$1"
  if [ "$sel" = "__document__" ]; then
    cat <<'JSEOF'
(() => {
  window.__SC_PRESCROLL_DONE = 0;
  const total = document.documentElement.scrollHeight;
  const step = Math.max(window.innerHeight * 0.8, 400);
  let y = 0;
  let dir = 1;
  const timer = setInterval(() => {
    window.scrollTo(0, y);
    y += step * dir;
    if (dir === 1 && y >= total) { dir = -1; y = total; }
    else if (dir === -1 && y <= 0) {
      clearInterval(timer);
      let n = 0;
      const settle = () => {
        window.scrollTo(0, 0);
        window.dispatchEvent(new Event('scroll'));
        document.dispatchEvent(new Event('scroll'));
        n++;
        if (n < 16) requestAnimationFrame(settle);
        else { window.scrollTo(0, 0); window.__SC_PRESCROLL_DONE = 1; }
      };
      requestAnimationFrame(settle);
    }
  }, 120);
  return total;
})()
JSEOF
  else
    cat <<JSEOF
(() => {
  window.__SC_PRESCROLL_DONE = 0;
  const w = document.querySelector('$sel');
  if (!w) { window.scrollTo(0, document.documentElement.scrollHeight); window.scrollTo(0, 0); window.__SC_PRESCROLL_DONE = 1; return 0; }
  const total = w.scrollHeight;
  const step = Math.max(w.clientHeight * 0.8, 400);
  let y = 0;
  let dir = 1;
  const timer = setInterval(() => {
    w.scrollTop = y;
    w.dispatchEvent(new Event('scroll'));
    y += step * dir;
    if (dir === 1 && y >= total) { dir = -1; y = total; }
    else if (dir === -1 && y <= 0) {
      clearInterval(timer);
      let n = 0;
      const settle = () => {
        w.scrollTop = 0;
        w.dispatchEvent(new Event('scroll'));
        window.dispatchEvent(new Event('scroll'));
        n++;
        if (n < 16) requestAnimationFrame(settle);
        else { w.scrollTop = 0; window.__SC_PRESCROLL_DONE = 1; }
      };
      requestAnimationFrame(settle);
    }
  }, 120);
  return total;
})()
JSEOF
  fi
}

# Wait for the fire-and-forget setInterval round-trip + settle to complete on
# both sessions. Polls for window.__SC_PRESCROLL_DONE === 1; bounded so a
# single hung session can't block the whole comparison.
_wait_prescroll_done() {
  local session="$1"
  local max_seconds="${PRESCROLL_TIMEOUT:-30}"
  local i
  for i in $(seq 1 "$max_seconds"); do
    local out
    out=$(agent-browser --session "$session" eval "(() => window.__SC_PRESCROLL_DONE || 0)()" 2>/dev/null | tail -1 | tr -d '"')
    if [ "$out" = "1" ]; then return 0; fi
    sleep 1
  done
  echo "  ⚠  Pre-scroll did not signal done within ${max_seconds}s on $session — proceeding anyway" >&2
  return 1
}

ref_eval "$(_pre_scroll_js "$REF_SCROLLER_SEL")" > /dev/null 2>&1
agent-browser --session "$SESSION_IMPL" eval "$(_pre_scroll_js "$IMPL_SCROLLER_SEL")" > /dev/null 2>&1
[ "$REUSE_FROZEN_REF" = "1" ] || _wait_prescroll_done "$SESSION_REF"
_wait_prescroll_done "$SESSION_IMPL"
sleep "$WAIT_LAZY_LOAD"  # Extra time for lazy content (images, IO callbacks) to render
ref_eval "$(_scroll_js "$REF_SCROLLER_SEL" 0)" > /dev/null 2>&1
agent-browser --session "$SESSION_IMPL" eval "$(_scroll_js "$IMPL_SCROLLER_SEL" 0)" > /dev/null 2>&1
sleep "$WAIT_SCROLL_SETTLE"

# ── Step 1: Enumerate sections on both sites ──
echo "▸ Enumerating sections..."

# Single-sourced enumerator (also consumed by alignment-sweep-check.sh):
# emits per-section rect, text fingerprints, clientWidth, contentBox,
# contentGroups, and leftGap/rightGap. See lib/enumerate-sections.js.
# The dynamic mask (PAUSE_ANIMATIONS, above) sets `visibility: hidden` on the
# DYNAMIC_SELECTORS to absorb timer-phase MOTION from the screenshot. That mask
# is applied BEFORE this enumeration, and visibility:hidden also makes the
# enumerator's contentBox/contentGroups filters drop the masked elements — so a
# STATIC geometry defect under a mask (specific regression footer cards baked left:426px /
# ±192 transform, off-center at non-extraction viewports) became invisible to
# alignment-parity (exemption-without-compensation). visibility:hidden PRESERVES
# layout, so we hand the enumerator the masked selector list; it measures
# mask-hidden geometry (rect/contentGroups) while still skipping display:none and
# zero-size elements. Applied identically to ref + impl, so the comparison stays
# ref-relative. The list is quote-free by construction (validated above).
ENUMERATE_SECTIONS="window.__UI_RE_DYNAMIC_SELECTORS__ = \"${DYNAMIC_SELECTORS}\";
$(cat "$SCRIPTS_DIR/lib/enumerate-sections.js")"

# Ref enumeration is skipped in frozen-ref mode — the cached ref-sections.json
# is reused as-is (a stdout redirect through ref_browser would truncate it).
if [ "$REUSE_FROZEN_REF" != "1" ]; then
  agent-browser --session "$SESSION_REF" eval "$ENUMERATE_SECTIONS" > "$DIR/sections/ref-sections.json" 2>&1
  cp "$DIR/sections/ref-sections.json" "$DIR/sections/ref-runtime-sections.json"
fi
agent-browser --session "$SESSION_IMPL" eval "$ENUMERATE_SECTIONS" > "$DIR/sections/impl-sections.json" 2>&1

IMPL_SEMANTIC_CANDIDATES='(() => {
  const selectors = "main, section, header, footer, nav, article, [id], [class]";
  const landmarkTags = new Set(["main", "header", "footer", "nav", "article"]);
  const seen = new Set();

  return Array.from(document.querySelectorAll(selectors)).flatMap((el, i) => {
    if (seen.has(el)) return [];
    seen.add(el);

    const rect = el.getBoundingClientRect();
    const isLandmark = landmarkTags.has(el.tagName.toLowerCase());
    if (rect.height < (isLandmark ? 24 : 50) || rect.width < 100) return [];

    const text = el.innerText || "";
    const words = text.replace(/\s+/g, " ").trim().substring(0, 200);
    const fingerprint = words.substring(0, 100).toLowerCase().replace(/[^a-z0-9 ]/g, "");
    const textWords = text.replace(/\s+/g, " ").trim().toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim().substring(0, 800);
    const svgs = el.querySelectorAll("svg");
    const hasSvgText = [...svgs].some(svg => {
      const paths = svg.querySelectorAll("path");
      if (paths.length < 3) return false;
      const totalD = [...paths].reduce((sum, p) => sum + (p.getAttribute("d")?.length || 0), 0);
      return totalD > 500;
    });
    const cs = getComputedStyle(el);
    const visibleRect = node => {
      const r = node.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return null;
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden"
          || Number.parseFloat(style.opacity || "1") === 0) return null;
      return r;
    };
    const dynamicSelectors = String(window.__UI_RE_DYNAMIC_SELECTORS__ || "")
      .split(",").map(selector => selector.trim()).filter(Boolean);
    const isDynamicMasked = node => dynamicSelectors.some(selector => {
      try { return node.matches(selector); } catch (_error) { return false; }
    });
    const visibleMedia = Array.from(
      el.querySelectorAll("img,video,canvas,iframe,picture,object,embed")
    ).filter(node => {
      const mediaRect = node.getBoundingClientRect();
      if (mediaRect.width <= 0 || mediaRect.height <= 0) return false;
      const mediaStyle = getComputedStyle(node);
      return mediaStyle.display !== "none"
        && (mediaStyle.visibility !== "hidden" || isDynamicMasked(node))
        && Number.parseFloat(mediaStyle.opacity || "1") > 0;
    });
    const visibleMediaKindCounts = visibleMedia.reduce((counts, node) => {
      const kind = node.tagName.toLowerCase();
      counts[kind] = (counts[kind] || 0) + 1;
      return counts;
    }, {});
    const directBoxes = Array.from(el.children).map(visibleRect).filter(Boolean);
    const contentBox = directBoxes.length ? (() => {
      const left = Math.min(...directBoxes.map(box => box.left));
      const right = Math.max(...directBoxes.map(box => box.right));
      return {
        left: Math.round(left),
        width: Math.round(right - left),
        boxCount: directBoxes.length,
      };
    })() : null;
    const contentGroups = Array.from(el.children).flatMap(child => {
      const childRect = visibleRect(child);
      const boxes = Array.from(child.children).map(visibleRect).filter(Boolean);
      if (!childRect || boxes.length < 2) return [];
      const left = Math.min(...boxes.map(box => box.left));
      const right = Math.max(...boxes.map(box => box.right));
      const rawName = (child.className?.toString?.() || "").trim().split(" ")[0]
        || child.tagName.toLowerCase();
      const name = rawName.includes("__")
        ? rawName.substring(0, rawName.lastIndexOf("__"))
        : rawName;
      return [{
        name: name.substring(0, 40),
        containerLeft: Math.round(childRect.left),
        containerWidth: Math.round(childRect.width),
        unionLeft: Math.round(left),
        unionWidth: Math.round(right - left),
        childCount: boxes.length,
        childCenters: boxes.slice(0, 24).map(box => Math.round(box.left + box.width / 2)),
      }];
    });

    return [{
      index: i,
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      className: (el.className?.toString?.() || "").substring(0, 80),
      fingerprint,
      textWords,
      hasSvgText,
      hasVisibleMedia: visibleMedia.length > 0,
      visibleMediaCount: visibleMedia.length,
      visibleMediaKinds: Object.keys(visibleMediaKindCounts).sort(),
      visibleMediaKindCounts,
      rect: {
        top: Math.round(rect.top + window.scrollY),
        left: Math.round(rect.left),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
      display: cs.display,
      gridCols: cs.gridTemplateColumns !== "none" ? cs.gridTemplateColumns : null,
      childCount: el.children.length,
      clientWidth: document.documentElement.clientWidth,
      contentBox,
      contentGroups,
      leftGap: contentBox ? Math.round(contentBox.left - rect.left) : null,
      rightGap: contentBox
        ? Math.round((rect.left + rect.width) - (contentBox.left + contentBox.width))
        : null,
    }];
  });
})()'

# section-map.json ground truth — observed benchmark gap:
# the runtime ENUMERATE_SECTIONS JS above descends `<main>` only when its
# children include `<section>` or `<main>` (line 709-712). When the ref's
# `<main>` contains only `<div>` children, enumeration collapses many
# visible sections into a single "section-0" container. result.txt then
# only carries 2 rows (section-0 + footer) — the rest never compared at all.
#
# extraction-time section-map.json already records 16 sections with their
# semantic tags + selectors (its tag-attribution is best-effort but its
# Y-coordinate ranges are reliable). When that file exists, override
# ref-sections.json with its entries so the matcher sees the full set.
# Falls back to the runtime enumeration when section-map.json is absent
# or doesn't validate.
# D23 (loop-nvti-1): section-map.json lives ONLY at the ref root — in the
# VIEWPORTS fan-out $DIR is sections/viewports/<WxH>/ and the override was
# silently skipped, so the raw (pin-released, over-segmented) enumeration
# became the frozen baseline and every impl crop mis-mapped. Same
# REF_ROOT_DIR sibling fallback as transition-spec/asset-substitution
# above — the third ref-root input to hit this class.
SECTION_MAP_FILE="$DIR/section-map.json"
if [ ! -f "$SECTION_MAP_FILE" ] && [ -n "${REF_ROOT_DIR:-}" ] && [ -f "${REF_ROOT_DIR}/section-map.json" ]; then
  SECTION_MAP_FILE="${REF_ROOT_DIR}/section-map.json"
fi
if [ -f "$SECTION_MAP_FILE" ]; then
  python3 - "$SECTION_MAP_FILE" 2>/dev/null <<'PY' || true
import json, sys

try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
ci = d.get("capturedIdle")
if ci is None:
    sys.stderr.write(
        "section-compare: ADVISORY - section-map.json has no capturedIdle "
        "provenance; cannot confirm frozen-ref ground truth was captured idle.\n"
    )
elif not ci.get("idle"):
    sys.stderr.write(
        "section-compare: ADVISORY - section-map.json was NOT captured idle "
        "(%s). A faithful idle clone may fail against this ground truth; "
        "re-extract the section map from an idle page.\n"
        % json.dumps({k: ci.get(k) for k in ("scrollY", "openStateMatches", "reset")})
    )
PY
  if [ "$REUSE_FROZEN_REF" != "1" ]; then
    ref_eval "$IMPL_SEMANTIC_CANDIDATES" > "$DIR/sections/ref-semantic-candidates.json" 2>&1 || true
  fi
  agent-browser --session "$SESSION_IMPL" eval "$IMPL_SEMANTIC_CANDIDATES" > "$DIR/sections/impl-semantic-candidates.json" 2>&1 || true
  PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m ui_clone.section_compare_sections augment-impl \
    "$SECTION_MAP_FILE" \
    "$DIR/sections/impl-sections.json" \
    "$DIR/sections/impl-semantic-candidates.json" 2>/dev/null || true

  if [ "$REUSE_FROZEN_REF" != "1" ]; then
    python3 - "$SECTION_MAP_FILE" "$DIR/sections/ref-sections.json" 2>/dev/null <<'PY' || true
import json
import sys
import os
sm_path, out_path = sys.argv[1], sys.argv[2]
try:
    sm = json.load(open(sm_path))
except Exception:
    sys.exit(0)
sections = sm.get("sections") if isinstance(sm, dict) else None
if not isinstance(sections, list) or len(sections) < 3:
    # Either the file is malformed or has fewer sections than runtime —
    # don't override.
    sys.exit(0)
import re
def _norm_text(raw):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(raw or "").lower())).strip()[:800]
# Preserve the real innerText the runtime enumerator captured just before this
# override rewrote ref-sections.json. The synthesis has no innerText of its
# own, so without this the text-content matcher would see only class-derived
# seeds on the ref side. Build identity -> textWords lookups so a synthesized
# row inherits the live text of the matching runtime row.
_runtime_by_id = {}
_runtime_by_cls = {}
# Full runtime ROW lookups so the synthesis can also inherit the live-measured
# ALIGNMENT fields (contentGroups, clientWidth, leftGap/rightGap, contentBox)
# the section-map ground truth lacks — without these the ref side of
# alignment-parity has contentGroups=None and a content-bearing ref row reads as
# unmeasurable, failing the gate against its own reference. Matched by id, then
# by class token. (batch-13 ITEM 3)
_rt_all = []
try:
    _prev = json.load(open(out_path))
except Exception:
    _prev = []
if isinstance(_prev, list):
    for pr in _prev:
        if not isinstance(pr, dict):
            continue
        _rt_all.append(pr)
        pid = str(pr.get("id") or "").strip()
        tw = str(pr.get("textWords") or "").strip()
        if not tw:
            continue
        if pid:
            _runtime_by_id.setdefault(pid, tw)
        for tok in str(pr.get("className") or "").split():
            if len(tok) >= 4:
                _runtime_by_cls.setdefault(tok, tw)

def _runtime_row(sid, cls, y):
    # POSITION-aware: a class token shared by adjacent sections (e.g. an FAQ
    # section and a following CTA share a wrapper class) makes a first-token-wins
    # lookup hand the CTA the FAQ section's contentGroups. Rank candidates by
    # id-exact, then class-overlap, then closest
    # document top — the same disambiguation the matcher uses. (batch-13 ITEM 3)
    sid = str(sid or "").strip()
    cls_toks = {t for t in str(cls or "").split() if len(t) >= 4}
    try:
        yf = float(y)
    except (TypeError, ValueError):
        yf = 0.0
    best = None
    best_key = None
    for pr in _rt_all:
        pid = str(pr.get("id") or "").strip()
        ptoks = {t for t in str(pr.get("className") or "").split() if len(t) >= 4}
        id_match = bool(sid and pid == sid)
        cls_match = bool(cls_toks & ptoks)
        if not id_match and not cls_match:
            continue
        prt = pr.get("rect") if isinstance(pr.get("rect"), dict) else {}
        ptop = prt.get("top")
        dtop = abs(float(ptop) - yf) if isinstance(ptop, (int, float)) else 1e9
        key = (0 if id_match else 1, 0 if cls_match else 1, dtop)
        if best_key is None or key < best_key:
            best_key = key
            best = pr
    return best
# Synthesize ref-sections rows in the shape ENUMERATE_SECTIONS returns,
# preserving the index-by-Y order section-map.json already uses.
#
# Key compatibility: section-map.json (extraction-time ground truth) uses
# `top`/`cls`/`height`. Older synthesis code (and some test fixtures) used
# `y`/`class`/`height`. Read both — bug observed in the 3-round benchmark
# where every section's rect.top fell back to 0 because the synthesis only
# read `y`, producing a "phantom ref" with collapsed coords that triggered
# uniform AE/Mpx ~950k across every section and 632 wasted retry iterations.
out = []
active_view_width = int(os.environ.get("VIEW_W") or 1440)
# Fix 12 — drop layout-only zero-height wrappers from synthesis.
# V8 (d4b369d) section-map had 15 sections but all carried `height: 0` for
# the same reason scroll-reveal animations leave them collapsed at capture
# time. Including them in ref-sections produced 5 catastrophic-AE rows
# (~900k) that dragged ae_avg from a real ~250k up to 509k. Skip entries
# below the minimum-visible threshold so synthesis matches only real
# content sections.
_MIN_VISIBLE_HEIGHT = 50
sections_sorted = sorted(sections, key=lambda s: s.get("top") or s.get("y") or 0)
for i, s in enumerate(sections_sorted):
    # section-map.json may use either `height` (runtime enumeration shape) or
    # `h` (extraction-time short shape). Read both. Without this fallback,
    # every section reads as h=0 and gets filtered, collapsing the override
    # to zero rows and re-using the runtime 1-giant-section enumeration.
    h_raw = int(s.get("height") or s.get("h") or 0)
    if h_raw < _MIN_VISIBLE_HEIGHT:
        # Layout-only wrapper, not a content section — skip.
        continue
    cls = (s.get("cls") or s.get("className") or s.get("class") or "").strip()
    # Same fallback for id: extraction-time section-map uses `name`.
    sid = s.get("id") or s.get("name")
    tag = s.get("tag") or "section"
    if tag not in {"main", "section", "header", "footer", "nav", "article"} and not sid and not cls:
        # Anonymous non-semantic children have no stable cross-DOM identity.
        # Runtime enumeration may add them affirmatively; map geometry alone
        # must not create a reference-only row.
        continue
    y = int(s.get("top") or s.get("y") or 0)
    h = h_raw
    x = int(s.get("left") or s.get("x") or 0)
    w = int(s.get("width") or s.get("w") or active_view_width)
    fp_seed = sid or cls or f"sec-{i}"
    # fingerprint: lowercase alphanumeric, take first 100 chars of the
    # human-readable seed. Runtime ENUMERATE_SECTIONS uses innerText; we
    # don't have it here, so the class/id-derived fingerprint serves as a
    # stable matching key for impl rows that DO have innerText. The
    # matcher already tolerates partial-match fingerprints (substring).
    fp = re.sub(r"[^a-z0-9 ]", "", fp_seed.lower())[:100]
    # textWords: prefer real runtime innerText (by id, then class token),
    # then section-map textPreview, then the class/id seed as a last resort.
    tw = ""
    if sid and str(sid).strip() in _runtime_by_id:
        tw = _runtime_by_id[str(sid).strip()]
    if not tw:
        for tok in cls.split():
            if len(tok) >= 4 and tok in _runtime_by_cls:
                tw = _runtime_by_cls[tok]
                break
    if not tw:
        tw = _norm_text(s.get("textPreview"))
    if not tw:
        tw = _norm_text(fp_seed)
    row = {
        "index": i,
        "tag": tag,
        "id": sid,
        "className": cls[:80],
        "fingerprint": fp,
        "textWords": tw,
        "hasSvgText": False,
        "rect": {"top": y, "left": x, "width": w, "height": h},
        "display": s.get("display") or "block",
        "gridCols": s.get("gridCols") or None,
        "childCount": int(s.get("childCount") or 0),
        "hasVisibleMedia": s.get("hasVisibleMedia") is True,
        "visibleMediaCount": int(s.get("visibleMediaCount") or 0),
        "visibleMediaKinds": s.get("visibleMediaKinds") if isinstance(s.get("visibleMediaKinds"), list) else [],
        "visibleMediaKindCounts": s.get("visibleMediaKindCounts") if isinstance(s.get("visibleMediaKindCounts"), dict) else {},
    }
    # Inherit the live-measured alignment fields the section-map lacks, so the
    # synthesized ref row carries the SAME contentGroups/gaps the impl row
    # enumerates (batch-13 ITEM 3 — keeps alignment-parity's ref side measurable).
    _rt = _runtime_row(sid, cls, y)
    if isinstance(_rt, dict):
        for _k in (
            "contentGroups", "clientWidth", "leftGap", "rightGap", "contentBox",
            "hasVisibleMedia", "visibleMediaCount", "visibleMediaKinds", "visibleMediaKindCounts",
        ):
            _v = _rt.get(_k)
            if _v is not None:
                row[_k] = _v
        if not row.get("childCount"):
            try:
                row["childCount"] = int(_rt.get("childCount") or 0)
            except (TypeError, ValueError):
                pass
    out.append(row)
# Fix 12 safety — if the h>=50 filter removed too many sections, the
# resulting synthesis is degenerate. Fall back to the runtime enumeration
# (don't write a thin override) so section-compare can still pair real
# content sections after they scroll-reveal.
if len(out) < 3:
    sys.exit(0)
with open(out_path, "w") as fh:
    json.dump(out, fh)
PY
  fi
  if [ "$REUSE_FROZEN_REF" != "1" ] && [ -s "$DIR/sections/ref-semantic-candidates.json" ]; then
    PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
      python3 -m ui_clone.section_compare_sections synthesize-ref \
      "$SECTION_MAP_FILE" \
      "$DIR/sections/ref-sections.json" \
      "$DIR/sections/ref-semantic-candidates.json" \
      "$VIEW_W" \
      "$DIR/sections/ref-runtime-sections.json"
  fi
  if [ -s "$DIR/sections/ref-sections.json" ] && head -1 "$DIR/sections/ref-sections.json" | grep -q "^\["; then
    echo "▸ ref-sections.json overridden from section-map.json (extraction-time ground truth)" >&2
  fi
fi

_parse_section_count() {
  local f="$1"
  # Check for JS error (agent-browser eval failure) before parsing JSON
  local first
  first=$(head -1 "$f" 2>/dev/null || echo "")
  if echo "$first" | grep -qE '^(SyntaxError|TypeError|ReferenceError|Error:|Uncaught|\[object)'; then
    echo "JS_ERROR: $first" >&2
    echo "0"
    return
  fi
  python3 -c "
import json, sys
try:
    d = json.loads(open('$f').read())
    print(len(d) if isinstance(d, list) else 0)
except Exception as e:
    print(0, file=sys.stderr)
    print(0)
" 2>/dev/null || echo "0"
}
REF_COUNT=$(_parse_section_count "$DIR/sections/ref-sections.json")
IMPL_COUNT=$(_parse_section_count "$DIR/sections/impl-sections.json")

echo "  Ref:  $REF_COUNT sections"
echo "  Impl: $IMPL_COUNT sections"

if [ "$REF_COUNT" = "0" ] || [ "$IMPL_COUNT" = "0" ]; then
  echo "ERROR: Failed to enumerate sections — check if pages loaded correctly"
  echo "  Ref JSON head: $(head -3 "$DIR/sections/ref-sections.json" 2>/dev/null || echo "(missing)")"
  echo "  Impl JSON head: $(head -3 "$DIR/sections/impl-sections.json" 2>/dev/null || echo "(missing)")"
  exit 1
fi

# ── Step 2: Match sections by text-content + identity similarity ──
# Pairing logic lives in ui_clone.section_compare_sections.pair_sections so it
# is unit-tested (tests/measure/test_section_compare.py). It anchors pairs by
# (1) semantic-key identity, (2) className-exact tokens, (3) TEXT-CONTENT
# similarity (what a section SAYS), and only then falls back to same-tag + DOM
# order. Text content is what lets a faithful Tailwind clone pair against a
# CSS-Modules reference whose class signatures share nothing. This is a
# PAIRING-only signal — the AE/dssim/structure comparison downstream is
# unchanged, so better pairing yields more accurate measurement, never an
# easier pass.
echo "▸ Matching sections..."

PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m ui_clone.section_compare_sections pair \
  "$DIR/sections/ref-sections.json" \
  "$DIR/sections/impl-sections.json" \
  "$DIR/sections/matches.json" 2>&1

# ── Step 3: Crop element screenshots per matched section ──
echo "▸ Capturing section screenshots..."

MATCH_COUNT=$(python3 -c "import json; m=json.load(open('$DIR/sections/matches.json')); print(len([x for x in m if x.get('ref') and x.get('impl')]))" 2>/dev/null || echo "0")

# EC-SC-3: Zero matches means fingerprint extraction failed on one side (wrong URL, JS error,
# CSP-blocked eval). Continuing would compare stale screenshots from a previous run —
# a false-pass risk. Exit early with a clear diagnostic.
if [ "$MATCH_COUNT" -eq 0 ]; then
  echo ""
  echo "ERROR: 0 sections matched between ref and impl."
  echo "  Possible causes:"
  echo "    1. Wrong URL passed (orig vs impl swapped?)"
  echo "    2. JS eval blocked by CSP on one page"
  echo "    3. Page not fully loaded — try adding a delay or scrolling to trigger lazy-load"
  echo "    4. Single-section site — fingerprint matching needs ≥2 sections"
  echo ""
  echo "  Debug: check $DIR/sections/matches.json"
  echo "  Expected: entries with both 'ref' and 'impl' populated"
  # Write a FAIL result.txt so the Stop gate gives a useful message instead of "not run"
  mkdir -p "$DIR/sections"
  {
    echo "| Section | AE | AE/Mpx | Severity | Status |"
    echo "|---------|-----|--------|----------|--------|"
    echo "| (none) | — | — | — | ❌ |"
    echo ""
    echo "**Result: 0 PASS, 1 FAIL, 0 SKIP**"
    echo ""
    echo "FAILURE REASON: 0 sections matched — fingerprint extraction failed."
    echo "Re-run section-compare.sh after fixing the URL or page load issue."
  } > "$DIR/sections/result.txt"
  exit 1
fi

# ── Step 3a: Record dynamic-mask coverage sidecar ─────────────────────
# Evidence-only artifact for pass-under-mask audits. Do NOT change result.txt:
# check-converged.sh parses that table shape. The active DYNAMIC_SELECTORS are
# evaluated in the REF session at capture time; Python computes exact union
# coverage per matched section and writes {section: coveragePct}.
echo "▸ Recording dynamic-mask coverage..."
MASK_ELEMENTS_FILE="$DIR/sections/mask-elements.json"
if [ "$EXCLUDE_DYNAMIC" = "1" ] && [ "$REUSE_FROZEN_REF" != "1" ]; then
  MASK_SELECTORS_JSON=$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$DYNAMIC_SELECTORS")
  MASK_RECTS_JS="(() => {
    const selectors = ${MASK_SELECTORS_JSON};
    try {
      return Array.from(document.querySelectorAll(selectors)).flatMap((el, index) => {
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return [];
        return [{
          index,
          tag: el.tagName.toLowerCase(),
          id: el.id || null,
          className: (el.className?.toString?.() || '').substring(0, 120),
          top: Math.round((rect.top + window.scrollY) * 100) / 100,
          left: Math.round((rect.left + window.scrollX) * 100) / 100,
          width: Math.round(rect.width * 100) / 100,
          height: Math.round(rect.height * 100) / 100,
        }];
      });
    } catch (_err) {
      return [];
    }
  })()"
  if ! ref_eval "$MASK_RECTS_JS" > "$MASK_ELEMENTS_FILE.tmp" 2>&1; then
    echo "[]" > "$MASK_ELEMENTS_FILE.tmp"
  fi
  PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 - "$MASK_ELEMENTS_FILE.tmp" "$MASK_ELEMENTS_FILE" <<'PY' 2>/dev/null || echo "[]" > "$MASK_ELEMENTS_FILE"
import sys
from pathlib import Path
from ui_clone.section_compare_sections import parse_agent_browser_json_list

src = Path(sys.argv[1])
out = Path(sys.argv[2])
raw = src.read_text(encoding="utf-8", errors="ignore")
data = parse_agent_browser_json_list(raw)
out.write_text(__import__("json").dumps(data, indent=2), encoding="utf-8")
PY
  rm -f "$MASK_ELEMENTS_FILE.tmp"
elif [ "$REUSE_FROZEN_REF" = "1" ] && [ -s "$MASK_ELEMENTS_FILE" ]; then
  echo "  Reusing frozen reference mask geometry from $MASK_ELEMENTS_FILE"
else
  echo "[]" > "$MASK_ELEMENTS_FILE"
fi
PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m ui_clone.section_compare_sections mask-coverage \
  "$DIR/sections/matches.json" \
  "$MASK_ELEMENTS_FILE" \
  "$DIR/sections/mask-coverage.json" 2>/dev/null || echo "{}" > "$DIR/sections/mask-coverage.json"

SECTION_CAPTURE_DIR="$DIR" \
SECTION_CAPTURE_SESSION_REF="$SESSION_REF" \
SECTION_CAPTURE_SESSION_IMPL="$SESSION_IMPL" \
SECTION_CAPTURE_REF_SCROLLER_SEL="$REF_SCROLLER_SEL" \
SECTION_CAPTURE_IMPL_SCROLLER_SEL="$IMPL_SCROLLER_SEL" \
SECTION_CAPTURE_REUSE_FROZEN_REF="${REUSE_FROZEN_REF:-0}" \
SECTION_CAPTURE_SKIP_FINISH="${SKIP_FINISH_ANIMATIONS:-0}" \
SECTION_CAPTURE_WAIT_SCROLL_SETTLE="${WAIT_SCROLL_SETTLE:-0.5}" \
SECTION_CAPTURE_DYNAMIC_PAUSE_EXTRA="${DYNAMIC_PAUSE_EXTRA:-}" \
SECTION_CAPTURE_FIXED_OVERLAY_SELECTORS="${SECTION_FIXED_OVERLAY_SELECTORS:-}" \
SECTION_CAPTURE_REF_CALIB="${SECTION_REF_CALIB:-0}" \
SECTION_CAPTURE_REF_URL="$ORIG_URL" \
SECTION_CAPTURE_VIEW_W="${VIEW_W:-}" \
SECTION_CAPTURE_VIEW_H="${VIEW_H:-}" \
PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m ui_clone.section_capture "$DIR/sections/matches.json" 2>&1

# ── batch-13 ITEM 1: ref-instability dynamic-section classification ──
# Compute each section's REF-OWN frame-to-frame variance (ref vs ref-calib, two
# independent reference loads) as AE/Mpx + dssim and write sections/ref-dynamic.json.
# A section the reference cannot self-match is dynamic (framer scroll-scrub /
# splash / carousel) and switches to structural/layout parity in the AE loop
# below. Absent ref-calib (calibration off, or a frozen corpus without it) -> no
# dynamic classification, every section keeps strict AE (backward compatible).
if [ -d "$DIR/sections/ref-calib" ] && ls "$DIR/sections/ref-calib/"*.png >/dev/null 2>&1; then
  echo "▸ Computing ref-instability calibration (ref vs ref-calib)..."
  PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  SECTION_DYNAMIC_THRESHOLD="${SECTION_DYNAMIC_REF_SELF_AE_PER_MPX:-2000}" \
  SECTION_FUZZ="${SECTION_FUZZ:-8%}" \
    python3 "$SCRIPTS_DIR/lib/ref-dynamic-classify.py" "$DIR/sections" 2>&1 || true
fi

# ── Crop-evidence guards (vacuous-pass closure) ─────────────────────
# Computes per-crop truth (unique colors, dominant fraction, mean/std) plus
# mask coverage and writes crop-guards.{json,tsv}. The AE loop below uses the
# tsv to convert vacuous verdicts (blank ref, symmetric-blank, >60% masked,
# color-flattened pair) on content-bearing sections into explicit UNMEASURED
# rows — observed failure mode: a near-bottom section whose reveal never
# mounted in the capture window produced byte-identical 2-color crops on both
# sides and passed AE=0 over zero actual content.
echo "▸ Evaluating crop-evidence guards..."
# Mandatory (review-1 MAJOR 2): a guard crash/setup failure must not
# silently disable the vacuous-pass closure — blank-crop AE=0 passes would
# flow through again. On failure, a blocking row is injected into the
# results table below instead of continuing as if guards had run.
GUARDS_FAILED=0
if ! PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m ui_clone.section_guards "$DIR/sections" 2>&1; then
  GUARDS_FAILED=1
  echo "  ⛔ crop-guard evaluation FAILED — vacuous-pass closure inactive; this run cannot pass."
fi

# ── Perceptual-dense pre-pass (opt-in) ──────────────────────────────
# Computes, per matched section, the DOM structure severity (mirroring the
# Step-5 logic below) and a DENSE flag derived from REF morphology only,
# written to sections/structure-severity.txt as: <name>\t<domSev>\t<dense 0|1>.
# Runs under SECTION_PERCEPTUAL_DENSE=1 (now the DEFAULT); set =0 for the strict
# escape hatch (byte-identical to the pre-promotion strict-AE path).
PERCEPTUAL_REFSHOT_CLEAN=1
if [ "$SECTION_PERCEPTUAL_DENSE" = "1" ]; then
  echo "▸ SECTION_PERCEPTUAL_DENSE=1 — dense sections may pass-by-perceptual"
  echo "  (global dssim ≤ ${SECTION_DSSIM_DENSE_MAX}, zero critical/major DOM delta, no localized band ≥ ${SECTION_DSSIM_LOCAL_FAIL})."
  # A screenshot cheat would fake a low dssim, so the perceptual path is
  # disabled wholesale when ref-screenshot-asset.json reports a violation.
  # This never relaxes the existing global enforcement — only adds a guard.
  if [ -f "$DIR/ref-screenshot-asset.json" ]; then
    _shot_status=$(python3 -c "import json;print(json.load(open('$DIR/ref-screenshot-asset.json')).get('status',''))" 2>/dev/null || echo "")
    if [ "$_shot_status" = "fail" ]; then
      PERCEPTUAL_REFSHOT_CLEAN=0
      echo "  ⛔ ref-screenshot-asset.json status=fail — perceptual pass disabled (screenshot cheat detected)."
    fi
  fi
  python3 - "$DIR/sections/matches.json" "$DIR/sections/structure-severity.txt" <<'PY' 2>/dev/null || true
import json, sys
matches_path, out_path = sys.argv[1], sys.argv[2]
try:
    matches = json.load(open(matches_path))
except Exception:
    open(out_path, "w").close()
    sys.exit(0)
rows = []
for m in matches:
    ref = m.get('ref'); impl = m.get('impl'); name = m.get('name')
    if not ref or not impl or not name:
        continue
    # DENSE classification — REF evidence only (text fingerprint or SVG-text),
    # so deleting impl content can never earn relaxed scoring.
    fp = (ref.get('fingerprint') or '').strip()
    dense = 1 if (len(fp) >= 8 or ref.get('hasSvgText')) else 0
    # DOM structure severity — mirrors the Step-5 logic below.
    issues = []
    if ref.get('hasSvgText') and not impl.get('hasSvgText'):
        issues.append('SVG_TEXT_MISSING')
    if ref.get('gridCols') and not impl.get('gridCols'):
        issues.append('LAYOUT_MISMATCH')
    if ref.get('display') != impl.get('display'):
        issues.append('DISPLAY_MISMATCH')
    rh = (ref.get('rect') or {}).get('height', 0)
    ih = (impl.get('rect') or {}).get('height', 0)
    h_ratio = (ih / rh) if rh > 0 else 1.0
    if rh > 0 and (h_ratio < 0.7 or h_ratio > 1.3):
        issues.append('HEIGHT_MISMATCH')
    rc = ref.get('childCount', 0); ic = impl.get('childCount', 0)
    if rc > 0 and abs(rc - ic) > max(2, rc * 0.3):
        issues.append('CHILD_COUNT_MISMATCH')
    fingerprint_strong = m.get('score', 0) >= 0.85
    sev = 'ok'
    if any(i in ('SVG_TEXT_MISSING', 'LAYOUT_MISMATCH') for i in issues):
        sev = 'critical'
    elif h_ratio < 0.3 or h_ratio > 3.0:
        sev = 'critical'
    elif any(i in ('HEIGHT_MISMATCH', 'DISPLAY_MISMATCH') for i in issues):
        sev = 'major'
    elif 'CHILD_COUNT_MISMATCH' in issues:
        sev = 'minor' if fingerprint_strong else 'major'
    elif issues:
        sev = 'minor'
    rows.append(f"{name}\t{sev}\t{dense}")
with open(out_path, "w") as fh:
    fh.write("\n".join(rows) + ("\n" if rows else ""))
PY
fi

# Perceptual helpers — inert unless SECTION_PERCEPTUAL_DENSE=1 populated the map.
_perceptual_dom_sev() {
  awk -F'\t' -v n="$1" '$1==n{print $2; f=1} END{if(!f)print "ok"}' \
    "$DIR/sections/structure-severity.txt" 2>/dev/null || echo "ok"
}
_perceptual_is_dense() {
  awk -F'\t' -v n="$1" '$1==n && $3=="1"{f=1} END{exit !f}' \
    "$DIR/sections/structure-severity.txt" 2>/dev/null
}
# Returns 0 (true) when a section crop pair has a LOCALIZED structural defect:
# any horizontal band of height SECTION_LOCAL_BAND_PX whose dssim reaches
# SECTION_DSSIM_LOCAL_FAIL. A misplaced/missing element trips this; uniform
# font-AA / idle-drift noise does not. Impl is expected pre-resized to ref dims.
_perceptual_localized_defect() {
  local ref_img="$1" impl_img="$2"
  command -v dssim >/dev/null 2>&1 || return 1
  local size w h
  size=$(magick identify -format "%wx%h" "$ref_img" 2>/dev/null) || return 1
  w=${size%x*}; h=${size#*x}
  case "$w" in ''|*[!0-9]*) return 1 ;; esac
  case "$h" in ''|*[!0-9]*) return 1 ;; esac
  local bandpx="$SECTION_LOCAL_BAND_PX"
  case "$bandpx" in ''|*[!0-9]*) bandpx=200 ;; esac
  [ "$bandpx" -lt 1 ] && bandpx=200
  local tmpd; tmpd=$(mktemp -d) || return 1
  local y bh d defect=1   # defect=1 → "no defect" (return non-zero / false)
  for ((y=0; y<h; y+=bandpx)); do
    bh=$bandpx
    [ $((y + bh)) -gt "$h" ] && bh=$((h - y))
    [ "$bh" -lt 8 ] && continue
    magick "$ref_img" -crop "${w}x${bh}+0+${y}" +repage "$tmpd/r.png" 2>/dev/null
    magick "$impl_img" -crop "${w}x${bh}+0+${y}" +repage "$tmpd/i.png" 2>/dev/null
    d=$(dssim "$tmpd/r.png" "$tmpd/i.png" 2>/dev/null | awk '{print $1}')
    [ -z "$d" ] && continue
    if awk -v d="$d" -v t="$SECTION_DSSIM_LOCAL_FAIL" 'BEGIN{exit !(d+0 >= t+0)}'; then
      defect=0
    fi
  done
  rm -rf "$tmpd"
  return $defect
}

# ── Step 4: AE comparison per section ──
echo ""
echo "▸ Comparing sections..."

RESULTS=""
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
SUBSTITUTED_COUNT=0
# Guard-driven STRUCTURAL_ONLY rows are automatic evidence deferrals rather
# than operator-declared asset substitutions. Track them separately so they
# stay in the 50% non-structural-evidence denominator below.
GUARD_STRUCTURAL_COUNT=0
# UNMEASURED is tracked apart from SKIP. SKIP means "the impl does not have this
# section"; UNMEASURED means "the REFERENCE crop carried no signal, so neither
# side was measured". Folding the second into the first produced a 0-FAIL
# summary that read as a clean pass on a run that had measured nothing for
# those sections — and blank ref crops cluster on mid-reveal/animated sections,
# i.e. precisely what this tool exists to verify.
UNMEASURED_COUNT=0
JUDGE_OVERRIDE_COUNT=0
JUDGE_OVERRIDE_NAMES=""
# Review-1 MAJOR 2: guard evaluation is mandatory for matched crop runs.
# A failed guard pass injects a blocking row so the table (and every
# consumer keying on FAIL counts) sees the closure was inactive.
if [ "${GUARDS_FAILED:-0}" = "1" ]; then
  RESULTS="${RESULTS}| (crop-guards) | — | — | setup | ❌ FAIL (crop-guard evaluation failed — vacuous-pass closure inactive; fix ui_clone.section_guards setup and re-run) |\n"
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi
# NON_STRUCTURAL_PASS_COUNT tracks ONLY real pixel-level passes (ok/minor AE).
# STRUCTURAL_ONLY (substituted) rows DO NOT increment this — they pass the
# layout sniff test but skip pixel diff, so they're not visual evidence on
# their own. Final pass condition (line 1503) requires non-structural evidence
NON_STRUCTURAL_PASS_COUNT=0

# Build a lookup of wrapper-only sections so the AE loop can skip them.
# These have no ref content of their own (sticky-image holders, spacer wrappers).
WRAPPER_NAMES=$(python3 -c "
import json
m = json.loads(open('$DIR/sections/matches.json').read())
print(' '.join(x['name'] for x in m if x.get('wrapper')))
" 2>/dev/null || echo "")

# Guard: nullglob — if no ref PNGs were captured, the glob expands to a literal string
PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m ui_clone.section_compare_sections crop-manifest \
  "$DIR/sections/matches.json" \
  "$DIR/sections/ref" \
  "$DIR/sections/impl" \
  "$DIR/sections/crop-manifest.json"

# Evaluate only crops named by the CURRENT matches.json. Frozen references are
# intentionally reused, so globbing ref/*.png can pick up an orphan from an
# older enumeration (e.g. style_blurb-3 after the current match set has two
# blurbs) and falsely report a missing implementation section.
REF_IMGS=()
while IFS= read -r _matched_crop_name; do
  [ -z "$_matched_crop_name" ] && continue
  _matched_ref_crop="$DIR/sections/ref/${_matched_crop_name}.png"
  if [ -f "$_matched_ref_crop" ]; then
    REF_IMGS+=("$_matched_ref_crop")
  else
    RESULTS="${RESULTS}| ${_matched_crop_name} | — | — | setup | ❌ FAIL (current matched reference crop missing — recapture required) |\n"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
done < <(
  python3 -c '
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
for row in manifest.get("rows", []):
    print(row.get("name", ""))
' "$DIR/sections/crop-manifest.json"
)
if [ ${#REF_IMGS[@]} -eq 0 ]; then
  echo "ERROR: No ref section images captured in $DIR/sections/ref/ — check Step 3 output above"
  exit 1
fi

for REF_IMG in "${REF_IMGS[@]}"; do
  NAME=$(basename "$REF_IMG" .png)
  IMPL_IMG="$DIR/sections/impl/${NAME}.png"

  if [ ! -f "$IMPL_IMG" ]; then
    # A4 (Fix 95) — a ref section with no impl crop means the section never
    # rendered (genuinely missing), not a benign skip. Fail it by default so a
    # build that drops whole sections can't pass with FAIL_COUNT==0; set
    # UI_CLONE_ALLOW_MISSING_SECTIONS=1 to downgrade to a non-blocking skip.
    if [ "${UI_CLONE_ALLOW_MISSING_SECTIONS:-0}" = "1" ]; then
      RESULTS="${RESULTS}| ${NAME} | — | — | — | ⚠️ MISSING impl (allowed) |\n"
      SKIP_COUNT=$((SKIP_COUNT + 1))
    else
      RESULTS="${RESULTS}| ${NAME} | — | — | — | ❌ FAIL (ref section has no impl crop — section missing) |\n"
      FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
    continue
  fi

  # Skip strict AE for STRUCTURAL_WRAPPER sections (ref has no visible content
  # of its own — pixel comparison would always fail against the impl that
  # actually renders something).
  if echo " $WRAPPER_NAMES " | grep -q " ${NAME} "; then
    RESULTS="${RESULTS}| ${NAME} | — | — | wrapper | ⏭️ SKIP (structural wrapper) |\n"
    SKIP_COUNT=$((SKIP_COUNT + 1))
    continue
  fi

  # Skip strict AE for SUBSTITUTED sections (asset-substitution.json declared
  # the impl uses different fonts/images/videos than the ref). Layout regressions
  # are still caught by the structure diff in Step 5 below — only pixel-level
  # comparison is bypassed here.
  IS_SUBSTITUTED=0
  if [ "$SUBSTITUTION_ALL" -eq 1 ]; then
    IS_SUBSTITUTED=1
  elif [ -n "$SUBSTITUTION_PATTERNS" ]; then
    for PAT in $SUBSTITUTION_PATTERNS; do
      case "$NAME" in
        *"$PAT"*) IS_SUBSTITUTED=1; break ;;
      esac
    done
  fi
  if [ "$IS_SUBSTITUTED" -eq 1 ]; then
    if is_motion_structural_only_protected "$NAME"; then
      RESULTS="${RESULTS}| ${NAME} | — | — | motion-critical | ❌ FAIL (motion-critical section cannot use STRUCTURAL_ONLY) |\n"
      FAIL_COUNT=$((FAIL_COUNT + 1))
      continue
    fi
    # STRUCTURAL_ONLY rows are visual-evidence-deferred, not pixel passes.
    # They increment SUBSTITUTED_COUNT only — NOT PASS_COUNT — so the footer's
    # PASS count stays equal to the ✅ rows in the table (no hidden inflation).
    # Convergence still accepts them: check-converged keys on FAIL==0, and
    # post-implement counts PASS+STRUCTURAL_ONLY as evidence.
    RESULTS="${RESULTS}| ${NAME} | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n"
    SUBSTITUTED_COUNT=$((SUBSTITUTED_COUNT + 1))
    continue
  fi

  # A blank reference crop is capture failure, not implementation evidence.
  # Apply policy=all guards before the near-black detector below; otherwise a
  # black impl crop can be counted as a real FAIL and `continue` before the
  # existing guard conversion ever runs.
  GUARD_ROW=$(awk -F'\t' -v n="$NAME" '$1==n{print; exit}' "$DIR/sections/crop-guards.tsv" 2>/dev/null || true)
  GUARD_REASON=""
  GUARD_POLICY=""
  GUARD_SOURCE=""
  if [ -n "$GUARD_ROW" ]; then
    GUARD_REASON=$(printf '%s' "$GUARD_ROW" | cut -f2)
    GUARD_POLICY=$(printf '%s' "$GUARD_ROW" | cut -f3)
    GUARD_SOURCE=$(printf '%s' "$GUARD_ROW" | cut -f4)
  fi
  if [ "$GUARD_POLICY" = "all" ]; then
    UNMEASURED_COUNT=$((UNMEASURED_COUNT + 1))
    GUARD_SHORT=$(printf '%s' "$GUARD_REASON" | cut -c1-110)
    RESULTS="${RESULTS}| ${NAME} | — | — | unmeasured | ⚠️ UNMEASURED (${GUARD_SHORT}) |\n"
    echo "  ↳ ${NAME}: verdict converted to UNMEASURED before black detection — ${GUARD_REASON}"
    continue
  fi

  # Resize impl to match ref dimensions
  REF_SIZE=$(magick identify -format "%wx%h" "$REF_IMG" 2>/dev/null)
  IMPL_SIZE=$(magick identify -format "%wx%h" "$IMPL_IMG" 2>/dev/null)

  # batch-11 ITEM 4(c): crop-scale-mismatch tolerance. When the ref/impl crop
  # dims differ, force-resizing with "!" STRETCHES the impl and distorts its
  # aspect, inflating AE on identical content (an anti-aliasing shift across the
  # whole crop) — the "dssim leniency blocked by AE cap" class. The exact-stretch
  # stays the PRIMARY resized impl (keeps existing behaviour + the localized-
  # defect band check valid); within SECTION_CROP_SCALE_TOL we ALSO build an
  # aspect-preserving cover-fit CANDIDATE, and the AE step below takes the MIN of
  # stretch-vs-cover-fit. Taking the min can only LOWER AE on identical content
  # the stretch distorted, NEVER raise it, and the dssim_cap (THRESHOLD x
  # SECTION_DSSIM_AE_CAP_MULT) still gates extreme AE — so detection is never
  # weakened. Both candidates are EXACTLY ref dims.
  IMPL_COVER=""
  # SECTION_SKIP_IMPL_RESIZE=1 (calib-snapshot pass) leaves the impl crops at
  # their NATIVE box dims so a later copy into ref-calib/ preserves the impl-path
  # box (a scroll-scrub element captured at scale 0.67 stays w964, not stretched
  # to the frozen ref's w1440). The AE verdict of that pass is discarded; only its
  # crops are reused as the ref-instability calibration. (batch-13 ITEM 1)
  if [ "${SECTION_SKIP_IMPL_RESIZE:-0}" != "1" ] && [ "$REF_SIZE" != "$IMPL_SIZE" ]; then
    _R_W=$(echo "$REF_SIZE" | cut -dx -f1); _R_H=$(echo "$REF_SIZE" | cut -dx -f2)
    _I_W=$(echo "$IMPL_SIZE" | cut -dx -f1); _I_H=$(echo "$IMPL_SIZE" | cut -dx -f2)
    CROP_SCALE_TOL="${SECTION_CROP_SCALE_TOL:-1.04}"
    if awk -v rw="$_R_W" -v rh="$_R_H" -v iw="$_I_W" -v ih="$_I_H" \
           -v tol="$CROP_SCALE_TOL" 'BEGIN {
             if (iw<=0 || ih<=0 || rw<=0 || rh<=0) exit 1
             wr = (rw>iw) ? rw/iw : iw/rw
             hr = (rh>ih) ? rh/ih : ih/rh
             exit !(wr<=tol && hr<=tol)
           }'; then
      IMPL_COVER="$DIR/sections/diff/.${NAME}.cover.png"
      magick "$IMPL_IMG" -resize "${_R_W}x${_R_H}^" -gravity center \
        -extent "${_R_W}x${_R_H}" -quality 95 "$IMPL_COVER" 2>/dev/null || IMPL_COVER=""
    fi
    magick "$IMPL_IMG" -resize "$REF_SIZE!" -quality 95 "$IMPL_IMG" 2>/dev/null
  fi

  # A4 (Fix 95) — all-black/blank-impl detector. AE/dssim can score a near-black
  # impl crop as "close enough" against a content-bearing ref; catch the
  # omx-style black-hero (a section renders as a near-black band) by comparing
  # luminance directly. Fail ONLY when the impl crop is near-black AND the ref
  # is NOT — a legitimately dark section (dark in both) does not trip.
  IMPL_LUM=$(magick "$IMPL_IMG" -colorspace Gray -format "%[fx:mean] %[fx:standard_deviation]" info: 2>/dev/null)
  REF_LUM=$(magick "$REF_IMG" -colorspace Gray -format "%[fx:mean] %[fx:standard_deviation]" info: 2>/dev/null)
  if [ -n "$IMPL_LUM" ] && [ -n "$REF_LUM" ] && \
     awk -v il="$IMPL_LUM" -v rl="$REF_LUM" \
         -v bm="${UI_CLONE_BLACK_MEAN_MAX:-0.06}" -v bs="${UI_CLONE_BLACK_STD_MAX:-0.06}" '
       BEGIN { split(il, a, " "); split(rl, b, " ");
         impl_black = (a[1] + 0 < bm && a[2] + 0 < bs);
         ref_black  = (b[1] + 0 < bm && b[2] + 0 < bs);
         exit !(impl_black && !ref_black) }'; then
    RESULTS="${RESULTS}| ${NAME} | — | — | black | ❌ FAIL (impl section near-black; ref has content) |\n"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    continue
  fi

  DIFF_IMG="$DIR/sections/diff/${NAME}.png"
  # -fuzz tolerance: pixels with color diff <= fuzz% are considered identical.
  # Filters sub-pixel AA noise, font hinting, paper-texture/JPEG grain — keeping AE on structural divergence.
  FUZZ="${SECTION_FUZZ:-8%}"
  # Background fill for the vacated strip of a non-circular translate. A real
  # vertical scroll-phase shift reveals the page BACKGROUND, so the strip is
  # filled opaque (NOT transparent: a transparent strip is scored as zero-diff by
  # `magick compare -metric AE`, which would let a LARGE shift earn free
  # alignment credit for the revealed region). The fill MUST be the page
  # background, NOT a sampled edge row: when ref content touches the top edge
  # (headings/nav/hero bands — very common), an edge sample IS content, and
  # filling the revealed strip with that content color forges the exact ref
  # pixels — fake-collapsing AE/dssim and turning a real layout misplacement into
  # a false pass-by-motion-phase (anti-cheat blocker). Use the MODAL (most-
  # frequent) color of the WHOLE crop: for a content-on-background section the
  # background dominates by pixel count, so the mode is the true page bg and is
  # robust to content touching any single edge. Quantize first so anti-aliased
  # shades collapse into the dominant bucket. SECTION_SHIFT_BG overrides for
  # operators (humans-only knob).
  SHIFT_BG="${SECTION_SHIFT_BG:-}"
  if [ -z "$SHIFT_BG" ]; then
    SHIFT_BG=$(magick "$REF_IMG" +dither -colors 16 -depth 8 -format "%c" \
                 histogram:info:- 2>/dev/null \
                 | sort -rn | head -1 | grep -oiE '#[0-9A-F]{6,8}' | head -1)
    [ -z "$SHIFT_BG" ] && SHIFT_BG="white"
  fi
  _mk_shift() {  # write base img $1 translated vertically by $2 px (NON-circular) to $3
    # dy>0 moves content DOWN: chop |dy| rows off the BOTTOM (they fall off, do
    # NOT wrap) and splice |dy| opaque rows onto the TOP. dy<0 moves content UP:
    # chop the TOP, splice the BOTTOM. Result stays exactly ref-dimensioned. No
    # -roll, no -virtual-pixel wrap: content scrolled off one edge is DISCARDED,
    # never re-introduced at the opposite edge, so misplaced content cannot wrap
    # into alignment.
    local base="$1" dy="$2" out="$3" k
    case "$dy" in
      -*) k="${dy#-}"
          magick "$base" -background "$SHIFT_BG" -alpha remove \
            -gravity North -chop "0x${k}" -gravity South -splice "0x${k}" \
            "$out" 2>/dev/null || return 1 ;;
      *)  k="${dy#+}"
          magick "$base" -background "$SHIFT_BG" -alpha remove \
            -gravity South -chop "0x${k}" -gravity North -splice "0x${k}" \
            "$out" 2>/dev/null || return 1 ;;
    esac
  }
  _ae_at() {  # AE: ref vs base img $1 translated vertically by $2 px (NON-circular) -> diff to $3
    local base="$1" dy="$2" out="$3"
    local shifted="$base"
    if [ "$dy" != "0" ]; then
      shifted="$DIR/sections/diff/.${NAME}.shift.png"
      _mk_shift "$base" "$dy" "$shifted" || shifted="$base"
    fi
    local a
    a=$(magick compare -metric AE -fuzz "$FUZZ" "$REF_IMG" "$shifted" "$out" 2>&1 || true)
    if [ "$shifted" = "$DIR/sections/diff/.${NAME}.shift.png" ]; then
      rm -f "$shifted" 2>/dev/null || true
    fi
    # Normalize the raw AE (pixel_count * QuantumRange on IM Q16) back to a raw
    # mismatched-pixel COUNT. This is the single chokepoint every AE flows
    # through, so all downstream comparisons, ratios (AE_ZERO_RAW), and severity
    # tiers see consistent pixel-count units. No per-call assert here (dims not
    # handy); the loud tripwire is at the top-level REF_W/REF_H site below.
    local _raw_a
    _raw_a=$(echo "$a" | head -1 | awk '{ if ($1 ~ /^[0-9.eE+-]+$/) printf "%.0f", $1 }')
    [ -n "$_raw_a" ] && _ae_normalize "$_raw_a"
  }
  # AE = the MINIMUM over {stretch, cover-fit} x {0, +/- vertical phase offsets},
  # keeping the diff image of the winning alignment. Each candidate can only
  # LOWER AE; a real (localized/structural) defect cannot be aligned or rescaled
  # away (shifting the whole crop to fix one element misaligns the rest), so its
  # min stays high — and the dssim_cap still gates extreme AE downstream.
  AE=$(_ae_at "$IMPL_IMG" 0 "$DIFF_IMG")
  # Capture the TRUE unshifted AE before the ±6px _try_base sweep below overwrites
  # $AE with the post-sweep min. The motion-phase collapse ratio (Commit 2) must
  # be measured against the genuine dy=0 AE, not the already-slightly-collapsed min.
  AE_ZERO_RAW="$AE"
  _try_base() {  # update AE/DIFF if (base, dy) scores lower than the current AE
    local base="$1" dy="$2"
    if [ -z "$base" ]; then return 0; fi
    local c
    c=$(_ae_at "$base" "$dy" "$DIR/sections/diff/.${NAME}.cand.png")
    if [ -n "$c" ]; then
      if [ -z "$AE" ] || [ "$c" -lt "$AE" ] 2>/dev/null; then
        AE="$c"
        mv -f "$DIR/sections/diff/.${NAME}.cand.png" "$DIFF_IMG" 2>/dev/null || true
      fi
    fi
    rm -f "$DIR/sections/diff/.${NAME}.cand.png" 2>/dev/null || true
    return 0
  }
  # batch-11 ITEM 4(c): the aspect-preserving cover-fit candidate at offset 0.
  if [ -n "$IMPL_COVER" ]; then _try_base "$IMPL_COVER" 0; fi
  # batch-11 ITEM 4(b): scroll-phase-offset tolerance. Two independent captures
  # of identical content can settle a few px apart vertically, so a whole section
  # is uniformly shifted — inflating AE on content that reads identical. Sweep a
  # SMALL symmetric vertical-offset band (2px granularity, closest first) on both
  # bases and keep the lowest AE. Bounded by SECTION_SCROLL_PHASE_TOL_PX
  # (0 disables); a defect-scale shift is never aligned away.
  SCROLL_PHASE_TOL_PX="${SECTION_SCROLL_PHASE_TOL_PX:-6}"
  if [ -n "$AE" ] && [ "$AE" -gt 0 ] 2>/dev/null && [ "${SCROLL_PHASE_TOL_PX:-0}" -gt 0 ] 2>/dev/null; then
    for _mag in $(seq 2 2 "$SCROLL_PHASE_TOL_PX"); do
      _try_base "$IMPL_IMG" "+${_mag}"
      _try_base "$IMPL_IMG" "-${_mag}"
      if [ -n "$IMPL_COVER" ]; then
        _try_base "$IMPL_COVER" "+${_mag}"
        _try_base "$IMPL_COVER" "-${_mag}"
      fi
    done
  fi
  if [ -n "$IMPL_COVER" ]; then rm -f "$IMPL_COVER" 2>/dev/null || true; fi

  if [ -z "$AE" ]; then
    RESULTS="${RESULTS}| ${NAME} | ERROR | — | — | ⚠️ |\n"
    SKIP_COUNT=$((SKIP_COUNT + 1))
    continue
  fi

  # Normalize AE by section pixel area (per megapixel) so a 1200px-tall section
  # isn't unfairly penalized vs a 600px-tall one with identical defect density.
  # Severity tiers below use this normalized value, not raw AE.
  REF_W=$(echo "$REF_SIZE" | cut -dx -f1)
  REF_H=$(echo "$REF_SIZE" | cut -dx -f2)
  # Loud tripwire: AE is already normalized to a pixel count by _ae_at, so it
  # must not exceed the crop's pixel budget. If it does, the AE unit divisor is
  # wrong (ImageMagick metric behavior changed again) — warn so the run is not
  # silently mis-tiered like the 2026-07 65535x inflation was.
  if [ -n "$REF_W" ] && [ -n "$REF_H" ] && [ "$REF_W" -gt 0 ] 2>/dev/null; then
    awk -v ae="$AE" -v w="$REF_W" -v h="$REF_H" 'BEGIN{ exit (ae > w*h*1.01) ? 1 : 0 }' || \
      echo "section-compare: WARNING AE $AE > pixels ${REF_W}x${REF_H} for ${NAME} — AE unit divisor may be wrong (see lib/ae-quantum.sh)" >&2
  fi
  AE_PER_MPX=$(awk -v ae="$AE" -v w="$REF_W" -v h="$REF_H" 'BEGIN { area = (w*h)/1000000; if (area > 0) printf "%.0f", ae/area; else print "0" }')

  # ── batch-13 ITEM 1: REF-DYNAMIC sections use STRUCTURAL/LAYOUT PARITY ──
  # A section the REFERENCE could not self-match (ref vs ref-calib, two
  # independent loads) is dynamic — framer scroll-scrub / splash / carousel — and
  # cannot be pixel-AE-compared against any impl. Switch to structural/layout
  # parity against the reference's OWN scrub noise floor. Detection is preserved:
  # a blanked section already FAILED the near-black check above; here a resized
  # box or a structural change beyond the noise floor still FAILS (the verdict +
  # noise floor are unit-tested in ui_clone.section_dynamic). Static sections
  # never enter this branch, so their strict AE is untouched.
  if [ -f "$DIR/sections/ref-dynamic.json" ]; then
    DYN_VERDICT=$(PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 - \
        "$DIR/sections/ref-dynamic.json" "$NAME" "$REF_IMG" "$IMPL_IMG" \
        "$REF_W" "$REF_H" "$IMPL_SIZE" "$AE_PER_MPX" <<'PY' 2>/dev/null || true
import json, subprocess, sys
from ui_clone.section_dynamic import dynamic_section_verdict
dynjson, name, ref_img, impl_img, ref_w, ref_h, impl_size, ae_per_mpx = sys.argv[1:9]
try:
    rec = (json.load(open(dynjson)) or {}).get(name) or {}
except Exception:
    rec = {}
if not rec.get("dynamic"):
    print("static"); sys.exit(0)
def _dssim(a, b):
    try:
        o = subprocess.run(["dssim", a, b], capture_output=True, text=True, timeout=60).stdout.split()
        return float(o[0]) if o else None
    except Exception:
        return None
try:
    iw, ih = (int(x) for x in str(impl_size).lower().split("x"))
except Exception:
    iw = ih = 0
def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
status, reason = dynamic_section_verdict(
    ref_self_dssim=rec.get("selfDssim"),
    impl_dssim=_dssim(ref_img, impl_img),
    ref_w=float(ref_w), ref_h=float(ref_h), impl_w=float(iw), impl_h=float(ih),
    impl_near_black=False,
    calib_w=_f(rec.get("calibW")), calib_h=_f(rec.get("calibH")),
    # Task B / specific regression: same-frame strict-AE gate. impl AE/Mpx at the SAME scroll
    # frame must stay within the ref's OWN same-frame AE noise (selfAePerMpx) — a
    # content defect over the ceiling FAILS even if dssim<=floor (closes F1).
    impl_ae_per_mpx=_f(ae_per_mpx),
    ref_self_ae_per_mpx=_f(rec.get("selfAePerMpx")),
)
print(f"{status}\t{reason}")
PY
)
    if [ -n "$DYN_VERDICT" ] && [ "$DYN_VERDICT" != "static" ]; then
      DYN_STATUS=$(printf '%s' "$DYN_VERDICT" | head -1 | cut -f1)
      DYN_REASON=$(printf '%s' "$DYN_VERDICT" | head -1 | cut -f2- | cut -c1-90)
      if [ "$DYN_STATUS" = "pass" ]; then
        RESULTS="${RESULTS}| ${NAME} | ${AE} | ${AE_PER_MPX} | dynamic-parity | 🌀 DYNAMIC (${DYN_REASON}) |\n"
        PASS_COUNT=$((PASS_COUNT + 1))
        NON_STRUCTURAL_PASS_COUNT=$((NON_STRUCTURAL_PASS_COUNT + 1))
      else
        RESULTS="${RESULTS}| ${NAME} | ${AE} | ${AE_PER_MPX} | dynamic-defect | ❌ FAIL (ref-dynamic section: ${DYN_REASON}) |\n"
        FAIL_COUNT=$((FAIL_COUNT + 1))
      fi
      continue
    fi
  fi

  # Thresholds operate on AE/Mpx (defect density). Default 2000 still works for
  # static content; use SECTION_THRESHOLD=50000 for image/animation-rich pages.
  # Saturation band (AE/Mpx >= 800k) means the per-pixel diff is so dense that
  # AE has lost its gradient — visual-judge can't reduce it with macro tweaks
  # and the agent must return to Phase 4 LLM refinement (typography, asset
  # references, scroll-trigger wiring) before more iteration is useful.
  THRESHOLD="${SECTION_THRESHOLD:-2000}"
  SATURATION="${AE_SATURATION:-800000}"
  DSSIM_FALLBACK="${SECTION_DSSIM_FALLBACK:-1}"
  DSSIM_PASS_MAX="${SECTION_DSSIM_PASS_MAX:-0.015}"
  DSSIM_SCORE=""
  # NOTE: no upper AE bound here — dSSIM is computed even in the saturated band
  # (AE_PER_MPX >= AE_SATURATION). Saturation means AE lost its gradient, which
  # is exactly when independent dSSIM evidence matters: the strict tier below
  # rescues subpixel-phase artifacts that saturate AE while dSSIM stays ~0.
  # The looser tiers (pass-by-dssim / pass-by-perceptual) still carry their own
  # < SATURATION and dssim_cap_allows bounds unchanged.
  if [ "$DSSIM_FALLBACK" = "1" ] \
     && [ "$AE_PER_MPX" -gt "$THRESHOLD" ] \
     && command -v dssim >/dev/null 2>&1; then
    DSSIM_SCORE=$(dssim "$REF_IMG" "$IMPL_IMG" 2>/dev/null | awk '{print $1}')
  fi
  # Ref-variance guard. dssim/SSIM is DEGENERATE when the reference crop has
  # ~zero variance (blank / near-uniform): structure & contrast terms collapse,
  # so dssim -> ~0 regardless of impl content as long as the impl is also sparse.
  # That false-passes a blank-ref section via pass-by-dssim/perceptual against any
  # impl. Require real ref content (std >= SECTION_REF_MIN_STD) before trusting
  # any dssim-based pass. Blank crops measure std=0; real content >= ~0.13.
  REF_HAS_VARIANCE=1
  if [ -n "$DSSIM_SCORE" ] && command -v magick >/dev/null 2>&1; then
    REF_STD=$(magick "$REF_IMG" -format "%[fx:standard_deviation]" info: 2>/dev/null)
    if [ -n "$REF_STD" ] && awk -v s="$REF_STD" -v m="${SECTION_REF_MIN_STD:-0.05}" 'BEGIN{exit !(s+0 < m+0)}'; then
      REF_HAS_VARIANCE=0
    fi
  fi
  if [ "$AE_PER_MPX" -le 500 ]; then
    STATUS="✅"
    SEV="ok"
    PASS_COUNT=$((PASS_COUNT + 1))
    NON_STRUCTURAL_PASS_COUNT=$((NON_STRUCTURAL_PASS_COUNT + 1))
  elif [ "$AE_PER_MPX" -le "$THRESHOLD" ]; then
    STATUS="✅"
    SEV="minor"
    PASS_COUNT=$((PASS_COUNT + 1))
    NON_STRUCTURAL_PASS_COUNT=$((NON_STRUCTURAL_PASS_COUNT + 1))
  elif [ -n "$DSSIM_SCORE" ] \
       && [ "$REF_HAS_VARIANCE" = "1" ] \
       && [ "$AE_PER_MPX" -lt "$SATURATION" ] \
       && dssim_cap_allows "$AE_PER_MPX" "$THRESHOLD" "$SECTION_DSSIM_AE_CAP_MULT" \
            "$DIR/sections/${NAME}-judge.json" "$IMPL_IMG" "$REF_IMG" \
       && awk -v d="$DSSIM_SCORE" -v max="$DSSIM_PASS_MAX" 'BEGIN{exit !(d+0 <= max+0)}'; then
    STATUS="✅"
    SEV="pass-by-dssim"
    PASS_COUNT=$((PASS_COUNT + 1))
    NON_STRUCTURAL_PASS_COUNT=$((NON_STRUCTURAL_PASS_COUNT + 1))
    if [ "${DSSIM_LAST_CAP_OVERRIDE:-0}" = "1" ]; then
      JUDGE_OVERRIDE_COUNT=$((JUDGE_OVERRIDE_COUNT + 1))
      JUDGE_OVERRIDE_NAMES="${JUDGE_OVERRIDE_NAMES}${JUDGE_OVERRIDE_NAMES:+, }${NAME}"
    fi
  elif [ "$SECTION_PERCEPTUAL_DENSE" = "1" ] \
       && [ "$PERCEPTUAL_REFSHOT_CLEAN" = "1" ] \
       && [ -n "$DSSIM_SCORE" ] \
       && [ "$REF_HAS_VARIANCE" = "1" ] \
       && awk -v d="$DSSIM_SCORE" -v max="$SECTION_DSSIM_STRICT_MAX" 'BEGIN{exit !(d+0 <= max+0)}' \
       && [ "$(_perceptual_dom_sev "$NAME")" != "critical" ] \
       && [ "$(_perceptual_dom_sev "$NAME")" != "major" ] \
       && ! _perceptual_localized_defect "$REF_IMG" "$IMPL_IMG"; then
    # Subpixel-phase artifact rescue (loop-ebpb-3 evidence class). Deliberately
    # exempt from BOTH dssim_cap_allows and the AE_SATURATION bound: at
    # dSSIM <= SECTION_DSSIM_STRICT_MAX with MEASURED ref variance, no
    # major/critical DOM delta, and no localized 200px defect band, a saturated
    # AE is evidence the metric lost its gradient — not evidence of a defect.
    # The 42x-threshold incident that motivated dssim_cap involved the LOOSE
    # dense ceiling (0.12); this tier is 4x tighter and keeps every anti-cheat
    # guard the perceptual tier carries: PERCEPTUAL_REFSHOT_CLEAN (an impl that
    # EMBEDS the ref screenshot scores dSSIM~0, so the screenshot-paste
    # detector holds the same veto it holds on pass-by-perceptual) and the
    # DENSE=1 mode gate (in DENSE=0 escape-hatch mode structure-severity.txt
    # and the refshot check are never produced — the dom guard would be
    # vacuous and the documented byte-identical strict-AE contract would
    # break).
    STATUS="✅"
    SEV="pass-by-dssim-strict"
    PASS_COUNT=$((PASS_COUNT + 1))
    NON_STRUCTURAL_PASS_COUNT=$((NON_STRUCTURAL_PASS_COUNT + 1))
  elif [ "$SECTION_PERCEPTUAL_DENSE" = "1" ] \
       && [ "$PERCEPTUAL_REFSHOT_CLEAN" = "1" ] \
       && [ "$REF_HAS_VARIANCE" = "1" ] \
       && [ -n "$DSSIM_SCORE" ] \
       && [ "$AE_PER_MPX" -lt "$SATURATION" ] \
       && dssim_cap_allows "$AE_PER_MPX" "$THRESHOLD" "$SECTION_DSSIM_AE_CAP_MULT" \
            "$DIR/sections/${NAME}-judge.json" "$IMPL_IMG" "$REF_IMG" \
       && _perceptual_is_dense "$NAME" \
       && awk -v d="$DSSIM_SCORE" -v max="$SECTION_DSSIM_DENSE_MAX" 'BEGIN{exit !(d+0 <= max+0)}' \
       && [ "$(_perceptual_dom_sev "$NAME")" != "critical" ] \
       && [ "$(_perceptual_dom_sev "$NAME")" != "major" ] \
       && ! _perceptual_localized_defect "$REF_IMG" "$IMPL_IMG"; then
    # Dense (text/SVG-rich) section whose global divergence is perceptually
    # small AND has no critical/major DOM delta AND no localized structural
    # defect band. Counted as a genuine ✅ pass, like pass-by-dssim. Buggy
    # sections with a low global dssim but a real localized defect (e.g. a
    # misplaced label) FAIL here via the localized-defect band check.
    STATUS="✅"
    SEV="pass-by-perceptual"
    PASS_COUNT=$((PASS_COUNT + 1))
    NON_STRUCTURAL_PASS_COUNT=$((NON_STRUCTURAL_PASS_COUNT + 1))
    if [ "${DSSIM_LAST_CAP_OVERRIDE:-0}" = "1" ]; then
      JUDGE_OVERRIDE_COUNT=$((JUDGE_OVERRIDE_COUNT + 1))
      JUDGE_OVERRIDE_NAMES="${JUDGE_OVERRIDE_NAMES}${JUDGE_OVERRIDE_NAMES:+, }${NAME}"
    fi
  elif [ "$AE" -lt "${SECTION_AE_ABS_FLOOR:-8000}" ] \
       && [ -n "$DSSIM_SCORE" ] \
       && [ "$REF_HAS_VARIANCE" = "1" ] \
       && awk -v d="$DSSIM_SCORE" -v max="$SECTION_DSSIM_DENSE_MAX" 'BEGIN{exit !(d+0 <= max+0)}' \
       && awk -v w="$REF_W" -v h="$REF_H" -v max="${SECTION_AE_FLOOR_MAX_MPX:-0.5}" \
            'BEGIN{ area=(w*h)/1000000; exit !(area > 0 && area <= max+0) }' \
       && ! _perceptual_localized_defect "$REF_IMG" "$IMPL_IMG"; then
    # Absolute-AE floor: a tiny crop (e.g. a ~0.06 Mpx header bar) inflates
    # AE/Mpx into the critical band on a near-zero ABSOLUTE pixel difference —
    # AE_PER_MPX = AE/area, so a sub-0.1 Mpx denominator turns a few-thousand-pixel
    # diff into a "critical" score (the navercorp header: AE=3335 -> AE/Mpx 23160).
    # Require DSSIM_SCORE to be non-empty: REF_HAS_VARIANCE is only MEASURED when
    # dssim ran (else it defaults open to 1), so this makes the blank/near-uniform
    # ref guard real evidence, not a default — without a measured ref-std the
    # section conservatively falls through to the fail branches.
    # N4: the floor previously checked only ABSOLUTE AE + variance + localized band,
    # but NOT the global-dssim ceiling its sibling pass tiers (pass-by-dssim /
    # pass-by-perceptual) carry — so a low-absolute-AE but high-global-dssim
    # DISTRIBUTED defect (wrong font / global tint / uniform layout shift) rode the
    # floor to green because a uniformly-spread divergence trips no 200px localized
    # band (reproduced AE=4988 / dssim=0.199). Add (a) the same SECTION_DSSIM_DENSE_MAX
    # (0.12) global-dssim ceiling, and (b) an upper REF-area Mpx bound
    # (SECTION_AE_FLOOR_MAX_MPX, default 0.5) so the floor only rescues genuinely
    # tiny-crop denominator artifacts — its stated navercorp-header purpose — not a
    # full-size section that merely has few absolute diff pixels.
    STATUS="✅"
    SEV="pass-by-ae-floor"
    PASS_COUNT=$((PASS_COUNT + 1))
    NON_STRUCTURAL_PASS_COUNT=$((NON_STRUCTURAL_PASS_COUNT + 1))
  elif [ "$SECTION_MOTION_PHASE" = "1" ] \
       && [ -n "$DSSIM_SCORE" ] \
       && [ "$REF_HAS_VARIANCE" = "1" ] \
       && [ "$AE_PER_MPX" -lt "$SATURATION" ] \
       && [ -n "$AE_ZERO_RAW" ] \
       && [ "$AE_ZERO_RAW" -gt 0 ] 2>/dev/null; then
    # Motion shift-search tier (Commit 2). A faithful scroll-reveal section caught
    # at a different sub-frame is IDENTICAL content uniformly translated vertically
    # by tens-to-low-hundreds px. The ±6px _try_base sweep above is too narrow.
    # Here we run a WIDE NON-circular vertical search (no -roll wrap) and let
    # motion_phase_verdict decide: collapse + shifted-structure + no localized
    # defect. A broken impl does NOT collapse (best stays ~dy=0) and/or has low
    # shifted structure and/or a localized band — and FAILS, landing on its
    # original major/critical severity (preserved below).
    MP_BEST_AE="$AE_ZERO_RAW"   # baseline to beat = true unshifted AE@0
    MP_BEST_DY=0
    MP_TMP="$DIR/sections/diff/.${NAME}.mp.png"
    for _md in $(seq "$SECTION_MOTION_PHASE_STEP" "$SECTION_MOTION_PHASE_STEP" "$SECTION_MOTION_PHASE_MAX_PX"); do
      for _sgn in "-" "+"; do
        _c=$(_ae_at "$IMPL_IMG" "${_sgn}${_md}" "$MP_TMP")
        if [ -n "$_c" ] && { [ -z "$MP_BEST_AE" ] || [ "$_c" -lt "$MP_BEST_AE" ] 2>/dev/null; }; then
          MP_BEST_AE="$_c"; MP_BEST_DY="${_sgn}${_md}"
        fi
      done
    done
    rm -f "$MP_TMP" 2>/dev/null || true
    # Re-measure shifted-pair dssim at THE winning offset (same non-circular
    # translate the AE used), so collapse and structure agree on one alignment.
    # Default to the UNSHIFTED dssim; only adopt the re-measured shifted dssim when
    # it is non-empty. A glitched/empty re-measure must NOT clobber it to empty
    # (which would force a "missing dssim" fail). Falling back to the unshifted
    # DSSIM_SCORE is conservative — it is >= the shifted value, so it can only make
    # G2 harder to pass, never let a broken impl through.
    MP_SHIFT_DSSIM="$DSSIM_SCORE"
    # Localized-defect (G4) must be judged at the WINNING alignment, not on the
    # unshifted pair. A faithfully scroll-shifted section differs in EVERY band
    # when compared unshifted (the whole frame is offset by best_dy), so the
    # per-band localized check on IMPL_IMG always tripped — vetoing the very
    # uniform-shift case this tier exists to pass, leaving it inert. Recompute it
    # on the impl shifted by MP_BEST_DY (the same non-circular translate the AE
    # search used) so only a defect that SURVIVES alignment vetoes. best_dy=0 →
    # the shifted image IS the unshifted impl, so the unshifted pair is correct.
    MP_LOC_IMG="$IMPL_IMG"
    MP_SH="$DIR/sections/diff/.${NAME}.mpsh.png"
    if [ "$MP_BEST_DY" != "0" ] && _mk_shift "$IMPL_IMG" "$MP_BEST_DY" "$MP_SH"; then
      _mp_sd=$(dssim "$REF_IMG" "$MP_SH" 2>/dev/null | awk '{print $1}')
      [ -n "$_mp_sd" ] && MP_SHIFT_DSSIM="$_mp_sd"
      MP_LOC_IMG="$MP_SH"
    fi
    MP_LOCALIZED=0
    _perceptual_localized_defect "$REF_IMG" "$MP_LOC_IMG" && MP_LOCALIZED=1
    rm -f "$MP_SH" 2>/dev/null || true
    MP_VERDICT=$(PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 - \
        "$AE_ZERO_RAW" "$MP_BEST_AE" "$MP_SHIFT_DSSIM" "$REF_HAS_VARIANCE" "$MP_LOCALIZED" \
        "$SECTION_MOTION_COLLAPSE_MIN" "$SECTION_MOTION_MIN_STRUCT" <<'PY' 2>/dev/null || true
import sys
from ui_clone.section_dynamic import motion_phase_verdict
azr, asm, sd, refvar, loc, cmin, mstruct = sys.argv[1:8]
def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
status, reason = motion_phase_verdict(
    ae_zero=_f(azr), ae_shift_min=_f(asm), shifted_dssim=_f(sd),
    ref_has_variance=(refvar == "1"), localized_defect=(loc == "1"),
    collapse_min=float(cmin), min_struct=float(mstruct),
)
print(f"{status}\t{reason}")
PY
)
    MP_STATUS=$(printf '%s' "$MP_VERDICT" | head -1 | cut -f1)
    MP_REASON=$(printf '%s' "$MP_VERDICT" | head -1 | cut -f2- | cut -c1-110)
    if [ "$MP_STATUS" = "pass" ]; then
      STATUS="✅"
      SEV="pass-by-motion-phase"
      PASS_COUNT=$((PASS_COUNT + 1))
      NON_STRUCTURAL_PASS_COUNT=$((NON_STRUCTURAL_PASS_COUNT + 1))
      echo "  ↳ ${NAME}: motion-phase pass (best_dy=${MP_BEST_DY}) — ${MP_REASON}"
    else
      # Not a motion phase — preserve the ORIGINAL major/critical severity this
      # section would have received without the tier (it never reaches the
      # branches below because this is an elif; saturated is excluded by the guard).
      if [ "$AE_PER_MPX" -le $((THRESHOLD * 10)) ]; then SEV="major"; else SEV="critical"; fi
      STATUS="❌"
      FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
  elif [ "$AE_PER_MPX" -le $((THRESHOLD * 10)) ]; then
    STATUS="❌"
    SEV="major"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  elif [ "$AE_PER_MPX" -lt "$SATURATION" ]; then
    STATUS="❌"
    SEV="critical"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  else
    STATUS="🌑"
    SEV="saturated"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi

  # Crop-evidence guard conversion (vacuous-pass closure). A guard row in
  # crop-guards.tsv means the crop pair is not pixel evidence for this
  # content-bearing section: blank ref / symmetric-blank / >60% masked /
  # color-flattened pair. policy "all" converts every verdict (a blank REF
  # crop is a capture failure — failing the impl for it is as wrong as
  # passing it); policy "pass-only" converts pass tiers but keeps fails
  # (a fail on the unmasked remainder is still real evidence).
  if [ -n "$GUARD_ROW" ]; then
    if [ "$GUARD_POLICY" = "structural-only" ] && [ "$STATUS" = "✅" ] \
       && { ! is_motion_structural_only_protected "$NAME" \
            || [ "$GUARD_SOURCE" = "masked-media-motion" ]; }; then
      # Run strict pixel comparison first. A real failure on the unmasked
      # remainder must remain a failure; only a pass-tier crop may defer to the
      # independently required media/structure proofs. Motion-critical media
      # receives this distinct source only when the plan also declares the
      # dedicated motion-parity gate; generic STRUCTURAL_ONLY stays blocked.
      PASS_COUNT=$((PASS_COUNT - 1))
      NON_STRUCTURAL_PASS_COUNT=$((NON_STRUCTURAL_PASS_COUNT - 1))
      SUBSTITUTED_COUNT=$((SUBSTITUTED_COUNT + 1))
      GUARD_STRUCTURAL_COUNT=$((GUARD_STRUCTURAL_COUNT + 1))
      RESULTS="${RESULTS}| ${NAME} | ${AE} | ${AE_PER_MPX} | ${GUARD_SOURCE:-masked-media} | 🔁 STRUCTURAL_ONLY |\n"
      echo "  ↳ ${NAME}: pass-tier pixel verdict deferred to STRUCTURAL_ONLY — ${GUARD_REASON}"
      continue
    fi
    if [ "$STATUS" = "✅" ] || [ "$GUARD_POLICY" = "all" ]; then
      case "$SEV" in
        ok|minor|pass-by-dssim|pass-by-dssim-strict|pass-by-perceptual|pass-by-ae-floor|pass-by-motion-phase)
          PASS_COUNT=$((PASS_COUNT - 1))
          NON_STRUCTURAL_PASS_COUNT=$((NON_STRUCTURAL_PASS_COUNT - 1))
          ;;
        major|critical|saturated)
          FAIL_COUNT=$((FAIL_COUNT - 1))
          ;;
      esac
      UNMEASURED_COUNT=$((UNMEASURED_COUNT + 1))
      GUARD_SHORT=$(printf '%s' "$GUARD_REASON" | cut -c1-110)
      RESULTS="${RESULTS}| ${NAME} | ${AE} | ${AE_PER_MPX} | unmeasured | ⚠️ UNMEASURED (${GUARD_SHORT}) |\n"
      echo "  ↳ ${NAME}: verdict converted to UNMEASURED — ${GUARD_REASON}"
      continue
    fi
  fi

  # Diagnostic: name the cap when it (not the dssim score) closed the
  # leniency path, so the agent knows the sanctioned next step is a
  # visual-judge confirmation, not threshold tuning.
  if [ "$STATUS" = "❌" ] && [ -n "$DSSIM_SCORE" ] \
     && [ "$AE_PER_MPX" -gt $((THRESHOLD * SECTION_DSSIM_AE_CAP_MULT)) ] \
     && awk -v d="$DSSIM_SCORE" -v max="$SECTION_DSSIM_DENSE_MAX" 'BEGIN{exit !(d+0 <= max+0)}'; then
    echo "  ↳ ${NAME}: dssim leniency blocked by AE cap (AE/Mpx ${AE_PER_MPX} > ${SECTION_DSSIM_AE_CAP_MULT}x threshold)."
    echo "    To confirm a perceptual pass, dispatch visual-debug-reviewer for this section and write"
    echo "    $DIR/sections/${NAME}-judge.json with verdict PASS bound to the exact crop bytes reviewed:"
    echo "      {\"verdict\":\"PASS\", \"implSha256\":\"\$(shasum -a 256 '$IMPL_IMG' | cut -d' ' -f1)\","
    echo "       \"refSha256\":\"\$(shasum -a 256 '$REF_IMG' | cut -d' ' -f1)\", \"rationale\":\"<why it passes despite high AE>\"}"
    echo "    The override is re-verified against the live crop sha256 at read time; a bare verdict line,"
    echo "    a mismatched or absent hash, or an empty rationale is ignored."
  fi

  RESULTS="${RESULTS}| ${NAME} | ${AE} | ${AE_PER_MPX} | ${SEV} | ${STATUS} |\n"
done

# A3 (Fix 94) — gate large EXTRA_IN_IMPL sections. pair_sections records impl
# sections that paired with NO ref section as EXTRA_IN_IMPL; the AE loop above
# iterates only ref crops, so a duplicated/misplaced impl block (a hero
# re-rendered at the page bottom, or a dedup-renamed "-2" section) never reaches
# PASS/FAIL and a structurally broken page scores fine. A faithful clone has ~0
# large extra sections. BOUNDED: fail only on tall extras (absolute px floor),
# not a general structural/order diff. Raise UI_CLONE_EXTRA_SECTION_MIN_PX to relax.
EXTRA_SECTION_MIN_PX="${UI_CLONE_EXTRA_SECTION_MIN_PX:-500}"
EXTRA_OUT=$(python3 - "$DIR/sections/matches.json" "$EXTRA_SECTION_MIN_PX" <<'PY' 2>/dev/null || true
import json, sys
from ui_clone.section_compare_sections import find_large_extra_sections
try:
    m = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
if not isinstance(m, list):
    sys.exit(0)
for name, h in find_large_extra_sections(m, int(sys.argv[2])):
    print(f"{name}|{h}")
PY
)
if [ -n "$EXTRA_OUT" ]; then
  while IFS='|' read -r X_NAME X_H; do
    [ -z "$X_NAME" ] && continue
    RESULTS="${RESULTS}| ${X_NAME} | — | ${X_H}px | extra | ❌ FAIL (extra impl section absent from ref — duplicate/misplaced) |\n"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  done <<< "$EXTRA_OUT"
fi

echo ""
echo "| Section | AE | AE/Mpx | Severity | Status |"
echo "|---------|-----|--------|----------|--------|"
echo -e "$RESULTS"
echo ""
echo "**Result: ${PASS_COUNT} PASS, ${FAIL_COUNT} FAIL, ${SKIP_COUNT} SKIP, ${SUBSTITUTED_COUNT} STRUCTURAL_ONLY, ${UNMEASURED_COUNT} UNMEASURED**"
echo "(Severity is based on AE/Mpx — defect density per megapixel — not raw AE.)"

if [ "${JUDGE_OVERRIDE_COUNT:-0}" -gt 0 ]; then
  echo ""
  echo "⚠ Visual-judge overrides: ${JUDGE_OVERRIDE_COUNT} section(s) passed above the AE cap via a"
  echo "  crop-sha256-bound verdict (${JUDGE_OVERRIDE_NAMES}). Each is a human/LLM PASS on a high-AE"
  echo "  section, not a pixel-level match; re-confirm if the impl crops changed."
fi

# Count saturated rows for the agent's stop-decision routing.
SATURATED_COUNT=$(echo -e "$RESULTS" | grep -c "| saturated | 🌑 |" || true)
if [ "$SATURATED_COUNT" -gt 0 ]; then
  echo ""
  echo "⚠ Saturation: ${SATURATED_COUNT} section(s) at AE/Mpx ≥ ${SATURATION} (gradient dead)."
  echo "  Visual-judge cannot reduce these with class/wrapper tweaks alone — the"
  echo "  underlying components are missing typography / fonts / images / scroll-"
  echo "  trigger wiring that Phase 4 LLM refinement should have supplied."
  echo "  Before another visual-judge pass: revisit each saturated section's"
  echo "  impl/src/components/<Name>.tsx and apply the Phase 4 refinement"
  echo "  checklist (Tailwind class swap, asset reference, font variable,"
  echo "  state/handlers, scroll-trigger animation). Then re-run section-compare."
fi

# ── Auto-save result for Stop gate hook ──
mkdir -p "$DIR/sections"
{
  echo "| Section | AE | AE/Mpx | Severity | Status |"
  echo "|---------|-----|--------|----------|--------|"
  echo -e "$RESULTS"
  echo ""
  # UNMEASURED is a fifth canonical field, not a side-channel line: the evidence
  # state has to live in the denominator every consumer already parses, or each
  # one has to independently learn about it and they desync (which is the bug
  # class this whole change exists to close).
  echo "**Result: ${PASS_COUNT} PASS, ${FAIL_COUNT} FAIL, ${SKIP_COUNT} SKIP, ${SUBSTITUTED_COUNT} STRUCTURAL_ONLY, ${UNMEASURED_COUNT} UNMEASURED**"
  echo "(Severity is based on AE/Mpx — defect density per megapixel — not raw AE.)"
} > "$DIR/sections/result.txt"

# Machine-readable twin of result.txt for the self-healing loop (comparison-
# fix.md H1) — that loop's classifier needs per-section JSON, which this
# script never produced before (the documented loop dead-ended at parse).
python3 - "$DIR/sections/result.txt" "$DIR/sections/result.json" <<'PY'
import json
import re
import sys
from pathlib import Path

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
rows = []
row_re = re.compile(r"^\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<ae>[^|]*?)\s*\|\s*(?P<aempx>[^|]*?)\s*\|\s*(?P<sev>[^|]*?)\s*\|\s*(?P<status>[^|]*?)\s*\|\s*$")
for line in src.read_text(encoding="utf-8").splitlines():
    m = row_re.match(line)
    if not m or m.group("name") in ("Section", "---------"):
        continue
    name = m.group("name")
    if set(name) <= {"-"}:
        continue
    status_raw = m.group("status")
    if "✅" in status_raw or "PASS" in status_raw:
        status = "pass"
    elif "MISSING" in status_raw or "missing" in status_raw:
        status = "missing"
    elif "STRUCTURAL" in status_raw:
        status = "structural-only"
    elif "🌑" in status_raw:
        status = "saturated"
    elif "❌" in status_raw or "FAIL" in status_raw:
        status = "fail"
    elif "UNMEASURED" in status_raw:
        # Distinct from "unknown": the row was deliberately not compared because
        # the reference crop carried no signal. The self-healing loop classifier
        # reads this file, and "unknown" reads as a parse artifact it can ignore.
        status = "unmeasured"
    else:
        status = "unknown"

    def num(s):
        s = s.replace(",", "").strip()
        try:
            return int(float(s))
        except ValueError:
            return None

    diff_crop = dst.parent / "diff" / f"{name}.png"
    rows.append({
        "name": name,
        "ae": num(m.group("ae")),
        "aePerMpx": num(m.group("aempx")),
        "severity": m.group("sev") or None,
        "status": status,
        "statusRaw": status_raw,
        "diffCrop": str(diff_crop) if diff_crop.is_file() else None,
    })
summary_re = re.compile(
    r"\*\*Result: (\d+) PASS, (\d+) FAIL, (\d+) SKIP, (\d+) STRUCTURAL_ONLY"
    r"(?:, (\d+) UNMEASURED)?\*\*"
)
summary = {}
msum = summary_re.search(src.read_text(encoding="utf-8"))
if msum:
    summary = {"pass": int(msum.group(1)), "fail": int(msum.group(2)),
               "skip": int(msum.group(3)), "structuralOnly": int(msum.group(4))}
dst.write_text(json.dumps({
    "schemaVersion": 1,
    "source": "section-compare.sh",
    "summary": summary,
    "sections": rows,
}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

# ── Step 5: Structure diff per section ──
echo ""
echo "▸ Structure comparison..."

python3 -c "
import json

matches = json.loads(open('$DIR/sections/matches.json').read())
diffs = []

for m in matches:
    ref = m.get('ref')
    impl = m.get('impl')
    if not ref or not impl:
        continue
    # Off-canvas synthetic pairs (unmounted overlays) carry only a rect —
    # structure comparison is meaningless for content neither page renders.
    if impl.get('offCanvas'):
        continue

    issues = []

    # Check SVG-as-text mismatch
    if ref.get('hasSvgText') and not impl.get('hasSvgText'):
        issues.append('SVG_TEXT_MISSING: ref has SVG text paths, impl does not')
    if not ref.get('hasSvgText') and impl.get('hasSvgText'):
        issues.append('SVG_TEXT_EXTRA: impl has SVG text paths, ref does not')

    # Check layout system mismatch
    if ref.get('gridCols') and not impl.get('gridCols'):
        issues.append(f'LAYOUT_MISMATCH: ref uses grid ({ref[\"gridCols\"][:40]}), impl does not')
    if ref.get('display') != impl.get('display'):
        issues.append(f'DISPLAY_MISMATCH: ref={ref[\"display\"]}, impl={impl[\"display\"]}')

    # Check height ratio
    rh = ref['rect']['height']
    ih = impl['rect']['height']
    if rh > 0:
        ratio = ih / rh
        if ratio < 0.7 or ratio > 1.3:
            issues.append(f'HEIGHT_MISMATCH: ref={rh}px, impl={ih}px (ratio={ratio:.2f})')

    # Check child count
    rc = ref.get('childCount', 0)
    ic = impl.get('childCount', 0)
    if rc > 0 and abs(rc - ic) > max(2, rc * 0.3):
        issues.append(f'CHILD_COUNT_MISMATCH: ref={rc}, impl={ic}')

    # Classify severity
    rh = ref['rect']['height']
    ih = impl['rect']['height']
    h_ratio = ih / rh if rh > 0 else 1.0
    # When fingerprint similarity is high (>=0.85), the visible content matches
    # closely — child-count differences usually reflect harmless DOM nesting
    # variations (semantic <article> wrappers, extra grid containers) rather
    # than real divergence. Downgrade those to minor.
    score = m.get('score', 0)
    fingerprint_strong = score >= 0.85
    sev = 'ok'
    if any('SVG_TEXT_MISSING' in i or 'LAYOUT_MISMATCH' in i for i in issues):
        sev = 'critical'
    elif h_ratio < 0.3 or h_ratio > 3.0:
        sev = 'critical'
    elif any('HEIGHT_MISMATCH' in i or 'DISPLAY_MISMATCH' in i for i in issues):
        sev = 'major'
    elif any('CHILD_COUNT_MISMATCH' in i for i in issues):
        sev = 'minor' if fingerprint_strong else 'major'
    elif issues:
        sev = 'minor'

    if issues:
        diffs.append({'section': m['name'], 'issues': issues, 'severity': sev, 'score': score})

json.dump(diffs, open('$DIR/sections/structure-diff.json', 'w'), indent=2)

if diffs:
    # Sort by severity: critical first, then major, then minor
    sev_order = {'critical': 0, 'major': 1, 'minor': 2, 'ok': 3}
    diffs.sort(key=lambda d: sev_order.get(d.get('severity', 'ok'), 3))
    print('')
    for d in diffs:
        sev_icon = {'critical': '🔴', 'major': '🟠', 'minor': '🟡'}.get(d['severity'], '⚪')
        print(f'  {sev_icon} [{d[\"severity\"].upper()}] {d[\"section\"]}:')
        for issue in d['issues']:
            print(f'     - {issue}')
    print('')
    crit = sum(1 for d in diffs if d['severity'] == 'critical')
    maj = sum(1 for d in diffs if d['severity'] == 'major')
    minor = sum(1 for d in diffs if d['severity'] == 'minor')
    print(f'  Severity: {crit} critical, {maj} major, {minor} minor')
    if crit > 0:
        print(f'  ⛔ Fix {crit} CRITICAL issue(s) first — these indicate missing/broken sections')
else:
    print('  ✅ No structural mismatches detected')
" 2>&1

# ── Summary ──
echo ""
echo "═══ Section Compare Complete ═══"
echo "  Screenshots: $DIR/sections/{ref,impl,diff}/"
echo "  Matches:     $DIR/sections/matches.json"
echo "  Diffs:       $DIR/sections/structure-diff.json"

# Persist the impl hash so the next ONLY_IF_CHANGED=1 run can short-circuit.
# Written regardless of pass/fail — the hash records "this code was checked",
# not "this code passed". Re-running on the same broken impl is still wasteful.
if [ "$ONLY_IF_CHANGED" = "1" ] && [ -n "${CURRENT_HASH:-}" ]; then
  echo "$CURRENT_HASH" > "$HASH_FILE"
fi

if [ "$FAIL_COUNT" -gt 0 ]; then
  echo ""
  echo "⛔ ${FAIL_COUNT} section(s) FAILED visual comparison."
  echo "For each FAIL, read the diff image:"
  for REF_IMG in "${REF_IMGS[@]}"; do
    NAME=$(basename "$REF_IMG" .png)
    DIFF_IMG="$DIR/sections/diff/${NAME}.png"
    if [ -f "$DIFF_IMG" ]; then
      echo "  Read $DIFF_IMG"
    fi
  done

  # ── Context injection: Root Cause guidance ──
  SKILL_DIR="$(cd "$(dirname "$0")/../../ui-reverse-engineering" && pwd 2>/dev/null || echo "")"
  DIAGNOSIS="$SKILL_DIR/diagnosis.md"
  SKIP_ZONES="$SKILL_DIR/skip-zones.md"

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "▶ DIAGNOSIS GUIDE — pick the matching root cause:"
  echo ""
  echo "  Layout/structure wrong?    → Root Cause A (DOM Mismatch)"
  echo "  Color/font/weight wrong?   → Root Cause B (CSS Cascade Conflict)"
  echo "  Spacing/shadow wrong?      → Root Cause C (Missing Wrapper)"
  echo "  Element type wrong?        → Root Cause D (Wrong Element Type)"
  echo "  Animation doesn't animate? → Root Cause E (Animation)"
  echo ""
  if [ -f "$SKIP_ZONES" ]; then
    echo "▶ ZONE 5 VERIFICATION RULES (what was skipped):"
    awk '/^## ZONE 5:/,/^---/' "$SKIP_ZONES" | sed -n '1,25p'
    echo ""
  fi
  if [ -f "$DIAGNOSIS" ]; then
    echo "▶ ROOT CAUSE DIAGNOSIS COMMANDS:"
    awk '/^## Root Cause/,/^---/' "$DIAGNOSIS" | sed -n '1,50p'
  fi
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  exit 1
fi

# ── All sections passed ──
# The Stop hook (section_gate.py) owns WIP marker cleanup — it removes .ui-re-active
# after calling mark_passed("section-compare") and recording "done" in pipeline-state.json.
# section-compare.sh intentionally does NOT remove the marker here, so the Stop hook
# can still fire once more to record the completed state.
# An unmeasured section is absence of evidence, not evidence of absence. The
# crop-evidence guard deliberately refuses to FAIL the impl for a blank REFERENCE
# crop — but the run must not exit 0 either, or the pipeline records a pass over
# sections nothing ever compared. Blank ref crops concentrate on mid-reveal and
# mid-animation sections, so exiting 0 here certified exactly the sections a
# motion clone is least able to verify.
if [ "$UNMEASURED_COUNT" -gt 0 ]; then
  echo ""
  echo "  ⛔ Section-compare INCONCLUSIVE: ${UNMEASURED_COUNT} section(s) UNMEASURED — the"
  echo "     reference crop carried no signal, so neither side was compared."
  echo "     Fix the CAPTURE, not the impl. A blank ref crop means the screenshot was"
  echo "     taken before the section revealed/animated. Raise the settle window with"
  echo "     WAIT_SCROLL_SETTLE=<seconds> (normally derived from the longest transition"
  echo "     in transition-spec.json; setting it pins the derivation), then re-run."
  echo "     Iterating on impl/src for these rows tunes against nothing."
  exit 1
fi

TOTAL_ROWS=$((PASS_COUNT + FAIL_COUNT + SKIP_COUNT + UNMEASURED_COUNT + GUARD_STRUCTURAL_COUNT))
# Template-mode escape valve (Common cheat pattern): when asset-substitution.json
# declares wholesale wildcard substitution ("*"), the agent has explicitly
# committed to a design-template clone where copy/imagery are intentionally
# different. The 50% NON_STRUCTURAL_PASS rule has no pass path in that mode
# (every row is STRUCTURAL_ONLY by construction) — so bypass it. The
# substitution declaration itself IS the up-front contract; gaming protection
# only matters when substitution is partial (per-section, not wildcard).
if [ "$SUBSTITUTION_ALL" = "1" ]; then
  # Coverage safeguard (Sonnet-Opus comparison finding): even with template
  # mode (wildcard substitution + paid-features evidence), require minimum
  # section-map coverage. Block the "agent gets a 2-section ref capture by
  # luck, declares wildcard substitution, gate passes" path. Threshold:
  # TOTAL_ROWS must be ≥ ceil(N/2) where N = section-map.json section count.
  # D23: same ref-root fallback as the section-map override — this coverage
  # gate is the second consumer of section-map.json in the viewport fan-out.
  SECTION_MAP="$DIR/section-map.json"
  if [ ! -f "$SECTION_MAP" ] && [ -n "${REF_ROOT_DIR:-}" ] && [ -f "${REF_ROOT_DIR}/section-map.json" ]; then
    SECTION_MAP="${REF_ROOT_DIR}/section-map.json"
  fi
  EXPECTED_SECTIONS=$(python3 -c "
import json
try:
    d = json.loads(open('$SECTION_MAP').read())
    if isinstance(d, dict):
        sections = d.get('sections', [])
    elif isinstance(d, list):
        sections = d
    else:
        sections = []
    print(len(sections))
except Exception:
    print(0)
" 2>/dev/null)
  REQUIRED_COVERAGE=$(( (EXPECTED_SECTIONS + 1) / 2 ))
  TOTAL_TEMPLATE=$((PASS_COUNT + FAIL_COUNT + SKIP_COUNT))
  if [ "$EXPECTED_SECTIONS" -ge 4 ] && [ "$TOTAL_TEMPLATE" -lt "$REQUIRED_COVERAGE" ]; then
    echo "  ⛔ Template mode REJECTED — only ${TOTAL_TEMPLATE} sections compared but section-map.json has ${EXPECTED_SECTIONS} (need ≥${REQUIRED_COVERAGE})."
    echo "    Wholesale substitution does not excuse low ref-capture coverage. Re-capture ref or fix the matcher."
    exit 1
  fi
  if [ "$FAIL_COUNT" -eq 0 ] && [ "$SKIP_COUNT" -eq 0 ]; then
    echo "  ✓ Section-compare passed (template mode — wildcard substitution, coverage ${TOTAL_TEMPLATE}/${EXPECTED_SECTIONS}) — Stop hook will record completion on next write."
  elif [ "$SKIP_COUNT" -gt 0 ]; then
    echo "  ⚠  $SKIP_COUNT section(s) missing from impl — implement them and re-run section-compare.sh."
  fi
else
  # Require non-structural pixel evidence on at least ceil(TOTAL_ROWS / 2) rows.
  # This prevents an agent from satisfying section-compare with mostly
  # STRUCTURAL_ONLY (substituted) rows when only a couple of sections actually
  # render real pixel-matching content. STRUCTURAL_ONLY is a deferral, not
  # evidence of visual fidelity.
  REQUIRED_NS_PASS=$(( (TOTAL_ROWS + 1) / 2 ))
  if [ "$FAIL_COUNT" -eq 0 ] && [ "$SKIP_COUNT" -eq 0 ] && [ "$NON_STRUCTURAL_PASS_COUNT" -ge "$REQUIRED_NS_PASS" ]; then
    echo "  ✓ Section-compare passed — Stop hook will record completion on next write."
  elif [ "$FAIL_COUNT" -eq 0 ] && [ "$SKIP_COUNT" -eq 0 ]; then
    echo "  ⚠  Section-compare INCOMPLETE: only ${NON_STRUCTURAL_PASS_COUNT}/${TOTAL_ROWS} rows have non-structural pixel passes (need ≥${REQUIRED_NS_PASS})."
    echo "    STRUCTURAL_ONLY rows skip pixel diff — they are not visual evidence."
    echo "    Implement remaining sections with real content (matching ref fonts/assets) or remove the substitution patterns."
    exit 1
  elif [ "$SKIP_COUNT" -gt 0 ]; then
    echo "  ⚠  $SKIP_COUNT section(s) missing from impl — implement them and re-run section-compare.sh."
  fi
fi
