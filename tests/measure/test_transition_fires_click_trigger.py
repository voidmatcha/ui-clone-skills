"""The click driver must separate the interaction trigger from the measured target."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ui_clone.gates import transition_fires as tf

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "visual-debug"
    / "scripts"
    / "transition-fires-check.sh"
)


def _phase2_probe(entry: dict) -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index('PHASE2_TEMPLATE="')
    assignment = source[start : source.index("\nENTRY_COUNT=", start)]
    env = dict(
        os.environ,
        ENTRIES_B64=base64.b64encode(json.dumps([entry]).encode()).decode(),
        SETTLE_MS="0",
        SNAP_JS="const snap = el => ({ opacity: el.opacity, height: el.height });",
    )
    proc = subprocess.run(
        ["bash", "-c", assignment + "\nprintf '%s' \"$PHASE2_TEMPLATE\""],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return proc.stdout.replace("__CHUNK_JSON__", "[0]")


def _run_click_probe(trigger_selector: str) -> dict:
    entry = {
        "id": "pattern-tabs-click",
        "target": ".tabs-container",
        "kind": "click",
        "trigger": f"click {trigger_selector}",
        "triggerSelector": trigger_selector,
        "prop": "",
    }
    js = _phase2_probe(entry)
    harness = r"""
const target = {
  isConnected: true,
  opacity: 1,
  height: 42,
  attrs: {'data-tf-idxs':'0'},
  getAttribute(k) { return this.attrs[k] || ''; },
  setAttribute(k, v) { this.attrs[k] = v; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
const trigger = {
  click() { target.opacity = 0; target.height = 84; },
};
global.document = {
  querySelector(selector) {
    if (selector === '.tabs-container') return target;
    if (selector === '.tab-second') return trigger;
    return null;
  },
  querySelectorAll(selector) {
    return selector === '[data-tf-idxs]' ? [target] : [];
  },
};
global.window = { innerHeight: 900, scrollY: 0 };
global.getComputedStyle = el => ({ opacity: String(el.opacity), transform: 'none' });
global.setTimeout = fn => fn();
Promise.resolve(PROBE).then(value => console.log(value));
""".replace("PROBE", js)
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    observation = json.loads(result.stdout)["0"]
    assert isinstance(observation, dict)
    return observation


def test_declared_click_selector_drives_trigger_but_measures_target() -> None:
    observation = _run_click_probe(".tab-second")

    assert observation["after"] == {"opacity": 0, "height": 84}
    assert "error" not in observation
    verdict = tf.decide(
        {
            "id": "pattern-tabs-click",
            "trigger": "click .tab-second",
            "target": ".tabs-container",
            "animation": {"type": "layout-state"},
        },
        {
            "found": True,
            "before": {"opacity": 1, "height": 42},
            "after": observation["after"],
        },
        set(),
    )
    assert verdict["status"] == "pass", verdict


@pytest.mark.parametrize("selector", [".wrong-tab", ".missing-tab"])
def test_wrong_or_missing_declared_click_selector_does_not_fallback(selector: str) -> None:
    observation = _run_click_probe(selector)

    assert observation["after"] == {"opacity": 1, "height": 42}
    assert observation["error"] == f"declared click trigger not found: {selector}"
    verdict = tf.decide(
        {
            "id": "pattern-tabs-click",
            "trigger": f"click {selector}",
            "target": ".tabs-container",
            "animation": {"type": "layout-state"},
        },
        {
            "found": True,
            "before": {"opacity": 1, "height": 42},
            "after": observation["after"],
            "driverError": observation["error"],
        },
        set(),
    )
    assert verdict["status"] == "fail", verdict


def test_driver_error_cannot_pass_on_independent_target_motion() -> None:
    verdict = tf.decide(
        {
            "id": "pattern-tabs-click",
            "trigger": "click .missing-tab",
            "target": ".tabs-container",
            "animation": {"type": "layout-state"},
        },
        {
            "found": True,
            "before": {"opacity": 1, "height": 42},
            "after": {"opacity": 0, "height": 84},
            "driverError": "declared click trigger not found: .missing-tab",
        },
        set(),
    )

    assert verdict["status"] == "fail", verdict
    assert "trigger drive failed" in verdict["observed"]
    assert '"driverError": a.get("error")' in SCRIPT.read_text(encoding="utf-8")


def test_null_trigger_selector_falls_back_to_selector_in_trigger_text(
    tmp_path: Path,
) -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    marker = 'ENTRIES_B64=$(run_py - "$SPEC" <<\'PY\'\n'
    start = source.index(marker) + len(marker)
    code = source[start : source.index("\nPY\n)", start)]
    spec = tmp_path / "transition-spec.json"
    spec.write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "pattern-tabs-click",
                        "trigger": "click .tab-second",
                        "triggerSelector": None,
                        "target": ".tabs-container",
                        "animation": {"type": "layout-state"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(spec)],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
        cwd=SCRIPT.parents[3],
    )
    rows = json.loads(base64.b64decode(proc.stdout))

    assert rows[0]["triggerSelector"] == ".tab-second"


def test_click_selector_is_extracted_from_trigger_text() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'match = re.match(r"^\\s*click\\s+([.#\\[].+)$"' in source
    assert '"triggerSelector": trigger_selector' in source
