"""Browser-free tests for desktop-band-fluidity-check.sh.

The live path drives two agent-browser sessions across the desktop band; the
verdict math is factored behind a `--judge <measurements-json> <out-artifact>`
mode so it can be exercised without a browser. Each test feeds a measurements
fixture through that mode and asserts on the emitted artifact.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "desktop-band-fluidity-check.sh"


def _judge(
    tmp_path: Path, measurements: dict, tol: str | None = None
) -> tuple[subprocess.CompletedProcess[str], dict]:
    meas = tmp_path / "measurements.json"
    meas.write_text(json.dumps(measurements), encoding="utf-8")
    out = tmp_path / "desktop-band-fluidity.json"
    env = None
    if tol is not None:
        import os

        env = {**os.environ, "FLUIDITY_DOCH_TOL_PCT": tol}
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--judge", str(meas), str(out)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert out.is_file(), "artifact must always be written (emit-or-fail): " + proc.stderr
    return proc, json.loads(out.read_text())


def test_all_widths_within_tolerance_pass(tmp_path: Path) -> None:
    proc, art = _judge(tmp_path, {"widths": [
        {"viewport": "1440x900", "refDocH": 10000, "implDocH": 10200,
         "refOverflowX": False, "implOverflowX": False},
        {"viewport": "1280x800", "refDocH": 10000, "implDocH": 10100,
         "refOverflowX": False, "implOverflowX": False},
        {"viewport": "1024x800", "refDocH": 10000, "implDocH": 9900,
         "refOverflowX": False, "implOverflowX": False},
    ]})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"
    assert art["widthBaked"] is False
    assert all(w["pass"] for w in art["widths"])


def test_doch_delta_beyond_tolerance_fails(tmp_path: Path) -> None:
    # 1280 width: impl 12000 vs ref 10000 = 20% > 8% tolerance -> that width fails.
    proc, art = _judge(tmp_path, {"widths": [
        {"viewport": "1440x900", "refDocH": 10000, "implDocH": 10100,
         "refOverflowX": False, "implOverflowX": False},
        {"viewport": "1280x800", "refDocH": 10000, "implDocH": 12000,
         "refOverflowX": False, "implOverflowX": False},
    ]})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert art["status"] == "fail"
    failed = {w["viewport"]: w for w in art["widths"]}
    assert failed["1280x800"]["pass"] is False
    assert failed["1280x800"]["dochDeltaPct"] == pytest.approx(20.0)
    assert failed["1440x900"]["pass"] is True


def test_impl_only_horizontal_overflow_fails(tmp_path: Path) -> None:
    proc, art = _judge(tmp_path, {"widths": [
        {"viewport": "1440x900", "refDocH": 10000, "implDocH": 10050,
         "refOverflowX": False, "implOverflowX": True},
    ]})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert art["status"] == "fail"
    row = art["widths"][0]
    assert row["pass"] is False
    assert row["implOverflowX"] is True and row["refOverflowX"] is False


def test_width_baked_detected(tmp_path: Path) -> None:
    # Ref reflows 23220 -> 20000 across the band (~13.9%); impl is frozen at
    # 25000 -> widthBaked. Tolerance widened so only the baked signal drives fail.
    proc, art = _judge(tmp_path, {"widths": [
        {"viewport": "1440x900", "refDocH": 23220, "implDocH": 25000,
         "refOverflowX": False, "implOverflowX": False},
        {"viewport": "1024x800", "refDocH": 20000, "implDocH": 25000,
         "refOverflowX": False, "implOverflowX": False},
    ]}, tol="100")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert art["status"] == "fail"
    assert art["widthBaked"] is True


def test_empty_measurement_set_fails_closed(tmp_path: Path) -> None:
    proc, art = _judge(tmp_path, {"widths": []})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert art["status"] == "fail"
    assert art["widths"] == []
    assert art["widthBaked"] is False
    assert art["schemaVersion"] == 1


def test_unmeasured_width_fails_closed(tmp_path: Path) -> None:
    """A width whose ref/impl measurement is missing (eval flake, dead session)
    must fail the row, not silently pass — same hole the dispatcher's
    emit-or-fail invariant closes."""
    meas = tmp_path / "meas.json"
    out = tmp_path / "out.json"
    meas.write_text(json.dumps({"widths": [
        {"viewport": "1440x900", "refDocH": None, "implDocH": 20000,
         "refOverflowX": False, "implOverflowX": False},
    ]}))
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--judge", str(meas), str(out)],
        capture_output=True, text=True, timeout=60,
    )
    artifact = json.loads(out.read_text())
    assert proc.returncode == 1
    assert artifact["status"] == "fail"
    assert artifact["widths"][0]["pass"] is False


def _probe_widths(tmp_path: Path, breakpoints: list[str]) -> list[int]:
    """Drive the live path against a fake agent-browser and return the widths
    it actually probed, in ascending order."""
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "detected-breakpoints.json").write_text(
        json.dumps({"schemaVersion": 1, "breakpoints": breakpoints}),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    browser = bin_dir / "agent-browser"
    browser.write_text(
        """#!/usr/bin/env bash
case "$3" in
  open|close)
    exit 0
    ;;
  set)
    printf '%s\\n' "$5" >> "$FAKE_VIEWPORT_LOG"
    exit 0
    ;;
  eval)
    printf '{"docH":10000,"overflowX":false,"bodyWidth":1000}\\n'
    exit 0
    ;;
esac
exit 2
""",
        encoding="utf-8",
    )
    browser.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)

    viewport_log = tmp_path / "viewports.log"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "FAKE_VIEWPORT_LOG": str(viewport_log),
        }
    )
    env.pop("DESKTOP_BAND_WIDTHS", None)
    subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "fl-test",
            "https://ref.example/",
            "https://impl.example/",
            str(ref_dir),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    widths = {
        int(line.strip())
        for line in viewport_log.read_text(encoding="utf-8").splitlines()
        if line.strip().isdigit()
    }
    return sorted(widths)


def test_probe_widths_cover_reference_breakpoints_above_the_default_cap(
    tmp_path: Path,
) -> None:
    """The fixed 1440 default never probed the band a wide reference actually
    defines, so a clone that only breaks above 1440 passed unmeasured."""
    widths = _probe_widths(tmp_path, ["1024px", "1280px", "1440px", "1680px", "1920px"])

    assert 1680 in widths
    assert 1920 in widths


def test_probe_widths_extend_past_the_widest_declared_breakpoint(
    tmp_path: Path,
) -> None:
    """The widest breakpoint is a lower bound, not a ceiling: reflow has to be
    measured somewhere inside the open-ended band above it."""
    widths = _probe_widths(tmp_path, ["1024px", "1280px", "1920px"])

    assert max(widths) > 1920


def test_explicit_width_override_still_wins(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "detected-breakpoints.json").write_text(
        json.dumps({"schemaVersion": 1, "breakpoints": ["1920px"]}),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    browser = bin_dir / "agent-browser"
    browser.write_text(
        """#!/usr/bin/env bash
case "$3" in
  open|close) exit 0 ;;
  set) printf '%s\\n' "$5" >> "$FAKE_VIEWPORT_LOG"; exit 0 ;;
  eval) printf '{"docH":10000,"overflowX":false,"bodyWidth":1000}\\n'; exit 0 ;;
esac
exit 2
""",
        encoding="utf-8",
    )
    browser.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)

    viewport_log = tmp_path / "viewports.log"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "FAKE_VIEWPORT_LOG": str(viewport_log),
            "DESKTOP_BAND_WIDTHS": "1200x800,1300x800",
        }
    )
    subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "fl-test",
            "https://ref.example/",
            "https://impl.example/",
            str(ref_dir),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    widths = sorted(
        {
            int(line.strip())
            for line in viewport_log.read_text(encoding="utf-8").splitlines()
            if line.strip().isdigit()
        }
    )
    assert widths == [1200, 1300]
