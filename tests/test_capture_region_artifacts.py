"""Tests for the real-hover region artifact capture bridge."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from PIL import Image

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

with calls.with_suffix(".env.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({key: os.environ.get(key) for key in (
        "AGENT_BROWSER_NAMESPACE", "AGENT_BROWSER_COLOR_SCHEME"
    )}) + "\\n")
args = sys.argv[1:]
session = args[args.index("--session") + 1]
command_index = args.index("--session") + 2
command = args[command_index]
rest = args[command_index + 1:]
if " ".join([command, *rest]) == os.environ.get("FAKE_SETUP_FAIL"):
    print(os.environ.get("FAKE_SETUP_ERROR", "setup failed"), file=sys.stderr)
    sys.exit(1)
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
elif command == "open":
    print(json.dumps({"success": True, "data": {"url": os.environ.get("FAKE_FINAL_URL", "https://example.test")}}))
elif command == "eval":
    origin = os.environ.get("FAKE_EVAL_ORIGIN", os.environ.get("FAKE_FINAL_URL", "https://example.test"))
    if state.get("screenshot_taken") and os.environ.get("FAKE_SCREENSHOT_DRIFT"):
        origin = "https://wrong.test"
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
    if "maxScroll:" in script:
        print(json.dumps({"success": True, "data": {"origin": origin, "result": {
            "found": True, "scrollY": float(state.get(scroll_key, 0)),
            "scrollHeight": 2000, "maxScroll": 1000, "viewportHeight": 1000,
        }}}))
        sys.exit(0)
    found = ".missing" not in script
    matches = 1 if found else 0
    gated = (
        os.environ.get("FAKE_GATED_TARGET") == "1"
        and "scrollIntoView" in script
        and not state.get(session + ":adaptive-unlocked")
    )
    if gated:
        found = False
        matches = 1
        state[scroll_key] = 1000
        state_path.write_text(json.dumps(state), encoding="utf-8")
        scrolled = 1000
    if os.environ.get("FAKE_AFFECTED_OUTSIDE") == "1" and "activation.contains(" in script:
        # The affected selector is rendered in the document, but not inside
        # the activated region: the observation target cannot be pinned.
        found = False
        matches = 1
    if os.environ.get("FAKE_DESCENDANT_ON_HOVER") == "1" and ".menu" in script:
        # The descendant is mounted only while the activation is hovered (a
        # hover-rendered menu): absent at idle, present after the hover.
        found = bool(state.get(session, False))
        matches = 1 if found else 0
    result = {
        "found": found,
        "activationFound": True,
        "matches": matches,
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
    if gated:
        result.update({
            "blockedBeyondExtent": True,
            "beyondExtent": 1,
            "maxBlockedBottom": 1800,
            "scrollHeight": 1000,
        })
    if found and "const tracked=" in script:
        active = state.get(session, False) and os.environ.get("FAKE_NO_CHANGE") != "1"
        descendant_style = (
            os.environ.get("FAKE_DESCENDANT_STYLE") == "1"
            and "data-uiclone-observation" in script
        )
        if descendant_style:
            result.update({
                "styles": {
                    "backgroundColor": "rgb(200, 0, 0)" if active else "rgb(0, 0, 0)",
                },
                "transitionProperty": "background-color",
                "transitionDuration": "0.15s",
                "transitionTimingFunction": "ease-in-out",
            })
        else:
            result.update({
                "styles": {
                    "transform": "matrix(1.1, 0, 0, 1.1, 0, 0)" if active else "none",
                    "boxShadow": (
                        "rgba(0, 0, 0, 0.07) 0px 4px 12px 0px"
                        if scrolled >= 100 and os.environ.get("FAKE_NO_CHANGE") != "1"
                        else "none"
                    ),
                    "opacity": str(round(scrolled / 1000, 2)) if os.environ.get("FAKE_SCROLL_MODE") == "scrubbed" else "1",
                },
                "transitionProperty": "transform",
                "transitionDuration": "0.2s",
                "transitionTimingFunction": "ease-out",
            })
    print(json.dumps({"success": True, "data": {"origin": origin, "result": result}}))
elif command == "hover":
    state[session] = bool(rest and rest[0] != "body")
    state_path.write_text(json.dumps(state), encoding="utf-8")
elif command == "scroll":
    if os.environ.get("FAKE_GATED_NEVER_UNLOCK") != "1":
        state[session + ":adaptive-unlocked"] = True
    state[session + ":scroll"] = 1000
    state_path.write_text(json.dumps(state), encoding="utf-8")
elif (
    command == "wait"
    and rest == ["300"]
    and os.environ.get("FAKE_DELAYED_SCROLL_DRIFT") == "1"
):
    state[session + ":delayed-scroll-drift"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
elif command == "screenshot":
    state["screenshot_taken"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
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
    setup_fail: str | None = None,
    browser_env: dict[str, str] | None = None,
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
    descendant_style: bool = False,
    affected_outside: bool = False,
    descendant_on_hover: bool = False,
    gated_target: bool = False,
    gated_never_unlock: bool = False,
    prior_artifacts: bool = False,
    prior_artifact_bytes: bytes | None = None,
    session: str = "capture",
    timeout: int = 20,
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
                path.write_bytes(prior_artifact_bytes or b"prior-artifact")
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
    env.pop("AGENT_BROWSER_NAMESPACE", None)
    env.pop("AGENT_BROWSER_COLOR_SCHEME", None)
    env.update(browser_env or {})
    if setup_fail:
        env["FAKE_SETUP_FAIL"] = setup_fail
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
    if descendant_style:
        env["FAKE_DESCENDANT_STYLE"] = "1"
    if affected_outside:
        env["FAKE_AFFECTED_OUTSIDE"] = "1"
    if descendant_on_hover:
        env["FAKE_DESCENDANT_ON_HOVER"] = "1"
    if gated_target:
        env["FAKE_GATED_TARGET"] = "1"
    if gated_never_unlock:
        env["FAKE_GATED_NEVER_UNLOCK"] = "1"
    args = [
        sys.executable,
        str(SCRIPT),
        "https://example.test",
        session,
        str(ref_dir),
    ]
    if reuse_session:
        args.append("--reuse-session")
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
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
    env.pop("AGENT_BROWSER_NAMESPACE", None)
    env.pop("AGENT_BROWSER_COLOR_SCHEME", None)
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
    calls = (
        [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
        if calls_path.is_file()
        else []
    )
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
    assert commands.count("close") == 2
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
    assert [call[2] for call in calls] == ["close", "get", "set", "set", "open", "close"]
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
    assert [call[2] for call in calls].count("close") == 2
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


def test_generated_hover_transition_preserves_distinct_affected_target(
    tmp_path: Path,
) -> None:
    proc, ref_dir, _ = _run(
        tmp_path,
        [
            {
                "name": "card-title",
                "triggerType": "hover",
                "selector": ".card",
                "dispatchOnly": True,
            },
            {
                "name": "same-target",
                "triggerType": "hover",
                "selector": ".same",
                "dispatchOnly": True,
            },
        ],
        transition_spec={
            "source": "ui_clone.extraction_artifacts",
            "placeholder": True,
            "transitions": [],
        },
        interactions={
            "source": "ui_clone.extraction_artifacts",
            "interactions": [
                {"id": "card", "trigger": "hover", "target": ".card"},
                {"id": "same", "trigger": "hover", "target": ".same"},
            ],
        },
        hover_css_rules={
            "source": "scripts/extract/capture-hover.sh",
            "rules": [
                {
                    "selector": ".card:hover .title",
                    "activation": ".card",
                    "affected": ".card .title",
                },
                {
                    "selector": ".same:hover",
                    "activation": ".same",
                    "affected": ".same",
                },
            ],
        },
    )

    assert proc.returncode == 0, proc.stderr
    spec = json.loads((ref_dir / "transition-spec.json").read_text())
    by_target = {transition["target"]: transition for transition in spec["transitions"]}
    assert by_target[".card"]["affectedTarget"] == ".card .title"
    assert "affectedTarget" not in by_target[".same"]
    assert (ref_dir / "hover-css-rules.json").stat().st_mtime_ns >= (
        ref_dir / "interactions-detected.json"
    ).stat().st_mtime_ns


def test_generated_hover_transition_observes_affected_descendant_styles(
    tmp_path: Path,
) -> None:
    proc, ref_dir, calls = _run(
        tmp_path,
        [
            {
                "name": "card-title",
                "triggerType": "hover",
                "selector": ".card",
                "dispatchOnly": True,
            }
        ],
        transition_spec={
            "source": "ui_clone.extraction_artifacts",
            "placeholder": True,
            "transitions": [],
        },
        hover_css_rules={
            "rules": [
                {
                    "selector": ".card:hover .title",
                    "activation": ".card",
                    "affected": ".card .title",
                }
            ]
        },
        descendant_style=True,
    )

    assert proc.returncode == 0, proc.stderr
    spec = json.loads((ref_dir / "transition-spec.json").read_text())
    transition = spec["transitions"][0]
    assert transition["target"] == ".card"
    assert transition["affectedTarget"] == ".card .title"
    assert transition["animation"] == {
        "type": "css-hover",
        "property": "backgroundColor",
        "changedProperties": ["backgroundColor"],
        "from": {"backgroundColor": "rgb(0, 0, 0)"},
        "to": {"backgroundColor": "rgb(200, 0, 0)"},
        "duration": "0.15s",
        "easing": "ease-in-out",
        "pixelCorroborated": True,
    }
    assert any(call[2] == "hover" and "data-uiclone-region" in call[3] for call in calls)
    evals = [call[-1] for call in calls if call[2] == "eval"]
    assert any(
        json.dumps(".card .title") in script and "activation.contains(node)" in script
        for script in evals
    )
    assert any(
        "data-uiclone-observation" in script and "getComputedStyle(observed)" in script
        for script in evals
    )


def test_hover_pseudo_element_target_uses_queryable_owner(tmp_path: Path) -> None:
    module = _load_capture_module()
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "hover-css-rules.json").write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "selector": ".doc-link:hover .icon::before",
                        "activation": ".doc-link",
                        "affected": ":is(.doc-link, .doc-link.active) .icon::before",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert module._hover_rule_affected_targets(ref_dir) == {
        ".doc-link": ":is(.doc-link, .doc-link.active) .icon"
    }


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
    # The maximum-cap fixture performs 20 complete hover captures (more than
    # 300 fake browser process calls), so it legitimately exceeds the helper's
    # default timeout on a loaded serial CI run.
    proc, ref_dir, _ = _run(tmp_path, regions, timeout=60)
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


def test_live_transition_id_stays_stable_after_regions_are_rederived(
    tmp_path: Path,
) -> None:
    stable_id = "00-auto-hover-0"
    proc, ref_dir, _ = _run(
        tmp_path,
        [
            {
                "name": stable_id,
                "triggerType": "hover",
                "selector": ".card",
                "dispatchOnly": True,
            }
        ],
        transition_spec={
            "source": "ui_clone.extraction_artifacts",
            "placeholder": True,
            "transitions": [],
        },
    )
    assert proc.returncode == 0, proc.stderr
    first_spec = json.loads((ref_dir / "transition-spec.json").read_text())
    assert [transition["id"] for transition in first_spec["transitions"]] == [stable_id]

    (ref_dir / "regions.json").unlink()
    rerun_dir = tmp_path / "rerun"
    rerun_dir.mkdir()
    rerun, _ = _run_existing_ref(rerun_dir, ref_dir)
    assert rerun.returncode == 0, rerun.stderr
    second_spec = json.loads((ref_dir / "transition-spec.json").read_text())
    assert [transition["id"] for transition in second_spec["transitions"]] == [stable_id]


def test_hover_target_resolution_forces_instant_scroll() -> None:
    """Smooth-scroll pages must not be hit-tested before scrolling settles."""
    module = _load_capture_module()
    script = module._resolve_target_js('".button"', "region-0")

    assert script.count("behavior:'instant'") == 2
    assert "requestAnimationFrame" in script
    assert script.index("requestAnimationFrame") < script.index("document.elementFromPoint")


def test_owned_capture_adaptively_reaches_target_beyond_document_extent(
    tmp_path: Path,
) -> None:
    proc, ref_dir, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        gated_target=True,
    )

    assert proc.returncode == 0, proc.stderr
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["counts"]["captured"] == 1
    assert summary["unsupported"] == []
    assert summary["captured"][0]["observation"]["adaptiveScrollTraversal"] is True
    assert any(call[2:] == ["scroll", "down", "600"] for call in calls)
    assert any(call[2:] == ["wait", "1200"] for call in calls)


def test_reused_capture_does_not_mutate_caller_with_adaptive_traversal(
    tmp_path: Path,
) -> None:
    proc, ref_dir, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        gated_target=True,
        reuse_session=True,
    )

    assert proc.returncode != 0
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["captured"] == []
    assert summary["skipped"][0]["reason"] == (
        "selector matches 1 elements but none are hoverable"
    )
    assert not any(call[2] == "scroll" for call in calls)


def test_adaptive_traversal_stays_fail_closed_when_target_remains_blocked(
    tmp_path: Path,
) -> None:
    proc, ref_dir, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        gated_target=True,
        gated_never_unlock=True,
    )

    assert proc.returncode != 0
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["captured"] == []
    assert summary["status"] == "fail"
    assert summary["skipped"][0]["reason"] == (
        "selector matches 1 elements but none are hoverable"
    )
    regions = json.loads((ref_dir / "regions.json").read_text())
    assert regions["regions"] == [
        {"name": "button", "triggerType": "hover", "selector": ".button"}
    ]
    scroll_calls = [call for call in calls if call[2] == "scroll"]
    assert 1 <= len(scroll_calls) <= 3


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


def test_bridge_recapture_retires_identical_prior_hover_frames(tmp_path: Path) -> None:
    """A fresh negative can retire only the bridge's own stale hover proof."""
    artifacts = {
        "idle": "clip/ref/00-card-idle.png",
        "active": "clip/ref/00-card-active.png",
    }
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="PNG")
    proc, ref_dir, _ = _run(
        tmp_path,
        [{"name": "card", "triggerType": "hover", "selector": ".card", "artifacts": artifacts}],
        region_source="scripts/extract/capture-region-artifacts.py",
        transition_spec={
            "source": "scripts/extract/capture-region-artifacts.py",
            "placeholder": False,
            "transitions": [
                {
                    "id": "00-card",
                    "trigger": "hover",
                    "target": ".card",
                    "bundle_branch": "live-capture: agent-browser CDP hover",
                    "reference_frames": list(artifacts.values()),
                }
            ],
        },
        no_change=True,
        prior_artifacts=True,
        prior_artifact_bytes=image.getvalue(),
    )

    assert proc.returncode == 0, proc.stderr
    regions = json.loads((ref_dir / "regions.json").read_text())
    spec = json.loads((ref_dir / "transition-spec.json").read_text())
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert regions["regions"] == []
    assert spec["transitions"] == []
    assert summary["skipped"][0]["resolution"] == "absence-measured"
    assert summary["autoSpec"] is True


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


def test_transform_matrix_serialization_jitter_is_not_a_hover_change() -> None:
    module = _load_capture_module()
    assert module._changed_properties(
        {"transform": "matrix(1.06999, 0, 0, 1.06999, 0, 0)"},
        {"transform": "matrix(1.07, 0, 0, 1.07, 0, 0)"},
    ) == []
    assert module._changed_properties(
        {"transform": "matrix(1.07, 0, 0, 1.07, 0, 0)"},
        {"transform": "matrix(1.08, 0, 0, 1.08, 0, 0)"},
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


@pytest.mark.parametrize(
    "browser_env", [{}, {"AGENT_BROWSER_NAMESPACE": "custom", "AGENT_BROWSER_COLOR_SCHEME": "dark"}]
)
def test_fresh_bridge_configures_capture_environment(
    tmp_path: Path, browser_env: dict[str, str]
) -> None:
    proc, _, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        browser_env=browser_env,
    )
    assert proc.returncode == 0, proc.stderr
    scheme = browser_env.get("AGENT_BROWSER_COLOR_SCHEME", "light")
    assert [call[2:] for call in calls[:6]] == [
        ["close"],
        ["get", "url"],
        ["set", "viewport", "1440", "900"],
        ["set", "media", scheme],
        ["open", "https://example.test", "--json"],
        ["wait", "3500"],
    ]
    checksum = subprocess.run(
        ["cksum"], input="capture", text=True, capture_output=True, check=True, timeout=5
    ).stdout.split()[0]
    expected = {
        "AGENT_BROWSER_NAMESPACE": browser_env.get(
            "AGENT_BROWSER_NAMESPACE", f"ui-clone-{checksum}"
        ),
        "AGENT_BROWSER_COLOR_SCHEME": browser_env.get("AGENT_BROWSER_COLOR_SCHEME"),
    }
    environments = [
        json.loads(line) for line in (tmp_path / "calls.env.jsonl").read_text().splitlines()
    ]
    assert environments and all(env == expected for env in environments)
    assert calls[-1][2:] == ["close"]


def test_fresh_bridge_bounds_long_derived_session_name(tmp_path: Path) -> None:
    long_session = "feconf-fresh-audit-" + "한글세션" * 4
    proc, ref_dir, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        session=long_session,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    derived = summary["session"]
    assert derived.startswith("ra-feconf-f-")
    assert len(derived.encode("utf-8")) <= 24
    assert calls and all(call[1] == derived for call in calls)
    assert all(call[1] != long_session for call in calls)


@pytest.mark.parametrize(
    "step", ["close", "get url", "set viewport 1440 900", "set media light", "wait 3500"]
)
def test_bridge_setup_failure_does_not_capture(tmp_path: Path, step: str) -> None:
    proc, ref_dir, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        setup_fail=step,
    )
    assert proc.returncode != 0
    assert not any(call[2] in {"eval", "screenshot", "hover"} for call in calls)
    assert calls[-1][2:] == ["close"]
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["status"] == "fail"
    assert summary["captured"] == []


def test_bridge_setup_failure_preserves_agent_browser_diagnostic(tmp_path: Path) -> None:
    diagnostic = (
        "Session name is too long. Socket path would be 104 bytes (max 103). "
        "Use a shorter session name or socket directory."
    )
    proc, ref_dir, _ = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        setup_fail="close",
        browser_env={"FAKE_SETUP_ERROR": diagnostic},
    )
    assert proc.returncode == 2
    assert diagnostic in proc.stderr
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["skipped"][0]["diagnostic"] == diagnostic
    assert summary["skipped"][0]["returnCode"] == 1
    assert summary["skipped"][0]["command"] == ["close"]


@pytest.mark.parametrize(
    "browser_env", [{}, {"AGENT_BROWSER_NAMESPACE": "caller", "AGENT_BROWSER_COLOR_SCHEME": "dark"}]
)
def test_reused_bridge_preserves_browser_environment(
    tmp_path: Path, browser_env: dict[str, str]
) -> None:
    proc, _, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        reuse_session=True,
        browser_env=browser_env,
    )
    assert proc.returncode == 0
    assert all(call[1] == "capture" and call[2] not in {"open", "close", "set"} for call in calls)
    environments = [
        json.loads(line) for line in (tmp_path / "calls.env.jsonl").read_text().splitlines()
    ]
    expected = {
        key: browser_env.get(key)
        for key in ("AGENT_BROWSER_NAMESPACE", "AGENT_BROWSER_COLOR_SCHEME")
    }
    assert environments and all(env == expected for env in environments)


def test_bridge_restores_scoped_environment_after_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    module = _load_capture_module()
    ref_dir = tmp_path / "ref"
    _write_regions(ref_dir, [{"name": "button", "triggerType": "hover", "selector": ".button"}])
    monkeypatch.delenv("AGENT_BROWSER_NAMESPACE", raising=False)
    original_env = os.environ.copy()

    def fail_capture(*args: object) -> int:
        assert module._BROWSER_ENV.get()["AGENT_BROWSER_NAMESPACE"].startswith("ui-clone-")
        assert os.environ == original_env
        raise RuntimeError("capture interrupted")

    monkeypatch.setattr(module, "_capture_main", fail_capture)
    with pytest.raises(RuntimeError, match="capture interrupted"):
        module.main(["https://example.test", "capture", str(ref_dir)])
    assert module._BROWSER_ENV.get() is None
    assert os.environ == original_env


@pytest.mark.parametrize(
    "suffix", ["::before", "::after", ":before", ":after", ":first-letter", ":first-line"]
)
def test_legacy_pseudo_element_owner(suffix: str) -> None:
    module = _load_capture_module()
    assert module._observable_hover_target(f".icon{suffix}") == ".icon"
    assert module._observable_hover_target(".icon:first-child") == ".icon:first-child"


@pytest.mark.parametrize("drift", ["eval", "screenshot"])
def test_bridge_rejects_wrong_origin_before_publishing(tmp_path: Path, drift: str) -> None:
    browser_env = (
        {"FAKE_EVAL_ORIGIN": "https://wrong.test"}
        if drift == "eval"
        else {"FAKE_SCREENSHOT_DRIFT": "1"}
    )
    proc, ref_dir, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        browser_env=browser_env,
    )
    assert proc.returncode != 0
    assert calls[-1][2:] == ["close"]
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["status"] == "fail"
    assert not (ref_dir / "transition-spec.json").exists()


def test_bridge_accepts_its_recorded_redirect(tmp_path: Path) -> None:
    proc, ref_dir, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        browser_env={"FAKE_FINAL_URL": "https://www.example.test/final"},
    )
    assert proc.returncode == 0, proc.stderr
    assert ["open", "https://example.test", "--json"] in [call[2:] for call in calls]
    receipt = json.loads((ref_dir / "capture-region-artifacts-navigation.json").read_text())
    assert receipt["finalUrl"] == "https://www.example.test/final"
    assert receipt["session"] == "capture-region-artifacts"


@pytest.mark.parametrize("mismatch", [None, "session", "namespace", "requestedUrl"])
def test_reused_bridge_requires_matching_redirect_receipt(
    tmp_path: Path, mismatch: str | None
) -> None:
    receipt = {
        "requestedUrl": "https://example.test",
        "finalUrl": "https://www.example.test/final",
        "session": "capture",
        "namespace": "caller-space",
    }
    if mismatch:
        receipt[mismatch] = "wrong"
    proc, _, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        reuse_session=True,
        browser_env={
            "AGENT_BROWSER_NAMESPACE": "caller-space",
            "FAKE_FINAL_URL": receipt["finalUrl"],
        },
        source_files={"capture-navigation.json": json.dumps(receipt)},
    )
    assert proc.returncode == (2 if mismatch else 0), proc.stderr
    assert all(call[2] not in {"open", "close"} for call in calls)


@pytest.mark.parametrize(
    ("selector", "owner"),
    [
        ('[data-label="::before"]::after', '[data-label="::before"]'),
        ('[data-label="icon:before"]:after', '[data-label="icon:before"]'),
        ('[data-label="a  b"]::before', '[data-label="a  b"]'),
        (r'[data-label="a\"::before"]::after', r'[data-label="a\"::before"]'),
        (r'.icon\:\:before::after', r'.icon\:\:before'),
        (':is(.icon, [data-label="::before"]):first-child::after',
         ':is(.icon, [data-label="::before"]):first-child'),
        ('[data-label="::before"]:not(.disabled)', '[data-label="::before"]:not(.disabled)'),
        ('slot::slotted(:is(.a, .b))', 'slot'),
    ],
)
def test_hover_owner_preserves_selector_literals(selector: str, owner: str) -> None:
    assert _load_capture_module()._observable_hover_target(selector) == owner


@pytest.mark.parametrize(
    ("summary", "overrides", "expected"),
    [
        ({"checked": True, "durationMs": 8000}, {}, 8500),
        ({"checked": False, "durationMs": 8000}, {}, 3500),
        ({"checked": True, "durationMs": 100}, {}, 3500),
        ({"checked": True, "durationMs": "bad"}, {}, 3500),
        ([], {}, 3500),
        ({"checked": True, "durationMs": 8000},
         {"CAPTURE_DERIVED_READY_WAIT_MS": "9000", "CAPTURE_DERIVED_READY_BUFFER_MS": "100"},
         9000),
        ({"checked": True, "durationMs": 8000},
         {"CAPTURE_DERIVED_READY_WAIT_MS": "bad", "CAPTURE_DERIVED_READY_BUFFER_MS": "-1"},
         8500),
    ],
)
def test_bridge_waits_for_checked_splash_before_probing(
    tmp_path: Path, summary: object, overrides: dict[str, str], expected: int,
) -> None:
    proc, _, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        browser_env={"STATES_PREFIX": "alternate-states", **overrides},
        source_files={"alternate-states/splash/summary.json": json.dumps(summary)},
    )
    assert proc.returncode == 0, proc.stderr
    assert calls[5][2:] == ["wait", str(expected)]
    assert calls[6][2] == "mouse"


def test_reused_bridge_does_not_repeat_splash_wait(tmp_path: Path) -> None:
    proc, _, calls = _run(
        tmp_path,
        [{"name": "button", "triggerType": "hover", "selector": ".button"}],
        reuse_session=True,
        source_files={"states/splash/summary.json": '{"checked":true,"durationMs":8000}'},
    )
    assert proc.returncode == 0, proc.stderr
    assert calls[0][2] == "mouse"
    assert not any(call[2:] == ["wait", "8500"] for call in calls)


def test_opener_navigation_is_a_probe_failure_not_measured_absence() -> None:
    """An opener that leaves the page proves nothing about the region.

    The escape used to fold into the generic "none are hoverable" skip, which
    reads as a probe failure but hides the cause; worse, any reason that ever
    reached RESOLVED_ABSENCE_REASONS would retire the region from regions.json
    on the strength of a measurement that never happened.
    """
    module = _load_capture_module()
    reason = "opener control navigated instead of revealing"
    assert module._is_probe_failure(reason) is True
    assert module._is_resolved_absence(reason) is False
    assert reason not in module.RESOLVED_ABSENCE_REASONS


def test_navigating_opener_walks_history_back_and_reports_restoration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walk must put the document back before the next region is probed."""
    module = _load_capture_module()
    calls: list[tuple[str, ...]] = []
    urls = iter(["https://ref.test/", "https://ref.test/search", "https://ref.test/"])

    def fake_eval(session: str, javascript: str) -> dict[str, object]:
        if "location.href" in javascript:
            return {"found": True, "url": next(urls)}
        if "openers" in javascript or "controls" in javascript:
            return {"openers": [], "controls": [{"name": "c0", "mode": "click", "path": "button"}]}
        return {"found": False, "matches": 1}

    def fake_run(session: str, *args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module, "_eval", fake_eval)
    monkeypatch.setattr(module, "_run", fake_run)

    resolved, opener = module._open_then_resolve("s", '".t"', "m", 0)

    assert resolved["navigated"] is True
    assert resolved["restored"] is True
    assert opener is None
    assert ("back",) in calls


def test_absent_descendant_target_falls_back_to_observing_the_activation(
    tmp_path: Path,
) -> None:
    """A rule's absent descendant retires the descendant, not the activation.

    navercorp.com/tech/innovation ships `.header .nav__link:hover .en` next to
    `.header .nav__link:hover{font-weight:600}`; `.en` is not rendered, so the
    prober used to skip the whole activation as measured absence and the real,
    visible hover on `.nav__link` went unverified while the gate stayed green.
    """
    proc, ref_dir, _ = _run(
        tmp_path,
        [{"name": "nav-link", "triggerType": "hover", "selector": ".nav-link"}],
        hover_css_rules={
            "rules": [
                {
                    "selector": ".nav-link:hover .missing",
                    "activation": ".nav-link",
                    "affected": ".nav-link .missing",
                }
            ]
        },
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert [region["name"] for region in payload["regions"]] == ["nav-link"]
    assert sorted(payload["regions"][0]["artifacts"]) == ["active", "idle"]
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["skipped"] == []
    assert summary["counts"]["captured"] == 1
    observation = summary["captured"][0]["observation"]
    assert observation["changedProperties"] == ["transform"]
    # The delta belongs to the activation: the spec must not attribute it to a
    # descendant that is not in the document.
    assert observation["affectedTargetAbsent"] == ".nav-link .missing"
    spec = json.loads((ref_dir / "transition-spec.json").read_text())
    transition = spec["transitions"][0]
    assert transition["target"] == ".nav-link"
    assert "affectedTarget" not in transition
    assert transition["affectedTargetAbsent"] == ".nav-link .missing"


def test_absent_descendant_with_unchanged_activation_is_still_retired(
    tmp_path: Path,
) -> None:
    """Falling back must not turn a genuine absence into a permanent blocker.

    When the descendant is not rendered AND the activation itself shows no
    hover delta, the browser has answered: the candidate is retired from
    regions.json and its audit row is tagged as measured absence.
    """
    proc, ref_dir, _ = _run(
        tmp_path,
        [{"name": "card", "triggerType": "hover", "selector": ".card"}],
        hover_css_rules={
            "rules": [
                {
                    "selector": ".card:hover .missing",
                    "activation": ".card",
                    "affected": ".card .missing",
                }
            ]
        },
        no_change=True,
    )

    payload = json.loads((ref_dir / "regions.json").read_text())
    assert payload["regions"] == []
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["captured"] == []
    assert len(summary["skipped"]) == 1
    row = summary["skipped"][0]
    assert row["region"] == "card"
    assert row["resolution"] == "absence-measured"
    assert "not present in document" in row["reason"]
    assert "no observable change" in row["reason"]
    assert proc.returncode != 0


def test_auto_inventory_with_only_measured_absences_completes_with_bound_receipt(
    tmp_path: Path,
) -> None:
    """A fully measured auto false-positive is evidence, not an empty-probe failure."""
    transition_spec = {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "placeholder": True,
        "transitions": [
            {
                "id": "auto-hover-0",
                "trigger": "hover",
                "target": ".card",
                "animation": {"type": "css-hover"},
            }
        ],
    }
    proc, ref_dir, _ = _run(
        tmp_path,
        [{"name": "auto-hover-0", "triggerType": "hover", "selector": ".card"}],
        transition_spec=transition_spec,
        hover_css_rules={"rules": [{"selector": ".card:hover", "activation": ".card"}]},
        no_change=True,
    )

    assert proc.returncode == 0, proc.stderr
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["status"] == "pass"
    assert summary["autoSpec"] is True
    assert summary["autoCandidateInputFingerprint"].startswith("sha256:")
    assert len(summary["autoCandidateInputFingerprint"]) == 71
    assert summary["counts"] == {
        "attempted": 1,
        "captured": 0,
        "skipped": 1,
        "unsupported": 0,
        "notInstantiated": 0,
    }
    assert summary["skipped"][0]["candidateKey"] == {
        "triggerType": "hover",
        "selector": ".card",
    }
    assert (
        summary["skipped"][0]["autoCandidateInputFingerprint"]
        == summary["autoCandidateInputFingerprint"]
    )

    regions = json.loads((ref_dir / "regions.json").read_text())
    assert regions["regions"] == []
    assert regions["source"] == "scripts/extract/capture-region-artifacts.py"
    assert regions["resolvedAutoCandidates"] == [
        {"triggerType": "hover", "selector": ".card"}
    ]
    assert (
        regions["autoCandidateInputFingerprint"]
        == summary["autoCandidateInputFingerprint"]
    )

    retry_tmp = tmp_path / "same-input-retry"
    retry_tmp.mkdir()
    retry, calls = _run_existing_ref(retry_tmp, ref_dir)
    assert retry.returncode == 0, retry.stderr
    assert calls == []
    retried_summary = json.loads(
        (ref_dir / "capture-region-artifacts-summary.json").read_text()
    )
    assert retried_summary["reusedResolvedAutoCandidates"] is True
    assert [row["selector"] for row in retried_summary["attempted"]] == [".card"]
    assert retried_summary["counts"] == summary["counts"]


def test_changed_auto_hover_inputs_invalidate_the_negative_region_receipt(
    tmp_path: Path,
) -> None:
    """A prior negative must not suppress a candidate produced by changed inputs."""
    auto_spec = {
        "schemaVersion": 1,
        "source": "ui_clone.extraction_artifacts",
        "placeholder": True,
        "transitions": [
            {
                "id": "auto-hover-0",
                "trigger": "hover",
                "target": ".old",
                "animation": {"type": "css-hover"},
            }
        ],
    }
    first, ref_dir, _ = _run(
        tmp_path,
        [{"name": "auto-hover-0", "triggerType": "hover", "selector": ".old"}],
        transition_spec=auto_spec,
        hover_css_rules={"rules": [{"selector": ".old:hover", "activation": ".old"}]},
        no_change=True,
    )
    assert first.returncode == 0, first.stderr

    (ref_dir / "hover-css-rules.json").write_text(
        json.dumps({"rules": [{"selector": ".new:hover", "activation": ".new"}]}),
        encoding="utf-8",
    )
    changed_spec = {
        **auto_spec,
        "transitions": [
            {
                "id": "auto-hover-0",
                "trigger": "hover",
                "target": ".new",
                "animation": {"type": "css-hover"},
            }
        ],
    }
    (ref_dir / "transition-spec.json").write_text(json.dumps(changed_spec), encoding="utf-8")

    second_tmp = tmp_path / "second"
    second_tmp.mkdir()
    second, _ = _run_existing_ref(second_tmp, ref_dir)
    assert second.returncode == 0, second.stderr
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert [row["selector"] for row in summary["attempted"]] == [".new"]
    assert summary["counts"]["captured"] == 1


def test_mixed_positive_retry_preserves_only_the_fresh_hover_negative(
    tmp_path: Path,
) -> None:
    """Receipt metadata is carried as evidence and never traversed as a region."""
    ref_dir = tmp_path / "ref"
    _write_regions(
        ref_dir,
        [{"name": "positive", "triggerType": "hover", "selector": ".positive"}],
        source="scripts/extract/capture-region-artifacts.py",
    )
    (ref_dir / "hover-css-rules.json").write_text(
        json.dumps({"rules": [{"selector": ".positive:hover", "activation": ".positive"}]}),
        encoding="utf-8",
    )
    (ref_dir / "transition-spec.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "source": "ui_clone.extraction_artifacts",
                "placeholder": True,
                "transitions": [],
            }
        ),
        encoding="utf-8",
    )
    module = _load_capture_module()
    fingerprint = module._hover_candidate_input_fingerprint(ref_dir)
    regions = json.loads((ref_dir / "regions.json").read_text())
    regions["resolvedAutoCandidates"] = [
        {"triggerType": "hover", "selector": ".negative"}
    ]
    regions["autoCandidateInputFingerprint"] = fingerprint
    (ref_dir / "regions.json").write_text(json.dumps(regions), encoding="utf-8")
    (ref_dir / "capture-region-artifacts-summary.json").write_text(
        json.dumps(
            {
                "autoSpec": True,
                "autoCandidateInputFingerprint": fingerprint,
                "status": "pass",
                "attempted": [
                    {
                        "region": "negative",
                        "triggerType": "hover",
                        "selector": ".negative",
                    }
                ],
                "skipped": [
                    {
                        "region": "negative",
                        "triggerType": "hover",
                        "selector": ".negative",
                        "reason": "hover produced no observable change",
                        "resolution": "absence-measured",
                        "candidateKey": {
                            "triggerType": "hover",
                            "selector": ".negative",
                        },
                        "autoCandidateInputFingerprint": fingerprint,
                    }
                ],
                "captured": [],
                "unsupported": [],
                "counts": {
                    "attempted": 1,
                    "captured": 0,
                    "skipped": 1,
                    "unsupported": 0,
                    "notInstantiated": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    run_tmp = tmp_path / "mixed-retry"
    run_tmp.mkdir()
    proc, _ = _run_existing_ref(run_tmp, ref_dir)
    assert proc.returncode == 0, proc.stderr
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert {row["selector"] for row in summary["attempted"]} == {
        ".negative",
        ".positive",
    }
    assert [row["selector"] for row in summary["captured"]] == [".positive"]
    assert [row["selector"] for row in summary["skipped"]] == [".negative"]
    output = json.loads((ref_dir / "regions.json").read_text())
    assert output["resolvedAutoCandidates"] == [
        {"triggerType": "hover", "selector": ".negative"}
    ]

    second_tmp = tmp_path / "mixed-second-retry"
    second_tmp.mkdir()
    second, _ = _run_existing_ref(second_tmp, ref_dir)
    assert second.returncode == 0, second.stderr
    second_summary = json.loads(
        (ref_dir / "capture-region-artifacts-summary.json").read_text()
    )
    assert second_summary["reusedResolvedAutoCandidates"] is True
    assert [row["selector"] for row in second_summary["skipped"]] == [".negative"]


def test_scroll_without_observable_change_is_not_retired_as_measured_absence(
    tmp_path: Path,
) -> None:
    """A scroll ladder that saw nothing move has not proven the region absent.

    No reference run has produced this row, and a scroll ladder samples only a
    handful of rungs, so its negative is not corroborated the way a hover's is.
    The skip row must stay unmarked so the reference gate treats it as unproven
    rather than as evidence.
    """
    proc, ref_dir, _ = _run(
        tmp_path,
        [{"name": "banner", "triggerType": "scroll", "selector": ".banner"}],
        no_change=True,
    )

    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["captured"] == []
    assert [row["reason"] for row in summary["skipped"]] == [
        "scroll produced no observable change"
    ]
    assert "resolution" not in summary["skipped"][0]
    assert proc.returncode != 0


def test_real_spec_dispatch_only_region_survives_a_measured_absence(
    tmp_path: Path,
) -> None:
    """A CSS-hover negative does not discharge a JS-dispatched obligation.

    `dispatchOnly` means the spec says the transition fires from script, not
    from `:hover`; hovering it and seeing no CSS delta measures the wrong
    thing. Under an authored spec the region stays an unsupported obligation
    exactly as it does for every other failed capture.
    """
    hover = {
        "name": "authored-hover",
        "triggerType": "hover",
        "selector": ".button",
        "dispatchOnly": True,
    }
    proc, ref_dir, _ = _run(
        tmp_path,
        [hover],
        transition_spec={
            "source": "agent-authored",
            "placeholder": False,
            "transitions": [],
        },
        no_change=True,
    )

    assert proc.returncode != 0
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert payload["regions"] == [hover]
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["unsupported"] == [{"region": "authored-hover", "triggerType": "hover"}]
    assert [row["reason"] for row in summary["skipped"]] == [
        "hover produced no observable change"
    ]
    assert "resolution" not in summary["skipped"][0]


def test_absent_activation_is_not_instantiated_before_its_descendant_is_consulted(
    tmp_path: Path,
) -> None:
    """An activation that is not in the document is unproven, not retired.

    The descendant question never arises: the region's own selector matching
    nothing is recorded as `notInstantiated` and the candidate stays in
    regions.json for a corrected re-run, even though a hover rule names an
    absent descendant under it.
    """
    proc, ref_dir, _ = _run(
        tmp_path,
        [{"name": "gone", "triggerType": "hover", "selector": ".missing"}],
        hover_css_rules={
            "rules": [
                {
                    "selector": ".missing:hover .missing-child",
                    "activation": ".missing",
                    "affected": ".missing .missing-child",
                }
            ]
        },
    )

    assert proc.returncode != 0
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert [region["selector"] for region in payload["regions"]] == [".missing"]
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["captured"] == []
    assert summary["skipped"] == []
    assert [row["reason"] for row in summary["notInstantiated"]] == [
        "selector matches no elements"
    ]
    assert "resolution" not in summary["notInstantiated"][0]
    assert summary["status"] == "fail"


def test_descendant_rendered_outside_the_activation_is_a_probe_failure(
    tmp_path: Path,
) -> None:
    """Rendered elsewhere is not rendered nowhere.

    When the affected selector matches somewhere in the document but not inside
    this activation, the observation target could not be pinned. That is a
    failed measurement: the activation is not hovered in its own right, the
    region is neither captured nor retired, and it stays in regions.json.
    """
    proc, ref_dir, _ = _run(
        tmp_path,
        [{"name": "card", "triggerType": "hover", "selector": ".card"}],
        hover_css_rules={
            "rules": [
                {
                    "selector": ".card:hover .title",
                    "activation": ".card",
                    "affected": ".card .title",
                }
            ]
        },
        affected_outside=True,
    )

    assert proc.returncode != 0
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert [region["name"] for region in payload["regions"]] == ["card"]
    assert "artifacts" not in payload["regions"][0]
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["captured"] == []
    assert [row["reason"] for row in summary["skipped"]] == [
        "affected selector missing or not observable"
    ]
    assert "resolution" not in summary["skipped"][0]


def test_descendant_rendered_only_on_hover_is_not_retired_as_measured_absence(
    tmp_path: Path,
) -> None:
    """Absent at idle is not absent: the descendant must be re-queried hovered.

    A menu that is rendered only while its opener is hovered, and positioned
    outside the opener's box, matches nothing at idle and leaves the opener's
    own crop and computed style unchanged. Retiring that as "descendant absent
    AND activation unchanged" ships a clone without the menu. The bridge must
    ask again after the hover and, on finding the descendant, keep the region
    as an unproven candidate instead of tagging it `absence-measured`.
    """
    proc, ref_dir, _ = _run(
        tmp_path,
        [{"name": "card", "triggerType": "hover", "selector": ".card"}],
        hover_css_rules={
            "rules": [
                {
                    "selector": ".card:hover .menu",
                    "activation": ".card",
                    "affected": ".card .menu",
                }
            ]
        },
        descendant_on_hover=True,
        no_change=True,
    )

    assert proc.returncode != 0
    payload = json.loads((ref_dir / "regions.json").read_text())
    assert [region["name"] for region in payload["regions"]] == ["card"]
    assert "artifacts" not in payload["regions"][0]
    summary = json.loads((ref_dir / "capture-region-artifacts-summary.json").read_text())
    assert summary["captured"] == []
    assert len(summary["skipped"]) == 1
    row = summary["skipped"][0]
    assert row["region"] == "card"
    assert "resolution" not in row
    assert "not present in document" not in row["reason"]
    assert "hovered" in row["reason"]
