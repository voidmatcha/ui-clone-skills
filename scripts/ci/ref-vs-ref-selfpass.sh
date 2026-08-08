#!/usr/bin/env bash
# ref-vs-ref-selfpass.sh — the ACHIEVABILITY meta-check, full-pipeline edition
# (tools-batch-11 ITEM 5, expanded in tools-batch-12 ITEM 6).
#
# Runs every achievability-sensitive BLOCK-severity gate's CHECK SCRIPT with the
# LIVE REFERENCE as the impl (impl-url := ref-url) against a frozen ref dir. The
# reference trivially matches itself, so EVERY gate MUST PASS. Any gate that fails
# here has an achievability bug (it consumes an artifact no pipeline step
# produces, or computes an input from the wrong layer) — the exact class
# loop-e2e-12 hit on masked-region-static / state-reveal / hover-fallback /
# section-compare, which six bypass/false-positive adversarial rounds never caught
# because they fed gate inputs directly instead of producing them through the real
# pipeline.
#
# This is the DECISIVE, full-pipeline complement to tests/gates/test_ref_self_pass.py
# (which proves the same invariant at the pure-python verdict layer, in CI). It
# needs a live browser + network + a frozen ref corpus, and the corpus lives
# under the gitignored tmp/, so it is OPT-IN and never runs on CI:
#
#   UI_CLONE_REF_SELFPASS=1 bash scripts/ci/ref-vs-ref-selfpass.sh <ref-dir> <ref-url> [session]
#
# Run a SUBSET (incremental validation) with a space-separated allow-list:
#   UI_CLONE_SELFPASS_ONLY="section-compare alignment-parity" UI_CLONE_REF_SELFPASS=1 \
#     bash scripts/ci/ref-vs-ref-selfpass.sh <ref-dir> <ref-url>
#
# tools-batch-12 ITEM 6 closes two holes the batch-11 edition had:
#   (a) a REQUIRED gate that produces NO artifact is now a FAILURE, not a silent
#       SKIP — a gate that consumes/produces nothing through the pipeline is
#       exactly the achievability class this meta-check exists to catch. (The
#       final exit only checked FAIL, so a skipped gate passed without ever being
#       exercised.) A genuine setup error (exit 2: browser/network/missing dep) is
#       still a SKIP, not blamed on the gate.
#   (b) the gate list is now EVERY block-severity LIVE-PROBE gate (a gate whose
#       input is derived by probing the live target), led by section-compare (the
#       highest-risk gate — it got the AE crop-scale tolerance change). The
#       STATIC-SOURCE gates (file-IO over an impl SOURCE tree: text-fidelity,
#       css-mirror, html-paste, image-fidelity, *-grounding, asset-*, impl-scope,
#       monolithic-impl, signature-effects-coverage, …) have no "reference source
#       tree", so "live-ref-as-impl" is undefined for them; their achievability is
#       a different invariant covered by test_ref_self_pass.py at the verdict
#       layer and by the corpus. They are deliberately OUT of this live list (see
#       the STATIC-SOURCE note below), never silently dropped.
#
# Exit: 0 = every probed gate self-passed, 1 = a gate failed ref-vs-ref, 2 = setup.

set -uo pipefail

if [ "${UI_CLONE_REF_SELFPASS:-0}" != "1" ]; then
  echo "ref-vs-ref-selfpass: opt-in — set UI_CLONE_REF_SELFPASS=1 to run" >&2
  exit 0
fi

REF_DIR="${1:?Usage: ref-vs-ref-selfpass.sh <ref-dir> <ref-url> [session]}"
REF_URL="${2:?Usage: ref-vs-ref-selfpass.sh <ref-dir> <ref-url> [session]}"
SESSION="${3:-refself-$$}"
ONLY="${UI_CLONE_SELFPASS_ONLY:-}"

[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }
command -v agent-browser >/dev/null 2>&1 || { echo "agent-browser not found" >&2; exit 2; }

# REF_DIR must be absolute so the check scripts that cd internally still write
# their artifacts into it.
case "$REF_DIR" in
  /*) : ;;
  *) REF_DIR="$(cd "$REF_DIR" && pwd)" ;;
esac

SCRIPTS_DIR="$(cd "$(dirname "$0")/../../skills/visual-debug/scripts" && pwd)"
PASS=0
FAIL=0
SKIP=0
FAILED_GATES=""

# Read an artifact's status field; empty when absent or not JSON (e.g. a .txt/.md
# text artifact, whose verdict is taken from the gate's EXIT CODE instead).
_status() {
  local f="$1"
  [ -f "$f" ] || { echo ""; return; }
  python3 -c "import json,sys
try:
    print((json.load(open(sys.argv[1])) or {}).get('status',''))
except Exception:
    print('')" "$f" 2>/dev/null
}

# Run one gate's check script (impl := ref) and decide its ref-vs-ref verdict.
#   _gate <name> <artifact-relative-to-ref-dir> <required:0|1> -- <cmd...>
# A gate FAILS ref-vs-ref when its EXIT CODE is 1 (the script's own fail verdict)
# OR its JSON artifact status is "fail". A REQUIRED gate that produces NO artifact
# (and did NOT hit a setup error) is a FAILURE — the batch-12 ITEM 6 SKIP-hole
# fix. A setup error (exit 2: browser/network/missing dep, no artifact) is a SKIP,
# not an achievability defect. pass/warn/skip statuses and exit 0 all self-pass.
_gate() {
  local name="$1" artifact="$2" required="$3"; shift 3
  if [ -n "$ONLY" ] && ! printf ' %s ' "$ONLY" | grep -q " $name "; then
    return
  fi
  echo "── ref-vs-ref: $name (required=$required) ──"
  # Per-gate RETRY for live-site flakiness. The reference is a live dynamic site
  # (Lenis smooth-scroll, scroll-scrub/scaffold-scale, timer carousels, lazy
  # media); a single full sweep of ~31 live-capture gates inevitably has a gate
  # whose page load / scroll-reveal / animation phase lands badly ONCE (observed:
  # impl=39 DOM nodes from a starved load; 5 scroll-cards mid-transform at one
  # capture; the rollups that aggregate them then fail too). That is NOT an
  # achievability bug — a transient flake PASSES on a clean retry, while a real
  # achievability bug (gate consumes an artifact no pipeline step produces, reads
  # the wrong layer, mis-declares a tolerance) fails DETERMINISTICALLY on every
  # attempt. So retry fail/setup-error verdicts; keep the first pass. Detection
  # is preserved: nothing that fails all attempts is reported as passing.
  local max_attempts=$(( ${UI_CLONE_SELFPASS_RETRIES:-2} + 1 ))
  local attempt=1 rc st exists outcome reason
  while [ "$attempt" -le "$max_attempts" ]; do
    # Reap prior gates' headless-Chrome sessions before each attempt so
    # accumulated browser instances across the sweep don't starve this load.
    agent-browser close --all >/dev/null 2>&1 || true
    rm -f "$REF_DIR/$artifact" 2>/dev/null || true
    "$@" >/dev/null 2>&1
    rc=$?
    st="$(_status "$REF_DIR/$artifact")"
    exists=0; [ -f "$REF_DIR/$artifact" ] && exists=1
    if [ "$rc" = "2" ] && [ "$exists" = "0" ]; then
      # setup error with no artifact (browser/network/missing dep) — transient,
      # retry; only a persistent setup error becomes a skip.
      outcome="skip"; reason="setup error (exit 2, no $artifact)"
    elif [ "$st" = "fail" ] || { [ "$rc" != "0" ] && [ "$rc" != "2" ]; }; then
      outcome="fail"; reason="exit=$rc status=${st:-none}"
    elif [ "$exists" = "0" ]; then
      if [ "$required" = "1" ]; then
        outcome="fail"; reason="produced no $artifact (required)"
      else
        outcome="skip"; reason="produced no $artifact (optional)"
      fi
    else
      outcome="pass"; reason="exit=$rc status=${st:-n/a}"
    fi
    [ "$outcome" = "pass" ] && break
    if [ "$attempt" -lt "$max_attempts" ]; then
      echo "  ↻ $name attempt $attempt = $outcome ($reason) — retrying (live-site flake guard)"
      attempt=$((attempt+1)); continue
    fi
    break
  done
  case "$outcome" in
    pass) echo "  ✓ $name self-passes (attempt $attempt; $reason)"; PASS=$((PASS+1)) ;;
    skip) echo "  ⚠️  $name skipped after $attempt attempt(s) — $reason"; SKIP=$((SKIP+1)) ;;
    *)    echo "  ❌ $name FAILED ref-vs-ref after $attempt attempt(s) ($reason) — achievability bug"
          FAIL=$((FAIL+1)); FAILED_GATES="$FAILED_GATES $name" ;;
  esac
}

# ── LIVE-PROBE block gates, impl-url := ref-url (and ref-url := ref-url where the
# gate compares two live layers). Out-dir args are pinned to $REF_DIR so the
# gate-consumed artifact lands where the verdict reads it (several check scripts
# default their out-dir to tmp/, not the ref dir). ──

# section-compare runs FIRST: it (re)captures sections/viewports/*/sections/
# matches.json that alignment-parity + alignment-sweep consume, and is the
# highest-risk gate (the AE crop-scale tolerance change).
#
# batch-13 ITEM 1 — FROZEN-REF TWO-PASS. The default RECATCH_REF=1 path captures
# the dynamic ref AND the impl(=ref) LIVE, so a splash/scroll/animation site
# lands on a different frame each capture -> false self-mismatch (undeclared-
# dynamic AE, blank-ref crops, duplicate-id footer cross-pairing). section-compare
# already ships the fix (RECATCH_REF=0 reuses frozen sections/ref/*.png +
# ref-sections.json while capturing the impl fresh). Run it as two passes:
#   pass 1 (RECATCH_REF=1) MATERIALIZES a fresh frozen ref baseline — setup only,
#           its verdict is ignored;
#   pass 2 (RECATCH_REF=0) reuses that baseline, captures impl(=ref) live, and
#           ITS result.txt is the self-pass verdict (and the matches.json that
#           alignment-parity/-sweep then consume).
# EXCLUDE_DYNAMIC=1 on BOTH passes so the dynamic mask is symmetric. If pass 1
# fails to materialize the baseline, return exit 2 (SKIP, a setup error) rather
# than mislabel it an achievability FAIL — pass 2 would otherwise fall back to a
# live ref capture and reintroduce the flake.
# batch-13 ITEM 1 — frozen-ref THREE-pass with an IMPL-PATH calibration.
#
# A scroll-scrub / scroll-entrance section (realfood's 1992-pyramid scale, the
# card-bg scaffold scale) renders a DIFFERENT sub-frame when captured through the
# REF path (pass 1 frozen baseline) vs the IMPL path (the live measurement), even
# at the identical forced scrollY — a deterministic ~13% cross-path scrub
# variance. That variance is exactly what a faithful clone's impl capture also
# carries, so it must be the gate's NOISE FLOOR, not a failure.
#
# The ref-instability calibration therefore captures the reference a SECOND time
# THROUGH THE IMPL PATH (pass 2a) and uses those crops as ref-calib. A minimal /
# ref-path calib is deterministic vs the frozen baseline (selfAE ~0) and would
# mis-classify the section static, then strict-AE-fail the cross-path impl.
# Detection is preserved: static sections still measure selfAE ~0 -> strict AE,
# and a real defect still exceeds the structural-parity floor (section_dynamic).
_section_compare_frozen() {
  # Single-sourced 3-pass frozen-ref + impl-path calibration. The full logic lives
  # in the shared wrapper (skills/visual-debug/scripts/section-compare-frozen.sh),
  # which the real-clone verify path (run-required-checks.sh, comprehensive tier)
  # ALSO dispatches — so the meta-check and the verify path exercise the SAME code.
  # The meta-check runs it ref-vs-ref (impl-url == ref-url). The wrapper's
  # single-viewport path is byte-identical to the prior inline 3-pass: drop stale
  # viewports/, pass 1 freeze (RECATCH_REF=1) with the same materialization guard,
  # pass 2a impl-path calib (RECATCH_REF=0 SECTION_SKIP_IMPL_RESIZE=1) snapshotted
  # to ref-calib/, pass 2b measurement (RECATCH_REF=0). EXCLUDE_DYNAMIC defaults to
  # 1 in the wrapper (matching the inline =1). Exit 2 on a non-materialized pass.
  bash "$SCRIPTS_DIR/section-compare-frozen.sh" "$REF_URL" "$REF_URL" "$SESSION" "$REF_DIR"
}
_gate "section-compare" "sections/result.txt" 1 _section_compare_frozen

_gate "masked-region-static" "masked-region-static.json" 1 \
  bash "$SCRIPTS_DIR/masked-region-static-check.sh" "${SESSION}-mrs" "$REF_URL" "$REF_DIR" "$REF_URL"
_gate "state-reveal" "state-reveal.json" 1 \
  bash "$SCRIPTS_DIR/state-reveal-proof-check.sh" "${SESSION}-sr" "$REF_URL" "$REF_DIR"
_gate "hover-fallback" "hover-fallback.json" 1 \
  bash "$SCRIPTS_DIR/hover-fallback-probe.sh" "${SESSION}-hov" "$REF_URL" "$REF_DIR"

# alignment-parity + alignment-sweep consume section-compare's matches.json.
_gate "alignment-parity" "alignment-parity.json" 1 \
  bash "$SCRIPTS_DIR/alignment-parity-check.sh" "$REF_DIR"
_gate "alignment-sweep" "alignment-sweep.json" 1 \
  bash "$SCRIPTS_DIR/alignment-sweep-check.sh" "${SESSION}-asw" "$REF_URL" "$REF_DIR"

# Single-live-URL probes (impl-url := ref-url).
_gate "blank-viewport" "blank-viewport.json" 1 \
  bash "$SCRIPTS_DIR/blank-viewport-check.sh" "${SESSION}-bv" "$REF_URL" "$REF_DIR"
_gate "hydration-check" "hydration-check.json" 1 \
  bash "$SCRIPTS_DIR/hydration-check.sh" "${SESSION}-hyd" "$REF_URL" "$REF_DIR"
_gate "geometry-sanity" "geometry-sanity.json" 1 \
  bash "$SCRIPTS_DIR/geometry-sanity-check.sh" "${SESSION}-geo" "$REF_URL" "$REF_DIR"
_gate "content-cardinality" "content-cardinality.json" 1 \
  bash "$SCRIPTS_DIR/content-cardinality-check.sh" "${SESSION}-cc" "$REF_URL" "$REF_DIR"
_gate "hidden-children" "hidden-children.json" 1 \
  bash "$SCRIPTS_DIR/hidden-children-check.sh" "${SESSION}-hc" "$REF_URL" "$REF_DIR"
_gate "scroll-end-completion" "scroll-completion.json" 1 \
  bash "$SCRIPTS_DIR/scroll-end-completion-check.sh" "${SESSION}-sec" "$REF_URL" "$REF_DIR"
_gate "runtime-frame-proof" "runtime-frame-proof.json" 1 \
  bash "$SCRIPTS_DIR/runtime-frame-proof-check.sh" "${SESSION}-rfp" "$REF_URL" "$REF_DIR"
_gate "video-play-proof" "video-play-proof.json" 1 \
  bash "$SCRIPTS_DIR/video-play-proof-check.sh" "${SESSION}-vpp" "$REF_URL" "$REF_DIR"
_gate "masked-region-motion" "masked-region-motion.json" 1 \
  bash "$SCRIPTS_DIR/masked-region-motion-proof-check.sh" "${SESSION}-mrm" "$REF_URL" "$REF_DIR"
_gate "transition-fires" "transition-fires.json" 1 \
  bash "$SCRIPTS_DIR/transition-fires-check.sh" "${SESSION}-tf" "$REF_URL" "$REF_DIR"

# Paired live-URL probes (ref-url := impl-url := REF_URL -> self-match).
_gate "scroll-state-machine" "scroll-state-machine.json" 1 \
  bash "$SCRIPTS_DIR/scroll-state-machine-check.sh" "${SESSION}-ssm" "$REF_URL" "$REF_URL" "$REF_DIR"
_gate "svg-provenance" "svg-provenance.json" 1 \
  bash "$SCRIPTS_DIR/svg-provenance-check.sh" "${SESSION}-svp" "$REF_URL" "$REF_URL" "$REF_DIR"
_gate "mobile-viewport-parity" "mobile-viewport-parity.json" 1 \
  bash "$SCRIPTS_DIR/mobile-viewport-parity-check.sh" "${SESSION}-mvp" "$REF_URL" "$REF_URL" "$REF_DIR"
_gate "header-state-runtime" "header-state-runtime.json" 1 \
  bash "$SCRIPTS_DIR/header-state-runtime-check.sh" "${SESSION}-hsr" "$REF_URL" "$REF_URL" "$REF_DIR"
_gate "typography-parity" "typography-parity.json" 1 \
  bash "$SCRIPTS_DIR/typography-parity-check.sh" "${SESSION}-typ" "$REF_URL" "$REF_URL" "$REF_DIR"
_gate "runtime-dom-parity" "runtime-dom-parity.json" 1 \
  bash "$SCRIPTS_DIR/runtime-dom-parity-check.sh" "${SESSION}-rdp" "$REF_URL" "$REF_URL" "$REF_DIR"
_gate "svg-dom-parity" "svg-dom-parity.json" 1 \
  bash "$SCRIPTS_DIR/svg-dom-parity-check.sh" "${SESSION}-sdp" "$REF_URL" "$REF_URL" "$REF_DIR"

# transitions/* text artifacts (out-dir -> $REF_DIR; verdict from the exit code).
_gate "transition-compare" "transitions/result.txt" 1 \
  bash "$SCRIPTS_DIR/transition-compare.sh" "$REF_URL" "$REF_URL" "${SESSION}-tc" "$REF_DIR"
_gate "video-motion-compare" "transitions/video-motion-result.txt" 1 \
  bash "$SCRIPTS_DIR/video-motion-compare.sh" "$REF_URL" "$REF_URL" "${SESSION}-vmc" "$REF_DIR"
_gate "hover-state-compare" "transitions/hover-state-result.txt" 1 \
  bash "$SCRIPTS_DIR/hover-state-compare.sh" "$REF_URL" "$REF_URL" "${SESSION}-hsc" "$REF_DIR"
_gate "hover-tree-diff" "hover-tree-diff.md" 1 \
  bash "$SCRIPTS_DIR/hover-tree-diff.sh" "${SESSION}-htd" "$REF_URL" "$REF_URL" "$REF_DIR"
_gate "live-parity-sweep" "live-parity.json" 1 \
  bash "$SCRIPTS_DIR/live-parity-sweep.sh" "$REF_URL" "$REF_URL" "${SESSION}-lps" "$REF_DIR"

# Corpus/rollup gates (file-IO over the ref dir; optional — they self-pass only
# once their inputs are present, so a missing input is a SKIP, not a gate bug).
_gate "invalidation" "invalidation.json" 0 \
  bash "$SCRIPTS_DIR/invalidation-check.sh" "$REF_DIR"
_gate "runtime-proof" "runtime-proof.json" 0 \
  bash "$SCRIPTS_DIR/runtime-proof-rollup.sh" "$REF_DIR"
_gate "transition-proof" "transition-proof.json" 0 \
  bash "$SCRIPTS_DIR/transition-proof-rollup.sh" "$REF_DIR"

# ── STATIC-SOURCE block gates — DELIBERATELY EXCLUDED from the live meta-check.
# These compare ref artifacts against an impl SOURCE TREE (text-fidelity,
# hero-composite, html-paste, ref-screenshot-asset, proxy-mirror, bundle-paste,
# forced-state-class, body-opacity-unlock, impl-scope, color-token-grounding,
# duration-easing-grounding, signature-effects-coverage, transition-spec-coverage,
# spec-implementation-coverage, image-fidelity, asset-transfer/-utilization/
# -placement, remote-asset-ref, entry-coherence, scaffold-residue/-warn,
# required-media-coverage, css-mirror, monolithic-impl, scroll-engine-parity,
# motion-coverage, bundle-impl-coverage, ref-js-loader, runtime-env, junk-token).
# There is no "reference source tree", so impl-url := ref-url does not exercise
# them — "live-ref-as-impl" is undefined. Their achievability is a DIFFERENT
# invariant (ref artifacts vs a faithful impl source tree), enforced at the
# pure-python verdict layer by tests/gates/test_ref_self_pass.py and by the loop's
# own impl. Listed here so the exclusion is explicit, never a silent drop. ──

echo
echo "════════════════════════════════════════"
echo "  ref-vs-ref self-pass: $PASS passed, $FAIL failed, $SKIP skipped"
[ -n "$FAILED_GATES" ] && echo "  failed gates:$FAILED_GATES"
echo "════════════════════════════════════════"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
