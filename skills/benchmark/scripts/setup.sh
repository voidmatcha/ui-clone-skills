#!/usr/bin/env bash
# setup.sh — idempotent benchmark Step-1 setup. Always wipes the current-SHA
# work dir and re-creates the tmp/ref/realfood symlink. Safe to re-run; safe to
# call when prior runs left stale state.
#
# Why this script exists: SKILL.md's inline-bash Step 1 was getting skipped by
# agents that saw existing tmp/ref/realfood data and assumed Phase 1 was done.
# This script is the single mandatory entry point so the agent doesn't have to
# reason about whether setup is needed — it just runs this. The pre_bash hook
# (ui_clone.hooks.pre_bash) blocks benchmark-related commands when the
# tmp/ref/realfood symlink doesn't match the current HEAD's SHA, forcing a
# call here before further work.
#
# Usage:
#   bash skills/benchmark/scripts/setup.sh
set -euo pipefail

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git not found on PATH" >&2
  exit 2
fi

SHA=$(git rev-parse --short HEAD)
WORK_DIR="benchmark/work/${SHA}"
REF_DIR="${WORK_DIR}/ref"
IMPL_DIR="${WORK_DIR}/impl"
EXPECTED_LINK="$(pwd)/$REF_DIR"

# Wipe prior run at same SHA for a clean baseline.
rm -rf "$WORK_DIR"
mkdir -p "$REF_DIR" "$IMPL_DIR" tmp/ref

# Force-relink. -fn overwrites any stale symlink that's still pointing at a
# previous-SHA's work dir (the inheritance bug observed 3× in rounds A/B/V3).
ln -sfn "$EXPECTED_LINK" tmp/ref/realfood

# Fail-fast verification.
ACTUAL_LINK="$(readlink tmp/ref/realfood)"
if [ "$ACTUAL_LINK" != "$EXPECTED_LINK" ]; then
  echo "ERROR: tmp/ref/realfood points at $ACTUAL_LINK, expected $EXPECTED_LINK" >&2
  exit 2
fi

date +%s > "$REF_DIR/.benchmark-start"
echo "✓ Benchmark setup complete"
echo "  SHA:       $SHA"
echo "  work dir:  $WORK_DIR"
echo "  symlink:   tmp/ref/realfood -> $EXPECTED_LINK"
