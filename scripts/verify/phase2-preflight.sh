#!/usr/bin/env bash
# phase2-preflight.sh — fail fast on environment traps BEFORE the
# iteration agent burns cycles on broken Vite/Next/Hydration state.
#
# Usage:
#   phase2-preflight.sh <ref-dir> <impl-root> [<impl-url>]
#
#
# What this wrapper does:
#   1. If <impl-url> not provided, try to auto-detect by scanning lsof
#      for vite/next-dev/dev-server processes whose cwd matches
#      <impl-root>.
#   2. Run runtime-env-check.sh with the resolved URL.
#   3. If runtime-env fails (NODE_ENV trap, port-routing mismatch,
#      module-load error, etc.), exit 1 and print actionable next
#      steps. The iteration agent should stop iterating until the env
#      is fixed.
#
# Recommended pipeline integration: invoke after Phase 1 extraction
# completes and the impl dev server has started. Run BEFORE Phase 3
# implementation begins.
#
# Exit 0 on env clean / dev server not yet running, 1 on env trap,
# 2 on setup error.

set -uo pipefail

REF_DIR="${1:?Usage: phase2-preflight.sh <ref-dir> <impl-root> [<impl-url>]}"
IMPL_ROOT="${2:?impl-root required}"
IMPL_URL="${3:-}"

[ -d "$REF_DIR" ]   || { echo "phase2-preflight: ref-dir not found" >&2; exit 2; }
[ -d "$IMPL_ROOT" ] || { echo "phase2-preflight: impl-root not found" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUNTIME_ENV="$ROOT_DIR/skills/visual-debug/scripts/runtime-env-check.sh"

if [ ! -f "$RUNTIME_ENV" ]; then
  echo "phase2-preflight: runtime-env-check.sh not found at $RUNTIME_ENV" >&2
  exit 2
fi

# ── Auto-detect impl-url if not provided ──────────────────────────────
if [ -z "$IMPL_URL" ]; then
  REAL_IMPL=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$IMPL_ROOT" 2>/dev/null || echo "$IMPL_ROOT")
  # Scan listening node processes; pick the one whose cwd matches impl-root
  if command -v lsof >/dev/null 2>&1; then
    while IFS= read -r line; do
      PID=$(echo "$line" | awk '{print $2}')
      PORT=$(echo "$line" | awk '{print $9}' | sed 's/.*:\([0-9]*\).*/\1/')
      [ -z "$PID" ] || [ -z "$PORT" ] && continue
      CWD=$(lsof -p "$PID" -d cwd -Fn 2>/dev/null | awk '/^n/ {print substr($0,2); exit}')
      [ -z "$CWD" ] && continue
      REAL_CWD=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$CWD" 2>/dev/null || echo "$CWD")
      if [ "$REAL_CWD" = "$REAL_IMPL" ]; then
        IMPL_URL="http://localhost:$PORT"
        break
      fi
    done < <(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -i node)
  fi
fi

if [ -z "$IMPL_URL" ]; then
  # Dev server isn't running yet — gate cannot probe; preflight passes
  # trivially with a note. This is normal during Phase 2 (extraction
  # before impl exists). Phase 3 onwards should re-invoke once the dev
  # server is up.
  cat <<JSON > "$REF_DIR/phase2-preflight.json"
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "no impl dev server detected yet — preflight skipped (re-invoke once server is up)",
  "implRoot": "$IMPL_ROOT"
}
JSON
  echo "phase2-preflight: SKIP (no dev server yet)"
  exit 0
fi

echo "phase2-preflight: probing $IMPL_URL"
bash "$RUNTIME_ENV" "$REF_DIR" "$IMPL_ROOT" "$IMPL_URL"
RC=$?

if [ "$RC" -ne 0 ]; then
  cat >&2 <<EOF

❌ phase2-preflight FAILED — env trap detected at $IMPL_URL.
   Do NOT proceed to Phase 3 implementation until this is resolved.
   See $REF_DIR/runtime-env.json for the specific trap and next action.
EOF
fi

exit "$RC"
