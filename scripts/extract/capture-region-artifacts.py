#!/usr/bin/env python3
"""Capture concrete idle/active artifacts for real hover regions."""

from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any

HOVER_TRIGGERS = {"hover", "css-hover"}
SUMMARY_NAME = "capture-region-artifacts-summary.json"
MAX_REGIONS = 20
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
AUTO_SOURCE = "ui_clone.extraction_artifacts"
BRIDGE_SOURCE = "scripts/extract/capture-region-artifacts.py"


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_region_geometry(node: Any) -> None:
    if isinstance(node, dict):
        trigger_type = node.get("triggerType")
        if isinstance(trigger_type, str):
            bounds = node.get("bounds")
            if isinstance(bounds, dict):
                bx = _as_number(bounds.get("x"))
                by = _as_number(bounds.get("y"))
                bw = _as_number(bounds.get("width"))
                bh = _as_number(bounds.get("height"))
                x = _as_number(node.get("x"))
                y = _as_number(node.get("y"))
                w = _as_number(node.get("width"))
                h = _as_number(node.get("height"))

                if x is None or x < 0:
                    node["x"] = bx
                if y is None or y < 0:
                    node["y"] = by
                if w is None or w <= 0:
                    node["width"] = bw
                if h is None or h <= 0:
                    node["height"] = bh

                if (
                    type(node.get("x")) not in (int, float)
                    or type(node.get("y")) not in (int, float)
                    or type(node.get("width")) not in (int, float)
                    or type(node.get("height")) not in (int, float)
                ):
                    return

        for key, value in node.items():
            if type(value) in (dict, list):
                _normalize_region_geometry(value)
    elif isinstance(node, list):
        for value in node:
            _normalize_region_geometry(value)


def _run(session: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["agent-browser", "--session", session, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _eval(session: str, javascript: str) -> dict[str, Any]:
    result = _run(session, "eval", "--json", javascript)
    if result.returncode != 0:
        return {}
    try:
        value: Any = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return {}
    if (
        isinstance(value, dict)
        and isinstance(value.get("data"), dict)
        and "result" in value["data"]
    ):
        value = value["data"]["result"]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _safe_name(value: object, index: int) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-._")
    return f"{index:02d}-{(name or 'hover')[:60]}"


def _safe_transition_id(value: object, index: int) -> str:
    """Keep an existing capture ID stable while retaining indexed fallbacks."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-._")
    if re.match(r"^\d{2}-", name):
        return name[:63]
    return _safe_name(name, index)


def _wait_ms(region: dict[str, Any]) -> int:
    raw = str(region.get("transitionDuration") or "").strip().lower()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ms|s)", raw)
    if not match:
        return 500
    value = float(match.group(1))
    milliseconds = value * (1000 if match.group(2) == "s" else 1)
    return max(100, min(5000, round(milliseconds + 100)))


def _png_pixels(path: Path) -> tuple[tuple[int, int, int, int], bytes]:
    """Return normalized PNG scanlines so compression metadata cannot fake a diff."""
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG")

    offset = len(PNG_SIGNATURE)
    ihdr: bytes | None = None
    compressed = bytearray()
    palette = bytearray()
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        if len(chunk) != length:
            raise ValueError("truncated PNG chunk")
        offset += 12 + length
        if chunk_type == b"IHDR":
            ihdr = chunk
        elif chunk_type == b"IDAT":
            compressed.extend(chunk)
        elif chunk_type in {b"PLTE", b"tRNS"}:
            palette.extend(chunk_type + chunk)
        elif chunk_type == b"IEND":
            break

    if ihdr is None or len(ihdr) != 13 or not compressed:
        raise ValueError("incomplete PNG")
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if (
        not channels
        or bit_depth != 8
        or compression != 0
        or filtering != 0
        or interlace != 0
        or width <= 0
        or height <= 0
    ):
        raise ValueError("unsupported PNG format")

    raw = zlib.decompress(bytes(compressed))
    stride = width * channels
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise ValueError("unexpected PNG scanline size")

    previous = bytearray(stride)
    normalized = bytearray()
    pos = 0
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        current = bytearray(raw[pos : pos + stride])
        pos += stride
        for i, value in enumerate(current):
            left = current[i - channels] if i >= channels else 0
            above = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                current[i] = (value + left) & 0xFF
            elif filter_type == 2:
                current[i] = (value + above) & 0xFF
            elif filter_type == 3:
                current[i] = (value + ((left + above) // 2)) & 0xFF
            elif filter_type == 4:
                estimate = left + above - upper_left
                pa = abs(estimate - left)
                pb = abs(estimate - above)
                pc = abs(estimate - upper_left)
                predictor = left if pa <= pb and pa <= pc else above if pb <= pc else upper_left
                current[i] = (value + predictor) & 0xFF
            elif filter_type != 0:
                raise ValueError("unknown PNG filter")
        normalized.extend(current)
        previous = current
    return (width, height, bit_depth, color_type), bytes(palette + normalized)


def _images_differ(idle: Path, active: Path) -> bool:
    if not idle.is_file() or not active.is_file():
        return False
    if idle.stat().st_size <= 0 or active.stat().st_size <= 0:
        return False
    try:
        return _png_pixels(idle) != _png_pixels(active)
    except (OSError, ValueError, zlib.error):
        return False


def _load_or_derive_regions(ref_dir: Path) -> dict[str, Any]:
    regions_path = ref_dir / "regions.json"
    current: Any = None
    try:
        current = json.loads(regions_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed regions.json: {exc}") from exc

    from _capture_artifacts import derive_regions_json, produce_regions_json

    try:
        transition_spec: Any = json.loads(
            (ref_dir / "transition-spec.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError):
        transition_spec = None
    try:
        section_map: Any = json.loads((ref_dir / "section-map.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        section_map = None

    current_source = current.get("source") if isinstance(current, dict) else None
    current_is_live = current_source == BRIDGE_SOURCE or any(
        isinstance(region.get("artifacts"), dict) and bool(region["artifacts"])
        for region in _walk_region_dicts(current)
    )
    current_is_derived = current_source in {
        "derive-from-transition-spec",
        AUTO_SOURCE,
    }
    spec_is_auto = isinstance(transition_spec, dict) and (
        bool(transition_spec.get("placeholder")) or transition_spec.get("source") == AUTO_SOURCE
    )
    derived = derive_regions_json(transition_spec, section_map)
    derivation_mismatch = (
        isinstance(current, dict)
        and current_is_derived
        and isinstance(derived, dict)
        and _region_signature(current) != _region_signature(derived)
    )
    should_refresh = (
        not isinstance(current, dict)
        or current.get("placeholder") is True
        or (current_is_derived and not current_is_live and (spec_is_auto or derivation_mismatch))
    )
    if should_refresh:
        if isinstance(derived, dict):
            _write_json(regions_path, derived)
        else:
            produce_regions_json(ref_dir)
        try:
            current = json.loads(regions_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("regions.json is missing and could not be derived") from exc
    _normalize_region_geometry(current)
    if not isinstance(current, dict):
        raise ValueError("regions.json must contain a JSON object")
    return current


def _region_signature(node: Any) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            (
                str(region.get("name") or ""),
                str(region.get("triggerType") or "").strip().lower(),
                str(region.get("selector") or "").strip(),
            )
            for region in _walk_region_dicts(node)
        )
    )


TRACKED_STYLE_PROPERTIES = (
    "transform",
    "opacity",
    "color",
    "backgroundColor",
    "backgroundImage",
    "backgroundPosition",
    "filter",
    "backdropFilter",
    "boxShadow",
    "borderColor",
    "borderRadius",
    "clipPath",
    "height",
    "padding",
    "top",
)

REGION_MARKER_ATTRIBUTE = "data-uiclone-region"
OBSERVATION_MARKER_ATTRIBUTE = "data-uiclone-observation"

MIN_HOVER_TARGET_PX = 4


def _write_png_rgb(path: Path, width: int, height: int, rows: list[bytes]) -> None:
    """Encode 8-bit RGB scanlines as a PNG using only unfiltered rows."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    raw = bytearray()
    for row in rows:
        raw.append(0)
        raw.extend(row)
    path.write_bytes(
        PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )


def _crop_png(
    source: Path,
    dest: Path,
    box: tuple[float, float, float, float],
    viewport_css_width: float,
) -> bool:
    """Crop a viewport screenshot down to one CSS-pixel box.

    Element-clipped screenshots come back uniformly blank for in-flow content
    that sits outside the initially painted viewport, which silently turns a
    real hover delta into "pixels are identical". Cropping a viewport capture
    keeps the compare honest.
    """
    (width, height, _, color_type), pixels = _png_pixels(source)
    channels = {2: 3, 6: 4}.get(color_type)
    if not channels:
        raise ValueError(f"unsupported screenshot colour type: {color_type}")
    stride = width * channels
    if len(pixels) != height * stride:
        raise ValueError("unexpected screenshot pixel buffer")
    scale = width / viewport_css_width if viewport_css_width > 0 else 1.0
    left = max(0, min(width, round(box[0] * scale)))
    top = max(0, min(height, round(box[1] * scale)))
    right = max(left, min(width, round((box[0] + box[2]) * scale)))
    bottom = max(top, min(height, round((box[1] + box[3]) * scale)))
    if right <= left or bottom <= top:
        return False
    rows: list[bytes] = []
    for row_index in range(top, bottom):
        start = row_index * stride + left * channels
        row = pixels[start : start + (right - left) * channels]
        if channels == 4:
            row = bytes(value for offset, value in enumerate(row) if offset % 4 != 3)
        rows.append(bytes(row))
    _write_png_rgb(dest, right - left, bottom - top, rows)
    return True


IDENTITY_STYLE_VALUES = {
    "transform": {"none", "matrix(1, 0, 0, 1, 0, 0)"},
    "filter": {"none"},
    "backgroundImage": {"none"},
    "boxShadow": {"none"},
}

# Properties whose visual change renders beyond the border box, so a crop
# fitted to the rect cannot corroborate them no matter how it is taken.
OUTSIDE_BOX_PROPERTIES = frozenset({"boxShadow", "filter"})


def _normalized_style(name: str, value: object) -> str:
    """Collapse values that differ as strings but render identically.

    getComputedStyle reports an untransformed element as either "none" or the
    identity matrix depending on the property mix, and colours as rgb() or
    rgba() depending on alpha. Comparing the raw strings turns those into
    fabricated evidence of a transition.
    """
    text = " ".join(str(value or "").split())
    if text in IDENTITY_STYLE_VALUES.get(name, frozenset()):
        return "none"
    match = re.fullmatch(r"rgb\((\d+),\s*(\d+),\s*(\d+)\)", text)
    if match:
        return f"rgba({match.group(1)}, {match.group(2)}, {match.group(3)}, 1)"
    return text


def _changed_properties(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(
        key
        for key in set(before) | set(after)
        if _normalized_style(key, before.get(key)) != _normalized_style(key, after.get(key))
    )


def _resolve_target_js(literal: str, marker: str) -> str:
    """Tag the first hoverable match so every later step shares one element."""
    marker_literal = json.dumps(marker)
    return (
        "(() => {return (async () => {"
        f"const nodes=[...document.querySelectorAll({literal})];"
        f"for(const stale of document.querySelectorAll('[{REGION_MARKER_ATTRIBUTE}]'))"
        f"stale.removeAttribute('{REGION_MARKER_ATTRIBUTE}');"
        "let chosen=null;"
        "for(const el of nodes){"
        "const initial=el.getBoundingClientRect();"
        f"if(initial.width<{MIN_HOVER_TARGET_PX}||initial.height<{MIN_HOVER_TARGET_PX})continue;"
        "el.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});"
        "await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));"
        "const r=el.getBoundingClientRect();"
        "const cx=r.left+r.width/2,cy=r.top+r.height/2;"
        "if(cx<0||cy<0||cx>window.innerWidth||cy>window.innerHeight)continue;"
        "const hit=document.elementFromPoint(cx,cy);"
        "if(hit&&(hit===el||el.contains(hit))){chosen=el;break;}"
        "}"
        "if(!chosen)return {found:false,matches:nodes.length};"
        f"chosen.setAttribute('{REGION_MARKER_ATTRIBUTE}',{marker_literal});"
        "chosen.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});"
        "return {found:true,matches:nodes.length};"
        "})()})()"
    )


def _settle_target_js(target_literal: str) -> str:
    """Recenter a marked target after delayed scroll-state work has settled."""
    return (
        "(() => {return (async () => {"
        f"const el=document.querySelector({target_literal});"
        "if(!el)return {found:false};"
        "el.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});"
        "await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));"
        "const r=el.getBoundingClientRect();"
        "return {found:true,intersectsViewport:"
        "r.right>0&&r.bottom>0&&r.left<window.innerWidth&&r.top<window.innerHeight};"
        "})()})()"
    )


def _resolve_observation_target_js(
    target_literal: str,
    affected_literal: str,
    marker: str,
) -> str:
    """Pin an affected node contained by the exact activated region."""
    marker_literal = json.dumps(marker)
    return (
        "(() => {"
        f"for(const stale of document.querySelectorAll('[{OBSERVATION_MARKER_ATTRIBUTE}]'))"
        f"stale.removeAttribute('{OBSERVATION_MARKER_ATTRIBUTE}');"
        f"const activation=document.querySelector({target_literal});"
        "if(!activation)return {found:false,activationFound:false,matches:0};"
        f"const candidates=[...document.querySelectorAll({affected_literal})];"
        "const observed=candidates.find(node=>node===activation||activation.contains(node));"
        "if(!observed)return {found:false,activationFound:true,matches:candidates.length};"
        f"observed.setAttribute('{OBSERVATION_MARKER_ATTRIBUTE}',{marker_literal});"
        "return {found:true,activationFound:true,matches:candidates.length};"
        "})()"
    )


def _observe_target_js(
    target_literal: str,
    observation_target_literal: str | None = None,
) -> str:
    tracked = json.dumps(list(TRACKED_STYLE_PROPERTIES))
    observed_literal = observation_target_literal or target_literal
    return (
        "(() => {"
        f"const tracked={tracked};"
        f"const el=document.querySelector({target_literal});"
        f"const observed=document.querySelector({observed_literal});"
        "if(!el||!observed)return {found:false};"
        "const r=el.getBoundingClientRect();"
        "const cs=getComputedStyle(observed);"
        "const styles={};for(const name of tracked)styles[name]=cs[name];"
        "return {found:true,x:r.x,y:r.y,width:r.width,height:r.height,styles,"
        "scrollX:window.scrollX,scrollY:window.scrollY,"
        "viewportWidth:window.innerWidth,"
        "transitionProperty:cs.transitionProperty,"
        "transitionDuration:cs.transitionDuration,"
        "transitionTimingFunction:cs.transitionTimingFunction};"
        "})()"
    )


def _capture_one(
    session: str,
    region: dict[str, Any],
    index: int,
    ref_dir: Path,
) -> tuple[bool, str, dict[str, str] | None, dict[str, Any] | None]:
    selector = region.get("selector")
    if not isinstance(selector, str) or not selector.strip():
        return False, "missing selector", None, None
    selector = selector.strip()
    literal = json.dumps(selector)
    marker = f"region-{index}"
    target = f'[{REGION_MARKER_ATTRIBUTE}="{marker}"]'
    target_literal = json.dumps(target)
    affected_target = _hover_rule_affected_targets(ref_dir).get(_hover_activation(selector))
    observation_marker = f"observation-{index}"
    observation_target = f'[{OBSERVATION_MARKER_ATTRIBUTE}="{observation_marker}"]'
    observation_target_literal = json.dumps(observation_target) if affected_target else None

    # A prior hover can leave a fixed mega-menu covering the next target.
    # Moving outside the viewport clears the real CSS :hover chain before
    # hit-testing; synthetic mouseleave alone does not update Chromium's
    # pointer state.
    _run(session, "mouse", "move", "-100", "-100")

    def _release() -> None:
        _eval(
            session,
            (
                "(() => {"
                f"for(const el of document.querySelectorAll({target_literal}))"
                f"el.removeAttribute('{REGION_MARKER_ATTRIBUTE}');"
                f"for(const el of document.querySelectorAll('[{OBSERVATION_MARKER_ATTRIBUTE}]'))"
                f"el.removeAttribute('{OBSERVATION_MARKER_ATTRIBUTE}');"
                "return {found:true};"
                "})()"
            ),
        )

    resolved = _eval(session, _resolve_target_js(literal, marker))
    if resolved.get("found") is not True:
        matches = int(resolved.get("matches") or 0)
        reason = (
            "selector matches no elements"
            if matches <= 0
            else f"selector matches {matches} elements but none are hoverable"
        )
        return False, reason, None, None

    _run(session, "wait", "300")
    settled = _eval(session, _settle_target_js(target_literal))
    if settled.get("found") is not True:
        _release()
        return False, "selector missing or not observable", None, None
    if affected_target:
        observation_target_result = _eval(
            session,
            _resolve_observation_target_js(
                target_literal,
                json.dumps(affected_target),
                observation_marker,
            ),
        )
        if observation_target_result.get("found") is not True:
            _release()
            return False, "affected selector missing or not observable", None, None
    idle_observation = _eval(
        session,
        _observe_target_js(target_literal, observation_target_literal),
    )
    if (
        idle_observation.get("found") is not True
        or float(idle_observation.get("width") or 0) <= 0
        or float(idle_observation.get("height") or 0) <= 0
    ):
        _release()
        return False, "selector missing or not observable", None, None

    stem = _safe_name(region.get("name") or selector, index)
    relative_idle = f"clip/ref/{stem}-idle.png"
    relative_active = f"clip/ref/{stem}-active.png"
    idle = ref_dir / relative_idle
    active = ref_dir / relative_active
    idle.parent.mkdir(parents=True, exist_ok=True)
    idle_viewport = idle.parent / f".{stem}-idle-viewport.png"
    active_viewport = idle.parent / f".{stem}-active-viewport.png"
    idle_pending = idle.parent / f".{stem}-idle-pending.png"
    active_pending = idle.parent / f".{stem}-active-pending.png"
    viewport_width = float(idle_observation.get("viewportWidth") or 0)

    def _shoot(dest: Path) -> tuple[bool, str]:
        if _run(session, "screenshot", str(dest)).returncode != 0:
            return False, "viewport screenshot failed"
        return True, ""

    def _discard() -> None:
        for path in (idle_pending, active_pending, idle_viewport, active_viewport):
            path.unlink(missing_ok=True)

    # Both viewports are captured first and cropped afterwards: the crop box has
    # to be widened by the shadow extent of BOTH states, which is not known
    # until the hovered state has been observed.
    idle_ok, idle_reason = _shoot(idle_viewport)
    if not idle_ok:
        _discard()
        _release()
        return False, idle_reason, None, None

    hover_result = _run(session, "hover", target)
    if hover_result.returncode != 0:
        _discard()
        _release()
        return False, "CDP hover failed", None, None

    _eval(
        session,
        (
            "(() => {"
            f"const el=document.querySelector({target_literal});"
            "if(!el)return {found:false};"
            "const common={bubbles:true,cancelable:true,view:window};"
            "el.dispatchEvent(new PointerEvent('pointerover',{...common,pointerId:1}));"
            "el.dispatchEvent(new MouseEvent('mouseover',common));"
            "el.dispatchEvent(new MouseEvent('mouseenter',{...common,bubbles:false}));"
            "el.dispatchEvent(new MouseEvent('mousemove',common));"
            "return {found:true};"
            "})()"
        ),
    )
    _run(session, "wait", str(_wait_ms(region)))
    active_observation = _eval(
        session,
        _observe_target_js(target_literal, observation_target_literal),
    )

    idle_scroll_x = float(idle_observation.get("scrollX") or 0)
    idle_scroll_y = float(idle_observation.get("scrollY") or 0)
    if (
        float(active_observation.get("scrollX") or 0) != idle_scroll_x
        or float(active_observation.get("scrollY") or 0) != idle_scroll_y
    ):
        # Both crops must come from the same viewport box or the diff is noise.
        _eval(
            session,
            (
                "(() => {"
                f"window.scrollTo({idle_scroll_x},{idle_scroll_y});"
                "return {found:true};"
                "})()"
            ),
        )
        _run(session, "hover", target)
        _run(session, "wait", str(_wait_ms(region)))

    active_ok, active_reason = _shoot(active_viewport)

    _eval(
        session,
        (
            "(() => {"
            f"const el=document.querySelector({target_literal});"
            "if(!el)return {found:false};"
            "const common={bubbles:true,cancelable:true,view:window};"
            "el.dispatchEvent(new MouseEvent('mouseout',common));"
            "el.dispatchEvent(new MouseEvent('mouseleave',{...common,bubbles:false}));"
            "return {found:true};"
            "})()"
        ),
    )
    _run(session, "hover", "body")
    _run(session, "mouse", "move", "-100", "-100")
    _release()

    if not active_ok:
        _discard()
        return False, active_reason, None, None

    before = idle_observation.get("styles")
    after = active_observation.get("styles")
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    changed = _changed_properties(before, after)

    # Always the exact border box. A padded box drags the content behind a
    # fixed or sticky element into the ring, where it differs for reasons
    # unrelated to this region: the compare could then never fail, and the
    # saved artifact would not be comparable against an implementation either.
    tight = (
        float(idle_observation.get("x") or 0),
        float(idle_observation.get("y") or 0),
        float(idle_observation.get("width") or 0),
        float(idle_observation.get("height") or 0),
    )

    def _crop_pair(box: tuple[float, float, float, float]) -> tuple[bool, str]:
        for source_png, dest in (
            (idle_viewport, idle_pending),
            (active_viewport, active_pending),
        ):
            try:
                cropped = _crop_png(source_png, dest, box, viewport_width)
            except (OSError, ValueError, zlib.error) as exc:
                return False, f"region crop failed: {exc}"
            if not cropped:
                return False, "region rect lies outside the viewport"
        return True, ""

    crop_ok, crop_reason = _crop_pair(tight)
    if not crop_ok:
        _discard()
        return False, crop_reason, None, None

    idle_viewport.unlink(missing_ok=True)
    active_viewport.unlink(missing_ok=True)

    # A measured computed-style delta is direct evidence that the reference
    # changes state. Demanding pixel corroboration on top of it would require
    # proving an outside-the-box effect with a view that cannot contain it.
    pixels_differ = _images_differ(idle_pending, active_pending)
    if not changed and not pixels_differ:
        _discard()
        return False, "hover produced no observable change", None, None

    if not changed:
        changed = ["renderedPixels"]
        before = {"renderedPixels": relative_idle}
        after = {"renderedPixels": relative_active}
    observation: dict[str, Any] = {
        "changedProperties": changed,
        "from": {key: before.get(key) for key in changed},
        "to": {key: after.get(key) for key in changed},
        "pixelCorroborated": pixels_differ,
    }
    outside_box = [key for key in changed if key in OUTSIDE_BOX_PROPERTIES]
    if outside_box:
        observation["outsideBoxChange"] = outside_box
    duration = active_observation.get("transitionDuration") or idle_observation.get(
        "transitionDuration"
    )
    easing = active_observation.get("transitionTimingFunction") or idle_observation.get(
        "transitionTimingFunction"
    )
    if isinstance(duration, str) and duration.strip():
        observation["duration"] = duration.strip()
    if isinstance(easing, str) and easing.strip():
        observation["easing"] = easing.strip()
    idle_pending.replace(idle)
    active_pending.replace(active)
    return (
        True,
        "",
        {"idle": relative_idle, "active": relative_active},
        observation,
    )


# Mirrors scripts/extract/capture-scroll.sh: a fixed ladder makes no assumption
# that a scroll-driven change is monotonic or reversible, which a search for a
# single trigger offset does. IntersectionObserver reveals with once:true and
# pinned sections violate that assumption outright.
SCROLL_LADDER_PCTS = (0, 10, 25, 50, 75, 90, 100)
SCROLL_SETTLE_FLOOR_MS = 500
SCROLL_STABILITY_POLL_MS = 200

# Engines that virtualise scrolling; window.scrollTo does not move their
# timeline, so probing one of these measures nothing.
HIJACKING_SCROLL_ENGINES = frozenset({"lenis", "locomotive", "smooth-scrollbar"})


def _is_scroll_trigger(trigger: str) -> bool:
    return trigger.startswith("scroll")


def _hijacked_scroll_engine(ref_dir: Path) -> str | None:
    try:
        payload = json.loads((ref_dir / "scroll-engine.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    detected = payload.get("detected") if isinstance(payload, dict) else None
    if not isinstance(detected, dict):
        return None
    for name in detected:
        if str(name).strip().lower() in HIJACKING_SCROLL_ENGINES:
            return str(name)
    return None


def _ladder_sample_js(target_literal: str, scroll_y: float) -> str:
    """Scroll, wait for the element to stop changing, and report it.

    The settle is a stability poll rather than a fixed sleep so a slow reveal is
    not sampled mid-flight, and the whole step is one eval so a seven-rung
    ladder does not cost twenty-eight subprocess round-trips.
    """
    tracked = json.dumps(list(TRACKED_STYLE_PROPERTIES))
    return (
        "(async () => {"
        f"const el=document.querySelector({target_literal});"
        "if(!el)return {found:false};"
        f"const tracked={tracked};"
        f"window.scrollTo({{top:{scroll_y},left:0,behavior:'instant'}});"
        "const snap=()=>{const r=el.getBoundingClientRect();"
        "const cs=getComputedStyle(el);const styles={};"
        "for(const name of tracked)styles[name]=cs[name];"
        "return {styles,x:r.x,y:r.y,width:r.width,height:r.height,"
        "fullyVisible:r.top>=0&&r.left>=0&&r.bottom<=window.innerHeight&&r.right<=window.innerWidth};};"
        "const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));"
        f"await sleep({SCROLL_SETTLE_FLOOR_MS});"
        "let previous=JSON.stringify(snap());let stable=0;"
        "for(let i=0;i<15&&stable<2;i++){"
        f"await sleep({SCROLL_STABILITY_POLL_MS});"
        "const now=JSON.stringify(snap());"
        "if(now===previous){stable++;}else{stable=0;previous=now;}}"
        "const current=snap();const cs=getComputedStyle(el);"
        "return {found:true,styles:current.styles,x:current.x,y:current.y,"
        "width:current.width,height:current.height,"
        "fullyVisible:current.fullyVisible,"
        "scrollY:window.scrollY,viewportWidth:window.innerWidth,"
        "transitionProperty:cs.transitionProperty,"
        "transitionDuration:cs.transitionDuration,"
        "transitionTimingFunction:cs.transitionTimingFunction};"
        "})()"
    )


def _resolve_scroll_target_js(literal: str, marker: str) -> str:
    """Tag the first rendered match without scrolling to it.

    Scroll offset is the independent variable here, so the hover resolver's
    scrollIntoView would destroy the measurement before it starts.
    """
    marker_literal = json.dumps(marker)
    return (
        "(() => {"
        f"const nodes=[...document.querySelectorAll({literal})];"
        f"for(const stale of document.querySelectorAll('[{REGION_MARKER_ATTRIBUTE}]'))"
        f"stale.removeAttribute('{REGION_MARKER_ATTRIBUTE}');"
        "let chosen=null;"
        "for(const el of nodes){"
        "const r=el.getBoundingClientRect();"
        f"if(r.width<{MIN_HOVER_TARGET_PX}||r.height<{MIN_HOVER_TARGET_PX})continue;"
        "chosen=el;break;}"
        "if(!chosen)return {found:false,matches:nodes.length};"
        f"chosen.setAttribute('{REGION_MARKER_ATTRIBUTE}',{marker_literal});"
        "return {found:true,matches:nodes.length};"
        "})()"
    )


def _capture_scroll_one(
    session: str,
    region: dict[str, Any],
    index: int,
    ref_dir: Path,
) -> tuple[bool, str, dict[str, str] | None, dict[str, Any] | None]:
    selector = region.get("selector")
    if not isinstance(selector, str) or not selector.strip():
        return False, "missing selector", None, None
    selector = selector.strip()
    hijacker = _hijacked_scroll_engine(ref_dir)
    if hijacker:
        return False, f"scroll is virtualised by {hijacker}", None, None

    literal = json.dumps(selector)
    marker = f"region-{index}"
    target = f'[{REGION_MARKER_ATTRIBUTE}="{marker}"]'
    target_literal = json.dumps(target)

    def _release() -> None:
        _eval(
            session,
            (
                "(() => {"
                f"for(const el of document.querySelectorAll({target_literal}))"
                f"el.removeAttribute('{REGION_MARKER_ATTRIBUTE}');"
                "return {found:true};"
                "})()"
            ),
        )

    def _reset_scroll_capture() -> None:
        _release()
        _eval(
            session,
            "(() => {window.scrollTo({top:0,left:0,behavior:'instant'});return {found:true};})()",
        )

    _eval(
        session,
        "(() => {window.scrollTo({top:0,left:0,behavior:'instant'});return {found:true};})()",
    )
    resolved = _eval(session, _resolve_scroll_target_js(literal, marker))
    if resolved.get("found") is not True:
        matches = int(resolved.get("matches") or 0)
        return (
            False,
            (
                "selector matches no elements"
                if matches <= 0
                else f"selector matches {matches} elements but none are renderable"
            ),
            None,
            None,
        )

    max_scroll = _max_scroll(session)
    if max_scroll <= 0:
        _release()
        return False, "page does not scroll", None, None

    stem = _safe_name(region.get("name") or selector, index)
    clip_dir = ref_dir / "clip" / "ref"
    clip_dir.mkdir(parents=True, exist_ok=True)
    viewport = clip_dir / f".{stem}-viewport.png"

    rungs: list[dict[str, Any]] = []
    for pct in SCROLL_LADDER_PCTS:
        scroll_y = max_scroll * pct / 100
        sample = _eval(session, _ladder_sample_js(target_literal, scroll_y))
        if sample.get("found") is not True:
            continue
        frame = clip_dir / f".{stem}-{pct:03d}.png"
        if _run(session, "screenshot", str(viewport)).returncode != 0:
            _cleanup_paths([viewport], rungs)
            _release()
            return False, "viewport screenshot failed", None, None
        box = (
            float(sample.get("x") or 0),
            float(sample.get("y") or 0),
            float(sample.get("width") or 0),
            float(sample.get("height") or 0),
        )
        try:
            cropped = _crop_png(viewport, frame, box, float(sample.get("viewportWidth") or 0))
        except (OSError, ValueError, zlib.error) as exc:
            _cleanup_paths([viewport], rungs)
            _release()
            return False, f"region crop failed: {exc}", None, None
        finally:
            viewport.unlink(missing_ok=True)
        if not cropped:
            # Out of view at this rung; the ladder continues without it.
            continue
        styles = sample.get("styles")
        rungs.append(
            {
                "pct": pct,
                "frame": frame,
                "styles": styles if isinstance(styles, dict) else {},
                "fullyVisible": sample.get("fullyVisible") is True,
                "observation": sample,
            }
        )

    if len(rungs) < 2:
        _cleanup_paths([viewport], rungs)
        _reset_scroll_capture()
        return False, "region is observable at fewer than two scroll positions", None, None

    baseline = rungs[0]

    def _pixels_comparable(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return bool(left["fullyVisible"] and right["fullyVisible"])

    changed_at = [
        rung
        for rung in rungs[1:]
        if _changed_properties(baseline["styles"], rung["styles"])
        or (_pixels_comparable(baseline, rung) and _images_differ(baseline["frame"], rung["frame"]))
    ]
    if not changed_at:
        _cleanup_paths([viewport], rungs)
        _reset_scroll_capture()
        return False, "scroll produced no observable change", None, None

    first_change = changed_at[0]
    before = rungs[rungs.index(first_change) - 1]
    after = changed_at[-1]

    distinct: list[list[str]] = []
    for rung in rungs:
        signature = sorted(
            f"{key}={_normalized_style(key, value)}" for key, value in rung["styles"].items()
        )
        if signature not in distinct:
            distinct.append(signature)
    if len(distinct) > 2:
        progression = "scrubbed"
    elif len(distinct) == 2:
        progression = "threshold"
    else:
        # Pixels moved while every tracked property held still, so the shape of
        # the progression is not something this probe measured.
        progression = "unknown"

    middle = [rung for rung in rungs if before["pct"] < rung["pct"] < after["pct"]]
    mid = middle[len(middle) // 2] if middle else None
    if mid is None:
        midpoint = (float(before["pct"]) + float(after["pct"])) / 2
        midpoint_pct: float | int = int(midpoint) if midpoint.is_integer() else midpoint
        sample = _eval(
            session,
            _ladder_sample_js(target_literal, max_scroll * midpoint / 100),
        )
        midpoint_frame = clip_dir / f".{stem}-midpoint.png"
        if (
            sample.get("found") is not True
            or _run(session, "screenshot", str(viewport)).returncode != 0
        ):
            _cleanup_paths([viewport, midpoint_frame], rungs)
            _reset_scroll_capture()
            return False, "midpoint screenshot failed", None, None
        box = (
            float(sample.get("x") or 0),
            float(sample.get("y") or 0),
            float(sample.get("width") or 0),
            float(sample.get("height") or 0),
        )
        try:
            midpoint_cropped = _crop_png(
                viewport,
                midpoint_frame,
                box,
                float(sample.get("viewportWidth") or 0),
            )
        except (OSError, ValueError, zlib.error) as exc:
            _cleanup_paths([viewport, midpoint_frame], rungs)
            _reset_scroll_capture()
            return False, f"midpoint region crop failed: {exc}", None, None
        finally:
            viewport.unlink(missing_ok=True)
        if not midpoint_cropped:
            _cleanup_paths([midpoint_frame], rungs)
            _reset_scroll_capture()
            return False, "midpoint region lies outside the viewport", None, None
        styles = sample.get("styles")
        mid = {
            "pct": midpoint_pct,
            "frame": midpoint_frame,
            "styles": styles if isinstance(styles, dict) else {},
            "fullyVisible": sample.get("fullyVisible") is True,
            "observation": sample,
        }

    relative: dict[str, str] = {}
    artifacts: dict[str, str] = {}
    named_rungs: list[tuple[str, dict[str, Any]]] = [("before", before)]
    if mid is not None:
        named_rungs.append(("mid", mid))
    named_rungs.append(("after", after))
    for name, rung in named_rungs:
        rel = f"clip/ref/{stem}-{name}.png"
        (ref_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        rung["frame"].replace(ref_dir / rel)
        rung["frame"] = ref_dir / rel
        relative[name] = rel
        artifacts[name] = rel

    _cleanup_paths([viewport], [r for r in rungs if r["frame"].name.startswith(".")])

    changed = _changed_properties(before["styles"], after["styles"])
    pixels_comparable = _pixels_comparable(before, after)
    pixels_differ = pixels_comparable and _images_differ(before["frame"], after["frame"])
    if not changed:
        changed = ["renderedPixels"]
        before_styles = {"renderedPixels": relative["before"]}
        after_styles = {"renderedPixels": relative["after"]}
    else:
        before_styles = {key: before["styles"].get(key) for key in changed}
        after_styles = {key: after["styles"].get(key) for key in changed}

    observation: dict[str, Any] = {
        "changedProperties": changed,
        "from": before_styles,
        "to": after_styles,
        "pixelCorroborated": pixels_differ,
        "pixelComparable": pixels_comparable,
        "progression": progression,
        "ladderPcts": [rung["pct"] for rung in rungs],
        "changedAtPcts": [rung["pct"] for rung in changed_at],
        "beforePct": before["pct"],
        "afterPct": after["pct"],
    }
    if mid is not None:
        observation["midPct"] = mid["pct"]
    outside_box = [key for key in changed if key in OUTSIDE_BOX_PROPERTIES]
    if outside_box:
        observation["outsideBoxChange"] = outside_box
    duration = after["observation"].get("transitionDuration")
    easing = after["observation"].get("transitionTimingFunction")
    if isinstance(duration, str) and duration.strip():
        observation["duration"] = duration.strip()
    if isinstance(easing, str) and easing.strip():
        observation["easing"] = easing.strip()
    _reset_scroll_capture()
    return True, "", artifacts, observation


def _cleanup_paths(extra: list[Path], rungs: list[dict[str, Any]]) -> None:
    for path in extra:
        path.unlink(missing_ok=True)
    for rung in rungs:
        frame = rung.get("frame")
        if isinstance(frame, Path):
            frame.unlink(missing_ok=True)


def _max_scroll(session: str) -> float:
    metrics = _eval(
        session,
        (
            "(() => {"
            "const d=document.documentElement;"
            "return {found:true,maxScroll:Math.max(0,d.scrollHeight-window.innerHeight)};"
            "})()"
        ),
    )
    return float(metrics.get("maxScroll") or 0)


def _spec_is_bridge_owned(ref_dir: Path) -> bool:
    """True when transition-spec.json is this pipeline's own output.

    An auto placeholder and a live-capture promotion are both the bridge's own
    claims, so it may prove or disprove their dispatch-only regions. A spec
    carrying another author's transitions keeps its own source (see
    _promote_transition_spec), so it can never be laundered into this state by
    an unrelated capture.
    """
    try:
        spec = json.loads((ref_dir / "transition-spec.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return isinstance(spec, dict) and (
        bool(spec.get("placeholder")) or spec.get("source") in {AUTO_SOURCE, BRIDGE_SOURCE}
    )


def _is_capturable(region: dict[str, Any], *, dispatch_is_obligation: bool) -> bool:
    trigger = str(region.get("triggerType") or "").strip().lower()
    if trigger in HOVER_TRIGGERS:
        return True
    if not _is_scroll_trigger(trigger):
        return False
    return not (dispatch_is_obligation and bool(region.get("dispatchOnly")))


def _capture_regions(
    node: Any,
    session: str,
    ref_dir: Path,
    summary: dict[str, Any],
    seen: set[tuple[str, str]],
    counter: list[int],
    preserve_failed_dispatch: bool,
    dispatch_is_obligation: bool,
) -> Any:
    if isinstance(node, list):
        kept = []
        for item in node:
            if isinstance(item, dict) and isinstance(item.get("triggerType"), str):
                trigger = item["triggerType"].strip().lower()
                if _is_capturable(item, dispatch_is_obligation=dispatch_is_obligation):
                    selector = str(item.get("selector") or "").strip()
                    key = (selector, trigger)
                    label = str(item.get("name") or selector or f"region-{counter[0]}")
                    if key in seen:
                        summary["skipped"].append(
                            {
                                "region": label,
                                "selector": selector,
                                "triggerType": trigger,
                                "reason": "duplicate selector and trigger",
                            }
                        )
                        continue
                    seen.add(key)
                    if counter[0] >= MAX_REGIONS:
                        summary["skipped"].append(
                            {
                                "region": label,
                                "selector": selector,
                                "triggerType": trigger,
                                "reason": f"capture limit {MAX_REGIONS}",
                            }
                        )
                        if preserve_failed_dispatch and item.get("dispatchOnly"):
                            kept.append(item)
                        continue
                    index = counter[0]
                    counter[0] += 1
                    summary["attempted"].append(
                        {"region": label, "selector": selector, "triggerType": trigger}
                    )
                    capture = _capture_one if trigger in HOVER_TRIGGERS else _capture_scroll_one
                    ok, reason, artifacts, observation = capture(session, item, index, ref_dir)
                    if not ok or artifacts is None or observation is None:
                        summary["skipped"].append(
                            {
                                "region": label,
                                "selector": selector,
                                "triggerType": trigger,
                                "reason": reason,
                            }
                        )
                        # A probe failure leaves this region unproven. Dropping
                        # it deletes the candidate a corrected re-run needs,
                        # even when sibling regions captured successfully.
                        prior_artifacts = item.get("artifacts")
                        if (
                            (isinstance(prior_artifacts, dict) and bool(prior_artifacts))
                            or _is_probe_failure(reason)
                            or (preserve_failed_dispatch and item.get("dispatchOnly"))
                        ):
                            kept.append(item)
                        continue
                    updated = dict(item)
                    updated.pop("dispatchOnly", None)
                    updated["artifacts"] = artifacts
                    kept.append(updated)
                    summary["captured"].append(
                        {
                            "region": label,
                            "selector": selector,
                            "triggerType": trigger,
                            "artifacts": artifacts,
                            "observation": observation,
                        }
                    )
                    continue
            kept.append(
                _capture_regions(
                    item,
                    session,
                    ref_dir,
                    summary,
                    seen,
                    counter,
                    preserve_failed_dispatch,
                    dispatch_is_obligation,
                )
            )
        return kept
    if isinstance(node, dict):
        return {
            key: _capture_regions(
                value,
                session,
                ref_dir,
                summary,
                seen,
                counter,
                preserve_failed_dispatch,
                dispatch_is_obligation,
            )
            for key, value in node.items()
        }
    return node


def _unsupported_regions(node: Any, *, dispatch_only_is_supported: bool) -> list[dict[str, str]]:
    unsupported: list[dict[str, str]] = []
    if isinstance(node, dict):
        trigger = node.get("triggerType")
        if isinstance(trigger, str):
            artifacts = node.get("artifacts")
            capture_needed = (
                not dispatch_only_is_supported or not node.get("dispatchOnly")
            ) and not (isinstance(artifacts, dict) and bool(artifacts))
            if capture_needed and (
                trigger.strip().lower() not in HOVER_TRIGGERS or bool(node.get("dispatchOnly"))
            ):
                unsupported.append(
                    {
                        "region": str(node.get("name") or node.get("selector") or "region"),
                        "triggerType": trigger,
                    }
                )
        for value in node.values():
            unsupported.extend(
                _unsupported_regions(value, dispatch_only_is_supported=dispatch_only_is_supported)
            )
    elif isinstance(node, list):
        for value in node:
            unsupported.extend(
                _unsupported_regions(value, dispatch_only_is_supported=dispatch_only_is_supported)
            )
    return unsupported


def _spec_is_auto(ref_dir: Path) -> bool:
    try:
        spec = json.loads((ref_dir / "transition-spec.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    return isinstance(spec, dict) and (
        bool(spec.get("placeholder")) or spec.get("source") == AUTO_SOURCE
    )


def _prune_auto_dispatch_regions(
    node: Any,
    skipped: list[dict[str, Any]],
    signals: dict[str, Any],
) -> Any:
    if isinstance(node, list):
        kept = []
        for item in node:
            if isinstance(item, dict) and item.get("dispatchOnly"):
                trigger = str(item.get("triggerType") or "").strip().lower()
                signal_keys = _signal_keys_for_trigger(trigger)
                signals_are_false = bool(signal_keys) and all(
                    signals.get(key) is False for key in signal_keys
                )
                if not signals_are_false:
                    kept.append(item)
                    continue
                skipped.append(
                    {
                        "region": str(item.get("name") or item.get("selector") or "region"),
                        "selector": str(item.get("selector") or ""),
                        "triggerType": trigger,
                        "reason": (
                            "auto dispatch-only region was not live-captured "
                            "and current verification-plan signals are false"
                        ),
                    }
                )
                continue
            kept.append(_prune_auto_dispatch_regions(item, skipped, signals))
        return kept
    if isinstance(node, dict):
        return {
            key: _prune_auto_dispatch_regions(value, skipped, signals)
            for key, value in node.items()
        }
    return node


def _signal_keys_for_trigger(trigger: str) -> tuple[str, ...]:
    if trigger.startswith("scroll"):
        return ("hasScrollScrub", "hasScrollStateMachine")
    if any(token in trigger for token in ("intersection", "reveal", "in-view", "io-")):
        return ("hasIOReveal",)
    if trigger.startswith("click"):
        return ("hasClickStateTransition",)
    if trigger in HOVER_TRIGGERS or "hover" in trigger:
        return ("hasHover",)
    if trigger in {"mousemove", "cursor", "canvas", "webgl"}:
        return ("hasCanvas",)
    if any(token in trigger for token in ("timer", "carousel", "swiper")):
        return ("hasSwiper", "hasLottie", "hasCanvas")
    if "lottie" in trigger:
        return ("hasLottie",)
    if trigger in {"load", "page-load", "splash"}:
        return ("hasSplash",)
    return ()


def _verification_signals(ref_dir: Path) -> dict[str, Any]:
    try:
        plan = json.loads((ref_dir / "verification-plan.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    signals = plan.get("signals") if isinstance(plan, dict) else None
    return signals if isinstance(signals, dict) else {}


def _hover_activation(selector: object) -> str:
    value = " ".join(str(selector or "").split())
    return value.split(":hover", 1)[0].strip() if ":hover" in value else value


def _real_source_chunk(ref_dir: Path, value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.lower() == "inline init":
        return "inline init"
    if normalized.lower() in {
        "n/a",
        "none",
        "unknown",
        "unresolved",
    }:
        return None
    available: set[str] = set()
    for directory_name in ("css", "bundles", "html"):
        directory = ref_dir / directory_name
        if not directory.is_dir():
            continue
        available.update(path.name for path in directory.iterdir() if path.is_file())
    pieces = re.split(r"\s*\+\s*|\s+or\s+", normalized, flags=re.IGNORECASE)
    basenames = []
    for piece in pieces:
        cleaned = re.sub(r"\s*\([^)]*\)", "", piece).strip()
        if not cleaned:
            continue
        basenames.append(Path(cleaned).name)
    if basenames and all(basename in available for basename in basenames):
        return normalized
    return None


def _hover_rule_source_chunks(ref_dir: Path) -> dict[str, str]:
    path = ref_dir / "hover-css-rules.json"
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    rules = (
        payload.get("rules") or payload.get("entries")
        if isinstance(payload, dict)
        else payload
        if isinstance(payload, list)
        else None
    )
    if not isinstance(rules, list):
        return {}
    mapped: dict[str, str] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        selector = _hover_activation(rule.get("activation") or rule.get("selector"))
        source_chunk = _real_source_chunk(ref_dir, rule.get("sourceFile"))
        if selector and source_chunk:
            mapped[selector] = source_chunk
    return mapped


def _hover_rule_affected_targets(ref_dir: Path) -> dict[str, str]:
    path = ref_dir / "hover-css-rules.json"
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    rules = (
        payload.get("rules") or payload.get("entries")
        if isinstance(payload, dict)
        else payload
        if isinstance(payload, list)
        else None
    )
    if not isinstance(rules, list):
        return {}

    affected_by_activation: dict[str, list[str]] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        activation = _hover_activation(rule.get("activation") or rule.get("selector"))
        affected = " ".join(str(rule.get("affected") or "").split())
        if not activation or not affected or affected == activation:
            continue
        targets = affected_by_activation.setdefault(activation, [])
        if affected not in targets:
            targets.append(affected)
    return {
        activation: ", ".join(targets) for activation, targets in affected_by_activation.items()
    }


def _reconcile_interactions(
    ref_dir: Path,
    captured: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> list[dict[str, str]]:
    path = ref_dir / "interactions-detected.json"
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    is_wrapper = isinstance(payload, dict)
    interactions = (
        payload.get("interactions")
        if is_wrapper
        else payload
        if isinstance(payload, list)
        else None
    )
    if not isinstance(interactions, list):
        return []
    captured_by_selector = {str(item["selector"]): item for item in captured}
    captured_selectors = set(captured_by_selector)
    source = payload.get("source") if is_wrapper else None
    auto_inventory = source in {AUTO_SOURCE, BRIDGE_SOURCE}

    if not auto_inventory:
        unsupported: list[dict[str, str]] = []
        for index, interaction in enumerate(interactions):
            if not isinstance(interaction, dict):
                continue
            selector = _hover_activation(interaction.get("target") or interaction.get("selector"))
            if selector in captured_selectors:
                continue
            unsupported.append(
                {
                    "region": str(interaction.get("id") or selector or f"interaction-{index}"),
                    "triggerType": str(
                        interaction.get("trigger")
                        or interaction.get("triggerType")
                        or "interaction"
                    ),
                    "source": "interactions-detected.json",
                }
            )
        return unsupported

    existing_by_selector = {
        _hover_activation(item.get("target") or item.get("selector")): item
        for item in interactions
        if isinstance(item, dict)
    }
    active: list[dict[str, Any]] = []
    for index, selector in enumerate(sorted(captured_selectors)):
        capture = captured_by_selector[selector]
        existing = existing_by_selector.get(selector)
        item = dict(existing) if isinstance(existing, dict) else {}
        item.update(
            {
                "id": str(item.get("id") or f"live-hover-{index}"),
                "trigger": "hover",
                "target": selector,
                "referenceArtifacts": capture["artifacts"],
            }
        )
        active.append(item)

    prior_skips = payload.get("skipped") if is_wrapper else None
    interaction_skips = (
        [item for item in prior_skips if isinstance(item, dict)]
        if isinstance(prior_skips, list)
        else []
    )
    for item in skipped:
        selector = str(item.get("selector") or "")
        reason = str(item.get("reason") or "")
        trigger = str(item.get("triggerType") or "hover").strip().lower()
        if not selector or trigger not in HOVER_TRIGGERS or reason.startswith("duplicate selector"):
            continue
        interaction_skips.append(
            {
                "sourceArtifact": "regions.json",
                "sourceId": str(item.get("region") or selector),
                "trigger": trigger,
                "target": selector,
                "reason": reason,
            }
        )
    skipped_selectors = {
        str(item.get("target") or "") for item in interaction_skips if isinstance(item, dict)
    }
    for selector, interaction in existing_by_selector.items():
        if not selector or selector in captured_selectors or selector in skipped_selectors:
            continue
        interaction_skips.append(
            {
                "sourceArtifact": "interactions-detected.json",
                "sourceId": str(interaction.get("id") or selector),
                "trigger": str(
                    interaction.get("trigger") or interaction.get("triggerType") or "interaction"
                ),
                "target": selector,
                "reason": "auto interaction selector was not live-captured",
            }
        )

    reconciled = dict(payload)
    prior_derived = payload.get("derivedFrom") if is_wrapper else None
    derived_from = (
        [value for value in prior_derived if isinstance(value, str)]
        if isinstance(prior_derived, list)
        else []
    )
    if SUMMARY_NAME not in derived_from:
        derived_from.append(SUMMARY_NAME)
    if active or not _probe_failed(skipped):
        reconciled.update(
            {
                "source": BRIDGE_SOURCE,
                "interactions": active,
                "summary": {"hover": len(active), "click": 0, "scroll": 0},
                "derivedFrom": derived_from,
            }
        )
    if interaction_skips:
        reconciled["skipped"] = interaction_skips
    else:
        reconciled.pop("skipped", None)
    _write_json(path, reconciled)
    return []


PROBE_FAILURE_REASONS = frozenset(
    {
        "selector missing or not observable",
        "idle screenshot failed",
        "active screenshot failed",
        "viewport screenshot failed",
        "CDP hover failed",
        "affected selector missing or not observable",
        "region rect lies outside the viewport",
        "page does not scroll",
        "region is observable at fewer than two scroll positions",
    }
)


def _is_probe_failure(reason: str) -> bool:
    return (
        reason in PROBE_FAILURE_REASONS
        or reason.startswith("region crop failed")
        or "none are hoverable" in reason
        or "none are renderable" in reason
        or reason.startswith("scroll is virtualised by")
        or reason == "selector matches no elements"
    )


def _probe_failed(skipped: list[dict[str, Any]]) -> bool:
    return any(_is_probe_failure(str(entry.get("reason") or "")) for entry in skipped)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _promote_transition_spec(
    ref_dir: Path,
    url: str,
    session: str,
    captured: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> None:
    """Replace auto stubs with only live-observed hover transitions."""
    spec_path = ref_dir / "transition-spec.json"
    existing: Any = {}
    try:
        existing = json.loads(spec_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    existing = existing if isinstance(existing, dict) else {}
    auto_spec = bool(existing.get("placeholder")) or (existing.get("source") == AUTO_SOURCE)
    preserved = existing.get("transitions")
    existing_transitions = (
        [item for item in preserved if isinstance(item, dict)]
        if isinstance(preserved, list)
        else []
    )
    hover_rule_sources = _hover_rule_source_chunks(ref_dir)
    hover_rule_affected_targets = _hover_rule_affected_targets(ref_dir)
    spec_is_live = existing.get("source") == BRIDGE_SOURCE or (
        isinstance(existing.get("provenance"), dict)
        and existing["provenance"].get("kind") == "live-capture"
    )
    repaired_existing: list[dict[str, Any]] = []
    repaired_any = False
    for transition in existing_transitions:
        trigger = str(transition.get("trigger") or "").strip().lower()
        prior_animation = transition.get("animation")
        animation_type = (
            str(prior_animation.get("type") or "").strip().lower()
            if isinstance(prior_animation, dict)
            else ""
        )
        entry_is_live = (
            spec_is_live or "live-capture" in str(transition.get("bundle_branch") or "").lower()
        )
        is_hover = trigger.startswith("hover") or "hover" in animation_type
        source_chunk = _real_source_chunk(ref_dir, transition.get("source_chunk"))
        if entry_is_live and is_hover and source_chunk is None:
            target = str(transition.get("target") or transition.get("selector") or "")
            repaired = dict(transition)
            repaired["source_chunk"] = hover_rule_sources.get(
                _hover_activation(target), "inline init"
            )
            repaired_existing.append(repaired)
            repaired_any = True
        else:
            repaired_existing.append(transition)

    prior_skips = existing.get("skipped")
    structured_skips = (
        [item for item in prior_skips if isinstance(item, dict)]
        if isinstance(prior_skips, list)
        else []
    )
    for item in skipped:
        reason = str(item.get("reason") or "")
        if reason.startswith("duplicate selector"):
            continue
        structured_skips.append(
            {
                "sourceArtifact": "regions.json",
                "sourceId": str(item.get("region") or "hover-region"),
                "trigger": str(item.get("triggerType") or "hover"),
                "reason": reason,
            }
        )

    if not captured:
        if existing and auto_spec and structured_skips:
            # Capturing nothing proves nothing about the candidates when the
            # probe failed; emptying transitions would delete the list a
            # corrected re-run needs. A probed negative may still prune.
            existing["transitions"] = repaired_existing if _probe_failed(skipped) else []
            existing["skipped"] = structured_skips
            _write_json(spec_path, existing)
        elif existing and repaired_any:
            existing["transitions"] = repaired_existing
            _write_json(spec_path, existing)
        return

    existing_transitions = repaired_existing
    source_chunks: dict[str, str] = {}
    for transition in existing_transitions:
        source_chunk = _real_source_chunk(ref_dir, transition.get("source_chunk"))
        if source_chunk is None:
            continue
        target = str(transition.get("target") or transition.get("selector") or "")
        source_chunks[target] = source_chunk
        source_chunks[_hover_activation(target)] = source_chunk
    for selector, source_chunk in hover_rule_sources.items():
        source_chunks.setdefault(selector, source_chunk)
    preserving_foreign = (
        not auto_spec and isinstance(preserved, list) and bool(existing_transitions)
    )
    transitions = [] if auto_spec or not isinstance(preserved, list) else existing_transitions
    captured_keys = {
        (
            str(item["selector"]),
            "scroll"
            if _is_scroll_trigger(str(item.get("triggerType") or "").strip().lower())
            else "hover",
        )
        for item in captured
    }
    transitions = [
        item
        for item in transitions
        if (
            str(item.get("target") or item.get("selector") or ""),
            (
                "hover"
                if str(item.get("trigger") or "").strip().lower().startswith("hover")
                else str(item.get("trigger") or "").strip().lower()
            ),
        )
        not in captured_keys
    ]
    transition_ids = {
        str(item.get("id") or "") for item in transitions if str(item.get("id") or "")
    }

    for index, item in enumerate(captured):
        observation = item["observation"]
        changed = observation["changedProperties"]
        is_scroll = _is_scroll_trigger(str(item.get("triggerType") or "").strip().lower())
        transition_id = _safe_transition_id(item["region"], index)
        if transition_id in transition_ids:
            base_id = transition_id
            suffix = 2
            while f"{base_id}-{suffix}" in transition_ids:
                suffix += 1
            transition_id = f"{base_id}-{suffix}"
        transition_ids.add(transition_id)
        generated_animation: dict[str, Any] = {
            "type": "scroll" if is_scroll else "css-hover",
            "property": ", ".join(changed),
            "changedProperties": changed,
            "from": observation["from"],
            "to": observation["to"],
        }
        for key in (
            "duration",
            "easing",
            "pixelCorroborated",
            "pixelComparable",
            "outsideBoxChange",
            "progression",
            "ladderPcts",
            "changedAtPcts",
        ):
            if key in observation:
                generated_animation[key] = observation[key]
        generated_transition: dict[str, Any] = {
            "id": transition_id,
            "trigger": "scroll" if is_scroll else "hover",
            "source_chunk": source_chunks.get(str(item["selector"]), "inline init"),
            "bundle_branch": (
                "live-capture: agent-browser scroll ladder"
                if is_scroll
                else "live-capture: agent-browser CDP hover plus DOM hover events"
            ),
            # Keep the activation selector as the primary target so the entry
            # remains a valid input to regions.json derivation and hover dispatch.
            "target": item["selector"],
            "animation": generated_animation,
            "reference_frames": [
                item["artifacts"][key]
                for key in ("idle", "active", "before", "mid", "after")
                if key in item["artifacts"]
            ],
        }
        if not is_scroll:
            affected_target = hover_rule_affected_targets.get(_hover_activation(item["selector"]))
            if affected_target:
                generated_transition["affectedTarget"] = affected_target
        transitions.append(generated_transition)

    promoted: dict[str, Any] = {
        **existing,
        "schemaVersion": existing.get("schemaVersion", 1),
        "source": existing.get("source") if preserving_foreign else BRIDGE_SOURCE,
        "placeholder": False,
        "provenance": {
            "kind": "live-capture",
            "url": url,
            "session": session,
            "driver": "agent-browser CDP hover plus DOM hover events",
        },
        "transitions": transitions,
    }
    if structured_skips:
        promoted["skipped"] = structured_skips
    else:
        promoted.pop("skipped", None)
    _write_json(spec_path, promoted)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("session")
    parser.add_argument("ref_dir", type=Path)
    parser.add_argument("--reuse-session", action="store_true")
    args = parser.parse_args(argv)

    ref_dir = args.ref_dir.resolve()
    summary: dict[str, Any] = {
        "schemaVersion": 1,
        "url": args.url,
        "session": args.session if args.reuse_session else f"{args.session}-region-artifacts",
        "reuseSession": args.reuse_session,
        "attempted": [],
        "captured": [],
        "skipped": [],
        "unsupported": [],
        "status": "fail",
    }
    summary_path = ref_dir / SUMMARY_NAME
    try:
        regions = _load_or_derive_regions(ref_dir)
    except (OSError, ValueError) as exc:
        summary["skipped"].append({"region": "regions.json", "reason": str(exc)})
        _write_json(summary_path, summary)
        print(f"capture-region-artifacts: {exc}", file=sys.stderr)
        return 2

    auto_spec = _spec_is_auto(ref_dir)
    session = summary["session"]
    if auto_spec:
        regions = _prune_auto_dispatch_regions(
            regions, summary["skipped"], _verification_signals(ref_dir)
        )
    dispatch_is_obligation = not _spec_is_bridge_owned(ref_dir)
    capturable_count = sum(
        1
        for region in _walk_region_dicts(regions)
        if _is_capturable(region, dispatch_is_obligation=dispatch_is_obligation)
    )
    opened = False
    if capturable_count and not args.reuse_session:
        opened = _run(session, "open", args.url).returncode == 0
        if not opened:
            _run(session, "close")
            summary["skipped"].append({"region": "session", "reason": "agent-browser open failed"})
            _write_json(summary_path, summary)
            return 2
        _run(session, "wait", "1000")

    try:
        updated = _capture_regions(
            regions,
            session,
            ref_dir,
            summary,
            set(),
            [0],
            preserve_failed_dispatch=not auto_spec,
            dispatch_is_obligation=dispatch_is_obligation,
        )
        summary["unsupported"] = _unsupported_regions(updated, dispatch_only_is_supported=False)
        summary["unsupported"].extend(
            _reconcile_interactions(
                ref_dir,
                summary["captured"],
                summary["skipped"],
            )
        )
        inventory_unproven = _probe_failed(summary["skipped"])
        # Kept before the notInstantiated split below moves entries out of
        # summary["skipped"]; consumers that judge whether the probe worked
        # must see every skip, not the post-split remainder.
        probe_skips = list(summary["skipped"])
        not_instantiated = [
            entry
            for entry in summary["skipped"]
            if entry.get("reason") == "selector matches no elements"
        ]
        if not_instantiated:
            dropped = {id(entry) for entry in not_instantiated}
            names = {str(entry.get("region")) for entry in not_instantiated}
            summary["notInstantiated"] = not_instantiated
            summary["skipped"] = [entry for entry in summary["skipped"] if id(entry) not in dropped]
            summary["attempted"] = [
                entry for entry in summary["attempted"] if str(entry.get("region")) not in names
            ]
        summary["counts"] = {
            "attempted": len(summary["attempted"]),
            "captured": len(summary["captured"]),
            "skipped": len(summary["skipped"]),
            "unsupported": len(summary["unsupported"]),
            "notInstantiated": len(not_instantiated),
        }
        expected_evidence = bool(
            summary["counts"]["attempted"] or summary["counts"]["notInstantiated"]
        )
        summary["status"] = (
            "fail"
            if summary["unsupported"] or (expected_evidence and not summary["counts"]["captured"])
            else "pass"
        )
        if summary["captured"] and isinstance(updated, dict):
            updated["source"] = BRIDGE_SOURCE
            updated["placeholder"] = False
            updated["detectionRan"] = True
            updated["liveCaptureBacked"] = True
            derived_from = updated.get("derivedFrom")
            derived_from = (
                [value for value in derived_from if isinstance(value, str)]
                if isinstance(derived_from, list)
                else []
            )
            if SUMMARY_NAME not in derived_from:
                derived_from.append(SUMMARY_NAME)
            updated["derivedFrom"] = derived_from
        # Persisting the pruned list after a failed probe would delete the
        # candidate inventory instead of recording that it went unproven.
        if summary["captured"] or not inventory_unproven:
            _normalize_region_geometry(updated)
            _write_json(ref_dir / "regions.json", updated)
        _promote_transition_spec(
            ref_dir,
            args.url,
            session,
            summary["captured"],
            probe_skips,
        )
        _write_json(summary_path, summary)
        if summary["unsupported"]:
            return 4
        # A run that attempted regions and captured none proved nothing; an exit
        # code of 0 here would hand the pipeline an empty evidence set as a pass.
        return 5 if summary["status"] == "fail" else 0
    finally:
        if opened:
            _run(session, "close")


def _walk_region_dicts(node: Any) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if isinstance(node.get("triggerType"), str):
            regions.append(node)
        for value in node.values():
            regions.extend(_walk_region_dicts(value))
    elif isinstance(node, list):
        for value in node:
            regions.extend(_walk_region_dicts(value))
    return regions


if __name__ == "__main__":
    raise SystemExit(main())
