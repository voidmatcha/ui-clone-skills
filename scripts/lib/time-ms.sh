#!/usr/bin/env bash
# Sourceable helper for portable millisecond timestamps.

ui_clone_now_ms() {
  python3 - <<'PY'
import time

print(time.monotonic_ns() // 1_000_000)
PY
}
