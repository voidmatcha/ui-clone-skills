#!/usr/bin/env python3
"""Compare captured transition elements and emit transition artifacts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

GENERIC_SELECTORS = {
    ".nclick-target",
    ".swiper-slide",
    ".swiper-wrapper",
    "a",
    "button",
    "img",
    ".active",
    ".is-active",
    ".is-show",
    ".show",
    ".on",
}


def _is_generic_selector(selector: str) -> bool:
    if not selector or selector in GENERIC_SELECTORS:
        return True
    return bool(re.fullmatch(r"\.h_\d+", selector))


def _match_key(element: dict[str, Any]) -> dict[str, str]:
    match_key = element.get("matchKey") or {}
    text = (match_key.get("text") or element.get("text") or "").strip()
    return {
        "tag": (match_key.get("tag") or element.get("tag") or "").strip(),
        "text": text,
        "href": (match_key.get("href") or "").strip(),
        "role": (match_key.get("role") or "").strip(),
        "aria": (match_key.get("aria") or "").strip(),
    }


def _size_close(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_rect = first.get("rect") or {}
    second_rect = second.get("rect") or {}
    try:
        first_width = float(first_rect.get("width") or 0)
        first_height = float(first_rect.get("height") or 0)
        second_width = float(second_rect.get("width") or 0)
        second_height = float(second_rect.get("height") or 0)
    except (TypeError, ValueError):
        return False
    if min(first_width, first_height, second_width, second_height) <= 0:
        return False
    return abs(first_width - second_width) <= max(8, first_width * 0.15) and abs(
        first_height - second_height
    ) <= max(8, first_height * 0.15)


def _slot_close(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_rect = first.get("rect") or {}
    second_rect = second.get("rect") or {}
    try:
        first_left = float(first_rect.get("left"))
        first_top = float(first_rect.get("top"))
        second_left = float(second_rect.get("left"))
        second_top = float(second_rect.get("top"))
    except (TypeError, ValueError):
        return False
    return abs(first_left - second_left) <= 8 and abs(first_top - second_top) <= 8


def _dynamic_carousel_targets(output_dir: Path) -> tuple[str, ...]:
    spec_path = output_dir / "transition-spec.json"
    if not spec_path.is_file():
        return ()
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    transitions = spec.get("transitions") if isinstance(spec, dict) else None
    if not isinstance(transitions, list):
        return ()

    targets: list[str] = []
    for transition in transitions:
        if not isinstance(transition, dict) or transition.get("dynamic") is not True:
            continue
        animation = transition.get("animation")
        animation_type = (
            str(animation.get("type", "")).lower() if isinstance(animation, dict) else ""
        )
        if not any(marker in animation_type for marker in ("carousel", "slideshow")):
            continue
        target = transition.get("target")
        if isinstance(target, str):
            targets.extend(part.strip() for part in target.split(",") if part.strip())
    return tuple(dict.fromkeys(targets))


def _inside_dynamic_carousel(selector: str, targets: tuple[str, ...]) -> bool:
    return any(target in selector for target in targets)


def _skip_transition_noise(element: dict[str, Any]) -> bool:
    key = _match_key(element)
    selector = element.get("selector") or ""
    if key["tag"] == "img" and not key["text"]:
        return True
    return (
        key["tag"] == "a"
        and any(
            marker in selector
            for marker in (
                "masonry-list",
                "swiper-wrapper",
                "carousel",
                "gallery",
                "news-list",
                "card-list",
                "tile-list",
                "slide",
            )
        )
        and not any(
            marker in selector
            for marker in (
                "btn",
                "button",
                "cta",
                "nav",
                "tab",
                "logo",
                "social",
            )
        )
    )


def find_impl_match(
    ref_element: dict[str, Any],
    impl_elements: list[dict[str, Any]],
    *,
    dynamic_carousel: bool = False,
) -> dict[str, Any] | None:
    ref_selector = ref_element["selector"]
    ref_key = _match_key(ref_element)
    generic_ref = _is_generic_selector(ref_selector)

    if dynamic_carousel:
        same_slot = [
            impl_element
            for impl_element in impl_elements
            if _match_key(impl_element)["tag"] == ref_key["tag"]
            and _size_close(ref_element, impl_element)
            and _slot_close(ref_element, impl_element)
        ]
        if len(same_slot) == 1:
            return same_slot[0]
        if not same_slot:
            # Phase-dependent aria labels such as "Previous slide" and
            # "Back to final slide" describe the same control slot. Never let
            # an exact label from a different carousel steal this match.
            return None

    if not generic_ref:
        for impl_element in impl_elements:
            if impl_element["selector"] == ref_selector:
                return impl_element

    def semantic_score(impl_element: dict[str, Any]) -> int:
        impl_key = _match_key(impl_element)
        score = 0
        if impl_key["tag"] and ref_key["tag"] and impl_key["tag"] == ref_key["tag"]:
            score += 10
        if ref_key["text"] and impl_key["text"]:
            if ref_key["text"] == impl_key["text"]:
                score += 80
            elif len(ref_key["text"]) >= 6 and (
                ref_key["text"] in impl_key["text"] or impl_key["text"] in ref_key["text"]
            ):
                score += 55
        if ref_key["href"] and impl_key["href"] == ref_key["href"]:
            score += 55
        if ref_key["aria"] and impl_key["aria"] == ref_key["aria"]:
            score += 45
        if _size_close(ref_element, impl_element):
            score += 8
        if impl_element.get("selector") == ref_selector:
            score += 15 if generic_ref else 100
        return score

    scored = sorted(
        ((semantic_score(element), element) for element in impl_elements),
        key=lambda item: item[0],
        reverse=True,
    )
    if scored and scored[0][0] >= (40 if generic_ref else 30):
        return scored[0][1]

    ref_class = ref_selector.replace(".", "").replace("#", "")
    for impl_element in impl_elements:
        impl_class = impl_element["selector"].replace(".", "").replace("#", "")
        if (
            ref_class
            and impl_class
            and not generic_ref
            and (ref_class in impl_class or impl_class in ref_class)
        ):
            return impl_element

    ref_text = (ref_element.get("text") or "").strip()
    if ref_text and len(ref_text) >= 6:
        for impl_element in impl_elements:
            impl_text = (impl_element.get("text") or "").strip()
            if impl_text and (
                ref_text == impl_text or ref_text in impl_text or impl_text in ref_text
            ):
                return impl_element

    ref_root = re.sub(r"__[A-Za-z0-9]{6,}$", "", ref_class or "")
    if ref_root and ref_root != ref_class:
        for impl_element in impl_elements:
            impl_class = impl_element["selector"].replace(".", "").replace("#", "")
            impl_root = re.sub(r"__[A-Za-z0-9]{6,}$", "", impl_class or "")
            if impl_root and (
                ref_root == impl_root or ref_root in impl_root or impl_root in ref_root
            ):
                return impl_element
    return None


def _regroup_paren(tokens: list[str]) -> list[str]:
    grouped: list[str] = []
    buffer = ""
    depth = 0
    for token in tokens:
        buffer = token if not buffer else f"{buffer}, {token}"
        depth += token.count("(") - token.count(")")
        if depth <= 0:
            grouped.append(buffer)
            buffer = ""
            depth = 0
    if buffer:
        grouped.append(buffer)
    return grouped


def _prop_timing_map(transition: dict[str, Any]) -> dict[str, tuple[str, str]]:
    properties = [prop.strip() for prop in (transition.get("properties") or [])]
    durations = transition.get("durations") or []
    easings = _regroup_paren(transition.get("easings") or [])
    result: dict[str, tuple[str, str]] = {}
    for index, prop in enumerate(properties):
        if not prop or prop == "none":
            continue
        duration = durations[index % len(durations)] if durations else ""
        easing = easings[index % len(easings)] if easings else ""
        result[prop] = (duration, easing)
    return result


def _lookup_timing(
    prop: str,
    timings: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    if prop in timings:
        return timings[prop]
    return timings.get("all")


def _normalize_transform(value: str) -> str:
    if value.replace(" ", "") == "matrix(1,0,0,1,0,0)":
        return "none"
    return value


def _norm_hover(prop: str, value: Any) -> str:
    normalized = str(value or "").strip()
    if prop == "transform":
        return _normalize_transform(normalized)
    if prop == "scale" and normalized in {"", "normal"}:
        return "none"
    return normalized


def _hover_changed(prop: str, idle: Any, hover: Any) -> bool:
    normalized_idle = _norm_hover(prop, idle)
    normalized_hover = _norm_hover(prop, hover)
    if normalized_idle == normalized_hover:
        return False
    inert = {"", "none", "normal", "auto"}
    return not (normalized_idle in inert and normalized_hover in inert)


def _find_hover(
    rows: list[dict[str, Any]],
    selector: str,
) -> dict[str, Any] | None:
    return next((row for row in rows if row["selector"] == selector), None)


def compare_transitions(
    ref_elements: list[dict[str, Any]],
    impl_elements: list[dict[str, Any]],
    hover_states: dict[str, list[dict[str, Any]]],
    compare_limit: int,
    dynamic_carousel_targets: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], int, int]:
    report: list[dict[str, Any]] = []
    pass_count = 0
    fail_count = 0
    hover_props = ["opacity", "transform", "scale", "backgroundColor", "color"]

    for ref_element in ref_elements[:compare_limit]:
        if _skip_transition_noise(ref_element):
            continue
        impl_element = find_impl_match(
            ref_element,
            impl_elements,
            dynamic_carousel=_inside_dynamic_carousel(
                str(ref_element.get("selector") or ""),
                dynamic_carousel_targets,
            ),
        )
        entry: dict[str, Any] = {
            "selector": ref_element["selector"],
            "text": ref_element.get("text", ""),
            "issues": [],
            "warnings": [],
        }

        if not impl_element:
            entry["issues"].append("MISSING: no matching element in impl")
            entry["status"] = "FAIL"
            fail_count += 1
            report.append(entry)
            continue

        ref_map = _prop_timing_map(ref_element["transition"])
        impl_map = _prop_timing_map(impl_element["transition"])
        for prop, (ref_duration, ref_easing) in ref_map.items():
            impl_timing = _lookup_timing(prop, impl_map)
            if impl_timing is None:
                entry["issues"].append(
                    f"MISSING_TRANSITION: ref animates {prop} "
                    f"(dur={ref_duration}, ease={ref_easing}), "
                    "impl has no matching transition"
                )
                continue
            impl_duration, impl_easing = impl_timing
            if ref_duration != impl_duration:
                entry["issues"].append(
                    f"DURATION_MISMATCH: prop={prop} ref={ref_duration} impl={impl_duration}"
                )
            if ref_easing != impl_easing:
                entry["issues"].append(
                    f"EASING_MISMATCH: prop={prop} ref={ref_easing} impl={impl_easing}"
                )

        for prop in ["opacity", "transform", "backgroundColor", "color"]:
            ref_value = ref_element["idleStyle"].get(prop, "")
            impl_value = impl_element["idleStyle"].get(prop, "")
            if prop == "transform":
                ref_value = _normalize_transform(ref_value)
                impl_value = _normalize_transform(impl_value)
            if ref_value != impl_value and ref_value and impl_value:
                if prop in {"backgroundColor", "color"} and (
                    ref_value.replace(" ", "") == impl_value.replace(" ", "")
                ):
                    continue
                entry["issues"].append(
                    f"IDLE_{prop.upper()}_MISMATCH: ref={ref_value}, impl={impl_value}"
                )

        ref_hover = _find_hover(
            hover_states.get("ref", []),
            ref_element["selector"],
        )
        impl_hover = _find_hover(
            hover_states.get("impl", []),
            impl_element.get("selector", ""),
        )
        ref_hover_style = ref_hover.get("hoverStyle") if ref_hover else {}
        if not isinstance(ref_hover_style, dict):
            ref_hover_style = {}
        ref_hover_unverified = bool(
            ref_hover and (ref_hover.get("hoverVerified") is False or ref_hover.get("captureError"))
        )
        impl_hover_unverified = bool(
            impl_hover
            and (impl_hover.get("hoverVerified") is False or impl_hover.get("captureError"))
        )
        ref_has_hover_motion = bool(ref_hover_style) and any(
            _hover_changed(
                prop,
                ref_element["idleStyle"].get(prop, ""),
                ref_hover_style.get(prop, ""),
            )
            for prop in hover_props
        )

        if (
            ref_hover
            and impl_hover
            and not ref_hover_unverified
            and not impl_hover_unverified
            and ref_hover.get("hoverStyle")
            and impl_hover.get("hoverStyle")
        ):
            for prop in hover_props:
                ref_hover_value = ref_hover["hoverStyle"].get(prop, "")
                impl_hover_value = impl_hover["hoverStyle"].get(prop, "")
                ref_idle = ref_element["idleStyle"].get(prop, "")
                impl_idle = impl_element["idleStyle"].get(prop, "")
                ref_changes = _hover_changed(prop, ref_idle, ref_hover_value)
                impl_changes = _hover_changed(prop, impl_idle, impl_hover_value)
                if ref_changes and not impl_changes:
                    entry["issues"].append(
                        f"HOVER_{prop.upper()}_NOT_APPLIED: "
                        f"ref changes {prop} on hover "
                        f"({ref_idle} -> {ref_hover_value}), impl stays same"
                    )
                elif impl_changes and not ref_changes:
                    entry["issues"].append(
                        f"EXTRA_HOVER_{prop.upper()}_APPLIED: "
                        f"impl changes {prop} on hover "
                        f"({impl_idle} -> {impl_hover_value}) "
                        f"but ref stays {ref_idle}"
                    )
        elif ref_hover_unverified or impl_hover_unverified or ref_has_hover_motion:
            failed_sides = []
            if ref_hover_unverified:
                failed_sides.append("ref")
            if impl_hover_unverified:
                failed_sides.append("impl")
            failure_note = (
                f" ({'/'.join(failed_sides)} pointer capture failed)" if failed_sides else ""
            )
            entry["warnings"].append(
                "HOVER_UNVERIFIED: hover state for "
                f"{ref_element['selector']} was not fully "
                f"captured{failure_note} — hover "
                "fidelity unverified, not confirmed"
            )

        if entry["issues"]:
            entry["status"] = "FAIL"
            fail_count += 1
        else:
            entry["status"] = "PASS"
            pass_count += 1
        report.append(entry)

    return report, pass_count, fail_count


def write_artifacts(
    transitions_dir: Path,
    report: list[dict[str, Any]],
    pass_count: int,
    fail_count: int,
) -> None:
    (transitions_dir / "report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )
    lines = [f"Transition compare: {pass_count} PASS, {fail_count} FAIL"]
    for row in report:
        marker = "✅ PASS" if row["status"] == "PASS" else "❌ FAIL"
        lines.append(f"{marker}  {row.get('selector', '?')[:60]}")
        lines.extend(f"    - {issue[:120]}" for issue in row.get("issues", [])[:3])
        lines.extend(f"    ⚠ {warning[:120]}" for warning in row.get("warnings", [])[:3])
    (transitions_dir / "result.txt").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def print_summary(
    report: list[dict[str, Any]],
    pass_count: int,
    fail_count: int,
) -> None:
    print("")
    print("| Element | Status | Issues |")
    print("|---------|--------|--------|")
    for row in report:
        issues = "; ".join(row["issues"][:2]) if row["issues"] else "—"
        print(f"| {row['selector'][:30]} | {row['status']} | {issues[:60]} |")
    warning_count = sum(len(row.get("warnings") or []) for row in report)
    print("")
    print(f"**Result: {pass_count} PASS, {fail_count} FAIL**")
    if warning_count:
        print(f"⚠ {warning_count} hover-unverified warning(s) — see result.txt (non-fatal)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("compare_limit", type=int)
    args = parser.parse_args()
    transitions_dir = args.output_dir / "transitions"
    ref_elements = json.loads((transitions_dir / "ref-elements.json").read_text(encoding="utf-8"))
    impl_elements = json.loads((transitions_dir / "impl-elements.json").read_text(encoding="utf-8"))
    hover_states = json.loads((transitions_dir / "hover-states.json").read_text(encoding="utf-8"))
    report, pass_count, fail_count = compare_transitions(
        ref_elements,
        impl_elements,
        hover_states,
        args.compare_limit,
        _dynamic_carousel_targets(args.output_dir),
    )
    write_artifacts(transitions_dir, report, pass_count, fail_count)
    print_summary(report, pass_count, fail_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
