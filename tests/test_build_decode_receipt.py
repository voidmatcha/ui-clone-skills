"""Tests for the standalone decode-receipt HTML builder.

Step F of the positioning rollout: emit a shareable single-file HTML
that captures one motion-forensics trial against a live URL.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "build-decode-receipt.sh"


def _run(ref: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(out)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_minimal_ref_dir_produces_valid_html(tmp_path: Path) -> None:
    """Even with only pipeline-state.json present, output is valid HTML."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "pipeline-state.json").write_text(json.dumps({
        "component": "demo",
        "targetUrl": "https://example.com",
        "unclonable_reasons": [],
    }))
    out = tmp_path / "receipt.html"
    proc = _run(ref, out)
    assert proc.returncode == 0, f"build failed: {proc.stdout}\n{proc.stderr}"
    body = out.read_text()
    assert body.startswith("<!doctype html>")
    assert body.rstrip().endswith("</html>")
    assert "Motion forensics receipt" in body
    assert "example.com" in body
    assert "Not affiliated with example.com" in body


def test_gate_verdicts_render(tmp_path: Path) -> None:
    """Status badges + reasons surface for each gate artifact present."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "pipeline-state.json").write_text(json.dumps({"targetUrl": "https://x.test"}))
    (ref / "bundle-paste-check.json").write_text(json.dumps({
        "status": "pass",
        "reason": "no bundle paste detected",
    }))
    (ref / "html-paste.json").write_text(json.dumps({
        "status": "fail",
        "reason": "structural similarity 78% > 70%",
    }))

    out = tmp_path / "receipt.html"
    proc = _run(ref, out)
    assert proc.returncode == 0
    body = out.read_text()
    assert "no bundle paste" in body
    assert "structural similarity 78%" in body
    assert "v-fail" in body  # CSS class for fail status
    assert "v-pass" in body


def test_unclonable_reasons_render_with_fallbacks(tmp_path: Path) -> None:
    """Unclonable rows + fallback_suggestions (Step G payload) render as a
    bulleted list under the reason."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "pipeline-state.json").write_text(json.dumps({
        "targetUrl": "https://x.test",
        "unclonable_reasons": [
            {
                "gate": "post-implement",
                "category": "drm-canvas",
                "reason": "Reference renders inside a paywalled canvas.",
                "fallback_suggestions": [
                    "Mock the canvas with a static SVG placeholder.",
                    "Use CSS @property + animation-timeline for scroll-driven motion.",
                ],
            },
        ],
    }))
    out = tmp_path / "receipt.html"
    proc = _run(ref, out)
    assert proc.returncode == 0
    body = out.read_text()
    assert "Unclonable reasons" in body
    assert "drm-canvas" in body
    assert "static SVG placeholder" in body
    assert "animation-timeline" in body
    assert "fallbacks" in body


def test_sections_result_tail_rendered(tmp_path: Path) -> None:
    """sections/result.txt tail (last 30 lines) appears in a <pre> block."""
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True)
    (ref / "pipeline-state.json").write_text(json.dumps({"targetUrl": "https://x.test"}))
    rows = "\n".join(f"| Section {i:02d} | AE | 0 | low | PASS |" for i in range(40))
    (sections / "result.txt").write_text(rows + "\n")
    out = tmp_path / "receipt.html"
    proc = _run(ref, out)
    assert proc.returncode == 0
    body = out.read_text()
    assert "Section 39" in body  # last few lines rendered
    # Earliest rows trimmed (>30 line cap)
    assert "Section 00 " not in body, "first row should have been trimmed by tail cap"


def test_html_special_chars_escaped(tmp_path: Path) -> None:
    """Site URLs / reasons with HTML metachars are properly escaped."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "pipeline-state.json").write_text(json.dumps({
        "targetUrl": "https://x.test/<script>alert(1)</script>",
    }))
    (ref / "bundle-paste-check.json").write_text(json.dumps({
        "status": "fail",
        "reason": "pasted <iframe> bundle & 0 < threshold",
    }))
    out = tmp_path / "receipt.html"
    proc = _run(ref, out)
    assert proc.returncode == 0
    body = out.read_text()
    assert "<script>alert" not in body, "raw script must not appear"
    assert "&lt;script&gt;" in body
    assert "&lt;iframe&gt;" in body
    assert "&amp;" in body


def test_default_outbox_path_used_when_no_output_arg(tmp_path: Path) -> None:
    """When no output path provided, receipt lands in
    <repo>/outbox/<date>/<component>/receipt.html."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "pipeline-state.json").write_text(json.dumps({"targetUrl": "https://x.test"}))

    # Point PLUGIN_ROOT at tmp_path so we can find the default-emit location.
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref)],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PLUGIN_ROOT": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, f"stdout={proc.stdout} stderr={proc.stderr}"
    out_files = list((tmp_path / "outbox").rglob("receipt.html"))
    assert out_files, f"expected receipt under {tmp_path}/outbox/, got: {list((tmp_path / 'outbox').rglob('*')) if (tmp_path / 'outbox').exists() else 'no outbox'}"
    body = out_files[0].read_text()
    assert "Motion forensics receipt" in body


def test_setup_error_on_bad_ref(tmp_path: Path) -> None:
    """Non-existent ref → exit 2."""
    proc = _run(tmp_path / "no-such-ref", tmp_path / "out.html")
    assert proc.returncode == 2
