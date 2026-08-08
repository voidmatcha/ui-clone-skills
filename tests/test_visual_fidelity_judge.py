"""Hermetic tests for visual-fidelity-judge-check.sh (the VLM "automated
eyeball" check).

No browser, no claude CLI, no dispatcher: the verdict-math tests drive the
script's ``--judge-artifact`` mode with a pre-collected measurements JSON; the
J-1 crop-staleness and J-2 settle-derivation tests drive the filesystem-only
``--print-static-plan`` / ``--print-settle`` probe modes with controlled mtimes.
A source-level guard asserts the verification-plan.sh row is registered at
severity=warn.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "skills" / "visual-debug" / "scripts" / "visual-fidelity-judge-check.sh"
_DRIVER = _SCRIPT.with_name("visual_fidelity_judge.py")
_PLAN = _ROOT / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"


def test_python_driver_is_standalone() -> None:
    shell = _SCRIPT.read_text(encoding="utf-8")
    assert "<<'PY'" not in shell
    assert 'python3 "$SCRIPTS_DIR/visual_fidelity_judge.py"' in shell
    ast.parse(_DRIVER.read_text(encoding="utf-8"), filename=str(_DRIVER))


def test_visual_fidelity_driver_imports_under_macos_system_python(tmp_path: Path) -> None:
    """The dispatcher may invoke this driver through macOS /usr/bin/python3."""
    host_python = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else shutil.which("python3")
    if not host_python:
        pytest.skip("python3 not available")

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    meas_file = tmp_path / "measurements.json"
    meas_file.write_text(json.dumps(_good_measurements()), encoding="utf-8")

    proc = subprocess.run(
        [
            host_python,
            "-c",
            (
                "import importlib.util;"
                f"spec=importlib.util.spec_from_file_location('vfj', {str(_DRIVER)!r});"
                "mod=importlib.util.module_from_spec(spec);"
                "spec.loader.exec_module(mod)"
            ),
            "judge",
            "",
            "",
            "",
            str(ref_dir),
            str(meas_file),
            str(_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr


def _run_judge(
    tmp_path: Path, measurements: dict | str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir(parents=True, exist_ok=True)
    meas_file = tmp_path / "measurements.json"
    # `measurements` may be a dict (serialized) or a raw string (malformed input).
    if isinstance(measurements, str):
        meas_file.write_text(measurements, encoding="utf-8")
    else:
        meas_file.write_text(json.dumps(measurements), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(_SCRIPT), "--judge-artifact", str(meas_file), str(ref_dir)],
        capture_output=True, text=True,
    )
    return proc, ref_dir / "visual-fidelity-judge.json"


def _print_settle(ref_dir: Path) -> int:
    proc = subprocess.run(
        ["bash", str(_SCRIPT), "--print-settle", str(ref_dir)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return int(proc.stdout.strip())


def _static_plan(ref_dir: Path, impl_root: Path | None) -> dict:
    env = dict(os.environ)
    if impl_root is not None:
        env["IMPL_ROOT"] = str(impl_root)
    proc = subprocess.run(
        ["bash", str(_SCRIPT), "--print-static-plan", str(ref_dir)],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout.strip())
    assert isinstance(data, dict)
    return data


def _make_crop(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    os.utime(path, (mtime, mtime))


def _good_measurements() -> dict:
    return {
        "source": "unit",
        "staticSections": [
            {"label": "hero", "score": 9.0, "issues": []},
            {"label": "footer", "score": 7.5, "issues": ["minor spacing"]},
        ],
        "motion": {
            "axes": {"layout": 9, "text": 8, "color": 9, "animation": 8},
            "differsAt": [],
            "notes": ["motion matches"],
        },
    }


def test_all_axes_and_sections_ok_passes(tmp_path: Path) -> None:
    proc, artifact = _run_judge(tmp_path, _good_measurements())
    assert proc.returncode == 0, proc.stderr
    assert artifact.is_file()
    data = json.loads(artifact.read_text())
    assert data["status"] == "pass"
    assert data["overall"]["worstAxis"] == "text"  # lowest axis value (8)
    assert data["overall"]["worstSection"] == "footer"  # lowest static (7.5)
    # J-3: headline is the MEAN of (axes 9,8,9,8 + sections 9.0,7.5) = 50.5/6;
    # the min is preserved alongside.
    assert data["overall"]["score"] == 8.42
    assert data["overall"]["min"] == 7.5
    assert data["schemaVersion"] == 1
    assert {s["label"] for s in data["staticSections"]} == {"hero", "footer"}


def test_low_animation_axis_fails_with_worst_axis_animation(tmp_path: Path) -> None:
    meas = _good_measurements()
    meas["motion"]["axes"]["animation"] = 4
    proc, artifact = _run_judge(tmp_path, meas)
    assert proc.returncode == 1, proc.stderr
    data = json.loads(artifact.read_text())
    assert data["status"] == "fail"
    assert data["overall"]["worstAxis"] == "animation"
    # headline mean of (9,8,9,4 + 9.0,7.5) = 46.5/6 = 7.75; min preserved as 4.
    assert data["overall"]["score"] == 7.75
    assert data["overall"]["min"] == 4


def test_low_static_section_fails(tmp_path: Path) -> None:
    meas = _good_measurements()
    meas["staticSections"][0]["score"] = 5.0  # below STATIC_THRESHOLD (7)
    proc, artifact = _run_judge(tmp_path, meas)
    assert proc.returncode == 1, proc.stderr
    data = json.loads(artifact.read_text())
    assert data["status"] == "fail"
    hero = next(s for s in data["staticSections"] if s["label"] == "hero")
    assert hero["verdict"] == "fail"


def test_probe_failure_marker_is_error(tmp_path: Path) -> None:
    proc, artifact = _run_judge(tmp_path, {"probeFailed": True})
    assert proc.returncode == 2, proc.stderr
    assert artifact.is_file()  # emit-or-fail even on error
    assert json.loads(artifact.read_text())["status"] == "error"


def test_missing_axes_is_error(tmp_path: Path) -> None:
    meas = _good_measurements()
    del meas["motion"]["axes"]["color"]  # incomplete axes
    proc, artifact = _run_judge(tmp_path, meas)
    assert proc.returncode == 2, proc.stderr
    assert json.loads(artifact.read_text())["status"] == "error"


def test_static_section_missing_score_is_error(tmp_path: Path) -> None:
    meas = _good_measurements()
    meas["staticSections"].append({"label": "no-score"})
    proc, artifact = _run_judge(tmp_path, meas)
    assert proc.returncode == 2, proc.stderr
    assert json.loads(artifact.read_text())["status"] == "error"


@pytest.mark.parametrize(
    "bad_score",
    [True, float("nan"), float("inf"), -1, 11, 100],
    ids=["bool", "nan", "infinity", "negative", "eleven", "hundred"],
)
def test_nonfinite_or_out_of_range_scores_are_errors(
    tmp_path: Path, bad_score: object
) -> None:
    meas = _good_measurements()
    meas["motion"]["axes"]["layout"] = bad_score

    proc, artifact = _run_judge(tmp_path, meas)

    assert proc.returncode == 2, proc.stderr
    raw = artifact.read_text(encoding="utf-8")
    assert "NaN" not in raw and "Infinity" not in raw
    assert json.loads(raw)["status"] == "error"


def test_nonstandard_json_numeric_constants_are_rejected(tmp_path: Path) -> None:
    raw = json.dumps(_good_measurements()).replace(
        '"layout": 9', '"layout": NaN'
    )

    proc, artifact = _run_judge(tmp_path, raw)

    assert proc.returncode == 2, proc.stderr
    assert json.loads(artifact.read_text(encoding="utf-8"))["status"] == "error"


def test_malformed_measurements_is_error(tmp_path: Path) -> None:
    proc, artifact = _run_judge(tmp_path, "not json{{{")
    assert proc.returncode == 2, proc.stderr
    assert artifact.is_file()
    assert json.loads(artifact.read_text())["status"] == "error"


def test_empty_static_sections_still_judges_motion(tmp_path: Path) -> None:
    meas = _good_measurements()
    meas["staticSections"] = []
    proc, artifact = _run_judge(tmp_path, meas)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(artifact.read_text())
    assert data["status"] == "pass"
    assert data["staticSections"] == []


def test_artifact_always_written_on_pass_fail_and_error(tmp_path: Path) -> None:
    # pass
    p1, a1 = _run_judge(tmp_path / "a", _good_measurements())
    # fail
    fail_meas = _good_measurements()
    fail_meas["motion"]["axes"]["layout"] = 3
    p2, a2 = _run_judge(tmp_path / "b", fail_meas)
    # error
    p3, a3 = _run_judge(tmp_path / "c", {"setupError": "boom"})
    assert (p1.returncode, p2.returncode, p3.returncode) == (0, 1, 2)
    for art in (a1, a2, a3):
        assert art.is_file(), f"artifact not written: {art}"


def test_unpaired_samples_are_reflected_in_artifact(tmp_path: Path) -> None:
    """A partially-divergent run (some depths snap-diverged) still judges the
    paired subset, and the divergence list is preserved in the artifact so the
    unpaired samples are visible evidence, not silently dropped."""
    meas = _good_measurements()
    meas["motion"]["samples"] = [
        {"depth": 1, "requestedY": 1000, "refY": 980, "implY": 985, "paired": True},
        {"depth": 2, "requestedY": 8900, "refY": 7263, "implY": 4000,
         "paired": False, "reason": "|refY-implY|=3263px > 150px"},
    ]
    meas["motion"]["unpairedSamples"] = [meas["motion"]["samples"][1]]
    proc, artifact = _run_judge(tmp_path, meas)
    assert proc.returncode == 0, proc.stderr  # paired subset all >=7
    data = json.loads(artifact.read_text())
    assert data["status"] == "pass"
    assert len(data["motion"]["unpairedSamples"]) == 1
    assert data["motion"]["unpairedSamples"][0]["depth"] == 2
    assert len(data["motion"]["samples"]) == 2


def test_all_unpaired_is_error_not_pass(tmp_path: Path) -> None:
    """Every depth diverged (no axes because no paired frames were judged) =>
    hard error, never a pass, and the divergence list survives in the artifact."""
    unpaired = [
        {"depth": i, "requestedY": i * 3000, "refY": i * 2400,
         "implY": i * 3000, "paired": False, "reason": "divergent scroll"}
        for i in range(1, 7)
    ]
    meas = {
        "source": "unit",
        "staticSections": [{"label": "hero", "score": 9.0}],
        "motion": {"samples": unpaired, "unpairedSamples": unpaired},
    }
    proc, artifact = _run_judge(tmp_path, meas)
    assert proc.returncode == 2, proc.stderr
    data = json.loads(artifact.read_text())
    assert data["status"] == "error"
    assert len(data["motion"]["unpairedSamples"]) == 6
    assert "diverged" in data["note"]


# ── J-3: mean headline with min preserved ────────────────────────────────────
def test_overall_score_is_mean_not_min(tmp_path: Path) -> None:
    """8 passing sections + 1 wall section at 0.0 must NOT zero the headline:
    overall.score is the MEAN (well above 0) while overall.min preserves the 0.0.
    Status still fails closed on the sub-7 section."""
    meas = {
        "source": "unit",
        "staticSections": [{"label": f"s{i}", "score": 9.0} for i in range(8)]
                          + [{"label": "mo-nav", "score": 0.0}],
        "motion": {"axes": {"layout": 9, "text": 9, "color": 9, "animation": 9},
                   "differsAt": [], "notes": []},
    }
    proc, artifact = _run_judge(tmp_path, meas)
    assert proc.returncode == 1, proc.stderr  # fail-closed on the 0.0 section
    data = json.loads(artifact.read_text())
    assert data["status"] == "fail"
    assert data["overall"]["min"] == 0.0
    assert data["overall"]["worstSection"] == "mo-nav"
    # mean of (9,9,9,9 axes + eight 9.0 + one 0.0) = 108/13 ≈ 8.31 — nowhere near 0.
    assert data["overall"]["score"] > 8.0
    assert data["overall"]["score"] != data["overall"]["min"]


# ── J-2: derived settle window (reuses section_capture H9) ────────────────────
def test_derive_settle_cases(tmp_path: Path) -> None:
    # 1.06s transition → margin 0.4 + 1.06 = 1.46s → 1460ms.
    ref_a = tmp_path / "a"
    ref_a.mkdir()
    (ref_a / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"animation": {"duration": 1.06}}]}))
    assert _print_settle(ref_a) == 1460

    # Absent spec → sibling floor 0.5s, lifted to the judge's 1200ms floor.
    ref_b = tmp_path / "b"
    ref_b.mkdir()
    assert _print_settle(ref_b) == 1200

    # Huge duration → sibling's 4.0s cap → 4000ms.
    ref_c = tmp_path / "c"
    ref_c.mkdir()
    (ref_c / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"animation": {"duration": 100}}]}))
    assert _print_settle(ref_c) == 4000


# ── J-1: stale-crop exclusion ─────────────────────────────────────────────────
def _impl_fixture(tmp_path: Path, src_mtime: float) -> Path:
    impl = tmp_path / "impl"
    src = impl / "src" / "App.tsx"
    src.parent.mkdir(parents=True)
    src.write_text("export default function App(){return null}\n")
    os.utime(src, (src_mtime, src_mtime))
    return impl


def test_static_plan_excludes_stale_crops(tmp_path: Path) -> None:
    """A crop older than the newest impl change describes a dead tree: it is
    excluded and recorded as stale-crop, while a fresh crop is still judged."""
    ref = tmp_path / "ref"
    impl = _impl_fixture(tmp_path, src_mtime=1000.0)
    # hero crops predate the impl change (stale); footer crops postdate it (fresh).
    _make_crop(ref / "sections" / "ref" / "hero.png", 500.0)
    _make_crop(ref / "sections" / "impl" / "hero.png", 500.0)
    _make_crop(ref / "sections" / "ref" / "footer.png", 1500.0)
    _make_crop(ref / "sections" / "impl" / "footer.png", 1500.0)

    plan = _static_plan(ref, impl)
    assert plan["implRootResolved"] is True
    assert plan["cropSet"] == "sections"
    assert plan["judged"] == ["footer"]
    assert [row["label"] for row in plan["skipped"]] == ["hero"]
    assert plan["skipped"][0]["reason"] == "stale-crop"
    assert plan["allStale"] is False


def test_static_plan_all_stale_is_motion_only(tmp_path: Path) -> None:
    """When EVERY crop is stale, the static pass judges nothing (never a dead
    tree) and records a motion-only note."""
    ref = tmp_path / "ref"
    impl = _impl_fixture(tmp_path, src_mtime=2000.0)
    for name in ("hero", "footer"):
        _make_crop(ref / "sections" / "ref" / f"{name}.png", 500.0)
        _make_crop(ref / "sections" / "impl" / f"{name}.png", 500.0)

    plan = _static_plan(ref, impl)
    assert plan["judged"] == []
    assert {row["label"] for row in plan["skipped"]} == {"hero", "footer"}
    assert plan["allStale"] is True
    assert plan["note"] and "motion-only" in plan["note"]


def test_static_plan_prefers_freshest_viewport_set(tmp_path: Path) -> None:
    """section-compare now also writes sections/viewports/<WxH>/{ref,impl}/ — the
    plan probes both and picks the freshest set (documented via cropSet)."""
    ref = tmp_path / "ref"
    impl = _impl_fixture(tmp_path, src_mtime=100.0)  # older than all crops → none stale
    _make_crop(ref / "sections" / "ref" / "hero.png", 500.0)
    _make_crop(ref / "sections" / "impl" / "hero.png", 500.0)
    vp = ref / "sections" / "viewports" / "1440x900"
    _make_crop(vp / "ref" / "hero.png", 5000.0)  # much fresher
    _make_crop(vp / "impl" / "hero.png", 5000.0)

    plan = _static_plan(ref, impl)
    assert plan["cropSet"] == "sections/viewports/1440x900"
    assert plan["judged"] == ["hero"]


def test_verification_plan_registers_row_as_warn() -> None:
    """Source-level guard: the add_check row exists, targets the check script,
    and ships at severity=warn (advisory, non-gating)."""
    text = _PLAN.read_text(encoding="utf-8")
    m = re.search(
        r'add_check\s+"visual-fidelity-judge"\s*\\\s*\n'
        r'\s*"skills/visual-debug/scripts/visual-fidelity-judge-check\.sh"\s*\\'
        r'[\s\S]{0,600}?"warn"',
        text,
    )
    assert m, "visual-fidelity-judge add_check row missing or not severity=warn"
    # argsRecipe must be self-describing (session + both urls + ref_dir).
    assert "{session} {ref_url} {impl_url} {ref_dir}" in text


def test_static_plan_discovers_non_default_viewport_set(tmp_path: Path) -> None:
    """codex C1: a run captured at a viewport OTHER than 1440x900 writes its fresh
    crops under sections/viewports/<WxH>/. The plan must discover ALL viewport crop
    sets (not just the hardcoded 1440x900) and pick the freshest, else it scores
    the advisory verdict on the stale top-level crops or runs motion-only."""
    ref = tmp_path / "ref"
    impl = _impl_fixture(tmp_path, src_mtime=100.0)  # older than all crops
    _make_crop(ref / "sections" / "ref" / "hero.png", 500.0)
    _make_crop(ref / "sections" / "impl" / "hero.png", 500.0)
    vp = ref / "sections" / "viewports" / "1280x800"
    _make_crop(vp / "ref" / "hero.png", 6000.0)  # much fresher, non-default viewport
    _make_crop(vp / "impl" / "hero.png", 6000.0)

    plan = _static_plan(ref, impl)
    assert plan["cropSet"] == "sections/viewports/1280x800", (
        f"plan must prefer the fresh non-1440x900 viewport crops; got {plan['cropSet']}"
    )
    assert plan["judged"] == ["hero"]


def test_visual_fidelity_judge_hashes_viewport_crops() -> None:
    """codex C2: the staleness cache key must hash per-viewport crops too, else a
    viewport-crop regeneration reuses a stale visual-fidelity-judge.json."""
    from ui_clone.check_inputs import CHECK_INPUTS

    ref_globs = CHECK_INPUTS["visual-fidelity-judge"].ref
    assert "sections/viewports/*/ref/*.png" in ref_globs, ref_globs
    assert "sections/viewports/*/impl/*.png" in ref_globs, ref_globs
