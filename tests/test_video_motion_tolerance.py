"""Video-motion residual tolerance (e2e-8 brief, three live-network classes).

tmp/ref/realfood-e2e-8/brief/video-motion-residual-gaps-e2e-8.json documented
three reference-side capture artifacts that the impl cannot legally absorb:

(a) media-presentation state (preview/poster crossfade) — spec marks the
    targets dynamic:true; section-compare masks them (EXCLUDE_DYNAMIC) but
    position-compare had no masking;
(b) CDN re-encode variance — an all-image viewport deterministically landed
    at SSIM 0.89957 vs the fixed 0.90 threshold while live DOM parity was
    exact (ref-vs-ref noise is the only honest calibration);
(c) splash load latency — ref first-paint jitters 18-108 frames run-to-run,
    so the absolute first-change offset delta (MAX_ALIGN_DELTA=12) fails
    honest runs; arc-INTERNAL timing is the property that distinguishes a
    wrong impl timeline.

Design reviewed by an independent Codex pass (2026-06-11): masking must be
spec-exact + area-capped + per-side-counted; the noise floor applies only in
a narrow borderline band with an absolute floor; arc timing keeps the
no-change-point anti-bypass.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POS_LIB = REPO / "scripts" / "verify" / "lib" / "position-compare.sh"
ALIGN_LIB = REPO / "scripts" / "verify" / "lib" / "frame-align.sh"
SCRIPT = REPO / "scripts" / "verify" / "video-transition-compare.sh"


def _bash(snippet: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, timeout=120)


def _make_frames(d: Path, colors: list[str], prefix: str = "f") -> None:
    d.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(colors, start=1):
        subprocess.run(
            # 128x128 = 16384 px: a full-frame color change clears the
            # 5000-px changed-pixel threshold in analyze_timing.
            ["magick", "-size", "128x128", f"xc:{c}", str(d / f"{prefix}-{i:06d}.png")],
            check=True,
            capture_output=True,
        )


# ── (c) arc-internal splash timing ──────────────────────────────────────────


def test_analyze_timing_writes_last_change(tmp_path: Path) -> None:
    d = tmp_path / "frames"
    _make_frames(d, ["white", "white", "black", "black", "red", "red"])
    r = _bash(f'source "{ALIGN_LIB}"; analyze_timing "{d}" test')
    assert r.returncode == 0, r.stdout + r.stderr
    assert (d / ".first-change").read_text().strip() == "3"
    assert (d / ".last-change").is_file(), ".last-change sidecar missing"
    assert (d / ".last-change").read_text().strip() == "5"


def test_analyze_timing_counts_pixels_not_q16_error_magnitude(tmp_path: Path) -> None:
    """One changed pixel stays below, while a 6000px region clears 5000."""
    d = tmp_path / "frames"
    _make_frames(d, ["white", "white", "white"])
    subprocess.run(
        [
            "magick",
            str(d / "f-000002.png"),
            "-fill",
            "black",
            "-draw",
            "point 0,0",
            str(d / "f-000002.png"),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "magick",
            "-size",
            "128x128",
            "xc:white",
            "-fill",
            "black",
            "-draw",
            "rectangle 0,0 99,59",
            str(d / "f-000003.png"),
        ],
        check=True,
        capture_output=True,
    )
    r = _bash(f'source "{ALIGN_LIB}"; analyze_timing "{d}" test')
    assert r.returncode == 0, r.stdout + r.stderr
    assert (d / ".first-change").read_text().strip() == "3"
    assert (d / ".last-change").read_text().strip() == "3"
    assert "1 change points detected" in r.stdout


def test_analyze_timing_no_changes_writes_one_for_both(tmp_path: Path) -> None:
    d = tmp_path / "frames"
    _make_frames(d, ["white", "white", "white"])
    _bash(f'source "{ALIGN_LIB}"; analyze_timing "{d}" test')
    assert (d / ".first-change").read_text().strip() == "1"
    assert (d / ".last-change").read_text().strip() == "1"


def test_arc_timing_verdict_equal_arcs_pass(tmp_path: Path) -> None:
    r = _bash(f'source "{ALIGN_LIB}"; arc_timing_verdict 109 300 49 240 18')
    assert r.returncode == 0, r.stdout + r.stderr


def test_arc_timing_verdict_large_arc_delta_fails(tmp_path: Path) -> None:
    # ref arc 191 frames vs impl arc 120 frames -> delta 71 > 18
    r = _bash(f'source "{ALIGN_LIB}"; arc_timing_verdict 109 300 49 169 18')
    assert r.returncode != 0


def test_arc_timing_verdict_one_side_no_motion_fails(tmp_path: Path) -> None:
    """Anti-bypass: a side with no change points (first==last==1) cannot pass
    against a side with a real arc — missing transition."""
    r = _bash(f'source "{ALIGN_LIB}"; arc_timing_verdict 109 300 1 1 18')
    assert r.returncode != 0


def test_script_no_longer_hard_fails_on_offset_delta() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert "arc_timing_verdict" in body, "splash mode must use arc-internal timing"
    # the old absolute-offset hard-fail incremented FAIL on OFF_DELTA alone
    assert "start-timing mismatch: first-change offsets differ" not in body, (
        "absolute first-change offset delta must be informational, not a FAIL "
        "(ref-side load-latency jitter measured at 18-108 frames run-to-run)"
    )


# ── (c2) looping-video arc bound (e2e-9 splash residual) ────────────────────
#
# tmp/ref/realfood-e2e-9/brief/video-motion-residual-gaps-e2e-9.json: a hero
# bg <video loop> that defeats the freeze stub (autoplay remount after the
# re-pause sweeps) keeps whole-frame change detection alive to the END of
# each clip, so the measured arc equals the RECORDING length and the verdict
# compares recorder-stop jitter (ref 486 frames vs impl 390 — arc delta 96
# == recording-length delta 96). When BOTH sides report a looping video,
# arc measurement is bounded to the common recording window
# min(ref_total, impl_total); a splash that settles early keeps
# last-change << cutoff, so a wrong impl timeline still fails.


def test_has_looping_video_double_encoded_sidecar(tmp_path: Path) -> None:
    """Real sidecar shape: agent-browser eval output is a JSON-encoded string."""
    sc = tmp_path / "media-freeze-ref.json"
    sc.write_text(
        '"[{\\"src\\":\\"bgv.mp4\\",\\"autoplay\\":true,\\"loop\\":true,\\"muted\\":true}]"'
    )
    r = _bash(f'source "{ALIGN_LIB}"; has_looping_video "{sc}"')
    assert r.returncode == 0, r.stdout + r.stderr


def test_has_looping_video_plain_array_no_loop(tmp_path: Path) -> None:
    sc = tmp_path / "media-freeze-impl.json"
    sc.write_text('[{"src": "clip.mp4", "autoplay": true, "loop": false}]')
    r = _bash(f'source "{ALIGN_LIB}"; has_looping_video "{sc}"')
    assert r.returncode != 0


def test_has_looping_video_missing_or_garbage_is_false(tmp_path: Path) -> None:
    r = _bash(f'source "{ALIGN_LIB}"; has_looping_video "{tmp_path}/nope.json"')
    assert r.returncode != 0
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all")
    r = _bash(f'source "{ALIGN_LIB}"; has_looping_video "{bad}"')
    assert r.returncode != 0


def test_clamp_arc_last_bounds_to_cutoff(tmp_path: Path) -> None:
    r = _bash(f'source "{ALIGN_LIB}"; clamp_arc_last 61 486 390')
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == "390"


def test_clamp_arc_last_noop_inside_window(tmp_path: Path) -> None:
    r = _bash(f'source "{ALIGN_LIB}"; clamp_arc_last 61 300 390')
    assert r.stdout.strip() == "300"


def test_clamp_arc_last_never_below_first(tmp_path: Path) -> None:
    """Motion that starts after the cutoff collapses to arc 0 (conservative:
    the anti-bypass one-side-no-motion rule then applies)."""
    r = _bash(f'source "{ALIGN_LIB}"; clamp_arc_last 400 486 390')
    assert r.stdout.strip() == "400"


def test_e2e9_looping_video_arc_passes_when_bounded(tmp_path: Path) -> None:
    """The exact e2e-9 numbers: unbounded arcs fail on recording-length
    noise; bounded to the common window min(486,390)=390 they agree."""
    # unbounded: ref arc 486-61=425, impl arc 390-60=330 -> delta 95 > 18
    r = _bash(f'source "{ALIGN_LIB}"; arc_timing_verdict 61 486 60 390 18')
    assert r.returncode != 0, "unbounded recording-length delta must fail without the bound"
    # bounded: ref last 486->390 (arc 329), impl arc 330 -> delta 1 <= 18
    r = _bash(
        f'source "{ALIGN_LIB}"; '
        f"RL=$(clamp_arc_last 61 486 390); IL=$(clamp_arc_last 60 390 390); "
        f'arc_timing_verdict 61 "$RL" 60 "$IL" 18'
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_script_bounds_splash_arc_only_for_looping_video() -> None:
    """The bound is gated on BOTH freeze sidecars reporting loop:true — a
    one-sided looping video (impl missing it) keeps the unbounded verdict."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert "has_looping_video" in body, (
        "splash arc must be bounded to the common recording window when a "
        "looping video defeats the freeze (e2e-9 arc==recording-length class)"
    )
    assert "clamp_arc_last" in body
    assert body.count('has_looping_video "$OUT_DIR/media-freeze-') >= 2, (
        "bound must require loop evidence from BOTH sides' sidecars"
    )


# ── (a2) dynamic-mask area cap denominator (e2e-9 scroll residual) ──────────
#
# tmp/ref/realfood-e2e-9/transitions/video-motion/scroll/dynamic-mask-ref.json:
# areaPct values of 201/135.6/63.4 PERCENT prove the cap compared full-page
# element area against a single viewport's area — on a 20133px-tall page the
# spec-declared eatReal carousel (41.8% of one viewport, ~1.9% of the page)
# could never be masked and pos-024 failed at every fan-out viewport. The
# masked-area cap must be measured against the full scrolled page area — the
# surface the position sweep actually compares.


def test_mask_area_cap_uses_page_area_denominator() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert (
        "scrollHeight"
        in body.split("mask_dynamic_selectors()", 1)[1].split("capture_scroll_positions()", 1)[0]
    ), (
        "mask_dynamic_selectors must measure the area cap against the full "
        "scrolled page area, not a single viewport (e2e-9: areaPct 201% on a "
        "22-viewport page made every full-bleed dynamic section unmaskable)"
    )


# ── (b) ref-vs-ref noise-floor calibration ──────────────────────────────────


def test_noise_floor_allows_within_margin(tmp_path: Path) -> None:
    # impl 0.8996 vs refref noise floor 0.9050: 0.8996 >= 0.9050-0.015 -> pass
    r = _bash(f'source "{POS_LIB}"; noise_floor_allows 0.8996 0.9050 0.90')
    assert r.returncode == 0, r.stdout + r.stderr


def test_noise_floor_denies_below_margin(tmp_path: Path) -> None:
    # impl 0.86 vs refref 0.99: far below the ref's own noise -> genuine defect
    r = _bash(f'source "{POS_LIB}"; noise_floor_allows 0.86 0.99 0.90')
    assert r.returncode != 0


def test_noise_floor_denies_below_absolute_floor(tmp_path: Path) -> None:
    """A catastrophically noisy ref-vs-ref must not whitelist a bad impl:
    absolute floor 0.87 applies regardless of the measured noise."""
    r = _bash(f'source "{POS_LIB}"; noise_floor_allows 0.85 0.852 0.90')
    assert r.returncode != 0


def test_noise_floor_only_applies_in_borderline_band(tmp_path: Path) -> None:
    """Below threshold-0.02 the calibration must not even be consulted —
    noise_floor_allows itself refuses out-of-band scores."""
    r = _bash(f'source "{POS_LIB}"; noise_floor_allows 0.8700 0.99 0.90')
    assert r.returncode != 0


def test_compare_position_frames_writes_ssim_sidecar(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    diff = tmp_path / "diff"
    for d, colors in ((ref, ["red", "blue"]), (impl, ["red", "green"])):
        d.mkdir()
        for i, c in enumerate(colors):
            subprocess.run(
                ["magick", "-size", "64x64", f"xc:{c}", str(d / f"pos-{i:03d}.png")],
                check=True,
                capture_output=True,
            )
    _bash(f'source "{POS_LIB}"; compare_position_frames "{ref}" "{impl}" "{diff}" 0.90')
    sidecar = diff / "position-ssim.tsv"
    assert sidecar.is_file(), "per-position SSIM sidecar missing"
    rows = dict(line.split("\t")[:2] for line in sidecar.read_text().strip().splitlines())
    assert "pos-000.png" in rows and "pos-001.png" in rows


# ── (a) dynamic-selector masking ────────────────────────────────────────────


def test_dynamic_selectors_from_spec(tmp_path: Path) -> None:
    spec = tmp_path / "transition-spec.json"
    spec.write_text(
        json.dumps(
            {
                "transitions": [
                    {"id": "a", "dynamic": True, "target": ".hero video"},
                    {"id": "b", "dynamic": False, "target": ".static"},
                    {"id": "c", "dynamic": True, "target": ".preview"},
                    {"id": "d", "dynamic": True},  # no target -> skipped
                ]
            }
        )
    )
    r = _bash(f'source "{POS_LIB}"; dynamic_selectors_from_spec "{spec}"')
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == ".hero video||.preview"


def test_dynamic_selectors_from_spec_missing_file_is_empty(tmp_path: Path) -> None:
    r = _bash(f'source "{POS_LIB}"; dynamic_selectors_from_spec "{tmp_path}/nope.json"')
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_dynamic_selectors_include_grounded_canvas_surface(tmp_path: Path) -> None:
    spec = tmp_path / "transition-spec.json"
    spec.write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "canvas-physics",
                        "dynamic": True,
                        "target": ".playground-content",
                        "animation": {
                            "type": "canvas physics and infinite marquee",
                            "property": "canvas pixels and translateX",
                        },
                    },
                    {
                        "id": "duplicate-canvas-target",
                        "dynamic": True,
                        "target": "canvas",
                        "animation": {"type": "canvas"},
                    },
                ]
            }
        )
    )
    r = _bash(f'source "{POS_LIB}"; dynamic_selectors_from_spec "{spec}"')
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == ".playground-content||canvas"


def test_script_wires_dynamic_masking_into_scroll_capture() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert "mask_dynamic_selectors" in body, (
        "scroll capture must apply spec-declared dynamic masking"
    )
    # anti-cheat guarantees called out by the codex review
    assert "MASK_AREA_CAP_PCT" in body or "mask area" in body.lower()


def test_selector_recordings_mask_dynamic_backdrops_but_protect_target() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'mask_dynamic_selectors "${SESSION}-orig" ref' in body
    assert 'mask_dynamic_selectors "${SESSION}-impl" impl' in body
    assert "protectedTargets" in body
    assert "el.contains(intendedTarget)" in body
    assert "const intendedTarget = protectedTargets.find" in body
    assert "for (const sibling of parent.children)" in body
    assert "sibling.style.visibility = 'hidden'" in body
    assert "hiddenCount" in body
    assert "data-ui-clone-dynamic-mask" in body
    assert "visibility:hidden!important" in body
    assert "Boolean(intendedTarget) || (maskedPct + areaPct) <= cap" in body


def test_selector_runtime_masks_are_applied_after_record_context_swap() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    ref_segment = body.split("# ── Phase 1: Record original", 1)[1].split(
        "# ── Phase 2: Record implementation", 1
    )[0]
    impl_segment = body.split("# ── Phase 2: Record implementation", 1)[1].split(
        'echo "  ✓ Implementation recorded"', 1
    )[0]
    assert ref_segment.index('record start "$OUT_DIR/ref-video/raw.webm"') < (
        ref_segment.index('mask_dynamic_selectors "${SESSION}-orig" ref')
    )
    assert impl_segment.index('record start "$OUT_DIR/impl-video/raw.webm"') < (
        impl_segment.index('mask_dynamic_selectors "${SESSION}-impl" impl')
    )


def test_hover_wrapper_forwards_spec_dynamic_selectors() -> None:
    hover = (REPO / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh").read_text(
        encoding="utf-8"
    )
    assert "dynamic_selectors_from_spec" in hover
    assert "VIDEO_COMPARE_DYNAMIC_SELECTORS" in hover


def test_hover_wrapper_forwards_affected_selector_to_video_compare() -> None:
    hover = (REPO / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh").read_text(
        encoding="utf-8"
    )
    assert "affected_selector_for_hover()" in hover
    assert 'item.get("affectedTarget")' in hover
    assert 'item.get("affected")' in hover
    assert 'AFFECTED_SELECTOR="$(affected_selector_for_hover "$SELECTOR"' in hover
    assert 'VIDEO_COMPARE_AFFECTED_SELECTOR="$AFFECTED_SELECTOR"' in hover
