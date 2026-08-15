"""ui_clone.section_guards — crop-evidence guards against vacuous section passes.

Loop-9 regression class: footer-2 crops at the 1600/1920 fan-out viewports
came back flattened to 2 colors on BOTH sides (background rectangles only —
the near-bottom reveal never mounted in the capture window), so AE=0 produced
a clean "ok" verdict over zero actual content. mask-elements.json was [] and
mask-coverage.json 0.0, so no telemetry contradicted the pass.

The guards module computes per-crop truth (unique colors, dominant-color
fraction, mean/std) plus mask coverage and emits a per-section unmeasured
reason so the AE loop can convert vacuous passes to UNMEASURED rows.

Fixtures under tests/fixtures/section_guards/ are COPIES of the archived
loop-9 crops (tmp/ref/realfood-e2e-9 is a read-only corpus):
  footer-2-flat-*    — viewports/1600x900 crops, 2-color flattened (defect)
  footer-2-content-* — root sections/ crops at 1440, real content (negative)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "section_guards"

from ui_clone.section_guards import (  # noqa: E402
    crop_stats,
    evaluate_sections_dir,
    guard_reason,
    is_content_bearing,
)


def _row(text: str = "eat real food join the movement", children: int = 2) -> dict:
    return {"textWords": text, "childCount": children, "rect": {"top": 100, "height": 850}}


def _masked_media_row(**overrides: object) -> dict:
    row: dict[str, object] = {
        "tag": "section",
        "display": "block",
        "textWords": "",
        "fingerprint": "",
        "hasSvgText": False,
        "contentGroups": [],
        "childCount": 1,
        "visibleMediaCount": 1,
        "visibleMediaKinds": ["video"],
        "visibleMediaKindCounts": {"video": 1},
        "rect": {"x": 10, "y": 20, "width": 800, "height": 450},
        "contentBox": {"x": 10, "y": 20, "width": 800, "height": 450, "boxCount": 1},
    }
    row.update(overrides)
    return row


def _plan(check_ids: list[str]) -> dict:
    return {
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": check_id, "severity": "block", "minTier": "standard"} for check_id in check_ids
        ],
    }


def _build_masked_media_sections_dir(
    tmp_path: Path,
    *,
    mask_pct: float = 99.5,
    ref_row: dict | None = None,
    impl_row: dict | None = None,
    score: float = 0.99,
    plan_checks: list[str] | None = None,
    mask_tag: str | None = "video",
    viewport: bool = False,
) -> Path:
    ref_dir = tmp_path / "ref"
    d = (
        ref_dir / "sections" / "viewports" / "375x812" / "sections"
        if viewport
        else ref_dir / "sections"
    )
    (d / "ref").mkdir(parents=True)
    (d / "impl").mkdir(parents=True)
    shutil.copy(FIXTURES / "footer-2-flat-ref.png", d / "ref" / "masked-hero.png")
    shutil.copy(FIXTURES / "footer-2-flat-impl.png", d / "impl" / "masked-hero.png")
    if plan_checks is not None:
        (ref_dir / "verification-plan.json").write_text(json.dumps(_plan(plan_checks)))
    matches = [
        {
            "name": "masked-hero",
            "score": score,
            "ref": ref_row or _masked_media_row(),
            "impl": impl_row or _masked_media_row(),
        }
    ]
    (d / "matches.json").write_text(json.dumps(matches), encoding="utf-8")
    (d / "mask-coverage.json").write_text(json.dumps({"masked-hero": mask_pct}))
    mask_elements = []
    if mask_tag is not None:
        mask_elements.append(
            {
                "tag": mask_tag,
                "left": 10,
                "top": 20,
                "width": 800,
                "height": 450,
            }
        )
    (d / "mask-elements.json").write_text(json.dumps(mask_elements), encoding="utf-8")
    return d


# ── crop_stats on the archived defect crops ────────────────────────────


def test_flat_crop_stats_detect_two_colors() -> None:
    stats = crop_stats(FIXTURES / "footer-2-flat-ref.png")
    assert stats is not None
    assert stats["unique"] <= 4
    assert stats["dominant"] >= 0.6


def test_content_crop_stats_have_many_colors() -> None:
    stats = crop_stats(FIXTURES / "footer-2-content-ref.png")
    assert stats is not None
    assert stats["unique"] > 8


# ── guard_reason policy ────────────────────────────────────────────────


def test_loop9_flat_crops_yield_unmeasured_reason() -> None:
    """The archived AE=0 'ok' pair must be flagged: both sides flattened to
    2 colors on a content-bearing section."""
    ref = crop_stats(FIXTURES / "footer-2-flat-ref.png")
    impl = crop_stats(FIXTURES / "footer-2-flat-impl.png")
    reason, policy = guard_reason(ref, impl, content_bearing=True, mask_pct=0.0)
    assert reason is not None and "flat" in reason
    assert policy == "pass-only"


def test_loop9_content_crops_pass_clean() -> None:
    ref = crop_stats(FIXTURES / "footer-2-content-ref.png")
    impl = crop_stats(FIXTURES / "footer-2-content-impl.png")
    reason, _ = guard_reason(ref, impl, content_bearing=True, mask_pct=0.0)
    assert reason is None


def test_blank_ref_guard_applies_to_all_tiers() -> None:
    """(a) ref crop std below SECTION_REF_MIN_STD on a content-bearing row is
    UNMEASURED for every verdict tier — capture failure is not impl evidence."""
    ref = {"mean": 0.5, "std": 0.001, "unique": 1, "dominant": 1.0}
    impl = {"mean": 0.4, "std": 0.2, "unique": 300, "dominant": 0.3}
    reason, policy = guard_reason(ref, impl, content_bearing=True, mask_pct=0.0)
    assert reason is not None and "blank-ref" in reason
    assert policy == "all"


def test_masked_crop_preserves_fail_tier_before_blank_ref_guard() -> None:
    ref = {"mean": 0.0, "std": 0.0, "unique": 1, "dominant": 1.0}
    impl = {"mean": 1.0, "std": 0.0, "unique": 1, "dominant": 1.0}

    reason, policy = guard_reason(
        ref,
        impl,
        content_bearing=True,
        media_bearing=True,
        mask_pct=100.0,
    )

    assert reason is not None and "masked:" in reason
    assert policy == "pass-only"


def test_symmetric_near_black_is_unmeasured() -> None:
    """(b) both sides near-black on a content-bearing section is absence of
    evidence, not a pass."""
    side = {"mean": 0.02, "std": 0.03, "unique": 12, "dominant": 0.9}
    reason, policy = guard_reason(dict(side), dict(side), content_bearing=True, mask_pct=0.0)
    assert reason is not None
    assert policy == "all"


def test_low_contrast_signal_rich_media_is_measured() -> None:
    ref = {"mean": 0.0533, "std": 0.0406, "unique": 38, "dominant": 0.2829}
    impl = {"mean": 0.0533, "std": 0.0406, "unique": 38, "dominant": 0.2829}

    reason, _ = guard_reason(
        ref,
        impl,
        content_bearing=True,
        media_bearing=True,
        mask_pct=0.0,
    )

    assert reason is None


def test_low_contrast_signal_poor_media_remains_unmeasured() -> None:
    side = {"mean": 0.02, "std": 0.03, "unique": 12, "dominant": 0.9}

    reason, policy = guard_reason(
        dict(side),
        dict(side),
        content_bearing=True,
        media_bearing=True,
        mask_pct=0.0,
    )

    assert reason is not None
    assert policy == "all"


def test_low_variance_sparse_detail_is_measured() -> None:
    ref = {"mean": 0.9765, "std": 0.0274, "unique": 147, "dominant": 0.9698}
    impl = {"mean": 0.9765, "std": 0.0274, "unique": 145, "dominant": 0.9698}

    reason, _ = guard_reason(
        ref,
        impl,
        content_bearing=True,
        mask_pct=0.0,
    )

    assert reason is None


def test_mask_majority_blocks_ok_verdicts() -> None:
    """(d) >60% masked compared area cannot produce a tier-ok verdict."""
    ref = crop_stats(FIXTURES / "footer-2-content-ref.png")
    impl = crop_stats(FIXTURES / "footer-2-content-impl.png")
    reason, policy = guard_reason(ref, impl, content_bearing=True, mask_pct=72.5)
    assert reason is not None and "mask" in reason
    assert policy == "pass-only"


def test_empty_section_never_guarded() -> None:
    ref = {"mean": 0.5, "std": 0.0, "unique": 1, "dominant": 1.0}
    reason, _ = guard_reason(ref, dict(ref), content_bearing=False, mask_pct=100.0)
    assert reason is None


def test_is_content_bearing() -> None:
    assert is_content_bearing(_row())
    assert is_content_bearing({"textWords": "", "childCount": 3})
    assert is_content_bearing({"textWords": "", "childCount": 0, "hasSvgText": True})
    assert is_content_bearing({"textWords": "", "childCount": 1, "hasVisibleMedia": True})
    assert is_content_bearing({"textWords": "", "childCount": 1, "visibleMediaCount": 1})
    assert is_content_bearing({"textWords": "", "childCount": 1, "contentBox": {"boxCount": 2}})
    assert not is_content_bearing({"textWords": "", "childCount": 1})
    assert not is_content_bearing({"textWords": "", "childCount": 1, "contentBox": {"boxCount": 1}})
    assert not is_content_bearing({"textWords": "", "fingerprint": "", "childCount": 0})


# ── CLI batch mode over a sections dir ─────────────────────────────────


def _build_sections_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sections"
    (d / "ref").mkdir(parents=True)
    (d / "impl").mkdir(parents=True)
    shutil.copy(FIXTURES / "footer-2-flat-ref.png", d / "ref" / "footer-2.png")
    shutil.copy(FIXTURES / "footer-2-flat-impl.png", d / "impl" / "footer-2.png")
    shutil.copy(FIXTURES / "footer-2-content-ref.png", d / "ref" / "hero.png")
    shutil.copy(FIXTURES / "footer-2-content-impl.png", d / "impl" / "hero.png")
    matches = [
        {"name": "footer-2", "ref": _row(), "impl": _row()},
        {"name": "hero", "ref": _row("real food wins", 1), "impl": _row("real food wins", 1)},
    ]
    (d / "matches.json").write_text(json.dumps(matches), encoding="utf-8")
    (d / "mask-coverage.json").write_text(json.dumps({"footer-2": 0.0, "hero": 0.0}))
    return d


def test_cli_writes_guards_artifacts(tmp_path: Path) -> None:
    d = _build_sections_dir(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "ui_clone.section_guards", str(d)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    guards = json.loads((d / "crop-guards.json").read_text(encoding="utf-8"))
    assert guards["sections"]["footer-2"]["reason"]
    assert guards["sections"]["hero"]["reason"] is None
    assert guards["sections"]["footer-2"]["contentBearing"] is True
    assert guards["sections"]["hero"]["contentBearing"] is True
    # telemetry records true flatness for the audit trail
    assert guards["sections"]["footer-2"]["ref"]["unique"] <= 4
    tsv = (d / "crop-guards.tsv").read_text(encoding="utf-8")
    lines = [ln for ln in tsv.splitlines() if ln.strip()]
    assert any(ln.startswith("footer-2\t") for ln in lines)
    assert not any(ln.startswith("hero\t") for ln in lines)


def test_evaluate_sections_records_explicit_non_content_bearing_row(tmp_path: Path) -> None:
    d = _build_sections_dir(tmp_path)
    shutil.copy(FIXTURES / "footer-2-flat-ref.png", d / "ref" / "decorative-panel.png")
    shutil.copy(FIXTURES / "footer-2-flat-impl.png", d / "impl" / "decorative-panel.png")
    matches = json.loads((d / "matches.json").read_text(encoding="utf-8"))
    empty_row = {"textWords": "", "childCount": 1, "contentGroups": []}
    matches.append({"name": "decorative-panel", "ref": empty_row, "impl": empty_row})
    (d / "matches.json").write_text(json.dumps(matches), encoding="utf-8")

    guards = evaluate_sections_dir(d)

    panel = guards["sections"]["decorative-panel"]
    assert panel["contentBearing"] is False
    assert panel["reason"] is None


def test_evaluate_sections_guards_single_child_visible_media_row(tmp_path: Path) -> None:
    d = _build_sections_dir(tmp_path)
    shutil.copy(FIXTURES / "footer-2-flat-ref.png", d / "ref" / "media-panel.png")
    shutil.copy(FIXTURES / "footer-2-flat-impl.png", d / "impl" / "media-panel.png")
    matches = json.loads((d / "matches.json").read_text(encoding="utf-8"))
    media_row = {
        "textWords": "",
        "childCount": 1,
        "contentBox": {"boxCount": 1},
        "visibleMediaCount": 1,
    }
    matches.append({"name": "media-panel", "ref": media_row, "impl": media_row})
    (d / "matches.json").write_text(json.dumps(matches), encoding="utf-8")

    guards = evaluate_sections_dir(d)

    panel = guards["sections"]["media-panel"]
    assert panel["contentBearing"] is True
    assert panel["mediaBearing"] is True
    assert panel["reason"] is not None


def test_evaluate_sections_guards_single_child_multi_box_row(tmp_path: Path) -> None:
    d = _build_sections_dir(tmp_path)
    shutil.copy(FIXTURES / "footer-2-flat-ref.png", d / "ref" / "multi-box-panel.png")
    shutil.copy(FIXTURES / "footer-2-flat-impl.png", d / "impl" / "multi-box-panel.png")
    matches = json.loads((d / "matches.json").read_text(encoding="utf-8"))
    boxed_row = {
        "textWords": "",
        "childCount": 1,
        "contentBox": {"boxCount": 2},
        "contentGroups": [],
    }
    matches.append({"name": "multi-box-panel", "ref": boxed_row, "impl": boxed_row})
    (d / "matches.json").write_text(json.dumps(matches), encoding="utf-8")

    guards = evaluate_sections_dir(d)

    panel = guards["sections"]["multi-box-panel"]
    assert panel["contentBearing"] is True
    assert panel["reason"] is not None


def test_fully_masked_media_only_section_defers_to_structural_policy(tmp_path: Path) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        plan_checks=[
            "required-media-coverage",
            "video-play-proof",
            "video-motion-compare",
            "hero-composite-check",
        ],
    )

    guards = evaluate_sections_dir(d)

    panel = guards["sections"]["masked-hero"]
    assert panel["policy"] == "structural-only"
    assert "fully-masked-media-only" in panel["reason"]
    assert panel["source"] == "masked-media-motion"
    assert panel["maskedMediaOnly"]["maskPct"] == 99.5
    assert panel["maskedMediaOnly"]["matchScore"] == 0.99


def test_fully_masked_media_only_rejects_994_percent_mask(tmp_path: Path) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        mask_pct=99.4,
        plan_checks=["required-media-coverage", "video-play-proof"],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["reason"] is not None
    assert "masked:" in panel["reason"]


def test_fully_masked_media_only_rejects_text_overlay(tmp_path: Path) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        ref_row=_masked_media_row(textWords="Watch the film"),
        plan_checks=["required-media-coverage", "video-play-proof"],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["reason"] is not None
    assert "masked:" in panel["reason"]


def test_fully_masked_media_only_rejects_geometry_mismatch(tmp_path: Path) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        impl_row=_masked_media_row(rect={"x": 10, "y": 20, "width": 850, "height": 450}),
        plan_checks=["required-media-coverage", "video-play-proof"],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["reason"] is not None
    assert "masked:" in panel["reason"]


def test_fully_masked_media_only_requires_plan_declared_live_proof(tmp_path: Path) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        plan_checks=["required-media-coverage", "hero-composite-check"],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["reason"] is not None
    assert "masked:" in panel["reason"]


def test_fully_masked_video_requires_video_play_proof_not_unrelated_frame_proof(
    tmp_path: Path,
) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        plan_checks=[
            "required-media-coverage",
            "runtime-frame-proof",
            "hero-composite-check",
        ],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["maskedMediaOnly"]["maskedMediaKinds"] == ["video"]
    assert panel["maskedMediaOnly"]["rejectReason"] == (
        "video-play-proof not declared as block for video mask"
    )


def test_fully_masked_video_requires_motion_parity_proof(tmp_path: Path) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        plan_checks=[
            "required-media-coverage",
            "video-play-proof",
            "hero-composite-check",
        ],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["maskedMediaOnly"]["rejectReason"] == (
        "video-motion-compare not declared as block for masked media motion"
    )


def test_fully_masked_video_rejects_impl_static_image(tmp_path: Path) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        impl_row=_masked_media_row(
            visibleMediaKinds=["img"],
            visibleMediaKindCounts={"img": 1},
        ),
        plan_checks=[
            "required-media-coverage",
            "video-play-proof",
            "hero-composite-check",
        ],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["maskedMediaOnly"]["rejectReason"] == (
        "visible media kind mismatch (video != img)"
    )


def test_fully_masked_media_rejects_different_kind_multiplicity(tmp_path: Path) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        ref_row=_masked_media_row(
            visibleMediaCount=3,
            visibleMediaKinds=["img", "video"],
            visibleMediaKindCounts={"img": 1, "video": 2},
        ),
        impl_row=_masked_media_row(
            visibleMediaCount=3,
            visibleMediaKinds=["img", "video"],
            visibleMediaKindCounts={"img": 2, "video": 1},
        ),
        plan_checks=[
            "required-media-coverage",
            "video-play-proof",
            "video-motion-compare",
            "hero-composite-check",
        ],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["maskedMediaOnly"]["rejectReason"] == (
        "visible media kind counts mismatch "
        "(img:1,video:2 != img:2,video:1)"
    )


def test_fully_masked_media_requires_recognized_media_to_cover_the_section(
    tmp_path: Path,
) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        mask_tag="div",
        plan_checks=[
            "required-media-coverage",
            "video-play-proof",
            "hero-composite-check",
        ],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["maskedMediaOnly"]["maskedMediaKinds"] == []
    assert panel["maskedMediaOnly"]["recognizedMediaMaskPct"] == 0.0


def test_fully_masked_canvas_defers_only_with_runtime_frame_proof(tmp_path: Path) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        mask_tag="canvas",
        ref_row=_masked_media_row(
            visibleMediaKinds=["canvas"],
            visibleMediaKindCounts={"canvas": 1},
        ),
        impl_row=_masked_media_row(
            visibleMediaKinds=["canvas"],
            visibleMediaKindCounts={"canvas": 1},
        ),
        plan_checks=[
            "required-media-coverage",
            "runtime-frame-proof",
            "video-motion-compare",
            "geometry-sanity",
        ],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "structural-only"
    assert panel["maskedMediaOnly"]["maskedMediaKinds"] == ["canvas"]
    assert panel["maskedMediaOnly"]["requiredLiveMediaProofs"] == ["runtime-frame-proof"]


def test_fully_masked_media_only_requires_plan_declared_structural_anchor(
    tmp_path: Path,
) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        plan_checks=["required-media-coverage", "video-play-proof"],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["reason"] is not None
    assert "masked:" in panel["reason"]


def test_fully_masked_media_only_requires_observed_structure(tmp_path: Path) -> None:
    ref_row = _masked_media_row()
    impl_row = _masked_media_row()
    ref_row.pop("tag")
    impl_row.pop("tag")
    ref_row.pop("rect")
    impl_row.pop("rect")
    d = _build_masked_media_sections_dir(
        tmp_path,
        ref_row=ref_row,
        impl_row=impl_row,
        plan_checks=[
            "required-media-coverage",
            "video-play-proof",
            "video-motion-compare",
            "hero-composite-check",
        ],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["reason"] is not None
    assert "masked:" in panel["reason"]


def test_fully_masked_deferral_thresholds_are_not_operator_tunable() -> None:
    env = os.environ.copy()
    env.update(
        {
            "UI_CLONE_FULLY_MASKED_MEDIA_MIN_PCT": "0",
            "UI_CLONE_FULLY_MASKED_MEDIA_MIN_SCORE": "0",
            "UI_CLONE_SECTION_GEOM_REL_TOL": "100",
            "UI_CLONE_SECTION_GEOM_ABS_TOL": "100000",
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; import ui_clone.section_guards as g; "
                "print(json.dumps([g.FULLY_MASKED_MEDIA_MIN_PCT, "
                "g.FULLY_MASKED_MEDIA_MIN_SCORE, g.GEOM_REL_TOL, g.GEOM_ABS_TOL]))"
            ),
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout) == [99.5, 0.98, 0.01, 2.0]


def test_fully_masked_media_only_requires_verification_plan(tmp_path: Path) -> None:
    d = _build_masked_media_sections_dir(tmp_path)

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["reason"] is not None
    assert "masked:" in panel["reason"]


def test_fully_masked_media_does_not_bind_unrelated_ancestor_plan(tmp_path: Path) -> None:
    d = _build_masked_media_sections_dir(tmp_path / "component")
    (tmp_path / "verification-plan.json").write_text(
        json.dumps(
            _plan(
                [
                    "required-media-coverage",
                    "video-play-proof",
                    "hero-composite-check",
                ]
            )
        ),
        encoding="utf-8",
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "pass-only"
    assert panel["maskedMediaOnly"]["declaredBlockChecks"] == []


def test_fully_masked_media_finds_canonical_viewport_plan(tmp_path: Path) -> None:
    d = _build_masked_media_sections_dir(
        tmp_path,
        viewport=True,
        plan_checks=[
            "required-media-coverage",
            "video-play-proof",
            "video-motion-compare",
            "hero-composite-check",
        ],
    )

    panel = evaluate_sections_dir(d)["sections"]["masked-hero"]

    assert panel["policy"] == "structural-only"


def test_section_compare_guard_run_is_mandatory() -> None:
    """Review-1 MAJOR 2 lock: the guards invocation in section-compare.sh
    must not be soft-failed (`|| true`) — a crash must inject a blocking
    setup row, or blank-crop AE=0 passes silently flow through again."""
    script = ROOT / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")
    guard_call_idx = text.index("ui_clone.section_guards")
    window = text[guard_call_idx - 400 : guard_call_idx + 400]
    assert "|| true" not in window, "section_guards execution must be mandatory — no soft-fail"
    assert "GUARDS_FAILED" in text
    assert "crop-guard evaluation failed" in text


def test_section_compare_applies_all_policy_before_black_detector() -> None:
    """Blank-reference guards must prevent black-crop false implementation FAILs."""
    script = ROOT / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")
    guard_idx = text.index('if [ "$GUARD_POLICY" = "all" ]')
    black_idx = text.index("all-black/blank-impl detector")
    assert guard_idx < black_idx


def test_section_compare_applies_structural_policy_only_after_pixel_verdict() -> None:
    script = ROOT / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")
    structural_idx = text.index('if [ "$GUARD_POLICY" = "structural-only" ]')
    all_idx = text.index('if [ "$GUARD_POLICY" = "all" ]')
    black_idx = text.index("all-black/blank-impl detector")
    verdict_idx = text.index("# Crop-evidence guard conversion")
    assert all_idx < black_idx < verdict_idx < structural_idx


def test_section_compare_guard_structural_policy_preserves_fail_and_motion_protection() -> None:
    script = ROOT / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")
    structural_idx = text.index('if [ "$GUARD_POLICY" = "structural-only" ]')
    generic_guard_idx = text.index('    if [ "$STATUS" = "✅" ]', structural_idx)
    window = text[structural_idx:generic_guard_idx]
    assert '[ "$STATUS" = "✅" ]' in window
    assert "is_motion_structural_only_protected" in window
    assert '"$GUARD_SOURCE" = "masked-media-motion"' in window
    assert "FAIL_COUNT=$((FAIL_COUNT - 1))" not in window


def test_guard_structural_rows_remain_in_non_structural_evidence_denominator() -> None:
    script = ROOT / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")
    assert "GUARD_STRUCTURAL_COUNT=0" in text
    assert "GUARD_STRUCTURAL_COUNT=$((GUARD_STRUCTURAL_COUNT + 1))" in text
    assert (
        "TOTAL_ROWS=$((PASS_COUNT + FAIL_COUNT + SKIP_COUNT + UNMEASURED_COUNT + "
        "GUARD_STRUCTURAL_COUNT))"
    ) in text


def test_section_compare_evaluates_current_crop_manifest_not_ref_glob() -> None:
    """Frozen orphan PNGs must not become fake missing implementation rows."""
    script = ROOT / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")
    assert "crop-manifest.json" in text
    assert 'REF_IMGS=("$DIR/sections/ref/"*.png)' not in text
