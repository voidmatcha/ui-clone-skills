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

    Catches the silent-killer "selector matched but no motion declared" gap:
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



def test_verification_plan_emits_transition_compare_when_spec_present_without_hover(
    tmp_path: Path,
) -> None:
    """A transition spec requires runtime comparison even when hover is absent."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-scroll", "trigger": "scroll", "type": "scroll-driven"}]
    }))

    plan = _run_verification_plan(ref)
    ids = [c["id"] for c in plan["requiredChecks"]]

    assert "transition-compare" in ids, (
        "transition-compare must be required for transition-spec.json, not only "
        f"hover signals: {ids}"
    )



def test_verification_plan_spec_implementation_coverage_tier_is_standard(tmp_path: Path) -> None:
    """spec-implementation-coverage must be tagged tier=standard.

    The row is meaningful only after the agent has generated impl source — at
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
    plus the Fix 8 anti-fabrication gates (text-fidelity-check, dom-mirror-check)
    and proxy-mirror-check, which blocks original-runtime proxy/cache mirrors;
    plus the loop-9 ref-screenshot-asset anti-cheat (static filesystem scan +
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
        # Universal anti-cheat — class-signature preservation (L62 root cause):
        "class-signature-preservation",
        # Universal anti-cheat — bundle-paste (L41/L44 wholesale-paste cheat):
        "bundle-paste",
        # Universal anti-cheat — class-signature CSS coverage (L64 metric/visual decoupling):
        "class-signature-css-coverage",
        # Loop-9 family A1/A2/A3 anti-cheat (static):
        "entry-coherence",
        "scaffold-residue",
        "html-paste",
        # Loop-9 family A5 (static CSS mirror):
        "css-mirror",
        # Loop-9 fix #4 — explicit invalidation stamp:
        "invalidation",
        # Signal 1 — scaffold-warn placeholders:
        "scaffold-warn",
        # ui-capture handoff contract — regions with triggerType must enumerate
        # concrete ref clip/video artifacts. Pure JSON + file stat.
        "capture-artifact-inventory",
        # Diagnosis B — required-media coverage (dispatched
        # unconditionally; script self-skips when ref has no required
        # video/Lottie/SVG):
        "required-media-coverage",
        # Codex-2 findings — monolithic-impl + motion-coverage:
        "monolithic-impl",
        "motion-coverage",
        # scroll-engine-parity — engine class match (Lenis / GSAP
        # ScrollTrigger / scroll-scrub / scroll-pin):
        "scroll-engine-parity",
        # 2026-05-22 retune (user direction A) — hero-composite spot-check
        # replaces dom-mirror's structural-enforcement role. Verifies the
        # 4-element hero pattern (video + button + h1/h2 + label) which
        # LLMs consistently flatten away. Pure static (regex over impl
        # source + structure.json walk), so tier=quick.
        "hero-composite-check",
        # 2026-05-22 codex-rescue (a125b997) — composite roll-ups +
        # ref-js-loader anti-cheat. All three are pure file IO (rollups
        # read existing artifacts; loader does static grep on impl
        # source) so they belong in tier=quick.
        "runtime-proof",
        "transition-proof",
        "ref-js-loader",
        # 2026-05-22 user observation (gate-cheat block) — impl-scope
        # guard runs `git diff` only, no browser. tier=quick.
        "impl-scope",
        # 2026-05-22 codex-rescue grounding audit — color-token diff
        # against ref palette. Pure regex + math, no browser → quick.
        "color-token-grounding",
        # 2026-05-22 user request — duration/easing grounding. Pure
        # source scan, no browser → quick.
        "duration-easing-grounding",
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
    visible-images.json exists. Both block-severity (severity upgraded from
    warn after the realfood.gov benchmark showed the agent reliably skips
    actual download — see CHANGELOG entry on `asset-transfer-check.sh`).
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
    # min_tier dropped standard → quick in HEAD after 077d8c3 — the agent set
    # tier=quick to silently drop these rows; making them quick-tier ensures
    # they fire at every cost-tier (cheap file-existence + grep checks).
    assert rows["image-fidelity"]["tier"] == "quick"
    assert rows["image-fidelity"]["produces"] == "image-fidelity.json"
    # Asset-transfer is the companion check — code refs vs actual files in impl/public/.
    assert "asset-transfer" in rows
    assert rows["asset-transfer"]["severity"] == "block"
    assert rows["asset-transfer"]["tier"] == "quick"
    assert rows["asset-transfer"]["produces"] == "asset-transfer.json"



def test_verification_plan_emits_asset_utilization_when_visible_images_present(tmp_path: Path) -> None:
    """Regression — c9b638d shipped 45 downloaded images with only 2 referenced
    in src (95% orphan). New `asset-utilization` row requires ≥60% referenced.
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
