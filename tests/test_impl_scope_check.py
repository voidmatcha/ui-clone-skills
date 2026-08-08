from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "impl-scope-check.sh"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


def test_reference_capture_summary_not_flagged_outside_scope(tmp_path: Path) -> None:
    """A `<component>-clean/html/_summary.json` reference-capture sidecar is
    produced by the capture pipeline, not an agent impl edit — impl-scope must
    not flag it as outside-iteration-scope."""
    repo = tmp_path / "repo"
    (repo / "impl").mkdir(parents=True)
    ref = tmp_path / "refdir"
    ref.mkdir()
    (repo / "impl" / ".gitkeep").write_text("", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "baseline")

    def run() -> dict:
        subprocess.run(["bash", str(SCRIPT), str(ref), str(repo / "impl")],
                       capture_output=True, text=True, timeout=120)
        art: dict = json.loads((ref / "impl-scope.json").read_text())
        return art

    run()  # first call initializes the baseline SHA
    # Capture pipeline drops a reference summary sidecar (untracked).
    (repo / "realfood-clean" / "html").mkdir(parents=True)
    (repo / "realfood-clean" / "html" / "_summary.json").write_text("{}", encoding="utf-8")

    art = run()
    offenders = [v["path"] for v in art.get("violations", [])]
    assert not any("_summary.json" in p for p in offenders), (
        f"reference-capture summary must be exempt, got violations: {offenders}"
    )
    assert art["status"] != "fail"
