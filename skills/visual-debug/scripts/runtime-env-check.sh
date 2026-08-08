#!/usr/bin/env bash
# runtime-env-check.sh — detect environment traps that cause the impl
# to render incorrectly even though build and lint pass.
#
# Usage:
#   runtime-env-check.sh <ref-dir> <impl-root> <impl-url>
#
# Why this exists:
#   Observed port-conflict failure mode: an orphan Vite dev server from a
#   previous iteration kept serving the same port, so verification ran
#   against the wrong impl. dom-mirror / section-compare then reported
#   failures that didn't reflect the current iteration's code.
#
# What this gate catches:
#   1. NODE_ENV=production while Vite dev server is running
#      (detected via $RefreshSig$ undefined OR document title mismatch).
#   2. Port-routing mismatch: the process serving <impl-url> has a cwd
#      that doesn't match <impl-root>. Indicates orphan dev server
#      from a previous loop is intercepting the URL.
#   3. Uncaught JavaScript errors in the impl page (any error visible
#      in console at load time → blocks downstream gates from being
#      meaningful).
#   4. Hydration mismatch warnings in console.
#   5. <body> with empty children (page never rendered).
#
# Writes:
#   <ref-dir>/runtime-env.json
#
# Exit 0 on pass/skip, 1 on any env trap detected, 2 on setup error.

set -uo pipefail

REF_DIR="${1:?Usage: runtime-env-check.sh <ref-dir> <impl-root> <impl-url>}"
IMPL_ROOT="${2:?impl-root required}"
IMPL_URL="${3:?impl-url required}"
WAIT_MS="${RUNTIME_ENV_WAIT_MS:-2500}"

[ -d "$REF_DIR" ]   || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }
[ -d "$IMPL_ROOT" ] || { echo "impl-root not found: $IMPL_ROOT" >&2; exit 2; }

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "runtime-env: agent-browser CLI missing" >&2
  exit 2
fi

OUT="$REF_DIR/runtime-env.json"
SESSION="runtime-env-$$"
SESSION_OPENED="false"
PROBE_RAW=$(mktemp -t rt-env.XXXX.json)
cleanup_runtime_env() {
  rm -f "$PROBE_RAW"
  if [ "$SESSION_OPENED" = "true" ]; then
    agent-browser --session "$SESSION" close >/dev/null 2>&1 || true
  fi
}
trap cleanup_runtime_env EXIT

PORT=$(python3 -c "from urllib.parse import urlparse; import sys; p = urlparse(sys.argv[1]); print(p.port or (443 if p.scheme=='https' else 80))" "$IMPL_URL" 2>/dev/null || echo 0)
SERVING_PID=""
SERVING_CWD=""
PORT_OWNER_MISMATCH="false"
PORT_CHECK_SKIPPED=""
PLATFORM=$(uname -s 2>/dev/null || echo "unknown")
if [[ "$PLATFORM" == "MINGW"* || "$PLATFORM" == "MSYS"* || "$PLATFORM" == "CYGWIN"* ]]; then
  PORT_CHECK_SKIPPED="windows-no-lsof"
elif ! command -v lsof >/dev/null 2>&1; then
  PORT_CHECK_SKIPPED="lsof-not-installed"
elif [ -n "$PORT" ] && [ "$PORT" != "0" ] && [ "$PORT" != "443" ] && [ "$PORT" != "80" ]; then
  # Only check local listening ports (skip remote https/http).
  SERVING_PID=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $2}')
  if [ -n "$SERVING_PID" ]; then
    SERVING_CWD=$(lsof -a -p "$SERVING_PID" -d cwd -Fn 2>/dev/null | awk '/^n/ {print substr($0, 2); exit}')
    if [ -n "$SERVING_CWD" ]; then
      # Resolve canonical paths for both sides before comparing
      REAL_CWD=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$SERVING_CWD" 2>/dev/null || echo "$SERVING_CWD")
      REAL_IMPL=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$IMPL_ROOT" 2>/dev/null || echo "$IMPL_ROOT")
      if [ "$REAL_CWD" != "$REAL_IMPL" ]; then
        PORT_OWNER_MISMATCH="true"
      fi
    fi
  fi
fi

# ── Reachability probe ────────────────────────────────────────────────
# If the URL is not even reachable (no one listening, DNS fail, etc.) the
# downstream agent-browser eval can land on a stale tab from a prior open()
# call and silently produce a misleading "pass". Cheap curl check first.
URL_REACHABLE="true"
URL_REACHABLE_DETAIL=""
if command -v curl >/dev/null 2>&1; then
  if ! curl -o /dev/null -s --max-time 5 --connect-timeout 3 "$IMPL_URL" 2>/dev/null; then
    URL_REACHABLE="false"
    URL_REACHABLE_DETAIL="curl could not establish a connection within 3s"
  fi
fi

if [ "$URL_REACHABLE" != "true" ]; then
  python3 - "$OUT" "$IMPL_URL" "$IMPL_ROOT" "$PORT_OWNER_MISMATCH" "${SERVING_PID:-}" "${SERVING_CWD:-}" "$URL_REACHABLE_DETAIL" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

(
    out_path,
    impl_url,
    impl_root,
    port_mismatch,
    serving_pid,
    serving_cwd,
    url_reachable_detail,
) = sys.argv[1:8]

reasons = [
    f"impl-url {impl_url!r} is not reachable "
    f"({url_reachable_detail or 'connection failed'}). "
    "Start the dev server bound to the expected port before running "
    "the runtime gate. Without a reachable URL the downstream runtime "
    "probe is unreliable and may silently produce a misleading 'pass'."
]
if port_mismatch == "true":
    reasons.insert(
        0,
        f"port-routing mismatch: <impl-url> port is served by PID {serving_pid} "
        f"with cwd {serving_cwd!r}, which does not match impl-root {impl_root!r}. "
        "Likely an orphan dev server from a previous loop.",
    )

payload = {
    "schemaVersion": 1,
    "status": "fail",
    "portRouting": {
        "implUrl": impl_url,
        "implRoot": impl_root,
        "mismatch": port_mismatch == "true",
        "servingPid": serving_pid or None,
        "servingCwd": serving_cwd or None,
    },
    "runtime": {
        "skipped": "impl-url-unreachable",
        "bodyChildren": 0,
        "refreshTrap": False,
        "moduleErr": False,
        "hydrationErr": False,
        "errorCount": 0,
    },
    "reasons": reasons,
    "nextAction": (
        "Start the correct dev server before re-running downstream gates; "
        "dependent runtime checks should skip until runtime-env passes."
    ),
    "rule": (
        "Impl-url must be reachable before browser runtime probes run. "
        "Unreachable URLs can make agent-browser evaluate a stale tab and "
        "produce misleading downstream verdicts."
    ),
}
Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": "fail", "reasons": len(reasons), "out": out_path}, ensure_ascii=False))
PY
  exit 1
fi

# ── Runtime probe ─────────────────────────────────────────────────────
SESSION_OPENED="true"
agent-browser --session "$SESSION" open "$IMPL_URL" >/dev/null 2>&1 || true
sleep "$(( (WAIT_MS + 999) / 1000 ))"  # open --wait is not a supported flag; settle explicitly

agent-browser --session "$SESSION" eval '
(() => {
  const errs = (window.__abErrors || []);
  const consoleText = (Array.isArray(errs) ? errs : []).join(" ").slice(0, 4000);
  const title = document.title || "";
  const bodyChildren = document.body ? document.body.childElementCount : 0;
  const bodyText = document.body ? document.body.innerText.slice(0, 2000) : "";
  // Concatenate body + console for the regex pass below.
  const fullText = bodyText + " " + consoleText;
  const refreshTrap = /\$RefreshSig\$ is not defined|@vitejs\/plugin-react can.t detect preamble|@react-refresh\/runtime/i.test(fullText);
  const moduleErr = /Failed to fetch dynamically imported module|404 \(Not Found\) loading module|ChunkLoadError|Loading chunk \d+ failed|Module not found:|Internal Server Error/i.test(fullText);
  const hydrationErr = /Hydration failed|did not match server-rendered|There was an error while hydrating|Hydration node mismatch|hydration_mismatch|Minified React error #418|Minified React error #419|Minified React error #423|Minified React error #425|Text content does not match server-rendered/i.test(fullText);
  return JSON.stringify({
    title,
    bodyChildren,
    bodyTextHead: bodyText.slice(0, 300),
    refreshTrap,
    moduleErr,
    hydrationErr,
    errors: errs.slice(0, 20),
  });
})()
' > "$PROBE_RAW" 2>/dev/null || true

python3 - "$OUT" "$PROBE_RAW" "$IMPL_URL" "$IMPL_ROOT" "$PORT_OWNER_MISMATCH" "${SERVING_PID:-}" "${SERVING_CWD:-}" "$URL_REACHABLE" "$URL_REACHABLE_DETAIL" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

(out_path, probe_raw, impl_url, impl_root,
 port_mismatch, serving_pid, serving_cwd,
 url_reachable, url_reachable_detail) = sys.argv[1:10]

def read_probe(path: str) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return {"error": "probe-missing"}
    for line in reversed(text.strip().splitlines()):
        s = line.strip()
        # agent-browser wraps JSON.stringify return values in another JSON
        # layer (string-quoted), so the probe output may start with `"`.
        if not (s.startswith("{") or s.startswith('"')):
            continue
        try:
            value = json.loads(s)
            if isinstance(value, str):
                value = json.loads(value)
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    return {"error": "probe-parse-failed"}

probe = read_probe(probe_raw)
reasons: list[str] = []

# ── Port-routing diagnosis ─────────────────────────────────────────
port_diag = {
    "implUrl": impl_url,
    "implRoot": impl_root,
    "mismatch": port_mismatch == "true",
    "servingPid": serving_pid or None,
    "servingCwd": serving_cwd or None,
}
if port_diag["mismatch"]:
    reasons.append(
        f"port-routing mismatch: <impl-url> port is served by PID {serving_pid} "
        f"with cwd {serving_cwd!r}, which does not match impl-root {impl_root!r}. "
        "Likely an orphan dev server from a previous loop. Kill it before "
        "running verification (`lsof -nP -iTCP:<port> -sTCP:LISTEN | "
        "awk 'NR>1 {print $2}' | xargs kill`)."
    )

# ── Reachability check ─────────────────────────────────────────────
# If the URL didn't respond to a 3-second curl, the runtime probe is
# unreliable (agent-browser may eval against a stale tab from a prior
# open() call). Surface this as a hard failure — downstream gates that
# depend on runtime-env will skip rather than run against garbage.
if url_reachable != "true":
    reasons.append(
        f"impl-url {impl_url!r} is not reachable "
        f"({url_reachable_detail or 'connection failed'}). "
        "Start the dev server bound to the expected port before running "
        "the runtime gate. Without a reachable URL the downstream runtime "
        "probe is unreliable and may silently produce a misleading 'pass'."
    )

# ── Runtime probe diagnosis ────────────────────────────────────────
runtime_diag = {
    "title": probe.get("title", ""),
    "bodyChildren": probe.get("bodyChildren", 0),
    "refreshTrap": bool(probe.get("refreshTrap")),
    "moduleErr": bool(probe.get("moduleErr")),
    "hydrationErr": bool(probe.get("hydrationErr")),
    "errorCount": len(probe.get("errors", [])),
}

if probe.get("error"):
    reasons.append(f"runtime probe failed: {probe['error']}")
elif int(probe.get("bodyChildren", 0)) == 0:
    reasons.append(
        "impl page rendered with empty <body> — bundler error or initial "
        "render crash. Check console / network tabs at impl URL."
    )
elif probe.get("refreshTrap"):
    reasons.append(
        "Vite Fast Refresh preamble missing — symptom: `$RefreshSig$ is not "
        "defined`. Almost always caused by NODE_ENV being set to "
        "'production' (or another non-dev value) in the shell that started "
        "the dev server. Fix: `unset NODE_ENV` before `npm run dev`, OR "
        "pin the dev script to `NODE_ENV=development vite`."
    )
elif probe.get("moduleErr"):
    reasons.append(
        "Dynamic import failed / 404 module — check that all impl/src files "
        "the bundle references are present in the running dev tree."
    )
elif probe.get("hydrationErr"):
    reasons.append(
        "Hydration mismatch — SSR and CSR rendered different DOMs. Usually "
        "caused by a `useEffect` that mutates the DOM before hydration "
        "settles, or a server-only branch leaking client state."
    )

status = "pass" if not reasons else "fail"

payload = {
    "schemaVersion": 1,
    "status": status,
    "portRouting": port_diag,
    "runtime": runtime_diag,
    "reasons": reasons,
    "nextAction": (
        "Fix the env trap before re-running downstream gates — section-compare "
        "and runtime-dom-parity will produce misleading results against a "
        "non-rendered page." if reasons else "no env traps detected"
    ),
    "rule": (
        "Impl-url must serve the current iteration's impl-root (no orphan dev "
        "server intercepting the port) AND the rendered page must not show "
        "common env traps (Vite Fast Refresh preamble missing, hydration "
        "mismatch, module-not-found, empty body). These traps make every "
        "downstream visual/runtime gate produce misleading verdicts."
    ),
}

Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": status, "reasons": len(reasons), "out": out_path}, ensure_ascii=False))
sys.exit(0 if status == "pass" else 1)
PY
