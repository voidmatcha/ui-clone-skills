"""Tests for scripts/extract/capture-states.sh — Phase A splash transition
snapshots. Uses a fake `agent-browser` executable on PATH per codex review
item (f). No live browser invocation.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "extract" / "capture-states.sh"


def _make_fake_agent_browser(
    tmp_path: Path, eval_payload: str, open_returncode: int = 0,
    eval_returncode: int = 0,
) -> Path:
    """Build a fake `agent-browser` shell wrapper that records its argv to
    `<tmp_path>/calls.log` and returns the given eval payload (stdout) on
    `eval`-subcommand invocations. `open` returns the configured rc with no
    output.
    """
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "agent-browser"
    # Use single-quoted heredoc so $1, $@ inside the wrapper are NOT expanded
    # by the outer python f-string layer.
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> '{tmp_path / 'calls.log'}'\n"
        "# Find the subcommand position — after --session NAME comes 'open' or 'eval'.\n"
        "shift 2  # consume --session NAME\n"
        'if [ "$1" = "open" ]; then\n'
        f"  exit {open_returncode}\n"
        'elif [ "$1" = "eval" ]; then\n'
        f"  echo '{eval_payload}'\n"
        f"  exit {eval_returncode}\n"
        "fi\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    return bin_dir


def _run_capture_states(
    ref_dir: Path, bin_dir: Path, *, reuse_session: bool = False
) -> subprocess.CompletedProcess[str]:
    """Invoke capture-states.sh with the fake bin dir prepended to PATH."""
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    # Keep the fake bash-backed agent-browser from emitting host locale
    # warnings into stdout/stderr that the capture script parses as JSON.
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    args = [str(SCRIPT), "https://example.test", "sess1", str(ref_dir)]
    if reuse_session:
        args.append("--reuse-session")
    return subprocess.run(
        args, capture_output=True, text=True, env=env, timeout=20
    )


def _eval_payload(states: list[dict], duration_ms: int = 100,
                  timed_out: bool = False, reason: str = "stable-2s") -> str:
    """Return a JSON-as-stdout payload matching the script's expected shape.
    Single quote escape: payload goes through bash echo single-quoted.
    """
    payload = {
        "states": states,
        "durationMs": duration_ms,
        "polls": len(states),
        "timedOut": timed_out,
        "reason": reason,
    }
    # Escape single quotes for embedding inside the fake script's `echo '...'`.
    return json.dumps(payload, ensure_ascii=False).replace("'", "'\\''")


# ── tests ────────────────────────────────────────────────────────────


def test_static_page_no_transitions_writes_summary(tmp_path: Path) -> None:
    """A page with no splash (single state @ 0ms, no further changes) →
    trajectory.json has 1 entry, summary.json reason='no-change',
    0ms.json present with the initial outerHTML."""
    ref_dir = tmp_path / "ref"
    initial = {
        "ts_ms": 0,
        "hash": 12345,
        "bodyClass": "body",
        "htmlClass": "no-js",
        "compositeDigest": "body|no-js|visible|0|1000|",
        "domLength": 1000,
        "fullHTML": "<html><body>static</body></html>",
        "bookend": "0ms",
    }
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload([initial], duration_ms=500, reason="no-change")
    )
    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    splash = ref_dir / "states" / "splash"
    summary = json.loads((splash / "summary.json").read_text())
    assert summary["checked"] is True
    assert summary["reason"] == "no-change"
    assert summary["polls"] == 1

    trajectory = json.loads((splash / "trajectory.json").read_text())
    assert len(trajectory) == 1
    assert trajectory[0]["bodyClass"] == "body"
    assert "fullHTML" not in trajectory[0], "trajectory must NOT carry fullHTML"

    initial_snap = json.loads((splash / "0ms.json").read_text())
    assert "static" in initial_snap["outerHTML"]


def test_multi_transition_emits_trajectory_and_bookends(tmp_path: Path) -> None:
    """Page with splash (multiple class transitions) → trajectory has all
    entries, 0ms.json + settled.json contain the bookend full DOMs, structural
    delta intermediate has its own NNms.json."""
    ref_dir = tmp_path / "ref"
    states = [
        {
            "ts_ms": 0,
            "hash": 1,
            "bodyClass": "is-loading",
            "htmlClass": "no-js",
            "compositeDigest": "x",
            "domLength": 1000,
            "fullHTML": "<html><body class='is-loading'></body></html>",
            "bookend": "0ms",
        },
        {
            "ts_ms": 300,
            "hash": 2,
            "bodyClass": "is-loading transition",
            "htmlClass": "no-js",
            "compositeDigest": "y",
            "domLength": 1100,
            "fullHTML": None,  # small delta, no full snapshot
            "structuralDelta": False,
        },
        {
            "ts_ms": 800,
            "hash": 3,
            "bodyClass": "is-loaded",
            "htmlClass": "no-js loaded",
            "compositeDigest": "z",
            "domLength": 1800,
            "fullHTML": "<html><body class='is-loaded'>real content</body></html>",
            "structuralDelta": True,
        },
        {
            "ts_ms": 2900,
            "hash": 4,
            "bodyClass": "is-loaded",
            "htmlClass": "no-js loaded",
            "compositeDigest": "z2",
            "domLength": 1850,
            "fullHTML": "<html><body class='is-loaded'>real content settled</body></html>",
            "bookend": "settled",
        },
    ]
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=2900, reason="stable-2s")
    )
    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    splash = ref_dir / "states" / "splash"
    trajectory = json.loads((splash / "trajectory.json").read_text())
    assert len(trajectory) == 4

    # Bookend full DOMs
    bookend_0 = json.loads((splash / "0ms.json").read_text())
    assert "is-loading" in bookend_0["outerHTML"]
    bookend_settled = json.loads((splash / "settled.json").read_text())
    assert "real content settled" in bookend_settled["outerHTML"]

    # Mid-transition structural delta @ 800ms got its own snapshot
    structural = json.loads((splash / "800ms.json").read_text())
    assert "real content" in structural["outerHTML"]

    # 300ms had no fullHTML → no 300ms.json file
    assert not (splash / "300ms.json").is_file()


def test_timeout_marks_summary_timed_out(tmp_path: Path) -> None:
    """5s wall-clock cap hit → summary.timedOut=true + reason='wall-clock-cap'."""
    ref_dir = tmp_path / "ref"
    states = [
        {
            "ts_ms": 0, "hash": 1, "bodyClass": "loading", "htmlClass": "",
            "compositeDigest": "a", "domLength": 1000,
            "fullHTML": "<html></html>", "bookend": "0ms",
        },
    ]
    bin_dir = _make_fake_agent_browser(
        tmp_path,
        _eval_payload(states, duration_ms=5000, timed_out=True, reason="wall-clock-cap"),
    )
    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0

    summary = json.loads((ref_dir / "states" / "splash" / "summary.json").read_text())
    assert summary["timedOut"] is True
    assert summary["reason"] == "wall-clock-cap"


def test_agent_browser_open_failure_exit_2(tmp_path: Path) -> None:
    """Phase 1: open returncode != 0 → script exits 2."""
    ref_dir = tmp_path / "ref"
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload([]), open_returncode=1)
    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 2
    assert "open failed" in proc.stderr


def test_invalid_eval_response_exit_3(tmp_path: Path) -> None:
    """eval returns non-JSON → script exits 3."""
    ref_dir = tmp_path / "ref"
    bin_dir = _make_fake_agent_browser(tmp_path, "not json{{{")
    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 3


def test_derived_session_used_by_default(tmp_path: Path) -> None:
    """Codex item (d): default behavior uses ${SESSION}-states derived
    session, not the caller's session directly."""
    ref_dir = tmp_path / "ref"
    states = [{
        "ts_ms": 0, "hash": 1, "bodyClass": "", "htmlClass": "",
        "compositeDigest": "", "domLength": 100,
        "fullHTML": "<html></html>", "bookend": "0ms",
    }]
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload(states))
    proc = _run_capture_states(ref_dir, bin_dir, reuse_session=False)
    assert proc.returncode == 0
    calls = (tmp_path / "calls.log").read_text()
    assert "sess1-states" in calls
    # Caller's session "sess1" should NOT appear on its own (only embedded
    # as a prefix of "sess1-states").
    bare_session_lines = [
        line for line in calls.splitlines()
        if "--session sess1 " in (line + " ") and "sess1-states" not in line
    ]
    assert not bare_session_lines, f"derived session must be used: {calls}"


def test_reuse_session_flag_uses_callers_session(tmp_path: Path) -> None:
    """--reuse-session flag → use the caller's session directly, no -states
    suffix. For when capture-states.sh runs inside capture.sh on a quiet
    sequential session."""
    ref_dir = tmp_path / "ref"
    states = [{
        "ts_ms": 0, "hash": 1, "bodyClass": "", "htmlClass": "",
        "compositeDigest": "", "domLength": 100,
        "fullHTML": "<html></html>", "bookend": "0ms",
    }]
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload(states))
    proc = _run_capture_states(ref_dir, bin_dir, reuse_session=True)
    assert proc.returncode == 0
    calls = (tmp_path / "calls.log").read_text()
    assert "--session sess1 " in (calls + " "), (
        f"reuse-session must invoke caller's session: {calls}"
    )
    assert "sess1-states" not in calls
