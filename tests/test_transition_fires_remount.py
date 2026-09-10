"""Execute the shipped browser probe against scroll-driven target replacement."""
from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/visual-debug/scripts/transition-fires-check.sh"


def _probe(*, initially_marked: bool, disappears: bool = False) -> dict:
    source = SCRIPT.read_text()
    assignment = source[source.index('PHASE2_TEMPLATE="'):source.index('\nENTRY_COUNT=', source.index('PHASE2_TEMPLATE="'))]
    entry = {"id": "label", "target": "#label", "kind": "scrub", "prop": "transform", "trigger": "scroll"}
    env = dict(os.environ, ENTRIES_B64=base64.b64encode(json.dumps([entry]).encode()).decode(), SETTLE_MS="250", SNAP_JS="const snap = () => ({});")
    proc = subprocess.run(["bash", "-c", assignment + '''\nprintf '%s' "$PHASE2_TEMPLATE"'''], env=env, capture_output=True, text=True, check=True, timeout=10)
    js = proc.stdout.replace("__CHUNK_JSON__", "[0]")
    harness = r"""
let current, generation = 0;
const makeNode = marked => ({
 isConnected: true, offsetHeight: 20,
 attrs: marked ? {'data-tf-idxs':'0'} : {},
 getAttribute(k) { return this.attrs[k] || ''; },
 setAttribute(k,v) { this.attrs[k]=v; },
 getBoundingClientRect() { return {top: 0, width: 100, height: 20}; },
 querySelectorAll() {return [];}
});
current = makeNode(INITIALLY_MARKED);
global.window = {innerHeight:800, scrollY:0, scrollTo(x,y) {
 window.scrollY=y; generation++;
 if (current) current.isConnected=false;
 current=DISAPPEARS ? null : makeNode(false);
}};
global.document = {
 documentElement:{scrollHeight:4800,className:''},
 querySelector(s) {return s === '#label' ? current : null;},
 querySelectorAll(s) {return current && current.attrs['data-tf-idxs'] ? [current] : [];}
};
global.getComputedStyle = node => {
 if (!node.isConnected) throw Error('detached target sampled');
 return {transform:'matrix(1,0,0,1,0,' + window.scrollY + ')',opacity:'1',filter:'none',animationName:'none'};
};
global.setTimeout = f => {f();};
Promise.resolve(PROBE).then(x=>console.log(x));
"""
    harness = harness.replace("INITIALLY_MARKED", json.dumps(initially_marked)).replace("DISAPPEARS", json.dumps(disappears)).replace("PROBE", js)
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True, timeout=10)
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    return parsed


@pytest.mark.parametrize("initially_marked", [False, True])
def test_scroll_target_is_reacquired_before_chunk_and_after_each_scroll(initially_marked: bool) -> None:
    result = _probe(initially_marked=initially_marked)
    assert "0" in result, "remount discarded the phase-one marker"
    assert not result["0"].get("error"), result
    assert len({sample["transform"] for sample in result["0"]["samples"]}) > 1


def test_disappeared_target_does_not_use_detached_or_unrelated_element() -> None:
    result = _probe(initially_marked=True, disappears=True)
    assert result["0"].get("error"), result
    assert not result["0"].get("samples"), result


def test_wheel_snapshot_has_no_dependency_on_scrub_local_binding() -> None:
    source = SCRIPT.read_text()
    start = source.index("out[t.idx] = { transform:")
    snapshot = source[start:source.index(";", start)]
    result = subprocess.run(["node", "-e", """
const out={}, t={idx:0}, cs={opacity:'1'}, rr={}, sig='', csig='';
const el={getAttribute:()=> 'wheel-target'}, window={scrollY:1};
const smoothEngine=false, engineDriven=false, animRunning=false;
""" + snapshot + ";console.log(out[0].cls)"], capture_output=True, text=True, check=True, timeout=10)
    assert result.stdout.strip() == 'wheel-target'
