import time
from pathlib import Path

import pytest

from ui_clone.dag import (
    GENERATION_PLAN_SOURCES,
    StalenessIssue,
    _assert_no_cycles,
    check_staleness,
    stale_set,
)


def test_stale_set_direct_dependency() -> None:
    """structure.json change includes directly dependent extracted.json"""
    result = stale_set("structure.json")
    assert "extracted.json" in result


def test_stale_set_transitive() -> None:
    """structure.json → section-map.json → component-map.json → extracted.json transitive chain"""
    result = stale_set("structure.json")
    assert "section-map.json" in result
    assert "component-map.json" in result
    assert "extracted.json" in result


def test_stale_set_topological_order() -> None:
    """section-map.json must appear before component-map.json (prerequisite dependency)"""
    result = stale_set("structure.json")
    if "section-map.json" in result and "component-map.json" in result:
        assert result.index("section-map.json") < result.index("component-map.json")


def test_interactions_detected_invalidates_hover_css_rules() -> None:
    """interactions-detected.json change must mark hover-css-rules.json stale.

    Regression test for: missing edge interactions-detected → hover-css-rules in DEPS.
    """
    result = stale_set("interactions-detected.json")
    assert "hover-css-rules.json" in result
    # hover-css-rules must appear before extracted.json (it feeds into it)
    assert result.index("hover-css-rules.json") < result.index("extracted.json")


def test_stale_set_no_dependents() -> None:
    """Returns empty list when there are no dependents"""
    result = stale_set("generation-plan.json")
    assert result == []


@pytest.mark.parametrize(
    "source",
    tuple(path for path in GENERATION_PLAN_SOURCES if "*" not in path),
)
def test_every_generation_plan_source_invalidates_generation_plan(
    source: str,
) -> None:
    assert "generation-plan.json" in stale_set(source)


def test_media_inventories_invalidate_canonical_downstream_artifacts() -> None:
    assert "required-media.json" in GENERATION_PLAN_SOURCES
    runtime_dependents = stale_set("runtime-media.json")
    assert "required-media.json" in runtime_dependents
    assert "generation-plan.json" in runtime_dependents


def test_check_staleness_detects_stale(tmp_path: Path) -> None:
    """Returns StalenessIssue when parent is newer than child"""
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    import os

    base_time = time.time() - 2.0

    child = ref_dir / "extracted.json"
    child.write_text("{}")
    os.utime(child, (base_time, base_time))

    parent = ref_dir / "structure.json"
    parent.write_text("{}")
    os.utime(parent, (base_time + 1.0, base_time + 1.0))

    issues = check_staleness(ref_dir)
    stale_names = [i.stale for i in issues]
    assert "extracted.json" in stale_names


def test_check_staleness_clean(tmp_path: Path) -> None:
    """No issue when child is newer than parent"""
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    import os

    base_time = time.time() - 2.0

    parent = ref_dir / "structure.json"
    parent.write_text("{}")
    os.utime(parent, (base_time, base_time))

    child = ref_dir / "extracted.json"
    child.write_text("{}")
    os.utime(child, (base_time + 1.0, base_time + 1.0))

    issues = check_staleness(ref_dir)
    stale_names = [i.stale for i in issues]
    assert "extracted.json" not in stale_names


def test_check_staleness_missing_files(tmp_path: Path) -> None:
    """No issue when files are missing (check_file handles that separately)"""
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    issues = check_staleness(ref_dir)
    assert issues == []


def test_staleness_issue_has_fix() -> None:
    """StalenessIssue.fix field must not be empty"""
    issue = StalenessIssue(
        stale="extracted.json",
        because_of="structure.json",
        severity="block",
        fix="Re-run Step 6b",
    )
    assert issue.fix != ""


def test_staleness_severity_extracted_is_block(tmp_path: Path) -> None:
    """extracted.json staleness is block-level severity"""
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    import os

    base_time = time.time() - 2.0

    child = ref_dir / "extracted.json"
    child.write_text("{}")
    os.utime(child, (base_time, base_time))

    parent = ref_dir / "structure.json"
    parent.write_text("{}")
    os.utime(parent, (base_time + 1.0, base_time + 1.0))

    issues = check_staleness(ref_dir)
    extracted_issues = [i for i in issues if i.stale == "extracted.json"]
    assert any(i.severity == "block" for i in extracted_issues)


def test_stale_set_cycle_guard() -> None:
    """stale_set returns all affected nodes in an acyclic DAG (cycle guard verification)."""
    # Our DEPS graph is acyclic; stale_set("structure.json") should return all
    # transitively affected nodes without infinite loop.
    result = stale_set("structure.json")
    # Must terminate and return a list (not hang)
    assert isinstance(result, list)
    # All expected nodes present
    assert "extracted.json" in result
    assert "section-map.json" in result
    assert "component-map.json" in result


def test_stale_set_bundle_map_transitively_marks_extracted() -> None:
    """bundle-map.json → transition-spec.json → extracted.json transitive chain."""
    result = stale_set("bundle-map.json")
    assert "transition-spec.json" in result
    assert "extracted.json" in result


def test_check_staleness_bundle_map_chain(tmp_path: Path) -> None:
    """check_staleness detects extracted.json stale when bundle-map.json is newer via transition-spec."""
    import os

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    base_time = time.time() - 4.0

    # extracted.json is oldest
    extracted = ref_dir / "extracted.json"
    extracted.write_text("{}")
    os.utime(extracted, (base_time, base_time))

    # transition-spec.json is between
    spec = ref_dir / "transition-spec.json"
    spec.write_text("{}")
    os.utime(spec, (base_time + 1.0, base_time + 1.0))

    # bundle-map.json is newest — makes transition-spec stale, which in turn makes extracted stale
    bundle_map = ref_dir / "bundle-map.json"
    bundle_map.write_text("{}")
    os.utime(bundle_map, (base_time + 2.0, base_time + 2.0))

    issues = check_staleness(ref_dir)
    stale_names = [i.stale for i in issues]
    assert "transition-spec.json" in stale_names, "transition-spec.json must be stale (direct edge)"
    assert "extracted.json" in stale_names, (
        "extracted.json must be stale (transition-spec.json edge)"
    )


def test_stale_set_animation_init_and_svg_text_in_chain() -> None:
    """animation-init-styles.json and svg-text-elements.json both mark extracted.json stale."""
    for parent in ("animation-init-styles.json", "svg-text-elements.json"):
        result = stale_set(parent)
        assert "extracted.json" in result, f"{parent} must transitively mark extracted.json stale"


def test_assert_no_cycles_passes_on_current_deps() -> None:
    """DEPS graph must be acyclic — _assert_no_cycles() must not raise."""
    _assert_no_cycles()  # should not raise


def test_assert_no_cycles_detects_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """_assert_no_cycles() raises RuntimeError when a cycle is introduced."""
    import ui_clone.dag as dag_module

    cyclic_deps = {
        "a.json": ["b.json"],
        "b.json": ["c.json"],
        "c.json": ["a.json"],  # cycle: a → b → c → a
    }
    monkeypatch.setattr(dag_module, "DEPS", cyclic_deps)
    # Reimport _assert_no_cycles so it picks up patched DEPS
    # Call directly with patched module state
    import pytest

    with pytest.raises(RuntimeError, match="Cycle detected"):
        dag_module._assert_no_cycles()


def test_check_staleness_transitive_propagation(tmp_path: Path) -> None:
    """check_staleness propagates transitively: bundle-map newer than transition-spec
    must also flag extracted.json as stale even when extracted.json is newer than transition-spec.

    Before this fix, only direct mtime comparisons were checked — a stale parent
    wouldn't cascade to grandchildren unless the parent was also mtime-newer.
    """
    import os

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    base_time = time.time() - 4.0

    # extracted.json is the newest by mtime — but should still be stale
    # because its parent transition-spec.json is stale
    extracted = ref_dir / "extracted.json"
    extracted.write_text("{}")
    os.utime(extracted, (base_time + 3.0, base_time + 3.0))

    # transition-spec.json is older than extracted but newer than bundle-map... wait, no.
    # We want: bundle-map (newest) > transition-spec (middle) → transition-spec is stale
    # But extracted is newer than transition-spec → direct check says extracted is fine
    # The transitive fix should still flag extracted because transition-spec is stale
    spec = ref_dir / "transition-spec.json"
    spec.write_text("{}")
    os.utime(spec, (base_time, base_time))  # oldest

    bundle_map = ref_dir / "bundle-map.json"
    bundle_map.write_text("{}")
    os.utime(bundle_map, (base_time + 1.0, base_time + 1.0))  # newer than spec

    # extracted (base+3) > spec (base+0) — direct check would say "not stale"
    # But spec IS stale (bundle-map > spec), so extracted should be transitively stale
    issues = check_staleness(ref_dir)
    stale_names = [i.stale for i in issues]
    assert "transition-spec.json" in stale_names, "transition-spec must be directly stale"
    assert "extracted.json" in stale_names, (
        "extracted.json must be transitively stale because transition-spec.json is stale"
    )


def test_conftest_fixture_covers_all_deps_artifacts(ref_dir_with_artifacts: Path) -> None:
    """Ensure ref_dir_with_artifacts fixture creates files for every artifact in DEPS.

    Prevents silent test breakage when new artifacts are added to the DAG.
    """
    from ui_clone.dag import DEPS

    all_artifacts: set[str] = set(DEPS.keys())
    for targets in DEPS.values():
        all_artifacts.update(targets)

    missing = [name for name in all_artifacts if not (ref_dir_with_artifacts / name).exists()]
    assert not missing, (
        f"conftest ref_dir_with_artifacts fixture is missing DEPS artifacts: {missing}"
    )
