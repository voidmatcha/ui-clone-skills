"""The scoped-clone evidence producers: the shared computed-style contract,
`ui_clone.element_capture` (capture manifest), `element-state-capture.sh`, and
`python -m ui_clone.scoped_diff`."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from ui_clone import element_capture, scoped_diff, scoped_provenance
from ui_clone.computed_style_diff import (
    COMPUTED_STYLE_PROPS,
    SUBTREE_MAX_NODES,
    diff_records,
    diff_styles,
    properties_sha256,
)
from ui_clone.scoped_frames import (
    ARC_MAX_DELTA,
    CHANGE_PIXEL_DELTA_PERCENT,
    CHANGE_THRESHOLD_CEILING,
    JITTER_FRAMES,
    SSIM_THRESHOLD,
    change_threshold,
    first_last_change,
)

from ._scoped_fixtures import (
    IMPL_URL,
    REF_URL,
    build_scoped_evidence,
    element_target_payload,
    sample_resources,
    sample_styles,
    sample_subtree,
    set_mtime,
    write_computed,
    write_manifest,
    write_png,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "extract" / "element-state-capture.sh"
REF_ORIGIN = "https://example.org:443"
CLI = {"entry": "cli", "argv": ["record-clip"]}


REF_CODE = {
    "https://example.org/assets/app.js": ["script"],
    "https://example.org/assets/app.css": ["link"],
    "https://cdn.contentful-host.net/site/main.[hash].js": ["script"],
    "https://cdn.contentful-host.net/site/theme.css": ["stylesheet"],
}


def write_ref_inventory(ref: Path, code: dict[str, list[str]] | None = None) -> None:
    """A reference manifest (no frames yet) whose codeResources is what the
    reference captures loaded: its own bundle plus a CDN chunk and stylesheet."""
    element_capture._save_manifest(
        ref / "frames" / "ref",
        {
            "schemaVersion": element_capture.SCHEMA_VERSION,
            "producer": element_capture.PRODUCER,
            "side": "ref",
            "codeResources": REF_CODE if code is None else code,
            "entries": {},
        },
    )


def _ref_dir(tmp_path: Path, name: str = "hero", *, ref_manifest: bool = True) -> Path:
    """A scoped ref dir with the target record and (by default) a reference
    manifest carrying the code inventory; impl captures need both."""
    ref = tmp_path / "tmp" / "ref" / name
    ref.mkdir(parents=True, exist_ok=True)
    (ref / "element-target.json").write_text(json.dumps(element_target_payload()), encoding="utf-8")
    if ref_manifest:
        write_ref_inventory(ref)
    return ref


@pytest.fixture
def driver_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """What element-state-capture.sh exports before driving the recorder."""
    monkeypatch.setenv(scoped_provenance.DRIVER_ENV, str(scoped_provenance.DRIVER_SCRIPT))


# -- computed-style contract parity with computed-diff.sh ---------------------


def test_property_list_matches_computed_diff_sh() -> None:
    script = (ROOT / "skills" / "visual-debug" / "scripts" / "computed-diff.sh").read_text(encoding="utf-8")
    match = re.search(r"^PROPS='(\[.*?\])'$", script, re.M)
    assert match, "computed-diff.sh PROPS list not found"
    assert tuple(json.loads(match.group(1))) == COMPUTED_STYLE_PROPS
    assert re.search(r'FONT_SIZE_PROPS = \{"fontSize", "lineHeight", "width", "height", "letterSpacing"\}', script)


def test_diff_styles_applies_computed_diff_skip_rules() -> None:
    ref = sample_styles(border="0px none rgb(0, 0, 0)", fontFamily='"Inter", sans-serif', margin="")
    impl = sample_styles(border="0px solid rgb(0, 0, 0)", fontFamily="Inter, Arial", margin="none")
    assert diff_styles(ref, impl) == []
    assert diff_styles(ref, sample_styles(fontSize="15px")) == [{"property": "fontSize", "ref": "16px", "impl": "15px"}]
    assert diff_styles(ref, sample_styles(fontSize="15px"), ignore_font_size=True) == []
    assert diff_styles(ref, None) == [{"property": "-", "ref": "found", "impl": "NOT FOUND"}]
    assert properties_sha256() == properties_sha256(list(COMPUTED_STYLE_PROPS))
    assert properties_sha256(("display",)) != properties_sha256()


# -- page-level motion criteria are cited, not invented -----------------------


def test_motion_constants_match_video_transition_compare() -> None:
    vtc = (ROOT / "scripts" / "verify" / "video-transition-compare.sh").read_text(encoding="utf-8")
    align = (ROOT / "scripts" / "verify" / "lib" / "frame-align.sh").read_text(encoding="utf-8")
    assert f'SSIM_THRESHOLD="${{SSIM_THRESHOLD:-{SSIM_THRESHOLD:.2f}}}"' in vtc
    assert f'JITTER_FRAMES="${{UI_CLONE_VMC_JITTER_FRAMES:-{JITTER_FRAMES}}}"' in vtc
    assert f'max_delta="${{5:-{ARC_MAX_DELTA}}}"' in align
    assert f'pixel_delta="${{FRAME_CHANGE_PIXEL_DELTA_PERCENT:-{CHANGE_PIXEL_DELTA_PERCENT}}}"' in align
    assert f'[[ "$threshold" -gt {CHANGE_THRESHOLD_CEILING} ]] && threshold={CHANGE_THRESHOLD_CEILING}' in align
    assert "threshold=$((pixels / 20))" in align
    assert change_threshold(1440, 900) == CHANGE_THRESHOLD_CEILING
    assert change_threshold(40, 10) == 20


def test_first_last_change_reports_one_when_static(tmp_path: Path) -> None:
    paths = []
    for i in range(4):
        p = tmp_path / f"f-{i:04d}.png"
        write_png(p, (5, 5, 5), size=(16, 12))
        paths.append(p)
    assert first_last_change(paths) == (1, 1)
    write_png(paths[2], (200, 5, 5), size=(16, 12))
    assert first_last_change(paths) == (3, 4)


# -- element_capture manifest -------------------------------------------------


def _probe(url: str = IMPL_URL, **styles: str) -> dict[str, object]:
    return {
        "ok": True,
        "url": url,
        "selector": "section.hero",
        "matchCount": 1,
        "bbox": {"x": 2, "y": 1, "width": 8, "height": 8},
        "visibility": {"display": "block", "visibility": "visible", "opacity": "1", "hiddenBy": None},
        "visible": True,
        "viewport": {"innerWidth": 16, "innerHeight": 12, "dpr": 1},
        "computedStyle": sample_styles(**styles),
        "subtree": sample_subtree(),
        "descendantCount": 2,
        "resources": sample_resources(url),
        "resourceEntryCount": 2,
    }


def test_record_clip_crops_writes_computed_and_manifest(tmp_path: Path, driver_env: None) -> None:
    ref = _ref_dir(tmp_path)
    full = tmp_path / "full.png"
    image = Image.new("RGB", (16, 12), (1, 2, 3))
    for x in range(2, 10):
        for y in range(1, 9):
            image.putpixel((x, y), (200, 100, 50))
    image.save(full)
    entry = element_capture.record_clip(ref, "impl", "idle", _probe(), session="s1", full=full, invocation=CLI)
    clip = ref / "frames" / "impl" / "idle.png"
    with Image.open(clip) as saved:
        assert saved.size == (8, 8)
        assert saved.getpixel((0, 0)) == (200, 100, 50)
    computed = json.loads((ref / "frames" / "impl" / "idle.computed.json").read_text())
    assert computed["schemaVersion"] == element_capture.COMPUTED_SCHEMA_VERSION
    assert computed["computedStyle"]["fontSize"] == "16px"
    assert computed["properties"] == list(COMPUTED_STYLE_PROPS)
    assert [n["path"] for n in computed["subtree"]] == ["div[0]", "div[0]/span[0]"]
    assert computed["descendantCount"] == 2
    assert computed["resourceOrigins"] == {"http://localhost:5173": ["script", "stylesheet"]}
    manifest = element_capture.load_manifest(ref / "frames" / "impl")
    assert manifest is not None and manifest["side"] == "impl"
    assert manifest["entries"]["idle.png"] == entry
    assert manifest["codeResources"] == {
        "http://localhost:5173/assets/app.css": ["link"],
        "http://localhost:5173/assets/app.js": ["script"],
    }
    assert entry["matchCount"] == 1 and entry["visible"] is True and entry["visibility"]["hiddenBy"] is None
    assert entry["origin"] == "http://localhost:5173"
    assert entry["sha256"] == element_capture.sha256_file(clip)
    assert entry["computedSha256"] == element_capture.sha256_file(ref / "frames" / "impl" / "idle.computed.json")
    assert entry["resourceOrigins"] == computed["resourceOrigins"]
    assert entry["producer"]["entry"] == "cli" and entry["producer"]["argv"] == ["record-clip"]
    assert entry["producer"]["driver"]["sha256"] == scoped_provenance.driver_script_sha256()
    assert entry["producer"]["moduleSha256"] == scoped_provenance.module_sha256("ui_clone.element_capture")
    assert (
        element_capture.verify_side(
            ref / "frames" / "impl",
            {"idle.png": clip},
            reference_origin=REF_ORIGIN,
            side="impl",
            driver_sha256=scoped_provenance.driver_script_sha256(),
        )
        == []
    )


def test_record_clip_scales_bbox_by_screenshot_ratio(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    full = tmp_path / "full.png"
    Image.new("RGB", (32, 24), (1, 2, 3)).save(full)  # dpr 2 screenshot of a 16x12 viewport
    element_capture.record_clip(ref, "ref", "idle", _probe(url=REF_URL), session="s", full=full)
    with Image.open(ref / "frames" / "ref" / "idle.png") as clip:
        assert clip.size == (16, 16)


def test_api_record_is_not_the_cli(tmp_path: Path) -> None:
    """record_clip imported and called directly stamps `entry: api` without a
    driver, which verify_side rejects (the second line behind the Bash guard)."""
    ref = _ref_dir(tmp_path)
    full = tmp_path / "full.png"
    Image.new("RGB", (16, 12)).save(full)
    entry = element_capture.record_clip(ref, "impl", "idle", _probe(), session="s", full=full)
    assert entry["producer"]["entry"] == "api" and entry["producer"]["driver"] is None
    failures = element_capture.verify_side(
        ref / "frames" / "impl",
        {"idle.png": ref / "frames" / "impl" / "idle.png"},
        reference_origin=REF_ORIGIN,
        side="impl",
        driver_sha256=scoped_provenance.driver_script_sha256(),
    )
    assert [c for c, _ in failures] == ["impl-frame-producer"]
    assert "not the CLI" in failures[0][1]
    assert element_capture.producer_problem({"entry": "cli", "driver": {"sha256": "0" * 64}}, "1" * 64) == (
        "driver script differs from the shipped element-state-capture.sh"
    )
    record = {"entry": "cli", "driver": {"sha256": "1" * 64}, "moduleSha256": "2" * 64}
    assert element_capture.producer_problem(record, "1" * 64, "3" * 64) == (
        "recorder module differs from the shipped ui_clone/element_capture.py (release manifest)"
    )
    assert element_capture.producer_problem(record, "1" * 64, "2" * 64) is None


@pytest.mark.parametrize(
    "probe, message",
    [
        ({"ok": False, "url": IMPL_URL, "error": "selector not found"}, "probe failed"),
        ({**_probe(), "url": "about:blank"}, "page url"),
        ({**_probe(), "computedStyle": None}, "computedStyle"),
        ({**_probe(), "bbox": {"x": 0, "y": 0, "width": 0, "height": 0}}, "degenerate"),
        ({**_probe(), "bbox": {"x": 0, "y": 0, "width": 7, "height": 40}}, "degenerate"),
        ({**_probe(), "matchCount": 2}, "matches 2 element"),
        ({**_probe(), "matchCount": 0}, "matches 0 element"),
        ({k: v for k, v in _probe().items() if k != "matchCount"}, "matchCount not recorded"),
        (
            {
                **_probe(),
                "visible": False,
                "visibility": {"display": "none", "visibility": "visible", "opacity": "1", "hiddenBy": "target display:none"},
            },
            r"not visible in the probed state \(target display:none\)",
        ),
        ({k: v for k, v in _probe().items() if k != "resources"}, "resource list"),
        ({k: v for k, v in _probe().items() if k != "subtree"}, "subtree record"),
        ({**_probe(), "subtree": [{"path": "DIV", "computedStyle": {}}]}, "malformed"),
        ({**_probe(), "url": REF_URL}, "on the reference origin"),
        (
            {**_probe(), "resources": [*sample_resources(IMPL_URL), {"url": "https://www.example.org/main.js", "kind": "script"}]},
            "loads resources from the reference site",
        ),
        (
            {**_probe(), "resources": [{"url": "https://cdn.example.org/app.css", "kind": "link"}]},
            "loads resources from the reference site",
        ),
        (
            {**_probe(), "resources": [{"url": "http://localhost:5173/embed", "kind": "iframe"}, {"url": "https://example.org/", "kind": "iframe"}]},
            "loads resources from the reference site",
        ),
    ],
)
def test_record_clip_rejects_bad_probes(tmp_path: Path, probe: dict[str, object], message: str) -> None:
    ref = _ref_dir(tmp_path)
    full = tmp_path / "full.png"
    Image.new("RGB", (16, 12)).save(full)
    with pytest.raises(ValueError, match=message):
        element_capture.record_clip(ref, "impl", "idle", probe, session="s", full=full)
    assert not (ref / "frames" / "impl" / "capture-manifest.json").exists()
    assert not (ref / "frames" / "impl" / "idle.png").exists()


@pytest.mark.parametrize(
    "resources, refused",
    [
        # media / font hotlinks, from the reference host or elsewhere: allowed
        ([{"url": "https://example.org/hero.jpg", "kind": "img"}, {"url": "https://example.org/fonts/inter.woff2", "kind": "css"}, {"url": "https://example.org/clip.webm", "kind": "video"}], False),
        # the reference's own bundle (re-deployed content hash) from its host
        ([{"url": "https://example.org/assets/app.9f8e7d6c.js", "kind": "script"}], True),
        # the reference bundle served by a third-party CDN, and that CDN's stylesheet
        ([{"url": "https://cdn.contentful-host.net/site/main.0badf00d.js?x=1", "kind": "script"}], True),
        ([{"url": "https://cdn.contentful-host.net/site/theme.css", "kind": "link"}], True),
        # any other code from an origin that served reference code
        ([{"url": "https://cdn.contentful-host.net/site/vendor.js", "kind": "script"}], True),
        # unrelated third-party code the reference never loaded: allowed
        ([{"url": "https://www.googletagmanager.com/gtag/js?id=G-1", "kind": "script"}, {"url": "https://unpkg.com/gsap@3/dist/gsap.min.js", "kind": "script"}], False),
    ],
)
def test_impl_capture_reference_code_hotlinks(tmp_path: Path, resources: list[dict[str, str]], refused: bool) -> None:
    ref = _ref_dir(tmp_path)
    full = tmp_path / "full.png"
    Image.new("RGB", (16, 12)).save(full)
    probe = {**_probe(), "resources": [*sample_resources(IMPL_URL), *resources]}
    if refused:
        # A reference-host bundle trips the host rule first; CDN-hosted reference code the inventory rule.
        with pytest.raises(ValueError, match="reference runtime|resources from the reference site"):
            element_capture.record_clip(ref, "impl", "idle", probe, session="s", full=full)
        assert not (ref / "frames" / "impl" / "idle.png").exists()
        with pytest.raises(ValueError, match="reference runtime|resources from the reference site"):
            element_capture.record_video_frames(ref, "impl", "open", url=IMPL_URL, session="s", source=full, resources=probe["resources"])
    else:
        entry = element_capture.record_clip(ref, "impl", "idle", probe, session="s", full=full)
        assert entry["sha256"]
        manifest = element_capture.load_manifest(ref / "frames" / "impl")
        assert manifest is not None
        assert not any("hero.jpg" in url or "woff2" in url for url in manifest["codeResources"])


def test_impl_capture_needs_the_reference_inventory(tmp_path: Path) -> None:
    """Without a current reference manifest there is nothing to check the impl
    page's scripts against, so the impl capture is refused with the order."""
    ref = _ref_dir(tmp_path, ref_manifest=False)
    full = tmp_path / "full.png"
    Image.new("RGB", (16, 12)).save(full)
    with pytest.raises(ValueError, match="capture the reference side with element-state-capture.sh before"):
        element_capture.record_clip(ref, "impl", "idle", _probe(), session="s", full=full)
    # An older reference manifest (no code inventory) names the re-capture instead.
    element_capture._save_manifest(
        ref / "frames" / "ref",
        {"schemaVersion": 1, "producer": element_capture.PRODUCER, "side": "ref", "entries": {}},
    )
    with pytest.raises(ValueError, match="older element-state-capture.sh \\(schemaVersion 1"):
        element_capture.record_clip(ref, "impl", "idle", _probe(), session="s", full=full)
    assert element_capture.manifest_schema_problem(ref / "frames" / "ref") is not None
    assert element_capture.manifest_schema_problem(ref / "frames" / "impl") is None


def test_impl_capture_needs_the_target_record(tmp_path: Path) -> None:
    full = tmp_path / "full.png"
    Image.new("RGB", (16, 12)).save(full)
    with pytest.raises(ValueError, match="element-target.json missing"):
        element_capture.record_clip(tmp_path / "ref", "impl", "idle", _probe(), session="s", full=full)
    # The reference side needs no target record (it is captured first).
    element_capture.record_clip(tmp_path / "ref", "ref", "idle", _probe(url=REF_URL), session="s", full=full)


def test_reference_host_rule() -> None:
    assert scoped_provenance.reference_base_host("https://www.example.org:443") == "example.org"
    assert scoped_provenance.reference_base_host("https://app.example.org:443") == "app.example.org"
    assert scoped_provenance.host_matches_reference("cdn.example.org", "https://www.example.org:443")
    assert scoped_provenance.host_matches_reference("example.org", "https://www.example.org:443")
    assert not scoped_provenance.host_matches_reference("notexample.org", "https://www.example.org:443")
    assert not scoped_provenance.host_matches_reference("cdn.example.org", "https://app.example.org:443")
    assert not scoped_provenance.host_matches_reference("localhost", "https://example.org:443")
    origins = scoped_provenance.resource_origins(
        [{"url": "https://Example.org/a.js", "kind": "script"}, {"url": "data:text/plain,x", "kind": "img"}, {"url": "https://example.org/b.css", "kind": "link"}]
    )
    assert origins == {"https://example.org:443": ["script", "stylesheet"]}
    assert scoped_provenance.resource_origins(None) is None
    assert scoped_provenance.resource_origins(["x"]) is None
    assert scoped_provenance.reference_loads(origins, REF_ORIGIN) == ["https://example.org:443 (script,stylesheet)"]
    # Media and fonts from the reference host are its preserved asset URLs, not its runtime.
    media = scoped_provenance.resource_origins(
        [
            {"url": "https://example.org/hero.jpg", "kind": "img"},
            {"url": "https://cdn.example.org/fonts/inter.woff2", "kind": "css"},
            {"url": "https://example.org/clip.mp4", "kind": "video"},
            {"url": "https://example.org/fonts/inter.woff2", "kind": "preload"},
        ]
    )
    assert scoped_provenance.reference_loads(media, REF_ORIGIN) == []
    runtime = scoped_provenance.resource_origins(
        [
            {"url": "https://example.org/hero.jpg", "kind": "img"},
            {"url": "https://example.org/api/page", "kind": "fetch"},
            {"url": "https://example.org/embed", "kind": "iframe"},
            {"url": "https://example.org/theme", "kind": "stylesheet"},
            {"url": "https://example.org/vendor.mjs?v=3", "kind": "preload"},
        ]
    )
    assert scoped_provenance.reference_loads(runtime, REF_ORIGIN) == ["https://example.org:443 (fetch,iframe,script,stylesheet)"]


def test_code_resource_rules() -> None:
    """Reference script/stylesheet hotlinks (any host) are caught by normalized
    URL or by a non-first-party origin that served reference code; media,
    fonts, and code the reference never loaded are not."""
    norm = scoped_provenance.normalize_code_url
    assert norm("https://CDN.Example.com:443/a/main.3f2a1b9c.js?v=2#x") == "https://cdn.example.com/a/main.[hash].js"
    assert norm("https://cdn.example.com:8443/chunk-A1b2C3d4.css") == "https://cdn.example.com:8443/chunk-[hash].css"
    assert norm("https://cdn.example.com/bootstrap.min.js") == "https://cdn.example.com/bootstrap.min.js"
    assert norm("data:text/javascript,1") is None and norm(None) is None
    assert scoped_provenance.is_code_resource("https://h/x", "script")
    assert scoped_provenance.is_code_resource("https://h/x.css?x=1", "link")
    assert scoped_provenance.is_code_resource("https://h/x.mjs", "other")
    assert not scoped_provenance.is_code_resource("https://h/x.woff2", "css")
    assert not scoped_provenance.is_code_resource("https://h/x.jpg", "img")
    inventory = scoped_provenance.code_resources(
        [
            {"url": "https://example.org/assets/app.abc12345.js", "kind": "script"},
            {"url": "https://example.org/hero.jpg", "kind": "img"},
            {"url": "https://cdn.contentful-host.net/site/theme.css", "kind": "link"},
            {"url": "https://fonts.gstatic.com/inter.woff2", "kind": "css"},
        ]
    )
    assert inventory == {
        "https://cdn.contentful-host.net/site/theme.css": ["link"],
        "https://example.org/assets/app.[hash].js": ["script"],
    }
    assert scoped_provenance.code_resources(None) is None
    assert scoped_provenance.code_origins(inventory) == {"https://cdn.contentful-host.net", "https://example.org"}
    hits = scoped_provenance.reference_code_hits
    page = "http://localhost:5173/"
    # media hotlink (any host) and the impl's own bundle: allowed
    assert hits({"http://localhost:5173/assets/index.js": ["script"]}, page, inventory) == []
    # the reference CDN bundle (re-deployed hash) and the reference stylesheet: rejected
    assert hits({"https://example.org/assets/app.[hash].js": ["script"]}, page, inventory) == [
        "https://example.org/assets/app.[hash].js (script; reference code)"
    ]
    assert hits({"https://cdn.contentful-host.net/site/theme.css": ["stylesheet"]}, page, inventory) == [
        "https://cdn.contentful-host.net/site/theme.css (stylesheet; reference code)"
    ]
    # another file from a CDN origin that served reference code: rejected
    assert hits({"https://cdn.contentful-host.net/site/other.js": ["script"]}, page, inventory) == [
        "https://cdn.contentful-host.net/site/other.js (script; https://cdn.contentful-host.net serves reference code)"
    ]
    # third-party code the reference never loaded (analytics, a locally hosted lib): allowed
    assert hits({"https://www.googletagmanager.com/gtag/js": ["script"], "https://unpkg.com/gsap@3/dist/gsap.min.js": ["script"]}, page, inventory) == []
    # the impl's own origin is first party even when the reference inventory names it
    assert hits({"http://localhost:5173/x.js": ["script"]}, page, {"http://localhost:5173/y.js": ["script"]}) == []
    merged = scoped_provenance.merge_code_resources(inventory, {"https://example.org/assets/app.[hash].js": ["modulepreload"]}, None)
    assert merged["https://example.org/assets/app.[hash].js"] == ["modulepreload", "script"]


def test_record_video_frames_and_verify_origin_rules(tmp_path: Path, driver_env: None) -> None:
    ref = _ref_dir(tmp_path)
    frames = ref / "frames" / "impl"
    for i in (1, 2):
        write_png(frames / f"open-{i:04d}.png", (i, 0, 0))
    write_png(frames / "idle.png", (9, 9, 9))
    source = ref / "open.webm"
    source.write_bytes(b"\x1aE\xdf\xa3")
    names = element_capture.record_video_frames(
        ref, "impl", "open", url=IMPL_URL, session="s", source=source, resources=sample_resources(IMPL_URL), invocation=CLI
    )
    assert names == ["open-0001.png", "open-0002.png"]
    manifest = element_capture.load_manifest(frames)
    assert manifest is not None
    assert manifest["entries"]["open-0001.png"]["source"] == "open.webm"
    assert manifest["entries"]["open-0001.png"]["resourceOrigins"] == {"http://localhost:5173": ["script", "stylesheet"]}
    driver = scoped_provenance.driver_script_sha256()
    listing = {n: frames / n for n in ("open-0001.png", "open-0002.png", "idle.png")}
    codes = [c for c, _ in element_capture.verify_side(frames, listing, reference_origin=REF_ORIGIN, side="impl", driver_sha256=driver)]
    assert codes == ["impl-frame-unmanifested"]
    # Recorded from the reference site, or from a page that loads it: refused up front.
    with pytest.raises(ValueError, match="on the reference origin"):
        element_capture.record_video_frames(ref, "impl", "open", url=REF_URL, session="s", source=source, resources=[])
    with pytest.raises(ValueError, match="loads resources from the reference site"):
        element_capture.record_video_frames(
            ref, "impl", "open", url=IMPL_URL, session="s", source=source,
            resources=[{"url": "https://example.org/bundle.js", "kind": "script"}],
        )
    with pytest.raises(ValueError, match="resource list"):
        element_capture.record_video_frames(ref, "impl", "open", url=IMPL_URL, session="s", source=source)
    with pytest.raises(FileNotFoundError):
        element_capture.record_video_frames(ref, "impl", "close", url=IMPL_URL, session="s", source=source, resources=[])
    # A manifest entry that records reference loads (edited or old) fails verification.
    manifest["entries"]["open-0001.png"]["resourceOrigins"] = {"https://example.org:443": ["script"]}
    element_capture._save_manifest(frames, manifest)
    listing.pop("idle.png")
    codes = [c for c, _ in element_capture.verify_side(frames, listing, reference_origin=REF_ORIGIN, side="impl", driver_sha256=driver)]
    assert codes == ["impl-frame-loads-reference"]


def test_origin_of_normalizes_default_ports() -> None:
    assert element_capture.origin_of("https://Example.org/x") == "https://example.org:443"
    assert element_capture.origin_of("http://localhost:5173/") == "http://localhost:5173"
    assert element_capture.origin_of("about:blank") is None
    assert element_capture.origin_of(None) is None


# -- element-state-capture.sh with a fake agent-browser -----------------------


def _fake_agent_browser(tmp_path: Path, envelope: dict[str, object], screenshot: Path | None) -> Path:
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    payload = json.dumps(envelope)
    (bin_dir / "payload.json").write_text(payload, encoding="utf-8")
    shot = str(screenshot) if screenshot else ""
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'PAYLOAD="{bin_dir / "payload.json"}"\n'
        f'SHOT="{shot}"\n'
        'cmd=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        "    --session) shift 2 ;;\n"
        '    eval) cmd=eval; shift; break ;;\n'
        '    screenshot) cmd=screenshot; shift; break ;;\n'
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        'if [ "$cmd" = eval ]; then cat "$PAYLOAD"; fi\n'
        'if [ "$cmd" = screenshot ]; then [ -n "$SHOT" ] && cp "$SHOT" "$1"; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return bin_dir


def _run_script(bin_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "LC_ALL": "C", "LANG": "C"}
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)


def test_script_clip_mode_records_frame_and_manifest(tmp_path: Path) -> None:
    ref = _ref_dir(tmp_path)
    full = tmp_path / "viewport.png"
    Image.new("RGB", (16, 12), (7, 8, 9)).save(full)
    envelope = {"success": True, "data": {"origin": "http://localhost:5173", "result": _probe()}}
    bin_dir = _fake_agent_browser(tmp_path, envelope, full)
    proc = _run_script(bin_dir, "clip", "s1", IMPL_URL, "section.hero", str(ref), "impl", "idle")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (ref / "frames" / "impl" / "idle.png").is_file()
    assert (ref / "frames" / "impl" / "idle.computed.json").is_file()
    manifest = element_capture.load_manifest(ref / "frames" / "impl")
    assert manifest is not None and manifest["entries"]["idle.png"]["session"] == "s1"
    producer = manifest["entries"]["idle.png"]["producer"]
    assert producer["entry"] == "cli" and producer["argv"][:3] == ["record-clip", str(ref), "impl"]
    assert producer["driver"]["sha256"] == scoped_provenance.driver_script_sha256()
    assert element_capture.producer_problem(producer, scoped_provenance.driver_script_sha256()) is None


def test_script_clip_mode_rejects_wrong_origin(tmp_path: Path) -> None:
    ref = tmp_path / "tmp" / "ref" / "hero"
    ref.mkdir(parents=True)
    full = tmp_path / "viewport.png"
    Image.new("RGB", (16, 12)).save(full)
    envelope = {"success": True, "data": {"origin": "https://example.org", "result": _probe(url=REF_URL)}}
    bin_dir = _fake_agent_browser(tmp_path, envelope, full)
    proc = _run_script(bin_dir, "clip", "s1", IMPL_URL, "section.hero", str(ref), "impl", "idle")
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "expected page origin" in proc.stderr
    assert not (ref / "frames" / "impl" / "idle.png").exists()


def test_script_usage_errors() -> None:
    proc = subprocess.run([str(SCRIPT), "clip", "s"], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
    proc = subprocess.run([str(SCRIPT), "video", "s", IMPL_URL, "/nonexistent", "nope", "x.webm", "open"], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2 and "ref or impl" in proc.stderr


def test_script_video_mode_extracts_frames(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for video frame extraction")
    ref = tmp_path / "tmp" / "ref" / "modal"
    ref.mkdir(parents=True)
    recording = ref / "open.webm"
    encode = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=16x16:d=0.1:r=10", "-c:v", "libvpx", str(recording)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if encode.returncode != 0 or not recording.exists():
        pytest.skip("ffmpeg cannot encode a webm fixture here")
    (ref / "element-target.json").write_text(json.dumps(element_target_payload()), encoding="utf-8")
    write_ref_inventory(ref)
    result = {"ok": True, "url": IMPL_URL, "resources": sample_resources(IMPL_URL), "resourceEntryCount": 2}
    envelope = {"success": True, "data": {"origin": "http://localhost:5173", "result": result}}
    bin_dir = _fake_agent_browser(tmp_path, envelope, None)
    proc = _run_script(bin_dir, "video", "s1", IMPL_URL, str(ref), "impl", str(recording), "open")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    frames = sorted((ref / "frames" / "impl").glob("open-*.png"))
    assert len(frames) >= 2
    manifest = element_capture.load_manifest(ref / "frames" / "impl")
    assert manifest is not None
    entry = manifest["entries"][frames[0].name]
    assert entry["source"] == "open.webm"
    assert entry["resourceOrigins"] == {"http://localhost:5173": ["script", "stylesheet"]}
    assert entry["producer"]["entry"] == "cli" and entry["producer"]["driver"]["sha256"] == scoped_provenance.driver_script_sha256()


def test_script_embedded_eval_is_valid_javascript(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to syntax-check the embedded browser eval")
    script = SCRIPT.read_text(encoding="utf-8")
    res_start = script.index("\n", script.index("<<'JS'")) + 1
    resources_js = script[res_start : script.index("\nJS\n", res_start)]
    assert 'performance.getEntriesByType("resource")' in resources_js
    assert 'querySelectorAll("iframe[src],frame[src]")' in resources_js
    for block in ("read -r -d '' EVAL_JS", "read -r -d '' VIDEO_JS"):
        start = script.index("(() => {", script.index(block))
        end = script.index("\nJS\n", start)
        js = (
            script[start:end]
            .replace("${SELECTOR_JSON}", '"footer"')
            .replace("${PROPS_JSON}", '["display"]')
            .replace("${MAX_NODES}", "40")
            .replace("${RESOURCES_JS}", resources_js)
        )
        assert "collectResources()" in js
        js_path = tmp_path / "capture.js"
        js_path.write_text(js, encoding="utf-8")
        result = subprocess.run(["node", "--check", str(js_path)], check=False, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    assert "descendantCount" in script and "subtree.push" in script
    assert "agent-browser --session \"$SESSION\" eval --json" in script
    assert 'python3 "$ORIGIN_VALIDATOR" "$EXPECTED_URL"' in script
    assert 'export UI_CLONE_CAPTURE_DRIVER="$SCRIPT_DIR/' in script


def test_script_clip_mode_refuses_reference_loads(tmp_path: Path) -> None:
    ref = _ref_dir(tmp_path)
    full = tmp_path / "viewport.png"
    Image.new("RGB", (16, 12), (7, 8, 9)).save(full)
    probe = {**_probe(), "resources": [{"url": "https://example.org/_next/static/chunks/main.js", "kind": "script"}]}
    envelope = {"success": True, "data": {"origin": "http://localhost:5173", "result": probe}}
    bin_dir = _fake_agent_browser(tmp_path, envelope, full)
    proc = _run_script(bin_dir, "clip", "s1", IMPL_URL, "section.hero", str(ref), "impl", "idle")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "loads resources from the reference site" in proc.stderr
    assert not (ref / "frames" / "impl" / "idle.png").exists()
    assert not (ref / "frames" / "impl" / "capture-manifest.json").exists()


# -- scoped_diff producer -----------------------------------------------------


def test_scoped_diff_cli_writes_provenance(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ref = build_scoped_evidence(tmp_path)
    (ref / "pixel-perfect-diff.json").unlink()
    assert scoped_diff.main([str(ref), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["producer"] == "ui_clone.scoped_diff" and data["schemaVersion"] == 3
    assert data["result"] == "pass" and data["mismatches"] == 0, data["problems"]
    assert data["propertiesSha256"] == properties_sha256()
    assert {
        "element-target.json",
        "frames/ref/capture-manifest.json",
        "frames/impl/idle.png",
        "frames/impl/idle.computed.json",
        "proxy-mirror-check.json",
        "bundle-paste-check.json",
    } <= set(data["inputs"])
    assert any(p.endswith("Hero.tsx") for p in data["sources"])
    assert [r["state"] for r in data["elements"]] == ["active", "idle"]
    assert data["elements"][0]["subtreeNodes"] == 2
    assert data["producerRecord"]["entry"] == "cli" and data["producerRecord"]["argv"] == [str(ref), "--json"]
    assert data["producerRecord"]["moduleSha256"] == scoped_provenance.module_sha256("ui_clone.scoped_diff")
    assert data["noCheat"]["pageLevel"]["proxy-mirror-check"]["status"] == "pass"
    assert data["noCheat"]["pageLevel"]["bundle-paste-check"]["status"] == "pass"
    assert data["noCheat"]["sourceScan"] == {"files": 1, "findings": []}
    assert (ref / "proxy-mirror-check.json").is_file() and (ref / "bundle-paste-check.json").is_file()
    written = json.loads((ref / "pixel-perfect-diff.json").read_text())
    assert written["recordSha256"] == scoped_provenance.record_checksum(written)
    assert scoped_diff.provenance_problems(None) == ["no implementation provenance recorded"]


def test_scoped_diff_records_reference_runtime_signals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A proxy server, a reference iframe in the app shell, and a reference
    bundle in the component all fail the diff and are named."""
    ref = build_scoped_evidence(tmp_path)
    impl = tmp_path / "impl"
    (impl / "server.js").write_text(
        'const upstream = "https://example.org";\nmodule.exports = (req) => fetch(upstream + req.url);\n', encoding="utf-8"
    )
    (impl / "index.html").write_text('<html><body><iframe src="https://www.example.org/pricing"></iframe></body></html>\n', encoding="utf-8")
    (impl / "src" / "components" / "Hero.tsx").write_text(
        'export default function Hero() { return <script src="https://cdn.example.org/main.js" /> }\n', encoding="utf-8"
    )
    assert scoped_diff.main([str(ref)]) == 1
    out = capsys.readouterr().out
    data = json.loads((ref / "pixel-perfect-diff.json").read_text())
    assert data["result"] == "fail"
    rules = {f["rule"] for f in data["noCheat"]["sourceScan"]["findings"]}
    assert rules == {"reference-load", "upstream-proxy"}
    assert data["noCheat"]["pageLevel"]["proxy-mirror-check"]["status"] == "fail"
    assert "implementation provenance: proxy-mirror-check: fail" in out
    assert "reference-load" in out and "Hero.tsx" in out
    files = {Path(f["file"]).name for f in data["noCheat"]["sourceScan"]["findings"]}
    assert files == {"server.js", "index.html", "Hero.tsx"}
    assert str(impl / "index.html") in data["sources"] and str(impl / "server.js") in data["sources"]


def test_source_scan_rules(tmp_path: Path) -> None:
    origin = REF_ORIGIN
    cases = {
        "attribution.tsx": ("// cloned from https://example.org/pricing\nexport default 1\n", set()),
        "other-host.tsx": ('<img src="https://images.ctfassets.net/a.png" />\n', set()),
        "notexample.tsx": ('<img src="https://notexample.org/a.png" />\n', set()),
        "css.css": ("@import url(https://example.org/style.css);\n.a { background: url('https://cdn.example.org/bg.png') }\n", {"reference-load"}),
        "fetch.ts": ("const r = await fetch(`https://api.example.org/v1`)\n", {"reference-load"}),
        "mirror.tsx": ("const html = document.documentElement.outerHTML\n", {"document-mirror"}),
        "raw.tsx": ("import markup from './page.html?raw'\nexport default () => <div dangerouslySetInnerHTML={{ __html: markup }} />\n", {"raw-html-mount"}),
        "raw-only.tsx": ("import markup from './page.html?raw'\nconsole.log(markup.length)\n", set()),
        "proxy.mjs": ("import { createProxyMiddleware } from 'http-proxy-middleware'\n", {"upstream-proxy"}),
    }
    for name, (text, _) in cases.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    for name, (_, expected) in cases.items():
        found = {f["rule"] for f in scoped_provenance.scan_sources([tmp_path / name], origin)}
        assert found == expected, name
    assert scoped_provenance.scan_sources([tmp_path / "missing.tsx"], origin) == []
    assert scoped_provenance.scan_sources([tmp_path / "css.css"], "") == []


def test_page_level_checks_run_against_impl_root(tmp_path: Path) -> None:
    ref = tmp_path / "tmp" / "ref" / "hero"
    ref.mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "public" / "_next" / "static" / "chunks").mkdir(parents=True)
    (impl / "public" / "_next" / "static" / "chunks" / "main.js").write_text("x", encoding="utf-8")
    results = scoped_provenance.run_page_level_checks(ref, impl)
    assert results["bundle-paste-check"]["status"] == "fail" and results["bundle-paste-check"]["findings"] >= 1
    assert results["proxy-mirror-check"]["status"] == "pass" and results["proxy-mirror-check"]["exit"] == 0
    assert (ref / "bundle-paste-check.json").is_file()
    problems = scoped_diff.provenance_problems({"pageLevel": results, "sourceScan": {"files": 0, "findings": []}})
    assert problems == ["bundle-paste-check: fail"]
    skipped = scoped_provenance.run_page_level_checks(ref, None)
    assert {v["status"] for v in skipped.values()} == {"skip"}


# -- subtree diff ---------------------------------------------------------------


def _record(subtree: list[Any] | None, count: int | None = None, **styles: str) -> dict[str, Any]:
    record: dict[str, Any] = {"computedStyle": sample_styles(**styles)}
    if subtree is not None:
        record["subtree"] = subtree
        record["descendantCount"] = len(subtree) if count is None else count
    return record


def test_diff_records_matches_subtree_by_path() -> None:
    assert diff_records(_record(sample_subtree()), _record(sample_subtree())) == []
    rows = diff_records(_record(sample_subtree()), _record(sample_subtree(color="rgb(9, 9, 9)")))
    assert rows == [{"path": "div[0]/span[0]", "property": "color", "ref": "rgb(0, 0, 0)", "impl": "rgb(9, 9, 9)"}]
    # Target mismatch keeps path "" and comes first.
    rows = diff_records(_record(sample_subtree(), display="flex"), _record(sample_subtree(color="rgb(9, 9, 9)")))
    assert [(r["path"], r["property"]) for r in rows] == [("", "display"), ("div[0]/span[0]", "color")]


def test_diff_records_reports_structure_mismatch_precisely() -> None:
    ref_nodes = sample_subtree()
    impl_nodes = [ref_nodes[0], {"path": "div[0]/a[0]", "tag": "a", "computedStyle": sample_styles()}]
    rows = diff_records(_record(ref_nodes), _record(impl_nodes))
    assert rows == [
        {"path": "div[0]/a[0]", "property": "-", "ref": "NOT FOUND", "impl": "found"},
        {"path": "div[0]/span[0]", "property": "-", "ref": "found", "impl": "NOT FOUND"},
    ]
    # Same recorded nodes but a different total (truncated at the cap on one side).
    rows = diff_records(_record(ref_nodes, count=SUBTREE_MAX_NODES + 5), _record(ref_nodes))
    assert rows == [{"path": "", "property": "descendantCount", "ref": str(SUBTREE_MAX_NODES + 5), "impl": "2"}]


def test_diff_records_requires_subtree_on_both_sides() -> None:
    rows = diff_records(_record(sample_subtree()), _record(None))
    assert rows == [{"path": "", "property": "subtree", "ref": "recorded", "impl": "NOT RECORDED"}]
    rows = diff_records(_record(None), _record(sample_subtree()))
    assert rows == [{"path": "", "property": "subtree", "ref": "NOT RECORDED", "impl": "recorded"}]
    malformed = [{"path": "DIV", "computedStyle": {}}]
    rows = diff_records(_record(malformed), _record(sample_subtree()))
    assert rows[0]["property"] == "subtree" and rows[0]["ref"] == "NOT RECORDED"
    # A missing record on one side is still the single `-` row of diff_styles.
    assert diff_records(None, _record(sample_subtree())) == [{"path": "", "property": "-", "ref": "NOT FOUND", "impl": "found"}]


def test_scoped_diff_reports_style_and_pixel_mismatches(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ref = build_scoped_evidence(tmp_path)
    write_computed(ref, "impl", "idle", sample_styles(color="rgb(255, 0, 0)"))
    write_png(ref / "frames" / "impl" / "active.png", (99, 0, 0))
    set_mtime(ref / "frames" / "impl" / "active.png", time.time() - 50)
    write_manifest(ref, "impl")
    assert scoped_diff.main([str(ref)]) == 1
    out = capsys.readouterr().out
    assert "scoped_diff FAIL" in out
    assert "color: ref `rgb(0, 0, 0)` vs impl `rgb(255, 0, 0)`" in out
    data = json.loads((ref / "pixel-perfect-diff.json").read_text())
    rows = {r["state"]: r for r in data["elements"]}
    assert rows["idle"]["mismatches"] == 1 and rows["idle"]["status"] == "fail"
    assert rows["active"]["ae"] == 48 and rows["active"]["status"] == "fail"
    assert data["mismatches"] == 1


def test_scoped_diff_refuses_without_captures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ref = tmp_path / "tmp" / "ref" / "hero"
    ref.mkdir(parents=True)
    assert scoped_diff.main([str(ref)]) == 2
    assert "element-target.json" in capsys.readouterr().err
    ref = build_scoped_evidence(tmp_path, "other")
    (ref / "pixel-perfect-diff.json").unlink()
    (ref / "frames" / "impl" / "idle.computed.json").unlink()
    (ref / "frames" / "impl" / "capture-manifest.json").unlink()
    assert scoped_diff.main([str(ref)]) == 1
    data = json.loads((ref / "pixel-perfect-diff.json").read_text())
    assert data["result"] == "fail"
    assert any("capture-manifest.json missing" in p for p in data["problems"])
    assert any("idle.computed.json" in p for p in data["problems"])


def test_scoped_diff_module_is_importable_as_cli() -> None:
    proc = subprocess.run([sys.executable, "-m", "ui_clone.scoped_diff", "--help"], capture_output=True, text=True, cwd=ROOT, timeout=30)
    assert proc.returncode == 0 and "pixel-perfect-diff.json" in proc.stdout


def test_fetch_only_bundles_are_classified_by_content_type_and_origin() -> None:
    """A bundle the page pulled through fetch()/import() from an extensionless
    URL is code when resource timing reports a code content type; any
    non-media load from a non-first-party origin that served reference code
    is a hit even without a content type."""
    is_code = scoped_provenance.is_code_resource
    assert is_code("https://cdn.example.net/chunk/abc", "fetch", "application/javascript; charset=utf-8")
    assert is_code("https://cdn.example.net/chunk/abc", "other", "text/javascript")
    assert is_code("https://cdn.example.net/styles/main", "fetch", "text/css")
    assert is_code("https://cdn.example.net/wasm/core", "fetch", "application/wasm")
    assert not is_code("https://cdn.example.net/api/page", "fetch", "application/json")
    assert not is_code("https://cdn.example.net/chunk/abc", "fetch", "")
    assert not is_code("https://cdn.example.net/chunk/abc", "fetch", None)
    assert not is_code("https://fonts.googleapis.com/css2?family=Inter", "fetch", "text/css")  # font service stylesheet
    assert is_code("https://fonts.googleapis.com/loader", "fetch", "text/javascript")
    assert scoped_provenance.resource_kind("https://h/chunk", "fetch", "text/css") == "stylesheet"
    assert scoped_provenance.resource_kind("https://h/chunk", "fetch", "application/javascript") == "script"
    assert scoped_provenance.resource_kind("https://h/api", "fetch", "application/json") == "fetch"
    resources = [
        {"url": "https://cdn.example.net/chunk/abc", "kind": "fetch", "contentType": "application/javascript"},
        {"url": "https://cdn.example.net/data/page", "kind": "fetch", "contentType": "application/json"},
        {"url": "https://cdn.example.net/hero.jpg", "kind": "img"},
        {"url": "https://example.org/assets/app.js", "kind": "script"},
    ]
    inventory = scoped_provenance.code_resources(resources)
    assert inventory == {"https://cdn.example.net/chunk/abc": ["fetch"], "https://example.org/assets/app.js": ["script"]}
    assert scoped_provenance.resource_origins(resources) == {
        "https://cdn.example.net:443": ["fetch", "img", "script"],
        "https://example.org:443": ["script"],
    }
    hits = scoped_provenance.reference_code_hits
    page = "http://localhost:5173/"
    # impl fetches an extensionless chunk from the CDN that served reference code: hit via origin
    impl_origins = {"https://cdn.example.net:443": ["fetch"], "http://localhost:5173:80": ["script"]}
    assert hits({}, page, inventory, impl_origins) == [
        "https://cdn.example.net (fetch; non-media load from an origin that serves reference code)"
    ]
    # media from that origin, and non-media from an origin the reference used for no code: allowed
    assert hits({}, page, inventory, {"https://cdn.example.net:443": ["img", "font", "css"]}) == []
    assert hits({}, page, inventory, {"https://api.other.net:443": ["fetch"]}) == []
    # the impl's own origin is never a hit even when the reference inventory names it
    assert hits({}, page, {"http://localhost:5173/x.js": ["script"]}, {"http://localhost:5173:80": ["fetch"]}) == []
    assert hits({}, page, inventory, None) == [] and hits({}, page, inventory, "bad") == []


def test_impl_capture_refuses_non_media_fetch_from_reference_code_origin(tmp_path: Path) -> None:
    ref = _ref_dir(tmp_path)
    write_manifest(
        ref,
        "ref",
        code_resources={"https://cdn.example.net/chunk/[hash]": ["fetch"], f"{REF_URL}assets/app.js": ["script"]},
    )
    full = tmp_path / "full.png"
    Image.new("RGB", (16, 12)).save(full)
    chunk = {"url": "https://cdn.example.net/chunk/zzz", "kind": "fetch", "contentType": ""}
    probe = {**_probe(), "resources": [*sample_resources(IMPL_URL), chunk]}
    with pytest.raises(ValueError, match="fetches from the origin that serves them"):
        element_capture.record_clip(ref, "impl", "idle", probe, session="s", full=full)
    poster = {"url": "https://cdn.example.net/poster.jpg", "kind": "img", "contentType": "image/jpeg"}
    probe = {**_probe(), "resources": [*sample_resources(IMPL_URL), poster]}
    assert element_capture.record_clip(ref, "impl", "idle", probe, session="s", full=full)["sha256"]


def test_scoped_check_rechecks_non_media_origin_loads(tmp_path: Path) -> None:
    from ui_clone import scoped_check
    from ui_clone.element_capture import origin_of

    ref = build_scoped_evidence(tmp_path)
    write_manifest(ref, "ref", code_resources={"https://cdn.example.net/chunk/[hash]": ["fetch"], f"{REF_URL}assets/app.js": ["script"]})
    impl_origin = origin_of(IMPL_URL)
    assert impl_origin is not None
    write_manifest(ref, "impl", resource_origins={impl_origin: ["script"], "https://cdn.example.net:443": ["xmlhttprequest"]})
    failures = {f["code"]: f["reason"] for f in scoped_check.check(ref)["failures"]}
    assert "impl-loads-reference-code" in failures
    assert "non-media load from an origin that serves reference code" in failures["impl-loads-reference-code"]
