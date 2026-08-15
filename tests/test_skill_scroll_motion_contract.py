import json
import re
from pathlib import Path
from typing import Any, cast

from ui_clone.gates.pre_generate import _MOTION_GROUNDING_ARTIFACTS

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "ui-reverse-engineering"


def _doc(name: str) -> str:
    return (SKILL_DIR / name).read_text(encoding="utf-8")


def _runtime_dump_example() -> dict[str, Any]:
    text = _doc("animation-detection.md")
    match = re.search(
        r"Writes `tmp/ref/<component>/animation-runtime-dump\.json`:\n\n```json\n(.*?)\n```",
        text,
        flags=re.S,
    )
    assert match, "animation-runtime-dump.json example block missing"
    return cast(dict[str, Any], json.loads(match.group(1)))


def test_animation_detection_runtime_dump_records_trustworthy_capture_status() -> None:
    text = _doc("animation-detection.md")
    example = _runtime_dump_example()

    assert example["captureStatus"] == "ok"
    assert example["captureError"] is None

    scroll_audit = example["scrollAudit"]
    assert scroll_audit["engine"] == "native"
    assert isinstance(scroll_audit["maxScroll"], int | float)
    samples = scroll_audit["samples"]
    assert isinstance(samples, list)
    assert len(samples) >= 3
    assert {sample["method"] for sample in samples} == {"native"}
    assert all(set(sample) == {"requested", "observed", "method"} for sample in samples)
    assert all(0 <= sample["requested"] <= 1 for sample in samples)
    assert all(0 <= sample["observed"] <= 1 for sample in samples)
    assert len({sample["observed"] for sample in samples}) >= 3

    for snippet in [
        '"sourceId": "runtime-scroll-filter-001"',
        '"selector": ".hero-media"',
        '"filter": ["filter"]',
        '"varies": ["filter"]',
        '"0.5": {',
    ]:
        assert snippet in text

    row = example["scrollLinkedStyles"][0]
    assert set(row) == {"sourceId", "selector", "filter", "varies", "byScroll", "latched"}
    assert row["sourceId"] == "runtime-scroll-filter-001"
    assert row["selector"] == ".hero-media"
    assert row["filter"] == ["filter"]
    assert row["varies"] == ["filter"]
    assert row["latched"] is False
    assert set(row["byScroll"]) >= {"0", "0.5", "1"}
    assert row["byScroll"]["0"]["filter"] == "blur(12px) brightness(0.8)"

    assert (
        "Only `captureStatus: \"ok\"` with a trustworthy `scrollAudit` is usable"
        in text
    )
    assert "scrollable page without >=3 distinct observed positions" in text
    assert "never interpret as no motion" in text
    assert "Missing runtime families may be `null` only on successful capture" in text
    assert "stable `blur(px) brightness(number)` runtime curve is replayable" in text
    assert "order-changing, extra-function, negative, nonfinite, or mixed compound filters" in text
    assert "Runtime dump rows stay one `sourceId` per observed site" in text
    assert "planner may collapse identical repeated non-latched runtime curves" in text
    assert '`replay: "all-matches"` plus `sourceIds[]`' in text


def test_transition_spec_requires_runtime_rows_or_structured_skips() -> None:
    text = _doc("transition-spec-rules.md")

    assert (
        "Check `animation-runtime-dump.json` `captureStatus` and `scrollAudit` first"
        in text
    )
    assert (
        '"sourceArtifact": "animation-runtime-dump.json"` plus the exact `sourceId`'
        in text
    )
    assert "`skipped[]` entry with the same `sourceArtifact` and `sourceId`" in text
    assert "Selector fallback is legacy-only and must be unambiguous" in text
    assert "A capture error on a motion-rich reference blocks `gate spec`" in text
    assert "Successful `scrollLinkedStyles[]` rows are mapped or skipped" in text
    assert "A planner-collapsed all-match site does not rewrite the spec inventory" in text
    assert "one transition plus structured skips for the remaining original `sourceId` rows is valid" in text
    assert "CSS-grounded evidence, not an inference from capture viewport width" in text
    assert "planner must preserve the media guard by exact runtime `sourceId`" in text
    assert re.search(
        r"runtime replay must restore styles it wrote when that media query becomes\s+inactive",
        text,
    )
    assert "omit that runtime replay instead of falling back" in text


def test_enrichment_motion_wires_are_structured_and_grounded() -> None:
    text = _doc("enrichment.md")

    assert "`tmp/ref/<component>/animation-runtime-dump.json`" in text
    assert "`tmp/ref/<component>/states/scroll/trajectory.json`" in text
    assert '"wires": [' in text
    for snippet in [
        '"kind": "motion"',
        '"library": "framer-motion"',
        '"hooks": ["useScroll", "useTransform"]',
        '"trigger": "scroll"',
        '"replay": "all-matches"',
        '"media": "(min-width: 581px)"',
        '"sourceArtifact": "animation-runtime-dump.json"',
        '"sourceId": "runtime-scroll-filter-001"',
        '"sourceIds": ["runtime-scroll-filter-001", "runtime-scroll-filter-002"]',
    ]:
        assert snippet in text

    assert "Non-motion wire strings remain allowed" in text
    assert 'Do not emit motion-like strings such as `"useScroll"`' in text
    assert "must match the same `scrollLinkedStyles[]` row" in text
    assert "`interactions-detected.json`" not in text
    assert "Never cite `generation-plan.json`, `extracted.json`, or self-authored notes" in text
    assert "runtime dump hash" in text
    assert 'replay: "all-matches"' in text
    assert "mixed curves must stay selector-indexed" in text
    assert "preserve that exact string on the corresponding generation" in text
    assert "by exact runtime `sourceId`" in text
    assert "Do not guess media guards from capture" in text
    assert re.search(r"restore\s+inline styles it previously wrote", text)
    assert "omit its replay instead" in text
    assert re.search(
        r"runtime dump and transition spec wires match the\s+same-row selector/sourceId",
        text,
    )
    assert "other gate-approved artifacts require exact `sourceId` presence" in text

    sentence = re.search(
        r"Motion wires may cite only gate-approved\s+forensic artifacts such as (.*?)\.\s+Never cite",
        text,
        flags=re.S,
    )
    assert sentence, "motion grounding allowlist sentence missing"
    documented = set(re.findall(r"`([^`]+\.json(?:/[^`]*)?)`", sentence.group(1)))
    assert documented
    assert documented <= _MOTION_GROUNDING_ARTIFACTS


def test_skill_pipeline_prompts_grounded_motion_wires_and_runtime_provenance() -> None:
    text = _doc("SKILL.md")

    assert "map each successful `scrollLinkedStyles[]` runtime row" in text
    assert "A capture error is not a skip" in text
    assert "check `animation-runtime-dump.json` `captureStatus` and `scrollAudit`" in text
    assert "rerun or recover the browser session" in text
    assert "structured grounded motion wires" in text
    assert "no prose motion wires" in text
    assert "include `animation-runtime-dump.json` provenance" in text
    assert "Follow each motion wire's `sourceArtifact` and `sourceId`" in text
    assert "Do not implement uncited motion instructions" in text
    assert "stable `blur(px) brightness(number)` filters are replayable" in text
    assert "identical repeated non-latched runtime rows replay across all matched elements" in text
