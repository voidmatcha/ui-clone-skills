#!/usr/bin/env python3
"""Helper routines for verification-plan.sh.

Keep interpreter bodies out of Bash heredocs. This script is side-effect-light so
verification-plan.sh can call it during quick-tier plan synthesis without stdin
pipe delivery.
"""

import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

MOTION_PROPS: tuple[str, ...] = ("transform", "opacity", "scale", "clipPath")
PROPS: tuple[str, ...] = (*MOTION_PROPS, "top")
TOP_MOTION_TOLERANCE_PX = 1.0
CAROUSEL_RE: re.Pattern[str] = re.compile(
    r"slide|carousel|rotat|gallery|marquee|slider|embla|splide|keen|swiper|rail",
    re.IGNORECASE,
)
VECTOR_RE: re.Pattern[str] = re.compile(r"canvas|svg|rive|\.riv|lottie|bodymovin", re.IGNORECASE)
CSS_RULE_RE: re.Pattern[str] = re.compile(r"([^{}]+)\{([^{}]*)\}")
BOOLEAN_STATE_ATTR_RE: re.Pattern[str] = re.compile(r"^data-[a-z0-9_.:-]+$")
MOTION_DECL_RE: re.Pattern[str] = re.compile(
    r"(?:^|[;{])\s*(transform|translate|rotate|scale|opacity|clip-path)\s*:|"
    r"--(?:translate|rotate|scale|transform|opacity)",
    re.IGNORECASE,
)
TRANSITION_DECL_RE: re.Pattern[str] = re.compile(
    r"(?:^|[;{])\s*transition(?:-[a-z-]+)?\s*:[^;}]*"
    r"(all|transform|translate|rotate|scale|opacity|clip-path)",
    re.IGNORECASE,
)


def load(path: str) -> Any | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _walk_nodes(value: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(value, dict):
        nodes.append(value)
        for child in value.get("children", []) or []:
            nodes.extend(_walk_nodes(child))
    elif isinstance(value, list):
        for item in value:
            nodes.extend(_walk_nodes(item))
    return nodes


def _classes(node: dict[str, Any]) -> set[str]:
    value = node.get("class") or node.get("className")
    if isinstance(value, str):
        return {part for part in value.split() if part}
    return set()


def _boolean_state_attrs(node: dict[str, Any]) -> list[tuple[str, str]]:
    attrs: list[tuple[str, str]] = []
    for key, value in node.items():
        if not isinstance(key, str) or not BOOLEAN_STATE_ATTR_RE.match(key):
            continue
        text = str(value).strip().lower()
        if text in {"true", "false"}:
            attrs.append((key, text))
    return attrs


def _css_text(ref_dir: str) -> str:
    root = Path(ref_dir)
    parts: list[str] = []
    for subdir in ("ref-css", "css", "resources", "bundles"):
        directory = root / subdir
        if not directory.exists():
            continue
        for path in directory.rglob("*.css"):
            try:
                parts.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    return "\n".join(parts)


def _split_selectors(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _selector_compounds(selector: str) -> list[str]:
    return [part for part in re.split(r"\s+|[>+~]", selector) if part]


def _selector_subject(selector: str) -> str:
    compounds = _selector_compounds(selector)
    return compounds[-1] if compounds else ""


def _selector_matches_node(selector: str, node: dict[str, Any], attr: str, terminal: str) -> bool:
    attr_re = re.compile(
        r"\[" + re.escape(attr) + r"(?:\s*=\s*[\"']?" + re.escape(terminal) + r"[\"']?)?\]",
        re.IGNORECASE,
    )
    node_classes = _classes(node)
    node_tag = str(node.get("tag", "")).lower()
    node_id = str(node.get("id", ""))
    subject = _selector_subject(selector)
    if not subject or not attr_re.search(subject):
        return False
    class_tokens = set(re.findall(r"\.([A-Za-z0-9_-]+)", subject))
    if class_tokens and not class_tokens.issubset(node_classes):
        return False
    id_tokens = set(re.findall(r"#([A-Za-z0-9_-]+)", subject))
    if id_tokens and any(token != node_id for token in id_tokens):
        return False
    tag_match = re.match(r"^[A-Za-z][A-Za-z0-9_-]*", subject)
    if tag_match and node_tag and tag_match.group(0).lower() != node_tag:
        return False
    return True


def _selector_targets_node_base(selector: str, node: dict[str, Any]) -> bool:
    node_classes = _classes(node)
    node_tag = str(node.get("tag", "")).lower()
    node_id = str(node.get("id", ""))
    subject = _selector_subject(selector)
    if not subject:
        return False
    class_tokens = set(re.findall(r"\.([A-Za-z0-9_-]+)", subject))
    if class_tokens and not class_tokens.issubset(node_classes):
        return False
    id_tokens = set(re.findall(r"#([A-Za-z0-9_-]+)", subject))
    if id_tokens and any(token != node_id for token in id_tokens):
        return False
    tag_match = re.match(r"^[A-Za-z][A-Za-z0-9_-]*", subject)
    if tag_match and node_tag and tag_match.group(0).lower() != node_tag:
        return False
    attr_tokens = set(re.findall(r"\[\s*([A-Za-z_][A-Za-z0-9_.:-]*)", subject))
    if attr_tokens and not attr_tokens.issubset(node.keys()):
        return False
    return bool(class_tokens or id_tokens or tag_match or attr_tokens)


def boolean_css_reveal(ref_dir: str) -> bool:
    structure = load(str(Path(ref_dir) / "structure.json"))
    if structure is None:
        return False
    css = _css_text(ref_dir)
    if not css:
        return False
    rules = [
        (selectors, declarations)
        for selectors, declarations in CSS_RULE_RE.findall(css)
    ]
    if not rules:
        return False
    for node in _walk_nodes(structure):
        for attr, captured in _boolean_state_attrs(node):
            terminal = "true" if captured == "false" else "false"
            matching_terminal_motion = False
            matching_transition = False
            for selectors_raw, declarations in rules:
                selectors = _split_selectors(selectors_raw)
                if not selectors:
                    continue
                if any(_selector_matches_node(sel, node, attr, terminal) for sel in selectors):
                    matching_terminal_motion = matching_terminal_motion or bool(
                        MOTION_DECL_RE.search(declarations)
                    )
                    matching_transition = matching_transition or bool(
                        TRANSITION_DECL_RE.search(declarations)
                    )
                elif any(_selector_targets_node_base(sel, node) for sel in selectors):
                    matching_transition = matching_transition or bool(
                        TRANSITION_DECL_RE.search(declarations)
                    )
            if matching_terminal_motion and matching_transition:
                return True
    return False


def transition_coverage_scroll(tc_path: str) -> bool:
    data = load(tc_path)
    if not isinstance(data, dict):
        return False
    for element in data.get("animatedElements", []) or []:
        if not isinstance(element, dict):
            continue
        trigger = str(element.get("trigger", "")).lower()
        decoded = element.get("decoded") or {}
        position = str(decoded.get("position", "")).lower() if isinstance(decoded, dict) else ""
        if position == "sticky":
            changed = (
                decoded.get("changedProperties", [])
                if isinstance(decoded, dict)
                else []
            )
            changed_props = {
                str(prop).lower()
                for prop in changed
                if isinstance(prop, str)
            }
            motion_type = " ".join(
                str(value).lower()
                for value in (
                    element.get("type"),
                    decoded.get("type") if isinstance(decoded, dict) else None,
                )
                if value is not None
            )
            has_non_sticky_motion = bool(
                changed_props - {"position", "top"}
            ) or any(
                token in motion_type
                for token in ("scrub", "parallax", "transform", "opacity", "scale")
            )
            if not has_non_sticky_motion:
                continue
        if "scroll" in trigger:
            return True
    return False


def transition_spec_scroll_scrub(ts_path: str) -> bool:
    """Return whether a transition spec declares scroll-linked motion.

    Plain CSS sticky positioning is proved by transition-fires/header checks.
    It is not a scrub trajectory and must not dispatch whole-page AE/video
    trajectory comparisons.
    """
    data = load(ts_path)
    if not isinstance(data, dict):
        return False
    raw_entries = data.get("transitions") or data.get("entries") or []
    if not isinstance(raw_entries, list):
        return False
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        trigger = str(entry.get("trigger", "")).lower()
        if "scroll" not in trigger:
            continue
        animation = entry.get("animation")
        animation_type = str(
            animation.get("type", "")
            if isinstance(animation, dict)
            else entry.get("type", "")
        ).strip().lower()
        if animation_type in {"css-sticky", "sticky", "position-sticky"}:
            continue
        return True
    return False


def animations_detected_scroll(ad_path: str) -> bool:
    data = load(ad_path)
    return bool(data.get("scrollAnimations")) if isinstance(data, dict) else False


def animations_detected_reveal(ad_path: str) -> bool:
    data = load(ad_path)
    if not isinstance(data, dict):
        return False
    if data.get("textReveals") or data.get("reveals"):
        return True
    for scroll_animation in data.get("scrollAnimations", []) or []:
        if not isinstance(scroll_animation, dict):
            continue
        if "reveal" in str(scroll_animation.get("type", "")).lower():
            return True
    return False


def element_tracking_frames(et_path: str) -> list[Any] | None:
    data = load(et_path)
    if not isinstance(data, list) or len(data) < 2:
        return None
    return data


def element_tracking_scroll(et_path: str) -> bool:
    frames = element_tracking_frames(et_path)
    if not frames:
        return False
    seen: dict[Any, dict[str, set[Any]]] = {}
    for frame in frames:
        elements = frame.get("elements", []) if isinstance(frame, dict) else []
        for element in elements or []:
            if not isinstance(element, dict):
                continue
            selector = element.get("selector")
            if selector is None:
                continue
            bucket = seen.setdefault(
                selector,
                {
                    **{prop: set() for prop in MOTION_PROPS},
                    "viewportTop": set(),
                    "documentTop": set(),
                },
            )
            for prop in MOTION_PROPS:
                bucket[prop].add(json.dumps(element.get(prop), sort_keys=True))
            top = element.get("top")
            scroll_y = frame.get("scrollY") if isinstance(frame, dict) else None
            if (
                isinstance(top, int | float)
                and not isinstance(top, bool)
                and isinstance(scroll_y, int | float)
                and not isinstance(scroll_y, bool)
            ):
                bucket["viewportTop"].add(float(top))
                bucket["documentTop"].add(float(top + scroll_y))
    for props in seen.values():
        if any(len(props[prop]) >= 2 for prop in MOTION_PROPS):
            return True
        viewport_tops = props["viewportTop"]
        document_tops = props["documentTop"]
        if (
            viewport_tops
            and document_tops
            and max(viewport_tops) - min(viewport_tops) > TOP_MOTION_TOLERANCE_PX
            and max(document_tops) - min(document_tops) > TOP_MOTION_TOLERANCE_PX
        ):
            return True
    return False


def element_tracking_reveal(et_path: str) -> bool:
    frames = element_tracking_frames(et_path)
    if not frames:
        return False
    states: dict[Any, list[tuple[bool, str]]] = {}
    for frame in frames:
        elements = frame.get("elements", []) if isinstance(frame, dict) else []
        for element in elements or []:
            if not isinstance(element, dict):
                continue
            selector = element.get("selector")
            if selector is None:
                continue
            fingerprint = json.dumps(
                [element.get(prop) for prop in PROPS],
                sort_keys=True,
            )
            states.setdefault(selector, []).append((bool(element.get("inViewport")), fingerprint))
    for sequence in states.values():
        for index in range(1, len(sequence)):
            prev_in_viewport, prev_fingerprint = sequence[index - 1]
            cur_in_viewport, cur_fingerprint = sequence[index]
            if (not prev_in_viewport) and cur_in_viewport and prev_fingerprint != cur_fingerprint:
                return True
    return False


def animations_detected_carousel(ad_path: str) -> bool:
    data = load(ad_path)
    if not isinstance(data, dict):
        return False
    for timer in data.get("autoTimers", []) or []:
        if not isinstance(timer, dict):
            continue
        haystack = str(timer.get("type", "")) + " " + str(timer.get("selector", ""))
        if CAROUSEL_RE.search(haystack):
            return True
    return False


def animations_detected_vector(ad_path: str) -> bool:
    data = load(ad_path)
    if not isinstance(data, dict):
        return False
    for key in ("autoTimers", "scrollAnimations", "textReveals", "reveals"):
        for entry in data.get(key, []) or []:
            if isinstance(entry, dict) and VECTOR_RE.search(str(entry.get("selector", ""))):
                return True
    return False


def element_tracking_vector(et_path: str) -> bool:
    frames = element_tracking_frames(et_path)
    if not frames:
        return False
    seen: dict[Any, dict[str, set[str]]] = {}
    for frame in frames:
        elements = frame.get("elements", []) if isinstance(frame, dict) else []
        for element in elements or []:
            if not isinstance(element, dict):
                continue
            selector = element.get("selector")
            if selector is None or not VECTOR_RE.search(str(selector)):
                continue
            bucket = seen.setdefault(selector, {prop: set() for prop in PROPS})
            for prop in PROPS:
                bucket[prop].add(json.dumps(element.get(prop), sort_keys=True))
    return any(len(values) >= 2 for props in seen.values() for values in props.values())


def observed_motion(mode: str, tc_path: str, ad_path: str, et_path: str) -> str:
    values = (
        transition_coverage_scroll(tc_path)
        or animations_detected_scroll(ad_path)
        or element_tracking_scroll(et_path),
        animations_detected_reveal(ad_path) or element_tracking_reveal(et_path),
        animations_detected_carousel(ad_path),
        animations_detected_vector(ad_path) or element_tracking_vector(et_path),
    )
    if mode == "all":
        return " ".join("true" if value else "false" for value in values)
    index_by_mode = {"scroll": 0, "reveal": 1, "carousel": 2, "vector": 3}
    if mode not in index_by_mode:
        return "false"
    return "true" if values[index_by_mode[mode]] else "false"


def plan_generated_epoch(path: str) -> str:
    data = json.load(open(path, encoding="utf-8"))
    value = data.get("generatedAt")
    if not isinstance(value, str) or not value:
        raise ValueError("missing generatedAt")
    return str(int(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()))


def amend_plan(base_path: str, fresh_path: str, out_path: str, plan_derived_raw: str) -> str:
    plan_derived = set(plan_derived_raw.split())
    with open(base_path, encoding="utf-8") as fh:
        base = json.load(fh)
    with open(fresh_path, encoding="utf-8") as fh:
        fresh = json.load(fh)

    base_req = base.get("requiredChecks") or []
    base_def = base.get("deferredChecks") or []
    existing = {check.get("id") for check in base_req} | {check.get("id") for check in base_def}
    fresh_req = {check.get("id"): check for check in (fresh.get("requiredChecks") or [])}
    fresh_def = {check.get("id"): check for check in (fresh.get("deferredChecks") or [])}

    appended = []
    for check_id in sorted(plan_derived):
        if not check_id or check_id in existing:
            continue
        if check_id in fresh_req:
            base_req.append(fresh_req[check_id])
            appended.append(check_id)
        elif check_id in fresh_def:
            base_def.append(fresh_def[check_id])
            appended.append(check_id)

    base["requiredChecks"] = base_req
    base["deferredChecks"] = base_def
    base["amendedAt"] = datetime.datetime.now(datetime.UTC).isoformat()
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(base, fh, indent=2)
        fh.write("\n")
    return "amended plan-derived rows: " + (", ".join(appended) if appended else "none")


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "observed-motion" and len(argv) == 6:
        print(observed_motion(argv[2], argv[3], argv[4], argv[5]))
        return 0
    if command == "transition-spec-scroll-scrub" and len(argv) == 3:
        print("true" if transition_spec_scroll_scrub(argv[2]) else "false")
        return 0
    if command == "boolean-css-reveal" and len(argv) == 3:
        print("true" if boolean_css_reveal(argv[2]) else "false")
        return 0
    if command == "plan-generated-epoch" and len(argv) == 3:
        try:
            print(plan_generated_epoch(argv[2]))
            return 0
        except Exception:
            return 1
    if command == "amend-plan" and len(argv) == 6:
        print(amend_plan(argv[2], argv[3], argv[4], argv[5]))
        return 0
    print("usage: verification_plan_helpers.py <command> ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
