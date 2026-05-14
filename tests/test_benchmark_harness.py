"""Tests for ui_clone.benchmark_harness — Python-driven benchmark loop.

Mock `subprocess.run` for claude invocations; assert correct prompt
building, stop-condition checking, and outcome reporting.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from ui_clone import benchmark_harness


def _make_ref_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Return (ref_dir, impl_dir) pair under tmp_path."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    return ref, impl


def _write_state(ref_dir: Path, current_gate: str, gate_fails: dict | None = None) -> None:
    payload = {
        "component": ref_dir.name,
        "started_at": "2026-05-15T00:00:00Z",
        "completed_steps": [],
        "current_gate": current_gate,
        "last_updated": "2026-05-15T00:00:00Z",
        "gate_fail_counts": gate_fails or {},
        "unclonable_reasons": [],
    }
    (ref_dir / "pipeline-state.json").write_text(json.dumps(payload))


# ── check_strict_done ─────────────────────────────────────────────────────


def test_check_strict_done_fresh_state_unmet(tmp_path: Path) -> None:
    """Empty ref → many unmet conditions."""
    ref, impl = _make_ref_dir(tmp_path)
    _write_state(ref, "reference")
    done, unmet = benchmark_harness.check_strict_done(ref, impl)
    assert not done
    assert any("current_gate=" in u for u in unmet)
    assert any("page.tsx missing" in u for u in unmet)


def test_check_strict_done_all_satisfied(tmp_path: Path) -> None:
    """All STRICT v2 conditions met → done=True."""
    ref, impl = _make_ref_dir(tmp_path)
    _write_state(ref, "done")
    # Structure: page.tsx < 200 LOC, components > 3
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "src" / "app" / "page.tsx").write_text(
        "\n".join(f"// line {i}" for i in range(100)) + "\n",
        encoding="utf-8",
    )
    comps = impl / "src" / "components"
    comps.mkdir()
    for n in ("Hero", "Nav", "Footer", "Stats", "Cta"):
        (comps / f"{n}.tsx").write_text(f"export default function {n}() {{ return null; }}\n")
    # Section-compare result with 0 ❌
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| hero | 100 | 50 | ok | ✅ |\n"
        "| footer | 200 | 100 | minor | ✅ |\n"
        "**Result: 2 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    # tree-diff-status PASS
    (ref / "tree-diff-status.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass", "elements_walked": 150,
        "counts": {"critical": 0, "major": 0, "layout-major": 0, "minor": 0, "layout-minor": 0, "ok": 150, "unpaired": 0},
        "errorCount": 0, "reason": "all ok",
    }))
    done, unmet = benchmark_harness.check_strict_done(ref, impl)
    assert done, f"all conditions satisfied but unmet: {unmet}"


def test_check_strict_done_monolithic_page_blocks(tmp_path: Path) -> None:
    """page.tsx 300 LOC + 0 components → unmet."""
    ref, impl = _make_ref_dir(tmp_path)
    _write_state(ref, "done")
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "src" / "app" / "page.tsx").write_text(
        "\n".join(f"// line {i}" for i in range(300)) + "\n",
        encoding="utf-8",
    )
    done, unmet = benchmark_harness.check_strict_done(ref, impl)
    assert not done
    assert any("LOC >= 200" in u for u in unmet)
    assert any("components has 0" in u for u in unmet)


def test_check_strict_done_tree_diff_fail_blocks(tmp_path: Path) -> None:
    """tree-diff-status.json status=fail → unmet."""
    ref, impl = _make_ref_dir(tmp_path)
    _write_state(ref, "done")
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "src" / "app" / "page.tsx").write_text("hi\n")
    comps = impl / "src" / "components"
    comps.mkdir()
    for n in ("A", "B", "C", "D"):
        (comps / f"{n}.tsx").write_text("export default function X() { return null; }\n")
    (ref / "sections").mkdir()
    (ref / "sections" / "result.txt").write_text("**Result: 0 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n")
    (ref / "tree-diff-status.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "fail",
        "errorCount": 17, "reason": "17 critical mismatches",
    }))
    done, unmet = benchmark_harness.check_strict_done(ref, impl)
    assert not done
    assert any("tree-diff status='fail'" in u for u in unmet)


# ── Prompt building ──────────────────────────────────────────────────────


def test_build_iter_prompt_iter1_initial_when_no_state(tmp_path: Path) -> None:
    """iter 1 + no pipeline-state.json → use INITIAL prompt."""
    ref, impl = _make_ref_dir(tmp_path)
    prompt = benchmark_harness.build_iter_prompt(
        ref, impl, "https://realfood.gov", "http://localhost:3000",
        iter_count=1, max_iter=100, budget_remaining=500_000,
    )
    assert "iter 1" in prompt
    assert "realfood.gov" in prompt
    assert "ui-capture" in prompt
    # Should NOT include the ITER template's "What's still unmet" header
    # because that's only for the continuation prompt.
    assert "What's still unmet" not in prompt


def test_build_iter_prompt_continuation_with_state(tmp_path: Path) -> None:
    """iter > 1 → ITER prompt with goal output + failures."""
    ref, impl = _make_ref_dir(tmp_path)
    _write_state(ref, "post-implement", gate_fails={"post-implement": 5})
    prompt = benchmark_harness.build_iter_prompt(
        ref, impl, "https://realfood.gov", "http://localhost:3000",
        iter_count=3, max_iter=100, budget_remaining=100_000,
    )
    assert "iter 3" in prompt
    assert "Token budget remaining: 100,000" in prompt
    assert "What's still unmet" in prompt
    # Should mention specific unmet conditions
    assert "STRICT v2 conditions still unmet" in prompt


# ── Main loop wiring (mocked claude) ─────────────────────────────────────


def _mock_claude_response(tokens: int = 5000, session_id: str = "test-session") -> dict:
    return {
        "result": "Mock claude response",
        "session_id": session_id,
        "usage": {"input_tokens": tokens // 2, "output_tokens": tokens // 2},
        "is_error": False,
    }


def test_loop_exits_on_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When check_strict_done returns True, loop exits with DONE outcome."""
    ref, impl = _make_ref_dir(tmp_path)

    # Stage all-pass state from the start
    _write_state(ref, "done")
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "src" / "app" / "page.tsx").write_text("hi\n")
    comps = impl / "src" / "components"
    comps.mkdir()
    for n in ("A", "B", "C", "D"):
        (comps / f"{n}.tsx").write_text("x\n")
    (ref / "sections").mkdir()
    (ref / "sections" / "result.txt").write_text("**Result: 0 FAIL**\n")
    (ref / "tree-diff-status.json").write_text(json.dumps({"status": "pass"}))

    args = mock.Mock(
        ref_dir=str(ref),
        impl_dir=str(impl),
        orig_url="https://example.com",
        impl_url="http://localhost:3000",
        max_iter=5,
        token_budget=100_000,
        wall_budget_s=60,
        session_id=None,
    )
    outcome = benchmark_harness.run_loop(args)
    assert outcome == "DONE"


def test_loop_hits_max_iter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When iters reach --max-iter without done, outcome=INCOMPLETE_MAX_ITER."""
    ref, impl = _make_ref_dir(tmp_path)
    _write_state(ref, "reference")  # never reaches done

    def fake_invoke(prompt: str, session_id: str, plugin_dir: Path, cwd: Path, iter_count: int) -> dict:
        return _mock_claude_response(tokens=1000)

    monkeypatch.setattr(benchmark_harness, "invoke_claude", fake_invoke)

    args = mock.Mock(
        ref_dir=str(ref),
        impl_dir=str(impl),
        orig_url="https://example.com",
        impl_url="http://localhost:3000",
        max_iter=3,
        token_budget=100_000,
        wall_budget_s=60,
        session_id=None,
    )
    outcome = benchmark_harness.run_loop(args)
    assert outcome == "INCOMPLETE_MAX_ITER"


def test_loop_hits_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When total tokens >= budget, outcome=INCOMPLETE_BUDGET."""
    ref, impl = _make_ref_dir(tmp_path)
    _write_state(ref, "reference")

    def fake_invoke(prompt: str, session_id: str, plugin_dir: Path, cwd: Path, iter_count: int) -> dict:
        # Each iter burns 60% of the small budget → 2 iters exceed
        return _mock_claude_response(tokens=6000)

    monkeypatch.setattr(benchmark_harness, "invoke_claude", fake_invoke)

    args = mock.Mock(
        ref_dir=str(ref),
        impl_dir=str(impl),
        orig_url="https://example.com",
        impl_url="http://localhost:3000",
        max_iter=100,
        token_budget=10_000,
        wall_budget_s=60,
        session_id=None,
    )
    outcome = benchmark_harness.run_loop(args)
    assert outcome == "INCOMPLETE_BUDGET"


def test_loop_aborts_on_unclonable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """unclonable_reasons → ABORTED outcome."""
    ref, impl = _make_ref_dir(tmp_path)
    payload = {
        "component": ref.name,
        "started_at": "2026-05-15T00:00:00Z",
        "completed_steps": [],
        "current_gate": "paid-features",
        "last_updated": "2026-05-15T00:00:00Z",
        "gate_fail_counts": {"paid-features": 1},
        "unclonable_reasons": [
            {"gate": "paid-features", "reason": "paid font detected", "detail": "Die Grotesk requires license"}
        ],
    }
    (ref / "pipeline-state.json").write_text(json.dumps(payload))

    args = mock.Mock(
        ref_dir=str(ref),
        impl_dir=str(impl),
        orig_url="https://example.com",
        impl_url="http://localhost:3000",
        max_iter=10,
        token_budget=100_000,
        wall_budget_s=60,
        session_id=None,
    )
    outcome = benchmark_harness.run_loop(args)
    assert outcome == "ABORTED"


def test_log_file_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """benchmark-harness.log.jsonl is created with start + end events."""
    ref, impl = _make_ref_dir(tmp_path)
    _write_state(ref, "done")
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "src" / "app" / "page.tsx").write_text("hi\n")
    comps = impl / "src" / "components"
    comps.mkdir()
    for n in ("A", "B", "C", "D"):
        (comps / f"{n}.tsx").write_text("x\n")
    (ref / "sections").mkdir()
    (ref / "sections" / "result.txt").write_text("**Result: 0 FAIL**\n")
    (ref / "tree-diff-status.json").write_text(json.dumps({"status": "pass"}))

    args = mock.Mock(
        ref_dir=str(ref),
        impl_dir=str(impl),
        orig_url="https://example.com",
        impl_url="http://localhost:3000",
        max_iter=3,
        token_budget=100_000,
        wall_budget_s=60,
        session_id=None,
    )
    benchmark_harness.run_loop(args)
    log = ref / "benchmark-harness.log.jsonl"
    assert log.is_file()
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line)["event"] for line in lines]
    assert events[0] == "start"
    assert events[-1] == "end"
