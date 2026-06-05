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


def test_generation_plan_sticky_records_containing_block(tmp_path: Path) -> None:
    """A css-sticky element must carry its relative containing-block ancestor
    (selector + height) and renderAt that wrapper — not flat at App — so the
    clone's sticky releases at the section end instead of pinning to the page
    body for the whole scroll."""
    ref = tmp_path / "ref" / "realfood"
    (ref / "css").mkdir(parents=True)
    structure = {
        "tag": "body",
        "children": [
            {
                "tag": "section",
                "class": "dga_resources_section__VAZi",
                "styles": {"position": "relative", "height": "2700px"},
                "children": [
                    {
                        "tag": "div",
                        "class": "dga_resources_sticky__EiBuU",
                        "styles": {"position": "sticky", "top": "0px"},
                        "children": [{"tag": "p", "text": "Resources"}],
                    }
                ],
            }
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure))
    (ref / "dom-scaffold.json").write_text(json.dumps(structure))
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"id": 0, "tag": "section", "cls": "dga_resources_section__VAZi"}]})
    )
    (ref / "sticky-elements.json").write_text(
        json.dumps({"elements": [{
            "tag": "div",
            "className": "dga_resources_sticky__EiBuU",
            "position": "sticky",
            "stickyTop": "0px",
        }]})
    )
    (ref / "css" / "x.css").write_text(".dga_resources_sticky__EiBuU{position:sticky}\n")

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    sticky = plan["stickyStrategy"]
    assert len(sticky) == 1
    entry = sticky[0]
    assert entry["mechanism"] == "css-sticky"
    assert entry["top"] == "0px", "stickyTop must map to top (field-name fix)"
    cb = entry["containingBlock"]
    assert cb is not None
    assert "dga_resources_section__VAZi" in cb["selector"]
    assert cb["height"] == "2700px"
    assert cb["position"] == "relative"
    assert entry["renderAt"] == cb["selector"], "css-sticky must render inside its containing block"


def test_generation_plan_smoothscroll_carries_lenis_config(tmp_path: Path) -> None:
    """When scroll-engine.json declares Lenis with concrete options, the
    smoothScroll plan must thread those options into a `config` block so the
    generated SmoothScroll.tsx uses the site's real lerp/duration/easing/
    wheelMultiplier instead of Lenis library defaults."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)

    (ref / "section-map.json").write_text(
        json.dumps(
            [
                {"index": i, "tag": "section", "id": f"s-{i}", "className": f"sec_{i}", "height": 600}
                for i in range(4)
            ]
        )
    )
    (ref / "scroll-engine.json").write_text(
        json.dumps(
            {
                "engine": "lenis",
                "custom": False,
                "smoothScroll": True,
                "evidence": "html.lenis class + lerp:/wheelMultiplier in bundles",
                "options": {
                    "lerp": 0.1,
                    "duration": 1.2,
                    "wheelMultiplier": 1,
                    "easing": "expoOut",
                },
            }
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    ss = plan["smoothScroll"]
    assert ss["required"] is True
    assert ss["library"] == "lenis"
    cfg = ss["config"]
    assert cfg["lerp"] == 0.1
    assert cfg["duration"] == 1.2
    assert cfg["wheelMultiplier"] == 1
    assert cfg["easing"] == "expoOut"


def test_generation_plan_smoothscroll_config_empty_when_no_options(tmp_path: Path) -> None:
    """Lenis detected but no concrete options → config is an empty dict, never
    fabricated defaults (the generator falls back to Lenis defaults itself)."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps([{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)])
    )
    (ref / "scroll-engine.json").write_text(
        json.dumps({"engine": "lenis", "smoothScroll": True, "evidence": "html.lenis class"})
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    ss = plan["smoothScroll"]
    assert ss["required"] is True
    assert ss["config"] == {}


def test_generation_plan_smoothscroll_for_realfood_lenis(tmp_path: Path) -> None:
    """Regression (Fix 41 must NOT over-reject): realfood's actual
    scroll-engine.json (engine=lenis, smoothScroll true, lenis evidence) MUST
    still require smooth scroll. Pairs with the native(no-lenis) test below."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps([{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)])
    )
    # Faithful to tmp/ref/realfood-gov-l63/scroll-engine.json.
    (ref / "scroll-engine.json").write_text(json.dumps({
        "engine": "lenis",
        "custom": False,
        "smoothScroll": True,
        "evidence": "html.lenis class + lerp:/wheelMultiplier in bundles",
        "options": {"wheelMultiplier": 1},
    }))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": [
        {"name": "lenis", "type": "smooth-scroll", "evidence": "html.lenis class", "clone": "use"},
    ]}))
    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )
    plan = json.loads((ref / "generation-plan.json").read_text())
    assert plan["smoothScroll"]["required"] is True, "realfood (engine=lenis) must keep smooth scroll"
    assert plan["smoothScroll"]["library"] == "lenis"
    assert "lenis" in plan["libraries"]["required"]


def test_generation_plan_no_smoothscroll_for_native_engine(tmp_path: Path) -> None:
    """Regression (codex review): has_smooth_scroll matched any 'smooth'
    substring, so {"engine":"native","smoothScroll":false} still injected Lenis.
    Native scroll with smoothScroll false must NOT require smooth scroll."""
    ref = tmp_path / "ref" / "site"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps([{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)])
    )
    (ref / "scroll-engine.json").write_text(
        json.dumps({"engine": "native", "smoothScroll": False, "evidence": "no smooth-scroll lib"})
    )
    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )
    plan = json.loads((ref / "generation-plan.json").read_text())
    assert plan["smoothScroll"]["required"] is False
    assert plan["smoothScroll"]["library"] is None
    assert plan["smoothScroll"]["config"] == {}
    assert "lenis" not in plan["libraries"]["required"]


def test_generation_plan_surfaces_framer_scroll_driven_block(tmp_path: Path) -> None:
    """When scroll-engine.json declares a framer-motion scrollDriven block
    (useScroll / useTransform / scrollYProgress), the plan must surface it as a
    `scrollDriven` contract so the generator wires the real scroll-progress
    reveal mechanism — not just a generic scroll listener."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps([{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)])
    )
    (ref / "scroll-engine.json").write_text(
        json.dumps(
            {
                "engine": "lenis",
                "smoothScroll": True,
                "scrollDriven": {
                    "library": "framer-motion",
                    "hooks": ["useScroll", "useTransform", "scrollYProgress"],
                    "evidence": "21 scrollYProgress refs in bundles",
                },
            }
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    sd = plan["scrollDriven"]
    assert sd["required"] is True
    assert sd["library"] == "framer-motion"
    assert sd["hooks"] == ["useScroll", "useTransform", "scrollYProgress"]
    assert "21 scrollYProgress" in sd["evidence"]
    # must reconcile with smooth-scroll: progress comes from the Lenis source,
    # not a raw window 'scroll' listener.
    assert "lenis" in sd["note"].lower() or "smooth" in sd["note"].lower()


def test_generation_plan_surfaces_scroll_scrub_from_bundle_extraction(tmp_path: Path) -> None:
    """bundle-extraction.json's framer useScroll/useTransform tables must surface
    as a concrete `scrollScrub` plan (offset + input/output ranges per site) so the
    generator emits real scroll-scrub motion, not a generic opacity fade. Sites with
    no transform table are detection-only and must NOT become scrub contracts."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps([{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)])
    )
    (ref / "bundle-extraction.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "extractions": {
                    "framerMotion": [
                        {
                            "kind": "useScroll",
                            "progressVar": "e",
                            "target": "e",
                            "offset": '["start end","end start"]',
                            "transforms": [{"input": "[0,1]", "output": "[1,1.2]"}],
                            "transformCount": 1,
                            "source": "bundles/page.js",
                        },
                        {
                            "kind": "useScroll",
                            "progressVar": "w",
                            "target": "w",
                            "offset": '["start end","end end"]',
                            "transforms": [],
                            "transformCount": 0,
                            "source": "bundles/page.js",
                        },
                        {"kind": "useMotionValueEvent", "valueVar": "w", "event": "change"},
                    ]
                },
            }
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    ss = plan["scrollScrub"]
    assert ss["required"] is True
    assert ss["library"] == "framer-motion"
    # only the site WITH a transform table becomes a scrub contract
    assert ss["count"] == 1
    site = ss["sites"][0]
    assert site["offset"] == '["start end","end start"]'
    assert site["transforms"] == [{"input": "[0,1]", "output": "[1,1.2]"}]
    assert "lenis" in ss["note"].lower() or "scroll source" in ss["note"].lower()


def test_generation_plan_scroll_scrub_absent_when_no_bundle_extraction(tmp_path: Path) -> None:
    """No bundle-extraction.json (or no framer transform tables) → scrollScrub not
    required, empty sites — generation stays unaffected on non-scroll-scrub sites."""
    ref = tmp_path / "ref" / "plain"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps([{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(3)])
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    ss = plan["scrollScrub"]
    assert ss["required"] is False
    assert ss["count"] == 0
    assert ss["sites"] == []


def test_generation_plan_scroll_driven_absent_when_no_block(tmp_path: Path) -> None:
    """No scrollDriven evidence → required is False and hooks empty."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps([{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)])
    )
    (ref / "scroll-engine.json").write_text(json.dumps({"engine": "native", "smoothScroll": False}))

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    sd = plan["scrollDriven"]
    assert sd["required"] is False
    assert sd["hooks"] == []


def test_component_generation_docs_consume_scroll_plan_fields() -> None:
    """The generator doc must tell implementers to consume the smoothScroll.config
    (real Lenis options) and scrollDriven (Framer scroll-progress reveal) plan
    fields, so Fix 28/29's threaded data is not silently ignored at generation."""
    root = _project_root()
    doc = (root / "skills/ui-reverse-engineering/component-generation.md").read_text()
    assert "smoothScroll.config" in doc
    assert "scrollDriven" in doc
    assert "scrollYProgress" in doc
    # Warn against the common downgrade error: turning a continuous scroll-scrub
    # reveal into a one-shot IntersectionObserver fade.
    assert "IntersectionObserver" in doc and "one-shot" in doc


def test_component_generation_documents_canonical_scroll_driven_snippet() -> None:
    """The generator doc must carry the render-verified canonical scrollDriven
    reveal pattern (useScroll(target,offset) + useTransform) and must NOT claim
    framer-motion useScroll fails under Lenis — verified false by a real render:
    opacity interpolated 0->1 with scroll while html.lenis was active."""
    root = _project_root()
    doc = (root / "skills/ui-reverse-engineering/component-generation.md").read_text()
    # Render-verified canonical pattern is documented.
    assert "useScroll({ target" in doc
    assert "useTransform(scrollYProgress" in doc
    # The false blanket claim is gone.
    assert "will NOT receive events" not in doc
    # Positive correction: framer useScroll tracks Lenis-driven scroll.
    assert "tracks Lenis" in doc
    # Preserve the contained-scroll caveat (custom wrapper -> useScroll container).
    assert "container: wrapperRef" in doc


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


def test_scroll_driven_inferred_from_framer_when_no_block(tmp_path: Path) -> None:
    """P6 (full-build): the LLM decode step may omit the scroll-engine
    scrollDriven block (tmp/ref/realfood has engine=lenis but no block), so
    scrollDriven.required stayed False and ScrollReveal never wired even though
    framer-motion is present. Infer scrollDriven.required from a detected
    framer-motion library when the explicit block is absent."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps([{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)])
    )
    (ref / "scroll-engine.json").write_text(json.dumps({"engine": "lenis", "smoothScroll": True}))
    (ref / "external-sdks.json").write_text(json.dumps({"sdks": [
        {"name": "framer-motion", "type": "animation", "evidence": "framer in bundles", "clone": "use"},
    ]}))
    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )
    plan = json.loads((ref / "generation-plan.json").read_text())
    assert plan["scrollDriven"]["required"] is True, "framer-motion should imply scrollDriven"
    assert plan["scrollDriven"]["library"] == "framer-motion"
