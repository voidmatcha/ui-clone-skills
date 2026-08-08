"""Shared test helpers for gates/.

Extracted from test_boundary.py so split test files import from a
single source of truth instead of duplicating ~100 lines of
prelude each. (Codex Item-6 follow-up.)
"""

import json
from pathlib import Path

from ui_clone.check_inputs import compute_check_input_hash, sidecar_path


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
    (ref / "runtime-media.json").write_text(json.dumps({
        "schemaVersion": 1,
        "url": "https://example.com",
        "videos": [],
        "totals": {"video": 0},
        "sources": {"extractor": "runtime-media.sh", "scrollSamples": 5},
    }))
    (ref / "required-media.json").write_text(json.dumps({
        "schemaVersion": 1,
        "videos": [],
        "lottie": [],
        "totals": {"video": 0, "lottie": 0},
        "sources": {
            "extractor": "required-media.sh",
            "htmlSectionsScanned": 0,
            "runtimeMediaScanned": True,
            "bundlesScanned": 0,
        },
    }))



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



def _read_impl_marker(ref: Path) -> Path | None:
    marker = ref / ".impl-root"
    if not marker.is_file():
        return None
    try:
        first = marker.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    if not first:
        return None
    candidate = Path(first).expanduser()
    return candidate if candidate.is_dir() else None


def _write_impl_fixture(ref: Path) -> Path:
    """Write a minimal implementation tree plus fingerprintable ref inputs."""
    impl = _read_impl_marker(ref) or ref.parent / "impl"
    (impl / "src").mkdir(parents=True, exist_ok=True)
    (impl / "public").mkdir(exist_ok=True)
    (impl / "package.json").write_text(
        '{"name":"post-implement-fixture"}\n',
        encoding="utf-8",
    )
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <main>Fixture</main>}\n",
        encoding="utf-8",
    )
    (impl / "public" / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    (ref / "dom-scaffold.json").write_text(
        json.dumps({"sections": [], "tree": {"tag": "body"}}),
        encoding="utf-8",
    )
    (ref / "runtime-text.json").write_text(
        json.dumps({"blocks": ["Fixture"]}),
        encoding="utf-8",
    )
    (ref / "asset-substitution.json").write_text(
        json.dumps({"substitutions": []}),
        encoding="utf-8",
    )
    (ref / "regions.json").write_text(
        json.dumps({"regions": []}),
        encoding="utf-8",
    )
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [], "totalCount": 0}),
        encoding="utf-8",
    )
    (ref / "component-map.json").write_text(
        json.dumps({"sections": [], "sectionCount": 0}),
        encoding="utf-8",
    )
    return impl


def _stamp_check_input_hash(ref: Path, check_id: str, impl: Path | None = None) -> None:
    """Record the current declared inputs for a required-check fixture."""
    resolved_impl = impl or _read_impl_marker(ref)
    digest = compute_check_input_hash(resolved_impl, ref, check_id)
    assert digest is not None and digest != "", (
        f"{check_id} has no fingerprintable inputs"
    )
    sidecar_path(ref, check_id).write_text(digest + "\n", encoding="utf-8")


def _post_implement_baseline(ref: Path, *, with_impl: bool = True) -> None:
    """Write minimal artifacts so gate_post_implement passes baseline checks."""
    if with_impl:
        _write_impl_fixture(ref)
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
    # Seed fixture.js into bundles/ so _check_spec_bundle_grounding (F from
    # docs/claude-fidelity-analysis.md) passes for baseline runs. Tests that
    # exercise the grounding check itself overwrite transition-spec.json
    # after this baseline.
    bundles = ref / "bundles"
    bundles.mkdir(parents=True, exist_ok=True)
    (bundles / "fixture.js").write_text("// fixture bundle for baseline", encoding="utf-8")
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
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
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
    cleanup = target.with_name("cleanup-sessions.sh")
    cleanup.write_text("#!/usr/bin/env bash\nexit 0\n")
    cleanup.chmod(0o755)



__all__ = [
    "_write_pre_generate_baseline",
    "_write_valid_artifact_provenance",
    "_write_impl_fixture",
    "_post_implement_baseline",
    "_build_renamed_impl",
    "_project_root",
    "_run_verification_plan",
    "_fixture_all_signals",
    "_write_min_spec_artifacts",
    "_RESULT_TABLE_TEMPLATE",
    "_make_stub_compare",
]
