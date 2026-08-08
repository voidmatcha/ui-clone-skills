import base64
import json
import os
import time
from pathlib import Path

import pytest

# Tiny, genuinely decodable media keeps this all-artifacts fixture honest.
# The PNG is a 1x1 black RGB image. The WebM is a single-frame 2x2 lossless
# VP9 video generated with deterministic ffmpeg bitexact flags; storing the
# bytes avoids making fixture construction depend on an encoder invocation.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhV"
    "AAAAAElFTkSuQmCC"
)
_TINY_WEBM = base64.b64decode(
    "GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAAGtEU2bdLpNu4tT"
    "q4QVSalmU6yBoU27i1OrhBZUrmtTrIHGTbuMU6uEElTDZ1OsggETTbuMU6uEHFO7a1OsggGX7AEA"
    "AAAAAABZAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAVSalmoCrXsYMPQkBNgIRMYXZm"
    "V0GETGF2ZkSJiECPQAAAAAAAFlSua8iuAQAAAAAAAD/XgQFzxYgAAAAAAAAAAZyBACK1nIN1bmSI"
    "gQCGhVZfVlA5g4EBI+ODhDuaygDgkLCBArqBApqBAlWwhFW5gQESVMNn1HNz0WPAi2PFiAAAAAAA"
    "AAABZ8icRaOHRU5DT0RFUkSHj0xhdmMgbGlidnB4LXZwOWfIoUWjiERVUkFUSU9ORIeTMDA6MDA6"
    "MDEuMDAwMDAwMDAwAB9DtnWm54EAo6GBAACAgkmDQgAAEAAWADgkHBgAAAAgAAARv///7ZPwAAAc"
    "U7trkbuPs4EAt4r3gQHxggFs8IED"
)


@pytest.fixture(autouse=True)
def _short_ab_open_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bound the agent-browser navigation watchdog for the whole suite.

    Browser-touching gate scripts source skills/visual-debug/scripts/lib/
    ab-timeout.sh, which shadows `agent-browser` and runs open/goto/navigate
    under UI_CLONE_AB_OPEN_TIMEOUT seconds (default 30). Tests drive those
    scripts against unreachable hosts like https://ref.example (NXDOMAIN); at
    the 30s default the pre-flight `open` deadlocks past pytest's per-test
    timeout. Setting it to 5s here makes a dead-URL open fail fast — and because
    tests build their subprocess env from os.environ (e.g. {**os.environ, ...}),
    monkeypatch.setenv propagates the override into the script's environment.

    open alone is NOT enough: on a timeout the watchdog reaps the detached
    session by calling `agent-browser close`, and gate scripts also close their
    own probe sessions — but `close` itself BLOCKS ~8-12s on a wedged dead-host
    session. With two viewports that reap+close cost (≈2×~10s) pushed the
    hover-state-compare fan-out past pytest's 30s even with the open bounded. So
    also bound the post-timeout reap (UI_CLONE_AB_REAP_TIMEOUT) and explicit
    close (UI_CLONE_AB_CLOSE_TIMEOUT) to 3s. A bounded close that is killed
    before the server exits leaves an orphan reaped by `agent-browser close
    --all`; the test run itself stays well under 30s.

    Scope/safety: these only shorten agent-browser open/reap/close ceilings. They
    have no effect on non-browser tests; 5s is far above any healthy open and 3s
    is far above any healthy (sub-second) close, so none can make a real,
    reachable operation spuriously time out.
    """
    monkeypatch.setenv("UI_CLONE_AB_OPEN_TIMEOUT", "5")
    monkeypatch.setenv("UI_CLONE_AB_REAP_TIMEOUT", "3")
    monkeypatch.setenv("UI_CLONE_AB_CLOSE_TIMEOUT", "3")


@pytest.fixture
def ref_dir(tmp_path: Path) -> Path:
    """Minimal valid ref_dir fixture."""
    d = tmp_path / "tmp" / "ref" / "test-component"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def ref_dir_with_artifacts(ref_dir: Path) -> Path:
    """ref_dir fixture for gate tests — includes all required artifacts."""
    reference_media: list[Path] = []

    # Phase 1: reference screenshots
    screenshots = ref_dir / "static" / "ref"
    screenshots.mkdir(parents=True)
    for i in range(5):
        screenshot = screenshots / f"scroll_{i:02d}.png"
        screenshot.write_bytes(_TINY_PNG)
        reference_media.append(screenshot)

    # Phase 1: transition videos
    transitions_ref = ref_dir / "transitions" / "ref"
    transitions_ref.mkdir(parents=True)
    transition_video = transitions_ref / "hover_hero.webm"
    transition_video.write_bytes(_TINY_WEBM)
    reference_media.append(transition_video)

    (ref_dir / "regions.json").write_text(json.dumps({"regions": []}))

    # Phase 2: extraction artifacts
    (ref_dir / "structure.json").write_text(json.dumps({"sections": [], "totalCount": 0}))
    (ref_dir / "styles.json").write_text(json.dumps({"selectors": {}}))
    (ref_dir / "head.json").write_text(json.dumps({"title": "Test"}))
    (ref_dir / "fonts.json").write_text(json.dumps({"faces": []}))
    css_dir = ref_dir / "css"
    css_dir.mkdir()
    (css_dir / "variables.txt").write_text(":root { --color: #fff; }")
    (ref_dir / "visible-images.json").write_text(json.dumps({"images": []}, indent=2))
    (ref_dir / "inline-svgs.json").write_text(json.dumps({"svgs": []}, indent=2))
    (ref_dir / "body-state.json").write_text(json.dumps({"state": "idle"}, indent=2))
    (ref_dir / "design-bundles.json").write_text(json.dumps({"bundles": []}, indent=2))
    (ref_dir / "interactions-detected.json").write_text(
        json.dumps({"interactions": [], "hasPreloader": False})
    )
    (ref_dir / "scroll-engine.json").write_text(json.dumps({"engine": "native"}))
    (ref_dir / "external-sdks.json").write_text(json.dumps({"sdks": []}, indent=2))
    generation_plan_inputs = {
        "animations-detected.json": {"animations": []},
        "asset-substitution.json": {"violations": []},
        "bundle-extraction.json": {"constructions": [], "unresolved": []},
        "canvas-webgl-detection.json": {"detected": False},
        "dom-scaffold.json": {"sections": []},
        "font-parity.json": {"fonts": []},
        "hidden-elements.json": [],
        "mobile-swap.json": {"mobile_swap_sections": []},
        "paid-features.json": {"features": []},
        "required-media.json": {
            "schemaVersion": 1,
            "videos": [],
            "lottie": [],
            "totals": {"video": 0, "lottie": 0},
            "sources": {
                "extractor": "required-media.sh",
                "htmlSectionsScanned": 0,
                "runtimeMediaScanned": True,
                "bundlesScanned": 0,
            },
        },
        "signature-effects-candidates.json": {"candidates": []},
        "sticky-elements.json": [],
        "runtime-media.json": {
            "schemaVersion": 1,
            "url": "https://example.com",
            "videos": [],
            "totals": {"video": 0},
            "sources": {"extractor": "runtime-media.sh", "scrollSamples": 5},
        },
    }
    for name, payload in generation_plan_inputs.items():
        (ref_dir / name).write_text(json.dumps(payload), encoding="utf-8")

    bundles = ref_dir / "bundles"
    bundles.mkdir()
    for i in range(3):
        (bundles / f"chunk-{i}.js").write_text("// chunk")

    (ref_dir / "bundle-map.json").write_text(json.dumps({"chunks": []}, indent=2))
    (ref_dir / "transition-spec.json").write_text(json.dumps({"transitions": []}))

    verify = ref_dir / "verify"
    verify.mkdir()
    for i in range(5):
        frame = verify / f"frame_{i:02d}.png"
        frame.write_bytes(_TINY_PNG)
        reference_media.append(frame)

    (ref_dir / "animation-init-styles.json").write_text(
        json.dumps({"elements": []}, indent=2)
    )
    (ref_dir / "section-map.json").write_text(
        json.dumps({"sections": [], "totalCount": 0, "hasFooter": False})
    )
    responsive = ref_dir / "responsive"
    responsive.mkdir()
    # A real Step 4-C2 sweep is a selector-keyed expression map, NOT the
    # single-viewport finalizer sentinel — otherwise the responsive content
    # gate treats this "complete" fixture as an unfilled sweep.
    (responsive / "sizing-expressions.json").write_text(
        json.dumps(
            {".hero": {"width": {"768": 384, "1280": 1216, "1440": 1376}}},
            indent=2,
        )
    )
    (ref_dir / "svg-text-elements.json").write_text(json.dumps({"elements": []}, indent=2))
    (ref_dir / "hover-css-rules.json").write_text(json.dumps({"rules": []}, indent=2))

    (ref_dir / "element-roles.json").write_text(json.dumps({"roles": []}, indent=2))
    (ref_dir / "element-groups.json").write_text(json.dumps({"groups": []}, indent=2))
    (ref_dir / "layout-decisions.json").write_text(json.dumps({"decisions": []}, indent=2))
    (ref_dir / "component-map.json").write_text(json.dumps({"sections": [], "sectionCount": 0}))
    (ref_dir / "generation-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "componentList": []})
    )

    # Set parent artifacts to a fixed past time
    base_time = time.time() - 2.0
    parent_artifacts = [
        "structure.json",
        "styles.json",
        "head.json",
        "fonts.json",
        "visible-images.json",
        "inline-svgs.json",
        "body-state.json",
        "design-bundles.json",
        "interactions-detected.json",
        "scroll-engine.json",
        "external-sdks.json",
        "bundle-map.json",
        "transition-spec.json",
        "animation-init-styles.json",
        "section-map.json",
        "svg-text-elements.json",
        "hover-css-rules.json",
        "element-roles.json",
        "element-groups.json",
        "layout-decisions.json",
        "component-map.json",
    ]
    for name in parent_artifacts:
        p = ref_dir / name
        if p.exists():
            os.utime(p, (base_time, base_time))

    # extracted.json gets strictly newer time (+1s)
    extracted_time = base_time + 1.0
    (ref_dir / "extracted.json").write_text(
        json.dumps({"sections": [], "url": "https://example.com"})
    )
    os.utime(ref_dir / "extracted.json", (extracted_time, extracted_time))

    # transition-coverage.json also gets parent time (it's a parent of extracted.json)
    (ref_dir / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [], "staticElements": []})
    )
    os.utime(ref_dir / "transition-coverage.json", (base_time, base_time))

    provenance_artifacts = [
        "extracted.json",
        "transition-spec.json",
        "animation-init-styles.json",
        "section-map.json",
        "svg-text-elements.json",
        "responsive/sizing-expressions.json",
        "interactions-detected.json",
        "transition-coverage.json",
        "component-map.json",
    ]
    (ref_dir / "artifact-provenance.json").write_text(json.dumps({
        "artifacts": [
            {
                "path": artifact,
                "source": "agent-browser-eval" if artifact != "transition-spec.json" else "bundle-grep",
                "evidence": [artifact],
                "generatedAt": "2026-05-14T00:00:00Z",
            }
            for artifact in provenance_artifacts
        ],
    }))

    # Assert the contract is met
    assert (ref_dir / "extracted.json").stat().st_mtime > (
        ref_dir / "structure.json"
    ).stat().st_mtime, "extracted.json must be newer than structure.json"

    from ui_clone.gates.spec import _reference_media_is_decodable

    for media_path in reference_media:
        decodable, reason = _reference_media_is_decodable(media_path)
        assert decodable, f"{media_path.name} must be decodable: {reason}"

    return ref_dir
