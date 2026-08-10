#!/usr/bin/env bash
# run-required-checks.sh — single-pass dispatcher that reads
# verification-plan.json and runs every requiredCheck whose artifact
# is missing (or stale).
#
# Why this exists:
#   validation run hit the agent's "10 consecutive Bash failures →
#   hard ABORT" circuit breaker because the comprehensive
#   verification plan declared 31 checks, auto-verify.sh only ran a
#   handful (layout-health, batch-compare, gate post-implement), and
#   the agent was left to invoke the remaining 25+ scripts one at a
#   time. Each missing artifact registers as a separate Bash failure
#   in the agent's iteration loop, and the 10-failures threshold is
#   reached before half the checks have produced their output.
#
#   This script collapses that into ONE invocation: read the plan,
#   dispatch each requiredCheck with the correct args based on a
#   known-signatures table, skip scripts whose artifact already
#   passes, except live runtime-text evidence which is always recaptured.
#   The agent runs this once at Step 8 and gets every static + runtime gate
#   artifact materialized in a single Bash call.
#
# Usage:
#   run-required-checks.sh <session> <ref-url> <impl-url> <ref-dir>
#
# Exit:
#   0 — every dispatched check exited 0 (pass) OR was skipped
#   1 — at least one block-severity check exited non-zero (fail) — gate.py will
#       enforce the actual pass/fail verdict via the artifacts
#   2 — setup error (missing plan, unreachable URL, etc.)
#
# What it does NOT do:
#   - Replace gate.py enforcement. The agent still runs
#     `uv run python -m ui_clone.gate <ref-dir> post-implement` to
#     get the canonical verdict.
#   - Iterate / fix failures. This is one shot per call. The agent
#     reads the resulting status JSONs and applies targeted fixes
#     (or invokes visual-debug-iterator).
#   - Run extract-phase scripts (extract-dom, extract-assets, etc).
#     Only post-implement / spec verification checks listed in
#     verification-plan.json's requiredChecks.

set -uo pipefail

# Verification review: per-check timeout via Python wrapper.
# The first attempt used bash timeout-shim.sh's pure-bash fallback, which
# only SIGTERM's the immediate child PID and leaves the spawned tree
# (bash → node → chromium) alive inside the `if cmd | tail | sed; then`
# pipeline. run_with_timeout.py uses subprocess.Popen(start_new_session=True)
# + os.killpg() so the whole process group is terminated on timeout.
# Default 3 min; override via RUN_REQUIRED_CHECK_TIMEOUT_SEC.
: "${RUN_REQUIRED_CHECK_TIMEOUT_SEC:=180}"
_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Bash 5.1 switched here-documents from temporary files to pipes. On macOS,
# sufficiently large interpreter heredocs can block in heredoc_write before
# the child ever starts reading. Scope the official Bash 5.0 compatibility
# mode to dispatched check children only; Bash 3/4 retain their exact command
# path. The project-specific override is intentionally explicit and narrow.
_DISPATCH_CHILD_BASH_COMPAT=""
if [ "${BASH_VERSINFO[0]:-0}" -gt 5 ] \
  || { [ "${BASH_VERSINFO[0]:-0}" -eq 5 ] && [ "${BASH_VERSINFO[1]:-0}" -ge 1 ]; }; then
  _DISPATCH_CHILD_BASH_COMPAT="${UI_CLONE_DISPATCH_BASH_COMPAT:-5.0}"
fi

SESSION="${1:-}"
REF_URL="${2:-}"
IMPL_URL="${3:-}"
REF_DIR="${4:-}"

if [ -z "$SESSION" ] || [ -z "$REF_URL" ] || [ -z "$IMPL_URL" ] || [ -z "$REF_DIR" ]; then
  echo "Usage: run-required-checks.sh <session> <ref-url> <impl-url> <ref-dir>" >&2
  exit 2
fi

# Universality audit MEDIUM: prior version derived deterministic
# session suffixes (`{session}-hyd`, `{session}-rdp`, etc) from the
# caller's session name. Repeated invocations against the same parent
# session reused stale browser state and leaked across loops. Append
# a per-run UUID to the session prefix so each dispatch gets fresh
# agent-browser sessions, and trap-close all derived sessions on exit.
RUN_UUID=$(date +%s%N | tail -c 8)
SESSION="${SESSION}-${RUN_UUID}"

# shellcheck disable=SC2329 # Invoked via trap.
cleanup_browser_sessions() {
  command -v agent-browser >/dev/null 2>&1 || return 0
  # Discover first, then close only live sessions owned by this unique run
  # prefix. Guessing derived names and closing absent sessions can create ghost
  # registrations on some agent-browser versions; prefix discovery also catches
  # nested names created internally (for example hover fallback sessions).
  bash "$_SCRIPT_DIR/cleanup-sessions.sh" "$SESSION" >/dev/null 2>&1 || true
}
trap cleanup_browser_sessions EXIT INT TERM

if [ ! -d "$REF_DIR" ]; then
  echo "ref-dir not found: $REF_DIR" >&2
  exit 2
fi

PLAN="$REF_DIR/verification-plan.json"
if [ ! -f "$PLAN" ]; then
  echo "verification-plan.json missing — run verification-plan.sh first" >&2
  exit 2
fi

REPO_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}}}"

_resolve_python_bin() {
  if [ -n "${PYTHON_BIN:-}" ]; then
    if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
      command -v "$PYTHON_BIN"
      return 0
    fi
    echo "$PYTHON_BIN"
    return 0
  fi
  if [ -n "${VIRTUAL_ENV:-}" ]; then
    if [ -x "$VIRTUAL_ENV/bin/python3" ]; then
      echo "$VIRTUAL_ENV/bin/python3"
      return 0
    fi
    if [ -x "$VIRTUAL_ENV/bin/python" ]; then
      echo "$VIRTUAL_ENV/bin/python"
      return 0
    fi
  fi
  if [ -x "$REPO_ROOT/.venv/bin/python3" ]; then
    echo "$REPO_ROOT/.venv/bin/python3"
    return 0
  fi
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    echo "$REPO_ROOT/.venv/bin/python"
    return 0
  fi
  command -v python3 2>/dev/null || true
}

PYTHON_BIN="$(_resolve_python_bin)"
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "run-required-checks: ERROR — python3 not found; requires Python >=3.11." >&2
  exit 2
fi
PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print(".".join(str(p) for p in sys.version_info[:3]))' 2>/dev/null || echo "unknown")"
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
  echo "run-required-checks: ERROR — requires Python >=3.11; selected interpreter '$PYTHON_BIN' reports $PYTHON_VERSION." >&2
  exit 2
fi
export PYTHON_BIN
PATH="$(dirname "$PYTHON_BIN"):$PATH"
export PATH
_RUN_WITH_TIMEOUT=("$PYTHON_BIN" "${_SCRIPT_DIR}/../lib/run_with_timeout.py")
RUNTIME_TEXT_PROVENANCE="$REF_DIR/runtime-text-sequence.provenance.json"

# (No agent-browser watchdog here: this dispatcher runs every check in a fresh
# `bash "$script_path"` child that does NOT inherit a sourced shell function, and
# each row is already bounded by run_with_timeout.py. The ab-timeout.sh shadow is
# sourced where it actually fires — inside the capture scripts that call
# `agent-browser open` directly, e.g. section-compare.sh / hover-state-compare.sh.)

# Portable file mtime (epoch seconds): BSD/macOS `stat -f %m`, GNU/Linux
# `stat -c %Y`. Used by the B1 per-check staleness fallback + fresh-write seed.
_mtime() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0; }
_RUN_REQUIRED_HELPERS="$REPO_ROOT/scripts/verify/run_required_helpers.py"
_mtime_ns() { "$PYTHON_BIN" "$_RUN_REQUIRED_HELPERS" mtime-ns "$1"; }

# A runtime-text capture is bound to the URLs it actually opened, not merely to
# impl/ref file hashes. Compare the current invocation against every requested,
# opened, and observed receipt written by runtime-text-sequence-check.sh.
_runtime_text_urls_match() {
  "$PYTHON_BIN" "$_RUN_REQUIRED_HELPERS" runtime-text-urls-match "$1" "$REF_URL" "$IMPL_URL"
}

_runtime_text_clear_cache() {
  rm -f "$RUNTIME_TEXT_PROVENANCE"
  local input_sidecar
  input_sidecar=$(PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -m ui_clone.check_inputs sidecar "$REF_DIR" "runtime-text-sequence" 2>/dev/null || echo "")
  [ -n "$input_sidecar" ] && rm -f "$input_sidecar"
}

_runtime_text_write_provenance() {
  PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" "$_RUN_REQUIRED_HELPERS" runtime-text-write-provenance "$1" "$RUNTIME_TEXT_PROVENANCE" "$REF_URL" "$IMPL_URL" "$2"
}

_hover_state_partial_valid() {
  PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" "$_RUN_REQUIRED_HELPERS" hover-state-partial-valid "$REF_DIR" "$1"
}

_required_check_reusable() {
  PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" "$_RUN_REQUIRED_HELPERS" required-check-reusable "$REF_DIR" "$1" "$2"
}

_section_compare_reusable() {
  PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" "$_RUN_REQUIRED_HELPERS" section-compare-reusable "$REF_DIR" "$1"
}

# Determine impl_root by walking up from REF_DIR's parent (typical:
# tmp/ref/<c> → repo/impl) and falling back to the canonical resolver.
IMPL_ROOT=""
RESOLVER="$REPO_ROOT/scripts/extract/find-impl-root.sh"
if [ -f "$RESOLVER" ]; then
  IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
fi
if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  # Fall back to the conventional <ref-dir>/../../../impl path.
  CAND="$(cd "$REF_DIR/../../.." && pwd)/impl"
  if [ -d "$CAND" ]; then
    IMPL_ROOT="$CAND"
  fi
fi

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  echo "run-required-checks: ERROR — could not resolve impl_root for $REF_DIR." >&2
  echo "  Tried: $RESOLVER (returned empty/non-dir)" >&2
  echo "  Tried: $(cd "$REF_DIR/../../.." 2>/dev/null && pwd)/impl (not found)" >&2
  echo "  Without a valid impl_root, dispatchers would compose '/src' = filesystem root." >&2
  echo "  Verify <ref-dir>/../../../impl exists, OR symlink it from your impl location." >&2
  exit 2
fi
IMPL_SRC="${IMPL_ROOT}/src"
IMPL_PUBLIC="${IMPL_ROOT}/public"
# Belt-and-suspenders: also reject paths that resolve to the
# filesystem root or to /src (which only exists on some systems but
# is never an impl source dir for us).
case "$IMPL_ROOT" in
  /|/src|/usr|/opt|/etc|/var|/tmp|/Users|/home)
    echo "run-required-checks: ERROR — impl_root '$IMPL_ROOT' is a filesystem root or system dir." >&2
    exit 2
    ;;
esac
# validation run lesson follow-up: even a valid-looking path could be a
# typo'd directory that lacks impl markers. Require at least one of
# package.json + (src/ or app/ or pages/) before treating IMPL_ROOT
# as a usable impl tree. This catches the case where the resolver
# stumbles into an unrelated directory that happens to exist.
if [ ! -f "$IMPL_ROOT/package.json" ]; then
  echo "run-required-checks: ERROR — impl_root '$IMPL_ROOT' lacks package.json." >&2
  echo "  Not a usable impl tree; refusing to dispatch checks against it." >&2
  exit 2
fi
if [ ! -d "$IMPL_ROOT/src" ] && [ ! -d "$IMPL_ROOT/app" ] && [ ! -d "$IMPL_ROOT/pages" ]; then
  echo "run-required-checks: ERROR — impl_root '$IMPL_ROOT' has no src/, app/, or pages/ directory." >&2
  echo "  Not a usable impl tree; refusing to dispatch checks against it." >&2
  exit 2
fi
_IMPL_BINDING_ERR=""
if ! _IMPL_BINDING_ERR=$(PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" "$_RUN_REQUIRED_HELPERS" persist-impl-binding "$REF_DIR" "$IMPL_ROOT" 2>&1); then
  echo "run-required-checks: ERROR — failed to persist impl binding for ref '$REF_DIR' and impl '$IMPL_ROOT'." >&2
  [ -n "$_IMPL_BINDING_ERR" ] && printf '%s\n' "$_IMPL_BINDING_ERR" | sed 's/^/  /' >&2
  exit 2
fi
unset _IMPL_BINDING_ERR

GREEN="\033[0;32m"; RED="\033[0;31m"; YELLOW="\033[1;33m"; NC="\033[0m"
TOTAL=0; PASS=0; FAIL=0; WARN=0; SKIP=0; STALE=0

# Build the list of (id, script, produces, args-mode) tuples from the plan.
# args-mode is determined by the script basename — kept small and
# explicit so adding a new gate means updating this table.
"$PYTHON_BIN" "$REPO_ROOT/scripts/verify/build_required_dispatch.py" "$PLAN" "$REF_DIR" "$REPO_ROOT" "$IMPL_ROOT" "$IMPL_SRC" "$IMPL_PUBLIC" "$REF_URL" "$IMPL_URL" "$SESSION" > "$REF_DIR/.run-required-checks-dispatch.txt"


SETUP_FAILURE=0
FAILED_IDS=""
mark_failed() {
  local id="$1"
  if ! echo " $FAILED_IDS " | grep -q " $id "; then
    FAILED_IDS="$FAILED_IDS $id"
  fi
}
dep_failed() {
  local deps="$1"
  [ -z "$deps" ] && return 1
  for dep in $deps; do
    if echo " $FAILED_IDS " | grep -q " $dep "; then
      echo "$dep"
      return 0
    fi
  done
  return 1
}
while IFS=$'\t' read -r kind cid script_path args produces severity deps; do
  TOTAL=$((TOTAL + 1))
  severity="${severity:-block}"
  # Dry mode: print the resolved dispatch rows without executing anything.
  # Used by tests to assert row composition (e.g. the VIEWPORTS env on the
  # synthesized section-compare row) without launching browsers.
  if [ "${UI_CLONE_DISPATCH_DRY:-0}" = "1" ]; then
    echo "DRY|$kind|$cid|$args|$produces|$severity"
    continue
  fi
  case "$kind" in
    SKIP)
      echo -e "${YELLOW}~${NC} $cid: $kind"
      SKIP=$((SKIP + 1))
      continue
      ;;
    NOSCRIPT|NOSIG)
      echo -e "${RED}!${NC} $cid: $kind — wire the script into run-required-checks.sh SIGNATURES table"
      SKIP=$((SKIP + 1))
      SETUP_FAILURE=1
      continue
      ;;
  esac
  # MANUAL recipes are advisory scripts that need agent-provided
  # args (e.g. scroll-anim-temporal-diff needs a selector). Log
  # but skip dispatch — these are not SETUP failures.
  if [ "$args" = "MANUAL" ]; then
    echo -e "${YELLOW}~${NC} $cid: MANUAL (agent invokes when applicable)"
    SKIP=$((SKIP + 1))
    continue
  fi
  if [ -n "${deps:-}" ]; then
    failing_dep=$(dep_failed "$deps") || failing_dep=""
    if [ -n "$failing_dep" ]; then
      echo -e "${YELLOW}~${NC} $cid: SKIPPED_DEP (depends on failed: $failing_dep)"
      SKIP=$((SKIP + 1))
      continue
    fi
  fi
  # Skip when artifact already exists with status=pass (idempotency).
  art="$REF_DIR/$produces"
  if [ -f "$art" ]; then
    cur_status=$("$PYTHON_BIN" -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(d.get('status') if isinstance(d, dict) else 'unknown')
except Exception:
    print('parse-error')
" "$art" 2>/dev/null)
    _semantic_cache_note=""
    if [ "$cur_status" = "parse-error" ]; then
      if [ "$cid" = "section-compare" ]; then
        _semantic_cache_note=$(_section_compare_reusable "$art" 2>/dev/null) \
          || _semantic_cache_note=""
      else
        _semantic_cache_note=$(_required_check_reusable "$cid" "$art" 2>/dev/null) \
          || _semantic_cache_note=""
      fi
      case "$_semantic_cache_note" in
        pass$'\t'*) cur_status="pass" ;;
        warn$'\t'*) cur_status="partial" ;;
      esac
    fi
    # Infrastructure/capture errors are never reusable evidence, even for an
    # advisory row. Re-dispatch them on every run until the producer emits a
    # real pass/fail/warn verdict. Ordinary advisory failures remain cacheable.
    if [ "$cur_status" != "error" ] \
      && { [ "$cur_status" = "pass" ] || [ "$cur_status" = "partial" ] \
        || [ "$severity" != "block" ]; }; then
      # Stale-check (B1): prefer the per-check input fingerprint
      # (ui_clone.check_inputs) so a check is stale iff its OWN declared inputs
      # changed — one impl edit no longer re-dispatches every satisfied check.
      # The hash + the sidecar path BOTH come from the shared module (shell-out),
      # so this and the Python gate cannot diverge on file-set, algorithm, or
      # sidecar identity. Before a sidecar exists, fall back to a per-check
      # declared-input mtime sweep (conservative — never treat an
      # un-fingerprinted artifact as fresh); the sidecar is seeded ONLY in the
      # post-dispatch path once a fresh write is proven (not in the skip path).
      stale_seen=0
      if [ "$cid" = "runtime-text-sequence" ]; then
        # This artifact is live browser evidence, not a deterministic
        # file-input result. Always recapture it on a canonical run so a
        # previously passing render cannot certify a later runtime state.
        # Fresh artifacts are still URL- and provenance-validated below.
        _runtime_text_clear_cache
        stale_seen=1
      fi
      if [ "$stale_seen" = "0" ]; then
        cur_ih=$(PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -m ui_clone.check_inputs hash "$IMPL_ROOT" "$REF_DIR" "$cid" 2>/dev/null || echo "UNREGISTERED")
        ihfile=$(PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -m ui_clone.check_inputs sidecar "$REF_DIR" "$cid" 2>/dev/null || echo "")
        if [ "$cur_ih" = "EMPTY" ]; then
          : # registered input-independent check — never stale
        elif [ "$cur_ih" = "UNAVAILABLE" ]; then
          # Every declared side must be present, matched, and readable. Do not
          # let a ref-only partial hash certify a deleted/empty impl input.
          stale_seen=1
        elif [ "$cur_ih" = "UNREGISTERED" ]; then
          # Unregistered (registry-completeness test prevents this for known
          # checks): conservative legacy newest-file sweep over the 8 impl roots.
          if [ -n "$IMPL_ROOT" ]; then
            for sub in src app pages components public lib hooks contexts; do
              d="$IMPL_ROOT/$sub"
              [ -d "$d" ] || continue
              if find "$d" -type f -newer "$art" 2>/dev/null | head -1 | grep -q .; then
                stale_seen=1
                break
              fi
            done
          fi
        elif [ -n "$ihfile" ] && [ -f "$ihfile" ]; then
          if [ "$(cat "$ihfile" 2>/dev/null)" != "$cur_ih" ]; then
            stale_seen=1
          fi
        elif [ "$cid" = "runtime-text-sequence" ]; then
          # Runtime capture migration is intentionally fail-closed. A valid
          # dispatcher provenance receipt without its canonical input hash is
          # incomplete certification, so never fall back to coarse mtimes.
          stale_seen=1
        else
          # Registered, no sidecar yet (migration): newest mtime over the check's
          # DECLARED inputs — the SAME glob set the hash uses, so staleness is
          # already scoped per-check (no src+public-only under-scan, matches the
          # Python gate). Do NOT seed the sidecar here: the +1s mtime tolerance is
          # an inherent same-second ambiguity, transient in the mtime path but it
          # would be FROZEN as a false-fresh verdict if written to a sidecar. The
          # sidecar is seeded ONLY after a real dispatch proves a fresh write.
          nin=$(PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -m ui_clone.check_inputs mtime "$IMPL_ROOT" "$REF_DIR" "$cid" 2>/dev/null || echo "UNREGISTERED")
          art_m=$(_mtime "$art")
          if [[ "$nin" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
            if awk "BEGIN{exit !($nin > $art_m + 1)}"; then
              stale_seen=1
            fi
          else
            # No sidecar plus an unreadable/unavailable declared-input mtime is
            # not fresh migration evidence. Re-dispatch and let the producer
            # create a canonical fingerprint sidecar.
            stale_seen=1
          fi
        fi
      fi
      if [ "$stale_seen" = "1" ]; then
        STALE=$((STALE + 1))
        # Fall through to re-dispatch.
      else
        if [ "$cur_status" = "pass" ]; then
          PASS=$((PASS + 1))
        else
          WARN=$((WARN + 1))
        fi
        continue
      fi
    fi
  fi
  # Dispatch the check. Record the artifact mtime first so the B1 sidecar seed
  # can prove the artifact was FRESHLY written this run (not an old one left in
  # place by a check that exited non-zero).
  art_mtime_ns_before=0
  [ -f "$art" ] && art_mtime_ns_before=$(_mtime_ns "$art")
  if [ "$cid" = "runtime-text-sequence" ]; then
    # Remove prior certification before dispatch so failures, error artifacts,
    # and mismatched receipts cannot retain reusable provenance.
    _runtime_text_clear_cache
  fi
  _dispatch_ih=$(PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -m ui_clone.check_inputs hash "$IMPL_ROOT" "$REF_DIR" "$cid" 2>/dev/null || echo "")
  if [ -n "$_dispatch_ih" ] && [ "$_dispatch_ih" != "UNREGISTERED" ] \
    && [ "$_dispatch_ih" != "EMPTY" ]; then
    _dispatch_ihf=$(PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -m ui_clone.check_inputs sidecar "$REF_DIR" "$cid" 2>/dev/null || echo "")
    if [ -n "$_dispatch_ihf" ] && { [ -e "$_dispatch_ihf" ] || [ -L "$_dispatch_ihf" ]; }; then
      printf '%s' "DISPATCH_INFLIGHT_INVALID" > "$_dispatch_ihf" 2>/dev/null || true
    fi
  fi
  env_vars=""
  positional="$args"
  if [[ "$args" == ENV:* ]]; then
    env_spec="${args#ENV:}"
    env_vars="${env_spec%% -- *}"
    positional="${env_spec##* -- }"
  fi
  echo -e "▶ $cid"
  # Per-row timeout override (Task B / review F3): a multi-pass row (e.g. the
  # frozen section-compare wrapper) carries ROW_TIMEOUT_SEC in its ENV: prefix so
  # it gets its own budget instead of the shared single-check 180s.
  row_timeout="$RUN_REQUIRED_CHECK_TIMEOUT_SEC"
  # D20 (loop-nvti-0): 60fps frame-compare rows drive full scroll/interaction
  # sweeps whose duration scales with page height. Hover comparison can measure
  # up to five targets and each target may require one fresh confirmation, so
  # it needs a larger dedicated budget than the other heavy rows. An explicit
  # ROW_TIMEOUT_SEC in the row ENV still wins below.
  case "$cid" in
    hover-state-compare)
      row_timeout="${RUN_REQUIRED_HOVER_TIMEOUT_SEC:-1800}"
      ;;
    transition-compare|click-state-compare|video-motion-compare)
      row_timeout="${RUN_REQUIRED_HEAVY_TIMEOUT_SEC:-540}"
      ;;
  esac
  for _kv in $env_vars; do
    case "$_kv" in ROW_TIMEOUT_SEC=*) row_timeout="${_kv#ROW_TIMEOUT_SEC=}" ;; esac
  done
  # Pick the interpreter from the row's script extension. Hardcoding `bash`
  # here meant every .py-backed row (unresolved-imports) died on a shell
  # syntax error and could never emit its artifact.
  case "$script_path" in
    *.py) _row_interp="$PYTHON_BIN" ;;
    *) _row_interp="bash" ;;
  esac
  # shellcheck disable=SC2086 # intentional word-split on positional
  if [ -n "$env_vars" ]; then
    # shellcheck disable=SC2086 # intentional word-split on env_vars
    if [ -n "$_DISPATCH_CHILD_BASH_COMPAT" ]; then
      # Put the dispatcher-owned assignment last so a plan row cannot silently
      # defeat the containment; use UI_CLONE_DISPATCH_BASH_COMPAT to override.
      # shellcheck disable=SC2086 # intentional word-split on env_vars/positional
      if "${_RUN_WITH_TIMEOUT[@]}" "$row_timeout" env $env_vars "BASH_COMPAT=$_DISPATCH_CHILD_BASH_COMPAT" "$_row_interp" "$script_path" $positional 2>&1 | tail -3 | sed 's/^/  /'; then
        rc=0
      else
        rc=$?
      fi
    elif "${_RUN_WITH_TIMEOUT[@]}" "$row_timeout" env $env_vars "$_row_interp" "$script_path" $positional 2>&1 | tail -3 | sed 's/^/  /'; then
      rc=0
    else
      rc=$?
    fi
  else
    if [ -n "$_DISPATCH_CHILD_BASH_COMPAT" ]; then
      # shellcheck disable=SC2086 # intentional word-split on positional
      if "${_RUN_WITH_TIMEOUT[@]}" "$row_timeout" env "BASH_COMPAT=$_DISPATCH_CHILD_BASH_COMPAT" "$_row_interp" "$script_path" $positional 2>&1 | tail -3 | sed 's/^/  /'; then
        rc=0
      else
        rc=$?
      fi
    elif "${_RUN_WITH_TIMEOUT[@]}" "$row_timeout" "$_row_interp" "$script_path" $positional 2>&1 | tail -3 | sed 's/^/  /'; then
      rc=0
    else
      rc=$?
    fi
  fi
  if [ "$rc" -eq 124 ]; then
    echo "  → check timed out after ${row_timeout}s (row budget; see RUN_REQUIRED_CHECK_TIMEOUT_SEC / RUN_REQUIRED_HEAVY_TIMEOUT_SEC / RUN_REQUIRED_HOVER_TIMEOUT_SEC / ROW_TIMEOUT_SEC)" >&2
  fi
  _artifact_fresh=0
  if [ -f "$art" ] && [ "$(_mtime_ns "$art")" != "$art_mtime_ns_before" ]; then
    _artifact_fresh=1
  fi
  _hover_partial_note=""
  if [ "$cid" = "hover-state-compare" ] && [ "$_artifact_fresh" = "1" ]; then
    _hover_partial_note=$(_hover_state_partial_valid "$art" 2>/dev/null) || _hover_partial_note=""
  fi
  _seed_ok=0
  if [ "$rc" -eq 0 ] && [ ! -f "$art" ]; then
    # Emit-or-fail invariant: a dispatched check that exits 0 without
    # writing its declared artifact is invisible to every rollup and
    # silently reads as green — the script-side analogue of an agent
    # skip-faking a step. Treat it as a hard check failure.
    echo "  → $cid exited 0 but did not write $produces — emit-or-fail invariant violated" >&2
    FAIL=$((FAIL + 1))
    mark_failed "$cid"
  elif [ "$rc" -eq 0 ]; then
    PASS=$((PASS + 1))
    _seed_ok=1
  elif [ -n "$_hover_partial_note" ]; then
    WARN=$((WARN + 1))
    echo "  → hover-state-compare returned $rc with valid partial evidence; $_hover_partial_note"
    _seed_ok=1
  elif [ "$severity" != "block" ] && [ -f "$art" ]; then
    WARN=$((WARN + 1))
    echo "  → advisory check returned $rc but produced $produces (severity=$severity); canonical gate will report it as a warning."
    _artifact_status=$("$PYTHON_BIN" -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(d.get('status') if isinstance(d, dict) else 'unknown')
except Exception:
    print('parse-error')
" "$art" 2>/dev/null)
    if [ "$_artifact_status" != "error" ]; then
      _seed_ok=1
    fi
  else
    FAIL=$((FAIL + 1))
    mark_failed "$cid"
    if [ -f "$art" ] && [ "$_artifact_fresh" = "1" ]; then
      _artifact_status=$("$PYTHON_BIN" -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(d.get('status') if isinstance(d, dict) else 'unknown')
except Exception:
    print('parse-error')
" "$art" 2>/dev/null)
      [ "$_artifact_status" = "fail" ] && _seed_ok=1
    fi
  fi
  # Seed/update the per-check input-hash sidecar (B1) only when the artifact was
  # freshly written this run and is reusable evidence: pass, cacheable advisory,
  # or a hard status:fail verdict. Keep the invalid pre-dispatch sentinel for
  # status:error and for nonzero producers that leave an old artifact in place.
  if [ "$_seed_ok" = "1" ] && [ -f "$art" ]; then
    _seed_status=$("$PYTHON_BIN" -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(d.get('status') if isinstance(d, dict) else 'unknown')
except Exception:
    print('parse-error')
" "$art" 2>/dev/null)
    [ "$_seed_status" = "error" ] && _seed_ok=0
  fi
  _runtime_provenance_written=0
  if [ "$cid" = "runtime-text-sequence" ]; then
    if [ "$_seed_ok" != "1" ] || [ ! -f "$art" ] \
      || ! _runtime_text_urls_match "$art" \
      || ! _runtime_text_write_provenance "$art" "$art_mtime_ns_before"; then
      _runtime_text_clear_cache
      _seed_ok=0
    else
      # The provenance writer proves nanosecond-level freshness and binds the
      # exact bytes. Preserve that proof when the portable second-resolution
      # mtime happens not to advance.
      _runtime_provenance_written=1
    fi
  fi
  if [ "$_seed_ok" = "1" ] && [ -f "$art" ] \
    && { [ "$_runtime_provenance_written" = "1" ] \
      || [ "$(_mtime_ns "$art")" != "$art_mtime_ns_before" ]; }; then
    _ih=$(PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -m ui_clone.check_inputs hash "$IMPL_ROOT" "$REF_DIR" "$cid" 2>/dev/null || echo "")
    if [ -n "$_ih" ] && [ "$_ih" != "UNREGISTERED" ] \
      && [ "$_ih" != "UNAVAILABLE" ] && [ "$_ih" != "EMPTY" ]; then
      _ihf=$(PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -m ui_clone.check_inputs sidecar "$REF_DIR" "$cid" 2>/dev/null || echo "")
      [ -n "$_ihf" ] && printf '%s' "$_ih" > "$_ihf" 2>/dev/null || true
    fi
  fi
  # Every row owns a unique child session and must set its own viewport. Reap
  # the completed row's live session family before the next one starts. The old
  # approach set a viewport on the bare run prefix after every row, which
  # created a persistent browser that accumulated across the whole dispatcher
  # and eventually poisoned late runtime-text/section captures.
  cleanup_browser_sessions
done < "$REF_DIR/.run-required-checks-dispatch.txt"

rm -f "$REF_DIR/.run-required-checks-dispatch.txt"

echo
echo "═══ run-required-checks summary ═══"
echo "  dispatched: $TOTAL"
echo "  pass:       $PASS"
echo "  warn:       $WARN (advisory non-zero or non-pass artifact; not a hard dispatcher failure)"
echo "  fail:       $FAIL"
echo "  skipped:    $SKIP (unknown signature or missing script — wire into SIGNATURES table)"
echo "  stale:      $STALE (re-dispatched because impl source moved)"
echo
if [ "$SETUP_FAILURE" = "1" ]; then
  echo -e "${RED}DISPATCHER_SETUP_FAILED — at least one required-check has no SIGNATURES entry or its script is missing. Wire it into run-required-checks.sh before re-running.${NC}"
  exit 2
fi

if [ "$FAIL" -gt 0 ]; then
  echo -e "${RED}CHECKS_FAILED count=$FAIL — this is NOT a dispatcher break.${NC}"
  echo -e "${RED}Run \`uv run python -m ui_clone.gate $REF_DIR post-implement\` for the canonical verdict and per-check fix commands.${NC}"
  exit 1
fi
echo -e "${GREEN}CHECKS_PASSED count=$PASS dispatched=$TOTAL.${NC}"
exit 0
