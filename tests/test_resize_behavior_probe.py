"""Regression tests for the blocking multi-viewport resize probe."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "resize-behavior-probe.sh"


def _responsive_ref(tmp_path: Path, sizing_doc: dict | None = None) -> Path:
    ref = tmp_path / "ref"
    responsive = ref / "responsive"
    responsive.mkdir(parents=True)
    (ref / "detected-breakpoints.json").write_text(
        json.dumps({"breakpoints": ["768px", "1024px"]}),
        encoding="utf-8",
    )
    if sizing_doc is None:
        sizing_doc = {
            ".hero": {
                "width": {
                    "type": "vw",
                    "value": "83.3vw",
                    "samples": {"768": 640, "1280": 1067},
                },
            },
        }
    (responsive / "sizing-expressions.json").write_text(
        json.dumps(sizing_doc),
        encoding="utf-8",
    )
    return ref


def _fake_agent_browser(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state = tmp_path / "viewport-width"
    browser = fake_bin / "agent-browser"
    browser.write_text(
        """#!/usr/bin/env bash
case "$3" in
  open|close|wait)
    exit 0
    ;;
  set)
    if [ "$4" = "viewport" ]; then
      printf '%s' "$5" > "$FAKE_VIEWPORT_STATE"
      exit 0
    fi
    ;;
  eval)
    printf '%s' "$4" > "$FAKE_CAPTURED_EVAL"
    width="$(cat "$FAKE_VIEWPORT_STATE")"
    measured="$((width * 5 / 6))"
    case "$FAKE_MODE" in
      changing)
        printf '{"viewport":{"width":%s,"height":900},"results":[{"selector":".hero","expect":"responsive","present":true,"measuredProperties":{"width":%s},"rect":{"x":0,"y":0,"width":%s,"height":100},"style":{"display":"block"}}]}\\n' "$width" "$measured" "$measured"
        ;;
      fixed)
        printf '{"viewport":{"width":%s,"height":900},"results":[{"selector":".hero","expect":"responsive","present":true,"measuredProperties":{"width":640},"rect":{"x":0,"y":0,"width":640,"height":100},"style":{"display":"block"}}]}\\n' "$width"
        ;;
      fixed-width-changing-y)
        printf '{"viewport":{"width":%s,"height":900},"results":[{"selector":".hero","expect":"responsive","present":true,"measuredProperties":{"width":640},"rect":{"x":0,"y":%s,"width":640,"height":100},"style":{"display":"block"}}]}\\n' "$width" "$width"
        ;;
      absent-at-reference-widths)
        if [ "$width" = "768" ] || [ "$width" = "1280" ]; then
          printf '{"viewport":{"width":%s,"height":900},"results":[{"selector":".hero","expect":"responsive","present":false}]}\\n' "$width"
        else
          printf '{"viewport":{"width":%s,"height":900},"results":[{"selector":".hero","expect":"responsive","present":true,"measuredProperties":{"width":%s},"rect":{"x":0,"y":0,"width":%s,"height":100},"style":{"display":"block"}}]}\\n' "$width" "$measured" "$measured"
        fi
        ;;
      hidden-at-small)
        if [ "$width" = "375" ] || [ "$width" = "768" ]; then
          printf '{"viewport":{"width":%s,"height":900},"results":[{"selector":".hero","expect":"responsive","present":false}]}\\n' "$width"
        else
          printf '{"viewport":{"width":%s,"height":900},"results":[{"selector":".hero","expect":"responsive","present":true,"measuredProperties":{"width":640},"rect":{"x":0,"y":0,"width":640,"height":100},"style":{"display":"block"}}]}\\n' "$width"
        fi
        ;;
      empty)
        printf '{"viewport":{"width":%s,"height":900},"results":[]}\\n' "$width"
        ;;
      invalid)
        printf 'not-json\\n'
        ;;
      tag-drift-changing)
        printf '{"viewport":{"width":%s,"height":900},"results":[{"selector":"section.hero","resolvedSelector":".hero","expect":"responsive","present":true,"measuredProperties":{"width":%s},"rect":{"x":0,"y":0,"width":%s,"height":100},"style":{"display":"block"}}]}\\n' "$width" "$measured" "$measured"
        ;;
      tag-drift-fixed)
        printf '{"viewport":{"width":%s,"height":900},"results":[{"selector":"section.hero","resolvedSelector":".hero","expect":"responsive","present":true,"measuredProperties":{"width":640},"rect":{"x":0,"y":0,"width":640,"height":100},"style":{"display":"block"}}]}\\n' "$width"
        ;;
      semantic-main-changing)
        printf '{"viewport":{"width":%s,"height":900},"results":[{"selector":"main","resolvedSelector":"[role=main]","expect":"responsive","present":true,"measuredProperties":{"width":%s},"rect":{"x":0,"y":0,"width":%s,"height":100},"style":{"display":"block"}}]}\\n' "$width" "$measured" "$measured"
        ;;
    esac
    exit 0
    ;;
esac
exit 1
""",
        encoding="utf-8",
    )
    browser.chmod(0o755)
    return fake_bin, state


def _run_probe(
    tmp_path: Path,
    ref: Path,
    mode: str,
    *,
    impl_source: str | None = None,
) -> subprocess.CompletedProcess[str]:
    fake_bin, state = _fake_agent_browser(tmp_path)
    if impl_source is not None:
        impl = tmp_path / "impl"
        (impl / "src").mkdir(parents=True)
        (impl / "src" / "app.ts").write_text(impl_source, encoding="utf-8")
        (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_MODE": mode,
        "FAKE_VIEWPORT_STATE": str(state),
        "FAKE_CAPTURED_EVAL": str(tmp_path / "eval.js"),
    }
    return subprocess.run(
        ["bash", str(SCRIPT), "http://impl.test", str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def test_non_responsive_ref_skips_without_browser(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "detected-breakpoints.json").write_text(
        json.dumps({"breakpoints": ["768px"]}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), "http://127.0.0.1:9", str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "resize-behavior.json").read_text())
    assert artifact["status"] == "skip"
    assert "not responsive" in artifact["reason"]


def test_missing_breakpoints_skips(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    proc = subprocess.run(
        ["bash", str(SCRIPT), "http://127.0.0.1:9", str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "resize-behavior.json").read_text())
    assert artifact["status"] == "skip"


@pytest.mark.parametrize(
    "sizing_doc",
    [
        {
            ".hero": {
                "width": {
                    "type": "vw",
                    "samples": {"768": 640, "1280": 1067},
                },
            },
        },
        {"expressions": [{"selector": ".hero", "classification": "vw"}]},
    ],
    ids=["bare-selector-map", "legacy-expressions-wrapper"],
)
def test_actual_geometry_change_passes_for_both_sizing_schemas(
    tmp_path: Path,
    sizing_doc: dict,
) -> None:
    ref = _responsive_ref(tmp_path, sizing_doc)
    proc = _run_probe(tmp_path, ref, "changing")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "resize-behavior.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["changedSelectors"] == [".hero"]
    assert artifact["validViewportSamples"] >= 2
    width_contract = artifact["selectors"][0]["responsiveProperties"][0]
    assert width_contract["name"] == "width"
    assert width_contract["referenceSamplesMatch"] is True
    assert width_contract["referenceTrendMatch"] is True


def test_probe_keeps_first_dom_match_across_hidden_breakpoint(
    tmp_path: Path,
) -> None:
    ref = _responsive_ref(tmp_path)
    proc = _run_probe(tmp_path, ref, "changing")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    captured = (tmp_path / "eval.js").read_text(encoding="utf-8")
    assert "const element = elements[0];" in captured
    assert "elements.find(candidate" not in captured


def test_inactive_media_rule_cannot_pass_without_measured_change(
    tmp_path: Path,
) -> None:
    ref = _responsive_ref(tmp_path)
    proc = _run_probe(
        tmp_path,
        ref,
        "fixed",
        impl_source="""
const inactive = window.matchMedia("(min-width: 5000px)").matches;
if (inactive) document.body.classList.add("wide");
""",
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "resize-behavior.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["unchangedResponsiveSelectors"] == [".hero"]


def test_width_contract_cannot_pass_from_unrelated_y_change(
    tmp_path: Path,
) -> None:
    ref = _responsive_ref(tmp_path)
    proc = _run_probe(tmp_path, ref, "fixed-width-changing-y")

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "resize-behavior.json").read_text())
    assert artifact["status"] == "fail"
    width_contract = artifact["selectors"][0]["responsiveProperties"][0]
    assert width_contract["name"] == "width"
    assert width_contract["changed"] is False
    assert artifact["failedResponsiveProperties"] == [".hero:width"]


def test_reference_sample_widths_require_visible_selector_evidence(
    tmp_path: Path,
) -> None:
    ref = _responsive_ref(tmp_path)
    proc = _run_probe(tmp_path, ref, "absent-at-reference-widths")

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "resize-behavior.json").read_text())
    assert artifact["status"] == "fail"
    width_contract = artifact["selectors"][0]["responsiveProperties"][0]
    assert width_contract["changed"] is True
    assert width_contract["referenceComparisons"] == []
    assert width_contract["requiredReferenceSamples"] == 2
    assert width_contract["referenceCoverageMatch"] is False
    assert artifact["failedResponsiveProperties"] == [".hero:width"]


def test_responsive_presence_change_counts_when_reference_samples_match(
    tmp_path: Path,
) -> None:
    ref = _responsive_ref(
        tmp_path,
        {
            ".hero": {
                "width": {
                    "type": "vw",
                    "samples": {"1280": 640, "1440": 640},
                },
            },
        },
    )
    proc = _run_probe(tmp_path, ref, "hidden-at-small")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "resize-behavior.json").read_text())
    assert artifact["status"] == "pass"
    row = artifact["selectors"][0]
    assert row["presenceChanged"] is True
    contract = row["responsiveProperties"][0]
    assert contract["changed"] is False
    assert contract["presenceChanged"] is True
    assert contract["referenceSamplesMatch"] is True
    assert contract["passed"] is True


@pytest.mark.parametrize("mode", ["invalid", "empty"])
def test_eval_failure_or_zero_selector_evidence_fails_closed(
    tmp_path: Path,
    mode: str,
) -> None:
    ref = _responsive_ref(tmp_path)
    proc = _run_probe(tmp_path, ref, mode)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "resize-behavior.json").read_text())
    assert artifact["status"] == "fail"
    assert "responsive" in artifact["reason"] or "evidence" in artifact["reason"]


def test_preference_match_media_does_not_false_fail(tmp_path: Path) -> None:
    ref = _responsive_ref(tmp_path)
    proc = _run_probe(
        tmp_path,
        ref,
        "changing",
        impl_source="""
const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
document.documentElement.dataset.theme = dark ? "dark" : "light";
""",
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "resize-behavior.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["jsOneShotViewportMatchMedia"] is False


@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_returncode"),
    [
        ("tag-drift-changing", "pass", 0),
        ("tag-drift-fixed", "fail", 1),
    ],
)
def test_stable_class_fallback_still_requires_measured_geometry_change(
    tmp_path: Path,
    mode: str,
    expected_status: str,
    expected_returncode: int,
) -> None:
    ref = _responsive_ref(
        tmp_path,
        {
            "section.hero": {
                "width": {
                    "type": "vw",
                    "samples": {"768": 640, "1280": 1067},
                },
            },
        },
    )
    proc = _run_probe(tmp_path, ref, mode)

    assert proc.returncode == expected_returncode, proc.stdout + proc.stderr
    artifact = json.loads((ref / "resize-behavior.json").read_text())
    assert artifact["status"] == expected_status
    assert artifact["selectors"][0]["resolvedSelectors"] == [".hero"]
    eval_source = (tmp_path / "eval.js").read_text()
    assert "stableFallback" in eval_source
    assert "resolvedSelector" in eval_source


def test_main_landmark_falls_back_to_equivalent_role(tmp_path: Path) -> None:
    ref = _responsive_ref(
        tmp_path,
        {
            "main": {
                "width": {
                    "type": "vw",
                    "samples": {"768": 640, "1280": 1067},
                },
            },
        },
    )
    proc = _run_probe(tmp_path, ref, "semantic-main-changing")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "resize-behavior.json").read_text())
    assert artifact["selectors"][0]["resolvedSelectors"] == ["[role=main]"]
    eval_source = (tmp_path / "eval.js").read_text(encoding="utf-8")
    assert "main: '[role=main]'" in eval_source
