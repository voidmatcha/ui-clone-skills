#!/usr/bin/env bash
# Atomic, process-owned directory locks for artifact writers.
#
# ui_clone_exclusive_lock_acquire <lock-dir> <token> <label> <session>
# ui_clone_exclusive_lock_release <lock-dir> <token>
#
# The directory creation is the atomic ownership boundary. Metadata makes a
# live owner diagnosable and permits conservative stale-lock recovery. Only
# known metadata files are removed; unknown contents keep the lock fail-closed.

ui_clone_exclusive_lock_acquire() {
  local lock_dir="$1" token="$2" label="$3" session="$4"
  local attempt owner_pid owner_session

  for attempt in 1 2; do
    if mkdir "$lock_dir" 2>/dev/null; then
      printf '%s\n' "$$" > "$lock_dir/pid"
      printf '%s\n' "$token" > "$lock_dir/token"
      printf '%s\n' "$session" > "$lock_dir/session"
      return 0
    fi

    owner_pid=$(cat "$lock_dir/pid" 2>/dev/null || true)
    if [ -z "$owner_pid" ]; then
      # A winner may be between atomic mkdir and its first metadata write.
      sleep 0.1
      owner_pid=$(cat "$lock_dir/pid" 2>/dev/null || true)
    fi
    owner_session=$(cat "$lock_dir/session" 2>/dev/null || echo unknown)
    if [[ "$owner_pid" =~ ^[0-9]+$ ]] && kill -0 "$owner_pid" 2>/dev/null; then
      echo "exclusive-lock: ERROR — already running for $label (pid=$owner_pid session=$owner_session)" >&2
      return 1
    fi

    rm -f "$lock_dir/token" "$lock_dir/pid" "$lock_dir/session"
    if ! rmdir "$lock_dir" 2>/dev/null; then
      echo "exclusive-lock: ERROR — stale lock for $label contains unknown state; remove $lock_dir after confirming no writer is active" >&2
      return 1
    fi
  done

  echo "exclusive-lock: ERROR — could not acquire lock for $label" >&2
  return 1
}

ui_clone_exclusive_lock_release() {
  local lock_dir="$1" token="$2"
  if [ -f "$lock_dir/token" ] \
    && [ "$(cat "$lock_dir/token" 2>/dev/null)" = "$token" ]; then
    rm -f "$lock_dir/token" "$lock_dir/pid" "$lock_dir/session"
    rmdir "$lock_dir" 2>/dev/null || true
  fi
}
