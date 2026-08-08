"""Tests for the real-hover region artifact capture bridge."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from ui_clone.gate import Gate

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "extract" / "capture-region-artifacts.py"


def _make_fake_agent_browser(tmp_path: Path, *, identical: bool = False) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "agent-browser"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import os
import re
import struct
import sys
import zlib
from pathlib import Path

calls = Path(os.environ["FAKE_CALLS"])
with calls.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")

args = sys.argv[1:]
session = args[args.index("--session") + 1]
command_index = args.index("--session") + 2
command = args[command_index]
rest = args[command_index + 1:]
state_path = Path(os.environ["FAKE_STATE"])
try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except FileNotFoundError:
    state = {}

def png(width, height, fill, box, box_fill):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF
        )
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            inside = box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]
            raw += bytes(box_fill if inside else fill)
    return (
        b"\\x89PNG\\r\\n\\x1a\\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )

if command == "open" and os.environ.get("FAKE_OPEN_FAIL") == "1":
    sys.exit(2)
elif command == "eval":
    script = rest[-1]
    scroll_key = session + ":scroll"
    drift_key = session + ":delayed-scroll-drift"
    if (
        os.environ.get("FAKE_DELAYED_SCROLL_DRIFT") == "1"
        and state.get(drift_key)
        and "scrollIntoView" in script
    ):
        state[drift_key] = False
        state_path.write_text(json.dumps(state), encoding="utf-8")
    moved = re.search(r"window\\.scrollTo\\(\\{top:([0-9.]+)", script)
    if moved:
        state[scroll_key] = float(moved.group(1))
        state_path.write_text(json.dumps(state), encoding="utf-8")
    scrolled = float(state.get(scroll_key, 0))
    if "scrollHeight" in script:
        print(json.dumps({"success": True, "data": {"result": {"found": True, "maxScroll": 1000}}}))
        sys.exit(0)
    found = ".missing" not in script
    result = {
        "found": found,
        "matches": 1 if found else 0,
        "x": 10,
        "y": (
            150
            if state.get(drift_key)
            else 1000
            if os.environ.get("FAKE_ADJACENT_SCROLL_ONLY") == "1" and scrolled > 100
            else 10
        ),
        "width": 120 if found else 0,
        "height": 40 if found else 0,
        "scrollX": 0,
        "scrollY": scrolled,
        "fullyVisible": os.environ.get("FAKE_OFFSCREEN") != "1",
        "viewportWidth": 200,
    }
    if found and "const tracked=" in script:
        active = state.get(session, False) and os.environ.get("FAKE_NO_CHANGE") != "1"
        result.update({
            "styles": {
                "transform": "matrix(1.1, 0, 0, 1.1, 0, 0)" if active else "none",
                "boxShadow": "rgba(0, 0, 0, 0.07) 0px 4px 12px 0px" if scrolled >= 100 else "none",
                "opacity": str(round(scrolled / 1000, 2)) if os.environ.get("FAKE_SCROLL_MODE") == "scrubbed" else "1",
            },
            "transitionProperty": "transform",
            "transitionDuration": "0.2s",
            "transitionTimingFunction": "ease-out",
        })
    print(json.dumps({"success": True, "data": {"result": result}}))
elif command == "hover":
    state[session] = bool(rest and rest[0] != "body")
    state_path.write_text(json.dumps(state), encoding="utf-8")
elif (
    command == "wait"
    and rest == ["300"]
    and os.environ.get("FAKE_DELAYED_SCROLL_DRIFT") == "1"
):
    state[session + ":delayed-scroll-drift"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
elif command == "screenshot":
    output = Path(rest[-1])
    output.parent.mkdir(parents=True, exist_ok=True)
    active = (
        state.get(session, False) or float(state.get(session + ":scroll", 0)) >= 100
    ) and os.environ.get("FAKE_IDENTICAL") != "1" and os.environ.get("FAKE_NO_CHANGE") != "1"
    output.write_bytes(
        png(
            200,
            100,
            (255, 255, 255, 255),
            (10, 10, 120, 40),
            (255, 0, 0, 255) if active else (255, 255, 0, 255),
        )
    )
sys.exit(0)
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    if identical:
        (tmp_path / "identical").touch()
    return bin_dir


def _write_regions(
    ref_dir: Path,
    regions: list[dict],
    *,
    source: str = "derive-from-transition-spec",
) -> None:
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "regions.json").write_text(
        json.dumps(
            {
                "placeholder": False,
                "source": source,
                "regions": regions,
            }
        ),
        encoding="utf-8",
    )


def _run(
    tmp_path: Path,
    regions: list[dict],
    *,
    identical: bool = False,
    reuse_session: bool = False,
    open_fail: bool = False,
    transition_spec: dict | None = None,
    interactions: dict | list | None = None,
    verification_signals: dict | None = None,
    region_source: str = "derive-from-transition-spec",
    source_files: dict[str, str] | None = None,
    hover_css_rules: dict | list | None = None,
    scroll_engine: dict | None = None,
    scroll_mode: str | None = None,
    offscreen: bool = False,
    adjacent_scroll_only: bool = False,
    delayed_scroll_drift: bool = False,
    no_change: bool = False,
    prior_artifacts: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, list[list[str]]]:
    ref_dir = tmp_path / "ref"
    _write_regions(ref_dir, regions, source=region_source)
    if prior_artifacts:
        for region in regions:
            artifacts = region.get("artifacts")
            if not isinstance(artifacts, dict):
                continue
            for relative in artifacts.values():
                path = ref_dir / str(relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"prior-artifact")
    if transition_spec is not None:
        (ref_dir / "transition-spec.json").write_text(
            json.dumps(transition_spec),
            encoding="utf-8",
        )
    if interactions is not None:
        (ref_dir / "interactions-detected.json").write_text(
            json.dumps(interactions),
            encoding="utf-8",
        )
    if verification_signals is not None:
        (ref_dir / "verification-plan.json").write_text(
            json.dumps({"signals": verification_signals, "requiredChecks": []}),
            encoding="utf-8",
        )
    for relative, content in (source_files or {}).items():
        source_path = ref_dir / relative
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(content, encoding="utf-8")
    if scroll_engine is not None:
        (ref_dir / "scroll-engine.json").write_text(
            json.dumps(scroll_engine),
            encoding="utf-8",
        )
    if hover_css_rules is not None:
        (ref_dir / "hover-css-rules.json").write_text(
            json.dumps(hover_css_rules),
            encoding="utf-8",
        )
    bin_dir = _make_fake_agent_browser(tmp_path, identical=identical)
    calls_path = tmp_path / "calls.jsonl"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_CALLS"] = str(calls_path)
    env["FAKE_STATE"] = str(tmp_path / "state.json")
    if identical:
        env["FAKE_IDENTICAL"] = "1"
    if open_fail:
        env["FAKE_OPEN_FAIL"] = "1"
    if scroll_mode:
        env["FAKE_SCROLL_MODE"] = scroll_mode
    if offscreen:
        env["FAKE_OFFSCREEN"] = "1"
    if adjacent_scroll_only:
        env["FAKE_ADJACENT_SCROLL_ONLY"] = "1"
    if delayed_scroll_drift:
        env["FAKE_DELAYED_SCROLL_DRIFT"] = "1"
    if no_change:
        env["FAKE_NO_CHANGE"] = "1"
    args = [
        sys.executable,
        str(SCRIPT),
        "https://example.test",
        "capture",
        str(ref_dir),
    ]
    if reuse_session:
        args.append("--reuse-session")
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    calls = (
        [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
        if calls_path.is_file()
        else []
    )
    return proc, ref_dir, calls


def _run_existing_ref(
    tmp_path: Path,
    ref_dir: Path,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    bin_dir = _make_fake_agent_browser(tmp_path)
    calls_path = tmp_path / "calls.jsonl"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_CALLS"] = str(calls_path)
    env["FAKE_STATE"] = str(tmp_path / "state.json")
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "https://example.test",
            "capture",
            str(ref_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    return proc, calls


def test_dedupes_hover_regions_and_writes_explicit_artifacts(tmp_path: Path) -> None:
    scroll = {
        "name": "hero-scroll",
        "triggerType": "scroll",
        "selector": ".hero",
        "dispatchOnly": True,
    }
    proc, ref_dir, calls = _run(
        tmp_path,
        [
            {
                "name": "button",
                "triggerType": "hover",
                "selector": ".button[data-label='a b']",
                "dispatchOnly": True,
            },
            {
                "name": "button-copy",
                "triggerType": "hover",
                "selector": ".button[data-label='a b']",
                "dispatchOnly": True,
            },
            scroll,
        ],
        transition_spec={
            "schemaVersion": 1,
            "source": "ui_clone.extraction_artifacts",
            "placeholder": True,
            "transitions": [
                {
                    "id": "auto-hover-0",
                    "trigger": "hover",
                    "source_chunk": "x.css",
                    "bundle_branch": "settled branch observed during capture",
                    "target": ".button[data-label='a b']:hover",
                    "animation": {"type": "css-hover"},
                    "reference_frames": "none",
                }
            ],
        },
        interactions={
            "schemaVersion": 1,
            "source": "ui_clone.extraction_artifacts",
            "interactions": [
                {
                    "id": "hover-0",
                    "trigger": "hover",
                    "target": ".button[data-label='a b']",
                },
                {
                    "id": "hover-stale",
                    "trigger": "hover",
                    "target": ".stale",
                },
            ],
        },
        verification_signals={
            "hasScrollScrub": False,
            "hasScrollStateMachine": False,
        },
        region_source="manual-capture-fixture",
        source_files={"css/x.css": ".button:hover { transform: scale(1.1) }"},
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert len(payload["regions"]) == 1
    hover = payload["regions"][0]
    assert hover["artifacts"] == {
        "idle": "clip/ref/00-button-idle.png",
        "active": "clip/ref/00-button-active.png",
    }
    assert "dispatchOnly" not in hover
    for relative in hover["artifacts"].values():
        assert (ref_dir / relative).stat().st_size > 0

    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["counts"] == {
        "attempted": 1,
        "captured": 1,
        "skipped": 2,
        "unsupported": 0,
        "notInstantiated": 0,
    }
    reasons = [entry["reason"] for entry in summary["skipped"]]
    assert "duplicate selector and trigger" in reasons
    assert summary["skipped"][0] == {
        "region": "hero-scroll",
        "selector": ".hero",
        "triggerType": "scroll",
        "reason": (
            "auto dispatch-only region was not live-captured and current "
            "verification-plan signals are false"
        ),
    }

    commands = [call[2] for call in calls]
    assert commands.count("open") == 1
    assert commands.count("close") == 1
    assert all(call[1] == "capture-region-artifacts" for call in calls)
    marker_selector = '[data-uiclone-region="region-0"]'
    assert any(call[2] == "hover" and call[3] == marker_selector for call in calls)
    assert any(call[2:] == ["mouse", "move", "-100", "-100"] for call in calls)
    screenshots = [call for call in calls if call[2] == "screenshot"]
    assert len(screenshots) == 2
    assert all("--clip" not in call for call in screenshots)
    assert all(call[3].endswith(".png") for call in screenshots)
    evals = [call[-1] for call in calls if call[2] == "eval"]
    assert evals and all(script.startswith("(() => {") for script in evals)
    assert any(json.dumps(".button[data-label='a b']") in script for script in evals)
    assert any(json.dumps(marker_selector) in script for script in evals)

    spec = json.loads((ref_dir / "transition-spec.json").read_text())
    assert spec["placeholder"] is False
    assert spec["source"] == "scripts/extract/capture-region-artifacts.py"
    assert spec["provenance"]["kind"] == "live-capture"
    assert len(spec["transitions"]) == 1
    transition = spec["transitions"][0]
    assert transition["bundle_branch"].startswith("live-capture:")
    assert transition["source_chunk"] == "x.css"
    assert transition["reference_frames"] == [
        "clip/ref/00-button-idle.png",
        "clip/ref/00-button-active.png",
    ]
    assert transition["animation"] == {
        "type": "css-hover",
        "property": "transform",
        "changedProperties": ["transform"],
        "from": {"transform": "none"},
        "to": {"transform": "matrix(1.1, 0, 0, 1.1, 0, 0)"},
        "duration": "0.2s",
        "easing": "ease-out",
        "pixelCorroborated": True,
    }
    interactions = json.loads((ref_dir / "interactions-detected.json").read_text())
    assert interactions["source"] == "scripts/extract/capture-region-artifacts.py"
    assert interactions["interactions"] == [
        {
            "id": "hover-0",
            "trigger": "hover",
            "target": ".button[data-label='a b']",
            "referenceArtifacts": hover["artifacts"],
        }
    ]
    assert interactions["skipped"] == [
        {
            "sourceArtifact": "interactions-detected.json",
            "sourceId": "hover-stale",
            "trigger": "hover",
            "target": ".stale",
            "reason": "auto interaction selector was not live-captured",
        }
    ]
    (ref_dir / "bundle-map.json").write_text(json.dumps({"chunks": []}), encoding="utf-8")
    (ref_dir / "external-sdks.json").write_text(json.dumps({"sdks": []}), encoding="utf-8")
    (ref_dir / "verification-plan.json").write_text(
        json.dumps({"signals": {"hasHover": True}, "requiredChecks": []}),
        encoding="utf-8",
    )
    failures = [result for result in Gate(ref_dir).gate_spec() if result.status == "fail"]
    assert failures == []


def test_missing_selector_region_is_dropped_without_artifacts(tmp_path: Path) -> None:
    proc, ref_dir, _ = _run(
        tmp_path,
        [{"name": "gone", "triggerType": "css-hover", "selector": ".missing"}],
    )
    assert proc.returncode == 5
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert [region["selector"] for region in payload["regions"]] == [".missing"]
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["captured"] == []
    assert summary["skipped"] == []
    assert summary["notInstantiated"][0]["reason"] == "selector matches no elements"
    assert summary["status"] == "fail"
    assert summary["counts"]["attempted"] == 0
    assert summary["counts"]["notInstantiated"] == 1
    assert summary["counts"]["captured"] == 0
    assert not list((ref_dir / "clip" / "ref").glob("*.png"))


def test_style_only_change_is_captured_without_pixel_corroboration(tmp_path: Path) -> None:
    placeholder = {
        "source": "ui_clone.extraction_artifacts",
        "placeholder": True,
        "transitions": [{"id": "auto-hover-0"}],
    }
    proc, ref_dir, _ = _run(
        tmp_path,
        [{"name": "static-hover", "triggerType": "hover", "selector": ".same"}],
        identical=True,
        transition_spec=placeholder,
        interactions={
            "source": "ui_clone.extraction_artifacts",
            "interactions": [{"id": "hover-0", "trigger": "hover", "target": ".same"}],
        },
    )
    assert proc.returncode == 0
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert sorted(payload["regions"][0]["artifacts"]) == ["active", "idle"]
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["counts"]["captured"] == 1
    assert summary["skipped"] == []
    assert len(list((ref_dir / "clip" / "ref").glob("*.png"))) == 2

    spec = json.loads((ref_dir / "transition-spec.json").read_text())
    assert spec["placeholder"] is False
    assert spec["source"] == "scripts/extract/capture-region-artifacts.py"
    animation = spec["transitions"][0]["animation"]
    # The measured style delta is the evidence; the crop could not corroborate
    # it, and that has to travel with the claim rather than be assumed away.
    assert animation["changedProperties"] == ["transform"]
    assert animation["pixelCorroborated"] is False

    bundles = ref_dir / "bundles"
    bundles.mkdir()
    (bundles / "app.js").write_text("// fixture", encoding="utf-8")
    (ref_dir / "scroll-engine.json").write_text(json.dumps({"type": "native"}), encoding="utf-8")
    results = Gate(ref_dir).gate_bundle()
    assert [r.label for r in results if r.status == "fail"] == []
    assert "style-only transition evidence" in [r.label for r in results if r.status == "warn"]

    interactions = json.loads((ref_dir / "interactions-detected.json").read_text())
    assert [entry["target"] for entry in interactions["interactions"]] == [".same"]


def test_reuse_session_does_not_open_or_close_callers_session(tmp_path: Path) -> None:
    proc, _, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        reuse_session=True,
    )
    assert proc.returncode == 0
    assert all(call[1] == "capture" for call in calls)
    assert "open" not in [call[2] for call in calls]
    assert "close" not in [call[2] for call in calls]


def test_failed_open_still_closes_only_the_derived_session(tmp_path: Path) -> None:
    proc, _, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        open_fail=True,
    )
    assert proc.returncode != 0
    assert [call[2] for call in calls] == ["open", "close"]
    assert all(call[1] == "capture-region-artifacts" for call in calls)


def test_unsupported_capture_needing_region_fails_and_is_preserved(
    tmp_path: Path,
) -> None:
    click = {
        "name": "menu",
        "triggerType": "click-toggle",
        "selector": ".menu",
    }
    proc, ref_dir, calls = _run(tmp_path, [click])
    assert proc.returncode != 0
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert payload["regions"] == [click]
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["unsupported"] == [{"region": "menu", "triggerType": "click-toggle"}]
    assert calls == []


def test_real_spec_dispatch_only_region_remains_an_unsupported_obligation(
    tmp_path: Path,
) -> None:
    hover = {
        "name": "authored-hover",
        "triggerType": "hover",
        "selector": ".missing",
        "dispatchOnly": True,
    }
    scroll = {
        "name": "authored-scroll",
        "triggerType": "scroll",
        "selector": ".authored-scroll",
        "dispatchOnly": True,
    }
    proc, ref_dir, calls = _run(
        tmp_path,
        [hover, scroll],
        transition_spec={
            "source": "agent-authored",
            "placeholder": False,
            "transitions": [],
        },
    )
    assert proc.returncode != 0
    assert [call[2] for call in calls].count("open") == 1
    assert [call[2] for call in calls].count("close") == 1
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert payload["regions"] == [hover, scroll]
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["unsupported"] == [
        {"region": "authored-hover", "triggerType": "hover"},
        {"region": "authored-scroll", "triggerType": "scroll"},
    ]


def test_auto_dispatch_region_is_pruned_when_current_signals_are_false(
    tmp_path: Path,
) -> None:
    scroll = {
        "name": "stale-scroll",
        "triggerType": "scroll",
        "selector": ".stale-scroll",
        "dispatchOnly": True,
    }
    proc, ref_dir, calls = _run(
        tmp_path,
        [scroll],
        transition_spec={
            "source": "ui_clone.extraction_artifacts",
            "placeholder": True,
            "transitions": [{"id": "auto-scroll-0"}],
        },
        verification_signals={
            "hasScrollScrub": False,
            "hasScrollStateMachine": False,
        },
    )
    assert proc.returncode == 0
    assert calls == []
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert payload["regions"] == []
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["unsupported"] == []
    assert "signals are false" in summary["skipped"][0]["reason"]


def test_auto_dispatch_region_is_captured_when_signal_is_active(
    tmp_path: Path,
) -> None:
    scroll = {
        "name": "active-scroll-state",
        "triggerType": "scroll",
        "selector": ".active-scroll",
        "dispatchOnly": True,
    }
    proc, ref_dir, calls = _run(
        tmp_path,
        [scroll],
        transition_spec={
            "source": "ui_clone.extraction_artifacts",
            "placeholder": True,
            "transitions": [{"id": "auto-scroll-0"}],
        },
        verification_signals={
            "hasScrollScrub": False,
            "hasScrollStateMachine": True,
        },
    )
    assert proc.returncode == 0
    assert calls
    payload = json.loads((ref_dir / "regions.json").read_text())
    captured_region = payload["regions"][0]
    # An auto-derived projection is the bridge's own claim, so proving it with
    # real frames discharges the obligation instead of deferring it forever.
    assert sorted(captured_region["artifacts"]) == ["after", "before", "mid"]
    assert "dispatchOnly" not in captured_region
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["skipped"] == []
    assert summary["unsupported"] == []
    assert summary["counts"]["captured"] == 1


def test_stale_derived_regions_refresh_from_current_auto_spec(
    tmp_path: Path,
) -> None:
    stale = {
        "name": "old-hover",
        "triggerType": "hover",
        "selector": ".old-hover",
        "dispatchOnly": True,
    }
    proc, ref_dir, calls = _run(
        tmp_path,
        [stale],
        transition_spec={
            "source": "ui_clone.extraction_artifacts",
            "placeholder": True,
            "transitions": [
                {
                    "id": "auto-scroll-state-0",
                    "trigger": "scroll state machine",
                    "source_chunk": "app.js",
                    "bundle_branch": "settled branch observed during capture",
                    "selector": ".active-scroll",
                    "animation": {"type": "scroll-state-machine"},
                    "reference_frames": "none",
                }
            ],
        },
        verification_signals={
            "hasScrollScrub": False,
            "hasScrollStateMachine": True,
        },
    )
    assert proc.returncode == 0
    payload = json.loads((ref_dir / "regions.json").read_text())
    refreshed = payload["regions"][0]
    assert refreshed["name"] == "auto-scroll-state-0"
    assert refreshed["selector"] == ".active-scroll"
    assert sorted(refreshed["artifacts"]) == ["after", "before", "mid"]
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["unsupported"] == []
    assert summary["counts"]["captured"] == 1


def test_legacy_tooling_source_chunk_is_repaired_from_hover_rules(
    tmp_path: Path,
) -> None:
    proc, ref_dir, _ = _run(
        tmp_path,
        [
            {
                "name": "mapped",
                "triggerType": "hover",
                "selector": ".mapped",
                "dispatchOnly": True,
            },
            {
                "name": "unmapped",
                "triggerType": "hover",
                "selector": ".unmapped",
                "dispatchOnly": True,
            },
        ],
        transition_spec={
            "source": "scripts/extract/capture-region-artifacts.py",
            "placeholder": False,
            "transitions": [
                {
                    "id": "mapped-old",
                    "trigger": "hover",
                    "source_chunk": "capture-hover.sh:live-cssom",
                    "bundle_branch": "live-capture",
                    "target": ".mapped",
                    "animation": {"type": "css-hover"},
                    "reference_frames": ["old-idle.png", "old-active.png"],
                },
                {
                    "id": "unmapped-old",
                    "trigger": "hover",
                    "source_chunk": "capture-hover.sh:live-cssom",
                    "bundle_branch": "live-capture",
                    "target": ".unmapped",
                    "animation": {"type": "css-hover"},
                    "reference_frames": ["old-idle.png", "old-active.png"],
                },
            ],
        },
        region_source="scripts/extract/capture-region-artifacts.py",
        source_files={"css/real.css": ".mapped:hover { transform: scale(1.1) }"},
        hover_css_rules={
            "source": "scripts/extract/capture-hover.sh",
            "rules": [
                {
                    "selector": ".mapped:hover",
                    "activation": ".mapped",
                    "sourceFile": "css/real.css",
                }
            ],
        },
    )
    assert proc.returncode == 0, proc.stderr
    spec = json.loads((ref_dir / "transition-spec.json").read_text())
    by_target = {transition["target"]: transition for transition in spec["transitions"]}
    assert by_target[".mapped"]["source_chunk"] == "css/real.css"
    assert by_target[".unmapped"]["source_chunk"] == "inline init"
    assert all(
        transition["source_chunk"] != "capture-hover.sh:live-cssom"
        for transition in spec["transitions"]
    )


def test_preserved_live_hover_transitions_are_repaired_without_recapture(
    tmp_path: Path,
) -> None:
    mapped = {
        "id": "footer-link",
        "trigger": "hover",
        "source_chunk": "capture-hover.sh:live-cssom",
        "bundle_branch": "live-capture: prior run",
        "target": ".footer__menu .menu__link2",
        "animation": {
            "type": "css-hover",
            "from": {"opacity": "0.8"},
            "to": {"opacity": "1"},
        },
        "reference_frames": [
            "clip/ref/footer-idle.png",
            "clip/ref/footer-active.png",
        ],
    }
    unmapped = {
        "id": "external-link",
        "trigger": "hover",
        "source_chunk": "capture-hover.sh:live-cssom",
        "bundle_branch": "live-capture: prior run",
        "target": "[target=_blank]",
        "animation": {"type": "css-hover", "property": "color"},
        "reference_frames": [
            "clip/ref/external-idle.png",
            "clip/ref/external-active.png",
        ],
    }
    authored_scroll = {
        "id": "authored-scroll",
        "trigger": "scroll",
        "source_chunk": "capture-hover.sh:live-cssom",
        "bundle_branch": "authored behavior",
        "target": ".hero",
        "animation": {"type": "scroll-state-machine"},
        "reference_frames": ["verify/hero-before.png", "verify/hero-after.png"],
    }
    proc, ref_dir, calls = _run(
        tmp_path,
        [],
        transition_spec={
            "source": "scripts/extract/capture-region-artifacts.py",
            "placeholder": False,
            "provenance": {"kind": "live-capture"},
            "transitions": [mapped, unmapped, authored_scroll],
        },
        region_source="scripts/extract/capture-region-artifacts.py",
        source_files={"css/footer.css": ".menu__link2:hover { opacity: 1 }"},
        hover_css_rules={
            "rules": [
                {
                    "selector": ".footer__menu .menu__link2:hover",
                    "activation": ".footer__menu .menu__link2",
                    "sourceFile": "css/footer.css",
                }
            ]
        },
    )
    assert proc.returncode == 0
    assert calls == []
    spec = json.loads((ref_dir / "transition-spec.json").read_text())
    by_id = {transition["id"]: transition for transition in spec["transitions"]}
    assert by_id["footer-link"]["source_chunk"] == "css/footer.css"
    assert by_id["external-link"]["source_chunk"] == "inline init"
    assert by_id["footer-link"]["animation"] == mapped["animation"]
    assert by_id["footer-link"]["reference_frames"] == mapped["reference_frames"]
    assert by_id["external-link"]["reference_frames"] == unmapped["reference_frames"]
    assert by_id["authored-scroll"] == authored_scroll


def test_authored_interactions_are_not_rewritten_and_fail_when_uncaptured(
    tmp_path: Path,
) -> None:
    authored = {
        "source": "human-review",
        "interactions": [{"id": "manual-hover", "trigger": "hover", "target": ".same"}],
    }
    proc, ref_dir, _ = _run(
        tmp_path,
        [{"name": "static-hover", "triggerType": "hover", "selector": ".missing"}],
        identical=True,
        transition_spec={
            "source": "ui_clone.extraction_artifacts",
            "placeholder": True,
            "transitions": [{"id": "auto-hover-0"}],
        },
        interactions=authored,
    )
    assert proc.returncode != 0
    assert json.loads((ref_dir / "interactions-detected.json").read_text()) == authored
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["unsupported"] == [
        {
            "region": "manual-hover",
            "triggerType": "hover",
            "source": "interactions-detected.json",
        }
    ]


def test_derives_regions_from_transition_spec_before_capture(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "derived-button",
                        "trigger": "hover",
                        "selector": ".derived",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    proc, _ = _run_existing_ref(tmp_path, ref_dir)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert payload["regions"][0]["name"] == "derived-button"
    assert payload["regions"][0]["artifacts"] == {
        "idle": "clip/ref/00-derived-button-idle.png",
        "active": "clip/ref/00-derived-button-active.png",
    }


def test_capture_is_capped_at_twenty_unique_hover_regions(tmp_path: Path) -> None:
    regions = [
        {
            "name": f"hover-{index}",
            "triggerType": "hover",
            "selector": f".hover-{index}",
        }
        for index in range(21)
    ]
    proc, ref_dir, _ = _run(tmp_path, regions)
    assert proc.returncode == 0
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert len(payload["regions"]) == 20
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["counts"]["captured"] == 20
    assert summary["skipped"][-1] == {
        "region": "hover-20",
        "selector": ".hover-20",
        "triggerType": "hover",
        "reason": "capture limit 20",
    }


def _load_capture_module() -> ModuleType:
    key = "_capture_region_artifacts_test_module"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, str(SCRIPT))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def test_promoted_transition_ids_remain_unique_across_preserved_and_captured_rows(
    tmp_path: Path,
) -> None:
    module = _load_capture_module()
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "transition-spec.json").write_text(
        json.dumps(
            {
                "source": "human-review",
                "placeholder": False,
                "transitions": [
                    {
                        "id": "00-header-nav-link",
                        "trigger": "hover",
                        "target": ".nav__item",
                        "animation": {"type": "css-hover"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    captured = [
        {
            "region": "header-nav-link",
            "selector": "a.nav__link",
            "triggerType": "hover",
            "artifacts": {
                "idle": "clip/ref/00-header-nav-link-idle.png",
                "active": "clip/ref/00-header-nav-link-active.png",
            },
            "observation": {
                "changedProperties": ["renderedPixels"],
                "from": {"renderedPixels": "idle"},
                "to": {"renderedPixels": "active"},
            },
        }
    ]

    module._promote_transition_spec(
        ref_dir,
        "https://example.test",
        "collision-test",
        captured,
        [],
    )

    spec = json.loads((ref_dir / "transition-spec.json").read_text())
    ids = [transition["id"] for transition in spec["transitions"]]
    assert ids == ["00-header-nav-link", "00-header-nav-link-2"]
    assert len(ids) == len(set(ids))


def test_hover_target_resolution_forces_instant_scroll() -> None:
    """Smooth-scroll pages must not be hit-tested before scrolling settles."""
    module = _load_capture_module()
    script = module._resolve_target_js('".button"', "region-0")

    assert script.count("behavior:'instant'") == 2
    assert "requestAnimationFrame" in script
    assert script.index("requestAnimationFrame") < script.index("document.elementFromPoint")


def test_hover_capture_recenters_marked_target_after_release_scroll_drift(
    tmp_path: Path,
) -> None:
    """Delayed scroll-state work must not leave the crop outside the viewport."""
    proc, ref_dir, calls = _run(
        tmp_path,
        [
            {
                "name": "button",
                "triggerType": "hover",
                "selector": ".button",
            }
        ],
        delayed_scroll_drift=True,
    )

    assert proc.returncode == 0, proc.stderr
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["counts"]["captured"] == 1
    settle_calls = [
        call
        for call in calls
        if "eval" in call and "data-uiclone-region" in call[-1] and "scrollIntoView" in call[-1]
    ]
    assert len(settle_calls) >= 2


def test_failed_recapture_preserves_prior_region_artifacts(tmp_path: Path) -> None:
    artifacts = {
        "idle": "clip/ref/00-button-idle.png",
        "active": "clip/ref/00-button-active.png",
    }
    region = {
        "name": "button",
        "triggerType": "hover",
        "selector": ".button",
        "artifacts": artifacts,
    }
    proc, ref_dir, _ = _run(
        tmp_path,
        [region],
        no_change=True,
        prior_artifacts=True,
    )

    assert proc.returncode == 5
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert payload["regions"] == [region]
    for relative in artifacts.values():
        assert (ref_dir / relative).read_bytes() == b"prior-artifact"


def test_identity_and_colour_notation_are_not_treated_as_change() -> None:
    """Raw string diffs invent transitions that do not exist.

    getComputedStyle reports an untransformed element as "none" or as the
    identity matrix depending on the property mix, and colours with or without
    an alpha channel.
    """
    module = _load_capture_module()
    assert (
        module._changed_properties(
            {"transform": "none", "color": "rgb(0, 0, 0)"},
            {"transform": "matrix(1, 0, 0, 1, 0, 0)", "color": "rgba(0, 0, 0, 1)"},
        )
        == []
    )


def test_real_change_still_registers_after_normalisation() -> None:
    module = _load_capture_module()
    assert module._changed_properties(
        {"transform": "none", "opacity": "1"},
        {"transform": "matrix(1.1, 0, 0, 1.1, 0, 0)", "opacity": "1"},
    ) == ["transform"]


_SCROLL_REGION = {
    "name": "sticky-header",
    "triggerType": "scroll",
    "selector": ".hdr",
}
_AUTO_SPEC = {
    "source": "ui_clone.extraction_artifacts",
    "placeholder": True,
    "transitions": [{"id": "auto-scroll-0"}],
}


def test_scroll_ladder_reports_a_scrubbed_progression(tmp_path: Path) -> None:
    """A value that keeps moving with the offset is not a threshold toggle.

    progression is written into transition-spec.json and consumed as truth, so
    calling a scrub a threshold would tell a clone to build the wrong mechanism.
    """
    proc, ref_dir, _ = _run(
        tmp_path,
        [_SCROLL_REGION],
        transition_spec=_AUTO_SPEC,
        scroll_mode="scrubbed",
    )
    assert proc.returncode == 0
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    observation = summary["captured"][0]["observation"]
    assert observation["progression"] == "scrubbed"
    assert observation["ladderPcts"] == [0, 10, 25, 50, 75, 90, 100]


def test_scroll_ladder_threshold_progression(tmp_path: Path) -> None:
    proc, ref_dir, _ = _run(tmp_path, [_SCROLL_REGION], transition_spec=_AUTO_SPEC)
    assert proc.returncode == 0
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    observation = summary["captured"][0]["observation"]
    assert observation["progression"] == "threshold"
    assert observation["changedProperties"] == ["boxShadow"]


def test_scroll_ladder_captures_midpoint_between_adjacent_visible_rungs(
    tmp_path: Path,
) -> None:
    proc, ref_dir, _ = _run(
        tmp_path,
        [_SCROLL_REGION],
        transition_spec=_AUTO_SPEC,
        adjacent_scroll_only=True,
    )
    assert proc.returncode == 0
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    captured = summary["captured"][0]

    assert sorted(captured["artifacts"]) == ["after", "before", "mid"]
    assert captured["observation"]["midPct"] == 5
    assert (ref_dir / captured["artifacts"]["mid"]).is_file()


def test_offscreen_region_yields_no_pixel_evidence(tmp_path: Path) -> None:
    """An element taller than the viewport is clipped to a different slice at
    every rung, so its frames always differ while nothing is animating."""
    proc, ref_dir, _ = _run(
        tmp_path,
        [_SCROLL_REGION],
        transition_spec=_AUTO_SPEC,
        offscreen=True,
    )
    assert proc.returncode == 0
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    observation = summary["captured"][0]["observation"]
    assert observation["pixelComparable"] is False
    assert observation["pixelCorroborated"] is False


def test_virtualised_scroll_engine_is_a_probe_failure(tmp_path: Path) -> None:
    """window.scrollTo does not move a hijacked timeline, so probing measures
    nothing; the candidate must survive rather than be pruned as inert."""
    proc, ref_dir, _ = _run(
        tmp_path,
        [_SCROLL_REGION],
        transition_spec=_AUTO_SPEC,
        scroll_engine={"detected": {"lenis": {"matches": 4}}},
    )
    assert proc.returncode == 4
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert "virtualised by lenis" in summary["skipped"][0]["reason"]
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert [region["selector"] for region in payload["regions"]] == [".hdr"]


def test_capture_does_not_launder_an_authored_spec_into_bridge_ownership(
    tmp_path: Path,
) -> None:
    """One unrelated capture must not relabel somebody else's spec as ours.

    If it did, the next run would read the spec as bridge-owned and quietly
    discharge the dispatch-only obligations the author left in it.
    """
    authored = {
        "source": "agent-authored",
        "placeholder": False,
        "transitions": [{"id": "authored-scroll", "trigger": "scroll", "target": ".authored"}],
    }
    proc, ref_dir, _ = _run(
        tmp_path,
        [
            {"name": "real-hover", "triggerType": "hover", "selector": ".button"},
            {
                "name": "authored-scroll",
                "triggerType": "scroll",
                "selector": ".authored",
                "dispatchOnly": True,
            },
        ],
        transition_spec=authored,
    )
    assert proc.returncode != 0
    spec = json.loads((ref_dir / "transition-spec.json").read_text())
    assert spec["source"] == "agent-authored"
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert {entry["region"] for entry in summary["unsupported"]} == {"authored-scroll"}
