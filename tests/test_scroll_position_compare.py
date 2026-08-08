"""Scroll-mode redesign (e2e-7 closeout, 2026-06-11): position-aligned compare.

Time-indexed 60fps frame SSIM is non-discriminative for scroll mode: the
sweep quantizes into SCROLL_STEPS instant jumps (~323px/step on a 19,233px
site) scheduled per-side with in-page setTimeout, and main-thread contention
slides step execution by ±1 step independently per side. The control
experiment that motivated this redesign: the live reference compared against
ITSELF failed 210/324 frames (65%) — evidence in
tmp/ref/realfood-e2e-7/brief/video-motion-scroll-tool-gap.md.

The redesign replaces scroll-mode video recording with scroll-POSITION-aligned
still capture: both sides screenshot at the same proportional scroll fractions
after a settle wait, and same-named position frames are SSIM-compared. Same
input therefore compares identical bytes — the ref-vs-ref self-test property
is structural, and these tests bake it as a regression:

- lib level: compare_position_frames over identical dirs MUST pass.
- script level: a stubbed agent-browser producing identical screenshots on
  both sessions MUST drive the full scroll mode to exit 0.

Splash/hover/click modes keep the time-indexed recorder (time-driven by
nature; freeze + first-change alignment validated them on e2e-7).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "verify" / "video-transition-compare.sh"
LIB = REPO / "scripts" / "verify" / "lib" / "position-compare.sh"


def _make_pos_frames(d: Path, colors: list[str]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(colors):
        subprocess.run(
            ["magick", "-size", "128x128", f"xc:{c}", str(d / f"pos-{i:03d}.png")],
            check=True,
            capture_output=True,
        )


def _compare(ref: Path, impl: Path, diff: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            f'source "{LIB}"; compare_position_frames "{ref}" "{impl}" "{diff}" 0.90',
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


# ── lib: comparison stage ──────────────────────────────────────────────────


def test_identical_position_frames_pass(tmp_path: Path) -> None:
    """Baked ref-vs-ref regression: identical input MUST pass (the old
    time-indexed scroll compare failed the reference against itself at 65%)."""
    colors = ["red", "green", "blue", "yellow"]
    _make_pos_frames(tmp_path / "ref", colors)
    _make_pos_frames(tmp_path / "impl", colors)
    proc = _compare(tmp_path / "ref", tmp_path / "impl", tmp_path / "diff")
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "Fail: 0" in proc.stdout, proc.stdout


def test_self_compare_same_dir_passes(tmp_path: Path) -> None:
    """Literal self-compare (ref dir against itself) — must pass."""
    _make_pos_frames(tmp_path / "ref", ["red", "white", "black"])
    proc = _compare(tmp_path / "ref", tmp_path / "ref", tmp_path / "diff")
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"


def test_divergent_position_frame_fails(tmp_path: Path) -> None:
    """Discriminative power retained: a position whose content differs must
    fail and be named in the output."""
    _make_pos_frames(tmp_path / "ref", ["red", "green", "blue"])
    _make_pos_frames(tmp_path / "impl", ["red", "white", "blue"])
    proc = _compare(tmp_path / "ref", tmp_path / "impl", tmp_path / "diff")
    assert proc.returncode == 1, f"{proc.stdout}\n{proc.stderr}"
    assert "pos-001" in proc.stdout, proc.stdout
    assert "Fail: 1" in proc.stdout, proc.stdout


def test_missing_impl_frame_fails(tmp_path: Path) -> None:
    """A position captured on ref but missing on impl is a failure, never a
    silent skip."""
    _make_pos_frames(tmp_path / "ref", ["red", "green"])
    _make_pos_frames(tmp_path / "impl", ["red"])
    proc = _compare(tmp_path / "ref", tmp_path / "impl", tmp_path / "diff")
    assert proc.returncode == 1, f"{proc.stdout}\n{proc.stderr}"


def test_zero_position_pairs_is_vacuous_fail(tmp_path: Path) -> None:
    """Anti-bypass: empty capture dirs must fail, not vacuously pass."""
    (tmp_path / "ref").mkdir()
    (tmp_path / "impl").mkdir()
    proc = _compare(tmp_path / "ref", tmp_path / "impl", tmp_path / "diff")
    assert proc.returncode == 1, f"{proc.stdout}\n{proc.stderr}"
    assert "vacuous" in proc.stdout.lower(), proc.stdout


# ── script: scroll mode drives position capture, not video recording ──────

# Stub agent-browser: logs every call; `screenshot <path>` writes a PNG whose
# color is fixed (identical both sides) or per-session (divergent test).
_STUB = """#!/usr/bin/env bash
echo "$@" >> "$AB_CALL_LOG"
args=("$@")
for i in "${!args[@]}"; do
  if [[ "${args[$i]}" == "screenshot" ]]; then
    path="${args[$((i+1))]}"
    color="$AB_SHOT_COLOR"
    if [[ -n "$AB_SHOT_COLOR_IMPL" && "$*" == *"-impl"* ]]; then
      color="$AB_SHOT_COLOR_IMPL"
    fi
    mkdir -p "$(dirname "$path")"
    magick -size 64x64 "xc:$color" "$path"
  fi
done
exit 0
"""


def _run_scroll(
    tmp_path: Path, impl_color: str = "", out_dir: Path | None = None
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "agent-browser"
    stub.write_text(_STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    log.write_text("")
    if out_dir is None:
        out_dir = tmp_path / "out"
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        AB_CALL_LOG=str(log),
        AB_SHOT_COLOR="gray",
        AB_SHOT_COLOR_IMPL=impl_color,
        PRE_ACTION_WAIT="0",
        SCROLL_SAMPLES="4",
        SCROLL_SETTLE="0",
    )
    proc = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "tst",
            "http://ref.example",
            "http://impl.example",
            str(out_dir),
            "scroll",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    return proc, log.read_text().splitlines(), out_dir


def test_scroll_mode_identical_sides_pass_end_to_end(tmp_path: Path) -> None:
    """End-to-end same-input regression: with a stub producing identical
    screenshots on both sessions, the full scroll mode MUST exit 0."""
    proc, lines, out_dir = _run_scroll(tmp_path)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert (out_dir / "result.txt").exists()
    assert "Fail: 0" in (out_dir / "result.txt").read_text()


def test_scroll_mode_takes_position_screenshots_not_video(tmp_path: Path) -> None:
    """Scroll mode must capture position-aligned stills — no video recording
    (the per-side recording timeline is what made time-indexed SSIM noise)."""
    _, lines, _ = _run_scroll(tmp_path)
    assert not any("record start" in ln for ln in lines), lines
    for session in ("tst-orig", "tst-impl"):
        shots = [ln for ln in lines if "screenshot" in ln and session in ln]
        # SCROLL_SAMPLES=4 → positions 0..4 inclusive = 5 screenshots
        assert len(shots) == 5, (session, shots)


def test_scroll_mode_freezes_media_before_first_screenshot(tmp_path: Path) -> None:
    """Time-coupled media freeze (c96780d) must still run, before captures."""
    _, lines, _ = _run_scroll(tmp_path)
    for session in ("tst-orig", "tst-impl"):
        freeze = next(
            (
                i
                for i, ln in enumerate(lines)
                if "eval" in ln and "currentTime = 0" in ln and session in ln
            ),
            -1,
        )
        first_shot = next(
            (
                i
                for i, ln in enumerate(lines)
                if "screenshot" in ln and session in ln
            ),
            -1,
        )
        assert freeze != -1, f"no freeze eval for {session}"
        assert first_shot != -1, f"no screenshot for {session}"
        assert freeze < first_shot, (session, freeze, first_shot)


def test_scroll_mode_divergent_sides_fail(tmp_path: Path) -> None:
    """Discriminative power at script level: per-session screenshot colors
    differ → scroll mode must exit 1."""
    proc, _, out_dir = _run_scroll(tmp_path, impl_color="navy")
    assert proc.returncode == 1, f"{proc.stdout}\n{proc.stderr}"
    text = (out_dir / "result.txt").read_text()
    assert "Fail: 0" not in text, text


def test_scroll_mode_clears_stale_frames_from_prior_runs(tmp_path: Path) -> None:
    """Anti-contamination: the out-dir may hold 60fps f-*.png frames from a
    prior time-indexed run (or pos-*.png from an earlier capture). Scroll mode
    must compare ONLY the frames it just captured — stale files inflated a
    real run to 'Total positions compared: 373' with 318 phantom fails."""
    out_dir = tmp_path / "out"
    for side in ("ref-frames", "impl-frames"):
        d = out_dir / side
        d.mkdir(parents=True)
        subprocess.run(
            ["magick", "-size", "64x64", "xc:purple", str(d / "f-000001.png")],
            check=True,
            capture_output=True,
        )
    # stale ref-only extra position frame would also phantom-fail as missing-impl
    subprocess.run(
        ["magick", "-size", "64x64", "xc:purple", str(out_dir / "ref-frames" / "pos-999.png")],
        check=True,
        capture_output=True,
    )
    proc, _, out = _run_scroll(tmp_path, out_dir=out_dir)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    text = (out / "result.txt").read_text()
    assert "Total frames: 5" in text, text
