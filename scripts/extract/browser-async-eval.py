#!/usr/bin/env python3
"""Run one in-page promise through short native agent-browser eval commands."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid


def command(session: str, script: str, timeout: float) -> dict:
    result = subprocess.run(
        ['agent-browser', '--session', session, 'eval', '--json', '--stdin'],
        input=script, text=True, capture_output=True, timeout=timeout, check=True,
    )
    response = json.loads(result.stdout)
    if not isinstance(response, dict):
        raise RuntimeError("invalid async browser response envelope")
    return response


def run(session: str, expression: str, timeout_ms: int, *, poll_ms: int = 250) -> dict:
    deadline = time.monotonic() + timeout_ms / 1000
    slot = json.dumps('__uiCloneAsync_' + uuid.uuid4().hex)
    # The eval itself returns immediately; only the stored promise awaits work.
    script = f'''(() => {{
      const key = {slot};
      const record = {{state: "pending"}};
      globalThis[key] = record;
      Promise.resolve().then(() => ({expression.strip().rstrip(';')})).then(
        value => {{ record.state = "done"; record.value = value; }},
        error => {{ record.state = "error"; record.error = String(error); }});
      setTimeout(() => {{ delete globalThis[key]; }}, {timeout_ms + 1000});
      return {{state: "pending"}};
    }})()'''
    poll = f'''(() => {{
      const key = {slot};
      const record = globalThis[key];
      if (!record) return {{state: "missing"}};
      if (record.state !== "pending") delete globalThis[key];
      return record;
    }})()'''
    origin = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('async browser evaluation exceeded total deadline')
        response = command(session, script, min(remaining, 20.0))
        if response.get('success') is not True or not isinstance(response.get('data'), dict):
            raise RuntimeError(f'invalid async browser response: {str(response)[:300]}')
        data = response['data']
        actual_origin = data.get('origin')
        if not isinstance(actual_origin, str) or not actual_origin.startswith(('https://', 'http://')):
            raise RuntimeError(f'async browser evaluation lost the page target (origin): {actual_origin!r}')
        if origin is not None and origin != actual_origin:
            raise RuntimeError('async browser evaluation changed page origin')
        origin = actual_origin
        result = data.get('result')
        if not isinstance(result, dict):
            raise RuntimeError('invalid async browser result')
        state = result.get('state')
        if state == 'done':
            data['result'] = result.get('value')
            return response
        if state != 'pending':
            raise RuntimeError(f'async browser evaluation {state}: {result.get("error", "lost slot")}')
        script = poll
        time.sleep(max(0, min(poll_ms / 1000, deadline - time.monotonic())))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    parser.add_argument('--timeout-ms', type=int, required=True)
    args = parser.parse_args()
    if args.timeout_ms <= 0:
        parser.error('--timeout-ms must be positive')
    try:
        print(json.dumps(run(args.session, sys.stdin.read(), args.timeout_ms)))
    except (RuntimeError, TimeoutError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'async browser evaluation failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
