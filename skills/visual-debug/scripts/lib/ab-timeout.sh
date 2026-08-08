# shellcheck shell=bash
# ab-timeout.sh — sourceable library that shadows `agent-browser` with a
# navigation watchdog so a dead / NXDOMAIN / unreachable URL FAILS FAST instead
# of deadlocking the capture scripts.
#
# Why this exists:
#   `agent-browser open <url>` (and its `goto` / `navigate` aliases) waits on
#   the page load. Against an unreachable host (NXDOMAIN, refused, black-holed)
#   the load never resolves, so the call hangs well past 30s. Capture and
#   verification scripts call `open` in `if ...; then` guards and `$(...)`
#   substitutions; a hang there deadlocks the whole gate and times out
#   ~15 browser-touching tests (e.g. hover-state-compare's pre-flight
#   `agent-browser --session X open <ref-url>` probe).
#
# What it does:
#   Sourcing this file defines a shell function named `agent-browser` that
#   shadows the binary for every call in the sourcing script (and its
#   subshells). The function inspects the SUBCOMMAND:
#     - open | goto | navigate  → run under a wall-clock timeout
#       (UI_CLONE_AB_OPEN_TIMEOUT seconds, default 30) and exit 124 if it
#       overruns, EXACTLY mirroring GNU `timeout`'s behavior.
#     - anything else (eval, set, close, hover, click, wait, …) → pass through
#       byte-identically with NO timeout, NO wrapping.
#   stdout, stderr, and the exit code are preserved exactly so existing
#   `$(...)` captures and `if`/`&&` conditionals keep working unchanged.
#
# Default rationale (30s):
#   Real sites in this environment finish loading in <15s. 30s is a generous
#   ceiling that never trips on a healthy page — it only bounds a true hang.
#   Tests lower it to ~5s (UI_CLONE_AB_OPEN_TIMEOUT=5) so dead-URL probes fail
#   fast without recording 60fps video against a host that will never answer.
#
# Reaping a timed-out session is ALSO bounded:
#   On a timeout the watchdog kill -9's the open's process group, but
#   agent-browser detaches its `--session` browser server (double-fork to PPID
#   1), which escapes that group. The reap closes that session — but
#   `agent-browser close` connects to the now-wedged server and can itself BLOCK
#   ~12s on a dead host. That reap hang, not the open, is what kept the gate
#   well past its deadline. The reap therefore runs under its own short
#   timeout (UI_CLONE_AB_REAP_TIMEOUT, default 5s) so it can never stall.
#
# Usage:
#   . "<dir>/lib/ab-timeout.sh"          # source from a capture/verify script
#   agent-browser --session s open URL   # bounded by UI_CLONE_AB_OPEN_TIMEOUT
#   agent-browser --session s eval JS     # straight through, never bounded
#
# Env:
#   UI_CLONE_AB_OPEN_TIMEOUT  — open/goto/navigate ceiling in seconds (def 30)
#   UI_CLONE_AB_REAP_TIMEOUT  — post-timeout session-close ceiling, sec (def 5)
#
# Portability:
#   The watchdog uses /usr/bin/perl (present on macOS and every Linux this repo
#   targets) rather than GNU `timeout`/`gtimeout`, which are absent on a stock
#   macOS. perl forks the child, arms SIGALRM, kill -9's the child on overrun,
#   and propagates the child's real exit code (or 124 on timeout).

# _ab_subcommand <args...> — echo the subcommand token: the first non-flag arg,
# skipping global flags. Value-taking global flags (--session, --headers,
# --init-script, --state, --extension) consume the next arg; boolean global
# flags (--json, --headed, --auto-connect, etc.) do not. Robust to `--flag=val`
# (self-contained, never consumes the next arg) and an unknown `--flag` (treated
# as boolean — does not consume, so a real subcommand is still found).
_ab_subcommand() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --session|--session-name|--headers|--init-script|--state|--extension|--model|--enable)
        # Value-taking global flag: skip the flag AND its value.
        shift 2 2>/dev/null || return 0
        ;;
      --*=*)
        # Self-contained `--flag=value`: never consumes the next arg.
        shift
        ;;
      --*)
        # Any other flag (boolean global flag or unknown): does not consume a
        # value, so just skip it and keep looking for the subcommand.
        shift
        ;;
      -?*)
        # Short flag (-q/-v/-V/…): boolean; skip so it is not mistaken for the
        # subcommand.
        shift
        ;;
      *)
        # First non-flag token is the subcommand.
        printf '%s' "$1"
        return 0
        ;;
    esac
  done
  return 0
}

# _ab_session <args...> — echo the --session value (for reaping an orphaned
# session after a timeout), or nothing when the default session is used.
_ab_session() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --session) printf '%s' "${2:-}"; return 0 ;;
      --session=*) printf '%s' "${1#--session=}"; return 0 ;;
      *) shift ;;
    esac
  done
  return 0
}

# _ab_perl_timeout <timeout_secs> <bin> [args...] — run `<bin> args...` under a
# portable perl wall-clock timeout. perl forks the child into its own process
# group, arms SIGALRM for <timeout_secs>, and on overrun kill -9's the whole
# child group and exits 124 (GNU timeout's convention). On a clean finish the
# child's real exit code is propagated. stdout/stderr stream through untouched.
# Used for BOTH the navigation open and the post-timeout session reap so neither
# can hang. perl is used instead of GNU timeout/gtimeout (absent on stock macOS).
_ab_perl_timeout() {
  /usr/bin/perl -e '
    my $timeout = shift @ARGV;
    my $pid = fork();
    if (!defined $pid) { die "fork failed: $!"; }
    if ($pid == 0) {
      # Child: own process group so we can kill any browser it spawns.
      setpgrp(0, 0);
      exec { $ARGV[0] } @ARGV or die "exec failed: $!\n";
    }
    # Parent: arm the alarm, reap the child.
    $SIG{ALRM} = sub {
      # kill the whole child process group, then the child directly.
      kill(-9, $pid);
      kill(9, $pid);
      $main::TIMED_OUT = 1;
    };
    alarm($timeout);
    my $reaped = waitpid($pid, 0);
    my $status = $?;
    alarm(0);
    if ($main::TIMED_OUT) { exit(124); }
    if ($reaped == -1)    { exit(1); }
    if ($status & 127)    { exit(128 + ($status & 127)); }
    exit($status >> 8);
  ' "$@"
}

# agent-browser — shadow function. Bounds navigation subcommands, passes
# everything else straight to the real binary via `command agent-browser`.
agent-browser() {
  local _sub
  _sub="$(_ab_subcommand "$@")"

  case "$_sub" in
    open|goto|navigate)
      local _t="${UI_CLONE_AB_OPEN_TIMEOUT:-30}"
      # Resolve the REAL binary path in the shell. `type -P` forces a PATH file
      # lookup, skipping this shadowing function (and any alias/builtin), so it
      # returns the executable path — perl exec's that path directly.
      local _bin
      _bin="$(type -P agent-browser)"
      if [ -z "$_bin" ]; then
        # No real binary on PATH — nothing we can wrap; let the would-be call
        # surface its own "command not found" exactly as before.
        command agent-browser "$@"
        return $?
      fi
      # Bounded navigation (see _ab_perl_timeout): clean finish propagates the
      # child's real exit code; overrun returns 124 after kill -9'ing the group.
      _ab_perl_timeout "$_t" "$_bin" "$@"
      local _rc=$?
      if [ "$_rc" = "124" ]; then
        # The watchdog kill -9'd the CLI front-end's process group, but
        # agent-browser detaches its --session browser server (double-fork to
        # PPID 1), which escapes that group and would leak a Chrome per timeout.
        # Reap it by closing the exact session we were opening (NOT --all, which
        # would tear down sibling captures).
        #
        # CRITICAL: `agent-browser close` connects to the now-wedged session
        # server and can itself BLOCK ~12s on a dead host — that reap hang, not
        # the open, is what kept the gate well past its deadline. Bound the reap
        # with its own short perl timeout (default 5s) so a hung close can never
        # stall the caller. _ab_perl_timeout exec's the resolved binary directly,
        # bypassing this shadow function (equivalent to `command agent-browser`).
        local _reap_t="${UI_CLONE_AB_REAP_TIMEOUT:-5}"
        local _sess
        _sess="$(_ab_session "$@")"
        if [ -n "$_sess" ]; then
          _ab_perl_timeout "$_reap_t" "$_bin" --session "$_sess" close >/dev/null 2>&1 || true
        else
          _ab_perl_timeout "$_reap_t" "$_bin" close >/dev/null 2>&1 || true
        fi
        printf 'ab-timeout: agent-browser %s timed out after %ss — killed + reaped session %s (capture may be incomplete)\n' \
          "$_sub" "$_t" "${_sess:-<default>}" >&2
      fi
      return "$_rc"
      ;;
    close)
      # `agent-browser close` (incl. close --all) connects to the session server
      # to shut it down and can itself BLOCK ~8-12s on a wedged/dead-host session
      # — the same pathology the open watchdog guards, and the reason a gate that
      # opens a dead URL then closes still ran long after the open was bounded.
      # Bound it too (UI_CLONE_AB_CLOSE_TIMEOUT, default 30s — generous, so a
      # healthy sub-second close never trips; tests lower it). If a bounded close
      # is killed before the server exits, the orphan is reaped later by
      # `agent-browser close --all`.
      local _ct="${UI_CLONE_AB_CLOSE_TIMEOUT:-30}"
      local _cbin
      _cbin="$(type -P agent-browser)"
      if [ -z "$_cbin" ]; then
        command agent-browser "$@"
        return $?
      fi
      _ab_perl_timeout "$_ct" "$_cbin" "$@"
      ;;
    *)
      command agent-browser "$@"
      ;;
  esac
}
