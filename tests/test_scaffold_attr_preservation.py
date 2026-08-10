"""Attr-preservation defect families at the GENERATOR (campaign charter items).

Campaign evidence (each family reproduced against a live clone):
- A allow-attr drop: ebpb loop-1 — transpiler dropped iframe
  `allow="autoplay; fullscreen; ..."`; Chrome permissions policy then blocked
  Vimeo autoplay in all 3 embeds (poster+Play symptom).
- B data-* drop: generic data-* never reached the emitter (realfood
  `data-word-id` word-reveal spans); only capture-lazy data-src/... stay
  intentionally dropped (U1 — promoted onto src/srcset/poster instead).
- C custom-property mangling: `kebab_to_camel("--index")` → "Index" — every
  inline `--var` was renamed into a bogus property, breaking var() refs
  (ebpb `--index` strip).
- D svg element casing: extract-dom lowercases tagName, the emitter re-emitted
  `<clippath>` — invalid in React, so the rounded-tile clip never applied
  (ebpb loop-2).
- E unfamiliar svg attr drop: extract-dom captures EVERY svg attribute
  (universality audit) but the emitter iterated only its own whitelist, so
  the capture-side promise "the JSX emitter can apply the rename to whatever
  it sees" was unimplemented (ebpb loop-1 tagline path-d class).

Same integration idiom as test_scaffold_sizing_expressions.py: run the real
script against a minimal ref fixture and assert on the emitted .tsx blob.
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
    return "".join(p.read_text(encoding="utf-8") for p in src.rglob("*.tsx")) if src.is_dir() else ""


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    (impl / "src").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120, env={**os.environ},
    )


def _ref(tmp_path: Path, children: list[dict]) -> Path:
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {"tag": "body", "styles": {}, "children": [
        {"tag": "section", "class": "hero",
         "styles": {"background-color": "rgb(255,255,255)"},
         "children": children},
    ]}
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8")
    return ref


def _emit(tmp_path: Path, node: dict) -> str:
    ref = _ref(tmp_path, [node])
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _blob(impl)


# ── C. custom properties survive verbatim ──


def test_inline_custom_property_survives_verbatim(tmp_path: Path) -> None:
    blob = _emit(tmp_path, {
        "tag": "div", "class": "card",
        "styles": {"--index": "3", "color": "rgb(255, 0, 0)"},
        "children": [],
    })
    assert '"--index": "3"' in blob, blob
    # The mangled forms the old kebab_to_camel produced:
    assert "Index:" not in blob
    assert 'color: "rgb(255, 0, 0)"' in blob
    assert 'as import("react").CSSProperties' in blob


# ── A. allow / allowfullscreen emitted ──


def test_iframe_allow_attrs_emitted(tmp_path: Path) -> None:
    blob = _emit(tmp_path, {
        "tag": "iframe", "class": "embed",
        "src": "https://player.example.com/video/1",
        "allow": "autoplay; fullscreen; picture-in-picture",
        "allowfullscreen": "true",
        "children": [],
    })
    assert 'allow="autoplay; fullscreen; picture-in-picture"' in blob, blob
    assert "allowFullScreen" in blob, blob


def test_aria_popup_state_attrs_emitted(tmp_path: Path) -> None:
    blob = _emit(tmp_path, {
        "tag": "button", "class": "menu-trigger",
        "aria-label": "Open navigation",
        "aria-haspopup": "menu",
        "aria-expanded": "false",
        "children": [],
    })
    assert 'aria-haspopup="menu"' in blob, blob
    assert 'aria-expanded="false"' in blob, blob


def test_long_class_list_preserves_late_css_module_token_in_jsx(
    tmp_path: Path,
) -> None:
    tokens = [f"utility-{i:03d}" for i in range(35)]
    late_token = "MarkdownContent-module__heading--abc123"
    class_name = " ".join([*tokens, late_token])
    assert 300 < len(class_name) < 2000

    blob = _emit(tmp_path, {
        "tag": "h2",
        "class": class_name,
        "text": "Install GitHub CLI",
        "children": [],
    })
    assert f'className="{class_name}"' in blob, blob
    assert late_token in blob


def test_jsx_class_safety_envelope_never_cuts_a_token(tmp_path: Path) -> None:
    first = "a" * 1990
    boundary_token = "moduleXYZ"
    expected = f"{first} {boundary_token}"
    assert len(expected) == 2000

    blob = _emit(tmp_path, {
        "tag": "div",
        "class": f"{expected} truncated-token",
        "children": [],
    })
    assert f'className="{expected}"' in blob, blob
    assert "truncated-token" not in blob


# ── B. generic data-* pass through; U1 lazy artifacts stay dropped ──


def test_generic_data_attrs_emitted_but_lazy_artifacts_dropped(tmp_path: Path) -> None:
    blob = _emit(tmp_path, {
        "tag": "span", "class": "word",
        "data-word-id": "w17",
        "data-state": "dimmed",
        "data-src": "https://cdn.example.com/lazy.jpg",  # U1 — must NOT emit
        "data-lazy": "1",  # U1 — must NOT emit
        "children": [],
        "text": "chronic",
    })
    assert 'data-word-id="w17"' in blob, blob
    assert 'data-state="dimmed"' in blob, blob
    assert "data-src=" not in blob
    assert "data-lazy=" not in blob


# ── D. svg element tag casing restored ──


def test_svg_element_tag_casing_restored(tmp_path: Path) -> None:
    blob = _emit(tmp_path, {
        "tag": "svg", "class": "", "svg": True,
        "viewBox": "0 0 100 100",
        "children": [
            {"tag": "defs", "class": "", "svg": True, "children": [
                {"tag": "clippath", "class": "", "svg": True, "id": "tile",
                 "children": [
                     {"tag": "path", "class": "", "svg": True,
                      "d": "M0 0H100V100H0Z", "children": []},
                 ]},
                {"tag": "lineargradient", "class": "", "svg": True, "id": "g1",
                 "children": []},
            ]},
        ],
    })
    assert "<clipPath" in blob, blob
    assert "</clipPath>" in blob, blob
    assert "<clippath" not in blob
    assert "<linearGradient" in blob, blob
    assert 'd="M0 0H100V100H0Z"' in blob, blob


# ── E. unfamiliar captured svg attrs emitted with rename ──


def test_unfamiliar_svg_attrs_emitted_with_rename(tmp_path: Path) -> None:
    blob = _emit(tmp_path, {
        "tag": "svg", "class": "", "svg": True,
        "viewBox": "0 0 10 10",
        "children": [
            {"tag": "path", "class": "", "svg": True,
             "d": "M0 0L10 10Z",
             "clip-path": "url(#tile)",
             "vector-effect": "non-scaling-stroke",  # in no whitelist pre-fix
             "children": []},
        ],
    })
    # rewrite_css_urls normalizes url(#tile) -> url("#tile"), which the
    # emitter then wraps as a JS-string expression — both forms are valid;
    # assert the attribute+target survive rather than one literal spelling.
    assert "clipPath=" in blob, blob
    assert "#tile" in blob, blob
    assert 'vectorEffect="non-scaling-stroke"' in blob, blob


def test_captured_inline_style_attr_never_emitted_as_string(tmp_path: Path) -> None:
    # fable MAJOR-1: the capture-everything SVG loop records an inline
    # style="..." attr as node["style"]. Emitting it as a STRING attribute
    # would sit next to the rendered style={{...}} object — a duplicate JSX
    # attribute TypeScript rejects (build-breaking on the ebpb scrub scene,
    # whose g groups carry inline transform-origin styles).
    blob = _emit(tmp_path, {
        "tag": "svg", "class": "", "svg": True,
        "viewBox": "0 0 10 10",
        "children": [
            {"tag": "g", "class": "", "svg": True,
             "style": "transform-origin: 1400px 950px",
             "styles": {"transform-origin": "1400px 950px"},
             "children": []},
        ],
    })
    assert 'style="transform-origin' not in blob, blob
    assert 'transformOrigin: "1400px 950px"' in blob, blob


def test_svg_transform_origin_raw_property_moves_into_style_object(
    tmp_path: Path,
) -> None:
    """React does not type transformOrigin as an SVG element attribute."""
    blob = _emit(tmp_path, {
        "tag": "svg", "class": "", "svg": True,
        "viewBox": "0 0 10 10",
        "children": [{
            "tag": "g", "class": "", "svg": True,
            "transform-origin": "5px 5px",
            "style": "transform-origin: 5px 5px",
            "styles": {"transform": "matrix(0.5, 0, 0, 0.5, 0, 0)"},
            "children": [],
        }],
    })

    assert 'transformOrigin="5px 5px"' not in blob, blob
    assert 'transformOrigin: "5px 5px"' in blob, blob


def test_boolean_state_data_attrs_deferred_not_baked(tmp_path: Path) -> None:
    # fable MAJOR-2 + gen-M4: boolean data-* is captured runtime STATE, not
    # identity — a scrolled capture bakes data-in-view="true" and forces the
    # revealed state the runtime controller owns. The raw attr must NOT be baked,
    # but (gen-M4) it is DEFERRED to the StateRevealDriver, which sets it on
    # viewport entry so a [data-in-view=true]-gated reveal fires instead of
    # rendering the pre-state forever. Identity hooks must still pass.
    ref = _ref(tmp_path, [{
        "tag": "div", "class": "card",
        "data-in-view": "true",
        "data-word-id": "12",
        "children": [],
    }])
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _blob(impl)
    # The raw boolean state is NOT baked (would force the revealed state)...
    assert 'data-in-view="true"' not in blob, blob
    # ...it is deferred to the StateRevealDriver via the reveal stamp.
    assert 'data-ui-clone-state-reveal="data-in-view=true"' in blob, blob
    assert 'data-word-id="12"' in blob, blob
    assert "boolean state attr" in proc.stderr
    assert "data-in-view" in proc.stderr


def test_boolean_state_reveal_uses_ref_css_terminal_attr_value(tmp_path: Path) -> None:
    # A pre-entry capture can record data-in-view="false", while the mirrored
    # ref CSS gates the resting/revealed state on [data-in-view=true]. The reveal
    # driver must set the CSS-gated terminal value, not replay the captured
    # pre-state forever.
    ref = _ref(tmp_path, [{
        "tag": "div", "class": "card",
        "data-in-view": "false",
        "children": [{"tag": "p", "text": "reveals on scroll"}],
    }])
    css_dir = ref / "css"
    css_dir.mkdir()
    (css_dir / "ref.css").write_text(
        ".card[data-in-view=true] { opacity: 1; transform: none; }\n",
        encoding="utf-8",
    )
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _blob(impl)
    assert 'data-in-view="false"' not in blob, blob
    assert 'data-ui-clone-state-reveal="data-in-view=true"' in blob, blob


def test_boolean_state_reveal_inverts_single_hidden_selector_value(tmp_path: Path) -> None:
    """A single selector can describe the hidden pre-state rather than the
    terminal state. Replaying that captured value would keep the node hidden.
    """
    ref = _ref(tmp_path, [{
        "tag": "div", "class": "card",
        "data-in-view": "false",
        "children": [{"tag": "p", "text": "reveals on scroll"}],
    }])
    css_dir = ref / "css"
    css_dir.mkdir()
    (css_dir / "ref.css").write_text(
        ".card { opacity: 1; transform: none; transition: opacity .3s, transform .3s; }\n"
        ".card[data-in-view=false] { opacity: 0; transform: translateY(24px); }\n",
        encoding="utf-8",
    )
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _blob(impl)
    assert 'data-in-view="false"' not in blob, blob
    assert 'data-ui-clone-state-reveal="data-in-view=true"' in blob, blob


def test_boolean_state_reveal_ignores_ancestor_attr_selector(tmp_path: Path) -> None:
    ref = _ref(tmp_path, [{
        "tag": "div", "class": "viewport", "data-in-view": "false",
        "children": [{
            "tag": "div", "class": "card", "data-in-view": "false",
            "children": [],
        }],
    }])
    css_dir = ref / "css"
    css_dir.mkdir()
    (css_dir / "ref.css").write_text(
        ".viewport[data-in-view=true] .card { opacity: 1; transform: none; }\n",
        encoding="utf-8",
    )
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _blob(impl)
    assert (
        'className="card" data-ui-clone-state-reveal="data-in-view=false"'
        in blob
    ), blob


def test_disclosure_control_boolean_state_attr_is_not_viewport_deferred(
    tmp_path: Path,
) -> None:
    """G disclosure-state forcing (realfood-v4 faqs).

    gen-M4 defers a boolean data-* to the StateRevealDriver, which sets the
    ref-CSS TERMINAL value on viewport entry — correct for an
    IntersectionObserver-owned reveal, wrong for a CLICK-owned disclosure. The
    FAQ accordion's ref CSS declares `.faqs button[data-open=true]` (the open
    pill highlight) as the only button-subject candidate, so every one of the 9
    captured-closed items opened on scroll: all answers expanded and every pill
    painted the open-state `--highlight` lime. `aria-expanded` marks the node as
    a user-toggled control, so its captured state is emitted verbatim.
    """
    ref = _ref(tmp_path, [{
        "tag": "button", "class": "faq_btn",
        "aria-expanded": "false",
        "data-open": "false",
        "children": [{
            "tag": "div", "class": "faq_item",
            "children": [{"tag": "p", "class": "", "text": "answer body"}],
        }],
    }])
    css_dir = ref / "css"
    css_dir.mkdir()
    (css_dir / "ref.css").write_text(
        ".faqs button[data-open=true]{background-color:#e8f77f}\n"
        ".faqs button[data-open=true] .faq_item p{grid-template-rows:1fr}\n"
        ".faqs button[data-open=false] .faq_item p{grid-template-rows:0fr}\n",
        encoding="utf-8",
    )
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _blob(impl)
    assert 'data-open="false"' in blob, blob
    assert "data-ui-clone-state-reveal" not in blob, blob


def test_disclosure_control_only_exempts_click_owned_boolean_attr(tmp_path: Path) -> None:
    """A disclosure button can still carry independent viewport reveal state."""
    ref = _ref(tmp_path, [{
        "tag": "button", "class": "faq_btn",
        "aria-expanded": "false",
        "data-open": "false",
        "data-in-view": "false",
        "children": [{"tag": "span", "class": "", "text": "Question"}],
    }])
    css_dir = ref / "css"
    css_dir.mkdir()
    (css_dir / "ref.css").write_text(
        ".faq_btn[data-open=true]{background-color:#e8f77f}\n"
        ".faq_btn[data-in-view=true]{opacity:1;transform:none}\n"
        ".faq_btn[data-in-view=false]{opacity:0;transform:translateY(24px)}\n",
        encoding="utf-8",
    )
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _blob(impl)
    assert 'data-open="false"' in blob, blob
    assert 'data-in-view="false"' not in blob, blob
    assert 'data-ui-clone-state-reveal="data-in-view=true"' in blob, blob


def test_split_text_word_gap_span_renders_its_space(tmp_path: Path) -> None:
    """H word-gap collapse (realfood-v4 solution-solvable / container).

    Split-text libraries emit a dedicated childless whitespace element per word
    gap. Once capture preserves it (extract-dom records U+00A0 for a whitespace
    -only leaf that measures non-zero), the emitter must paint it: the gap span
    is a flex item, and JSX whitespace BETWEEN flex items is not rendered, so
    only the element's own text can restore the gap.

    Emitting the character LITERALLY is not enough. SWC — the JSX transform
    Next.js uses — trims a whitespace-only JSX text child using Rust's
    `char::is_whitespace`, which follows Unicode White_Space and therefore eats
    U+00A0 too. Measured on the realfood-v4 impl: `<span> </span>` compiled to a
    span with textContent "" and width 0, and the heading rendered
    "RealFoodcan". The gap must therefore be an escaped string EXPRESSION,
    which no JSX transform touches, plus `white-space: pre` so a block box does
    not collapse a plain space back to zero advance width.
    """
    blob = _emit(tmp_path, {
        "tag": "div", "class": "line",
        "styles": {"display": "flex"},
        "children": [
            {"tag": "span", "class": "word", "text": "Real", "children": []},
            {"tag": "span", "class": "", "text": " ", "wsAfter": True,
             "children": []},
            {"tag": "span", "class": "word", "text": "Food", "children": []},
        ],
    })
    assert '{"\\u00a0"}' in blob, blob
    assert "\u00a0</span>" not in blob, (
        "a bare whitespace text child is trimmed away by the JSX transform")
    assert "whiteSpace" in blob, blob
    # The gap is carried ONCE, by the span's own text — wsAfter must not
    # also append {' '} or inline-flow parents render a double word gap.
    assert "{' '}" not in blob, blob


def test_extract_dom_preserves_measured_whitespace_only_leaf() -> None:
    # Capture-side lockstep for H: the emitter can only paint the word gap if
    # extract-dom stops trimming the whitespace-only text node away. directText
    # ends in .trim(), so `<span> </span>` used to survive as an empty element
    # and the clone rendered "RealFoodcan". The non-zero computed width is the
    # guard — whitespace the ref itself collapses must stay empty.
    body = (ROOT / "skills" / "visual-debug" / "scripts" / "lib" / "extract-dom.js").read_text(
        encoding="utf-8")
    assert "/^\\s+$/.test(el.textContent || '')" in body
    assert "parseFloat(s.width) > 0" in body
    assert "out.text = '\\u00a0';" in body


def test_extract_dom_captures_allow_and_generic_data_attrs() -> None:
    # Capture-side lockstep: the emitter fixes above are dead code unless
    # extract-dom actually records the attributes. ATTR_KEYS must carry
    # allow/allowfullscreen (A) and a generic data-* capture loop must exist
    # (B) — the structure tests can't exercise the browser-eval JS directly.
    body = (ROOT / "skills" / "visual-debug" / "scripts" / "lib" / "extract-dom.js").read_text(
        encoding="utf-8")
    attr_keys_line = next(
        ln for ln in body.splitlines() if "const ATTR_KEYS" in ln)
    assert "'allow'" in attr_keys_line
    assert "'allowfullscreen'" in attr_keys_line
    assert "'aria-haspopup'" in attr_keys_line
    assert "'aria-expanded'" in attr_keys_line
    assert "'width'" in attr_keys_line
    assert "'height'" in attr_keys_line
    assert "nm.startsWith('data-')" in body


def test_structural_keys_never_emitted_as_attrs(tmp_path: Path) -> None:
    blob = _emit(tmp_path, {
        "tag": "svg", "class": "c", "svg": True,
        "display": "block", "position": "static",
        "children": [
            {"tag": "path", "class": "", "svg": True, "d": "M0 0Z",
             "wsAfter": True, "children": []},
        ],
    })
    assert 'display="' not in blob
    assert 'position="' not in blob
    assert "wsAfter" not in blob


def test_img_intrinsic_attrs_preserved_from_markup_attrs(tmp_path: Path) -> None:
    """F intrinsic-size drop (realfood-v4 broken_system_image).

    extract-dom records the literal width/height attributes. The generator must
    preserve that direct markup evidence instead of inferring attrs from CSS.
    """
    blob = _emit(tmp_path, {
        "tag": "img", "class": "shot", "src": "/images/a.webp",
        "width": "440", "height": "340",
        "display": "block", "position": "static",
        "styles": {"aspect-ratio": "auto 440 / 340", "max-width": "100%"},
        "children": [],
    })
    assert 'width="440"' in blob
    assert 'height="340"' in blob


def test_img_without_intrinsic_attrs_stays_unsized(tmp_path: Path) -> None:
    """A bare `aspect-ratio: auto` carries no markup size — invent nothing."""
    blob = _emit(tmp_path, {
        "tag": "img", "class": "shot", "src": "/images/a.webp",
        "display": "block", "position": "static",
        "styles": {"aspect-ratio": "auto"},
        "children": [],
    })
    assert 'width="' not in blob
    assert 'height="' not in blob


def test_img_css_authored_auto_aspect_ratio_does_not_invent_attrs(tmp_path: Path) -> None:
    """CSS can author `aspect-ratio:auto W/H`; without markup attrs it is not evidence."""
    blob = _emit(tmp_path, {
        "tag": "img", "class": "shot", "src": "/images/a.webp",
        "display": "block", "position": "static",
        "styles": {"aspect-ratio": "auto 16 / 9", "max-width": "100%"},
        "children": [],
    })
    assert 'width="16"' not in blob
    assert 'height="9"' not in blob
    assert 'width="' not in blob
    assert 'height="' not in blob
