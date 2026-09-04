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
        "cmd=''\n"
        "# Find the subcommand position after global options.\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        "    --session|--init-script) shift 2 ;;\n"
        "    --json) shift ;;\n"
        "    open|eval) cmd=\"$1\"; shift; break ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        'if [ "$cmd" = "open" ]; then\n'
        f"  exit {open_returncode}\n"
        'elif [ "$cmd" = "eval" ]; then\n'
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


def test_browser_eval_uses_bounded_selector_fallback_for_classless_elements() -> None:
    """Classless overlay targets must not fall back to broad tag selectors."""
    script = SCRIPT.read_text()

    assert "nth-of-type" in script
    assert "el.tagName.toLowerCase()" not in script
    assert "selectorFor(el)" in script


def test_init_sampler_waits_for_document_root_before_computing_state() -> None:
    """Pre-navigation init scripts may run before html/body exist."""
    script = SCRIPT.read_text()

    wait_marker = "while (!document.documentElement || !document.body)"
    assert wait_marker in script
    assert script.index(wait_marker) < script.index("const startedAt = performance.now()")


def test_fullscreen_overlay_capture_extends_until_exit() -> None:
    """Cold-cache loaders may outlive the ordinary 5s observation window."""
    script = SCRIPT.read_text()

    assert "awaitingInitialOverlayExit ? 15000 : 5000" in script
    assert "!awaitingInitialOverlayExit && (now - lastChangeAt) >= 2000" in script
    assert "if (initialOverlayExited) break" in script
    assert "elapsed >= captureLimitMs" in script


def test_overlay_candidate_matches_runtime_probe_visibility_threshold() -> None:
    """Capture overlay detection must mirror the runtime splash probe predicate."""
    script = SCRIPT.read_text()

    assert "visibleWidth" in script
    assert "visibleHeight" in script
    assert "viewportCoverage" in script
    assert "viewportCoverage >= 0.75" in script
    assert 'cs.position === "sticky"' in script
    assert 'cs.position === "fixed" || (cs.position === "absolute" && z >= 10)' in script
    assert "opacity > 0.05" in script
    assert "r.width >= vw * 0.95" not in script
    assert "z > 10" not in script


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


def test_classless_fullscreen_splash_writes_contract(tmp_path: Path) -> None:
    """Classless fullscreen splash lifecycles must become an explicit
    schema-versioned contract, not just a trajectory side effect."""
    ref_dir = tmp_path / "ref"
    states = [
        {
            "ts_ms": 0,
            "hash": 1,
            "bodyClass": "",
            "htmlClass": "",
            "compositeDigest": "overlay:#intro|coverage:1|animation:1|media:bg",
            "domLength": 1200,
            "overlay": {
                "selector": "#intro",
                "coverage": 0.98,
                "visible": True,
                "opacity": "1",
            },
            "animationEvidence": {
                "activeCount": 1,
                "runningCount": 1,
                "samples": [{"selector": "#intro", "currentTime": 0, "duration": 900}],
            },
            "motionEvidence": {
                "changed": True,
                "signals": ["overlay-coverage", "active-animation"],
            },
            "mediaFingerprint": {
                "videos": [{"src": "/splash.webm", "currentTime": 0, "paused": False}],
                "hash": "media-start",
            },
            "fullHTML": "<html><body><div id='intro'><video src='/splash.webm'></video></div><main hidden></main></body></html>",
            "bookend": "0ms",
        },
        {
            "ts_ms": 940,
            "hash": 2,
            "bodyClass": "",
            "htmlClass": "",
            "compositeDigest": "overlay:none|coverage:0|animation:0|media:bg",
            "domLength": 1210,
            "overlay": {
                "selector": None,
                "coverage": 0,
                "visible": False,
                "opacity": "0",
            },
            "animationEvidence": {
                "activeCount": 0,
                "runningCount": 0,
                "samples": [],
            },
            "motionEvidence": {
                "changed": True,
                "signals": ["overlay-exit"],
            },
            "mediaFingerprint": {
                "videos": [{"src": "/splash.webm", "currentTime": 0.94, "paused": True}],
                "hash": "media-end",
            },
            "fullHTML": None,
            "structuralDelta": False,
        },
        {
            "ts_ms": 2940,
            "hash": 3,
            "bodyClass": "",
            "htmlClass": "",
            "compositeDigest": "overlay:none|coverage:0|animation:0|media:settled",
            "domLength": 1210,
            "overlay": {
                "selector": None,
                "coverage": 0,
                "visible": False,
                "opacity": "0",
            },
            "animationEvidence": {
                "activeCount": 0,
                "runningCount": 0,
                "samples": [],
            },
            "motionEvidence": {
                "changed": False,
                "signals": [],
            },
            "mediaFingerprint": {
                "videos": [{"src": "/splash.webm", "currentTime": 0.94, "paused": True}],
                "hash": "media-end",
            },
            "fullHTML": "<html><body><main>loaded</main></body></html>",
            "bookend": "settled",
        },
    ]
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=2940, reason="stable-2s")
    )
    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["schemaVersion"] == 1
    assert contract["detected"] is True
    assert contract["overlay"]["selector"] == "#intro"
    assert contract["overlay"]["maxCoverage"] == 0.98
    assert contract["activeAnimation"]["maxActiveCount"] == 1
    assert contract["motionEvidence"]["changed"] is True
    assert contract["mediaFingerprint"]["hashes"] == ["media-start", "media-end"]
    assert contract["exitTiming"]["fromMs"] == 0
    assert contract["exitTiming"]["toMs"] == 940
    assert "states/splash/0ms.json" in contract["bookends"]
    assert "states/splash/settled.json" in contract["bookends"]


def test_background_media_motion_without_overlay_is_not_a_splash(tmp_path: Path) -> None:
    """A normal page-load video/animation must not promote itself to a splash."""
    ref_dir = tmp_path / "ref"
    states = []
    for ts_ms, media_hash in ((0, "video-0"), (900, "video-1"), (2900, "video-2")):
        states.append(
            {
                "ts_ms": ts_ms,
                "hash": ts_ms + 1,
                "bodyClass": "",
                "htmlClass": "",
                "compositeDigest": media_hash,
                "domLength": 1200,
                "overlay": {
                    "selector": None,
                    "coverage": 0,
                    "visible": False,
                    "opacity": "0",
                },
                "animationEvidence": {
                    "activeCount": 1,
                    "runningCount": 1,
                    "samples": [{"selector": "#hero-video", "currentTime": ts_ms}],
                },
                "motionEvidence": {"changed": ts_ms > 0, "signals": ["media"]},
                "mediaFingerprint": {
                    "videos": [{"src": "/hero.mp4", "currentTime": ts_ms / 1000}],
                    "hash": media_hash,
                },
                "fullHTML": "<html><body><main><video id='hero-video'></video></main></body></html>",
                "bookend": "0ms" if ts_ms == 0 else "settled" if ts_ms == 2900 else None,
            }
        )
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=2900, reason="stable-2s")
    )
    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["detected"] is False
    assert contract["overlay"]["selector"] is None


def test_persistent_fullscreen_overlay_is_not_a_completed_splash(tmp_path: Path) -> None:
    """A static persistent modal plus unrelated motion has no splash exit lifecycle."""
    ref_dir = tmp_path / "ref"
    states = []
    for ts_ms in (0, 900, 2900):
        states.append(
            {
                "ts_ms": ts_ms,
                "hash": ts_ms + 1,
                "bodyClass": "",
                "htmlClass": "",
                "compositeDigest": f"modal|video-{ts_ms}",
                "domLength": 1200,
                "overlay": {
                    "selector": "#consent-modal",
                    "coverage": 1,
                    "visible": True,
                    "opacity": "1",
                },
                "animationEvidence": {
                    "activeCount": 1,
                    "runningCount": 1,
                    "samples": [{"selector": "#hero-video", "currentTime": ts_ms}],
                },
                "motionEvidence": {"changed": ts_ms > 0, "signals": ["media"]},
                "mediaFingerprint": {"videos": [{"src": "/hero.mp4"}], "hash": f"v-{ts_ms}"},
                "fullHTML": "<html><body><div id='consent-modal'></div><video id='hero-video'></video></body></html>",
                "bookend": "0ms" if ts_ms == 0 else "settled" if ts_ms == 2900 else None,
            }
        )
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=2900, reason="stable-2s")
    )
    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["detected"] is False
    assert contract["overlay"]["exitObserved"] is False


def test_persistent_changing_overlay_is_not_a_completed_splash(tmp_path: Path) -> None:
    """Phase changes without disappearance must not prove splash completion."""
    ref_dir = tmp_path / "ref"
    states = []
    for ts_ms, opacity in ((0, "1"), (600, "0.5"), (2600, "0.8")):
        states.append(
            {
                "ts_ms": ts_ms,
                "hash": ts_ms + 1,
                "bodyClass": "",
                "htmlClass": "",
                "compositeDigest": f"persistent-overlay|{opacity}",
                "domLength": 1200,
                "overlay": {
                    "selector": "#persistent-overlay",
                    "coverage": 1,
                    "visible": True,
                    "opacity": opacity,
                    "position": "fixed",
                    "zIndex": 100,
                },
                "animationEvidence": {
                    "activeCount": 1,
                    "runningCount": 1,
                    "samples": [{"selector": "#persistent-overlay", "currentTime": ts_ms}],
                },
                "motionEvidence": {"changed": ts_ms > 0, "signals": ["active-animation"]},
                "mediaFingerprint": {"videos": [], "hash": "empty"},
                "fullHTML": "<html><body><div id='persistent-overlay'></div></body></html>",
                "bookend": "0ms" if ts_ms == 0 else "settled" if ts_ms == 2600 else None,
            }
        )
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=2600, reason="stable-2s")
    )

    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["detected"] is False
    assert contract["overlay"]["exitObserved"] is False
    assert contract["overlay"]["everVisible"] is True
    assert contract["capture"]["stateCount"] == 3
    assert contract["capture"]["timedOut"] is False
    assert contract["capture"]["authoritativeNegative"] is False
    assert contract["exitTiming"]["durationMs"] is None


def test_stable_no_overlay_negative_is_authoritative(tmp_path: Path) -> None:
    """A single stable pre-navigation state with no overlay is an authoritative negative."""
    ref_dir = tmp_path / "ref"
    states = [{
        "ts_ms": 0,
        "hash": 1,
        "bodyClass": "",
        "htmlClass": "",
        "compositeDigest": "stable-no-overlay",
        "domLength": 100,
        "overlay": {
            "selector": None,
            "identity": None,
            "coverage": 0,
            "visible": False,
            "opacity": "0",
        },
        "fullHTML": "<html><body><main>stable</main></body></html>",
        "bookend": "0ms",
    }]
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=100, reason="no-change")
    )

    proc = _run_capture_states(ref_dir, bin_dir, reuse_session=False)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["detected"] is False
    assert contract["overlay"]["everVisible"] is False
    assert contract["capture"]["stateCount"] == 1
    assert contract["capture"]["timedOut"] is False
    assert contract["capture"]["reason"] == "no-change"
    assert contract["capture"]["authoritativeNegative"] is True


def test_overlay_class_flip_does_not_count_as_exit(tmp_path: Path) -> None:
    """Stable overlay identity must survive mutable class selector changes."""
    ref_dir = tmp_path / "ref"
    states = []
    for ts_ms, selector in ((0, "div.loading"), (600, "div"), (2600, "div.loading")):
        states.append(
            {
                "ts_ms": ts_ms,
                "hash": ts_ms + 1,
                "bodyClass": "",
                "htmlClass": "",
                "compositeDigest": f"overlay|{selector}",
                "domLength": 1200,
                "overlay": {
                    "selector": selector,
                    "identity": "body > div:nth-of-type(1)",
                    "coverage": 1,
                    "visible": True,
                    "opacity": "1",
                    "position": "fixed",
                    "zIndex": 100,
                },
                "animationEvidence": {"activeCount": 0, "runningCount": 0, "samples": []},
                "motionEvidence": {"changed": ts_ms > 0, "signals": ["fullscreen-overlay"]},
                "mediaFingerprint": {"videos": [], "hash": "empty"},
                "fullHTML": "<html><body><div></div></body></html>",
                "bookend": "0ms" if ts_ms == 0 else "settled" if ts_ms == 2600 else None,
            }
        )
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=2600, reason="stable-2s")
    )

    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["detected"] is False
    assert contract["overlay"]["identity"] == "body > div:nth-of-type(1)"
    assert contract["overlay"]["exitObserved"] is False
    assert contract["exitTiming"]["toMs"] is None


def test_overlay_class_flip_before_disappearance_uses_real_exit_time(tmp_path: Path) -> None:
    """Selector churn before disappearance must not shorten exit timing."""
    ref_dir = tmp_path / "ref"
    states = [
        {
            "ts_ms": 0,
            "hash": 1,
            "bodyClass": "",
            "htmlClass": "",
            "compositeDigest": "overlay|loading",
            "domLength": 1200,
            "overlay": {
                "selector": "div.loading",
                "identity": "body > div:nth-of-type(1)",
                "coverage": 1,
                "visible": True,
                "opacity": "1",
            },
            "animationEvidence": {"activeCount": 1, "runningCount": 1, "samples": []},
            "motionEvidence": {"changed": True, "signals": ["fullscreen-overlay"]},
            "mediaFingerprint": {"videos": [], "hash": "empty"},
            "fullHTML": "<html><body><div class='loading'></div></body></html>",
            "bookend": "0ms",
        },
        {
            "ts_ms": 500,
            "hash": 2,
            "bodyClass": "",
            "htmlClass": "",
            "compositeDigest": "overlay|class-removed",
            "domLength": 1200,
            "overlay": {
                "selector": "div",
                "identity": "body > div:nth-of-type(1)",
                "coverage": 1,
                "visible": True,
                "opacity": "1",
            },
            "animationEvidence": {"activeCount": 1, "runningCount": 1, "samples": []},
            "motionEvidence": {"changed": True, "signals": ["fullscreen-overlay"]},
            "mediaFingerprint": {"videos": [], "hash": "empty"},
            "fullHTML": None,
            "structuralDelta": False,
        },
        {
            "ts_ms": 1400,
            "hash": 3,
            "bodyClass": "",
            "htmlClass": "",
            "compositeDigest": "overlay|gone",
            "domLength": 800,
            "overlay": {
                "selector": None,
                "identity": None,
                "coverage": 0,
                "visible": False,
                "opacity": "0",
            },
            "animationEvidence": {"activeCount": 0, "runningCount": 0, "samples": []},
            "motionEvidence": {"changed": True, "signals": ["overlay-exit"]},
            "mediaFingerprint": {"videos": [], "hash": "empty"},
            "fullHTML": None,
            "structuralDelta": True,
        },
        {
            "ts_ms": 3400,
            "hash": 4,
            "bodyClass": "",
            "htmlClass": "",
            "compositeDigest": "settled",
            "domLength": 800,
            "overlay": {
                "selector": None,
                "identity": None,
                "coverage": 0,
                "visible": False,
                "opacity": "0",
            },
            "animationEvidence": {"activeCount": 0, "runningCount": 0, "samples": []},
            "motionEvidence": {"changed": False, "signals": []},
            "mediaFingerprint": {"videos": [], "hash": "empty"},
            "fullHTML": "<html><body><main>loaded</main></body></html>",
            "bookend": "settled",
        },
    ]
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=3400, reason="stable-2s")
    )

    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["detected"] is True
    assert contract["overlay"]["identity"] == "body > div:nth-of-type(1)"
    assert contract["overlay"]["exitObserved"] is True
    assert contract["exitTiming"]["toMs"] == 1400
    assert contract["exitTiming"]["durationMs"] == 1400


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


def test_about_blank_eval_envelope_fails_closed(tmp_path: Path) -> None:
    """A wrong-origin envelope fails closed and publishes no capture output."""
    ref_dir = tmp_path / "ref"
    payload = json.dumps({
        "success": True,
        "data": {
            "origin": "about:blank",
            "result": {
                "states": [],
                "durationMs": 100,
                "polls": 0,
                "timedOut": False,
                "reason": "no-change",
            },
        },
    }).replace("'", "'\\''")
    bin_dir = _make_fake_agent_browser(tmp_path, payload)

    proc = _run_capture_states(ref_dir, bin_dir)

    assert proc.returncode == 3
    assert "lost the page target" in proc.stderr
    assert not (ref_dir / "states" / "splash" / "summary.json").exists()
    assert not (ref_dir / "states" / "splash" / "trajectory.json").exists()


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


def test_derived_session_installs_init_script_before_open(tmp_path: Path) -> None:
    """Default first-load capture must begin before navigation, not after open."""
    ref_dir = tmp_path / "ref"
    states = [{
        "ts_ms": 0, "hash": 1, "bodyClass": "", "htmlClass": "",
        "compositeDigest": "", "domLength": 100,
        "fullHTML": "<html></html>", "bookend": "0ms",
    }]
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload(states))

    proc = _run_capture_states(ref_dir, bin_dir, reuse_session=False)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    calls = (tmp_path / "calls.log").read_text().splitlines()
    open_calls = [line for line in calls if " open " in f" {line} "]
    assert open_calls, f"expected an open call: {calls}"
    assert "--init-script " in open_calls[0]
    assert open_calls[0].index("--init-script ") < open_calls[0].index(" open ")
    assert "sleep 2" not in SCRIPT.read_text()


def test_summary_and_contract_capture_mode_default_pre_navigation(tmp_path: Path) -> None:
    """Default capture mode has pre-navigation negative authority."""
    ref_dir = tmp_path / "ref"
    states = [{
        "ts_ms": 0, "hash": 1, "bodyClass": "", "htmlClass": "",
        "compositeDigest": "", "domLength": 100,
        "fullHTML": "<html></html>", "bookend": "0ms",
    }]
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload(states))

    proc = _run_capture_states(ref_dir, bin_dir, reuse_session=False)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    splash = ref_dir / "states" / "splash"
    summary = json.loads((splash / "summary.json").read_text())
    contract = json.loads((splash / "contract.json").read_text())
    assert summary["captureMode"] == "pre-navigation"
    assert contract["captureMode"] == "pre-navigation"


def test_summary_and_contract_capture_mode_reuse_session(tmp_path: Path) -> None:
    """Reuse-session capture mode is weaker for missed first-load negatives."""
    ref_dir = tmp_path / "ref"
    states = [{
        "ts_ms": 0, "hash": 1, "bodyClass": "", "htmlClass": "",
        "compositeDigest": "", "domLength": 100,
        "fullHTML": "<html></html>", "bookend": "0ms",
    }]
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload(states))

    proc = _run_capture_states(ref_dir, bin_dir, reuse_session=True)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    splash = ref_dir / "states" / "splash"
    summary = json.loads((splash / "summary.json").read_text())
    contract = json.loads((splash / "contract.json").read_text())
    assert summary["captureMode"] == "reuse-session"
    assert contract["captureMode"] == "reuse-session"


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
