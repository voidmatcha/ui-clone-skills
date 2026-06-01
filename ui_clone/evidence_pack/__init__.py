from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

STYLE_ALLOWLIST = (
    "display",
    "position",
    "inset",
    "top",
    "right",
    "bottom",
    "left",
    "width",
    "height",
    "minWidth",
    "minHeight",
    "maxWidth",
    "maxHeight",
    "margin",
    "padding",
    "boxSizing",
    "overflow",
    "zIndex",
    "flex",
    "flexDirection",
    "alignItems",
    "justifyContent",
    "gap",
    "gridTemplateColumns",
    "gridTemplateRows",
    "gridColumn",
    "gridRow",
    "fontFamily",
    "fontSize",
    "fontWeight",
    "lineHeight",
    "letterSpacing",
    "textAlign",
    "color",
    "background",
    "backgroundColor",
    "backgroundImage",
    "border",
    "borderRadius",
    "boxShadow",
    "opacity",
    "transform",
    "transformOrigin",
    "filter",
    "mixBlendMode",
    "transitionProperty",
    "transitionDuration",
    "transitionTimingFunction",
    "transitionDelay",
    "animationName",
    "animationDuration",
    "animationTimingFunction",
    "animationDelay",
    "animationIterationCount",
    "animationFillMode",
)
STYLE_VALUE_MAX_CHARS = 160
ATTRIBUTE_VALUE_MAX_CHARS = 180

BUNDLE_ARTIFACTS = (
    ("bundle-map.json", "bundle-map"),
    ("external-sdks.json", "external-sdks"),
    ("scroll-engine.json", "scroll-engine"),
    ("transition-spec.json", "transition-spec"),
    ("animation-runtime-dump.json", "animation-runtime"),
    ("animations-detected.json", "animations-detected"),
    ("extracted.json", "extracted"),
)

NORTH_STAR = """# North Star

Copy the reference UI as closely as possible.

- Do not optimize for generic prettiness.
- Do not invent replacement layouts or placeholder assets.
- Do not simplify motion or change trigger type.
- Latest is not best. Prefer the best verified snapshot when the latest attempt regresses.
- Use structured evidence first; open raw DOM, large JSON, screenshots, or videos only for a targeted drill-down.
"""


def load_pack(path: str | Path) -> dict[str, Any]:
    pack_path = Path(path)
    if pack_path.is_dir():
        return build_pack_from_ref_dir(pack_path)
    if not pack_path.is_file():
        raise FileNotFoundError(f"evidence pack not found: {pack_path}")
    data = json.loads(pack_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("evidence pack must be a JSON object")
    return data


def build_pack_from_ref_dir(ref_dir: str | Path) -> dict[str, Any]:
    root = Path(ref_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"ref dir not found: {root}")

    annotations: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    def rel(path: Path) -> str:
        return str(path.relative_to(root))

    def add_artifact(path: Path, kind: str) -> str:
        artifact_id = rel(path).replace("/", "-").replace(".", "-")
        artifacts.append({"id": artifact_id, "kind": kind, "path": rel(path)})
        return artifact_id

    def read_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    splash = root / "states" / "splash" / "summary.json"
    if splash.is_file():
        artifact_id = add_artifact(splash, "state-summary")
        data = read_json(splash)
        annotations.append(
            {
                "id": "state-splash",
                "selector": "(splash timeline)",
                "note": _summary_note(data),
                "timeline": [{"phase": "idle", "changed": True}],
                "artifacts": [artifact_id],
            }
        )

    hover = root / "states" / "hover" / "summary.json"
    if hover.is_file():
        artifact_id = add_artifact(hover, "state-summary")
        data = read_json(hover)
        annotations.append(
            {
                "id": "state-hover",
                "selector": "(hover candidates)",
                "note": _summary_note(data),
                "timeline": [{"phase": "hover", "changed": True}],
                "artifacts": [artifact_id],
            }
        )

    scroll = root / "states" / "scroll" / "summary.json"
    if scroll.is_file():
        artifact_id = add_artifact(scroll, "state-summary")
        data = read_json(scroll)
        annotations.append(
            {
                "id": "state-scroll",
                "selector": "(scroll states)",
                "note": _summary_note(data),
                "timeline": [{"phase": "scroll", "changed": True}],
                "artifacts": [artifact_id],
            }
        )

    element_paths = sorted(root.glob("element-*.json"))
    for element_path in element_paths:
        artifact_id = add_artifact(element_path, "element-evidence")
        data = read_json(element_path)
        annotation = data.get("annotation") if isinstance(data.get("annotation"), dict) else {}
        if not annotation:
            continue
        copied = {
            key: annotation[key]
            for key in (
                "selector",
                "selectorCandidates",
                "note",
                "bbox",
                "computedStyle",
                "timeline",
                "component",
            )
            if key in annotation
        }
        copied["id"] = annotation.get("id") if isinstance(annotation.get("id"), str) else element_path.stem
        if copied["id"] == "element-probe":
            copied["id"] = element_path.stem
        existing_artifacts = annotation.get("artifacts", [])
        copied["artifacts"] = [
            item for item in existing_artifacts if isinstance(item, str)
        ] + [artifact_id]
        annotations.append(copied)

    verification = root / "verification-plan.json"
    if verification.is_file():
        artifact_id = add_artifact(verification, "verification-plan")
        data = read_json(verification)
        signals = data.get("signals", {}) if isinstance(data.get("signals"), dict) else {}
        active = ", ".join(key for key, value in sorted(signals.items()) if value) or "no active signals"
        annotations.append(
            {
                "id": "verification-plan",
                "selector": "(site signals)",
                "note": f"Verification signals: {active}",
                "timeline": [],
                "artifacts": [artifact_id],
            }
        )

    bundle_artifact_ids: list[str] = []
    bundle_paths: list[str] = []
    for filename, kind in BUNDLE_ARTIFACTS:
        path = root / filename
        if path.is_file():
            bundle_artifact_ids.append(add_artifact(path, kind))
            bundle_paths.append(filename)
    if bundle_artifact_ids:
        annotations.append(
            {
                "id": "bundle-analysis",
                "selector": "(bundle/runtime analysis)",
                "note": (
                    "Bundle analysis is mandatory for motion fidelity. "
                    "Use these artifacts for library, duration, easing, and trigger evidence: "
                    + ", ".join(bundle_paths)
                ),
                "timeline": [],
                "artifacts": bundle_artifact_ids,
            }
        )

    sections_result = root / "sections" / "result.txt"
    if sections_result.is_file():
        add_artifact(sections_result, "section-result")

    return {
        "schemaVersion": 1,
        "session": {
            "id": root.name,
            "url": _read_optional_text(root / "reference-url.txt"),
            "refDir": str(root),
        },
        "annotations": annotations,
        "artifacts": artifacts,
    }


def _summary_note(data: dict[str, Any]) -> str:
    if not data:
        return "Summary artifact present; details available by path."
    parts: list[str] = []
    for key in (
        "status",
        "checked",
        "changed",
        "durationMs",
        "candidatesFound",
        "candidatesProcessed",
        "timedOut",
        "reason",
    ):
        if key in data:
            parts.append(f"{key}={data[key]}")
    return ", ".join(parts) if parts else "Summary artifact present; details available by path."


def _read_optional_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _brief_value(value: Any, max_chars: int = STYLE_VALUE_MAX_CHARS) -> str:
    text = str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def slice_computed_style(style: dict[str, Any]) -> dict[str, str]:
    return {_key: _brief_value(style[_key]) for _key in STYLE_ALLOWLIST if _key in style}


def classify_trigger(annotation: dict[str, Any]) -> str:
    timeline = annotation.get("timeline")
    if not isinstance(timeline, list):
        return "unknown"

    changed_phases: list[str] = []
    for entry in timeline:
        if isinstance(entry, dict) and entry.get("changed") is True:
            phase = entry.get("phase")
            if isinstance(phase, str):
                changed_phases.append(phase)

    for phase in ("hover", "scroll", "click", "focus", "pointer", "mousemove"):
        if phase in changed_phases:
            return "pointer-follow" if phase in {"pointer", "mousemove"} else phase
    if "idle" in changed_phases:
        return "initial-auto"
    return "unknown"


def _session(pack: dict[str, Any]) -> dict[str, Any]:
    session = pack.get("session", {})
    return session if isinstance(session, dict) else {}


def _annotations(pack: dict[str, Any]) -> list[dict[str, Any]]:
    annotations = pack.get("annotations", [])
    if not isinstance(annotations, list):
        return []
    return [item for item in annotations if isinstance(item, dict)]


def _artifact_paths(pack: dict[str, Any], annotation: dict[str, Any]) -> list[str]:
    artifact_map: dict[str, str] = {}
    artifacts = pack.get("artifacts", [])
    if isinstance(artifacts, list):
        for item in artifacts:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                path = item.get("path")
                if isinstance(path, str):
                    artifact_map[item["id"]] = path

    paths: list[str] = []
    for artifact in annotation.get("artifacts", []):
        if isinstance(artifact, str):
            paths.append(artifact_map.get(artifact, artifact))
    return paths


def _bbox_text(annotation: dict[str, Any]) -> str:
    bbox = annotation.get("bbox")
    if not isinstance(bbox, dict):
        return "bbox: unknown"
    x = bbox.get("x", "?")
    y = bbox.get("y", "?")
    width = bbox.get("width", "?")
    height = bbox.get("height", "?")
    return f"bbox: x={x} y={y} w={width} h={height}"


def summarize_annotation(pack: dict[str, Any], annotation: dict[str, Any]) -> dict[str, Any]:
    style = annotation.get("computedStyle", {})
    if not isinstance(style, dict):
        style = {}
    selector_candidates = annotation.get("selectorCandidates", [])
    if not isinstance(selector_candidates, list):
        selector_candidates = []
    return {
        "id": annotation.get("id", "unknown"),
        "selector": annotation.get("selector", "unknown"),
        "selectorCandidates": [
            _brief_value(item, ATTRIBUTE_VALUE_MAX_CHARS)
            for item in selector_candidates
            if isinstance(item, str)
        ][:8],
        "component": annotation.get("component"),
        "note": annotation.get("note", ""),
        "bbox": annotation.get("bbox", {}),
        "trigger": classify_trigger(annotation),
        "style": slice_computed_style(style),
        "artifacts": _artifact_paths(pack, annotation),
    }


def trigger_counts(pack: dict[str, Any]) -> dict[str, int]:
    counts = Counter(classify_trigger(annotation) for annotation in _annotations(pack))
    return dict(sorted(counts.items()))


def build_reference_brief(pack: dict[str, Any], *, max_chars: int = 4000) -> str:
    session = _session(pack)
    viewport = session.get("viewport", {})
    lines = [
        "# Reference Brief",
        "",
        f"- Target URL: {session.get('url', '(unknown)')}",
        f"- Viewport: {viewport if isinstance(viewport, dict) else '(unknown)'}",
        f"- Annotations: {len(_annotations(pack))}",
        f"- Trigger counts: {trigger_counts(pack)}",
        "",
        "## Annotated Elements",
    ]
    for annotation in _annotations(pack):
        summary = summarize_annotation(pack, annotation)
        lines.extend(
            [
                "",
                f"### {summary['id']}",
                f"- Selector: `{summary['selector']}`",
                f"- Trigger: {summary['trigger']}",
                f"- {_bbox_text(annotation)}",
            ]
        )
        if summary.get("component"):
            lines.append(f"- Component: {summary['component']}")
        if summary.get("note"):
            lines.append(f"- Note: {summary['note']}")
        if summary["selectorCandidates"]:
            lines.append("- Selector candidates: " + ", ".join(summary["selectorCandidates"]))
        if summary["style"]:
            style_text = ", ".join(f"{key}={value}" for key, value in summary["style"].items())
            lines.append(f"- Style slice: {style_text}")
        if summary["artifacts"]:
            lines.append("- Artifacts: " + ", ".join(summary["artifacts"]))
    return _truncate("\n".join(lines).rstrip() + "\n", max_chars)


def build_worker_brief(pack: dict[str, Any], *, max_chars: int = 3000) -> str:
    session = _session(pack)
    lines = [
        "# Worker Brief",
        "",
        "Goal: Copy the reference UI as closely as possible, including transitions.",
        "",
        f"- Target URL: {session.get('url', '(unknown)')}",
        f"- Annotation count: {len(_annotations(pack))}",
        f"- Trigger counts: {trigger_counts(pack)}",
        "",
        "Rules:",
        "- Use this compact evidence first; do not load raw DOM or full artifact JSON by default.",
        "- Evidence-pack briefs do not replace extraction gates; bundle analysis is mandatory for motion values.",
        "- Preserve real selectors, text, assets, geometry, and motion trigger type.",
        "- If latest implementation regresses, return to the best verified snapshot.",
        "",
        "Focus elements:",
    ]
    for annotation in _annotations(pack):
        summary = summarize_annotation(pack, annotation)
        lines.append(
            f"- `{summary['selector']}` ({summary['id']}): trigger: {summary['trigger']}; "
            f"{_bbox_text(annotation)}; note: {summary['note'] or '(none)'}"
        )
        if summary["artifacts"]:
            lines.append(f"  artifacts: {', '.join(summary['artifacts'])}")
        if summary["selectorCandidates"]:
            lines.append(f"  selector candidates: {', '.join(summary['selectorCandidates'])}")
        if summary["style"]:
            style_text = ", ".join(f"{key}={value}" for key, value in summary["style"].items())
            lines.append(f"  style: {style_text}")
    return _truncate("\n".join(lines).rstrip() + "\n", max_chars)


def build_attempt_feedback(pack: dict[str, Any], *, max_chars: int = 2000) -> str:
    lines = [
        "# Attempt Feedback",
        "",
        "No implementation attempt has been summarized in this pack yet.",
        "",
        f"- Annotation count: {len(_annotations(pack))}",
        f"- Trigger counts: {trigger_counts(pack)}",
        "- Next worker should preserve trigger types and inspect only targeted artifacts.",
    ]
    return _truncate("\n".join(lines).rstrip() + "\n", max_chars)


def current_state(pack: dict[str, Any]) -> dict[str, Any]:
    session = _session(pack)
    return {
        "schemaVersion": 1,
        "targetUrl": session.get("url"),
        "viewport": session.get("viewport", {}),
        "annotationCount": len(_annotations(pack)),
        "triggerCounts": trigger_counts(pack),
        "bestSnapshot": None,
        "latestSnapshot": None,
    }


def materialize_skill_briefs(
    pack_path: str | Path,
    out_dir: str | Path,
    *,
    max_chars: int = 3000,
) -> list[Path]:
    pack = load_pack(pack_path)
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)

    files = {
        "ATTEMPT_FEEDBACK.md": build_attempt_feedback(pack, max_chars=max_chars),
        "CURRENT_STATE.json": json.dumps(current_state(pack), indent=2, sort_keys=True) + "\n",
        "NORTH_STAR.md": NORTH_STAR,
        "REFERENCE_BRIEF.md": build_reference_brief(pack, max_chars=max_chars),
        "WORKER_BRIEF.md": build_worker_brief(pack, max_chars=max_chars),
    }
    written: list[Path] = []
    for name in sorted(files):
        path = output / name
        path.write_text(files[name], encoding="utf-8")
        written.append(path)
    return written


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    suffix = "\n\n[truncated: use targeted artifact paths for drill-down]\n"
    return text[: max(0, max_chars - len(suffix))].rstrip() + suffix
