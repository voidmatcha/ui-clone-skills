"""Computed-style property contract shared by the element-scope tooling.

`skills/visual-debug/scripts/computed-diff.sh` compares getComputedStyle values
between a reference and an implementation for a fixed property list and a
fixed set of skip rules. The scoped-clone producers (`element-state-capture.sh`
captures the values per state, `python -m ui_clone.scoped_diff` diffs them and
`python -m ui_clone.scoped_check` re-diffs them) must judge with the SAME
contract, so the list and the rules live here. The shell script keeps its own
inline copy so page-level behavior is byte-identical; `tests/` pins the two
copies together.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# Property list of computed-diff.sh (`PROPS`), in the same order.
COMPUTED_STYLE_PROPS: tuple[str, ...] = (
    "display",
    "position",
    "width",
    "height",
    "padding",
    "margin",
    "fontSize",
    "fontWeight",
    "fontFamily",
    "lineHeight",
    "letterSpacing",
    "color",
    "backgroundColor",
    "borderRadius",
    "border",
    "boxShadow",
    "opacity",
    "transform",
    "zIndex",
    "gap",
    "flexDirection",
    "alignItems",
    "justifyContent",
    "gridTemplateColumns",
)

# Properties where OS-level font scaling causes spurious diffs (computed-diff.sh
# `IGNORE_FONT_SIZE=1`).
FONT_SIZE_PROPS: frozenset[str] = frozenset(
    {"fontSize", "lineHeight", "width", "height", "letterSpacing"}
)
_UNSET_VALUES: frozenset[str] = frozenset({"", "none", "normal", "auto"})


def properties_sha256(props: tuple[str, ...] | list[str] = COMPUTED_STYLE_PROPS) -> str:
    """Stable fingerprint of a property list, recorded in the diff artifact."""
    return hashlib.sha256(json.dumps(list(props)).encode("utf-8")).hexdigest()


def _first_family(value: str) -> str:
    return value.split(",")[0].strip().strip("\"'")


def diff_styles(
    ref: dict[str, Any] | None,
    impl: dict[str, Any] | None,
    *,
    ignore_font_size: bool = False,
    props: tuple[str, ...] | list[str] = COMPUTED_STYLE_PROPS,
) -> list[dict[str, str]]:
    """Return the mismatching properties for one element, computed-diff.sh rules.

    A missing element on either side is one mismatch (`property: "-"`), never
    a silent pass. Values are compared as strings; the skip rules are the
    script's: both semantically unset, a zero-width border differing only in
    style keyword, or a font stack whose first family matches.
    """
    if ref is None and impl is None:
        return [{"property": "-", "ref": "NOT FOUND", "impl": "NOT FOUND"}]
    if ref is None:
        return [{"property": "-", "ref": "NOT FOUND", "impl": "found"}]
    if impl is None:
        return [{"property": "-", "ref": "found", "impl": "NOT FOUND"}]
    rows: list[dict[str, str]] = []
    for prop in props:
        ov = "" if ref.get(prop) is None else str(ref.get(prop))
        iv = "" if impl.get(prop) is None else str(impl.get(prop))
        if ov == iv:
            continue
        if ov in _UNSET_VALUES and iv in _UNSET_VALUES:
            continue
        if prop == "border" and ov.startswith("0px") and iv.startswith("0px"):
            continue
        if prop == "fontFamily" and _first_family(ov) == _first_family(iv):
            continue
        if ignore_font_size and prop in FONT_SIZE_PROPS:
            continue
        rows.append({"property": prop, "ref": ov[:60], "impl": iv[:60]})
    return rows


# ── target subtree (scoped clones only) ───────────────────────────────────
#
# `element-state-capture.sh clip` records the target's element descendants in
# document (depth-first, pre-order) order, up to SUBTREE_MAX_NODES, each with a
# structural path and the same property list. The path rule is fixed:
#
#   <tag>[<i>]/<tag>[<i>]/...   i = index among the parent's ELEMENT children
#
# Text and comment nodes never count, so `div[0]/span[1]` is the second element
# child of the first element child of the target. Reference and implementation
# nodes are matched by this path only. A path present on one side but not the
# other, or a different tag at the same position (which yields a different
# path), is a structural mismatch reported as a `-` property row — never a
# silent skip. Total descendant counts must agree as well, so a subtree that is
# truncated at the cap on one side and not the other still fails.
SUBTREE_MAX_NODES = 40
SUBTREE_PATH_RE = re.compile(r"^(?:[a-z][a-z0-9-]*\[\d+\])(?:/[a-z][a-z0-9-]*\[\d+\])*$")


def _node_map(nodes: object) -> dict[str, dict[str, Any]] | None:
    """`{path: computedStyle}` for a recorded subtree list, None when malformed."""
    if not isinstance(nodes, list):
        return None
    out: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            return None
        path = node.get("path")
        styles = node.get("computedStyle")
        if not isinstance(path, str) or not SUBTREE_PATH_RE.match(path) or not isinstance(styles, dict):
            return None
        out[path] = styles
    return out


def diff_records(
    ref: dict[str, Any] | None,
    impl: dict[str, Any] | None,
    *,
    props: tuple[str, ...] | list[str] = COMPUTED_STYLE_PROPS,
    max_nodes: int = SUBTREE_MAX_NODES,
) -> list[dict[str, str]]:
    """Diff two `<state>.computed.json` records: the target (`path: ""`) plus
    its recorded subtree, matched by structural path (rule above).

    A record without a `subtree` list (an older capture) or with a malformed
    one fails with a `subtree` row so it is re-captured, never trusted.
    """
    ref_styles = ref.get("computedStyle") if isinstance(ref, dict) else None
    impl_styles = impl.get("computedStyle") if isinstance(impl, dict) else None
    rows: list[dict[str, str]] = [
        {"path": "", **row}
        for row in diff_styles(
            ref_styles if isinstance(ref_styles, dict) else None,
            impl_styles if isinstance(impl_styles, dict) else None,
            props=props,
        )
    ]
    if not isinstance(ref, dict) or not isinstance(impl, dict):
        return rows
    ref_nodes = _node_map(ref.get("subtree"))
    impl_nodes = _node_map(impl.get("subtree"))
    for side, nodes in (("ref", ref_nodes), ("impl", impl_nodes)):
        if nodes is None:
            rows.append(
                {
                    "path": "",
                    "property": "subtree",
                    "ref": "recorded" if side == "impl" else "NOT RECORDED",
                    "impl": "recorded" if side == "ref" else "NOT RECORDED",
                }
            )
    if ref_nodes is None or impl_nodes is None:
        return rows
    ref_count = ref.get("descendantCount")
    impl_count = impl.get("descendantCount")
    if ref_count != impl_count:
        rows.append(
            {
                "path": "",
                "property": "descendantCount",
                "ref": str(ref_count),
                "impl": str(impl_count),
            }
        )
    for path in sorted(set(ref_nodes) | set(impl_nodes), key=lambda p: (p.count("/"), p)):
        if len(rows) > max_nodes * len(props):
            break
        if path not in impl_nodes:
            rows.append({"path": path, "property": "-", "ref": "found", "impl": "NOT FOUND"})
            continue
        if path not in ref_nodes:
            rows.append({"path": path, "property": "-", "ref": "NOT FOUND", "impl": "found"})
            continue
        rows.extend(
            {"path": path, **row} for row in diff_styles(ref_nodes[path], impl_nodes[path], props=props)
        )
    return rows
