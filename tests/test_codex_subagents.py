import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_CODEX_AGENTS = {
    "bundle-analyzer": {
        "contract": "skills/ui-reverse-engineering/js-animation-extraction.md",
        "required_terms": ["bundle-map.json", "bundle-extraction.json", "ScrollTrigger"],
    },
    "generation-planner": {
        "contract": "skills/ui-reverse-engineering/enrichment.md",
        "required_terms": ["generation-plan.json", "schemaVersion 2"],
    },
    "mismatch-diagnoser": {
        "contract": "skills/ui-reverse-engineering/diagnosis.md",
        "required_terms": ["Root Cause A-R", "Do not apply fixes"],
    },
    "visual-debug-iterator": {
        "contract": "skills/ui-reverse-engineering/iteration-discipline.md",
        "required_terms": ["section-compare.sh", "Read(*.png)"],
    },
    "visual-debug-reviewer": {
        "contract": "skills/visual-debug/comparison-fix.md",
        "required_terms": ["Phase E", "phase-e-review.json"],
    },
}


def test_codex_native_agents_exist_for_every_skill_subagent_role() -> None:
    for name, spec in EXPECTED_CODEX_AGENTS.items():
        path = ROOT / ".codex" / "agents" / f"{name}.toml"

        assert path.is_file(), f"missing Codex native agent adapter: {path}"

        data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert data["name"] == name
        assert data["model"] == "gpt-5.5"

        instructions = data["developer_instructions"]
        assert spec["contract"] in instructions
        for term in spec["required_terms"]:
            assert term in instructions


def test_claude_and_codex_agent_role_names_stay_in_sync() -> None:
    claude_manifest = json.loads(
        (ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
    )
    manifest_agents = {
        Path(agent_path).stem for agent_path in claude_manifest.get("agents", [])
    }

    for name in EXPECTED_CODEX_AGENTS:
        path = ROOT / ".claude-plugin" / "agents" / f"{name}.md"
        assert path.is_file(), f"missing Claude agent adapter: {path}"
        assert name in manifest_agents


def test_codex_default_prompt_routes_named_subagents_before_inline_fallback() -> None:
    prompt = " ".join(
        json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))[
            "interface"
        ]["defaultPrompt"]
    )

    assert ".codex/agents" in prompt
    assert "inline fallback" in prompt
    for name in EXPECTED_CODEX_AGENTS:
        assert name in prompt


def test_skill_docs_describe_host_neutral_subagent_dispatch() -> None:
    skill = (ROOT / "skills/ui-reverse-engineering/SKILL.md").read_text(
        encoding="utf-8"
    )
    visual_debug = (ROOT / "skills/visual-debug/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "Host-neutral subagent dispatch" in skill
    assert "Codex native subagent" in skill
    assert "Codex hosts skip the sub-agent" not in skill
    for name in EXPECTED_CODEX_AGENTS:
        assert name in skill or name == "visual-debug-reviewer"

    assert "Codex native subagent" in visual_debug
    assert "visual-debug-reviewer" in visual_debug


def test_shared_contract_docs_no_longer_describe_codex_as_inline_only() -> None:
    paths = [
        ROOT / "skills/ui-reverse-engineering/enrichment.md",
        ROOT / "skills/ui-reverse-engineering/iteration-discipline.md",
        ROOT / "skills/ui-reverse-engineering/diagnosis.md",
        ROOT / "skills/visual-debug/comparison-fix.md",
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "Codex native" in text, path
        assert "Codex inline" not in text, path
