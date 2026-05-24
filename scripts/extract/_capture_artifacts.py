"""Deterministic artifact-handling helpers for `scripts/extract/capture.sh`.

Extracted per codex review (agentId a541f8a835d9e8569, verdict
partial_migrate) so the JSON/parsing/timing layer is unit-testable
while agent-browser orchestration stays in shell. Shell wrapper calls
these via:
    python3 _capture_artifacts.py parse-height '"5400"'
    python3 _capture_artifacts.py write-regions <ref_dir> <h> <w>
    python3 _capture_artifacts.py summarize <ref_dir>

Public API:
    parse_page_height(raw: str, *, fallback: int = 5000) -> int
    write_regions_json(ref_dir: Path, page_height: int, viewport_width: int = 1440) -> None
    summarize_artifacts(ref_dir: Path) -> dict
    main(argv: list[str]) -> int
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_FALLBACK_HEIGHT = 5000


def parse_page_height(raw: str, *, fallback: int = _FALLBACK_HEIGHT) -> int:
    """Parse the JSON-encoded value returned by `agent-browser eval`.

    `agent-browser eval` double-encodes its return value (a number comes
    back as `"5400"` — string containing a number). We unwrap once to
    get the int. Fallback when:
      - raw is empty or whitespace
      - raw isn't valid JSON
      - decoded value isn't numeric
      - decoded value is <= 0 (would cause divide-by-zero downstream)
    """
    if not raw or not raw.strip():
        return fallback
    text = raw.strip()
    # Try direct int parse first (raw int output from some browsers).
    try:
        n = int(text)
        return n if n > 0 else fallback
    except ValueError:
        pass
    # Try JSON unwrap (string-wrapped number is the agent-browser case).
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return fallback
    # Decoded could be a number or a string containing a number.
    if isinstance(decoded, int | float):
        n = int(decoded)
        return n if n > 0 else fallback
    if isinstance(decoded, str):
        try:
            n = int(decoded.strip())
            return n if n > 0 else fallback
        except ValueError:
            return fallback
    return fallback


def write_regions_json(
    ref_dir: Path,
    page_height: int,
    viewport_width: int = 1440,
) -> None:
    """Write the minimal `regions.json` containing a single full-page region.

    Proper region segmentation belongs to the `ui-capture` skill's
    detection pipeline. This minimal shape unblocks the `reference`
    gate row that requires `regions.json` to exist.
    """
    ref_dir = Path(ref_dir)
    ref_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "regions": [
            {
                "name": "full-page",
                "x": 0,
                "y": 0,
                "width": int(viewport_width),
                "height": int(page_height),
            }
        ]
    }
    (ref_dir / "regions.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def summarize_artifacts(ref_dir: Path) -> dict[str, Any]:
    """Return the count summary capture.sh prints after capture finishes.

    Mirrors the shell `find ... | wc -l` calls (lines 92-95) so the
    same totals are available to Python callers and unit tests without
    a subprocess. Keys match the shell labels for readability.
    """
    ref_dir = Path(ref_dir)
    def _count(subpath: str, pattern: str = "*") -> int:
        d = ref_dir / subpath
        if not d.is_dir():
            return 0
        return sum(
            1
            for p in d.glob(pattern)
            if p.is_file() and not p.name.startswith(".")
        )

    return {
        "static_ref_screenshots": _count("static/ref"),
        "scroll_video_ref_videos": _count("scroll-video/ref"),
        "transitions_ref_videos": _count("transitions/ref"),
        "regions_json_present": (ref_dir / "regions.json").is_file(),
    }


def _print_summary(summary: dict[str, Any], ref_dir: Path) -> None:
    """Reproduce capture.sh's textual summary block."""
    print(f"capture.sh: Phase 1 artifacts written to {ref_dir}")
    print(f"  static/ref/: {summary['static_ref_screenshots']} screenshots")
    print(f"  scroll-video/ref/: {summary['scroll_video_ref_videos']} videos")
    print(f"  transitions/ref/: {summary['transitions_ref_videos']} videos")
    print(
        f"  regions.json: {'ok' if summary['regions_json_present'] else 'MISSING'}"
    )


def main(argv: list[str]) -> int:
    """CLI entry point — dispatches to subcommand."""
    if not argv:
        print(
            "usage: _capture_artifacts.py {parse-height|write-regions|summarize} ...",
            file=sys.stderr,
        )
        return 2
    cmd = argv[0]
    rest = argv[1:]

    if cmd == "parse-height":
        if not rest:
            print("usage: parse-height <raw>", file=sys.stderr)
            return 2
        # Concatenate remaining args (shell may split JSON whitespace).
        raw = " ".join(rest)
        print(parse_page_height(raw))
        return 0

    if cmd == "write-regions":
        if len(rest) < 2:
            print(
                "usage: write-regions <ref_dir> <page_height> [viewport_width]",
                file=sys.stderr,
            )
            return 2
        ref_dir = Path(rest[0])
        try:
            page_height = int(rest[1])
        except ValueError:
            print(f"page_height must be integer, got: {rest[1]!r}", file=sys.stderr)
            return 2
        viewport_width = int(rest[2]) if len(rest) >= 3 else 1440
        write_regions_json(ref_dir, page_height, viewport_width)
        return 0

    if cmd == "summarize":
        if not rest:
            print("usage: summarize <ref_dir>", file=sys.stderr)
            return 2
        ref_dir = Path(rest[0])
        summary = summarize_artifacts(ref_dir)
        _print_summary(summary, ref_dir)
        return 0

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
