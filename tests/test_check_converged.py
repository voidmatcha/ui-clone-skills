"""Tests for scripts/verify/check-converged.sh.

The convergence detector exits 0 iff the LAST `**Result: ...**` line in
sections/result.txt shows `0 FAIL`. Convergence is the canonical STOP
signal used by every staged loop in the section-staged plan; the briefing
explicitly classifies STRUCTURAL_ONLY as PASS, so only FAIL counts gate.

Exit codes:
- 0: converged (last Result line shows 0 FAIL)
- 1: not converged (Result line present, FAIL > 0)
- 2: setup error (ref-dir missing, no Result line found, malformed)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify" / "check-converged.sh"


def _run(ref: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref)],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _write_result(ref: Path, line: str, *, trailer: str = "") -> None:
    sections = ref / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    body = "| row | … | PASS |\n\n" + line + "\n"
    if trailer:
        body += trailer + "\n"
    (sections / "result.txt").write_text(body)


def test_converged_all_pass_no_fail_exits_zero(tmp_path: Path) -> None:
    """14 PASS, 0 FAIL → converged (exit 0)."""
    ref = tmp_path / "ref"
    _write_result(ref, "**Result: 14 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**")
    proc = _run(ref)
    assert proc.returncode == 0, f"expected 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"


def test_structural_only_counts_as_pass(tmp_path: Path) -> None:
    """STRUCTURAL_ONLY rows are PASS per briefing §2C; 0 FAIL → exit 0."""
    ref = tmp_path / "ref"
    _write_result(ref, "**Result: 13 PASS, 0 FAIL, 2 SKIP, 13 STRUCTURAL_ONLY**")
    proc = _run(ref)
    assert proc.returncode == 0


def test_skip_present_but_zero_fail_still_converges(tmp_path: Path) -> None:
    """SKIP doesn't gate; only FAIL > 0 blocks convergence."""
    ref = tmp_path / "ref"
    _write_result(ref, "**Result: 5 PASS, 0 FAIL, 9 SKIP, 0 STRUCTURAL_ONLY**")
    proc = _run(ref)
    assert proc.returncode == 0


def test_fail_present_exits_one(tmp_path: Path) -> None:
    """Any FAIL > 0 → not converged (exit 1)."""
    ref = tmp_path / "ref"
    _write_result(ref, "**Result: 0 PASS, 14 FAIL, 1 SKIP, 0 STRUCTURAL_ONLY**")
    proc = _run(ref)
    assert proc.returncode == 1


def test_one_fail_blocks(tmp_path: Path) -> None:
    """Even a single FAIL blocks convergence — strict 0-FAIL rule."""
    ref = tmp_path / "ref"
    _write_result(ref, "**Result: 13 PASS, 1 FAIL, 0 SKIP, 13 STRUCTURAL_ONLY**")
    proc = _run(ref)
    assert proc.returncode == 1


def test_result_line_not_at_eof_is_still_found(tmp_path: Path) -> None:
    """Real result.txt files have an explanatory trailer after the Result
    line (e.g., 'Severity is based on AE/Mpx ...'). The script must find
    the last Result line, not just inspect file's last line."""
    ref = tmp_path / "ref"
    _write_result(
        ref,
        "**Result: 8 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**",
        trailer="(Severity is based on AE/Mpx — defect density per megapixel — not raw AE.)",
    )
    proc = _run(ref)
    assert proc.returncode == 0


def test_last_result_line_wins_when_multiple(tmp_path: Path) -> None:
    """If result.txt was appended-to and has multiple Result lines, the
    last one is authoritative (matches `tail -1 | grep` semantics)."""
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True)
    (sections / "result.txt").write_text(
        "**Result: 5 PASS, 2 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
        "more rows after re-run...\n"
        "**Result: 7 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
    )
    proc = _run(ref)
    assert proc.returncode == 0, "last Result line shows 0 FAIL — should converge"


def test_missing_ref_dir_exits_two(tmp_path: Path) -> None:
    """Ref dir doesn't exist → setup error (exit 2), not a silent pass."""
    proc = _run(tmp_path / "does-not-exist")
    assert proc.returncode == 2


def test_missing_sections_result_txt_exits_two(tmp_path: Path) -> None:
    """Ref exists but sections/result.txt missing → setup error."""
    ref = tmp_path / "ref"
    ref.mkdir()
    proc = _run(ref)
    assert proc.returncode == 2


def test_result_txt_without_result_line_exits_two(tmp_path: Path) -> None:
    """File present but no `**Result: ...**` line → cannot decide → exit 2.
    Refuses to silently pass on garbage input."""
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True)
    (sections / "result.txt").write_text("| row | … | PASS |\nno result summary here\n")
    proc = _run(ref)
    assert proc.returncode == 2


def test_no_args_exits_two(tmp_path: Path) -> None:
    """Called with no args → usage error → exit 2."""
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 2


def test_early_exit_three_field_format_is_recognized_as_fail(tmp_path: Path) -> None:
    """section-compare.sh has TWO Result formats:
      4-field (normal): `**Result: N PASS, N FAIL, N SKIP, N STRUCTURAL_ONLY**`
      3-field (early exit when 0 sections match): `**Result: 0 PASS, 1 FAIL, 0 SKIP**`

    Real Stage B output triggered the 3-field early-exit case (fingerprint
    extraction failed for linear.app). check-converged must treat the
    3-field format as a real Result line, not a setup error.
    """
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True)
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        "| (none) | — | — | — | ❌ |\n\n"
        "**Result: 0 PASS, 1 FAIL, 0 SKIP**\n\n"
        "FAILURE REASON: 0 sections matched — fingerprint extraction failed.\n"
    )
    proc = _run(ref)
    assert proc.returncode == 1, (
        f"3-field early-exit Result with 1 FAIL must exit 1 (not converged), "
        f"not 2 (setup error). got={proc.returncode}, stdout={proc.stdout}"
    )


def test_early_exit_three_field_zero_fail_converges(tmp_path: Path) -> None:
    """3-field format with 0 FAIL → converged. (Defensive: probably never
    happens in practice but the parser shouldn't silently reject it.)"""
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True)
    (sections / "result.txt").write_text(
        "**Result: 3 PASS, 0 FAIL, 0 SKIP**\n"
    )
    proc = _run(ref)
    assert proc.returncode == 0


def _read_stamp(ref: Path) -> dict[str, Any]:
    path = ref / "structural-convergence-stamp.json"
    assert path.is_file(), f"stamp not written at {path}"
    import json as _json
    return cast(dict[str, Any], _json.loads(path.read_text()))


def test_write_stamp_flag_emits_structural_stamp_on_convergence(tmp_path: Path) -> None:
    """--write-stamp on a converged ref writes structural-convergence-stamp.json
    with canonical fields. Stop hook will consume this as the structural
    closeout proof (codex review: separate stamp, NOT scope-down of
    verify-stamp.json, to preserve the canonical contract)."""
    ref = tmp_path / "ref"
    _write_result(ref, "**Result: 3 PASS, 0 FAIL, 7 SKIP, 3 STRUCTURAL_ONLY**")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), "--write-stamp"],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, f"converged but exit {proc.returncode}: {proc.stderr}"
    stamp = _read_stamp(ref)
    assert stamp["schemaVersion"] == 1
    assert stamp["closeoutKind"] == "structural"
    assert stamp["stampedBy"] == "scripts/verify/check-converged.sh"
    assert stamp["sectionResult"].startswith("**Result: 3 PASS, 0 FAIL")
    # Hash of sections/result.txt — Stop hook uses this to detect post-stamp
    # tampering of the underlying convergence evidence.
    assert len(stamp["sectionsResultSha256"]) == 64
    # verifiedAt is ISO8601 UTC (matches verify-stamp.json format used by
    # _enforce_verify_stamp's strptime "%Y-%m-%dT%H:%M:%SZ").
    assert stamp["verifiedAt"].endswith("Z") and "T" in stamp["verifiedAt"]


def test_write_stamp_with_stage_flag_records_stage(tmp_path: Path) -> None:
    """--stage A/B/C/D records the stage label in the stamp so the receipt
    builder can attribute convergence to the right pipeline stage."""
    ref = tmp_path / "ref"
    _write_result(ref, "**Result: 1 PASS, 0 FAIL, 9 SKIP, 1 STRUCTURAL_ONLY**")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), "--write-stamp", "--stage", "B"],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0
    stamp = _read_stamp(ref)
    assert stamp["stage"] == "B"


def test_write_stamp_not_emitted_when_not_converged(tmp_path: Path) -> None:
    """Non-converged ref → exit 1 AND no stamp written. The stamp is the
    convergence-attested artifact; emitting it on failure would let later
    Stop-hook reads grant a false canonical pass."""
    ref = tmp_path / "ref"
    _write_result(ref, "**Result: 0 PASS, 3 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), "--write-stamp"],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 1
    assert not (ref / "structural-convergence-stamp.json").exists()


def test_write_stamp_rejects_invalid_stage(tmp_path: Path) -> None:
    """--stage Z is rejected with exit 2 (setup error) — refuse to attribute
    convergence to a stage that doesn't exist in the plan."""
    ref = tmp_path / "ref"
    _write_result(ref, "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), "--write-stamp", "--stage", "Z"],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 2


def test_no_write_stamp_flag_preserves_legacy_behavior(tmp_path: Path) -> None:
    """Default invocation (no --write-stamp) must NOT write the stamp.
    Existing finalize-stage.sh callers don't pass the flag yet — adding the
    flag is opt-in. Regression guard for the just-shipped check-converged.sh."""
    ref = tmp_path / "ref"
    _write_result(ref, "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0
    assert not (ref / "structural-convergence-stamp.json").exists()


def test_partial_impl_with_missing_sections_converges_naturally(tmp_path: Path) -> None:
    """Section-staged Loop B emits hero-only impl; out-of-scope sections become
    MISSING impl → SKIP (not FAIL) per section-compare.sh logic. The convergence
    detector therefore exits 0 without any explicit SECTIONS scoping.

    This is the rationale for NOT building UI_CLONE_VERIFY_SECTIONS plumbing into
    verification-plan.sh / run-required-checks.sh / pipeline.py — the existing
    SKIP-not-FAIL semantic of MISSING impl naturally scopes convergence to the
    sections actually rendered.

    Pre-condition for this to hold: the impl must NOT emit stub components for
    out-of-scope sections (stubs would render → FAIL, not skip). This is enforced
    via the per-stage prompt template, not via infra.
    """
    ref = tmp_path / "ref"
    _write_result(ref, "**Result: 1 PASS, 0 FAIL, 13 SKIP, 0 STRUCTURAL_ONLY**")
    proc = _run(ref)
    assert proc.returncode == 0, (
        f"hero-only impl with 13 MISSING-impl sections should converge under the "
        f"chosen done criterion (sections/result.txt 0-FAIL alone). "
        f"stdout={proc.stdout} stderr={proc.stderr}"
    )
