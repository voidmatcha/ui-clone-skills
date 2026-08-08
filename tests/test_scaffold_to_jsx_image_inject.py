from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _run(tmp_path: Path, body: dict, visible_images: list) -> str:
    """Drive scaffold-to-jsx with a structure + a visible-images.json manifest and
    return the concatenated generated JSX. The injector (_inject_missing_images)
    reads visible-images.json from the ref dir."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(body), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8")
    (ref / "visible-images.json").write_text(json.dumps(visible_images), encoding="utf-8")
    env = os.environ.copy()
    env.pop("BASH_COMPAT", None)
    env["UI_CLONE_SKIP_ASSET_DOWNLOAD"] = "1"
    bash = shutil.which("bash")
    assert bash is not None
    proc = subprocess.run(
        [bash, str(SCRIPT), str(ref), str(impl)],
        capture_output=True, env=env, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "".join(
        p.read_text() for p in (impl / "src" / "components").glob("*.tsx"))


def _wrap(container_class: str) -> dict:
    """A hero section holding one (initially empty) container div whose class the
    injector will try to match by /images/<category>/ path segment."""
    return {"tag": "body", "children": [
        {"tag": "section", "class": "hero", "children": [
            {"tag": "div", "class": container_class, "children": []}]}]}


def test_shell_wrapper_avoids_large_python_heredoc() -> None:
    """Default Bash 5.1+ must not pipe the transpiler body through a heredoc."""
    shell = SCRIPT.read_text(encoding="utf-8")
    helper = SCRIPT.parent / "lib" / "scaffold_to_jsx.py"

    assert "<<" not in shell
    assert 'python3 "$SCRIPT_DIR/lib/scaffold_to_jsx.py"' in shell
    assert helper.is_file()


def test_realfood_shaped_image_injected_into_matching_container(tmp_path: Path) -> None:
    """A lazy image absent from the DOM scaffold whose /images/<category>/ segment
    matches a delimiter-bounded token of a container class (realfood's underscore
    CSS-module container `mod_erf_pyramid__wyDoE`, token "pyramid") is injected."""
    blob = _run(
        tmp_path,
        _wrap("mod_erf_pyramid__wyDoE"),
        [{"src": "https://cdn.example.com/images/pyramid/milk.webp",
          "alt": "milk", "width": 268, "height": 558}])
    assert "milk.webp" in blob, blob


def test_injected_image_carries_captured_dimensions(tmp_path: Path) -> None:
    """The injected <img> bakes the captured rendered WIDTH (width/height stored as
    NUMBERS in visible-images) as a px inline style, so a run of injected images is
    constrained to its real footprint instead of ballooning to intrinsic
    resolution (ebay s-l1600 = 1586px). The transpiler's responsive-media path
    then sets height:auto + maxWidth:100% to preserve aspect — the width is the
    load-bearing anti-tower dimension."""
    blob = _run(
        tmp_path,
        _wrap("mod_erf_pyramid__wyDoE"),
        [{"src": "https://cdn.example.com/images/pyramid/milk.webp",
          "alt": "milk", "width": 268, "height": 558}])
    line = next(ln for ln in blob.splitlines() if "milk.webp" in ln)
    assert 'width: "268px"' in line, line
    assert 'height: "auto"' in line, line


def test_cdn_shard_category_not_injected(tmp_path: Path) -> None:
    """eBay product images are served from /images/g/<hash>/ — the category "g" is
    a 1-char CDN shard, not a semantic segment. It must NOT be injected (the old
    substring match dumped all of them into the header's gh-sch-prom div, building
    a multi-thousand-px vertical image tower)."""
    blob = _run(
        tmp_path,
        _wrap("gh-sch-prom"),
        [{"src": "https://i.ebayimg.com/images/g/s9IAAeSwesFpusZk/s-l1600.webp",
          "alt": "", "width": 1586, "height": 425}])
    assert "s9IAAeSwesFpusZk" not in blob, blob


def test_substring_category_does_not_false_match_container(tmp_path: Path) -> None:
    """Guard against the reviewer's ">=3-char shard" case: category "art" must not
    match container class "cart" (a bare substring would; a token match must not),
    so no false injection tower is built for a longer shard-like category."""
    blob = _run(
        tmp_path,
        _wrap("cart"),
        [{"src": "https://cdn.example.com/images/art/hero.png",
          "alt": "", "width": 100, "height": 100}])
    assert "hero.png" not in blob, blob


def test_dashed_container_token_matched(tmp_path: Path) -> None:
    """A dash-delimited container class (`photo-gallery`) is matched on its
    "gallery" token — token matching splits on '-' as well as '_'."""
    blob = _run(
        tmp_path,
        _wrap("photo-gallery"),
        [{"src": "https://cdn.example.com/images/gallery/shot.png",
          "alt": "", "width": 320, "height": 240}])
    assert "shot.png" in blob, blob
