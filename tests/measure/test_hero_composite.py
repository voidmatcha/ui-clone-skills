from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ._helpers import (
    _project_root,
)


def test_hero_composite_check_passes_when_impl_has_all_kinds(tmp_path: Path) -> None:
    """User direction A + hero-composite gate (2026-05-22): when ref hero
    has all 4 kinds (video, button, h1/h2, label) and impl Hero component
    contains them all (with button-video proximity satisfied), PASS.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src" / "components"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "div",
            "children": [
                {"tag": "section", "class": "dga_hero__X",
                 "children": [
                     {"tag": "h1", "text": "Real Food Wins", "children": []},
                     {"tag": "span", "class": "hero-video__label", "children": []},
                 ]},
                # Sibling hero-video container — matches realfood.gov layout
                # where the video is a sibling, not descendant, of the hero
                # section. The check collects ALL hero-named subtrees.
                {"tag": "div", "class": "dga_hero_video__Y",
                 "children": [
                     {"tag": "video", "src": "/video/hero.mp4", "children": []},
                     {"tag": "button", "class": "hero-video", "children": []},
                 ]},
            ],
        }],
    }))
    (src / "Hero.tsx").write_text(
        'export function Hero() {\n'
        '  return (\n'
        '    <section data-section="hero">\n'
        '      <video src="/video/hero.mp4" />\n'
        '      <button className="hero-video">\n'
        '        <span className="hero-video__label">Play</span>\n'
        '      </button>\n'
        '      <h1>Real Food Wins</h1>\n'
        '    </section>\n'
        '  );\n'
        '}\n'
    )

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "hero-composite-check.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "hero-composite.json").read_text())
    assert art["status"] == "pass", art
    assert art["ref"]["video"] and art["ref"]["button"] and art["ref"]["h1OrH2"] and art["ref"]["label"], art
    assert art["impl"]["video"] and art["impl"]["button"] and art["impl"]["h1OrH2"] and art["impl"]["label"], art
    assert not art["missingInImpl"], art



def test_hero_composite_check_fails_when_impl_drops_overlay_button(tmp_path: Path) -> None:
    """Core failure mode across 17 codex iterations: LLM flattens ref's
    4-layer hero composite into 2 layers, dropping the overlay button
    + label. Gate must FAIL when ref has video+button but impl only has
    video+h1.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src" / "components"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero",
             "children": [
                 {"tag": "video", "children": []},
                 {"tag": "button", "class": "hero-video", "children": []},
                 {"tag": "span", "class": "hero-video__label", "children": []},
                 {"tag": "h1", "text": "Title", "children": []},
             ]},
        ],
    }))
    # Impl: only video + h1 (typical LLM flattening — button + label dropped).
    (src / "Hero.tsx").write_text(
        'export function Hero() {\n'
        '  return (<section><video /><h1>Title</h1></section>);\n'
        '}\n'
    )

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "hero-composite-check.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "hero-composite.json").read_text())
    assert art["status"] == "fail", art
    assert set(art["missingInImpl"]) >= {"button", "label"}, art



def test_hero_composite_check_rejects_navbar_button_via_proximity(tmp_path: Path) -> None:
    """Button proximity check (codex-rescue Q3): a `<button` only counts
    when there's a `<video` within 500 chars. Prevents a navbar button
    that happens to live in the same file from satisfying the button
    requirement.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero",
             "children": [
                 {"tag": "video", "children": []},
                 {"tag": "button", "children": []},
             ]},
        ],
    }))
    # Impl: video at top, then 800 chars of unrelated content, then a
    # navbar-style button. Proximity check should NOT count this button.
    padding = "  // unrelated content " + ("x" * 100) + "\n"
    (src / "Hero.tsx").write_text(
        'export function Hero() {\n'
        '  return (\n'
        '    <main>\n'
        '      <video />\n'
        + padding * 6 +
        '      <button>Sign In</button>\n'
        '    </main>\n'
        '  );\n'
        '}\n'
    )

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "hero-composite-check.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "hero-composite.json").read_text())
    assert "button" in art["missingInImpl"], (
        f"navbar button at >500 chars from video must not satisfy "
        f"the button requirement; got: {art}"
    )



def test_hero_composite_check_prefers_data_section_locator(tmp_path: Path) -> None:
    """Codex-rescue Q2: `data-section="hero"` is the strongest locator,
    even when the file name doesn't contain 'hero'. Verifies P1
    candidates win over P2 (file name) and P3 (any-video).
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "section", "class": "hero",
            "children": [
                {"tag": "video", "children": []},
                {"tag": "h1", "text": "T", "children": []},
            ],
        }],
    }))
    # File name does NOT contain 'hero' but has data-section="hero".
    (src / "Banner.tsx").write_text(
        'export function Banner() {\n'
        '  return (\n'
        '    <section data-section="hero">\n'
        '      <video />\n'
        '      <h1>T</h1>\n'
        '    </section>\n'
        '  );\n'
        '}\n'
    )

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "hero-composite-check.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "hero-composite.json").read_text())
    assert art["status"] == "pass", art
    assert any("Banner" in f for f in art["implCandidateFiles"]), art



def test_hero_composite_check_inventories_canvas_kind(tmp_path: Path) -> None:
    """FIX 2a addendum (rank235): a ref hero with a <canvas> (bare WebGL mount
    div) must be inventoried as the `canvas` kind, so an impl whose hero has no
    canvas FAILS with canvas listed in missingInImpl. Previously the kind set
    (video/button/h1|h2/span) omitted canvas, so a blank WebGL hero slipped by.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src" / "components"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "section", "class": "site_hero__X",
            "children": [
                {"tag": "canvas", "class": "hero-webgl", "children": []},
                {"tag": "h1", "text": "Shaders", "children": []},
            ],
        }],
    }))
    # Impl hero renders the heading but mounts no canvas.
    (src / "Hero.tsx").write_text(
        'export function Hero() {\n'
        '  return (\n'
        '    <section data-section="hero">\n'
        '      <h1>Shaders</h1>\n'
        '    </section>\n'
        '  );\n'
        '}\n'
    )
    script = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "hero-composite-check.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "hero-composite.json").read_text(encoding="utf-8"))
    assert artifact["ref"]["canvas"] is True
    assert artifact["impl"]["canvas"] is False
    assert "canvas" in artifact["missingInImpl"]
