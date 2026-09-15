"""Tests for scripts/extract/capture-states.sh — Phase A splash transition
snapshots. Uses a fake `agent-browser` executable on PATH per codex review
item (f). No live browser invocation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

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
    ref_dir: Path, bin_dir: Path, *, reuse_session: bool = False, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke capture-states.sh with the fake bin dir prepended to PATH.

    `cwd` is the caller's working directory (the pipeline runs this from an
    impl tree or a scratch dir, never from the repo root); tests that pass it
    are checking that the script does not depend on it."""
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
        args, capture_output=True, text=True, env=env, timeout=20, cwd=cwd
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
    """Cold-cache loaders may outlive the ordinary observation window.

    The no-overlay ceiling is 10000ms rather than the original 5000ms: the loop
    only exits early on a 2s quiet window, so a page whose own entry animations
    run ~1.6s while a hero video moves the media fingerprint as it loads cannot
    go quiet inside 5s. It then hits the cap mid-load and records a settled
    bookend before the page actually settled. The wider ceiling does not change
    what `authoritativeNegative` rests on (the sampled evidence plus the run
    settling on its own, see ui_clone.splash_contract); it only gives a page
    more room to settle.
    """
    script = SCRIPT.read_text()

    assert "awaitingInitialOverlayExit ? 15000 : 10000" in script
    assert "!awaitingInitialOverlayExit && (now - lastChangeAt) >= 2000" in script
    assert "if (initialOverlayExited) break" in script
    assert "elapsed >= captureLimitMs" in script


def test_sampler_surveys_covering_elements_through_shadow_roots() -> None:
    """The probe alone cannot see an in-flow loader, a low-z absolute one, or a
    shadow-root splash. The sampler must walk open shadow roots and record the
    covering-element map every state, or the python rule has nothing to read."""
    script = SCRIPT.read_text()

    assert "yield* eachRenderedElement(el.shadowRoot)" in script
    assert "for (const el of eachRenderedElement(document.body))" in script
    assert 'const candidates = document.querySelectorAll("body *")' not in script
    assert "if (viewportCoverage < COVERING_RECORD_FLOOR) continue;" in script
    assert script.count("covering: ") >= 4, "covering must ride every pushed state"
    assert "coveringIdentities.join" in script, "covering set must feed the state hash"
    assert "from ui_clone.splash_contract import absence_evidence, certify_absence" in script


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


_NO_OVERLAY = {
    "selector": None,
    "identity": None,
    "coverage": 0,
    "visible": False,
    "opacity": "0",
}
_WRAPPER = "body > div:nth-of-type(1)"


def _quiet_state(
    ts_ms: int,
    *,
    body_class: str = "",
    html_class: str = "",
    dom_length: int = 1200,
    covering: dict[str, float] | None = None,
    active_animations: int = 0,
    media_hash: str = "media",
    bookend: str | None = None,
    full_html: str | None = None,
) -> dict:
    """A sample in which the overlay probe returned its all-zero default."""
    return {
        "ts_ms": ts_ms,
        "hash": ts_ms + 1,
        "bodyClass": body_class,
        "htmlClass": html_class,
        "compositeDigest": f"{body_class}|{html_class}|{dom_length}|{media_hash}",
        "domLength": dom_length,
        "overlay": dict(_NO_OVERLAY),
        "covering": {_WRAPPER: 1.0} if covering is None else covering,
        "animationEvidence": {
            "activeCount": active_animations,
            "runningCount": active_animations,
            "samples": [],
        },
        "motionEvidence": {
            "changed": ts_ms > 0,
            "signals": ["active-animation"] if active_animations else [],
        },
        "mediaFingerprint": {"videos": [], "hash": media_hash},
        "fullHTML": full_html,
        "bookend": bookend,
    }


def _splash_events(ref_dir: Path) -> list[dict]:
    spec = json.loads((ref_dir / "state-structure-spec.json").read_text())
    return [event for event in spec["events"] if event["phase"] == "splash"]


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
        "overlay": dict(_NO_OVERLAY),
        "covering": {_WRAPPER: 1.0},
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


def test_entry_choreography_without_splash_is_certified_absent(tmp_path: Path) -> None:
    """navercorp.com/tech/innovation as captured: 13 states from entry
    animations and media readiness, html/body classes empty throughout, DOM
    121759 -> 124262, overlay never visible, settled at 6256ms on stable-2s.

    The `len(states) == 1` rule refused this and kept splash checks dispatched
    against a page with no splash on either side. The certificate must now
    stand, and state-structure-spec must not turn the 13 samples into a
    splash:page-load event.
    """
    ref_dir = tmp_path / "ref"
    samples = [
        (0, 121759, 0, "m0"), (127, 121759, 0, "m0"), (559, 122542, 8, "m0"),
        (676, 122548, 12, "m0"), (1021, 124130, 20, "m0"), (1146, 124130, 20, "m0"),
        (1489, 124130, 18, "m0"), (1604, 124130, 16, "m0"), (2187, 124130, 8, "m0"),
        (2655, 124130, 0, "m0"), (3573, 124130, 0, "m1"), (3804, 124130, 0, "m2"),
        (4154, 124262, 0, "m2"),
    ]
    states = [
        _quiet_state(
            ts,
            dom_length=length,
            active_animations=animations,
            media_hash=media,
            bookend="0ms" if index == 0 else "settled-same" if index == len(samples) - 1 else None,
            full_html="<html><body><div><main>tech</main></div></body></html>"
            if index in (0, len(samples) - 1) else None,
        )
        for index, (ts, length, animations, media) in enumerate(samples)
    ]
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=6256, reason="stable-2s")
    )

    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["captureMode"] == "pre-navigation"
    assert contract["detected"] is False
    assert contract["overlay"]["everVisible"] is False
    assert contract["capture"]["stateCount"] == 13
    assert contract["capture"]["timedOut"] is False
    assert contract["capture"]["absenceEvidence"] == {
        "overlayEverVisible": False,
        "coveringSurveyed": True,
        "coveringExits": [],
        "rootClassesRemoved": [],
        "structuralShift": False,
    }
    assert contract["capture"]["authoritativeNegative"] is True
    assert _splash_events(ref_dir) == []


def test_loading_class_lifecycle_the_probe_cannot_see_is_not_certified(tmp_path: Path) -> None:
    """The over-certification reproduction, verbatim: three states, body
    `is-loading` -> `loaded`, DOM 1000 -> 4000, every overlay record the
    all-zero default. Certifying this suppressed the splash:page-load event
    that state-structure-spec should have kept.
    """
    ref_dir = tmp_path / "ref"
    states = [
        _quiet_state(0, body_class="is-loading", dom_length=1000, bookend="0ms",
                     full_html="<html><body class='is-loading'><div>loading</div></body></html>"),
        _quiet_state(700, body_class="is-loading", dom_length=1000),
        _quiet_state(1400, body_class="loaded", dom_length=4000, bookend="settled",
                     full_html="<html><body class='loaded'><div><main>content</main></div></body></html>"),
    ]
    states[2]["structuralDelta"] = True
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=3400, reason="stable-2s")
    )

    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["detected"] is False
    assert contract["overlay"]["everVisible"] is False
    assert contract["capture"]["timedOut"] is False
    evidence = contract["capture"]["absenceEvidence"]
    assert evidence["rootClassesRemoved"] == ["body.is-loading"]
    assert evidence["structuralShift"] is True
    assert contract["capture"]["authoritativeNegative"] is False

    events = _splash_events(ref_dir)
    assert len(events) == 1
    event = events[0]
    assert event["id"] == "splash:page-load"
    assert event["status"] == "observed"
    assert (event["fromMs"], event["toMs"]) == (0, 1400)
    assert (event["bodyClassBefore"], event["bodyClassAfter"]) == ("is-loading", "loaded")
    assert (event["domLengthBefore"], event["domLengthAfter"]) == (1000, 4000)


def test_in_flow_loader_exit_is_not_certified(tmp_path: Path) -> None:
    """A static/relative full-viewport loader replaced in place: no overlay
    record, no root class, DOM length nearly flat. Its covering lifecycle is the
    only thing that says it was there."""
    ref_dir = tmp_path / "ref"
    states = [
        _quiet_state(0, covering={_WRAPPER: 1.0, "#loader": 1.0}, bookend="0ms",
                     full_html="<html><body><div><div id='loader'></div></div></body></html>"),
        _quiet_state(900, dom_length=1210, covering={_WRAPPER: 1.0}),
        _quiet_state(2900, dom_length=1210, covering={_WRAPPER: 1.0, "#hero": 0.9}, bookend="settled",
                     full_html="<html><body><div><section id='hero'></section></div></body></html>"),
    ]
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=2900, reason="stable-2s")
    )

    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["overlay"]["everVisible"] is False
    evidence = contract["capture"]["absenceEvidence"]
    assert evidence == {
        "overlayEverVisible": False,
        "coveringSurveyed": True,
        "coveringExits": ["#loader"],
        "rootClassesRemoved": [],
        "structuralShift": False,
    }
    assert contract["capture"]["authoritativeNegative"] is False
    assert len(_splash_events(ref_dir)) == 1


def test_payload_without_covering_survey_is_not_certified(tmp_path: Path) -> None:
    """A payload that never surveyed covering elements has not looked
    everywhere the rule reads; it fails closed rather than certifying by
    omission."""
    ref_dir = tmp_path / "ref"
    states = [_quiet_state(0, bookend="0ms", full_html="<html><body><div></div></body></html>")]
    del states[0]["covering"]
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=100, reason="no-change")
    )

    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["capture"]["absenceEvidence"]["coveringSurveyed"] is False
    assert contract["capture"]["authoritativeNegative"] is False


def test_setup_classes_added_after_first_sample_keep_the_certificate(tmp_path: Path) -> None:
    """`lenis lenis-smooth` / `is-ready` landing after the first sample is
    setup finishing, which pages with no splash do too."""
    ref_dir = tmp_path / "ref"
    states = [
        _quiet_state(0, bookend="0ms", full_html="<html><body><div></div></body></html>"),
        _quiet_state(400, html_class="lenis lenis-smooth", body_class="is-ready"),
        _quiet_state(2400, html_class="lenis lenis-smooth", body_class="is-ready", bookend="settled",
                     full_html="<html class='lenis lenis-smooth'><body class='is-ready'><div></div></body></html>"),
    ]
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=2400, reason="stable-2s")
    )

    proc = _run_capture_states(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["capture"]["absenceEvidence"]["rootClassesRemoved"] == []
    assert contract["capture"]["authoritativeNegative"] is True
    assert _splash_events(ref_dir) == []


def test_reuse_session_run_with_every_channel_empty_is_not_certified(tmp_path: Path) -> None:
    """Attached after navigation, the sampler may have missed the splash
    entirely; the same all-clear evidence does not certify."""
    ref_dir = tmp_path / "ref"
    states = [_quiet_state(0, bookend="0ms", full_html="<html><body><div></div></body></html>")]
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(states, duration_ms=100, reason="no-change")
    )

    proc = _run_capture_states(ref_dir, bin_dir, reuse_session=True)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["captureMode"] == "reuse-session"
    assert contract["capture"]["absenceEvidence"]["coveringSurveyed"] is True
    assert contract["capture"]["authoritativeNegative"] is False


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


def test_different_http_origin_fails_closed(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    payload = json.dumps({
        "success": True,
        "data": {"origin": "https://wrong.example", "result": {}},
    }).replace("'", "'\\''")
    bin_dir = _make_fake_agent_browser(tmp_path, payload)

    proc = _run_capture_states(ref_dir, bin_dir)

    assert proc.returncode == 3
    assert "expected origin" in proc.stderr
    assert not (ref_dir / "states" / "splash" / "summary.json").exists()


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
    close_index = next(i for i, line in enumerate(calls) if line.endswith(" close"))
    viewport_index = next(i for i, line in enumerate(calls) if line.endswith(" set viewport 1440 900"))
    target_index = next(
        i for i, line in enumerate(calls)
        if line.endswith(" open https://example.test --json") and "--init-script " in line
    )
    assert close_index < viewport_index < target_index
    assert not any("open about:blank" in line for line in calls)
    assert calls[target_index].index("--init-script ") < calls[target_index].index(" open ")
    assert "sleep 2" not in SCRIPT.read_text()


# ── the shipped sampler against a fake DOM, end to end ───────────────────────
#
# Payload-level tests above pin the python writer. These run the JS the shell
# actually installs (after threshold substitution) through
# tests/measure/splash_sampler_harness.js, so the sampler, the writer and the
# certificate are judged together on what a page does, not on what a payload
# says it did.

_SAMPLER_HARNESS = REPO_ROOT / "tests" / "measure" / "splash_sampler_harness.js"


def _make_dom_driven_agent_browser(tmp_path: Path, scenario: str) -> Path:
    """A fake `agent-browser` whose `eval` runs the installed init script
    against the named fake-DOM scenario and returns the sampler's payload."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "init=''; cmd=''\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    --init-script) init="$2"; shift 2 ;;\n'
        "    --session) shift 2 ;;\n"
        "    --json) shift ;;\n"
        '    open|eval) cmd="$1"; shift; break ;;\n'
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        'if [ "$cmd" = "eval" ]; then\n'
        f'  exec node "{_SAMPLER_HARNESS}" "$init" "{scenario}"\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return bin_dir


def _run_sampler_scenario(tmp_path: Path, scenario: str) -> dict:
    ref_dir = tmp_path / "ref"
    proc = _run_capture_states(ref_dir, _make_dom_driven_agent_browser(tmp_path, scenario))
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    contract: dict = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    return contract


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sampler_sees_a_half_viewport_in_flow_loader_leave(tmp_path: Path) -> None:
    """A static loader covering 55% of the viewport is a splash to the
    lifecycle probe (>= 45%). The installed sampler must record it and the
    writer must refuse absence when it leaves."""
    contract = _run_sampler_scenario(tmp_path, "half-viewport-loader-exits")

    evidence = contract["capture"]["absenceEvidence"]
    assert evidence["coveringSurveyed"] is True
    assert "#loader" in evidence["coveringExits"]
    assert contract["capture"]["authoritativeNegative"] is False


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sampler_sees_a_loading_class_that_lands_after_the_first_sample(tmp_path: Path) -> None:
    contract = _run_sampler_scenario(tmp_path, "deferred-loading-class")

    assert contract["capture"]["absenceEvidence"]["rootClassesRemoved"] == ["body.is-loading"]
    assert contract["capture"]["authoritativeNegative"] is False


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sampler_certifies_entry_choreography_with_no_splash(tmp_path: Path) -> None:
    """Persistent wrappers, a hero reflowing 76% -> 72%, a smooth-scroll class
    landing late, DOM +2%: the certificate must stand, or it is useless."""
    contract = _run_sampler_scenario(tmp_path, "entry-choreography-no-splash")

    assert contract["capture"]["timedOut"] is False
    assert contract["capture"]["absenceEvidence"] == {
        "overlayEverVisible": False,
        "coveringSurveyed": True,
        "coveringExits": [],
        "rootClassesRemoved": [],
        "structuralShift": False,
    }
    assert contract["capture"]["authoritativeNegative"] is True


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.parametrize("scenario", ["inflow-intro-shrinks-to-35", "inflow-intro-shrinks-to-10"])
def test_sampler_sees_an_in_flow_intro_shrink_out_of_the_viewport(
    tmp_path: Path, scenario: str
) -> None:
    """An id-less in-flow intro covering the whole viewport that settles to a
    strip is a curtain leaving. Where it settles must not decide the verdict:
    an exit threshold pinned to the probe's candidate floor (20%) would call a
    settle at 35% "still present" and certify the page as splash-free, while
    the probe itself would report overlay-never-exited. Nothing else in these
    scenarios moves, so `covering` is the only channel that can refuse."""
    contract = _run_sampler_scenario(tmp_path, scenario)

    evidence = contract["capture"]["absenceEvidence"]
    assert evidence["coveringSurveyed"] is True
    assert evidence["rootClassesRemoved"] == []
    assert evidence["structuralShift"] is False
    assert evidence["coveringExits"], "the intro shrinking out of the viewport must be an exit"
    assert contract["capture"]["authoritativeNegative"] is False


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sampler_sees_a_class_only_preloader_leave_behind_an_id_less_wrapper(tmp_path: Path) -> None:
    """The covering identity must be the node, not its DOM position. A
    class-only preloader `body > div:nth-of-type(1)` is removed and the hidden
    wrapper behind it is shown; keyed by position the wrapper inherits the
    preloader's key, the map records no exit, and a page with a loader
    certifies absence. The `#preloader` control below is the same page with
    an id, which is what isolates the defect to identity aliasing."""
    contract = _run_sampler_scenario(tmp_path, "class-only-preloader-aliased")

    evidence = contract["capture"]["absenceEvidence"]
    assert evidence["coveringSurveyed"] is True
    assert evidence["coveringExits"], "the removed preloader must register as a covering exit"
    assert contract["capture"]["authoritativeNegative"] is False


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sampler_sees_an_id_preloader_leave_behind_an_id_less_wrapper(tmp_path: Path) -> None:
    contract = _run_sampler_scenario(tmp_path, "id-preloader-control")

    assert "#preloader" in contract["capture"]["absenceEvidence"]["coveringExits"]
    assert contract["capture"]["authoritativeNegative"] is False


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sampler_sees_a_deeply_nested_class_only_preloader_leave(tmp_path: Path) -> None:
    """Same defect class, other edge: the positional path is bounded at eight
    levels, so a class-only preloader nested deeper than that had NO identity
    at all, was never recorded, and its exit was invisible to the survey."""
    contract = _run_sampler_scenario(tmp_path, "deep-class-only-preloader")

    evidence = contract["capture"]["absenceEvidence"]
    assert evidence["coveringExits"], "a preloader below the path depth bound must still register its exit"
    assert contract["capture"]["authoritativeNegative"] is False


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_covering_thresholds_are_not_read_from_the_working_directory(tmp_path: Path) -> None:
    """The sampler's thresholds come from the repo's ui_clone.splash_contract.
    A `ui_clone/` package in the caller's cwd (an impl tree, a scratch dir)
    must not shadow it: with decoy thresholds of 99%/98% a 55% loader would
    never be recorded and the page would certify absence."""
    decoy = tmp_path / "decoy-cwd" / "ui_clone"
    decoy.mkdir(parents=True)
    (decoy / "__init__.py").write_text("", encoding="utf-8")
    (decoy / "splash_contract.py").write_text(
        "COVERING_ENTER = 0.99\nCOVERING_EXIT = 0.98\nCOVERING_RECORD_FLOOR = 0.98\n",
        encoding="utf-8",
    )
    ref_dir = tmp_path / "ref"
    proc = _run_capture_states(
        ref_dir,
        _make_dom_driven_agent_browser(tmp_path, "half-viewport-loader-exits"),
        cwd=decoy.parent,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())

    assert "#loader" in contract["capture"]["absenceEvidence"]["coveringExits"]
    assert contract["capture"]["authoritativeNegative"] is False


# ── in-place replacement of an id-less covering element ──────────────────────
#
# Keying an id-less element by node closed the aliasing hole above, but a
# framework re-mount also throws a node away and mounts a fresh one in its
# place. That is not a splash leaving. The rule: a fresh node inherits the
# identity of the serial-keyed covering node the immediately preceding survey
# recorded at the same nth-of-type path, if that node is now detached and both
# carry the same tag and classes. Everything else still records an exit.


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sampler_certifies_a_hydration_remount_of_an_id_less_wrapper(tmp_path: Path) -> None:
    """React/Next hydration mismatch: the server-rendered full-viewport
    `div.app` is replaced within one poll by a fresh `div.app` node. No splash;
    the certificate must stand."""
    contract = _run_sampler_scenario(tmp_path, "hydration-remount-same-class")

    evidence = contract["capture"]["absenceEvidence"]
    assert evidence["coveringSurveyed"] is True
    assert evidence["coveringExits"] == []
    assert contract["capture"]["authoritativeNegative"] is True


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sampler_certifies_a_skeleton_replaced_in_place_by_its_content(tmp_path: Path) -> None:
    """A `div.page` skeleton covering 50% of the viewport is replaced within
    one poll by a fresh `div.page` holding the content. No splash."""
    contract = _run_sampler_scenario(tmp_path, "skeleton-replaced-in-place")

    assert contract["capture"]["absenceEvidence"]["coveringExits"] == []
    assert contract["capture"]["authoritativeNegative"] is True


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sampler_refuses_a_loader_replaced_in_place_by_content_of_another_class(tmp_path: Path) -> None:
    """Same path, same tag, different class: `div.loader` replaced by
    `div.content` is a loading shell leaving, not a re-mount. Keyed by DOM
    position alone (the original sampler) this aliased and certified."""
    contract = _run_sampler_scenario(tmp_path, "loader-replaced-by-content-different-class")

    evidence = contract["capture"]["absenceEvidence"]
    assert evidence["structuralShift"] is False, "the refusal must isolate to the covering identity"
    assert evidence["coveringExits"], "the replaced loader must register as a covering exit"
    assert contract["capture"]["authoritativeNegative"] is False


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sampler_refuses_a_same_class_remount_more_than_one_poll_apart(tmp_path: Path) -> None:
    """`div.app` removed at 500ms, a fresh `div.app` appended at 800ms: the
    viewport was uncovered for several polls in between, which is what a loader
    leaving looks like. Inheritance reaches back one survey and no further."""
    contract = _run_sampler_scenario(tmp_path, "remount-two-polls-apart")

    evidence = contract["capture"]["absenceEvidence"]
    assert evidence["structuralShift"] is False, "the refusal must isolate to the covering identity"
    assert evidence["coveringExits"], "the gap between removal and re-mount must register as an exit"
    assert contract["capture"]["authoritativeNegative"] is False


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


def test_owned_session_preserves_launch_identity_and_emulation(tmp_path: Path) -> None:
    """Model native launch hashing: changing init scripts resets page/emulation."""
    import sys

    fake = tmp_path / "agent-browser"
    fake.write_text(
        f"#!{sys.executable}\n" + r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
args = args[2:]
script = None
if args[:1] == ['--init-script']:
    script, args = args[1], args[2:]
    assert Path(script).is_file(), 'init script removed before close'
path = Path(os.environ['MODEL_STATE'])
state = json.loads(path.read_text()) if path.exists() else {}
# Native explicit launch options compare the full hash before each command.
key = [script, os.environ.get('AGENT_BROWSER_ARGS')]
if not state or ((script or key[1]) and state['key'] != key):
    state = {'key': key, 'url': 'about:blank', 'viewport': [1280, 720], 'media': None}
if args[0] == 'close':
    path.unlink(missing_ok=True)
    sys.exit(0)
if args[:2] == ['set', 'viewport']:
    state['viewport'] = [int(v) for v in args[2:4]]
if args[:2] == ['set', 'media']:
    state['media'] = args[2]
if args[0] == 'open':
    assert state['viewport'] == [1440, 900], state
    assert state['media'] == 'dark', state
    assert script and '__UI_CLONE_SPLASH_CAPTURE__' in Path(script).read_text()
    state['url'] = args[1]
    print(json.dumps({'success': True, 'data': {'url': state['url']}}))
if args[0] == 'eval':
    assert state['url'] == 'https://example.test', state
    assert state['viewport'] == [1440, 900] and state['media'] == 'dark', state
    print(json.dumps({'success': True, 'data': {'origin': state['url'], 'result': {
        'states': [], 'durationMs': 0, 'polls': 0, 'timedOut': False, 'reason': 'no-change'
    }}}))
path.write_text(json.dumps(state))
''', encoding="utf-8")
    fake.chmod(0o755)
    for launch_args in (None, "--disable-dev-shm-usage"):
        env = os.environ.copy()
        env.update(PATH=f"{tmp_path}:{env['PATH']}", MODEL_STATE=str(tmp_path / "model.json"),
                   AGENT_BROWSER_COLOR_SCHEME="dark")
        env.pop("AGENT_BROWSER_ARGS", None)
        if launch_args:
            env["AGENT_BROWSER_ARGS"] = launch_args
        proc = subprocess.run(
            ["bash", str(SCRIPT), "https://example.test", "owned", str(tmp_path / "ref")],
            capture_output=True, text=True, env=env, timeout=20,
        )
        assert proc.returncode == 0, proc.stderr
        assert not (tmp_path / "model.json").exists(), 'owned browser must be closed'
