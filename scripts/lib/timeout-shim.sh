# shellcheck shell=bash
# timeout-shim.sh — sourceable library that defines a `timeout` shell
# function when the GNU `timeout` cmd is not on PATH (default on macOS).
#
# Closes the failure class observed on the c9b638d benchmark: the agent
# wrapped its own `*-compare.sh` invocations in `timeout 30 ...` defensively;
# macOS shipped without GNU coreutils so `(eval):6: command not found:
# timeout` blocked transition-compare / hover-state-compare / video-motion-
# compare for 30+ min while the agent retried meaninglessly.
#
# Usage:
#   . scripts/lib/timeout-shim.sh        # source from another shell script
#   timeout 30 some-long-command         # works on macOS too
#
# Resolution order:
#   1. real `timeout` on PATH — used as-is
#   2. `gtimeout` (brew coreutils) — wrap via shell function
#   3. pure-bash fallback — background the child, kill after deadline
#
# Note: pure-bash fallback's exit code is approximate (137 on kill, child's
# code otherwise). Sufficient for "did command finish under deadline" gating
# but not for distinguishing all kill signals.

# Already provided by real binary? Leave alone.
if command -v timeout >/dev/null 2>&1; then
  return 0 2>/dev/null || exit 0
fi

# brew coreutils provides gtimeout — proxy
if command -v gtimeout >/dev/null 2>&1; then
  timeout() {
    gtimeout "$@"
  }
  # Export so subshells (e.g. inside bash -c) see it.
  export -f timeout 2>/dev/null || true
  return 0 2>/dev/null || exit 0
fi

# Pure-bash fallback. First arg is seconds (may have s/m/h suffix per GNU).
timeout() {
  local seconds="$1"
  shift
  # Translate GNU-style suffix: 30s → 30, 2m → 120, 1h → 3600
  case "$seconds" in
    *s) seconds="${seconds%s}" ;;
    *m) seconds=$(( ${seconds%m} * 60 )) ;;
    *h) seconds=$(( ${seconds%h} * 3600 )) ;;
  esac
  # Spawn child, watchdog, wait whichever finishes first.
  "$@" &
  local child_pid=$!
  ( sleep "$seconds" && kill -TERM "$child_pid" 2>/dev/null ) &
  local watchdog_pid=$!
  # `wait` returns the child's exit code (or 143/SIGTERM if killed).
  wait "$child_pid" 2>/dev/null
  local rc=$?
  kill -TERM "$watchdog_pid" 2>/dev/null
  wait "$watchdog_pid" 2>/dev/null
  return "$rc"
}
export -f timeout 2>/dev/null || true
