"""Shared test helpers for hooks/.

Extracted from test_pre_bash.py so split test files import from a
single source of truth instead of duplicating ~100 lines of
prelude each. (Codex Item-6 follow-up.)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def run_hook(
    module: str, stdin_data: str = "", env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run a hook module as a subprocess, returning CompletedProcess."""
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", module],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=merged_env,
    )



def make_search_root(tmp_path: Path) -> Path:
    """Create a tmp/ref directory and return it."""
    sr = tmp_path / "tmp" / "ref"
    sr.mkdir(parents=True)
    return sr



def make_ref_dir(search_root: Path, name: str = "test-session") -> Path:
    """Create a ref dir inside search_root."""
    ref = search_root / name
    ref.mkdir(parents=True, exist_ok=True)
    return ref



def set_active_marker(ref_dir: Path, age_seconds: float = 0.0) -> Path:
    """Touch a .ui-re-active marker inside ref_dir, optionally with a past mtime."""
    marker = ref_dir / ".ui-re-active"
    marker.touch()
    if age_seconds > 0:
        t = time.time() - age_seconds
        os.utime(marker, (t, t))
    return marker



def write_extracted_json(ref_dir: Path) -> None:
    """Write a minimal extracted.json so mtime fallback picks this ref."""
    (ref_dir / "extracted.json").write_text(
        json.dumps({"sections": [], "url": "https://example.com"}),
        encoding="utf-8",
    )



def _populate_pre_generate_artifacts(ref_dir: Path) -> None:
    """Write the minimal artifact set that makes gate_pre_generate pass.

    Sets parent artifacts to a fixed past mtime and extracted.json to a newer
    mtime so the DAG staleness check doesn't flag anything.
    """
    base_time = time.time() - 2.0
    extracted_time = base_time + 1.0

    # Core extraction artifacts
    (ref_dir / "structure.json").write_text(json.dumps({"sections": [], "totalCount": 0}))
    (ref_dir / "styles.json").write_text(json.dumps({"selectors": {}}))
    (ref_dir / "section-map.json").write_text(
        json.dumps({"sections": [], "totalCount": 0, "hasFooter": False})
    )
    (ref_dir / "component-map.json").write_text(json.dumps({"sections": [], "sectionCount": 0}))
    (ref_dir / "interactions-detected.json").write_text(
        json.dumps({"interactions": [], "hasPreloader": False})
    )
    (ref_dir / "hover-css-rules.json").write_text(json.dumps({"rules": []}))
    # Fix 9 — dom-scaffold.json now a pre-generate prereq.
    (ref_dir / "dom-scaffold.json").write_text(json.dumps({"sections": [], "tree": {"tag": "body"}}))
    (ref_dir / "transition-spec.json").write_text(json.dumps({"transitions": []}))
    (ref_dir / "bundle-map.json").write_text(json.dumps({"chunks": []}))
    (ref_dir / "animation-init-styles.json").write_text(json.dumps({"elements": []}))
    (ref_dir / "svg-text-elements.json").write_text(json.dumps({"elements": []}))
    (ref_dir / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [], "staticElements": []})
    )
    (ref_dir / "element-roles.json").write_text(json.dumps({"roles": []}))
    (ref_dir / "element-groups.json").write_text(json.dumps({"groups": []}))
    (ref_dir / "layout-decisions.json").write_text(json.dumps({"decisions": []}))
    responsive = ref_dir / "responsive"
    responsive.mkdir(exist_ok=True)
    (responsive / "sizing-expressions.json").write_text(json.dumps({"expressions": []}))

    # Set all parents to base_time
    for name in [
        "structure.json", "styles.json", "section-map.json", "component-map.json",
        "interactions-detected.json", "hover-css-rules.json", "transition-spec.json",
        "bundle-map.json", "animation-init-styles.json", "svg-text-elements.json",
        "transition-coverage.json",
    ]:
        p = ref_dir / name
        if p.exists():
            os.utime(p, (base_time, base_time))

    # extracted.json must be strictly newer
    (ref_dir / "extracted.json").write_text(
        json.dumps({"sections": [], "url": "https://example.com"})
    )
    os.utime(ref_dir / "extracted.json", (extracted_time, extracted_time))

    # generation-plan.json — required by gate_pre_generate (research1 fix)
    (ref_dir / "generation-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "componentList": [], "guidance": {}})
    )
    os.utime(ref_dir / "generation-plan.json", (extracted_time, extracted_time))

    provenance_artifacts = [
        "extracted.json",
        "transition-spec.json",
        "animation-init-styles.json",
        "section-map.json",
        "svg-text-elements.json",
        "responsive/sizing-expressions.json",
        "interactions-detected.json",
        "transition-coverage.json",
        "component-map.json",
    ]
    (ref_dir / "artifact-provenance.json").write_text(json.dumps({
        "artifacts": [
            {
                "path": artifact,
                "source": "agent-browser-eval" if artifact != "transition-spec.json" else "bundle-grep",
                "evidence": [artifact],
                "generatedAt": "2026-05-14T00:00:00Z",
            }
            for artifact in provenance_artifacts
        ],
    }))



def _bash_input(cmd: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})



def _set_done_state(ref_dir: Path) -> None:
    """Write pipeline-state.json with current_gate='done'."""
    from ui_clone.state import GATE_ORDER as _GO
    (ref_dir / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": ref_dir.name,
                "started_at": "2026-01-01T00:00:00Z",
                "completed_steps": list(_GO),
                "current_gate": "done",
                "last_updated": "2026-01-01T02:00:00Z",
            }
        )
    )



def _set_section_compare_state(ref_dir: Path) -> None:
    """Write pipeline-state.json with current_gate='section-compare'."""
    from ui_clone.state import GATE_ORDER as _GO
    (ref_dir / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": ref_dir.name,
                "started_at": "2026-01-01T00:00:00Z",
                "completed_steps": list(_GO[:-1]),
                "current_gate": "section-compare",
                "last_updated": "2026-01-01T02:00:00Z",
            }
        )
    )



def _set_extraction_state(ref_dir: Path) -> None:
    """Write pipeline-state.json after reference pass, before extraction pass."""
    (ref_dir / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": ref_dir.name,
                "started_at": "2026-01-01T00:00:00Z",
                "completed_steps": ["reference"],
                "current_gate": "extraction",
                "last_updated": "2026-01-01T00:05:00Z",
                "gate_fail_counts": {},
                "unclonable_reasons": [],
            }
        )
    )



def _set_post_implement_state(ref_dir: Path) -> None:
    """Write pipeline-state.json after pre-generate pass; implementation may run."""
    from ui_clone.state import GATE_ORDER as _GO
    (ref_dir / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": ref_dir.name,
                "started_at": "2026-01-01T00:00:00Z",
                "completed_steps": list(_GO[:6]),
                "current_gate": "post-implement",
                "last_updated": "2026-01-01T01:30:00Z",
                "gate_fail_counts": {},
                "unclonable_reasons": [],
            }
        )
    )



def _write_passing_result_txt(ref_dir: Path) -> None:
    sections_dir = ref_dir / "sections"
    sections_dir.mkdir(exist_ok=True)
    (sections_dir / "result.txt").write_text(
        "Section 01 hero: ✅ PASS\nSection 02 cta: ✅ PASS\n"
    )



def _write_failing_result_txt(ref_dir: Path) -> None:
    sections_dir = ref_dir / "sections"
    sections_dir.mkdir(exist_ok=True)
    (sections_dir / "result.txt").write_text(
        "Section 01 hero: ✅ PASS\nSection 02 cta: ❌ FAIL diff=12.4%\n"
    )



def _write_missing_impl_result_txt(ref_dir: Path) -> None:
    sections_dir = ref_dir / "sections"
    sections_dir.mkdir(exist_ok=True)
    (sections_dir / "result.txt").write_text(
        "Section 01 hero: ✅ PASS\nSection 02 cta: ⚠️ MISSING impl\n"
    )



def _set_phase2_only_state(ref_dir: Path) -> None:
    """Phase 2 ran but Phase 3+ has not. current_gate is still at the
    bundle gate — pre-generate has not been reached. Mimics the loop-codex-5
    state where extract-dom + dom-scaffold produced their artifacts but
    bundle / paid-features / spec / pre-generate were never run.
    """
    (ref_dir / "pipeline-state.json").write_text(
        json.dumps({
            "component": ref_dir.name,
            "started_at": "2026-01-01T00:00:00Z",
            "completed_steps": ["reference", "extraction"],
            "current_gate": "bundle",
            "last_updated": "2026-01-01T00:30:00Z",
        })
    )



def _set_pre_generate_passed_state(ref_dir: Path) -> None:
    """pre-generate gate has passed — scaffold commands should run unblocked."""
    (ref_dir / "pipeline-state.json").write_text(
        json.dumps({
            "component": ref_dir.name,
            "started_at": "2026-01-01T00:00:00Z",
            "completed_steps": [
                "reference", "extraction", "bundle",
                "paid-features", "spec", "pre-generate",
            ],
            "current_gate": "post-implement",
            "last_updated": "2026-01-01T01:00:00Z",
        })
    )



__all__ = [
    "run_hook",
    "make_search_root",
    "make_ref_dir",
    "set_active_marker",
    "write_extracted_json",
    "_populate_pre_generate_artifacts",
    "_bash_input",
    "_set_done_state",
    "_set_section_compare_state",
    "_set_extraction_state",
    "_set_post_implement_state",
    "_write_passing_result_txt",
    "_write_failing_result_txt",
    "_write_missing_impl_result_txt",
    "_set_phase2_only_state",
    "_set_pre_generate_passed_state",
]
