import json
from pathlib import Path

from ui_clone.extraction_artifacts import (
    finalize_full_extraction_artifacts,
    refresh_extracted_artifact,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_full_finalizer_writes_canonical_handoff_artifacts(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_json(
        ref / "structure.json",
        {
            "tag": "body",
            "class": "home",
            "styles": {"width": "1440px", "transition-duration": "0.3s"},
            "children": [
                {
                    "tag": "header",
                    "class": "site-header",
                    "text": "Logo",
                    "styles": {"opacity": "0", "transition-duration": "0.2s"},
                    "children": [],
                },
                {
                    "tag": "section",
                    "class": "hero",
                    "text": "Hello",
                    "styles": {"transform": "translateY(10px)"},
                    "children": [],
                },
                {"tag": "footer", "class": "footer", "styles": {}, "children": []},
            ],
        },
    )
    _write_json(ref / "styles.json", {"tags": ["body"]})
    _write_json(ref / "head.json", {"title": "Example"})
    _write_json(ref / "assets.json", {"images": []})
    _write_json(ref / "visible-images.json", {"images": []})
    _write_json(ref / "fonts.json", {"fonts": []})
    _write_json(
        ref / "section-map.json",
        {
            "totalCount": 3,
            "hasHeader": True,
            "hasFooter": True,
            "sections": [
                {"index": 0, "tag": "header", "className": "site-header", "top": 0, "height": 80},
                {"index": 1, "tag": "section", "className": "hero", "top": 80, "height": 400},
                {"index": 2, "tag": "footer", "className": "footer", "top": 480, "height": 120},
            ],
        },
    )
    _write_json(ref / "dom-scaffold.json", {"sections": []})
    (ref / "css").mkdir()
    (ref / "css" / "site.css").write_text(".hero:hover{opacity:.8}@media (max-width: 768px){.hero{display:block}}", encoding="utf-8")
    (ref / "static" / "ref").mkdir(parents=True)
    (ref / "static" / "ref" / "section-0.png").write_bytes(b"png")

    actions = finalize_full_extraction_artifacts(ref)

    expected = [
        "svg-text-elements.json",
        "animation-init-styles.json",
        "detected-breakpoints.json",
        "responsive/sizing-expressions.json",
        "hover-css-rules.json",
        "interactions-detected.json",
        "bundle-map.json",
        "external-sdks.json",
        "scroll-engine.json",
        "transition-spec.json",
        "extracted.json",
        "component-map.json",
        "transition-coverage.json",
        "artifact-provenance.json",
    ]
    for rel in expected:
        assert (ref / rel).is_file(), rel
    assert actions

    spec = json.loads((ref / "transition-spec.json").read_text(encoding="utf-8"))
    assert spec["transitions"]
    assert spec["transitions"][0]["trigger"] == "hover"
    assert spec["transitions"][0]["source_chunk"] == "css/site.css"

    component_map = json.loads((ref / "component-map.json").read_text(encoding="utf-8"))
    assert component_map["sectionCount"] == 3
    assert any("footer" in c["sourceTag"] for c in component_map["components"])

    provenance = json.loads((ref / "artifact-provenance.json").read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in provenance["artifacts"]}
    assert "extracted.json" in paths
    assert "transition-spec.json" in paths


def test_refresh_extracted_leaves_current_handoff_mtime_unchanged(
    tmp_path: Path,
) -> None:
    """A read-only pre-generate check must not stale its downstream plan."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_json(
        ref / "extracted.json",
        {
            "schemaVersion": 1,
            "source": "ui_clone.extraction_artifacts",
            "sections": [],
        },
    )
    _write_json(ref / "generation-plan.json", {"schemaVersion": 2})
    before = (ref / "extracted.json").stat().st_mtime_ns

    actions = refresh_extracted_artifact(ref)

    assert actions == {}
    assert (ref / "extracted.json").stat().st_mtime_ns == before


def test_live_hover_inventory_drives_interactions_and_transitions(tmp_path: Path) -> None:
    from ui_clone.extraction_artifacts import (
        _finalize_interactions,
        _finalize_transition_spec,
    )

    ref = tmp_path / "ref"
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "site.css").write_text(
        ".live:hover{opacity:.8}.unused:hover{opacity:.5}",
        encoding="utf-8",
    )
    live_payload = {
        "schemaVersion": 1,
        "source": "scripts/extract/capture-hover.sh",
        "status": "pass",
        "rules": [
            {
                "selector": ".live:hover .icon",
                "activation": ".live",
                "affected": ".live .icon",
                "declarations": "opacity: 0.8",
                "sourceHrefs": ["https://cdn.example.test/assets/site.css?v=42"],
                "source": "scripts/extract/capture-hover.sh:live-cssom",
            },
            {
                "selector": ".live:hover .label",
                "activationSelector": "  .live  ",
                "source": "scripts/extract/capture-hover.sh:live-cssom",
            },
        ],
        "derivedFrom": ["live-cssom", "hover/manifest.json"],
    }
    _write_json(ref / "hover-css-rules.json", live_payload)

    actions: dict[str, str] = {}
    _finalize_interactions(ref, actions)
    _finalize_transition_spec(ref, actions)

    interactions = json.loads(
        (ref / "interactions-detected.json").read_text(encoding="utf-8")
    )
    assert interactions["interactions"] == [
        {
            "id": "hover-0",
            "trigger": "hover",
            "target": ".live",
            "timingSource": "css",
            "source": "scripts/extract/capture-hover.sh:live-cssom",
        }
    ]
    assert interactions["derivedFrom"] == ["hover-css-rules.json"]

    transitions = json.loads(
        (ref / "transition-spec.json").read_text(encoding="utf-8")
    )
    hover_targets = [
        entry["target"]
        for entry in transitions["transitions"]
        if entry["trigger"] == "hover"
    ]
    assert hover_targets == [".live"]
    assert ".unused" not in hover_targets
    assert transitions["transitions"][0]["source_chunk"] == "css/site.css"
    assert transitions["transitions"][0]["animation"]["cssText"] == (
        ".live:hover .icon {opacity: 0.8}"
    )
    assert json.loads(
        (ref / "hover-css-rules.json").read_text(encoding="utf-8")
    ) == live_payload


def test_unmapped_live_hover_source_becomes_inline_init(tmp_path: Path) -> None:
    from ui_clone.extraction_artifacts import _finalize_transition_spec

    ref = tmp_path / "ref"
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "downloaded.css").write_text(".live:hover{opacity:.8}", encoding="utf-8")
    _write_json(
        ref / "hover-css-rules.json",
        {
            "schemaVersion": 1,
            "source": "scripts/extract/capture-hover.sh",
            "status": "pass",
            "rules": [
                {
                    "selector": ".live:hover",
                    "activation": ".live",
                    "declarations": "opacity: 0.8",
                    "sourceHrefs": ["https://cdn.example.test/missing.css"],
                    "source": "scripts/extract/capture-hover.sh:live-cssom",
                }
            ],
            "derivedFrom": ["live-cssom"],
        },
    )

    _finalize_transition_spec(ref, {})

    spec = json.loads((ref / "transition-spec.json").read_text(encoding="utf-8"))
    assert spec["transitions"][0]["source_chunk"] == "inline init"


def test_swiper_signal_emits_an_explicit_placeholder_obligation(
    tmp_path: Path,
) -> None:
    from ui_clone.extraction_artifacts import _finalize_transition_spec

    ref = tmp_path / "ref"
    _write_json(
        ref / "verification-plan.json",
        {"signals": {"hasSwiper": True}, "requiredChecks": []},
    )

    _finalize_transition_spec(ref, {})

    spec = json.loads((ref / "transition-spec.json").read_text(encoding="utf-8"))
    swiper = [
        entry
        for entry in spec["transitions"]
        if entry["trigger"] == "swiper"
    ]
    assert spec["placeholder"] is True
    assert len(swiper) == 1
    assert swiper[0]["animation"] == {
        "type": "swiper",
        "mechanism": (
            "unresolved — capture the live Swiper instance and reference "
            "frames with scripts/extract/capture-swiper-artifacts.py"
        ),
    }


def test_empty_live_hover_inventory_suppresses_unused_raw_css(
    tmp_path: Path,
) -> None:
    from ui_clone.extraction_artifacts import (
        _finalize_interactions,
        _finalize_transition_spec,
    )

    ref = tmp_path / "ref"
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "site.css").write_text(
        ".not-on-page:hover{opacity:.5}",
        encoding="utf-8",
    )
    _write_json(
        ref / "hover-css-rules.json",
        {
            "schemaVersion": 1,
            "source": "scripts/extract/capture-hover.sh",
            "status": "pass",
            "observation": "no-hover-css-rules-observed",
            "rules": [],
            "derivedFrom": ["live-cssom", "hover"],
        },
    )

    actions: dict[str, str] = {}
    _finalize_interactions(ref, actions)
    _finalize_transition_spec(ref, actions)

    interactions = json.loads(
        (ref / "interactions-detected.json").read_text(encoding="utf-8")
    )
    assert interactions["interactions"] == []
    transitions = json.loads(
        (ref / "transition-spec.json").read_text(encoding="utf-8")
    )
    assert [entry["trigger"] for entry in transitions["transitions"]] == [
        "page-load"
    ]


def test_invalid_live_hover_inventory_falls_back_to_raw_css(tmp_path: Path) -> None:
    from ui_clone.extraction_artifacts import _finalize_interactions

    ref = tmp_path / "ref"
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "site.css").write_text(
        ".fallback:hover{opacity:.5}",
        encoding="utf-8",
    )
    _write_json(
        ref / "hover-css-rules.json",
        {
            "schemaVersion": 1,
            "source": "scripts/extract/capture-hover.sh",
            "status": "pass",
            "rules": [{"selector": "opacity: .5", "activation": ""}],
            "derivedFrom": ["live-cssom"],
        },
    )

    _finalize_interactions(ref, {})

    interactions = json.loads(
        (ref / "interactions-detected.json").read_text(encoding="utf-8")
    )
    assert [entry["target"] for entry in interactions["interactions"]] == [
        ".fallback"
    ]
    assert interactions["interactions"][0]["source"] == "css/site.css"


def test_external_sdks_framer_motion_false_positive_fixed(tmp_path: Path) -> None:
    """A2: a bundle full of `Array.from` / `useScrollAnimation` / `motion.value`
    must NOT flip framer-motion 'detected' (the old `motion\\.`/`useScroll`
    tokens did, mis-routing generation to install framer-motion on sites that
    never ship it). Real Framer/GSAP usage is still detected, and construction
    sites surface in the additive `usedMotion` field."""
    from ui_clone.extraction_artifacts import _finalize_bundles

    ref = tmp_path / "ref"
    (ref / "bundles").mkdir(parents=True)
    # decoy: framer-motion false-positive triggers, no real Framer
    (ref / "bundles" / "app.js").write_text(
        "const a = Array.from(list); const cfg = {useScrollAnimation:true};"
        " const v = motion.value; Buffer.from(x);",
        encoding="utf-8",
    )
    # real Framer usage
    (ref / "bundles" / "anim.js").write_text(
        "const { scrollYProgress } = useScroll(); const y = useTransform(scrollYProgress,[0,1],[0,8]);"
        " <motion.div whileInView={{opacity:1}} whileHover={{scale:1.1}} />",
        encoding="utf-8",
    )
    # real GSAP construction
    (ref / "bundles" / "gsap.js").write_text(
        "gsap.registerPlugin(ScrollTrigger); gsap.to(el,{autoAlpha:1,duration:.6});"
        " ScrollTrigger.create({trigger:el,start:'top 85%'});",
        encoding="utf-8",
    )

    _finalize_bundles(ref, {})
    sdks = json.loads((ref / "external-sdks.json").read_text(encoding="utf-8"))
    detected = sdks["detected"]
    used = sdks["usedMotion"]

    # decoy bundle alone must NOT detect framer-motion; real usage does
    assert "framer-motion" in detected, "real Framer usage (anim.js) must be detected"
    assert "gsap" in detected
    # construction evidence present for both libs that actually drive motion
    assert "framer-motion" in used and "gsap" in used
    # if ONLY the decoy bundle existed, framer-motion must be absent
    ref2 = tmp_path / "ref2"
    (ref2 / "bundles").mkdir(parents=True)
    (ref2 / "bundles" / "only-decoy.js").write_text(
        "Array.from(x); const c={useScrollAnimation:1}; motion.value; Buffer.from(y);",
        encoding="utf-8",
    )
    _finalize_bundles(ref2, {})
    detected2 = json.loads((ref2 / "external-sdks.json").read_text(encoding="utf-8"))["detected"]
    assert "framer-motion" not in detected2, (
        f"decoy-only bundle must not detect framer-motion: {detected2}"
    )

    # basic `motion.div` component usage (no hooks/while*) must still detect
    # framer-motion — the tag allowlist catches it while excluding `motion.value`.
    ref3 = tmp_path / "ref3"
    (ref3 / "bundles").mkdir(parents=True)
    (ref3 / "bundles" / "basic.js").write_text(
        "const Box = motion.div; const Title = motion.h1; const v = motion.value;",
        encoding="utf-8",
    )
    _finalize_bundles(ref3, {})
    sdks3 = json.loads((ref3 / "external-sdks.json").read_text(encoding="utf-8"))
    assert "framer-motion" in sdks3["detected"], (
        f"basic motion.div usage must detect framer-motion: {sdks3['detected']}"
    )
    assert "framer-motion" in sdks3["usedMotion"], "motion.div is a construction site"


def test_external_sdks_webflow_ix2_ignores_css_module_decoys(tmp_path: Path) -> None:
    from ui_clone.extraction_artifacts import _finalize_bundles

    decoy_ref = tmp_path / "decoy"
    (decoy_ref / "bundles").mkdir(parents=True)
    (decoy_ref / "bundles" / "app.js").write_text(
        'const styles = {fixed: "fix20", icon: "IX25P"}; const ix2 = 2;',
        encoding="utf-8",
    )
    _finalize_bundles(decoy_ref, {})
    decoy_sdks = json.loads(
        (decoy_ref / "external-sdks.json").read_text(encoding="utf-8")
    )
    assert "webflow-ix2" not in decoy_sdks["detected"]

    webflow_ref = tmp_path / "webflow"
    (webflow_ref / "bundles").mkdir(parents=True)
    (webflow_ref / "bundles" / "app.js").write_text(
        "Webflow.require('ix2').init();",
        encoding="utf-8",
    )
    _finalize_bundles(webflow_ref, {})
    webflow_sdks = json.loads(
        (webflow_ref / "external-sdks.json").read_text(encoding="utf-8")
    )
    assert "webflow-ix2" in webflow_sdks["detected"]
    assert "webflow-ix2" in webflow_sdks["usedMotion"]


def test_finalize_transition_spec_mines_real_io_reveal_from_states(tmp_path: Path) -> None:
    """Fix 4 (honest-only): when the IO-reveal signal is set AND init-styles
    captured a real hidden/offset from-state, the placeholder floor carries the
    OBSERVED from-state instead of an 'unresolved — mine ... per Step 5d' stub.
    Identity/visible elements are never emitted (no fabricated motion)."""
    from ui_clone.extraction_artifacts import _finalize_transition_spec

    ref = tmp_path / "ref"
    ref.mkdir()
    _write_json(ref / "verification-plan.json", {"signals": {"hasIOReveal": True}})
    _write_json(ref / "animation-init-styles.json", {"entries": [
        {"id": "init-0", "className": "reveal-card", "tag": "div",
         "initialStyles": {"opacity": "0", "transform": "matrix(1, 0, 0, 1, 0, -200)"}},
        {"id": "init-1", "className": "fade-in", "tag": "section",
         "initialStyles": {"opacity": "0"}},
        {"id": "init-2", "className": "settled", "tag": "div",
         "initialStyles": {"opacity": "1", "transform": "none"}},  # NOT a reveal
    ]})

    _finalize_transition_spec(ref, {})
    spec = json.loads((ref / "transition-spec.json").read_text(encoding="utf-8"))
    reveals = [t for t in spec["transitions"] if t["animation"].get("type") == "io-reveal"]

    assert reveals, "io-reveal entries should be emitted"
    assert all(r["animation"]["mechanism"] == "io-reveal" for r in reveals), \
        "mined reveals must carry a real mechanism, not 'unresolved'"
    assert any("translateY(-200px)" in r["animation"]["from"] for r in reveals)
    assert any(r["animation"]["from"] == "opacity:0" for r in reveals)
    # honest-only: the visible/identity element is not a reveal
    assert not any(r["target"] == ".settled" for r in reveals)
    # gate behavior unchanged: the floor is still tagged placeholder
    assert spec["placeholder"] is True


def test_finalize_transition_spec_io_reveal_falls_back_to_stub_when_no_init(tmp_path: Path) -> None:
    """No usable init-styles observation -> keep the honest 'unresolved' stub
    rather than inventing a from-state."""
    from ui_clone.extraction_artifacts import _finalize_transition_spec

    ref = tmp_path / "ref"
    ref.mkdir()
    _write_json(ref / "verification-plan.json", {"signals": {"hasIOReveal": True}})
    # no animation-init-styles.json present

    _finalize_transition_spec(ref, {})
    spec = json.loads((ref / "transition-spec.json").read_text(encoding="utf-8"))
    io = [t for t in spec["transitions"] if t["animation"].get("type") == "io-reveal"]
    assert io and "unresolved" in io[0]["animation"]["mechanism"]


def test_translate_from_transform_rejects_non_translation() -> None:
    """Review follow-up (honest-only): scale/rotate/translateZ/zero-translate must
    NOT be read as a reveal from-state."""
    from ui_clone.extraction_artifacts import _translate_from_transform as t
    assert t("translate(0px, 0px) scale(1.05)") == ""
    assert t("translateY(0px) rotate(3deg)") == ""
    assert t("translateZ(50px)") == ""
    assert t("scale(1.2)") == ""
    assert t("rotate(10deg)") == ""
    assert t("translateX(0.4px)") == ""          # sub-pixel
    # real 2D translations survive
    assert t("translateY(-200px)") == "translateY(-200px)"
    assert t("matrix(1, 0, 0, 1, 0, -200)") == "translateY(-200px)"


def test_translate_from_transform_parses_matrix3d_and_scaled_matrix() -> None:
    """Review follow-up: real reveals serialize as matrix3d / scale+translate
    matrices and must NOT be silently dropped."""
    from ui_clone.extraction_artifacts import _translate_from_transform as t
    assert t("matrix3d(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -200, 0, 1)") == "translateY(-200px)"
    assert t("matrix(0.5, 0, 0, 0.5, 0, -200)") == "translateY(-200px)"


def test_reveal_from_channels_opacity_floor() -> None:
    from ui_clone.extraction_artifacts import _reveal_from_channels as r
    assert r({"opacity": "0"}) == {"opacity": "0"}
    assert r({"opacity": "0.5"}) == {"opacity": "0.5"}
    assert r({"opacity": "0.999"}) == {}      # visually settled, not a reveal
    assert r({"opacity": "1"}) == {}


def test_io_reveal_skips_bare_tag_and_dedups(tmp_path: Path) -> None:
    from ui_clone.extraction_artifacts import _io_reveal_from_states
    ref = tmp_path / "ref"
    ref.mkdir()
    _write_json(ref / "animation-init-styles.json", {"entries": [
        {"id": "a", "className": "", "tag": "div", "initialStyles": {"opacity": "0.5"}},   # bare tag -> skip
        {"id": "b", "className": "reveal", "tag": "div", "initialStyles": {"opacity": "0"}},
        {"id": "c", "className": "reveal", "tag": "section", "initialStyles": {"opacity": "0"}},  # dup of b
    ]})
    out = _io_reveal_from_states(ref)
    sels = [o["selector"] for o in out]
    assert "div" not in sels and "section" not in sels  # no bare-tag targets
    assert sels.count(".reveal") == 1                   # deduped


def test_derive_inline_svgs_reconstructs_faithful_outerhtml() -> None:
    """BUG 2: structure.json carries the full SVG subtree (every attribute is
    captured generically by extract-dom.sh), so a verbatim outerHTML can be
    rebuilt offline — no live browser needed."""
    import xml.etree.ElementTree as ET

    from ui_clone.extraction_artifacts import _derive_inline_svgs

    structure = {
        "tag": "body",
        "class": "home",
        "children": [
            {
                "tag": "header",
                "class": "site-header",
                "children": [
                    {
                        "tag": "svg",
                        "class": "brand-mark",
                        "viewBox": "0 0 24 24",
                        "width": "24",
                        "height": "24",
                        "aria-label": "Home",
                        "display": "block",
                        "position": "static",
                        "styles": {"color": "rgb(0,0,0)"},
                        "svg": True,
                        "children": [
                            {
                                "tag": "path",
                                "class": "",
                                "fill": "#FF0000",
                                "fill-rule": "evenodd",
                                "d": "M0 0h24v24H0z",
                                "svg": True,
                                "styles": {},
                                "children": [],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    entries = _derive_inline_svgs(structure)
    assert len(entries) == 1
    e = entries[0]
    assert e["role"] == "brandmark"  # aria-label present
    assert e["selector"] == "svg.brand-mark"
    assert e["viewBox"] == "0 0 24 24"
    assert e["section"] == "site-header"
    assert e["ariaLabel"] == "Home"
    html = e["outerHTML"]
    # verbatim geometry + presentation attrs survive; computed-style
    # bookkeeping (styles/display/position/svg flag) must NOT leak in.
    assert 'd="M0 0h24v24H0z"' in html
    assert 'fill-rule="evenodd"' in html
    assert 'viewBox="0 0 24 24"' in html
    assert "styles" not in html and "display=" not in html and "position=" not in html
    ET.fromstring(html)  # well-formed


def test_finalize_inline_svgs_derives_entries_when_svgs_present(tmp_path: Path) -> None:
    """Regression: previously _finalize_inline_svgs returned WITHOUT writing the
    file whenever the site had inline SVGs, hard-stopping the extraction gate."""
    from ui_clone.extraction_artifacts import _finalize_inline_svgs

    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "div",
        "children": [
            {
                "tag": "svg",
                "class": "icon",
                "viewBox": "0 0 16 16",
                "children": [{"tag": "path", "d": "M0 0h16v16H0z", "children": []}],
            }
        ],
    }
    actions: dict[str, str] = {}
    _finalize_inline_svgs(ref, structure, actions)
    data = json.loads((ref / "inline-svgs.json").read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 1
    assert "outerHTML" in data[0]
    assert "derived" in actions["inline-svgs.json"]


def test_finalize_inline_svgs_preserves_live_eval_file(tmp_path: Path) -> None:
    """Resume-safety (mirrors BUG 1): a real agent-browser-eval inline-svgs.json
    is the higher-fidelity source and must never be clobbered by the offline
    derivation."""
    from ui_clone.extraction_artifacts import _finalize_inline_svgs

    ref = tmp_path / "ref"
    ref.mkdir()
    live = [{"role": "logo", "selector": "svg.logo", "outerHTML": "<svg><use/></svg>"}]
    _write_json(ref / "inline-svgs.json", live)
    structure = {"tag": "svg", "class": "icon", "children": []}
    _finalize_inline_svgs(ref, structure, {})
    assert json.loads((ref / "inline-svgs.json").read_text(encoding="utf-8")) == live


def test_finalize_inline_svgs_sentinel_when_no_svg(tmp_path: Path) -> None:
    from ui_clone.extraction_artifacts import _finalize_inline_svgs

    ref = tmp_path / "ref"
    ref.mkdir()
    _finalize_inline_svgs(ref, {"tag": "div", "children": []}, {})
    data = json.loads((ref / "inline-svgs.json").read_text(encoding="utf-8"))
    assert data["observation"] == "no-inline-svgs"
    assert data["svgCount"] == 0
