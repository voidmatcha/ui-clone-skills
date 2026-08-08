#!/usr/bin/env bash
# Shared agent-browser viewport helper.
#
# agent-browser's `open` silently ignores unknown flags — `--viewport WxH`
# and `--wait N` are NOT supported. Historically these shipped as dead
# flags, leaving motion probes at the default window size (1280x633) where
# vw-sized references reflow but px-baked impls do not, so trajectory
# compares failed on every vw-heavy site (loop-145 finding). The supported
# sequence is: open, then `set viewport <w> <h>`, then settle.
#
# ab_open_at_viewport <session> <url> <width> <height> [settle_seconds]
#   Opens the URL, applies the viewport, settles, then hard-fails (return 1)
#   unless window.innerWidth matches the requested width (±24px scrollbar
#   tolerance) — converting any future silent-ignore regression into a loud
#   error instead of a bogus motion verdict.

ab_unwrap_eval_number() {
  # agent-browser eval output is double-JSON-encoded; unwrap to a bare int.
  python3 -c '
import json, sys
v = sys.stdin.read().strip()
for _ in range(4):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            break
    elif isinstance(v, dict):
        v = v.get("data") if v.get("data") is not None else v.get("result")
    else:
        break
try:
    print(int(float(v)))
except Exception:
    print("")
' 2>/dev/null
}

ab_open_at_viewport() {
  local session="$1" url="$2" w="$3" h="$4" settle="${5:-2}"
  if ! agent-browser --session "$session" open "$url" >/dev/null 2>&1; then
    echo "viewport.sh: agent-browser open failed for $url (session $session)" >&2
    return 1
  fi
  if ! agent-browser --session "$session" set viewport "$w" "$h" >/dev/null 2>&1; then
    echo "viewport.sh: set viewport ${w}x${h} failed (session $session)" >&2
    return 1
  fi
  sleep "$settle"
  local got
  got=$(agent-browser --session "$session" eval '(() => window.innerWidth)()' 2>/dev/null | ab_unwrap_eval_number)
  if [ -z "$got" ]; then
    echo "viewport.sh: could not read window.innerWidth (session $session) — refusing to probe at unknown viewport" >&2
    return 1
  fi
  if [ "$got" -lt "$((w - 24))" ] || [ "$got" -gt "$w" ]; then
    echo "viewport.sh: viewport assert failed — requested ${w}x${h} but innerWidth=$got (session $session). Motion probes at the wrong viewport produce false verdicts; aborting." >&2
    return 1
  fi
  return 0
}
