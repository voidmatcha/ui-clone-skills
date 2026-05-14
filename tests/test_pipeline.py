"""Tests for ui_clone.pipeline — pipeline status checker."""

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ui_clone.hooks._common import load_json_safe as _load_json_safe
from ui_clone.pipeline import (
    Pipeline,
    _check_dependencies,
    _count_tsx_files,
    _find_app_dir,
    _has_files,
)


class TestCheckDependencies:
    def test_all_present(self) -> None:
        """No missing tools when all are available."""
        with patch("shutil.which", return_value="/usr/bin/tool"):
            missing = _check_dependencies()
        assert missing == []

    def test_missing_tool(self) -> None:
        """Missing tool returned in list."""
        original_which = __import__("shutil").which

        def fake_which(name: str) -> Any:
            if name == "agent-browser":
                return None
            return original_which(name)

        with patch("shutil.which", side_effect=fake_which):
            missing = _check_dependencies()
        assert any("agent-browser" in m for m in missing)


class TestHasFiles:
    def test_has_files_true(self, tmp_path: Path) -> None:
        d = tmp_path / "imgs"
        d.mkdir()
        for i in range(5):
            (d / f"img_{i}.png").write_text("x" * 20)
        assert _has_files(d, "*.png", 5)

    def test_has_files_false(self, tmp_path: Path) -> None:
        d = tmp_path / "imgs"
        d.mkdir()
        (d / "img_0.png").write_text("x" * 20)
        assert not _has_files(d, "*.png", 5)

    def test_missing_dir(self, tmp_path: Path) -> None:
        assert not _has_files(tmp_path / "nonexistent", "*.png", 1)


class TestLoadJsonSafe:
    def test_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "test.json"
        p.write_text('{"key": "value"}')
        assert _load_json_safe(p) == {"key": "value"}

    def test_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "test.json"
        p.write_text("not json")
        assert _load_json_safe(p) is None

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _load_json_safe(tmp_path / "nope.json") is None

    def test_array_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "test.json"
        p.write_text("[1, 2, 3]")
        assert _load_json_safe(p) is None


class TestFindAppDir:
    def test_flat_layout(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "components").mkdir(parents=True)
        result = _find_app_dir(tmp_path, "hero")
        assert result == tmp_path

    def test_monorepo_specific(self, tmp_path: Path) -> None:
        (tmp_path / "apps" / "hero" / "src" / "components").mkdir(parents=True)
        result = _find_app_dir(tmp_path, "hero")
        assert result == tmp_path / "apps" / "hero"

    def test_monorepo_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "apps" / "other" / "src" / "components").mkdir(parents=True)
        result = _find_app_dir(tmp_path, "hero")
        assert result == tmp_path / "apps" / "other"

    def test_no_app_dir(self, tmp_path: Path) -> None:
        assert _find_app_dir(tmp_path, "hero") is None


class TestCountTsxFiles:
    def test_counts_tsx(self, tmp_path: Path) -> None:
        comp_dir = tmp_path / "src" / "components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "Hero.tsx").write_text("export default function Hero() {}")
        (comp_dir / "Footer.tsx").write_text("export default function Footer() {}")
        assert _count_tsx_files(tmp_path) == 2

    def test_no_tsx(self, tmp_path: Path) -> None:
        assert _count_tsx_files(tmp_path) == 0


class TestPipeline:
    def test_done_state_short_circuits(self, tmp_path: Path) -> None:
        """Pipeline with current_gate=done exits immediately."""
        ref_dir = tmp_path / "tmp" / "ref" / "test-comp"
        ref_dir.mkdir(parents=True)
        state = {
            "component": "test-comp",
            "started_at": "2025-01-01T00:00:00Z",
            "completed_steps": [
                "reference",
                "extraction",
                "bundle",
                "spec",
                "pre-generate",
                "post-implement",
                "section-compare",
            ],
            "current_gate": "done",
            "last_updated": "2025-01-01T01:00:00Z",
        }
        (ref_dir / "pipeline-state.json").write_text(json.dumps(state))

        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            with patch("ui_clone.pipeline._check_dependencies", return_value=[]):
                pipeline = Pipeline("https://example.com", "test-comp", "sess")
                pipeline.project_root = tmp_path
                pipeline.ref_dir = ref_dir
                result = pipeline.run()
        assert result == 0

    def test_missing_deps_returns_1(self, tmp_path: Path) -> None:
        """Pipeline returns 1 when dependencies are missing."""
        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            with patch("ui_clone.pipeline._check_dependencies", return_value=["agent-browser"]):
                pipeline = Pipeline("https://example.com", "test-comp", "sess")
                pipeline.project_root = tmp_path
                pipeline.ref_dir = tmp_path / "tmp" / "ref" / "test-comp"
                result = pipeline.run()
        assert result == 1


class TestPipelineFullRun:
    """Integration test for Pipeline.run() with a fully populated ref_dir."""

    def test_full_run_all_phases_present(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Pipeline.run() completes without error when all artifacts exist."""
        # Create app dir so Phase 3 passes
        comp_dir = tmp_path / "src" / "components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "Hero.tsx").write_text("export default function Hero() {}")

        # Set pipeline state to done
        (ref_dir_with_artifacts / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": ref_dir_with_artifacts.name,
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference",
                        "extraction",
                        "bundle",
                        "spec",
                        "pre-generate",
                        "post-implement",
                        "section-compare",
                    ],
                    "current_gate": "done",
                    "last_updated": "2026-01-01T01:00:00Z",
                }
            )
        )

        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            with patch("ui_clone.pipeline._check_dependencies", return_value=[]):
                pipeline = Pipeline("https://example.com", ref_dir_with_artifacts.name, "sess")
                pipeline.project_root = tmp_path
                pipeline.ref_dir = ref_dir_with_artifacts
                result = pipeline.run()
        assert result == 0

    def test_full_run_incomplete_reports_next_phase(self, tmp_path: Path) -> None:
        """Pipeline.run() reports next phase when artifacts are missing."""
        ref_dir = tmp_path / "tmp" / "ref" / "test-comp"
        ref_dir.mkdir(parents=True)
        # Only canvas detection exists — Phase 1 should be next
        (ref_dir / "canvas-webgl-detection.json").write_text(
            json.dumps({"primaryRenderType": "DOM", "hasCanvas": False, "hasWebGL": False})
        )

        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            with patch("ui_clone.pipeline._check_dependencies", return_value=[]):
                pipeline = Pipeline("https://example.com", "test-comp", "sess")
                pipeline.project_root = tmp_path
                pipeline.ref_dir = ref_dir
                result = pipeline.run()
        assert result == 0
        assert pipeline.next_phase == "1"


class TestPipelinePhases:
    """Unit tests for individual Pipeline.check_phase_* methods."""

    def _make_pipeline(self, tmp_path: Path, ref_dir: Path) -> Any:
        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            p = Pipeline("https://example.com", ref_dir.name, "sess")
            p.project_root = tmp_path
            p.ref_dir = ref_dir
        return p

    def test_check_phase_0a_missing(self, tmp_path: Path) -> None:
        """Phase 0A: no canvas-webgl-detection.json → check fails."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_0a()
        assert result.name == "0A"
        assert any(not c.passed for c in result.checks)

    def test_check_phase_0a_present(self, tmp_path: Path) -> None:
        """Phase 0A: canvas-webgl-detection.json present → check passes."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "canvas-webgl-detection.json").write_text(
            json.dumps({"primaryRenderType": "DOM", "hasCanvas": False, "hasWebGL": False})
        )
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_0a()
        assert any(c.passed for c in result.checks)

    def test_check_phase_0a_canvas_detected(self, tmp_path: Path) -> None:
        """Phase 0A: canvas detected → still passes but sets next_phase if ref_dir is new."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "canvas-webgl-detection.json").write_text(
            json.dumps({"primaryRenderType": "canvas", "hasCanvas": True, "hasWebGL": False})
        )
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_0a()
        assert any(c.passed for c in result.checks)

    def test_check_phase_0_no_prior_data(self, tmp_path: Path) -> None:
        """Phase 0: no prior data → both checks fail."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_0()
        assert result.name == "0"
        assert not any(c.passed for c in result.checks)

    def test_check_phase_0_with_prior_data(self, tmp_path: Path) -> None:
        """Phase 0: both transition-spec.json and extracted.json present."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "transition-spec.json").write_text(json.dumps({"transitions": []}))
        (ref_dir / "extracted.json").write_text(json.dumps({"sections": []}))
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_0()
        assert all(c.passed for c in result.checks)

    def test_check_phase_1_no_screenshots(self, tmp_path: Path) -> None:
        """Phase 1: no reference screenshots → sets next_phase."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_1()
        assert result.name == "1"
        assert not any(c.passed for c in result.checks)
        assert p.next_phase == "1"

    def test_check_phase_1_with_screenshots(self, tmp_path: Path) -> None:
        """Phase 1: all reference artifacts present."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        screenshots = ref_dir / "static" / "ref"
        screenshots.mkdir(parents=True)
        for i in range(5):
            (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        scroll_dir = ref_dir / "scroll-video" / "ref"
        scroll_dir.mkdir(parents=True)
        (scroll_dir / "scroll.webm").write_bytes(b"\x1a" + b"\x00" * 100)
        trans_dir = ref_dir / "transitions" / "ref"
        trans_dir.mkdir(parents=True)
        (trans_dir / "hover.webm").write_bytes(b"\x1a" + b"\x00" * 100)
        (ref_dir / "regions.json").write_text(json.dumps({"regions": []}))
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_1()
        assert all(c.passed for c in result.checks)

    def test_check_phase_2_skipped_without_ref(self, tmp_path: Path) -> None:
        """Phase 2: skipped when has_ref=False."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_2(has_ref=False)
        assert result.skipped

    def test_check_phase_1_regions_only_does_not_set_has_ref(self, tmp_path: Path) -> None:
        """Regression: regions.json existing alone must not satisfy has_ref.

        The supplementary phase-1 checks (scroll-video, transitions, regions.json)
        can pass independently. Only static/ref/ screenshots is the canonical
        "reference exists" signal — the run_status() codepath at pipeline.py uses
        phase_1.checks[0].passed to decide whether Phase 2 may proceed.
        """
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "regions.json").write_text(json.dumps({"regions": []}))
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_1()
        assert result.checks[0].passed is False, "static/ref/ screenshots must fail"
        assert result.checks[3].passed is True, "regions.json must pass"
        # The fix: has_ref derives from the canonical first check, not any().
        assert result.checks[0].passed is False

    def test_check_phase_3_no_app_dir(self, tmp_path: Path) -> None:
        """Phase 3: no app directory → sets next_phase."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        p.next_phase = ""  # Reset
        result = p.check_phase_3()
        assert result.name == "3"
        assert p.next_phase == "3"

    def test_check_phase_3_with_tsx_files(self, tmp_path: Path) -> None:
        """Phase 3: tsx files present → passes."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        comp_dir = tmp_path / "src" / "components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "Hero.tsx").write_text("export default function Hero() {}")
        p = self._make_pipeline(tmp_path, ref_dir)
        p.next_phase = ""
        result = p.check_phase_3()
        assert result.name == "3"
        # next_phase should NOT be set to "3" since component exists
        assert p.next_phase != "3"

    def test_check_phase_4_no_impl(self, tmp_path: Path) -> None:
        """Phase 4: no impl screenshots → sets next_phase."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        p.next_phase = ""
        result = p.check_phase_4()
        assert result.name == "4"
        assert p.next_phase == "4"

    def test_check_phase_4_with_impl(self, tmp_path: Path) -> None:
        """Phase 4: impl screenshots and diffs present → next_phase set to 4 (verify step)."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        impl_dir = ref_dir / "static" / "impl"
        impl_dir.mkdir(parents=True)
        for i in range(5):
            (impl_dir / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        diff_dir = ref_dir / "static" / "diff"
        diff_dir.mkdir(parents=True)
        (diff_dir / "diff_0.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        p = self._make_pipeline(tmp_path, ref_dir)
        p.next_phase = ""
        result = p.check_phase_4()
        assert result.name == "4"
        # Phase 4 always sets next_phase to "4" when no prior phase was incomplete
        assert p.next_phase == "4"

    def test_set_next_first_incomplete_wins(self, tmp_path: Path) -> None:
        """_set_next only records the first incomplete phase."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        p._set_next("1", "Do phase 1")
        p._set_next("2", "Do phase 2")
        assert p.next_phase == "1"
        assert p.next_step == "Do phase 1"

    def test_json_output(self, tmp_path: Path) -> None:
        """Pipeline.run(json_output=True) prints JSON summary."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        (ref_dir / "pipeline-state.json").write_text(
            json.dumps(
                {
                    "component": "comp",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_steps": [
                        "reference",
                        "extraction",
                        "bundle",
                        "spec",
                        "pre-generate",
                        "post-implement",
                        "section-compare",
                    ],
                    "current_gate": "done",
                    "last_updated": "2026-01-01T01:00:00Z",
                }
            )
        )
        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            with patch("ui_clone.pipeline._check_dependencies", return_value=[]):
                p = Pipeline("https://example.com", "comp", "sess")
                p.project_root = tmp_path
                p.ref_dir = ref_dir
                result = p.run(json_output=True)
        assert result == 0


class TestCheckPhase2:
    """Unit tests for Pipeline.check_phase_2 — the extraction phase."""

    def _make_pipeline(self, tmp_path: Path, ref_dir: Path) -> Any:
        with patch("ui_clone.pipeline.find_project_root", return_value=tmp_path):
            p = Pipeline("https://example.com", ref_dir.name, "sess")
            p.project_root = tmp_path
            p.ref_dir = ref_dir
        return p

    def test_skipped_when_no_ref(self, tmp_path: Path) -> None:
        """check_phase_2(has_ref=False) → skipped."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        result = p.check_phase_2(has_ref=False)
        assert result.skipped
        assert result.skip_reason

    def test_empty_ref_dir_sets_next_phase_2(self, tmp_path: Path) -> None:
        """has_ref=True but no artifacts → next_phase=2."""
        ref_dir = tmp_path / "tmp" / "ref" / "comp"
        ref_dir.mkdir(parents=True)
        p = self._make_pipeline(tmp_path, ref_dir)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"

    def _add_breakpoints(self, ref_dir: Path) -> None:
        """Add detected-breakpoints.json — missing from ref_dir_with_artifacts fixture."""
        (ref_dir / "detected-breakpoints.json").write_text(
            json.dumps({"breakpoints": [768, 1024, 1440]})
        )

    def test_all_extraction_artifacts_present(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """All extraction artifacts present → no next_phase set to 2."""
        self._add_breakpoints(ref_dir_with_artifacts)
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase != "2"

    def test_missing_structure_json_sets_next(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Missing structure.json → next_phase=2 with dom-extraction hint."""
        self._add_breakpoints(ref_dir_with_artifacts)
        (ref_dir_with_artifacts / "structure.json").unlink()
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"
        assert "dom-extraction" in p.next_step.lower() or "structure" in p.next_step.lower()

    def test_missing_bundles_sets_next(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Missing bundles/ directory → next_phase=2 with bundle hint (first-incomplete wins)."""
        import shutil

        self._add_breakpoints(ref_dir_with_artifacts)
        shutil.rmtree(ref_dir_with_artifacts / "bundles")
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"
        assert "bundle" in p.next_step.lower()

    def test_missing_transition_spec_sets_next(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Missing transition-spec.json → next_phase=2."""
        self._add_breakpoints(ref_dir_with_artifacts)
        (ref_dir_with_artifacts / "transition-spec.json").unlink()
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"
        assert "transition-spec" in p.next_step.lower() or "bundle" in p.next_step.lower()

    def test_missing_extracted_json_sets_next(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Missing extracted.json → next_phase=2 with assemble hint."""
        self._add_breakpoints(ref_dir_with_artifacts)
        (ref_dir_with_artifacts / "extracted.json").unlink()
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"
        assert "assemble" in p.next_step.lower() or "extracted" in p.next_step.lower()

    def test_missing_component_map_sets_next(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Missing component-map.json → next_phase=2 with section-audit hint."""
        self._add_breakpoints(ref_dir_with_artifacts)
        (ref_dir_with_artifacts / "component-map.json").unlink()
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"
        assert "audit" in p.next_step.lower() or "component-map" in p.next_step.lower()

    def test_stale_extracted_json_warns(self, tmp_path: Path, ref_dir_with_artifacts: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """extracted.json older than its parent → staleness warning printed."""
        import os

        self._add_breakpoints(ref_dir_with_artifacts)
        now = time.time()
        os.utime(ref_dir_with_artifacts / "structure.json", (now, now))
        os.utime(ref_dir_with_artifacts / "extracted.json", (now - 5, now - 5))

        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        captured = capsys.readouterr()
        assert "STALE" in captured.out

    def test_few_js_chunks_warns(self, tmp_path: Path, ref_dir_with_artifacts: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Only 1-2 JS chunks → advisory warning printed."""
        import shutil

        self._add_breakpoints(ref_dir_with_artifacts)
        shutil.rmtree(ref_dir_with_artifacts / "bundles")
        bundles = ref_dir_with_artifacts / "bundles"
        bundles.mkdir()
        (bundles / "chunk-0.js").write_text("// single chunk")

        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        captured = capsys.readouterr()
        assert "1 JS chunk" in captured.out or "Only 1" in captured.out

    def test_missing_responsive_sizing_sets_next(self, tmp_path: Path, ref_dir_with_artifacts: Path) -> None:
        """Missing responsive/sizing-expressions.json → next_phase=2."""
        self._add_breakpoints(ref_dir_with_artifacts)
        (ref_dir_with_artifacts / "responsive" / "sizing-expressions.json").unlink()
        p = self._make_pipeline(tmp_path, ref_dir_with_artifacts)
        p.check_phase_2(has_ref=True)
        assert p.next_phase == "2"
        assert "sizing" in p.next_step.lower() or "responsive" in p.next_step.lower()


class TestDagDepsCoverage:
    """Verify that all artifacts in DEPS are checked by at least one gate."""

    def test_deps_artifacts_referenced_in_gates(self) -> None:
        """Every DEPS key and value should appear in at least one gate check."""
        from ui_clone import dag
        from ui_clone.gate import Gate

        # Collect all artifact names from DEPS
        all_artifacts = set(dag.DEPS.keys())
        for targets in dag.DEPS.values():
            all_artifacts.update(targets)

        # Read gate.py source and check each artifact appears somewhere
        import inspect

        source = inspect.getsource(Gate)

        missing = []
        for artifact in all_artifacts:
            if artifact not in source:
                missing.append(artifact)

        assert not missing, f"DEPS artifacts not referenced in Gate: {missing}"
