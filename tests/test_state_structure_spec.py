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
    (splash / "contract.json").write_text(json.dumps({
        "schemaVersion": 1,
        "detected": True,
        "overlay": {"selector": "#splash", "maxCoverage": 1.0},
        "activeAnimation": {"maxActiveCount": 1, "samples": [{"selector": "#splash"}]},
        "motionEvidence": {"changed": True, "signals": ["active-animation"]},
        "mediaFingerprint": {"hashes": ["media-start", "media-end"]},
        "exitTiming": {"fromMs": 0, "toMs": 600, "durationMs": 600},
        "bookends": ["states/splash/0ms.json", "states/splash/settled.json"],
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
    (scroll / "dom-mutations.json").write_text(json.dumps([{
        "fromPct": 0,
        "toPct": 50,
        "firstScrollY": 420,
        "lastScrollY": 500,
        "selector": "section.features",
        "type": "attributes",
        "attribute": "class",
        "oldValue": "features",
        "newValue": "features is-visible",
        "count": 1,
    }]))

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
    assert "states/splash/contract.json" in splash_event["artifacts"]
    assert splash_event["domMutation"]["changed"] is True
    assert splash_event["splashContract"]["detected"] is True
    assert splash_event["splashContract"]["overlay"]["selector"] == "#splash"
    assert splash_event["splashContract"]["activeAnimation"]["maxActiveCount"] == 1
    assert splash_event["splashContract"]["exitTiming"]["durationMs"] == 600

    scroll_event = next(event for event in events if event["phase"] == "scroll")
    assert scroll_event["scrollEngine"] == "lenis"
    assert scroll_event["fromPct"] == 0
    assert scroll_event["toPct"] == 50
    assert scroll_event["domMutation"]["observedMutationCount"] == 1
    assert scroll_event["domMutation"]["observedMutations"][0]["attribute"] == "class"
    assert "states/scroll/dom-mutations.json" in scroll_event["artifacts"]

    hover_event = next(event for event in events if event["phase"] == "hover")
    assert hover_event["signalKinds"] == ["css", "js", "dom"]
    assert hover_event["activation"] == ".card"

    click_event = next(event for event in events if event["phase"] == "click")
    assert click_event["navigationType"] == "external"
    assert click_event["guard"]["restored"] is True

    serialized = json.dumps(spec)
    assert "outerHTML" not in serialized
    assert "<section" not in serialized


def test_state_structure_spec_omits_authoritative_negative_splash(tmp_path: Path) -> None:
    """Legacy media-driven multi-sample captures must not become splash events."""
    ref = tmp_path / "ref"
    splash = ref / "states" / "splash"
    splash.mkdir(parents=True)
    (splash / "summary.json").write_text(
        json.dumps({"checked": True, "polls": 3}),
        encoding="utf-8",
    )
    (splash / "trajectory.json").write_text(
        json.dumps(
            [
                {"ts_ms": 0, "hash": 1, "bodyClass": "", "htmlClass": ""},
                {"ts_ms": 900, "hash": 2, "bodyClass": "", "htmlClass": ""},
                {"ts_ms": 2900, "hash": 3, "bodyClass": "", "htmlClass": ""},
            ]
        ),
        encoding="utf-8",
    )
    (splash / "contract.json").write_text(
        json.dumps({"schemaVersion": 1, "detected": False}),
        encoding="utf-8",
    )

    proc = _run_state_structure_spec(ref)
    assert proc.returncode == 0, proc.stderr

    spec = json.loads((ref / "state-structure-spec.json").read_text(encoding="utf-8"))
    assert not [event for event in spec["events"] if event["phase"] == "splash"]
    assert spec["phases"]["splash"]["present"] is False
    assert spec["phases"]["splash"]["eventCount"] == 0


def test_state_structure_spec_omits_pre_navigation_negative_splash(tmp_path: Path) -> None:
    """Pre-navigation negative captures remain authoritative."""
    ref = tmp_path / "ref"
    splash = ref / "states" / "splash"
    splash.mkdir(parents=True)
    (splash / "summary.json").write_text(
        json.dumps({"checked": True, "polls": 3}),
        encoding="utf-8",
    )
    (splash / "trajectory.json").write_text(
        json.dumps(
            [
                {"ts_ms": 0, "hash": 1, "bodyClass": "", "htmlClass": ""},
                {"ts_ms": 900, "hash": 2, "bodyClass": "", "htmlClass": ""},
            ]
        ),
        encoding="utf-8",
    )
    (splash / "contract.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "captureMode": "pre-navigation",
                "detected": False,
                "overlay": {"selector": None, "maxCoverage": 0, "exitObserved": False},
            }
        ),
        encoding="utf-8",
    )

    proc = _run_state_structure_spec(ref)
    assert proc.returncode == 0, proc.stderr

    spec = json.loads((ref / "state-structure-spec.json").read_text(encoding="utf-8"))
    assert not [event for event in spec["events"] if event["phase"] == "splash"]
    assert spec["phases"]["splash"]["present"] is False
    assert spec["phases"]["splash"]["eventCount"] == 0


def test_state_structure_spec_falls_through_for_observed_overlay_negative(
    tmp_path: Path,
) -> None:
    """Observed-but-unexited overlays keep independent splash evidence available."""
    ref = tmp_path / "ref"
    splash = ref / "states" / "splash"
    splash.mkdir(parents=True)
    (splash / "summary.json").write_text(
        json.dumps({"checked": True, "polls": 3, "reason": "stable-2s"}),
        encoding="utf-8",
    )
    (splash / "trajectory.json").write_text(
        json.dumps(
            [
                {"ts_ms": 0, "hash": 1, "bodyClass": "loading", "htmlClass": ""},
                {"ts_ms": 900, "hash": 2, "bodyClass": "loading-mid", "htmlClass": ""},
                {"ts_ms": 2900, "hash": 3, "bodyClass": "loading", "htmlClass": ""},
            ]
        ),
        encoding="utf-8",
    )
    (splash / "contract.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "captureMode": "pre-navigation",
                "detected": False,
                "overlay": {"everVisible": True, "exitObserved": False},
                "capture": {
                    "stateCount": 3,
                    "timedOut": False,
                    "reason": "stable-2s",
                    "authoritativeNegative": False,
                },
            }
        ),
        encoding="utf-8",
    )

    proc = _run_state_structure_spec(ref)
    assert proc.returncode == 0, proc.stderr

    spec = json.loads((ref / "state-structure-spec.json").read_text(encoding="utf-8"))
    splash_events = [event for event in spec["events"] if event["phase"] == "splash"]
    assert len(splash_events) == 1
    assert splash_events[0]["splashContract"]["overlay"]["everVisible"] is True


def test_state_structure_spec_reuse_session_negative_splash_falls_through_to_trajectory(
    tmp_path: Path,
) -> None:
    """Reuse-session negatives do not mask independent trajectory evidence."""
    ref = tmp_path / "ref"
    splash = ref / "states" / "splash"
    splash.mkdir(parents=True)
    (splash / "summary.json").write_text(
        json.dumps({"checked": True, "polls": 3}),
        encoding="utf-8",
    )
    (splash / "trajectory.json").write_text(
        json.dumps(
            [
                {"ts_ms": 0, "hash": 1, "bodyClass": "loading", "htmlClass": ""},
                {"ts_ms": 900, "hash": 2, "bodyClass": "loaded", "htmlClass": ""},
            ]
        ),
        encoding="utf-8",
    )
    (splash / "contract.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "captureMode": "reuse-session",
                "detected": False,
            }
        ),
        encoding="utf-8",
    )

    proc = _run_state_structure_spec(ref)
    assert proc.returncode == 0, proc.stderr

    spec = json.loads((ref / "state-structure-spec.json").read_text(encoding="utf-8"))
    splash_events = [event for event in spec["events"] if event["phase"] == "splash"]
    assert len(splash_events) == 1
    assert splash_events[0]["splashContract"]["captureMode"] == "reuse-session"
    assert splash_events[0]["splashContract"]["detected"] is False
    assert spec["phases"]["splash"]["present"] is True
    assert spec["phases"]["splash"]["eventCount"] == 1
