"""Tests for dynamic-behavior-parity.sh verdict logic (no live browser).

The script exposes a `--judge <measurements-json> <ref-dir>` mode that skips the
browser and computes verdicts + artifact from pre-collected fingerprints. These
tests exercise that mode plus the browser-free discovery path (no dynamic
regions declared).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "dynamic-behavior-parity.sh"
HELPER = (
    ROOT
    / "skills"
    / "visual-debug"
    / "scripts"
    / "lib"
    / "dynamic_behavior_parity.py"
)
ARTIFACT_NAME = "dynamic-behavior-parity.json"


def test_shell_has_no_large_heredoc() -> None:
    """Keep the large Python judge out of Bash's pipe-backed heredoc path."""
    shell = SCRIPT.read_text(encoding="utf-8")
    assert "<<" not in shell
    assert HELPER.is_file()
    assert 'python3 \\\n  "$SCRIPTS_DIR/lib/dynamic_behavior_parity.py"' in shell


def test_judge_completes_on_current_bash_without_compat(tmp_path: Path) -> None:
    """The default Bash must not need inherited heredoc compatibility state."""
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    measurements = tmp_path / "measurements.json"
    measurements.write_text('{"regions":[]}\n', encoding="utf-8")
    env = os.environ.copy()
    env.pop("BASH_COMPAT", None)
    bash = shutil.which("bash")
    assert bash is not None

    proc = subprocess.run(
        [bash, str(SCRIPT), "--judge", str(measurements), str(ref_dir)],
        capture_output=True,
        env=env,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref_dir / ARTIFACT_NAME).read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"


def test_dynamic_behavior_helper_imports_under_macos_system_python(
    tmp_path: Path,
) -> None:
    """The dispatcher may invoke this helper through macOS /usr/bin/python3."""
    host_python = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else shutil.which("python3")
    if not host_python:
        import pytest

        pytest.skip("python3 not available")

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    measurements = tmp_path / "measurements.json"
    measurements.write_text('{"regions":[]}\n', encoding="utf-8")

    proc = subprocess.run(
        [
            host_python,
            str(HELPER),
            "judge",
            "",
            "",
            "",
            str(ref_dir),
            str(measurements),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr


def _judge(tmp_path: Path, measurements: dict) -> subprocess.CompletedProcess[str]:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir(parents=True, exist_ok=True)
    meas_file = tmp_path / "measurements.json"
    meas_file.write_text(json.dumps(measurements), encoding="utf-8")
    return subprocess.run(
        ["bash", str(SCRIPT), "--judge", str(meas_file), str(ref_dir)],
        capture_output=True, text=True, timeout=120,
    )


def _artifact(tmp_path: Path) -> dict:
    data: dict = json.loads((tmp_path / "ref" / ARTIFACT_NAME).read_text(encoding="utf-8"))
    return data


def _region(
    selector: str, ref_changed: bool, impl_changed: bool, **extra: object
) -> dict:
    reg: dict[str, object] = {
        "selector": selector,
        "ref": {"present": True, "changed": ref_changed},
        "impl": {"present": True, "changed": impl_changed},
    }
    reg.update(extra)
    return reg


# (a) ref + impl both changed → behavior-match, status pass
def test_both_changed_is_behavior_match_pass(tmp_path: Path) -> None:
    proc = _judge(tmp_path, {"regions": [_region(".carousel", True, True)]})
    assert proc.returncode == 0, proc.stderr
    art = _artifact(tmp_path)
    assert art["status"] == "pass"
    row = art["regions"][0]
    assert row["verdict"] == "behavior-match"
    assert row["refChanged"] is True and row["implChanged"] is True


# (b) ref changed, impl static → static-in-impl, status fail
def test_ref_changed_impl_static_is_fail(tmp_path: Path) -> None:
    proc = _judge(tmp_path, {"regions": [_region(".ticker", True, False)]})
    assert proc.returncode == 1
    art = _artifact(tmp_path)
    assert art["status"] == "fail"
    row = art["regions"][0]
    assert row["verdict"] == "static-in-impl"
    assert row["refChanged"] is True and row["implChanged"] is False


# (b') element missing on impl → static-in-impl, fail
def test_element_missing_on_impl_is_static_in_impl(tmp_path: Path) -> None:
    reg = {
        "selector": ".feed",
        "ref": {"present": True, "changed": True},
        "impl": {"present": False},
    }
    proc = _judge(tmp_path, {"regions": [reg]})
    assert proc.returncode == 1
    row = _artifact(tmp_path)["regions"][0]
    assert row["verdict"] == "static-in-impl"
    assert "missing on impl" in row.get("note", "")


# (c) ref unchanged in window → no-dynamics-in-window, status pass
def test_ref_unchanged_is_no_dynamics_pass(tmp_path: Path) -> None:
    proc = _judge(tmp_path, {"regions": [_region(".maybe-static", False, False)]})
    assert proc.returncode == 0, proc.stderr
    art = _artifact(tmp_path)
    assert art["status"] == "pass"
    assert art["regions"][0]["verdict"] == "no-dynamics-in-window"


# (c') ref unchanged but impl changed → still not a defect (no ref dynamics to match)
def test_ref_unchanged_impl_changed_is_pass(tmp_path: Path) -> None:
    proc = _judge(tmp_path, {"regions": [_region(".x", False, True)]})
    assert proc.returncode == 0
    assert _artifact(tmp_path)["regions"][0]["verdict"] == "no-dynamics-in-window"


# (d) period mismatch >25% → behavior-match-period-off, fail
def test_period_mismatch_beyond_tolerance_fails(tmp_path: Path) -> None:
    reg = _region(".swiper", True, True, refPeriodMs=3000, implPeriodMs=5000)
    proc = _judge(tmp_path, {"regions": [reg]})
    assert proc.returncode == 1
    art = _artifact(tmp_path)
    assert art["status"] == "fail"
    row = art["regions"][0]
    assert row["verdict"] == "behavior-match-period-off"
    assert row["refPeriodMs"] == 3000 and row["implPeriodMs"] == 5000


# (d') period within 25% → behavior-match, pass
def test_period_within_tolerance_passes(tmp_path: Path) -> None:
    reg = _region(".swiper", True, True, refPeriodMs=3000, implPeriodMs=3400)
    proc = _judge(tmp_path, {"regions": [reg]})
    assert proc.returncode == 0, proc.stderr
    assert _artifact(tmp_path)["regions"][0]["verdict"] == "behavior-match"


# [P2] known ref period + undetectable impl period → period-unverified, fail
def test_known_ref_period_undetectable_impl_period_fails(tmp_path: Path) -> None:
    # both changed, ref cadence known (3000ms), impl cadence undiscoverable (omitted)
    reg = _region(".swiper", True, True, refPeriodMs=3000)
    proc = _judge(tmp_path, {"regions": [reg]})
    assert proc.returncode == 1
    art = _artifact(tmp_path)
    assert art["status"] == "fail"
    row = art["regions"][0]
    assert row["verdict"] == "behavior-match-period-unverified"
    assert row["refPeriodMs"] == 3000
    assert "implPeriodMs" not in row  # undetectable → omitted


# [P2] no cadence declared on either side → plain behavior-match (cadence opt-in)
def test_no_period_declared_stays_plain_behavior_match(tmp_path: Path) -> None:
    proc = _judge(tmp_path, {"regions": [_region(".carousel", True, True)]})
    assert proc.returncode == 0, proc.stderr
    assert _artifact(tmp_path)["regions"][0]["verdict"] == "behavior-match"


# [P1] a command-level probe failure escalates to status error + exit 2 (never green)
def test_probe_failure_escalates_to_error_exit2(tmp_path: Path) -> None:
    reg = {
        "selector": ".carousel",
        "ref": {"probeFailed": True, "reason": "eval command failed on ref session"},
        "impl": {"present": True, "changed": True},
    }
    proc = _judge(tmp_path, {"regions": [reg]})
    assert proc.returncode == 2
    art = _artifact(tmp_path)
    assert art["status"] == "error"
    row = art["regions"][0]
    assert row["verdict"] == "probe-failed"
    assert "probe failed" in row.get("note", "")


# [P1] probe failure outranks an otherwise-passing region in the same run
def test_probe_failure_outranks_pass_in_run(tmp_path: Path) -> None:
    regions = [
        _region(".good", True, True),  # would be behavior-match / pass
        {"selector": ".bad", "impl": {"probeFailed": True, "reason": "timeout"},
         "ref": {"present": True, "changed": True}},
    ]
    proc = _judge(tmp_path, {"regions": regions})
    assert proc.returncode == 2
    assert _artifact(tmp_path)["status"] == "error"


# [P1] a recorded page-open failure → status error, exit 2 (models collect() open failure)
def test_setup_error_open_failure_is_error_exit2(tmp_path: Path) -> None:
    proc = _judge(tmp_path, {"setupError": "agent-browser open failed for session(s): ref"})
    assert proc.returncode == 2
    art = _artifact(tmp_path)
    assert art["status"] == "error"
    assert "open failed" in art.get("note", "")


# in-page {"error":...} from a probe that RAN is still honest-unmeasurable (pass),
# NOT escalated as an infra probe failure — the two must not be conflated.
def test_inpage_error_is_unmeasurable_not_probe_failure(tmp_path: Path) -> None:
    reg = {
        "selector": ".x",
        "ref": {"error": "TypeError: cannot read properties of null"},
        "impl": {"present": True, "changed": True},
    }
    proc = _judge(tmp_path, {"regions": [reg]})
    assert proc.returncode == 0, proc.stderr
    art = _artifact(tmp_path)
    assert art["status"] == "pass"
    assert art["regions"][0]["verdict"] == "honest-unmeasurable"


# unmeasurable side → honest-unmeasurable, not a fail
def test_unmeasurable_ref_is_honest_unmeasurable_pass(tmp_path: Path) -> None:
    reg = {
        "selector": ".broken",
        "ref": {"error": "eval failed: SyntaxError"},
        "impl": {"present": True, "changed": True},
    }
    proc = _judge(tmp_path, {"regions": [reg]})
    assert proc.returncode == 0, proc.stderr
    row = _artifact(tmp_path)["regions"][0]
    assert row["verdict"] == "honest-unmeasurable"
    assert "eval failed" in row.get("note", "")


# fingerprint-based change detection (fp0 != fp1) without a precomputed `changed`
def test_fingerprint_diff_drives_change_detection(tmp_path: Path) -> None:
    reg = {
        "selector": ".carousel",
        "ref": {"present": True,
                "fp0": {"present": True, "textHash": "1", "styleSig": "a", "rectSig": "0,0,1,1"},
                "fp1": {"present": True, "textHash": "2", "styleSig": "a", "rectSig": "0,0,1,1"}},
        "impl": {"present": True,
                 "fp0": {"present": True, "textHash": "9", "styleSig": "b", "rectSig": "0,0,1,1"},
                 "fp1": {"present": True, "textHash": "9", "styleSig": "b", "rectSig": "0,0,1,1"}},
    }
    proc = _judge(tmp_path, {"regions": [reg]})
    assert proc.returncode == 1  # ref changed, impl identical fp → static-in-impl
    row = _artifact(tmp_path)["regions"][0]
    assert row["verdict"] == "static-in-impl"
    assert row["refChanged"] is True and row["implChanged"] is False


def test_fingerprint_probe_includes_natural_media_state() -> None:
    helper = HELPER.read_text(encoding="utf-8")
    fp_body = re.search(
        r"def fp_js\(selector: str\) -> str:\n(?P<body>.*?)(?=\n\ndef period_js)",
        helper,
        flags=re.DOTALL,
    )
    assert fp_body is not None
    body = fp_body.group("body")
    assert "HTMLMediaElement" in body
    assert "currentTime" in body
    assert "paused" in body
    assert "readyState" in body
    assert "mediaSig:mediaSig" in body


def test_fingerprint_probe_includes_image_and_background_state() -> None:
    helper = HELPER.read_text(encoding="utf-8")
    fp_body = re.search(
        r"def fp_js\(selector: str\) -> str:\n(?P<body>.*?)(?=\n\ndef period_js)",
        helper,
        flags=re.DOTALL,
    )
    assert fp_body is not None
    body = fp_body.group("body")
    assert "HTMLImageElement" in body
    assert "currentSrc" in body
    assert "srcset" in body
    assert "backgroundImage" in body
    assert "imageSig:imageSig" in body
    assert "backgroundSig:backgroundSig" in body


def test_playing_media_fingerprint_drives_change_detection(tmp_path: Path) -> None:
    def side(first_time: float, second_time: float) -> dict:
        shared = {
            "present": True,
            "textHash": "0",
            "styleSig": "none|1",
            "rectSig": "0,0,640,360",
        }
        return {
            "present": True,
            "fp0": {**shared, "mediaSig": f"VIDEO|{first_time}|false|4"},
            "fp1": {**shared, "mediaSig": f"VIDEO|{second_time}|false|4"},
        }

    reg = {
        "selector": "video",
        "ref": side(1.0, 5.0),
        "impl": side(2.0, 6.0),
    }
    proc = _judge(tmp_path, {"regions": [reg]})
    assert proc.returncode == 0, proc.stderr
    row = _artifact(tmp_path)["regions"][0]
    assert row["verdict"] == "behavior-match"
    assert row["refChanged"] is True and row["implChanged"] is True


def test_image_only_carousel_fingerprint_drives_change_detection(tmp_path: Path) -> None:
    def side(first_src: str, second_src: str) -> dict:
        shared = {
            "present": True,
            "textHash": "0",
            "styleSig": "none|1",
            "rectSig": "0,0,640,360",
            "mediaSig": "",
        }
        return {
            "present": True,
            "fp0": {
                **shared,
                "imageSig": f"IMG|{first_src}|/a.jpg|/a.jpg 1x, /a@2x.jpg 2x",
                "backgroundSig": 'url("/static/a.jpg")|50% 50%|cover',
            },
            "fp1": {
                **shared,
                "imageSig": f"IMG|{second_src}|/b.jpg|/b.jpg 1x, /b@2x.jpg 2x",
                "backgroundSig": 'url("/static/b.jpg")|50% 50%|cover',
            },
        }

    reg = {
        "selector": ".image-carousel",
        "ref": side("https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"),
        "impl": side("https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"),
    }
    proc = _judge(tmp_path, {"regions": [reg]})
    assert proc.returncode == 0, proc.stderr
    row = _artifact(tmp_path)["regions"][0]
    assert row["verdict"] == "behavior-match"
    assert row["refChanged"] is True and row["implChanged"] is True


def test_static_container_fingerprint_remains_unchanged(tmp_path: Path) -> None:
    fingerprint = {
        "present": True,
        "textHash": "0",
        "styleSig": "none|1",
        "rectSig": "0,0,640,360",
        "mediaSig": "",
        "imageSig": "",
        "backgroundSig": "",
    }
    side = {"present": True, "fp0": fingerprint, "fp1": dict(fingerprint)}
    proc = _judge(
        tmp_path,
        {"regions": [{"selector": ".static", "ref": side, "impl": side}]},
    )
    assert proc.returncode == 0, proc.stderr
    row = _artifact(tmp_path)["regions"][0]
    assert row["verdict"] == "no-dynamics-in-window"
    assert row["refChanged"] is False and row["implChanged"] is False


# (e) no regions declared → pass artifact with empty regions (discovery path, no browser)
def test_no_regions_declared_is_pass_empty(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["bash", str(SCRIPT), "sess", "https://ref.example", "https://impl.example", str(ref_dir)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    art = _artifact(tmp_path)
    assert art["status"] == "pass"
    assert art["regions"] == []
    assert "no dynamic regions" in art.get("note", "")


# discovery from transition-spec time-driven entries (hermetic --discover mode)
def test_discovers_carousel_region_from_transition_spec(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "hero", "trigger": "autoplay (interval)", "target": ".swiper-wrapper",
             "animation": {"type": "carousel"}},
            {"id": "hov", "trigger": "hover", "target": ".nav a", "animation": {"type": "css-hover"}},
        ]
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--discover", str(ref_dir)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    art = _artifact(tmp_path)
    assert art["source"] == "transition-spec time-driven entries"
    selectors = [r["selector"] for r in art["regions"]]
    assert ".swiper-wrapper" in selectors  # time-driven region found
    assert ".nav a" not in selectors  # hover region excluded


# discovery prefers curated dynamic-regions.json and carries its period
def test_discovery_prefers_dynamic_regions_json(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "dynamic-regions.json").write_text(json.dumps({
        "regions": [{"selector": ".feed", "label": "news feed", "periodMs": 3000}]
    }), encoding="utf-8")
    # A transition-spec also exists, but priority 1 must win.
    (ref_dir / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "c", "trigger": "carousel", "target": ".other"}]
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--discover", str(ref_dir)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    art = _artifact(tmp_path)
    assert art["source"] == "dynamic-regions.json"
    assert art["regions"][0]["selector"] == ".feed"
    assert art["regions"][0]["periodMs"] == 3000


# (f) artifact is always written, even on empty regions and on judge runs
def test_artifact_always_written(tmp_path: Path) -> None:
    _judge(tmp_path, {"regions": [_region(".a", True, True)]})
    assert (tmp_path / "ref" / ARTIFACT_NAME).exists()
    assert _artifact(tmp_path)["schemaVersion"] == 1
    assert "generatedAt" in _artifact(tmp_path)


# [D22] a CURATED region missing on the ref is honest-unmeasurable, NOT a
# vacuous no-dynamics pass; when it is the only curated region and nothing else
# measured, the run is a hard error (measured nothing = no parity evidence).
def test_curated_missing_on_ref_is_error_not_vacuous_pass(tmp_path: Path) -> None:
    reg: dict[str, object] = {
        "selector": ".tech-hero video",
        "curated": True,
        "ref": {"present": False},
        "impl": {"present": True, "changed": True},
    }
    proc = _judge(tmp_path, {"source": "dynamic-regions.json", "regions": [reg]})
    assert proc.returncode == 2
    art = _artifact(tmp_path)
    assert art["status"] == "error"
    assert art["unverifiedCurated"] == 1
    row = art["regions"][0]
    assert row["verdict"] == "honest-unmeasurable"
    assert "curated region not found on ref" in row.get("note", "")


# [D22] a curated region missing on ref does NOT sink the run when another
# region produced a real measurement — but it still reports as unmeasurable and
# is tallied in unverifiedCurated (never a silent no-dynamics pass).
def test_curated_missing_with_other_real_measurement_is_not_error(tmp_path: Path) -> None:
    curated_missing: dict[str, object] = {
        "selector": ".tech-hero video",
        "curated": True,
        "ref": {"present": False},
        "impl": {"present": True, "changed": True},
    }
    measured = _region(".carousel", True, True, curated=True)
    proc = _judge(tmp_path, {"regions": [curated_missing, measured]})
    assert proc.returncode == 0, proc.stderr
    art = _artifact(tmp_path)
    assert art["status"] == "pass"
    assert art["unverifiedCurated"] == 1
    verdicts = {r["selector"]: r["verdict"] for r in art["regions"]}
    assert verdicts[".tech-hero video"] == "honest-unmeasurable"
    assert verdicts[".carousel"] == "behavior-match"


# [D22] all curated regions unverified → status error / exit 2 even with two of them
def test_all_curated_unverified_is_error(tmp_path: Path) -> None:
    regs: list[dict[str, object]] = [
        {"selector": ".a", "curated": True, "ref": {"present": False},
         "impl": {"present": True, "changed": True}},
        {"selector": ".b", "curated": True, "ref": {"present": False},
         "impl": {"present": True, "changed": True}},
    ]
    proc = _judge(tmp_path, {"regions": regs})
    assert proc.returncode == 2
    art = _artifact(tmp_path)
    assert art["status"] == "error"
    assert art["unverifiedCurated"] == 2


# [D22] a SPEC-discovered (non-curated) region missing on ref KEEPS the
# no-dynamics-in-window / pass semantics — the curated escalation is scoped.
def test_spec_discovered_missing_on_ref_stays_no_dynamics_pass(tmp_path: Path) -> None:
    reg: dict[str, object] = {
        "selector": ".maybe-dynamic",
        # no curated flag → spec-discovered
        "ref": {"present": False},
        "impl": {"present": True, "changed": True},
    }
    proc = _judge(tmp_path, {"source": "transition-spec time-driven entries",
                             "regions": [reg]})
    assert proc.returncode == 0, proc.stderr
    art = _artifact(tmp_path)
    assert art["status"] == "pass"
    assert art["unverifiedCurated"] == 0
    row = art["regions"][0]
    assert row["verdict"] == "no-dynamics-in-window"
    assert "element missing on ref" in row.get("note", "")


def test_mixed_curated_unverified_with_spec_missing_is_error(tmp_path: Path) -> None:
    """Codex round-2 P2: a spec region merely MISSING on the ref
    (no-dynamics-in-window by absence) is not a real measurement — in a mixed
    artifact where every curated region went unverified, it must not defeat
    the measured-nothing guard and produce a vacuous pass."""
    measurements = {
        "source": "dynamic-regions.json",
        "regions": [
            {
                "selector": ".hero video",
                "curated": True,
                "ref": {"present": False},
                "impl": {"present": True, "changed": True},
            },
            {
                "selector": ".spec-carousel",
                "ref": {"present": False},
                "impl": {"present": False},
            },
        ],
    }
    proc = _judge(tmp_path, measurements)
    artifact = _artifact(tmp_path)
    assert artifact["status"] == "error", artifact
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert artifact["unverifiedCurated"] == 1
