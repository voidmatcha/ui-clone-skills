#!/usr/bin/env bash
# transition-proof-rollup.sh — composite transition-fidelity aggregator.
#
# Usage:
#   transition-proof-rollup.sh <ref-dir>
#
# Roll-up validator that confirms every transition-spec entry has BOTH static
# coverage (impl file references the spec id / selector / type) AND
# runtime evidence (browser actually triggered the transition).
#
# Aggregated source artifacts (read-only):
#   transition-spec-coverage.json     — every spec entry has ≥1 impl file
#   spec-implementation-coverage.json — every covered entry has motion declaration
#   transition-coverage.json          — runtime per-element scroll samples
#   reveal-trigger.json               — IO-driven reveals advance after IO fires
#   scroll-completion.json            — scroll-scrub reveals settle by maxScroll
#   keyframes-diff.json               — @keyframes match between ref and impl
#   transitions/result.txt            — hover/click compare verdicts (if present)
#   transitions/video-motion-result.txt — 60fps SSIM verdict (if present)
#
# Failure modes the rollup catches that individual gates miss:
#   - spec-coverage status=pass with covered<total (silent partial
#     coverage that the static gate didn't itself fail)
#   - spec-implementation withMotion < total (entries matched a file
#     but the file has no motion declaration)
#   - transition-coverage with empty animatedElements (probe ran but
#     found nothing — likely impl URL was wrong or page didn't load)
#   - keyframes-diff with "only-on-ref" or "different-steps" entries
#     present (impl missed an entrance animation)
#   - video-motion-result with non-zero FAIL count
#
# Writes:
#   <ref-dir>/transition-proof.json
#
# Exit 0 on pass/skip, 1 on any transition tier failure, 2 on setup error.

set -uo pipefail

REF_DIR="${1:?Usage: transition-proof-rollup.sh <ref-dir>}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

OUT="$REF_DIR/transition-proof.json"

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPTS_DIR/transition_proof_rollup.py" "$REF_DIR" "$OUT"
