"""Tests for scripts/extract/state-structure-spec.py.

The spec is a compact, derived index over browser-observed state captures.
It must never inline the large raw HTML blobs that capture-* scripts store
under states/**.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "extract" / "state-structure-spec.py"


def _run_state_structure_spec(ref_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(SCRIPT), str(ref_dir)],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_state_structure_spec_aggregates_browser_observed_states(tmp_path: Path) -> None:
    """Splash/scroll/hover/click evidence becomes one compact event index."""
    ref = tmp_path / "ref"
    splash = ref / "states" / "splash"
    scroll = ref / "states" / "scroll"
    hover = ref / "states" / "hover"
    click = ref / "states" / "click"
    splash.mkdir(parents=True)
    scroll.mkdir(parents=True)
    hover.mkdir(parents=True)
    click.mkdir(parents=True)

    (splash / "summary.json").write_text(json.dumps({"checked": True, "polls": 2}))
    (splash / "trajectory.json").write_text(json.dumps([
        {
            "ts_ms": 0,
            "hash": 1,
            "bodyClass": "is-loading",
            "htmlClass": "no-js",
            "domLength": 100,
            "compositeDigest": "loading",
        },
        {
            "ts_ms": 600,
            "hash": 2,
            "bodyClass": "is-loaded",
            "htmlClass": "js",
            "domLength": 240,
            "compositeDigest": "loaded",
        },
    ]))
    (splash / "0ms.json").write_text(json.dumps({
        "outerHTML": "<html class='no-js'><body class='is-loading'><div id='splash'></div></body></html>"
    }))
    (splash / "settled.json").write_text(json.dumps({
        "outerHTML": "<html class='js'><body class='is-loaded'><main><section class='hero'></section></main></body></html>"
    }))

    (scroll / "summary.json").write_text(json.dumps({
        "checked": True,
        "static": False,
        "scrollEngine": "lenis",
    }))
    (scroll / "trajectory.json").write_text(json.dumps([
        {"pct": 0, "scrollY": 0, "visibleSections": [{"selector": "section.hero"}]},
        {"pct": 50, "scrollY": 500, "visibleSections": [{"selector": "section.features"}]},
    ]))
    (scroll / "0pct.json").write_text(json.dumps({
        "outerHTML": "<html><body><section class='hero'></section></body></html>"
    }))
    (scroll / "50pct.json").write_text(json.dumps({
        "outerHTML": "<html><body><section class='features is-visible'></section></body></html>"
    }))

    (hover / "manifest.json").write_text(json.dumps({
        "entries": [
            {"id": "abc123", "kind": "css+js", "file": "elem-abc123.json",
             "selector": ".card", "activation": ".card", "changedCount": 2}
        ]
    }))
    (hover / "elem-abc123.json").write_text(json.dumps({
        "id": "abc123",
        "activation": ".card",
        "affected": ".card .title",
        "kind": "css+js",
        "cssProperties": {"transform": "scale(1.04)"},
        "jsChanges": [{"selector": ".card", "computedStyleAfter": {"opacity": "1"}}],
        "domChanges": [{"selector": ".card", "classBefore": "card", "classAfter": "card is-hover"}],
    }))

    (click / "manifest.json").write_text(json.dumps({
        "entries": [
            {"id": "nav", "file": "click-nav.json", "selector": "a.external",
             "triggerType": "click-navigation", "navigationType": "external"},
        ]
    }))
    (click / "click-nav.json").write_text(json.dumps({
        "id": "nav",
        "selector": "a.external",
        "triggerType": "click-navigation",
        "navigationType": "external",
        "declaredOnly": True,
        "guard": {"isolatedSession": True, "restored": True},
    }))

    proc = _run_state_structure_spec(ref)
    assert proc.returncode == 0, proc.stderr

    spec_path = ref / "state-structure-spec.json"
    spec = json.loads(spec_path.read_text())
    assert spec["schemaVersion"] == 1
    assert spec["producer"] == "scripts/extract/state-structure-spec.py"

    events = spec["events"]
    triggers = {(event["phase"], event["trigger"]) for event in events}
    assert ("splash", "page-load") in triggers
    assert ("scroll", "scroll") in triggers
    assert ("hover", "hover") in triggers
    assert ("click", "click") in triggers

    splash_event = next(event for event in events if event["phase"] == "splash")
    assert splash_event["bodyClassBefore"] == "is-loading"
    assert splash_event["bodyClassAfter"] == "is-loaded"
    assert "states/splash/trajectory.json" in splash_event["artifacts"]
    assert splash_event["domMutation"]["changed"] is True

    scroll_event = next(event for event in events if event["phase"] == "scroll")
    assert scroll_event["scrollEngine"] == "lenis"
    assert scroll_event["fromPct"] == 0
    assert scroll_event["toPct"] == 50

    hover_event = next(event for event in events if event["phase"] == "hover")
    assert hover_event["signalKinds"] == ["css", "js", "dom"]
    assert hover_event["activation"] == ".card"

    click_event = next(event for event in events if event["phase"] == "click")
    assert click_event["navigationType"] == "external"
    assert click_event["guard"]["restored"] is True

    serialized = json.dumps(spec)
    assert "outerHTML" not in serialized
    assert "<section" not in serialized
