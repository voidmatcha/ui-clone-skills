"""G-family: default-on generator-side un-bake of ref-CSS-covered px bakes.

The transpiler emits captured computed px verbatim; inline px beats the
mirrored ref CSS, freezing clones at the capture width (campaign pain #2 —
nvti-3/ebpb-3 removed ~246 such bakes BY HAND, verified per-width). This
implements that proven recipe at the generator: drop a baked px only where
a BASE (non-@media) rule of the mirrored ref CSS declares that property for
one of the node's class tokens — the impl mirrors the ref CSS byte-for-byte,
so the browser re-resolves the SAME cascade the ref used.

Guards (fable design review, approve-with-conditions):
- @media-only declarations keep the bake (clearing computes auto at the
  default width; media-condition evaluation is the documented v2 path).
- node.inlineProps (new capture field): props the REF element declared in
  its own inline style attr are never un-baked — the ref's inline beat its
  own CSS, so the CSS value is NOT what rendered (framer-driven widths).
- Runs BEFORE every synthesis pass (Fix 20/21 height conversion, P5 reflow,
  Fix 127/128) so it can only ever drop CAPTURED values, never synthesized
  ones (condition 1).
- px-only values, 7-prop campaign-proven set, kill-switch env, aggregate
  stderr summary (never-silent).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _blob(impl: Path) -> str:
    src = impl / "src"
    return (
        "".join(p.read_text(encoding="utf-8") for p in src.rglob("*.tsx")) if src.is_dir() else ""
    )


def _run(ref: Path, impl: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess[str]:
    (impl / "src").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def _ref(tmp_path: Path, node: dict, css: str | None) -> Path:
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "body",
        "styles": {},
        "children": [
            {"tag": "section", "class": "sec", "styles": {}, "children": [node]},
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "sec"}]}), encoding="utf-8"
    )
    if css is not None:
        (ref / "css").mkdir()
        (ref / "css" / "main.css").write_text(css, encoding="utf-8")
    return ref


def _emit(tmp_path: Path, node: dict, css: str | None, env: dict | None = None) -> tuple[str, str]:
    ref = _ref(tmp_path, node, css)
    impl = tmp_path / "impl"
    proc = _run(ref, impl, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _blob(impl), proc.stderr


_HERO = {
    "tag": "div",
    "class": "hero-box",
    "children": [],
    "styles": {"width": "1280px", "background-color": "rgb(1,2,3)"},
}


def test_base_rule_coverage_drops_the_bake(tmp_path: Path) -> None:
    blob, err = _emit(tmp_path, dict(_HERO), ".hero-box { width: 100%; }")
    # Neither the raw bake nor the P5-converted shape may survive: the drop
    # happens BEFORE P5, so no width/max-width pair is synthesized from it.
    assert '"1280px"' not in blob, blob
    assert "un-baked" in err, err


def test_media_only_coverage_keeps_the_bake(tmp_path: Path) -> None:
    blob, _ = _emit(
        tmp_path, dict(_HERO), "@media (max-width: 768px) { .hero-box { width: 50%; } }"
    )
    # Kept: P5 then converts the large captured width to max-width+100%
    # (pre-existing behavior — the point is it was NOT silently dropped).
    assert 'maxWidth: "1280px"' in blob, blob


def test_inline_props_guard_keeps_the_bake(tmp_path: Path) -> None:
    node = dict(_HERO)
    node["inlineProps"] = ["width"]
    blob, _ = _emit(tmp_path, node, ".hero-box { width: 100%; }")
    assert 'maxWidth: "1280px"' in blob, blob


def test_transition_spec_class_reveal_unbakes_active_subject_only(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    node = {
        "tag": "div",
        "class": "effect-data active",
        "children": [
            {
                "tag": "span",
                "class": "effect-flip",
                "styles": {
                    "opacity": "1",
                    "transform": "matrix(1, 0, 0, 1, 0, 0)",
                    "transition-property": "opacity, transform",
                    "transition-duration": "0.6s, 0.6s",
                },
            },
            {
                "tag": "span",
                "class": "badge active",
                "styles": {"opacity": "1"},
            },
        ],
        "styles": {
            "opacity": "1",
            "transform": "matrix(1, 0, 0, 1, 0, 0)",
            "transition-property": "opacity, transform",
            "transition-duration": "0.6s, 0.6s",
        },
    }
    structure = {
        "tag": "body",
        "styles": {},
        "children": [{"tag": "section", "class": "sec", "styles": {}, "children": [node]}],
    }
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "sec"}]}),
        encoding="utf-8",
    )
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "selector": ".effect-data",
                        "trigger": "IntersectionObserver viewport reveal",
                        "animation": {
                            "type": "class-toggle",
                            "property": "className",
                            "from": {"className": "effect-data"},
                            "to": {"className": "effect-data active"},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "css").mkdir()
    (ref / "css" / "main.css").write_text(
        ".effect-data:not(.active),.effect-data:not(.active) .effect-flip{"
        "opacity:0;transform:translateY(24px);transition-property:opacity,transform;"
        "transition-duration:.6s,.6s}"
        ".effect-data.active,.effect-data.active .effect-flip{"
        "opacity:1;transform:translateY(0);transition-property:opacity,transform;"
        "transition-duration:.6s,.6s}",
        encoding="utf-8",
    )
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    blob = _blob(impl)
    subject = next(line for line in blob.splitlines() if 'className="effect-data' in line)
    descendant = next(line for line in blob.splitlines() if 'className="effect-flip' in line)
    static_active = next(line for line in blob.splitlines() if 'className="badge active"' in line)
    assert 'className="effect-data active"' not in subject
    assert 'className="effect-data"' in subject
    assert 'data-io-class-reveal="active"' in subject
    assert 'opacity: "1"' not in subject
    assert 'transform: "matrix(1, 0, 0, 1, 0, 0)"' not in subject
    assert 'transitionProperty: "opacity, transform"' not in descendant
    assert 'transitionDuration: "0.6s, 0.6s"' not in descendant
    assert 'opacity: "1"' not in descendant
    assert 'transform: "matrix(1, 0, 0, 1, 0, 0)"' not in descendant
    assert static_active
    assert "IOClassRevealDriver" in blob
    assert "IntersectionObserver" in blob


def test_transition_spec_class_reveal_keeps_inline_guarded_transitions(
    tmp_path: Path,
) -> None:
    node = {
        "tag": "div",
        "class": "effect-data active",
        "children": [],
        "inlineProps": ["transition-property", "transition-duration"],
        "styles": {
            "opacity": "1",
            "transition-property": "opacity",
            "transition-duration": "0.6s",
        },
    }
    ref = _ref(tmp_path, node, ".effect-data:not(.active){opacity:0}.effect-data.active{opacity:1}")
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "selector": ".effect-data:not(.active)",
                        "trigger": "viewport IntersectionObserver",
                        "animation": {
                            "type": "class-toggle",
                            "property": "className",
                            "from": {"className": "effect-data"},
                            "to": {"className": "effect-data active"},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _blob(impl)

    opening_tag = next(line for line in blob.splitlines() if 'className="effect-data' in line)
    assert 'className="effect-data active"' not in opening_tag
    assert 'data-io-class-reveal="active"' in opening_tag
    assert 'transitionProperty: "opacity"' in opening_tag
    assert 'transitionDuration: "0.6s"' in opening_tag


def test_transition_spec_class_name_and_not_active_descendant_unbakes_subjects(
    tmp_path: Path,
) -> None:
    """Lock the bundle-extracted Naver reveal shape without synthetic from/to states."""
    ref = tmp_path / "ref"
    ref.mkdir()
    reveal_nodes = []
    for subject_class, duration in (
        ("effect-data active", "1.6s, 1.6s"),
        ("items effect-flip active", "1.6s, 1.6s"),
        ("items effect-flip", "0s, 0s"),
    ):
        reveal_nodes.append(
            {
                "tag": "div",
                "class": subject_class,
                "styles": {},
                "children": [
                    {
                        "tag": "div",
                        "class": "effect-value",
                        "styles": {
                            "opacity": "1",
                            "transform": "matrix(1, 0, 0, 1, 0, 0)",
                            "transition": (
                                "opacity 0s ease, transform 0s ease"
                                if duration.startswith("0s")
                                else "opacity 1.6s ease, transform 1.6s ease"
                            ),
                            "transition-property": "opacity, transform",
                            "transition-duration": duration,
                        },
                        "children": [],
                    }
                ],
            }
        )
    reveal_nodes.append(
        {
            "tag": "span",
            "class": "badge active",
            "styles": {"opacity": "1"},
            "children": [],
        }
    )
    structure = {
        "tag": "body",
        "styles": {},
        "children": [
            {
                "tag": "section",
                "class": "sec",
                "styles": {},
                "children": reveal_nodes,
            }
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "sec"}]}),
        encoding="utf-8",
    )
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "effect-data-flip-reveal",
                        "trigger": "intersection",
                        "target": (
                            ".effect-data:not(.active) .effect-value, "
                            ".effect-flip:not(.active) .effect-value"
                        ),
                        "animation": {
                            "type": "intersectionobserver-class-toggle",
                            "property": "opacity, transform",
                            "className": "active",
                            "threshold": 0,
                            "rootMargin": "0px 0px 1px 0px",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "css").mkdir()
    (ref / "css" / "main.css").write_text(
        ".effect-data:not(.active) .effect-value{"
        "opacity:0;transform:rotateY(180deg);"
        "transition-property:opacity,transform;transition-duration:0s,0s}"
        ".effect-data.active .effect-value{"
        "opacity:1;transform:none;"
        "transition-property:opacity,transform;transition-duration:1.6s,1.6s}"
        ".items .effect-value{opacity:0;transform:rotateY(180deg);"
        "transition:opacity 0s ease,transform 0s ease}"
        ".items.active .effect-value{opacity:1;transform:none;"
        "transition-duration:1.6s,1.6s}",
        encoding="utf-8",
    )

    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _blob(impl)

    data_subject = next(
        line for line in blob.splitlines() if 'className="effect-data' in line
    )
    assert 'className="effect-data active"' not in data_subject
    assert 'data-io-class-reveal="active"' in data_subject
    flip_subjects = [
        line for line in blob.splitlines() if 'className="items effect-flip' in line
    ]
    assert len(flip_subjects) == 2
    assert all('className="items effect-flip active"' not in line for line in flip_subjects)
    assert all('data-io-class-reveal="active"' in line for line in flip_subjects)
    for descendant in (
        line for line in blob.splitlines() if 'className="effect-value"' in line
    ):
        assert 'opacity: "1"' not in descendant
        assert 'transform: "matrix(1, 0, 0, 1, 0, 0)"' not in descendant
        assert 'transition: "' not in descendant
        assert "transitionDuration" not in descendant
    assert 'className="badge active"' in blob
    assert "IOClassRevealDriver" in blob
    driver = (impl / "src" / "lib" / "IOClassRevealDriver.tsx").read_text(encoding="utf-8")
    assert "const documentTop = el.getBoundingClientRect().top + window.scrollY" in driver
    assert "window.scrollY + window.innerHeight > documentTop" in driver


def test_stateful_css_releases_captured_idle_values(tmp_path: Path) -> None:
    submenu = {
        "tag": "div",
        "class": "nav__list2",
        "children": [],
        "styles": {
            "transform": "matrix(1, 0, 0, 1, 0, -500)",
            "visibility": "hidden",
            "opacity": "0",
        },
    }
    node = {
        "tag": "header",
        "class": "header",
        "children": [
            {
                "tag": "ul",
                "class": "nav__list",
                "children": [submenu],
                "styles": {},
            }
        ],
        "styles": {},
    }
    css = (
        ".nav__list2{transform:translateY(-100%);visibility:hidden;opacity:0}"
        ".header .nav__list.is-show .nav__list2.is-active{"
        "transform:translateY(0);visibility:visible;opacity:1}"
    )

    blob, _ = _emit(tmp_path, node, css)

    opening_tag = next(line for line in blob.splitlines() if 'className="nav__list2"' in line)
    assert 'transform: "matrix(1, 0, 0, 1, 0, -500)"' not in opening_tag
    assert 'visibility: "hidden"' not in opening_tag
    assert 'opacity: "0"' not in opening_tag


def test_stateful_css_releases_ancestor_state_color(tmp_path: Path) -> None:
    node = {
        "tag": "header",
        "class": "header",
        "children": [
            {
                "tag": "a",
                "class": "nav__link",
                "children": [],
                "text": "Company",
                "styles": {"color": "rgb(26, 29, 36)"},
            }
        ],
        "styles": {},
    }
    css = (
        ".header .nav__link{color:#1a1d24}"
        ".header.is-nav-active .nav__link{color:#1a1d24}"
        ".header:not(.is-nav-active) .nav__link:hover{color:#ff5f00}"
    )

    blob, _ = _emit(tmp_path, node, css)

    opening_tag = next(line for line in blob.splitlines() if 'className="nav__link"' in line)
    assert 'color: "rgb(26, 29, 36)"' not in opening_tag


def test_stateful_css_releases_mirrored_hover_font_weight(tmp_path: Path) -> None:
    link = {
        "tag": "a",
        "class": "nav__link",
        "children": [],
        "text": "Company",
        "styles": {"font-weight": "400"},
    }
    node = {
        "tag": "nav",
        "class": "nav",
        "children": [
            {
                "tag": "div",
                "class": "nav__item",
                "children": [link],
                "styles": {},
            }
        ],
        "styles": {},
    }
    css = ".nav__item .nav__link{font-weight:400}.nav__item:hover .nav__link{font-weight:600}"

    blob, _ = _emit(tmp_path, node, css)

    opening_tag = next(line for line in blob.splitlines() if 'className="nav__link"' in line)
    assert 'fontWeight: "400"' not in opening_tag


def test_stateful_parent_color_releases_classless_inherited_child(tmp_path: Path) -> None:
    child = {
        "tag": "em",
        "children": [],
        "text": "Privacy policy",
        "styles": {"color": "rgb(113, 118, 128)"},
    }
    node = {
        "tag": "button",
        "class": "service__link2",
        "children": [child],
        "styles": {"color": "rgb(113, 118, 128)"},
    }
    css = ".footer .service__link2{color:#717680}.footer .service__link2:hover{color:#1a1d24}"
    wrapped = {
        "tag": "footer",
        "class": "footer",
        "children": [node],
        "styles": {},
    }

    blob, _ = _emit(tmp_path, wrapped, css)

    button_tag = next(line for line in blob.splitlines() if 'className="service__link2"' in line)
    child_tag = next(line for line in blob.splitlines() if "<em" in line)
    assert 'color: "rgb(113, 118, 128)"' not in button_tag
    assert 'color: "rgb(113, 118, 128)"' not in child_tag


def test_stateful_css_preserves_value_outside_required_ancestor(tmp_path: Path) -> None:
    node = {
        "tag": "a",
        "class": "nav__link",
        "children": [],
        "text": "Unrelated link",
        "styles": {"color": "rgb(26, 29, 36)"},
    }
    css = ".header .nav__link{color:#1a1d24}.header.is-nav-active .nav__link{color:#ffffff}"

    blob, _ = _emit(tmp_path, node, css)

    opening_tag = next(line for line in blob.splitlines() if 'className="nav__link"' in line)
    assert 'color: "rgb(26, 29, 36)"' in opening_tag


def test_stateful_css_preserves_value_for_reversed_ancestor_chain(tmp_path: Path) -> None:
    link = {
        "tag": "a",
        "class": "nav__link",
        "children": [],
        "text": "Reversed",
        "styles": {"color": "rgb(26, 29, 36)"},
    }
    node = {
        "tag": "div",
        "class": "menu",
        "styles": {},
        "children": [{"tag": "header", "class": "header", "styles": {}, "children": [link]}],
    }
    css = ".header .menu .nav__link{color:#1a1d24}.header.is-active .menu .nav__link{color:#ffffff}"

    blob, _ = _emit(tmp_path, node, css)

    opening_tag = next(line for line in blob.splitlines() if 'className="nav__link"' in line)
    assert 'color: "rgb(26, 29, 36)"' in opening_tag


def test_stateful_css_rejects_unproven_child_combinator(tmp_path: Path) -> None:
    link = {
        "tag": "a",
        "class": "nav__link",
        "children": [],
        "text": "Nested child",
        "styles": {"color": "rgb(26, 29, 36)"},
    }
    node = {
        "tag": "header",
        "class": "header",
        "styles": {},
        "children": [
            {
                "tag": "div",
                "class": "wrapper",
                "styles": {},
                "children": [{"tag": "div", "class": "menu", "styles": {}, "children": [link]}],
            }
        ],
    }
    css = (
        ".header > .menu .nav__link{color:#1a1d24}"
        ".header.is-active > .menu .nav__link{color:#ffffff}"
    )

    blob, _ = _emit(tmp_path, node, css)

    opening_tag = next(line for line in blob.splitlines() if 'className="nav__link"' in line)
    assert 'color: "rgb(26, 29, 36)"' in opening_tag


def test_stateful_css_preserves_author_inline_value(tmp_path: Path) -> None:
    node = {
        "tag": "div",
        "class": "panel",
        "children": [],
        "styles": {"visibility": "hidden"},
        "inlineProps": ["visibility"],
    }
    css = ".panel{visibility:hidden}.panel.is-open{visibility:visible}"

    blob, _ = _emit(tmp_path, node, css)

    assert 'visibility: "hidden"' in blob


def test_stateful_css_requires_a_base_endpoint(tmp_path: Path) -> None:
    node = {
        "tag": "div",
        "class": "panel",
        "children": [],
        "styles": {"opacity": "0"},
    }

    blob, _ = _emit(tmp_path, node, ".panel.is-open{opacity:1}")

    assert 'opacity: "0"' in blob


def test_hoisted_root_class_releases_computed_padding_shorthand(
    tmp_path: Path,
) -> None:
    """A responsive CSS padding longhand must outrank no computed inline snapshot."""
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "body",
        "class": "",
        "styles": {"padding": "72px 0px 0px"},
        "children": [
            {
                "tag": "main",
                "class": "style_home__UfVkI",
                "styles": {},
                "children": [{"tag": "h1", "text": "eBay Playbook"}],
            }
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "main", "cls": "style_home__UfVkI"}]}),
        encoding="utf-8",
    )
    (ref / "css").mkdir()
    (ref / "css" / "main.css").write_text(
        ".style_home__UfVkI{padding-top:var(--dimension-core-500)}"
        ".style_home__UfVkI h1{max-width:10ch}"
        "@media(min-width:768px){"
        ".style_home__UfVkI{padding-top:var(--dimension-core-800)}}",
        encoding="utf-8",
    )
    impl = tmp_path / "impl"

    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    opening_tag = next(line for line in app.splitlines() if 'className="style_home__UfVkI"' in line)

    assert 'padding: "72px 0px 0px"' not in opening_tag


def test_padding_shorthand_keeps_author_inline_longhand(tmp_path: Path) -> None:
    node = {
        "tag": "div",
        "class": "padded",
        "children": [],
        "styles": {"padding": "72px 0px 0px"},
        "inlineProps": ["padding-top"],
    }
    blob, _ = _emit(tmp_path, node, ".padded{padding-top:4rem}")

    assert 'paddingTop: "72px"' in blob, blob


def test_padding_shorthand_preserves_uncredited_nonzero_sides(
    tmp_path: Path,
) -> None:
    node = {
        "tag": "div",
        "class": "padded",
        "children": [],
        "styles": {"padding": "72px 24px 32px 16px"},
    }
    blob, _ = _emit(tmp_path, node, ".padded{padding-top:4rem}")

    assert 'padding: "72px 24px 32px 16px"' not in blob, blob
    assert 'paddingTop: "72px"' not in blob, blob
    assert 'paddingRight: "24px"' in blob, blob
    assert 'paddingBottom: "32px"' in blob, blob
    assert 'paddingLeft: "16px"' in blob, blob


def test_no_ref_css_is_a_no_op(tmp_path: Path) -> None:
    blob, err = _emit(tmp_path, dict(_HERO), None)
    assert 'maxWidth: "1280px"' in blob, blob
    assert "un-baked" not in err


def test_no_ref_css_keeps_computed_line_height(tmp_path: Path) -> None:
    node = {
        "tag": "h2",
        "class": "h3",
        "children": [],
        "text": "GitHub Docs",
        "styles": {"font-size": "18px", "line-height": "27px"},
    }
    blob, err = _emit(tmp_path, node, None)
    assert 'lineHeight: "27px"' in blob, blob
    assert "un-baked" not in err


def test_kill_switch_disables(tmp_path: Path) -> None:
    blob, _ = _emit(
        tmp_path,
        dict(_HERO),
        ".hero-box { width: 100%; }",
        env={"UI_CLONE_UNBAKE_REF_COVERED": "0"},
    )
    assert 'maxWidth: "1280px"' in blob, blob


def test_grid_template_dropped_when_covered(tmp_path: Path) -> None:
    node = {
        "tag": "div",
        "class": "thumbs",
        "children": [],
        "styles": {"grid-template-columns": "302px 1006px"},
    }
    blob, _ = _emit(tmp_path, node, ".thumbs { grid-template-columns: repeat(2, 1fr); }")
    assert '"302px 1006px"' not in blob, blob


def test_non_px_values_untouched(tmp_path: Path) -> None:
    node = {
        "tag": "div",
        "class": "fluid",
        "children": [],
        "styles": {"width": "83.3vw"},
    }
    blob, _ = _emit(tmp_path, node, ".fluid { width: 100%; }")
    assert 'width: "83.3vw"' in blob, blob


def test_unbake_precedes_height_conversion(tmp_path: Path) -> None:
    # Condition 1: the drop happens on CAPTURED styles, before Fix 20/21 —
    # a covered height must not leave a synthesized min-height floor behind.
    node = {
        "tag": "div",
        "class": "tall",
        "children": [],
        "styles": {"height": "1800px"},
    }
    blob, _ = _emit(tmp_path, node, ".tall { height: 200vh; }")
    assert 'minHeight: "1800px"' not in blob, blob
    assert '"1800px"' not in blob, blob


def test_uncovered_height_still_converts_to_min_height(tmp_path: Path) -> None:
    # Regression guard for the protected fix: without CSS coverage the
    # existing Fix 20/21 conversion must still run.
    node = {
        "tag": "div",
        "class": "tall2",
        "children": [{"tag": "p", "text": "content", "children": []}],
        "styles": {"height": "1800px"},
    }
    blob, _ = _emit(tmp_path, node, ".other { color: red; }")
    assert 'minHeight: "1800px"' in blob, blob


def test_multi_class_compound_subject_not_credited(tmp_path: Path) -> None:
    # fable MAJOR: `.card.wide{width}` declares width for the NARROWER
    # .card.wide set; crediting each token would drop the bake on a node
    # with class "card" alone — a node the rule does not apply to, with
    # nothing taking over (capture-width regression, the forensic-ghost
    # failure mode). Only an exactly-one-bare-class subject may credit.
    node = {
        "tag": "div",
        "class": "card",
        "children": [],
        "styles": {"width": "600px", "background-color": "rgb(1,2,3)"},
    }
    blob, err = _emit(tmp_path, node, ".card.wide { width: 100%; }")
    assert 'maxWidth: "600px"' in blob, blob  # P5 shape of the KEPT bake
    assert "un-baked" not in err


def test_tag_qualified_subject_not_credited(tmp_path: Path) -> None:
    # Same hole shape: `div.item{width}` must not credit `item` for
    # arbitrary nodes (a <span class="item"> is outside the rule).
    node = {
        "tag": "span",
        "class": "item",
        "children": [],
        "styles": {"width": "600px"},
    }
    blob, err = _emit(tmp_path, node, "div.item { width: 100%; }")
    assert 'maxWidth: "600px"' in blob, blob
    assert "un-baked" not in err


def test_exact_tag_class_subject_drops_captured_line_height(tmp_path: Path) -> None:
    node = {
        "tag": "h2",
        "class": "h3",
        "children": [],
        "text": "GitHub Docs",
        "styles": {"line-height": "32px"},
    }
    blob, err = _emit(tmp_path, node, "h2.h3 { line-height: 1.25; }")
    assert 'lineHeight: "32px"' not in blob, blob
    assert "un-baked" in err, err


def test_exact_tag_class_subject_respects_inline_props_guard(tmp_path: Path) -> None:
    node = {
        "tag": "h2",
        "class": "h3",
        "children": [],
        "text": "GitHub Docs",
        "styles": {"line-height": "32px"},
        "inlineProps": ["line-height"],
    }
    blob, err = _emit(tmp_path, node, "h2.h3 { line-height: 1.25; }")
    assert 'lineHeight: "32px"' in blob, blob
    assert "un-baked" not in err


def test_credited_font_size_drops_derived_px_line_height(tmp_path: Path) -> None:
    node = {
        "tag": "h2",
        "class": "h3",
        "children": [],
        "text": "GitHub Docs",
        "styles": {"font-size": "20px", "line-height": "30px"},
    }
    blob, err = _emit(tmp_path, node, ".h3 { font-size: 18px; }")
    assert 'fontSize: "20px"' not in blob, blob
    assert 'lineHeight: "30px"' not in blob, blob
    assert "un-baked" in err, err


def test_credited_font_size_keeps_author_inline_line_height(tmp_path: Path) -> None:
    node = {
        "tag": "h2",
        "class": "h3",
        "children": [],
        "text": "GitHub Docs",
        "styles": {"font-size": "20px", "line-height": "30px"},
        "inlineProps": ["line-height"],
    }
    blob, _ = _emit(tmp_path, node, ".h3 { font-size: 18px; }")
    assert 'fontSize: "20px"' not in blob, blob
    assert 'lineHeight: "30px"' in blob, blob


def test_mirrored_css_releases_ancestor_line_height_bakes(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "body",
        "class": "",
        "styles": {"line-height": "21px"},
        "children": [
            {
                "tag": "section",
                "class": "docs",
                "styles": {"line-height": "21px"},
                "children": [
                    {
                        "tag": "div",
                        "class": "",
                        "styles": {"line-height": "21px"},
                        "children": [
                            {
                                "tag": "h2",
                                "class": "h3",
                                "text": "GitHub Docs",
                                "styles": {"font-size": "20px", "line-height": "30px"},
                                "children": [],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "docs"}]}),
        encoding="utf-8",
    )
    (ref / "css").mkdir()
    (ref / "css" / "main.css").write_text(
        "body{line-height:1.5}.h3{font-size:18px}",
        encoding="utf-8",
    )
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _blob(impl)
    assert 'lineHeight: "21px"' not in blob, blob
    assert 'lineHeight: "30px"' not in blob, blob
    assert 'fontSize: "20px"' not in blob, blob


def test_mirrored_css_keeps_author_inline_ancestor_line_height(
    tmp_path: Path,
) -> None:
    node = {
        "tag": "div",
        "class": "",
        "children": [],
        "styles": {"line-height": "21px"},
        "inlineProps": ["line-height"],
    }
    blob, _ = _emit(tmp_path, node, "body { line-height: 1.5; }")
    assert 'lineHeight: "21px"' in blob, blob


# ── v2: @media / @container condition evaluation ──────────────────────────
# A rule inside a media block credits its subject class IFF the condition
# APPLIES at the capture width (default 1440, env UI_CLONE_UNBAKE_CAPTURE_W).
# A min-width block that applies at capture width is actively sizing the
# element there; un-baking lets the mirrored cascade drive at every width,
# and below the breakpoint the ref itself computes base/auto too.


def test_media_min_width_at_capture_drops_the_bake(tmp_path: Path) -> None:
    # THE v2 FLIP: v1 kept this (any @media). min-width 1024px applies at
    # the 1440 capture width, so the bake is a legitimate un-bake source.
    blob, err = _emit(
        tmp_path, dict(_HERO), "@media (min-width: 1024px) { .hero-box { width: 50%; } }"
    )
    assert '"1280px"' not in blob, blob
    assert "un-baked" in err, err


def test_width_media_releases_captured_flex_posture(tmp_path: Path) -> None:
    node = {
        "tag": "div",
        "class": "footer__service",
        "children": [],
        "styles": {
            "display": "flex",
            "flex-direction": "column",
            "align-items": "flex-start",
            "justify-content": "flex-start",
        },
    }
    css = (
        ".footer__service{display:flex;align-items:center}"
        "@media(max-width:1599px){.footer__service{"
        "flex-direction:column;align-items:flex-start;justify-content:flex-start}}"
    )

    blob, err = _emit(tmp_path, node, css)
    opening_tag = next(line for line in blob.splitlines() if 'className="footer__service"' in line)

    assert "flexDirection" not in opening_tag
    assert "alignItems" not in opening_tag
    assert "justifyContent" not in opening_tag
    assert "responsive CSS" in err


def test_width_media_flex_posture_respects_author_inline_guard(tmp_path: Path) -> None:
    node = {
        "tag": "div",
        "class": "footer__service",
        "children": [],
        "inlineProps": ["flex-direction"],
        "styles": {"display": "flex", "flex-direction": "column"},
    }
    css = "@media(max-width:1599px){.footer__service{flex-direction:column}}"

    blob, _ = _emit(tmp_path, node, css)
    opening_tag = next(line for line in blob.splitlines() if 'className="footer__service"' in line)

    assert 'flexDirection: "column"' in opening_tag


def test_non_applying_width_media_keeps_captured_flex_posture(tmp_path: Path) -> None:
    node = {
        "tag": "div",
        "class": "toolbar",
        "children": [],
        "styles": {"display": "flex", "align-items": "flex-start"},
    }
    css = "@media(max-width:768px){.toolbar{align-items:center}}"

    blob, _ = _emit(tmp_path, node, css)
    opening_tag = next(line for line in blob.splitlines() if 'className="toolbar"' in line)

    assert 'alignItems: "flex-start"' in opening_tag


def test_media_min_width_above_capture_keeps_the_bake(tmp_path: Path) -> None:
    # 1600 > 1440 capture → block does not apply → keep bake.
    blob, _ = _emit(
        tmp_path, dict(_HERO), "@media (min-width: 1600px) { .hero-box { width: 50%; } }"
    )
    assert 'maxWidth: "1280px"' in blob, blob


def test_media_non_width_condition_keeps_the_bake(tmp_path: Path) -> None:
    # prefers-* is not a width term → unknown → keep bake (conservative).
    blob, _ = _emit(
        tmp_path,
        dict(_HERO),
        "@media (prefers-reduced-motion: reduce) { .hero-box { width: 50%; } }",
    )
    assert 'maxWidth: "1280px"' in blob, blob


def test_media_range_excluding_capture_keeps_the_bake(tmp_path: Path) -> None:
    # 768..1200 does not include 1440 → the `and` chain fails → keep bake.
    blob, _ = _emit(
        tmp_path,
        dict(_HERO),
        "@media (min-width: 768px) and (max-width: 1200px) { .hero-box { width: 50%; } }",
    )
    assert 'maxWidth: "1280px"' in blob, blob


def test_media_range_including_capture_drops_the_bake(tmp_path: Path) -> None:
    # 768..1600 includes 1440 → both terms apply → un-bake.
    blob, err = _emit(
        tmp_path,
        dict(_HERO),
        "@media (min-width: 768px) and (max-width: 1600px) { .hero-box { width: 50%; } }",
    )
    assert '"1280px"' not in blob, blob
    assert "un-baked" in err, err


def test_media_comma_or_branch_applies_drops_the_bake(tmp_path: Path) -> None:
    # `,` is OR: the second branch (min-width 1000px) applies at 1440.
    blob, err = _emit(
        tmp_path,
        dict(_HERO),
        "@media (min-width: 2000px), (min-width: 1000px) { .hero-box { width: 50%; } }",
    )
    assert '"1280px"' not in blob, blob
    assert "un-baked" in err, err


def test_media_capture_width_env_override(tmp_path: Path) -> None:
    # 1600 does not apply at default 1440, but DOES at an overridden 1700.
    blob, err = _emit(
        tmp_path,
        dict(_HERO),
        "@media (min-width: 1600px) { .hero-box { width: 50%; } }",
        env={"UI_CLONE_UNBAKE_CAPTURE_W": "1700"},
    )
    assert '"1280px"' not in blob, blob
    assert "un-baked" in err, err


def test_media_only_screen_prefix_drops_the_bake(tmp_path: Path) -> None:
    # `only screen and (min-width: ...)` — the media-type words are neutral;
    # the applying width term credits the bake.
    blob, err = _emit(
        tmp_path,
        dict(_HERO),
        "@media only screen and (min-width: 1024px) { .hero-box { width: 50%; } }",
    )
    assert '"1280px"' not in blob, blob
    assert "un-baked" in err, err


def test_media_print_type_keeps_the_bake(tmp_path: Path) -> None:
    # `print` is a constraint we cannot honor at capture width → keep bake.
    blob, _ = _emit(
        tmp_path, dict(_HERO), "@media print and (min-width: 1024px) { .hero-box { width: 50%; } }"
    )
    assert 'maxWidth: "1280px"' in blob, blob


def test_media_not_all_keeps_the_bake(tmp_path: Path) -> None:
    # `not` inverts the query; we do not evaluate negation → keep bake.
    blob, _ = _emit(
        tmp_path,
        dict(_HERO),
        "@media not all and (min-width: 1024px) { .hero-box { width: 50%; } }",
    )
    assert 'maxWidth: "1280px"' in blob, blob


def test_media_trailing_empty_comma_branch_keeps_the_bake(tmp_path: Path) -> None:
    # An empty comma-branch (from a trailing comma, or a comment-stripped
    # branch) must NEVER grant credit — otherwise a non-applying block
    # (min-width 1600 > 1440) would be falsely un-baked (the catastrophic
    # false-un-bake → auto-collapse direction). Reachable from real CSS after
    # the comment strip, e.g. `@media (min-width:1600px) /* note */,`.
    blob, _ = _emit(
        tmp_path, dict(_HERO), "@media (min-width: 1600px), { .hero-box { width: 50%; } }"
    )
    assert 'maxWidth: "1280px"' in blob, blob


def test_media_leading_empty_comma_branch_keeps_the_bake(tmp_path: Path) -> None:
    # Same, empty branch first; the real branch (2000px) does not apply at 1440.
    blob, _ = _emit(
        tmp_path, dict(_HERO), "@media , (min-width: 2000px) { .hero-box { width: 50%; } }"
    )
    assert 'maxWidth: "1280px"' in blob, blob


def test_media_rem_width_applies_drops_the_bake(tmp_path: Path) -> None:
    # Media-query rem/em are relative to the INITIAL font size (16px),
    # independent of the page font-size, so 64rem == 1024px applies at 1440.
    blob, err = _emit(
        tmp_path, dict(_HERO), "@media (min-width: 64rem) { .hero-box { width: 50%; } }"
    )
    assert '"1280px"' not in blob, blob
    assert "un-baked" in err, err


def test_media_rem_width_above_capture_keeps_the_bake(tmp_path: Path) -> None:
    # 100rem == 1600px > 1440 → does not apply → keep bake.
    blob, _ = _emit(
        tmp_path, dict(_HERO), "@media (min-width: 100rem) { .hero-box { width: 50%; } }"
    )
    assert 'maxWidth: "1280px"' in blob, blob


def test_container_query_keeps_the_bake(tmp_path: Path) -> None:
    # @container width is relative to the container, not the viewport — we
    # cannot evaluate it against the capture width → keep bake (conservative).
    blob, _ = _emit(
        tmp_path, dict(_HERO), "@container (min-width: 300px) { .hero-box { width: 50%; } }"
    )
    assert 'maxWidth: "1280px"' in blob, blob


def test_extract_dom_captures_inline_props() -> None:
    body = (ROOT / "skills" / "visual-debug" / "scripts" / "lib" / "extract-dom.js").read_text(
        encoding="utf-8"
    )
    assert "inlineProps" in body


def test_unbake_preserves_centering_transform(tmp_path: Path) -> None:
    """gen-H4: un-bake drops ref-CSS-covered width/height, but
    _is_centering_transform verifies a translate(-50%,-50%) (resolved to
    matrix(...,-W/2,-H/2)) against the CAPTURED width/height. If it reads the
    UN-BAKED styles (width/height gone), the centering is unrecognized and the
    transform is stripped as a parallax state, displacing the element by half
    its own size (loop-129 Fix 68 regression)."""
    node = {
        "tag": "div",
        "class": "centered-box",
        "children": [],
        "styles": {
            "position": "absolute",
            "top": "50%",
            "left": "50%",
            "width": "1282px",
            "height": "810px",
            "transform": "matrix(1, 0, 0, 1, -641, -405)",
        },
    }
    css = ".centered-box{width:1282px;height:810px;}"
    blob, _ = _emit(tmp_path, node, css)
    assert "matrix(1, 0, 0, 1, -641, -405)" in blob, (
        "centering transform must survive un-bake dropping width/height "
        "(Fix 68 must verify against captured, not un-baked, dimensions)"
    )


# ── Fix B: font-size un-bake (Fable-reviewed, accept-with-375-A/B) ──────────
# Ref bakes are clamp/vw-driven, e.g. font-size:clamp(var(--fs),15vw,164px):
# 15vw computes 216->clamps 164px at 1440 (baked), 56.25px at 375. The baked
# inline px freezes the responsive text. Un-bake it only where a BASE rule
# covers the class, inheriting correctness from the mirrored ref CSS.
_HEADLINE = {
    "tag": "h1",
    "class": "headline",
    "children": [],
    "styles": {"font-size": "164px", "color": "rgb(1,2,3)"},
}


def test_unbake_font_size_ref_covered_drops_the_bake(tmp_path: Path) -> None:
    blob, err = _emit(
        tmp_path, dict(_HEADLINE), ".headline { font-size: clamp(1rem, 15vw, 164px); }"
    )
    assert 'fontSize: "164px"' not in blob, blob
    assert "un-baked" in err, err


def test_unbake_font_size_no_coverage_keeps_the_bake(tmp_path: Path) -> None:
    blob, _ = _emit(tmp_path, dict(_HEADLINE), ".other { color: red; }")
    assert 'fontSize: "164px"' in blob, blob


def test_unbake_font_size_media_only_keeps_the_bake(tmp_path: Path) -> None:
    blob, _ = _emit(
        tmp_path, dict(_HEADLINE), "@media (max-width: 768px) { .headline { font-size: 32px; } }"
    )
    assert 'fontSize: "164px"' in blob, blob


def test_unbake_font_size_inline_props_guard_keeps_the_bake(tmp_path: Path) -> None:
    node = dict(_HEADLINE)
    node["inlineProps"] = ["font-size"]
    blob, _ = _emit(tmp_path, node, ".headline { font-size: clamp(1rem, 15vw, 164px); }")
    assert 'fontSize: "164px"' in blob, blob


# ── Fix B-v2: root-anchored descendant un-bake (Fable-reviewed) ────────────
# Ref sizes the class-less hero <h1> via a DESCENDANT rule on the structure
# root: `.style_home__UfVkI h1{font-size:clamp(var,15vw,164px);line-height:.89em;
# letter-spacing:-.03em;max-width:10ch}`. The root class is emitted as the App
# root, so every node is under it → credit `(rootToken, tag)` when a base rule
# `.rootToken tag{...}` covers the prop. Co-bakes (line-height/letter-spacing/
# max-width) and the Fix 20/21 min-height floor must also release, else the hero
# stays tall / malformed at mobile.
_HOME_H1_CSS = (
    ".style_home__UfVkI h1{font-size:clamp(1rem,15vw,164px);"
    "line-height:.89em;letter-spacing:-.03em;max-width:10ch}"
)


def _ref_rooted(tmp_path: Path, root_class: str, node: dict) -> Path:
    """Structure root carries `root_class`; node sits inside a section under it."""
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "body",
        "class": root_class,
        "styles": {},
        "children": [
            {"tag": "section", "class": "sec", "styles": {}, "children": [node]},
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "sec"}]}), encoding="utf-8"
    )
    (ref / "css").mkdir()
    (ref / "css" / "main.css").write_text(_HOME_H1_CSS, encoding="utf-8")
    return ref


def _emit_rooted(
    tmp_path: Path, root_class: str, node: dict, css: str | None = None
) -> tuple[str, str]:
    ref = _ref_rooted(tmp_path, root_class, node)
    if css is not None:
        (ref / "css" / "main.css").write_text(css, encoding="utf-8")
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _blob(impl), proc.stderr


def _hero_h1(cls: str | None = None, inline_props: list[str] | None = None) -> dict:
    styles = {
        "font-size": "164px",
        "line-height": "145.96px",
        "letter-spacing": "-4.92px",
        "max-width": "1125.03px",
        "height": "437.859px",
        "color": "rgb(1,2,3)",
    }
    node: dict = {
        "tag": "h1",
        "children": [],
        "text": "One system for everyone to love.",
        "styles": styles,
    }
    if cls is not None:
        node["class"] = cls
    if inline_props is not None:
        node["inlineProps"] = inline_props
    return node


def test_desc_unbake_root_anchored_h1_drops_font_cluster(tmp_path: Path) -> None:
    blob, err = _emit_rooted(tmp_path, "style_home__UfVkI", _hero_h1())
    assert 'fontSize: "164px"' not in blob, blob
    assert 'lineHeight: "145.96px"' not in blob, blob
    assert 'letterSpacing: "-4.92px"' not in blob, blob
    assert 'maxWidth: "1125.03px"' not in blob, blob
    assert "un-baked" in err, err


def test_desc_unbake_skips_minheight_floor_when_font_credited(tmp_path: Path) -> None:
    # Floor companion: height→min-height floor must NOT be emitted for a
    # font-credited growable text node with no ref height rule.
    blob, _ = _emit_rooted(tmp_path, "style_home__UfVkI", _hero_h1())
    assert 'minHeight: "437.859px"' not in blob, blob


def test_desc_unbake_anti_ghost_intermediate_wrapper_kept(tmp_path: Path) -> None:
    # The crediting ancestor must be a ROOT token. Root carries its OWN class
    # (style_home), and the rule anchors on a DIFFERENT non-root class (wrap-mid)
    # on an intermediate wrapper. wrap-mid is not a root token → NO credit → the
    # bake is kept (locks out any raw-ancestry "simplification" that would drop
    # the bake on a rule whose ancestor might be flattened out of the impl).
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "body",
        "class": "style_home__UfVkI",
        "styles": {},
        "children": [
            {
                "tag": "div",
                "class": "wrap-mid",
                "styles": {},
                "children": [
                    {"tag": "section", "class": "sec", "styles": {}, "children": [_hero_h1()]},
                ],
            },
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "sec"}]}), encoding="utf-8"
    )
    (ref / "css").mkdir()
    (ref / "css" / "main.css").write_text(
        ".wrap-mid h1{font-size:clamp(1rem,15vw,164px)}", encoding="utf-8"
    )
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'fontSize: "164px"' in _blob(impl), "non-root ancestor class must NOT credit"


def test_desc_unbake_child_combinator_kept(tmp_path: Path) -> None:
    blob, _ = _emit_rooted(
        tmp_path,
        "style_home__UfVkI",
        _hero_h1(),
        css=".style_home__UfVkI>h1{font-size:clamp(1rem,15vw,164px)}",
    )
    assert 'fontSize: "164px"' in blob, "child combinator must not credit any-descendant"


def test_desc_unbake_multi_compound_ancestor_kept(tmp_path: Path) -> None:
    blob, _ = _emit_rooted(
        tmp_path, "style_home__UfVkI", _hero_h1(), css=".a .b h1{font-size:clamp(1rem,15vw,164px)}"
    )
    assert 'fontSize: "164px"' in blob, "two-ancestor chain must not credit"


def test_desc_unbake_qualified_subject_kept(tmp_path: Path) -> None:
    node = _hero_h1(cls="title")
    blob, _ = _emit_rooted(
        tmp_path,
        "style_home__UfVkI",
        node,
        css=".style_home__UfVkI h1.title{font-size:clamp(1rem,15vw,164px)}",
    )
    assert 'fontSize: "164px"' in blob, "tag+class subject must not credit via desc path"


def test_desc_unbake_inline_props_guard_kept(tmp_path: Path) -> None:
    node = _hero_h1(inline_props=["font-size"])
    blob, _ = _emit_rooted(tmp_path, "style_home__UfVkI", node)
    assert 'fontSize: "164px"' in blob, "ref-inline prop must never be un-baked"


def test_desc_unbake_media_only_kept(tmp_path: Path) -> None:
    blob, _ = _emit_rooted(
        tmp_path,
        "style_home__UfVkI",
        _hero_h1(),
        css="@media (max-width:700px){.style_home__UfVkI h1{font-size:32px}}",
    )
    assert 'fontSize: "164px"' in blob, "non-applying @media desc rule must not credit at 1440"


def test_desc_unbake_kill_switch(tmp_path: Path) -> None:
    ref = _ref_rooted(tmp_path, "style_home__UfVkI", _hero_h1())
    impl = tmp_path / "impl"
    proc = _run(ref, impl, {"UI_CLONE_UNBAKE_REF_COVERED": "0"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'fontSize: "164px"' in _blob(impl), "kill-switch must keep every bake"
