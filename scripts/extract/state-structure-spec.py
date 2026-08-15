#!/usr/bin/env python3
"""Build state-structure-spec.json from browser-observed state artifacts.

The capture scripts keep raw phase files under states/**. This post-pass
normalizes those files into one compact index for generators, gates, and
source-forensics subagents. It intentionally omits full HTML/outerHTML blobs.
"""

from __future__ import annotations

import hashlib
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

PRODUCER = "scripts/extract/state-structure-spec.py"
MAX_SIGNATURES = 80


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _rel(ref_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(ref_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:12]


class _SignatureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.signatures: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(self.signatures) >= MAX_SIGNATURES:
            return
        attr_map = {name: value or "" for name, value in attrs}
        ident = f"#{attr_map['id']}" if attr_map.get("id") else ""
        classes = ""
        if attr_map.get("class"):
            classes = "." + ".".join(attr_map["class"].split()[:3])
        state_bits = []
        for key in ("data-state", "data-section", "aria-expanded", "role"):
            if key in attr_map:
                state_bits.append(f"[{key}={attr_map[key]}]")
        self.signatures.append(f"{tag}{ident}{classes}{''.join(state_bits)}")


def _html_signatures(path: Path) -> set[str]:
    data = _load_json(path)
    if not isinstance(data, dict):
        return set()
    html = data.get("outerHTML") or data.get("html") or ""
    if not isinstance(html, str) or not html:
        return set()
    parser = _SignatureParser()
    try:
        parser.feed(html)
    except Exception:
        return set()
    return set(parser.signatures)


def _snapshot_file_for_splash(splash_dir: Path, entry: dict[str, Any]) -> Path | None:
    bookend = entry.get("bookend")
    if bookend == "0ms":
        return splash_dir / "0ms.json"
    if bookend in ("settled", "settled-same"):
        return splash_dir / "settled.json"
    ts = entry.get("ts_ms")
    if isinstance(ts, int):
        candidate = splash_dir / f"{ts}ms.json"
        return candidate if candidate.is_file() else None
    return None


def _dom_signature_delta(before_file: Path | None, after_file: Path | None) -> dict[str, Any]:
    before = _html_signatures(before_file) if before_file else set()
    after = _html_signatures(after_file) if after_file else set()
    added = sorted(after - before)[:20]
    removed = sorted(before - after)[:20]
    return {
        "addedSignatures": added,
        "removedSignatures": removed,
        "signatureCountBefore": len(before),
        "signatureCountAfter": len(after),
    }


def _build_splash_events(ref_dir: Path) -> list[dict[str, Any]]:
    splash_dir = ref_dir / "states" / "splash"
    summary_path = splash_dir / "summary.json"
    trajectory_path = splash_dir / "trajectory.json"
    contract_path = splash_dir / "contract.json"
    summary = _load_json(summary_path)
    trajectory = _load_json(trajectory_path)
    if not isinstance(summary, dict) or not isinstance(trajectory, list) or len(trajectory) < 2:
        return []
    splash_contract = _load_json(contract_path)
    if not isinstance(splash_contract, dict):
        splash_contract = {}
    if _splash_contract_is_authoritative_negative(splash_contract):
        return []

    first = trajectory[0] if isinstance(trajectory[0], dict) else {}
    last = trajectory[-1] if isinstance(trajectory[-1], dict) else {}
    before_file = _snapshot_file_for_splash(splash_dir, first)
    after_file = _snapshot_file_for_splash(splash_dir, last) or splash_dir / "settled.json"
    dom_delta = _dom_signature_delta(before_file, after_file)
    dom_changed = any((
        first.get("hash") != last.get("hash"),
        first.get("bodyClass") != last.get("bodyClass"),
        first.get("htmlClass") != last.get("htmlClass"),
        first.get("domLength") != last.get("domLength"),
        bool(dom_delta["addedSignatures"] or dom_delta["removedSignatures"]),
    ))
    artifacts = [summary_path, trajectory_path]
    if contract_path.is_file():
        artifacts.append(contract_path)
    for file in (before_file, after_file):
        if file and file.is_file():
            artifacts.append(file)
    return [{
        "id": "splash:page-load",
        "phase": "splash",
        "trigger": "page-load",
        "eventDriver": "agent-browser.navigation",
        "status": "observed",
        "fromMs": first.get("ts_ms"),
        "toMs": last.get("ts_ms"),
        "bodyClassBefore": first.get("bodyClass", ""),
        "bodyClassAfter": last.get("bodyClass", ""),
        "htmlClassBefore": first.get("htmlClass", ""),
        "htmlClassAfter": last.get("htmlClass", ""),
        "domLengthBefore": first.get("domLength"),
        "domLengthAfter": last.get("domLength"),
        "domMutation": {"changed": bool(dom_changed), **dom_delta},
        "splashContract": splash_contract,
        "artifacts": [_rel(ref_dir, path) for path in artifacts],
    }]


def _splash_contract_is_authoritative_negative(contract: dict[str, Any]) -> bool:
    if contract.get("schemaVersion") is None:
        return False
    if contract.get("detected") is not False:
        return False
    if contract.get("captureMode") == "reuse-session":
        return False
    overlay = contract.get("overlay")
    capture = contract.get("capture")
    has_overlay_metadata = isinstance(overlay, dict) and "everVisible" in overlay
    has_capture_metadata = isinstance(capture, dict)
    if not has_overlay_metadata and not has_capture_metadata:
        return True
    if isinstance(capture, dict) and capture.get("authoritativeNegative") is False:
        return False
    if isinstance(capture, dict) and capture.get("authoritativeNegative") is True:
        return True
    ever_visible = bool(overlay.get("everVisible")) if isinstance(overlay, dict) else False
    state_count = capture.get("stateCount") if isinstance(capture, dict) else None
    timed_out = bool(capture.get("timedOut")) if isinstance(capture, dict) else False
    return not ever_visible and state_count == 1 and not timed_out


def _build_scroll_events(ref_dir: Path) -> list[dict[str, Any]]:
    scroll_dir = ref_dir / "states" / "scroll"
    summary_path = scroll_dir / "summary.json"
    trajectory_path = scroll_dir / "trajectory.json"
    summary = _load_json(summary_path)
    trajectory = _load_json(trajectory_path)
    if not isinstance(summary, dict) or not isinstance(trajectory, list) or len(trajectory) < 2:
        return []
    events: list[dict[str, Any]] = []
    for index, (before, after) in enumerate(zip(trajectory, trajectory[1:])):
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        from_pct = before.get("pct")
        to_pct = after.get("pct")
        before_file = scroll_dir / f"{from_pct}pct.json"
        after_file = scroll_dir / f"{to_pct}pct.json"
        dom_delta = _dom_signature_delta(
            before_file if before_file.is_file() else None,
            after_file if after_file.is_file() else None,
        )
        visible_before = before.get("visibleSections", [])
        visible_after = after.get("visibleSections", [])
        changed = bool(
            before.get("scrollY") != after.get("scrollY")
            or visible_before != visible_after
            or dom_delta["addedSignatures"]
            or dom_delta["removedSignatures"]
        )
        artifacts = [summary_path, trajectory_path]
        for file in (before_file, after_file):
            if file.is_file():
                artifacts.append(file)
        events.append({
            "id": f"scroll:{from_pct}->{to_pct}",
            "phase": "scroll",
            "trigger": "scroll",
            "eventDriver": "agent-browser.eval.scrollTo",
            "status": "observed",
            "index": index,
            "fromPct": from_pct,
            "toPct": to_pct,
            "scrollYBefore": before.get("scrollY"),
            "scrollYAfter": after.get("scrollY"),
            "scrollEngine": summary.get("scrollEngine", "native"),
            "visibleSectionsBefore": visible_before,
            "visibleSectionsAfter": visible_after,
            "domMutation": {"changed": changed, **dom_delta},
            "artifacts": [_rel(ref_dir, path) for path in artifacts],
        })
    return events


def _build_hover_events(ref_dir: Path) -> list[dict[str, Any]]:
    hover_dir = ref_dir / "states" / "hover"
    manifest_path = hover_dir / "manifest.json"
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        return []
    entries = manifest.get("entries") or []
    if not isinstance(entries, list):
        return []
    events: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        file_name = entry.get("file")
        snap_path = hover_dir / file_name if isinstance(file_name, str) else None
        snap = _load_json(snap_path) if snap_path else None
        if not isinstance(snap, dict):
            snap = {}
        css_props = snap.get("cssProperties") or {}
        js_changes = snap.get("jsChanges") or []
        dom_changes = snap.get("domChanges") or []
        signal_kinds: list[str] = []
        if css_props:
            signal_kinds.append("css")
        if js_changes:
            signal_kinds.append("js")
        if dom_changes:
            signal_kinds.append("dom")
        artifacts = [manifest_path]
        if snap_path and snap_path.is_file():
            artifacts.append(snap_path)
        event_id = entry.get("id") or _short_hash(str(entry.get("selector", "")))
        events.append({
            "id": f"hover:{event_id}",
            "phase": "hover",
            "trigger": "hover",
            "eventDriver": snap.get("eventDriver", "agent-browser.hover-or-eval"),
            "status": "observed",
            "activation": snap.get("activation") or entry.get("activation") or entry.get("selector"),
            "affected": snap.get("affected") or entry.get("selector"),
            "kind": snap.get("kind") or entry.get("kind"),
            "signalKinds": signal_kinds,
            "cssPropertyCount": len(css_props) if isinstance(css_props, dict) else 0,
            "jsChangeCount": len(js_changes) if isinstance(js_changes, list) else 0,
            "domChangeCount": len(dom_changes) if isinstance(dom_changes, list) else 0,
            "domMutation": {"changed": bool(dom_changes)},
            "artifacts": [_rel(ref_dir, path) for path in artifacts],
        })
    return events


def _build_click_events(ref_dir: Path) -> list[dict[str, Any]]:
    click_dir = ref_dir / "states" / "click"
    manifest_path = click_dir / "manifest.json"
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        return []
    entries = manifest.get("entries") or []
    if not isinstance(entries, list):
        return []
    events: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        file_name = entry.get("file")
        snap_path = click_dir / file_name if isinstance(file_name, str) else None
        snap = _load_json(snap_path) if snap_path else None
        if not isinstance(snap, dict):
            snap = {}
        dom_mutation = snap.get("domMutation")
        if not isinstance(dom_mutation, dict):
            dom_mutation = {"changed": False}
        artifacts = [manifest_path]
        if snap_path and snap_path.is_file():
            artifacts.append(snap_path)
        event_id = entry.get("id") or snap.get("id") or _short_hash(str(entry.get("selector", "")))
        events.append({
            "id": f"click:{event_id}",
            "phase": "click",
            "trigger": "click",
            "eventDriver": snap.get("eventDriver", "agent-browser.click"),
            "status": "observed" if not snap.get("declaredOnly") else "declared",
            "selector": snap.get("selector") or entry.get("selector"),
            "triggerType": snap.get("triggerType") or entry.get("triggerType"),
            "navigationType": snap.get("navigationType") or entry.get("navigationType", "unknown"),
            "navigationOnly": bool(snap.get("navigationOnly", entry.get("navigationOnly", False))),
            "declaredOnly": bool(snap.get("declaredOnly", False)),
            "guard": snap.get("guard") if isinstance(snap.get("guard"), dict) else {},
            "bodyClassBefore": snap.get("bodyClassBefore", ""),
            "bodyClassAfter": snap.get("bodyClassAfter", ""),
            "htmlClassBefore": snap.get("htmlClassBefore", ""),
            "htmlClassAfter": snap.get("htmlClassAfter", ""),
            "domMutation": dom_mutation,
            "artifacts": [_rel(ref_dir, path) for path in artifacts],
        })
    return events


def build_spec(ref_dir: Path) -> dict[str, Any]:
    events = (
        _build_splash_events(ref_dir)
        + _build_scroll_events(ref_dir)
        + _build_hover_events(ref_dir)
        + _build_click_events(ref_dir)
    )
    phases: dict[str, dict[str, Any]] = {}
    for phase in ("splash", "scroll", "hover", "click"):
        phase_events = [event for event in events if event["phase"] == phase]
        phases[phase] = {
            "eventCount": len(phase_events),
            "present": bool(phase_events),
            "artifacts": sorted({
                artifact
                for event in phase_events
                for artifact in event.get("artifacts", [])
            }),
        }
    return {
        "schemaVersion": 1,
        "producer": PRODUCER,
        "contract": "derived-from-agent-browser-state-artifacts",
        "events": events,
        "phases": phases,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: state-structure-spec.py <ref_dir>", file=sys.stderr)
        return 1
    ref_dir = Path(argv[1])
    if not ref_dir.is_dir():
        print(f"state-structure-spec: ref_dir not found: {ref_dir}", file=sys.stderr)
        return 1
    spec = build_spec(ref_dir)
    out = ref_dir / "state-structure-spec.json"
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"state-structure-spec: wrote {len(spec['events'])} event(s) to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
