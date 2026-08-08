from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "skills"
    / "visual-debug"
    / "scripts"
    / "breakpoint-collision-check.sh"
)


def _run_check(
    tmp_path: Path,
    *,
    detected: object,
    impl_detected: object,
    explicit_breakpoints: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[int], list[object]]:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "detected-breakpoints.json").write_text(
        json.dumps(detected),
        encoding="utf-8",
    )
    (ref_dir / "impl-detected-breakpoints.json").write_text(
        json.dumps(impl_detected),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    browser = bin_dir / "agent-browser"
    browser.write_text(
        """#!/usr/bin/env bash
case "$3" in
  navigate|close)
    exit 0
    ;;
  set)
    printf '%s\\n' "$5" >> "$FAKE_VIEWPORT_LOG"
    printf '%s' "$5" > "$FAKE_VIEWPORT_STATE"
    exit 0
    ;;
  eval)
    width="$(cat "$FAKE_VIEWPORT_STATE")"
    bp="$(printf '%s' "$4" | sed -n 's/.*const bp = \\([0-9][0-9]*\\);.*/\\1/p')"
    printf '{"bp":%s,"width":%s,"bodyScrollWidth":%s,"htmlScrollWidth":%s,"overflowing":false,"rootFontSize":16,"mqMaxBp":false,"mqMinBp":false,"collision":false}\\n' "$bp" "$width" "$width" "$width"
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
            "REF_DIR": str(ref_dir),
            "WAIT_MS": "0",
            "FAKE_VIEWPORT_LOG": str(viewport_log),
            "FAKE_VIEWPORT_STATE": str(tmp_path / "viewport-state"),
        }
    )
    args = ["bash", str(SCRIPT), "bp-test", "https://impl.example/"]
    if explicit_breakpoints is not None:
        args.append(explicit_breakpoints)
    proc = subprocess.run(
        args,
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )
    widths = [
        int(line)
        for line in viewport_log.read_text(encoding="utf-8").splitlines()
    ]
    artifact = json.loads(
        (ref_dir / "responsive" / "boundary-collisions.json").read_text(
            encoding="utf-8"
        )
    )
    return proc, widths, artifact


def test_default_sweep_merges_valid_detected_breakpoints_once(
    tmp_path: Path,
) -> None:
    proc, widths, artifact = _run_check(
        tmp_path,
        detected={
            "breakpoints": [1599, "1600px", 1600, "bad", 1.5, 0, -1, True],
        },
        impl_detected={
            "breakpoints": ["1640", 1919, "1920px", 1536, None, False],
        },
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    expected_breakpoints = [640, 768, 1024, 1280, 1536, 1599, 1600, 1640, 1919, 1920]
    assert widths == [
        width
        for breakpoint in expected_breakpoints
        for width in (breakpoint - 1, breakpoint, breakpoint + 1)
    ]
    assert artifact == []


def test_explicit_breakpoints_replace_detected_default_sweep(
    tmp_path: Path,
) -> None:
    proc, widths, artifact = _run_check(
        tmp_path,
        detected={"breakpoints": [1599, 1600]},
        impl_detected={"breakpoints": [1919, 1920]},
        explicit_breakpoints="768 1920 768 bad -1 1.5",
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert widths == [767, 768, 769, 1919, 1920, 1921]
    assert artifact == []
