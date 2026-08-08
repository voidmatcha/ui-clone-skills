"""Pre-ebpb harness batch: H9 (derived capture settle) + V-1 (in-page
viewport assertion).

H9 (loop-nvti-3/4): the fixed 0.5s settle captured choreography-alive refs
mid-transition — transient ref crops overturned two eyeball observations.
Settle must be DERIVED from the spec's longest transition (+rest margin),
never site-tuned (fable constraint).

V-1 (loop-nvti-4): the agent-browser session viewport silently reverts
mid-session (loop-145 confound); a 14-depth sweep ran at 1280x633 and was
discarded. Every screenshot must be preceded by an in-page innerWidth
assertion that re-sets once and aborts on persistent mismatch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ui_clone.section_capture import _ensure_viewport, derive_settle_seconds

SC_SH = (Path(__file__).resolve().parents[1]
         / "skills" / "visual-debug" / "scripts" / "section-compare.sh")


# ── H9: derive_settle_seconds ─────────────────────────────────────────
def _spec(tmp_path: Path, durations: list[float]) -> Path:
    p = tmp_path / "transition-spec.json"
    p.write_text(json.dumps({
        "transitions": [
            {"id": f"t{i}", "animation": {"duration": d}}
            for i, d in enumerate(durations)
        ]
    }), encoding="utf-8")
    return p


def test_settle_derived_from_longest_transition(tmp_path: Path) -> None:
    # nvti shape: longest declared transition 1.06s -> 0.4 + 1.06 = 1.46
    assert derive_settle_seconds(_spec(tmp_path, [0.6, 1.06, 0.4])) == 1.46


def test_settle_floor_when_spec_absent(tmp_path: Path) -> None:
    assert derive_settle_seconds(tmp_path / "missing.json") == 0.5


def test_settle_floor_when_no_durations(tmp_path: Path) -> None:
    p = tmp_path / "transition-spec.json"
    p.write_text('{"transitions": [{"id": "x"}]}', encoding="utf-8")
    assert derive_settle_seconds(p) == 0.5


def test_settle_capped_for_huge_durations(tmp_path: Path) -> None:
    assert derive_settle_seconds(_spec(tmp_path, [30.0])) == 4.0


def _spec_raw(tmp_path: Path, durations: list[object]) -> Path:
    """Spec whose animation.duration values are written verbatim (strings or
    numbers), mirroring what transition-spec-extract actually emits."""
    p = tmp_path / "transition-spec.json"
    p.write_text(json.dumps({
        "transitions": [
            {"id": f"t{i}", "animation": {"duration": d}}
            for i, d in enumerate(durations)
        ]
    }), encoding="utf-8")
    return p


def test_settle_parses_ms_duration_strings(tmp_path: Path) -> None:
    # transition-spec-extract emits CSS ms strings; 1200ms -> 0.4 + 1.2 = 1.6.
    # Regression: float("1200ms") raised ValueError -> silent 0.5s floor ->
    # reference sections captured mid-transition (codex P2 / extract H1).
    assert derive_settle_seconds(_spec_raw(tmp_path, ["1200ms"])) == 1.6


def test_settle_parses_s_duration_strings(tmp_path: Path) -> None:
    assert derive_settle_seconds(_spec_raw(tmp_path, ["1s", "800ms"])) == 1.4


def test_settle_parses_bare_numeric_string_as_seconds(tmp_path: Path) -> None:
    # transition-spec-rules.md documents unitless seconds; a JSON string "1.06"
    # must still read as 1.06s, not fall through.
    assert derive_settle_seconds(_spec_raw(tmp_path, ["1.06"])) == 1.46


def test_settle_skips_garbage_duration_string(tmp_path: Path) -> None:
    assert derive_settle_seconds(_spec_raw(tmp_path, ["fast"])) == 0.5


def test_settle_unparseable_spec_keeps_floor(tmp_path: Path) -> None:
    p = tmp_path / "transition-spec.json"
    p.write_text("not json", encoding="utf-8")
    assert derive_settle_seconds(p) == 0.5


def test_section_compare_wires_derivation() -> None:
    src = SC_SH.read_text(encoding="utf-8")
    assert "--print-settle" in src, "section-compare.sh must derive the settle"
    assert 'WAIT_SCROLL_SETTLE_USER' in src, "caller pin must win over derivation"
    # fable substitute-review CRITICAL: the derived settle must ride the
    # viewport fan-out's inner env prefix, or the exact crops the dispatcher
    # and judge consume keep the old mid-transition 0.5s capture (the fourth
    # ref-root input to hit the D23 class).
    inner_at = src.index('SECTION_COMPARE_INNER=1')
    prefix = src[inner_at:src.index('bash "$SECTION_COMPARE_INNER_CMD"', inner_at)]
    assert 'WAIT_SCROLL_SETTLE="$WAIT_SCROLL_SETTLE"' in prefix


# ── V-1: _ensure_viewport ─────────────────────────────────────────────
def test_viewport_match_is_noop() -> None:
    calls: list[str] = []
    _ensure_viewport(
        "s", 1440,
        evaluator=lambda _s, _js: "1440",
        setter=lambda _s, _w: calls.append("set"),
        settle=0,
    )
    assert calls == []


def test_viewport_mismatch_resets_once_and_recovers() -> None:
    state = {"w": "1280"}
    def ev(_s: str, _js: str) -> str:
        return state["w"]
    def st(_s: str, _w: int) -> None:
        state["w"] = "1440"
    _ensure_viewport("s", 1440, evaluator=ev, setter=st, settle=0)
    assert state["w"] == "1440"


def test_viewport_persistent_mismatch_aborts() -> None:
    with pytest.raises(SystemExit):
        _ensure_viewport(
            "s", 1440,
            evaluator=lambda _s, _js: "1280",
            setter=lambda _s, _w: None,
            settle=0,
        )


def test_capture_asserts_viewport_before_screenshot() -> None:
    src = (Path(__file__).resolve().parents[1]
           / "ui_clone" / "section_capture.py").read_text(encoding="utf-8")
    shoot = src.index("_ensure_viewport(session, int(expect_w_raw))")
    shot = src.index("_run_screenshot(session, output_path)", shoot)
    assert shoot < shot, "viewport assert must precede the screenshot"
