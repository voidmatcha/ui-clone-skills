"""Inline <script> bodies must reach bundles/ so the extractors can see them.

resource-mirror.sh enumerates `script[src]` and download-chunks.sh fetches those
URLs, so a site declaring its motion inline shipped no bundle evidence at all.
Measured on webflow.com: 30 inline scripts, ~64 KB, carrying 24 GSAP
construction sites including a scroll-linked ScrollTrigger.create — none of
which any extractor could reach before.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "extract" / "_inline_scripts.py"
EVAL_JS = ROOT / "scripts" / "extract" / "inline-scripts-eval.js"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(ref: Path, payload: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    response = ref / "response.json"
    response.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(MODULE), str(ref), str(response)],
        capture_output=True, text=True, timeout=60,
    )


def test_inline_scripts_written_into_bundles(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    proc = _run(ref, {
        "url": "https://example.test/",
        "scripts": [
            {"index": 0, "type": "text/javascript", "bytes": 20,
             "body": "gsap.to(e,{y:1,scrollTrigger:{trigger:e,scrub:1}});"},
            {"index": 1, "type": "module", "module": True, "bytes": 10,
             "body": "animate(el,{opacity:0,ease:'out'});"},
        ],
        "skipped": [],
    })

    assert proc.returncode == 0, proc.stderr
    assert (ref / "bundles" / "inline-000.js").is_file()
    assert (ref / "bundles" / "inline-001.js").is_file()

    summary = json.loads((ref / "inline-scripts.json").read_text())
    assert summary["count"] == 2
    assert summary["url"] == "https://example.test/"
    assert {f["file"] for f in summary["files"]} == {
        "bundles/inline-000.js", "bundles/inline-001.js"
    }


def test_inline_scripts_reach_the_bundle_extractor(tmp_path: Path) -> None:
    """The whole point: a motion site declared inline must become extractable."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _run(ref, {
        "url": "https://example.test/",
        "scripts": [{
            "index": 0, "type": "", "bytes": 60,
            "body": "ScrollTrigger.create({trigger:marquee,start:'top bottom',scrub:1});",
        }],
        "skipped": [],
    })

    extraction = _load("be", ROOT / "scripts" / "extract" / "_bundle_extraction.py")
    gsap = extraction.parse_bundles(ref)["extractions"].get("gsap")

    assert gsap, "inline motion must be visible to the bundle extractor"
    assert gsap[0]["scrollLinked"] is True
    assert "inline-000.js" in gsap[0]["source"]


def test_inline_scripts_skips_blank_and_unwraps_envelope(tmp_path: Path) -> None:
    """Real agent-browser output is wrapped; blank bodies carry nothing."""
    ref = tmp_path / "ref"
    ref.mkdir()
    proc = _run(ref, {
        "success": True,
        "data": {
            "origin": "https://example.test",
            "result": {
                "url": "https://example.test/",
                "scripts": [
                    {"index": 0, "type": "", "body": "   \n  "},
                    {"index": 1, "type": "", "body": "gsap.timeline({repeat:-1});"},
                ],
                "skipped": [],
            },
        },
    })

    assert proc.returncode == 0, proc.stderr
    summary = json.loads((ref / "inline-scripts.json").read_text())
    assert summary["count"] == 1
    assert not (ref / "bundles" / "inline-000.js").exists()
    assert (ref / "bundles" / "inline-001.js").is_file()


def test_inline_scripts_rejects_unexpected_payload(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    proc = _run(ref, {"url": "https://example.test/", "unexpected": True})

    assert proc.returncode == 3
    assert "unexpected payload shape" in proc.stderr
    assert not (ref / "inline-scripts.json").exists()


def test_inline_scripts_eval_is_an_iife_and_filters_non_js() -> None:
    """AGENTS.md requires IIFE evals; JSON-LD and importmap carry no motion."""
    source = EVAL_JS.read_text(encoding="utf-8")
    assert source.lstrip().startswith("(() =>")
    assert 'script:not([src])' in source
    # An allowlist, not a denylist: an unknown type must be skipped by default
    # rather than needing to be named.
    assert "JS_TYPES" in source
    assert "JS_TYPES.has(rawType)" in source


def test_inline_scripts_shell_validates_origin() -> None:
    """A lost page target must not publish an empty inline-script inventory."""
    shell = (ROOT / "scripts" / "extract" / "inline-scripts.sh").read_text(encoding="utf-8")
    assert "validate-agent-browser-origin.py" in shell
    assert "--session" in shell
