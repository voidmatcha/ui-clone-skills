from __future__ import annotations

import json
import os
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


def _make_fake_agent_browser(tmp_path: Path, eval_payload: str) -> Path:
    """Fake `agent-browser` that echoes a fixed eval payload on `eval`."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "cmd=\"\"\n"
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    --session) shift 2 ;;\n'
        '    eval) cmd="eval"; shift; break ;;\n'
        '    *) shift ;;\n'
        "  esac\n"
        "done\n"
        'if [ "$cmd" = "eval" ]; then\n'
        f"  echo '{eval_payload}'\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return bin_dir


def test_element_evidence_rejects_non_page_origin(tmp_path: Path) -> None:
    """An about:blank envelope must fail closed instead of writing evidence.

    element-evidence.sh produces an artifact with no verdict of its own, so a
    lost page target would otherwise be published as empty evidence.
    """
    payload = json.dumps(
        {"success": True, "data": {"origin": "about:blank", "result": {}}}
    ).replace("'", "'\\''")
    bin_dir = _make_fake_agent_browser(tmp_path, payload)
    out = tmp_path / "evidence.json"

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    script = Path(__file__).resolve().parents[1] / "scripts" / "extract" / "element-evidence.sh"
    proc = subprocess.run(
        [str(script), "sess1", ".hero", str(out)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert proc.returncode == 3, f"{proc.stdout}\n{proc.stderr}"
    assert "lost the page target" in proc.stderr
    assert not out.exists()


def test_element_evidence_rejects_failure_envelope(tmp_path: Path) -> None:
    """A `success: false` envelope carries no page evidence, so it must not be
    published. Only the data.origin shape was checked before, so an explicit
    failure envelope passed straight through."""
    payload = json.dumps(
        {"success": False, "error": "target closed"}
    ).replace("'", "'\\''")
    bin_dir = _make_fake_agent_browser(tmp_path, payload)
    out = tmp_path / "evidence.json"

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    script = Path(__file__).resolve().parents[1] / "scripts" / "extract" / "element-evidence.sh"
    proc = subprocess.run(
        [str(script), "sess1", ".hero", str(out)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert proc.returncode == 3, f"{proc.stdout}\n{proc.stderr}"
    assert "reported failure" in proc.stderr
    assert not out.exists()
