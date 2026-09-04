"""Integration test for scripts/extract/capture-scroll.sh (Phase B —
scroll-progress snapshots).

The fixture `fixtures/scroll.html` is 5 sections × 1600 px = 8000 px tall, so
`maxScrollable` is well above zero and the script's "static page" path is
NOT taken. We expect exactly 7 stops at pcts [0, 10, 25, 50, 75, 90, 100] plus
the corresponding 7 per-pct json files, and `summary.scrollEngine == "native"`
(no Lenis / Locomotive on the fixture).

Gated by UI_CLONE_INTEGRATION=1 via tests/integration/conftest.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

EXPECTED_PCTS = [0, 10, 25, 50, 75, 90, 100]


def _close_session(session: str) -> None:
    """Best-effort agent-browser session teardown."""
    subprocess.run(
        ["agent-browser", "close", "--session", session],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_scroll_sweep_captures_seven_stops(tmp_path: Path, http_server: str, repo_root: Path) -> None:
    """8000px fixture (5 distinct sections) produces:
    - trajectory.json with exactly 7 entries at pcts [0,10,25,50,75,90,100]
    - one <pct>pct.json file per stop (7 total), each carrying outerHTML
    - summary.scrollEngine == "native"
    - summary.static is False (the fixture is taller than the viewport)
    """
    script = repo_root / "scripts" / "extract" / "capture-scroll.sh"
    assert script.is_file(), f"capture-scroll.sh missing at {script}"

    url = f"{http_server}scroll.html"
    session = f"itscroll-{uuid.uuid4().hex[:12]}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    derived = f"{session}-scroll"

    proc: subprocess.CompletedProcess[str] | None = None
    try:
        proc = subprocess.run(
            [str(script), url, session, str(ref_dir)],
            capture_output=True,
            text=True,
            # 7 stops × ~1.1s stability wait + Chrome startup → 60s is conservative.
            timeout=60,
            env={**os.environ},
        )
    finally:
        _close_session(derived)

    assert proc is not None
    assert proc.returncode == 0, (
        f"capture-scroll.sh failed (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

    scroll_dir = ref_dir / "states" / "scroll"
    assert scroll_dir.is_dir(), f"scroll dir missing: {scroll_dir}"

    trajectory_path = scroll_dir / "trajectory.json"
    summary_path = scroll_dir / "summary.json"
    assert trajectory_path.is_file(), f"trajectory.json missing in {scroll_dir}"
    assert summary_path.is_file(), f"summary.json missing in {scroll_dir}"

    trajectory = json.loads(trajectory_path.read_text())
    summary = json.loads(summary_path.read_text())

    assert isinstance(trajectory, list), f"trajectory not a list: {type(trajectory)}"
    assert len(trajectory) == len(EXPECTED_PCTS), (
        f"expected {len(EXPECTED_PCTS)} stops, got {len(trajectory)}:\n"
        f"{json.dumps(trajectory, indent=2)}"
    )
    actual_pcts = [entry.get("pct") for entry in trajectory]
    assert actual_pcts == EXPECTED_PCTS, (
        f"pct sequence mismatch.\n  expected: {EXPECTED_PCTS}\n  actual:   {actual_pcts}"
    )

    # Per-pct full DOM snapshots — one per stop, each with non-empty outerHTML.
    for pct in EXPECTED_PCTS:
        per_pct = scroll_dir / f"{pct}pct.json"
        assert per_pct.is_file(), f"{pct}pct.json missing in {scroll_dir}"
        payload = json.loads(per_pct.read_text())
        assert payload.get("pct") == pct, f"{per_pct.name} pct mismatch: {payload!r}"
        assert isinstance(payload.get("outerHTML"), str), (
            f"{per_pct.name} outerHTML not a string: {payload!r}"
        )
        assert len(payload["outerHTML"]) > 50, (
            f"{per_pct.name} outerHTML suspiciously small "
            f"(len={len(payload['outerHTML'])}): {payload['outerHTML'][:100]!r}"
        )

    assert summary.get("checked") is True, f"summary.checked != True: {summary}"
    assert summary.get("scrollEngine") == "native", (
        f"expected scrollEngine='native' on plain HTML fixture, got "
        f"{summary.get('scrollEngine')!r}; full summary: {summary}"
    )
    assert summary.get("scrollTransportProven") is True, summary
    assert summary.get("static") is False, (
        f"fixture is 8000px tall — summary.static should be False, got {summary.get('static')!r}"
    )
    assert summary.get("schemaVersion") == 2, f"schemaVersion drift: {summary}"
    assert summary.get("scrollHeight", 0) >= 4000, (
        f"scrollHeight={summary.get('scrollHeight')} — fixture should report at least 4000px"
    )


def test_marker_only_lenis_fails_without_publishing_artifacts(
    tmp_path: Path, http_server: str, repo_root: Path
) -> None:
    """A Lenis class without a callable transport is not capture proof."""
    script = repo_root / "scripts" / "extract" / "capture-scroll.sh"
    url = f"{http_server}replay-track-lenis-marker-only.html"
    session = f"itsm-{uuid.uuid4().hex[:8]}"
    ref_dir = tmp_path / "ref-marker"
    ref_dir.mkdir()

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
        _close_session(f"{session}-scroll")

    assert proc is not None
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "scroll transport is unproven" in proc.stderr
    assert not (ref_dir / "states" / "scroll").exists()


def test_hidden_lenis_with_root_wheel_listener_is_proven(
    tmp_path: Path, http_server: str, repo_root: Path
) -> None:
    """A pre-navigation root listener probe can prove a closure-hidden engine."""
    script = repo_root / "scripts" / "extract" / "capture-scroll.sh"
    url = f"{http_server}replay-track-lenis-wheel.html"
    session = f"itsw-{uuid.uuid4().hex[:8]}"
    ref_dir = tmp_path / "ref-wheel"
    ref_dir.mkdir()

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
        _close_session(f"{session}-scroll")

    assert proc is not None
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads(
        (ref_dir / "states" / "scroll" / "summary.json").read_text()
    )
    assert summary["scrollEngine"] == "lenis"
    assert summary["scrollTransportProven"] is True
    assert summary["scrollControlMethod"] == "proven-wheel-engine-with-native-positioning"
    assert "root non-passive wheel listener" in summary["scrollEngineReason"]
    assert summary["alignmentFailures"] == []
