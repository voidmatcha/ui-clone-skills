#!/usr/bin/env bash
# find-impl-root.sh — shared resolver for impl directory location.
# Single source of truth so gate.py, bundle-impl-coverage-check.sh,
# transition-spec-coverage.sh, verify-loop.sh, measure.py, and future
# impl-source checks all locate the same target — even when the agent
# renamed `impl/` to `<component>-clone/` etc.
#
#
# Usage: find-impl-root.sh <ref-dir>
#   ref-dir   tmp/ref/<component>/ — used to anchor the loop / benchmark root
#
# Output: one line per resolved path (or empty lines for unresolved):
#   <impl_root>
#   <impl_src>
#   <impl_package_json>
#
# Exit 0 if impl found, 2 if not found. Stderr carries diagnostic info.
set -euo pipefail

REF_DIR="${1:-}"
if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: $0 <ref-dir>" >&2
  exit 2
fi
REF_DIR="$(cd "$REF_DIR" && pwd)"

# Avoid version-manager shim startup on every resolver call. The test suite and
# visual gates invoke this helper many times; pyenv/asdf `python3` shims can add
# seconds per invocation on macOS. Prefer an explicit caller-provided Python,
# then an active/repo virtualenv, then fall back to PATH.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ -z "${PYTHON_BIN:-}" ]; then
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python3" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python3"
  elif [ -x "$REPO_ROOT/.venv/bin/python3" ]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python3"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi


exec "$PYTHON_BIN" "$REPO_ROOT/scripts/extract/find_impl_root.py" "$REF_DIR"
