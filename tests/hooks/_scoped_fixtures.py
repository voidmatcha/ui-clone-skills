"""Scoped-clone evidence builders shared by the scoped run / scoped_check tests.

The evidence is built the way the canonical producers build it: frames get a
`capture-manifest.json` entry through `ui_clone.element_capture` (reference
frames from the reference origin, impl frames from a local origin), resting
states get `<state>.computed.json`, and `pixel-perfect-diff.json` is produced
by `ui_clone.scoped_diff.build`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from PIL import Image

from ui_clone import scoped_diff, scoped_ledger
from ui_clone.computed_style_diff import COMPUTED_STYLE_PROPS
from ui_clone.element_capture import (
    COMPUTED_SCHEMA_VERSION,
    PRODUCER,
    SCHEMA_VERSION,
    _save_manifest,
    computed_name,
    origin_of,
    sha256_file,
)
from ui_clone.scoped_provenance import DRIVER_SCRIPT, driver_script_sha256, module_sha256

REF_URL = "https://example.org/"
IMPL_URL = "http://localhost:5173/"


def sample_resources(url: str) -> list[dict[str, str]]:
    """What the capture eval reports for a page that loads only its own bundle."""
    return [{"url": f"{url}assets/app.js", "kind": "script"}, {"url": f"{url}assets/app.css", "kind": "link"}]


def sample_resource_origins(side: str) -> dict[str, list[str]]:
    origin = origin_of(REF_URL if side == "ref" else IMPL_URL)
    assert origin is not None
    return {origin: ["script", "stylesheet"]}


def sample_code_resources(side: str) -> dict[str, list[str]]:
    """The manifest-level code inventory the recorder derives from sample_resources()."""
    url = REF_URL if side == "ref" else IMPL_URL
    return {f"{url}assets/app.css": ["link"], f"{url}assets/app.js": ["script"]}


SAMPLE_VISIBILITY = {"display": "block", "visibility": "visible", "opacity": "1", "hiddenBy": None}


def sample_subtree(**overrides: str) -> list[dict[str, object]]:
    """Two-node subtree (`div[0]` and its child `div[0]/span[0]`) with sample styles."""
    return [
        {"path": "div[0]", "tag": "div", "computedStyle": sample_styles()},
        {"path": "div[0]/span[0]", "tag": "span", "computedStyle": sample_styles(**overrides)},
    ]


def cli_producer_record(module: str = "ui_clone.element_capture", argv: list[str] | None = None) -> dict[str, object]:
    """The producer record the canonical CLI stamps (driven by the shipped script)."""
    return {
        "entry": "cli",
        "argv": argv or [],
        "moduleSha256": module_sha256(module),
        "driver": {"path": str(DRIVER_SCRIPT), "sha256": driver_script_sha256()},
    }


def element_target_payload(
    selector: str = "section.hero", role: str | None = None, *, schema_version: int = 2
) -> dict[str, object]:
    """Shape written by scripts/extract/element-evidence.sh (schemaVersion 2;
    1 reproduces a record from the older script without target sanity)."""
    attributes: dict[str, str] = {}
    if role is not None:
        attributes["role"] = role
    sanity: dict[str, object] = (
        {"matchCount": 1, "visibility": dict(SAMPLE_VISIBILITY), "visible": True} if schema_version >= 2 else {}
    )
    return {
        "schemaVersion": schema_version,
        "ok": True,
        "url": REF_URL,
        "annotation": {
            "id": "element-probe",
            "selector": selector,
            "selectorCandidates": [selector],
            "text": "Hero",
            **sanity,
            "bbox": {"x": 0, "y": 0, "width": 1440, "height": 900},
            "attributes": attributes,
            "computedStyle": {},
            "timeline": [],
            "animations": [],
        },
    }


def write_png(path: Path, color: tuple[int, int, int] = (20, 40, 60), size: tuple[int, int] = (8, 6)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def set_mtime(path: Path, when: float) -> None:
    os.utime(path, (when, when))


def sample_styles(**overrides: str) -> dict[str, str]:
    styles = {prop: "" for prop in COMPUTED_STYLE_PROPS}
    styles.update(
        {
            "display": "block",
            "fontSize": "16px",
            "color": "rgb(0, 0, 0)",
            "fontFamily": "Inter, sans-serif",
            "border": "0px none rgb(0, 0, 0)",
        }
    )
    styles.update(overrides)
    return styles


def write_computed(
    ref: Path,
    side: str,
    state: str,
    styles: dict[str, str] | None = None,
    url: str | None = None,
    *,
    subtree: list[dict[str, object]] | None = None,
    descendant_count: int | None = None,
    schema_version: int = COMPUTED_SCHEMA_VERSION,
) -> Path:
    path = ref / "frames" / side / computed_name(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    nodes = sample_subtree() if subtree is None else subtree
    record: dict[str, object] = {
        "schemaVersion": schema_version,
        "producer": PRODUCER,
        "side": side,
        "state": state,
        "url": url or (REF_URL if side == "ref" else IMPL_URL),
        "selector": "section.hero",
        "bbox": {"x": 0, "y": 0, "width": 8, "height": 6},
        "properties": list(COMPUTED_STYLE_PROPS),
        "computedStyle": styles or sample_styles(),
        "resourceOrigins": sample_resource_origins(side),
    }
    if schema_version >= COMPUTED_SCHEMA_VERSION:
        record["subtree"] = nodes
        record["descendantCount"] = len(nodes) if descendant_count is None else descendant_count
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_manifest(
    ref: Path,
    side: str,
    url: str | None = None,
    session: str = "scoped-session",
    *,
    resource_origins: dict[str, list[str]] | None = None,
    producer: dict[str, object] | None = None,
    code_resources: dict[str, list[str]] | None = None,
    schema_version: int = SCHEMA_VERSION,
    bbox: dict[str, object] | None = None,
    match_count: int = 1,
    visible: bool = True,
) -> Path:
    """(Re)write frames/<side>/capture-manifest.json for every frame present."""
    frames_dir = ref / "frames" / side
    url = url or (REF_URL if side == "ref" else IMPL_URL)
    origins = sample_resource_origins(side) if resource_origins is None else resource_origins
    record = cli_producer_record() if producer is None else producer
    box = {"x": 0, "y": 0, "width": 8, "height": 8} if bbox is None else bbox
    visibility = dict(SAMPLE_VISIBILITY) if visible else {**SAMPLE_VISIBILITY, "display": "none", "hiddenBy": "target display:none"}
    entries: dict[str, Any] = {}
    for png in sorted(frames_dir.glob("*.png")):
        stem = png.stem
        computed = frames_dir / computed_name(stem)
        if computed.is_file():
            entries[png.name] = {
                "mode": "clip",
                "sha256": sha256_file(png),
                "url": url,
                "origin": origin_of(url),
                "session": session,
                "capturedAt": "t",
                "selector": "section.hero",
                "matchCount": match_count,
                "bbox": box,
                "visibility": visibility,
                "visible": visible,
                "computed": computed.name,
                "computedSha256": sha256_file(computed),
                "resourceOrigins": origins,
                "producer": record,
            }
        else:
            entries[png.name] = {
                "mode": "video",
                "sha256": sha256_file(png),
                "url": url,
                "origin": origin_of(url),
                "session": session,
                "capturedAt": "t",
                "source": f"{stem.split('-')[0]}.webm",
                "sourceSha256": "0" * 64,
                "resourceOrigins": origins,
                "producer": record,
            }
    manifest: dict[str, Any] = {"schemaVersion": schema_version, "producer": PRODUCER, "side": side, "entries": entries}
    if schema_version >= SCHEMA_VERSION:
        manifest["codeResources"] = sample_code_resources(side) if code_resources is None else code_resources
    path = _save_manifest(frames_dir, manifest)
    set_mtime(path, time.time() - 40)
    ledger(ref, scoped_ledger.PRODUCER_CAPTURE, f"frames/{side}/capture-manifest.json")
    return path


def ledger(ref: Path, producer: str, rel: str) -> None:
    """Record `rel` the way the PostToolUse hook does after its producer command ran."""
    scoped_ledger.record(ref, producer, (rel,), f"fixture {producer}")


HOVER_FRAMES = ("idle.png", "active.png")
MODAL_FRAMES = ("idle.png", "open.png", "open-0001.png", "open-0002.png", "close-0001.png", "close-0002.png")


def build_scoped_evidence(
    root: Path,
    name: str = "hero",
    *,
    modal: bool = False,
    component: str = "Hero.tsx",
) -> Path:
    """A complete, passing scoped run under root/tmp/ref/<name>.

    Timeline: element-target (t-100) -> ref frames/recordings (t-90) ->
    impl source (t-60) -> impl frames (t-50) -> manifests (t-40) ->
    pixel-perfect-diff.json (t-10, produced by scoped_diff.build).
    """
    now = time.time()
    ref = root / "tmp" / "ref" / name
    ref.mkdir(parents=True, exist_ok=True)
    target = ref / "element-target.json"
    target.write_text(json.dumps(element_target_payload()), encoding="utf-8")
    set_mtime(target, now - 100)
    ledger(ref, scoped_ledger.PRODUCER_TARGET, "element-target.json")
    frames = MODAL_FRAMES if modal else HOVER_FRAMES
    for index, frame in enumerate(frames):
        color = (10 * index, 40, 60)
        write_png(ref / "frames" / "ref" / frame, color)
        set_mtime(ref / "frames" / "ref" / frame, now - 90)
        write_png(ref / "frames" / "impl" / frame, color)
        set_mtime(ref / "frames" / "impl" / frame, now - 50)
    if modal:
        for recording in ("open.webm", "close.webm"):
            (ref / recording).write_bytes(b"\x1aE\xdf\xa3webm")
            set_mtime(ref / recording, now - 90)
    states = [Path(f).stem for f in frames if "-" not in Path(f).stem]
    for state in states:
        for side in ("ref", "impl"):
            write_computed(ref, side, state)
    for side in ("ref", "impl"):
        write_manifest(ref, side)
    source = root / "impl" / "src" / "components" / component
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("export default function C() { return null }\n", encoding="utf-8")
    set_mtime(source, now - 60)
    produce_diff(ref, now - 10)
    return ref


def produce_diff(
    ref: Path,
    when: float | None = None,
    impl_files: list[str] | None = None,
    *,
    entry: str = "cli",
) -> dict[str, Any]:
    """Run the canonical producer (as its CLI would) and return the artifact it wrote."""
    data = scoped_diff.build(ref, None, impl_files, entry=entry, argv=[str(ref)])
    path = scoped_diff.write(ref.resolve(), data)
    set_mtime(path, time.time() - 10 if when is None else when)
    ledger(ref, scoped_ledger.PRODUCER_DIFF, "pixel-perfect-diff.json")
    return data


def write_diff(ref: Path, data: object, when: float | None = None) -> Path:
    """Hand-write pixel-perfect-diff.json (what an agent forge looks like)."""
    path = ref / "pixel-perfect-diff.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    set_mtime(path, time.time() - 10 if when is None else when)
    return path


def write_sequence(
    ref: Path,
    side: str,
    prefix: str,
    colors: list[tuple[int, int, int]],
    size: tuple[int, int] = (16, 12),
) -> list[Path]:
    """`<prefix>-NNNN.png` frames with the given colors (one frame per entry)."""
    paths: list[Path] = []
    for index, color in enumerate(colors, start=1):
        path = ref / "frames" / side / f"{prefix}-{index:04d}.png"
        write_png(path, color, size=size)
        set_mtime(path, time.time() - (90 if side == "ref" else 50))
        paths.append(path)
    return paths
