"""run-required-checks.sh must dispatch Python rows with the selected PYTHON_BIN."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "scripts" / "verify" / "run_required_helpers.py"
RUNNER = ROOT / "scripts" / "verify" / "run-required-checks.sh"


def _write_python_recorder(tmp_path: Path, real_python: str) -> tuple[Path, Path]:
    bin_dir = tmp_path / "selected-python"
    bin_dir.mkdir()
    log_path = tmp_path / "selected-python-calls.log"
    wrapper = bin_dir / "python3"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        f"printf '%s\\n' \"$*\" >> {str(log_path)!r}\n"
        f"exec {real_python!r} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper, log_path


def _write_impl_root(impl: Path) -> None:
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text('{"dependencies": {}}\n', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return null}\n",
        encoding="utf-8",
    )


def test_python_required_check_row_uses_selected_python_bin(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _write_impl_root(impl)
    (ref / ".impl-root").write_text(f"{impl}\n", encoding="utf-8")

    check_script = tmp_path / "required_check.py"
    check_script.write_text(
        "from __future__ import annotations\n"
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        "ref_dir = Path(sys.argv[1])\n"
        "(ref_dir / 'python-row.json').write_text(\n"
        "    json.dumps({'schemaVersion': 1, 'status': 'pass', 'executable': sys.executable}),\n"
        "    encoding='utf-8',\n"
        ")\n",
        encoding="utf-8",
    )

    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "python-row",
                        "script": str(check_script),
                        "produces": "python-row.json",
                        "argsRecipe": "{ref_dir}",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(ROOT)
    python_wrapper, python_log = _write_python_recorder(tmp_path, sys.executable)
    env["PYTHON_BIN"] = str(python_wrapper)
    proc = subprocess.run(
        [
            "bash",
            str(RUNNER),
            "python-row-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "python-row.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"
    python_calls = python_log.read_text(encoding="utf-8")
    assert str(check_script) in python_calls


def test_interpreter_selection_has_no_helper_subcommand() -> None:
    helper_text = HELPERS.read_text(encoding="utf-8")
    assert "check-interpreter" not in helper_text
    assert "def check_interpreter" not in helper_text
