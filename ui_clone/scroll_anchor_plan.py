"""Build section-aligned scroll capture plans for visual scroll coverage.

Percentage-based scroll screenshots compare 25% of the ref page with 25% of
an impl page. That is useful only when the two pages have identical section
heights. This module pairs semantic section anchors first, then emits per-side
scroll offsets so ref section N is compared with impl section N even when their
absolute heights differ. Sticky/pinned and scroll-transition anchors get extra
entry/mid/exit probes because their failures often happen at phase boundaries,
not at a single resting top frame.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from ui_clone.section_capture import safe_section_name
from ui_clone.section_compare_sections import pair_sections

Section = dict[str, Any]
PlanRow = dict[str, Any]

_SCROLL_TRIGGER_RE = re.compile(r"scroll|sticky|pin|scrub|intersection|inview|parallax", re.I)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{4,}")
_DOCUMENT_ROOT_IDS = {"root", "__next", "__nuxt", "app", "svelte"}


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _rect(row: Section) -> dict[str, object]:
    raw = row.get("rect")
    if isinstance(raw, dict):
        return raw
    return {
        "top": row.get("top") or row.get("y") or 0,
        "height": row.get("height") or row.get("h") or 0,
        "width": row.get("width") or row.get("w") or 0,
        "left": row.get("left") or row.get("x") or 0,
    }


def _top(row: Section) -> float:
    return _as_float(_rect(row).get("top"))


def _height(row: Section) -> float:
    return _as_float(_rect(row).get("height"))


def _is_document_wrapper(row: Section, viewport_height: int) -> bool:
    tag = _norm_text(row.get("tag")).lower()
    if tag in {"html", "body"}:
        return True
    row_id = _norm_text(row.get("id")).lower()
    return (
        row_id in _DOCUMENT_ROOT_IDS
        and _height(row) >= viewport_height * 1.5
    )


def _is_fixed_overlay(row: Section, viewport_height: int) -> bool:
    position = _norm_text(row.get("position")).lower()
    return position == "fixed" and (
        _top(row) < 0 or _height(row) >= viewport_height * 0.5
    )


def _is_scroll_anchor_candidate(row: Section, viewport_height: int) -> bool:
    return not (
        _is_document_wrapper(row, viewport_height)
        or _is_fixed_overlay(row, viewport_height)
    )


def _norm_text(value: object) -> str:
    return str(value or "").strip()


def _tokens(value: object) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(str(value or ""))}


def _row_tokens(row: Section) -> set[str]:
    values = [
        row.get("id"),
        row.get("className"),
        row.get("cls"),
        row.get("selector"),
        row.get("name"),
        row.get("fingerprint"),
    ]
    out: set[str] = set()
    for value in values:
        out |= _tokens(value)
    return out


def _coerce_sections(value: object) -> list[Section]:
    if not isinstance(value, list):
        return []
    sections: list[Section] = []
    for idx, row in enumerate(value):
        if not isinstance(row, dict):
            continue
        copied = dict(row)
        copied.setdefault("index", idx)
        rect = _rect(copied)
        copied["rect"] = {
            "top": _as_float(rect.get("top")),
            "height": _as_float(rect.get("height")),
            "width": _as_float(rect.get("width")),
            "left": _as_float(rect.get("left")),
        }
        copied.setdefault("tag", _norm_text(copied.get("tag") or "section").lower())
        if "fingerprint" not in copied:
            copied["fingerprint"] = _norm_text(
                copied.get("textWords") or copied.get("text") or copied.get("textPreview")
            )[:500]
        sections.append(copied)
    sections.sort(key=lambda row: (_top(row), int(_as_float(row.get("index"), 0))))
    for idx, row in enumerate(sections):
        row["index"] = idx
    return sections


def _load_json_file(path: str | Path | None) -> object | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        parsed: object = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        return parsed
    except (OSError, json.JSONDecodeError):
        return None


def load_eval_json(path: str | Path) -> object | None:
    """Read raw `agent-browser eval` output and peel common wrappers."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return None
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        # Some eval implementations print a quoted JSON string with log noise.
        start = min([i for i in (raw.find("["), raw.find("{")) if i >= 0], default=-1)
        if start < 0:
            return None
        try:
            parsed = json.loads(raw[start:])
        except json.JSONDecodeError:
            return None

    # agent-browser --json envelope: {success, data:{result:<inner>}}
    if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
        data = parsed["data"]
        if "result" in data:
            parsed = data["result"]
    # Legacy/simple wrapper: {result:<inner>}
    if isinstance(parsed, dict) and "result" in parsed:
        parsed = parsed["result"]
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            return None
    return parsed


def _sticky_tokens(sticky: object | None) -> set[str]:
    if isinstance(sticky, dict):
        raw = sticky.get("elements") or sticky.get("stickyElements") or []
    else:
        raw = sticky
    tokens: set[str] = set()
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            for key in ("selector", "cls", "className", "class", "id", "target"):
                tokens |= _tokens(entry.get(key))
    return tokens


def _transition_tokens_and_scroll_signal(spec: object | None) -> tuple[set[str], bool]:
    transitions: object = []
    if isinstance(spec, dict):
        transitions = spec.get("transitions") or []
    elif isinstance(spec, list):
        transitions = spec
    tokens: set[str] = set()
    has_scroll = False
    if isinstance(transitions, list):
        for entry in transitions:
            if not isinstance(entry, dict):
                continue
            haystack = " ".join(
                str(entry.get(k) or "")
                for k in ("id", "type", "trigger", "selector", "target", "bundle_branch")
            )
            if _SCROLL_TRIGGER_RE.search(haystack):
                has_scroll = True
                tokens |= _tokens(haystack)
    return tokens, has_scroll


def _motion_reason(
    ref: Section,
    impl: Section,
    *,
    sticky_tokens: set[str],
    transition_tokens: set[str],
    has_scroll_transition: bool,
    viewport_height: int,
) -> str | None:
    joined = " ".join(
        str(ref.get(k) or "") for k in ("id", "className", "cls", "selector", "name")
    )
    row_tokens = _row_tokens(ref) | _row_tokens(impl)
    if _SCROLL_TRIGGER_RE.search(joined) or row_tokens & sticky_tokens:
        return "sticky-phase"
    if row_tokens & transition_tokens:
        return "scroll-transition-phase"
    if has_scroll_transition and max(_height(ref), _height(impl)) >= viewport_height * 1.35:
        return "scroll-transition-phase"
    return None


def _scroll_y_for(row: Section, phase: str, viewport_height: int) -> float:
    top = _top(row)
    height = _height(row)
    if phase in {"top", "section"}:
        y = top - 50
    elif phase == "enter":
        y = top - viewport_height * 0.25
    elif phase in {"mid", "lock"}:
        y = top + max(0.0, height / 2 - viewport_height / 2)
    elif phase == "exit":
        y = top + max(0.0, height - viewport_height + 50)
    else:
        y = top - 50
    return max(0.0, y)


def _append_row(
    rows: list[PlanRow],
    *,
    name: str,
    phase: str,
    reason: str,
    ref: Section,
    impl: Section,
    viewport_height: int,
) -> None:
    ref_y = _scroll_y_for(ref, phase, viewport_height)
    impl_y = _scroll_y_for(impl, phase, viewport_height)
    # Avoid near-duplicate probes from sticky enter/top around page start.
    for existing in rows:
        if abs(float(existing["refY"]) - ref_y) < 80 and abs(float(existing["implY"]) - impl_y) < 80:
            return
    rows.append({
        "name": safe_section_name(name, max_length=72),
        "phase": phase,
        "reason": reason,
        "refY": int(round(ref_y)),
        "implY": int(round(impl_y)),
        "refTop": int(round(_top(ref))),
        "implTop": int(round(_top(impl))),
        "refHeight": int(round(_height(ref))),
        "implHeight": int(round(_height(impl))),
    })


def build_scroll_anchor_plan(
    ref_sections: object,
    impl_sections: object,
    *,
    viewport_height: int,
    max_anchors: int = 24,
    sticky: object | None = None,
    transition_spec: object | None = None,
) -> list[PlanRow]:
    ref = [
        row
        for row in _coerce_sections(ref_sections)
        if _is_scroll_anchor_candidate(row, viewport_height)
    ]
    impl = [
        row
        for row in _coerce_sections(impl_sections)
        if _is_scroll_anchor_candidate(row, viewport_height)
    ]
    if not ref or not impl:
        return []

    sticky_tok = _sticky_tokens(sticky)
    transition_tok, has_scroll_transition = _transition_tokens_and_scroll_signal(transition_spec)
    matches = pair_sections(ref, impl)
    rows: list[PlanRow] = []

    for match in matches:
        r = match.get("ref")
        im = match.get("impl")
        if not isinstance(r, dict) or not isinstance(im, dict):
            continue
        base_name = safe_section_name(match.get("name") or r.get("id") or r.get("className") or f"section-{len(rows)}")
        reason = _motion_reason(
            r,
            im,
            sticky_tokens=sticky_tok,
            transition_tokens=transition_tok,
            has_scroll_transition=has_scroll_transition,
            viewport_height=viewport_height,
        )
        if reason:
            for phase in ("enter", "mid", "exit"):
                _append_row(
                    rows,
                    name=f"{base_name}__{phase}",
                    phase=phase,
                    reason=reason,
                    ref=r,
                    impl=im,
                    viewport_height=viewport_height,
                )
        else:
            _append_row(
                rows,
                name=base_name,
                phase="top",
                reason="section-anchor",
                ref=r,
                impl=im,
                viewport_height=viewport_height,
            )
        if len(rows) >= max_anchors:
            break

    rows.sort(key=lambda row: (int(row["refY"]), int(row["implY"]), str(row["name"])))
    return rows[:max_anchors]


def _cmd_build(args: argparse.Namespace) -> int:
    ref = load_eval_json(args.ref_anchors)
    impl = load_eval_json(args.impl_anchors)
    sticky = _load_json_file(args.sticky)
    transition_spec = _load_json_file(args.transition_spec)
    plan = build_scroll_anchor_plan(
        ref,
        impl,
        viewport_height=args.viewport_height,
        max_anchors=args.max_anchors,
        sticky=sticky,
        transition_spec=transition_spec,
    )
    Path(args.out).write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"scroll-anchor-plan: {len(plan)} anchor probe(s) written to {args.out}")
    return 0 if plan else 1


def _cmd_tsv(args: argparse.Namespace) -> int:
    raw = _load_json_file(args.plan)
    if not isinstance(raw, list):
        return 1
    for row in raw:
        if not isinstance(row, dict):
            continue
        print("\t".join(str(row.get(k, "")) for k in ("name", "refY", "implY", "reason")))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("ref_anchors")
    build.add_argument("impl_anchors")
    build.add_argument("out")
    build.add_argument("--viewport-height", type=int, required=True)
    build.add_argument("--max-anchors", type=int, default=24)
    build.add_argument("--sticky")
    build.add_argument("--transition-spec")
    build.set_defaults(func=_cmd_build)

    tsv = sub.add_parser("tsv")
    tsv.add_argument("plan")
    tsv.set_defaults(func=_cmd_tsv)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
