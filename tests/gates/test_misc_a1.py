import json
from pathlib import Path

import pytest

from ui_clone.gate import Gate

from ._helpers import (
    _build_renamed_impl,
    _post_implement_baseline,
)


def test_check_file_pass(ref_dir_with_artifacts: Path) -> None:
    gate = Gate(ref_dir_with_artifacts)
    result = gate.check_file(ref_dir_with_artifacts / "structure.json", "structure.json")
    assert result.status == "pass"



def test_check_file_missing(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    result = gate.check_file(ref / "missing.json", "missing.json")
    assert result.status == "fail"
    assert "MISSING" in result.message



def test_check_file_empty(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    empty = ref / "empty.json"
    empty.write_bytes(b"")
    gate = Gate(ref)
    result = gate.check_file(empty, "empty.json")
    assert result.status == "fail"
    assert "empty" in result.message.lower()



def test_check_dir_pass(ref_dir_with_artifacts: Path) -> None:
    gate = Gate(ref_dir_with_artifacts)
    result = gate.check_dir(ref_dir_with_artifacts / "static" / "ref", "screenshots", min_files=5)
    assert result.status == "pass"



def test_check_dir_missing(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    result = gate.check_dir(ref / "nonexistent", "dir", min_files=1)
    assert result.status == "fail"



def test_check_dir_too_few_files(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    d = ref / "screenshots"
    d.mkdir()
    (d / "only_one.png").write_bytes(b"PNG")
    gate = Gate(ref)
    result = gate.check_dir(d, "screenshots", min_files=5)
    assert result.status == "fail"
    assert "1" in result.message



def test_check_json_key_pass(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    f = ref / "extracted.json"
    f.write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    gate = Gate(ref)
    result = gate.check_json_key(f, "sections", "extracted.json has sections")
    assert result.status == "pass"



def test_check_json_key_missing_key(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    f = ref / "extracted.json"
    f.write_text(json.dumps({"url": "https://example.com"}))
    gate = Gate(ref)
    result = gate.check_json_key(f, "sections", "extracted.json has sections")
    assert result.status == "fail"
    assert "sections" in result.message



def test_check_json_key_malformed(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    f = ref / "bad.json"
    f.write_text("{not valid json")
    gate = Gate(ref)
    result = gate.check_json_key(f, "sections", "bad.json")
    assert result.status == "fail"
    assert "malformed" in result.message.lower()



def test_run_returns_0_on_pass(ref_dir_with_artifacts: Path) -> None:
    gate = Gate(ref_dir_with_artifacts)
    code = gate.run("reference")
    assert code == 0



def test_run_returns_1_on_fail(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    code = gate.run("reference")
    assert code == 1



def test_run_returns_2_on_unknown_gate(ref_dir_with_artifacts: Path) -> None:
    gate = Gate(ref_dir_with_artifacts)
    code = gate.run("nonexistent-gate")
    assert code == 2



def test_json_output_structure(ref_dir_with_artifacts: Path, capsys: pytest.CaptureFixture[str]) -> None:
    gate = Gate(ref_dir_with_artifacts)
    gate.run("reference", json_output=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "passed" in data
    assert "fail_count" in data
    assert "failures" in data
    assert isinstance(data["failures"], list)



def test_run_gate_pass_writes_pipeline_state(ref_dir_with_artifacts: Path) -> None:
    """Gate PASS: pipeline-state.json is created and the gate is recorded."""
    from ui_clone.state import PipelineState

    gate = Gate(ref_dir_with_artifacts)
    exit_code = gate.run("reference", json_output=True)
    assert exit_code == 0
    state = PipelineState.load(ref_dir_with_artifacts)
    assert "reference" in state.completed_steps
    assert state.current_gate == "extraction"



def test_run_gate_fail_bumps_consecutive_fail_count(tmp_path: Path) -> None:
    """Gate FAIL on the active gate: gate_fail_counts[gate] increments and is
    written to pipeline-state.json so the goal card can surface a STUCK banner
    after the threshold. completed_steps stays empty — the gate did not pass.
    """
    from ui_clone.state import PipelineState

    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    for expected_count in (1, 2, 3):
        exit_code = gate.run("reference", json_output=True)
        assert exit_code == 1
        state = PipelineState.load(ref)
        assert state.gate_fail_counts.get("reference") == expected_count
        assert "reference" not in state.completed_steps
        assert state.current_gate == "reference"



def test_run_gate_fails_when_pipeline_state_skips_prerequisites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A later active gate with missing earlier completed_steps must fail closed."""
    from ui_clone.state import PipelineState

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": "ref",
                "completed_steps": ["extraction"],
                "current_gate": "post-implement",
            }
        ),
        encoding="utf-8",
    )

    code = Gate(ref).run("post-implement", json_output=True)
    data = json.loads(capsys.readouterr().out)
    failures = data["failures"]

    assert code == 1
    assert any(f["label"] == "pipeline-state prerequisites" for f in failures)
    reason = " ".join(f["reason"] for f in failures)
    assert "reference" in reason
    assert "pre-generate" in reason
    state = PipelineState.load(ref)
    assert state.current_gate == "post-implement"
    assert "post-implement" not in state.completed_steps



def test_componentization_gate_fails_on_monolithic_page(tmp_path: Path) -> None:
    """Regression — c9b638d benchmark shipped a 214-line page.tsx with 0
    files in impl/src/components/. New post-implement check enforces:
    page.tsx > 200 LOC AND components/ < 3 → FAIL.
    """
    work = tmp_path / "benchmark" / "work" / "deadbee"
    ref = work / "ref"
    impl = work / "impl"
    ref.mkdir(parents=True)
    _post_implement_baseline(ref)
    (impl / "src" / "app").mkdir(parents=True)
    page = impl / "src" / "app" / "page.tsx"
    page.write_text("\n".join(f"// line {i}" for i in range(220)) + "\n", encoding="utf-8")
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert any(r.label == "componentization" for r in failures), (
        f"monolithic page.tsx must fail post-implement: {failures}"
    )



def test_componentization_gate_passes_when_split(tmp_path: Path) -> None:
    """page.tsx > 200 LOC but components/ has ≥ 3 .tsx files → PASS.
    Counterpart that confirms the guard only triggers on monolithic shape.
    """
    work = tmp_path / "benchmark" / "work" / "deadbee"
    ref = work / "ref"
    impl = work / "impl"
    ref.mkdir(parents=True)
    _post_implement_baseline(ref)
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "src" / "app" / "page.tsx").write_text(
        "\n".join(f"// line {i}" for i in range(220)) + "\n", encoding="utf-8"
    )
    comps = impl / "src" / "components"
    comps.mkdir()
    for name in ("Hero", "Stats", "Footer"):
        (comps / f"{name}.tsx").write_text(f"export default function {name}() {{ return null; }}\n")
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert not any(r.label == "componentization" for r in failures), (
        f"split impl must not trigger componentization fail: {failures}"
    )



def test_componentization_gate_skipped_when_no_impl(tmp_path: Path) -> None:
    """Regular tmp/ref/ flow with no co-located impl → silent skip."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _post_implement_baseline(ref)
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert not any(r.label == "componentization" for r in failures)



def test_find_impl_root_detects_renamed_sibling(tmp_path: Path) -> None:
    """Codex L38 issue 11 — adversarial rename happy path.

    Loop-37 sub-agent renamed `impl/` → `realfood-clone/` to bypass gate
    hooks that hard-coded the `impl/` path. The shared resolver
    (`scripts/extract/find-impl-root.sh`, wired into Gate._find_impl_root)
    must detect any sibling directory that LOOKS like an impl scaffold
    (package.json + src/app + .tsx) regardless of its name.
    """
    loop_root = tmp_path / "scratch" / "loop-X"
    ref = loop_root / "tmp" / "ref" / "realfood-main"
    ref.mkdir(parents=True)
    _build_renamed_impl(loop_root, "realfood-clone", page_loc=220)
    gate = Gate(ref)
    resolved = gate._find_impl_root()
    assert resolved is not None, "resolver must locate renamed impl dir"
    assert resolved.name == "realfood-clone", f"got {resolved}"
