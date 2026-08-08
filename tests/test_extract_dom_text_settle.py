"""DOM extraction waits for hydrated visible copy before snapshotting."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "extract-dom.sh"


def test_extract_dom_waits_until_visible_headings_have_text(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.jsonl"
    state = tmp_path / "state"
    fake = bin_dir / "agent-browser"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

calls = Path(os.environ["FAKE_CALLS"])
with calls.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
command = args[args.index("--session") + 2]
if command == "wait":
    sys.exit(0)
if command == "eval" and "--stdin" in args:
    print(json.dumps({"tag": "body", "children": []}))
    sys.exit(0)
state = Path(os.environ["FAKE_STATE"])
attempt = int(state.read_text() if state.exists() else "0") + 1
state.write_text(str(attempt), encoding="utf-8")
print(json.dumps("120:2" if attempt == 1 else "140:0"))
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    ref = tmp_path / "ref"
    ref.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_CALLS"] = str(calls)
    env["FAKE_STATE"] = str(state)

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), "settle-test", "body"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    recorded = [
        json.loads(line)
        for line in calls.read_text(encoding="utf-8").splitlines()
    ]
    stdin_index = next(
        index
        for index, call in enumerate(recorded)
        if "eval" in call and "--stdin" in call
    )
    readiness_evals = [
        call
        for call in recorded[:stdin_index]
        if "eval" in call and "--stdin" not in call
    ]
    assert len(readiness_evals) >= 3
    assert any("h1,h2,h3,h4,h5,h6" in call[-1] for call in readiness_evals)
    assert sum(1 for call in recorded[:stdin_index] if "wait" in call) >= 2
