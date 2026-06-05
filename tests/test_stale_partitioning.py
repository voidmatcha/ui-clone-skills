from __future__ import annotations

from ui_clone.gates.base import CheckResult, partition_failures, stale_refresh_hint


def test_partition_separates_stale_from_real() -> None:
    """Meta item (2): 13 of loop-129's 23 post-implement fails were
    stale-artifact mtime bookkeeping, indistinguishable from real visual
    failures in the rollup — agents wasted cycles debugging phantom bugs.
    Stale fails partition into a dedicated class; passes/warns are ignored."""
    results = [
        CheckResult("section-compare", "fail", "12 FAIL sections"),
        CheckResult("image-fidelity", "fail", "stale artifact", stale=True),
        CheckResult("css-mirror", "fail", "stale artifact", stale=True),
        CheckResult("hidden-children", "pass", "ok"),
        CheckResult("keyframes-diff", "warn", "library injectables missing"),
    ]
    real, stale = partition_failures(results)
    assert [r.label for r in real] == ["section-compare"]
    assert [r.label for r in stale] == ["image-fidelity", "css-mirror"]


def test_stale_hint_lists_names_and_refresh_command() -> None:
    stale = [
        CheckResult("image-fidelity", "fail", "stale", stale=True),
        CheckResult("css-mirror", "fail", "stale", stale=True),
    ]
    hint = stale_refresh_hint(stale)
    assert "image-fidelity" in hint and "css-mirror" in hint
    assert "run-required-checks.sh" in hint, "must point at the refresh path"
    assert "refresh" in hint.lower()


def test_no_hint_when_nothing_stale() -> None:
    assert stale_refresh_hint([]) == ""


def test_stale_still_fails_fast() -> None:
    # the stale class is NOT a pass — never auto-regenerated, never waved through
    results = [CheckResult("image-fidelity", "fail", "stale", stale=True)]
    real, stale = partition_failures(results)
    assert not real and len(stale) == 1
    assert stale[0].status == "fail"
