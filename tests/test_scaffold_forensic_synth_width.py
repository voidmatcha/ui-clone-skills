"""Forensic box-model strip must not preserve a P5-synthesized width (realfood v4).

P5 reflow rewrites a captured desktop px width into the responsive PAIR
``max-width:<captured>px`` + ``width:100%``, which still resolves to the
captured width at the capture viewport.

In forensic className-only mode the box-model strip then removes every
box-model prop so the mirrored ref CSS owns layout — except props the REF
declared in its own inline style attr (``node.inlineProps``), whose whole
premise is "the ref's inline beat its own CSS, so the CSS value is not what
rendered".

Those two passes compose wrongly. On realfood's hero_video the ref carried a
framer-driven inline ``width`` (the 80vw→100vw scroll scrub), so ``width`` is
inline-guarded — but P5 had ALREADY replaced the ref's inline value with the
synthesized ``100%``. The strip dropped the unguarded ``max-width`` cap and
kept the synthesized ``width:100%``, which overrode the mirrored
``.hero_video{width:80vw}``: 1152px → 1440px, section height 666 → 828, and
every one of the 12 sections below it drifted by exactly +162px.

The guard must protect only values the ref itself inlined, never a value a
synthesis pass wrote over them.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"

REF_CSS = ".hero_video{width:80vw;margin:0 auto;padding:20px;display:flex}"


def _run(tmp_path: Path, node: dict) -> str:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "ref.css").write_text(REF_CSS, encoding="utf-8")
    (ref / "structure.json").write_text(
        json.dumps({"tag": "body", "class": "", "styles": {},
                    "children": [dict(node, children=[{"tag": "p", "text": "x"}])]}),
        encoding="utf-8")
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "tag": "section", "cls": node["class"]}]}),
        encoding="utf-8")
    # forensic className-only mode is what mirrors the ref CSS and strips the box model
    (ref / "generation-plan.json").write_text(
        json.dumps({"forensicPreservation": {"strategy": "ref-derived-jsx-with-local-css"}}),
        encoding="utf-8")
    (impl / "package.json").write_text('{"name":"i","dependencies":{}}', encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted((impl / "src" / "components").glob("*.tsx")))


def test_synthesized_width_not_preserved_by_inline_guard(tmp_path: Path) -> None:
    """The realfood hero_video shape: inline-guarded width + P5 reflow."""
    blob = _run(tmp_path, {
        "tag": "section", "class": "hero_video",
        "styles": {"width": "1152px", "margin": "0px 144px", "padding": "20px",
                   "display": "flex", "box-sizing": "border-box"},
        "inlineProps": ["width"],
    })
    # In forensic mode the mirrored ref CSS owns layout; neither half of the
    # synthesized responsive pair should survive.
    assert 'width: "100%"' not in blob, blob
    assert "maxWidth" not in blob, blob


def test_unguarded_width_is_stripped_in_forensic_mode(tmp_path: Path) -> None:
    """Baseline: with no inline guard the whole box model goes to the CSS."""
    blob = _run(tmp_path, {
        "tag": "section", "class": "hero_video",
        "styles": {"width": "1152px", "padding": "20px", "display": "flex"},
    })
    assert 'width: "100%"' not in blob
    assert "maxWidth" not in blob
