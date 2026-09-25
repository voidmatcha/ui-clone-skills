"""Launcher guards for tailwind-transform-conflict-check.sh."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "visual-debug"
    / "scripts"
    / "tailwind-transform-conflict-check.sh"
)


def _write_fake_agent_browser(bin_dir: Path, log_path: Path) -> None:
    fake = bin_dir / "agent-browser"
    fake.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {str(log_path)!r}
case " $* " in
  *" eval "*)
    if [ "${{TAILWIND_FAKE_EVAL_FAIL:-}}" = "1" ]; then
      echo "fake eval failed" >&2
      exit 42
    fi
    echo '"[]"'
    ;;
  *) echo '{{}}' ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _base_env(bin_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return env


def test_system_bash_runs_probe_without_bad_substitution(
    tmp_path: Path,
) -> None:
    """System Bash 3.2 must not expand JS/template syntax before browser eval."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "agent-browser.log"
    _write_fake_agent_browser(bin_dir, log_path)
    ref = tmp_path / "ref"
    ref.mkdir()
    env = _base_env(bin_dir)

    proc = subprocess.run(
        [
            "/bin/bash",
            str(SCRIPT),
            "tw-bash32",
            "http://impl.invalid",
            "1440",
            "900",
            '[data-project="${component}"]',
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No Tailwind" in proc.stdout
    assert "bad substitution" not in proc.stderr


def test_scope_with_js_template_marker_reaches_browser_eval(
    tmp_path: Path,
) -> None:
    """Selector text that contains `${...}` must not be expanded by Bash."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "agent-browser.log"
    _write_fake_agent_browser(bin_dir, log_path)
    env = _base_env(bin_dir)
    scope = '[data-project="${component}"]'

    proc = subprocess.run(
        [
            "/bin/bash",
            str(SCRIPT),
            "tw-scope",
            "http://impl.invalid",
            "1440",
            "900",
            scope,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    log = log_path.read_text(encoding="utf-8")
    assert '${component}' in log
    assert '[data-project=\\"${component}\\"]' in log


def test_browser_eval_failure_exits_setup_error(tmp_path: Path) -> None:
    """A browser-side probe failure must not collapse into no-conflict PASS."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "agent-browser.log"
    _write_fake_agent_browser(bin_dir, log_path)
    env = _base_env(bin_dir)
    env["TAILWIND_FAKE_EVAL_FAIL"] = "1"

    proc = subprocess.run(
        [
            "/bin/bash",
            str(SCRIPT),
            "tw-eval-fail",
            "http://impl.invalid",
            "1440",
            "900",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "probe failed during browser eval" in proc.stderr
    assert "fake eval failed" in proc.stderr
    assert "No Tailwind" not in proc.stdout


def _write_parity_agent_browser(
    bin_dir: Path, impl_conflicts: list[dict[str, str]], ref_conflicts: list[dict[str, str]]
) -> None:
    (bin_dir / "impl.json").write_text(json.dumps(json.dumps(impl_conflicts)), encoding="utf-8")
    (bin_dir / "ref.json").write_text(json.dumps(json.dumps(ref_conflicts)), encoding="utf-8")
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "session=''\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--session" ]; then session="$2"; shift 2; continue; fi\n'
        '  if [ "$1" = "eval" ]; then\n'
        f'    case "$session" in *-ref) cat "{bin_dir / "ref.json"}" ;; '
        f'      *) cat "{bin_dir / "impl.json"}" ;; esac\n'
        "    exit 0\n"
        "  fi\n"
        "  shift\n"
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _conflict(*, scale: str = "0.9", x: int = 100) -> dict[str, str]:
    return {
        "tag": "DIV",
        "id": "",
        "cls": "fc-snail-autoplay fc-snail-a",
        "transform": f"matrix(1, 0, 0, 1, {x}, 0)",
        "translate": "0px 20%",
        "rotate": "none",
        "scale": scale,
        "matchKey": "div.fc-snail-a.fc-snail-autoplay",
    }


def test_reference_matching_composition_is_not_reported_as_tailwind_conflict(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_parity_agent_browser(bin_dir, [_conflict(x=691)], [_conflict(x=512)])
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "head.json").write_text(
        json.dumps({"sourceUrl": "https://reference.invalid/"}), encoding="utf-8"
    )
    env = _base_env(bin_dir)
    env.update({"REF_DIR": str(ref_dir), "WAIT_MS": "0"})

    proc = subprocess.run(
        ["/bin/bash", str(SCRIPT), "tw-reference", "http://impl.invalid"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    artifact = json.loads((ref_dir / "tailwind-conflict.json").read_text(encoding="utf-8"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert artifact["status"] == "pass"
    assert artifact["referenceMatchCount"] == 1
    assert artifact["conflicts"] == []


def test_reference_with_different_individual_scale_keeps_conflict_failure(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_parity_agent_browser(bin_dir, [_conflict(scale="0.9")], [_conflict(scale="1")])
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "head.json").write_text(
        json.dumps({"sourceUrl": "https://reference.invalid/"}), encoding="utf-8"
    )
    env = _base_env(bin_dir)
    env.update({"REF_DIR": str(ref_dir), "WAIT_MS": "0"})

    proc = subprocess.run(
        ["/bin/bash", str(SCRIPT), "tw-reference-mismatch", "http://impl.invalid"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    artifact = json.loads((ref_dir / "tailwind-conflict.json").read_text(encoding="utf-8"))
    assert proc.returncode == 1
    assert artifact["status"] == "fail"
    assert artifact["conflictCount"] == 1
    assert artifact["referenceMatchCount"] == 0
