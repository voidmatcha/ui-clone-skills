from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/extract/section-clips.sh"
OWNED_DOCS = (
    ROOT / "skills/ui-capture/SKILL.md",
    ROOT / "skills/ui-capture/capture-transitions.md",
    ROOT / "skills/visual-debug/verification.md",
)


def test_section_clips_uses_selector_then_path(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "agent-browser"
    log_path = tmp_path / "calls.jsonl"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with Path(os.environ["AGENT_BROWSER_CALLS"]).open("a") as log:
    log.write(json.dumps(args) + "\\n")

command_index = args.index("--session") + 2
command = args[command_index]
if command == "eval":
    source = args[command_index + 1]
    if "Collect all major sections" in source:
        print('[{"name":"hero","y":0,"height":600,"width":1440}]')
    elif "Key element selectors" in source:
        print('[{"name":"cta","tag":"button","selector":"[data-ui-clone-clip-id=clip-0]","x":20,"y":40,"width":120,"height":40}]')
elif command == "screenshot":
    Path(args[-1]).write_bytes(b"png")
""",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    output_dir = tmp_path / "output"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["AGENT_BROWSER_CALLS"] = str(log_path)
    result = subprocess.run(
        ["bash", str(SCRIPT), "test-session", str(output_dir), "ref"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    element_path = str(output_dir / "clips/ref/elements/00-cta.png")
    expected_call = [
        "--session",
        "test-session",
        "screenshot",
        "[data-ui-clone-clip-id=clip-0]",
        element_path,
    ]
    assert [call for call in calls if call[-1:] == [element_path]] == [expected_call]


def test_owned_capture_surfaces_do_not_prescribe_clip_flag() -> None:
    for path in (SCRIPT, *OWNED_DOCS):
        assert "--clip" not in path.read_text(encoding="utf-8"), path
