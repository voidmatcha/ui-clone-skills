from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _impl_blob(impl: Path) -> str:
    comp = impl / "src" / "components"
    parts = [p.read_text(encoding="utf-8") for p in comp.glob("*.tsx")] if comp.is_dir() else []
    return "".join(parts)


def _src_blob(impl: Path) -> str:
    src = impl / "src"
    return "".join(p.read_text(encoding="utf-8") for p in src.rglob("*.tsx")) if src.is_dir() else ""


def test_scaffold_drops_captured_lifecycle_state_classes(tmp_path: Path) -> None:
    """Captured terminal lifecycle classes belong to runtime state, not base
    markup. Freezing them makes entry/loading transitions render already-settled."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    splash = ref / "states" / "splash"
    splash.mkdir(parents=True)
    (splash / "trajectory.json").write_text(json.dumps([
        {"ts_ms": 0, "bodyClass": "loading", "htmlClass": ""},
        {"ts_ms": 800, "bodyClass": "loaded complete", "htmlClass": ""},
    ]), encoding="utf-8")
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "class": "antialiased",
        "styles": {},
        "children": [{
            "tag": "section",
            "class": "hero loaded complete",
            "styles": {},
            "children": [{"tag": "p", "text": "Ready"}],
        }],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8",
    )
    (impl / "package.json").write_text(
        json.dumps({"name": "impl", "dependencies": {"next": "16.0.0", "react": "19.0.0"}}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert 'className="hero"' in blob
    assert "loaded" not in blob
    assert "complete" not in blob


def test_scaffold_preserves_stable_lifecycle_named_classes_without_splash_evidence(
    tmp_path: Path,
) -> None:
    """Lifecycle-like names are common stable semantic classes. Do not strip
    them unless capture evidence proves they are transition state tokens."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "class": "antialiased",
        "styles": {},
        "children": [{
            "tag": "section",
            "class": "task-list done complete",
            "styles": {},
            "children": [{"tag": "p", "text": "All tasks complete"}],
        }],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "task-list"}]}),
        encoding="utf-8",
    )
    (impl / "package.json").write_text(
        json.dumps({"name": "impl", "dependencies": {"next": "16.0.0", "react": "19.0.0"}}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert 'className="task-list done complete"' in blob


def test_intro_state_body_bg_replaced_with_dominant_page_bg(tmp_path: Path) -> None:
    """When the captured body background-color equals its text color, the body
    was captured in an unrevealed intro state (text painted invisibly on the
    pre-animation dark backdrop, e.g. realfood's rgb(17,0,0) intro). That is
    never the resting page background — propagating it (Fix 56) paints the whole
    page dark (the loop-124 regression). The transpiler must instead use the
    dominant content background-color (the real cream page bg) for both the
    root div and the global html,body override (Fix 63)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    cream = "rgb(253, 251, 238)"
    dark = "rgb(17, 0, 0)"
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "class": "antialiased",
        # Intro pre-reveal capture: bg == text color (invisible) + a transition.
        "styles": {"background-color": dark, "color": dark,
                   "transition": "background-color 0.15s"},
        "children": [
            {"tag": "div", "class": "page-wrapper", "styles": {"background-color": cream},
             "children": [
                 {"tag": "section", "class": "hero", "styles": {"background-color": cream},
                  "children": [{"tag": "h1", "text": "Eat Real Food"}]},
                 {"tag": "section", "class": "cta", "styles": {"background-color": cream},
                  "children": [{"tag": "p", "text": "Join us"}]},
             ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "hero"},
        {"index": 1, "tag": "section", "cls": "cta"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    # Global html,body override must use the cream page bg, not the dark intro.
    assert f"html,body{{background-color:{cream} !important" in blob, (
        f"global override must use dominant page bg; got:\n{blob}"
    )
    assert "html,body{background-color:rgb(17, 0, 0) !important" not in blob, (
        "must not propagate the unrevealed intro bg to the page base"
    )
    # The viewport-filling root div must also carry the cream bg.
    assert f'backgroundColor: "{cream}"' in blob, "root div bg must be cream"


def test_pseudo_before_precedes_text_content(tmp_path: Path) -> None:
    """Synthetic ::before spans must render before the element text.

    If text is emitted first, absolute pseudos with no explicit top/left use the
    text's static-position fallback and icon buttons hang below their 32px box.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "class": "antialiased",
        "styles": {},
        "children": [
            {"tag": "section", "class": "hero", "styles": {}, "children": [
                {"tag": "button", "class": "btn-auto-control", "text": "자동멈춤",
                 "styles": {"position": "relative", "width": "32px", "min-height": "32px"},
                 "before_styles": {
                     "content": "\"\"",
                     "display": "block",
                     "position": "absolute",
                     "width": "32px",
                     "height": "32px",
                     "background-image": "url(\"/icon-pause.svg\")",
                 },
                 "after_styles": {
                     "content": "\"\"",
                     "display": "block",
                     "position": "absolute",
                     "width": "32px",
                     "height": "32px",
                     "background-image": "url(\"/icon-play.svg\")",
                     "opacity": "0",
                 }},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "hero"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    before_i = blob.index('<span data-pseudo="before"')
    text_i = blob.index("자동멈춤")
    after_i = blob.index('<span data-pseudo="after"')
    assert before_i < text_i < after_i, blob
    assert '*:has(> [data-pseudo="before"])::before' in blob
    assert '*:has(> [data-pseudo="after"])::after' in blob


def test_color_hover_unfreezes_computed_zero_border_currentcolor(tmp_path: Path) -> None:
    """Computed zero/none borders must not freeze currentColor hover parity."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "section",
            "class": "footer",
            "children": [{
                "tag": "a",
                "class": "menu__link2",
                "text": "NAVER D2SF",
                "styles": {
                    "display": "inline-block",
                    "color": "rgb(113, 118, 128)",
                    "border": "0px none rgb(113, 118, 128)",
                    "transition": "color 0.2s cubic-bezier(0.33, 1, 0.68, 1)",
                    "transition-property": "color",
                    "transition-duration": "0.2s",
                },
                "hover_styles": {
                    "color": "rgb(26, 29, 36)",
                },
            }],
        }],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "footer"},
    ]}), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert re.search(r"\.h_\d+ \{[^}]*color: rgb\(113, 118, 128\)", blob), blob
    assert "border: 0px none currentColor" in blob
    assert 'border: "0px none rgb(113, 118, 128)"' not in blob
    assert re.search(r"\.h_\d+:hover \{[^}]*color: rgb\(26, 29, 36\)", blob), blob


def test_materialized_pseudo_responds_to_parent_hover(tmp_path: Path) -> None:
    """Same-subject :hover::after rules must target the synthetic span."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "section",
            "class": "footer",
            "children": [{
                "tag": "a",
                "class": "menu__link2",
                "text": "NAVER D2SF",
                "styles": {
                    "display": "inline-block",
                    "position": "relative",
                    "color": "rgb(113, 118, 128)",
                    "transition": "color 0.2s cubic-bezier(0.33, 1, 0.68, 1)",
                },
                "after_styles": {
                    "content": "\"\"",
                    "display": "inline-block",
                    "width": "0px",
                    "height": "1px",
                    "vertical-align": "top",
                    "border": "0px none rgb(113, 118, 128)",
                    "background-color": "currentColor",
                },
                "after_hover_styles": {
                    "display": "inline-block",
                    "width": "21px",
                    "top": "auto",
                    "left": "auto",
                    "vertical-align": "top",
                    "opacity": "1",
                    "border": "0px none currentColor",
                    "background-color": "currentColor",
                    "transition-delay": "0s, 0s",
                },
            }],
        }],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "footer"},
    ]}), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert '<span data-pseudo="after"' in blob
    base_rule = re.search(r'\.(h_\d+) > \[data-pseudo="after"\] \{([^}]*)\}', blob)
    assert base_rule, blob
    hov_class = re.escape(base_rule.group(1))
    hover_rule = re.search(
        r'\.' + hov_class + r':hover > \[data-pseudo="after"\] \{([^}]*)\}',
        blob,
    )
    assert hover_rule, blob
    assert re.search(rf'className="[^"]*\b{base_rule.group(1)}\b', blob), blob
    assert "width: 0px" in base_rule.group(2)
    assert "vertical-align: top" in base_rule.group(2)
    assert "width: 21px" in hover_rule.group(1)
    assert "top: auto" in hover_rule.group(1)
    assert "left: auto" in hover_rule.group(1)
    assert "opacity: 1" in hover_rule.group(1)
    assert "vertical-align: top" in hover_rule.group(1)
    assert "transition-delay: 0s, 0s" in hover_rule.group(1)
    assert 'width: "0px"' not in blob
    assert 'border: "0px none rgb(113, 118, 128)"' not in blob


def test_fixed_top_header_releases_captured_bottom_complement(
    tmp_path: Path,
) -> None:
    """Computed far edges must not freeze a viewport-anchored fixed header.

    The extractor omits zero-valued ``top`` but retains computed ``bottom``.
    At a 900px capture height that turns a real ``top: 0; height: 64px`` header
    into ``bottom: 836px`` unless the scaffold reconstructs the near edge.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(
        json.dumps(
            {
                "tag": "body",
                "styles": {},
                "children": [
                    {
                        "tag": "header",
                        "class": "header white",
                        "styles": {
                            "position": "fixed",
                            "bottom": "836px",
                            "width": "1440px",
                            "height": "64px",
                        },
                        "children": [{"tag": "a", "text": "NAVER"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "section-map.json").write_text(
        json.dumps(
            {"sections": [{"index": 0, "tag": "header", "cls": "header white"}]}
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert 'top: "0px"' in blob
    assert 'bottom: "836px"' not in blob


def test_fixed_complement_handles_horizontal_axis_and_inline_guard(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(
        json.dumps(
            {
                "tag": "body",
                "styles": {},
                "children": [
                    {
                        "tag": "aside",
                        "class": "left-rail",
                        "styles": {
                            "position": "fixed",
                            "right": "1240px",
                            "width": "200px",
                        },
                    },
                    {
                        "tag": "div",
                        "class": "author-inset",
                        "inlineProps": ["bottom"],
                        "styles": {
                            "position": "fixed",
                            "bottom": "800px",
                            "height": "100px",
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "section-map.json").write_text(
        json.dumps(
            {
                "sections": [
                    {"index": 0, "tag": "aside", "cls": "left-rail"},
                    {"index": 1, "tag": "div", "cls": "author-inset"},
                ]
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert 'left: "0px"' in blob
    assert 'right: "1240px"' not in blob
    assert 'bottom: "800px"' in blob


def test_capture_icon_sentinel_is_not_rendered_as_visible_copy(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(
        json.dumps(
            {
                "tag": "body",
                "styles": {},
                "children": [
                    {
                        "tag": "section",
                        "class": "hero",
                        "styles": {},
                        "children": [
                            {
                                "tag": "button",
                                "styles": {},
                                "children": [
                                    {"tag": "span", "text": "{{icon}}", "styles": {}},
                                    {
                                        "tag": "svg",
                                        "svg": True,
                                        "viewBox": "0 0 16 16",
                                        "styles": {},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "section-map.json").write_text(
        json.dumps(
            {"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert "{{icon}}" not in blob
    assert "&#123;&#123;icon&#125;&#125;" not in blob
    assert "<svg" in blob


def test_numeric_anchor_artifact_normalized_to_anchor(tmp_path: Path) -> None:
    """Captured DOM can contain invalid anchor-like tags such as <a3>.
    Scaffold output should preserve link semantics as <a>, not emit a custom
    lowercase React element that triggers runtime warnings."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "class": "",
        "styles": {},
        "children": [
            {"tag": "footer", "class": "footer", "styles": {}, "children": [
                {"tag": "a3", "class": "service__link2", "text": "고객센터",
                 "href": "https://help.example.test", "styles": {"display": "block"}},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "footer", "cls": "footer"},
    ]}), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert "<a " in blob
    assert "</a>" in blob
    assert "<a3" not in blob
    assert "</a3>" not in blob
    assert "고객센터" in blob


def test_scaffold_skips_hidden_debug_counter_pseudo_spans(tmp_path: Path) -> None:
    """Full-cover numeric pseudo overlays are capture/debug artifacts, not UI.

    Some content-card captures include `::before` as content "1"… with red
    92px full-cover styles while the live idle pseudo is hidden.
    The transpiler must not turn those into visible JSX text, but it should
    still emit legitimate icon/background pseudo spans.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "main",
        "class": "site-shell",
        "children": [{
            "tag": "section",
            "class": "content-grid",
            "children": [{
                "tag": "a",
                "class": "tracking-target content-card",
                "text": "News card",
                "before_styles": {
                    "content": "\"1\"",
                    "position": "absolute",
                    "width": "100%",
                    "height": "100%",
                    "background-color": "rgba(0, 0, 0, 0.5)",
                    "color": "rgb(255, 0, 0)",
                    "font-size": "92px",
                    "justify-content": "center",
                    "align-items": "center",
                    "z-index": "150",
                },
                "after_styles": {
                    "content": "\"\"",
                    "position": "absolute",
                    "width": "16px",
                    "height": "16px",
                    "background-image": "url(\"/img/common/ic-right-arrow.svg\")",
                },
            }],
        }],
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert '<span data-pseudo="before"' not in blob
    assert ">1</span>" not in blob
    assert '<span data-pseudo="after"' in blob
    assert "ic-right-arrow.svg" in blob


def test_text_outside_section_map_is_not_dropped(tmp_path: Path) -> None:
    """Text-bearing nodes not covered by section-map (header/nav buttons,
    deeply nested copy) must still be emitted somewhere — otherwise the
    transpiler silently drops them and text-fidelity can never reach 0."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "header", "class": "topbar", "children": [
                {"tag": "button", "children": [
                    {"tag": "span", "children": [
                        {"tag": "span", "text": "Get Involved"},
                    ]},
                ]},
            ]},
            {"tag": "section", "class": "hero", "children": [
                {"tag": "h1", "text": "Real Food Wins"},
            ]},
        ],
    }), encoding="utf-8")
    # section-map covers ONLY the hero section, not the header nav.
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "Real Food Wins" in blob  # mapped section (sanity)
    assert "Get Involved" in blob, "uncovered header/nav text must not be dropped"


def test_nested_nonmap_section_demoted_to_div(tmp_path: Path) -> None:
    """A nested <section> descendant inside a claimed section's subtree that is NOT
    a section-map entry (e.g. realfood's food-pyramid category cards
    <section class=...sections_section> captured into structure.json but absent
    from section-map.json — capture-state mismatch) must be emitted as a <div>,
    never a <section>. section-compare enumerates impl sections by tag=section; a
    nested <section> inflates the impl section count vs the ref's section-map (the
    realfood 18-vs-14 spurious EXTRA_IN_IMPL / duplicate bug). The claimed section
    ROOT keeps its real <section>; content is preserved (no drop)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "h1", "text": "Real Food Wins"},
                # nested <section> NOT in section-map (lazy cards, capture mismatch)
                {"tag": "section", "class": "cards__sections_section", "children": [
                    {"tag": "p", "text": "Protein category detail"},
                ]},
            ]},
        ],
    }), encoding="utf-8")
    # section-map covers ONLY the hero — the nested cards section is not enumerated.
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    # content preserved (no drop)
    assert "Protein category detail" in blob
    # exactly ONE <section> landmark (the claimed hero root); the nested cards
    # section is demoted to <div> so impl section count == len(section-map).
    assert blob.count("<section") == 1, (
        f"expected exactly 1 <section> (hero root); nested non-map section must be "
        f"demoted to <div>. Found {blob.count('<section')}:\n{blob}"
    )


def test_nested_section_preserved_inside_mapped_main_landmark(tmp_path: Path) -> None:
    """A mapped coarse landmark owns a region, not a section identity.

    Nested sections inside a mapped <main> remain real landmarks because
    demoting them would erase the reference document's section semantics. The
    duplicate-prevention demotion is only appropriate when the mapped subtree
    root itself is a <section>.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "main", "class": "page-main", "children": [
                {"tag": "section", "class": "docs-intro", "children": [
                    {"tag": "h1", "text": "Documentation"},
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "main", "cls": "page-main"}]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert "Documentation" in blob
    assert "<main" in blob
    assert '<section className="docs-intro"' in blob, blob


def test_nested_sections_preserved_inside_mapped_div_region(tmp_path: Path) -> None:
    """A mapped div can own a coarse page region just like a mapped main.

    Only a mapped section root may demote nested non-map sections for duplicate
    suppression. Demoting sections inside a div drops real landmarks and can
    disable tag-qualified responsive CSS such as ``section.report-banner``.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "div", "class": "page-region", "children": [
                {"tag": "section", "class": "content-block", "children": [
                    {"tag": "h1", "text": "Sustainability"},
                ]},
                {"tag": "section", "class": "report-banner", "children": [
                    {"tag": "p", "text": "Integrated report"},
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "div", "cls": "page-region"}]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert '<section className="content-block"' in blob, blob
    assert '<section className="report-banner"' in blob, blob


def test_section_map_tag_mismatch_recovers_subtree(tmp_path: Path) -> None:
    """A section-map entry whose `tag` differs from the real DOM tag (the map
    says `section`, but the captured scaffold has the class on a `div`) must
    still resolve to its subtree. Without a tag-relaxed fallback the strict
    tag+class match returns None, the section emits an empty
    `subtree-not-found` stub, and its content is misplaced into a generic
    _Uncovered fragment — a section-identity / placement fidelity loss (Fix 61).
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            # Real DOM wraps the section content in a <div>, not a <section>.
            {"tag": "div", "class": "features__7a2b", "children": [
                {"tag": "h2", "text": "Why Real Food Matters"},
                {"tag": "p", "text": "Whole foods improve health outcomes."},
            ]},
            {"tag": "section", "class": "hero", "children": [
                {"tag": "h1", "text": "Real Food Wins"},
            ]},
        ],
    }), encoding="utf-8")
    # section-map records the section as a <section> (decode normalisation),
    # but the captured node tag is <div>.
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "features__7a2b"},
        {"index": 1, "tag": "section", "cls": "hero"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    features = impl / "src" / "components" / "Features.tsx"
    assert features.exists(), "Features section component must be emitted"
    feat_text = features.read_text(encoding="utf-8")
    assert "subtree-not-found" not in feat_text, (
        "tag-relaxed fallback must resolve the div subtree, not emit a stub"
    )
    assert "Why Real Food Matters" in feat_text, (
        "section content must render inside its own section, not a stub"
    )
    # And it must not be double-counted in an uncovered catch-all fragment.
    blob = _impl_blob(impl)
    assert blob.count("Why Real Food Matters") == 1, (
        "content must appear exactly once (in Features, not also _Uncovered)"
    )


def test_classless_section_map_entries_claim_exact_tags_in_document_order(
    tmp_path: Path,
) -> None:
    """Entries without id/class identity must claim exact-tag roots in order.

    GitHub Docs exposes a classless footer in section-map.json. Identity-only
    matching cannot resolve it and emits a subtree-not-found warning stub.
    Two anonymous roots also prove that consumed tracking advances to the next
    exact-tag match instead of reusing or absorbing the first root's parent.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "footer", "children": [
                {"tag": "p", "text": "PRIMARY_FOOTER_COPY"},
            ]},
            {"tag": "footer", "children": [
                {"tag": "p", "text": "SECONDARY_FOOTER_COPY"},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "footer"},
        {"index": 1, "tag": "footer"},
    ]}), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    first = (impl / "src" / "components" / "Section0.tsx").read_text(
        encoding="utf-8",
    )
    second = (impl / "src" / "components" / "Section1.tsx").read_text(
        encoding="utf-8",
    )
    blob = _src_blob(impl)
    assert "data-scaffold-warn" not in blob
    assert "subtree-not-found" not in blob
    assert "PRIMARY_FOOTER_COPY" in first
    assert "SECONDARY_FOOTER_COPY" not in first
    assert "SECONDARY_FOOTER_COPY" in second
    assert "PRIMARY_FOOTER_COPY" not in second
    assert blob.count("PRIMARY_FOOTER_COPY") == 1
    assert blob.count("SECONDARY_FOOTER_COPY") == 1


def test_catch_all_does_not_duplicate_rendered_content(tmp_path: Path) -> None:
    """When a section resolves to a deep node, the uncovered-content catch-all
    must NOT re-render that already-rendered content (no duplicate output)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "div", "class": "wrapper", "children": [
                {"tag": "section", "class": "hero", "children": [
                    {"tag": "p", "text": "Shared Body Copy"},
                ]},
            ]},
        ],
    }), encoding="utf-8")
    # section-map resolves the DEEP section.hero; its ancestor div.wrapper is
    # uncovered but its subtree was already rendered.
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert blob.count("Shared Body Copy") == 1, (
        f"content rendered {blob.count('Shared Body Copy')} times — catch-all duplicated it"
    )


def test_cdn_image_subdir_path_is_preserved(tmp_path: Path) -> None:
    """CDN/image-optimizer URLs served from a subdirectory must keep that
    subdirectory in the rewritten local path, because asset-download.sh places
    them at impl/public/images/<subdir>/<name>. Flattening to basename
    (the realfood 8/10-broken-images bug) yields /images/<name> which 404s."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    cdn = (
        "https://realfood.gov/cdn-cgi/image/"
        "width=3840,quality=90,format=auto,fit=scale-down/images/pyramid/broccoli.webp"
    )
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "pyramid", "children": [
                {"tag": "img", "src": cdn, "alt": "broccoli"},
                {"tag": "img", "src": "https://realfood.gov/images/covers/1.webp", "alt": "cover"},
                {"tag": "video", "src": "https://realfood.gov/videos/clip.mp4"},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "pyramid"}]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    # cdn-cgi subdir image keeps its subdir
    assert "/images/pyramid/broccoli.webp" in blob
    assert '"/images/broccoli.webp"' not in blob, "must not flatten subdir to basename"
    # non-cdn subdir image also preserved
    assert "/images/covers/1.webp" in blob
    # video stays flat under /videos/ (extract-assets.sh places it there)
    assert "/videos/clip.mp4" in blob


def test_image_outside_section_map_is_not_dropped(tmp_path: Path) -> None:
    """An <img> in a region not covered by section-map must still be emitted —
    otherwise the transpiler drops it (asset-utilization/image-fidelity fail)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "header", "class": "topbar", "children": [
                {"tag": "img", "src": "/images/logo.webp", "alt": "logo"},
            ]},
            {"tag": "section", "class": "hero", "children": [
                {"tag": "h1", "text": "Real Food Wins"},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "/images/logo.webp" in blob, "uncovered <img> must not be dropped"


def test_scaffold_emits_scroll_helpers_when_plan_requires(tmp_path: Path) -> None:
    """The deterministic transpiler base must also emit the scroll helpers when
    generation-plan.json requires them, so a generated impl ships SmoothScroll/
    ScrollReveal automatically (Fix 35/36 wired into Phase-4 base)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "hero", "children": [{"tag": "h1", "text": "Hi"}]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": True, "config": {"lerp": 0.1, "duration": 1.2}},
        "scrollDriven": {"required": True, "hooks": ["useScroll", "useTransform", "scrollYProgress"]},
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (impl / "src" / "lib" / "SmoothScroll.tsx").exists()
    assert (impl / "src" / "lib" / "ScrollReveal.tsx").exists()


def test_scaffold_without_plan_emits_no_scroll_helpers(tmp_path: Path) -> None:
    """No generation-plan.json → transpiler must not fail and emits no helpers."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body", "children": [{"tag": "section", "class": "hero", "children": [{"tag": "h1", "text": "Hi"}]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "lib" / "SmoothScroll.tsx").exists()


def _app_tsx(impl: Path) -> str:
    p = impl / "src" / "App.tsx"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def test_app_unlocks_ref_css_root_opacity_when_sanitize_report_requires_it(tmp_path: Path) -> None:
    """Copied ref CSS can include loader locks such as body{opacity:0}.

    The scaffold must emit a local unlock style when sanitize-ref-css detected
    such a root lock; otherwise a Vite preview can mount a full DOM that stays
    completely invisible.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [{"tag": "h1", "text": "Hi"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8",
    )
    (ref / "ref-css-sanitize-report.json").write_text(json.dumps({
        "requiresRuntimeUnlock": True,
        "runtimeUnlockHints": [{"selector": "body", "declaration": "opacity:0"}],
    }), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    assert "opacity:1 !important" in app
    assert "visibility:visible !important" in app
    assert "#root,#__next,#app" in app


def test_app_wraps_in_smoothscroll_when_plan_requires(tmp_path: Path) -> None:
    """When smoothScroll.required, App.tsx must import and wrap its body in the
    emitted <SmoothScroll> so the built page actually uses Lenis smooth scroll."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "hero", "children": [{"tag": "h1", "text": "Hi"}]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": True, "config": {"lerp": 0.1}},
        "scrollDriven": {"required": False, "hooks": []},
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    assert "import SmoothScroll from './lib/SmoothScroll'" in app
    assert "<SmoothScroll>" in app and "</SmoothScroll>" in app


def test_app_no_smoothscroll_wrap_without_plan(tmp_path: Path) -> None:
    """No plan → App.tsx must not reference SmoothScroll (no regression)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body", "children": [{"tag": "section", "class": "hero", "children": [{"tag": "h1", "text": "Hi"}]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SmoothScroll" not in _app_tsx(impl)


def test_hover_scaffold_filters_destructive_unverified_deltas(tmp_path: Path) -> None:
    """Captured hover deltas can be paired to the wrong element.

    The deterministic scaffold may preserve low-risk color/background feedback,
    but must not invent destructive hover transforms/opacity/font/layout changes
    that make unrelated elements rotate, disappear, or reflow.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "button", "class": "cta", "text": "Hover me",
                 "hover_styles": {
                     "transform": "rotate(180deg)",
                     "opacity": "0",
                     "font-weight": "700",
                     "letter-spacing": "2px",
                     "transition": "all 0.3s",
                     "color": "rgb(1, 2, 3)",
                     "background-color": "rgb(4, 5, 6)",
                 }},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert ":hover" in blob, "safe hover color feedback should still be emitted"
    assert "color: rgb(1, 2, 3)" in blob
    assert "background-color: rgb(4, 5, 6)" in blob
    assert "rotate(180deg)" not in blob
    assert "opacity: 0" not in blob
    assert "font-weight" not in blob
    assert "letter-spacing" not in blob
    assert "transition: all" not in blob


def _hover_ref(tmp_path: Path, node: dict) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "hero", "children": [node]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8",
    )
    return ref, impl


def test_hover_unbake_pops_inline_base_bg_so_hover_wins(tmp_path: Path) -> None:
    """An inline base background-color beats the emitted `.h_N:hover` rule on
    specificity, so the hover is dead. The transpiler must POP the inline base
    color and emit it as a `.h_N { }` base rule instead, so base + :hover are
    both stylesheet and :hover wins."""
    ref, impl = _hover_ref(tmp_path, {
        "tag": "button", "class": "cta", "text": "Hover",
        "styles": {"background-color": "rgb(247, 247, 247)"},
        "hover_styles": {"background-color": "rgb(10, 20, 30)"},
    })
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    # The base bg must NOT be an inline style anymore.
    assert 'backgroundColor: "rgb(247, 247, 247)"' not in blob, blob
    # Both a base rule and a :hover rule for it must be present in the <style>.
    assert "background-color: rgb(247, 247, 247)" in blob, blob   # base rule
    assert "background-color: rgb(10, 20, 30)" in blob, blob      # hover rule
    assert ":hover" in blob


def test_hover_unbake_pops_inline_border_shorthand_for_border_color(tmp_path: Path) -> None:
    ref, impl = _hover_ref(tmp_path, {
        "tag": "a", "class": "cta", "text": "Hover",
        "styles": {"border": "1px solid rgb(26, 29, 36)"},
        "hover_styles": {"border-color": "rgb(113, 118, 128)"},
    })
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert 'border: "1px solid rgb(26, 29, 36)"' not in blob, blob
    assert "border: 1px solid rgb(26, 29, 36)" in blob, blob
    assert "border-color: rgb(113, 118, 128)" in blob, blob


def test_hover_unbake_preserves_inline_bg_when_no_hover(tmp_path: Path) -> None:
    """A node with a baked base color but NO hover delta must keep its inline
    color — the suppression is gated strictly on having a hover delta for it."""
    ref, impl = _hover_ref(tmp_path, {
        "tag": "div", "class": "plain",
        "styles": {"background-color": "rgb(247, 247, 247)"},
    })
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert 'backgroundColor: "rgb(247, 247, 247)"' in blob, blob


def test_hover_unbake_keeps_ref_inline_prop(tmp_path: Path) -> None:
    """If the REF itself set the prop inline (inlineProps), its own stylesheet
    hover was dead on the live site too — reproduce that by KEEPING the inline
    base and NOT emitting a base rule (do not invent a hover the ref lacks)."""
    ref, impl = _hover_ref(tmp_path, {
        "tag": "button", "class": "cta", "text": "Hover",
        "styles": {"background-color": "rgb(247, 247, 247)"},
        "inlineProps": ["background-color"],
        "hover_styles": {"background-color": "rgb(10, 20, 30)"},
    })
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert 'backgroundColor: "rgb(247, 247, 247)"' in blob, blob


def test_hover_unbake_global_counter_distinct_ids(tmp_path: Path) -> None:
    """Two hover nodes in different sections must get DISTINCT h_N ids — with a
    document-global counter a later `.h_0 { base }` rule can't recolor an earlier
    section's `.h_0` element at rest."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "a", "children": [
                {"tag": "button", "class": "ba", "text": "A",
                 "styles": {"background-color": "rgb(1, 1, 1)"},
                 "hover_styles": {"background-color": "rgb(2, 2, 2)"}}]},
            {"tag": "section", "class": "b", "children": [
                {"tag": "button", "class": "bb", "text": "B",
                 "styles": {"background-color": "rgb(3, 3, 3)"},
                 "hover_styles": {"background-color": "rgb(4, 4, 4)"}}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "a"},
        {"index": 1, "tag": "section", "cls": "b"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    # The two hover classes must be different ids (not both h_0).
    assert "h_0" in blob and "h_1" in blob, blob


def test_hover_motion_recovered_when_base_css_declares_the_transition(tmp_path: Path) -> None:
    """gen-H5: a hover MOTION delta (fade/lift/zoom) is genuine — not mis-pairing
    noise — when the element's OWN base CSS declares a transition for that
    property. Recover it so real hover motion is cloned; layout/font/display
    deltas still stay dropped even here."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "hero", "children": [
            {"tag": "div", "class": "card", "text": "Lift me",
             # base CSS transitions transform+opacity -> the hover deltas below
             # are this element's own intentional motion.
             "styles": {"transition-property": "transform, opacity"},
             "hover_styles": {
                 "transform": "translateY(-8px)",
                 "opacity": "0.9",
                 "width": "500px",          # layout delta -> must STILL drop
                 "color": "rgb(1, 2, 3)",
             }},
        ]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "translateY(-8px)" in blob, "hover transform must be recovered when base CSS transitions it"
    assert "opacity: 0.9" in blob, "hover opacity must be recovered when base CSS transitions it"
    assert "color: rgb(1, 2, 3)" in blob, "safe color feedback still emitted"
    assert "width: 500px" not in blob, "layout deltas stay dropped even with a base transition"


def test_hover_motion_stays_dropped_without_base_transition(tmp_path: Path) -> None:
    """The noise defense holds: with NO base transition on the element, a hover
    transform is treated as mis-pairing noise and dropped."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "hero", "children": [
            {"tag": "div", "class": "card", "text": "x",
             "hover_styles": {"transform": "scale(1.2)", "color": "rgb(9, 9, 9)"}},
        ]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "scale(1.2)" not in blob, "hover motion must stay dropped without a base transition"
    assert "color: rgb(9, 9, 9)" in blob, "safe color feedback still emitted"


def test_scaffold_emits_required_lottie_runtime_bridge(tmp_path: Path) -> None:
    """Bundle-discovered Lottie JSON must be source-referenced and mounted.

    Otherwise required-media/lottie-runtime gates fail even after the JSON files
    have been mirrored to impl/public.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (impl / "package.json").write_text(
        json.dumps({
            "scripts": {"dev": "vite --host 127.0.0.1"},
            "dependencies": {
                "@vitejs/plugin-react": "latest",
                "vite": "latest",
                "react": "latest",
                "react-dom": "latest",
                "lottie-react": "latest",
            },
        }),
        encoding="utf-8",
    )
    (ref / "required-media.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "videos": [],
            "lottie": [
                {"path": "/img/lottie/reference-main-intro.json"},
                {"path": "/img/lottie/reference-main-outro-pc.json"},
            ],
            "svgs": [],
        }),
        encoding="utf-8",
    )
    (ref / "structure.json").write_text(json.dumps({
        "tag": "main",
        "class": "site-shell",
        "styles": {},
        "children": [
            {"tag": "section", "class": "hero", "styles": {}, "children": [
                {"tag": "h1", "text": "REFERENCE"},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "hero"},
    ]}), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    bridge = impl / "src" / "components" / "RequiredLotties.tsx"
    assert bridge.exists()
    bridge_text = bridge.read_text(encoding="utf-8")
    assert "import('lottie-web')" in bridge_text
    assert "loadAnimation" in bridge_text
    assert "/img/lottie/reference-main-intro.json" in bridge_text
    assert "/img/lottie/reference-main-outro-pc.json" in bridge_text
    assert "data-lottie={status}" in bridge_text
    assert '"id": "introLottie"' in bridge_text
    assert '"id": "outroLottie"' in bridge_text
    assert "goToAndStop" in bridge_text
    assert "data-animation-path={src}" in bridge_text
    assert "<svg aria-hidden=\"true\"" in bridge_text
    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "import RequiredLotties from './components/RequiredLotties';" in app
    assert "<RequiredLotties />" in app


def test_scaffold_emits_required_video_runtime_bridge(tmp_path: Path) -> None:
    """Runtime-discovered videos must become real autoplaying <video> nodes."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (impl / "package.json").write_text(
        json.dumps({
            "scripts": {"dev": "vite --host 127.0.0.1"},
            "dependencies": {
                "@vitejs/plugin-react": "latest",
                "vite": "latest",
                "react": "latest",
                "react-dom": "latest",
            },
        }),
        encoding="utf-8",
    )
    (ref / "required-media.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "videos": [
                {
                    "section": "hero",
                    "src": "https://cdn.example.com/assets/video/hero-main.mp4?cache=1",
                    "poster": "https://cdn.example.com/assets/video/hero-main.jpg",
                    "autoplay": True,
                    "loop": True,
                    "muted": True,
                    "playsInline": True,
                },
            ],
            "lottie": [],
            "svgs": [],
        }),
        encoding="utf-8",
    )
    (ref / "structure.json").write_text(json.dumps({
        "tag": "main",
        "class": "site-shell",
        "styles": {},
        "children": [
            {"tag": "section", "class": "hero", "styles": {}, "children": [
                {"tag": "h1", "text": "REFERENCE"},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "hero"},
    ]}), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    bridge = impl / "src" / "components" / "RequiredVideos.tsx"
    assert bridge.exists()
    bridge_text = bridge.read_text(encoding="utf-8")
    assert "/videos/hero-main.mp4" in bridge_text
    assert "/assets/video/hero-main.jpg" in bridge_text
    assert "data-required-media=\"video\"" in bridge_text
    assert "video.play()" in bridge_text
    assert "autoPlay={item.autoplay !== false}" in bridge_text
    assert "muted={item.muted !== false}" in bridge_text
    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "import RequiredVideos from './components/RequiredVideos';" in app
    assert "<RequiredVideos />" in app


def test_scaffold_does_not_duplicate_captured_required_video(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (impl / "package.json").write_text(
        json.dumps({
            "scripts": {"dev": "vite --host 127.0.0.1"},
            "dependencies": {
                "@vitejs/plugin-react": "latest",
                "vite": "latest",
                "react": "latest",
                "react-dom": "latest",
            },
        }),
        encoding="utf-8",
    )
    video_url = "https://cdn.example.com/assets/video/hero-main.mp4"
    (ref / "required-media.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "videos": [{"section": "hero", "src": video_url}],
            "lottie": [],
            "svgs": [],
        }),
        encoding="utf-8",
    )
    (ref / "structure.json").write_text(json.dumps({
        "tag": "main",
        "class": "site-shell",
        "styles": {},
        "children": [{
            "tag": "section",
            "class": "hero",
            "styles": {},
            "children": [{"tag": "video", "src": video_url}],
        }],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "hero"},
    ]}), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "components" / "RequiredVideos.tsx").exists()
    blob = _impl_blob(impl)
    assert blob.count("/videos/hero-main.mp4") == 1
    assert "autoPlay" in blob
    assert "muted" in blob
    assert "loop" in blob
    assert "playsInline" in blob


def test_large_fixed_header_gets_scroll_compact_controller(tmp_path: Path) -> None:
    """Large fixed/sticky header captures need a tiny runtime shrink controller.

    Static scroll=0 geometry leaves sites with shrinking headers frozen at
    100px. The scaffold should install the controller only for real header roots,
    giving header-state-runtime-check an evidence-checkable runtime delta.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "header", "class": "site-header",
             "styles": {
                 "position": "fixed",
                 "top": "0px",
                 "height": "100px",
                 "min-height": "100px",
                 "padding-top": "18px",
                 "padding-bottom": "18px",
             },
             "children": [
                 {"tag": "a", "class": "logo", "text": "LOGO"},
                 {"tag": "nav", "children": [{"tag": "a", "text": "News"}]},
             ]},
            {"tag": "section", "class": "hero", "children": [{"tag": "h1", "text": "Hero"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "header", "cls": "site-header"},
        {"index": 1, "tag": "section", "cls": "hero"},
    ]}), encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert "import { useEffect, useState } from 'react'" in blob
    assert "uiCloneHeaderCompact" in blob
    assert "window.scrollY > 80" in blob
    assert 'data-ui-clone-header-scroll="true"' in blob
    assert "ui-clone-header-scroll.is-compact" in blob
    assert "height:100px!important" in blob
    assert "min-height:100px!important" in blob
    assert "height:64px!important" in blob
    assert "min-height:64px!important" in blob
    assert "padding-top:0!important" in blob
    assert "className={`ui-clone-header-scroll" in blob


def _component_containing(impl: Path, needle: str) -> str:
    comp = impl / "src" / "components"
    for p in sorted(comp.glob("*.tsx")):
        if needle in p.read_text(encoding="utf-8"):
            return p.stem
    return ""


def test_uncovered_block_renders_in_document_position(tmp_path: Path) -> None:
    """A section-uncovered block that sits BETWEEN two mapped sections in the DOM
    must render between them in App.tsx — not dumped last. RealFood loop-120 bug:
    pyramid category blocks landed at page bottom -> section-compare 0/14."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "alpha", "children": [{"tag": "h2", "text": "Alpha Heading"}]},
            # uncovered sibling between the two mapped sections:
            {"tag": "div", "class": "midblock", "children": [{"tag": "p", "text": "MIDDLE_UNCOVERED"}]},
            {"tag": "section", "class": "beta", "children": [{"tag": "h2", "text": "Beta Heading"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "alpha"},
        {"index": 1, "tag": "section", "cls": "beta"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    alpha = _component_containing(impl, "Alpha Heading")
    beta = _component_containing(impl, "Beta Heading")
    uncov = _component_containing(impl, "MIDDLE_UNCOVERED")
    assert alpha and beta and uncov, f"missing components: {alpha=} {beta=} {uncov=}"
    app = _app_tsx(impl)

    def _ref_pos(name: str) -> int:
        pos = app.find(f"<{name} ")
        return pos if pos != -1 else app.find(f"<{name}/>")

    ia, iu, ib = _ref_pos(alpha), _ref_pos(uncov), _ref_pos(beta)
    assert ia != -1 and iu != -1 and ib != -1, f"refs not all in App: {ia=} {iu=} {ib=}\n{app}"
    assert ia < iu < ib, "uncovered block must render between Alpha and Beta, not last"


def test_uncovered_footer_preserves_nested_sections_but_other_sections_demote(
    tmp_path: Path,
) -> None:
    """Coarse uncovered landmarks retain their real nested section semantics."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "h1", "text": "Hero"},
            ]},
            {"tag": "section", "class": "cta", "children": [
                {"tag": "h2", "text": "CTA"},
            ]},
            {"tag": "section", "class": "lazy-panel", "children": [
                {"tag": "p", "text": "Lazy details"},
            ]},
            {"tag": "footer", "class": "site-footer", "children": [
                {"tag": "section", "class": "footer-links", "children": [
                    {"tag": "h2", "text": "Links"},
                ]},
                {"tag": "section", "class": "footer-legal", "children": [
                    {"tag": "h2", "text": "Legal"},
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [
            {"index": 0, "tag": "section", "cls": "hero"},
            {"index": 1, "tag": "section", "cls": "cta"},
        ]}),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert '<footer className="site-footer"' in blob, blob
    assert '<section className="footer-links"' in blob, blob
    assert '<section className="footer-legal"' in blob, blob
    assert '<div className="lazy-panel"' in blob, blob
    assert '<section className="lazy-panel"' not in blob, blob


def test_section_claims_doc_later_duplicate_not_earlier(tmp_path: Path) -> None:
    """FIX-3: a repeated CSS-module class (eBay Playbook: playerWrapper appears 3x)
    must be claimed in DOCUMENT ORDER. section-map entries are top-sorted, so the
    LATER entry must take the LATER duplicate — not steal the earlier one, which
    duplicates that content AND orphans the true subtree as a stacked
    _UncoveredAfter band. Reproduces the ebpb 'Specs corner radius' misclaim."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            # doc-earlier duplicate (belongs to the "widget" section)
            {"tag": "section", "class": "widget", "children": [
                {"tag": "div", "class": "composite", "children": [
                    {"tag": "p", "text": "FIRST_COPY"}]}]},
            # a plain section between the two composites
            {"tag": "section", "class": "blurb", "children": [{"tag": "h2", "text": "Blurb"}]},
            # doc-later duplicate (the composite section's own subtree)
            {"tag": "div", "class": "composite", "children": [
                {"tag": "p", "text": "SECOND_COPY"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "widget"},
        {"index": 1, "tag": "section", "cls": "blurb"},
        {"index": 2, "tag": "div", "cls": "composite"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    # The composite SECTION component must claim the doc-LATER copy...
    comp = _component_containing(impl, "SECOND_COPY")
    assert comp and comp.startswith("StyleComposite") or (comp and "composite" in comp.lower()) or comp, \
        f"composite section must exist; got {comp}"
    # ...and SECOND_COPY must appear exactly once (no double emission) and NOT in
    # an _UncoveredAfter fragment.
    assert blob.count("SECOND_COPY") == 1, f"SECOND_COPY emitted {blob.count('SECOND_COPY')}x (dup?)"
    uncov_files = [p.read_text(encoding="utf-8")
                   for p in (impl / "src" / "components").glob("_Uncovered*.tsx")]
    assert not any("SECOND_COPY" in u for u in uncov_files), \
        "the true composite subtree must be CLAIMED, not orphaned into _UncoveredAfter"


def test_single_duplicate_still_claimed_doc_earlier(tmp_path: Path) -> None:
    """FIX-3 fallback safety: min_doc is a PREFERENCE, not a filter. When there is
    only ONE instance of a class and it sits doc-EARLIER than the previous
    section's subtree, the section must still claim it (unconstrained fallback) —
    no subtree-not-found stub, no uncovered fragment."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            # 'lonely' appears doc-BEFORE the 'tall' section that precedes it in
            # the section-map order — the only instance, must still be claimed.
            {"tag": "div", "class": "lonely", "children": [{"tag": "p", "text": "LONELY_COPY"}]},
            {"tag": "section", "class": "tall", "children": [{"tag": "h2", "text": "Tall"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "tall"},
        {"index": 1, "tag": "div", "cls": "lonely"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert "LONELY_COPY" in blob, "the only 'lonely' instance must still be claimed"
    assert "subtree-not-found" not in blob, "no stub — the fallback must recover it"


def test_svg_url_attr_emits_valid_jsx_not_escaped_quotes(tmp_path: Path) -> None:
    """SVG attrs whose value contains a quoted url() — mask="url(\"#id\")" — must
    emit valid JSX (an expression or single-quoted), NOT a double-quoted value
    with backslash-escaped inner quotes, which esbuild rejects and breaks the
    entire build (realfood Winning.tsx / _UncoveredHead.tsx)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "svg", "svg": True, "children": [
                    {"tag": "path", "svg": True, "fill": "#2BC03C",
                     "mask": 'url("#rfw-checkmark-mask-0")'},
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "rfw-checkmark-mask-0" in blob, "mask url must be preserved"
    # The broken pattern: a double-quoted attr containing \" — must NOT appear.
    assert 'mask="url(\\"' not in blob, "must not emit backslash-escaped quotes in a double-quoted JSX attr"
    # Valid form: a JSX expression container for the mask value.
    assert "mask={" in blob, "quoted-url attr must be emitted as a JSX expression"


def test_small_static_translate_is_preserved_large_reveal_reset(tmp_path: Path) -> None:
    """Codex-review HIGH regression: _is_scroll_state_translation stripped ANY
    pure px translate >=4px with no animation marker, dropping legitimate static
    layout nudges (translateX(8px)) on other sites. Small marker-less translates
    must be PRESERVED; only large mid-scroll displacements (realfood reveals were
    37-81px) are reset."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                # static layout nudge, NO transition/animation marker -> keep
                {"tag": "div", "class": "nudge", "text": "STATIC_NUDGE",
                 "styles": {"transform": "translateX(8px)"}},
                # large marker-less displacement (mid-scroll capture) -> reset
                {"tag": "div", "class": "reveal", "text": "BIG_REVEAL",
                 "styles": {"transform": "translateX(60px)"}},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "translateX(8px)" in blob, "small static translate must be preserved"
    assert "translateX(60px)" not in blob, "large marker-less mid-scroll translate must be reset"


def test_fixed_overlay_offscreen_transform_is_preserved(tmp_path: Path) -> None:
    """A position:fixed full-screen overlay parked OFF-SCREEN via a large
    translate (intro splash: transform translateY(-900px)) must keep that
    transform — stripping it un-hides the overlay so it covers the whole page
    (realfood intro-animation_overlay rendered the page green/black). Fixed/
    sticky transforms park/position; they are never scroll-scrub reveals."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "div", "class": "intro-overlay", "text": "SPLASH",
                 "styles": {"position": "fixed", "z-index": "100000",
                            "background-color": "rgb(6, 103, 66)",
                            "transform": "translateY(-900px)"}},
                # a real (non-fixed) mid-scroll reveal still resets:
                {"tag": "div", "class": "reveal", "text": "REVEAL",
                 "styles": {"position": "relative", "transform": "translateX(60px)"}},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "translateY(-900px)" in blob, "fixed overlay off-screen transform must be preserved"
    assert "translateX(60px)" not in blob, "non-fixed mid-scroll reveal still resets"


def test_root_body_emitted_as_viewport_div_with_ref_bg(tmp_path: Path) -> None:
    """P1: the captured root <body>/<html> must NOT be re-emitted as a nested
    <body> inside #root (invalid HTML; page base bg may not paint). Render it as
    a viewport-filling <div> carrying the ref body's background (cream), so the
    page base is the ref body color — not a dark section leaking to the root."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "class": "antialiased",
        "styles": {"background-color": "rgb(253, 251, 238)", "color": "rgb(17, 0, 0)"},
        "children": [
            {"tag": "section", "class": "hero", "styles": {"background-color": "rgb(17, 0, 0)"},
             "children": [{"tag": "h1", "text": "Dark Hero"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    assert "<body" not in app, "must not emit a nested <body> inside the mount point"
    # root carries the ref body background (cream) and fills the viewport
    assert 'backgroundColor: "rgb(253, 251, 238)"' in app
    assert 'minHeight: "100vh"' in app, "page base must fill the viewport so cream backs the whole page"


def _word_split(words: list[str]) -> list[dict]:
    """Build a Framer-style per-word split run: each word in its own span,
    individually wrapped (no whitespace text nodes between)."""
    return [{"tag": "span", "children": [{"tag": "span", "class": "dga_line_dimmed__x",
                                          "text": w}]} for w in words]


def test_word_split_run_collapses_to_clean_text(tmp_path: Path) -> None:
    """TOP BUG: per-WORD split spans (96 one-word leaf spans) survived Fix 27
    (which only collapses single-char splits), laid out in one line, and blew
    page width to 7154px. They must collapse to clean wrapping text."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    words = "For decades we have been misled by guidance that prioritized highly processed food".split()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "span", "class": "dga_headline", "children": _word_split(words)},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    # collapsed to contiguous text (would be split across spans before the fix)
    assert "For decades we have been misled by guidance" in blob


def test_split_text_collapse_wraps_headline_in_inner_div(tmp_path: Path) -> None:
    """Fix 120: a split-text headline carries its real size on the INNER char
    spans (96px) inside a narrower column (608px), while the OUTER wrapper the
    collapse fires on is a wide flex box at the inherited body 16px. The
    collapsed text must be emitted inside a NEW inner div that owns the
    typography + column max-width — the OUTER wrapper keeps its own styles
    (16px / 896px / flex), so its flow/flex parent role is never touched
    (putting the constraint on the wrapper itself cascades the page layout)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    chars = list("Real") + [" "] + list("Food") + [" "] + list("can")  # >=10 leaves
    big = {"font-size": "96px", "line-height": "91.2px", "color": "rgb(253, 251, 238)"}
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "solvable",
             "styles": {"font-size": "16px", "width": "896px", "display": "flex"},
             "children": [
                {"tag": "div", "class": "col",
                 "styles": dict(big, **{"width": "608px"}), "children": [
                    {"tag": "h2", "class": "headline",
                     "styles": dict(big, **{"width": "608px"}), "children": [
                        {"tag": "span", "styles": big, "text": c} for c in chars
                    ]},
                 ]},
             ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "solvable"}]}),
        encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    comp = (impl / "src" / "components" / "Solvable.tsx").read_text()
    assert "Real Food can" in comp  # clean reassembly survives
    # the OUTER wrapper keeps its inherited body size — NOT re-sized to 96px
    outer = comp[comp.index('className="solvable"'):comp.index(">", comp.index('className="solvable"'))]
    assert 'fontSize: "16px"' in outer, "outer wrapper font must stay 16px (untouched)"
    assert 'maxWidth: "608px"' not in outer, "column constraint must NOT land on the wrapper"
    # the inner div owns the headline typography + column width
    assert 'fontSize: "96px"' in comp
    assert 'lineHeight: "91.2px"' in comp
    assert 'maxWidth: "608px"' in comp
    assert 'marginLeft: "auto"' in comp and 'marginRight: "auto"' in comp


def test_split_text_collapse_stays_flat_when_no_distinct_inner_layout(tmp_path: Path) -> None:
    """Guard: when the split spans carry no distinct font and no narrower column
    (the wrapper already holds the real type), collapse stays FLAT — the text
    folds onto the wrapper and no spurious inner div / max-width is emitted."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    chars = list("Real") + [" "] + list("Food") + [" "] + list("now")
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "plain",
             "styles": {"font-size": "44px"}, "children": [
                {"tag": "h2", "class": "headline", "children": [
                    {"tag": "span", "text": c} for c in chars
                ]},
             ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "plain"}]}),
        encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    comp = (impl / "src" / "components" / "Plain.tsx").read_text()
    # flat: text sits directly in the wrapper, no synthetic inner column
    assert "Real Food now" in comp
    assert 'marginLeft: "auto"' not in comp, "no synthetic inner div when nothing distinct"


def test_split_text_preserved_when_signature_effect_targets_node(tmp_path: Path) -> None:
    """A declared per-character signature effect owns the split structure.

    The scaffold must preserve the captured char spans so downstream generation
    can wire the effect instead of flattening the target first.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    chars = list("Real") + [" "] + list("Food") + [" "] + list("now")
    (ref / "generation-plan.json").write_text(json.dumps({
        "schemaVersion": 2,
        "signatureEffects": [{
            "selector": ".headline",
            "name": "DisintegratingText",
            "effectType": "per-character-scroll-scrub",
        }],
    }), encoding="utf-8")
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "plain", "children": [
                {"tag": "h2", "class": "headline", "children": [
                    {"tag": "span", "text": c} for c in chars
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "plain"}]}),
        encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    comp = (impl / "src" / "components" / "Plain.tsx").read_text()
    assert comp.count("<span>") >= len(chars)
    assert "Real Food now" not in comp


def test_split_text_collapse_when_signature_effect_selector_does_not_match(
    tmp_path: Path,
) -> None:
    """Non-matching signature effects do not disable ordinary split collapse."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    chars = list("Real") + [" "] + list("Food") + [" "] + list("now")
    (ref / "generation-plan.json").write_text(json.dumps({
        "schemaVersion": 2,
        "signatureEffects": [{
            "selector": ".other-headline",
            "name": "DisintegratingText",
            "effectType": "per-character-scroll-scrub",
        }],
    }), encoding="utf-8")
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "plain", "children": [
                {"tag": "h2", "class": "headline", "children": [
                    {"tag": "span", "text": c} for c in chars
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "plain"}]}),
        encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    comp = (impl / "src" / "components" / "Plain.tsx").read_text()
    assert "Real Food now" in comp


WORD_SPLIT_WORDS = (
    "For the first time official guidance calls on Americans to avoid "
    "highly processed food every single day"
).split()


def _word_split_ref(tmp_path: Path, effect_type: str, selector: str) -> tuple[Path, Path]:
    """Ref whose body copy is a per-WORD split, in realfood-v2's exact shape:
    `div.container > p > span.text_line > span > span.line_dimmed`.

    The `<p>` matters: the ref sizes this copy with `p{font-size:clamp(...)}`, so
    a collapse that drops the `<p>` and the span tree also drops the rule that
    gives the block its height.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    word_spans = [
        {"tag": "span", "children": [
            {"tag": "span", "class": "line_dimmed", "text": w}]}
        for w in WORD_SPLIT_WORDS
    ]
    (ref / "generation-plan.json").write_text(json.dumps({
        "schemaVersion": 2,
        "signatureEffects": [{
            "selector": selector,
            "name": "WordRevealText",
            "effectType": effect_type,
        }],
    }), encoding="utf-8")
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "plain", "children": [
                {"tag": "div", "class": "container", "children": [
                    {"tag": "p", "children": [
                        {"tag": "span", "class": "text_line", "children": word_spans},
                    ]},
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "plain"}]}),
        encoding="utf-8")
    return ref, impl


# realfood-v2's declared selector, verbatim in shape: a COMMA LIST whose first
# alternative ends in a bare tag and whose second ends in an ATTRIBUTE selector.
WORD_SPLIT_SELECTOR = ".broken_system_text > span > span, .text_line span[data-word-id]"


def test_per_word_signature_effect_preserves_split_structure(tmp_path: Path) -> None:
    """A declared per-WORD signature effect owns its split structure exactly as a
    per-character one does, and must suppress the collapse.

    Two things previously stopped this from working on realfood-v2, so the
    paragraph flattened to one bare string and `p{font-size:clamp(42px,12vw,96px)}`
    had no `<p>` left to size:

      1. The effect-text allowlist accepted per-character/split/disintegrate but
         not per-WORD, so the selector never reached the preserve list at all.
      2. Even in the list it could not match: the selector is a COMMA LIST that
         was tested as a single string, and its subject (`span[data-word-id]`)
         is an attribute selector, which the conservative same-node matcher
         rejects outright — as it does the bare-tag subject of the other
         alternative.

    Preservation stays strictly opt-in: only a DECLARED effect suppresses the
    collapse, so undeclared word-splits still flatten (see
    test_word_split_collapse_stays_flat_no_inner_wrapper)."""
    ref, impl = _word_split_ref(
        tmp_path, "per-word scroll highlight", WORD_SPLIT_SELECTOR)
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    comp = (impl / "src" / "components" / "Plain.tsx").read_text()
    joined = " ".join(WORD_SPLIT_WORDS)
    assert joined not in comp, (
        f"the declared per-word split must NOT be collapsed to one string; got:\n{comp}"
    )
    assert "<p" in comp, (
        "the <p> must survive — the ref sizes this copy with a p{font-size:clamp()} "
        f"rule that cannot apply without it; got:\n{comp}"
    )
    assert "text_line" in comp and "line_dimmed" in comp, (
        f"the split wrapper/leaf classes must survive; got:\n{comp}"
    )
    assert comp.count("<span") >= len(WORD_SPLIT_WORDS), (
        f"every word span must survive; got {comp.count('<span')} spans:\n{comp}"
    )


def test_word_split_collapse_stays_flat_no_inner_wrapper(tmp_path: Path) -> None:
    """Fix 120 v4 gate: a per-WORD split (word-reveal body/quote) must NOT get
    the inner-wrapper headline treatment even when its captured leaves carry a
    large font — that size is a dimmed/transient reveal frame, not the rest
    state, so enlarging it amplifies the mismatch. Only per-CHARACTER splits
    (display headlines whose big font IS the rest state) get the inner div.
    The word-split here carries a captured 96px on its word leaves yet must
    collapse FLAT (text on the wrapper, no 96px inner div, no max-width)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    words = ("The government message is simple what we eat shapes how long "
             "and how well we live every single day of our lives").split()
    big = {"font-size": "96px", "line-height": "92.16px", "color": "rgb(17, 0, 0)"}
    # per-word split: each word its own leaf span (carrying the captured 96px)
    word_spans = [{"tag": "span", "children": [
        {"tag": "span", "styles": big, "text": w}]} for w in words]
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "endquote",
             "styles": {"font-size": "16px", "width": "1440px"}, "children": [
                {"tag": "div", "class": "col",
                 "styles": dict(big, **{"width": "1076px"}), "children": [
                    {"tag": "p", "styles": dict(big, **{"width": "1076px"}),
                     "children": word_spans},
                 ]},
             ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "endquote"}]}),
        encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    comp = (impl / "src" / "components" / "Endquote.tsx").read_text()
    assert "The government message is simple" in comp  # clean reassembly
    # word-split → flat collapse: no headline inner div, no 96px, no column
    assert 'fontSize: "96px"' not in comp, "word-split must NOT be enlarged to 96px"
    assert 'marginLeft: "auto"' not in comp, "word-split must NOT get the inner wrapper"
    assert 'maxWidth: "1076px"' not in comp, "word-split must NOT get a column constraint"


def test_word_split_guard_preserves_nav_links(tmp_path: Path) -> None:
    """Guard: a list of single-word interactive elements (nav links) must NOT be
    collapsed — they look like a word-split run but are real links."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    links = "Home About Programs Resources Guidance Pyramid Science Contact Login Search Blog News".split()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "nav", "class": "menu", "children": [
                    {"tag": "a", "href": "/" + w.lower(), "text": w} for w in links
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert blob.count("<a ") >= 12, "nav links must be preserved, not collapsed to text"


def test_split_text_guard_preserves_structured_palette_chips(tmp_path: Path) -> None:
    """AA/AAA contrast chips inside a semantic image are not split text."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    labels = [
        ("AA", "AAA"), ("AA", "AAA"), ("AA", "AA"), ("AA", "AAA"),
        ("AA", "AA"), ("AA", "AA"), ("AA", "AA"), ("AA", "AA"),
        ("AA", "AA"), ("AAA", "AA"), ("AAA", "AA"), ("AAA", "AA"),
    ]
    description = (
        "Animated showcase of the Evo design system's color palette, displaying "
        "tone-on-tone color chips with AA or AAA contrast labels"
    )
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "palette", "children": [
                {"tag": "div", "class": "style_flipGrid__root", "role": "img",
                 "aria-label": "Color palette", "children": [
                    {"tag": "div", "class": f"style_flipCard__chip style_tone_{i}",
                     "styles": {"background-color": "rgb(245, 245, 245)"},
                     "children": [
                         {"tag": "div", "class": "style_flipCard__inner", "children": [
                             {"tag": "div", "class": "style_flipCard__front", "children": [
                                 {"tag": "span", "class": "style_contrastLabel__label",
                                  "text": front},
                             ]},
                             {"tag": "div", "class": "style_flipCard__back", "children": [
                                 {"tag": "span", "class": "style_contrastLabel__label",
                                  "text": back},
                             ]},
                         ]},
                     ]} for i, (front, back) in enumerate(labels)
                 ] + [
                     {"tag": "p", "class": "clipped", "text": description},
                 ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "palette"}]}),
        encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert blob.count("style_flipCard__chip") == len(labels), (
        "palette cards must stay as repeated chip nodes, not collapse into one text run"
    )
    assert 'className="clipped"' in blob
    assert description in blob


def test_lazy_images_injected_into_matching_section(tmp_path: Path) -> None:
    """P2: images captured in visible-images.json but absent from structure.json
    (lazy/IntersectionObserver pyramid gallery) must be injected into the section
    whose class matches their /images/<category>/ path, so the transpiler emits
    them (and asset-download harvests them) instead of dropping the gallery."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "dga_erf_pyramid__x",
             "children": [{"tag": "div", "class": "gallery", "children": []}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "dga_erf_pyramid__x"}]}),
        encoding="utf-8",
    )
    base = "https://realfood.gov/cdn-cgi/image/width=2048,quality=90,format=auto,fit=scale-down"
    (ref / "visible-images.json").write_text(json.dumps([
        {"src": f"{base}/images/pyramid/broccoli.webp", "alt": "Broccoli"},
        {"src": f"{base}/images/pyramid/almond.webp", "alt": "Almond"},
        {"src": f"{base}/images/pyramid/milk.webp", "alt": "Milk"},
    ]), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "/images/pyramid/broccoli.webp" in blob
    assert "/images/pyramid/almond.webp" in blob
    assert "/images/pyramid/milk.webp" in blob
    assert blob.count("<img ") >= 3, "lazy gallery images must be emitted as <img>"


def test_cjk_char_split_collapses_icon_run_preserved(tmp_path: Path) -> None:
    """P3b (codex MED): split-text collapse must still reassemble a CJK per-char
    split (Korean), but must NOT collapse an icon-font glyph run (single PUA
    chars carry no real text) into garbage. Guard: require real letters."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    cjk = list("안녕하세요반갑습니다정말좋은하루되세요")  # >=12 single Hangul chars
    icons = ["", "", "", "", "", "",
             "", "", "", "", "", ""]
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "span", "class": "cjk-headline",
                 "children": [{"tag": "span", "text": c} for c in cjk]},
                {"tag": "div", "class": "icon-row",
                 "children": [{"tag": "i", "class": "icon-glyph", "text": g} for g in icons]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    # CJK split reassembles to contiguous text (no spaces inserted)
    assert "안녕하세요반갑습니다정말좋은하루되세요" in blob
    # icon-font glyph run is NOT collapsed — its spans survive
    assert blob.count("icon-glyph") >= 12, "icon-font glyph run must not be collapsed to text"


def test_app_wraps_reveal_sections_in_scrollreveal(tmp_path: Path) -> None:
    """P3a: ScrollReveal must not be dead code. App must import it and wrap ONLY
    sections that contain real scroll/load opacity reveals; static sections
    (no reveal reset) stay unwrapped so they do not wrongly animate."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "revealsec", "children": [
                {"tag": "div", "text": "Fades in",
                 "styles": {"opacity": "0", "transition-property": "opacity"}},
            ]},
            {"tag": "section", "class": "staticsec", "children": [
                {"tag": "div", "text": "Always visible", "styles": {"opacity": "1"}},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "revealsec"},
        {"index": 1, "tag": "section", "cls": "staticsec"},
    ]}), encoding="utf-8")
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": True, "config": {}},
        "scrollDriven": {"required": True, "hooks": ["useScroll", "useTransform", "scrollYProgress"]},
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (impl / "src" / "lib" / "ScrollReveal.tsx").exists()
    app = _app_tsx(impl)
    assert "import ScrollReveal from './lib/ScrollReveal'" in app
    import re as _re
    assert "<ScrollReveal>" in app and "</ScrollReveal>" in app
    # the wrapped component is the reveal one, not the static one
    wrapped = _re.findall(r"<ScrollReveal><(\w+) ?/?>", app)
    assert wrapped, f"no component wrapped in ScrollReveal:\n{app}"
    assert any("eveal" in w for w in wrapped), f"reveal section must be the wrapped one: {wrapped}"
    assert not any("tatic" in w for w in wrapped), f"static section must NOT be wrapped: {wrapped}"


def test_stale_autogen_components_removed_handwritten_kept(tmp_path: Path) -> None:
    """P4: reused impl dirs accumulate stale auto-generated components (e.g. a
    component renamed across versions like _UncoveredText -> _UncoveredAfter*),
    inflating the section count and risking duplicate/orphan content. The
    transpiler must remove its OWN stale auto-gen components on regen, while
    leaving hand-written components untouched."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    comp = impl / "src" / "components"
    comp.mkdir(parents=True)
    # stale auto-generated orphan from a previous run
    (comp / "_OldStale.tsx").write_text(
        "// Auto-generated by skills/visual-debug/scripts/scaffold-to-jsx.sh\n"
        "export default function _OldStale(){return <div>STALE_ORPHAN</div>}\n",
        encoding="utf-8",
    )
    # hand-written component must be preserved
    (comp / "MyCustom.tsx").write_text(
        "export default function MyCustom(){return <div>CUSTOM_KEEP</div>}\n",
        encoding="utf-8",
    )
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "hero", "children": [{"tag": "h1", "text": "Hi"}]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (comp / "_OldStale.tsx").exists(), "stale auto-gen component must be removed on regen"
    assert (comp / "MyCustom.tsx").exists(), "hand-written component must be preserved"
    assert "CUSTOM_KEEP" in (comp / "MyCustom.tsx").read_text()


def test_large_fixed_widths_become_responsive(tmp_path: Path) -> None:
    """P5: large fixed px widths on LAYOUT containers (desktop capture width)
    must become max-width + width:100% so the page reflows at narrow viewports.
    Replaced elements (img/video) and small fixed widths are left alone."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "styles": {"width": "1440px"}, "children": [
                {"tag": "div", "class": "container", "styles": {"width": "600px"},
                 "children": [{"tag": "p", "text": "Copy"}]},
                {"tag": "img", "src": "/images/logo.webp", "styles": {"width": "300px"}},
                {"tag": "button", "styles": {"width": "40px"}, "children": [{"tag": "span", "text": "x"}]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    # large layout widths -> responsive
    assert 'maxWidth: "1440px"' in blob and 'maxWidth: "600px"' in blob
    assert 'width: "100%"' in blob
    # fixed 1440/600 px width must be gone from layout containers
    assert 'width: "1440px"' not in blob, "section fixed width must be converted"
    assert 'width: "600px"' not in blob, "container fixed width must be converted"
    # replaced element keeps intrinsic width; small button keeps its fixed width
    assert 'width: "300px"' in blob, "img intrinsic width preserved"
    assert 'width: "40px"' in blob, "small fixed width preserved"


def test_reveal_section_with_sticky_descendant_not_wrapped(tmp_path: Path) -> None:
    """P6 regression: a ScrollReveal wrapper applies a transform, which breaks
    position:sticky on ANY descendant. A reveal section that CONTAINS a sticky
    element must NOT be wrapped (not just sections whose root is sticky)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "revealsticky", "children": [
                # reveal trigger (transform reset) -> would mark as reveal section
                {"tag": "div", "text": "Reveal", "styles": {"transform": "translateX(60px)"}},
                # sticky child that must keep pinning -> section must not be wrapped
                {"tag": "div", "class": "pin", "text": "Pinned", "styles": {"position": "sticky", "top": "0px"}},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "revealsticky"}]}), encoding="utf-8"
    )
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": True, "config": {}},
        "scrollDriven": {"required": True, "hooks": ["useScroll", "useTransform", "scrollYProgress"]},
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    assert "<ScrollReveal>" not in app, "section with a sticky descendant must NOT be ScrollReveal-wrapped"


def test_sticky_wrapper_minheight_bakes_negative_bottom_margin(tmp_path: Path) -> None:
    """S1: a position:sticky section's relative containing-block ancestor is
    re-emitted (Fix 26) to bound the pin's scroll range. The ancestor's captured
    `height` was used verbatim as the wrapper min-height, but realfood's
    `dga_solvable_problem` ancestor also carries a negative bottom margin
    (margin: 0 0 -675px) that overlaps the following section. Dropping that
    margin while keeping the full captured height inflates the wrapper by the
    margin amount (h=2700 vs ~2025 real flow height) and drifts every section
    below down (~+800px, docH inflation — the dominant 'sections drift'). The
    wrapper min-height must be the effective flow height: height + negative
    margin-bottom. A positive/zero bottom margin leaves the floor unchanged."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "div", "class": "solvable", "styles": {
                "position": "relative", "height": "2700px", "min-height": "2700px",
                "margin": "0px 0px -675px"},
             "children": [
                 {"tag": "div", "class": "pin", "text": "Pinned",
                  "styles": {"position": "sticky", "top": "0px"}},
             ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "div", "cls": "pin"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    # The relative ancestor wrapper is re-emitted around the sticky section.
    assert "solvable" in blob, f"relative ancestor wrapper must be re-emitted; got:\n{blob}"
    # min-height must bake the negative bottom margin: 2700 + (-675) = 2025.
    assert 'minHeight: "2025px"' in blob, (
        f"wrapper must size to effective flow height (height + neg margin-bottom); got:\n{blob}"
    )
    assert 'minHeight: "2700px"' not in blob, (
        "wrapper must not use the stale captured height verbatim (drops the overlap)"
    )


def test_section_root_preserves_negative_bottom_margin_overlap(tmp_path: Path) -> None:
    """S1 (dominant case): a section root (relative, not sticky) can deliberately
    overlap the next section with a negative bottom margin (captured height H,
    margin-bottom -M). Its box comes from the section root's own height→min-height
    floor — NOT the Fix 26 sticky-ancestor wrapper. That overlap is flow-NEUTRAL:
    the box is M px taller, and the negative margin pulls the next sibling up by
    exactly M, so the following section's flow position is unchanged. The
    transpiler must therefore keep the FULL captured height as the min-height
    floor AND preserve the negative bottom margin verbatim (NOT fold it to H-M
    and NOT zero margin-bottom) so the intentional overlap reproduces. Folding it
    away renders the box M px too short and erases the overlap, which section-
    compare then measures as a height mismatch."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "div", "class": "solvable", "styles": {
                "display": "flex", "position": "relative", "height": "2700px",
                "min-height": "2700px", "margin": "0px 0px -675px"},
             "children": [
                 {"tag": "h2", "text": "Real Food can solve this crisis."},
             ]},
            {"tag": "section", "class": "next", "children": [{"tag": "p", "text": "After"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "div", "cls": "solvable"},
        {"index": 1, "tag": "section", "cls": "next"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    import re as _re
    m = _re.search(r'className="solvable"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert m, f"solvable section must be emitted; got:\n{blob}"
    style = m.group(1)
    # Full captured height kept as the min-height floor (overlap is flow-neutral).
    assert 'minHeight: "2700px"' in style, f"floor must keep full captured height; got:\n{style}"
    assert 'minHeight: "2025px"' not in style, "must not fold the overlap into the floor (renders M px too short)"
    # Negative bottom margin preserved verbatim so the deliberate overlap reproduces.
    assert 'margin: "0px 0px -675px"' in style, f"negative bottom margin must be preserved; got:\n{style}"
    assert 'marginBottom: "0px"' not in style, "must not neutralise the bottom margin (erases the overlap)"


def test_nested_element_preserves_negative_bottom_margin_overlap(tmp_path: Path) -> None:
    """S1 (generic, non-root case): the negative-bottom-margin-is-flow-neutral
    rule is NOT special to section roots. A nested content element (e.g. a graphic
    that overlaps the element below it via margin-bottom -M while holding its own
    captured height) must likewise keep its full height as a min-height floor and
    preserve the negative bottom margin — not fold it away. This is the lever that
    fixes an inner overlap (~150px too tall) without any class-name special case."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "host", "styles": {
                "position": "relative", "height": "1098px", "padding": "256px 0px 0px"},
             "children": [
                 {"tag": "div", "class": "title", "text": "Title",
                  "styles": {"height": "130px"}},
                 {"tag": "div", "class": "graphic", "styles": {
                     "position": "relative", "height": "921px",
                     "margin": "-60px 0px -150px"},
                  "children": [{"tag": "span", "text": "node"}]},
             ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "host"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    import re as _re
    m = _re.search(r'className="graphic"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert m, f"graphic element must be emitted; got:\n{blob}"
    style = m.group(1)
    assert 'minHeight: "921px"' in style, f"nested element must keep full height as floor; got:\n{style}"
    assert 'minHeight: "771px"' not in style, "must not fold the inner overlap (921-150) into the floor"
    assert 'margin: "-60px 0px -150px"' in style, f"nested negative bottom margin must be preserved; got:\n{style}"
    assert 'marginBottom: "0px"' not in style, "must not neutralise the nested bottom margin"


def test_autoplay_background_video_gets_playback_attrs(tmp_path: Path) -> None:
    """P7: a background video is a JS-runtime element — assets.json records
    autoplay/loop/muted but the transpiler emitted a bare <video src>. Emit
    autoPlay/muted/loop/playsInline for autoplay videos so they actually play;
    non-autoplay videos (e.g. a click-to-play announcement) must NOT autoplay."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "video", "src": "https://realfood.gov/video/bgv.mp4"},
                {"tag": "video", "src": "https://realfood.gov/video/announce.mp4", "aria-label": "Announcement"},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    (ref / "assets.json").write_text(json.dumps({"videos": [
        {"src": "https://realfood.gov/video/bgv.mp4", "autoplay": True, "loop": True, "muted": True},
        {"src": "https://realfood.gov/video/announce.mp4", "autoplay": False, "loop": False, "muted": True},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    import re as _re
    # the bgv video tag must carry autoplay attrs
    bgv = _re.search(r"<video[^>]*bgv\.mp4[^>]*>", blob)
    assert bgv, "bgv video must be emitted"
    tag = bgv.group(0)
    assert "autoPlay" in tag and "muted" in tag and "loop" in tag and "playsInline" in tag, f"bg video missing playback attrs: {tag}"
    # the announce video must NOT autoplay
    ann = _re.search(r"<video[^>]*announce\.mp4[^>]*>", blob)
    assert ann and "autoPlay" not in ann.group(0), "non-autoplay video must not autoplay"


def test_stroke_draw_paths_stamped_and_driver_wired(tmp_path: Path) -> None:
    """transition-fires (P6): SVG paths the ref draws in via strokeDashoffset
    are captured frozen WITH a stroke-dasharray (the JS-prepared draw state).
    With a stroke-draw spec entry, the transpiler must stamp those paths
    data-stroke-draw and mount <ScrollStateDriver /> so the driver animates the
    draw. Paths without a dasharray are static art and must not be stamped."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "art", "children": [
                {"tag": "svg", "svg": True, "viewBox": "0 0 100 100", "children": [
                    {"tag": "path", "svg": True, "class": "draw",
                     "d": "M0 0L100 100", "stroke": "#111",
                     "stroke-dasharray": "240"},
                    {"tag": "path", "svg": True, "class": "staticart",
                     "d": "M0 100L100 0", "stroke": "#111"},
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "art"}]}), encoding="utf-8"
    )
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "svg-stroke-draw",
             "trigger": "in-view / scroll state (t ? drawn : hidden)",
             "bundle_branch": "initial:{strokeDashoffset:o} animate:{strokeDashoffset:t?0:o}",
             "animation": {"property": "strokeDashoffset", "from": "dashLength",
                           "to": 0, "duration": 1.0, "ease": "[0.25, 1, 0.5, 1]"}},
        ],
    }), encoding="utf-8")
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollDriven": {"required": True, "library": "framer-motion", "hooks": []},
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    import re as _re
    draw = _re.search(r'className="draw"[^>]*', blob)
    assert draw and "data-stroke-draw" in draw.group(0), (
        f"dasharray-frozen path must be stamped; got:\n{draw.group(0) if draw else blob}"
    )
    static = _re.search(r'className="staticart"[^>]*', blob)
    assert static and "data-stroke-draw" not in static.group(0), (
        "paths without a dasharray are static art"
    )
    app = _app_tsx(impl)
    assert "<ScrollStateDriver />" in app, "App must mount the driver for stroke stamps"


def test_ancestor_backdrop_propagates_to_flat_sections(tmp_path: Path) -> None:
    """Screenshot-verified defect: the solvable headline renders white-on-cream
    invisible — the ref wraps the mid-page sections in a dark band
    (dga_dark: background rgb(17,0,0)), and the flat section emission drops
    that wrapper, losing the backdrop. The nearest non-root ancestor's SOLID
    background must propagate onto a section root that has none; a section
    with its own background keeps it; sections with no dark ancestor are
    unchanged."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body", "class": "antialiased",
        "styles": {"background-color": "rgb(253, 251, 238)"},
        "children": [
            {"tag": "div", "class": "darkband",
             "styles": {"background-color": "rgb(17, 0, 0)"},
             "children": [
                 # no own bg -> must inherit the dark band
                 {"tag": "section", "class": "deepdark",
                  "styles": {"color": "rgb(255, 255, 255)"},
                  "children": [{"tag": "h2", "text": "White copy"}]},
                 # own bg -> keep it
                 {"tag": "section", "class": "ownbg",
                  "styles": {"background-color": "rgb(10, 20, 30)"},
                  "children": [{"tag": "h2", "text": "Own"}]},
             ]},
            # no dark ancestor -> untouched (no propagated bg)
            {"tag": "section", "class": "plain",
             "children": [{"tag": "h2", "text": "Plain"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "deepdark"},
        {"index": 1, "tag": "section", "cls": "ownbg"},
        {"index": 2, "tag": "section", "cls": "plain"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    import re as _re
    # the band paints via a full-bleed wrapper div AROUND the section (the ref
    # band is full-width while the section is a narrower column — painting only
    # the column would leave cream gutters)
    # Fix 97 (#1) — the band wrapper also breaks out to full viewport width so
    # the backdrop reaches the screen edge even when the reflowed root carries a
    # max-width (otherwise side gutters appear on wide screens).
    dd = _re.search(
        r'<div style=\{\{ backgroundColor: "rgb\(17, 0, 0\)", '
        r'width: "100vw", marginLeft: "calc\(50% - 50vw\)", '
        r'marginRight: "calc\(50% - 50vw\)" \}\}>\s*'
        r'<section className="deepdark"',
        blob,
    )
    assert dd, f"dark-band wrapper (full-bleed) must surround the bg-less section; got:\n{blob}"
    ob = _re.search(r'className="ownbg"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert ob and 'backgroundColor: "rgb(10, 20, 30)"' in ob.group(1), "own bg must win"
    own_file = [p.read_text(encoding="utf-8") for p in (impl / "src" / "components").glob("*.tsx")
                if 'className="ownbg"' in p.read_text(encoding="utf-8")][0]
    assert '<div style={{ backgroundColor: "rgb(17, 0, 0)" }}>' not in own_file, (
        "a section with its own bg needs no band wrapper"
    )
    pl = _re.search(r'className="plain"[^>]*', blob)
    assert pl and "rgb(17, 0, 0)" not in pl.group(0), "no dark ancestor -> no dark bg"


def _band_ref(tmp_path: Path, section_node: dict, dominant_bg: str | None = None) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "div", "class": "wrap",
                      "styles": {"background-color": "rgb(21, 21, 21)"},
                      "children": [section_node]}],
    }), encoding="utf-8")
    sec_entry: dict = {"index": 0, "tag": section_node["tag"], "cls": section_node["class"]}
    if dominant_bg is not None:
        sec_entry["dominantBg"] = dominant_bg
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [sec_entry]}), encoding="utf-8")
    return ref, impl


def test_band_wrapper_skipped_for_absolute_overlay_root(tmp_path: Path) -> None:
    """FIX-1: a section whose ROOT is position:absolute is an overlay/pinned
    backdrop, pulled from flow and anchored to an ancestor. A full-bleed
    `width:100vw; margin:calc(50%-50vw)` band around it is a meaningless 0-height
    div — do NOT emit it. (A static/relative bg-less section still gets the band.)"""
    ref, impl = _band_ref(tmp_path, {
        "tag": "div", "class": "overlay",
        "styles": {"position": "absolute", "top": "0", "left": "0"},
        "children": [{"tag": "button", "text": "Play"}],
    })
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "calc(50% - 50vw)" not in blob, f"no full-bleed band around an absolute overlay; got:\n{blob}"


def test_band_wrapper_still_emitted_for_static_root(tmp_path: Path) -> None:
    """The gate is strictly on out-of-flow roots: a normal static/relative bg-less
    section inside a dark ancestor STILL gets the full-bleed band."""
    ref, impl = _band_ref(tmp_path, {
        "tag": "section", "class": "flat",
        "styles": {"color": "rgb(255, 255, 255)"},
        "children": [{"tag": "h2", "text": "White copy"}],
    })
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "calc(50% - 50vw)" in blob, f"static bg-less section must still get the band; got:\n{blob}"


def test_dominant_bg_not_promoted_onto_absolute_overlay_root(tmp_path: Path) -> None:
    """FIX-1: the page-dominant-bg promotion must NOT paint an absolute overlay
    root opaque — that would occlude what it overlays (e.g. a control overlay
    covering its video)."""
    ref, impl = _band_ref(tmp_path, {
        "tag": "div", "class": "overlay",
        "styles": {"position": "absolute", "top": "0", "left": "0"},
        "children": [{"tag": "button", "text": "Play"}],
    }, dominant_bg="rgb(255, 255, 255)")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    over_files = [p.read_text(encoding="utf-8")
                  for p in (impl / "src" / "components").glob("*.tsx")
                  if 'className="overlay"' in p.read_text(encoding="utf-8")]
    assert over_files, "overlay component must exist"
    assert 'backgroundColor: "rgb(255, 255, 255)"' not in over_files[0], (
        "dominant bg must NOT be promoted onto an absolute overlay root")


def test_word_grouped_char_split_keeps_inter_word_spaces(tmp_path: Path) -> None:
    """Render-verified defect: 'Real Food can solve this crisis.' rendered as
    'RealFoodcansolvethiscrisis.' — the headline is per-WORD span groups of
    per-CHAR spans, with EMPTY separator spans between words (their lone-space
    text was trimmed away at capture). The flat char-collapse joined every leaf
    with no gaps. The collapse must be group-aware: join chars WITHIN a word
    group, join groups WITH spaces."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()

    def word(w: str) -> dict:
        return {"tag": "span", "children": [
            {"tag": "span", "class": "disint_char__x", "text": ch} for ch in w
        ]}

    sep = {"tag": "span", "text": ""}  # captured-empty separator (trimmed space)
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "s0", "children": [
            {"tag": "h2", "class": "disint__t", "children": [
                word("Real"), sep, word("Food"), sep, word("can"), sep,
                word("solve"), sep, word("this"), sep, word("crisis."),
            ]},
        ]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "s0"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "Real Food can solve this crisis." in blob, (
        f"word groups must join with spaces; got:\n{blob}"
    )
    assert "RealFood" not in blob.replace(" ", "_").replace("RealFood", "RealFood"), ""
    assert "RealFoodcansolve" not in blob, "run-on must not survive"


def test_flat_char_split_still_joins_without_spaces(tmp_path: Path) -> None:
    """Control: a FLAT per-char split (chars directly under the heading, no
    word grouping) must keep the original flat join — spacing every char would
    corrupt 'Real' into 'R e a l'."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "s0", "children": [
            {"tag": "h2", "class": "flat__t", "children": [
                {"tag": "span", "class": "c", "text": ch} for ch in "Real Food wins"
            ]},
        ]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "s0"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "Real Food wins" in blob, f"flat split must reassemble unchanged; got:\n{blob}"
    assert "R e a l" not in blob


def test_heuristic_constants_env_overridable(tmp_path: Path) -> None:
    """#18 generality polish: the transform scroll-state floor (24px), the
    split-text dominance ratio (0.85), and the word-split leaf minimum (12)
    were realfood-derived hardcodes a different site could not tune — unlike
    the rest of the pipeline's env thresholds. Each is now UI_CLONE_*
    overridable with the same default. Proof: raising
    UI_CLONE_TRANSFORM_MIN_PX above a marker-less translate's offset must
    PRESERVE the transform that the default strips."""
    import os as _os
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "s0", "children": [
            {"tag": "div", "class": "nudge", "text": "Nudge",
             "styles": {"position": "absolute", "width": "400px", "height": "200px",
                        "transform": "matrix(1, 0, 0, 1, 0, 60)"}},
        ]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "s0"}]}), encoding="utf-8"
    )
    import re as _re

    def _run_with(env_extra: dict[str, str]) -> str:
        env = dict(_os.environ)
        env.update(env_extra)
        proc = subprocess.run(
            ["bash", str(SCRIPT), str(ref), str(impl)],
            capture_output=True, text=True, timeout=120, env=env,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return _impl_blob(impl)

    # default: 60px marker-less translate >= 24 floor -> stripped (Fix 21)
    blob = _run_with({})
    n = _re.search(r'className="nudge"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert n and "matrix" not in n.group(1), "default floor must strip the 60px translate"

    # raised floor: 60px is now a static layout nudge -> preserved
    blob = _run_with({"UI_CLONE_TRANSFORM_MIN_PX": "200"})
    n = _re.search(r'className="nudge"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert n and "matrix(1, 0, 0, 1, 0, 60)" in n.group(1), (
        f"raised UI_CLONE_TRANSFORM_MIN_PX must preserve the transform; got:\n{n.group(1) if n else blob}"
    )


def test_sticky_ancestor_wrapper_track_emitted_as_vh(tmp_path: Path) -> None:
    """#17 (omx-39 audit: a resources wrapper rendered frozen 2700px): the
    Fix 26 re-emitted relative ancestor wrapper around a sticky section must
    also get the Fix 80 vh re-expression — its 300vh-authored track was
    captured as 2700px @ a 900px viewport. Live ref renders 1899 (= 300vh) at
    a 633 viewport; the wrapper's min-height must emit as 300vh, not px."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "orig-layout.json").write_text(json.dumps({
        "viewportHeight": 900, "viewportWidth": 1440,
    }), encoding="utf-8")
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            # relative track wrapper (300vh @ 900 = 2700px), no margin
            {"tag": "div", "class": "resources_wrap", "styles": {
                "position": "relative", "height": "2700px"},
             "children": [
                 # the sticky section itself (100vh @ 900)
                 {"tag": "div", "class": "res_sticky", "text": "Pinned",
                  "styles": {"position": "sticky", "top": "0px", "height": "900px"}},
             ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "div", "cls": "res_sticky"}]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    import re as _re
    wrap = _re.search(r'className="resources_wrap"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert wrap and 'minHeight: "300vh"' in wrap.group(1), (
        f"wrapper track must re-express as 300vh; got:\n{wrap.group(1) if wrap else blob}"
    )
    pin = _re.search(r'className="res_sticky"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert pin and '"100vh"' in pin.group(1), "the sticky child's 900px @ 900 must emit 100vh"


def test_class_entry_does_not_steal_id_reserved_subtree(tmp_path: Path) -> None:
    """faqs collapse (caught live by the geometry-sanity gate: ref 1192 ->
    impl 136): two sections share a CSS-module class (dga_section__k3uwv);
    the class-only section-map entry is processed FIRST and class-matches the
    id-bearing faqs node, consuming it — the later id=faqs entry finds its
    node consumed and falls back to a small fragment. A class-only entry must
    never consume a node whose id is reserved by another section entry."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            # the live l132 shape: a SMALL same-classed fragment appears FIRST
            # in document order (the 136px dga_sections_text impostor) — the
            # OR-match walk used to return it for the id entry
            {"tag": "div", "class": "wrap", "children": [
                {"tag": "section", "class": "shared_sec__k3 sections_text__x",
                 "styles": {"height": "136.375px"},
                 "children": [{"tag": "p", "text": "Impostor"}]},
            ]},
            # the real id-bearing section
            {"tag": "section", "class": "shared_sec__k3", "id": "faqs",
             "styles": {"height": "1191.5px"},
             "children": [{"tag": "h2", "text": "Questions"}]},
            {"tag": "section", "class": "shared_sec__k3",
             "styles": {"height": "900px"},
             "children": [{"tag": "h2", "text": "Call to action"}]},
        ],
    }), encoding="utf-8")
    # class-only entry processed BEFORE the id entry (the stealing order)
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "shared_sec__k3"},
        {"index": 1, "tag": "section", "cls": "shared_sec__k3", "id": "faqs"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    import re as _re
    faqs = _re.search(r'id="faqs"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert faqs and '"1191.5px"' in faqs.group(1), (
        f"the id entry must get its own node (1191.5px); got:\n{faqs.group(1) if faqs else blob}"
    )
    # the class-only entry takes the OTHER instance
    assert "Call to action" in blob and "Questions" in blob


def test_viewport_proportional_heights_emitted_as_vh(tmp_path: Path) -> None:
    """S1 root cause (live-measured): the ref authors sticky scroll tracks in
    vh (solvable: height 300vh, margin-bottom -75vh). The capture resolves them
    to px at the capture viewport (900px -> 2700/-675), and freezing those px
    renders +800px at any other viewport (ref renders 1899 at a 633 viewport =
    3x633). When orig-layout.json records the capture viewportHeight, captured
    px that are near-exact >=50vh multiples of 25vh must be re-expressed in vh
    so the clone scales like the ref. The section root keeps its FULL captured
    height (2700px -> 300vh, the ref's authored value) and its negative bottom
    margin verbatim — the overlap is flow-neutral and reproduced, not folded
    away. Non-multiples (hero 638px) stay px; with no viewport record nothing
    converts."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "orig-layout.json").write_text(json.dumps({
        "viewportHeight": 900, "viewportWidth": 1440, "totalHeight": 20133,
    }), encoding="utf-8")
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            # 300vh track w/ -75vh overlap -> full 2700px kept as floor -> 300vh,
            # overlap margin preserved verbatim (flow-neutral, not folded)
            {"tag": "div", "class": "solvable", "styles": {
                "position": "relative", "height": "2700px", "min-height": "2700px",
                "margin": "0px 0px -675px"},
             "children": [
                 # 100vh sticky child
                 {"tag": "div", "class": "pin", "text": "Pinned",
                  "styles": {"position": "sticky", "top": "0px", "height": "900px"}},
             ]},
            # NOT a vh multiple -> stays px
            {"tag": "section", "class": "hero", "styles": {"height": "638.141px"},
             "children": [{"tag": "h1", "text": "Hi"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "div", "cls": "solvable"},
        {"index": 1, "tag": "section", "cls": "hero"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    import re as _re
    solv = _re.search(r'className="solvable"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert solv and 'minHeight: "300vh"' in solv.group(1), (
        f"full 2700px @900 viewport must emit 300vh (the ref's authored height); "
        f"got:\n{solv.group(1) if solv else blob}"
    )
    # The negative bottom margin (the deliberate overlap) is preserved verbatim.
    assert solv and 'margin: "0px 0px -675px"' in solv.group(1), (
        f"the overlap margin must be preserved, not folded into the height; "
        f"got:\n{solv.group(1) if solv else blob}"
    )
    pin = _re.search(r'className="pin"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert pin and '"100vh"' in pin.group(1), (
        f"900px @900 viewport sticky child must emit 100vh; got:\n{pin.group(1) if pin else blob}"
    )
    hero = _re.search(r'className="hero"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert hero and "vh" not in hero.group(1), (
        f"non-multiple 638px must stay px; got:\n{hero.group(1) if hero else blob}"
    )


def test_app_root_does_not_bake_captured_page_height(tmp_path: Path) -> None:
    """The captured <body> height is DERIVED from content at capture time
    (e.g. 20133px). Baking it inline on the App root (a) freezes a stale page
    length (docH pinned regardless of content), and (b) becomes the resolution
    base for ref-CSS `height:100%` descendants — a footer ballooned to the
    full page height in loop-128/129. The root must size from content; only
    the viewport floor (min-height:100vh) is kept."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "class": "antialiased",
        "styles": {"height": "20133.3px", "min-height": "20133.3px",
                   "background-color": "rgb(253, 251, 238)"},
        "children": [
            {"tag": "section", "class": "hero", "children": [{"tag": "h1", "text": "Hi"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    import re as _re
    root = _re.search(r'<div className="antialiased"[^>]*style=\{\{([^}]*)\}\}', app)
    assert root, f"root div must be emitted: {app}"
    style = root.group(1)
    assert "20133" not in style, f"captured page height must not be baked on the root: {style}"
    assert 'minHeight: "100vh"' in style, f"viewport floor must remain: {style}"


def test_scroll_state_fade_elements_stamped_and_driver_wired(tmp_path: Path) -> None:
    """transition-fires (P6): elements captured at the spec's INACTIVE state
    (opacity 0.5, JS-driven so no CSS transition marker) freeze there and never
    produce a runtime delta. With a state-driven transition-spec entry, the
    transpiler must stamp them data-scroll-fade and mount <ScrollStateDriver />
    in the App so the emitted driver animates them to the active state.
    Elements whose opacity is CSS-transitioned are already reset by Fix 21 and
    must NOT be stamped; without a spec entry nothing is stamped."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "content", "children": [
                # frozen at the spec's inactive opacity, no transition -> stamp
                {"tag": "div", "class": "fadestate", "text": "Fade me",
                 "styles": {"opacity": "0.5"}},
                # CSS-transitioned opacity -> Fix 21 territory, not stamped
                {"tag": "div", "class": "csstrans", "text": "CSS",
                 "styles": {"opacity": "0.5", "transition": "opacity 0.3s",
                            "transition-property": "opacity"}},
                # different opacity -> not the spec state, not stamped
                {"tag": "div", "class": "dim", "text": "Dim",
                 "styles": {"opacity": "0.8"}},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "content"}]}),
        encoding="utf-8",
    )
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "scroll-progress-fade",
             "trigger": "scroll position state (a ? active : inactive)",
             "bundle_branch": "animate:{opacity:a?1:.5,y:80*!a}",
             "animation": {"property": "opacity, y",
                           "from": {"opacity": 0.5, "y": 80},
                           "to": {"opacity": 1, "y": 0},
                           "duration": 0.8, "ease": "[0.16, 1, 0.3, 1]"}},
        ],
    }), encoding="utf-8")
    (ref / "generation-plan.json").write_text(json.dumps({
        "smoothScroll": {"required": False, "config": {}},
        "scrollDriven": {"required": True, "library": "framer-motion", "hooks": []},
    }), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    import re as _re
    fade = _re.search(r'className="fadestate"[^>]*', blob)
    assert fade and "data-scroll-fade" in fade.group(0), (
        f"frozen inactive-state element must be stamped; got:\n{fade.group(0) if fade else blob}"
    )
    csst = _re.search(r'className="csstrans"[^>]*', blob)
    assert csst and "data-scroll-fade" not in csst.group(0), "CSS-transitioned opacity is Fix 21's path"
    dim = _re.search(r'className="dim"[^>]*', blob)
    assert dim and "data-scroll-fade" not in dim.group(0), "non-spec opacity must not be stamped"
    app = _app_tsx(impl)
    assert "ScrollStateDriver" in app and "<ScrollStateDriver />" in app, (
        f"App must mount the driver when elements are stamped; got:\n{app}"
    )


def test_unreferenced_handwritten_module_atticized(tmp_path: Path) -> None:
    """scaffold-residue: a regen replaces the agent's wired components, which
    severs references to hand-written helper modules under src/ (loop-129:
    SpecTransitions/WordReveal became 4 orphan exports -> gate fail at >=3
    orphans). The transpiler must not leave dead PascalCase exports in src/ —
    fully-unreferenced, un-imported, hand-written modules are relocated to
    impl/attic/ (outside the residue scanner's src/ scope, recoverable by the
    agent), while modules still imported anywhere stay untouched."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    lib = impl / "src" / "lib"
    lib.mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{"tag": "section", "class": "hero", "children": [
            {"tag": "h1", "text": "Hello"}]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    # hand-written entry imports Used -> Used must survive
    (impl / "src" / "main.tsx").write_text(
        "import Used from './lib/Used';\nexport default function Main(){return <Used/>;}\n",
        encoding="utf-8",
    )
    (lib / "Used.tsx").write_text(
        "export default function Used(){return <div/>;}\n", encoding="utf-8"
    )
    # hand-written, nothing imports or renders it -> atticized
    (lib / "Orphan.tsx").write_text(
        "export function OrphanThing(){return <div/>;}\n"
        "export function OrphanOther(){return <span/>;}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (lib / "Orphan.tsx").exists(), "unreferenced hand-written module must leave src/"
    assert (impl / "attic" / "lib" / "Orphan.tsx").exists(), "orphan must be preserved in attic/"
    assert (lib / "Used.tsx").exists(), "imported module must stay in src/"
    assert (impl / "src" / "main.tsx").exists(), "entry files are never touched"


def test_html_id_attribute_emitted(tmp_path: Path) -> None:
    """Section anchors: the ref names sections by HTML id (#problem,
    #solution-solvable, ...) and the canonical section-compare locates impl
    sections by that id — the transpiler's attr_map never emitted `id`, so the
    clone had no section anchors and 11/14 sections scored MISSING impl."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            # id present in the capture -> emitted directly
            {"tag": "section", "class": "prob", "id": "problem", "children": [
                {"tag": "h2", "text": "The problem"},
            ]},
            # pre-id capture: subtree root has NO id, but section-map names the
            # section by id -> the id must be stamped onto the section root
            {"tag": "section", "class": "pyr", "children": [
                {"tag": "h2", "text": "The pyramid"},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [
            {"index": 0, "tag": "section", "cls": "prob", "id": "problem"},
            {"index": 1, "tag": "section", "cls": "pyr", "id": "pyramid"},
        ]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert 'id="problem"' in blob, f"HTML id must be emitted for section anchors; got:\n{blob}"
    assert 'id="pyramid"' in blob, (
        f"section-map id must be stamped onto a pre-id capture's section root; got:\n{blob}"
    )


def test_centering_translate_matrix_is_preserved(tmp_path: Path) -> None:
    """Animation-state pinning (loop-129 post-implement 10x fail): a static
    translate(-50%,-50%) centering transform is resolved by getComputedStyle to
    px matrix form (matrix(1,0,0,1,-641,-405) on a 1282x810 hero glow), so the
    '%' guard in the Fix 21 scroll-state heuristic never fires and the centering
    transform is stripped as a parallax state — displacing the element by half
    its own size (+641/+405) and bleeding it into the sections below. A
    translate that pulls the element back by exactly half its captured
    width/height is centering, not scroll state — it must be preserved. A large
    translate that does NOT match half the element size stays stripped."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                # centering: tx == -width/2, ty == -height/2 -> PRESERVE
                {"tag": "img", "class": "glow", "src": "https://cdn-domain-1.example/images/h/glow.webp",
                 "styles": {"position": "absolute", "width": "1282px", "height": "810px",
                            "top": "347px", "left": "576px",
                            "transform": "matrix(1, 0, 0, 1, -641, -405)"}},
                # horizontal-only centering: tx == -width/2, ty == 0 -> PRESERVE
                {"tag": "div", "class": "hcenter", "text": "Centered",
                 "styles": {"position": "absolute", "width": "300px", "height": "50px",
                            "left": "50%", "transform": "matrix(1, 0, 0, 1, -150, 0)"}},
                # marker-less scroll state: big translate NOT matching half size -> STRIP
                {"tag": "div", "class": "reveal", "text": "Reveal",
                 "styles": {"position": "absolute", "width": "400px", "height": "200px",
                            "transform": "matrix(1, 0, 0, 1, 0, 600)"}},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    import re as _re
    glow = _re.search(r'className="glow"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert glow and "matrix(1, 0, 0, 1, -641, -405)" in glow.group(1), (
        f"centering translate must be preserved on the glow; got:\n{glow.group(1) if glow else blob}"
    )
    hc = _re.search(r'className="hcenter"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert hc and "matrix(1, 0, 0, 1, -150, 0)" in hc.group(1), (
        f"horizontal centering must be preserved; got:\n{hc.group(1) if hc else blob}"
    )
    rv = _re.search(r'className="reveal"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert rv and "matrix" not in rv.group(1), (
        f"non-centering big translate must still be stripped (scroll state); got:\n{rv.group(1) if rv else blob}"
    )


def test_lazy_image_data_src_promoted_to_src(tmp_path: Path) -> None:
    """U1: lazy-loaded <img>/<source> keep their real URL in data-src/data-srcset
    while `src` stays empty or a tiny placeholder (the IntersectionObserver never
    fires in the static capture). Emitting data-src verbatim leaves the image
    blank (the browser ignores it) — a dominant cause of clones with zero images.
    Promote the lazy URL onto src/srcSet (rewritten to the local asset path) so
    it renders; never override a real eager src."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    blank_gif = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "gallery", "children": [
                # lazy: empty src, real url in data-src
                {"tag": "img", "src": "", "data-src": "https://cdn-domain-1.example/images/g/lazy1.webp", "alt": "Lazy one"},
                # lazy: inline placeholder src, real url in data-src alias
                {"tag": "img", "src": blank_gif, "data-original": "https://cdn-domain-1.example/images/g/lazy2.png", "alt": "Lazy two"},
                # eager: real src must be preserved, data-src ignored
                {"tag": "img", "src": "https://cdn-domain-1.example/images/g/eager.jpg", "data-src": "https://cdn-domain-1.example/images/g/should-not-win.jpg", "alt": "Eager"},
                # lazy <picture><source data-srcset>
                {"tag": "picture", "children": [
                    {"tag": "source", "data-srcset": "https://cdn-domain-1.example/images/g/lazy3.avif 1x", "type": "image/avif"},
                    {"tag": "img", "src": "", "data-src": "https://cdn-domain-1.example/images/g/lazy3.webp"},
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "gallery"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    # lazy URLs promoted to a real local src (rewrite_asset_url maps to /images/...)
    assert 'src="/images/g/lazy1.webp"' in blob, f"data-src must be promoted to src; got:\n{blob}"
    assert 'src="/images/g/lazy2.png"' in blob, "data-original alias must be promoted"
    # eager src preserved, lazy alias must NOT override it
    assert 'src="/images/g/eager.jpg"' in blob, "real eager src must be preserved"
    assert "should-not-win" not in blob, "lazy alias must not override a real eager src"
    # <source data-srcset> promoted to srcSet
    assert 'srcSet="/images/g/lazy3.avif 1x"' in blob, "data-srcset must be promoted to srcSet"
    # no blank/placeholder src emitted for the promoted images
    assert 'src=""' not in blob, "empty placeholder src must not be emitted for lazy images"


def test_picture_source_media_query_preserved(tmp_path: Path) -> None:
    """A3: a <picture><source> carries a `media` query that routes desktop vs
    mobile art. Dropping it makes the browser pick the first matching <source>
    (the mobile one, media-less) at every viewport, so the clone renders the
    mobile image on desktop. `media` must survive extract->scaffold verbatim."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "picture", "children": [
                    {"tag": "source", "media": "(max-width: 767px)",
                     "srcset": "https://cdn-domain-1.example/img/mo/hero-m.png"},
                    {"tag": "source", "media": "(min-width: 768px)",
                     "srcset": "https://cdn-domain-1.example/img/pc/hero.png"},
                    {"tag": "img", "src": "https://cdn-domain-1.example/img/pc/hero.png", "alt": "Hero"},
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert 'media="(max-width: 767px)"' in blob, f"mobile <source> media query must survive; got:\n{blob}"
    assert 'media="(min-width: 768px)"' in blob, "desktop <source> media query must survive"


def test_runtime_text_fills_empty_animated_elements(tmp_path: Path) -> None:
    """P7: JS-injected text (count-up stat numbers) is empty in the static
    capture. runtime-text.json supplies the final values per class; the
    transpiler injects them into EMPTY matching elements in document order.
    Non-empty elements are never overwritten."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero", "children": [
                {"tag": "div", "class": "dga_stats_bar_number__a"},          # empty -> 50%
                {"tag": "div", "class": "dga_stats_bar_number__a"},          # empty -> 75%
                {"tag": "div", "class": "dga_stats_bar_number__a", "text": "EXISTING"},  # keep
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    (ref / "runtime-text.json").write_text(
        json.dumps({"byClass": {"dga_stats_bar_number": ["50%", "75%", "90%"]}}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "50%" in blob and "75%" in blob, "empty count-up numbers must be filled from runtime-text"
    assert "EXISTING" in blob, "existing text must not be overwritten"
    # third runtime value not consumed (only 2 empty elements) — that's fine


def test_global_html_body_bg_override_emitted(tmp_path: Path) -> None:
    """R1 (full-build): imported ref CSS can set html/body background dark (a
    later `body{background-color:inherit}` inherits the dark html bg), so the
    page base showed dark in margins/overscroll even with the cream root div.
    The transpiler must emit a global html,body background override = the ref
    body background, so the page base is cream everywhere."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "styles": {"background-color": "rgb(253, 251, 238)", "color": "rgb(17, 0, 0)"},
        "children": [{"tag": "section", "class": "hero", "children": [{"tag": "h1", "text": "Hi"}]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    assert "<style" in app, "must emit a global style"
    # global html,body override to the ref body background, important to beat ref CSS
    assert "html" in app and "body" in app and "rgb(253, 251, 238)" in app
    assert "!important" in app


def test_root_and_global_clip_horizontal_overflow(tmp_path: Path) -> None:
    """R3 (full-build): JS-positioned elements (pyramid foods at left up to
    ~1656px) extend the body to ~2402px. The transpiler must guarantee the
    horizontal overflow is clipped — root div overflow-x:clip + global
    html,body overflow-x:clip — so body scrollWidth stays <= viewport (the ref
    itself uses html{overflow-x:clip})."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "styles": {"background-color": "rgb(253, 251, 238)"},
        "children": [{"tag": "section", "class": "hero", "children": [
            {"tag": "div", "class": "food", "styles": {"position": "absolute", "left": "1656px"}},
        ]}],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    # global style clips html,body horizontally
    assert "overflow-x:clip" in app or "overflowX" in app
    # root div carries overflowX clip + max-width 100vw
    assert 'overflowX: "clip"' in app


def test_anonymous_parent_siblings_not_re_emitted_as_uncovered(tmp_path: Path) -> None:
    """Fix 89 — anonymous-wrapper promotion prevents sibling duplication.

    When a named section (e.g. dga_hero) lives inside an anonymous container
    (no class, optional DOM id like 'intro' that is NOT a section-map entry)
    alongside a sibling div (e.g. hero_video), the transpiler must:
      (a) promote the anonymous parent as the rendered subtree for the section,
      (b) include the sibling in the same component file, and
      (c) NOT generate a separate _UncoveredAfter<N>.tsx for the sibling.

    Before Fix 89 the sibling was skipped by RENDERED_IDS (only the section
    node was marked), then _collect_uncovered picked it up and wrote it as
    _UncoveredAfter0.tsx — causing the hero block to appear duplicated at the
    bottom of the page when that fragment rendered after unrelated late sections.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "styles": {"background-color": "rgb(253, 251, 238)"},
        "children": [
            {
                # Anonymous parent: no class; DOM id 'intro' is NOT in section-map.
                "tag": "div", "class": "", "id": "intro",
                "children": [
                    {
                        "tag": "section", "class": "hero__ABC",
                        "children": [{"tag": "h1", "text": "Real Food Wins"}],
                    },
                    {
                        # Sibling — must be rendered WITH the section, not separately.
                        "tag": "div", "class": "hero_video__XYZ",
                        "children": [{"tag": "video", "src": "hero.mp4"}],
                    },
                ],
            },
            {"tag": "section", "class": "stats__DEF",
             "children": [{"tag": "p", "text": "42% of Americans"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "hero__ABC"},
        {"index": 1, "tag": "section", "cls": "stats__DEF"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    comp = impl / "src" / "components"
    names = {p.name for p in comp.glob("*.tsx")}
    # No separate uncovered fragment should be created for the sibling.
    uncovered_after0 = {n for n in names if n.startswith("_UncoveredAfter0")}
    assert not uncovered_after0, (
        f"Fix 89: hero_video sibling must NOT be split into a separate fragment; "
        f"got {uncovered_after0}"
    )
    # The sibling's class must appear inside the hero section component.
    blob = _impl_blob(impl)
    assert "hero_video__XYZ" in blob, (
        "Fix 89: sibling div (hero_video__XYZ) must be rendered inside the hero component"
    )
    # Both the section headline and sibling video are in the same component file.
    hero_file = next(
        (p for p in comp.glob("*.tsx") if "hero_video__XYZ" in p.read_text(encoding="utf-8")),
        None,
    )
    assert hero_file is not None, "hero_video__XYZ not found in any component"
    hero_text = hero_file.read_text(encoding="utf-8")
    assert "Real Food Wins" in hero_text, (
        "Fix 89: section headline and sibling must live in the same component file"
    )


def test_shared_id_sections_each_get_own_subtree(tmp_path: Path) -> None:
    """Fix 90 — id+cls combined match prevents shared-id collision.

    Two sections both carry id='footer' in the DOM:
      (a) section.dga_end___VNIF (id=footer) — contains government text
      (b) section.dga_eatReal__hUKXz (id=footer) — contains 'Eat Real' carousel

    Before Fix 90 the id-only first pass could (under consumed-set edge cases or
    when the two sections were processed out of order) resolve dga_eatReal to the
    same subtree already assigned to dga_end___VNIF, populating Footer2 with
    'The government's message…' instead of 'Eat Real'.  Fix 90 adds an id+cls
    combined walk that unambiguously selects each section by its unique class even
    when the ids collide.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {
                "tag": "div", "class": "page_wrapper__ABC",
                "children": [
                    {
                        "tag": "main", "class": "",
                        "children": [
                            {
                                "tag": "section",
                                "class": "dga_end___VNIF",
                                "id": "footer",
                                "children": [
                                    {"tag": "p", "text": "The government message"},
                                ],
                            },
                        ],
                    },
                    {
                        # Same id='footer', different class — must NOT steal the
                        # dga_end___VNIF subtree.
                        "tag": "section",
                        "class": "dga_eatReal__hUKXz",
                        "id": "footer",
                        "children": [
                            {"tag": "h2", "text": "Eat Real"},
                        ],
                    },
                ],
            },
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "id": "footer", "tag": "section",
         "cls": "dga_end___VNIF"},
        {"index": 1, "id": "footer", "tag": "section",
         "cls": "dga_eatReal__hUKXz"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    comp = impl / "src" / "components"
    # Locate the component whose outer class is dga_eatReal__hUKXz
    eat_real_file = next(
        (p for p in comp.glob("*.tsx")
         if "dga_eatReal__hUKXz" in p.read_text(encoding="utf-8")),
        None,
    )
    assert eat_real_file is not None, (
        "Fix 90: no component generated for dga_eatReal__hUKXz"
    )
    eat_real_text = eat_real_file.read_text(encoding="utf-8")
    assert "Eat Real" in eat_real_text, (
        "Fix 90: dga_eatReal component must contain 'Eat Real' carousel content, "
        "not the government-message text from dga_end___VNIF"
    )
    assert "The government message" not in eat_real_text, (
        "Fix 90: dga_eatReal component must NOT contain dga_end___VNIF text — "
        "shared id='footer' caused wrong subtree assignment"
    )
    # dga_end___VNIF component must contain its own government text
    end_file = next(
        (p for p in comp.glob("*.tsx")
         if "dga_end___VNIF" in p.read_text(encoding="utf-8")),
        None,
    )
    assert end_file is not None, (
        "Fix 90: no component generated for dga_end___VNIF"
    )
    end_text = end_file.read_text(encoding="utf-8")
    assert "The government message" in end_text, (
        "Fix 90: dga_end___VNIF component must contain its own government-message text"
    )


def test_class_section_entry_does_not_match_substring_class_token(tmp_path: Path) -> None:
    """A section-map class token like dga_card must not consume a DOM node whose
    class is only a longer token such as dga_card_bg."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "dga_card_bg", "children": [
                {"tag": "h2", "text": "Background Card"},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "dga_card"},
        {"index": 1, "tag": "section", "cls": "dga_card_bg"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    card_file = impl / "src" / "components" / "DgaCard.tsx"
    assert card_file.exists(), "section-map entry should still emit a DgaCard stub"
    card_text = card_file.read_text(encoding="utf-8")
    assert "Background Card" not in card_text, (
        "dga_card entry must not consume dga_card_bg via substring class matching"
    )
    assert "subtree-not-found-for-DgaCard" in card_text


def test_collapsed_zero_scale_entrance_state_reset_to_visible(tmp_path: Path) -> None:
    """A node captured mid-entrance at transform:matrix(0,0,0,0)+opacity:0 (zero
    scale = invisible, no CSS marker) must be reset to its visible rest state —
    the empty-inverted-pyramid fix (realfood food items were baked invisible).
    A real design scale(0.9) on a sibling is preserved."""
    _json = json
    _sp = subprocess
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(_json.dumps({
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [
            {"tag": "section", "class": "pyramid", "styles": {}, "children": [
                {"tag": "div", "class": "food", "styles": {
                    "transform": "matrix(0, 0, 0, 0, 0, 20.57)",
                    "opacity": "0", "position": "absolute"},
                 "children": [{"tag": "img", "class": "food-img"}]},
                {"tag": "div", "class": "badge", "styles": {
                    "transform": "scale(1.05)", "opacity": "1"},
                 "children": [{"tag": "span", "text": "x"}]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(_json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "pyramid"},
    ]}), encoding="utf-8")
    proc = _sp.run(["bash", str(SCRIPT), str(ref), str(impl)],
                   capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert "matrix(0, 0, 0, 0" not in blob, (
        "zero-scale collapse transform must be stripped (empty-pyramid fix)")
    assert 'opacity: "0"' not in blob, (
        "companion opacity:0 must be stripped so the element renders visible")
    assert "<img" in blob, "the food image itself must be preserved"
    # up-scale is legit emphasis (a sub-unity down-scale would be treated as a
    # frozen scrub initial by Fix 108 — see the dedicated frozen-scale test)
    assert "scale(1.05)" in blob, "a real (up-)scale design must NOT be stripped"


def test_frozen_subunity_scrub_scale_reset_to_rest(tmp_path: Path) -> None:
    """A pure uniform DOWN-scale baked inline (matrix(0.9)/scale(0.9), no marker)
    on an IN-FLOW element is a frozen scroll-zoom/entrance initial — reset transform
    to rest (scale 1) so the element is not stuck shrunk, and stamp it for ScrollScrub.
    Up-scale and scale+translate are preserved (conservative). (A position:absolute
    backdrop like realfood's dga_card_bg is EXCLUDED by Fix 119 — the emitted
    ScrollScrub freezes on absolute targets; see test_absolute_frozen_scrub_scale_*.)"""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [
            {"tag": "section", "class": "zoom", "styles": {}, "children": [
                {"tag": "div", "class": "cardbg", "styles": {
                    "transform": "matrix(0.9, 0, 0, 0.9, 0, 0)"}},
                {"tag": "div", "class": "emph", "styles": {
                    "transform": "scale(1.05)"}},
                {"tag": "div", "class": "shift", "styles": {
                    "transform": "matrix(0.9, 0, 0, 0.9, -37, 0)"}},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "zoom"},
    ]}), encoding="utf-8")
    # Fix 116 — the frozen-scrub-scale reset fires only when the plan declares a
    # scrollScrub scale band (SCRUB_WRAP_ATTRS truthy); supply one so this path
    # is exercised (a plan-less sub-unity scale is now preserved, see Fix 116 test).
    (ref / "generation-plan.json").write_text(json.dumps({
        "scrollScrub": {"required": True, "sites": [{
            "offset": '["start end","end start"]',
            "transforms": [{"input": "[0,0.05,0.75,0.9]",
                            "output": "[0.9,1,1,1]", "property": "scale"}],
        }]},
    }), encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert "matrix(0.9, 0, 0, 0.9, 0, 0)" not in blob, (
        "frozen sub-unity zoom scale must be reset to rest")
    assert "scale(1.05)" in blob, "up-scale emphasis must be preserved"
    assert "matrix(0.9, 0, 0, 0.9, -37, 0)" in blob, (
        "scale+translate must be preserved (conservative)")
    # Fix 110 — the reset zoom element is stamped so ScrollScrub can target it
    # deterministically (closes the "which element zooms?" agent-guess gap).
    assert 'data-scroll-scrub-target="1"' in blob, (
        "the frozen scroll-zoom scale element must be stamped as a scrub target")
    assert 'data-scroll-scrub-prop="scale"' in blob


def test_static_subunity_scale_preserved_without_scrub_context(tmp_path: Path) -> None:
    """Fix 116 (generality, adversarially verified): a frozen sub-unity scale is
    reset to rest ONLY when the plan declares a scrollScrub scale band. With NO
    scrollScrub context a scale(0.9) is a deliberate static design choice (a
    shrunk badge / overlay / thumbnail), so it must be PRESERVED and NOT stamped
    as a scrub target — otherwise the element is mis-sized and a phantom scrub
    site appears. (Before Fix 116 the strip fired unconditionally on any 0<s<1
    uniform scale, mangling static decorative elements on non-scroll sites.)"""
    import json as _json
    import subprocess as _sp
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(_json.dumps({
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [
            {"tag": "section", "class": "promos", "styles": {}, "children": [
                {"tag": "div", "class": "badge", "styles": {
                    "transform": "matrix(0.9, 0, 0, 0.9, 0, 0)"},
                 "children": [{"tag": "span", "text": "sale"}]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(_json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "promos"},
    ]}), encoding="utf-8")
    # NO generation-plan.json → no scrollScrub context → SCRUB_WRAP_ATTRS empty.
    proc = _sp.run(["bash", str(SCRIPT), str(ref), str(impl)],
                   capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert "matrix(0.9, 0, 0, 0.9, 0, 0)" in blob, (
        "a static sub-unity scale must be preserved without scrollScrub context")
    assert "data-scroll-scrub-target" not in blob, (
        "no scrub-target stamp without a declared scale band")


def test_cross_effect_no_regression_one_transpile(tmp_path: Path) -> None:
    """DECOUPLING GUARD (Fix 112): the transpiler's shared render() transform/
    opacity reset path is touched by many per-effect fixes (Fix 21/68/97/107/108/
    110). Per-effect unit tests are siloed, so a change for one effect can silently
    regress another (e.g. Fix 108 stripping scale(0.9) broke a Fix-107 test). This
    exercises ALL the reset predicates as SIBLINGS in ONE transpile and asserts
    each is handled independently — a future predicate change that cross-regresses
    another element fails HERE rather than only in a live clone."""
    import json as _json
    import subprocess as _sp
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(_json.dumps({
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [
            {"tag": "section", "class": "mix", "styles": {}, "children": [
                # #2 collapse: zero-scale entrance -> transform + opacity stripped
                {"tag": "div", "class": "food", "styles": {
                    "transform": "matrix(0, 0, 0, 0, 0, 20.5)", "opacity": "0",
                    "position": "absolute"},
                 "children": [{"tag": "img", "class": "food-img"}]},
                # #3 frozen zoom (IN-FLOW): sub-unity scale -> stripped AND stamped
                # (a position:absolute backdrop would be excluded — Fix 119)
                {"tag": "div", "class": "zoombg", "styles": {
                    "transform": "matrix(0.9, 0, 0, 0.9, 0, 0)"}},
                # rotation: rotate(90deg) -> matrix(0,1,-1,0) -> MUST be preserved
                # (Fix 112: a≈0,d≈0 but b,c=±1 is rotation, not a zero-scale collapse)
                {"tag": "div", "class": "icon", "styles": {
                    "transform": "matrix(0, 1, -1, 0, 0, 0)"},
                 "children": [{"tag": "span", "text": "r"}]},
                # up-scale emphasis -> preserved
                {"tag": "div", "class": "emph", "styles": {
                    "transform": "scale(1.05)"},
                 "children": [{"tag": "span", "text": "e"}]},
                # opacity reveal (marker) -> opacity stripped, no transform
                {"tag": "div", "class": "rev", "styles": {
                    "opacity": "0", "transition": "opacity 0.4s"},
                 "children": [{"tag": "p", "text": "reveal"}]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(_json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "mix"},
    ]}), encoding="utf-8")
    # Fix 116 — the #3 frozen-zoom strip/stamp requires scrollScrub context.
    (ref / "generation-plan.json").write_text(_json.dumps({
        "scrollScrub": {"required": True, "sites": [{
            "offset": '["start end","end start"]',
            "transforms": [{"input": "[0,0.05,0.75,0.9]",
                            "output": "[0.9,1,1,1]", "property": "scale"}],
        }]},
    }), encoding="utf-8")
    proc = _sp.run(["bash", str(SCRIPT), str(ref), str(impl)],
                   capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    # #2 collapse stripped (food visible)
    assert "matrix(0, 0, 0, 0" not in blob, "zero-scale collapse must be stripped"
    assert "<img" in blob, "food image preserved"
    # #3 zoom stripped + stamped
    assert "matrix(0.9, 0, 0, 0.9, 0, 0)" not in blob, "frozen zoom scale must be stripped"
    assert 'data-scroll-scrub-target="1"' in blob, "zoom element must be stamped"
    # rotation PRESERVED (the regression Fix 112 guards against)
    assert "matrix(0, 1, -1, 0, 0, 0)" in blob, (
        "a 90deg rotation must NOT be stripped as a zero-scale collapse")
    # up-scale preserved
    assert "scale(1.05)" in blob, "up-scale emphasis preserved"
    # opacity reveal stripped (element still rendered)
    assert "reveal" in blob, "reveal content preserved"


def test_scrub_scale_section_auto_wrapped(tmp_path: Path) -> None:
    """Fix 113: a section whose element is frozen at a scroll-zoom scale (Fix 108
    detect + Fix 110 stamp) is AUTO-WRAPPED in <ScrollScrub scale=...> at the
    entry using the real band from generation-plan.scrollScrub — so #3 reproduces
    deterministically without the agent (decouples it from claude/codex host
    behaviour). The frozen inline scale is stripped (no double-transform)."""
    import json as _json
    import re as _re
    import subprocess as _sp
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(_json.dumps({
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [
            {"tag": "section", "class": "zoombg", "styles": {
                "transform": "matrix(0.9, 0, 0, 0.9, 0, 0)"},
             "children": [{"tag": "div", "text": "bg"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(_json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "zoombg"},
    ]}), encoding="utf-8")
    (ref / "generation-plan.json").write_text(_json.dumps({
        "scrollScrub": {"required": True, "sites": [{
            "offset": '["start end","end start"]',
            "transforms": [{"input": "[0,0.05,0.75,0.9]",
                            "output": "[0.9,1,1,1]", "property": "scale"}],
        }]},
    }), encoding="utf-8")
    proc = _sp.run(["bash", str(SCRIPT), str(ref), str(impl)],
                   capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert "import ScrollScrub" in blob, "entry must import ScrollScrub"
    assert _re.search(r"<ScrollScrub scale=\{\[\[[^\]]*\],\s*\[[^\]]*\]\]\}", blob), (
        "the scrub-scale section must be auto-wrapped in <ScrollScrub scale={band}>")
    assert 'data-scroll-scrub-target="1"' in blob, "stamp preserved for the gate"
    assert "matrix(0.9, 0, 0, 0.9, 0, 0)" not in blob, "frozen inline scale stripped"
    # Fix 115 (#4): a PURE frozen-scrub-scale section (no other reveal signal) is
    # wrapped only in <ScrollScrub>. Counting it as a REVEAL would ALSO wrap it in
    # <ScrollReveal> (double-wrap — the reveal's transform fights the scrub scale).
    assert "ScrollReveal" not in blob, (
        "pure scrub-scale section must not be double-wrapped in ScrollReveal")


def test_absolute_frozen_scrub_scale_not_auto_wrapped(tmp_path: Path) -> None:
    """Fix 119: a position:absolute element frozen at a sub-unity scrub scale
    (realfood's dga_card_bg zoom backdrop) must NOT be stripped, stamped, nor
    auto-wrapped in <ScrollScrub>. The emitted ScrollScrub drives scale from framer
    useScroll, which reads scrollYProgress≈0 from async measurement on an absolute /
    jump-scrolled target and FREEZES it at the band's START scale — measured to regress
    realfood card_bg 2.9x (322k->935k AE/Mpx) and the pyramid rendered on top of it 2.2x.
    Absolute backdrops need the bespoke rAF path (cf. DgaCardBg), never this component;
    the baked scale is KEPT so the element is unchanged from capture (no worse than
    baseline) rather than freeze-wrapped. Mirrors the fixed/sticky exclusion."""
    import json as _json
    import subprocess as _sp
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(_json.dumps({
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [
            {"tag": "section", "class": "zoom", "styles": {}, "children": [
                {"tag": "div", "class": "cardbg", "styles": {
                    "transform": "matrix(0.9, 0, 0, 0.9, 0, 0)", "position": "absolute"}},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(_json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "zoom"},
    ]}), encoding="utf-8")
    (ref / "generation-plan.json").write_text(_json.dumps({
        "scrollScrub": {"required": True, "sites": [{
            "offset": '["start end","end start"]',
            "transforms": [{"input": "[0,0.05,0.75,0.9]",
                            "output": "[0.9,1,1,1]", "property": "scale"}],
        }]},
    }), encoding="utf-8")
    proc = _sp.run(["bash", str(SCRIPT), str(ref), str(impl)],
                   capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    # baked scale KEPT (not stripped) — absolute backdrop left as captured
    assert "matrix(0.9, 0, 0, 0.9, 0, 0)" in blob, (
        "an absolute frozen scrub-scale must NOT be stripped (Fix 119)")
    # NOT stamped, NOT auto-wrapped, NOT imported into the entry
    assert "data-scroll-scrub-target" not in blob, (
        "an absolute scrub-scale element must not be stamped (Fix 119)")
    assert "<ScrollScrub" not in blob, (
        "an absolute scrub-scale section must not be auto-wrapped (Fix 119)")
    assert "import ScrollScrub" not in blob, (
        "no ScrollScrub import when the only scrub candidate is absolute")


def test_svg_line_draw_in_stamped_when_hidden(tmp_path: Path) -> None:
    """Fix 114 (#2 pyramid outline): an SVG <line>/<path> captured with
    stroke-dashoffset ≈ stroke-dasharray is the fully-HIDDEN draw-in initial
    frame — stamp it data-stroke-draw (even if transition-spec missed it) so the
    driver draws it in. A static dashed line (dashoffset 0) is left alone."""
    import json as _json
    import subprocess as _sp
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(_json.dumps({
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [
            {"tag": "section", "class": "pyr", "styles": {}, "children": [
                {"tag": "svg", "class": "tri", "styles": {}, "children": [
                    # hidden draw-in line -> stamped
                    {"tag": "line", "stroke": "#110000",
                     "stroke-dasharray": "50", "stroke-dashoffset": "50"},
                    # static dashed border -> NOT stamped (offset 0 = drawn)
                    {"tag": "line", "stroke": "#110000",
                     "stroke-dasharray": "4", "stroke-dashoffset": "0"},
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(_json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "pyr"},
    ]}), encoding="utf-8")
    proc = _sp.run(["bash", str(SCRIPT), str(ref), str(impl)],
                   capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _src_blob(impl)
    assert blob.count('data-stroke-draw="1"') == 1, (
        "exactly the hidden (offset==dasharray) line is stamped for draw-in")
    assert "ScrollStateDriver" in blob, "the driver that animates the draw-in must mount"


def test_next_page_mounts_state_driver_with_use_client(tmp_path: Path) -> None:
    """Fix 115 (#5): the emitted ScrollStateDriver animates stamped fade/draw-in
    elements to their active state. On the Next App Router stack the page must
    MOUNT it — mounting was Vite-entry-only before, so every stamped element was
    inert (stuck at its captured inactive frame) on Next. And the driver itself
    must carry 'use client' because it runs useEffect; without it a React Server
    Component build throws (the Fix 114/74 draw-in/fade is then dead on Next)."""
    import json as _json
    import subprocess as _sp
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    # Force the Next App Router stack: "next" in deps → _detect_stack() == "next".
    (impl / "package.json").write_text(_json.dumps(
        {"dependencies": {"next": "15.0.0", "react": "19.0.0"}}), encoding="utf-8")
    # The real pipeline always produces generation-plan.json before scaffold, so
    # emit-scroll-helpers runs. transition-spec.json is deliberately absent: the
    # draw-in stamp here comes from Fix 114's marker-less heuristic, so the driver
    # must still be emitted (Fix 115 coherence guard) keyed on the stamp itself.
    (ref / "generation-plan.json").write_text("{}", encoding="utf-8")
    (ref / "structure.json").write_text(_json.dumps({
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [
            {"tag": "section", "class": "pyr", "styles": {}, "children": [
                {"tag": "svg", "class": "tri", "styles": {}, "children": [
                    # hidden draw-in line -> stamped -> driver must mount + animate
                    {"tag": "line", "stroke": "#110000",
                     "stroke-dasharray": "50", "stroke-dashoffset": "50"},
                ]},
            ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(_json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "pyr"},
    ]}), encoding="utf-8")
    proc = _sp.run(["bash", str(SCRIPT), str(ref), str(impl)],
                   capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "stack=next" in proc.stdout, f"expected next stack, got: {proc.stdout}"
    page = next(impl.rglob("page.tsx"))
    page_src = page.read_text(encoding="utf-8")
    assert "import ScrollStateDriver" in page_src and "<ScrollStateDriver />" in page_src, (
        "Next page must mount the driver so stamped draw-in/fade elements animate")
    driver = next(impl.rglob("ScrollStateDriver.tsx"))
    assert driver.read_text(encoding="utf-8").lstrip().startswith("'use client'"), (
        "ScrollStateDriver runs useEffect → must be a client component on Next")


def test_absolute_backdrop_wrapper_regroups_contiguous_sections(tmp_path: Path) -> None:
    """KEYSTONE: a position:relative wrapper that is the CONTAINING BLOCK for a
    position:absolute full-bleed backdrop child (e.g. realfood's erf_wrapper >
    card_bg) is dropped by flat section emission — the backdrop then attaches to
    the static App root and collapses to zero paint (section-compare reads BLACK).
    The transpiler must re-emit that wrapper around the FULL contiguous run of
    section-map sections it spans (the content sections provide the flow height),
    carrying position (+ bg + class) but NEVER a min-height (the prior Fix 117
    baked a 4580px min-height around only the backdrop section, inflating flow and
    cascading lower sections to BLACK)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body", "class": "antialiased",
        "styles": {"background-color": "rgb(253, 251, 238)"},
        "children": [
            # erf_wrapper: relative containing block spanning erf + card_bg.
            {"tag": "div", "class": "erf_wrapper",
             "styles": {"position": "relative", "height": "4580px"},
             "children": [
                 # full-bleed absolute backdrop (no offsets) -> needs the wrapper.
                 {"tag": "div", "class": "card_bg",
                  "styles": {"position": "absolute", "height": "4580px",
                             "background-color": "rgb(253, 251, 238)"}},
                 # content section that provides the flow height.
                 {"tag": "section", "class": "erf", "id": "pyramid",
                  "styles": {"position": "relative"},
                  "children": [{"tag": "h2", "text": "Eat real food"}]},
             ]},
            # sibling section OUTSIDE the wrapper -> must NOT be re-grouped.
            {"tag": "section", "class": "winning", "id": "winning",
             "children": [{"tag": "h2", "text": "Real food wins"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "erf", "id": "pyramid"},
        {"index": 1, "tag": "div", "cls": "card_bg"},
        {"index": 2, "tag": "section", "cls": "winning", "id": "winning"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    import re as _re
    # The wrapper div is re-emitted with position:relative and the ref class.
    wrap = _re.search(
        r'<div className="erf_wrapper" style=\{\{ position: "relative" \}\}>'
        r'(?P<inner>.*?)</div>',
        app, _re.S,
    )
    assert wrap, f"erf_wrapper must be re-emitted as a relative grouping div; got:\n{app}"
    inner = wrap.group("inner")
    # NO min-height / height may be baked on the wrapper (prevents the Fix 117
    # flow-inflation cascade) — the content sections size the flow.
    assert "minHeight" not in wrap.group(0) and "height" not in wrap.group(0), (
        f"wrapper must not carry a min-height/height; got:\n{wrap.group(0)}"
    )
    # BOTH section components in the run (pyramid + card_bg) nest INSIDE; the
    # out-of-run sibling (winning) does NOT.
    assert ("Pyramid" in inner) or ("pyramid" in inner.lower()), (
        f"the erf/pyramid section must nest inside the wrapper; got:\n{inner}"
    )
    assert "CardBg" in inner or "Card" in inner or "card" in inner.lower(), (
        f"the card_bg backdrop section must nest inside the wrapper; got:\n{inner}"
    )
    assert "Winning" not in inner, (
        f"the out-of-run sibling must stay OUTSIDE the wrapper; got:\n{inner}"
    )
    assert "Winning" in app, "the out-of-run sibling must still render in the page"


def test_no_wrapper_regroup_without_absolute_backdrop(tmp_path: Path) -> None:
    """Fail-safe scope: a position:relative wrapper spanning >=2 sections that has
    NO absolute full-bleed backdrop child is NOT re-emitted as a grouping div —
    flat emission is preserved. Only the containing-block-for-backdrop case is
    handled (a bg band is already painted per-section by the Fix 88/97 logic), so
    we never introduce a second, competing wrapper mechanism."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body", "class": "antialiased",
        "children": [
            {"tag": "div", "class": "plainwrap",
             "styles": {"position": "relative"},
             "children": [
                 {"tag": "section", "class": "a", "id": "a",
                  "children": [{"tag": "h2", "text": "A"}]},
                 {"tag": "section", "class": "b", "id": "b",
                  "children": [{"tag": "h2", "text": "B"}]},
             ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "a", "id": "a"},
        {"index": 1, "tag": "section", "cls": "b", "id": "b"},
    ]}), encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    assert 'className="plainwrap"' not in app, (
        f"a backdrop-less relative wrapper must NOT be re-emitted (fail-safe); got:\n{app}"
    )


def _css_scope_ref(tmp_path: Path, css: str, forensic: bool = True) -> tuple[Path, Path]:
    """Ref whose wrapper has NO absolute backdrop child, so the only thing that can
    re-emit it is the ref-CSS scoping criterion.

    Shape is taken from realfood-v2's own structure.json rather than invented:
    `section.lineInTheSand` holds `div.solvable_problem` and `div.container` as
    DIRECT CHILDREN, and BOTH are section-map sections. `.container` being both a
    direct child and a section component is what makes `.lineInTheSand>.container`
    a live selector in the emitted tree. A sibling section outside the wrapper
    pins the contiguous-run boundary.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [
            {"tag": "section", "class": "lineInTheSand",
             "styles": {"position": "relative"},
             "children": [
                 {"tag": "div", "class": "solvable_problem", "styles": {},
                  "children": [{"tag": "h2", "text": "A solvable problem"}]},
                 {"tag": "div", "class": "container", "styles": {},
                  "children": [{"tag": "p", "text": "Eat real food"}]},
             ]},
            {"tag": "section", "class": "winning", "id": "winning", "styles": {},
             "children": [{"tag": "h2", "text": "Real food wins"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "div", "cls": "solvable_problem"},
        {"index": 1, "tag": "div", "cls": "container"},
        {"index": 2, "tag": "section", "cls": "winning", "id": "winning"},
    ]}), encoding="utf-8")
    (ref / "css").mkdir()
    (ref / "css" / "main.css").write_text(css, encoding="utf-8")
    if forensic:
        (ref / "generation-plan.json").write_text(json.dumps({
            "forensicPreservation": {"required": True,
                                     "strategy": "ref-derived-jsx-with-local-css"},
        }), encoding="utf-8")
    return ref, impl


CHILD_SCOPED_CSS = ".lineInTheSand>.container{padding-bottom:182px;min-height:1011px}"


def test_forensic_wrapper_regroups_when_ref_css_child_scopes_a_direct_child(
    tmp_path: Path,
) -> None:
    """A wrapper with NO absolute backdrop child must still be re-emitted when
    forensic className-only mode is active AND the ref CSS scopes one of its
    DIRECT CHILDREN through a CHILD combinator (`.lineInTheSand>.container`).

    In forensic mode the baked box model is stripped and layout is delegated to
    the mirrored ref CSS, so dropping the wrapper silently deletes every such
    scoped rule: realfood's `.container` rendered 38px against a ref 1011px,
    costing ~975px of document height and displacing every section below it.
    The backdrop criterion cannot see this case — the wrapper's job here is to
    satisfy a selector, not to be a containing block."""
    ref, impl = _css_scope_ref(tmp_path, CHILD_SCOPED_CSS)
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    # CLASSNAME-ONLY payload. This branch exists only in forensic mode, whose
    # contract is that mirrored ref CSS owns layout — the ref's own
    # `.lineInTheSand{position:relative}` supplies the position. Baking an inline
    # position here would INVENT a containing block whenever the captured
    # ancestor was static, re-anchoring every absolute descendant in the region;
    # an inline background would likewise outrank the mirrored CSS.
    wrap = re.search(
        r'<section className="lineInTheSand">(?P<inner>.*?)</section>',
        app, re.S,
    )
    assert wrap, (
        "a CSS-child-scoped wrapper must be re-emitted (className-only) so "
        f"`.lineInTheSand>.container` keeps matching; got:\n{app}"
    )
    assert "style=" not in wrap.group(0).split(">", 1)[0], (
        f"the CSS-scope branch must carry no inline style; got:\n{wrap.group(0)[:200]}"
    )
    inner = wrap.group("inner")
    assert "SolvableProblem" in inner, (
        f"the full contiguous run must nest inside the wrapper; got:\n{inner}"
    )
    assert "Winning" not in inner, (
        f"the out-of-run sibling must stay OUTSIDE the wrapper; got:\n{inner}"
    )
    # ADJACENCY is the whole point: re-emitting the wrapper only revives
    # `.lineInTheSand > .container` if the container stays a DIRECT child. An
    # interposed element (a Fix 88 band div, a ScrollReveal, a sticky wrapper)
    # would silently zero the fix while the transpiler still reports success.
    assert re.search(r"<Container\s*/>", inner), (
        f"the scoped container must render inside the wrapper; got:\n{inner}"
    )
    between = inner.split("<Container")[0]
    assert "<div" not in between and "<ScrollReveal" not in between, (
        "nothing may be interposed between the wrapper and the scoped container, "
        f"or the `>` selector stops matching; got:\n{inner}"
    )
    container = (impl / "src" / "components" / "Container.tsx").read_text(encoding="utf-8")
    assert 'className="container"' in container, (
        f"the container component must keep the class the rule targets; got:\n{container}"
    )


def _band_owner_ref(tmp_path: Path, paint_class: bool = True) -> tuple[Path, Path]:
    """Ref where the region's background is owned by an ANCESTOR (`div.dark`),
    which is what makes the Fix 88 band fire on every section beneath it.

    This is realfood-v2's actual shape: `.dark{background-color:var(--off-black)}`
    paints the band, `.lineInTheSand` paints nothing, and the sections inherit the
    backdrop only because the band div reproduces it per-section.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [
            {"tag": "div", "class": "dark",
             "styles": {"background-color": "rgb(17, 0, 0)"},
             "children": [
                 {"tag": "section", "class": "lineInTheSand",
                  "styles": {"position": "relative"},
                  "children": [
                      {"tag": "div", "class": "solvable_problem", "styles": {},
                       "children": [{"tag": "h2", "text": "A solvable problem"}]},
                      {"tag": "div", "class": "container", "styles": {},
                       "children": [{"tag": "p", "text": "Eat real food"}]},
                  ]},
                 {"tag": "section", "class": "other", "styles": {},
                  "children": [{"tag": "h2", "text": "Other"}]},
             ]},
            {"tag": "section", "class": "winning", "id": "winning", "styles": {},
             "children": [{"tag": "h2", "text": "Real food wins"}]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "div", "cls": "solvable_problem"},
        {"index": 1, "tag": "div", "cls": "container"},
        {"index": 2, "tag": "section", "cls": "other"},
        {"index": 3, "tag": "section", "cls": "winning", "id": "winning"},
    ]}), encoding="utf-8")
    (ref / "css").mkdir()
    dark_rule = ".dark{background-color:var(--off-black)}" if paint_class else ""
    (ref / "css" / "main.css").write_text(
        ":root{--off-black:#100}" + dark_rule + CHILD_SCOPED_CSS, encoding="utf-8")
    (ref / "generation-plan.json").write_text(json.dumps({
        "forensicPreservation": {"required": True,
                                 "strategy": "ref-derived-jsx-with-local-css"},
    }), encoding="utf-8")
    return ref, impl


def test_band_owning_ancestor_is_reemitted_instead_of_per_section_bands(
    tmp_path: Path,
) -> None:
    """LAYER 2: re-emitting the scoped wrapper is inert on its own, because the
    Fix 88 band div is interposed between it and the scoped child.

    On realfood-v2 every child of `.lineInTheSand` is rooted in an anonymous
    full-bleed band div reproducing `.dark`'s background, so `.lineInTheSand >
    .container` still matches nothing and none of the ~975px is recovered.

    The fix is to re-emit the ancestor that actually OWNS the background, so the
    mirrored ref CSS paints it natively (`.dark{background-color:var(--off-black)}`)
    and the per-section bands beneath it become redundant. That restores the
    parent/child adjacency the `>` selector needs while preserving the backdrop."""
    ref, impl = _band_owner_ref(tmp_path)
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    # The bg owner is re-emitted, className-only: the mirrored CSS paints it.
    owner = re.search(r'<div className="dark">(?P<inner>.*?)</div>', app, re.S)
    assert owner, (
        f"the background-owning ancestor must be re-emitted so its rule paints; got:\n{app}"
    )
    assert 'className="lineInTheSand"' in owner.group("inner"), (
        f"the scoped wrapper must nest inside the bg owner; got:\n{owner.group('inner')}"
    )
    assert "Winning" not in owner.group("inner"), (
        f"a section outside the bg owner must stay outside it; got:\n{owner.group('inner')}"
    )
    # The per-section band is now redundant and must NOT interpose.
    container = (impl / "src" / "components" / "Container.tsx").read_text(encoding="utf-8")
    body = container.split("return (", 1)[1]
    assert "backgroundColor" not in body, (
        "the per-section band must be dropped once the bg owner is re-emitted, "
        f"or it breaks `.lineInTheSand>.container`; got:\n{container}"
    )
    assert re.search(r'return \(\s*<div className="container"', container), (
        f"`.container` must be the component root for the `>` rule to match; got:\n{container}"
    )


def test_descendant_scoped_wrapper_is_not_regrouped(tmp_path: Path) -> None:
    """SPECIFICITY GUARD: a DESCENDANT-combinator scope (`.lineInTheSand .container`)
    must NOT re-emit the wrapper, even though it names the same two classes.

    A descendant scope is the idiom of a site-wide theme class rather than a
    structural wrapper — on navercorp-esg-sustainability `.navercorp` scopes 230
    such selectors and is already applied to the App root, so admitting descendant
    scopes would wrap every section in a spurious extra DOM level. Requiring an
    explicit `>` is what keeps the criterion to genuine structural wrappers.

    This test fails if `_css_child_scopes_direct_child` is loosened to accept a
    whitespace combinator."""
    ref, impl = _css_scope_ref(
        tmp_path, ".lineInTheSand .container{padding-bottom:182px}")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    assert 'className="lineInTheSand"' not in app, (
        f"a descendant-only scope must NOT re-emit the wrapper (fail-safe); got:\n{app}"
    )


def test_css_scoped_wrapper_is_not_regrouped_in_baked_mode(tmp_path: Path) -> None:
    """MODE GUARD: the CSS-scope branch is forensic-only.

    Without `forensicPreservation.strategy = ref-derived-jsx-with-local-css` the
    box model is baked inline, so the scoped rule is not load-bearing — realfood's
    container keeps its padding either way and the wrapper drop costs ~0px. Baked
    sites must stay byte-for-byte on the pre-existing flat path, which is also
    what keeps every pre-existing (baked) wrapper test honest.

    This test fails if the `_forensic_classname_only()` gate is dropped."""
    ref, impl = _css_scope_ref(tmp_path, CHILD_SCOPED_CSS, forensic=False)
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = _app_tsx(impl)
    assert 'className="lineInTheSand"' not in app, (
        f"baked mode must not re-emit the CSS-scoped wrapper; got:\n{app}"
    )


def _uncovered_ref(tmp_path: Path, bg: str, extra: dict | None = None) -> tuple[Path, Path]:
    """Ref whose wrapper holds an EMPTY out-of-flow backdrop plus one section.

    The backdrop is the shape realfood's `card_bg` has: a childless, textless
    div whose entire job is to paint a 4580px cream band behind the region. No
    section-map entry claims it, so it reaches the uncovered-fragment pass.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    backdrop_styles = {"position": "absolute", "height": "4580.34px"}
    if bg:
        backdrop_styles["background-color"] = bg
    if extra:
        backdrop_styles.update(extra)
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "class": "antialiased",
        "styles": {"background-color": "rgb(255, 255, 255)"},
        "children": [
            {"tag": "div", "class": "erf-wrapper", "styles": {"position": "relative"},
             "children": [
                 {"tag": "div", "class": "card-bg", "styles": backdrop_styles},
                 {"tag": "section", "class": "hero", "styles": {},
                  "children": [{"tag": "h1", "text": "Eat Real Food"}]},
             ]},
        ],
    }), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "hero"},
    ]}), encoding="utf-8")
    return ref, impl


def _transpile(ref: Path, impl: Path) -> str:
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _src_blob(impl)


def test_uncovered_pass_keeps_empty_painted_backdrop(tmp_path: Path) -> None:
    """An unclaimed empty div that PAINTS is visible ref content.

    _collect_uncovered's has_content test only looked for text or media, so a
    childless backdrop div was dropped silently — realfood's 4580px cream
    card_bg is captured in structure.json and appears nowhere in the emitted
    impl. extract-dom.sh already learned this at capture time (its past-cap
    keep test includes a paints-something branch for exactly these stat-grid
    bars / card-parallax layers / footer column backers); the emission side
    must not undo it.
    """
    ref, impl = _uncovered_ref(tmp_path, "rgb(253, 251, 238)")
    blob = _transpile(ref, impl)
    assert "card-bg" in blob, f"painted backdrop must survive emission; got:\n{blob[:1500]}"
    assert "4580.34px" in blob, "backdrop height must be preserved"


def test_uncovered_pass_keeps_backdrop_painted_by_border_only(tmp_path: Path) -> None:
    ref, impl = _uncovered_ref(tmp_path, "", {"border": "2px solid rgb(0, 0, 0)"})
    blob = _transpile(ref, impl)
    assert "card-bg" in blob, "a border-only painted backdrop is still visible content"


def test_uncovered_pass_still_drops_transparent_empty_wrapper(tmp_path: Path) -> None:
    """Negative control: rescuing painted nodes must not rescue invisible ones."""
    ref, impl = _uncovered_ref(tmp_path, "rgba(0, 0, 0, 0)")
    blob = _transpile(ref, impl)
    assert "card-bg" not in blob, (
        f"a fully transparent empty wrapper paints nothing and must stay dropped; got:\n{blob[:1200]}"
    )


def test_uncovered_out_of_flow_node_keeps_its_containing_block(tmp_path: Path) -> None:
    """A rescued out-of-flow node must carry its positioned ancestor.

    Uncovered fragments are emitted at App top level, so every ancestor is lost.
    The impl mirrors the ref CSS byte-for-byte, and realfood's rule is
    `.card_bg{position:absolute;z-index:1;top:0;bottom:0}` — with the relative
    erf_wrapper dropped those offsets resolve against the initial containing
    block. Measured in a browser: without the wrapper the 4580px band renders at
    top=0 over the 700px hero; with it, at its flow position (top=1900). So
    rescuing the node without its containing block trades "missing" for
    "covering" — both halves are required.
    """
    ref, impl = _uncovered_ref(tmp_path, "rgb(253, 251, 238)")
    blob = _transpile(ref, impl)
    assert "erf-wrapper" in blob, (
        f"the absolute backdrop's relative ancestor must be re-emitted; got:\n{blob[:1500]}"
    )
    # The wrapper must supply positioning only — painting stays with Fix 88.
    m = re.search(r'className="erf-wrapper" style=\{\{([^}]*)\}\}', blob)
    assert m, f"expected an emitted erf-wrapper with a style object; got:\n{blob[:1500]}"
    assert "position" in m.group(1), m.group(1)
    assert "background" not in m.group(1).lower(), (
        f"restored wrapper must not paint (no competing band mechanism): {m.group(1)}"
    )


def test_uncovered_in_flow_painted_node_is_not_wrapped(tmp_path: Path) -> None:
    """Only out-of-flow nodes need the containing block restored."""
    ref, impl = _uncovered_ref(tmp_path, "rgb(253, 251, 238)",
                               {"position": "static"})
    blob = _transpile(ref, impl)
    assert "card-bg" in blob, "in-flow painted node is still rescued"
    assert "erf-wrapper" not in blob, (
        f"an in-flow node renders at its flow position already; got:\n{blob[:1200]}"
    )
