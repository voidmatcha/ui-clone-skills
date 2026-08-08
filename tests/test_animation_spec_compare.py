from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast

SCRIPT = Path("skills/visual-debug/scripts/animation-spec-compare")


def _transition(
    ident: str,
    *,
    target: str = ".hero",
    typ: str = "scroll",
    prop: str = "transform",
    easing: str = "linear",
    duration: str = "1000ms",
    start: str = "matrix(1, 0, 0, 1, 0, 0)",
    end: str = "matrix(1, 0, 0, 1, 0, 100)",
) -> dict[str, object]:
    return {
        "id": ident,
        "target": target,
        "animation": {
            "type": typ,
            "property": prop,
            "easing": easing,
            "duration": duration,
            "from": start,
            "to": end,
            "engine": "test",
        },
    }


def _write_json(path: Path, data: Mapping[str, object]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _run_compare(tmp_path: Path, ref: Mapping[str, object], impl: Mapping[str, object]) -> dict[str, object]:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    impl_path = tmp_path / "impl-transition-spec.json"
    out_path = tmp_path / "motion-distance.json"
    _write_json(ref_dir / "transition-spec.json", ref)
    _write_json(impl_path, impl)

    subprocess.run(
        [str(SCRIPT), "http://impl.invalid", str(ref_dir), "--impl-spec", str(impl_path), "--out", str(out_path)],
        check=True,
    )
    return cast(dict[str, object], json.loads(out_path.read_text(encoding="utf-8")))


def _run_compare_via_extractor(
    tmp_path: Path, ref: Mapping[str, object], impl: Mapping[str, object]
) -> dict[str, object]:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    impl_path = tmp_path / "impl-transition-spec.json"
    out_path = tmp_path / "motion-distance.json"
    _write_json(ref_dir / "transition-spec.json", ref)
    _write_json(impl_path, impl)

    subprocess.run(
        [str(SCRIPT), str(impl_path), str(ref_dir), "--out", str(out_path)],
        check=True,
    )
    return cast(dict[str, object], json.loads(out_path.read_text(encoding="utf-8")))


def test_animation_spec_compare_reports_zero_for_noop(tmp_path: Path) -> None:
    spec = {"transitions": [_transition("hero-y"), _transition("fade", prop="opacity", start="0", end="1")]}

    result = _run_compare(tmp_path, spec, spec)

    assert result["distance"] == 0.0
    assert result["p95_cell"] == 0.0
    assert result["n_cells"] == 2
    assert result["ref_dynamic_degree"] == 2
    assert result["impl_dynamic_degree"] == 2


def test_animation_spec_compare_can_use_transition_spec_extract_file_mode(tmp_path: Path) -> None:
    spec = {"transitions": [_transition("hero-y")]}

    result = _run_compare_via_extractor(tmp_path, spec, spec)

    assert result["distance"] == 0.0
    assert result["n_cells"] == 1


def test_animation_spec_compare_uses_pooled_structural_costs_not_mean(tmp_path: Path) -> None:
    ref = {
        "transitions": [
            _transition("hero-y"),
            _transition("fade", prop="opacity", start="0", end="1"),
        ]
    }
    impl = {
        "transitions": [
            _transition(
                "hero-y",
                easing="ease-in",
                duration="500ms",
                end="matrix(1, 0, 0, 1, 0, 50)",
            ),
            _transition("extra", target=".extra", prop="opacity", start="0", end="1"),
        ]
    }

    result = _run_compare(tmp_path, ref, impl)

    costs = [1.4, 1.0, 0.5]  # hero mismatch, missing fade, extra impl animation
    expected_distance = round((sum(cost**3 for cost in costs) ** (1.0 / 3.0)) * 1000.0, 4)
    assert result["distance"] == expected_distance
    assert result["distance"] != round((sum(costs) / len(costs)) * 1000.0, 4)
    assert result["p95_cell"] == 1.4
    assert result["n_cells"] == 3
    assert result["ref_dynamic_degree"] == 2
    assert result["impl_dynamic_degree"] == 2


def _semantic_ref() -> dict[str, object]:
    """Hand-authored ref spec: semantic ids the extractor can never reproduce."""
    return {
        "transitions": [
            {
                "id": "hover-secondary-button",
                "target": ".style_button__tRrhW",
                "animation": {
                    "property": "background-color",
                    "easing": "linear",
                    "duration": "200ms",
                    "from": "0",
                    "to": "1",
                },
            },
            {
                # Bundle-mined scroll entry: no target selector is recoverable.
                "id": "scroll-scrub-hero-resize",
                "animation": {
                    "property": "width",
                    "easing": "linear",
                    "duration": "1000ms",
                    "from": "0",
                    "to": "100",
                },
            },
        ]
    }


def _extractor_impl(hover_duration: str = "200ms", include_scroll: bool = True) -> dict[str, object]:
    """Impl spec exactly as transition-spec-extract emits it (target|property ids)."""
    transitions: list[dict[str, object]] = [
        {
            "id": "button.style_button__tRrhW|backgroundColor",
            "target": "button.style_button__tRrhW",
            "animation": {
                "type": "CSSTransition",
                "property": "backgroundColor",
                "easing": "linear",
                "duration": hover_duration,
                "from": "0",
                "to": "1",
                "engine": "document.getAnimations",
            },
        },
    ]
    if include_scroll:
        transitions.append(
            {
                "id": "#hero|width",
                "target": "#hero",
                "animation": {
                    "type": "Animation",
                    "property": "width",
                    "easing": "linear",
                    "duration": "1000ms",
                    "from": "0",
                    "to": "100",
                    "engine": "document.getAnimations",
                },
            }
        )
    return {"transitions": transitions}


def test_semantic_ref_ids_match_extractor_shaped_impl(tmp_path: Path) -> None:
    result = _run_compare(tmp_path, _semantic_ref(), _extractor_impl())

    assert result["n_matched"] == 2
    assert result["n_cells"] == 2  # two matched pairs; no missing/extra count cells
    assert result["distance"] == 0.0  # structurally identical after normalization


def test_fuzzy_matched_entry_scores_granular_cost(tmp_path: Path) -> None:
    impl = _extractor_impl(hover_duration="100ms", include_scroll=False)

    result = _run_compare(tmp_path, _semantic_ref(), impl)

    # Matched hover: W_DURATION * min(1, |0.2-0.1|/0.2) = 0.3; missing scroll: 1.0.
    expected = round((0.3**3 + 1.0**3) ** (1.0 / 3.0) * 1000.0, 4)
    assert result["n_matched"] == 1
    assert result["distance"] == expected


def test_stripping_impl_animations_raises_distance(tmp_path: Path) -> None:
    full_dir = tmp_path / "full"
    stripped_dir = tmp_path / "stripped"
    full_dir.mkdir()
    stripped_dir.mkdir()

    full = _run_compare(full_dir, _semantic_ref(), _extractor_impl())
    stripped = _run_compare(stripped_dir, _semantic_ref(), {"transitions": []})

    # Pre-matcher regression: every ref entry scored W_MISSING and every impl
    # entry W_EXTRA regardless of content, so deleting impl motion strictly
    # LOWERED the distance. A faithful impl must always beat a stripped one.
    assert cast(float, stripped["distance"]) > cast(float, full["distance"])
    assert stripped["impl_dynamic_degree"] == 0
    assert full["impl_dynamic_degree"] == 2


def test_extractor_file_mode_dedups_duplicate_animation_objects(tmp_path: Path) -> None:
    """12 flip cards emit 12 identical Animation objects; dedup collapses them
    to one entry (count=12) so W_EXTRA no longer scales with element count."""
    extract = Path("skills/visual-debug/scripts/transition-spec-extract")
    duplicates = {
        "transitions": [
            {
                "id": "div.card|transform",
                "target": "div.card",
                "animation": {"type": "CSSTransition", "property": "transform",
                              "easing": "ease", "duration": "600ms"},
            }
        ] * 12
        + [
            {
                "id": "ul.list|opacity",
                "target": "ul.list",
                "animation": {"type": "CSSAnimation", "property": "opacity",
                              "easing": "linear", "duration": "1000ms"},
            }
        ]
    }
    src = tmp_path / "impl-raw.json"
    out = tmp_path / "impl-deduped.json"
    src.write_text(json.dumps(duplicates), encoding="utf-8")

    subprocess.run([str(extract), str(src), "--out", str(out)], check=True)

    result = json.loads(out.read_text(encoding="utf-8"))
    transitions = result["transitions"]
    assert len(transitions) == 2
    counts = {t["target"]: t["count"] for t in transitions}
    assert counts == {"div.card": 12, "ul.list": 1}


def test_dropped_stagger_delay_is_penalized(tmp_path: Path) -> None:
    """EXTRACT-M1: a ref that declares a stagger/delay compared against an impl
    that fires at 0 delay must NOT score identical to a faithful clone — the
    delay ladder is real motion texture."""
    ref_t = _transition("stag")
    ref_t["animation"]["delay"] = "0.1s"  # type: ignore[index]
    impl_t = _transition("stag")  # no delay -> fires immediately
    result = _run_compare(tmp_path, {"transitions": [ref_t]}, {"transitions": [impl_t]})
    assert cast(float, result["distance"]) > 0.0, (
        "an impl that drops the ref's 100ms stagger must be penalized"
    )


def test_matching_delay_scores_zero(tmp_path: Path) -> None:
    ref_t = _transition("stag")
    ref_t["animation"]["delay"] = "0.1s"  # type: ignore[index]
    impl_t = _transition("stag")
    impl_t["animation"]["delay"] = "100ms"  # type: ignore[index]  # same 0.1s, different unit
    result = _run_compare(tmp_path, {"transitions": [ref_t]}, {"transitions": [impl_t]})
    assert cast(float, result["distance"]) == 0.0, "a matching delay must not be penalized"
