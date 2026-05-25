#!/usr/bin/env bash
# bundle-grep.sh — ref-source grep across bundles + html + css subdirectories.
#
# Why this exists:
#   When a post-implement / transition-compare gate fails and the agent's
#   fix iterations stall, the next-action goal-card now points the agent at
#   this helper instead of leaving them to invent a fix. The helper greps
#   the ref's actual source (downloaded bundles, captured HTML/CSS) for the
#   relevant selector or animation hook so the agent can ground its fix in
#   the ref instead of guessing.
#
# Usage:
#   bundle-grep.sh <ref-dir> <pattern>
#
# Output: stdout lists each match as `file:line:snippet`. Empty stdout means
# the pattern is not present in the captured ref artifacts (which is itself
# a signal — maybe the ref imports the pattern from a chunk we missed at
# capture time, or the pattern is wrong).
#
# Exit:
#   0 — search completed (matches may be 0; an empty result is not an error)
#   2 — usage error
set -euo pipefail

REF_DIR="${1:-}"
PATTERN="${2:-}"

if [[ -z "$REF_DIR" || -z "$PATTERN" ]]; then
  echo "usage: bundle-grep.sh <ref-dir> <pattern>" >&2
  exit 2
fi
if [[ ! -d "$REF_DIR" ]]; then
  echo "bundle-grep: ref-dir not found: $REF_DIR" >&2
  exit 2
fi

for sub in bundles html css; do
  d="$REF_DIR/$sub"
  [[ -d "$d" ]] || continue
  grep -nIr \
    --include='*.js' --include='*.mjs' --include='*.cjs' \
    --include='*.html' --include='*.css' --include='*.json' \
    -F "$PATTERN" "$d" 2>/dev/null || true
done
