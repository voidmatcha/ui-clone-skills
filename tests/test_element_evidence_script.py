from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_element_evidence_script_is_extensionless_agent_browser_probe() -> None:
    script = Path("scripts/extract/element-evidence.sh").read_text(encoding="utf-8")

    assert "agent-browser --session \"$SESSION\" eval --json \"$EVAL_JS\"" in script
    assert "(() => {" in script
    assert "document.querySelector(selector)" in script
    assert "selectorCandidatesFor(element)" in script
    assert ":nth-of-type(" in script
    assert "data-testid" in script
    assert "getComputedStyle(element)" in script
    assert "truncateValue(computed[key]" in script
    assert "truncateValue(attr.value" in script
    assert '"inset"' in script
    assert "document.getAnimations()" in script
    assert "transitionDuration" in script
    assert "outerHTML" not in script
    assert "chrome." not in script


def test_element_evidence_embedded_eval_is_valid_javascript(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to syntax-check the embedded browser eval")

    script = Path("scripts/extract/element-evidence.sh").read_text(encoding="utf-8")
    start = script.index("(() => {")
    end = script.index("\nJS\n", start)
    js = script[start:end].replace("${SELECTOR_JSON}", '"footer"')
    js_path = tmp_path / "element-evidence.js"
    js_path.write_text(js, encoding="utf-8")

    result = subprocess.run(["node", "--check", str(js_path)], check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
