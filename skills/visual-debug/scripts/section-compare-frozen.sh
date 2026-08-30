#!/usr/bin/env bash
# section-compare-frozen.sh — three-pass frozen-ref + impl-path-calib wrapper
# around section-compare.sh, for the REAL-CLONE verify path (Task B / specific regression).
#
# WHY: the default single-pass section-compare captures the ref AND the impl LIVE
# each run, so a framer scroll-scrub section lands on a DIFFERENT sub-frame each
# capture -> run-to-run AE variance (specific regression: a faithful section PASSes one run,
# saturates the next). This wrapper freezes the ref ONCE and captures the impl at
# the SAME forced scroll frame so pixel AE is same-frame and meaningful again,
# while an impl-path calibration measures the ref's OWN same-frame scrub noise as
# the dynamic floor. Strict AE is KEPT (not discarded) — section_dynamic's AE
# ceiling gates same-frame defects (see ui_clone/section_dynamic.py).
#
# Generalized from ref-vs-ref-selfpass.sh::_section_compare_frozen: that meta-check
# runs all three passes with impl-url == ref-url; here PASS 2B uses the REAL impl.
#
# Usage: section-compare-frozen.sh <ref-url> <impl-url> <session> <out-dir>
# Honors: EXCLUDE_DYNAMIC (default 1), VIEW_W/VIEW_H, and every env section-compare
# already reads. PASS 2B's result.txt is the verdict. Single-viewport by design
# (the 3-pass cost is gated to the primary viewport; callers fan out separately).
set -uo pipefail

REF_URL="${1:?ref-url required}"
IMPL_URL="${2:?impl-url required}"
SESSION="${3:?session required}"
OUT_DIR="${4:?out-dir required}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECTION_COMPARE="$SCRIPT_DIR/section-compare.sh"
CLEANUP_SESSIONS="$SCRIPT_DIR/../../../scripts/verify/cleanup-sessions.sh"
if [ ! -f "$SECTION_COMPARE" ]; then
  echo "section-compare-frozen: section-compare.sh not found at $SECTION_COMPARE" >&2
  exit 2
fi

EXCLUDE_DYNAMIC="${EXCLUDE_DYNAMIC:-1}"

# Drop stale per-viewport fan-out dirs so a later consumer does not read an older
# run's divergent matches.json (matches selfpass behavior).
rm -rf "$OUT_DIR/sections/viewports" 2>/dev/null || true
mkdir -p "$OUT_DIR/sections"
RUN_NONCE="${SECTION_FROZEN_RUN_NONCE:-$$}"
SESSION_READABLE="$(
  printf '%s' "$SESSION" \
    | LC_ALL=C tr -c '[:alnum:]_-' '-' \
    | cut -c1-10
)"
[ -n "$SESSION_READABLE" ] || SESSION_READABLE="session"

_internal_session() {  # <phase>
  local phase="$1"
  local digest
  digest="$(
    printf '%s\n%s\n%s\n' "$SESSION" "$RUN_NONCE" "$phase" \
      | shasum -a 256 \
      | awk '{ print substr($1, 1, 12) }'
  )"
  # Keep the full agent-browser name below 64 chars even after the longest
  # viewport + "-sc-impl" suffix added by section-compare.sh.
  printf 'scf-%s-%s-%s\n' "$SESSION_READABLE" "$digest" "$phase"
}

_cleanup_owned_sessions() {  # <prefix> <phase>
  local prefix="$1"
  local phase="$2"
  local cleanup_output
  local cleanup_rc

  if ! command -v agent-browser >/dev/null 2>&1 || [ ! -f "$CLEANUP_SESSIONS" ]; then
    return 0
  fi

  set +e
  cleanup_output="$(bash "$CLEANUP_SESSIONS" "$prefix" 2>&1)"
  cleanup_rc=$?
  set -e
  if [ "$cleanup_rc" -eq 0 ]; then
    return 0
  fi

  echo "section-compare-frozen: cleanup failed for ${phase} session prefix '$prefix' (exit ${cleanup_rc})" >&2
  [ -z "$cleanup_output" ] || printf '%s\n' "$cleanup_output" >&2
  return 1
}

# ── PASS 1: materialize the FROZEN ref baseline (ref-path capture, urls=ref/ref).
# Viewport-aware materialization check: a single-viewport run freezes the ref at
# top-level sections/ref/; a multi-viewport run fans out into
# sections/viewports/<WxH>/sections/ref/. Accept either.
_have_frozen_ref() {
  if [ -s "$OUT_DIR/sections/ref-sections.json" ] \
     && ls "$OUT_DIR/sections/ref/"*.png >/dev/null 2>&1; then
    return 0
  fi
  ls "$OUT_DIR/sections/viewports/"*/sections/ref/*.png >/dev/null 2>&1
}
_pass1_last_log=""
for _pass1_attempt in 1 2; do
  # Every attempt gets a never-reused session family. Pre-closing a guessed
  # session name is unsafe: some agent-browser versions create a registration
  # while handling `close`, which can turn cleanup of a nonexistent session
  # into the very ghost session this retry is meant to avoid.
  _pass1_session="$(_internal_session "base-a${_pass1_attempt}")"
  _pass1_last_log="$OUT_DIR/sections/section-compare-frozen-pass1-attempt${_pass1_attempt}.log"
  set +e
  EXCLUDE_DYNAMIC="$EXCLUDE_DYNAMIC" RECATCH_REF=1 SECTION_SKIP_IMPL_RESIZE=1 \
    bash "$SECTION_COMPARE" "$REF_URL" "$REF_URL" "$_pass1_session" "$OUT_DIR" \
    >"$_pass1_last_log" 2>&1
  _pass1_rc=$?
  set -e
  if ! _cleanup_owned_sessions "$_pass1_session" "pass 1 attempt ${_pass1_attempt}"; then
    if [ "$_pass1_rc" -ne 0 ]; then
      exit "$_pass1_rc"
    fi
    exit 2
  fi
  if _have_frozen_ref; then
    break
  fi
  echo "section-compare-frozen: pass 1 attempt ${_pass1_attempt} did not materialize a frozen ref baseline; see $_pass1_last_log" >&2
done
if ! _have_frozen_ref; then
  echo "section-compare-frozen: frozen ref baseline not materialized (pass 1); last log: $_pass1_last_log" >&2
  exit 2
fi

# PASS 1 compares ref/ref, but the two sides travel different capture paths.
# Promote the impl-path side of that self-compare to the frozen baseline so the
# later real impl is measured against reference pixels and section metadata
# captured through the same path. This also recovers a viewport where the
# direct ref enumerator returned an empty list while the self-compare's impl
# path captured the reference successfully.
_promote_impl_path_ref() {  # <sections-dir>
  local section_dir="$1"
  local impl_dir="$section_dir/impl"
  local ref_dir="$section_dir/ref"
  local promoted_sections="$section_dir/ref-sections.promoted.json"
  local capture_matches="$section_dir/frozen-capture-matches.json"
  if [ ! -s "$section_dir/impl-sections.json" ] \
     || ! ls "$impl_dir/"*.png >/dev/null 2>&1; then
    return 1
  fi
  rm -rf "$ref_dir"
  mkdir -p "$ref_dir"
  cp "$impl_dir/"*.png "$ref_dir/"
  if [ ! -s "$capture_matches" ]; then
    capture_matches="$section_dir/matches.json"
  fi
  if [ -s "$capture_matches" ] \
     && PYTHONPATH="$SCRIPT_DIR/../../..${PYTHONPATH:+:$PYTHONPATH}" \
       python3 -m ui_clone.section_compare_sections promote-impl-path \
       "$capture_matches" "$promoted_sections"; then
    mv "$promoted_sections" "$section_dir/ref-sections.json"
  else
    rm -f "$promoted_sections"
    cp "$section_dir/impl-sections.json" "$section_dir/ref-sections.json"
  fi
  cp "$section_dir/ref-sections.json" "$section_dir/ref-runtime-sections.json"
  if [ -s "$section_dir/impl-scroll-positions.json" ]; then
    cp "$section_dir/impl-scroll-positions.json" \
      "$section_dir/ref-scroll-positions.json"
  fi
  if [ -s "$section_dir/impl-semantic-candidates.json" ]; then
    cp "$section_dir/impl-semantic-candidates.json" \
      "$section_dir/ref-semantic-candidates.json"
  fi
}
_promoted_ref=0
if ls -d "$OUT_DIR/sections/viewports/"*/ >/dev/null 2>&1; then
  for _vp in "$OUT_DIR/sections/viewports/"*/; do
    if _promote_impl_path_ref "${_vp}sections"; then
      _promoted_ref=1
    fi
  done
elif _promote_impl_path_ref "$OUT_DIR/sections"; then
  _promoted_ref=1
fi
if [ "$_promoted_ref" = "1" ]; then
  echo "section-compare-frozen: promoted pass-1 impl-path self-capture to frozen reference baseline"
fi

# ── PASS 2A: impl-path calibration (urls=ref/ref). Capture the REF a second time
# through the IMPL path at the frozen forced positions; snapshot those crops as
# ref-calib = the ref's OWN same-frame scrub noise floor. SECTION_SKIP_IMPL_RESIZE
# keeps native box dims so the layout-box self-variance is real.
_calib_session="$(_internal_session cal)"
set +e
EXCLUDE_DYNAMIC="$EXCLUDE_DYNAMIC" RECATCH_REF=0 SECTION_SKIP_IMPL_RESIZE=1 \
  bash "$SECTION_COMPARE" "$REF_URL" "$REF_URL" "$_calib_session" "$OUT_DIR" \
  >/dev/null 2>&1
_calib_rc=$?
set -e
if ! _cleanup_owned_sessions "$_calib_session" "pass 2a"; then
  if [ "$_calib_rc" -ne 0 ]; then
    exit "$_calib_rc"
  fi
  exit 2
fi
# Snapshot the pass-2A impl(=ref) crops as ref-calib. Viewport-aware: a multi-
# viewport run fans out into sections/viewports/<WxH>/sections/{impl,ref-calib}/
# — each viewport gets its OWN impl-path noise floor (closes the known
# multi-viewport copy-calib gap); a single-viewport run uses sections/{impl,ref-calib}/.
_calib_copied=0
_calib_one() {  # <impl-dir> <calib-dir>
  if ls "$1/"*.png >/dev/null 2>&1; then
    rm -rf "$2"; mkdir -p "$2"
    cp "$1/"*.png "$2/" 2>/dev/null || true
    _calib_copied=1
  fi
}
if ls -d "$OUT_DIR/sections/viewports/"*/ >/dev/null 2>&1; then
  for _vp in "$OUT_DIR/sections/viewports/"*/; do
    _calib_one "${_vp}sections/impl" "${_vp}sections/ref-calib"
  done
else
  _calib_one "$OUT_DIR/sections/impl" "$OUT_DIR/sections/ref-calib"
fi
if [ "$_calib_copied" != "1" ]; then
  # F4 (review): do NOT silently revert to single-pass strict AE — surface it.
  echo "section-compare-frozen: impl-path calib frames not materialized (pass 2a) \
— dynamic classification will be absent; NOT a faithful single-pass result" >&2
  printf 'ref-calib not produced at pass 2a (%s)\n' "$(date -u +%FT%TZ 2>/dev/null || echo unknown)" \
    > "$OUT_DIR/sections/ref-calib-missing.txt"
  exit 2
fi

# ── PASS 2B: measurement (urls=ref/REAL-IMPL). Reuse the frozen ref + ref-calib;
# capture the REAL clone at the SAME forced scroll frame. THIS result.txt is the
# verdict. section-compare classifies scroll-scrub sections dynamic and applies
# the same-frame AE ceiling + dssim floor; static sections keep strict AE.
_measurement_session="$(_internal_session run)"
set +e
EXCLUDE_DYNAMIC="$EXCLUDE_DYNAMIC" RECATCH_REF=0 \
  bash "$SECTION_COMPARE" "$REF_URL" "$IMPL_URL" "$_measurement_session" "$OUT_DIR"
_measurement_rc=$?
set -e
if ! _cleanup_owned_sessions "$_measurement_session" "pass 2b"; then
  if [ "$_measurement_rc" -ne 0 ]; then
    exit "$_measurement_rc"
  fi
  exit 2
fi
exit "$_measurement_rc"
