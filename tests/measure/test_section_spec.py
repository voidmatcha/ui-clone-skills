from __future__ import annotations

from ._helpers import (
    _project_root,
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

