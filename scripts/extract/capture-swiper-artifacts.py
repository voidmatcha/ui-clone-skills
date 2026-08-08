#!/usr/bin/env python3
"""Capture live Swiper idle/next-slide reference artifacts and runtime params."""

from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any

SUMMARY_NAME = "capture-swiper-artifacts-summary.json"
SOURCE = "scripts/extract/capture-swiper-artifacts.py"
MAX_SWIPERS = 6
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _png_pixels(path: Path) -> tuple[tuple[int, int, int, int], bytes]:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG")
    offset = len(PNG_SIGNATURE)
    header: bytes | None = None
    compressed = bytearray()
    palette = bytearray()
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        if len(chunk) != length:
            raise ValueError("truncated PNG chunk")
        offset += 12 + length
        if kind == b"IHDR":
            header = chunk
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind in {b"PLTE", b"tRNS"}:
            palette.extend(kind + chunk)
        elif kind == b"IEND":
            break
    if header is None or len(header) != 13 or not compressed:
        raise ValueError("incomplete PNG")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", header
    )
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if (
        channels is None
        or depth != 8
        or compression != 0
        or filtering != 0
        or interlace != 0
        or width <= 0
        or height <= 0
    ):
        raise ValueError("unsupported PNG format")
    raw = zlib.decompress(bytes(compressed))
    stride = width * channels
    if len(raw) != height * (stride + 1):
        raise ValueError("unexpected PNG scanline size")
    previous = bytearray(stride)
    normalized = bytearray()
    pos = 0
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        current = bytearray(raw[pos : pos + stride])
        pos += stride
        for index, value in enumerate(current):
            left = current[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                current[index] = (value + left) & 0xFF
            elif filter_type == 2:
                current[index] = (value + above) & 0xFF
            elif filter_type == 3:
                current[index] = (value + ((left + above) // 2)) & 0xFF
            elif filter_type == 4:
                estimate = left + above - upper_left
                pa = abs(estimate - left)
                pb = abs(estimate - above)
                pc = abs(estimate - upper_left)
                predictor = (
                    left if pa <= pb and pa <= pc else above if pb <= pc else upper_left
                )
                current[index] = (value + predictor) & 0xFF
            elif filter_type != 0:
                raise ValueError("unknown PNG filter")
        normalized.extend(current)
        previous = current
    return (width, height, depth, color), bytes(palette + normalized)


def _images_differ(idle: Path, active: Path) -> bool:
    try:
        return _png_pixels(idle) != _png_pixels(active)
    except (OSError, ValueError, zlib.error):
        return False


def _safe_name(value: object, index: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-._")
    return f"{index:02d}-{(cleaned or 'swiper')[:50]}"


def _discover(session: str) -> list[dict[str, Any]]:
    result = _eval(
        session,
        (
            "(() => {"
            "const found=[];"
            "window.__uiCloneSwiperCapture=[];"
            "for(const el of document.querySelectorAll('.swiper')){"
            "const s=el.swiper;if(!s||typeof s.slideNext!=='function')continue;"
            "const r=el.getBoundingClientRect(),cs=getComputedStyle(el);"
            "if(r.width<=0||r.height<=0||cs.display==='none'||cs.visibility==='hidden')continue;"
            f"if(found.length>={MAX_SWIPERS})break;"
            "const i=found.length,selector=`.swiper[data-ui-clone-swiper=\"${i}\"]`;"
            "el.setAttribute('data-ui-clone-swiper',String(i));"
            "const autoplayRunning=!!(s.autoplay&&s.autoplay.running);"
            "if(autoplayRunning&&typeof s.autoplay.stop==='function')s.autoplay.stop();"
            "window.__uiCloneSwiperCapture.push({el,s,index:s.activeIndex,"
            "realIndex:s.realIndex,autoplayRunning});"
            "const p=s.params||{};"
            "found.push({selector,index:i,activeIndex:s.activeIndex,realIndex:s.realIndex,"
            "params:{slidesPerView:p.slidesPerView??null,spaceBetween:p.spaceBetween??null,"
            "effect:p.effect??'slide',loop:!!p.loop,speed:Number(p.speed)||0,"
            "autoplay:p.autoplay||false},rect:{x:r.x,y:r.y,width:r.width,height:r.height},"
            "viewport:{width:innerWidth,height:innerHeight}});"
            "}return {instances:found};"
            "})()"
        ),
    )
    instances = result.get("instances")
    return [item for item in instances if isinstance(item, dict)] if isinstance(
        instances, list
    ) else []


def _move(session: str, index: int, direction: str) -> dict[str, Any]:
    if direction == "next":
        body = (
            "const item=window.__uiCloneSwiperCapture?.["
            f"{index}];if(!item)return {{ok:false}};"
            "item.s.slideNext();"
            "return {ok:true,speed:Number(item.s.params?.speed)||0,"
            "activeIndex:item.s.activeIndex,realIndex:item.s.realIndex};"
        )
    else:
        body = (
            "const item=window.__uiCloneSwiperCapture?.["
            f"{index}];if(!item)return {{ok:false}};"
            "const s=item.s;"
            "if(s.params?.loop&&typeof s.slideToLoop==='function')"
            "s.slideToLoop(item.realIndex,0,false);"
            "else if(typeof s.slideTo==='function')s.slideTo(item.index,0,false);"
            "return {ok:true,activeIndex:s.activeIndex,realIndex:s.realIndex};"
        )
    return _eval(session, f"(() => {{{body}}})()")


def _scroll_and_rect(session: str, index: int) -> dict[str, Any]:
    return _eval(
        session,
        (
            "(() => {"
            f"const item=window.__uiCloneSwiperCapture?.[{index}];"
            "if(!item)return {ok:false};"
            "const top=item.el.getBoundingClientRect().top+scrollY;"
            "window.scrollTo(0,Math.max(0,top-40));"
            "const r=item.el.getBoundingClientRect();"
            "return {ok:true,x:r.x,y:r.y,width:r.width,height:r.height,"
            "viewportWidth:innerWidth,viewportHeight:innerHeight};"
            "})()"
        ),
    )


def _crop_viewport(viewport: Path, output: Path, rect: dict[str, Any]) -> bool:
    try:
        (png_width, png_height, _, _), _ = _png_pixels(viewport)
        viewport_width = float(rect.get("viewportWidth") or 0)
        viewport_height = float(rect.get("viewportHeight") or 0)
        if viewport_width <= 0 or viewport_height <= 0:
            return False
        scale_x = png_width / viewport_width
        scale_y = png_height / viewport_height
        x = max(0, round(float(rect.get("x") or 0) * scale_x))
        y = max(0, round(float(rect.get("y") or 0) * scale_y))
        width = min(
            png_width - x, max(1, round(float(rect.get("width") or 0) * scale_x))
        )
        height = min(
            png_height - y,
            max(1, round(float(rect.get("height") or 0) * scale_y)),
        )
    except (OSError, TypeError, ValueError, zlib.error):
        return False
    result = subprocess.run(
        [
            "magick",
            str(viewport),
            "-crop",
            f"{width}x{height}+{x}+{y}",
            "+repage",
            f"PNG32:{output}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and output.is_file()


def _viewport_crop(session: str, index: int, output: Path) -> bool:
    rect = _scroll_and_rect(session, index)
    if rect.get("ok") is not True:
        return False
    _run(session, "wait", "150")
    viewport = output.with_name(output.stem + "-viewport.png")
    result = _run(session, "screenshot", str(viewport))
    try:
        return result.returncode == 0 and _crop_viewport(viewport, output, rect)
    finally:
        viewport.unlink(missing_ok=True)


def _capture_pair(
    session: str,
    instance: dict[str, Any],
    index: int,
    ref_dir: Path,
) -> tuple[dict[str, str] | None, dict[str, Any] | None, str]:
    selector = str(instance.get("selector") or "")
    if not selector:
        return None, None, "missing stable selector"
    stem = _safe_name(selector, index)
    idle_rel = f"transitions/ref/{stem}-idle.png"
    active_rel = f"transitions/ref/{stem}-active.png"
    idle = ref_dir / idle_rel
    active = ref_dir / active_rel
    idle.parent.mkdir(parents=True, exist_ok=True)

    idle_result = _run(session, "screenshot", selector, str(idle))
    moved = _move(session, index, "next")
    if moved.get("ok") is not True:
        idle.unlink(missing_ok=True)
        return None, None, "swiper.slideNext() failed"
    speed = max(0, min(10000, int(float(moved.get("speed") or 0))))
    _run(session, "wait", str(speed + 250))
    active_result = _run(session, "screenshot", selector, str(active))
    capture_mode = "selector"

    if (
        idle_result.returncode != 0
        or active_result.returncode != 0
        or not _images_differ(idle, active)
    ):
        _move(session, index, "restore")
        idle.unlink(missing_ok=True)
        active.unlink(missing_ok=True)
        if not _viewport_crop(session, index, idle):
            return None, None, "selector capture failed and viewport crop was unavailable"
        moved = _move(session, index, "next")
        if moved.get("ok") is not True:
            idle.unlink(missing_ok=True)
            return None, None, "swiper.slideNext() failed during viewport fallback"
        speed = max(0, min(10000, int(float(moved.get("speed") or 0))))
        _run(session, "wait", str(speed + 250))
        if not _viewport_crop(session, index, active) or not _images_differ(
            idle, active
        ):
            idle.unlink(missing_ok=True)
            active.unlink(missing_ok=True)
            return None, None, "idle and active pixels are identical or invalid"
        capture_mode = "viewport-post-crop"

    _move(session, index, "restore")
    return (
        {"idle": idle_rel, "active": active_rel},
        {
            "before": {
                "activeIndex": instance.get("activeIndex"),
                "realIndex": instance.get("realIndex"),
            },
            "after": {
                "activeIndex": moved.get("activeIndex"),
                "realIndex": moved.get("realIndex"),
            },
            "captureMode": capture_mode,
        },
        "",
    )


def _restore_all(session: str) -> None:
    _eval(
        session,
        (
            "(() => {"
            "for(const item of window.__uiCloneSwiperCapture||[]){"
            "const s=item.s;"
            "if(s.params?.loop&&typeof s.slideToLoop==='function')"
            "s.slideToLoop(item.realIndex,0,false);"
            "else if(typeof s.slideTo==='function')s.slideTo(item.index,0,false);"
            "if(item.autoplayRunning&&s.autoplay&&typeof s.autoplay.start==='function')"
            "s.autoplay.start();"
            "item.el.removeAttribute('data-ui-clone-swiper');"
            "}delete window.__uiCloneSwiperCapture;return {ok:true};"
            "})()"
        ),
    )


def _source_chunk(ref_dir: Path) -> str:
    try:
        payload: Any = json.loads(
            (ref_dir / "bundle-extraction.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {}
    unresolved = payload.get("unresolved") if isinstance(payload, dict) else None
    if isinstance(unresolved, list):
        for item in unresolved:
            if not isinstance(item, dict) or str(item.get("library")).lower() != "swiper":
                continue
            source = item.get("source")
            if isinstance(source, str) and source.strip():
                normalized = source.strip()
                if (ref_dir / normalized).is_file():
                    return normalized
    candidates: list[tuple[int, str]] = []
    bundles = ref_dir / "bundles"
    if bundles.is_dir():
        for path in bundles.iterdir():
            if not path.is_file():
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "slidesPerView" not in source:
                continue
            score = sum(
                source.count(token)
                for token in (
                    "slidesPerView",
                    "spaceBetween",
                    "slideNext",
                    "autoplay",
                    ".swiper",
                )
            )
            candidates.append((score, f"bundles/{path.name}"))
    if candidates:
        return max(candidates)[1]
    return "inline init"


def _is_swiper_transition(item: dict[str, Any]) -> bool:
    trigger = str(item.get("trigger") or "").lower()
    animation = item.get("animation")
    animation_type = (
        str(animation.get("type") or "").lower()
        if isinstance(animation, dict)
        else ""
    )
    return "swiper" in trigger or "swiper" in animation_type


def _replaceable_swiper_transition(item: dict[str, Any]) -> bool:
    if not _is_swiper_transition(item):
        return False
    identifier = str(item.get("id") or "")
    branch = str(item.get("bundle_branch") or "").lower()
    return (
        identifier.startswith("auto-")
        or identifier.startswith("live-swiper-")
        or branch.startswith("settled branch")
        or "el.swiper.slidenext()" in branch
    )


def _has_reference_pair(ref_dir: Path, item: dict[str, Any]) -> bool:
    frames = item.get("reference_frames")
    return (
        isinstance(frames, list)
        and len(frames) >= 2
        and all(
            isinstance(frame, str) and (ref_dir / frame).is_file()
            for frame in frames[:2]
        )
    )


def _merge_outputs(
    ref_dir: Path,
    url: str,
    session: str,
    captured: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> None:
    spec_path = ref_dir / "transition-spec.json"
    try:
        spec: Any = json.loads(spec_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        spec = {}
    spec = spec if isinstance(spec, dict) else {}
    prior = spec.get("transitions")
    transitions = (
        [item for item in prior if isinstance(item, dict)]
        if isinstance(prior, list)
        else []
    )
    if captured:
        transitions = [
            item for item in transitions if not _replaceable_swiper_transition(item)
        ]
    source_chunk = _source_chunk(ref_dir)
    for item in captured:
        params = item["params"]
        transitions.append(
            {
                "id": f"live-swiper-{item['index']}",
                "trigger": "swiper-next",
                "source_chunk": source_chunk,
                "bundle_branch": "live-capture: el.swiper.slideNext() runtime instance",
                "target": item["selector"],
                "selector": item["selector"],
                "placeholder": False,
                "animation": {
                    "type": "swiper",
                    "action": "slideNext",
                    **params,
                },
                "reference_frames": [
                    item["artifacts"]["idle"],
                    item["artifacts"]["active"],
                ],
            }
        )
    spec.update({"schemaVersion": spec.get("schemaVersion", 1), "transitions": transitions})
    if captured:
        spec["source"] = SOURCE
        spec["provenance"] = {
            "kind": "live-capture",
            "url": url,
            "session": session,
            "driver": "agent-browser el.swiper.slideNext()",
        }
        remaining_draft = any(
            item.get("placeholder") is not False
            and str(item.get("bundle_branch") or "").startswith("settled branch")
            for item in transitions
        )
        spec["placeholder"] = bool(spec.get("placeholder")) and remaining_draft
    if skipped:
        prior_skips = spec.get("skipped")
        merged_skips = (
            [item for item in prior_skips if isinstance(item, dict)]
            if isinstance(prior_skips, list)
            else []
        )
        merged_skips.extend(skipped)
        spec["skipped"] = merged_skips
    _write_json(spec_path, spec)

    regions_path = ref_dir / "regions.json"
    try:
        regions: Any = json.loads(regions_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        regions = {}
    regions = regions if isinstance(regions, dict) else {}
    current = regions.get("regions")
    entries = [item for item in current if isinstance(item, dict)] if isinstance(
        current, list
    ) else []
    if captured:
        entries = [
            item
            for item in entries
            if "swiper" not in str(item.get("triggerType") or "").lower()
        ]
    for item in captured:
        entries.append(
            {
                "name": f"live-swiper-{item['index']}",
                "triggerType": "swiper-next",
                "selector": item["selector"],
                "referenceFrames": [
                    item["artifacts"]["idle"],
                    item["artifacts"]["active"],
                ],
                "artifacts": item["artifacts"],
                "runtimeParams": item["params"],
            }
        )
    regions["regions"] = entries
    if captured:
        regions.update(
            {
                "placeholder": False,
                "source": SOURCE,
                "detectionRan": True,
                "liveCaptureBacked": True,
            }
        )
        derived = regions.get("derivedFrom")
        derived_from = (
            [value for value in derived if isinstance(value, str)]
            if isinstance(derived, list)
            else []
        )
        if SUMMARY_NAME not in derived_from:
            derived_from.append(SUMMARY_NAME)
        regions["derivedFrom"] = derived_from
    _write_json(regions_path, regions)
    _refresh_derived_transition_artifacts(ref_dir, transitions)


def _refresh_derived_transition_artifacts(
    ref_dir: Path, transitions: list[dict[str, Any]]
) -> None:
    coverage_path = ref_dir / "transition-coverage.json"
    try:
        coverage: Any = json.loads(coverage_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        coverage = None
    if isinstance(coverage, dict) and coverage.get("source") == (
        "ui_clone.extraction_artifacts"
    ):
        coverage["animatedElements"] = [
            {
                "id": item.get("id"),
                "selector": item.get("selector") or item.get("target"),
                "trigger": item.get("trigger"),
                "decoded": {"source": item.get("source_chunk")},
            }
            for item in transitions
        ]
        _write_json(coverage_path, coverage)

    # extracted.json depends on transition-coverage.json in the artifact DAG,
    # so write it last. Reversing this order makes every successful Swiper
    # capture leave the assembled handoff stale.
    extracted_path = ref_dir / "extracted.json"
    try:
        extracted: Any = json.loads(extracted_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        extracted = None
    if isinstance(extracted, dict) and extracted.get("source") == (
        "ui_clone.extraction_artifacts"
    ):
        extracted["transitions"] = transitions
        _write_json(extracted_path, extracted)


def _unsupported_authored_swipers(ref_dir: Path) -> list[dict[str, str]]:
    try:
        spec: Any = json.loads(
            (ref_dir / "transition-spec.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    transitions = spec.get("transitions") if isinstance(spec, dict) else None
    if not isinstance(transitions, list):
        return []
    unsupported = []
    for item in transitions:
        if (
            not isinstance(item, dict)
            or not _is_swiper_transition(item)
            or _replaceable_swiper_transition(item)
            or _has_reference_pair(ref_dir, item)
        ):
            continue
        unsupported.append(
            {
                "triggerType": str(item.get("trigger") or "swiper"),
                "selector": str(item.get("selector") or item.get("target") or ""),
                "reason": "authored Swiper obligation lacks two reference frame files",
            }
        )
    return unsupported


def _has_swiper_signal(ref_dir: Path) -> bool:
    try:
        plan: Any = json.loads(
            (ref_dir / "verification-plan.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    signals = plan.get("signals") if isinstance(plan, dict) else None
    return isinstance(signals, dict) and signals.get("hasSwiper") is True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("session")
    parser.add_argument("ref_dir", type=Path)
    args = parser.parse_args(argv)
    ref_dir = args.ref_dir.resolve()
    session = f"{args.session}-swiper-artifacts"
    summary: dict[str, Any] = {
        "schemaVersion": 1,
        "source": SOURCE,
        "url": args.url,
        "session": session,
        "attempted": [],
        "captured": [],
        "skipped": [],
        "unsupported": [],
        "status": "fail",
    }
    summary_path = ref_dir / SUMMARY_NAME
    opened = _run(session, "open", args.url).returncode == 0
    if not opened:
        _run(session, "close")
        summary["unsupported"].append(
            {"triggerType": "swiper", "reason": "agent-browser open failed"}
        )
        _write_json(summary_path, summary)
        return 2
    _run(session, "wait", "1000")
    try:
        instances = _discover(session)
        summary["unsupported"].extend(_unsupported_authored_swipers(ref_dir))
        for index, instance in enumerate(instances):
            selector = str(instance.get("selector") or "")
            summary["attempted"].append({"index": index, "selector": selector})
            artifacts, observation, reason = _capture_pair(
                session, instance, index, ref_dir
            )
            if artifacts is None or observation is None:
                summary["skipped"].append(
                    {
                        "index": index,
                        "selector": selector,
                        "triggerType": "swiper-next",
                        "reason": reason,
                    }
                )
                summary["unsupported"].append(
                    {
                        "triggerType": "swiper-next",
                        "selector": selector,
                        "reason": reason,
                    }
                )
                continue
            captured = {
                "index": index,
                "selector": selector,
                "params": (
                    instance.get("params")
                    if isinstance(instance.get("params"), dict)
                    else {}
                ),
                "artifacts": artifacts,
                "observation": observation,
            }
            summary["captured"].append(captured)
        if (
            _has_swiper_signal(ref_dir)
            and not summary["captured"]
            and not summary["attempted"]
        ):
            summary["unsupported"].append(
                {
                    "triggerType": "swiper",
                    "reason": "hasSwiper is true but no live Swiper transition was captured",
                }
            )
        _merge_outputs(
            ref_dir,
            args.url,
            session,
            summary["captured"],
            summary["skipped"],
        )
        summary["counts"] = {
            "attempted": len(summary["attempted"]),
            "captured": len(summary["captured"]),
            "skipped": len(summary["skipped"]),
            "unsupported": len(summary["unsupported"]),
        }
        summary["status"] = "fail" if summary["unsupported"] else "pass"
        _write_json(summary_path, summary)
        return 4 if summary["unsupported"] else 0
    finally:
        _restore_all(session)
        _run(session, "close")


if __name__ == "__main__":
    raise SystemExit(main())
