"""Capture provenance for element-scope frames (`frames/<side>/capture-manifest.json`).

`scripts/extract/element-state-capture.sh` is the canonical capture step for
a scoped clone (element-capture.md). For every clip or 60fps frame it writes,
it records a sidecar entry here: the page URL the agent-browser session was
on (validated against the expected origin by
`scripts/extract/validate-agent-browser-origin.py` before this module runs),
the session name, a timestamp, the sha256 of the frame, the origins of every
resource the page had loaded (`resourceOrigins`, from Performance resource
entries plus script/link/iframe/img/media element URLs), a `producer` record
(CLI entry, argv, module sha256, driver script sha256), and for resting-state
clips the sha256 of the `<state>.computed.json` record captured in the same
eval (the target's computed styles for the shared property list plus its
element subtree, `ui_clone.computed_style_diff` SUBTREE contract).

An impl capture is refused when the page is on the reference origin, has
loaded anything but media / fonts from the reference host or its subdomains
(a local proxy, an iframe of the site, or its bundles render pixel-identical
frames without being an implementation; image, video, and font hotlinks are
the preserved asset URLs and stay allowed), or has loaded any script /
stylesheet the reference captures inventoried (manifest-level
`codeResources`: normalized URLs, matched exactly or by a non-first-party
origin that served reference code, so a CDN-hosted reference bundle is
caught too). Clip captures also require target sanity (one visible match, a
box of at least TARGET_MIN_SIZE px; `matchCount` / `visibility` recorded per
entry). `python -m ui_clone.scoped_check` re-hashes every frame against this
manifest, re-checks those rules, and requires the producer record of the CLI
driven by element-state-capture.sh with the module and driver hashes of the
shipped release manifest (`ui_clone.scoped_producers`), so a reference frame
byte-copied into `frames/impl/` (or captured from the reference page) is
rejected even though it is pixel-identical. The manifest itself is hook
protected (no hand writes: bash_write / pre_generate), like `element-target.json`.

CLI (stdin = the raw `agent-browser eval --json` envelope the script validated):

    python -m ui_clone.element_capture record-clip <ref-dir> <side> <state> --full <viewport.png> --session <s>
    python -m ui_clone.element_capture record-video <ref-dir> <side> <prefix> --source <webm> --session <s>

`record-clip` crops the probed element's bbox out of the full-viewport
screenshot (agent-browser's `screenshot` has no clip flag; patterns.md) so the
clip geometry comes from the same eval as the computed styles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ui_clone.computed_style_diff import COMPUTED_STYLE_PROPS, SUBTREE_MAX_NODES, SUBTREE_PATH_RE
from ui_clone.hooks._common import TARGET_MIN_SIZE, target_sanity_problems
from ui_clone.scoped_provenance import (
    code_resources,
    driver_record,
    merge_code_resources,
    producer_record,
    reference_code_hits,
    reference_loads,
    resource_origins,
)

MANIFEST_NAME = "capture-manifest.json"
PRODUCER = "scripts/extract/element-state-capture.sh"
# 2: manifest-level `codeResources` (union of the script/stylesheet URLs the
# side's pages loaded, normalized) and per-clip target sanity
# (`matchCount`, `visibility`, `visible`). A schemaVersion 1 manifest is
# reported as needing re-capture, never read as evidence.
SCHEMA_VERSION = 2
_LEGACY_SCHEMA_VERSIONS = (1,)
# `<state>.computed.json`: 2 adds the target subtree (computed_style_diff
# SUBTREE_* contract) and the page's loaded resource origins.
COMPUTED_SCHEMA_VERSION = 2
SIDES = ("ref", "impl")
MODES = ("clip", "video")
ELEMENT_TARGET_NAME = "element-target.json"
_FRAME_RE = re.compile(r"^(?P<prefix>[a-z][a-z0-9_]*)-(?P<idx>\d{2,})\.png$")
_API_INVOCATION: dict[str, Any] = {"entry": "api", "argv": []}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def origin_of(url: object) -> str | None:
    """`scheme://host:port` of an http(s) URL (default port applied), else None."""
    if not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{parsed.hostname.lower()}:{port}"


def computed_name(state: str) -> str:
    return f"{state}.computed.json"


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def unwrap_envelope(raw: str) -> dict[str, Any]:
    """The eval result inside an `agent-browser eval --json` envelope (or bare)."""
    payload: Any = json.loads(raw)
    if isinstance(payload, dict) and "data" in payload:
        data = payload.get("data")
        if isinstance(data, dict) and "result" in data:
            payload = data["result"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("browser result must be a JSON object")
    return payload


def _read_manifest(frames_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((frames_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_manifest(frames_dir: Path) -> dict[str, Any] | None:
    data = _read_manifest(frames_dir)
    if (
        data is None
        or data.get("schemaVersion") != SCHEMA_VERSION
        or data.get("producer") != PRODUCER
        or not isinstance(data.get("entries"), dict)
        or not isinstance(data.get("codeResources"), dict)
    ):
        return None
    return data


def manifest_schema_problem(frames_dir: Path) -> str | None:
    """Why a manifest that exists is not readable as current evidence: written
    by an older element-state-capture.sh (no code-resource inventory / target
    sanity). None when the manifest is current or absent."""
    data = _read_manifest(frames_dir)
    if data is None or data.get("producer") != PRODUCER or load_manifest(frames_dir) is not None:
        return None
    version = data.get("schemaVersion")
    if version in _LEGACY_SCHEMA_VERSIONS or (
        version == SCHEMA_VERSION and "codeResources" not in data
    ):
        return (
            f"frames/{frames_dir.name}/{MANIFEST_NAME} was written by an older "
            f"element-state-capture.sh (schemaVersion {version!r}, no code-resource inventory "
            f"or target sanity); re-capture frames/{frames_dir.name}/ with the shipped script"
        )
    return None


def _save_manifest(frames_dir: Path, data: dict[str, Any]) -> Path:
    frames_dir.mkdir(parents=True, exist_ok=True)
    path = frames_dir / MANIFEST_NAME
    pending: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=frames_dir, delete=False
        ) as handle:
            pending = handle.name
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(pending, path)
    finally:
        if pending is not None:
            Path(pending).unlink(missing_ok=True)
    return path


def _open_manifest(frames_dir: Path, side: str) -> dict[str, Any]:
    data = load_manifest(frames_dir)
    if data is None or data.get("side") != side:
        data = {
            "schemaVersion": SCHEMA_VERSION,
            "producer": PRODUCER,
            "side": side,
            "codeResources": {},
            "entries": {},
        }
    return data


def reference_code_inventory(ref_dir: Path) -> dict[str, list[str]] | None:
    """The reference side's `codeResources` (script/stylesheet URLs its
    captures loaded), None when frames/ref/ has no current manifest."""
    manifest = load_manifest(ref_dir / "frames" / "ref")
    if manifest is None:
        return None
    return merge_code_resources(manifest.get("codeResources"))


def code_hotlink_failures(ref_dir: Path) -> list[tuple[str, str]]:
    """`impl-loads-reference-code` when the impl side's inventory names a
    reference script/stylesheet (by normalized URL) or a non-first-party
    origin that served reference code. Needs both current manifests; a
    missing or old-schema manifest is reported by `verify_side`."""
    reference = reference_code_inventory(ref_dir)
    impl = load_manifest(ref_dir / "frames" / "impl")
    if reference is None or impl is None:
        return []
    pages: list[tuple[str, object]] = sorted(
        {
            (str(entry.get("url")), json.dumps(entry.get("resourceOrigins"), sort_keys=True))
            for entry in impl["entries"].values()
            if isinstance(entry, dict) and isinstance(entry.get("url"), str)
        }
    ) or [("", "null")]
    hits: list[str] = []
    for page, origins_json in pages:
        origins = json.loads(str(origins_json))
        hits.extend(
            h
            for h in reference_code_hits(impl.get("codeResources"), page, reference, origins)
            if h not in hits
        )
    if not hits:
        return []
    return [
        (
            "impl-loads-reference-code",
            f"{len(hits)} load(s) on the implementation page belong to the reference runtime "
            f"(a page that runs the reference bundles, or fetches from the origin that serves "
            f"them, is a proxy, not a clone; media and font hotlinks are allowed): {_listed(hits)}",
        )
    ]


def crop_clip(full: Path, out: Path, bbox: dict[str, Any], viewport: dict[str, Any] | None) -> None:
    """Crop `bbox` (CSS px) out of a full-viewport screenshot into `out`.

    The screenshot may be scaled (device pixel ratio); the ratio is derived
    from the probe's `innerWidth` against the image width.
    """
    from PIL import Image

    x, y, w, h = (float(bbox[k]) for k in ("x", "y", "width", "height"))
    if w < 1 or h < 1:
        raise ValueError(f"element has no box to clip (bbox {bbox})")
    with Image.open(full) as image:
        ratio = 1.0
        inner = viewport.get("innerWidth") if isinstance(viewport, dict) else None
        if isinstance(inner, (int, float)) and not isinstance(inner, bool) and inner > 0:  # noqa: UP038
            ratio = image.width / float(inner)
        left = max(0, int(round(x * ratio)))
        top = max(0, int(round(y * ratio)))
        right = min(image.width, int(round((x + w) * ratio)))
        bottom = min(image.height, int(round((y + h) * ratio)))
        if right <= left or bottom <= top:
            raise ValueError(
                f"element bbox {bbox} lies outside the {image.width}x{image.height} screenshot"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        image.crop((left, top, right, bottom)).save(out)


def reference_origin_for(ref_dir: Path) -> str | None:
    """Reference origin from the ref dir's `element-target.json`, if valid."""
    try:
        data = json.loads((ref_dir / ELEMENT_TARGET_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("ok") is not True:
        return None
    return origin_of(data.get("url"))


def _check_impl_page(
    ref_dir: Path, side: str, url: object, origins: dict[str, list[str]], code: dict[str, list[str]]
) -> None:
    """An impl capture must be off the reference origin, load nothing from it,
    and load no script/stylesheet the reference captures inventoried."""
    if side != "impl":
        return
    reference_origin = reference_origin_for(ref_dir)
    if reference_origin is None:
        raise ValueError(
            f"{ELEMENT_TARGET_NAME} missing or invalid under {ref_dir}; record the target "
            "with element-evidence.sh before capturing the implementation"
        )
    if origin_of(url) == reference_origin:
        raise ValueError(
            f"impl capture is on the reference origin {reference_origin}, not the implementation"
        )
    loads = reference_loads(origins, reference_origin)
    if loads:
        raise ValueError(
            "impl page loads resources from the reference site (a proxy, iframe, or "
            f"reference bundle is not a clone): {', '.join(loads[:5])}"
        )
    reference_code = reference_code_inventory(ref_dir)
    if reference_code is None:
        problem = manifest_schema_problem(ref_dir / "frames" / "ref")
        raise ValueError(
            problem
            or f"frames/ref/{MANIFEST_NAME} missing under {ref_dir}; capture the reference "
            "side with element-state-capture.sh before the implementation (its script/"
            "stylesheet inventory is what an impl capture is checked against)"
        )
    hits = reference_code_hits(code, url, reference_code, origins)
    if hits:
        raise ValueError(
            "impl page loads resources of the reference runtime (a page that runs the "
            "reference bundles, or fetches from the origin that serves them, is a proxy, "
            f"not a clone; media and font hotlinks are allowed): {', '.join(hits[:5])}"
        )


def _check_target_sanity(probe: dict[str, Any]) -> None:
    """Clip captures need one visible target with a box >= TARGET_MIN_SIZE."""
    problems = target_sanity_problems(probe)
    if problems:
        raise ValueError(
            f"target sanity failed for {probe.get('selector')!r}: {'; '.join(problems)} "
            f"(one visible element of at least {TARGET_MIN_SIZE}x{TARGET_MIN_SIZE} px; capture a "
            "trigger-opened container in its open state)"
        )


def _subtree_of(probe: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    nodes = probe.get("subtree")
    count = probe.get("descendantCount")
    if not isinstance(nodes, list) or not isinstance(count, int) or isinstance(count, bool):
        raise ValueError("probe carries no subtree record (re-run element-state-capture.sh)")
    if len(nodes) > SUBTREE_MAX_NODES:
        raise ValueError(f"probe subtree exceeds {SUBTREE_MAX_NODES} nodes")
    recorded: list[dict[str, Any]] = []
    for node in nodes:
        path = node.get("path") if isinstance(node, dict) else None
        styles = node.get("computedStyle") if isinstance(node, dict) else None
        if (
            not isinstance(path, str)
            or not SUBTREE_PATH_RE.match(path)
            or not isinstance(styles, dict)
        ):
            raise ValueError("probe subtree node is malformed")
        recorded.append(
            {
                "path": path,
                "tag": str(node.get("tag", "")),
                "computedStyle": {prop: str(styles.get(prop, "")) for prop in COMPUTED_STYLE_PROPS},
            }
        )
    return recorded, count


def record_clip(
    ref_dir: Path,
    side: str,
    state: str,
    probe: dict[str, Any],
    *,
    session: str,
    full: Path,
    invocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Crop the clip, write `<state>.computed.json`, and add the manifest entry.

    `probe` is the script's eval result: `ok`, `url`, `selector`, `bbox`,
    `viewport`, `computedStyle` for `COMPUTED_STYLE_PROPS`, `subtree` +
    `descendantCount` (computed_style_diff SUBTREE contract), and `resources`
    (`[{url, kind}]` the page has loaded). `full` is the full-viewport
    screenshot taken right after that eval. An impl probe whose page loads
    anything from the reference site is refused. Returns the entry.
    """
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", state):
        raise ValueError(f"state must be a plain lowercase name, got {state!r}")
    if probe.get("ok") is not True:
        raise ValueError(f"probe failed: {probe.get('error') or 'selector not found'}")
    frames_dir = ref_dir / "frames" / side
    url = probe.get("url")
    if origin_of(url) is None:
        raise ValueError("probe carries no http(s) page url")
    selector = probe.get("selector")
    if not isinstance(selector, str) or not selector.strip():
        raise ValueError("probe carries no selector")
    styles = probe.get("computedStyle")
    if not isinstance(styles, dict):
        raise ValueError("probe carries no computedStyle")
    bbox = probe.get("bbox")
    if not isinstance(bbox, dict):
        raise ValueError("probe carries no bbox")
    origins = resource_origins(probe.get("resources"))
    code = code_resources(probe.get("resources"))
    if origins is None or code is None:
        raise ValueError("probe carries no resource list (re-run element-state-capture.sh)")
    subtree, descendant_count = _subtree_of(probe)
    _check_target_sanity(probe)
    _check_impl_page(ref_dir, side, url, origins, code)
    png = frames_dir / f"{state}.png"
    crop_clip(full, png, bbox, probe.get("viewport"))
    computed = {
        "schemaVersion": COMPUTED_SCHEMA_VERSION,
        "producer": PRODUCER,
        "side": side,
        "state": state,
        "url": url,
        "selector": selector,
        "bbox": probe.get("bbox"),
        "properties": list(COMPUTED_STYLE_PROPS),
        "computedStyle": {prop: styles.get(prop, "") for prop in COMPUTED_STYLE_PROPS},
        "subtree": subtree,
        "descendantCount": descendant_count,
        "resourceOrigins": origins,
    }
    computed_path = frames_dir / computed_name(state)
    computed_path.write_text(
        json.dumps(computed, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    entry = {
        "mode": "clip",
        "sha256": sha256_file(png),
        "url": url,
        "origin": origin_of(url),
        "session": session,
        "capturedAt": _timestamp(),
        "selector": selector,
        "matchCount": probe.get("matchCount"),
        "bbox": probe.get("bbox"),
        "visibility": probe.get("visibility"),
        "visible": probe.get("visible"),
        "computed": computed_path.name,
        "computedSha256": sha256_file(computed_path),
        "resourceOrigins": origins,
        "producer": _producer(invocation),
    }
    manifest = _open_manifest(frames_dir, side)
    manifest["entries"][png.name] = entry
    manifest["codeResources"] = merge_code_resources(manifest.get("codeResources"), code)
    _save_manifest(frames_dir, manifest)
    return entry


def _producer(invocation: dict[str, Any] | None) -> dict[str, Any]:
    inv = invocation or _API_INVOCATION
    record = producer_record(__name__, str(inv.get("entry", "api")), list(inv.get("argv", [])))
    record["driver"] = driver_record()
    return record


def record_video_frames(
    ref_dir: Path,
    side: str,
    prefix: str,
    *,
    url: str,
    session: str,
    source: Path,
    resources: object = None,
    invocation: dict[str, Any] | None = None,
) -> list[str]:
    """Add one manifest entry per `<prefix>-NNNN.png` extracted from `source`.

    `resources` is the session's loaded-resource list (`[{url, kind}]`); an
    impl side that loads from the reference site is refused.
    """
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    if origin_of(url) is None:
        raise ValueError("session url is not an http(s) page url")
    origins = resource_origins(resources)
    code = code_resources(resources)
    if origins is None or code is None:
        raise ValueError("session probe carries no resource list (re-run element-state-capture.sh)")
    _check_impl_page(ref_dir, side, url, origins, code)
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(f"recording missing or empty: {source}")
    frames_dir = ref_dir / "frames" / side
    frames = sorted(
        p for p in frames_dir.iterdir() if (m := _FRAME_RE.match(p.name)) and m["prefix"] == prefix
    )
    if not frames:
        raise FileNotFoundError(f"no {prefix}-NNNN.png frames under {frames_dir}")
    source_sha = sha256_file(source)
    stamp = _timestamp()
    producer = _producer(invocation)
    manifest = _open_manifest(frames_dir, side)
    for frame in frames:
        manifest["entries"][frame.name] = {
            "mode": "video",
            "sha256": sha256_file(frame),
            "url": url,
            "origin": origin_of(url),
            "session": session,
            "capturedAt": stamp,
            "source": source.name,
            "sourceSha256": source_sha,
            "resourceOrigins": origins,
            "producer": producer,
        }
    manifest["codeResources"] = merge_code_resources(manifest.get("codeResources"), code)
    _save_manifest(frames_dir, manifest)
    return [p.name for p in frames]


def verify_side(
    frames_dir: Path,
    frames: dict[str, Path],
    *,
    reference_origin: str,
    side: str,
    hashes: dict[str, str] | None = None,
    driver_sha256: str | None = None,
    module_sha256: str | None = None,
) -> list[tuple[str, str]]:
    """Failures (`code`, `reason`) for one side's frames against its manifest.

    Every frame needs an entry whose sha256 matches the file (`hashes` may
    supply precomputed digests). `ref` entries must come from the reference
    origin (element-target.json); `impl` entries must come from a different
    http(s) origin (the local implementation), so reference bytes can never
    stand in for an implementation capture. Every entry must carry a
    `producer` record from the CLI driven by element-state-capture.sh
    (`driver_sha256` / `module_sha256` are the shipped script's and recorder
    module's hashes from the release manifest) and a `resourceOrigins` map;
    impl entries whose page loaded anything from the reference site fail, and
    clip entries must still pass target sanity (one visible match, box >=
    TARGET_MIN_SIZE). An older-schema manifest is reported as needing
    re-capture.
    """
    label = frames_dir.name
    manifest = load_manifest(frames_dir)
    if manifest is None:
        schema = manifest_schema_problem(frames_dir)
        if schema is not None:
            return [(f"{label}-manifest-schema", schema)]
        return [
            (
                f"{label}-manifest-missing",
                f"frames/{label}/{MANIFEST_NAME} missing or not written by {PRODUCER}; "
                f"capture frames/{label}/ with that script",
            )
        ]
    if manifest.get("side") != side:
        return [
            (
                f"{label}-manifest-invalid",
                f"frames/{label}/{MANIFEST_NAME} records side {manifest.get('side')!r}",
            )
        ]
    entries = manifest["entries"]
    unmanifested: list[str] = []
    mismatched: list[str] = []
    wrong_origin: list[str] = []
    unrecorded: list[str] = []
    loads_reference: list[str] = []
    unsound: list[str] = []
    for name, path in frames.items():
        entry = entries.get(name)
        if not isinstance(entry, dict) or entry.get("mode") not in MODES:
            unmanifested.append(name)
            continue
        try:
            digest = hashes[name] if hashes and name in hashes else sha256_file(path)
            if entry.get("sha256") != digest:
                mismatched.append(name)
                continue
        except OSError:
            mismatched.append(name)
            continue
        origin = origin_of(entry.get("url"))
        session = entry.get("session")
        if origin is None or not isinstance(session, str) or not session.strip():
            wrong_origin.append(f"{name} (no page origin/session)")
        elif side == "ref" and origin != reference_origin:
            wrong_origin.append(f"{name} ({origin})")
        elif side == "impl" and origin == reference_origin:
            wrong_origin.append(f"{name} ({origin})")
        problem = producer_problem(entry.get("producer"), driver_sha256, module_sha256)
        if problem is not None:
            unrecorded.append(f"{name} ({problem})")
        origins = entry.get("resourceOrigins")
        if not isinstance(origins, dict):
            unrecorded.append(f"{name} (no resourceOrigins)")
        elif side == "impl":
            hits = reference_loads(origins, reference_origin)
            if hits:
                loads_reference.append(f"{name} ({', '.join(hits[:3])})")
        if entry.get("mode") == "clip":
            problems = target_sanity_problems(entry)
            if problems:
                unsound.append(f"{name} ({'; '.join(problems)})")
    failures: list[tuple[str, str]] = []
    if unsound:
        failures.append(
            (
                f"{label}-target-sanity",
                f"{len(unsound)} frames/{label}/ clip(s) were captured from a target that is not "
                f"one visible element of at least {TARGET_MIN_SIZE}x{TARGET_MIN_SIZE} px: {_listed(unsound)}",
            )
        )
    if unrecorded:
        failures.append(
            (
                f"{label}-frame-producer",
                f"{len(unrecorded)} frames/{label}/ frame(s) were not recorded by {PRODUCER} "
                f"through its CLI: {_listed(unrecorded)}",
            )
        )
    if loads_reference:
        failures.append(
            (
                f"{label}-frame-loads-reference",
                f"{len(loads_reference)} frames/{label}/ frame(s) were captured from a page that "
                f"loads resources from the reference site (a proxy, iframe, or reference bundle "
                f"is not an implementation): {_listed(loads_reference)}",
            )
        )
    if unmanifested:
        failures.append(
            (
                f"{label}-frame-unmanifested",
                f"{len(unmanifested)} frames/{label}/ frame(s) have no {MANIFEST_NAME} entry "
                f"(not captured by {PRODUCER}): {_listed(unmanifested)}",
            )
        )
    if mismatched:
        failures.append(
            (
                f"{label}-frame-hash-mismatch",
                f"{len(mismatched)} frames/{label}/ frame(s) differ from their capture record "
                f"(replaced after capture): {_listed(mismatched)}",
            )
        )
    if wrong_origin:
        expectation = (
            f"the reference origin {reference_origin}"
            if side == "ref"
            else f"a non-reference origin (the local implementation, not {reference_origin})"
        )
        failures.append(
            (
                f"{label}-frame-origin",
                f"{len(wrong_origin)} frames/{label}/ frame(s) were not captured from "
                f"{expectation}: {_listed(wrong_origin)}",
            )
        )
    return failures


def _listed(names: list[str], limit: int = 5) -> str:
    shown = ", ".join(names[:limit])
    extra = len(names) - limit
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def producer_problem(
    record: object, driver_sha256: str | None, module_sha256: str | None = None
) -> str | None:
    """Why a manifest entry's `producer` record is not the canonical CLI, else None.

    The record must say `entry: "cli"` (the `python -m ui_clone.element_capture`
    entry point, not an import), name a driver script whose sha256 equals the
    shipped element-state-capture.sh (`driver_sha256`), and carry the sha256
    of the shipped recorder module (`module_sha256`); None skips a comparison
    but still requires a driver.
    """
    if not isinstance(record, dict):
        return "no producer record"
    if record.get("entry") != "cli":
        return f"entry {record.get('entry')!r}, not the CLI"
    driver = record.get("driver")
    if not isinstance(driver, dict) or not isinstance(driver.get("sha256"), str):
        return "no driver script record"
    if driver_sha256 is not None and driver["sha256"] != driver_sha256:
        return "driver script differs from the shipped element-state-capture.sh"
    if module_sha256 is not None and record.get("moduleSha256") != module_sha256:
        return "recorder module differs from the shipped ui_clone/element_capture.py (release manifest)"
    return None


# ── CLI (called by element-state-capture.sh) ─────────────────────────────


def _read_probe() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("no eval payload on stdin")
    return unwrap_envelope(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ui_clone.element_capture", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    clip = sub.add_parser("record-clip", help="record a resting-state clip (stdin: eval envelope)")
    clip.add_argument("ref_dir")
    clip.add_argument("side", choices=SIDES)
    clip.add_argument("state")
    clip.add_argument("--full", required=True, help="full-viewport screenshot to crop")
    clip.add_argument("--session", required=True)
    video = sub.add_parser(
        "record-video", help="record extracted 60fps frames (stdin: url envelope)"
    )
    video.add_argument("ref_dir")
    video.add_argument("side", choices=SIDES)
    video.add_argument("prefix")
    video.add_argument("--source", required=True)
    video.add_argument("--session", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    invocation = {"entry": "cli", "argv": list(sys.argv[1:] if argv is None else argv)}
    try:
        probe = _read_probe()
        if args.command == "record-clip":
            entry = record_clip(
                Path(args.ref_dir),
                args.side,
                args.state,
                probe,
                session=args.session,
                full=Path(args.full),
                invocation=invocation,
            )
            print(
                json.dumps(
                    {
                        "frame": f"{args.state}.png",
                        "sha256": entry["sha256"],
                        "origin": entry["origin"],
                    }
                )
            )
            return 0
        names = record_video_frames(
            Path(args.ref_dir),
            args.side,
            args.prefix,
            url=str(probe.get("url", "")),
            session=args.session,
            source=Path(args.source),
            resources=probe.get("resources"),
            invocation=invocation,
        )
        print(json.dumps({"frames": len(names), "first": names[0], "last": names[-1]}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"element_capture: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
