#!/usr/bin/env python3
"""Write reveal-trigger artifacts without a Bash heredoc."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NoReturn


def _usage() -> NoReturn:
    raise SystemExit(
        "usage: reveal_trigger_artifact.py "
        "<error|pass|fail> <out> <url> <viewport> [payload]\n"
        "   or: reveal_trigger_artifact.py merge <json-array>...\n"
        "   or: reveal_trigger_artifact.py selectors <transition-spec.json>"
    )


def _write(
    path: str,
    payload: dict[str, object],
    *,
    ensure_ascii: bool,
    trailing_newline: bool,
) -> None:
    rendered = json.dumps(payload, ensure_ascii=ensure_ascii, indent=2)
    if trailing_newline:
        rendered += "\n"
    Path(path).write_text(
        rendered,
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "merge":
        merged: list[object] = []
        for raw in argv[2:]:
            entries = json.loads(raw)
            if not isinstance(entries, list):
                raise ValueError("reveal-trigger batch payload must be a JSON array")
            merged.extend(entries)
        print(json.dumps(merged, ensure_ascii=False))
        return 0

    if len(argv) == 3 and argv[1] == "selectors":
        payload = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("transition-spec must be a JSON object")
        transitions = payload.get("transitions")
        if not isinstance(transitions, list):
            print("[]")
            return 0

        selectors: list[str] = []
        seen: set[str] = set()
        for entry in transitions:
            if not isinstance(entry, dict):
                continue
            animation = entry.get("animation")
            animation_type = ""
            if isinstance(animation, dict):
                animation_type = str(animation.get("type") or "")
            signal = " ".join(
                [
                    str(entry.get("trigger") or ""),
                    str(entry.get("type") or ""),
                    animation_type,
                ]
            ).lower()
            if "intersection" not in signal and "reveal" not in signal:
                continue
            raw_selectors = [entry.get("selector"), entry.get("target")]
            for raw_selector in raw_selectors:
                if not isinstance(raw_selector, str):
                    continue
                selector = raw_selector.strip()
                if not selector or selector in seen:
                    continue
                selectors.append(selector)
                seen.add(selector)
        print(json.dumps(selectors, ensure_ascii=False))
        return 0

    if len(argv) < 5:
        _usage()

    mode, out_path, url, viewport = argv[1:5]
    result: dict[str, object] = {
        "schemaVersion": 1,
        "status": mode,
        "implUrl": url,
        "viewport": viewport,
    }

    if mode == "error":
        if len(argv) != 6:
            _usage()
        candidate_count = int(argv[5])
        result.update(
            {
                "candidateCount": candidate_count,
                "stuckCount": None,
                "stuck": [],
                "error": (
                    "phase-2 reveal sweep produced no output for "
                    f"{candidate_count} candidate(s); measurement incomplete "
                    "(eval-budget timeout) — not a clean pass"
                ),
            }
        )
    elif mode == "pass":
        if len(argv) not in {5, 6}:
            _usage()
        candidate_count = int(argv[5]) if len(argv) == 6 else 0
        result.update({"candidateCount": candidate_count, "stuckCount": 0, "stuck": []})
    elif mode == "fail":
        if len(argv) != 6:
            _usage()
        try:
            entries = json.loads(argv[5])
        except ValueError:
            entries = []
        if not isinstance(entries, list):
            entries = []
        result.update(
            {
                "stuckCount": len(entries),
                "stuck": entries[:30],
                "rule": (
                    "Elements with hidden-init style (opacity:0 or non-identity "
                    "transform) must advance to a visible state after being "
                    "scrolled into view. Stuck reveals indicate an IO+overflow:"
                    "hidden bug class — IntersectionObserver attached to the "
                    "transformed child instead of the non-moving outer wrapper, "
                    "or a CSS reset killing the transition."
                ),
            }
        )
    else:
        _usage()

    _write(
        out_path,
        result,
        ensure_ascii=mode == "fail",
        trailing_newline=mode != "fail",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
