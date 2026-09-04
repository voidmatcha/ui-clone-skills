#!/usr/bin/env python3
"""Capture implementation replay tracks and compare them to declared refs."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

from ui_clone.replay_track import (
    compare_tracks,
    track_sha256,
    validate_track,
    verify_recording_manifest,
)

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[2]
_DEFAULT_CAPTURE = _REPO_ROOT / "scripts" / "extract" / "capture-replay-track.sh"
_REPORT_REL = Path("transitions") / "replay-track-compare.json"
_IMPL_TRACK_DIR = Path("transitions") / "replay-tracks" / "impl"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _read_json(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _safe_ref_path(ref_dir: Path, raw: object, label: str) -> tuple[Path | None, str | None]:
    if not isinstance(raw, str) or not raw:
        return None, f"{label} must be a nonempty relative path"
    if "\\" in raw:
        return None, f"{label} must be a POSIX relative path"
    rel = PurePosixPath(raw)
    if rel.is_absolute() or ".." in rel.parts or rel == PurePosixPath("."):
        return None, f"{label} escapes ref dir: {raw}"
    path = (ref_dir / rel.as_posix()).resolve()
    try:
        path.relative_to(ref_dir.resolve())
    except ValueError:
        return None, f"{label} escapes ref dir: {raw}"
    if not path.is_file():
        return None, f"{label} missing or not a regular file: {raw}"
    if path.stat().st_size <= 0:
        return None, f"{label} is empty: {raw}"
    return path, None


def _declared_tracks(ref_dir: Path) -> tuple[list[tuple[str, Path, Path]], list[str]]:
    regions_path = ref_dir / "regions.json"
    if not regions_path.is_file():
        return [], ["regions.json missing; replay-track-compare requires declared replay tracks"]
    try:
        payload = _read_json(regions_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"regions.json unreadable: {exc}"]

    tracks: list[tuple[str, Path, Path]] = []
    failures: list[str] = []
    saw_replay_keys = False

    def visit(node: object, path: str) -> None:
        nonlocal saw_replay_keys
        if isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, f"{path}[{index}]")
            return
        if not isinstance(node, dict):
            return

        artifacts = node.get("artifacts")
        if not isinstance(artifacts, dict):
            for key, value in node.items():
                if key in {"replayTrack", "replayTrackManifest"}:
                    saw_replay_keys = True
                if isinstance(value, list | dict):
                    visit(value, f"{path}.{key}" if path else str(key))
            return

        has_track = "replayTrack" in artifacts
        has_manifest = "replayTrackManifest" in artifacts
        if not has_track and not has_manifest:
            for key, value in node.items():
                if isinstance(value, list | dict):
                    visit(value, f"{path}.{key}" if path else str(key))
            return
        saw_replay_keys = True
        region_name = str(node.get("name") or node.get("id") or path or "region")
        if not has_track or not has_manifest:
            failures.append(f"{region_name}: replayTrack and replayTrackManifest must be paired")
            return
        track_path, track_error = _safe_ref_path(ref_dir, artifacts.get("replayTrack"), f"{region_name}.replayTrack")
        manifest_path, manifest_error = _safe_ref_path(
            ref_dir,
            artifacts.get("replayTrackManifest"),
            f"{region_name}.replayTrackManifest",
        )
        if track_error:
            failures.append(track_error)
        if manifest_error:
            failures.append(manifest_error)
        if track_path is not None and manifest_path is not None:
            tracks.append((region_name, track_path, manifest_path))

    visit(payload, "")
    if saw_replay_keys and not tracks:
        failures.append("zero usable replay tracks declared in regions.json")
    elif not saw_replay_keys:
        failures.append("zero usable replay tracks declared in regions.json")
    return tracks, failures


def _candidate_path(ref_dir: Path, region_name: str, track_path: Path) -> Path:
    base = track_path.name
    if base.endswith(".json"):
        base = base[:-5]
    token = _SAFE_NAME_RE.sub("-", base).strip("-") or _SAFE_NAME_RE.sub("-", region_name).strip("-") or "replay-track"
    return ref_dir / _IMPL_TRACK_DIR / f"{token}.json"


def _manifest_for_track(track_path: Path) -> Path:
    return track_path.with_name(f"{track_path.stem}.manifest.json")


def _capture_command(capture: Path, args: list[str]) -> list[str]:
    if capture.suffix == ".py":
        return [sys.executable, str(capture), *args]
    return [str(capture), *args]


def _int_metric(result: dict[str, object], key: str) -> int:
    value = result.get(key)
    return value if isinstance(value, int) else 0


def _failure_messages(result: dict[str, object]) -> list[str]:
    failures = result.get("failures")
    if not isinstance(failures, list):
        return []
    return [failure for failure in failures if isinstance(failure, str)]


def _capture_args(impl_url: str, reference: dict[str, object], out_path: Path) -> list[str]:
    trigger = reference["trigger"]
    node = reference["node"]
    baseline = reference["baseline"]
    assert isinstance(trigger, dict)
    assert isinstance(node, dict)
    assert isinstance(baseline, dict)

    selector = str(node["selector"])
    mode = str(trigger["type"])
    if mode == "scroll-action":
        start_px = str(trigger["fromScrollY"])
        end_px = str(trigger["toScrollY"])
    else:
        start_px = str(trigger["startPx"])
        end_px = str(trigger["endPx"])
    baseline_sha = str(baseline.get("trackSha256") or track_sha256(reference))
    args = [impl_url, selector, str(out_path), start_px, end_px, baseline_sha, "--mode", mode]
    ready_wait_ms = trigger.get("readyWaitMs")
    if ready_wait_ms is not None:
        args.extend(["--ready-wait-ms", str(ready_wait_ms)])
    transport = trigger.get("transport")
    if mode == "scroll-progress" and transport == "lenis-wheel":
        args.extend(["--transport", "lenis-wheel"])
    if mode == "scroll-action":
        args.extend(["--driver", str(trigger["driver"])])
        args.extend(["--denominator-ms", str(trigger["denominatorMs"])])
        clock = trigger.get("clock")
        if isinstance(clock, dict) and "anchorMs" in clock:
            args.extend(["--anchor-ms", str(clock["anchorMs"])])
    return args


def _compare_one(
    *,
    impl_url: str,
    ref_dir: Path,
    capture: Path,
    region_name: str,
    reference_path: Path,
    manifest_path: Path,
) -> dict[str, object]:
    try:
        manifest_errors = verify_recording_manifest(_REPO_ROOT, _read_json(manifest_path))
    except (OSError, json.JSONDecodeError) as exc:
        manifest_errors = [f"manifest unreadable: {exc}"]
    if manifest_errors:
        return {
            "region": region_name,
            "reference": str(reference_path.relative_to(ref_dir)),
            "status": "fail",
            "matchedPairs": 0,
            "totalPairs": 1,
            "score": 0.0,
            "failures": [f"manifest {error}" for error in manifest_errors],
        }

    try:
        reference = _read_json(reference_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "region": region_name,
            "reference": str(reference_path.relative_to(ref_dir)),
            "status": "fail",
            "matchedPairs": 0,
            "totalPairs": 1,
            "score": 0.0,
            "failures": [f"reference track unreadable: {exc}"],
        }
    if not isinstance(reference, dict):
        return {
            "region": region_name,
            "reference": str(reference_path.relative_to(ref_dir)),
            "status": "fail",
            "matchedPairs": 0,
            "totalPairs": 1,
            "score": 0.0,
            "failures": ["reference track must be an object"],
        }
    reference_hash = track_sha256(reference)
    baseline = reference.get("baseline")
    if not isinstance(baseline, dict) or baseline.get("trackSha256") != reference_hash:
        return {
            "region": region_name,
            "reference": str(reference_path.relative_to(ref_dir)),
            "status": "fail",
            "matchedPairs": 0,
            "totalPairs": 1,
            "score": 0.0,
            "failures": ["reference baseline.trackSha256 must match reference track hash"],
        }
    reference_errors = validate_track(reference)
    if reference_errors:
        return {
            "region": region_name,
            "reference": str(reference_path.relative_to(ref_dir)),
            "status": "fail",
            "matchedPairs": 0,
            "totalPairs": 1,
            "score": 0.0,
            "failures": [f"reference {error}" for error in reference_errors],
        }

    out_path = _candidate_path(ref_dir, region_name, reference_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = _capture_command(capture, _capture_args(impl_url, reference, out_path))
    try:
        proc = subprocess.run(command, cwd=_REPO_ROOT, capture_output=True, text=True, timeout=180)
    except (subprocess.TimeoutExpired, OSError) as error:
        # Escaping here would skip _write_json and leave the previous run's
        # passing report on disk for any consumer reading the artifact.
        return {
            "region": region_name,
            "reference": str(reference_path.relative_to(ref_dir)),
            "candidate": str(out_path.relative_to(ref_dir)),
            "status": "fail",
            "matchedPairs": 0,
            "totalPairs": 1,
            "score": 0.0,
            "failures": [f"capture did not complete: {error}"],
        }
    if proc.returncode != 0:
        return {
            "region": region_name,
            "reference": str(reference_path.relative_to(ref_dir)),
            "candidate": str(out_path.relative_to(ref_dir)),
            "status": "fail",
            "matchedPairs": 0,
            "totalPairs": 1,
            "score": 0.0,
            "failures": [f"capture failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()}"],
        }
    candidate_manifest = _manifest_for_track(out_path)
    if not candidate_manifest.is_file() or candidate_manifest.stat().st_size <= 0:
        return {
            "region": region_name,
            "reference": str(reference_path.relative_to(ref_dir)),
            "candidate": str(out_path.relative_to(ref_dir)),
            "status": "fail",
            "matchedPairs": 0,
            "totalPairs": 1,
            "score": 0.0,
            "failures": ["candidate manifest missing or empty"],
        }
    try:
        candidate_manifest_errors = verify_recording_manifest(_REPO_ROOT, _read_json(candidate_manifest))
    except (OSError, json.JSONDecodeError) as exc:
        candidate_manifest_errors = [f"manifest unreadable: {exc}"]
    if candidate_manifest_errors:
        return {
            "region": region_name,
            "reference": str(reference_path.relative_to(ref_dir)),
            "candidate": str(out_path.relative_to(ref_dir)),
            "candidateManifest": str(candidate_manifest.relative_to(ref_dir)),
            "status": "fail",
            "matchedPairs": 0,
            "totalPairs": 1,
            "score": 0.0,
            "failures": [f"candidate manifest {error}" for error in candidate_manifest_errors],
        }
    try:
        candidate = _read_json(out_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "region": region_name,
            "reference": str(reference_path.relative_to(ref_dir)),
            "candidate": str(out_path.relative_to(ref_dir)),
            "status": "fail",
            "matchedPairs": 0,
            "totalPairs": 1,
            "score": 0.0,
            "failures": [f"candidate track unreadable: {exc}"],
        }
    if isinstance(candidate, dict):
        candidate_baseline = candidate.get("baseline")
        if not isinstance(candidate_baseline, dict) or candidate_baseline.get("trackSha256") != reference_hash:
            return {
                "region": region_name,
                "reference": str(reference_path.relative_to(ref_dir)),
                "candidate": str(out_path.relative_to(ref_dir)),
                "candidateManifest": str(candidate_manifest.relative_to(ref_dir)),
                "status": "fail",
                "matchedPairs": 0,
                "totalPairs": 1,
                "score": 0.0,
                "failures": ["candidate baseline.trackSha256 must match reference track hash"],
            }
    result = compare_tracks(reference, candidate, minimum_score=1.0)
    result.update(
        {
            "region": region_name,
            "reference": str(reference_path.relative_to(ref_dir)),
            "candidate": str(out_path.relative_to(ref_dir)),
            "candidateManifest": str(candidate_manifest.relative_to(ref_dir)),
        }
    )
    return result


def run(impl_url: str, ref_dir: Path, capture: Path) -> dict[str, object]:
    ref_dir = ref_dir.resolve()
    tracks, failures = _declared_tracks(ref_dir)
    track_results: list[dict[str, object]] = []
    if not failures:
        for region_name, reference_path, manifest_path in tracks:
            track_results.append(
                _compare_one(
                    impl_url=impl_url,
                    ref_dir=ref_dir,
                    capture=capture,
                    region_name=region_name,
                    reference_path=reference_path,
                    manifest_path=manifest_path,
                )
            )

    matched_pairs = sum(_int_metric(result, "matchedPairs") for result in track_results)
    total_pairs = sum(_int_metric(result, "totalPairs") for result in track_results)
    all_failures = list(failures)
    for result in track_results:
        for failure in _failure_messages(result):
            all_failures.append(f"{result.get('region', '?')}: {failure}")
    if failures and total_pairs == 0:
        total_pairs = len(failures)
    status = "pass" if not all_failures else "fail"
    return {
        "schemaVersion": 1,
        "status": status,
        "matchedPairs": matched_pairs,
        "totalPairs": total_pairs,
        "failures": all_failures,
        "tracks": track_results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("impl_url")
    parser.add_argument("ref_dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    capture = Path(os.environ.get("UI_CLONE_REPLAY_TRACK_CAPTURE", str(_DEFAULT_CAPTURE)))
    ref_dir = args.ref_dir.resolve()
    if not capture.is_file():
        payload = {
            "schemaVersion": 1,
            "status": "fail",
            "matchedPairs": 0,
            "totalPairs": 1,
            "failures": [f"capture wrapper missing: {capture}"],
            "tracks": [],
        }
    else:
        payload = run(args.impl_url, ref_dir, capture.resolve())
    _write_json(ref_dir / _REPORT_REL, payload)
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
