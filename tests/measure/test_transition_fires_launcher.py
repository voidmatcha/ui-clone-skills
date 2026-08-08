"""Launcher-level guards for transition-fires-check.sh.

These tests exercise the shell wrapper before the real browser probes start:
interpreter selection and fail-loud spec parsing must not silently collapse into
the legitimate "no transition-spec entries" 0/0 PASS path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "visual-debug"
    / "scripts"
    / "transition-fires-check.sh"
)


def _write_fake_agent_browser(bin_dir: Path) -> None:
    fake = bin_dir / "agent-browser"
    fake.write_text(
        """#!/usr/bin/env bash
case " $* " in
  *" eval "*) echo '"{}"' ;;
  *) echo '{}' ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _base_env(bin_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.pop("PYTHONPATH", None)
    return env


def _run(ref: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "t-sess", "http://impl.invalid", str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def test_malformed_transition_spec_exits_setup_error(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text("{not-json", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agent_browser(bin_dir)

    proc = _run(ref, _base_env(bin_dir))

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "transition-spec.json" in proc.stderr
    assert "0/0 transitions fire" not in proc.stdout
    assert not (ref / "transition-fires.json").exists()


def test_unreadable_transition_spec_exits_setup_error(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agent_browser(bin_dir)

    proc = _run(ref, _base_env(bin_dir))

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "transition-spec.json" in proc.stderr
    assert "0/0 transitions fire" not in proc.stdout
    assert not (ref / "transition-fires.json").exists()


def test_uses_uv_run_python_when_path_python_cannot_import_ui_clone(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": []}),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agent_browser(bin_dir)
    (bin_dir / "python3").write_text(
        """#!/usr/bin/env bash
echo "fake python3 cannot import ui_clone" >&2
exit 77
""",
        encoding="utf-8",
    )
    (bin_dir / "python3").chmod(0o755)
    uv_log = tmp_path / "uv.log"
    (bin_dir / "uv").write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {str(uv_log)!r}
if [ "$1" = "run" ] && [ "$2" = "python" ]; then
  shift 2
  exec {sys.executable!r} "$@"
fi
exit 64
""",
        encoding="utf-8",
    )
    (bin_dir / "uv").chmod(0o755)

    env = _base_env(bin_dir)
    env.pop("VIRTUAL_ENV", None)
    proc = _run(ref, env)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "0/0 transitions fire" in proc.stdout
    assert uv_log.read_text(encoding="utf-8").startswith("run python")


def test_classify_import_failure_exits_setup_error(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": []}),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agent_browser(bin_dir)
    plugin_root = tmp_path / "plugin"
    (plugin_root / "ui_clone" / "gates").mkdir(parents=True)
    (plugin_root / "ui_clone" / "__init__.py").write_text("", encoding="utf-8")
    (plugin_root / "ui_clone" / "gates" / "__init__.py").write_text(
        "",
        encoding="utf-8",
    )

    env = _base_env(bin_dir)
    env["PLUGIN_ROOT"] = str(plugin_root)
    proc = _run(ref, env)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "could not find a Python interpreter" in proc.stderr
    assert "0/0 transitions fire" not in proc.stdout
    assert not (ref / "transition-fires.json").exists()
