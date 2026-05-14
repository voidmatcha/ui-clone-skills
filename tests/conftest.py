import json
import os
import time
from pathlib import Path

import pytest


@pytest.fixture
def ref_dir(tmp_path: Path) -> Path:
    """Minimal valid ref_dir fixture."""
    d = tmp_path / "tmp" / "ref" / "test-component"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def ref_dir_with_artifacts(ref_dir: Path) -> Path:
    """ref_dir fixture for gate tests — includes all required artifacts."""
    # Phase 1: reference screenshots
    screenshots = ref_dir / "static" / "ref"
    screenshots.mkdir(parents=True)
    for i in range(5):
        (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

    # Phase 1: transition videos
    transitions_ref = ref_dir / "transitions" / "ref"
    transitions_ref.mkdir(parents=True)
    (transitions_ref / "hover_hero.webm").write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 100)

    (ref_dir / "regions.json").write_text(json.dumps({"regions": []}))

    # Phase 2: extraction artifacts
    (ref_dir / "structure.json").write_text(json.dumps({"sections": [], "totalCount": 0}))
    (ref_dir / "styles.json").write_text(json.dumps({"selectors": {}}))
    (ref_dir / "head.json").write_text(json.dumps({"title": "Test"}))
    (ref_dir / "fonts.json").write_text(json.dumps({"faces": []}))
    css_dir = ref_dir / "css"
    css_dir.mkdir()
    (css_dir / "variables.txt").write_text(":root { --color: #fff; }")
    (ref_dir / "visible-images.json").write_text(json.dumps({"images": []}, indent=2))
    (ref_dir / "inline-svgs.json").write_text(json.dumps({"svgs": []}, indent=2))
    (ref_dir / "body-state.json").write_text(json.dumps({"state": "idle"}, indent=2))
    (ref_dir / "design-bundles.json").write_text(json.dumps({"bundles": []}, indent=2))
    (ref_dir / "interactions-detected.json").write_text(
        json.dumps({"interactions": [], "hasPreloader": False})
    )
    (ref_dir / "scroll-engine.json").write_text(json.dumps({"engine": "native"}))
    (ref_dir / "external-sdks.json").write_text(json.dumps({"sdks": []}, indent=2))

    bundles = ref_dir / "bundles"
    bundles.mkdir()
    for i in range(3):
        (bundles / f"chunk-{i}.js").write_text("// chunk")

    (ref_dir / "bundle-map.json").write_text(json.dumps({"chunks": []}, indent=2))
    (ref_dir / "transition-spec.json").write_text(json.dumps({"transitions": []}))

    verify = ref_dir / "verify"
    verify.mkdir()
    for i in range(5):
        (verify / f"frame_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

    (ref_dir / "animation-init-styles.json").write_text(
        json.dumps({"elements": []}, indent=2)
    )
    (ref_dir / "section-map.json").write_text(
        json.dumps({"sections": [], "totalCount": 0, "hasFooter": False})
    )
    responsive = ref_dir / "responsive"
    responsive.mkdir()
    (responsive / "sizing-expressions.json").write_text(
        json.dumps({"expressions": []}, indent=2)
    )
    (ref_dir / "svg-text-elements.json").write_text(json.dumps({"elements": []}, indent=2))
    (ref_dir / "hover-css-rules.json").write_text(json.dumps({"rules": []}, indent=2))

    (ref_dir / "element-roles.json").write_text(json.dumps({"roles": []}, indent=2))
    (ref_dir / "element-groups.json").write_text(json.dumps({"groups": []}, indent=2))
    (ref_dir / "layout-decisions.json").write_text(json.dumps({"decisions": []}, indent=2))
    (ref_dir / "component-map.json").write_text(json.dumps({"sections": [], "sectionCount": 0}))

    # Set parent artifacts to a fixed past time
    base_time = time.time() - 2.0
    parent_artifacts = [
        "structure.json",
        "styles.json",
        "head.json",
        "fonts.json",
        "visible-images.json",
        "inline-svgs.json",
        "body-state.json",
        "design-bundles.json",
        "interactions-detected.json",
        "scroll-engine.json",
        "external-sdks.json",
        "bundle-map.json",
        "transition-spec.json",
        "animation-init-styles.json",
        "section-map.json",
        "svg-text-elements.json",
        "hover-css-rules.json",
        "element-roles.json",
        "element-groups.json",
        "layout-decisions.json",
        "component-map.json",
    ]
    for name in parent_artifacts:
        p = ref_dir / name
        if p.exists():
            os.utime(p, (base_time, base_time))

    # extracted.json gets strictly newer time (+1s)
    extracted_time = base_time + 1.0
    (ref_dir / "extracted.json").write_text(
        json.dumps({"sections": [], "url": "https://example.com"})
    )
    os.utime(ref_dir / "extracted.json", (extracted_time, extracted_time))

    # transition-coverage.json also gets parent time (it's a parent of extracted.json)
    (ref_dir / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [], "staticElements": []})
    )
    os.utime(ref_dir / "transition-coverage.json", (base_time, base_time))

    provenance_artifacts = [
        "extracted.json",
        "transition-spec.json",
        "animation-init-styles.json",
        "section-map.json",
        "svg-text-elements.json",
        "responsive/sizing-expressions.json",
        "interactions-detected.json",
        "transition-coverage.json",
        "component-map.json",
    ]
    (ref_dir / "artifact-provenance.json").write_text(json.dumps({
        "artifacts": [
            {
                "path": artifact,
                "source": "agent-browser-eval" if artifact != "transition-spec.json" else "bundle-grep",
                "evidence": [artifact],
                "generatedAt": "2026-05-14T00:00:00Z",
            }
            for artifact in provenance_artifacts
        ],
    }))

    # Assert the contract is met
    assert (ref_dir / "extracted.json").stat().st_mtime > (
        ref_dir / "structure.json"
    ).stat().st_mtime, "extracted.json must be newer than structure.json"

    return ref_dir
