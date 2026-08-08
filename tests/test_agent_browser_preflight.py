"""agent-browser-preflight.sh pure-logic guards.

ab_reap's global steps (close --all, pkill agent-browser-chrome) are destructive
and daemon-dependent, so they are NOT exercised here. Instead this locks the
pure, CI-safe helpers the reap/health path is built from:
  - _ab_prune_engines : stale *.engine pruning, scoped to a temp home
  - _ab_health_verdict: readyState string -> healthy/unhealthy verdict
  - ab_reap UI_CLONE_AB_REAP=0 : the disable path is a true no-op (returns
    before any destructive step and prunes nothing)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "skills" / "visual-debug" / "scripts" / "lib" / "agent-browser-preflight.sh"


def _bash(script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full = f'set -euo pipefail\nsource "{LIB}"\n{script}\n'
    base = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    if env:
        base.update(env)
    return subprocess.run(
        ["bash", "-c", full], capture_output=True, text=True, timeout=120,
        check=False, env=base,
    )


def test_prune_engines_removes_only_engine_files(tmp_path: Path) -> None:
    for name in ("a.engine", "b.engine", "c.engine"):
        (tmp_path / name).write_text("x")
    (tmp_path / "keep.json").write_text("{}")
    proc = _bash(f'_ab_prune_engines "{tmp_path}"')
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "3", proc.stdout
    assert not list(tmp_path.glob("*.engine")), "all .engine files pruned"
    assert (tmp_path / "keep.json").exists(), "non-engine files untouched"


def test_prune_engines_empty_dir_returns_zero(tmp_path: Path) -> None:
    proc = _bash(f'_ab_prune_engines "{tmp_path}"')
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "0", proc.stdout


def test_health_verdict_accepts_readystate_values() -> None:
    for good in ('complete', '"complete"', 'interactive', 'loading', '"loading"'):
        proc = _bash(f'_ab_health_verdict {good!r}')
        assert proc.returncode == 0, f"{good!r} should be healthy: {proc.stderr}"


def test_health_verdict_rejects_empty_and_errors() -> None:
    for bad in ('', 'undefined', 'Failed to read: os error 35', 'null'):
        proc = _bash(f'_ab_health_verdict {bad!r}')
        assert proc.returncode == 1, f"{bad!r} should be unhealthy"


def test_reap_disabled_is_noop(tmp_path: Path) -> None:
    """UI_CLONE_AB_REAP=0 must return early — no prune, no destructive step."""
    (tmp_path / "live.engine").write_text("x")
    proc = _bash(
        'ab_reap',
        env={"UI_CLONE_AB_REAP": "0", "AGENT_BROWSER_HOME": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "live.engine").exists(), "disabled reap must prune nothing"
