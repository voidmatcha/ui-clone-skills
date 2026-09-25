from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path

from ui_clone.gates.live_parity import (
    classify_image_drift,
    find_accessibility_text_leaks,
    scrollheight_within_tolerance,
)


def test_live_parity_system_python_paths_avoid_runtime_pep604_unions() -> None:
    root = Path(__file__).resolve().parents[1]
    execution_surfaces = (
        root / "ui_clone" / "gates" / "live_parity.py",
        root / "skills" / "visual-debug" / "scripts" / "live-parity-sweep.sh",
    )
    runtime_union = re.compile(
        r"isinstance\([^\n]*\b(?:int|float)\s*\|\s*(?:int|float)"
    )

    offenders = [
        path.relative_to(root).as_posix()
        for path in execution_surfaces
        if runtime_union.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        "live-parity-sweep can run under macOS system Python 3.9; runtime "
        f"PEP 604 unions inside isinstance() crash there: {offenders}"
    )


def test_timer_carousel_rotation_is_advisory() -> None:
    """loop-e2e-4: the ref's custom setInterval carousel rotates even while
    off-screen, so census-time food identity is phase noise. Same filename
    vocabulary + per-file delta <= 1 => advisory, not blocking."""
    verdict = classify_image_drift(
        {"butter.webp": 3, "salmon.webp": 2, "steak.webp": 1},
        {"butter.webp": 2, "salmon.webp": 3, "steak.webp": 1},
    )
    assert verdict == "advisory"


def test_missing_file_from_vocabulary_is_blocking() -> None:
    verdict = classify_image_drift(
        {"butter.webp": 2, "salmon.webp": 2},
        {"salmon.webp": 4},
    )
    assert verdict == "blocking"


def test_count_delta_over_one_is_blocking() -> None:
    # a 3-instance gap is structural duplication, not rotation phase
    verdict = classify_image_drift(
        {"butter.webp": 4, "salmon.webp": 1},
        {"butter.webp": 1, "salmon.webp": 4},
    )
    assert verdict == "blocking"


def test_no_drift_is_clean() -> None:
    assert classify_image_drift({"a.webp": 2}, {"a.webp": 2}) == "clean"


def test_scrollheight_within_tolerance() -> None:
    # loop-e2e-4: 19226 vs 19229 on a page whose own height oscillates +-180px
    assert scrollheight_within_tolerance(19229, 19226)
    assert scrollheight_within_tolerance(19229, 19354)  # 125px < 200px floor


def test_scrollheight_balloon_still_fails() -> None:
    assert not scrollheight_within_tolerance(17952, 39092)  # loop-129 balloon
    assert not scrollheight_within_tolerance(19229, 19650)  # 421px > max(0.5%, 200)


def test_scrollheight_missing_values_not_tolerated() -> None:
    assert not scrollheight_within_tolerance(None, 19226)
    assert not scrollheight_within_tolerance(19229, None)


def _find_accessibility_text_leaks(
    candidates: Sequence[Mapping[str, object]], ref_visible: str, impl_visible: str
) -> list[dict[str, str]]:
    return find_accessibility_text_leaks(candidates, ref_visible, impl_visible)


def test_hidden_accessibility_description_visible_only_in_impl_is_blocking() -> None:
    description = (
        "Animated showcase of the Evo design system's color palette, displaying "
        "tone-on-tone color chips with AA or AAA contrast labels"
    )
    leaks = _find_accessibility_text_leaks(
        [
            {
                "text": description,
                "source": "hidden-text",
                "tag": "P",
                "class": "clipped",
            }
        ],
        "AA AAA AA AAA Color palette",
        f"AA AAA AA AAA {description}",
    )

    assert leaks == [
        {
            "text": description,
            "source": "hidden-text",
            "tag": "P",
            "class": "clipped",
        }
    ]


def test_accessibility_description_already_visible_in_ref_is_not_a_leak() -> None:
    description = "A detailed product description that is intentionally visible as a caption"
    candidates = [
        {
            "text": description,
            "source": "aria-label",
            "tag": "IMG",
            "class": "product-image",
        }
    ]

    assert not _find_accessibility_text_leaks(
        candidates,
        f"Product heading {description}",
        f"Product heading {description}",
    )


def test_accessibility_leak_detection_ignores_short_or_unrelated_copy() -> None:
    candidates = [
        {"text": "Open navigation", "source": "aria-label", "tag": "BUTTON", "class": ""},
        {
            "text": "Long screen reader description that does not render in the clone",
            "source": "hidden-text",
            "tag": "SPAN",
            "class": "sr-only",
        },
    ]

    assert not _find_accessibility_text_leaks(
        candidates,
        "Home Products About",
        "Home Products About Open navigation",
    )


def test_accessibility_leak_detection_normalizes_case_and_whitespace() -> None:
    description = "Long accessibility description for the animated color palette"
    leaks = _find_accessibility_text_leaks(
        [{"text": description, "source": "alt", "tag": "CANVAS", "class": ""}],
        "Color palette",
        "LONG  ACCESSIBILITY\nDESCRIPTION FOR THE ANIMATED COLOR PALETTE",
    )

    assert len(leaks) == 1
    assert leaks[0]["source"] == "alt"


def test_live_parity_sweep_wires_accessibility_copy_census() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "visual-debug"
        / "scripts"
        / "live-parity-sweep.sh"
    ).read_text(encoding="utf-8")

    assert "accessibilityOnlyText" in script
    assert "visibleBodyText" in script
    assert "find_accessibility_text_leaks" in script
    assert "visible-accessibility-copy-leak" in script


def test_live_parity_sweep_requires_proven_reference_scroll_cap_normalization() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "visual-debug"
        / "scripts"
        / "live-parity-sweep.sh"
    ).read_text(encoding="utf-8")

    assert "REF_SCROLL_CAP_SELECTOR" in script
    assert "no-scroll-cap-candidate" in script
    assert "height-growth-not-proven" in script
    assert "ref-scroll-cap-normalization-unproven" in script
    assert '"referenceNormalization": ref_normalization' in script


def _svg_hashes(svg: str) -> tuple[str, str, list[str]]:
    paint_attrs = {"fill", "stroke", "color", "stop-color", "flood-color", "lighting-color"}
    root = ET.fromstring(svg)

    def local(name: str) -> str:
        return name.rsplit("}", 1)[-1]

    def canonical(node: ET.Element, ignore_paint: bool) -> list:
        attrs = sorted(
            [
                [local(name), re.sub(r"\s+", " ", value).strip()]
                for name, value in node.attrib.items()
                if local(name) != "xmlns"
                and not (ignore_paint and local(name) in paint_attrs)
            ],
            key=lambda row: row[0],
        )
        return [local(node.tag), attrs, [canonical(child, ignore_paint) for child in node]]

    def digest(value: list) -> str:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    paints = sorted({
        value
        for node in root.iter()
        for name, value in node.attrib.items()
        if local(name) in paint_attrs
    })
    return digest(canonical(root, True)), digest(canonical(root, False)), paints


def _run_live_parity_with_svg_fixtures(
    tmp_path: Path, *, matching_structure: bool, navigation: str = "ok"
) -> tuple[subprocess.CompletedProcess[str], dict]:
    script = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "visual-debug"
        / "scripts"
        / "live-parity-sweep.sh"
    )
    out = tmp_path / "out"
    resource_dir = out / "resources" / "ref.example"
    resource_dir.mkdir(parents=True)
    svg_template = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60" '
        'viewBox="0 0 120 60"><path d="M0 0H120V60H0Z" fill="{paint}" '
        'stroke="{paint}"/></svg>'
    )
    ref_svg = svg_template.format(paint="#F98B0F")
    resource_svg = svg_template.format(paint="#FF5080")
    impl_svg = (
        svg_template.format(paint="#3DB3F8")
        if matching_structure
        else '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60">'
             '<circle cx="30" cy="30" r="20" fill="#3DB3F8"/></svg>'
    )
    (resource_dir / "public-variant.svg").write_text(resource_svg, encoding="utf-8")
    ref_structure, ref_full, ref_paints = _svg_hashes(ref_svg)
    impl_structure, impl_full, impl_paints = _svg_hashes(impl_svg)
    ref_key = "data:image/svg+xml,<svg-orange>"
    impl_key = "snail-runtime.svg"

    base = {
        "scrollHeight": 1000,
        "headerHeight": 80,
        "imgCount": 1,
        "brokenImgs": 0,
        "fonts": ["sans"],
        "accessibilityOnlyText": [],
        "visibleBodyText": "same",
        "oversizedLeafText": [],
        "pseudoDuplicates": [],
    }
    ref_census = {
        **base,
        "imgFiles": [ref_key],
        "svgAssets": [{
            "file": ref_key, "count": 1, "kind": "data-svg",
            "structureHash": ref_structure, "fullHash": ref_full, "paints": ref_paints,
        }],
    }
    impl_census = {
        **base,
        "imgFiles": [impl_key],
        "svgAssets": [{
            "file": impl_key, "count": 1, "kind": "url-svg",
            "structureHash": impl_structure, "fullHash": impl_full, "paints": impl_paints,
        }],
    }
    ref_fixture = tmp_path / "ref-census.json"
    impl_fixture = tmp_path / "impl-census.json"
    ref_fixture.write_text(json.dumps(ref_census), encoding="utf-8")
    impl_fixture.write_text(json.dumps(impl_census), encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    browser = bin_dir / "agent-browser"
    browser.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv
command = args[3]
if command == "open" and os.environ.get("NAVIGATION") == "failed":
    raise SystemExit(1)
if command in {"open", "set", "close"}:
    raise SystemExit(0)
if command == "screenshot":
    pathlib.Path(args[4]).write_bytes(b"png")
    raise SystemExit(0)
if command == "eval":
    source = args[4]
    if "href: location.href" in source:
        url = "https://ref.example" if args[2].endswith("-lp-ref") else "https://impl.example"
        if os.environ.get("NAVIGATION") == "wrong-page":
            url += "/old-page"
        print(json.dumps(json.dumps({"href": url})))
    elif "const requested" in source:
        print('{"status":"absent","source":"auto-probe"}')
    elif "scrollHeight:" in source:
        fixture = os.environ["REF_CENSUS"] if args[2].endswith("-lp-ref") else os.environ["IMPL_CENSUS"]
        print(pathlib.Path(fixture).read_text())
    else:
        print("{}")
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    browser.chmod(0o755)
    for name, body in {
        "sleep": "#!/bin/sh\nexit 0\n",
        "magick": "#!/bin/sh\nprintf '0\\n'\n",
        "dssim": "#!/bin/sh\nprintf '0\\n'\n",
    }.items():
        command = bin_dir / name
        command.write_text(body, encoding="utf-8")
        command.chmod(0o755)

    env = dict(
        os.environ,
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        REF_CENSUS=str(ref_fixture),
        IMPL_CENSUS=str(impl_fixture),
        NAVIGATION=navigation,
    )
    proc = subprocess.run(
        [
            "bash", str(script), "https://ref.example", "https://impl.example",
            "svg-proof", str(out), "0",
        ],
        cwd=script.parents[3],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc, json.loads((out / "live-parity.json").read_text(encoding="utf-8"))


def test_generated_data_svg_pairs_only_with_run_local_palette_variability(
    tmp_path: Path,
) -> None:
    proc, report = _run_live_parity_with_svg_fixtures(
        tmp_path, matching_structure=True
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert report["status"] == "pass"
    assert report["findings"] == []
    proof = report["equivalentImageVariants"][0]
    assert proof["impl"]["file"] == "snail-runtime.svg"
    assert proof["proof"]["kind"] == "run-local-reference-palette-variability"
    assert proof["proof"]["resources"][0]["path"].endswith("public-variant.svg")


def test_unmatched_data_svg_remains_blocking(tmp_path: Path) -> None:
    proc, report = _run_live_parity_with_svg_fixtures(
        tmp_path, matching_structure=False
    )

    assert proc.returncode == 1
    assert report["status"] == "fail"
    kinds = {row["kind"] for row in report["findings"]}
    assert {"missing-images", "extra-images", "image-file-count-drift"} <= kinds
    assert report["equivalentImageVariants"] == []


def test_failed_navigation_cannot_reuse_live_old_page(tmp_path: Path) -> None:
    proc, report = _run_live_parity_with_svg_fixtures(
        tmp_path, matching_structure=True, navigation="failed"
    )
    assert proc.returncode != 0
    assert report["status"] == "error"
    assert report["reason"] == "navigation-unverified"


def test_wrong_navigation_target_cannot_certify_parity(tmp_path: Path) -> None:
    proc, report = _run_live_parity_with_svg_fixtures(
        tmp_path, matching_structure=True, navigation="wrong-page"
    )
    assert proc.returncode != 0
    assert report["status"] == "error"
    assert "URL differs" in report["detail"]
