"""Tests for scripts/extract/_capture_artifacts.py — the Python helpers
behind capture.sh (Phase 1 reference capture)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_module() -> ModuleType:
    """Load scripts/extract/_capture_artifacts.py by path."""
    key = "_capture_artifacts_test_module"
    if key in sys.modules:
        return sys.modules[key]
    path = _project_root() / "scripts" / "extract" / "_capture_artifacts.py"
    spec = importlib.util.spec_from_file_location(key, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


# ── parse_page_height ──


def test_parse_page_height_json_string_wrapped() -> None:
    """agent-browser eval returns numbers double-encoded as `"5400"`."""
    mod = _load_module()
    assert mod.parse_page_height('"5400"') == 5400


def test_parse_page_height_bare_int() -> None:
    """Some browsers / direct curl returns just the number."""
    mod = _load_module()
    assert mod.parse_page_height("3200") == 3200


def test_parse_page_height_json_number() -> None:
    """JSON-decoded number (no string wrap)."""
    mod = _load_module()
    assert mod.parse_page_height("4800") == 4800


def test_parse_page_height_empty_falls_back() -> None:
    """Empty input → fallback 5000."""
    mod = _load_module()
    assert mod.parse_page_height("") == 5000
    assert mod.parse_page_height("   ") == 5000


def test_parse_page_height_invalid_falls_back() -> None:
    """Garbage → fallback 5000."""
    mod = _load_module()
    assert mod.parse_page_height("not-a-number") == 5000
    assert mod.parse_page_height('"banana"') == 5000


def test_parse_page_height_zero_negative_falls_back() -> None:
    """Zero or negative → fallback so downstream `Y = PAGE_H * i / 5` is safe."""
    mod = _load_module()
    assert mod.parse_page_height("0") == 5000
    assert mod.parse_page_height("-100") == 5000


def test_parse_page_height_custom_fallback() -> None:
    """Caller can override fallback."""
    mod = _load_module()
    assert mod.parse_page_height("bad", fallback=8000) == 8000


# ── write_regions_json ──


def test_write_regions_json_shape(tmp_path: Path) -> None:
    """Minimal placeholder shape, self-incriminating so the reference gate
    can fail it on motion sites (Phase 2 detection must replace it)."""
    mod = _load_module()
    mod.write_regions_json(tmp_path, page_height=4200)
    payload = json.loads((tmp_path / "regions.json").read_text())
    assert payload == {
        "placeholder": True,
        "detectionRan": False,
        "regions": [
            {
                "name": "full-page",
                "x": 0,
                "y": 0,
                "width": 1440,
                "height": 4200,
            }
        ]
    }


def test_write_regions_json_custom_viewport(tmp_path: Path) -> None:
    """Viewport width is overridable for mobile capture."""
    mod = _load_module()
    mod.write_regions_json(tmp_path, page_height=8000, viewport_width=375)
    payload = json.loads((tmp_path / "regions.json").read_text())
    assert payload["regions"][0]["width"] == 375
    assert payload["regions"][0]["height"] == 8000


def test_write_regions_json_creates_dir(tmp_path: Path) -> None:
    """ref_dir doesn't have to exist — module creates it."""
    mod = _load_module()
    new_dir = tmp_path / "ref" / "deep"
    mod.write_regions_json(new_dir, page_height=1000)
    assert (new_dir / "regions.json").is_file()


# ── summarize_artifacts ──


def test_summarize_artifacts_counts(tmp_path: Path) -> None:
    """Count files in each canonical capture subdir."""
    mod = _load_module()
    for subpath, count in (
        ("static/ref", 5),
        ("scroll-video/ref", 1),
        ("transitions/ref", 2),
    ):
        d = tmp_path / subpath
        d.mkdir(parents=True)
        for i in range(count):
            (d / f"file_{i}.bin").write_bytes(b"x")
    (tmp_path / "regions.json").write_text("{}")
    summary = mod.summarize_artifacts(tmp_path)
    assert summary == {
        "static_ref_screenshots": 5,
        "scroll_video_ref_videos": 1,
        "transitions_ref_videos": 2,
        "regions_json_present": True,
    }


def test_summarize_artifacts_empty(tmp_path: Path) -> None:
    """No artifacts → all zeros, regions absent."""
    mod = _load_module()
    summary = mod.summarize_artifacts(tmp_path)
    assert summary == {
        "static_ref_screenshots": 0,
        "scroll_video_ref_videos": 0,
        "transitions_ref_videos": 0,
        "regions_json_present": False,
    }


def test_summarize_artifacts_ignores_hidden_files(tmp_path: Path) -> None:
    """Dotfiles (.DS_Store, etc.) don't count toward artifact totals."""
    mod = _load_module()
    d = tmp_path / "static" / "ref"
    d.mkdir(parents=True)
    (d / "real.png").write_bytes(b"")
    (d / ".DS_Store").write_bytes(b"")
    summary = mod.summarize_artifacts(tmp_path)
    assert summary["static_ref_screenshots"] == 1


# ── write_capture_error ──


def test_write_capture_error_shape_and_summary(tmp_path: Path) -> None:
    """Recorder lifecycle failures are persisted with actionable context."""
    mod = _load_module()
    screenshots = tmp_path / "static" / "ref"
    screenshots.mkdir(parents=True)
    for i in range(5):
        (screenshots / f"section-{i}.png").write_bytes(b"x")

    payload = mod.write_capture_error(
        tmp_path,
        stage="scroll-video:record-stop",
        exit_code=1,
        artifact=str(tmp_path / "scroll-video" / "ref" / "full-scroll.webm"),
        command="agent-browser --session demo record stop",
        message="✗ No recording in progress",
    )

    saved = json.loads((tmp_path / "capture-error.json").read_text())
    assert saved == payload
    assert payload["error"] == "capture-step-failed"
    assert payload["stage"] == "scroll-video:record-stop"
    assert payload["artifact"] == "scroll-video/ref/full-scroll.webm"
    assert payload["exitCode"] == 1
    assert payload["message"] == "✗ No recording in progress"
    assert payload["summary"]["static_ref_screenshots"] == 5
    assert payload["summary"]["scroll_video_ref_videos"] == 0


# ── CLI ──


def test_main_parse_height(capsys: pytest.CaptureFixture[str]) -> None:
    """parse-height subcommand prints int."""
    mod = _load_module()
    rc = mod.main(["parse-height", '"3200"'])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "3200"


def test_main_write_regions(tmp_path: Path) -> None:
    """write-regions subcommand writes JSON."""
    mod = _load_module()
    rc = mod.main(["write-regions", str(tmp_path), "1234"])
    assert rc == 0
    payload = json.loads((tmp_path / "regions.json").read_text())
    assert payload["regions"][0]["height"] == 1234
    assert payload["regions"][0]["width"] == 1440


def test_main_write_regions_with_viewport(tmp_path: Path) -> None:
    """write-regions accepts custom viewport width."""
    mod = _load_module()
    rc = mod.main(["write-regions", str(tmp_path), "3000", "375"])
    assert rc == 0
    payload = json.loads((tmp_path / "regions.json").read_text())
    assert payload["regions"][0]["width"] == 375


def test_main_summarize(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """summarize subcommand emits capture.sh's textual block."""
    mod = _load_module()
    (tmp_path / "static" / "ref").mkdir(parents=True)
    (tmp_path / "static" / "ref" / "0.png").write_bytes(b"")
    (tmp_path / "regions.json").write_text("{}")
    rc = mod.main(["summarize", str(tmp_path)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "static/ref/: 1 screenshots" in captured.out
    assert "regions.json: ok" in captured.out


def test_main_write_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """write-error subcommand writes capture-error.json and prints its path."""
    mod = _load_module()
    rc = mod.main(
        [
            "write-error",
            str(tmp_path),
            "transition-placeholder:record-stop",
            "1",
            "transitions/ref/placeholder.webm",
            "agent-browser record stop",
            "No recording in progress",
        ]
    )
    assert rc == 0
    payload = json.loads((tmp_path / "capture-error.json").read_text())
    assert payload["stage"] == "transition-placeholder:record-stop"
    assert payload["artifact"] == "transitions/ref/placeholder.webm"
    assert payload["message"] == "No recording in progress"
    captured = capsys.readouterr()
    assert str(tmp_path / "capture-error.json") in captured.out


def test_main_rejects_no_args() -> None:
    """Bare invocation → exit 2 (usage)."""
    mod = _load_module()
    assert mod.main([]) == 2


def test_main_rejects_unknown_subcommand() -> None:
    mod = _load_module()
    assert mod.main(["bogus"]) == 2


def test_main_parse_height_no_arg() -> None:
    mod = _load_module()
    assert mod.main(["parse-height"]) == 2


def test_main_write_regions_no_arg() -> None:
    mod = _load_module()
    assert mod.main(["write-regions"]) == 2


def test_main_write_regions_bad_height() -> None:
    """Non-int page_height → exit 2."""
    mod = _load_module()
    assert mod.main(["write-regions", "/tmp/x", "not-a-number"]) == 2


def test_main_write_error_bad_exit_code(tmp_path: Path) -> None:
    mod = _load_module()
    assert mod.main(["write-error", str(tmp_path), "scroll-video:record-stop", "bad"]) == 2
