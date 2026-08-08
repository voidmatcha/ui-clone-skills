"""Shared test helpers for measure/.

Extracted from test_section_compare.py so split test files import from a
single source of truth instead of duplicating ~100 lines of
prelude each. (Codex Item-6 follow-up.)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ui_clone.check_inputs import compute_check_input_hash, sidecar_path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]



def _run_script(script_rel: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    script = _project_root() / script_rel
    assert script.is_file(), f"missing {script}"
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True, text=True, timeout=timeout,
    )



def _make_verification_plan(ref: Path, check_id: str, produces: str,
                            severity: str = "block",
                            tier: str = "standard") -> None:
    plan = {
        "schemaVersion": 1,
        "tier": tier,
        "requiredChecks": [{
            "id": check_id,
            "script": f"skills/visual-debug/scripts/{check_id}-check.sh",
            "produces": produces,
            "reason": "fixture",
            "severity": severity,
            "tier": tier,
        }],
    }
    (ref / "verification-plan.json").write_text(json.dumps(plan))



def _impl_fixture(ref: Path) -> Path:
    impl = ref.parent / "impl"
    (impl / "src").mkdir(parents=True, exist_ok=True)
    (impl / "public").mkdir(exist_ok=True)
    (impl / "package.json").write_text('{"name":"measure-fixture"}\n', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <main>Fixture</main>}\n",
        encoding="utf-8",
    )
    (impl / "public" / "fixture.svg").write_text("<svg></svg>\n", encoding="utf-8")
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    return impl


def _stamp_check_input_hash(ref: Path, check_id: str, impl: Path | None = None) -> None:
    resolved_impl = impl or _impl_fixture(ref)
    digest = compute_check_input_hash(resolved_impl, ref, check_id)
    assert digest is not None and digest != "", (
        f"{check_id} has no fingerprintable inputs"
    )
    sidecar_path(ref, check_id).write_text(digest + "\n", encoding="utf-8")


def _baseline_post_implement_inputs(ref: Path) -> None:
    """Minimum artifacts post-implement gate reads beyond verification-plan."""
    _impl_fixture(ref)
    (ref / "extracted.json").write_text(json.dumps(
        {"sections": [{"name": "hero"}]}
    ))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "fixture", "trigger": "hover"}],
    }))
    (ref / "external-sdks.json").write_text(json.dumps({"detected": []}))
    (ref / "required-media.json").write_text(json.dumps({"required": []}))
    static_ref = ref / "static" / "ref"
    static_ref.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (static_ref / f"{i}.png").write_bytes(b"\x89PNG" + b"\0" * 20)
    sections = ref / "sections"
    sections.mkdir(exist_ok=True)
    (sections / "result.txt").write_text(
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    transitions = ref / "transitions"
    transitions.mkdir(exist_ok=True)
    (transitions / "result.txt").write_text(
        "Transition compare: 1 PASS, 0 FAIL\n"
        "✅ PASS .fixture\n",
        encoding="utf-8",
    )



__all__ = [
    "_project_root",
    "_run_script",
    "_make_verification_plan",
    "_baseline_post_implement_inputs",
    "_stamp_check_input_hash",
]
