"""ui_clone.gates.masked_region_motion — runtime motion proof for dynamic-masked
timer/carousel regions.

Loop-9/10 regression class: the eatReal footer carousel's region is dynamic-
masked out of pixel comparison, its timer runs, content swaps instantly —
but the spec-declared card-transform motion never happens (transition-duration
0s). Binary "something changed" probes pass it; this gate samples the live
impl DOM and verifies phase-free properties derived from spec/bundle truth
only: state count, change cadence, per-channel coverage, and item-sequence
membership.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ui_clone.gates.masked_region_motion import (
    evaluate_entry,
    extract_items,
    parse_params,
    select_entries,
)

CAROUSEL_ENTRY: dict[str, Any] = {
    "id": "eatreal-food-carousel",
    "trigger": "timer (setInterval 3500ms)",
    "source_chunk": "page-x.js",
    "bundle_branch": (
        "setInterval(()=>{t(ec())},3500); "
        "foods=[{food:'Steak',image:'/images/pyramid/steak.webp'},... x22]"
    ),
    "target": ".cards, .h2_food",
    "animation": {
        "property": "img src / label text / card transforms",
        "duration": "3500ms cycle",
        "type": "carousel",
    },
    "dynamic": True,
}


def _samples(
    *,
    interval_ms: int = 3500,
    cadence_ms: int = 250,
    total_ms: int = 8000,
    change_imgs: bool = True,
    change_text: bool = True,
    change_cards: bool = True,
    labels: list[str] | None = None,
) -> list[dict]:
    labels = labels or ["Steak", "Salmon", "Chicken"]
    out = []
    for t in range(0, total_ms + 1, cadence_ms):
        phase = t // interval_ms
        label = labels[phase % len(labels)]
        out.append(
            {
                "t": t,
                "imgSrcs": [f"{label.lower()}.webp"] if change_imgs else ["fixed.webp"],
                "text": f"eat {label}" if change_text else "eat fixed",
                "cards": (
                    [f"matrix({phase})|{phase}|1"] if change_cards
                    else ["matrix(1, 0, 0, 1, 0, 0)|0|1"]
                ),
            }
        )
    return out


# ── selection + params ─────────────────────────────────────────────────


def test_selects_dynamic_timer_carousel_entries() -> None:
    spec = {
        "transitions": [
            CAROUSEL_ENTRY,
            {"id": "static", "dynamic": False, "trigger": "hover"},
            {"id": "video", "dynamic": True, "trigger": "page load (video autoplay)"},
        ]
    }
    entries = select_entries(spec)
    assert [e["id"] for e in entries] == ["eatreal-food-carousel"]


def test_parse_params_interval_channels_selectors() -> None:
    params = parse_params(CAROUSEL_ENTRY)
    assert params["intervalMs"] == 3500
    assert params["channels"] == {"imgSrc", "text", "cardTransform"}
    assert params["selectors"] == [".cards", ".h2_food"]
    assert not params["unmeasurable"]


def test_parse_params_uses_structured_animation_interval_ms() -> None:
    entry: dict[str, Any] = dict(
        CAROUSEL_ENTRY,
        trigger="timer",
        bundle_branch="final Eat Real card stack",
    )
    entry["animation"] = {
        "property": "card asset and transform state",
        "intervalMs": 2000,
        "type": "auto-carousel",
    }

    params = parse_params(entry)

    assert params["intervalMs"] == 2000
    assert not params["unmeasurable"]


def test_structured_animation_interval_ms_overrides_legacy_text() -> None:
    entry = dict(CAROUSEL_ENTRY)
    entry["animation"] = {**CAROUSEL_ENTRY["animation"], "intervalMs": 2000}

    assert parse_params(entry)["intervalMs"] == 2000


def test_invalid_structured_animation_interval_ms_fails_closed() -> None:
    entry = dict(CAROUSEL_ENTRY)
    entry["animation"] = {**CAROUSEL_ENTRY["animation"], "intervalMs": 0}

    params = parse_params(entry)

    assert params["intervalMs"] is None
    assert params["unmeasurable"]


def test_parse_params_without_interval_is_unmeasurable() -> None:
    entry = dict(CAROUSEL_ENTRY, trigger="timer", bundle_branch="setInterval(fn)")
    entry["animation"] = {"property": "img src", "duration": "cycle", "type": "carousel"}
    params = parse_params(entry)
    assert params["unmeasurable"]


def test_extract_items_from_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "bundles" / "page-x.js"
    bundle.parent.mkdir(parents=True)
    bundle.write_text(
        'let foods=[{food:"Steak",image:"/i/steak.webp"},{food:"Salmon",image:"/i/salmon.webp"},'
        '{food:"Chicken",image:"/i/chicken.webp"}];setInterval(()=>{t(ec())},3500)',
        encoding="utf-8",
    )
    items = extract_items(CAROUSEL_ENTRY, tmp_path)
    assert items == ["Steak", "Salmon", "Chicken"]


# ── verdicts ───────────────────────────────────────────────────────────


def test_motionless_cards_fail_channel_coverage() -> None:
    """The user-observed loop-9 defect: img src + text swap but the declared
    card-transform channel never changes."""
    params = parse_params(CAROUSEL_ENTRY)
    verdict = evaluate_entry(params, _samples(change_cards=False))
    assert verdict["status"] == "fail"
    assert "cardTransform" in str(verdict["reasons"])


def test_fully_static_region_fails_state_count() -> None:
    params = parse_params(CAROUSEL_ENTRY)
    verdict = evaluate_entry(
        params,
        _samples(change_imgs=False, change_text=False, change_cards=False),
    )
    assert verdict["status"] == "fail"
    assert any("state" in r for r in verdict["reasons"])


def test_healthy_carousel_passes() -> None:
    params = parse_params(CAROUSEL_ENTRY)
    verdict = evaluate_entry(params, _samples())
    assert verdict["status"] == "pass", verdict


def test_animated_transition_bursts_count_as_one_change() -> None:
    """A 0.4s animated swap makes 2-3 consecutive samples differ (mid-flight
    transforms). Those bursts are ONE logical change — cadence must measure
    burst-start gaps, not sample-to-sample deltas."""
    params = parse_params(CAROUSEL_ENTRY)
    samples = []
    labels = ["Steak", "Salmon", "Chicken"]
    for t in range(0, 8001, 250):
        phase = t // 3500
        within = t - phase * 3500
        label = labels[phase % 3]
        # 0..500ms after each boundary: transform still animating
        animating = within < 500 and phase > 0
        samples.append(
            {
                "t": t,
                "imgSrcs": [f"{label.lower()}.webp"],
                "text": f"eat {label}",
                "cards": [f"matrix({phase}.{within if animating else 0})|{phase}|1"],
            }
        )
    verdict = evaluate_entry(params, samples)
    assert verdict["status"] == "pass", verdict


def test_faster_than_declared_cadence_passes() -> None:
    """batch-13: the declared interval is a TEXT-PARSED estimate and is often
    LARGER than the real change period (the live eatReal carousel changes every
    ~1015ms vs a declared 3500ms). Changing FASTER than declared is genuine
    paced motion, not a defect — it must pass so the reference clears its own
    gate (achievability)."""
    params = parse_params(CAROUSEL_ENTRY)
    verdict = evaluate_entry(params, _samples(interval_ms=1200))
    assert verdict["status"] == "pass", verdict


def test_too_slow_cadence_fails() -> None:
    """Detection preserved: a region that goes >2x the declared interval between
    changes is effectively static (the instant-swap / no-real-motion cheat) and
    still FAILs."""
    params = parse_params(CAROUSEL_ENTRY)  # declared 3500ms -> ceiling ~7525ms
    verdict = evaluate_entry(params, _samples(interval_ms=9000, total_ms=20000))
    assert verdict["status"] == "fail", verdict
    assert any("cadence" in r for r in verdict["reasons"]), verdict


def test_sequence_must_be_cyclic_contiguous() -> None:
    params = parse_params(CAROUSEL_ENTRY)
    params["items"] = ["Steak", "Salmon", "Chicken", "Cheese"]
    # observed order skips Salmon -> not contiguous
    bad = _samples(labels=["Steak", "Chicken", "Cheese"])
    verdict = evaluate_entry(params, bad)
    assert verdict["status"] == "fail"
    assert any("sequence" in r for r in verdict["reasons"])


def test_sequence_cyclic_wrap_passes() -> None:
    params = parse_params(CAROUSEL_ENTRY)
    params["items"] = ["Steak", "Salmon", "Chicken"]
    wrapped = _samples(labels=["Chicken", "Steak", "Salmon"])
    verdict = evaluate_entry(params, wrapped)
    assert verdict["status"] == "pass", verdict


def test_single_change_window_skips_cadence_not_fails() -> None:
    """A 1.5x window may catch only one change — cadence is then
    unmeasured (not a failure), state-count still proves motion."""
    params = parse_params(CAROUSEL_ENTRY)
    verdict = evaluate_entry(params, _samples(total_ms=5000))
    assert verdict["status"] == "pass", verdict


def test_unmeasurable_params_fail_explicitly() -> None:
    entry = dict(CAROUSEL_ENTRY, trigger="timer", bundle_branch="setInterval(fn)")
    entry["animation"] = {"property": "img src", "duration": "cycle", "type": "carousel"}
    params = parse_params(entry)
    verdict = evaluate_entry(params, _samples())
    assert verdict["status"] == "fail"
    assert "unmeasurable" in str(verdict["reasons"])
    assert verdict.get("remediation")


# ── CLI ────────────────────────────────────────────────────────────────


def test_cli_verdict_writes_artifact(tmp_path: Path) -> None:
    import subprocess
    import sys

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [CAROUSEL_ENTRY]}), encoding="utf-8"
    )
    samples_file = tmp_path / "samples.json"
    samples_file.write_text(
        json.dumps({"eatreal-food-carousel": _samples(change_cards=False)}),
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-m", "ui_clone.gates.masked_region_motion",
         "verdict", str(ref), str(samples_file)],
        capture_output=True, text=True, timeout=60, cwd=str(root),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "masked-region-motion.json").read_text(encoding="utf-8"))
    assert art["status"] == "fail"
    assert art["entries"][0]["id"] == "eatreal-food-carousel"


def test_sampler_filters_unpainted_nodes() -> None:
    """Review-2 finding 4 lock: the sampler JS must restrict digest channels
    to painted nodes (nonzero rect, visible styles, viewport band) — a
    hidden counter mutation must not satisfy the motion proof. (Behavioral
    bypass fixture: a page with static visible cards + a display:none
    mutator FAILs state-count when run live.)"""
    script = (
        Path(__file__).resolve().parents[2]
        / "skills" / "visual-debug" / "scripts"
        / "masked-region-motion-proof-check.sh"
    )
    text = script.read_text(encoding="utf-8")
    assert "isPainted" in text
    assert 'visibility === "hidden"' in text
    for channel_use in ("isPainted(el)", "isPainted(im)", "isPainted(c)"):
        assert channel_use in text, channel_use
