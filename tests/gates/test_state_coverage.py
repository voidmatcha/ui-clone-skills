"""Tests for ui_clone.gates.state_coverage — the gate that fails when ref
has multi-snapshot capture artifacts (states/splash, states/scroll,
states/hover) but the impl source doesn't reference the corresponding
class hooks / scroll listeners / hover handlers.

Pattern: build a synthetic ref_dir + impl_root, write the artifacts the
gate reads, run `Gate(ref_dir).gate_state_coverage()`, assert pass/fail.
Uses pipeline-state.json + impl-root resolution as the post-implement
gate does, so the gate finds the right impl source tree to grep.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ui_clone import state as _state
from ui_clone.gates import Gate


@pytest.fixture
def ref_dir(tmp_path: Path) -> Path:
    """Create a ref_dir with the bare minimum the gate needs to be invoked.
    Does NOT create states/ artifacts — individual tests add them."""
    d = tmp_path / "ref"
    d.mkdir()
    # Plant a pipeline-state.json so impl_root resolution can be done.
    (d / "pipeline-state.json").write_text(
        json.dumps({"schema_version": 1, "completed_steps": []}),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def impl_root(tmp_path: Path, ref_dir: Path) -> Path:
    """Create an impl_root with src/ and wire pipeline-state.json to it."""
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    # Wire impl_root via .impl-root marker — _find_impl_root in
    # post_implement.py reads this.
    (ref_dir / ".impl-root").write_text(str(impl), encoding="utf-8")
    return impl


def _write_impl_src(impl_root: Path, files: dict[str, str]) -> None:
    """Plant impl/src files for grep — keys are paths relative to src/."""
    src = impl_root / "src"
    for rel, content in files.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _write_splash(ref_dir: Path, trajectory: list[dict], summary: dict) -> None:
    splash = ref_dir / "states" / "splash"
    splash.mkdir(parents=True, exist_ok=True)
    (splash / "trajectory.json").write_text(
        json.dumps(trajectory), encoding="utf-8",
    )
    (splash / "summary.json").write_text(
        json.dumps(summary), encoding="utf-8",
    )


def _write_scroll(ref_dir: Path, trajectory: list[dict], summary: dict) -> None:
    scroll = ref_dir / "states" / "scroll"
    scroll.mkdir(parents=True, exist_ok=True)
    (scroll / "trajectory.json").write_text(
        json.dumps(trajectory), encoding="utf-8",
    )
    (scroll / "summary.json").write_text(
        json.dumps(summary), encoding="utf-8",
    )


def _write_hover(ref_dir: Path, manifest: dict, summary: dict) -> None:
    hover = ref_dir / "states" / "hover"
    hover.mkdir(parents=True, exist_ok=True)
    (hover / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )
    (hover / "summary.json").write_text(
        json.dumps(summary), encoding="utf-8",
    )


def _write_bundle_map(ref_dir: Path, content: dict) -> None:
    """Plant a `bundle-map.json` for the motion-rich detector."""
    (ref_dir / "bundle-map.json").write_text(
        json.dumps(content), encoding="utf-8",
    )


def _write_phase_baseline(ref_dir: Path) -> None:
    """Plant minimum scroll/hover artifacts so motion-rich tests that focus
    on the splash sub-check don't trip the Fix 2 partial-capture detector
    (gate_state_coverage requires all three phase artifacts to exist on
    motion-rich refs before consulting individual sub-checks). Splash is
    planted by the per-test `_write_splash` calls."""
    _write_scroll(
        ref_dir,
        trajectory=[],
        summary={"static": True},
    )
    _write_hover(
        ref_dir,
        manifest={"targets": []},
        summary={"checked": True},
    )


# ── tests ────────────────────────────────────────────────────────────


def test_no_states_dir_emits_single_skip(ref_dir: Path) -> None:
    """Legacy ref dir without states/ and NO bundle-map (or non-motion-rich) —
    gate stays lenient (pass with skip semantics) so existing pipelines
    stay green."""
    results = Gate(ref_dir).gate_state_coverage()
    assert len(results) == 1
    assert results[0].status == "pass"
    assert "no states/" in results[0].message.lower() or "skip" in results[0].message.lower()


def test_splash_transitions_with_class_hooks_pass(
    ref_dir: Path, impl_root: Path,
) -> None:
    """trajectory has is-loading / is-loaded class hooks; impl/src
    references them → pass."""
    _write_splash(
        ref_dir,
        trajectory=[
            {"ts_ms": 0, "hash": 1, "bodyClass": "is-loading", "htmlClass": ""},
            {"ts_ms": 800, "hash": 2, "bodyClass": "is-loaded", "htmlClass": ""},
        ],
        summary={"checked": True, "polls": 2, "reason": "stable-2s"},
    )
    _write_impl_src(impl_root, {
        "App.tsx": (
            "export function App() {\n"
            "  const [loaded, setLoaded] = useState(false);\n"
            "  return <body className={loaded ? 'is-loaded' : 'is-loading'}>\n"
        ),
    })
    results = Gate(ref_dir).gate_state_coverage()
    splash_results = [r for r in results if "splash" in r.label.lower()]
    assert splash_results, f"expected a splash check, got: {[r.label for r in results]}"
    assert any(r.status == "pass" for r in splash_results)


def test_splash_transitions_no_class_hooks_fail(
    ref_dir: Path, impl_root: Path,
) -> None:
    """trajectory has is-loading / is-loaded; impl/src has neither → fail."""
    _write_splash(
        ref_dir,
        trajectory=[
            {"ts_ms": 0, "hash": 1, "bodyClass": "is-loading", "htmlClass": ""},
            {"ts_ms": 800, "hash": 2, "bodyClass": "is-loaded", "htmlClass": ""},
        ],
        summary={"checked": True, "polls": 2, "reason": "stable-2s"},
    )
    _write_impl_src(impl_root, {
        "App.tsx": "export function App() { return <div>Static</div>; }",
    })
    results = Gate(ref_dir).gate_state_coverage()
    splash_fails = [
        r for r in results if r.status == "fail" and "splash" in r.label.lower()
    ]
    assert splash_fails, f"expected splash fail, got: {[(r.label, r.status) for r in results]}"
    # Failure message should mention the missing class names so the agent
    # knows which selectors to add.
    msg = splash_fails[0].message.lower()
    assert "is-loading" in msg or "is-loaded" in msg


def test_splash_no_transitions_no_check(ref_dir: Path, impl_root: Path) -> None:
    """polls=1 (static page, no class transitions detected) → splash
    check is N/A. Don't penalize the impl for not having class hooks the
    ref doesn't need."""
    _write_splash(
        ref_dir,
        trajectory=[{"ts_ms": 0, "hash": 1, "bodyClass": "", "htmlClass": ""}],
        summary={"checked": True, "polls": 1, "reason": "no-change"},
    )
    _write_impl_src(impl_root, {"App.tsx": "export function App() { return null; }"})
    results = Gate(ref_dir).gate_state_coverage()
    splash_fails = [
        r for r in results if r.status == "fail" and "splash" in r.label.lower()
    ]
    assert not splash_fails, (
        f"static splash must not fail; got: {[(r.label, r.status) for r in results]}"
    )


def test_scroll_growth_with_scroll_listener_pass(
    ref_dir: Path, impl_root: Path,
) -> None:
    """Scrollable ref + impl uses IntersectionObserver / ScrollTrigger →
    pass. Recognizes a wide set of scroll-state libraries."""
    _write_scroll(
        ref_dir,
        trajectory=[{"pct": pct} for pct in (0, 25, 50, 75, 100)],
        summary={
            "checked": True, "static": False, "scrollHeight": 8000,
            "viewportHeight": 1080, "scrollHeightGrew": False,
        },
    )
    _write_impl_src(impl_root, {
        "Hero.tsx": (
            "import { useEffect } from 'react';\n"
            "const obs = new IntersectionObserver((entries) => { ... });\n"
        ),
    })
    results = Gate(ref_dir).gate_state_coverage()
    scroll_results = [r for r in results if "scroll" in r.label.lower()]
    assert scroll_results
    assert any(r.status == "pass" for r in scroll_results)


def test_scroll_growth_no_listener_fail(ref_dir: Path, impl_root: Path) -> None:
    """Scrollable ref + impl has NO scroll-state primitive → fail. Many
    sites in the 26-site loop produced flat impls that ignored scroll
    state entirely; this gate catches that."""
    _write_scroll(
        ref_dir,
        trajectory=[{"pct": pct} for pct in (0, 25, 50, 75, 100)],
        summary={
            "checked": True, "static": False, "scrollHeight": 8000,
            "viewportHeight": 1080, "scrollHeightGrew": False,
        },
    )
    _write_impl_src(impl_root, {
        "App.tsx": "export function App() { return <div>Flat content</div>; }",
    })
    results = Gate(ref_dir).gate_state_coverage()
    scroll_fails = [
        r for r in results if r.status == "fail" and "scroll" in r.label.lower()
    ]
    assert scroll_fails


def test_scroll_static_page_no_scroll_check(
    ref_dir: Path, impl_root: Path,
) -> None:
    """static=true (page fits in viewport) → scroll check is N/A. No
    penalty for the impl ignoring scroll state on a non-scrollable page."""
    _write_scroll(
        ref_dir,
        trajectory=[{"pct": 0}],
        summary={
            "checked": True, "static": True, "scrollHeight": 800,
            "viewportHeight": 1080, "scrollHeightGrew": False,
        },
    )
    _write_impl_src(impl_root, {"App.tsx": "export function App() { return null; }"})
    results = Gate(ref_dir).gate_state_coverage()
    scroll_fails = [
        r for r in results if r.status == "fail" and "scroll" in r.label.lower()
    ]
    assert not scroll_fails


def test_hover_entries_with_handlers_pass(ref_dir: Path, impl_root: Path) -> None:
    """manifest has hover entries + impl uses `hover:` Tailwind classes
    OR onMouseEnter / whileHover handlers → pass."""
    _write_hover(
        ref_dir,
        manifest={"entries": [
            {"id": "abc12345", "kind": "css", "file": "elem-abc12345.json",
             "selector": "a.btn", "activation": "a.btn", "changedCount": 0,
             "schemaVersion": 1},
        ]},
        summary={
            "checked": True, "candidatesFound": 1, "candidatesProcessed": 1,
            "candidatesWithCssRule": 1, "candidatesWithJsDiff": 0,
            "candidatesWithAnySignal": 1,
        },
    )
    _write_impl_src(impl_root, {
        "Button.tsx": (
            "export function Button() {\n"
            "  return <button className=\"hover:bg-red-500\">Click</button>;\n"
            "}\n"
        ),
    })
    results = Gate(ref_dir).gate_state_coverage()
    hover_results = [r for r in results if "hover" in r.label.lower()]
    assert hover_results
    assert any(r.status == "pass" for r in hover_results)


def test_hover_entries_no_handlers_fail(ref_dir: Path, impl_root: Path) -> None:
    """manifest has hover entries + impl has zero hover handlers → fail."""
    _write_hover(
        ref_dir,
        manifest={"entries": [
            {"id": "abc12345", "kind": "css+js", "file": "elem-abc12345.json",
             "selector": "a.btn", "activation": "a.btn", "changedCount": 2,
             "schemaVersion": 1},
        ]},
        summary={
            "checked": True, "candidatesFound": 1, "candidatesProcessed": 1,
            "candidatesWithCssRule": 1, "candidatesWithJsDiff": 1,
            "candidatesWithAnySignal": 1,
        },
    )
    _write_impl_src(impl_root, {
        "App.tsx": "export function App() { return <div>No hover</div>; }",
    })
    results = Gate(ref_dir).gate_state_coverage()
    hover_fails = [
        r for r in results if r.status == "fail" and "hover" in r.label.lower()
    ]
    assert hover_fails


def test_hover_empty_manifest_no_check(ref_dir: Path, impl_root: Path) -> None:
    """manifest.entries empty (no :hover rules + no JS hover handlers on
    the ref) → hover check is N/A."""
    _write_hover(
        ref_dir,
        manifest={"entries": []},
        summary={
            "checked": True, "candidatesFound": 0, "candidatesProcessed": 0,
            "candidatesWithCssRule": 0, "candidatesWithJsDiff": 0,
            "candidatesWithAnySignal": 0,
        },
    )
    _write_impl_src(impl_root, {"App.tsx": "export function App() { return null; }"})
    results = Gate(ref_dir).gate_state_coverage()
    hover_fails = [
        r for r in results if r.status == "fail" and "hover" in r.label.lower()
    ]
    assert not hover_fails


def test_partial_states_dir_emits_only_present_checks(
    ref_dir: Path, impl_root: Path,
) -> None:
    """Only states/splash/ exists, no states/scroll/ or states/hover/ →
    only splash check runs. Backward-compat with partial ref dirs."""
    _write_splash(
        ref_dir,
        trajectory=[
            {"ts_ms": 0, "hash": 1, "bodyClass": "is-loading", "htmlClass": ""},
            {"ts_ms": 800, "hash": 2, "bodyClass": "is-loaded", "htmlClass": ""},
        ],
        summary={"checked": True, "polls": 2, "reason": "stable-2s"},
    )
    _write_impl_src(impl_root, {
        "App.tsx": "<body className='is-loading is-loaded'>",
    })
    results = Gate(ref_dir).gate_state_coverage()
    # No scroll/hover checks should appear
    labels = " ".join(r.label.lower() for r in results)
    assert "splash" in labels
    assert "scroll-coverage" not in labels and "scroll coverage" not in labels
    assert "hover-coverage" not in labels and "hover coverage" not in labels


def test_hover_handler_vue_at_mouseenter_pass(
    ref_dir: Path, impl_root: Path,
) -> None:
    """Vue `@mouseenter="..."` template syntax must satisfy the hover
    check (word-boundary regex catches `mouseenter` regardless of @ prefix)."""
    _write_hover(
        ref_dir,
        manifest={"entries": [{
            "id": "v1", "kind": "js", "file": "elem-v1.json",
            "selector": "button", "activation": "button", "changedCount": 1,
            "schemaVersion": 1,
        }]},
        summary={"checked": True, "candidatesFound": 1, "candidatesProcessed": 1,
                 "candidatesWithCssRule": 0, "candidatesWithJsDiff": 1,
                 "candidatesWithAnySignal": 1},
    )
    _write_impl_src(impl_root, {
        "Btn.vue": (
            "<template>\n"
            "  <button @mouseenter=\"onHover\" :class=\"{ active }\">Hover</button>\n"
            "</template>\n"
        ),
    })
    results = Gate(ref_dir).gate_state_coverage()
    hover_results = [r for r in results if "hover" in r.label.lower()]
    assert any(r.status == "pass" for r in hover_results), (
        f"Vue @mouseenter should match hover handler regex; got: "
        f"{[(r.label, r.status) for r in results]}"
    )


def test_hover_handler_svelte_on_mouseenter_pass(
    ref_dir: Path, impl_root: Path,
) -> None:
    """Svelte `on:mouseenter="..."` must satisfy the hover check."""
    _write_hover(
        ref_dir,
        manifest={"entries": [{
            "id": "s1", "kind": "js", "file": "elem-s1.json",
            "selector": "button", "activation": "button", "changedCount": 1,
            "schemaVersion": 1,
        }]},
        summary={"checked": True, "candidatesFound": 1, "candidatesProcessed": 1,
                 "candidatesWithCssRule": 0, "candidatesWithJsDiff": 1,
                 "candidatesWithAnySignal": 1},
    )
    _write_impl_src(impl_root, {
        "Btn.svelte": (
            "<script>let active = false;</script>\n"
            "<button on:mouseenter={() => active = true}>Hover</button>\n"
        ),
    })
    results = Gate(ref_dir).gate_state_coverage()
    hover_results = [r for r in results if "hover" in r.label.lower()]
    assert any(r.status == "pass" for r in hover_results)


def test_scroll_svelte_use_inview_pass(ref_dir: Path, impl_root: Path) -> None:
    """Svelte `use:inView` action must satisfy the scroll-state check."""
    _write_scroll(
        ref_dir,
        trajectory=[{"pct": pct} for pct in (0, 50, 100)],
        summary={"checked": True, "static": False, "scrollHeight": 8000,
                 "viewportHeight": 1080, "scrollHeightGrew": False},
    )
    _write_impl_src(impl_root, {
        "Section.svelte": (
            "<script>\n"
            "  import { inView } from '$lib/actions';\n"
            "  let visible = false;\n"
            "</script>\n"
            "<section use:inView={() => visible = true}>Content</section>\n"
        ),
    })
    results = Gate(ref_dir).gate_state_coverage()
    scroll_results = [r for r in results if "scroll" in r.label.lower()]
    assert any(r.status == "pass" for r in scroll_results)


def test_scroll_vue_useintersectionobserver_pass(
    ref_dir: Path, impl_root: Path,
) -> None:
    """vueuse `useIntersectionObserver` must satisfy scroll-state check."""
    _write_scroll(
        ref_dir,
        trajectory=[{"pct": pct} for pct in (0, 50, 100)],
        summary={"checked": True, "static": False, "scrollHeight": 8000,
                 "viewportHeight": 1080, "scrollHeightGrew": False},
    )
    _write_impl_src(impl_root, {
        "Section.vue": (
            "<script setup>\n"
            "import { useIntersectionObserver } from '@vueuse/core';\n"
            "useIntersectionObserver(target, ([{ isIntersecting }]) => visible.value = isIntersecting);\n"
            "</script>\n"
        ),
    })
    results = Gate(ref_dir).gate_state_coverage()
    scroll_results = [r for r in results if "scroll" in r.label.lower()]
    assert any(r.status == "pass" for r in scroll_results)


def test_state_coverage_in_gate_order_between_pre_generate_and_post_implement(
    ref_dir: Path,
) -> None:
    """Structural test — the gate name must be in GATE_ORDER between
    pre-generate and post-implement (per design)."""
    order = _state.GATE_ORDER
    assert "state-coverage" in order
    assert order.index("state-coverage") == order.index("pre-generate") + 1
    assert order.index("state-coverage") == order.index("post-implement") - 1


# ── codex juanmora review fixes (2026-05-25) ─────────────────────────


def test_motion_rich_no_states_fails(ref_dir: Path) -> None:
    """Codex finding #1: bundle-map declares GSAP/ScrollTrigger but states/
    is absent → fail-closed. Those sites REQUIRE Phase A/B/C capture for
    transition ground truth — silent skip lets juanmora-style gaps through."""
    _write_bundle_map(ref_dir, {
        "libraries": ["gsap", "ScrollTrigger", "SplitText"],
        "transitions": [],
    })
    results = Gate(ref_dir).gate_state_coverage()
    assert any(
        r.status == "fail" and "motion-rich" in r.message.lower()
        for r in results
    ), f"motion-rich + no states/ must fail; got: {[(r.label, r.status, r.message[:60]) for r in results]}"


def test_lenis_in_bundle_map_triggers_motion_rich(ref_dir: Path) -> None:
    """Lenis smooth-scroll alone is enough to mark motion-rich."""
    _write_bundle_map(ref_dir, {"libraries": ["lenis"]})
    results = Gate(ref_dir).gate_state_coverage()
    assert any(r.status == "fail" for r in results), (
        "Lenis alone should trigger motion-rich fail when states/ absent"
    )


def test_webflow_ix_in_bundle_map_triggers_motion_rich(ref_dir: Path) -> None:
    """Webflow IX2 (data-w-id / w-mod-ix) marks motion-rich."""
    _write_bundle_map(ref_dir, {"frameworks": ["webflow"], "attrs": ["data-w-id"]})
    results = Gate(ref_dir).gate_state_coverage()
    assert any(r.status == "fail" for r in results)


def test_non_motion_rich_bundle_keeps_legacy_skip(ref_dir: Path) -> None:
    """bundle-map without motion markers → states/ absent still pass-skip.
    Backward-compat: non-motion sites don't need Phase A/B/C."""
    _write_bundle_map(ref_dir, {"libraries": ["react", "tailwind"]})
    results = Gate(ref_dir).gate_state_coverage()
    assert all(r.status == "pass" for r in results)
    assert any("not motion-rich" in r.message.lower() or "skip" in r.message.lower()
               for r in results)


def test_infinite_scroll_emits_policy_hint(
    ref_dir: Path, impl_root: Path,
) -> None:
    """Fix 3 reframed (codex review 2026-05-27): scroll/summary.json
    `infiniteScroll=true` must produce a non-blocking policy-recommendation
    `warn` so the iteration loop sees the unclonable-shape signal BEFORE
    exhausting 10 post-implement iterations. Drives codex's
    'partial-unclonable signal → policy recommendation, not new gate' path.
    """
    _write_bundle_map(ref_dir, {"libraries": ["gsap"]})
    _write_splash(
        ref_dir,
        trajectory=[
            {"ts_ms": 0, "bodyClass": "is-loading", "htmlClass": ""},
            {"ts_ms": 800, "bodyClass": "is-loaded", "htmlClass": ""},
        ],
        summary={"checked": True, "polls": 2},
    )
    _write_scroll(
        ref_dir,
        trajectory=[],
        summary={"static": False, "infiniteScroll": True},
    )
    _write_hover(
        ref_dir,
        manifest={"targets": []},
        summary={"checked": True},
    )
    _write_impl_src(impl_root, {
        "App.tsx": "export default function App() { return <div />; }",
    })
    results = Gate(ref_dir).gate_state_coverage()
    hints = [
        r for r in results
        if r.status == "warn" and "infiniteScroll" in (r.message or "")
    ]
    assert hints, (
        f"infiniteScroll=true must emit a policy-recommendation warn; got: "
        f"{[(r.label, r.status, (r.message or '')[:80]) for r in results]}"
    )
    # The hint should point at closeoutPolicy switch, not at a code fix.
    assert any("closeoutPolicy" in (r.fix or "") for r in hints), (
        "warn.fix should recommend closeoutPolicy switch"
    )


def test_motion_rich_partial_capture_fails(
    ref_dir: Path, impl_root: Path,
) -> None:
    """Fix 2 (codex review 2026-05-27): motion-rich ref with states/ present
    but one or more phase artifacts MISSING must fail with a partial-
    capture diagnostic — not silently pass via the "no signal" fallback.

    Failure mode this protects against: capture-states.sh runs and writes
    splash artifacts, then capture-scroll.sh crashes (network blip,
    timeout). The sub-checks for the missing phases return None, the
    aggregator returns 'no signal' PASS, and a motion-rich ref ships with
    only partial transition ground truth — fidelity gaps are masked.
    """
    _write_bundle_map(ref_dir, {"libraries": ["gsap"]})
    _write_splash(
        ref_dir,
        trajectory=[
            {"ts_ms": 0, "bodyClass": "is-loading", "htmlClass": ""},
            {"ts_ms": 800, "bodyClass": "is-loaded", "htmlClass": ""},
        ],
        summary={"checked": True, "polls": 2},
    )
    # NOTE: deliberately omit _write_scroll and _write_hover — partial capture.
    results = Gate(ref_dir).gate_state_coverage()
    failures = [r for r in results if r.status == "fail"]
    assert failures, (
        f"motion-rich + missing scroll/hover must fail; got: "
        f"{[(r.label, r.status, r.message[:60]) for r in results]}"
    )
    assert any("missing required phase" in (r.message or "") for r in failures), (
        "fail message should explain the partial-capture cause"
    )


def test_motion_rich_states_exist_no_impl_root_fails(
    ref_dir: Path, tmp_path: Path,
) -> None:
    """Codex finding: motion-rich + states/ exist + impl_root unresolved →
    fail (cannot verify the class hooks reached impl). Non-motion case
    keeps the deferred-pass behavior (handled by separate test)."""
    _write_bundle_map(ref_dir, {"libraries": ["gsap"]})
    _write_splash(
        ref_dir,
        trajectory=[
            {"ts_ms": 0, "bodyClass": "is-loading", "htmlClass": ""},
            {"ts_ms": 800, "bodyClass": "is-loaded", "htmlClass": ""},
        ],
        summary={"checked": True, "polls": 2},
    )
    _write_phase_baseline(ref_dir)
    # NOTE: no .impl-root marker, no UI_CLONE_IMPL_ROOT env, no fallback impl dir.
    # _find_impl_root should return None.
    results = Gate(ref_dir).gate_state_coverage()
    assert any(
        r.status == "fail" and "impl_root" in r.message.lower()
        for r in results
    )


def test_generic_lenis_class_filtered_from_splash_check(
    ref_dir: Path, impl_root: Path,
) -> None:
    """Codex juanmora review: `lenis` class showed in trajectory and impl
    had Lenis library; gate falsely passed. With _GENERIC_NOISE_CLASSES
    filtering, `lenis` alone in trajectory contributes 0 effective class
    hooks → splash check N/A (no hooks to verify)."""
    _write_bundle_map(ref_dir, {"libraries": ["lenis"]})  # motion-rich but lenis-only
    _write_splash(
        ref_dir,
        trajectory=[
            {"ts_ms": 0, "bodyClass": "lenis", "htmlClass": ""},
            {"ts_ms": 1000, "bodyClass": "lenis lenis-scrolling", "htmlClass": ""},
        ],
        summary={"checked": True, "polls": 2},
    )
    _write_impl_src(impl_root, {
        "App.tsx": "import Lenis from 'lenis'; const lenis = new Lenis();",
    })
    results = Gate(ref_dir).gate_state_coverage()
    splash_results = [r for r in results if "splash" in r.label.lower()]
    # With lenis filtered out, classes is empty → splash check returns None
    # (N/A — transitions exist but no observable hooks). No splash result.
    assert not splash_results, (
        f"lenis-only trajectory should produce N/A (no splash result); "
        f"got: {[(r.label, r.status) for r in splash_results]}"
    )


def test_class_only_in_comment_not_matched(
    ref_dir: Path, impl_root: Path,
) -> None:
    """Codex juanmora review: class names mentioned only in `/* … */` or
    `// …` comments should NOT count as legitimate impl references."""
    _write_bundle_map(ref_dir, {"libraries": ["gsap"]})
    _write_splash(
        ref_dir,
        trajectory=[
            {"ts_ms": 0, "bodyClass": "is-loading", "htmlClass": ""},
            {"ts_ms": 800, "bodyClass": "is-loaded", "htmlClass": ""},
        ],
        summary={"checked": True, "polls": 2},
    )
    _write_phase_baseline(ref_dir)
    # Impl mentions `is-loading` only in a block comment + a line comment.
    _write_impl_src(impl_root, {
        "App.tsx": (
            "export function App() {\n"
            "  /* The ref uses is-loading / is-loaded classes. */\n"
            "  // is-loaded marks the post-splash state.\n"
            "  return <div className='loaded'>App</div>;\n"
            "}\n"
        ),
    })
    results = Gate(ref_dir).gate_state_coverage()
    splash_fails = [
        r for r in results
        if r.status == "fail" and "splash" in r.label.lower()
    ]
    assert splash_fails, (
        f"comment-only class mention must NOT pass splash check; "
        f"got: {[(r.label, r.status) for r in results]}"
    )


def test_url_double_slash_not_stripped_as_comment(
    ref_dir: Path, impl_root: Path,
) -> None:
    """Edge case for _strip_js_comments: `https://...` URLs contain `//`
    but must NOT be treated as line comments."""
    _write_bundle_map(ref_dir, {"libraries": ["gsap"]})
    _write_splash(
        ref_dir,
        trajectory=[
            {"ts_ms": 0, "bodyClass": "is-loading", "htmlClass": ""},
            {"ts_ms": 800, "bodyClass": "is-loaded", "htmlClass": ""},
        ],
        summary={"checked": True, "polls": 2},
    )
    _write_phase_baseline(ref_dir)
    # `is-loading` appears AFTER `https://` on the same line — must still match.
    _write_impl_src(impl_root, {
        "App.tsx": (
            "const ref = 'https://example.test'; // see ref\n"
            "const cls = loaded ? 'is-loaded' : 'is-loading';\n"
        ),
    })
    results = Gate(ref_dir).gate_state_coverage()
    splash_passes = [
        r for r in results
        if r.status == "pass" and "splash" in r.label.lower()
    ]
    assert splash_passes, (
        "URL with `//` must not be stripped; active is-loaded/is-loading "
        f"must be detected; got: {[(r.label, r.status) for r in results]}"
    )
