"""scaffold-to-jsx auto-margin centering recovery (navercorp #5, "80px shift").

Author intent `margin: 0 auto` (horizontal centering of a max-width box) is
resolved by getComputedStyle at the capture viewport to fixed symmetric px
(navercorp @1440: `.header__inner{max-width:1408px;margin:0 auto}` → captured
`margin: 0px 80px`, width == max-width == 1280). The transpiler used to bake
that px as an inline margin, which OVERRIDES the imported ref CSS's `margin:0
auto` (inline wins the cascade) and freezes the box off-center at every width
but the capture width — content shifts ~80px at other viewports.

The transpiler now recovers centering when the element is provably a capped,
symmetrically-margined box: `max-width` present AND resolved `width` ≈
`max-width` (the box sits AT its cap) AND `margin-left` ≈ `margin-right` > 0.
That trio is the signature of `margin:0 auto` on a constrained box; it does NOT
fire for genuine fixed gutters (asymmetric), negative grid compensation
(symmetric but negative), or `margin:0 5%`-style symmetric margins on an
unconstrained (no max-width, or width != max-width) box.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _run(
    tmp_path: Path,
    sections: list[dict],
    *,
    root_styles: dict[str, str] | None = None,
) -> str:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    children = [{"tag": "section", "class": s["cls"], "styles": s["styles"],
                 "children": [{"tag": "p", "text": s["cls"]}]} for s in sections]
    (ref / "structure.json").write_text(
        json.dumps({
            "tag": "body",
            "class": "",
            "styles": root_styles or {},
            "children": children,
        }),
        encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": i, "tag": "section", "cls": s["cls"]} for i, s in enumerate(sections)]}),
        encoding="utf-8")
    (impl / "package.json").write_text('{"name":"i","dependencies":{}}', encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted((impl / "src" / "components").glob("*.tsx")))


def test_full_bleed_body_root_does_not_become_centered_container(tmp_path: Path) -> None:
    impl = tmp_path / "impl"
    _run(
        tmp_path,
        [{"cls": "container-xl", "styles": {
            "width": "1280px",
            "max-width": "1280px",
            "margin": "0px 80px",
        }}],
        root_styles={"width": "1280px"},
    )

    app = (impl / "src" / "App.tsx").read_text(encoding="utf-8")
    component = (
        impl / "src" / "components" / "ContainerXl.tsx"
    ).read_text(encoding="utf-8")

    root_open = next(line for line in app.splitlines() if line.strip().startswith("<div"))
    assert 'width: "100%"' in root_open
    assert "maxWidth" not in root_open
    assert "marginLeft" not in root_open
    assert "marginRight" not in root_open
    assert 'maxWidth: "1280px"' in component
    assert 'margin: "0px auto 0px auto"' in component


def test_capped_symmetric_margin_recovered_as_auto_centering(tmp_path: Path) -> None:
    # header__inner: width == max-width == 1280, symmetric 80px horizontal margin
    # → the 80px is centering slack, restore `auto` so the clone re-centers.
    blob = _run(tmp_path, [
        {"cls": "header__inner", "styles": {
            "width": "1280px", "max-width": "1280px", "margin": "0px 80px"}},
    ])
    assert 'margin: "0px auto 0px auto"' in blob, blob


def test_fractional_capped_margin_recovered(tmp_path: Path) -> None:
    # nav__list__inner: fractional 72.5px symmetric margin (a computed-auto
    # fingerprint) with width == max-width → recovered, vertical margin preserved.
    blob = _run(tmp_path, [
        {"cls": "nav__list__inner", "styles": {
            "width": "1280px", "max-width": "1280px", "margin": "10px 72.5px"}},
    ])
    assert 'margin: "10px auto 10px auto"' in blob, blob


def test_asymmetric_gutter_margin_preserved(tmp_path: Path) -> None:
    # nav__item: margin-right:32px gutter (asymmetric) is a real fixed margin,
    # NOT centering — must survive verbatim.
    blob = _run(tmp_path, [
        {"cls": "nav__item", "styles": {
            "width": "55px", "margin": "0px 32px 0px 0px"}},
    ])
    assert 'margin: "0px 32px 0px 0px"' in blob, blob
    assert "auto" not in blob.split('margin: "')[1].split('"')[0]


def test_symmetric_margin_without_maxwidth_preserved(tmp_path: Path) -> None:
    # a symmetric px margin on a box with NO max-width cap (e.g. `margin:0 5%`
    # resolved, or an authored fixed `margin:0 80px` full-width block) is NOT
    # centering slack — no width cap means auto could not have produced it.
    blob = _run(tmp_path, [
        {"cls": "plain", "styles": {"width": "1280px", "margin": "0px 80px"}},
    ])
    assert 'margin: "0px 80px"' in blob, blob


def test_symmetric_margin_width_below_cap_preserved(tmp_path: Path) -> None:
    # width < max-width (box not AT its cap) → auto-centering would resolve the
    # margin to 0, so a nonzero symmetric margin here is a real fixed margin.
    blob = _run(tmp_path, [
        {"cls": "under", "styles": {
            "width": "900px", "max-width": "1280px", "margin": "0px 80px"}},
    ])
    assert 'margin: "0px 80px"' in blob, blob


def test_symmetric_negative_margin_preserved(tmp_path: Path) -> None:
    # main-news-list: symmetric NEGATIVE margin (`-12px`) is grid-gutter
    # compensation, never centering — must survive verbatim.
    blob = _run(tmp_path, [
        {"cls": "main-news-list", "styles": {
            "width": "1208px", "max-width": "1280px", "margin": "36px -12px 0px"}},
    ])
    assert 'margin: "36px -12px 0px"' in blob, blob
