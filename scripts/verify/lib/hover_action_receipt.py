#!/usr/bin/env python3
"""Validate that a recorded selector action reached the intended DOM target."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _unwrap(value: Any) -> Any:
    for _ in range(4):
        if isinstance(value, dict):
            data = value.get("data")
            if isinstance(data, dict) and "result" in data:
                value = data["result"]
                continue
            return value
        if not isinstance(value, str):
            return value
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return value


def validation_error(payload: Any) -> str | None:
    value = _unwrap(payload)
    if not isinstance(value, dict):
        return "receipt is not a JSON object"
    if value.get("found") is not True:
        return "selector target was not found after pointer movement"
    if value.get("hovered") is not True:
        return "target did not match :hover after pointer movement"
    if value.get("pointerReachable") is not True:
        return "target did not win elementFromPoint hit testing"
    match_index = value.get("matchIndex")
    if (
        not isinstance(match_index, int)
        or isinstance(match_index, bool)
        or match_index < 0
    ):
        return "receipt has an invalid matchIndex"
    selector = value.get("selector")
    if not isinstance(selector, str) or not selector:
        return "receipt has an invalid selector"
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: hover_action_receipt.py <receipt.json>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"hover action receipt unreadable: {exc}", file=sys.stderr)
        return 2
    error = validation_error(payload)
    if error is not None:
        print(f"hover action unverified: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
