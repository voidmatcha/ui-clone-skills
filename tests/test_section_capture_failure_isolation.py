from __future__ import annotations

import json
from pathlib import Path

import pytest

import ui_clone.section_capture as capture


def _matches() -> list[dict[str, object]]:
    def row(name: str, top: int) -> dict[str, object]:
        rect = {"top": top, "left": 0, "width": 1440, "height": 600}
        return {
            "name": name,
            "ref": {"tag": "section", "id": name, "rect": dict(rect)},
            "impl": {"tag": "section", "id": name, "rect": dict(rect)},
        }

    return [row("hero", 0), row("story", 700), row("footer", 1400)]


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failing: set[tuple[str, str]],
) -> tuple[int, list[tuple[str, str]]]:
    section_dir = tmp_path / "sections"
    for side in ("ref", "impl"):
        (section_dir / side).mkdir(parents=True)
    monkeypatch.setenv("SECTION_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("SECTION_CAPTURE_SESSION_REF", "ref-session")
    monkeypatch.setenv("SECTION_CAPTURE_SESSION_IMPL", "impl-session")
    monkeypatch.delenv("SECTION_CAPTURE_REUSE_FROZEN_REF", raising=False)
    monkeypatch.delenv("SECTION_CAPTURE_REF_CALIB", raising=False)
    monkeypatch.delenv("SECTION_CAPTURE_VIEW_W", raising=False)
    monkeypatch.setattr(capture, "_scroll_metrics", lambda *_a: {"y": 0.0, "vh": 900.0, "sh": 3000.0})
    monkeypatch.setattr(capture, "_run_agent_eval", lambda *_a: None)
    monkeypatch.setattr(
        capture,
        "_run_agent_eval_text",
        lambda *_a: '{"quiescent":true,"rounds":0,"runningAnimations":0}',
    )
    monkeypatch.setattr(capture, "_resolve_live_section_rect", lambda *_a: None)
    monkeypatch.setattr(capture, "_run_crop", lambda *_a: None)
    monkeypatch.setattr(capture, "crop_unique_colors", lambda *_a: 100)
    monkeypatch.setattr(capture, "_crop_is_blank", lambda *_a: False)
    monkeypatch.setattr(capture.time, "sleep", lambda *_a: None)

    shots: list[tuple[str, str]] = []

    def _screenshot(session: str, path: Path) -> None:
        side = "ref" if session == "ref-session" else "impl"
        shots.append((side, path.stem))
        if (side, path.stem) in failing:
            path.write_bytes(b"stub")
            raise RuntimeError("section screenshot invalid after 3 attempts: height=1")
        path.write_bytes(b"png")

    monkeypatch.setattr(capture, "_run_screenshot", _screenshot)
    rc = capture.capture_matched_sections(_matches())
    return rc, shots


def test_one_failed_screenshot_does_not_abort_other_sections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc, shots = _run(monkeypatch, tmp_path, failing={("ref", "story")})
    section_dir = tmp_path / "sections"

    assert rc == 0
    # The later section is still captured on both sides.
    assert ("ref", "footer") in shots and ("impl", "footer") in shots
    assert (section_dir / "ref" / "footer.png").is_file()
    assert (section_dir / "impl" / "footer.png").is_file()
    # The failed side leaves no stub crop behind, and the impl of a section
    # with no reference evidence is not captured (nothing to pair it with).
    assert not (section_dir / "ref" / "story.png").exists()
    assert ("impl", "story") not in shots

    failures = json.loads((section_dir / "capture-failures.json").read_text())
    assert set(failures) == {"story"}
    assert "invalid after 3 attempts" in failures["story"]["ref"]

    confidence = json.loads((section_dir / "capture-confidence.json").read_text())
    assert confidence["sections"]["story"]["ref"]["captureFailed"] is True
    assert "story" not in confidence["suspectSections"]
    # A failed reference records no scroll position for the frozen pass.
    positions = json.loads((section_dir / "ref-scroll-positions.json").read_text())
    assert set(positions) == {"hero", "footer"}


def test_impl_screenshot_failure_is_recorded_per_side(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc, _shots = _run(monkeypatch, tmp_path, failing={("impl", "hero")})
    section_dir = tmp_path / "sections"

    assert rc == 0
    assert (section_dir / "ref" / "hero.png").is_file()
    assert not (section_dir / "impl" / "hero.png").exists()
    failures = json.loads((section_dir / "capture-failures.json").read_text())
    assert failures == {"hero": {"impl": failures["hero"]["impl"]}}
    assert (section_dir / "impl" / "story.png").is_file()


def test_frozen_pass_keeps_reference_failures_from_capture_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    section_dir = tmp_path / "sections"
    (section_dir / "impl").mkdir(parents=True)
    (section_dir / "capture-failures.json").write_text(
        json.dumps({"story": {"ref": "height=1"}, "hero": {"impl": "old impl"}})
    )
    monkeypatch.setenv("SECTION_CAPTURE_REUSE_FROZEN_REF", "1")
    monkeypatch.setenv("SECTION_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("SECTION_CAPTURE_SESSION_REF", "ref-session")
    monkeypatch.setenv("SECTION_CAPTURE_SESSION_IMPL", "impl-session")
    monkeypatch.delenv("SECTION_CAPTURE_REF_CALIB", raising=False)
    monkeypatch.delenv("SECTION_CAPTURE_VIEW_W", raising=False)
    monkeypatch.setattr(capture, "_scroll_metrics", lambda *_a: {"y": 0.0, "vh": 900.0, "sh": 3000.0})
    monkeypatch.setattr(capture, "_run_agent_eval", lambda *_a: None)
    monkeypatch.setattr(capture, "_run_agent_eval_text", lambda *_a: '{"quiescent":true}')
    monkeypatch.setattr(capture, "_resolve_live_section_rect", lambda *_a: None)
    monkeypatch.setattr(capture, "_run_crop", lambda *_a: None)
    monkeypatch.setattr(capture, "crop_unique_colors", lambda *_a: 100)
    monkeypatch.setattr(capture, "_crop_is_blank", lambda *_a: False)
    monkeypatch.setattr(capture.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(capture, "_run_screenshot", lambda _s, path: path.write_bytes(b"png"))

    assert capture.capture_matched_sections(_matches()) == 0

    failures = json.loads((section_dir / "capture-failures.json").read_text())
    # Ref-side failures survive the frozen pass; stale impl failures do not.
    assert failures == {"story": {"ref": "height=1"}}
    # The frozen pass still skips the impl of a section without a ref crop.
    assert not (section_dir / "impl" / "story.png").exists()
    assert (section_dir / "impl" / "hero.png").is_file()


def test_clean_run_rewrites_empty_failure_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    section_dir = tmp_path / "sections"
    section_dir.mkdir()
    (section_dir / "capture-failures.json").write_text('{"hero": {"ref": "stale"}}')
    rc, _shots = _run(monkeypatch, tmp_path, failing=set())

    assert rc == 0
    assert json.loads((section_dir / "capture-failures.json").read_text()) == {}
