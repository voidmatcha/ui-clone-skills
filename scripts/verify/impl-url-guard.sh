#!/usr/bin/env bash
# impl-url-guard.sh — ensure a local impl URL is served by the expected impl root.
#
# Usage:
#   impl-url-guard.sh <ref-dir> <impl-url> [impl-root]
#
# Writes:
#   <ref-dir>/impl-url-guard.json
#
# Exit:
#   0 pass/skip, 1 mismatch/unreachable local port, 2 setup error.
set -uo pipefail

REF_DIR="${1:?Usage: impl-url-guard.sh <ref-dir> <impl-url> [impl-root]}"
IMPL_URL="${2:?impl-url required}"
IMPL_ROOT_ARG="${3:-}"

[ -d "$REF_DIR" ] || { echo "impl-url-guard: ref-dir not found: $REF_DIR" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}}}"
OUT="$REF_DIR/impl-url-guard.json"

IMPL_ROOT="$IMPL_ROOT_ARG"
if [ -z "$IMPL_ROOT" ] && [ -n "${UI_CLONE_IMPL_ROOT:-}" ]; then
  IMPL_ROOT="$UI_CLONE_IMPL_ROOT"
fi
if [ -z "$IMPL_ROOT" ] && [ -f "$REPO_ROOT/scripts/extract/find-impl-root.sh" ]; then
  IMPL_ROOT="$(bash "$REPO_ROOT/scripts/extract/find-impl-root.sh" "$REF_DIR" 2>/dev/null | head -1 || true)"
fi
if [ -z "$IMPL_ROOT" ] && [ -f "$REF_DIR/.impl-root" ]; then
  IMPL_ROOT="$(head -1 "$REF_DIR/.impl-root" | tr -d '\r' || true)"
fi

python3 - "$OUT" "$REF_DIR" "$IMPL_URL" "$IMPL_ROOT" <<'PY'
from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

out_path, ref_dir, impl_url, impl_root = sys.argv[1:5]
parsed = urlparse(impl_url)
host = (parsed.hostname or "").lower()
port = parsed.port or (443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else 0)
local_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
try:
    local_ip = socket.gethostbyname(host) if host else ""
except OSError:
    local_ip = ""
is_local = host in local_hosts or local_ip.startswith("127.")

payload = {
    "schemaVersion": 1,
    "source": "scripts/verify/impl-url-guard.sh",
    "status": "skip",
    "implUrl": impl_url,
    "implRoot": impl_root or None,
    "portRouting": {
        "local": is_local,
        "host": host or None,
        "port": port or None,
        "servingPid": None,
        "servingCwd": None,
        "mismatch": False,
        "skipped": None,
    },
    "reasons": [],
}

if parsed.scheme not in {"http", "https"}:
    payload["portRouting"]["skipped"] = "non-http-url"
elif not is_local or port in {80, 443, 0}:
    payload["portRouting"]["skipped"] = "non-local-or-default-port"
elif not impl_root:
    payload["status"] = "fail"
    payload["reasons"].append("cannot resolve canonical impl root for local impl-url guard")
else:
    try:
        proc = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        payload["portRouting"]["skipped"] = "lsof-not-installed"
    except subprocess.TimeoutExpired:
        payload["status"] = "fail"
        payload["reasons"].append("lsof timed out while checking local impl port")
    else:
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        if len(lines) < 2:
            payload["status"] = "fail"
            payload["reasons"].append(f"no local process is listening on impl-url port {port}")
        else:
            pid = lines[1].split()[1]
            payload["portRouting"]["servingPid"] = pid
            cwd = ""
            try:
                cwd_proc = subprocess.run(
                    ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for line in cwd_proc.stdout.splitlines():
                    if line.startswith("n"):
                        cwd = line[1:]
                        break
            except (OSError, subprocess.TimeoutExpired):
                cwd = ""
            payload["portRouting"]["servingCwd"] = cwd or None
            if not cwd:
                payload["status"] = "fail"
                payload["reasons"].append(f"could not read cwd for process serving impl-url port {port}")
            else:
                real_cwd = str(Path(cwd).resolve())
                real_impl = str(Path(impl_root).expanduser().resolve())
                mismatch = real_cwd != real_impl
                payload["portRouting"]["mismatch"] = mismatch
                if mismatch:
                    payload["status"] = "fail"
                    payload["reasons"].append(
                        f"port-routing mismatch: {impl_url} is served by PID {pid} cwd {real_cwd!r}, "
                        f"not canonical impl root {real_impl!r}"
                    )
                else:
                    payload["status"] = "pass"

if payload["status"] == "skip" and not payload["reasons"]:
    payload["reasons"].append(str(payload["portRouting"].get("skipped") or "guard not applicable"))
Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps({"status": payload["status"], "out": out_path, "reasons": len(payload["reasons"])}))
raise SystemExit(1 if payload["status"] == "fail" else 0)
PY
