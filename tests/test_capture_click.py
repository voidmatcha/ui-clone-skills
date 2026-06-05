"""Tests for scripts/extract/capture-click.sh.

Click capture must use real agent-browser click commands in isolated sessions.
External/navigation clicks are guarded and classified; only same-page clicks
can claim observed DOM mutation.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "extract" / "capture-click.sh"


def _make_fake_agent_browser(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "agent-browser"
    state = tmp_path / "fake-state"
    state.mkdir()
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"STATE='{state}'\n"
        f"echo \"$@\" >> '{tmp_path / 'calls.log'}'\n"
        "shift 2\n"
        "cmd=\"$1\"\n"
        "shift || true\n"
        "case \"$cmd\" in\n"
        "  open)\n"
        "    echo \"$1\" > \"$STATE/current_url\"\n"
        "    exit 0\n"
        "    ;;\n"
        "  eval)\n"
        "    count=$(cat \"$STATE/eval_count\" 2>/dev/null || echo 0)\n"
        "    count=$((count + 1))\n"
        "    echo \"$count\" > \"$STATE/eval_count\"\n"
        "    if [ \"$count\" = \"1\" ]; then\n"
        "      printf '%s\\n' '{\"candidates\":[{\"id\":\"ext\",\"name\":\"External\",\"selector\":\"a.external\",\"triggerType\":\"click-navigation\",\"href\":\"https://outside.test/\"},{\"id\":\"mailto\",\"name\":\"Email\",\"selector\":\"a.mail\",\"triggerType\":\"click-navigation\",\"href\":\"mailto:team@example.test\"},{\"id\":\"modal\",\"name\":\"Modal\",\"selector\":\"button.modal\",\"triggerType\":\"click-toggle\",\"href\":\"\"}]}'\n"
        "    else\n"
        "      clicked=$(cat \"$STATE/last_clicked\" 2>/dev/null || true)\n"
        "      if [ \"$clicked\" = \"button.modal\" ]; then\n"
        "        printf '%s\\n' '{\"url\":\"https://example.test/\",\"bodyClass\":\"modal-open\",\"htmlClass\":\"\",\"domHash\":222,\"domLength\":180,\"visibleTextHash\":2}'\n"
        "      else\n"
        "        printf '%s\\n' '{\"url\":\"https://example.test/\",\"bodyClass\":\"idle\",\"htmlClass\":\"\",\"domHash\":111,\"domLength\":100,\"visibleTextHash\":1}'\n"
        "      fi\n"
        "    fi\n"
        "    exit 0\n"
        "    ;;\n"
        "  click)\n"
        "    echo \"$1\" > \"$STATE/last_clicked\"\n"
        "    if [ \"$1\" = \"a.external\" ]; then\n"
        "      echo 'https://outside.test/' > \"$STATE/current_url\"\n"
        "    fi\n"
        "    exit 0\n"
        "    ;;\n"
        "  get)\n"
        "    cat \"$STATE/current_url\" 2>/dev/null || echo 'https://example.test/'\n"
        "    exit 0\n"
        "    ;;\n"
        "  back)\n"
        "    echo 'https://example.test/' > \"$STATE/current_url\"\n"
        "    exit 0\n"
        "    ;;\n"
        "  wait|close)\n"
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    return bin_dir


def _run_capture_click(ref_dir: Path, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return subprocess.run(
        [str(SCRIPT), "https://example.test/", "sess1", str(ref_dir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )


def test_click_capture_uses_isolated_agent_browser_sessions_and_navigation_guard(
    tmp_path: Path,
) -> None:
    ref_dir = tmp_path / "ref"
    bin_dir = _make_fake_agent_browser(tmp_path)

    proc = _run_capture_click(ref_dir, bin_dir)
    assert proc.returncode == 0, proc.stderr

    click_dir = ref_dir / "states" / "click"
    manifest = json.loads((click_dir / "manifest.json").read_text())
    assert len(manifest["entries"]) == 3

    external_entry = next(entry for entry in manifest["entries"] if entry["id"] == "ext")
    mailto_entry = next(entry for entry in manifest["entries"] if entry["id"] == "mailto")
    modal_entry = next(entry for entry in manifest["entries"] if entry["id"] == "modal")
    assert external_entry["navigationType"] == "external"
    assert mailto_entry["navigationType"] == "non-http-navigation"
    assert modal_entry["navigationType"] == "same-page"

    external = json.loads((click_dir / external_entry["file"]).read_text())
    assert external["navigationOnly"] is True
    assert external["guard"]["isolatedSession"] is True
    assert external["guard"]["restored"] is True
    assert external["domMutation"]["changed"] is False

    mailto = json.loads((click_dir / mailto_entry["file"]).read_text())
    assert mailto["navigationOnly"] is True
    assert mailto["declaredOnly"] is True
    assert mailto["eventDriver"] == "agent-browser.click.skipped"
    assert mailto["guard"]["skippedReason"] == "non-http-scheme:mailto"

    modal = json.loads((click_dir / modal_entry["file"]).read_text())
    assert modal["navigationOnly"] is False
    assert modal["domMutation"]["changed"] is True
    assert modal["bodyClassAfter"] == "modal-open"

    calls = (tmp_path / "calls.log").read_text()
    assert "--session sess1-click-discovery open https://example.test/" in calls
    assert "--session sess1-click-0 open https://example.test/" in calls
    assert "--session sess1-click-1 open https://example.test/" not in calls
    assert "--session sess1-click-1 click a.mail" not in calls
    assert "--session sess1-click-2 open https://example.test/" in calls
    assert "--session sess1-click-0 click a.external" in calls
    assert "--session sess1-click-0 back" in calls or (
        "--session sess1-click-0 open https://example.test/" in calls
    )
