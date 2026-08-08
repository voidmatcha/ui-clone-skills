"""Denominator reconciliation (loop-nvti-2 / fable review): every runtime
motion check iterates SPEC entries, so the spec author controls the
denominator of every "N/N" claim. Class-level coverage let 4 scroll entries
immunize 22 uncovered div.page-stack triggers (85% of the census) — the bulk
of the page's scroll choreography shipped dead while every gate passed, and
runtime-spec-coverage.json recorded scrollTriggerCount 26 / specEntryCount 7 /
status pass. The gate must reconcile trigger GROUPS: every group referenced
by a spec entry (target/trigger/id) or a named skipped[] row, else fail with
the uncovered groups by name+count."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1]
          / "skills" / "visual-debug" / "scripts" / "runtime-spec-coverage.sh")


def _run(
    tmp_path: Path,
    dump: dict,
    spec: dict,
    generation_plan: dict | None = None,
    impl_source: dict[str, str] | None = None,
) -> tuple[int, dict]:
    if shutil.which("node") is None:
        pytest.skip("node not available")
    ref = tmp_path / "ref"
    ref.mkdir(exist_ok=True)
    (ref / "animation-runtime-dump.json").write_text(json.dumps(dump), encoding="utf-8")
    (ref / "transition-spec.json").write_text(json.dumps(spec), encoding="utf-8")
    if generation_plan is not None:
        (ref / "generation-plan.json").write_text(json.dumps(generation_plan), encoding="utf-8")
    command = ["bash", str(SCRIPT), str(ref)]
    if impl_source is not None:
        impl = tmp_path / "impl" / "src"
        impl.mkdir(parents=True, exist_ok=True)
        for relative_path, content in impl_source.items():
            target = impl / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        command.append(str(impl))
    proc = subprocess.run(command,
                          capture_output=True, text=True, timeout=60)
    artifact = json.loads((ref / "runtime-spec-coverage.json").read_text(encoding="utf-8"))
    return proc.returncode, artifact


def _st(trigger: str, n: int) -> list[dict]:
    return [{"start": 100 + i, "end": 101 + i, "scrub": None, "pin": False,
             "trigger": trigger, "tween": None} for i in range(n)]


BASE_SPEC = {
    "transitions": [
        {"id": "hero-scrub", "trigger": "scroll-scrub", "target": ".tech-hero .item",
         "animation": {"property": "transform"}},
    ],
    "skipped": [],
}


def test_uncovered_trigger_group_fails_with_named_group() -> None:
    # the nvti shape: spec has A scroll entry (class-level pass) but the
    # page-stack group is untouched -> must FAIL naming the group.
    dump = {"scrollTrigger": _st("div.tech-hero.ready", 2) + _st("div.page-stack", 3)}
    rc, artifact = _run_tmp(dump, BASE_SPEC)
    assert artifact["status"] == "fail", artifact
    assert rc == 1
    joined = " ".join(artifact["missing"])
    assert "div.page-stack" in joined and "x3" in joined.replace(" x", " x"), joined


def test_group_covered_by_entry_target_passes() -> None:
    dump = {"scrollTrigger": _st("div.tech-hero.ready", 2)}
    rc, artifact = _run_tmp(dump, BASE_SPEC)
    assert artifact["status"] == "pass", artifact
    assert rc == 0


def test_group_covered_by_named_skip_passes() -> None:
    dump = {"scrollTrigger": _st("div.page-stack", 5)}
    spec = {
        "transitions": BASE_SPEC["transitions"],
        "skipped": [{"sourceId": "page-stack-flips",
                     "selector": "div.page-stack",
                     "reason": "panel class-flip choreography covered by the state-machine entry"}],
    }
    rc, artifact = _run_tmp(dump, spec)
    assert artifact["status"] == "pass", artifact


def test_scroll_linked_styles_selector_uncovered_fails() -> None:
    dump = {"scrollTrigger": [],
            "scrollLinkedStyles": [{"selector": "div.mystery-track",
                                    "varies": ["transform"]}]}
    rc, artifact = _run_tmp(dump, BASE_SPEC)
    assert artifact["status"] == "fail", artifact
    assert any("mystery-track" in m for m in artifact["missing"])


def test_legacy_dump_without_trigger_fields_keeps_class_level_behavior() -> None:
    # old dumps: scrollTrigger rows without a trigger selector — cannot
    # reconcile groups; class-level check remains the only signal.
    dump = {"scrollTrigger": [{"start": 1, "end": 2}] * 3}
    rc, artifact = _run_tmp(dump, BASE_SPEC)
    assert artifact["status"] == "pass", artifact


def test_note_mentions_do_not_count_as_coverage() -> None:
    # Anti-loosening: a selector appearing only in an entry's prose
    # (animation description/notes) is not a plan for that group.
    dump = {"scrollTrigger": _st("div.page-stack", 4)}
    spec = {
        "transitions": [
            {"id": "hero-scrub", "trigger": "scroll-scrub",
             "target": ".tech-hero .item",
             "animation": {"property": "transform",
                           "note": "corrected: .page-stack .item-outer was a drafting error"}},
        ],
        "skipped": [],
    }
    rc, artifact = _run_tmp(dump, spec)
    assert artifact["status"] == "fail", artifact


# module-level indirection so each test reads clearly
import tempfile  # noqa: E402


def _run_tmp(
    dump: dict,
    spec: dict,
    generation_plan: dict | None = None,
    impl_source: dict[str, str] | None = None,
) -> tuple[int, dict]:
    with tempfile.TemporaryDirectory() as td:
        return _run(Path(td), dump, spec, generation_plan, impl_source)


def test_group_covered_by_entry_selector_field_passes() -> None:
    # codex P2: some spec producers emit `selector` instead of `target` —
    # both are plan fields for reconciliation.
    dump = {"scrollTrigger": _st("div.page-stack", 4)}
    spec = {
        "transitions": [
            {"id": "stack-flips", "trigger": "scroll state machine",
             "selector": "div.page-stack",
             "animation": {"property": "class-flip"}},
        ],
        "skipped": [],
    }
    rc, artifact = _run_tmp(dump, spec)
    assert artifact["status"] == "pass", artifact


def test_swiper_parent_plan_covers_runtime_slide_state_classes() -> None:
    dump = {
        "scrollTrigger": [],
        "scrollLinkedStyles": [
            {"selector": "div.swiper-slide.swiper-slide-visible.swiper-slide-fully-visible",
             "varies": ["opacity"]},
            {"selector": "div.swiper-slide.swiper-slide-next",
             "varies": ["opacity"]},
        ],
    }
    spec = {
        "transitions": [
            {"id": "hero-swiper", "trigger": "swiper-next",
             "target": '.swiper[data-ui-clone-swiper="0"]',
             "animation": {"property": "transform"}},
        ],
        "skipped": [],
    }
    rc, artifact = _run_tmp(dump, spec)
    assert rc == 0
    assert artifact["status"] == "pass", artifact


def test_swiper_parent_plan_does_not_cover_unrelated_child_group() -> None:
    dump = {
        "scrollTrigger": [],
        "scrollLinkedStyles": [
            {"selector": "div.carousel-slide.is-next", "varies": ["opacity"]},
        ],
    }
    spec = {
        "transitions": [
            {"id": "hero-swiper", "trigger": "swiper-next",
             "target": '.swiper[data-ui-clone-swiper="0"]'},
        ],
        "skipped": [],
    }
    rc, artifact = _run_tmp(dump, spec)
    assert rc == 1
    assert artifact["status"] == "fail", artifact
    assert any("carousel-slide" in item for item in artifact["missing"])


@pytest.mark.parametrize(
    "impl_source",
    [
        {"View.tsx": '<div data-swiper-progress="span.bar" />'},
        {"SwiperActivator.ts": 'const selector = el.dataset.swiperProgress;'},
    ],
)
def test_swiper_progress_group_requires_attribute_and_dataset_consumer(
    impl_source: dict[str, str],
) -> None:
    dump = {
        "scrollTrigger": [],
        "scrollLinkedStyles": [{"selector": "span.bar", "varies": ["width"]}],
    }
    spec = {
        "transitions": [
            {"id": "hero-swiper", "trigger": "swiper-next",
             "target": '.swiper[data-ui-clone-swiper="0"]'},
        ],
        "skipped": [],
    }
    rc, artifact = _run_tmp(dump, spec, impl_source=impl_source)
    assert rc == 1
    assert artifact["status"] == "fail", artifact
    assert any("span.bar" in item for item in artifact["missing"])


def test_swiper_progress_group_covered_by_proven_implementation_hook() -> None:
    dump = {
        "scrollTrigger": [],
        "scrollLinkedStyles": [{"selector": "span.bar", "varies": ["width"]}],
    }
    spec = {
        "transitions": [
            {"id": "hero-swiper", "trigger": "swiper-next",
             "target": '.swiper[data-ui-clone-swiper="0"]'},
        ],
        "skipped": [],
    }
    impl_source = {
        "View.tsx": '<div data-swiper-progress="span.bar" />',
        "SwiperActivator.ts": "const selector = el.dataset.swiperProgress;",
    }
    rc, artifact = _run_tmp(dump, spec, impl_source=impl_source)
    assert rc == 0
    assert artifact["status"] == "pass", artifact


def test_scroll_scrub_sites_cover_runtime_descendant_groups() -> None:
    dump = {
        "scrollTrigger": [],
        "scrollLinkedStyles": [
            {"selector": ".style_scrollcontainer__Vup4r", "varies": ["opacity"]},
            {"selector": "svg", "varies": ["opacity"]},
            {"selector": "g#even", "varies": ["transform"]},
            {"selector": "g#odd", "varies": ["transform"]},
            {"selector": "div.style_imgWrapper__AFuB_", "varies": ["width", "borderRadius"]},
        ],
    }
    spec = {
        "transitions": [
            {"id": "scroll-featured-grid", "trigger": "scroll",
             "target": ".style_scrollcontainer__Vup4r",
             "animation": {"type": "framer-motion-scroll-scrub"}},
        ],
        "skipped": [],
    }
    generation_plan = {
        "scrollScrub": {
            "sites": [
                {"selector": ".style_scrollcontainer__Vup4r",
                 "target": ".style_scrollcontainer__Vup4r",
                 "scope": ".style_scrollcontainer__Vup4r",
                 "source": "animation-runtime-dump.json:scrollLinkedStyles"},
                {"selector": "svg", "target": ".style_scrollcontainer__Vup4r",
                 "scope": ".style_scrollcontainer__Vup4r",
                 "source": "animation-runtime-dump.json:scrollLinkedStyles"},
                {"selector": "g#even", "target": ".style_scrollcontainer__Vup4r",
                 "scope": ".style_scrollcontainer__Vup4r",
                 "source": "animation-runtime-dump.json:scrollLinkedStyles"},
                {"selector": "g#odd", "target": ".style_scrollcontainer__Vup4r",
                 "scope": ".style_scrollcontainer__Vup4r",
                 "source": "animation-runtime-dump.json:scrollLinkedStyles"},
                {"selector": "div.style_imgWrapper__AFuB_",
                 "target": ".style_scrollcontainer__Vup4r",
                 "scope": ".style_scrollcontainer__Vup4r",
                 "source": "animation-runtime-dump.json:scrollLinkedStyles"},
            ],
        },
    }
    rc, artifact = _run_tmp(dump, spec, generation_plan)
    assert rc == 0
    assert artifact["status"] == "pass", artifact


def test_scroll_scrub_parent_plan_does_not_cover_unmapped_runtime_group() -> None:
    dump = {
        "scrollTrigger": [],
        "scrollLinkedStyles": [
            {"selector": "div.unmapped-card", "varies": ["opacity"]},
        ],
    }
    spec = {
        "transitions": [
            {"id": "scroll-featured-grid", "trigger": "scroll",
             "target": ".style_scrollcontainer__Vup4r",
             "animation": {"type": "framer-motion-scroll-scrub"}},
        ],
        "skipped": [],
    }
    generation_plan = {
        "scrollScrub": {
            "sites": [
                {"selector": "svg", "target": ".style_scrollcontainer__Vup4r",
                 "scope": ".style_scrollcontainer__Vup4r",
                 "source": "animation-runtime-dump.json:scrollLinkedStyles"},
            ],
        },
    }
    rc, artifact = _run_tmp(dump, spec, generation_plan)
    assert rc == 1
    assert artifact["status"] == "fail", artifact
    assert any("div.unmapped-card" in item for item in artifact["missing"])
