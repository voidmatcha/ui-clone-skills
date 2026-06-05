import json
from pathlib import Path

from ._helpers import (
    _fixture_all_signals,
    _project_root,
    _run_verification_plan,
)


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



def test_verification_plan_emits_click_state_check_when_click_trigger_detected(tmp_path: Path) -> None:
    """regions.json with triggerType: click-* → click-state-compare row required.

    Click-state transitions (tabs/accordions/modals/menu toggles) have their
    own motion arc that hover-compare + section-compare never exercise.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "click": [
            {"name": "tabs", "triggerType": "click-cycle", "selector": ".tab"}
        ]
    }))
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



def test_verification_plan_emits_hover_state_check_when_hover_signal_detected(tmp_path: Path) -> None:
    """hasHover=true → hover-state-compare row required.

    Static transition-compare verifies idle/hover end-states only. hover-state-compare
    runs 60fps video over the entry arc to catch easing/duration divergence on
    hover transitions — same bug class as video-motion-compare for scroll motion.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "interactions-detected.json").write_text(json.dumps({
        "interactions": [{"trigger": "hover", "target": ".btn"}]
    }))
    plan = _run_verification_plan(ref)
    assert plan["signals"]["hasHover"] is True
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "hover-state-compare" in ids, (
        f"hover-state row missing when hasHover=true: {ids}"
    )
    # transition-compare should remain — they cover different bug classes.
    assert "transition-compare" in ids, (
        f"transition-compare should also be present alongside hover-state: {ids}"
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



def test_verification_plan_emits_runtime_spec_coverage_when_dump_and_spec_present(tmp_path: Path) -> None:
    """animation-runtime-dump.json + transition-spec.json present → runtime-spec-coverage row required.

    Turns transition-spec-rules.md Rule 7 ("consult animation-runtime-dump.json
    when authoring transition-spec.json") from an advisory into an enforced
    gate. If the dump shows ScrollTrigger entries but the spec has zero scroll
    entries, this check fails post-implement.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "scrollTrigger": [{"start": 100, "end": 500}],
        "webAnimations": None, "lenis": None, "ix2": None, "gsap": None
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero", "trigger": "scroll", "type": "scroll-driven"}]
    }))
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
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "x", "trigger": "hover"}]
    }))
    plan = _run_verification_plan(ref)
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "runtime-spec-coverage" not in ids



def test_verification_plan_emits_spec_implementation_coverage_when_spec_present(tmp_path: Path) -> None:
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
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "x", "trigger": "hover"}]
    }))
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
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-scroll", "trigger": "scroll", "type": "scroll-driven"}]
    }))

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
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "x", "trigger": "hover"}]
    }))
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

    Static-only set (with all signals firing): hydration-check,
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
        # Monolithic implementation and motion coverage:
        "monolithic-impl",
        "motion-coverage",
        # scroll-engine-parity — engine class match (Lenis / GSAP
        # ScrollTrigger / scroll-scrub / scroll-pin):
        "scroll-engine-parity",
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
        capture_output=True, text=True, timeout=10,
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
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/x.jpg", "element": "img"},
    ]))
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



def test_verification_plan_emits_asset_utilization_when_visible_images_present(tmp_path: Path) -> None:
    """Downloaded visible assets must be referenced by implementation source.

    The `asset-utilization` row requires at least 60% referenced assets.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/x.jpg", "element": "img"},
    ]))
    plan = _run_verification_plan(ref)
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "asset-utilization" in rows
    assert rows["asset-utilization"]["severity"] == "block"
    assert rows["asset-utilization"]["tier"] == "quick"
    assert rows["asset-utilization"]["produces"] == "asset-utilization.json"



def test_verification_plan_emits_capture_artifact_inventory_for_trigger_regions(tmp_path: Path) -> None:
    """regions.json trigger metadata needs an artifact-manifest gate."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "css-hover", "selector": ".btn"}]
    }))
    plan = _run_verification_plan(ref, tier="quick")
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "capture-artifact-inventory" in rows
    assert rows["capture-artifact-inventory"]["severity"] == "block"
    assert rows["capture-artifact-inventory"]["tier"] == "quick"
    assert rows["capture-artifact-inventory"]["produces"] == "capture-artifact-inventory.json"



def test_verification_plan_emits_asset_placement_when_section_mapping_exists(tmp_path: Path) -> None:
    """visible assets with section/component maps need section-local placement enforcement."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/x.jpg", "top": 100},
    ]))
    (ref / "section-map.json").write_text(json.dumps({"sections": [{"i": 0, "top": 0, "height": 500}]}))
    (ref / "component-map.json").write_text(json.dumps({"sections": [{"index": 0, "file": "src/components/Hero.tsx"}]}))
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
    (ref / "bundle-map.json").write_text(json.dumps({
        "resources": ["https://cdn.example.com/bodymovin.min.js"],
        "notes": "lottie-web registered animations",
    }))
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
    (splash / "summary.json").write_text(json.dumps({
        "checked": True,
        "polls": 4,
        "reason": "stable-2s",
    }))
    (splash / "trajectory.json").write_text(json.dumps([
        {"ts_ms": 0, "bodyClass": "is-loading"},
        {"ts_ms": 800, "bodyClass": "is-loaded"},
    ]))
    plan = _run_verification_plan(ref)
    # hasSplash=true forces splash-driven checks (lottie-runtime / runtime-proof
    # / scroll-end-completion / reveal-trigger). At minimum the plan should
    # report the signal as set.
    signals = plan.get("signals", {})
    assert signals.get("hasSplash") is True, (
        f"states/splash/summary.json polls=4 must set hasSplash=true; "
        f"got signals: {signals}"
    )


def test_verification_plan_derives_hasHover_from_states_manifest(tmp_path: Path) -> None:
    """Phase C `states/hover/manifest.json` entries must set HAS_HOVER=true."""
    ref = tmp_path / "ref"
    ref.mkdir()
    hover = ref / "states" / "hover"
    hover.mkdir(parents=True)
    (hover / "manifest.json").write_text(json.dumps({
        "entries": [
            {"id": "abc123", "kind": "css", "file": "elem-abc123.json",
             "selector": ".btn", "activation": ".btn", "changedCount": 0},
            {"id": "def456", "kind": "js", "file": "elem-def456.json",
             "selector": "a.cta", "activation": "a.cta", "changedCount": 2},
        ],
    }))
    plan = _run_verification_plan(ref)
    signals = plan.get("signals", {})
    assert signals.get("hasHover") is True, (
        f"states/hover/manifest.json with entries must set hasHover=true; "
        f"got signals: {signals}"
    )


def test_verification_plan_regenerates_when_state_structure_spec_newer(
    tmp_path: Path,
) -> None:
    """state-structure-spec.json is a compact state rollup; refreshing it
    after a plan must invalidate the old plan just like states/* summaries."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "generatedAt": "2000-01-01T00:00:00Z",
        "requiredChecks": [],
        "signals": {},
    }))
    (ref / "state-structure-spec.json").write_text(json.dumps({
        "schemaVersion": 1,
        "events": [{"phase": "click", "trigger": "click"}],
    }))

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
    (ref / "animation-runtime-dump.json").write_text(json.dumps({
        "scrollTrigger": [{"id": "st1", "start": "top 80%", "end": "bottom"}],
        "tweens": [{"duration": 0.8}],
    }))
    # NOTE: no transition-spec.json is written.
    plan = _run_verification_plan(ref)
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "runtime-spec-coverage" in rows, (
        "animation-runtime-dump.json alone must register runtime-spec-coverage; "
        f"got rows: {list(rows.keys())}"
    )
    assert rows["runtime-spec-coverage"]["severity"] == "block"
