"""Tests for scripts/loop/launch-stage.sh and scripts/loop/finalize-stage.sh.

These wrap the per-stage operational pattern from the convergence plan:
- launch-stage.sh: pure translator from <stage>{A,B,C,D} → shell commands
  the user copy-pastes to start a loop tab. No side effects.
- finalize-stage.sh: runs after a loop exits. Calls check-converged.sh,
  builds the receipt, prints the next-stage prev-receipt path.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "scripts" / "loop" / "launch-stage.sh"
FINALIZE = ROOT / "scripts" / "loop" / "finalize-stage.sh"


# ─── launch-stage.sh ────────────────────────────────────────────────────────

# Neutral launcher values: the script must not bake in any machine-specific
# multiplexer, workspace id, model, or permission mode.
LAUNCH_ENV = {
    "UI_CLONE_LOOP_MUX": "mux-cli",
    "UI_CLONE_LOOP_WORKSPACE": "workspace-1",
}
LAUNCH_PERSONAL_VARS = (
    "UI_CLONE_LOOP_MUX",
    "UI_CLONE_LOOP_WORKSPACE",
    "UI_CLONE_LOOP_AGENT_CMD",
    "UI_CLONE_LOOP_MODEL",
    "UI_CLONE_LOOP_PERMISSION_MODE",
    "UI_CLONE_LOOP_SHELL_PROMPT_ANSWER",
)


def _run_launch(
    stage: str, extra_env: dict[str, str] | None = None, *, base: bool = True
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in LAUNCH_PERSONAL_VARS}
    if base:
        env.update(LAUNCH_ENV)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(LAUNCH), stage],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


def test_launch_stage_a_emits_decode_command() -> None:
    """Stage A = decode sub-command, comprehensive tier, no SECTIONS scope."""
    proc = _run_launch("A")
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    out = proc.stdout
    assert "UI_CLONE_VERIFY_TIER=comprehensive" in out
    assert "decode" in out  # decode sub-command appears in prompt or invocation
    # No SECTIONS scope for decode-only stage
    assert "UI_CLONE_VERIFY_SECTIONS" not in out, (
        "Stage A is decode-only; no SECTIONS env should be set"
    )


def test_launch_stage_b_hero_only() -> None:
    """Stage B = clone, hero-only scope."""
    proc = _run_launch("B")
    assert proc.returncode == 0
    out = proc.stdout
    assert "clone" in out
    # Stage B is hero-only; the launch should at least surface 'hero' to the
    # prompt path or env so the agent knows the scope.
    assert "hero" in out.lower()


def test_launch_stage_d_full_scope_comprehensive() -> None:
    """Stage D = full verify, comprehensive tier."""
    proc = _run_launch("D")
    assert proc.returncode == 0
    out = proc.stdout
    assert "UI_CLONE_VERIFY_TIER=comprehensive" in out
    # Stage D references verify sub-command (re-uses impl from Stage C)
    assert "verify" in out


def test_launch_emits_env_driven_mux_tab_lines() -> None:
    """Every stage's launch output must include the multiplexer tab launch
    incantation, built from UI_CLONE_LOOP_MUX / UI_CLONE_LOOP_WORKSPACE."""
    for stage in ("A", "B", "C", "D"):
        proc = _run_launch(stage)
        assert proc.returncode == 0, f"stage {stage} failed: {proc.stderr}"
        assert "mux-cli tab create -w workspace-1" in proc.stdout, (
            f"stage {stage} missing env-driven tab create line"
        )
        assert 'mux-cli tab send -w workspace-1 "$TAB_ID"' in proc.stdout, (
            f"stage {stage} missing env-driven tab send line"
        )
        assert "--plugin-dir" in proc.stdout, (
            f"stage {stage} missing --plugin-dir flag (hooks won't fire without it)"
        )


def test_launch_requires_mux_and_workspace() -> None:
    """Unset required launcher values → exit 2 with a message naming the var."""
    for missing in ("UI_CLONE_LOOP_MUX", "UI_CLONE_LOOP_WORKSPACE"):
        env = {k: v for k, v in LAUNCH_ENV.items() if k != missing}
        proc = _run_launch("A", env, base=False)
        assert proc.returncode == 2, proc.stdout
        assert missing in proc.stderr
        assert "tab create" not in proc.stdout


def test_launch_defaults_omit_model_and_permission_flags() -> None:
    """Without UI_CLONE_LOOP_MODEL / _PERMISSION_MODE / _SHELL_PROMPT_ANSWER
    the launch uses host defaults: no --model, no --permission-mode, and no
    shell-prompt dismissal step."""
    proc = _run_launch("A")
    assert proc.returncode == 0, proc.stderr
    assert "--model" not in proc.stdout
    assert "--permission-mode" not in proc.stdout
    assert "Dismiss" not in proc.stdout
    assert "&& claude --plugin-dir" in proc.stdout


def test_launch_honours_model_permission_agent_and_prompt_answer_env() -> None:
    proc = _run_launch(
        "C",
        {
            "UI_CLONE_LOOP_AGENT_CMD": "agent-cli",
            "UI_CLONE_LOOP_MODEL": "model-x",
            "UI_CLONE_LOOP_PERMISSION_MODE": "mode-y",
            "UI_CLONE_LOOP_SHELL_PROMPT_ANSWER": "q",
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert "&& agent-cli --plugin-dir" in proc.stdout
    assert "--permission-mode mode-y" in proc.stdout
    assert "--model model-x" in proc.stdout
    assert 'mux-cli tab send -w workspace-1 "$TAB_ID" q\n' in proc.stdout


def test_launch_step_numbering_is_contiguous_with_and_without_prompt_answer() -> None:
    """The optional prompt-dismissal step must not leave a hole in the
    printed step numbers."""
    without = _run_launch("A")
    assert without.returncode == 0, without.stderr
    assert re.findall(r"^# (\d+)\) ", without.stdout, re.M) == ["1", "2", "3"]
    assert "Dismiss" not in without.stdout

    with_answer = _run_launch("A", {"UI_CLONE_LOOP_SHELL_PROMPT_ANSWER": "y"})
    assert with_answer.returncode == 0, with_answer.stderr
    assert re.findall(r"^# (\d+)\) ", with_answer.stdout, re.M) == ["1", "2", "3", "4"]
    assert "# 2) Dismiss the shell startup prompt:" in with_answer.stdout


def test_launch_shell_quotes_env_values() -> None:
    """Values with spaces or shell metacharacters are printed as single shell
    words (printf %q), so the copy-pasted command keeps them intact."""
    proc = _run_launch(
        "C",
        {
            "UI_CLONE_LOOP_WORKSPACE": "ws one",
            "UI_CLONE_LOOP_MODEL": "model x",
            "UI_CLONE_LOOP_PERMISSION_MODE": "mode;y",
            "UI_CLONE_LOOP_SHELL_PROMPT_ANSWER": "y $HOME",
            "UI_CLONE_LOOP_AGENT_CMD": "agent cli",
        },
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "tab create -w ws\\ one " in out
    assert "--model model\\ x" in out
    assert "--permission-mode mode\\;y" in out
    assert "&& agent\\ cli --plugin-dir" in out
    assert re.search(r'"\$TAB_ID" (y\\ \\\$HOME|\'y \$HOME\')\n', out), out
    # Stage C's placeholder section ids are angle-bracketed; they must not be
    # printed as bare redirections (%q also escapes the commas).
    assert "UI_CLONE_VERIFY_SECTIONS=hero\\,\\<sec2\\>\\,\\<sec3\\>" in out


def test_launch_script_carries_no_personal_launcher_values() -> None:
    """The script must not embed a multiplexer name, a workspace id, a model
    name, or a permission mode — those come from the environment."""
    source = LAUNCH.read_text(encoding="utf-8")
    for token in ("--model opus", "--permission-mode auto", " ws-"):
        assert token not in source, f"personal launcher value baked in: {token!r}"
    assert "UI_CLONE_LOOP_WORKSPACE" in source


def test_launch_emits_prompt_file_reference() -> None:
    """Each stage's launch should reference the per-stage prompt template
    so the user knows where the prompt lives."""
    for stage in ("A", "B", "C", "D"):
        proc = _run_launch(stage)
        assert proc.returncode == 0
        assert f"loop-{stage}" in proc.stdout, (
            f"stage {stage} should reference scratch/loop-{stage}/prompt.txt"
        )


def test_launch_invalid_stage_exits_two() -> None:
    """Unknown stage label → exit 2."""
    proc = _run_launch("Z")
    assert proc.returncode == 2


def test_launch_no_args_exits_two() -> None:
    """No args → exit 2."""
    proc = subprocess.run(
        ["bash", str(LAUNCH)], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 2


# ─── finalize-stage.sh ──────────────────────────────────────────────────────

def _write_state(ref: Path, target_url: str = "https://linear.app") -> None:
    """Minimal pipeline-state.json so build-decode-receipt.sh accepts the ref."""
    (ref / "pipeline-state.json").write_text(json.dumps({
        "component": ref.name,
        "targetUrl": target_url,
        "unclonable_reasons": [],
    }))


def _write_converged_result(ref: Path) -> None:
    sections = ref / "sections"
    sections.mkdir(parents=True)
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        "| hero | 100 | 50 | ok | ✅ |\n\n"
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
    )


def _write_failing_result(ref: Path) -> None:
    sections = ref / "sections"
    sections.mkdir(parents=True)
    (sections / "result.txt").write_text(
        "**Result: 0 PASS, 3 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
    )


def _run_finalize(
    ref: Path, stage: str, outbox_root: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(FINALIZE), str(ref), stage],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PLUGIN_ROOT": str(outbox_root), "PATH": "/usr/bin:/bin"},
    )


def test_finalize_converged_exits_zero_and_writes_receipt(tmp_path: Path) -> None:
    """Convergence + receipt build = success. Uses Stage B because Stage A's
    success criterion is decode artifacts (no sections/result.txt). See
    test_finalize_stage_a_succeeds_without_sections_result for Stage A path."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref)
    _write_converged_result(ref)

    proc = _run_finalize(ref, "B", tmp_path)
    assert proc.returncode == 0, f"stderr={proc.stderr}\nstdout={proc.stdout}"
    receipts = list((tmp_path / "outbox").rglob("receipt.html"))
    assert receipts, "Receipt was not written under outbox/"
    assert "converged" in proc.stdout.lower()


def test_finalize_not_converged_exits_one_but_still_writes_receipt(
    tmp_path: Path,
) -> None:
    """Loop hasn't converged. Finalize still writes the receipt (so the next
    iteration has context), but exits 1 to surface the not-yet-done state."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref)
    _write_failing_result(ref)

    proc = _run_finalize(ref, "B", tmp_path)
    assert proc.returncode == 1, f"stderr={proc.stderr}\nstdout={proc.stdout}"
    receipts = list((tmp_path / "outbox").rglob("receipt.html"))
    assert receipts, "Receipt should be written even on non-convergence"


def test_finalize_missing_ref_exits_two(tmp_path: Path) -> None:
    """Bad ref dir → setup error."""
    proc = _run_finalize(tmp_path / "no-such", "A", tmp_path)
    assert proc.returncode == 2


def test_finalize_prints_next_stage_hint(tmp_path: Path) -> None:
    """When stage A converges, output should hint how Stage B picks up the
    receipt (cross-loop memory pattern from briefing §2D)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref)
    # Stage A success = decode artifacts; sections/result.txt is irrelevant here.
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 0, "className": "Hero_root"}],
    }))

    proc = _run_finalize(ref, "A", tmp_path)
    assert proc.returncode == 0
    assert "next" in proc.stdout.lower() or "stage b" in proc.stdout.lower(), (
        f"finalize should hint at next stage, got: {proc.stdout}"
    )


def test_finalize_stage_d_no_next_hint(tmp_path: Path) -> None:
    """Stage D is terminal — no 'next stage' hint, instead a 'plan complete'
    or equivalent terminal message."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref)
    _write_converged_result(ref)

    proc = _run_finalize(ref, "D", tmp_path)
    assert proc.returncode == 0
    # No specific assertion on wording; just confirm output doesn't say "Stage E".
    assert "stage e" not in proc.stdout.lower()


def test_finalize_invalid_stage_exits_two(tmp_path: Path) -> None:
    """Stage must be A/B/C/D."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref)
    _write_converged_result(ref)
    proc = _run_finalize(ref, "Z", tmp_path)
    assert proc.returncode == 2


def _write_stage_a_artifacts(ref: Path) -> None:
    """Stage A is decode-only — it produces section-map.json, transition-spec.json,
    motion-tokens.json, animations-detected.json. It does NOT produce
    sections/result.txt (no impl to compare against)."""
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "className": "Hero_root__abc"},
            {"index": 1, "className": "PageSection_root__def"},
        ],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "motion_signature": {"dominant_feel": "gentle"},
        "entries": [],
    }))
    (ref / "motion-tokens.json").write_text(json.dumps({"tokens": []}))
    (ref / "animations-detected.json").write_text(json.dumps({"animations": []}))


def test_finalize_stage_a_succeeds_without_sections_result(tmp_path: Path) -> None:
    """Stage A is decode-only — section-compare never runs, so sections/result.txt
    never gets written. finalize-stage A must NOT exit 1 just because that file is
    absent; it should validate Stage A's actual success criterion (decode artifacts
    present with non-empty section-map and a dominant_feel)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref)
    _write_stage_a_artifacts(ref)
    # Note: deliberately NOT writing sections/result.txt.

    proc = _run_finalize(ref, "A", tmp_path)
    assert proc.returncode == 0, (
        f"Stage A finalize must succeed on decode-only artifacts (the actual "
        f"Stage A reality), not exit 1 because sections/result.txt is absent.\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_finalize_stage_a_fails_when_section_map_missing(tmp_path: Path) -> None:
    """Stage A success requires decode artifacts. If section-map.json is absent
    or empty, decode genuinely failed — exit 1 is correct."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref)
    # No section-map.json — decode didn't produce its key artifact.

    proc = _run_finalize(ref, "A", tmp_path)
    assert proc.returncode == 1, (
        f"Stage A with no section-map.json should fail. "
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_finalize_stage_a_fails_when_section_map_empty(tmp_path: Path) -> None:
    """Empty section-map.json means decode ran but extracted nothing useful."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref)
    (ref / "section-map.json").write_text(json.dumps({"sections": []}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "motion_signature": {"dominant_feel": "gentle"},
    }))

    proc = _run_finalize(ref, "A", tmp_path)
    assert proc.returncode == 1, (
        f"Stage A with empty section-map should fail. stdout={proc.stdout}"
    )


def test_finalize_stage_b_still_requires_sections_result(tmp_path: Path) -> None:
    """Stages B/C/D run section-compare → require sections/result.txt.
    Regression guard: the Stage-A relaxation must NOT silently apply to B/C/D."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_state(ref)
    _write_stage_a_artifacts(ref)  # decode artifacts present, but…
    # …no sections/result.txt → Stage B should still fail.

    proc = _run_finalize(ref, "B", tmp_path)
    assert proc.returncode == 1, (
        f"Stage B without sections/result.txt must still fail (it runs "
        f"section-compare). stdout={proc.stdout}\nstderr={proc.stderr}"
    )
