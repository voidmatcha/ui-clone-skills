"""F1 — video-embed iframe localization at the transpiler.

The reference site embeds video via cross-origin players (Vimeo `?h=` privacy
embeds, YouTube). Emitting the raw <iframe> verbatim renders a dead BLACK box
on the served clone: the `?h=` embed is domain-restricted and the iframe's
height:auto collapses inside its dark wrapper. The transpiler must instead
convert a video-embed iframe into a local <video> element that fills the
container (autoplay/muted/loop), pointing at a local /videos/<id>.mp4 + poster,
so the region shows a real frame/plays instead of a black void.

Non-video iframes (maps, forms, tracking) must be untouched by this path.
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


def _emit(tmp_path: Path, node: dict) -> str:
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {"tag": "body", "styles": {}, "children": [
        {"tag": "section", "class": "hero", "styles": {}, "children": [node]},
    ]}
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": "hero"}]}),
        encoding="utf-8")
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120, env={**os.environ},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _blob(impl)


_VIMEO_IFRAME = {
    "tag": "iframe",
    "src": "https://player.vimeo.com/video/949412882?h=9f91e88496&muted=1&autoplay=1&controls=0",
    "title": "Home - eBay Tagline",
    "allow": "autoplay; fullscreen; picture-in-picture",
    "styles": {"position": "absolute", "width": "1376px", "height": "auto"},
}


def test_vimeo_iframe_becomes_local_video(tmp_path: Path) -> None:
    blob = _emit(tmp_path, _VIMEO_IFRAME)
    # The dead cross-origin embed must be gone.
    assert "player.vimeo.com" not in blob, blob
    assert "<iframe" not in blob, blob
    # A local <video> is emitted instead.
    assert "<video" in blob, blob
    # Localized asset path keyed by the vimeo id.
    assert "949412882" in blob and "/videos/" in blob, blob
    # Background autoplay props so it plays muted (and passes video-play-proof).
    low = blob.lower()
    assert "autoplay" in low and "muted" in low and "loop" in low, blob


def test_non_video_iframe_untouched(tmp_path: Path) -> None:
    node = {
        "tag": "iframe",
        "src": "https://www.google.com/maps/embed?pb=abc",
        "title": "Map",
        "styles": {"width": "600px", "height": "400px"},
    }
    blob = _emit(tmp_path, node)
    # A genuine content iframe (map) is preserved, not turned into a video.
    assert "<iframe" in blob, blob
    assert "google.com/maps" in blob, blob
