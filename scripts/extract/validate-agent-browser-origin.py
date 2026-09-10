#!/usr/bin/env python3
"""Reject agent-browser eval results that are not from the expected origin."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse


def _normalized_origin(raw: object) -> tuple[str, str, int] | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            return None
        port = parsed.port
    except ValueError:
        return None
    return (
        parsed.scheme,
        parsed.hostname.lower(),
        port if port is not None else (443 if parsed.scheme == "https" else 80),
    )


def record_navigation(expected_url: str, session: str, path: Path, raw: str, namespace: str) -> int:
    """Bind a successful open response to this requested URL and browser identity."""
    try:
        response = json.loads(raw)
    except json.JSONDecodeError:
        response = None
    # Legacy CLIs may print text instead of a JSON response. Those receipts
    # authorize only the originally requested origin, never a redirect.
    final_url = expected_url
    if isinstance(response, dict):
        if response.get("success") is False:
            print("cannot record failed navigation", file=sys.stderr)
            return 1
        data = response.get("data")
        if response.get("success") is True and isinstance(data, dict) and "url" in data:
            final_url = data["url"]
    if _normalized_origin(final_url) is None:
        print("navigation returned an invalid final URL", file=sys.stderr)
        return 1
    receipt = {
        "requestedUrl": expected_url,
        "finalUrl": final_url,
        "session": session,
        "namespace": namespace,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    pending: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as output:
            pending = output.name
            json.dump(receipt, output)
            output.write("\n")
        os.replace(pending, path)
    finally:
        if pending is not None:
            Path(pending).unlink(missing_ok=True)
    return 0


def _navigation_origin(
    path: Path | None, expected_url: str, session: str | None, namespace: str
) -> tuple[str, str, int] | None:
    if path is None or not session:
        return None
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(receipt, dict) or (
        receipt.get("requestedUrl") != expected_url
        or receipt.get("session") != session
        or receipt.get("namespace") != namespace
    ):
        return None
    return _normalized_origin(receipt.get("finalUrl"))


def validate_payload(
    payload: object,
    expected_url: str,
    session: str | None = None,
    navigation: Path | None = None,
    namespace: str = "",
) -> int:
    """Validate an eval envelope without reading stdin or mutating process state."""
    expected = _normalized_origin(expected_url)
    if expected is None:
        print(f"invalid expected page URL: {expected_url!r}", file=sys.stderr)
        return 2
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

    data = payload.get("data")
    if isinstance(data, dict):
        origin = data.get("origin")
    else:
        # Unit-test shims and legacy wrappers emit the result bare. When they
        # include the evaluated page URL, hold it to the same origin contract.
        origin = payload.get("url")
        if origin is None:
            return 0

    actual = _normalized_origin(origin)
    if actual is None:
        print(
            f"agent-browser eval lost the page target (origin={origin!r})",
            file=sys.stderr,
        )
        return 1
    if actual != expected and actual != _navigation_origin(
        navigation, expected_url, session, namespace
    ):
        print(
            f"agent-browser eval returned the wrong page origin "
            f"(origin={origin!r}, expected origin={expected_url!r})",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected_url")
    parser.add_argument("--session")
    parser.add_argument("--navigation", type=Path)
    parser.add_argument(
        "--record", action="store_true", help="record the JSON response from agent-browser open"
    )
    args = parser.parse_args()
    expected = _normalized_origin(args.expected_url)
    if expected is None:
        print(f"invalid expected page URL: {args.expected_url!r}", file=sys.stderr)
        return 2
    if args.record:
        if args.navigation is None or not args.session:
            parser.error("--record requires --navigation and --session")
        return record_navigation(
            args.expected_url,
            args.session,
            args.navigation,
            sys.stdin.read(),
            os.environ.get("AGENT_BROWSER_NAMESPACE", ""),
        )

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # The capture parser owns malformed-payload diagnostics.

    return validate_payload(
        payload,
        args.expected_url,
        args.session,
        args.navigation,
        os.environ.get("AGENT_BROWSER_NAMESPACE", ""),
    )


if __name__ == "__main__":
    raise SystemExit(main())
