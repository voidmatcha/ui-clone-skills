"""scaffold-to-jsx emits a UA-margin reset (navercorp C1 residual offset).

extract-dom drops an author `margin:0` (its default-drop set treats 0px as
noise), so a tag with a NON-zero user-agent default margin (body 8px; h1-h6/p/
ul/ol/... ~1em) has that UA margin resurface in the clone and offset content —
navercorp's 0-margin ul.skip and inner footer/partner lists re-grew their UA
margins, pushing every section down. The scaffold zeroes the UA-margin tags
globally; a captured non-zero margin is baked inline (higher specificity) and
overrides the reset, so only author-reset-to-0 elements are affected.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _run(tmp_path: Path, section: dict, *, forensic: bool = False) -> str:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(
        json.dumps({"tag": "body", "class": "", "display": "block",
                    "styles": {}, "children": [section]}),
        encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": section["tag"], "cls": section["class"]}]}),
        encoding="utf-8")
    if forensic:
        (ref / "generation-plan.json").write_text(json.dumps({
            "forensicPreservation": {
                "required": True,
                "strategy": "ref-derived-jsx-with-local-css",
            },
        }), encoding="utf-8")
        (ref / "css").mkdir()
        (ref / "css" / "main.css").write_text(
            "p{margin:0 0 10px}", encoding="utf-8")
    (impl / "package.json").write_text('{"name":"i","dependencies":{}}', encoding="utf-8")
    env = {**os.environ}
    if forensic:
        env["UI_CLONE_FORENSIC_CLASSNAME_ONLY"] = "1"
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # the global reset lands in the app entry / global style, not a component
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted((impl / "src").rglob("*.tsx")))


def test_ua_margin_reset_emitted(tmp_path: Path) -> None:
    blob = _run(tmp_path, {
        "tag": "section", "class": "sec", "display": "block",
        "styles": {"height": "300px"},
        "children": [{"tag": "p", "text": "x"}]})
    # the UA-margin tags are zeroed globally
    for tag in ("body", "h1", "p", "ul", "ol", "figure", "menu"):
        assert tag in blob
    assert "{margin:0;}" in blob, "UA-margin reset rule must be emitted"


def test_reset_does_not_use_important(tmp_path: Path) -> None:
    # the reset must be overridable by a baked inline margin (inline already wins
    # on specificity; !important on the reset would defeat that and erase real
    # captured margins).
    blob = _run(tmp_path, {
        "tag": "section", "class": "sec", "display": "block",
        "styles": {"height": "300px"}, "children": [{"tag": "p", "text": "x"}]})
    i = blob.find("{margin:0;}")
    assert i != -1
    # no !important adjacent to the margin reset
    assert "margin:0 !important" not in blob


def test_forensic_mode_omits_reset_for_classless_source_margin(
    tmp_path: Path,
) -> None:
    blob = _run(tmp_path, {
        "tag": "section",
        "class": "sec",
        "display": "block",
        "styles": {"height": "300px"},
        "children": [{
            "tag": "p",
            "class": "",
            "text": "Legal",
            "styles": {"margin": "0px 0px 10px"},
            "children": [],
        }],
    }, forensic=True)
    assert "{margin:0;}" not in blob
    assert 'margin: "0px 0px 10px"' not in blob
    assert (tmp_path / "ref" / "css" / "main.css").read_text(
        encoding="utf-8"
    ) == "p{margin:0 0 10px}"
