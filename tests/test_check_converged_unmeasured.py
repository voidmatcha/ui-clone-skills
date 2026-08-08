"""The canonical convergence detector must not certify a run that measured nothing.

`scripts/verify/check-converged.sh` is the STOP signal for staged clone loops and
the writer of `structural-convergence-stamp.json`, which the Stop hook accepts as
closeout proof. It decided convergence from the `**Result:**` line alone —
0 FAIL plus at least one genuine PASS — and its header states outright that "SKIP
doesn't gate". Because blank-reference-crop rows are excluded from FAIL, an
UNMEASURED run read as converged:

    $ bash scripts/verify/check-converged.sh <ref>/sections/viewports/375x812
    converged: **Result: 20 PASS, 0 FAIL, 5 SKIP, 0 STRUCTURAL_ONLY**
    exit=0

That is the same laundering the producer, the gate, and the Stop hook were fixed
for — reached through a fourth consumer. The lesson is that the evidence state has
to live in the canonical denominator (the `**Result:**` line), not in a side-channel
line each consumer must independently learn to read.
"""

import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify" / "check-converged.sh"

_TABLE = (
    "| Section | AE | AE/Mpx | Severity | Status |\n"
    "|---------|-----|--------|----------|--------|\n"
    "| hero | 0 | 0 | ok | ✅ |\n"
    "| footer | 0 | 0 | ok | ✅ |\n"
)
_UNMEASURED_ROW = (
    "| style_slideshow_7xln1 | — | — | unmeasured | ⚠️ UNMEASURED "
    "(blank-ref: ref crop std 0.0056 < 0.05 — capture failure, not impl evidence) |\n"
)


def _run(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    sections = tmp_path / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "result.txt").write_text(body, encoding="utf-8")
    return subprocess.run(
        ["bash", str(_SCRIPT), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_unmeasured_in_result_line_is_not_converged(tmp_path: Path) -> None:
    body = (
        _TABLE
        + _UNMEASURED_ROW
        + "\n**Result: 2 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 1 UNMEASURED**\n"
    )
    proc = _run(tmp_path, body)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "unmeasured" in (proc.stdout + proc.stderr).lower()


def test_legacy_four_field_line_with_unmeasured_rows_is_not_converged(tmp_path: Path) -> None:
    # Artifacts written before the format bump carry no UNMEASURED field. The rows
    # are still on disk, so the row-level fallback has to catch them.
    body = _TABLE + _UNMEASURED_ROW + "\n**Result: 2 PASS, 0 FAIL, 1 SKIP, 0 STRUCTURAL_ONLY**\n"
    proc = _run(tmp_path, body)
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_clean_run_still_converges(tmp_path: Path) -> None:
    body = _TABLE + "\n**Result: 2 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 0 UNMEASURED**\n"
    proc = _run(tmp_path, body)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "converged" in proc.stdout


def test_legacy_clean_four_field_still_converges(tmp_path: Path) -> None:
    body = _TABLE + "\n**Result: 2 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
    proc = _run(tmp_path, body)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_real_failures_still_block(tmp_path: Path) -> None:
    body = _TABLE + "\n**Result: 2 PASS, 3 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 0 UNMEASURED**\n"
    proc = _run(tmp_path, body)
    assert proc.returncode == 1, proc.stdout + proc.stderr
