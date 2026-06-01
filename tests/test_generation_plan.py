import json
import os
import subprocess
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_generation_plan_requires_forensic_preservation_for_css_modules(tmp_path: Path) -> None:
    """CSS-module-heavy refs should plan a ref-derived JSX + local CSS path."""
    ref = tmp_path / "ref" / "realfood"
    (ref / "css").mkdir(parents=True)

    sections = []
    for idx in range(12):
        sections.append(
            {
                "index": idx,
                "tag": "section",
                "id": f"section-{idx}",
                "className": f"dga_section_{idx}__AbC{idx:02d}",
                "height": 600,
            }
        )
    (ref / "section-map.json").write_text(json.dumps(sections))
    (ref / "css" / "app.css").write_text(
        "\n".join(f".dga_section_{idx}__AbC{idx:02d}{{display:block}}" for idx in range(12))
        + "\n"
        + ("/* copied site css */\n" * 900)
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    preservation = plan.get("forensicPreservation")
    assert preservation and preservation["required"] is True
    assert preservation["strategy"] == "ref-derived-jsx-with-local-css"
    assert preservation["classSignatureCount"] >= 12
    assert preservation["cssBytes"] > 10_000
    assert "preserve original CSS-module className tokens" in preservation["rules"]


def test_generation_plan_blocks_standard_rebuild_when_css_modules_lack_css_artifacts(
    tmp_path: Path,
) -> None:
    """CSS-module signatures alone should block freehand rebuilds.

    RealFood incident: the capture exposed many CSS-module class signatures but
    no css/*.css chunks, which previously downgraded the plan to
    standard-react-rebuild and let agents recreate the site from vibes.
    """
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)

    sections = []
    for idx in range(30):
        sections.append(
            {
                "index": idx,
                "tag": "section",
                "id": f"section-{idx}",
                "className": f"dga_section_{idx}__AbC{idx:02d}",
                "height": 600,
            }
        )
    (ref / "section-map.json").write_text(json.dumps(sections))

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    preservation = plan.get("forensicPreservation")
    assert preservation and preservation["required"] is True
    assert preservation["strategy"] == "ref-derived-jsx-with-local-css"
    assert preservation["classSignatureCount"] >= 30
    assert preservation["cssBytes"] == 0
    assert preservation["cssArtifactStatus"] == "missing"
    assert preservation["missingCssArtifacts"] is True
    assert preservation["copyCssTo"] == "src/ref-css"
    assert any("do not use standard-react-rebuild" in rule for rule in preservation["rules"])


def test_generation_plan_recovers_stylesheet_links_before_forensic_decision(
    tmp_path: Path,
) -> None:
    """Linked CSS should be copied into ref/css before deciding the strategy."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    source_css = tmp_path / "source" / "app.css"
    source_css.parent.mkdir()
    source_css.write_text(
        "\n".join(f".dga_section_{idx}__AbC{idx:02d}{{display:block}}" for idx in range(30))
        + "\n"
        + ("/* recovered site css */\n" * 900),
        encoding="utf-8",
    )

    sections = []
    for idx in range(30):
        sections.append(
            {
                "index": idx,
                "tag": "section",
                "id": f"section-{idx}",
                "className": f"dga_section_{idx}__AbC{idx:02d}",
                "height": 600,
            }
        )
    (ref / "section-map.json").write_text(json.dumps(sections))
    (ref / "head.json").write_text(
        json.dumps(
            {
                "links": [
                    {
                        "rel": "stylesheet",
                        "href": source_css.resolve().as_uri(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["UI_CLONE_CSS_DOWNLOAD_ALLOW_FILE"] = "1"
    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
        env=env,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    preservation = plan.get("forensicPreservation")
    assert preservation and preservation["required"] is True
    assert preservation["strategy"] == "ref-derived-jsx-with-local-css"
    assert preservation["cssBytes"] > 10_000
    assert preservation["cssFiles"] == ["css/app.css"]
    assert preservation["cssArtifactStatus"] == "present"
    assert preservation["missingCssArtifacts"] is False
    assert (ref / "css" / "app.css").is_file()


def test_ui_reverse_engineering_docs_promote_forensic_preservation() -> None:
    """Step 7 docs should route CSS-module-heavy refs to the preserved scaffold path."""
    root = _project_root()
    skill = (root / "skills/ui-reverse-engineering/SKILL.md").read_text()
    component_generation = (root / "skills/ui-reverse-engineering/component-generation.md").read_text()
    site_detection = (root / "skills/ui-reverse-engineering/site-detection.md").read_text()

    for text in (skill, component_generation, site_detection):
        assert "forensicPreservation" in text
        assert "ref-derived JSX" in text
        assert "local CSS" in text
