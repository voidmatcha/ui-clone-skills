#!/usr/bin/env python3
"""Report replay-track coverage per region for verification-plan.sh.

Prints one tab-separated line: ``<declared-count>\t<comma-joined region names>``

  declared-count  how many regions declare a replayTrack/replayTrackManifest
                  artifact, i.e. whether replay-track-compare has anything to
                  compare at all.
  region names    scroll-driven regions that declare NO track, which is the
                  evidence gap the plan must state rather than silently skip.

Per-region rather than a file-wide string match: replay-track-compare only
compares the tracks that were actually declared, so one region carrying a track
must not suppress the gap for a sibling that carries none.

Lives in a file rather than a verification-plan.sh heredoc because Homebrew
Bash can deadlock delivering heredocs to child processes
(tests/gates/test_verification_plan_b.py::test_verification_plan_does_not_use_bash_heredocs).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TRACK_KEYS = ("replayTrack", "replayTrackManifest")
MAX_NAMES = 8


def scan(data: object) -> tuple[int, list[str]]:
    declared = 0
    missing: list[str] = []

    def walk(node: object) -> None:
        nonlocal declared
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        trigger = node.get("triggerType")
        if isinstance(trigger, str):
            artifacts = node.get("artifacts")
            artifacts = artifacts if isinstance(artifacts, dict) else {}
            has_track = any(
                isinstance(artifacts.get(key), str) and artifacts[key].strip()
                for key in TRACK_KEYS
            )
            if has_track:
                declared += 1
            elif trigger == "scroll-driven":
                name = node.get("name") or node.get("selector") or "unnamed"
                missing.append(str(name))
        for value in node.values():
            walk(value)

    walk(data)
    return declared, missing


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("0\t")
        return 0
    try:
        data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("0\t")
        return 0
    declared, missing = scan(data)
    print(f"{declared}\t" + ", ".join(missing[:MAX_NAMES]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
