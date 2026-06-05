from __future__ import annotations

import json
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert "Real Food Wins" in blob  # mapped section (sanity)
    assert "Get Involved" in blob, "uncovered header/nav text must not be dropped"


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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "lib" / "SmoothScroll.tsx").exists()


def _app_tsx(impl: Path) -> str:
    p = impl / "src" / "App.tsx"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SmoothScroll" not in _app_tsx(impl)


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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    # collapsed to contiguous text (would be split across spans before the fix)
    assert "For decades we have been misled by guidance" in blob


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
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    assert blob.count("<a ") >= 12, "nav links must be preserved, not collapsed to text"


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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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


def test_section_root_height_bakes_negative_bottom_margin(tmp_path: Path) -> None:
    """S1 (dominant case): realfood's `dga_solvable_problem` is itself a section
    (relative, not sticky), so its 2700px box comes from the section root's own
    height→min-height floor — NOT the Fix 26 wrapper. It carries margin
    0 0 -675px to overlap the next section. Keeping height 2700 while the margin
    still pulls siblings up makes the box ~800px taller than the ref's real
    section (the dominant 'sections drift'). The floor must fold in the negative
    bottom margin (2700-675=2025) and the bottom margin must be neutralised so
    the next section's flow position is unchanged while the box matches its real
    rendered height."""
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
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    import re as _re
    m = _re.search(r'className="solvable"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert m, f"solvable section must be emitted; got:\n{blob}"
    style = m.group(1)
    assert 'minHeight: "2025px"' in style, f"floor must fold negative margin (2700-675); got:\n{style}"
    assert 'minHeight: "2700px"' not in style, "must not keep the inflated captured height"
    # bottom margin neutralised so the next section does not move up an extra 675.
    assert 'marginBottom: "0px"' in style, f"bottom margin must be neutralised; got:\n{style}"


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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
            capture_output=True, text=True, timeout=30, env=env,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
    so the clone scales like the ref. Non-multiples (hero 638px) stay px; with
    no viewport record nothing converts."""
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
            # 300vh track w/ -75vh overlap -> Fix 64 folds to 2025px -> 225vh
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
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _impl_blob(impl)
    import re as _re
    solv = _re.search(r'className="solvable"[^>]*style=\{\{([^}]*)\}\}', blob)
    assert solv and 'minHeight: "225vh"' in solv.group(1), (
        f"folded 2025px @900 viewport must emit 225vh; got:\n{solv.group(1) if solv else blob}"
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
        capture_output=True, text=True, timeout=30,
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
                   capture_output=True, text=True, timeout=30)
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
    is a frozen scroll-zoom/entrance initial — reset transform to rest (scale 1)
    so the element is not stuck shrunk (realfood's dga_card_bg zoom background).
    Up-scale and scale+translate are preserved (conservative)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body", "class": "antialiased", "styles": {},
        "children": [
            {"tag": "section", "class": "zoom", "styles": {}, "children": [
                {"tag": "div", "class": "cardbg", "styles": {
                    "transform": "matrix(0.9, 0, 0, 0.9, 0, 0)", "position": "absolute"}},
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
                          capture_output=True, text=True, timeout=30)
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
                   capture_output=True, text=True, timeout=30)
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
                # #3 frozen zoom: sub-unity scale -> stripped AND stamped
                {"tag": "div", "class": "zoombg", "styles": {
                    "transform": "matrix(0.9, 0, 0, 0.9, 0, 0)", "position": "absolute"}},
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
                   capture_output=True, text=True, timeout=30)
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
                "transform": "matrix(0.9, 0, 0, 0.9, 0, 0)", "position": "absolute"},
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
                   capture_output=True, text=True, timeout=30)
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
                   capture_output=True, text=True, timeout=30)
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
                   capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "stack=next" in proc.stdout, f"expected next stack, got: {proc.stdout}"
    page = next(impl.rglob("page.tsx"))
    page_src = page.read_text(encoding="utf-8")
    assert "import ScrollStateDriver" in page_src and "<ScrollStateDriver />" in page_src, (
        "Next page must mount the driver so stamped draw-in/fade elements animate")
    driver = next(impl.rglob("ScrollStateDriver.tsx"))
    assert driver.read_text(encoding="utf-8").lstrip().startswith("'use client'"), (
        "ScrollStateDriver runs useEffect → must be a client component on Next")
