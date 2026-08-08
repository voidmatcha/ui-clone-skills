"""B1: transition-compare's hover channel must actually decode captured styles.

agent-browser DOUBLE-encodes a JS string return (our eval returns
JSON.stringify({...})), so raw stdout is a JSON string whose value is itself the
JSON object. The old parser did `.strip('"')` then a bogus backslash `.replace`,
so json.loads threw on EVERY element and hoverStyle silently became {} — the
whole hover channel was dead (a clone missing all its :hover rules passed on
timing strings alone; F10's HOVER_UNVERIFIED could never fire).

This test slices the REAL `_unwrap_ab_json` function out of its standalone
capture helper so there is no drift between the tested and shipped logic.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

HELPER = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "visual-debug"
    / "scripts"
    / "transition_capture_hover.py"
)
SCRIPT = HELPER.with_name("transition-compare.sh")


def _load_unwrap() -> Callable[[object], dict]:
    lines = HELPER.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("def _unwrap_ab_json("))
    end = next(
        i
        for i in range(start + 1, len(lines))
        if lines[i] and not lines[i][0].isspace() and not lines[i].startswith("def _unwrap_ab_json")
    )
    src = "\n".join(lines[start:end])
    ns: dict[str, Any] = {"json": json, "Any": Any}
    exec(src, ns)  # noqa: S102 — running the repo's own sliced helper
    fn = ns["_unwrap_ab_json"]
    assert callable(fn)
    return cast("Callable[[object], dict]", fn)


_unwrap = _load_unwrap()


def test_double_encoded_string_decodes_to_dict() -> None:
    # Exact shape agent-browser emits for JSON.stringify({...}).
    raw = '"{\\"opacity\\":\\"0.5\\",\\"transform\\":\\"none\\"}"'
    assert _unwrap(raw) == {"opacity": "0.5", "transform": "none"}


def test_envelope_object_with_data_result_string() -> None:
    inner = json.dumps({"opacity": "1"})
    raw = json.dumps({"success": True, "data": {"result": inner}})
    assert _unwrap(raw) == {"opacity": "1"}


def test_single_encoded_object_passes_through() -> None:
    raw = json.dumps({"color": "rgb(0, 0, 0)"})
    assert _unwrap(raw) == {"color": "rgb(0, 0, 0)"}


def test_empty_and_garbage_return_empty_dict() -> None:
    assert _unwrap("") == {}
    assert _unwrap("   ") == {}
    assert _unwrap(None) == {}
    assert _unwrap("not json at all") == {}


def test_error_sentinel_is_still_a_dict_caller_filters() -> None:
    # 'not found' path returns JSON.stringify({error:'not found'}); the helper
    # decodes it; the caller drops rows whose hoverStyle carries 'error'.
    raw = '"{\\"error\\":\\"not found\\"}"'
    assert _unwrap(raw) == {"error": "not found"}


def test_capture_verifies_real_hover_and_retries_with_fresh_box() -> None:
    source = HELPER.read_text(encoding="utf-8")

    assert "el.matches(':hover')" in source
    assert '"mouse", "move", "-100", "-100"' in source
    fresh_probe = source.index(
        "probe = _hover_probe(session, selector_literal)",
        source.index("def _ensure_real_hover("),
    )
    selector_hover = source.index(
        '_ab_command(session, "hover", selector)',
        fresh_probe,
    )
    assert fresh_probe < selector_hover
    assert "viewport: { width: innerWidth, height: innerHeight }" in source
    assert "if right <= left or bottom <= top:" in source
    visible_guard = source.index(
        '"if (visible) return \'already-visible\';"'
    )
    eager_scroll = source.index(
        '"el.scrollIntoView({ block: \'center\' });"',
        visible_guard,
    )
    assert visible_guard < eager_scroll
    assert '_ab_command(session, "scrollintoview", selector)' in source
    assert 'f"{(left + right) / 2:.2f}"' in source
    assert 'f"{(top + bottom) / 2:.2f}"' in source
    assert '"hoverVerified": hover_verified' in source
    assert '"captureError": "real pointer did not reach target"' in source


def test_transition_compare_pins_existing_swipers_before_detection() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    pin_start = source.index("PIN_SWIPERS_JS=")
    ref_pin = source.index(
        'agent-browser --session "$SESSION_REF" eval "$PIN_SWIPERS_JS"',
        pin_start,
    )
    impl_pin = source.index(
        'agent-browser --session "$SESSION_IMPL" eval "$PIN_SWIPERS_JS"',
        ref_pin,
    )
    ref_settle = source.index(
        'agent-browser --session "$SESSION_REF" wait "$SWIPER_SETTLE_WAIT"',
        impl_pin,
    )
    impl_settle = source.index(
        'agent-browser --session "$SESSION_IMPL" wait "$SWIPER_SETTLE_WAIT"',
        ref_settle,
    )
    detect = source.index('DETECT_HELPER="$_SCRIPT_DIR/lib/transition-detect.js"')

    pin_source = source[pin_start:ref_pin]
    assert 'document.querySelectorAll("*")' in pin_source
    assert "el.swiper" in pin_source
    assert "swiper.autoplay.stop()" in pin_source
    assert "swiper.slideToLoop(0, 0, false)" in pin_source
    assert "swiper.slideTo(0, 0, false)" in pin_source
    assert pin_start < ref_pin < impl_pin < ref_settle < impl_settle < detect


def test_transition_detection_prefers_pointer_reachable_semantic_clone() -> None:
    detector = HELPER.with_name("lib") / "transition-detect.js"
    source = detector.read_text(encoding="utf-8")

    assert "document.elementFromPoint(x, y)" in source
    assert "pointerReachable = Boolean" in source
    assert "semanticGeometryKey" in source
    assert "item.matchKey?.href" in source
    assert "item.rect?.top" in source
    assert "stateHiddenAncestor" in source
    assert '"is-hide", "is-hidden", "hidden"' in source
    assert "candidateRank(item) > candidateRank(current)" in source
    assert "return [...deduped.values()]" in source
    assert "!/^h_\\d+$/.test(c)" in source


def test_transition_compare_uses_a_wider_impl_lookup_pool() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "_DEFAULT_MAX_IMPL_TRANSITIONS=$((MAX_TRANSITIONS * 10))" in source
    assert 'MAX_IMPL_TRANSITIONS="${MAX_IMPL_TRANSITIONS:-$_DEFAULT_MAX_IMPL_TRANSITIONS}"' in source
    assert 'DETECT_TRANSITIONS_REF="${DETECT_TRANSITIONS_TEMPLATE/__MAX_TRANSITIONS__/$MAX_TRANSITIONS}"' in source
    assert 'DETECT_TRANSITIONS_IMPL="${DETECT_TRANSITIONS_TEMPLATE/__MAX_TRANSITIONS__/$MAX_IMPL_TRANSITIONS}"' in source
    assert 'eval "$DETECT_TRANSITIONS_REF"' in source
    assert 'eval "$DETECT_TRANSITIONS_IMPL"' in source


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_transition_detection_prefers_stable_offscreen_clone(
    tmp_path: Path,
) -> None:
    detector = HELPER.with_name("lib") / "transition-detect.js"
    source = (
        detector.read_text(encoding="utf-8")
        .replace("__EXCLUDE_SELECTORS_JSON__", "null")
        .replace("__MAX_TRANSITIONS__", "10")
    )
    harness = tmp_path / "detector-harness.js"
    harness.write_text(
        """
const makeElement = (id, stateHidden) => ({
  id,
  tagName: "A",
  className: "nav__link",
  textContent: "회사소개",
  childNodes: [],
  parentElement: null,
  getBoundingClientRect() {
    return {top: 1200, bottom: 1226, left: 100, right: 160, width: 60, height: 26};
  },
  closest(selector) {
    return stateHidden && selector.includes("is-hide") ? this : null;
  },
  contains(node) {
    return node === this;
  },
  getAttribute(name) {
    const values = {href: "/company/about", role: "", "aria-label": "", title: "", src: ""};
    return values[name] || "";
  },
});
const hidden = makeElement("hidden", true);
const stable = makeElement("stable", false);
global.window = {
  innerHeight: 900,
  innerWidth: 1440,
  scrollY: 0,
  CSS: {escape: (value) => String(value)},
};
global.CSS = window.CSS;
global.Node = {TEXT_NODE: 3};
global.document = {
  documentElement: {scrollHeight: 5000},
  querySelectorAll(selector) {
    if (selector.startsWith("a, button")) return [hidden, stable];
    if (selector === "#hidden") return [hidden];
    if (selector === "#stable") return [stable];
    if (selector === ".nav__link") return [hidden, stable];
    return [];
  },
  elementFromPoint() {
    throw new Error("offscreen candidates must not be hit-tested");
  },
};
global.getComputedStyle = () => ({
  display: "block",
  visibility: "visible",
  opacity: "1",
  transitionDuration: "0.4s",
  transitionProperty: "color",
  transitionTimingFunction: "ease",
  animationName: "none",
  transform: "none",
  backgroundColor: "rgba(0, 0, 0, 0)",
  color: "rgb(0, 0, 0)",
  scale: "none",
  filter: "none",
  boxShadow: "none",
});
const result = DETECTOR_SOURCE;
process.stdout.write(JSON.stringify(result));
""".replace("DETECTOR_SOURCE", source),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(harness)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert [row["selector"] for row in result] == ["#stable"]
    assert result[0]["pointerReachable"] is None
    assert result[0]["stateHiddenAncestor"] is False
