import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ._helpers import (
    _fixture_all_signals,
    _project_root,
    _run_verification_plan,
)

if TYPE_CHECKING:
    from ui_clone.gate import Gate


def test_verification_plan_omits_video_motion_check_when_no_motion_signal(tmp_path: Path) -> None:
    """No motion signals → no video-motion-compare row (cheap-check discipline).

    The video-motion check loads ref + impl in two agent-browser sessions,
    records ~5-10s of video, extracts 60fps frames, and SSIMs every pair.
    Skip it on static pages.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasScrollScrub"] is False
    assert plan["signals"]["hasSplash"] is False
    assert plan["signals"]["hasIOReveal"] is False
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "video-motion-compare" not in ids, (
        f"video-motion row present without motion signal: {ids}"
    )


def test_verification_plan_emits_click_state_check_when_click_trigger_detected(
    tmp_path: Path,
) -> None:
    """regions.json with triggerType: click-* → click-state-compare row required.

    Click-state transitions (tabs/accordions/modals/menu toggles) have their
    own motion arc that hover-compare + section-compare never exercise.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(
        json.dumps({"click": [{"name": "tabs", "triggerType": "click-cycle", "selector": ".tab"}]})
    )
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasClickStateTransition"] is True
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "click-state-compare" in ids, (
        f"click-state row missing when click trigger present: {ids}"
    )


def test_verification_plan_omits_click_state_check_when_no_click_trigger(tmp_path: Path) -> None:
    """No click triggers anywhere → no click-state-compare row."""
    ref = tmp_path / "ref"
    ref.mkdir()
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasClickStateTransition"] is False
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "click-state-compare" not in ids


def test_verification_plan_emits_hover_state_check_when_hover_signal_detected(
    tmp_path: Path,
) -> None:
    """hasHover=true → hover-state-compare row required.

    Static transition-compare verifies idle/hover end-states only. hover-state-compare
    runs 60fps video over the entry arc to catch easing/duration divergence on
    hover transitions — same bug class as video-motion-compare for scroll motion.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "interactions-detected.json").write_text(
        json.dumps({"interactions": [{"trigger": "hover", "target": ".btn"}]})
    )
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasHover"] is True
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "hover-state-compare" in ids, f"hover-state row missing when hasHover=true: {ids}"
    # transition-compare should remain — they cover different bug classes.
    assert "transition-compare" in ids, (
        f"transition-compare should also be present alongside hover-state: {ids}"
    )
    assert "hover-tree-diff" in ids, (
        f"hover-tree-diff should be present to catch impl-only hover motion: {ids}"
    )


def test_verification_plan_omits_hover_state_check_when_no_hover_signal(tmp_path: Path) -> None:
    """No hover signal → no hover-state-compare row.

    Static pages without hover interactions should skip the 60fps hover sweep
    (it loads ref + impl and records a video per target — expensive on no-op).
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasHover"] is False
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "hover-state-compare" not in ids


def test_verification_plan_emits_runtime_spec_coverage_when_dump_and_spec_present(
    tmp_path: Path,
) -> None:
    """animation-runtime-dump.json + transition-spec.json present → runtime-spec-coverage row required.

    Turns transition-spec-rules.md Rule 7 ("consult animation-runtime-dump.json
    when authoring transition-spec.json") from an advisory into an enforced
    gate. If the dump shows ScrollTrigger entries but the spec has zero scroll
    entries, this check fails post-implement.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(
        json.dumps(
            {
                "scrollTrigger": [{"start": 100, "end": 500}],
                "webAnimations": None,
                "lenis": None,
                "ix2": None,
                "gsap": None,
            }
        )
    )
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "hero", "trigger": "scroll", "type": "scroll-driven"}]})
    )
    plan = _run_verification_plan(ref)
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "runtime-spec-coverage" in ids, (
        f"runtime-spec-coverage row missing when dump + spec both present: {ids}"
    )


def test_verification_plan_omits_runtime_spec_coverage_when_dump_absent(tmp_path: Path) -> None:
    """No animation-runtime-dump.json → no runtime-spec-coverage row.

    Pre-Phase-0 captures (older runs or pages without runtime animations) won't
    have the dump. The coverage check should silently skip rather than fail.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "x", "trigger": "hover"}]})
    )
    plan = _run_verification_plan(ref)
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "runtime-spec-coverage" not in ids


def test_verification_plan_emits_spec_implementation_coverage_when_spec_present(
    tmp_path: Path,
) -> None:
    """transition-spec.json present → spec-implementation-coverage row required.

    Catches the "selector matched but no motion declared" gap:
    transition-spec-coverage answers "does the impl mention this entry?", but
    spec-implementation-coverage answers "and does the impl actually animate
    it?". Both rows must dispatch when transition-spec.json exists so the
    presence check (pre-generate sanity) and the declaration check
    (post-generate enforcement) cover the spec→generation seam end-to-end.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "x", "trigger": "hover"}]})
    )
    plan = _run_verification_plan(ref)
    ids = {c["id"] for c in plan["requiredChecks"]}
    assert "spec-implementation-coverage" in ids, (
        f"spec-implementation-coverage row missing when transition-spec.json present: {ids}"
    )
    # The presence row must remain — they cover different bug classes
    # (presence vs declaration) and the cheaper presence check stays at quick.
    assert "transition-spec-coverage" in ids


def test_verification_plan_uses_motion_proof_for_scroll_spec_without_hover(
    tmp_path: Path,
) -> None:
    """Scroll specs require motion proof, not hover/end-state comparison."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {"transitions": [{"id": "hero-scroll", "trigger": "scroll", "type": "scroll-driven"}]}
        )
    )

    plan = _run_verification_plan(ref)
    ids = [c["id"] for c in plan["requiredChecks"]]

    assert "transition-compare" not in ids, (
        "transition-compare is hover/end-state evidence and must not be a "
        f"fallback for scroll-only specs: {ids}"
    )
    assert "video-motion-compare" in ids
    assert "transition-fires" in ids
    assert "transition-proof" in ids


def test_verification_plan_spec_implementation_coverage_tier_is_standard(tmp_path: Path) -> None:
    """spec-implementation-coverage must be tagged tier=standard.

    The row is meaningful only after implementation source exists — at
    quick tier (inner iteration loop, often before generation), it would
    silently warn on every entry. Standard tier is the first level where the
    declaration check pays off. Locking the tier here prevents a future
    refactor from accidentally promoting it back to quick.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "x", "trigger": "hover"}]})
    )
    plan = _run_verification_plan(ref)
    entry = next(c for c in plan["requiredChecks"] if c["id"] == "spec-implementation-coverage")
    assert entry["tier"] == "standard", f"expected standard, got {entry['tier']!r}"


def test_verification_plan_default_tier_is_comprehensive(tmp_path: Path) -> None:
    """Default (no --tier flag, no env) must produce a comprehensive plan to
    preserve unconditional-dispatch behavior from before the tier system.

    Backward-compat lock: any change that flips this default needs a
    documented reason and a migration note for downstream consumers.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _fixture_all_signals(ref)
    plan = _run_verification_plan(ref)
    assert plan["tier"] == "comprehensive"


def test_verification_plan_quick_tier_filters_to_static_checks(tmp_path: Path) -> None:
    """tier=quick must emit only the static / JSON-comparison checks.

    Static-only set (with all signals firing): unresolved-imports, hydration-check,
    tailwind-transform-conflict, transition-spec-coverage, runtime-spec-coverage,
    plus the static mirror-detection gates (text-fidelity-check, dom-mirror-check)
    and proxy-mirror-check, which blocks original-runtime proxy/cache mirrors;
    plus the ref-screenshot-asset guard (static filesystem scan +
    sha256 fingerprint of impl tree vs ref's captured screenshot dirs);
    which are pure static AST/tree comparison — no browser, no LLM, no IO.
    Everything else (one-shot browser + 60fps video) must be filtered out.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _fixture_all_signals(ref)
    plan = _run_verification_plan(ref, tier="quick")
    assert plan["tier"] == "quick"
    ids = {c["id"] for c in plan["requiredChecks"]}
    assert ids == {
        # Pure static import-graph scan over src/**/*.tsx — no browser, no
        # node_modules, no build. Belongs at quick because a tree that cannot
        # resolve its own imports must fail in the inner loop, not after one.
        "unresolved-imports",
        "hydration-check",
        "tailwind-transform-conflict",
        "transition-spec-coverage",
        "runtime-spec-coverage",
        "text-fidelity-check",
        "dom-mirror-check",
        "proxy-mirror-check",
        "ref-screenshot-asset",
        # Bundle-paste detection:
        "bundle-paste",
        # Static source-coherence checks:
        "entry-coherence",
        "scaffold-residue",
        "html-paste",
        # Static CSS mirror detection:
        "css-mirror",
        # Explicit invalidation stamp:
        "invalidation",
        # Scaffold-warn placeholders:
        "scaffold-warn",
        # ui-capture handoff contract — regions with triggerType must enumerate
        # concrete ref clip/video artifacts. Pure JSON + file stat.
        "capture-artifact-inventory",
        # Required-media coverage self-skips when ref has no required
        # video/Lottie/SVG.
        "required-media-coverage",
        # font-binaries-present — unconditional static check; verifies
        # root-relative font binaries landed in impl/public (self-skips/pass
        # when transfer-fonts hasn't run). Pure JSON + filesystem stat.
        "font-binaries-present",
        # Monolithic implementation and motion coverage:
        "monolithic-impl",
        "motion-coverage",
        # scroll-engine-parity — engine class match (Lenis / GSAP
        # ScrollTrigger / scroll-scrub / scroll-pin):
        "scroll-engine-parity",
        # library-usage — ref animation libs (bundle-map / external-sdks) must be
        # imported in impl source, not just installed. Fires whenever either ref
        # evidence file exists; pure static grep over impl source.
        "library-usage",
        # Hero-composite spot-check verifies the 4-element hero pattern
        # (video + button + h1/h2 + label). Pure static (regex over impl
        # source + structure.json walk), so tier=quick.
        "hero-composite-check",
        # Composite roll-ups + ref-js-loader. All three are pure file IO
        # (rollups read existing artifacts; loader does static grep on impl
        # source) so they belong in tier=quick.
        "runtime-proof",
        "transition-proof",
        "ref-js-loader",
        # Implementation-scope guard runs `git diff` only, no browser.
        "impl-scope",
        # Color-token diff against ref palette. Pure regex + math, no browser.
        "color-token-grounding",
        # Duration/easing grounding. Pure source scan, no browser.
        "duration-easing-grounding",
        # Dynamic-state guard — static impl scan for forced final-state
        # classes/styles; script self-skips when the reference has no dynamic
        # state signal.
        "forced-state-class",
            # Static analysis of ref-css-sanitize-report.json + impl source; script
            # self-skips when no sanitize report / no first-paint root lock exists.
            "body-opacity-unlock",
            # Local machine capacity estimate. Pure Python/sysctl/vm_stat probe,
            # no browser session.
            "capacity-probe",
            # Alignment parity — pure file IO over section-compare
            # enumeration artifacts (matches.json), no browser.
            "alignment-parity",
            # Junk-token — static impl-source scan + one cheap DOM eval
            # (same cost class as hydration-check).
            "junk-token",
        }, f"quick tier emitted unexpected ids: {ids}"
    # Every emitted check must be tagged tier=quick.
    tiers = {c["tier"] for c in plan["requiredChecks"]}
    assert tiers == {"quick"}, f"quick plan contains non-quick tiers: {tiers}"


def test_verification_plan_standard_tier_drops_video_checks(tmp_path: Path) -> None:
    """tier=standard must include quick + standard but drop comprehensive-tier
    checks (60fps video recording rows).

    Comprehensive-only ids that MUST be absent: video-motion-compare,
    hover-state-compare, click-state-compare. These each spin up two browser
    sessions and record ~5-10s of video per signal — the cost class the tier
    system exists to gate.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _fixture_all_signals(ref)
    plan = _run_verification_plan(ref, tier="standard")
    assert plan["tier"] == "standard"
    ids = {c["id"] for c in plan["requiredChecks"]}
    for forbidden in ("video-motion-compare", "hover-state-compare", "click-state-compare"):
        assert forbidden not in ids, f"{forbidden} should be filtered at standard: {ids}"
    # Standard tier should still keep the cheap quick checks.
    assert {"hydration-check", "tailwind-transform-conflict"}.issubset(ids)
    assert "blank-viewport" in ids
    # No emitted check is tagged comprehensive.
    tiers = {c["tier"] for c in plan["requiredChecks"]}
    assert "comprehensive" not in tiers, f"standard plan leaked comprehensive: {tiers}"


def test_verification_plan_comprehensive_tier_emits_all_checks(tmp_path: Path) -> None:
    """tier=comprehensive must reproduce the prior unconditional dispatch.

    With every signal firing, comprehensive should include every check id the
    dispatcher can emit. This guards against accidentally adding a new
    add_check row that gets tagged at a higher (nonexistent) tier and silently
    falls out of the comprehensive plan.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _fixture_all_signals(ref)
    plan = _run_verification_plan(ref, tier="comprehensive")
    assert plan["tier"] == "comprehensive"
    ids = {c["id"] for c in plan["requiredChecks"]}
    expected = {
        "hydration-check",
        "tailwind-transform-conflict",
        "scroll-end-completion",
        "video-motion-compare",
        "transition-compare",
        "hover-state-compare",
        "hover-tree-diff",
        "click-state-compare",
        "transition-spec-coverage",
        "runtime-spec-coverage",
    }
    missing = expected - ids
    assert not missing, f"comprehensive plan missing expected checks: {missing}"


def test_verification_plan_rejects_invalid_tier(tmp_path: Path) -> None:
    """An unknown --tier value must exit non-zero with a clear error.

    Surfacing a typo (e.g. --tier=quik) at script time prevents silent
    degradation where every check falls below the bogus level and the plan
    ships with `requiredChecks: []`.
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), "--tier=quik"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode != 0
    assert "invalid --tier" in proc.stderr.lower() or "invalid --tier" in proc.stdout.lower()


def test_verification_plan_emits_image_fidelity_when_visible_images_present(tmp_path: Path) -> None:
    """verification-plan.sh must add the image-fidelity + asset-transfer rows when
    visible-images.json exists. Both are block-severity so visible assets must
    be downloaded and referenced instead of replaced with placeholders.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "visible-images.json").write_text(
        json.dumps(
            [
                {"type": "image", "src": "https://cdn.example.com/x.jpg", "element": "img"},
            ]
        )
    )
    plan = _run_verification_plan(ref)
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "image-fidelity" in rows
    assert rows["image-fidelity"]["severity"] == "block"
    # Keep this quick-tier so image checks cannot be skipped by tier selection.
    assert rows["image-fidelity"]["tier"] == "quick"
    assert rows["image-fidelity"]["produces"] == "image-fidelity.json"
    # Asset-transfer is the companion check — code refs vs actual files in impl/public/.
    assert "asset-transfer" in rows
    assert rows["asset-transfer"]["severity"] == "block"
    assert rows["asset-transfer"]["tier"] == "quick"
    assert rows["asset-transfer"]["produces"] == "asset-transfer.json"


def test_verification_plan_emits_asset_utilization_when_visible_images_present(
    tmp_path: Path,
) -> None:
    """Downloaded visible assets must be referenced by implementation source.

    The `asset-utilization` row requires at least 60% referenced assets.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "visible-images.json").write_text(
        json.dumps(
            [
                {"type": "image", "src": "https://cdn.example.com/x.jpg", "element": "img"},
            ]
        )
    )
    plan = _run_verification_plan(ref)
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "asset-utilization" in rows
    assert rows["asset-utilization"]["severity"] == "block"
    assert rows["asset-utilization"]["tier"] == "quick"
    assert rows["asset-utilization"]["produces"] == "asset-utilization.json"


def test_verification_plan_emits_capture_artifact_inventory_for_trigger_regions(
    tmp_path: Path,
) -> None:
    """regions.json trigger metadata needs an artifact-manifest gate."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(
        json.dumps({"hover": [{"name": "btn", "triggerType": "css-hover", "selector": ".btn"}]})
    )
    plan = _run_verification_plan(ref, tier="quick")
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "capture-artifact-inventory" in rows
    assert rows["capture-artifact-inventory"]["severity"] == "block"
    assert rows["capture-artifact-inventory"]["tier"] == "quick"
    assert rows["capture-artifact-inventory"]["produces"] == "capture-artifact-inventory.json"


def test_verification_plan_emits_asset_placement_when_section_mapping_exists(
    tmp_path: Path,
) -> None:
    """visible assets with section/component maps need section-local placement enforcement."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "visible-images.json").write_text(
        json.dumps(
            [
                {"type": "image", "src": "https://cdn.example.com/x.jpg", "top": 100},
            ]
        )
    )
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"i": 0, "top": 0, "height": 500}]})
    )
    (ref / "component-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "file": "src/components/Hero.tsx"}]})
    )
    plan = _run_verification_plan(ref, tier="quick")
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "asset-placement" in rows
    assert rows["asset-placement"]["severity"] == "block"
    assert rows["asset-placement"]["tier"] == "quick"
    assert rows["asset-placement"]["produces"] == "asset-placement.json"


def test_verification_plan_emits_lottie_runtime_when_lottie_detected(tmp_path: Path) -> None:
    """Lottie/bodymovin evidence must dispatch a hard runtime/json gate.

    Without this row, an impl can replace the original animation with generic
    GSAP/CSS motion and still satisfy unrelated transition marker checks.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(
        json.dumps(
            {
                "resources": ["https://cdn.example.com/bodymovin.min.js"],
                "notes": "lottie-web registered animations",
            }
        )
    )
    plan = _run_verification_plan(ref, tier="quick")
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "lottie-runtime" in rows
    assert rows["lottie-runtime"]["severity"] == "block"
    assert rows["lottie-runtime"]["tier"] == "quick"
    assert rows["lottie-runtime"]["produces"] == "lottie-runtime.json"


# ── states-derived signal regressions ─────────────────────────────────


def test_verification_plan_derives_hasSplash_from_states_summary(tmp_path: Path) -> None:
    """Phase A `states/splash/summary.json` polls > 1
    must set HAS_SPLASH=true even when upstream interactions/dom-state-diff
    didn't flag it."""
    ref = tmp_path / "ref"
    ref.mkdir()
    splash = ref / "states" / "splash"
    splash.mkdir(parents=True)
    (splash / "summary.json").write_text(
        json.dumps(
            {
                "checked": True,
                "polls": 4,
                "reason": "stable-2s",
            }
        )
    )
    (splash / "trajectory.json").write_text(
        json.dumps(
            [
                {"ts_ms": 0, "bodyClass": "is-loading"},
                {"ts_ms": 800, "bodyClass": "is-loaded"},
            ]
        )
    )
    plan = _run_verification_plan(ref)
    # hasSplash=true forces splash-driven checks (lottie-runtime / runtime-proof
    # / scroll-end-completion / reveal-trigger). At minimum the plan should
    # report the signal as set.
    signals = plan.get("signals", {})
    assert signals.get("hasSplash") is True, (
        f"states/splash/summary.json polls=4 must set hasSplash=true; got signals: {signals}"
    )


def test_verification_plan_derives_hasHover_from_states_manifest(tmp_path: Path) -> None:
    """Phase C `states/hover/manifest.json` entries must set HAS_HOVER=true."""
    ref = tmp_path / "ref"
    ref.mkdir()
    hover = ref / "states" / "hover"
    hover.mkdir(parents=True)
    (hover / "manifest.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "abc123",
                        "kind": "css",
                        "file": "elem-abc123.json",
                        "selector": ".btn",
                        "activation": ".btn",
                        "changedCount": 0,
                    },
                    {
                        "id": "def456",
                        "kind": "js",
                        "file": "elem-def456.json",
                        "selector": "a.cta",
                        "activation": "a.cta",
                        "changedCount": 2,
                    },
                ],
            }
        )
    )
    plan = _run_verification_plan(ref)
    signals = plan.get("signals", {})
    assert signals.get("hasHover") is True, (
        f"states/hover/manifest.json with entries must set hasHover=true; got signals: {signals}"
    )


def test_verification_plan_regenerates_when_state_structure_spec_newer(
    tmp_path: Path,
) -> None:
    """state-structure-spec.json is a compact state rollup; refreshing it
    after a plan must invalidate the old plan just like states/* summaries."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "generatedAt": "2000-01-01T00:00:00Z",
                "requiredChecks": [],
                "signals": {},
            }
        )
    )
    (ref / "state-structure-spec.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "events": [{"phase": "click", "trigger": "click"}],
            }
        )
    )

    plan = _run_verification_plan(ref)
    assert plan["generatedAt"] != "2000-01-01T00:00:00Z"
    assert plan["signals"]["hasClickStateTransition"] is True


def test_verification_plan_emits_runtime_spec_coverage_when_anim_dump_alone(tmp_path: Path) -> None:
    """Runtime coverage must not depend on transition-spec.json.

    runtime-spec-coverage must register when animation-runtime-dump.json
    exists even if transition-spec.json is absent.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(
        json.dumps(
            {
                "scrollTrigger": [{"id": "st1", "start": "top 80%", "end": "bottom"}],
                "tweens": [{"duration": 0.8}],
            }
        )
    )
    # NOTE: no transition-spec.json is written.
    plan = _run_verification_plan(ref)
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "runtime-spec-coverage" in rows, (
        "animation-runtime-dump.json alone must register runtime-spec-coverage; "
        f"got rows: {list(rows.keys())}"
    )
    assert rows["runtime-spec-coverage"]["severity"] == "block"


def test_post_implement_skips_broad_hover_artifacts_for_reset_only_known_skip(tmp_path: Path) -> None:
    from ui_clone.gate import Gate

    loop_root = tmp_path / "scratch" / "reset-only"
    ref = loop_root / "tmp" / "ref" / "comp"
    transitions = ref / "transitions"
    transitions.mkdir(parents=True)
    _b1_resolvable_impl(loop_root)
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": []}),
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
    (ref / "hover-css-rules.json").write_text(
        json.dumps([]),
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "video-motion-compare", "produces": "transitions/video-motion-result.txt", "severity": "block"},
            {"id": "hover-state-compare", "produces": "transitions/hover-state-result.txt", "severity": "block"},
            {"id": "hover-tree-diff", "produces": "hover-tree-diff.md", "severity": "block"},
        ],
    }), encoding="utf-8")
    (ref / "transition-fires.json").write_text(json.dumps({
        "status": "pass",
        "total": 1,
        "fired": 0,
        "known_skip": 1,
        "failed": 0,
        "unmeasurable": 0,
    }), encoding="utf-8")
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "status": "pass",
        "total": 0,
        "withMotion": 0,
    }), encoding="utf-8")
    (transitions / "video-motion-result.txt").write_text("❌ trajectory pre-filter FAILED\n", encoding="utf-8")
    (transitions / "hover-state-result.txt").write_text("❌ hover-state: 5/5 target-run(s) diverged\n", encoding="utf-8")
    (ref / "hover-tree-diff.md").write_text("❌ nav link missing-hover-effect\n", encoding="utf-8")

    results = Gate(ref)._check_verification_plan()

    assert results
    assert all(result.status == "pass" for result in results)
    assert all("reset-only hover specs" in (result.message or "") for result in results)


@pytest.mark.parametrize(
    ("include_transition_proof", "expected_status"),
    [(True, "warn"), (False, "fail")],
)
def test_post_implement_reconciles_partial_hover_with_orthogonal_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include_transition_proof: bool,
    expected_status: str,
) -> None:
    from ui_clone.gate import Gate
    from ui_clone.gates import verification_plan

    loop_root = tmp_path / "scratch" / "partial-hover"
    ref = loop_root / "tmp" / "ref" / "comp"
    transitions = ref / "transitions"
    transitions.mkdir(parents=True)
    _b1_resolvable_impl(loop_root)
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "00-auto-hover-2",
                        "trigger": "hover",
                        "target": ".nav-link",
                    },
                    {
                        "id": "01-auto-hover-3",
                        "trigger": "hover",
                        "target": ".nav-link",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    required_checks = [
        {
            "id": "hover-state-compare",
            "produces": "transitions/hover-state-result.txt",
            "severity": "block",
        },
        {
            "id": "transition-fires",
            "produces": "transition-fires.json",
            "severity": "block",
        },
        {
            "id": "transition-compare",
            "produces": "transitions/result.txt",
            "severity": "block",
        },
    ]
    if include_transition_proof:
        required_checks.append(
            {
                "id": "transition-proof",
                "produces": "transition-proof.json",
                "severity": "block",
            }
        )
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": required_checks,
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": 2,
                "fired": 2,
                "known_skip": 0,
                "failed": 0,
                "unmeasurable": 0,
                "entries": [
                    {
                        "id": "00-auto-hover-2",
                        "trigger": "hover",
                        "type": "css-hover",
                        "kind": "hover",
                        "status": "pass",
                    },
                    {
                        "id": "01-auto-hover-3",
                        "trigger": "hover",
                        "type": "css-hover",
                        "kind": "hover",
                        "status": "pass",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (transitions / "result.txt").write_text(
        "Transition compare: 2 PASS, 0 FAIL\n"
        "✅ PASS .first\n"
        "✅ PASS .second\n",
        encoding="utf-8",
    )
    (transitions / "hover-state-result.txt").write_text(
        "# hover-state-compare\n"
        "## auto-hover-2 (hover) [single]\n"
        "selector: .nav-link\n"
        "## auto-hover-3 (hover) [single]\n"
        "selector: .nav-link\n"
        "✅ auto-hover-2 clean [single]\n"
        "⚠️ auto-hover-3 unmeasurable-after-retry [single] — status 2\n"
        "hover-fallback: status=pass verified=0 static=4 failed=0\n"
        "# coverage: measured=2 failed=0 unmeasurable=1 fallbackFailed=0\n"
        "⚠️ 1/2 hover target-run(s) unmeasurable\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        verification_plan,
        "_registered_check_is_stale",
        lambda *_args, **_kwargs: False,
    )

    results = Gate(ref)._check_verification_plan()
    hover_result = next(
        result
        for result in results
        if result.label == "required: hover-state-compare"
    )

    assert hover_result.status == expected_status, hover_result.message
    if include_transition_proof:
        assert "PARTIAL" in hover_result.message
    else:
        assert "orthogonal checks" in hover_result.message


@pytest.mark.parametrize(
    "tamper",
    ["divergence", "legacy-missing-unmeasurable"],
)
def test_post_implement_rejects_conflicting_partial_hover_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    from ui_clone.gate import Gate
    from ui_clone.gates import verification_plan

    loop_root = tmp_path / "scratch" / "partial-hover-conflict"
    ref = loop_root / "tmp" / "ref" / "comp"
    transitions = ref / "transitions"
    transitions.mkdir(parents=True)
    _b1_resolvable_impl(loop_root)
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "00-auto-hover-2",
                        "trigger": "hover",
                        "target": ".nav-link",
                    },
                    {
                        "id": "01-auto-hover-3",
                        "trigger": "hover",
                        "target": ".nav-link",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "hover-state-compare",
                        "produces": "transitions/hover-state-result.txt",
                        "severity": "block",
                    },
                    {
                        "id": "transition-fires",
                        "produces": "transition-fires.json",
                        "severity": "block",
                    },
                    {
                        "id": "transition-compare",
                        "produces": "transitions/result.txt",
                        "severity": "block",
                    },
                    {
                        "id": "transition-proof",
                        "produces": "transition-proof.json",
                        "severity": "block",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": 2,
                "fired": 2,
                "known_skip": 0,
                "failed": 0,
                "unmeasurable": 0,
                "entries": [
                    {
                        "id": "00-auto-hover-2",
                        "trigger": "hover",
                        "type": "css-hover",
                        "kind": "hover",
                        "status": "pass",
                    },
                    {
                        "id": "01-auto-hover-3",
                        "trigger": "hover",
                        "type": "css-hover",
                        "kind": "hover",
                        "status": "pass",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (transitions / "result.txt").write_text(
        "Transition compare: 2 PASS, 0 FAIL\n"
        "✅ PASS .first\n"
        "✅ PASS .second\n",
        encoding="utf-8",
    )
    hover_body = (
        "# hover-state-compare\n"
        "## auto-hover-2 (hover) [single]\n"
        "selector: .nav-link\n"
        "## auto-hover-3 (hover) [single]\n"
        "selector: .nav-link\n"
        "✅ auto-hover-2 clean [single]\n"
        "⚠️ auto-hover-3 unmeasurable-after-retry [single] — status 2\n"
        "hover-fallback: status=pass verified=0 static=4 failed=0\n"
        "# coverage: measured=2 failed=0 unmeasurable=1 fallbackFailed=0\n"
        "⚠️ 1/2 hover target-run(s) unmeasurable\n"
    )
    if tamper == "divergence":
        hover_body += "❌ 1/2 hover target-run(s) diverged\n"
    else:
        hover_body = hover_body.replace(" unmeasurable=1", "")
    (transitions / "hover-state-result.txt").write_text(
        hover_body,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        verification_plan,
        "_registered_check_is_stale",
        lambda *_args, **_kwargs: False,
    )

    results = Gate(ref)._check_verification_plan()
    hover_result = next(
        result
        for result in results
        if result.label == "required: hover-state-compare"
    )

    assert hover_result.status == "fail", hover_result.message
    assert (
        "diverged" in hover_result.message
        if tamper == "divergence"
        else "omits unmeasurable" in hover_result.message
    )


def test_post_implement_accepts_complete_hover_after_retry_and_calibration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attempt-level failures must not override complete terminal coverage."""
    from ui_clone.gate import Gate
    from ui_clone.gates import verification_plan

    loop_root = tmp_path / "scratch" / "complete-hover"
    ref = loop_root / "tmp" / "ref" / "comp"
    transitions = ref / "transitions"
    transitions.mkdir(parents=True)
    _b1_resolvable_impl(loop_root)
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {"id": f"hover-{index}", "trigger": "hover", "target": f".target-{index}"}
                    for index in range(5)
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "hover-state-compare",
                        "produces": "transitions/hover-state-result.txt",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (transitions / "hover-state-result.txt").write_text(
        "# hover-state-compare\n"
        "## primary (css-hover) [single]\nselector: .target-0\n"
        "❌ arc timing: retryable capture jitter\n"
        "✅ primary pass-after-retry [single] — capture confirmed\n"
        "## menu (css-hover) [single]\nselector: .target-1\n"
        "✅ menu clean [single]\n"
        "## header (css-hover) [single]\nselector: .target-2\n"
        "❌ f-000013 | 0.69 | attempt-only mismatch\n"
        "✅ header pass-after-static-discrete-hover-state-calibration [single]\n"
        "## service (css-hover) [single]\nselector: .target-3\n"
        "✅ service pass-after-reference-self-calibration [single]\n"
        "## policy (css-hover) [single]\nselector: .target-4\n"
        "✅ policy pass-after-complementary-reference-self-calibration [single]\n"
        "hover-fallback: status=pass verified=0 static=0 failed=0\n"
        "# coverage: measured=5 failed=0 unmeasurable=0 fallbackFailed=0\n"
        "✅ all 5 measured hover target-run(s) within SSIM threshold; "
        "fallback probe covered the rest\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        verification_plan,
        "_registered_check_is_stale",
        lambda *_args, **_kwargs: False,
    )

    results = Gate(ref)._check_verification_plan()
    hover_result = next(
        result
        for result in results
        if result.label == "required: hover-state-compare"
    )

    assert hover_result.status == "pass", hover_result.message
    assert "5/5" in hover_result.message


@pytest.mark.parametrize(
    "tamper",
    [
        "reported-failure",
        "missing-terminal-verdict",
        "summary-count-mismatch",
        "fallback-failure",
        "terminal-divergence",
    ],
)
def test_complete_hover_evidence_stays_fail_closed(
    tmp_path: Path,
    tamper: str,
) -> None:
    from ui_clone.evidence_validation import hover_state_partial_result

    body = (
        "# hover-state-compare\n"
        "## first (css-hover) [single]\nselector: .first\n"
        "❌ attempt-only mismatch before retry\n"
        "✅ first pass-after-retry [single]\n"
        "## second (css-hover) [single]\nselector: .second\n"
        "✅ second clean [single]\n"
        "hover-fallback: status=pass verified=0 static=0 failed=0\n"
        "# coverage: measured=2 failed=0 unmeasurable=0 fallbackFailed=0\n"
        "✅ all 2 measured hover target-run(s) within SSIM threshold; "
        "fallback probe covered the rest\n"
    )
    replacements = {
        "reported-failure": ("failed=0", "failed=1"),
        "missing-terminal-verdict": ("✅ second clean [single]\n", ""),
        "summary-count-mismatch": ("✅ all 2 measured", "✅ all 1 measured"),
        "fallback-failure": (
            "hover-fallback: status=pass verified=0 static=0 failed=0",
            "hover-fallback: status=fail verified=0 static=0 failed=1",
        ),
        "terminal-divergence": (
            "# coverage: measured=2",
            "❌ 1/2 hover target-run(s) diverged\n# coverage: measured=2",
        ),
    }
    old, new = replacements[tamper]

    result = hover_state_partial_result(tmp_path, body.replace(old, new, 1))

    assert result is not None
    assert result[0] is False, result


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        (
            "Transition compare: 2 PASS, 0 FAIL\n"
            "✅ PASS .verified\n"
            "✅ PASS .abstained\n"
            "    ⚠ HOVER_UNVERIFIED: pointer did not reach target\n",
            "warn",
        ),
        (
            "Transition compare: 2 PASS, 0 FAIL\n"
            "✅ PASS .only-one\n",
            "fail",
        ),
        (
            "Transition compare: 1 PASS, 0 FAIL\n"
            "Transition compare: 1 PASS, 0 FAIL\n"
            "✅ PASS .duplicate-summary\n",
            "fail",
        ),
    ],
)
def test_post_implement_transition_compare_reconciles_rows_and_abstentions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    expected_status: str,
) -> None:
    from ui_clone.gate import Gate
    from ui_clone.gates import verification_plan

    loop_root = tmp_path / "scratch" / "transition-compare-receipt"
    ref = loop_root / "tmp" / "ref" / "comp"
    transitions = ref / "transitions"
    transitions.mkdir(parents=True)
    _b1_resolvable_impl(loop_root)
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "00-auto-hover",
                        "trigger": "hover",
                        "target": ".target",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "transition-compare",
                        "produces": "transitions/result.txt",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (transitions / "result.txt").write_text(body, encoding="utf-8")
    monkeypatch.setattr(
        verification_plan,
        "_registered_check_is_stale",
        lambda *_args, **_kwargs: False,
    )

    results = Gate(ref)._check_verification_plan()
    result = next(
        row
        for row in results
        if row.label == "required: transition-compare"
    )

    assert result.status == expected_status, result.message
    if expected_status == "warn":
        assert "PARTIAL" in result.message
    else:
        assert "semantically invalid" in result.message


def test_scroll_coverage_row_is_advisory_severity(tmp_path: Path) -> None:
    """scroll-coverage sweeps FULL-FRAME unmasked AE at scroll depths — on
    motion-heavy pages (smooth-scroll + timers, realfood loop-e2e-4) every
    frame differs by phase noise above threshold, so the masked
    section-compare is the canonical visual gate and this row must stay
    warn-severity (advisory). Pin it so a future edit cannot silently promote
    it to block."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text('{"regions": []}', encoding="utf-8")
    plan = _run_verification_plan(ref)
    rows = [c for c in plan["requiredChecks"] if c["id"] == "scroll-coverage"]
    assert rows, "scroll-coverage row missing despite regions.json present"
    assert rows[0]["severity"] == "warn", rows[0]


def _b1_resolvable_impl(loop_root: Path) -> Path:
    """A sibling impl find-impl-root.sh resolves (package.json + src/app/*.tsx),
    plus a .css file so css-mirror (CSS input profile) has a fingerprintable
    input."""
    impl = loop_root / "clone"
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "package.json").write_text('{"name":"clone"}\n', encoding="utf-8")
    (impl / "src" / "app" / "page.tsx").write_text("export default () => <div/>;\n", encoding="utf-8")
    (impl / "src" / "styles.css").write_text(".hero{color:red}\n", encoding="utf-8")
    return impl


def _b1_gate_with_css_mirror(tmp_path: Path):  # type: ignore[no-untyped-def]
    from ui_clone.gate import Gate

    loop_root = tmp_path / "scratch" / "loop-b1"
    ref = loop_root / "tmp" / "ref" / "comp"
    ref.mkdir(parents=True)
    impl = _b1_resolvable_impl(loop_root)
    (ref / "bundle-map.json").write_text("{}\n", encoding="utf-8")
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "requiredChecks": [
                {"id": "css-mirror", "produces": "css-mirror.json", "severity": "block"}
            ],
        }),
        encoding="utf-8",
    )
    (ref / "css-mirror.json").write_text(
        json.dumps({"status": "pass", "implRoot": str(impl)}), encoding="utf-8"
    )
    gate = Gate(ref)
    assert gate._find_impl_root() is not None, "resolver precondition failed"
    return gate, ref, impl


def test_b1_no_stale_when_sidecar_hash_current(tmp_path: Path) -> None:
    """B1: with a sidecar holding the current per-check input hash, the gate must
    NOT mark the path-checked artifact stale."""
    from ui_clone.check_inputs import compute_check_input_hash, sidecar_path

    gate, ref, impl = _b1_gate_with_css_mirror(tmp_path)
    cur = compute_check_input_hash(str(impl), str(ref), "css-mirror")
    assert cur
    sidecar_path(ref, "css-mirror").write_text(cur, encoding="utf-8")

    results = gate._check_verification_plan()
    stale = [r for r in results if getattr(r, "stale", False) and "css-mirror" in (r.message or "")]
    assert not stale, f"css-mirror stale despite current sidecar: {[r.message for r in results]}"


def test_b1_marks_stale_when_input_hash_changed(tmp_path: Path) -> None:
    """B1: when the css input changes after the sidecar was written, the gate
    marks css-mirror stale (per-check, via the sidecar — not global mtime)."""
    from ui_clone.check_inputs import compute_check_input_hash, sidecar_path

    gate, ref, impl = _b1_gate_with_css_mirror(tmp_path)
    cur = compute_check_input_hash(str(impl), str(ref), "css-mirror")
    assert cur
    sidecar_path(ref, "css-mirror").write_text(cur, encoding="utf-8")
    # Change the declared CSS input AFTER seeding the sidecar.
    (impl / "src" / "styles.css").write_text(".hero{color:blue}\n", encoding="utf-8")

    results = gate._check_verification_plan()
    stale = [
        r for r in results
        if r.status == "fail" and getattr(r, "stale", False) and "css-mirror" in (r.message or "")
    ]
    assert stale, f"css-mirror must be stale after a CSS input change: {[r.message for r in results]}"


def test_b1_sidecarless_deleted_declared_side_fails_closed(tmp_path: Path) -> None:
    """Migration mtimes cannot certify a check after its only CSS input vanished."""
    gate, _ref, impl = _b1_gate_with_css_mirror(tmp_path)
    (impl / "src" / "styles.css").unlink()

    results = gate._check_verification_plan()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.stale
        and "css-mirror" in (result.message or "")
        and "unverifiable" in (result.message or "").lower()
    ]
    assert failures, [result.message for result in results]


def test_b1_real_unreadable_declared_file_fails_closed(tmp_path: Path) -> None:
    """A real content-read failure invalidates an otherwise current sidecar."""
    from ui_clone.check_inputs import compute_check_input_hash, sidecar_path

    gate, ref, impl = _b1_gate_with_css_mirror(tmp_path)
    current_hash = compute_check_input_hash(impl, ref, "css-mirror")
    assert current_hash
    sidecar_path(ref, "css-mirror").write_text(current_hash, encoding="utf-8")
    source = impl / "src" / "styles.css"
    source.chmod(0)
    try:
        if os.access(source, os.R_OK):
            pytest.skip("test process can read chmod(0) files")
        results = gate._check_verification_plan()
    finally:
        source.chmod(0o600)

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.stale
        and "css-mirror" in (result.message or "")
        and "unverifiable" in (result.message or "").lower()
    ]
    assert failures, [result.message for result in results]


def test_b1_marks_registered_transition_fires_stale_after_impl_change(
    tmp_path: Path,
) -> None:
    """B1: registered non-path checks also honor their input fingerprint."""
    from ui_clone.check_inputs import compute_check_input_hash, sidecar_path
    from ui_clone.gate import Gate

    loop_root = tmp_path / "scratch" / "loop-b1-transition"
    ref = loop_root / "tmp" / "ref" / "comp"
    ref.mkdir(parents=True)
    impl = _b1_resolvable_impl(loop_root)
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "hero", "trigger": "scroll"}]}),
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "transition-fires",
                        "produces": "transition-fires.json",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-fires.json").write_text(
        json.dumps({"status": "pass", "total": 1, "fired": 1}),
        encoding="utf-8",
    )
    current_hash = compute_check_input_hash(str(impl), str(ref), "transition-fires")
    assert current_hash
    sidecar_path(ref, "transition-fires").write_text(current_hash, encoding="utf-8")

    (impl / "src" / "app" / "page.tsx").write_text(
        "export default () => <main className=\"changed\"/>;\n",
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    stale = [
        result
        for result in results
        if result.status == "fail"
        and result.stale
        and "transition-fires" in (result.message or "")
    ]
    assert stale, (
        "transition-fires must be stale after a declared impl input changes: "
        f"{[result.message for result in results]}"
    )


def test_b1_impl_dependent_check_fails_closed_without_impl_root(
    tmp_path: Path,
) -> None:
    """B1: impl-dependent evidence is unverifiable without a resolvable impl."""
    from ui_clone.check_inputs import sidecar_path
    from ui_clone.gate import Gate

    ref = tmp_path / "tmp" / "ref" / "comp"
    ref.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "hero", "trigger": "scroll"}]}),
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "transition-fires",
                        "produces": "transition-fires.json",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-fires.json").write_text(
        json.dumps({"status": "pass", "total": 1, "fired": 1}),
        encoding="utf-8",
    )
    sidecar_path(ref, "transition-fires").write_text(
        "ref-only-forgery",
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.stale
        and "transition-fires" in (result.message or "")
        and "impl" in (result.message or "").lower()
    ]
    assert failures, [result.message for result in results]


def test_b1_sidecarless_impl_dependent_check_fails_closed_without_impl_root(
    tmp_path: Path,
) -> None:
    """B1: migration fallback is unavailable until an active impl resolves."""
    from ui_clone.gate import Gate

    ref = tmp_path / "tmp" / "ref" / "comp"
    ref.mkdir(parents=True)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "css-mirror",
                        "produces": "css-mirror.json",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "css-mirror.json").write_text(
        json.dumps({"status": "pass"}),
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.stale
        and "css-mirror" in (result.message or "")
        and "no implementation root" in (result.message or "")
    ]
    assert failures, [result.message for result in results]


def test_b1_sidecarless_migration_requires_declared_input_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B1: no sidecar plus unmeasurable declared inputs is not fresh evidence."""
    from ui_clone.gates import verification_plan

    gate, _ref, _impl = _b1_gate_with_css_mirror(tmp_path)
    monkeypatch.setattr(
        verification_plan,
        "newest_input_mtime",
        lambda *_args, **_kwargs: None,
    )

    results = gate._check_verification_plan()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.stale
        and "css-mirror" in (result.message or "")
        and "unverifiable" in (result.message or "").lower()
    ]
    assert failures, [result.message for result in results]


def test_b1_ref_only_check_remains_valid_without_impl_root(tmp_path: Path) -> None:
    """B1: checks whose registry inputs are ref-only do not require an impl."""
    from ui_clone.check_inputs import compute_check_input_hash, sidecar_path
    from ui_clone.gate import Gate

    ref = tmp_path / "tmp" / "ref" / "comp"
    ref.mkdir(parents=True)
    (ref / "regions.json").write_text('{"regions": []}\n', encoding="utf-8")
    (ref / "section-map.json").write_text('{"sections": []}\n', encoding="utf-8")
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "capture-artifact-inventory",
                        "produces": "capture-artifact-inventory.json",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "capture-artifact-inventory.json").write_text(
        json.dumps({"status": "pass"}),
        encoding="utf-8",
    )
    current_hash = compute_check_input_hash(None, ref, "capture-artifact-inventory")
    assert current_hash
    sidecar_path(ref, "capture-artifact-inventory").write_text(
        current_hash,
        encoding="utf-8",
    )

    results = Gate(ref)._check_verification_plan()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and "capture-artifact-inventory" in (result.message or "")
    ]
    assert not failures, [result.message for result in results]


def test_b1_fingerprint_io_error_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B1: unreadable fingerprint evidence cannot be treated as fresh."""
    from ui_clone.check_inputs import compute_check_input_hash, sidecar_path
    from ui_clone.gate import Gate
    from ui_clone.gates import verification_plan

    loop_root = tmp_path / "scratch" / "loop-b1-io"
    ref = loop_root / "tmp" / "ref" / "comp"
    ref.mkdir(parents=True)
    impl = _b1_resolvable_impl(loop_root)
    (ref / "bundle-map.json").write_text("{}\n", encoding="utf-8")
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "css-mirror",
                        "produces": "css-mirror.json",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "css-mirror.json").write_text(
        json.dumps({"status": "pass", "implRoot": str(impl)}),
        encoding="utf-8",
    )
    current_hash = compute_check_input_hash(impl, ref, "css-mirror")
    assert current_hash
    sidecar_path(ref, "css-mirror").write_text(current_hash, encoding="utf-8")

    def _raise_oserror(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("fingerprint unreadable")

    monkeypatch.setattr(
        verification_plan,
        "compute_check_input_hash",
        _raise_oserror,
    )

    results = Gate(ref)._check_verification_plan()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.stale
        and "css-mirror" in (result.message or "")
        and "unverifiable" in (result.message or "").lower()
    ]
    assert failures, [result.message for result in results]


def test_b1_unreadable_sidecar_fails_closed(tmp_path: Path) -> None:
    """B1: a malformed sidecar path cannot fall back to a fresh mtime verdict."""
    from ui_clone.check_inputs import sidecar_path

    gate, ref, _impl = _b1_gate_with_css_mirror(tmp_path)
    sidecar_path(ref, "css-mirror").mkdir()

    results = gate._check_verification_plan()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.stale
        and "css-mirror" in (result.message or "")
        and "unverifiable" in (result.message or "").lower()
    ]
    assert failures, [result.message for result in results]


def test_b1_broken_sidecar_symlink_fails_closed(tmp_path: Path) -> None:
    """B1: broken canonical evidence cannot masquerade as a missing sidecar."""
    from ui_clone.check_inputs import sidecar_path

    gate, ref, _impl = _b1_gate_with_css_mirror(tmp_path)
    sidecar_path(ref, "css-mirror").symlink_to(ref / "missing-input-hash")

    results = gate._check_verification_plan()

    failures = [
        result
        for result in results
        if result.status == "fail"
        and result.stale
        and "css-mirror" in (result.message or "")
        and "unverifiable" in (result.message or "").lower()
    ]
    assert failures, [result.message for result in results]


def _b1_runtime_text_gate(
    tmp_path: Path,
    *,
    strict_warnings: bool,
    with_impl: bool = True,
) -> tuple["Gate", Path, Path | None]:
    from ui_clone.gate import Gate

    loop_root = tmp_path / "scratch" / "loop-b1-runtime-text"
    ref = loop_root / "tmp" / "ref" / "comp"
    ref.mkdir(parents=True)
    impl = _b1_resolvable_impl(loop_root) if with_impl else None
    (ref / "dom-scaffold.json").write_text('{"tree":{}}\n', encoding="utf-8")
    (ref / "runtime-text.json").write_text('{"blocks":["Copy"]}\n', encoding="utf-8")
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "strictWarnings": strict_warnings,
                "requiredChecks": [
                    {
                        "id": "runtime-text-sequence",
                        "produces": "runtime-text-sequence.json",
                        "severity": "warn",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "pass",
                "ref": {"blockCount": 1, "blocks": ["Copy"]},
                "impl": {"blockCount": 1, "blocks": ["Copy"]},
                "comparison": {
                    "lcsLength": 1,
                    "orderedSimilarity": 1.0,
                    "missingCount": 0,
                    "missingRatio": 0.0,
                    "extraCount": 0,
                },
                "violations": [],
            }
        ),
        encoding="utf-8",
    )
    return Gate(ref), ref, impl


@pytest.mark.parametrize("strict_warnings", [False, True])
def test_b1_runtime_text_staleness_always_blocks(
    tmp_path: Path,
    strict_warnings: bool,
) -> None:
    from ui_clone.check_inputs import compute_check_input_hash, sidecar_path

    gate, ref, impl = _b1_runtime_text_gate(
        tmp_path,
        strict_warnings=strict_warnings,
    )
    assert impl is not None
    current_hash = compute_check_input_hash(impl, ref, "runtime-text-sequence")
    assert current_hash
    sidecar_path(ref, "runtime-text-sequence").write_text(
        current_hash,
        encoding="utf-8",
    )
    (impl / "src" / "styles.css").write_text(
        ".hero{color:blue}\n",
        encoding="utf-8",
    )

    results = gate._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert results[0].stale
    assert "stale artifact" in (results[0].message or "")


@pytest.mark.parametrize("strict_warnings", [False, True])
def test_b1_runtime_text_missing_impl_always_blocks(
    tmp_path: Path,
    strict_warnings: bool,
) -> None:
    from ui_clone.check_inputs import sidecar_path

    gate, ref, impl = _b1_runtime_text_gate(
        tmp_path,
        strict_warnings=strict_warnings,
        with_impl=False,
    )
    assert impl is None
    sidecar_path(ref, "runtime-text-sequence").write_text(
        "requires-an-impl",
        encoding="utf-8",
    )

    results = gate._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert results[0].stale
    assert "no implementation root" in (results[0].message or "")


@pytest.mark.parametrize("strict_warnings", [False, True])
def test_b1_runtime_text_fingerprint_io_error_always_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict_warnings: bool,
) -> None:
    from ui_clone.check_inputs import compute_check_input_hash, sidecar_path
    from ui_clone.gates import verification_plan

    gate, ref, impl = _b1_runtime_text_gate(
        tmp_path,
        strict_warnings=strict_warnings,
    )
    assert impl is not None
    current_hash = compute_check_input_hash(impl, ref, "runtime-text-sequence")
    assert current_hash
    sidecar_path(ref, "runtime-text-sequence").write_text(
        current_hash,
        encoding="utf-8",
    )

    def _raise_oserror(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("fingerprint unreadable")

    monkeypatch.setattr(
        verification_plan,
        "compute_check_input_hash",
        _raise_oserror,
    )

    results = gate._check_verification_plan()

    assert len(results) == 1
    assert results[0].status == "fail"
    assert results[0].stale
    assert "unverifiable" in (results[0].message or "")
