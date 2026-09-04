#!/usr/bin/env python3
"""Reject successful agent-browser eval envelopes that lost their page target."""

from __future__ import annotations

import json
import sys
from urllib.parse import urlparse


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # The capture parser owns malformed-payload diagnostics.

    if not isinstance(payload, dict):
        return 0  # Unit-test shims and legacy wrappers emit the result bare.

    if payload.get("success") is False:
        # An explicit failure envelope carries no page evidence at all. Bare
        # shim payloads have no "success" key, so they stay unaffected.
        print(
            f"agent-browser eval reported failure (error={payload.get('error')!r})",
            file=sys.stderr,
        )
        return 1

    if not isinstance(payload.get("data"), dict):
        return 0  # Unit-test shims and legacy wrappers emit the result bare.

    origin = payload["data"].get("origin")
    parsed = urlparse(origin) if isinstance(origin, str) else None
    if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        print(
            f"agent-browser eval lost the page target (origin={origin!r})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
