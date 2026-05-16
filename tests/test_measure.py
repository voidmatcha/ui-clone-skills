"""Tests for ui_clone.measure — the locked-env Python orchestrator.

The whole point of measure.py is that the bash measurement scripts run
with `EXCLUDE_DYNAMIC=1` and `SECTION_THRESHOLD=2000` regardless of
what the caller passes. Mock subprocess.run and assert the env passed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from ui_clone import measure


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_section_compare_locks_exclude_dynamic_and_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    """`measure section-compare` MUST invoke bash with EXCLUDE_DYNAMIC=1
    and SECTION_THRESHOLD=2000, even when the parent shell sets them to
    permissive values. Locks down the d19e28d gaming pattern where the
    agent set SECTION_THRESHOLD=250000 to re-classify critical→minor.
    """
    captured_env: dict[str, str] = {}

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        captured_env.update(env)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir="/tmp/fake-ref",
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
    )
    with mock.patch.dict(os.environ, {
        "EXCLUDE_DYNAMIC": "0",         # caller's permissive default
        "SECTION_THRESHOLD": "250000",  # caller's gaming attempt
    }, clear=False), mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_section_compare(args)

    assert captured_env["EXCLUDE_DYNAMIC"] == "1", (
        f"EXCLUDE_DYNAMIC must be locked to 1, got: {captured_env.get('EXCLUDE_DYNAMIC')!r}"
    )
    assert captured_env["SECTION_THRESHOLD"] == "2000", (
        f"SECTION_THRESHOLD must be locked to 2000, got: {captured_env.get('SECTION_THRESHOLD')!r}"
    )
    # Status JSON on stdout
    out = capsys.readouterr().out.strip().splitlines()
    status = json.loads(out[-1])
    assert status["step"] == "section-compare"
    assert status["locked_env"] == {"EXCLUDE_DYNAMIC": "1", "SECTION_THRESHOLD": "2000"}


def test_transition_compare_does_not_lock_section_threshold() -> None:
    """transition-compare has its own scoring; the SECTION_THRESHOLD lock
    is irrelevant there. Only section-compare gets the AE-classifier lock.
    """
    captured_env: dict[str, str] = {}

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        captured_env.update(env)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir="/tmp/fake-ref",
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
    )
    with mock.patch.dict(os.environ, {"SECTION_THRESHOLD": "999"}, clear=False), \
         mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_transition_compare(args)
    # transition-compare doesn't override SECTION_THRESHOLD — caller's value passes through.
    assert captured_env["SECTION_THRESHOLD"] == "999"


def test_all_runs_section_compare_first(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """The `all` subcommand orders section-compare BEFORE transition-compare —
    static fidelity is measured first so motion noise doesn't contaminate
    the structural verdict.
    """
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    # Make transition-spec.json exist so transition-compare is included
    (ref_dir / "transition-spec.json").write_text(json.dumps({"transitions": []}))

    call_order: list[str] = []

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        # Extract script name from the bash invocation
        for c in cmd:
            if "section-compare.sh" in c:
                call_order.append("section-compare")
            elif "transition-compare.sh" in c:
                call_order.append("transition-compare")
            elif "asset-utilization-check.sh" in c:
                call_order.append("asset-utilization")
            elif "bundle-impl-coverage-check.sh" in c:
                call_order.append("bundle-impl-coverage")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir=str(ref_dir),
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
        impl_src=None,
        impl_pkg=None,
    )
    with mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_all(args)

    assert call_order[0] == "section-compare", (
        f"section-compare must run FIRST in the canonical sequence: {call_order}"
    )
    assert "transition-compare" in call_order
    assert call_order.index("section-compare") < call_order.index("transition-compare")


def test_all_skips_transition_compare_when_no_spec(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """No transition-spec.json → transition-compare skipped (recorded as skip
    in summary). The bash script would otherwise error on missing input.
    """
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    # No transition-spec.json

    invoked_scripts: list[str] = []

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        for c in cmd:
            if c.endswith(".sh"):
                invoked_scripts.append(Path(c).name)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir=str(ref_dir),
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
        impl_src=None,
        impl_pkg=None,
    )
    with mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_all(args)

    assert "transition-compare.sh" not in invoked_scripts, (
        f"transition-compare must be skipped when no spec exists; invoked: {invoked_scripts}"
    )
    out = capsys.readouterr().out.strip().splitlines()
    final = json.loads(out[-1])
    skip_entry = next((s for s in final["summary"] if s["step"] == "transition-compare"), None)
    assert skip_entry is not None
    assert skip_entry["exit_code"] == "skip"


def test_module_invocation_help_works() -> None:
    """`python -m ui_clone.measure --help` exits 0 with usage on stdout."""
    proc = subprocess.run(
        [sys.executable, "-m", "ui_clone.measure", "--help"],
        capture_output=True, text=True,
        cwd=_project_root(),
    )
    assert proc.returncode == 0
    assert "section-compare" in proc.stdout
    assert "asset-utilization" in proc.stdout
    assert "bundle-impl-coverage" in proc.stdout


def test_locked_defaults_exposed_for_audit() -> None:
    """The LOCKED_DEFAULTS dict must be importable so tooling/docs can
    surface which env vars are pinned without parsing source.
    """
    assert "EXCLUDE_DYNAMIC" in measure.LOCKED_DEFAULTS
    assert measure.LOCKED_DEFAULTS["EXCLUDE_DYNAMIC"] == "1"
    assert "SECTION_THRESHOLD" in measure.LOCKED_DEFAULTS
    assert measure.LOCKED_DEFAULTS["SECTION_THRESHOLD"] == "2000"


def test_dom_extraction_captures_direct_text() -> None:
    """Regression (Fix 6 v1): the DOM extraction eval in dom-extraction.md
    MUST capture each element's direct text (own text nodes, not descendants').
    Without `text` in the extracted schema, Phase 4 has no verbatim text to
    paste — agent fabricates from class names / URLs / asset filenames. The
    3-round benchmark showed Hero generated with "Eat Real Food" while the
    real ref hero said "Real Food Wins".
    """
    doc = _project_root() / "skills" / "ui-reverse-engineering" / "dom-extraction.md"
    text = doc.read_text(encoding="utf-8")

    # The direct-text helper that captures own-text without recursing into
    # descendants — keeps structure.json from exploding with duplicated text.
    assert "directText" in text, "dom-extraction.md must define directText helper"
    assert "nodeType === 3" in text, (
        "directText must filter to text nodes (nodeType === 3) to avoid "
        "capturing nested element duplicates"
    )
    # The extract function must populate `text` from the helper.
    assert "out.text = text" in text or "text: directText" in text, (
        "dom-extraction.md extract() must populate a `text` field on each node"
    )


def test_fix8_dom_scaffold_script_present() -> None:
    """Fix 8 — dom-scaffold.sh produces the source-of-truth scaffold for
    Phase 4 generation. Locks the script + its key responsibilities so a
    future refactor can't silently remove the determinism layer that
    closed the V4 (avg ~463k AE) → expected-V5 fidelity gap.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "dom-scaffold.sh"
    assert script.is_file(), "dom-scaffold.sh missing — Fix 8 incomplete"
    body = script.read_text(encoding="utf-8")
    # Reads the three Phase-2 artifacts.
    for input_name in ("structure.json", "styles.json", "section-map.json"):
        assert input_name in body, f"dom-scaffold.sh must read {input_name}"
    # Writes the canonical output path.
    assert "dom-scaffold.json" in body, "dom-scaffold.sh must write dom-scaffold.json"
    # Style keys carried through to the scaffold tree.
    for key in ("bg", "color", "ff", "fs", "fw", "lh"):
        assert f'"{key}"' in body, f"dom-scaffold.sh must carry styles.{key}"


def test_fix8_text_fidelity_check_script_present() -> None:
    """Fix 8 — text-fidelity-check.sh is the post-Phase-4 gate that blocks
    JSX text-position strings not present in the scaffold allowlist. Locks
    the script + the canonical fabrication-detection regex patterns.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    assert script.is_file(), "text-fidelity-check.sh missing — Fix 8 incomplete"
    body = script.read_text(encoding="utf-8")
    # Reads dom-scaffold as the allowlist source.
    assert "dom-scaffold.json" in body
    # Emits the canonical output artifact.
    assert "text-fidelity-check" in body  # appears in OUT name + identity
    # Has the fabrication-detection logic ("status": "fail" branch).
    assert "fabrications" in body, "must enumerate fabrications"


def test_fix8_dom_mirror_check_script_present() -> None:
    """Fix 8 — dom-mirror-check.sh compares impl JSX tag-multiset to the
    scaffold's tag-multiset. Locks the divergence-threshold default + that
    the script writes its verdict to dom-mirror-check.json.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "dom-mirror-check.sh"
    assert script.is_file(), "dom-mirror-check.sh missing — Fix 8 incomplete"
    body = script.read_text(encoding="utf-8")
    assert "dom-scaffold.json" in body
    assert "divergence" in body, "must report divergence percentage"
    # 30% default threshold (any change to default should be intentional).
    assert "THRESHOLD=30" in body, "default divergence threshold should be 30%"


def test_fix8_verification_plan_dispatches_new_gates() -> None:
    """Fix 8 — verification-plan.sh must dispatch text-fidelity-check and
    dom-mirror-check at tier=quick (static analysis, cheap) with severity=block.
    Without this dispatch the gates exist as scripts but never run as gates.
    """
    plan = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    body = plan.read_text(encoding="utf-8")
    assert 'add_check "text-fidelity-check"' in body, (
        "verification-plan.sh must dispatch text-fidelity-check"
    )
    assert 'add_check "dom-mirror-check"' in body, (
        "verification-plan.sh must dispatch dom-mirror-check"
    )


def test_section_spec_script_present_and_callable() -> None:
    """Regression (Fix 6 v2): section-spec.sh must exist with the required
    flags (--label, --out, --metadata, --text) so Phase 2.6 grounding can run
    on each section. Without this step Phase 4 has no LLM-verified spec and
    falls back to inferring from extracted JSON.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-spec.sh"
    assert script.is_file(), "section-spec.sh must exist for Phase 2.6"
    body = script.read_text(encoding="utf-8")
    # Required flags
    assert "--label" in body, "section-spec.sh must accept --label"
    assert "--out" in body, "section-spec.sh must accept --out"
    assert "--metadata" in body, "section-spec.sh must accept --metadata"
    assert "--text" in body, "section-spec.sh must accept --text"
    # Calls claude --print (LLM-driven, not script-only)
    assert "claude --print" in body, (
        "section-spec.sh must call claude --print — Fix 6 v2 is LLM-driven"
    )
    # Prompt template exists
    prompt = _project_root() / "skills" / "visual-debug" / "prompts" / "section-spec.md"
    assert prompt.is_file(), "section-spec.md prompt template must exist"
    prompt_text = prompt.read_text(encoding="utf-8")
    # Schema keys required for grounded generation
    for key in ('"label"', '"text"', '"colors"', '"typography"', '"layout"', '"key_elements"'):
        assert key in prompt_text, f"section-spec.md prompt missing schema key {key}"


def test_section_compare_synthesis_uses_correct_section_map_keys() -> None:
    """Regression: section-compare.sh synthesizes ref-sections from
    section-map.json when ENUMERATE_SECTIONS comes back too lean. The
    synthesis code MUST read `top`/`cls` keys (the actual schema written by
    extraction) — not just `y`/`class` (older fallback). The 3-round benchmark
    hit `gate_fail_counts[post-implement] == 632` because the synthesis only
    read `y`, collapsed every section's rect.top to 0, and produced phantom-ref
    coords that triggered uniform AE/Mpx ~950k across all sections. This is
    the data-key bug that made the prior three benchmark rounds' AE numbers
    meaningless.

    Locks the key-name reads in section-compare.sh as a guard. If a future
    refactor drops `s.get("top")` or `s.get("cls")` from the synthesis block,
    this test fires before the script is re-deployed.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    # The synthesis block must read both schemas (top/y, cls/class) so the
    # script tolerates whichever the upstream produced. Lock the canonical
    # patterns; either form fires test failure if dropped.
    assert 's.get("top") or s.get("y")' in text, (
        "section-compare.sh synthesis must read s.get('top') with fallback to "
        "s.get('y') — the 632-retry bug came from reading only 'y'"
    )
    assert 's.get("cls") or s.get("class")' in text, (
        "section-compare.sh synthesis must read s.get('cls') with fallback to "
        "s.get('class') — section-map.json writes 'cls', not 'class'"
    )
