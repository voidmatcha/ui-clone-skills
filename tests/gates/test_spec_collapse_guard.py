"""Regression guards for the spec-collapse class (realfood-e2e-1 forensics).

The Phase-2 finalizer auto-mints a gate-shaped 1-entry transition-spec
before the agent drafts Step 5d; gate_spec's only count check was len>0,
and the spec-derived transition-coverage.json fed the very check built to
catch a scroll-less spec. These tests pin the four-part fix: placeholder
tagging + placeholder hard-fail on motion refs + inventory coverage +
selector validity.
"""

from __future__ import annotations

import json
from pathlib import Path

from ui_clone.extraction_artifacts import _is_valid_selector, _iter_css_rules
from ui_clone.gate import Gate


def _mk_ref(tmp_path: Path) -> Path:
    ref = tmp_path / "tmp" / "ref" / "c1"
    ref.mkdir(parents=True)
    (ref / "bundle-map.json").write_text(json.dumps(
        {"chunks": [{"file": "x.js", "libraries": ["framer-motion", "lenis"]}]}))
    (ref / "external-sdks.json").write_text("{}")
    (ref / "verification-plan.json").write_text(json.dumps({
        "tier": "comprehensive",
        "signals": {"hasScrollScrub": True, "hasScrollStateMachine": True, "hasHover": True},
        "requiredChecks": [],
    }))
    return ref


def _placeholder_spec() -> dict:
    return {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "placeholder": True,
        "transitions": [{
            "id": "auto-hover-0", "trigger": "hover",
            "source_chunk": "x.css", "bundle_branch": "settled",
            "target": ".card:hover", "animation": {"type": "css-hover"},
            "reference_frames": "none",
        }],
    }


def test_placeholder_spec_fails_on_motion_rich_ref(tmp_path: Path) -> None:
    ref = _mk_ref(tmp_path)
    (ref / "transition-spec.json").write_text(json.dumps(_placeholder_spec()))
    results = Gate(ref).gate_spec()
    blob = " ".join(r.message for r in results if r.status == "fail")
    assert "auto-minted placeholder" in blob


def test_inventory_coverage_fails_unmapped_signal_classes(tmp_path: Path) -> None:
    ref = _mk_ref(tmp_path)
    spec = _placeholder_spec()
    spec["source"] = "agent"
    spec["placeholder"] = False
    (ref / "transition-spec.json").write_text(json.dumps(spec))
    results = Gate(ref).gate_spec()
    blob = " ".join(r.message for r in results if r.status == "fail")
    assert "scroll-scrub" in blob and "scroll state machine" in blob


def test_io_inventory_failure_names_boolean_css_evidence_sources(tmp_path: Path) -> None:
    ref = _mk_ref(tmp_path)
    plan = json.loads((ref / "verification-plan.json").read_text())
    plan["signals"] = {"hasIOReveal": True}
    (ref / "verification-plan.json").write_text(json.dumps(plan))
    spec = _placeholder_spec()
    spec["source"] = "agent"
    spec["placeholder"] = False
    (ref / "transition-spec.json").write_text(json.dumps(spec))

    results = Gate(ref).gate_spec()
    message = next(
        result.message
        for result in results
        if result.label == "spec-inventory-coverage"
    )

    assert "structure.json" in message
    assert "captured CSS" in message
    assert "dispatch hint" in message


def test_inventory_coverage_passes_when_classes_mapped(tmp_path: Path) -> None:
    ref = _mk_ref(tmp_path)
    spec = _placeholder_spec()
    spec["source"] = "agent"
    spec["placeholder"] = False
    base = spec["transitions"][0]
    spec["transitions"] = [
        base,
        {**base, "id": "t-scrub", "trigger": "scroll-scrub",
         "animation": {"type": "scroll-scrub", "mechanism": "useScroll width 80vw->100vw"}},
        {**base, "id": "t-nav", "trigger": "scroll",
         "animation": {"type": "scroll-state-machine", "mechanism": "nav spring state"}},
    ]
    (ref / "transition-spec.json").write_text(json.dumps(spec))
    results = Gate(ref).gate_spec()
    failures = [r.message for r in results if r.status == "fail"]
    assert not any("does not cover" in m for m in failures), failures
    assert not any("auto-minted placeholder" in m for m in failures), failures


def test_garbage_selector_target_fails(tmp_path: Path) -> None:
    ref = _mk_ref(tmp_path)
    spec = _placeholder_spec()
    spec["source"] = "agent"
    spec["placeholder"] = False
    spec["transitions"][0]["target"] = "transform .2s ease;&"
    (ref / "transition-spec.json").write_text(json.dumps(spec))
    results = Gate(ref).gate_spec()
    blob = " ".join(r.message for r in results if r.status == "fail")
    assert "not CSS" in blob or "selectors" in blob


def test_is_valid_selector_rejects_declaration_fragments() -> None:
    assert not _is_valid_selector("transform .2s ease;&")
    assert not _is_valid_selector("transition: all .2s ease")
    assert not _is_valid_selector("color: red")
    assert not _is_valid_selector("")
    assert _is_valid_selector("a:hover")
    assert _is_valid_selector(".dga_nav__E77In")
    assert _is_valid_selector(":root")
    assert _is_valid_selector("li:nth-child(2) > span")


def test_iter_css_rules_resolves_nesting_without_fragment_selectors() -> None:
    css = """
.card { color: red; transition: all .2s ease;
  &:hover { transform: scale(1.05); }
  .inner { opacity: 0.5; }
}
@media (min-width: 768px) { .card { padding: 2rem; } }
"""
    rules = _iter_css_rules(css)
    selectors = [s for s, _ in rules]
    assert ".card:hover" in selectors
    assert ".card .inner" in selectors
    assert ".card" in selectors  # both top-level and @media-wrapped
    for s in selectors:
        assert _is_valid_selector(s), f"fragment leaked as selector: {s!r}"


def test_regions_placeholder_fails_reference_gate_on_motion_site(tmp_path: Path) -> None:
    ref = _mk_ref(tmp_path)
    static = ref / "static" / "ref"
    static.mkdir(parents=True)
    for i in range(5):
        (static / f"s{i}.png").write_bytes(b"x" * 20)
    trans = ref / "transitions" / "ref"
    trans.mkdir(parents=True)
    (trans / "v.webm").write_bytes(b"x" * 20)
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": True, "detectionRan": False,
        "regions": [{"name": "full-page", "x": 0, "y": 0, "width": 1440, "height": 9000}],
    }))
    results = Gate(ref).gate_reference()
    blob = " ".join(r.message for r in results if r.status == "fail")
    assert "placeholder" in blob and "detection" in blob


def test_metadata_stripped_stubs_still_fail_inventory(tmp_path: Path) -> None:
    """Codex-review pin: editing away placeholder/source metadata while
    keeping the auto-minted stub transitions must NOT satisfy coverage —
    stubs are recognized by shape (auto-id + boilerplate branch /
    unresolved mechanism), not metadata."""
    ref = _mk_ref(tmp_path)
    spec = _placeholder_spec()
    spec["source"] = "agent"          # metadata laundered
    spec["placeholder"] = False
    spec["transitions"] = [{
        "id": "auto-scroll-scrub-1", "trigger": "scroll-scrub",
        "source_chunk": "x.css",
        "bundle_branch": "settled branch observed during capture",
        "target": ".card_hero", "reference_frames": "none",
        "animation": {"type": "scroll-scrub",
                      "mechanism": "unresolved — mine bundle-extraction.json per Step 5d"},
    }]
    (ref / "transition-spec.json").write_text(json.dumps(spec))
    results = Gate(ref).gate_spec()
    blob = " ".join(r.message for r in results if r.status == "fail")
    assert "does not cover" in blob


def test_bare_string_skip_does_not_satisfy_coverage(tmp_path: Path) -> None:
    ref = _mk_ref(tmp_path)
    spec = _placeholder_spec()
    spec["source"] = "agent"
    spec["placeholder"] = False
    spec["skipped"] = ["scroll", "state machine"]  # bare strings — invalid
    (ref / "transition-spec.json").write_text(json.dumps(spec))
    results = Gate(ref).gate_spec()
    blob = " ".join(r.message for r in results if r.status == "fail")
    assert "scroll-scrub" in blob
