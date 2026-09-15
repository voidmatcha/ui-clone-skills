import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "skills" / "visual-debug" / "scripts" / "lib" / "splash-lifecycle-probe.js"
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "splash-lifecycle-check.sh"


def _node_eval(source: str) -> dict[str, Any]:
    proc = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
    return cast(dict[str, Any], json.loads(proc.stdout))


def _compare(ref_samples: list[dict], impl_samples: list[dict]) -> dict:
    return _node_eval(
        f"""
        const probe = require({json.dumps(str(LIB))});
        const verdict = probe.compareLifecycles(
          {json.dumps(ref_samples)},
          {json.dumps(impl_samples)}
        );
        process.stdout.write(JSON.stringify(verdict));
        """
    )


def _install_sampler_with_window(wait_ms: int) -> dict[str, Any]:
    return _node_eval(
        f"""
        const probe = require({json.dumps(str(LIB))});
        global.window = {{
          document: null,
          innerWidth: 1280,
          innerHeight: 800,
          performance: {{ now: () => 0 }},
          setInterval: () => 7,
          clearInterval: () => {{}},
        }};
        global.document = {{
          documentElement: {{ nodeType: 1 }},
          body: {{ innerText: "" }},
          images: [],
          readyState: "loading",
          querySelectorAll: () => [],
        }};
        window.document = global.document;
        window.__UI_CLONE_SPLASH_LIFECYCLE_WINDOW_MS__ = {wait_ms};
        probe.installSampler(window);
        process.stdout.write(JSON.stringify(window.__uiCloneSplashLifecycleResult()));
        """
    )


def _selector_for_class_name(class_name_expr: str) -> dict[str, Any]:
    return _node_eval(
        f"""
        const probe = require({json.dumps(str(LIB))});
        global.document = {{
          documentElement: {{ nodeType: 1 }},
          body: null,
        }};
        const el = {{
          nodeType: 1,
          id: "",
          tagName: "svg",
          className: {class_name_expr},
          parentElement: null,
          previousElementSibling: null,
        }};
        process.stdout.write(JSON.stringify({{selector: probe.selectorFor(el)}}));
        """
    )


def _choose_best_overlay(candidates: list[dict], viewport: dict | None = None) -> dict | None:
    return _node_eval(
        f"""
        const probe = require({json.dumps(str(LIB))});
        const best = probe.chooseBestOverlay(
          {json.dumps(candidates)},
          {json.dumps(viewport or {"width": 1280, "height": 800})}
        );
        process.stdout.write(JSON.stringify(best));
        """
    )


def _sample(t: int, *, present: bool, signature: str = "", y: int = 0) -> dict:
    overlay = None
    if present:
        overlay = {
            "selector": "#splash",
            "signature": signature,
            "rect": {"x": 0, "y": y, "width": 1280, "height": 800},
            "opacity": 1,
            "transform": f"matrix(1, 0, 0, 1, 0, {y})",
        }
    return {"t": t, "overlay": overlay, "viewportMotion": 0.0}


def test_choose_best_overlay_rejects_offscreen_oversized_sticky_section() -> None:
    real_splash = {
        "selector": "#real-splash",
        "signature": "loading",
        "rect": {"x": 0, "y": 0, "width": 1280, "height": 800},
        "opacity": 1,
        "transform": "",
        "zIndex": 100000,
        "position": "fixed",
    }
    offscreen_sticky = {
        "selector": "div.dga-module__LrmiHG__resources_sticky",
        "signature": "Resources",
        "rect": {"x": 0, "y": 10706, "width": 1280, "height": 900},
        "opacity": 1,
        "transform": "",
        "zIndex": 0,
        "position": "sticky",
    }

    best = _choose_best_overlay([offscreen_sticky, real_splash])

    assert best is not None
    assert best["selector"] == "#real-splash"
    assert best["coverageRatio"] == 1


def test_choose_best_overlay_rejects_fullscreen_sticky_surface() -> None:
    sticky_surface = {
        "selector": ".resources-sticky",
        "signature": "Resources",
        "rect": {"x": 0, "y": 0, "width": 1280, "height": 800},
        "opacity": 1,
        "transform": "",
        "zIndex": 100,
        "position": "sticky",
    }

    assert _choose_best_overlay([sticky_surface]) is None


def _overlay_sample(t: int, overlay: dict | None) -> dict:
    return {"t": t, "overlay": overlay, "viewportMotion": 0.0}


def _overlay(
    selector: str,
    *,
    signature: str = "",
    y: int = 0,
    height: int = 800,
    coverage_ratio: float | None = None,
    dom_path: str | None = None,
    node_identity: str | None = None,
) -> dict:
    return {
        "selector": selector,
        **({"domPath": dom_path} if dom_path else {}),
        **({"nodeIdentity": node_identity} if node_identity else {}),
        **({"coverageRatio": coverage_ratio} if coverage_ratio is not None else {}),
        "signature": signature,
        "rect": {"x": 0, "y": y, "width": 1280, "height": height},
        "opacity": 1,
        "transform": f"matrix(1, 0, 0, 1, 0, {y})",
    }


def test_compare_lifecycles_passes_when_ref_and_impl_mount_move_and_exit() -> None:
    ref = [
        _sample(0, present=True, signature="loading", y=0),
        _sample(120, present=True, signature="loading", y=-40),
        _sample(360, present=False),
    ]
    impl = [
        _sample(0, present=True, signature="loading", y=0),
        _sample(140, present=True, signature="loading", y=-35),
        _sample(380, present=False),
    ]

    verdict = _compare(ref, impl)

    assert verdict["status"] == "pass"
    assert verdict["ref"]["mounted"] is True
    assert verdict["ref"]["phaseChanged"] is True
    assert verdict["ref"]["exited"] is True
    assert verdict["impl"]["mounted"] is True
    assert verdict["impl"]["phaseChanged"] is True
    assert verdict["impl"]["exited"] is True


def test_compare_lifecycles_fails_missing_static_and_stuck_impl() -> None:
    ref = [
        _sample(0, present=True, signature="loading", y=0),
        _sample(100, present=True, signature="loading", y=-50),
        _sample(320, present=False),
    ]

    absent = [_sample(0, present=False), _sample(120, present=False), _sample(320, present=False)]
    static = [
        _sample(0, present=True, signature="loading", y=0),
        _sample(100, present=True, signature="loading", y=0),
        _sample(320, present=False),
    ]
    stuck = [
        _sample(0, present=True, signature="loading", y=0),
        _sample(100, present=True, signature="loading", y=-50),
        _sample(320, present=True, signature="loading", y=-50),
    ]

    assert _compare(ref, absent)["status"] == "fail"
    assert "impl-overlay-absent" in _compare(ref, absent)["violations"]
    assert "impl-overlay-static" in _compare(ref, static)["violations"]
    assert "impl-overlay-never-exited" in _compare(ref, stuck)["violations"]


def test_compare_lifecycles_allows_static_reference_overlay_that_exits() -> None:
    ref = [
        _overlay_sample(0, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(100, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(300, None),
    ]

    verdict = _compare(ref, ref)

    assert verdict["status"] == "pass"
    assert verdict["ref"]["mounted"] is True
    assert verdict["ref"]["phaseChanged"] is False
    assert verdict["ref"]["exited"] is True


def test_compare_lifecycles_still_fails_static_impl_when_reference_moves() -> None:
    ref = [
        _overlay_sample(0, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(100, _overlay("#splash", signature="loading", y=-48)),
        _overlay_sample(300, None),
    ]
    impl = [
        _overlay_sample(0, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(100, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(300, None),
    ]

    verdict = _compare(ref, impl)

    assert verdict["status"] == "fail"
    assert "impl-overlay-static" in verdict["violations"]


def test_compare_lifecycles_fails_static_overlay_that_never_exits() -> None:
    samples = [
        _overlay_sample(0, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(100, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(300, _overlay("#splash", signature="loading", y=0)),
    ]

    verdict = _compare(samples, samples)

    assert verdict["status"] == "fail"
    assert "ref-overlay-never-exited" in verdict["violations"]
    assert "impl-overlay-never-exited" in verdict["violations"]


def test_compare_lifecycles_treats_replacement_overlay_as_splash_exit() -> None:
    """Overlay A can exit into unrelated fullscreen/sticky overlay B.

    The lifecycle proof is about the first-load splash identity, not any later
    overlay-like page surface. Once the initial splash candidate disappears or
    is replaced by a different stable identity, the splash has exited.
    """
    splash_a0 = _overlay("#splash", signature="loading", y=0)
    splash_a1 = _overlay("#splash", signature="loading", y=-48)
    unrelated_b = _overlay(".resources-sticky", signature="Resources", y=0)
    samples = [
        _overlay_sample(0, splash_a0),
        _overlay_sample(100, splash_a1),
        _overlay_sample(260, unrelated_b),
        _overlay_sample(6400, unrelated_b),
    ]

    verdict = _compare(samples, samples)

    assert verdict["status"] == "pass"
    assert verdict["ref"]["mounted"] is True
    assert verdict["ref"]["phaseChanged"] is True
    assert verdict["ref"]["exited"] is True
    assert verdict["ref"]["lastPresentMs"] == 100


def test_compare_lifecycles_treats_classless_sibling_overlay_as_replacement() -> None:
    """Classless fullscreen siblings must not share a bare `selector:div` identity."""
    splash_a0 = _overlay(
        "div",
        signature="loading",
        y=0,
        dom_path="body>div:nth-of-type(1)",
    )
    splash_a1 = _overlay(
        "div",
        signature="loading",
        y=-48,
        dom_path="body>div:nth-of-type(1)",
    )
    unrelated_b = _overlay(
        "div",
        signature="modal",
        y=0,
        dom_path="body>div:nth-of-type(2)",
    )
    samples = [
        _overlay_sample(0, splash_a0),
        _overlay_sample(100, splash_a1),
        _overlay_sample(260, unrelated_b),
    ]

    verdict = _compare(samples, samples)

    assert verdict["status"] == "pass"
    assert verdict["ref"]["identity"] == "path:body>div:nth-of-type(1)"
    assert verdict["ref"]["exited"] is True
    assert verdict["ref"]["lastPresentMs"] == 100


def test_compare_lifecycles_fails_same_dom_overlay_that_reappears_after_exit() -> None:
    """Class/selector changes on one DOM node must not hide final reappearance."""
    ref = [
        _overlay_sample(
            0,
            _overlay(
                "div.loading",
                signature="loading",
                y=0,
                dom_path="body>div:nth-of-type(1)",
                node_identity="node:1",
            ),
        ),
        _overlay_sample(
            120,
            _overlay(
                "div.loading",
                signature="loading",
                y=-48,
                dom_path="body>div:nth-of-type(1)",
                node_identity="node:1",
            ),
        ),
        _overlay_sample(
            260,
            None,
        ),
        _overlay_sample(
            3600,
            _overlay(
                "div.loaded",
                signature="done",
                y=-96,
                dom_path="body>div:nth-of-type(1)",
                node_identity="node:1",
            ),
        ),
    ]
    impl = [ref[0], ref[1], _overlay_sample(260, None)]

    verdict = _compare(ref, impl)

    assert verdict["status"] == "fail"
    assert verdict["ref"]["identity"] == "node:node:1"
    assert "ref-overlay-reappeared" in verdict["violations"]


def test_compare_lifecycles_treats_new_node_at_same_dom_path_as_replacement() -> None:
    """A removed splash node can be replaced by another element at the same nth-of-type path."""
    samples = [
        _overlay_sample(
            0,
            _overlay("div.loading", signature="loading", y=0, dom_path="body>div:nth-of-type(1)", node_identity="node:1"),
        ),
        _overlay_sample(
            100,
            _overlay(
                "div.loading",
                signature="loading",
                y=-48,
                dom_path="body>div:nth-of-type(1)",
                node_identity="node:1",
            ),
        ),
        _overlay_sample(240, None),
        _overlay_sample(
            360,
            _overlay("div.modal", signature="modal", y=0, dom_path="body>div:nth-of-type(1)", node_identity="node:2"),
        ),
    ]

    verdict = _compare(samples, samples)

    assert verdict["status"] == "pass"
    assert verdict["ref"]["identity"] == "node:node:1"
    assert verdict["ref"]["exited"] is True
    assert verdict["ref"]["reappeared"] is False


def test_compare_lifecycles_fails_impl_duration_that_collapses_ref_splash() -> None:
    ref = [
        _overlay_sample(0, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(1500, _overlay("#splash", signature="loading", y=-48)),
        _overlay_sample(3000, None),
    ]
    impl = [
        _overlay_sample(0, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(100, _overlay("#splash", signature="loading", y=-48)),
        _overlay_sample(150, None),
    ]

    verdict = _compare(ref, impl)

    assert verdict["status"] == "fail"
    assert "impl-duration-ratio-mismatch" in verdict["violations"]


def test_compare_lifecycles_fails_impl_duration_that_overstays_ref_splash() -> None:
    ref = [
        _overlay_sample(0, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(500, _overlay("#splash", signature="loading", y=-48)),
        _overlay_sample(1000, None),
    ]
    impl = [
        _overlay_sample(0, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(2500, _overlay("#splash", signature="loading", y=-48)),
        _overlay_sample(4000, None),
    ]

    verdict = _compare(ref, impl)

    assert verdict["status"] == "fail"
    assert "impl-duration-ratio-mismatch" in verdict["violations"]


def test_compare_lifecycles_fails_short_measurable_ref_duration_overstay() -> None:
    ref = [
        _overlay_sample(0, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(150, _overlay("#splash", signature="loading", y=-48)),
        _overlay_sample(200, None),
    ]
    impl = [
        _overlay_sample(0, _overlay("#splash", signature="loading", y=0)),
        _overlay_sample(1000, _overlay("#splash", signature="loading", y=-48)),
        _overlay_sample(1100, None),
    ]

    verdict = _compare(ref, impl)

    assert verdict["status"] == "fail"
    assert "impl-duration-ratio-mismatch" in verdict["violations"]


def test_compare_lifecycles_keeps_realfood_duration_ratio_inside_tolerance() -> None:
    ref = [
        _overlay_sample(176, _overlay("div.intro-animation-module__0093MG__overlay", y=0)),
        _overlay_sample(1000, _overlay("div.intro-animation-module__0093MG__overlay", y=-40)),
        _overlay_sample(2006, None),
    ]
    impl = [
        _overlay_sample(121, _overlay("div.intro-animation-module__0093MG__overlay", y=0)),
        _overlay_sample(900, _overlay("div.intro-animation-module__0093MG__overlay", y=-40)),
        _overlay_sample(1606, None),
    ]

    verdict = _compare(ref, impl)

    assert verdict["status"] == "pass"


def test_compare_lifecycles_fails_impl_overlay_with_tiny_initial_coverage() -> None:
    ref = [
        _overlay_sample(0, _overlay("#splash", y=0, coverage_ratio=1)),
        _overlay_sample(100, _overlay("#splash", y=-48, coverage_ratio=0.92)),
        _overlay_sample(300, None),
    ]
    impl = [
        _overlay_sample(0, _overlay("#splash", y=0, height=160, coverage_ratio=0.2)),
        _overlay_sample(100, _overlay("#splash", y=-48, height=160, coverage_ratio=0.18)),
        _overlay_sample(300, None),
    ]

    verdict = _compare(ref, impl)

    assert verdict["status"] == "fail"
    assert "impl-coverage-too-low" in verdict["violations"]


def test_compare_lifecycles_allows_partial_offscreen_coverage_during_exit_tracking() -> None:
    ref = [
        _overlay_sample(0, _overlay("#splash", y=0, coverage_ratio=1)),
        _overlay_sample(100, _overlay("#splash", y=-640, coverage_ratio=0.2)),
        _overlay_sample(180, None),
    ]

    verdict = _compare(ref, ref)

    assert verdict["status"] == "pass"
    assert verdict["ref"]["phaseChanged"] is True


def test_install_sampler_uses_configured_window_ms() -> None:
    result = _install_sampler_with_window(6500)

    assert result["sampleWindowMs"] == 6500


def test_selector_for_handles_svg_animated_class_name() -> None:
    result = _selector_for_class_name('{baseVal: "splash-icon active"}')

    assert result["selector"] == "svg.splash-icon.active"


def test_compare_lifecycles_does_not_count_background_motion_as_splash_proof() -> None:
    ref = [
        _sample(0, present=True, signature="loading", y=0),
        _sample(100, present=True, signature="loading", y=-50),
        _sample(320, present=False),
    ]
    impl = [
        {"t": 0, "overlay": None, "viewportMotion": 0.5},
        {"t": 100, "overlay": None, "viewportMotion": 0.9},
        {"t": 320, "overlay": None, "viewportMotion": 0.7},
    ]

    verdict = _compare(ref, impl)

    assert verdict["status"] == "fail"
    assert "impl-overlay-absent" in verdict["violations"]
    assert "background-motion-is-not-splash-proof" in verdict["violations"]


def test_shell_check_uses_pre_navigation_init_script() -> None:
    body = SCRIPT.read_text(encoding="utf-8")

    assert "--init-script" in body
    assert "splash-lifecycle-probe.js" in body
    assert "agent-browser --session" in body


def test_shell_check_creates_ref_dir_before_probe_missing_artifact(tmp_path: Path) -> None:
    script_copy = tmp_path / "splash-lifecycle-check.sh"
    script_copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    script_copy.chmod(0o755)
    ref_dir = tmp_path / "missing-ref-dir"

    proc = subprocess.run(
        [str(script_copy), "missing-probe", "https://ref.example", "https://impl.example", str(ref_dir)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    artifact = json.loads((ref_dir / "splash-lifecycle.json").read_text(encoding="utf-8"))
    assert proc.returncode == 1
    assert artifact["status"] == "fail"
    assert artifact["reason"] == "probe-script-missing"


def test_shell_check_rejects_untrusted_wait_ms_before_agent_browser(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    marker = tmp_path / "agent-browser-ran"
    fake_bin.mkdir()
    fake_agent = fake_bin / "agent-browser"
    fake_agent.write_text(
        f"""#!/usr/bin/env bash
printf 'ran\\n' >> {str(marker)!r}
if [ "${{@: -1}}" = "close" ]; then
  exit 0
fi
printf '{{"schemaVersion":1,"samples":[]}}\\n'
""",
        encoding="utf-8",
    )
    fake_agent.chmod(0o755)

    for index, wait_ms in enumerate((
        "4000;throw new Error('owned')",
        "9" * 80,
    )):
        ref_dir = tmp_path / f"ref-{index}"
        proc = subprocess.run(
            [str(SCRIPT), "malicious-wait", "https://ref.example", "https://impl.example", str(ref_dir)],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                "UI_CLONE_SPLASH_LIFECYCLE_WAIT_MS": wait_ms,
            },
            capture_output=True,
            text=True,
            timeout=20,
        )

        artifact = json.loads((ref_dir / "splash-lifecycle.json").read_text(encoding="utf-8"))
        assert proc.returncode == 1
        assert artifact["status"] == "fail"
        assert artifact["reason"] == "invalid-wait-ms"
    assert not marker.exists()


# ── a reference with no overlay: a FAIL that names the reference ────────────
#
# The check is dispatched by detectors that are deliberately biased toward
# false positives (splash-extraction.md: `hasPreloader` fires on any one of
# three signals) and by `summary.json polls > 1`. When the reference has no
# overlay the probe can see, `ref-overlay-absent` is a statement about the
# reference, not about the implementation, and nothing the implementer does
# can clear it. It stays a FAIL. The Phase A certificate in
# states/splash/contract.json is read only to say which reference measurement
# the FAIL is about: it comes from a sampler that enumerates elements exactly
# as this probe does, so the two share their blind spots (a pseudo-element
# curtain such as html:not(.loaded)::before, a body background over
# opacity-gated content) and their agreement is one measurement counted twice,
# not corroboration.


def _absent_samples(count: int = 6, *, motion: float = 0.0) -> list[dict[str, Any]]:
    return [{"t": index * 50, "overlay": None, "viewportMotion": motion, "readyState": "complete"} for index in range(count)]


def _mounted_samples() -> list[dict[str, Any]]:
    overlay = {
        "selector": "#intro",
        "signature": "intro",
        "rect": {"x": 0, "y": 0, "width": 1280, "height": 800},
        "coverageRatio": 1.0,
        "opacity": 1,
        "transform": "",
    }
    return [
        {"t": 0, "overlay": overlay, "viewportMotion": 0.0},
        {"t": 50, "overlay": dict(overlay, opacity=0.6), "viewportMotion": 0.0},
        {"t": 400, "overlay": None, "viewportMotion": 0.0},
        {"t": 450, "overlay": None, "viewportMotion": 0.0},
    ]


def _fake_agent_browser_by_side(
    tmp_path: Path, ref_samples: list[dict[str, Any]], impl_samples: list[dict[str, Any]], *, ref_open_rc: int = 0
) -> Path:
    """A fake `agent-browser` that answers `eval` per side: the check names its
    sessions `<session>-ref` / `<session>-impl`."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "ref.json").write_text(json.dumps({"schemaVersion": 1, "samples": ref_samples}), encoding="utf-8")
    (fake_bin / "impl.json").write_text(json.dumps({"schemaVersion": 1, "samples": impl_samples}), encoding="utf-8")
    fake = fake_bin / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "session=''; cmd=''\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    --session) session="$2"; shift 2 ;;\n'
        "    --init-script) shift 2 ;;\n"
        "    --json) shift ;;\n"
        '    open|eval|wait|close) cmd="$1"; shift; break ;;\n'
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        'case "$session" in *-ref) side=ref ;; *-impl) side=impl ;; *) side=none ;; esac\n'
        f'if [ "$cmd" = "open" ] && [ "$side" = "ref" ]; then exit {ref_open_rc}; fi\n'
        'if [ "$cmd" = "eval" ]; then\n'
        f'  cat "{fake_bin}/$side.json"\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake_bin


def _write_contract(ref_dir: Path, *, certified: bool) -> None:
    splash = ref_dir / "states" / "splash"
    splash.mkdir(parents=True, exist_ok=True)
    (splash / "contract.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "captureMode": "pre-navigation",
            "detected": False,
            "overlay": {"everVisible": False, "maxCoverage": 0, "exitObserved": False},
            "capture": {
                "stateCount": 13,
                "timedOut": not certified,
                "reason": "stable-2s" if certified else "wall-clock-cap",
                "authoritativeNegative": certified,
            },
        }),
        encoding="utf-8",
    )


def _run_check(
    tmp_path: Path, fake_bin: Path, ref_dir: Path, *, cwd: Path | None = None
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    proc = subprocess.run(
        [str(SCRIPT), "no-overlay", "https://ref.example", "https://impl.example", str(ref_dir)],
        cwd=cwd or ROOT,
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    artifact = json.loads((ref_dir / "splash-lifecycle.json").read_text(encoding="utf-8"))
    return proc, artifact


def test_ref_without_overlay_fails_even_when_phase_a_certifies(tmp_path: Path) -> None:
    """Both instruments saw nothing and the certificate is true: that is still
    `ref-overlay-absent`, because both instruments enumerate elements and a
    curtain neither can see (html:not(.loaded)::before) certifies on the one
    and passes the other. The FAIL stands; the artifact says the certificate
    agreed, names the reference, and points at the detector that dispatched
    the check instead of at the implementation."""
    ref_dir = tmp_path / "ref"
    _write_contract(ref_dir, certified=True)
    fake_bin = _fake_agent_browser_by_side(tmp_path, _absent_samples(), _absent_samples(motion=3.0))

    proc, artifact = _run_check(tmp_path, fake_bin, ref_dir)

    assert artifact["status"] == "fail", artifact
    assert "ref-overlay-absent" in artifact["violations"]
    assert artifact.get("reason") != "ref-overlay-absent-certified"
    assert artifact["refAbsence"]["certified"] is True
    guidance = artifact["refAbsence"]["guidance"].lower()
    assert "reference" in guidance
    assert "does not clear" in guidance
    assert proc.returncode == 1


def test_impl_overlay_on_a_certified_no_splash_ref_fails(tmp_path: Path) -> None:
    """An implementation that mounts a splash the reference never had fails on
    the reference-side violation like any other no-overlay reference; the
    certificate changes nothing about the verdict."""
    ref_dir = tmp_path / "ref"
    _write_contract(ref_dir, certified=True)
    fake_bin = _fake_agent_browser_by_side(tmp_path, _absent_samples(), _mounted_samples())

    proc, artifact = _run_check(tmp_path, fake_bin, ref_dir)

    assert artifact["status"] == "fail"
    assert "ref-overlay-absent" in artifact["violations"]
    assert proc.returncode == 1


def test_ref_without_overlay_stays_failed_when_phase_a_does_not_certify(tmp_path: Path) -> None:
    """The capture timed out (or saw a loading lifecycle the probe cannot
    classify). The FAIL stands and names the re-capture that would settle
    what the reference holds."""
    ref_dir = tmp_path / "ref"
    _write_contract(ref_dir, certified=False)
    fake_bin = _fake_agent_browser_by_side(tmp_path, _absent_samples(), _absent_samples())

    proc, artifact = _run_check(tmp_path, fake_bin, ref_dir)

    assert artifact["status"] == "fail"
    assert "ref-overlay-absent" in artifact["violations"]
    assert artifact["refAbsence"]["certified"] is False
    assert "reference" in artifact["refAbsence"]["guidance"].lower()
    assert "capture-states.sh" in artifact["refAbsence"]["guidance"]
    assert proc.returncode == 1


def test_certificate_lookup_is_not_shadowed_by_a_ui_clone_package_in_cwd(tmp_path: Path) -> None:
    """The certificate is written into the artifact's guidance, and it is read
    through ui_clone.splash_contract from a shell. Run from a directory holding
    a decoy `ui_clone/` whose reader certifies everything: the artifact must
    still report the reference as uncertified, and still FAIL."""
    decoy_cwd = tmp_path / "impl-tree"
    decoy = decoy_cwd / "ui_clone"
    decoy.mkdir(parents=True)
    (decoy / "__init__.py").write_text("", encoding="utf-8")
    (decoy / "splash_contract.py").write_text(
        "def is_authoritative_absence(contract):\n    return True\n", encoding="utf-8"
    )
    ref_dir = tmp_path / "ref"
    _write_contract(ref_dir, certified=False)
    fake_bin = _fake_agent_browser_by_side(tmp_path, _absent_samples(), _absent_samples())

    proc, artifact = _run_check(tmp_path, fake_bin, ref_dir, cwd=decoy_cwd)

    assert artifact["status"] == "fail", artifact
    assert "ref-overlay-absent" in artifact["violations"]
    assert artifact["refAbsence"]["certified"] is False
    assert proc.returncode == 1


def test_ref_capture_failure_is_not_measured_absence(tmp_path: Path) -> None:
    """A reference that never opened produced no samples: a capture error,
    not a measured absence, and no `refAbsence` annotation is written."""
    ref_dir = tmp_path / "ref"
    _write_contract(ref_dir, certified=True)
    fake_bin = _fake_agent_browser_by_side(tmp_path, [], _absent_samples(), ref_open_rc=1)

    proc, artifact = _run_check(tmp_path, fake_bin, ref_dir)

    assert artifact["status"] == "fail"
    assert "ref-open-failed" in artifact["violations"]
    assert "refAbsence" not in artifact
    assert proc.returncode == 1
