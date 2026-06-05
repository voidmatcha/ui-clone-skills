#!/usr/bin/env bash
# capture-click.sh — browser-real click-state snapshots with navigation guard.
#
# Usage:
#   capture-click.sh <url> <session> <ref_dir>
#
# The Python implementation owns subprocess/JSON handling; this wrapper keeps
# the same shell-entrypoint style as capture-states/scroll/hover.sh.

set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "Usage: $0 <url> <session> <ref_dir>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/_capture_click.py" "$@"
