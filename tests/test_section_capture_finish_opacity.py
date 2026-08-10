"""The capture fast-forward must never make a CSS-hidden element visible.

`_finish_js()` snaps near-settled framer/WAAPI elements to their end frame so
the screenshot is deterministic. Its translate3d snap read the element's
INLINE opacity with a `|| "1"` default, then wrote `el.style.opacity = "1"`
whenever that default kicked in — so an element that carries an inline
`translate3d(...)` but gets its opacity from a STYLESHEET rule (a scroll-gated
reveal: `.host:not([data-reveal="1"]) .item { opacity: 0 }`) was force-shown by
the capture itself.

realfood-v4-harness pyramid: 76 `.food` tiles are gated to opacity 0 at the
capture anchor on both ref and impl, but the impl's tiles carry a baked inline
transform, so only the impl got the injected `opacity: 1` — 63k AE of pure
measurement artifact that no impl edit could move.

The snap may still normalize an opacity the element already declares inline
(0.9995 -> 1); it must not introduce one.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from ui_clone.section_capture import _finish_js

HARNESS = """
const els = [
  mk("css-gated", "transform: translate3d(-2.8px, -2.3px, 0px);", ""),
  mk("inline-midflight", "transform: translate3d(1px, 1px, 0px); opacity: 0.9995;", "0.9995"),
  mk("inline-hidden", "transform: translate3d(1px, 1px, 0px); opacity: 0;", "0"),
];
function mk(name, attr, op) {
  return { name, _attr: attr, style: { opacity: op, transform: "" },
           getAttribute: function () { return this._attr; } };
}
globalThis.window = globalThis;
globalThis.document = {
  getAnimations: () => [],
  querySelectorAll: (sel) => (String(sel).indexOf("translate3d") >= 0 ? els : []),
};
%(FINISH)s;
console.log(JSON.stringify(els.map((e) => ({ name: e.name, opacity: e.style.opacity }))));
"""


def _run_finish_js() -> dict[str, str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the capture fast-forward JS")
    proc = subprocess.run(
        [node, "-e", HARNESS % {"FINISH": _finish_js()}],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return {row["name"]: row["opacity"] for row in json.loads(proc.stdout.strip())}


def test_finish_js_does_not_inject_opacity_on_stylesheet_gated_element() -> None:
    """The pyramid `.food` class: inline transform, no inline opacity."""
    assert _run_finish_js()["css-gated"] == ""


def test_finish_js_still_snaps_a_declared_inline_opacity_to_one() -> None:
    """A genuinely mid-flight framer element keeps being fast-forwarded."""
    assert _run_finish_js()["inline-midflight"] == "1"


def test_finish_js_leaves_an_inline_hidden_element_hidden() -> None:
    assert _run_finish_js()["inline-hidden"] == "0"
