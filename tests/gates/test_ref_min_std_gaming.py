"""Lowering the blank-ref threshold must not silently disarm the UNMEASURED closure.

Making UNMEASURED block the gate created an incentive that did not exist before:
`SECTION_REF_MIN_STD=0` means `ref["std"] < REF_MIN_STD` is never true
(ui_clone/section_guards.py:122), so no blank-ref guard fires, no row converts to
UNMEASURED, and the whole closure reverts to the pre-fix behaviour — silently.

`SECTION_THRESHOLD` has exactly this shape and is policed (gates/section_compare.py
flags rows labelled ok/minor whose AE/Mpx exceeds the canonical cap). This mirrors
that: crop-guards.json records each section's measured `ref.std` whether or not it
was guarded, so a lowered threshold is detectable after the fact from the telemetry
the run already writes.
"""

import json
from pathlib import Path

from ui_clone.gate import Gate

_CLEAN_RESULT = (
    "| Section | AE | AE/Mpx | Severity | Status |\n"
    "|---------|-----|--------|----------|--------|\n"
    "| hero | 0 | 0 | ok | ✅ |\n"
    "| slideshow | 0 | 0 | ok | ✅ |\n"
    "\n**Result: 2 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 0 UNMEASURED**\n"
)


def _ref(tmp_path: Path, guards: dict | None) -> Path:
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "result.txt").write_text(_CLEAN_RESULT, encoding="utf-8")
    if guards is not None:
        (sections / "crop-guards.json").write_text(json.dumps(guards), encoding="utf-8")
    return ref


def test_lowered_threshold_is_flagged(tmp_path: Path) -> None:
    guards = {
        "schemaVersion": 1,
        "thresholds": {"refMinStd": 0.0},
        "sections": {
            "slideshow": {"reason": "", "policy": "", "ref": {"std": 0.004}},
        },
    }
    results = Gate(_ref(tmp_path, guards)).gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "a lowered SECTION_REF_MIN_STD must not pass silently"
    blob = " ".join(f"{r.message} {r.fix}" for r in failures)
    assert "SECTION_REF_MIN_STD" in blob


def test_lowered_threshold_is_flagged_even_for_non_content_section(tmp_path: Path) -> None:
    guards = {
        "schemaVersion": 1,
        "thresholds": {"refMinStd": 0.0},
        "sections": {
            "decorative-panel": {
                "reason": None,
                "contentBearing": False,
                "ref": {"std": 0.004},
            },
        },
    }
    results = Gate(_ref(tmp_path, guards)).gate_section_compare()
    assert [r for r in results if r.status == "fail"]


def test_unguarded_blank_section_is_flagged_without_the_threshold_field(tmp_path: Path) -> None:
    # Artifact written before `thresholds` existed: the measured std is still the
    # tell — a section under the canonical floor that produced no guard row means
    # the floor in force was lower than canonical.
    guards = {
        "schemaVersion": 1,
        "sections": {
            "slideshow": {"reason": "", "policy": "", "ref": {"std": 0.004}},
        },
    }
    results = Gate(_ref(tmp_path, guards)).gate_section_compare()
    assert [r for r in results if r.status == "fail"]


def test_canonical_threshold_with_guarded_section_is_clean(tmp_path: Path) -> None:
    guards = {
        "schemaVersion": 1,
        "thresholds": {"refMinStd": 0.05},
        "sections": {
            "hero": {"reason": "", "policy": "", "ref": {"std": 0.4}},
        },
    }
    results = Gate(_ref(tmp_path, guards)).gate_section_compare()
    assert not [r for r in results if r.status == "fail"]


def test_canonical_threshold_allows_explicitly_non_content_bearing_low_std_section(
    tmp_path: Path,
) -> None:
    guards = {
        "schemaVersion": 1,
        "thresholds": {"refMinStd": 0.05},
        "sections": {
            "decorative-panel": {
                "reason": None,
                "policy": "pass-only",
                "contentBearing": False,
                "ref": {"std": 0.004},
            },
        },
    }
    results = Gate(_ref(tmp_path, guards)).gate_section_compare()
    assert not [r for r in results if r.status == "fail"]


def test_canonical_threshold_keeps_content_bearing_low_std_fail_closed(tmp_path: Path) -> None:
    guards = {
        "schemaVersion": 1,
        "thresholds": {"refMinStd": 0.05},
        "sections": {
            "hero": {
                "reason": None,
                "contentBearing": True,
                "ref": {"std": 0.004},
            },
        },
    }
    results = Gate(_ref(tmp_path, guards)).gate_section_compare()
    assert [r for r in results if r.status == "fail"]


def test_missing_guards_artifact_does_not_invent_a_failure(tmp_path: Path) -> None:
    results = Gate(_ref(tmp_path, None)).gate_section_compare()
    assert not [r for r in results if r.status == "fail"]
