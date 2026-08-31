from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

from ._helpers import (
    _project_root,
)


def test_verification_plan_includes_header_state_runtime_row() -> None:
    """The plan emitter must register header-state-runtime as a required
    check so the dispatcher actually runs it (and so the verify-stamp
    check counts its artifact). Severity must be block — a static
    header is the exact "captured-HTML-only" failure mode the user
    flagged.
    """
    plan_script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    text = plan_script.read_text(encoding="utf-8")
    assert "header-state-runtime" in text, (
        "verification-plan.sh must add the header-state-runtime row"
    )
    # Find the add_check block and check severity.
    import re
    block = re.search(
        r'add_check\s+"header-state-runtime"[\s\S]+?"(block|warn)"',
        text,
    )
    assert block, "header-state-runtime add_check block missing or malformed"
    assert block.group(1) == "block", (
        "header-state-runtime must be severity=block (captured-HTML paste is a real failure)"
    )



def test_verification_plan_includes_runtime_env_block_row() -> None:
    """runtime-env must be severity=block — env traps invalidate every
    downstream gate's verdict, so they need to be the first failure
    surface, not a silent warning.
    """
    plan_script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    text = plan_script.read_text(encoding="utf-8")
    import re
    block = re.search(
        r'add_check\s+"runtime-env"[\s\S]+?"(block|warn)"',
        text,
    )
    assert block, "runtime-env add_check missing or malformed"
    assert block.group(1) == "block", (
        "runtime-env must be severity=block — env traps make downstream "
        "gates produce misleading verdicts"
    )



def test_fix8_verification_plan_dispatches_new_gates() -> None:
    """Fix 8 — verification-plan.sh must dispatch text-fidelity-check and
    dom-mirror-check at tier=quick (static analysis, cheap) with severity=block.
    Without this dispatch the gates exist as scripts but never run as gates.
    """
    plan = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    body = plan.read_text(encoding="utf-8")
    assert 'add_check "text-fidelity-check"' in body, (
        "verification-plan.sh must dispatch text-fidelity-check"
    )
    assert 'add_check "dom-mirror-check"' in body, (
        "verification-plan.sh must dispatch dom-mirror-check"
    )



def test_verification_plan_dispatches_proxy_mirror_check(tmp_path: Path) -> None:
    """proxy/static mirrors must be a universal quick block check."""
    ref = tmp_path / "ref"
    ref.mkdir()

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "UI_CLONE_VERIFY_TIER": "quick"},
    )
    assert proc.returncode == 0, proc.stderr
    plan = json.loads((ref / "verification-plan.json").read_text())
    rows = {c["id"]: c for c in plan["requiredChecks"]}

    assert "proxy-mirror-check" in rows
    assert rows["proxy-mirror-check"]["severity"] == "block"
    assert rows["proxy-mirror-check"]["tier"] == "quick"
    assert rows["proxy-mirror-check"]["produces"] == "proxy-mirror-check.json"


def test_verification_plan_regenerates_when_extraction_is_newer(tmp_path: Path) -> None:
    """An existing verification-plan.json older than extraction artifacts is stale."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "extracted.json").write_text(json.dumps({"sections": []}))
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "generatedAt": "2000-01-01T00:00:00Z",
        "requiredChecks": [],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), "--tier=quick"],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stderr
    assert "verification-plan.json is stale" in proc.stderr
    assert "regenerating." in proc.stderr
    plan = json.loads((ref / "verification-plan.json").read_text())
    assert plan["generatedAt"] != "2000-01-01T00:00:00Z"


def test_verification_plan_staleness_uses_gnu_stat_mtime(tmp_path: Path) -> None:
    """GNU stat accepts `-f` as filesystem stats, so `-c %Y` must run first."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "extracted.json").write_text(json.dumps({"sections": []}))
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "generatedAt": "2000-01-01T00:00:00Z",
                "requiredChecks": [],
            }
        )
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_stat = bin_dir / "stat"
    fake_stat.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "if [ \"$1\" = \"-c\" ]; then echo 1788190486; exit 0; fi",
                "if [ \"$1\" = \"-f\" ]; then",
                "  printf '  File: \"%s\"\\nBlocks: Total: 22202607   Available: 22198511\\n' \"$3\"",
                "  exit 0",
                "fi",
                "exit 1",
            ]
        ),
        encoding="utf-8",
    )
    fake_stat.chmod(fake_stat.stat().st_mode | stat.S_IEXEC)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), "--tier=quick"],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert proc.returncode == 0, proc.stderr
    assert "integer expression expected" not in proc.stderr
    assert "verification-plan.json is stale" in proc.stderr


def test_verification_plan_keeps_anti_cheat_checks_in_universal_baseline(
    tmp_path: Path,
) -> None:
    """Anti-cheat rails must run before signal-gated and advisory rows."""
    ref = tmp_path / "ref"
    ref.mkdir()

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), "--tier=quick"],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stderr
    plan = json.loads((ref / "verification-plan.json").read_text())
    ids = [c["id"] for c in plan["requiredChecks"]]
    rows = {c["id"]: c for c in plan["requiredChecks"]}

    for check_id in ("html-paste", "ref-screenshot-asset", "proxy-mirror-check"):
        assert check_id in rows
        assert rows[check_id]["severity"] == "block"
        assert rows[check_id]["tier"] == "quick"
        assert ids.index(check_id) < ids.index("text-fidelity-check")
