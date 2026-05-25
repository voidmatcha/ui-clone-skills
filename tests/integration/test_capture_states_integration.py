"""Integration test for scripts/extract/capture-states.sh (Phase A — splash
transition snapshots).

The fixture `fixtures/splash.html` boots with `body.is-loading` + a fixed
full-screen overlay, then flips to `body.is-loaded` after 500 ms. The capture
script should observe at least one transition (hash change), bookend it with
0ms.json + settled.json, and report `summary.reason` ∈ {"stable-2s",
"wall-clock-cap"}.

Gated by UI_CLONE_INTEGRATION=1 via tests/integration/conftest.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path


def _close_session(session: str) -> None:
    """Best-effort agent-browser session teardown. Never raise — teardown
    failure shouldn't mask a real test failure.
    """
    subprocess.run(
        ["agent-browser", "close", "--session", session],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_splash_transition_captured(tmp_path: Path, http_server: str, repo_root: Path) -> None:
    """Splash fixture (is-loading → is-loaded @ 500ms) produces:
    - trajectory.json with >= 1 transition (>= 2 entries: initial + flip)
    - 0ms.json + settled.json present
    - summary.checked is True
    - summary.reason ∈ {"stable-2s", "wall-clock-cap"}
    """
    script = repo_root / "scripts" / "extract" / "capture-states.sh"
    assert script.is_file(), f"capture-states.sh missing at {script}"

    url = f"{http_server}splash.html"
    session = f"itstates-{uuid.uuid4().hex[:12]}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    # The script opens a derived session `<session>-states` internally.
    derived = f"{session}-states"

    proc: subprocess.CompletedProcess[str] | None = None
    try:
        proc = subprocess.run(
            [str(script), url, session, str(ref_dir)],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ},
        )
    finally:
        _close_session(derived)

    assert proc is not None
    assert proc.returncode == 0, (
        f"capture-states.sh failed (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

    splash_dir = ref_dir / "states" / "splash"
    assert splash_dir.is_dir(), f"splash dir missing: {splash_dir}"

    trajectory_path = splash_dir / "trajectory.json"
    summary_path = splash_dir / "summary.json"
    zero_ms_path = splash_dir / "0ms.json"
    settled_path = splash_dir / "settled.json"

    assert trajectory_path.is_file(), f"trajectory.json missing in {splash_dir}"
    assert summary_path.is_file(), f"summary.json missing in {splash_dir}"
    assert zero_ms_path.is_file(), f"0ms.json missing in {splash_dir}"
    assert settled_path.is_file(), f"settled.json missing in {splash_dir}"

    trajectory = json.loads(trajectory_path.read_text())
    summary = json.loads(summary_path.read_text())

    assert isinstance(trajectory, list), f"trajectory not a list: {type(trajectory)}"
    # Splash flip should register: initial (0ms) + at least one transition entry.
    # The settled bookend may collapse onto the last recorded entry, so >= 2 is the
    # contract — not >= 3.
    assert len(trajectory) >= 2, (
        f"expected >= 2 trajectory entries (initial + transition), got {len(trajectory)}:\n"
        f"{json.dumps(trajectory, indent=2)}"
    )

    body_classes = [entry.get("bodyClass", "") for entry in trajectory]
    assert any("is-loading" in c for c in body_classes), (
        f"no entry with body.is-loading — splash boot signal missing.\n"
        f"bodyClass timeline: {body_classes}"
    )
    assert any("is-loaded" in c for c in body_classes), (
        f"no entry with body.is-loaded — splash flip never captured.\n"
        f"bodyClass timeline: {body_classes}"
    )

    assert summary.get("checked") is True, f"summary.checked != True: {summary}"
    assert summary.get("reason") in {"stable-2s", "wall-clock-cap"}, (
        f"unexpected summary.reason={summary.get('reason')!r}; full summary: {summary}"
    )
    assert summary.get("schemaVersion") == 1, f"schemaVersion drift: {summary}"

    # Bookend snapshots must contain the full outerHTML — sanity-check they
    # aren't just empty stubs.
    zero = json.loads(zero_ms_path.read_text())
    settled = json.loads(settled_path.read_text())
    assert isinstance(zero.get("outerHTML"), str) and len(zero["outerHTML"]) > 50, (
        f"0ms.json outerHTML missing/tiny: {zero!r}"
    )
    assert isinstance(settled.get("outerHTML"), str) and len(settled["outerHTML"]) > 50, (
        f"settled.json outerHTML missing/tiny: {settled!r}"
    )
