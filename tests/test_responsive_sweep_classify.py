"""Step 4-C2 responsive sizing classifier (_responsive_classify.py).

The browser sweep is not exercised here (that needs agent-browser); these tests
pin the pure diff/classify logic and — critically — that its output does NOT
trip the pre-generate gate's unfilled-sentinel classifier, so a real sweep
actually satisfies the responsive gate that f8d020d made mandatory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "extract"))

import _responsive_classify as rc  # noqa: E402

from ui_clone.extraction_artifacts import (  # noqa: E402
    sizing_expressions_is_unfilled_sentinel,
)

# ── numeric classification ────────────────────────────────────────────────────

def test_fixed_px() -> None:
    out = rc.classify_numeric({768: 200.0, 1280: 200.0, 1440: 200.4})
    assert out is not None and out["type"] == "fixed-px"
    assert out["value"] == "200px"


def test_vw() -> None:
    out = rc.classify_numeric({768: 640.0, 1280: 1066.7, 1440: 1200.0})
    assert out is not None and out["type"] == "vw"
    assert out["value"] == "83.3vw"


def test_calc_viewport_minus_constant() -> None:
    out = rc.classify_numeric({768: 704.0, 1280: 1216.0, 1440: 1376.0})
    assert out is not None and out["type"] == "calc"
    assert out["value"] == "calc(100vw - 64px)"


def test_linear_slope_intercept() -> None:
    # width = 0.5*vw - 100 → 768→284, 1280→540, 1440→620. pct spread > vw
    # tolerance and offset non-constant, so it lands as linear, not vw/calc.
    out = rc.classify_numeric({768: 284.0, 1280: 540.0, 1440: 620.0})
    assert out is not None and out["type"] == "linear", out
    assert "vw" in out["value"] and "px" in out["value"]


def test_breakpoint_jump() -> None:
    out = rc.classify_numeric({768: 375.0, 1280: 900.0, 1440: 400.0})
    assert out is not None and out["type"] == "breakpoint-jump"
    assert out["value"] is None
    assert out["samples"] == {"768": 375.0, "1280": 900.0, "1440": 400.0}


def test_fewer_than_two_samples_is_none() -> None:
    assert rc.classify_numeric({768: 200.0}) is None
    assert rc.classify_numeric({}) is None


# ── categorical classification ────────────────────────────────────────────────

def test_categorical_switched() -> None:
    out = rc.classify_categorical({768: "block", 1280: "flex", 1440: "flex"})
    assert out is not None and out["type"] == "switched"
    assert out["samples"] == {"768": "block", "1280": "flex", "1440": "flex"}


def test_categorical_constant_is_none() -> None:
    assert rc.classify_categorical({768: "flex", 1280: "flex", 1440: "flex"}) is None


# ── build_expressions ─────────────────────────────────────────────────────────

def _per_viewport() -> dict[int, dict]:
    return {
        768: {".hero": {"width": 640.0, "display": "block"}, "body": {"width": 768.0}},
        1280: {".hero": {"width": 1066.7, "display": "flex"}, "body": {"width": 1280.0}},
        1440: {".hero": {"width": 1200.0, "display": "flex"}, "body": {"width": 1440.0}},
    }


def test_build_expressions_shape_and_types() -> None:
    expr = rc.build_expressions(_per_viewport())
    assert expr[".hero"]["width"]["type"] == "vw"
    assert expr[".hero"]["display"]["type"] == "switched"
    # body width == viewport → calc(100vw - 0px).
    assert expr["body"]["width"]["type"] == "calc"


def test_selector_present_in_one_viewport_is_dropped() -> None:
    per = {
        768: {".only": {"width": 100.0}},
        1280: {".hero": {"width": 640.0}},
        1440: {".hero": {"width": 720.0}},
    }
    expr = rc.build_expressions(per)
    assert ".only" not in expr  # single sample → no expression


# ── the load-bearing guarantee: output is NOT an unfilled sentinel ────────────

def test_output_is_not_unfilled_sentinel(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    (ref / "responsive").mkdir(parents=True)
    expr = rc.build_expressions(_per_viewport())
    rc.write_outputs(ref, expr, rc.VIEWPORTS, [768, 1280, 1440])
    # The real sweep output must satisfy the gate helper (not a sentinel).
    assert sizing_expressions_is_unfilled_sentinel(ref) is False
    data = json.loads((ref / "responsive" / "sizing-expressions.json").read_text())
    assert "sentinel" not in data
    assert data.get("observation") != "single-viewport-sizing-summary"
    assert data.get("expressions") != []
    # Meta sidecar carries provenance out of the bare map.
    meta = json.loads((ref / "responsive" / "sizing-sweep.json").read_text())
    assert meta["method"] == "multi-viewport-computed-sweep"
    assert meta["selectorCount"] == len(expr)


def test_empty_sweep_is_not_sentinel(tmp_path: Path) -> None:
    # Even a genuinely empty (but real) sweep must not read as the finalizer
    # sentinel — an empty dict is not `expressions == []`.
    ref = tmp_path / "ref"
    (ref / "responsive").mkdir(parents=True)
    rc.write_outputs(ref, {}, rc.VIEWPORTS, [])
    assert sizing_expressions_is_unfilled_sentinel(ref) is False


# ── main() end-to-end from per-viewport sample files ──────────────────────────

def test_main_reads_sizing_files_and_writes_expressions(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    resp = ref / "responsive"
    resp.mkdir(parents=True)
    for vp, w in ((768, 640.0), (1280, 1066.7), (1440, 1200.0)):
        (resp / f"sizing-{vp}.json").write_text(json.dumps({
            "viewport": vp, "elements": {".hero": {"width": w}},
        }))
    rc_code = rc.main([str(ref)])
    assert rc_code == 0
    expr = json.loads((resp / "sizing-expressions.json").read_text())
    assert expr[".hero"]["width"]["type"] == "vw"
    assert sizing_expressions_is_unfilled_sentinel(ref) is False
