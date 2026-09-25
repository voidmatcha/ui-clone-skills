#!/usr/bin/env bash
# Retry only the pre-navigation browser transport handshake. Never replay
# navigation, sampling, or recording: those operations change capture evidence.
capture_browser_bootstrap() {
  local label="$1"
  shift
  local attempts="${CAPTURE_BOOTSTRAP_ATTEMPTS:-3}"
  local attempt output status
  if ! [[ "$attempts" =~ ^[1-3]$ ]]; then
    attempts=3
  fi
  for ((attempt=1; attempt<=attempts; attempt++)); do
    if output="$("$@" get url 2>&1)"; then
      printf '%s\n' "$output"
      return 0
    else
      status=$?
    fi
    # ENOENT alone may mean a missing executable/init script. Require a
    # connection diagnostic rather than retrying arbitrary filesystem errors.
    if [ "$attempt" -ge "$attempts" ] || ! printf '%s' "$output" | grep -Eqi \
      'failed to connect|connection refused|browser.*not running|daemon.*(failed to start|not running|connection failed)'; then
      printf '%s\n' "$output" >&2
      return "$status"
    fi
    printf '%s: page bootstrap transport not ready; retrying (%s/%s)\n' "$label" "$attempt" "$attempts" >&2
    sleep 1
  done
}
