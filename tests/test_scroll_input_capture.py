"""Navigation-time input evidence preserves declared listener semantics."""

import json
import subprocess
from pathlib import Path


def test_root_input_registrations_preserve_passivity_and_do_not_change_handlers() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/extract/capture-scroll-init.js"
    result = subprocess.run(
        ["node", "-e", """
const fs = require('fs');
const calls = [];
global.EventTarget = class { addEventListener(...args) { calls.push(args); } };
global.window = new EventTarget();
global.document = new EventTarget();
document.documentElement = new EventTarget();
document.body = new EventTarget();
eval(fs.readFileSync(process.argv[1], 'utf8'));
const callback = () => {};
const options = {passive: false};
window.addEventListener('touchmove', callback, options);
window.addEventListener('touchmove', callback, options);
document.addEventListener('touchmove', callback, {passive: true});
document.addEventListener('wheel', callback);
document.body.addEventListener('keydown', callback, false);
new EventTarget().addEventListener('touchmove', callback, options);
window.addEventListener('resize', callback);
console.log(JSON.stringify({rows: window.__uiCloneScrollInputListeners,
  unchanged: calls.length === 7 && calls[0][1] === callback && calls[0][2] === options}));
""", str(script)],
        capture_output=True, text=True, check=True, timeout=10,
    )
    data = json.loads(result.stdout)
    assert data["unchanged"] is True
    assert data["rows"] == [
        {"target": "window", "type": "touchmove", "declaredPassive": False},
        {"target": "document", "type": "touchmove", "declaredPassive": True},
        {"target": "document", "type": "wheel", "declaredPassive": None},
        {"target": "body", "type": "keydown", "declaredPassive": None},
    ]
