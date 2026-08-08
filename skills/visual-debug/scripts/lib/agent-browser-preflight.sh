# shellcheck shell=bash
# agent-browser-preflight.sh — sourceable helpers for agent-browser daemon
# hygiene and readiness, split from the navigation watchdog (ab-timeout.sh).
#
# Why this exists (evidence, not theory):
#   - "Failed to read: Resource temporarily unavailable (os error 35)" is a
#     macOS EAGAIN on agent-browser's CDP read path. It is UNREPRODUCIBLE under
#     synthetic load (a fresh session works 100%; 90 concurrent evals and an
#     8000px full-page screenshot never triggered it) and only surfaces under
#     sustained heavy real load (live sites + 60fps video record + many
#     concurrent agents). It is a daemon-health / load condition, NOT a
#     per-operation defect — so it is handled at the SESSION/BATCH layer here,
#     never by retrying individual (often state-mutating) evals.
#   - agent-browser spawns one Chrome per --session under a temp user-data-dir
#     (agent-browser-chrome-<uuid>). `close` does not always reap the Chrome
#     Helper (Renderer) children, and ~/.agent-browser/*.engine session files
#     are never pruned, so both accumulate across loop iterations. That
#     accumulation IS the reproducible problem.
#
# Two helpers:
#   ab_reap            — DESTRUCTIVE global cleanup: close every session, kill
#                        orphan agent-browser Chrome processes, prune stale
#                        *.engine registrations. Reclaims the accumulation above.
#   ab_health <sess>   — read-only readiness probe (document.readyState). No
#                        side effects, so it is safe to call before a batch and
#                        safe to let its failure drive a session reset.
#
# DESTRUCTIVE-SCOPE WARNING (ab_reap):
#   ab_reap uses `agent-browser close --all` + a process kill by the
#   agent-browser-chrome user-data-dir marker. It cannot target a single loop's
#   sessions (the marker is a per-Chrome uuid, not the --session name), so it
#   tears down EVERY agent-browser session on the machine — including a sibling
#   capture batch running concurrently, and any unrelated browser work. Call it
#   ONLY at a serial boundary (loop-iteration start, before this batch opens its
#   own sessions) — never from inside a capture that may run alongside another.
#
# Env:
#   UI_CLONE_AB_REAP           — "0" disables ab_reap (no-op); default enabled.
#   UI_CLONE_AB_REAP_TIMEOUT   — bound (s) on the `close --all` step (def 8).
#   AGENT_BROWSER_HOME         — session-registration dir (def ~/.agent-browser).

# _ab_prune_engines [home] — remove stale *.engine session registrations under
# `home` (default AGENT_BROWSER_HOME or ~/.agent-browser) and echo the count
# removed. Pure/local (no daemon, no process kill) so it is unit-testable.
_ab_prune_engines() {
  local _home="${1:-${AGENT_BROWSER_HOME:-$HOME/.agent-browser}}"
  local _n=0 _f
  [ -d "$_home" ] || { printf '0'; return 0; }
  # Enumerate via find, not a `*.engine` glob: an unmatched glob is harmless in
  # bash but ABORTS the function under zsh ("no matches found"), and this lib is
  # sometimes sourced interactively. find prints nothing on no match in any shell.
  while IFS= read -r _f; do
    [ -n "$_f" ] || continue
    rm -f "$_f" 2>/dev/null && _n=$((_n + 1))
  done < <(find "$_home" -maxdepth 1 -type f -name '*.engine' 2>/dev/null)
  printf '%s' "$_n"
}

# ab_reap — see header. Honors UI_CLONE_AB_REAP=0 as a no-op so a caller can be
# wired unconditionally yet disabled by env in a concurrent context.
ab_reap() {
  if [ "${UI_CLONE_AB_REAP:-1}" = "0" ]; then
    return 0
  fi
  local timeout="${UI_CLONE_AB_REAP_TIMEOUT:-8}"

  # 1) close every session. `close --all` can itself block on a wedged session
  #    server, so bound it with a background pid + hard kill (no GNU timeout on
  #    stock macOS; keep this dependency-free).
  ( command agent-browser close --all >/dev/null 2>&1 ) &
  local _pid=$!
  local _waited=0
  while kill -0 "$_pid" 2>/dev/null && [ "$_waited" -lt "$timeout" ]; do
    sleep 1
    _waited=$((_waited + 1))
  done
  if kill -0 "$_pid" 2>/dev/null; then
    kill -9 "$_pid" 2>/dev/null || true
  fi
  wait "$_pid" 2>/dev/null || true

  # 2) reap orphan Chrome processes agent-browser spawned. The temp
  #    user-data-dir carries the agent-browser-chrome- marker on every process
  #    of the tree (browser + GPU/network/renderer helpers), so this catches the
  #    children `close` left behind. Safe at a serial batch boundary only.
  pkill -f 'agent-browser-chrome-' 2>/dev/null || true

  # 3) prune stale session registrations (dead after steps 1-2).
  _ab_prune_engines >/dev/null

  return 0
}

# _ab_health_verdict <output> — return 0 iff `output` is a document.readyState
# value (loading|interactive|complete, possibly JSON-quoted), non-zero for empty
# output or an error string. Pure string logic, unit-testable without a browser.
_ab_health_verdict() {
  case "$1" in
    *complete*|*interactive*|*loading*) return 0 ;;
    *) return 1 ;;
  esac
}

# ab_health <session> — return 0 iff the daemon answers a read-only
# document.readyState eval for `session`, non-zero otherwise (2 on empty arg).
# This is a DAEMON-responsiveness probe, not a "my page is still loaded" check:
# agent-browser lazily creates a blank session on eval, so a healthy daemon
# always answers (a blank page is "complete"). The failure mode it DOES catch is
# the one that matters — a wedged/overloaded daemon whose CDP read fails or
# hangs (the os error 35 condition) makes the eval error out or time out. Pure
# read: no scroll/click/animation side effects, so its failure can safely drive
# a session reset without corrupting a capture. Bounded so a hang cannot stall.
ab_health() {
  local _session="$1"
  local _timeout="${UI_CLONE_AB_HEALTH_TIMEOUT:-6}"
  [ -n "$_session" ] || return 2

  local _out=""
  local _tmp
  _tmp="$(mktemp -t ab-health.XXXXXX)"
  ( command agent-browser --session "$_session" \
      eval '(() => document.readyState)()' >"$_tmp" 2>/dev/null ) &
  local _pid=$!
  local _waited=0
  while kill -0 "$_pid" 2>/dev/null && [ "$_waited" -lt "$_timeout" ]; do
    sleep 1
    _waited=$((_waited + 1))
  done
  if kill -0 "$_pid" 2>/dev/null; then
    kill -9 "$_pid" 2>/dev/null || true
    wait "$_pid" 2>/dev/null || true
    rm -f "$_tmp"
    return 1
  fi
  wait "$_pid" 2>/dev/null || true
  _out="$(cat "$_tmp" 2>/dev/null)"
  rm -f "$_tmp"

  _ab_health_verdict "$_out"
}
