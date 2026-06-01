from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ._helpers import (
    _project_root,
    _run_script,
)


def test_fix12_synthesis_drops_zero_height_wrappers() -> None:
    """Fix 12 — section-compare.sh synthesis must skip section-map entries
    with height < 50 (layout-only wrappers from pre-reveal capture). V8
    (d4b369d) measured ae_avg 509k partly because 5 zero-height wrappers
    were pixel-compared as catastrophic critical sections. The filter
    removes those from the synthesized ref-sections so AE reflects only
    real content rows.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")
    assert "_MIN_VISIBLE_HEIGHT" in text, (
        "section-compare.sh must define _MIN_VISIBLE_HEIGHT for Fix 12 filter"
    )
    assert "if h_raw < _MIN_VISIBLE_HEIGHT" in text or "h_raw < _MIN_VISIBLE_HEIGHT" in text, (
        "section-compare.sh must filter h_raw < _MIN_VISIBLE_HEIGHT entries"
    )
    # Safety: empty-output fallback (don't override with thin synthesis).
    assert "if len(out) < 3" in text, (
        "section-compare.sh must fall back to runtime enumeration when "
        "the filter removes too many sections"
    )



def test_entry_coherence_fail_on_coexisting_entries(tmp_path: Path) -> None:
    """Vite+Next entry coexistence must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "app").mkdir()
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"vite": "5", "@vitejs/plugin-react": "4", "react": "19"},
        "scripts": {"dev": "vite", "build": "vite build"},
    }))
    (impl / "vite.config.ts").write_text("export default {}")
    (impl / "src" / "main.tsx").write_text("createRoot(...).render(<App/>)")
    (impl / "app" / "page.tsx").write_text("export default function Page(){return null}")
    (impl / "index.html").write_text(
        '<html><body><div id="root"></div></body></html>',
    )
    proc = _run_script(
        "skills/visual-debug/scripts/entry-coherence-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "entry-coherence.json").read_text())
    assert art["status"] == "fail"
    assert any(v["kind"] == "coexisting-entry-points" for v in art["violations"])



def test_entry_coherence_pass_on_clean_vite_scaffold(tmp_path: Path) -> None:
    """Plain Vite scaffold must PASS."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"vite": "5", "@vitejs/plugin-react": "4", "react": "19"},
        "scripts": {"dev": "vite", "build": "vite build"},
    }))
    (impl / "vite.config.ts").write_text("export default {}")
    (impl / "src" / "main.tsx").write_text("createRoot(...).render(<App/>)")
    (impl / "index.html").write_text(
        '<!DOCTYPE html><html><body>'
        '<div id="root"></div><script type="module" src="/src/main.tsx"></script>'
        '</body></html>',
    )
    proc = _run_script(
        "skills/visual-debug/scripts/entry-coherence-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "entry-coherence.json").read_text())
    assert art["status"] == "pass", art



def test_scaffold_residue_fail_on_unused_components(tmp_path: Path) -> None:
    """5 unused PascalCase components must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    components = impl / "src" / "components"
    components.mkdir(parents=True)
    (impl / "src" / "main.tsx").write_text(
        "import { createRoot } from 'react-dom/client'\n"
        "createRoot(document.getElementById('root')!).render(<div>nothing</div>)\n",
    )
    for c in ("Hero", "Footer", "Nav", "Banner", "Card"):
        (components / f"{c}.tsx").write_text(
            f"export default function {c}(){{return <div>{c}</div>}}\n",
        )
    proc = _run_script(
        "skills/visual-debug/scripts/scaffold-residue-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "scaffold-residue.json").read_text())
    assert art["status"] == "fail"
    assert art["orphanCount"] == 5



def test_scaffold_residue_pass_on_used_components(tmp_path: Path) -> None:
    """Components referenced as JSX in App.tsx must PASS."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    components = impl / "src" / "components"
    components.mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        "import Hero from './components/Hero'\n"
        "import Footer from './components/Footer'\n"
        "export default function App(){return <><Hero/><Footer/></>}\n",
    )
    (components / "Hero.tsx").write_text(
        "export default function Hero(){return <h1>x</h1>}\n",
    )
    (components / "Footer.tsx").write_text(
        "export default function Footer(){return <p>y</p>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/scaffold-residue-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "scaffold-residue.json").read_text())
    assert art["status"] == "pass"
    assert art["orphanCount"] == 0



def test_scaffold_residue_pass_on_barrel_reexports(tmp_path: Path) -> None:
    """Components re-exported from index.ts barrels must NOT count as orphans."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    components = impl / "src" / "components"
    components.mkdir(parents=True)
    for c in ("Hero", "Footer", "Nav"):
        (components / f"{c}.tsx").write_text(
            f"export function {c}(){{return <div>{c}</div>}}\n",
        )
    (components / "index.ts").write_text(
        "export { Hero } from './Hero'\n"
        "export { Footer } from './Footer'\n"
        "export { Nav } from './Nav'\n",
    )
    (impl / "src" / "main.tsx").write_text(
        "import { createRoot } from 'react-dom/client'\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/scaffold-residue-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "scaffold-residue.json").read_text())
    # Re-exports from index.ts qualify as intentional public API surface.
    assert art["status"] == "pass", art



def test_css_mirror_fail_on_byte_copy(tmp_path: Path) -> None:
    """Impl CSS byte-identical to a ref bundle must FAIL."""
    ref = tmp_path / "ref"
    (ref / "bundles").mkdir(parents=True)
    css_body = "\n".join([f".class{i} {{ color: #{i:03x}; padding: {i}px; }}" for i in range(80)])
    (ref / "bundles" / "main.css").write_text(css_body)
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "index.css").write_text(css_body)
    proc = _run_script(
        "skills/visual-debug/scripts/css-mirror-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "css-mirror.json").read_text())
    assert art["status"] == "fail"
    assert any(v["kind"] == "byte-identical-copy" for v in art["violations"])



def test_css_mirror_pass_on_clean_impl(tmp_path: Path) -> None:
    """Impl with its own CSS must PASS."""
    ref = tmp_path / "ref"
    (ref / "bundles").mkdir(parents=True)
    (ref / "bundles" / "main.css").write_text(
        "\n".join([f".x{i} {{ color: #{i:03x}; }}" for i in range(80)]),
    )
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "index.css").write_text(
        ":root { --bg: white; }\n"
        "body { font-family: system-ui; margin: 0; }\n"
        ".btn { padding: 8px 16px; border-radius: 8px; }\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/css-mirror-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "css-mirror.json").read_text())
    assert art["status"] == "pass", art



def test_scaffold_warn_fail_on_placeholder(tmp_path: Path) -> None:
    """data-scaffold-warn placeholders must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <div>\n"
        '<section data-scaffold-warn="subtree-not-found-for-hero" />\n'
        '<section data-scaffold-warn="subtree-not-found-for-cta" />\n'
        "</div>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/scaffold-warn-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "scaffold-warn.json").read_text())
    assert art["status"] == "fail"
    sections = {w["section"] for w in art["warnings"]}
    assert sections == {"hero", "cta"}



def test_scaffold_warn_fail_on_non_ascii_section_name(tmp_path: Path) -> None:
    """Non-ASCII (Korean) section names must still trigger the placeholder gate."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <div>\n"
        '<section data-scaffold-warn="subtree-not-found-for-검색바" />\n'
        "</div>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/scaffold-warn-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "scaffold-warn.json").read_text())
    assert art["status"] == "fail"
    assert any(w["section"] == "검색바" for w in art["warnings"])



def test_invalidation_fail_on_stamp(tmp_path: Path) -> None:
    """A .invalidated stamp must hard-fail the gate."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / ".invalidated").write_text(json.dumps({
        "reason": "loop-9 cheated by overlaying ref screenshots",
        "markedAt": "2026-05-21",
        "markedBy": "operator",
    }))
    proc = _run_script(
        "skills/visual-debug/scripts/invalidation-check.sh", str(ref),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "invalidation.json").read_text())
    assert art["status"] == "fail"
    assert "loop-9" in art["reason"]



def test_invalidation_pass_without_stamp(tmp_path: Path) -> None:
    """No stamp → gate passes."""
    ref = tmp_path / "ref"
    ref.mkdir()
    proc = _run_script(
        "skills/visual-debug/scripts/invalidation-check.sh", str(ref),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "invalidation.json").read_text())
    assert art["status"] == "pass"
    assert art["stampPresent"] is False



def test_html_paste_fail_on_high_structural_similarity(tmp_path: Path) -> None:
    """index.html with >=70% tag-multiset match to dom-scaffold must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {"tag": "html", "children": [
            {"tag": "body", "children": [
                {"tag": "header", "children": [
                    {"tag": "nav", "children": [
                        {"tag": "ul", "children": [
                            {"tag": "li"}, {"tag": "li"}, {"tag": "li"},
                        ]},
                    ]},
                ]},
                {"tag": "main", "children": [
                    {"tag": "section", "children": [
                        {"tag": "h1"}, {"tag": "p"}, {"tag": "img"},
                    ]},
                    {"tag": "section", "children": [
                        {"tag": "h2"}, {"tag": "p"}, {"tag": "video"},
                    ]},
                    {"tag": "section", "children": [
                        {"tag": "h2"}, {"tag": "ul", "children": [
                            {"tag": "li"}, {"tag": "li"},
                        ]},
                    ]},
                ]},
                {"tag": "footer", "children": [
                    {"tag": "div"}, {"tag": "div"},
                ]},
            ]},
        ]},
    }))
    impl = tmp_path / "impl"
    impl.mkdir()
    (impl / "index.html").write_text(
        "<html><body>"
        "<header><nav><ul><li>a</li><li>b</li><li>c</li></ul></nav></header>"
        "<main>"
        "<section><h1>Hero</h1><p>copy</p><img /></section>"
        "<section><h2>S2</h2><p>x</p><video /></section>"
        "<section><h2>S3</h2><ul><li>i1</li><li>i2</li></ul></section>"
        "</main>"
        "<footer><div /><div /></footer>"
        "</body></html>",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/html-paste-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "html-paste.json").read_text())
    assert art["status"] == "fail"
    assert any(
        v["kind"] == "structural-similarity-to-scaffold" for v in art["violations"]
    )



def test_html_paste_pass_on_vite_mount_only(tmp_path: Path) -> None:
    """Plain Vite mount file must PASS even when ref scaffold has rich shape."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {"tag": "html", "children": [
            {"tag": "body", "children": [
                {"tag": c} for c in [
                    "header", "nav", "main", "section", "section",
                    "section", "h1", "h2", "p", "img", "footer",
                ]
            ]},
        ]},
    }))
    impl = tmp_path / "impl"
    impl.mkdir()
    (impl / "index.html").write_text(
        '<!DOCTYPE html><html><head><title>App</title></head>'
        '<body><div id="root"></div>'
        '<script type="module" src="/src/main.tsx"></script>'
        "</body></html>",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/html-paste-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "html-paste.json").read_text())
    assert art["status"] == "pass", art



def test_monolithic_impl_fail_on_packed_app_jsx(tmp_path: Path) -> None:
    """Single 15KB App.jsx with 0 components must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 12, "hasFooter": True, "hasHeader": True,
        "sections": [{"index": i} for i in range(12)],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"react": "19", "vite": "5"},
    }))
    (impl / "src" / "App.jsx").write_text(
        "export default function App() {\n  return <>"
        + ("<section>" + "x" * 800 + "</section>") * 18  # ~14KB packed
        + "</>;\n}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/monolithic-impl-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "monolithic-impl.json").read_text())
    assert art["status"] == "fail"
    assert art["componentCount"] == 0



def test_monolithic_impl_pass_on_componentized(tmp_path: Path) -> None:
    """Componentized impl with 5 PascalCase children must PASS."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 12, "hasFooter": True, "hasHeader": True,
        "sections": [{"index": i} for i in range(12)],
    }))
    impl = tmp_path / "impl"
    components = impl / "src" / "components"
    components.mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"react": "19", "vite": "5"},
    }))
    (impl / "src" / "App.jsx").write_text(
        "import Hero from './components/Hero'\n"
        "export default function App(){return <Hero/>}\n",
    )
    for c in ("Hero", "Footer", "Nav", "Banner", "Card"):
        (components / f"{c}.jsx").write_text(
            f"export default function {c}(){{return <div/>}}\n",
        )
    proc = _run_script(
        "skills/visual-debug/scripts/monolithic-impl-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "monolithic-impl.json").read_text())
    assert art["status"] == "pass"
    assert art["componentCount"] == 5



def test_extract_styles_aggregates_structure_into_scaffold_input(tmp_path: Path) -> None:
    """extract-styles.sh must walk structure.json's per-node `styles` dicts
    and emit a tag/class-keyed aggregate using dom-scaffold's STYLE_KEYS
    shorthand (bg / ff / fs / fw / ...). Settles each (key, shortkey) to
    the modal value across all matching nodes.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "main",
        "class": "page",
        "styles": {
            "display": "flex",
            "background-color": "rgb(255, 255, 255)",
            "font-family": "Inter",
        },
        "children": [
            {
                "tag": "h1",
                "class": "hero-title big",
                "styles": {
                    "font-size": "48px",
                    "font-weight": "700",
                    "color": "rgb(20, 20, 20)",
                },
                "children": [],
            },
            {
                "tag": "h1",
                "class": "hero-title small",
                "styles": {
                    "font-size": "48px",
                    "font-weight": "700",
                    "color": "rgb(20, 20, 20)",
                },
                "children": [],
            },
            {
                "tag": "section",
                "class": "stats",
                "styles": {
                    "background-image": "linear-gradient(rgb(0,0,0), rgb(50,50,50))",
                    "padding": "64px 24px",
                },
                "children": [],
            },
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "extract-styles.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads((ref / "styles.json").read_text())
    # Per-tag aggregate
    assert out["main"]["display"] == "flex"
    assert out["main"]["bg"] == "rgb(255, 255, 255)"
    assert out["main"]["ff"] == "Inter"
    # background-image must win over background-color when both present.
    assert out["section"]["bg"].startswith("linear-gradient"), out["section"]
    # Per-first-class aggregate carries typographic + color/bg only.
    assert out[".page"]["bg"] == "rgb(255, 255, 255)"
    assert out[".hero-title"]["fs"] == "48px"
    assert out[".hero-title"]["fw"] == "700"
    # Structural keys at the class level would stamp dominant width/padding
    # onto exceptional instances (Codex 2026-05-22 review Q1). dom-scaffold
    # reads structural per-node from structure.json directly instead.
    assert "padding" not in out.get(".stats", {})
    assert "width" not in out.get(".page", {})
    assert "height" not in out.get(".hero-title", {})
    # Noise values (rgba(0,0,0,0), "none", "normal", "0px", empty) are dropped.
    for entry in out.values():
        for v in entry.values():
            assert v.strip(), f"empty style value leaked: {entry}"
            assert v.lower() not in {"none", "normal", "auto", "0px", "rgba(0, 0, 0, 0)"}



def test_extract_styles_errors_when_structure_missing(tmp_path: Path) -> None:
    """extract-styles.sh must refuse to run without structure.json instead
    of writing an empty styles.json that would silently pass dom-scaffold's
    existence check.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "extract-styles.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert not (ref / "styles.json").exists()



def test_dom_scaffold_prefers_per_node_styles_over_class_aggregate(tmp_path: Path) -> None:
    """Two `.card` instances exist on the page: a small catalog card (320px
    wide) and an exceptional hero card (800px wide). The class-level
    aggregate's modal width would be 320 if the small one is repeated. But
    Phase 4 must render the hero at its real per-node width.

    Codex 2026-05-22 review (Q1): dom-scaffold's walk() now reads per-node
    styles from structure.json directly and only falls back to the class
    aggregate for keys not captured per-node. Without this fix the hero
    silently inherits 320px and Phase 4 generates the wrong layout.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "main",
        "class": "page",
        "styles": {"display": "flex"},
        "children": [
            {
                "tag": "div",
                "class": "card hero",
                "styles": {"width": "800px", "padding": "64px"},
                "children": [],
            },
            {
                "tag": "div",
                "class": "card small",
                "styles": {"width": "320px", "padding": "16px"},
                "children": [],
            },
            {
                "tag": "div",
                "class": "card small",
                "styles": {"width": "320px", "padding": "16px"},
                "children": [],
            },
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure))
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 1,
        "hasFooter": False,
        "hasHeader": False,
        "sections": [{
            "index": 0, "tag": "main", "className": "page", "id": None,
            "role": None, "height": 1000, "top": 0, "childCount": 3,
            "textPreview": "",
        }],
    }))
    # Produce styles.json via the real extract-styles.sh — this confirms
    # the class-level structural carve-out is in effect.
    proc = subprocess.run(
        ["bash", str(_project_root() / "skills" / "visual-debug" / "scripts" / "extract-styles.sh"),
         str(ref)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # Run dom-scaffold.
    proc = subprocess.run(
        ["bash", str(_project_root() / "skills" / "visual-debug" / "scripts" / "dom-scaffold.sh"),
         str(ref)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    scaffold = json.loads((ref / "dom-scaffold.json").read_text())

    # Find the per-node entries in the global tree. Order matches structure.json.
    tree = scaffold.get("tree", {})
    children = tree.get("children", [])
    assert len(children) == 3, f"expected 3 cards, got {len(children)}: {children}"

    hero_node = children[0]
    small_node = children[1]

    # Hero must keep its exceptional 800px width — per-node wins over any
    # aggregate fallback.
    assert hero_node["styles"]["width"] == "800px", hero_node
    assert hero_node["styles"]["padding"] == "64px", hero_node
    # Small node carries its own 320px.
    assert small_node["styles"]["width"] == "320px", small_node
    assert small_node["styles"]["padding"] == "16px", small_node


def test_dom_scaffold_preserves_text_past_depth_cap(tmp_path: Path) -> None:
    """dom-scaffold caps tree DEPTH at 8 to keep the prompt small, but it must
    NEVER drop TEXT past the cap. Verbatim text is the fidelity ground truth;
    truncating it forces the generator to omit/fabricate copy. Real sites nest
    text below depth 8 (observed: a clone whose structure.json carried 232 text
    leaves to depth 14 produced a scaffold with only 49 — ~79% of the copy
    truncated, the dominant cause of missing clone content). Past the cap,
    text-bearing nodes must still be carried (lightweight: tag + text, no heavy
    styles dict)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    DEEP_TEXT = "Deep nested copy that must survive the depth cap"
    SHALLOW_TEXT = "Shallow headline within the cap"
    # span carrying DEEP_TEXT sits at depth 12 (> cap 8) under 11 wrappers.
    node: dict = {"tag": "span", "text": DEEP_TEXT, "children": []}
    for _ in range(11):
        node = {"tag": "div", "class": "wrap",
                "styles": {"display": "block"}, "children": [node]}
    structure: dict = {
        "tag": "main", "class": "page", "styles": {"display": "flex"},
        "children": [
            {"tag": "h1", "text": SHALLOW_TEXT,
             "styles": {"font-size": "96px"}, "children": []},
            node,
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure))
    (ref / "styles.json").write_text(json.dumps({}))
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 1, "hasFooter": False, "hasHeader": False,
        "sections": [{"index": 0, "tag": "main", "className": "page",
                      "id": None, "role": None, "height": 1000, "top": 0,
                      "childCount": 2, "textPreview": ""}],
    }))
    proc = subprocess.run(
        ["bash", str(_project_root() / "skills" / "visual-debug" / "scripts" / "dom-scaffold.sh"),
         str(ref)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = (ref / "dom-scaffold.json").read_text()
    assert SHALLOW_TEXT in blob, "shallow (within-cap) text must be present"
    assert DEEP_TEXT in blob, (
        "deep text at depth 12 (> cap 8) must survive the depth cap — "
        f"verbatim text is never truncated. scaffold head: {blob[:400]}"
    )


def test_dom_scaffold_deep_text_carries_typography_subset(tmp_path: Path) -> None:
    """RANK-4 fix: past the depth-8 cap, text-bearing nodes are emitted
    lightweight (Fix 20) — but the lightweight path dropped ALL styles, so deep
    leaf text arrived with no font-size/weight/color and the generator
    freehanded typography for every deep text node. The skill says "copy the
    measured px/weight for ALL text."

    Fix is ADDITIVE: the lightweight branch now ALSO attaches a small TYPOGRAPHY
    subset (fs/fw/color/lh/ls) from the node's per-node styles — but must STILL
    drop heavy structural props (box-shadow/border/etc.) past the cap so the cap
    keeps protecting the prompt budget.

    RED before the fix (deep node has no styles at all); GREEN after (deep node
    carries fs/fw/color but NOT box-shadow).
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    DEEP_TEXT = "Deep typographic copy that needs its real px/weight"
    # span at depth 12 (> cap 8) carries both typography AND a heavy structural
    # prop. The structural prop must be dropped; the typography must survive.
    node: dict = {
        "tag": "span",
        "text": DEEP_TEXT,
        "styles": {
            "font-size": "24px",
            "font-weight": "700",
            "color": "rgb(10, 10, 10)",
            "line-height": "32px",
            "letter-spacing": "0.5px",
            "box-shadow": "rgba(0, 0, 0, 0.3) 0px 4px 12px 0px",
            "border": "1px solid rgb(20, 20, 20)",
        },
        "children": [],
    }
    for _ in range(11):
        node = {"tag": "div", "class": "wrap",
                "styles": {"display": "block"}, "children": [node]}
    structure: dict = {
        "tag": "main", "class": "page", "styles": {"display": "flex"},
        "children": [node],
    }
    (ref / "structure.json").write_text(json.dumps(structure))
    (ref / "styles.json").write_text(json.dumps({}))
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 1, "hasFooter": False, "hasHeader": False,
        "sections": [{"index": 0, "tag": "main", "className": "page",
                      "id": None, "role": None, "height": 1000, "top": 0,
                      "childCount": 1, "textPreview": ""}],
    }))
    proc = subprocess.run(
        ["bash", str(_project_root() / "skills" / "visual-debug" / "scripts" / "dom-scaffold.sh"),
         str(ref)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    scaffold = json.loads((ref / "dom-scaffold.json").read_text())

    def find_text_node(n: dict) -> dict | None:
        if not isinstance(n, dict):
            return None
        if n.get("text") == DEEP_TEXT:
            return n
        for c in n.get("children", []) or []:
            hit = find_text_node(c)
            if hit is not None:
                return hit
        return None

    deep = find_text_node(scaffold["tree"])
    assert deep is not None, "deep text node must survive the depth cap (Fix 20)"
    styles = deep.get("styles", {})
    # Typography subset must reach the deep node verbatim.
    assert styles.get("fs") == "24px", styles
    assert styles.get("fw") == "700", styles
    assert styles.get("color") == "rgb(10, 10, 10)", styles
    assert styles.get("lh") == "32px", styles
    assert styles.get("ls") == "0.5px", styles
    # Heavy structural props must STILL be dropped past the cap.
    assert "box-shadow" not in styles, styles
    assert "border" not in styles, styles


def test_dom_scaffold_carries_fidelity_critical_props(tmp_path: Path) -> None:
    """RANK-1 fix: extract-dom.sh captures the fidelity-critical CSS props
    (box-shadow, text-align, z-index, border-radius, justify-content, gap, …)
    into structure.json, but the scaffold dropped all of them — so the
    generator (which reads dom-scaffold.json as source of truth) never saw
    them and freehanded shadows / alignment / radius. The scaffold node MUST
    now carry these keys, taken per-node from structure.json (per-node wins).
    RED before the fix (dropped), GREEN after.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "main",
        "class": "page",
        "styles": {"display": "flex"},
        "children": [
            {
                "tag": "div",
                "class": "card",
                "styles": {
                    "box-shadow": "rgba(0, 0, 0, 0.25) 0px 8px 24px 0px",
                    "text-align": "center",
                    "z-index": "75",
                    "border-radius": "16px",
                    "justify-content": "space-between",
                    "align-items": "center",
                    "gap": "24px",
                    "transform": "matrix(1, 0, 0, 1, 0, -8)",
                    "overflow": "hidden",
                    "border": "1px solid rgb(20, 20, 20)",
                },
                "children": [],
            },
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure))
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 1, "hasFooter": False, "hasHeader": False,
        "sections": [{"index": 0, "tag": "main", "className": "page",
                      "id": None, "role": None, "height": 1000, "top": 0,
                      "childCount": 1, "textPreview": ""}],
    }))
    scripts = _project_root() / "skills" / "visual-debug" / "scripts"
    proc = subprocess.run(
        ["bash", str(scripts / "extract-styles.sh"), str(ref)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = subprocess.run(
        ["bash", str(scripts / "dom-scaffold.sh"), str(ref)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    scaffold = json.loads((ref / "dom-scaffold.json").read_text())
    card = scaffold["tree"]["children"][0]
    styles = card.get("styles", {})
    # Every fidelity-critical prop captured into structure.json must reach the
    # scaffold node verbatim (per-node value wins over any class aggregate).
    assert styles.get("box-shadow") == "rgba(0, 0, 0, 0.25) 0px 8px 24px 0px", styles
    assert styles.get("text-align") == "center", styles
    assert styles.get("z-index") == "75", styles
    assert styles.get("border-radius") == "16px", styles
    assert styles.get("justify-content") == "space-between", styles
    assert styles.get("align-items") == "center", styles
    assert styles.get("gap") == "24px", styles
    assert styles.get("transform") == "matrix(1, 0, 0, 1, 0, -8)", styles
    assert styles.get("overflow") == "hidden", styles
    assert styles.get("border") == "1px solid rgb(20, 20, 20)", styles


def test_dom_scaffold_section_descriptor_uses_own_node_styles(tmp_path: Path) -> None:
    """RANK-3 fix: a section descriptor's `styles` block must come from the
    section's OWN root node in structure.json (per-node wins), not from the
    tag/class aggregate.

    extract-styles.sh strips structural keys (padding/display/margin/...) from
    per-CLASS buckets and keeps them only in the per-TAG bucket, so the tag
    aggregate resolves to the page-MODAL value across all elements of that tag.
    With three sections at padding 20px and one at 120px, the modal section
    padding is 20px — and the old descriptor builder (resolve_styles on the
    tag/class aggregate alone) stamped 20px on EVERY section, losing section
    A's real 120px. The fix reads each section's own root node from
    structure.json and lets per-node values win, with the aggregate kept only
    as a gap-filler. RED before (gets modal 20px), GREEN after (gets own 120px).
    """
    ref = tmp_path / "ref"
    ref.mkdir()

    def section(cls: str, pad: str, extra: dict | None = None) -> dict:
        st = {"display": "block", "padding": pad}
        if extra:
            st.update(extra)
        return {
            "tag": "section",
            "class": cls,
            "styles": st,
            "children": [{"tag": "p", "text": f"copy {cls}",
                          "styles": {"color": "rgb(0, 0, 0)"}, "children": []}],
        }

    structure = {
        "tag": "body",
        "class": "page",
        "styles": {"display": "flex"},
        "children": [
            # Section A: the exceptional section — 120px padding + grid + shadow.
            section("sec-a", "120px",
                    {"display": "grid",
                     "box-shadow": "rgba(0, 0, 0, 0.3) 0px 4px 12px 0px"}),
            # Sections B/C/D: 20px padding (modal by repetition) + block display.
            section("sec-b", "20px"),
            section("sec-c", "20px"),
            section("sec-d", "20px"),
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure))
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 4, "hasFooter": False, "hasHeader": False,
        "sections": [
            {"index": 0, "tag": "section", "className": "sec-a", "id": None,
             "role": None, "height": 400, "top": 0, "childCount": 1,
             "textPreview": "copy sec-a"},
            {"index": 1, "tag": "section", "className": "sec-b", "id": None,
             "role": None, "height": 400, "top": 400, "childCount": 1,
             "textPreview": "copy sec-b"},
            {"index": 2, "tag": "section", "className": "sec-c", "id": None,
             "role": None, "height": 400, "top": 800, "childCount": 1,
             "textPreview": "copy sec-c"},
            {"index": 3, "tag": "section", "className": "sec-d", "id": None,
             "role": None, "height": 400, "top": 1200, "childCount": 1,
             "textPreview": "copy sec-d"},
        ],
    }))
    scripts = _project_root() / "skills" / "visual-debug" / "scripts"
    # Real extract-styles.sh — confirms the class-level structural carve-out is
    # in effect so the section-tag aggregate genuinely resolves to modal 20px.
    proc = subprocess.run(
        ["bash", str(scripts / "extract-styles.sh"), str(ref)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = subprocess.run(
        ["bash", str(scripts / "dom-scaffold.sh"), str(ref)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    scaffold = json.loads((ref / "dom-scaffold.json").read_text())
    sections = {s["id"]: s for s in scaffold.get("sections", [])}

    # Sanity: the tag aggregate really is the modal 20px (the bug source).
    styles_map = json.loads((ref / "styles.json").read_text())
    tag_pad = styles_map.get("section", {}).get("padding")
    assert tag_pad == "20px", (
        f"precondition: section-tag aggregate padding should be modal 20px, "
        f"got {tag_pad!r}"
    )

    sec_a = sections["sec-a"]
    # Section A's descriptor must carry its OWN 120px padding, not the modal 20px.
    assert sec_a["styles"].get("padding") == "120px", (
        f"section A descriptor must read its own root-node padding (120px), "
        f"not the page-modal aggregate (20px): {sec_a['styles']}"
    )
    # Its own display (grid) and shadow must win over the aggregate too.
    assert sec_a["styles"].get("display") == "grid", sec_a["styles"]
    assert sec_a["styles"].get("box-shadow") == \
        "rgba(0, 0, 0, 0.3) 0px 4px 12px 0px", sec_a["styles"]
    # Modal sections keep their own 20px.
    assert sections["sec-b"]["styles"].get("padding") == "20px", sections["sec-b"]
    # Per-section padding must now VARY, not be one stamped value.
    pads = {sid: s["styles"].get("padding") for sid, s in sections.items()}
    assert len(set(pads.values())) >= 2, (
        f"section paddings must vary per-section, not be uniformly stamped: {pads}"
    )

