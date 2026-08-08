"""verify-H1: scroll-end-completion must FAIL CLOSED on an empty probe result.

The per-viewport probe is a monolithic async eval that snapshots the full DOM at
four viewports. On the ~25s eval budget it can time out and return empty stdout.
The downstream `JSON.parse(RAW || '{}')` then yielded stuck:[] -> STUCK_COUNT=0
-> "✅ settled" and exit 0, certifying a probe that never ran. Same class as the
F3 reveal-trigger fix. An empty/unparseable result must fail closed (exit 2), a
genuine "no stuck elements" run (valid JSON, stuck:[]) must still pass.

Mirrors the reveal-trigger harness: a fake agent-browser on PATH returns the
probe output we choose per subcommand.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1]
          / "skills" / "visual-debug" / "scripts" / "scroll-end-completion-check.sh")


def _fake_agent_browser(eval_output: str) -> str:
    return f"""#!/usr/bin/env bash
set -uo pipefail
if [ "${{1:-}}" = "--session" ]; then shift 2; fi
cmd="${{1:-}}"; shift || true
case "$cmd" in
  set|navigate|close) exit 0 ;;
  eval)
    printf '%s' {eval_output!r}
    exit 0
    ;;
esac
exit 0
"""


def _run(tmp_path: Path, eval_output: str) -> tuple[subprocess.CompletedProcess, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "agent-browser").write_text(_fake_agent_browser(eval_output), encoding="utf-8")
    (bin_dir / "sleep").chmod(0o755)
    (bin_dir / "agent-browser").chmod(0o755)

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["VIEWPORTS"] = "1280x800"
    env["WAIT_MS"] = "0"

    proc = subprocess.run(
        ["bash", str(SCRIPT), "scroll-end-test", "https://example.test/", str(ref_dir)],
        check=False, capture_output=True, env=env, text=True, timeout=120,
    )
    return proc, ref_dir / "scroll-completion.json"


def test_empty_probe_output_fails_closed(tmp_path: Path) -> None:
    proc, artifact = _run(tmp_path, eval_output="")
    assert proc.returncode != 0, (
        "empty scroll-end probe (eval timeout) must fail closed, not report settled; "
        f"stdout={proc.stdout!r}"
    )
    assert "settled" not in proc.stdout.split("\n")[-3:][0] if proc.stdout else True
    if artifact.exists():
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        assert payload["status"] != "pass"


def test_valid_probe_no_stuck_is_genuine_pass(tmp_path: Path) -> None:
    out = json.dumps({"maxScroll": 5000, "candidates": 2, "stuck": []})
    proc, artifact = _run(tmp_path, eval_output=out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"


def test_valid_probe_with_stuck_still_fails(tmp_path: Path) -> None:
    out = json.dumps({"maxScroll": 5000, "candidates": 2, "stuck": [".hero"]})
    proc, artifact = _run(tmp_path, eval_output=out)
    assert proc.returncode == 1, f"a real stuck element must fail (exit 1): {proc.stdout}"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "fail"
