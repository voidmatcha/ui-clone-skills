"""Tests for live Swiper reference artifact capture."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ui_clone.dag import check_staleness

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "extract" / "capture-swiper-artifacts.py"


def _fake_agent_browser(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "agent-browser"
    script.write_text(
        """#!/usr/bin/env python3
import json
import os
import struct
import sys
import zlib
from pathlib import Path

calls = Path(os.environ["FAKE_CALLS"])
with calls.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
session_i = args.index("--session")
session = args[session_i + 1]
command = args[session_i + 2]
rest = args[session_i + 3:]
state_path = Path(os.environ["FAKE_STATE"])
try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except FileNotFoundError:
    state = {}

def png(pixel, width=2, height=2):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF
        )
    row = b"\\x00" + bytes(pixel) * width
    raw = row * height
    return (
        b"\\x89PNG\\r\\n\\x1a\\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )

if command == "eval":
    js = rest[-1]
    if "const found=[]" in js:
        result = {"instances": [{
            "selector": ".swiper[data-ui-clone-swiper=\\"0\\"]",
            "index": 0,
            "activeIndex": 0,
            "realIndex": 0,
            "params": {
                "slidesPerView": 3,
                "spaceBetween": 24,
                "effect": "slide",
                "loop": True,
                "speed": 400,
                "autoplay": {"delay": 3000},
            },
            "rect": {"x": 0, "y": 40, "width": 2, "height": 2},
            "viewport": {"width": 2, "height": 2},
        }]}
    elif "slideNext()" in js:
        state[session] = True
        state_path.write_text(json.dumps(state), encoding="utf-8")
        result = {"ok": True, "speed": 0, "activeIndex": 1, "realIndex": 1}
    elif "window.scrollTo" in js:
        result = {
            "ok": True, "x": 0, "y": 0, "width": 2, "height": 2,
            "viewportWidth": 2, "viewportHeight": 2,
        }
    elif "slideToLoop" in js or "slideTo(item.index" in js:
        state[session] = False
        state_path.write_text(json.dumps(state), encoding="utf-8")
        result = {"ok": True, "activeIndex": 0, "realIndex": 0}
    else:
        result = {"ok": True}
    print(json.dumps({"success": True, "data": {"result": result}}))
elif command == "screenshot":
    output = Path(rest[-1])
    output.parent.mkdir(parents=True, exist_ok=True)
    is_selector = len(rest) == 2
    identical = os.environ.get("FAKE_IDENTICAL") == "1"
    selector_blank = os.environ.get("FAKE_SELECTOR_BLANK") == "1"
    active = state.get(session, False)
    if identical or (selector_blank and is_selector):
        pixel = (255, 255, 255, 255)
    else:
        pixel = (255, 0 if active else 255, 0, 255)
    output.write_bytes(png(pixel))
sys.exit(0)
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return bin_dir


def _run(
    tmp_path: Path,
    *,
    identical: bool = False,
    selector_blank: bool = False,
    extra_transitions: list[dict] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, list[list[str]]]:
    ref = tmp_path / "ref"
    (ref / "bundles").mkdir(parents=True)
    (ref / "bundles" / "swiper.js").write_text("new Swiper()", encoding="utf-8")
    (ref / "verification-plan.json").write_text(
        json.dumps({"signals": {"hasSwiper": True}}),
        encoding="utf-8",
    )
    (ref / "bundle-extraction.json").write_text(
        json.dumps(
            {
                "unresolved": [
                    {
                        "library": "swiper",
                        "source": "bundles/swiper.js",
                        "reason": "runtime params required",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "source": "scripts/extract/capture-region-artifacts.py",
                "placeholder": False,
                "transitions": [
                    {
                        "id": "hover",
                        "trigger": "hover",
                        "source_chunk": "inline init",
                        "bundle_branch": "live-capture: hover",
                        "target": ".button",
                        "animation": {"type": "css-hover"},
                        "reference_frames": ["clip/ref/a.png", "clip/ref/b.png"],
                    },
                    {
                        "id": "auto-swiper",
                        "trigger": "swiper",
                        "source_chunk": "inline init",
                        "bundle_branch": "settled branch observed during capture",
                        "target": "body",
                        "animation": {"type": "swiper"},
                        "reference_frames": [],
                    },
                    *(extra_transitions or []),
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "regions.json").write_text(
        json.dumps({"source": "fixture", "regions": []}),
        encoding="utf-8",
    )
    (ref / "extracted.json").write_text(
        json.dumps(
            {
                "source": "ui_clone.extraction_artifacts",
                "transitions": [{"id": "stale"}],
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-coverage.json").write_text(
        json.dumps(
            {
                "source": "ui_clone.extraction_artifacts",
                "animatedElements": [{"id": "stale"}],
            }
        ),
        encoding="utf-8",
    )
    calls = tmp_path / "calls.jsonl"
    env = os.environ.copy()
    env["PATH"] = f"{_fake_agent_browser(tmp_path)}:{env['PATH']}"
    env["FAKE_CALLS"] = str(calls)
    env["FAKE_STATE"] = str(tmp_path / "state.json")
    if identical:
        env["FAKE_IDENTICAL"] = "1"
    if selector_blank:
        env["FAKE_SELECTOR_BLANK"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "https://example.test",
            "dogfood",
            str(ref),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    recorded = [
        json.loads(line)
        for line in calls.read_text(encoding="utf-8").splitlines()
    ]
    return proc, ref, recorded


def test_captures_runtime_params_and_merges_with_live_hover(
    tmp_path: Path,
) -> None:
    proc, ref, calls = _run(tmp_path)

    assert proc.returncode == 0, proc.stderr
    spec = json.loads((ref / "transition-spec.json").read_text(encoding="utf-8"))
    assert [item["trigger"] for item in spec["transitions"]] == [
        "hover",
        "swiper-next",
    ]
    swiper = spec["transitions"][1]
    assert swiper["source_chunk"] == "bundles/swiper.js"
    assert swiper["placeholder"] is False
    assert swiper["animation"] == {
        "type": "swiper",
        "action": "slideNext",
        "slidesPerView": 3,
        "spaceBetween": 24,
        "effect": "slide",
        "loop": True,
        "speed": 400,
        "autoplay": {"delay": 3000},
    }
    assert all((ref / frame).is_file() for frame in swiper["reference_frames"])

    regions = json.loads((ref / "regions.json").read_text(encoding="utf-8"))
    region = regions["regions"][0]
    assert region["triggerType"] == "swiper-next"
    assert region["runtimeParams"]["slidesPerView"] == 3
    assert region["artifacts"] == {
        "idle": swiper["reference_frames"][0],
        "active": swiper["reference_frames"][1],
    }
    extracted = json.loads((ref / "extracted.json").read_text(encoding="utf-8"))
    coverage = json.loads(
        (ref / "transition-coverage.json").read_text(encoding="utf-8")
    )
    assert [item["trigger"] for item in extracted["transitions"]] == [
        "hover",
        "swiper-next",
    ]
    assert [item["trigger"] for item in coverage["animatedElements"]] == [
        "hover",
        "swiper-next",
    ]
    assert not [
        issue
        for issue in check_staleness(ref)
        if issue.stale == "extracted.json"
    ]

    summary = json.loads(
        (ref / "capture-swiper-artifacts-summary.json").read_text(encoding="utf-8")
    )
    assert summary["status"] == "pass"
    assert summary["counts"] == {
        "attempted": 1,
        "captured": 1,
        "skipped": 0,
        "unsupported": 0,
    }
    assert all(call[1] == "dogfood-swiper-artifacts" for call in calls)
    assert [call[2] for call in calls].count("close") == 1
    evals = [call[-1] for call in calls if call[2] == "eval"]
    assert evals and all(script.startswith("(() => {") for script in evals)
    screenshots = [call for call in calls if call[2] == "screenshot"]
    assert screenshots and all("--clip" not in call for call in screenshots)


def test_identical_pixels_keep_swiper_obligation_and_close_session(
    tmp_path: Path,
) -> None:
    proc, ref, calls = _run(tmp_path, identical=True)

    assert proc.returncode == 4
    spec = json.loads((ref / "transition-spec.json").read_text(encoding="utf-8"))
    assert any(item["trigger"] == "swiper" for item in spec["transitions"])
    summary = json.loads(
        (ref / "capture-swiper-artifacts-summary.json").read_text(encoding="utf-8")
    )
    assert summary["status"] == "fail"
    assert summary["counts"]["captured"] == 0
    assert summary["counts"]["unsupported"] == 1
    assert [call[2] for call in calls].count("close") == 1


def test_blank_selector_capture_falls_back_to_viewport_post_crop(
    tmp_path: Path,
) -> None:
    proc, ref, calls = _run(tmp_path, selector_blank=True)

    assert proc.returncode == 0, proc.stderr
    summary = json.loads(
        (ref / "capture-swiper-artifacts-summary.json").read_text(encoding="utf-8")
    )
    assert summary["captured"][0]["observation"]["captureMode"] == (
        "viewport-post-crop"
    )
    screenshots = [call for call in calls if call[2] == "screenshot"]
    assert any(len(call) == 4 for call in screenshots)


def test_authored_unsupported_swiper_is_preserved_and_fails(
    tmp_path: Path,
) -> None:
    authored = {
        "id": "authored-swiper",
        "trigger": "swiper-custom",
        "source_chunk": "inline init",
        "bundle_branch": "manual branch",
        "target": ".authored",
        "animation": {"type": "swiper", "action": "slideTo"},
        "reference_frames": [],
    }
    proc, ref, _ = _run(tmp_path, extra_transitions=[authored])

    assert proc.returncode == 4
    spec = json.loads((ref / "transition-spec.json").read_text(encoding="utf-8"))
    assert authored in spec["transitions"]
    summary = json.loads(
        (ref / "capture-swiper-artifacts-summary.json").read_text(encoding="utf-8")
    )
    assert summary["unsupported"] == [
        {
            "triggerType": "swiper-custom",
            "selector": ".authored",
            "reason": "authored Swiper obligation lacks two reference frame files",
        }
    ]
