#!/usr/bin/env python3
"""Standalone helpers for video-play-proof-check.sh."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

VIDEO_EXT = re.compile(r"\.(mp4|webm|mov|m3u8|mpd)(?:$|[?#\"'])", re.I)
RULE = (
    "When ref artifacts reference .mp4/.webm/.mov/.m3u8/.mpd media or a "
    "video-class transition, the impl page must contain ≥1 <video> element "
    "AND that element must advance currentTime by >0.1s within the "
    "observation window after attempted play(). Browsers allow muted "
    "autoplay without user gesture, so muted videos must demonstrate "
    "playback automatically; user-gesture-required (non-muted, non-autoplay) "
    "videos are not faulted because they cannot be triggered without UI."
)


def _load_json(ref_dir: Path, name: str) -> Any:
    path = ref_dir / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _contains_video_url(value: Any) -> bool:
    if isinstance(value, str):
        return bool(VIDEO_EXT.search(value))
    if isinstance(value, list):
        return any(_contains_video_url(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_video_url(item) for item in value.values())
    return False


def detect_ref_video(ref_dir: Path) -> bool:
    required = _load_json(ref_dir, "required-media.json")
    if isinstance(required, dict):
        for key in ("videos", "videoUrls", "videoURLs", "video", "requiredVideos"):
            value = required.get(key)
            if isinstance(value, list) and any(
                _contains_video_url(item) for item in value
            ):
                return True
        totals = required.get("totals")
        if isinstance(totals, dict) and int(totals.get("video", 0) or 0) > 0:
            return True
        if _contains_video_url(required):
            return True

    coverage = _load_json(ref_dir, "required-media-coverage.json")
    if isinstance(coverage, dict) and _contains_video_url(coverage.get("videos")):
        return True

    for name in ("transition-spec.json", "animations-detected.json"):
        data = _load_json(ref_dir, name)
        if not isinstance(data, dict):
            continue
        if _contains_video_url(data):
            return True
        text = json.dumps(data).lower()
        if "hero-video" in text or "videomime" in text:
            return True

    live_state = _load_json(ref_dir, "live-dynamic-state.json")
    if isinstance(live_state, dict):
        ref_state = live_state.get("ref")
        actions = ref_state.get("actions") if isinstance(ref_state, dict) else None
        if (
            isinstance(actions, list)
            and any(str(action).startswith("video:") for action in actions)
            and _contains_video_url(ref_state)
        ):
            return True

    manifest = _load_json(ref_dir, "resource-manifest.json")
    if isinstance(manifest, dict):
        entries = (
            manifest.get("resources")
            or manifest.get("entries")
            or manifest.get("items")
        )
        if isinstance(entries, list) and any(
            isinstance(entry, dict)
            and str(entry.get("initiatorType", "")).lower() == "video"
            and _contains_video_url(entry)
            for entry in entries
        ):
            return True
    elif isinstance(manifest, list) and any(
        isinstance(entry, dict)
        and str(entry.get("initiatorType", "")).lower() == "video"
        and _contains_video_url(entry)
        for entry in manifest
    ):
        return True

    return False


def write_skip(out_path: Path) -> None:
    payload = {
        "schemaVersion": 1,
        "status": "skip",
        "reasons": ["ref has no video signal — gate does not apply"],
        "rule": (
            "When ref artifacts reference .mp4/.webm/.mov media or a video-class "
            "transition, the impl page must contain ≥1 <video> element AND that "
            "element must advance currentTime by >0.1s within a 2s observation "
            "window after attempted play()."
        ),
    }
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "skip", "out": str(out_path)}))


def _read_probe(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"error": "probe-missing"}
    for line in reversed(text.strip().splitlines()):
        candidate = line.strip()
        if not (candidate.startswith("{") or candidate.startswith('"{')):
            continue
        try:
            value = json.loads(candidate)
            if isinstance(value, str):
                value = json.loads(value)
            if isinstance(value, dict):
                return value
        except (json.JSONDecodeError, TypeError):
            continue
    return {"error": "probe-parse-failed"}


def write_result(
    out_path: Path,
    probe_path: Path,
    impl_url: str,
) -> int:
    probe = _read_probe(probe_path)
    reasons: list[str] = []
    count = int(probe.get("count", 0))
    advanced = int(probe.get("advancedCount", 0))
    eligible = int(probe.get("eligibleCount", count))
    gesture_only = int(probe.get("gestureOnlyCount", 0))
    wait_ms = int(probe.get("waitMs", 2000))

    if probe.get("error"):
        status = "fail"
        reasons.append(f"probe failed: {probe['error']}")
    elif count == 0:
        status = "fail"
        reasons.append(
            "ref signaled video but impl page has zero <video> elements. "
            "required-media-coverage may pass on the .mp4 file existing in "
            "public/, but the element that renders it is missing."
        )
    elif eligible == 0 and gesture_only > 0:
        status = "pass"
        reasons.append(
            f"informational: all {gesture_only} <video> element(s) are "
            "gesture-required (unmuted, non-autoplay) — cannot be tested "
            "without UI interaction. Gate passed without runtime advancement."
        )
    elif advanced == 0:
        status = "fail"
        reasons.append(
            f"{eligible} <video> element(s) eligible for autoplay (muted/autoplay) "
            f"but none advanced currentTime by >0.1s in {wait_ms}ms. Causes: "
            "missing `autoplay muted playsinline`, src URL 404, codec mismatch, "
            "IntersectionObserver hiding the video before play() could fire, or "
            "src= bound to state that hasn't initialized."
        )
    else:
        status = "pass"

    payload = {
        "schemaVersion": 1,
        "status": status,
        "implUrl": impl_url,
        "videoCount": count,
        "advancedCount": advanced,
        "before": probe.get("before", [])[:10],
        "after": probe.get("after", [])[:10],
        "reasons": reasons,
        "nextAction": (
            "Add `autoplay muted playsinline` attributes to the impl <video>, "
            "OR programmatically call play() after the play-trigger condition "
            "is met. Confirm the src URL serves 200 and the codec is supported."
            if reasons
            else "all required videos advanced"
        ),
        "rule": RULE,
    }
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": status,
                "videos": count,
                "advanced": advanced,
                "out": str(out_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if status in {"pass", "skip"} else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect = subparsers.add_parser("detect-ref")
    detect.add_argument("ref_dir", type=Path)

    skip = subparsers.add_parser("write-skip")
    skip.add_argument("out_path", type=Path)

    result = subparsers.add_parser("write-result")
    result.add_argument("out_path", type=Path)
    result.add_argument("probe_path", type=Path)
    result.add_argument("impl_url")

    args = parser.parse_args()
    if args.command == "detect-ref":
        print("true" if detect_ref_video(args.ref_dir) else "false")
        return 0
    if args.command == "write-skip":
        write_skip(args.out_path)
        return 0
    return write_result(args.out_path, args.probe_path, args.impl_url)


if __name__ == "__main__":
    raise SystemExit(main())
