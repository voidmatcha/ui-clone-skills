"""Tests for the sound, deterministic regions producer in
scripts/extract/_capture_artifacts.py.

Fix 5 redesign: regions.json is derived as a pure-JSON projection of
transition-spec.json's real transitions (+ section-map.json geometry), NOT by
pixel-diffing non-overlapping scroll slices (the reverted, fabricating
approach). Honest-only: a static page (no real transitions) keeps the
placeholder; a re-run never downgrades a real regions.json.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_module() -> ModuleType:
    key = "_capture_artifacts_test_module"
    if key in sys.modules:
        return sys.modules[key]
    path = _project_root() / "scripts" / "extract" / "_capture_artifacts.py"
    spec = importlib.util.spec_from_file_location(key, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


# Ground-truth shapes mirrored from a real realfood ref dir.
def _spec(transitions: list[dict], skipped: list | None = None) -> dict:
    return {
        "schemaVersion": 1,
        "source": "step-5d-transition-spec-rules",
        "transitions": transitions,
        "skipped": skipped or [],
    }


def _section_map() -> dict:
    return {
        "totalCount": 3,
        "sections": [
            {"index": 0, "top": -900, "height": 900, "position": "fixed",
             "className": "intro-animation-module__0093MG__overlay", "id": None},
            {"index": 1, "top": 42, "height": 638, "position": "relative",
             "className": "dga-module__LrmiHG__hero", "id": None},
            {"index": 2, "top": 1345, "height": 1002, "position": "relative",
             "className": "dga-module__LrmiHG__stats", "id": "problem"},
        ],
    }


# ── derive_regions_json ──


def test_derive_none_for_no_real_transitions() -> None:
    """Static page: empty transitions → None (caller keeps placeholder)."""
    mod = _load_module()
    assert mod.derive_regions_json(_spec([]), _section_map()) is None


def test_derive_none_for_none_spec() -> None:
    """Missing transition-spec → None, never crash."""
    mod = _load_module()
    assert mod.derive_regions_json(None, None) is None
    assert mod.derive_regions_json({}, None) is None


def test_derive_ignores_skipped_entries() -> None:
    """skipped[] entries are non-real and must never become regions."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([], skipped=[{"id": "x", "trigger": "hover", "selector": ".btn"}]),
        _section_map(),
    )
    assert out is None


def test_derive_projects_real_transitions() -> None:
    """Real hover+scroll transitions → per-entry regions with triggerType,
    selector, and the transition's own reference artifacts."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([
            {"id": "hover-dl-0", "trigger": "hover", "selector": ".nav__download_button",
             "reference_frames": ["static/ref/section-0.png"]},
            {"id": "scroll-nav-0", "trigger": "scroll", "target": ".nav__nav",
             "reference_frames": ["static/ref/section-1.png"]},
        ]),
        _section_map(),
    )
    assert out is not None
    assert out["placeholder"] is False
    assert out["detectionRan"] is True
    regions = out["regions"]
    assert len(regions) == 2
    by_name = {r["name"]: r for r in regions}
    assert by_name["hover-dl-0"]["triggerType"] == "hover"
    assert by_name["hover-dl-0"]["selector"] == ".nav__download_button"
    # Spec frames are provenance, NOT a per-state capture manifest — they live
    # under referenceFrames, and the region is marked dispatch-only so the
    # capture-artifact-inventory check does not read them as capture proof.
    assert by_name["hover-dl-0"]["referenceFrames"] == ["static/ref/section-0.png"]
    assert "artifacts" not in by_name["hover-dl-0"]
    assert by_name["hover-dl-0"]["dispatchOnly"] is True
    assert by_name["scroll-nav-0"]["triggerType"] == "scroll"
    assert by_name["scroll-nav-0"]["selector"] == ".nav__nav"


def test_derive_skips_unknown_trigger() -> None:
    """An unrecognized free-text trigger must NOT mint a placeholder:false
    region — the old `return t.split()[0]` fallback fabricated detection for any
    junk trigger (multi-agent review M3)."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([{"id": "junk", "trigger": "wiggle-o-matic 3000", "selector": ".x"}]),
        None,
    )
    assert out is None  # only transition had an unknown trigger -> placeholder


def test_derive_unknown_trigger_dropped_among_real() -> None:
    """Unknown-trigger transitions are dropped; real ones still project."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([
            {"id": "junk", "trigger": "sparkle", "selector": ".a"},
            {"id": "real", "trigger": "hover", "selector": ".b"},
        ]),
        None,
    )
    assert out is not None
    assert [r["name"] for r in out["regions"]] == ["real"]


def test_derive_keeps_canonical_intersection_trigger() -> None:
    """Whitelist must NOT over-restrict: genuine IO-reveal/intersection triggers
    are real motion and must still project."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([{"id": "io", "trigger": "intersection reveal", "selector": ".sec"}]),
        None,
    )
    assert out is not None
    assert out["regions"][0]["triggerType"] == "intersection"


def test_derive_normalizes_bare_click_trigger() -> None:
    """A bare 'click' trigger maps to a click-* triggerType so the
    click-state-compare consumer (startswith 'click-') resolves it."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([{"id": "c", "trigger": "click", "selector": ".tab"}]), None
    )
    assert out is not None
    assert out["regions"][0]["triggerType"].startswith("click-")


def test_derive_normalizes_verbose_scroll_trigger() -> None:
    """Real specs (navercorp-esg) carry descriptive trigger sentences like
    'scroll: GSAP ScrollTrigger scrub on .page-hero'. The region triggerType
    must be the canonical leading class ('scroll'), not the whole sentence —
    benchmark-harvest scores on the count of distinct triggerType values."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([
            {"id": "hero", "trigger": "scroll: GSAP ScrollTrigger scrub on .page-hero",
             "target": ".parallax-items .item-outer"},
            {"id": "hdr", "trigger": "scroll threshold (state class toggle on .header)",
             "target": ".header"},
        ]),
        None,
    )
    assert out is not None
    assert {r["triggerType"] for r in out["regions"]} == {"scroll"}


def test_derive_classifies_page_load_not_scroll() -> None:
    """'page load (first paint, scroll-guard)' is load-triggered — the
    'scroll-guard' substring must NOT misclassify it as scroll."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([
            {"id": "guard", "trigger": "page load (first paint, scroll-guard)",
             "target": ".hero"},
            {"id": "auto", "trigger": "page load (autoplay)", "target": ".video"},
        ]),
        None,
    )
    assert out is not None
    assert {r["triggerType"] for r in out["regions"]} == {"load"}


def test_derive_attaches_inbounds_geometry_when_selector_resolves() -> None:
    """A selector matching a section className gets that section's geometry,
    and it is in-bounds (y+height <= page height)."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([{"id": "hero", "trigger": "scroll",
                "selector": ".dga-module__LrmiHG__hero"}]),
        _section_map(),
    )
    assert out is not None
    r = out["regions"][0]
    assert r["y"] == 42
    assert r["height"] == 638
    assert r["x"] == 0
    page_h = max(s["top"] + s["height"] for s in _section_map()["sections"])
    assert r["y"] + r["height"] <= page_h


def test_derive_resolves_section_by_id() -> None:
    """Selector that matches a section id (#problem) resolves geometry."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([{"id": "stats", "trigger": "scroll", "selector": "#problem"}]),
        _section_map(),
    )
    assert out is not None
    assert out["regions"][0]["y"] == 1345


def test_derive_selector_only_when_no_section_match() -> None:
    """A selector that matches no section is emitted selector-only — no
    fabricated geometry (most realfood nav-chrome transitions are this)."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([{"id": "navbtn", "trigger": "hover",
                "selector": ".nav-module__-Tun0a__dot_button"}]),
        _section_map(),
    )
    assert out is not None
    r = out["regions"][0]
    assert "y" not in r and "height" not in r and "x" not in r


def test_derive_skips_geometry_for_negative_top_sections() -> None:
    """A selector matching an off-canvas/fixed section (top<0) does not get
    negative, out-of-bounds geometry — emitted selector-only."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([{"id": "overlay", "trigger": "scroll",
                "selector": ".intro-animation-module__0093MG__overlay"}]),
        _section_map(),
    )
    assert out is not None
    assert "y" not in out["regions"][0]


def test_derive_filters_garbage_selectors() -> None:
    """Declaration-fragment 'selectors' must be excluded, not emitted."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([
            {"id": "junk", "trigger": "hover", "selector": "transform .2s ease;&"},
            {"id": "good", "trigger": "hover", "selector": ".btn"},
        ]),
        None,
    )
    assert out is not None
    assert len(out["regions"]) == 1
    assert out["regions"][0]["name"] == "good"


def test_derive_handles_matrix3d_and_descendant_selectors() -> None:
    """Complex descendant selectors resolve on the LAST class token without
    crashing (matrix3d animation type is irrelevant to projection)."""
    mod = _load_module()
    out = mod.derive_regions_json(
        _spec([{"id": "desc", "trigger": "scroll",
                "selector": ".wrap .dga-module__LrmiHG__stats",
                "animation": {"type": "scroll-scrub", "cssText": "transform:matrix3d(...)"}}]),
        _section_map(),
    )
    assert out is not None
    assert out["regions"][0]["y"] == 1345


def test_derive_is_deterministic() -> None:
    """Same input → byte-identical output (deterministic projection)."""
    mod = _load_module()
    spec = _spec([
        {"id": "a", "trigger": "hover", "selector": ".a"},
        {"id": "b", "trigger": "scroll", "selector": ".dga-module__LrmiHG__hero"},
    ])
    a = mod.derive_regions_json(spec, _section_map())
    b = mod.derive_regions_json(spec, _section_map())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ── produce_regions_json (read ref_dir, write, idempotent) ──


def _write_placeholder(ref: Path, page_height: int = 9000) -> None:
    mod = _load_module()
    mod.write_regions_json(ref, page_height=page_height)


def test_produce_upgrades_placeholder_to_real(tmp_path: Path) -> None:
    mod = _load_module()
    _write_placeholder(tmp_path)
    (tmp_path / "transition-spec.json").write_text(json.dumps(
        _spec([{"id": "hero", "trigger": "scroll",
                "selector": ".dga-module__LrmiHG__hero"}])))
    (tmp_path / "section-map.json").write_text(json.dumps(_section_map()))
    out = mod.produce_regions_json(tmp_path)
    assert out is not None
    written = json.loads((tmp_path / "regions.json").read_text())
    assert written["placeholder"] is False
    assert written["regions"][0]["name"] == "hero"


def test_produce_keeps_placeholder_when_no_real_transitions(tmp_path: Path) -> None:
    """Static page (distinct-but-static frames, only skipped[]) at ph=9000 →
    placeholder is preserved, not overwritten with a fabricated region."""
    mod = _load_module()
    _write_placeholder(tmp_path, page_height=9000)
    (tmp_path / "transition-spec.json").write_text(json.dumps(
        _spec([], skipped=[{"id": "x", "trigger": "hover", "selector": ".btn"}])))
    out = mod.produce_regions_json(tmp_path)
    assert out is None
    written = json.loads((tmp_path / "regions.json").read_text())
    assert written["placeholder"] is True


def test_produce_does_not_downgrade_real_regions(tmp_path: Path) -> None:
    """Idempotency: a real regions.json must not be downgraded to placeholder
    when transition-spec is transiently absent on re-run."""
    mod = _load_module()
    real = mod.derive_regions_json(
        _spec([{"id": "hero", "trigger": "scroll",
                "selector": ".dga-module__LrmiHG__hero"}]),
        _section_map(),
    )
    (tmp_path / "regions.json").write_text(json.dumps(real))
    # transition-spec.json intentionally absent now.
    out = mod.produce_regions_json(tmp_path)
    assert out is None
    written = json.loads((tmp_path / "regions.json").read_text())
    assert written["placeholder"] is False
    assert written["regions"][0]["name"] == "hero"


def test_produce_idempotent_repeat(tmp_path: Path) -> None:
    mod = _load_module()
    (tmp_path / "transition-spec.json").write_text(json.dumps(
        _spec([{"id": "hero", "trigger": "scroll",
                "selector": ".dga-module__LrmiHG__hero"}])))
    (tmp_path / "section-map.json").write_text(json.dumps(_section_map()))
    mod.produce_regions_json(tmp_path)
    first = (tmp_path / "regions.json").read_text()
    mod.produce_regions_json(tmp_path)
    second = (tmp_path / "regions.json").read_text()
    assert first == second


# ── write_regions_json (placeholder writer, resume-safe) ──


def test_write_regions_writes_placeholder_when_absent(tmp_path: Path) -> None:
    mod = _load_module()
    mod.write_regions_json(tmp_path, page_height=5000)
    written = json.loads((tmp_path / "regions.json").read_text())
    assert written["placeholder"] is True
    assert written["regions"][0]["height"] == 5000


def test_write_regions_preserves_real_regions(tmp_path: Path) -> None:
    """Resume-safety: a partial re-run of the early capture phases calls
    write-regions again, but must NOT clobber a real regions.json produced by a
    prior enrichment phase back to the placeholder (regressing `reference`)."""
    mod = _load_module()
    real = mod.derive_regions_json(
        _spec([{"id": "hero", "trigger": "scroll",
                "selector": ".dga-module__LrmiHG__hero"}]),
        _section_map(),
    )
    (tmp_path / "regions.json").write_text(json.dumps(real))
    mod.write_regions_json(tmp_path, page_height=5000)
    written = json.loads((tmp_path / "regions.json").read_text())
    assert written.get("placeholder") is not True
    assert written["source"] == "derive-from-transition-spec"
    assert written["regions"][0]["name"] == "hero"


def test_write_regions_refreshes_existing_placeholder(tmp_path: Path) -> None:
    """An existing placeholder is not 'real', so a re-run may refresh it (e.g.
    with an updated page height) — only real detections are preserved."""
    mod = _load_module()
    mod.write_regions_json(tmp_path, page_height=3000)
    mod.write_regions_json(tmp_path, page_height=7777)
    written = json.loads((tmp_path / "regions.json").read_text())
    assert written["placeholder"] is True
    assert written["regions"][0]["height"] == 7777


def test_produce_graceful_when_spec_missing(tmp_path: Path) -> None:
    """No transition-spec, no prior regions → None, no crash, no file."""
    mod = _load_module()
    out = mod.produce_regions_json(tmp_path)
    assert out is None
    assert not (tmp_path / "regions.json").exists()


# ── CLI ──


def test_main_derive_regions(tmp_path: Path) -> None:
    mod = _load_module()
    (tmp_path / "transition-spec.json").write_text(json.dumps(
        _spec([{"id": "hero", "trigger": "scroll",
                "selector": ".dga-module__LrmiHG__hero"}])))
    (tmp_path / "section-map.json").write_text(json.dumps(_section_map()))
    rc = mod.main(["derive-regions", str(tmp_path)])
    assert rc == 0
    written = json.loads((tmp_path / "regions.json").read_text())
    assert written["placeholder"] is False


def test_main_derive_regions_no_arg() -> None:
    mod = _load_module()
    assert mod.main(["derive-regions"]) == 2
