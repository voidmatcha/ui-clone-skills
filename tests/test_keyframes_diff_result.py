from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills" / "visual-debug" / "scripts" / "keyframes-diff.sh"


def _extract_python(text: str) -> str:
    start = text.index("<<'PYEOF'") + len("<<'PYEOF'")
    end = text.index("\nPYEOF", start)
    return text[start:end]


def _run(tmp_path: Path, ref: dict, impl: dict) -> Path:
    py = _extract_python(SCRIPT.read_text(encoding="utf-8"))
    ref_p = tmp_path / "ref.json"
    impl_p = tmp_path / "impl.json"
    out = tmp_path / "out"
    out.mkdir()
    ref_p.write_text(json.dumps(ref), encoding="utf-8")
    impl_p.write_text(json.dumps(impl), encoding="utf-8")
    harness = tmp_path / "kf.py"
    harness.write_text(py, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(harness), str(ref_p), str(impl_p), str(out)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    # rc 0 = clean match; rc 1 = diffs found (the script's warn signal) —
    # both are successful runs that must have written the artifacts.
    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
    return out


def test_result_artifact_written_with_plan_expected_name(tmp_path: Path) -> None:
    """verification-plan declares the keyframes-diff artifact as
    transitions/keyframes-diff-result.txt, but the producer wrote only
    keyframes-diff.{json,md} — so the gate reported MISSING_ARTIFACT forever,
    even after the check ran (producer/plan filename drift)."""
    kf = [{"stop": "0%", "opacity": "0"}, {"stop": "100%", "opacity": "1"}]
    out = _run(tmp_path, {"heroFade": kf}, {"heroFade": kf})
    result = out / "keyframes-diff-result.txt"
    assert result.exists(), "canonical result artifact must be written"
    first = result.read_text(encoding="utf-8").splitlines()[0]
    assert "-> PASS" in first, f"matching keyframes must verdict PASS: {first}"


def test_result_artifact_warns_on_missing_and_diffs(tmp_path: Path) -> None:
    ref = {
        "heroFade": [{"stop": "0%", "opacity": "0"}, {"stop": "100%", "opacity": "1"}],
        "toastSpin": [{"stop": "0%", "transform": "rotate(0deg)"}],
    }
    impl = {
        "heroFade": [{"stop": "0%", "opacity": "0.5"}, {"stop": "100%", "opacity": "1"}],
    }
    out = _run(tmp_path, ref, impl)
    text = (out / "keyframes-diff-result.txt").read_text(encoding="utf-8")
    assert "-> WARN" in text.splitlines()[0]
    assert "MISSING toastSpin" in text
    assert "DIFF heroFade" in text
