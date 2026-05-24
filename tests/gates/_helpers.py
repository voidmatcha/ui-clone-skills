"""Shared test helpers for gates/.

Extracted from test_boundary.py so split test files import from a
single source of truth instead of duplicating ~100 lines of
prelude each. (Codex Item-6 follow-up.)
"""

import json
from pathlib import Path


def _write_pre_generate_baseline(ref: Path) -> None:
    """Write enough artifacts for pre-generate so provenance is the only blocker."""
    (ref / "extracted.json").write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    (ref / "animation-init-styles.json").write_text(json.dumps({"elements": []}))
    (ref / "section-map.json").write_text(json.dumps({"sections": [], "totalCount": 0, "hasFooter": False}))
    (ref / "svg-text-elements.json").write_text(json.dumps([]))
    # Fix 9 — dom-scaffold.json now a pre-generate prereq.
    (ref / "dom-scaffold.json").write_text(json.dumps({"sections": [], "tree": {"tag": "body"}}))
    responsive = ref / "responsive"
    responsive.mkdir()
    (responsive / "sizing-expressions.json").write_text(json.dumps({"expressions": []}))
    (ref / "interactions-detected.json").write_text(json.dumps({"interactions": [], "hasPreloader": False}))
    (ref / "hover-css-rules.json").write_text(json.dumps([]))
    (ref / "transition-coverage.json").write_text(json.dumps({"animatedElements": [], "staticElements": []}))
    (ref / "element-roles.json").write_text(json.dumps({"roles": []}))
    (ref / "element-groups.json").write_text(json.dumps({"groups": []}))
    (ref / "layout-decisions.json").write_text(json.dumps({"decisions": []}))
    (ref / "component-map.json").write_text(json.dumps({"sections": [], "sectionCount": 0}))



def _write_valid_artifact_provenance(ref: Path) -> None:
    artifacts = [
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
    (ref / "artifact-provenance.json").write_text(json.dumps({
        "artifacts": [
            {
                "path": artifact,
                "source": "agent-browser-eval" if artifact != "transition-spec.json" else "bundle-grep",
                "evidence": [artifact],
                "generatedAt": "2026-05-14T00:00:00Z",
            }
            for artifact in artifacts
        ],
    }))



def _post_implement_baseline(ref: Path) -> None:
    """Write minimal artifacts so gate_post_implement passes baseline checks."""
    (ref / "extracted.json").write_text(json.dumps({"sections": [], "url": "https://example.com"}))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "fixture-reveal-on-scroll",
            "trigger": "intersection",
            "source_chunk": "fixture.js",
            "bundle_branch": "main",
            "target": ".fixture",
            "animation": "opacity-translateY",
            "reference_frames": ["frame_00.png"],
        }]
    }))
    screenshots = ref / "static" / "ref"
    screenshots.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (screenshots / f"scroll_{i:02d}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []}),
        encoding="utf-8",
    )
    sections = ref / "sections"
    sections.mkdir(exist_ok=True)
    (sections / "result.txt").write_text(
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    transitions = ref / "transitions"
    transitions.mkdir(exist_ok=True)
    (transitions / "result.txt").write_text(
        "Transition compare: 1 PASS, 0 FAIL\n"
        "✅ PASS .fixture\n",
        encoding="utf-8",
    )
    # visual-debug-stamp.json: required when sections/result.txt has ≥1 PASS
    # (L33 cheat guard — canonical auto-verify.sh entry must have been used).
    (ref / "visual-debug-stamp.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "passed": True,
            "exitCode": 0,
            "totalChecks": 4,
            "totalFail": 0,
            "phaseE": False,
        }),
        encoding="utf-8",
    )



def _build_renamed_impl(loop_root: Path, name: str, page_loc: int) -> Path:
    """Helper for rename-resolver tests. Creates
    `loop_root/<name>/{package.json, src/app/page.tsx}` with `page_loc` LOC.
    Returns the impl dir.
    """
    impl = loop_root / name
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "package.json").write_text('{"name":"clone","version":"0.1.0"}\n')
    (impl / "src" / "app" / "page.tsx").write_text(
        "\n".join(f"// line {i}" for i in range(page_loc)) + "\n", encoding="utf-8"
    )
    return impl



def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]



def _run_verification_plan(ref_dir: Path, tier: str | None = None) -> dict:
    import subprocess
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    cmd = ["bash", str(script), str(ref_dir)]
    if tier is not None:
        cmd.append(f"--tier={tier}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"verification-plan.sh failed: {proc.stderr}"
    return json.loads((ref_dir / "verification-plan.json").read_text())  # type: ignore[no-any-return]



def _fixture_all_signals(ref: Path) -> None:
    """Write extraction artifacts that fire every conditional signal so the
    dispatch produces one of every check type."""
    (ref / "external-sdks.json").write_text(json.dumps({
        "detected": ["useScroll", "scrollYProgress"]
    }))
    (ref / "interactions-detected.json").write_text(json.dumps({
        "interactions": [{"trigger": "hover", "target": ".btn"}]
    }))
    (ref / "regions.json").write_text(json.dumps({
        "click": [{"name": "tabs", "triggerType": "click-cycle", "selector": ".tab"}]
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "x", "trigger": "hover"}]
    }))
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "scrollTrigger": [{"start": 0}]
    }))
    (ref / "paid-features.json").write_text(json.dumps({
        "paidFonts": [{"family": "Foo", "cdn": "use.typekit.net", "decision": None}]
    }))



def _write_min_spec_artifacts(ref: Path, transitions: list[dict] | None = None) -> None:
    """Write the minimum artifacts gate_spec needs so we can exercise the
    cross-validation branch without satisfying every other check."""
    (ref / "bundle-map.json").write_text(json.dumps({}))
    (ref / "external-sdks.json").write_text(json.dumps({}))
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": transitions or []})
    )



_RESULT_TABLE_TEMPLATE = (
    "| Section | AE | AE/Mpx | Severity | Status |\n"
    "|---------|-----|--------|----------|--------|\n"
    "| hero    | 1500 | 1200 | critical | ❌ |\n"
    "| footer  | 30000 | 25000 | critical | ❌ |\n"
    "| nav     | 0 | 0 | ok | ✅ |\n"
)



def _make_stub_compare(plugin_root: Path) -> None:
    """Write a video-transition-compare.sh stub that exits 0 immediately.

    The real script (scripts/verify/video-transition-compare.sh) launches two
    agent-browser sessions and records video — neither tractable in a unit
    test. The fan-out logic in hover/click-state-compare lives in the OUTER
    loop, so a no-op inner is enough to verify per-viewport dirs + result.txt
    sections are emitted correctly.
    """
    target = plugin_root / "scripts" / "verify" / "video-transition-compare.sh"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "#!/usr/bin/env bash\n"
        "# stub: no-op inner compare for unit tests\n"
        "echo \"[stub] called with: $*\"\n"
        "exit 0\n"
    )
    target.chmod(0o755)



__all__ = [
    "_write_pre_generate_baseline",
    "_write_valid_artifact_provenance",
    "_post_implement_baseline",
    "_build_renamed_impl",
    "_project_root",
    "_run_verification_plan",
    "_fixture_all_signals",
    "_write_min_spec_artifacts",
    "_RESULT_TABLE_TEMPLATE",
    "_make_stub_compare",
]
