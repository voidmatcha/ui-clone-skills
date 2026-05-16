"""Tests for host-neutral UI clone goal cards."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _write_state(ref_dir: Path, current_gate: str) -> None:
    ref_dir.mkdir(parents=True, exist_ok=True)
    completed = {
        "reference": [],
        "spec": ["reference", "extraction", "bundle", "paid-features"],
        "pre-generate": ["reference", "extraction", "bundle", "paid-features", "spec"],
        "section-compare": [
            "reference",
            "extraction",
            "bundle",
            "paid-features",
            "spec",
            "pre-generate",
            "post-implement",
            "boundary",
            "font-parity",
        ],
        "done": [
            "reference",
            "extraction",
            "bundle",
            "paid-features",
            "spec",
            "pre-generate",
            "post-implement",
            "boundary",
            "font-parity",
            "section-compare",
        ],
    }[current_gate]
    (ref_dir / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": ref_dir.name,
                "started_at": "2026-01-01T00:00:00Z",
                "completed_steps": completed,
                "current_gate": current_gate,
                "last_updated": "2026-01-01T01:00:00Z",
            }
        ),
        encoding="utf-8",
    )


def test_goal_card_maps_reference_gate(tmp_path: Path) -> None:
    from ui_clone.goal import build_goal_card

    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state(ref_dir, "reference")

    card = build_goal_card(ref_dir)

    assert "Goal Card: hero" in card
    assert "Mission:" in card
    assert "Current goal: Capture reference evidence" in card
    assert "Next action: Run /ui-capture" in card
    assert "Required evidence: static/ref/ >=5 PNGs" in card
    assert "No infinite loop" in card


def test_goal_card_maps_spec_gate(tmp_path: Path) -> None:
    from ui_clone.goal import build_goal_card

    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state(ref_dir, "spec")

    card = build_goal_card(ref_dir)

    assert "Current goal: Finalize transition spec and verification plan" in card
    assert "python -m ui_clone.gate" in card
    assert "transition-spec.json" in card
    assert "verification-plan.json" in card


def test_goal_card_maps_section_compare_gate(tmp_path: Path) -> None:
    from ui_clone.goal import build_goal_card

    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state(ref_dir, "section-compare")

    card = build_goal_card(ref_dir)
    assert "Current goal: Prove every captured section matches" in card
    assert "section-compare.sh" in card
    assert "sections/result.txt" in card
    assert "0 FAIL lines" in card
    assert "0 MISSING impl lines" in card


def test_section_compare_with_failing_rows_routes_to_visual_judge(tmp_path: Path) -> None:
    """When current_gate=='section-compare' and result.txt has FAIL rows with
    matching ref/impl section PNGs, the next-action should override the generic
    section-compare invocation with a concrete visual-judge.sh command.
    """
    from ui_clone.goal import build_goal_card

    ref_dir = tmp_path / "tmp" / "ref" / "realfood"
    _write_state(ref_dir, "section-compare")

    sections_dir = ref_dir / "sections"
    ref_pngs = sections_dir / "ref"
    impl_pngs = sections_dir / "impl"
    ref_pngs.mkdir(parents=True)
    impl_pngs.mkdir(parents=True)
    # Fake PNGs for the top-3 worst-AE rows.
    for name in ("section-1", "section-4", "section-9"):
        (ref_pngs / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (impl_pngs / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    (sections_dir / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---------|-----|--------|----------|--------|\n"
        "| section-0 | 174645 | 134757 | critical | ❌ |\n"
        "| section-1 | 1227100 | 946836 | critical | ❌ |\n"
        "| section-4 | 1231280 | 950062 | critical | ❌ |\n"
        "| section-9 | 1228740 | 948102 | critical | ❌ |\n"
        "\n"
        "**Result: 0 PASS, 4 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )

    card = build_goal_card(ref_dir)
    # The override engaged — generic section-compare phrasing dropped, concrete
    # visual-judge commands present.
    assert "visual-judge.sh" in card
    # Top-3 worst-AE rows (section-4 950062, section-9 948102, section-1 946836)
    # appear in command examples — order doesn't matter, presence does.
    assert "section-1" in card
    assert "section-4" in card
    assert "section-9" in card
    # `selector_hint` and `priority_fix` are documented in the routing message
    # so the worker knows what to read from the visual-judge output.
    assert "priority_fix" in card or "selector_hint" in card


def test_visual_judge_routing_fires_on_post_implement_too(tmp_path: Path) -> None:
    """The post-implement gate transitively fails when section-compare has
    FAIL rows, but `current_gate` stays at 'post-implement' and never advances
    to 'section-compare'. The 3-round benchmark (Round 1 / A / B) observed
    `gate_fail_counts[post-implement]` climbing past 400 while the section-
    compare branch of the visual-judge override never fired. The override now
    fires on post-implement as well, breaking the runaway-retry loop.
    """
    from ui_clone.goal import build_goal_card

    ref_dir = tmp_path / "tmp" / "ref" / "realfood"
    _write_state(ref_dir, "section-compare")
    # Overwrite the state we just wrote: we want current_gate=='post-implement'
    # (the failing gate that ACTUALLY traps real benchmark runs), with the
    # section-compare evidence on disk that triggers the visual-judge override.
    (ref_dir / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": ref_dir.name,
                "started_at": "2026-01-01T00:00:00Z",
                "completed_steps": [
                    "reference",
                    "extraction",
                    "bundle",
                    "paid-features",
                    "spec",
                    "pre-generate",
                ],
                "current_gate": "post-implement",
                "last_updated": "2026-01-01T01:00:00Z",
                "gate_fail_counts": {"post-implement": 41},
            }
        ),
        encoding="utf-8",
    )

    sections_dir = ref_dir / "sections"
    ref_pngs = sections_dir / "ref"
    impl_pngs = sections_dir / "impl"
    ref_pngs.mkdir(parents=True)
    impl_pngs.mkdir(parents=True)
    for name in ("section-1", "section-9"):
        (ref_pngs / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (impl_pngs / f"{name}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    (sections_dir / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---------|-----|--------|----------|--------|\n"
        "| section-1 | 1227100 | 946836 | critical | ❌ |\n"
        "| section-9 | 1256000 | 969136 | critical | ❌ |\n",
        encoding="utf-8",
    )

    card = build_goal_card(ref_dir)
    # Override fired even though current_gate is post-implement, not
    # section-compare — the routing now reads result.txt for FAIL rows
    # regardless of which of the two gates the pipeline is stuck on.
    assert "visual-judge.sh" in card
    assert "section-1" in card
    assert "section-9" in card


def test_visual_judge_routing_skips_when_no_result(tmp_path: Path) -> None:
    """If sections/result.txt is absent (section-compare hasn't run yet), the
    next-action falls back to the generic section-compare invocation. The
    override fires only AFTER the first section-compare run has produced FAIL
    rows — otherwise we'd suggest visual-judge before there's anything to judge.
    """
    from ui_clone.goal import build_goal_card

    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state(ref_dir, "section-compare")
    # No sections/result.txt; PNG dirs absent — override should not fire.

    card = build_goal_card(ref_dir)
    assert "section-compare.sh" in card
    assert "visual-judge" not in card


def test_goal_card_done_requires_clean_section_compare(tmp_path: Path) -> None:
    from ui_clone.goal import build_goal_card

    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state(ref_dir, "done")
    result = ref_dir / "sections" / "result.txt"
    result.parent.mkdir(parents=True)
    result.write_text("| hero | PASS | ok |\n", encoding="utf-8")

    card = build_goal_card(ref_dir)
    assert "Current goal: Stop" in card
    assert 'Stop condition: current_gate == "done"' in card
    assert "section comparison has no FAIL / MISSING impl lines" in card
    assert "Required evidence: sections/result.txt" in card


def test_goal_card_unknown_gate_does_not_suggest_invalid_gate_command(tmp_path: Path) -> None:
    from ui_clone.goal import build_goal_card

    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": "hero",
                "started_at": "2026-01-01T00:00:00Z",
                "completed_steps": [],
                "current_gate": "nonexistent-gate-name",
                "last_updated": "2026-01-01T01:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    card = build_goal_card(ref_dir)

    assert "Current gate: nonexistent-gate-name" in card
    assert "Current goal: Resolve invalid pipeline state" in card
    assert "python -m ui_clone.gate" not in card
    assert "valid current_gate" in card


def test_goal_card_cli_prints_card(tmp_path: Path) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state(ref_dir, "pre-generate")

    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.goal", str(ref_dir)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )

    assert result.returncode == 0
    assert "Goal Card: hero" in result.stdout
    assert "Current goal: Resolve pre-generation readiness" in result.stdout
    assert "python -m ui_clone.gate" in result.stdout


def test_goal_card_cli_prints_json(tmp_path: Path) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state(ref_dir, "done")
    result_file = ref_dir / "sections" / "result.txt"
    result_file.parent.mkdir(parents=True)
    result_file.write_text("| hero | PASS | ok |\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.goal", str(ref_dir), "--json"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["component"] == "hero"
    assert data["current_gate"] == "done"
    assert data["current_goal"] == "Stop"
    assert data["next_action"].startswith("Do not continue automatically")
    assert data["stop_condition"] == (
        'current_gate == "done" and section comparison has no FAIL / MISSING impl lines.'
    )
    assert data["required_evidence"] == 'sections/result.txt and current_gate == "done"'
    assert data["manual_refresh"] == f"python -m ui_clone.goal {ref_dir}"
    assert data["no_infinite_loop"].startswith("this card describes one bounded next action")
    assert data["stop_evidence_status"] == (
        "satisfied: sections/result.txt has 0 FAIL lines and 0 MISSING impl lines"
    )


def test_goal_check_done_exits_zero_when_done_and_clean(tmp_path: Path) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state(ref_dir, "done")
    result_file = ref_dir / "sections" / "result.txt"
    result_file.parent.mkdir(parents=True)
    result_file.write_text("| hero | PASS | ok |\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.goal", str(ref_dir), "--check-done"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_goal_check_done_exits_one_when_done_but_section_compare_failing(tmp_path: Path) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state(ref_dir, "done")
    result_file = ref_dir / "sections" / "result.txt"
    result_file.parent.mkdir(parents=True)
    result_file.write_text("| hero | ❌ FAIL | diff > threshold |\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.goal", str(ref_dir), "--check-done"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )

    assert result.returncode == 1


def test_goal_check_done_exits_one_when_not_done(tmp_path: Path) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state(ref_dir, "spec")

    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.goal", str(ref_dir), "--check-done"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )

    assert result.returncode == 1


def test_goal_check_done_ignores_result_footer_fail_substring(tmp_path: Path) -> None:
    """Regression: section-compare.sh emits a summary footer line containing
    the literal substring "FAIL" even when 0 sections failed:

      **Result: 2 PASS, 0 FAIL, 0 SKIP, 2 STRUCTURAL_ONLY**

    Earlier the matcher counted any line with "FAIL" as a failed row, which
    mis-flagged this footer and broke external loop drivers' completion detection on every
    clean run. The fix narrows the count to markdown table rows beginning with
    "|" that carry the ❌ status marker. This test locks that in.
    """
    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state(ref_dir, "done")
    result_file = ref_dir / "sections" / "result.txt"
    result_file.parent.mkdir(parents=True)
    result_file.write_text(
        "| section-0 | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n"
        "| footer    | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n"
        "\n"
        "**Result: 2 PASS, 0 FAIL, 0 SKIP, 2 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.goal", str(ref_dir), "--check-done"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )

    assert result.returncode == 0, (
        f"--check-done must not trip on the '0 FAIL' summary footer; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_codex_default_prompt_mentions_goal_card_and_stop_condition() -> None:
    plugin_path = Path(__file__).resolve().parents[1] / ".codex-plugin" / "plugin.json"
    prompt = "\n".join(
        json.loads(plugin_path.read_text(encoding="utf-8"))["interface"]["defaultPrompt"]
    )

    assert "goal card" in prompt.lower()
    assert "python -m ui_clone.goal <ref-dir>" in prompt
    assert 'current_gate == "done"' in prompt
    assert "not an infinite loop" in prompt.lower()


def test_readme_documents_goal_driven_continuation_without_new_public_skill() -> None:
    repo = Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    skill_dirs = sorted(path.name for path in (repo / "skills").iterdir() if path.is_dir())

    assert "goal card" in readme.lower()
    assert "python -m ui_clone.goal <ref-dir>" in readme
    # Agent-driven continuation lives in a single Claude Code / Codex
    # session — explicitly NOT an external scheduler / daemon.
    assert "no external scheduler" in readme.lower()
    assert "background daemon" in readme.lower() or "no external scheduler" in readme.lower()
    # 3 public + N internal (see scripts/ci/review.sh internal_skills allowlist + AGENTS.md).
    internal_skills = {"benchmark"}
    public_skill_dirs = [d for d in skill_dirs if d not in internal_skills]
    assert public_skill_dirs == ["ui-capture", "ui-reverse-engineering", "visual-debug"]


# ── Stuck banner + abort banner + --check-done exit codes ──


def _write_state_full(ref_dir: Path, **fields: object) -> None:
    """Write pipeline-state.json from arbitrary fields, defaulting completed_steps to []."""
    ref_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "component": ref_dir.name,
        "started_at": "2026-01-01T00:00:00Z",
        "completed_steps": [],
        "current_gate": "reference",
        "last_updated": "2026-01-01T01:00:00Z",
        "gate_fail_counts": {},
        "unclonable_reasons": [],
    }
    payload.update(fields)
    (ref_dir / "pipeline-state.json").write_text(json.dumps(payload), encoding="utf-8")


def test_goal_card_emits_stuck_banner_at_threshold(tmp_path: Path) -> None:
    """Three consecutive fails on the active gate → STUCK banner with diagnosis routing."""
    from ui_clone.goal import build_goal_card, build_goal_card_data

    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state_full(ref_dir, current_gate="extraction", gate_fail_counts={"extraction": 3})

    data = build_goal_card_data(ref_dir)
    assert data.stuck_banner is not None
    assert "STUCK" in data.stuck_banner
    assert "extraction" in data.stuck_banner
    assert "diagnosis.md" in data.stuck_banner

    card = build_goal_card(ref_dir)
    assert "STUCK" in card


def test_goal_card_no_stuck_banner_below_threshold(tmp_path: Path) -> None:
    from ui_clone.goal import build_goal_card_data

    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state_full(ref_dir, current_gate="extraction", gate_fail_counts={"extraction": 2})

    data = build_goal_card_data(ref_dir)
    assert data.stuck_banner is None


def test_goal_card_emits_abort_banner_when_unclonable(tmp_path: Path) -> None:
    from ui_clone.goal import build_goal_card, build_goal_card_data

    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state_full(
        ref_dir,
        current_gate="paid-features",
        unclonable_reasons=[
            {
                "gate": "paid-features",
                "reason": "Helvetica Now Display has no free substitution",
                "detected_at": "2026-01-01T02:00:00Z",
            }
        ],
    )

    data = build_goal_card_data(ref_dir)
    assert data.abort_banner is not None
    assert "ABORT" in data.abort_banner
    assert "Helvetica" in data.abort_banner

    card = build_goal_card(ref_dir)
    assert "ABORT" in card


def test_check_done_exit_2_on_abort(tmp_path: Path) -> None:
    """--check-done exits 2 when unclonable_reasons is non-empty, even if gate looks done."""
    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state_full(
        ref_dir,
        current_gate="paid-features",
        unclonable_reasons=[
            {"gate": "paid-features", "reason": "test abort", "detected_at": "2026-01-01T02:00:00Z"}
        ],
    )
    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.goal", str(ref_dir), "--check-done"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_check_done_exit_1_when_not_done(tmp_path: Path) -> None:
    ref_dir = tmp_path / "tmp" / "ref" / "hero"
    _write_state_full(ref_dir, current_gate="extraction")
    result = subprocess.run(
        [sys.executable, "-m", "ui_clone.goal", str(ref_dir), "--check-done"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
