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
    assert "document.querySelectorAll(selector)" in script
    assert "matchCount !== 1" in script
    assert "visibilityOf(element)" in script
    assert "target_sanity_problems" in script
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
    assert 'python3 "$ORIGIN_VALIDATOR" "$EXPECTED_URL"' in script


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


def _passing_probe(url: str, **overrides: object) -> dict[str, object]:
    """A successful element-evidence.sh eval result (schemaVersion 2)."""
    annotation: dict[str, object] = {
        "id": "element-probe",
        "selector": ".hero",
        "selectorCandidates": [".hero"],
        "text": "Hero",
        "matchCount": 1,
        "bbox": {"x": 0, "y": 0, "width": 1440, "height": 600},
        "visibility": {"display": "block", "visibility": "visible", "opacity": "1", "hiddenBy": None},
        "visible": True,
        "attributes": {},
        "computedStyle": {},
        "timeline": [],
        "animations": [],
    }
    annotation.update(overrides)
    return {"schemaVersion": 2, "ok": True, "url": url, "annotation": annotation}


def _run_element_evidence(tmp_path: Path, result: dict[str, object], *, url: str = "https://example.test") -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run element-evidence.sh against a fake agent-browser returning `result`."""
    payload = json.dumps({"success": True, "data": {"origin": url, "result": result}}).replace("'", "'\\''")
    bin_dir = _make_fake_agent_browser(tmp_path, payload)
    out = tmp_path / "evidence" / "element-target.json"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "LC_ALL": "C", "LANG": "C"}
    script = Path(__file__).resolve().parents[1] / "scripts" / "extract" / "element-evidence.sh"
    proc = subprocess.run([str(script), "sess1", url, ".hero", str(out)], capture_output=True, text=True, env=env, timeout=30)
    return proc, out


def test_element_evidence_writes_schema_2_record_with_target_sanity(tmp_path: Path) -> None:
    proc, out = _run_element_evidence(tmp_path, _passing_probe("https://example.test/"))
    assert proc.returncode == 0, proc.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["schemaVersion"] == 2
    assert record["annotation"]["matchCount"] == 1 and record["annotation"]["visible"] is True


@pytest.mark.parametrize(
    "result, message",
    [
        ({"schemaVersion": 2, "ok": False, "url": "https://example.test/", "selector": ".hero", "matchCount": 0, "error": "selector not found"}, "probe failed (matches: 0): selector not found"),
        ({"schemaVersion": 2, "ok": False, "url": "https://example.test/", "selector": ".hero", "matchCount": 3, "error": "selector matches 3 elements, expected exactly 1"}, "matches 3 elements"),
        (_passing_probe("https://example.test/", bbox={"x": 0, "y": 0, "width": 1440, "height": 0}), "1440x0 is degenerate (minimum 8x8 CSS px)"),
        (_passing_probe("https://example.test/", bbox={"x": 0, "y": 0, "width": 7, "height": 7}), "7x7 is degenerate"),
        (
            _passing_probe(
                "https://example.test/",
                visible=False,
                visibility={"display": "block", "visibility": "visible", "opacity": "0", "hiddenBy": "ancestor <div> opacity:0"},
            ),
            "not visible in the probed state (ancestor <div> opacity:0)",
        ),
    ],
)
def test_element_evidence_refuses_unusable_targets(tmp_path: Path, result: dict[str, object], message: str) -> None:
    """Zero or multiple matches, a degenerate box, or a hidden element never
    become element-target.json; the reason and the open-state advice are printed."""
    proc, out = _run_element_evidence(tmp_path, result)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert message in proc.stderr, proc.stderr
    assert not out.exists()
    if result.get("ok") is True:
        assert "open a trigger-opened container" in proc.stderr


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
        [str(script), "sess1", "https://example.test", ".hero", str(out)],
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
        [str(script), "sess1", "https://example.test", ".hero", str(out)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert proc.returncode == 3, f"{proc.stdout}\n{proc.stderr}"
    assert "reported failure" in proc.stderr
    assert not out.exists()


@pytest.mark.parametrize("consumer", ["inline", "element-default", "element-explicit"])
@pytest.mark.parametrize("receipt_mode", ["valid", "missing", "wrong-session", "wrong-origin"])
def test_extractors_require_bound_navigation_receipt_for_redirects(
    tmp_path: Path, consumer: str, receipt_mode: str,
) -> None:
    requested = "http://example.test/"
    final_url = "https://www.example.test/"
    actual = "https://unrelated.test" if receipt_mode == "wrong-origin" else "https://www.example.test"
    result: dict[str, object] = {"url": final_url, "scripts": [], "skipped": [], "selector": ".hero"}
    if consumer != "inline":
        result = _passing_probe(final_url)
    payload = json.dumps({"success": True, "data": {"origin": actual, "result": result}})
    bin_dir = _make_fake_agent_browser(tmp_path, payload)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "AGENT_BROWSER_NAMESPACE": "receipt-test"}
    receipt = tmp_path / ("custom-navigation.json" if consumer == "element-explicit" else "capture-navigation.json")
    if receipt_mode != "missing":
        receipt.write_text(json.dumps({
            "requestedUrl": requested, "finalUrl": final_url,
            "session": "other" if receipt_mode == "wrong-session" else "sess1",
            "namespace": "receipt-test",
        }))
    scripts = Path(__file__).resolve().parents[1] / "scripts/extract"
    if consumer == "inline":
        command = [str(scripts / "inline-scripts.sh"), "sess1", requested, str(tmp_path)]
        output = tmp_path / "inline-scripts.json"
    else:
        output = tmp_path / "evidence.json"
        command = [str(scripts / "element-evidence.sh"), "sess1", requested, ".hero", str(output)]
        if consumer == "element-explicit":
            command.append(str(receipt))
    proc = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
    assert (proc.returncode == 0) is (receipt_mode == "valid"), proc.stderr
    assert output.exists() is (receipt_mode == "valid")
