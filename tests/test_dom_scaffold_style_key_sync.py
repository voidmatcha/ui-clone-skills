from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _extract_tuple(source: str, name: str) -> tuple[Any, ...]:
    m = re.search(rf"^{re.escape(name)} = \(\n(.*?)^\)\n", source, re.M | re.S)
    assert m, f"could not find {name} = (...) block in dom-scaffold.sh"
    body = m.group(1)
    # Comment lines are not valid tuple syntax on their own; strip them so
    # ast.literal_eval sees a plain comma-separated string tuple.
    stripped = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("#")
    )
    value: tuple[Any, ...] = ast.literal_eval(f"({stripped}\n)")
    return value


# _PER_NODE_SHORTHAND renames these five CSS properties to STYLE_KEYS's
# abbreviated typography names; every other key is carried through under
# the identical name in both tuples ("Same fidelity props as STYLE_KEYS").
_RENAMED_CSS_TO_SHORT = {
    "font-family": "ff",
    "font-size": "fs",
    "font-weight": "fw",
    "line-height": "lh",
    "letter-spacing": "ls",
}


def test_style_keys_and_per_node_shorthand_stay_in_sync() -> None:
    """STYLE_KEYS and _PER_NODE_SHORTHAND (dom-scaffold.sh) are parallel
    lists -- documented as carrying "Same fidelity props as STYLE_KEYS" --
    but with no sync check, so one can silently drift from the other (as
    happened with transition-property/animation-name: STYLE_KEYS gates
    class-level aggregation while _PER_NODE_SHORTHAND gates the per-node
    override that actually wins -- see walk() -- so a key present in only
    one of the two is silently dropped for either class-level or per-node
    styles depending on which list it's missing from). Compares the two
    tuples directly against each other (modulo the five known typography
    renames), not against a hardcoded snapshot of either.
    """
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "dom-scaffold.sh"
    ).read_text(encoding="utf-8")

    style_keys = set(_extract_tuple(script, "STYLE_KEYS"))
    shorthand_pairs = _extract_tuple(script, "_PER_NODE_SHORTHAND")
    per_node_keys = {css_name for css_name, _short in shorthand_pairs}

    # "bg" is not a simple rename: it's synthesized by bespoke code from a
    # combination of background-color and background-image, not from the
    # _PER_NODE_SHORTHAND pairs list, so it has no counterpart there.
    style_keys.discard("bg")

    # Normalize both sides to their CSS (un-abbreviated) names so the
    # comparison is symmetric: STYLE_KEYS's "ff"/"fs"/"fw"/"lh"/"ls" map back
    # to their CSS names; every other key is already identical in both.
    short_to_css = {short: css for css, short in _RENAMED_CSS_TO_SHORT.items()}
    style_keys_as_css = {short_to_css.get(k, k) for k in style_keys}

    missing_from_shorthand = style_keys_as_css - per_node_keys
    missing_from_style_keys = per_node_keys - style_keys_as_css

    assert not missing_from_shorthand, (
        f"keys in STYLE_KEYS but missing from _PER_NODE_SHORTHAND: {missing_from_shorthand}"
    )
    assert not missing_from_style_keys, (
        f"keys in _PER_NODE_SHORTHAND but missing from STYLE_KEYS: {missing_from_style_keys}"
    )
