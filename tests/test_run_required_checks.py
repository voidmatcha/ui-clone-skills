import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ui_clone.check_inputs import compute_check_input_hash, sidecar_path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _dispatcher_source() -> str:
    root = _project_root()
    shell = root / "scripts" / "verify" / "run-required-checks.sh"
    helper = root / "scripts" / "verify" / "build_required_dispatch.py"
    return shell.read_text() + "\n" + helper.read_text()


def _valid_runtime_text_artifact(ref_url: str, impl_url: str) -> dict:
    def receipt(url: str) -> dict:
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        assert parsed.hostname is not None
        origin = f"{parsed.scheme.lower()}://{parsed.hostname.lower()}"
        if parsed.port and parsed.port not in {80, 443}:
            origin += f":{parsed.port}"
        return {
            "requestedUrl": url,
            "openUrl": url,
            "actualUrl": url,
            "analysisUrl": url,
            "analysisOrigin": origin,
            "responseStatus": 200,
            "readyState": "complete",
            "navigationType": "navigate",
            "errorDocument": False,
            "batchCommandCount": 6,
            "attempt": 1,
            "closeAttempts": 1,
            "closed": True,
        }

    record = {
        "slot": "main>p:nth-of-type(1)::run(1)",
        "text": "Copy",
        "tag": "P",
        "initialViewport": False,
    }
    capture = {
        "blockCount": 1,
        "blocks": ["Copy"],
        "records": [record],
        "samples": [[record], [record]],
        "phaseSampleStartIndex": 0,
    }
    return {
        "schemaVersion": 1,
        "status": "pass",
        "refUrl": ref_url,
        "implUrl": impl_url,
        "actualRefUrl": ref_url,
        "actualImplUrl": impl_url,
        "captureReceipt": {
            "ref": receipt(ref_url),
            "impl": receipt(impl_url),
        },
        "thresholds": {
            "minOrderedSimilarity": 0.85,
            "maxMissingRatio": 0.15,
            "maxMissingBlocks": 1,
        },
        "ref": capture,
        "impl": capture,
        "phaseVariance": {"accepted": False, "reason": "exact-match"},
        "comparison": {
            "lcsLength": 1,
            "orderedSimilarity": 1.0,
            "missingCount": 0,
            "missingRatio": 0.0,
            "extraCount": 0,
        },
        "violations": [],
    }


def _write_python_recorder(tmp_path: Path, real_python: Path | str) -> tuple[Path, Path]:
    bin_dir = tmp_path / "selected-python"
    bin_dir.mkdir()
    log_path = tmp_path / "selected-python-calls.log"
    wrapper = bin_dir / "python3"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        f"printf '%s\\n' \"$*\" >> {str(log_path)!r}\n"
        f"exec {str(real_python)!r} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper, log_path


def _seed_bundle_check_with_stale_pass(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path]:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text('{"dependencies": {}}\n', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return null}\n",
        encoding="utf-8",
    )
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    bundle_map = ref / "bundle-map.json"
    bundle_map.write_text(
        json.dumps({"chunks": {}, "libraries": {}}),
        encoding="utf-8",
    )
    artifact = ref / "bundle-impl-coverage.json"
    artifact.write_text(
        json.dumps({"schemaVersion": 1, "status": "pass"}),
        encoding="utf-8",
    )
    old_hash = compute_check_input_hash(impl, ref, "bundle-impl-coverage")
    assert old_hash
    sidecar_path(ref, "bundle-impl-coverage").write_text(old_hash, encoding="utf-8")
    old_time = artifact.stat().st_mtime - 10
    os.utime(artifact, (old_time, old_time))
    bundle_map.write_text(
        json.dumps({"chunks": {}, "libraries": {"gsap": True}}),
        encoding="utf-8",
    )
    return ref, impl, bundle_map, artifact


def _make_impl_root(path: Path, text: str = "export default function App(){return null}\n") -> None:
    (path / "src").mkdir(parents=True)
    (path / "package.json").write_text('{"dependencies": {}}\n', encoding="utf-8")
    (path / "src" / "App.tsx").write_text(text, encoding="utf-8")


def test_run_required_checks_passes_package_json_to_bundle_impl_coverage(tmp_path: Path) -> None:
    """Dispatcher must pass impl/package.json, not the impl root directory."""
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"dependencies": {}, "devDependencies": {}}))
    (impl / "src" / "App.tsx").write_text("export default function App() { return null; }\n")
    (ref / ".impl-root").write_text(str(impl) + "\n")
    (ref / "bundle-map.json").write_text(json.dumps({"chunks": {}, "libraries": {}}))
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "bundle-impl-coverage",
                        "script": "skills/visual-debug/scripts/bundle-impl-coverage-check.sh",
                        "produces": "bundle-impl-coverage.json",
                        "reason": "test",
                        "severity": "block",
                    }
                ],
            }
        )
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    python_wrapper, python_log = _write_python_recorder(tmp_path, sys.executable)
    env["PYTHON_BIN"] = str(python_wrapper)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "bundle-dispatch-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "bundle-impl-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["implPkgJson"] == str(impl / "package.json")
    python_calls = python_log.read_text(encoding="utf-8")
    assert "scripts/verify/build_required_dispatch.py" in python_calls
    assert "bundle_impl_coverage.py" in python_calls
    assert sidecar_path(ref, "bundle-impl-coverage").read_text(
        encoding="utf-8"
    ) == compute_check_input_hash(impl, ref, "bundle-impl-coverage")


def test_run_required_checks_rejects_python_bin_below_minimum(tmp_path: Path) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text('{"dependencies": {}}\n', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return null}\n",
        encoding="utf-8",
    )
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []}),
        encoding="utf-8",
    )
    fake_python = tmp_path / "python3.9"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${1:-}\" = \"-c\" ]; then\n"
        "  case \"${2:-}\" in *'raise SystemExit'*) exit 1 ;; esac\n"
        "  printf '%s\\n' '3.9.6'\n"
        "  exit 0\n"
        "fi\n"
        "exit 99\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    env["PYTHON_BIN"] = str(fake_python)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "python-minimum-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 2
    assert "requires Python >=3.11" in proc.stderr
    assert str(fake_python) in proc.stderr


def test_run_required_checks_child_rows_inherit_selected_python_first_on_path(
    tmp_path: Path,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _make_impl_root(impl)
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    producer = tmp_path / "child-python-producer.sh"
    producer.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "python3 -c 'import json, sys; "
        'open(sys.argv[1], "w", encoding="utf-8").write('
        'json.dumps({"schemaVersion": 1, "status": "pass"}))'
        "' \"$1/child-python.json\"\n",
        encoding="utf-8",
    )
    producer.chmod(0o755)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "child-python-path",
                        "script": str(producer),
                        "argsRecipe": "{ref_dir}",
                        "produces": "child-python.json",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    python_wrapper, python_log = _write_python_recorder(tmp_path, sys.executable)
    env["PYTHON_BIN"] = str(python_wrapper)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "child-python-path-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "child-python.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"
    python_calls = python_log.read_text(encoding="utf-8")
    assert "scripts/verify/build_required_dispatch.py" in python_calls
    assert "child-python.json" in python_calls


def test_run_required_checks_prefers_virtualenv_python_for_dispatch_and_children(
    tmp_path: Path,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _make_impl_root(impl)
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    producer = tmp_path / "venv-python-producer.sh"
    producer.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "python3 -c 'import json, sys; "
        'open(sys.argv[1], "w", encoding="utf-8").write('
        'json.dumps({"schemaVersion": 1, "status": "pass"}))'
        "' \"$1/venv-python.json\"\n",
        encoding="utf-8",
    )
    producer.chmod(0o755)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "venv-python-path",
                        "script": str(producer),
                        "argsRecipe": "{ref_dir}",
                        "produces": "venv-python.json",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    fake_host_bin = tmp_path / "fake-host-bin"
    fake_host_bin.mkdir()
    fake_host_python = fake_host_bin / "python3"
    fake_host_python.write_text(
        "#!/usr/bin/env bash\n"
        "echo host-python-must-not-run >&2\n"
        "exit 99\n",
        encoding="utf-8",
    )
    fake_host_python.chmod(0o755)
    fake_venv = tmp_path / "fake-venv"
    fake_venv.mkdir()
    python_wrapper, python_log = _write_python_recorder(
        fake_venv,
        sys.executable,
    )
    assert python_wrapper == fake_venv / "selected-python" / "python3"
    venv_bin = fake_venv / "bin"
    venv_bin.mkdir()
    venv_python = venv_bin / "python3"
    venv_python.symlink_to(python_wrapper)

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    env.pop("PYTHON_BIN", None)
    env["VIRTUAL_ENV"] = str(fake_venv)
    env["PATH"] = f"{fake_host_bin}{os.pathsep}{env['PATH']}"
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "venv-python-path-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "venv-python.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"
    python_calls = python_log.read_text(encoding="utf-8")
    assert "scripts/verify/build_required_dispatch.py" in python_calls
    assert "venv-python.json" in python_calls
    assert "host-python-must-not-run" not in proc.stderr


def test_run_required_checks_persists_effective_impl_root_override(
    tmp_path: Path,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    old_impl = tmp_path / "old-impl"
    new_impl = tmp_path / "new-impl"
    ref.mkdir()
    _make_impl_root(old_impl)
    _make_impl_root(new_impl)
    old_terminal = {
        "status": "failed",
        "category": "operator-stop",
        "gate": "post-implement",
        "reason": "preserve me",
    }
    (ref / ".impl-root").write_text(str(old_impl) + "\n", encoding="utf-8")
    (old_impl / ".ref-dir").write_text(str(ref) + "\n", encoding="utf-8")
    (ref / "pipeline-state.json").write_text(
        json.dumps(
            {
                "component": "ref",
                "current_gate": "post-implement",
                "implRoot": str(old_impl),
                "impl_root": str(old_impl),
                "terminalState": old_terminal,
                "terminal_state": old_terminal,
            }
        ),
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []}),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    env["UI_CLONE_IMPL_ROOT"] = str(new_impl)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "impl-binding-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert Path((ref / ".impl-root").read_text(encoding="utf-8").strip()).resolve() == new_impl.resolve()
    state = json.loads((ref / "pipeline-state.json").read_text(encoding="utf-8"))
    assert Path(state["implRoot"]).resolve() == new_impl.resolve()
    assert Path(state["impl_root"]).resolve() == new_impl.resolve()
    assert state["terminalState"] == old_terminal
    assert state["terminal_state"] == old_terminal
    assert Path((new_impl / ".ref-dir").read_text(encoding="utf-8").strip()).resolve() == ref.resolve()

    resolver_env = os.environ.copy()
    resolver_env["PLUGIN_ROOT"] = str(root)
    resolver_env.pop("UI_CLONE_IMPL_ROOT", None)
    resolved = subprocess.run(
        ["bash", str(root / "scripts" / "extract" / "find-impl-root.sh"), str(ref)],
        cwd=root,
        env=resolver_env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert resolved.returncode == 0, resolved.stdout + resolved.stderr
    assert Path(resolved.stdout.splitlines()[0]).resolve() == new_impl.resolve()


def test_run_required_checks_fails_closed_on_corrupt_pipeline_state(
    tmp_path: Path,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _make_impl_root(impl)
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    (ref / "pipeline-state.json").write_text("{not-json", encoding="utf-8")
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []}),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    env["UI_CLONE_IMPL_ROOT"] = str(impl)
    command = [
        "bash",
        str(root / "scripts" / "verify" / "run-required-checks.sh"),
        "impl-binding-corrupt-state-test",
        "https://example.test",
        "http://127.0.0.1:1",
        str(ref),
    ]
    first = subprocess.run(
        command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    second = subprocess.run(
        command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert first.returncode == 2
    assert second.returncode == 2
    assert "failed to persist impl binding" in first.stderr
    assert "failed to persist impl binding" in second.stderr
    assert "pipeline-state.json" in first.stderr
    assert "pipeline-state.json" in second.stderr
    assert not (ref / "pipeline-state.json").exists()
    assert list(ref.glob("pipeline-state.json.corrupt.*"))


def test_run_required_checks_allows_valid_state_with_old_corrupt_quarantine(
    tmp_path: Path,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _make_impl_root(impl)
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    (ref / "pipeline-state.json").write_text(
        json.dumps({"component": "ref", "current_gate": "post-implement"}),
        encoding="utf-8",
    )
    (ref / "pipeline-state.json.corrupt.20260803T000000Z").write_text(
        "{old-corrupt",
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": []}),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    env["UI_CLONE_IMPL_ROOT"] = str(impl)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "impl-binding-valid-with-quarantine-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    state = json.loads((ref / "pipeline-state.json").read_text(encoding="utf-8"))
    assert Path(state["implRoot"]).resolve() == impl.resolve()
    assert (ref / "pipeline-state.json.corrupt.20260803T000000Z").exists()


def test_run_required_checks_replaces_stale_pass_sidecar_with_fresh_fail(
    tmp_path: Path,
) -> None:
    from ui_clone.gates.verification_plan import _registered_check_is_stale

    root = _project_root()
    ref, impl, _bundle_map, artifact = _seed_bundle_check_with_stale_pass(tmp_path)
    producer = tmp_path / "fresh-fail-producer.sh"
    producer.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "printf '%s\\n' "
        "'{\"schemaVersion\":1,\"status\":\"fail\",\"violations\":[{\"kind\":\"missing-dep\"}]}' "
        "> \"$1/bundle-impl-coverage.json\"\n"
        "exit 1\n",
        encoding="utf-8",
    )
    producer.chmod(0o755)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "bundle-impl-coverage",
                        "script": str(producer),
                        "argsRecipe": "{ref_dir}",
                        "produces": "bundle-impl-coverage.json",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "fresh-fail-sidecar-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert json.loads(artifact.read_text(encoding="utf-8"))["status"] == "fail"
    assert sidecar_path(ref, "bundle-impl-coverage").read_text(
        encoding="utf-8"
    ) == compute_check_input_hash(impl, ref, "bundle-impl-coverage")
    assert not _registered_check_is_stale(
        ref,
        impl,
        "bundle-impl-coverage",
        artifact,
    )


def test_run_required_checks_drops_stale_sidecar_when_nonzero_leaves_old_artifact(
    tmp_path: Path,
) -> None:
    from ui_clone.gates.verification_plan import _registered_check_is_stale

    root = _project_root()
    ref, impl, _bundle_map, artifact = _seed_bundle_check_with_stale_pass(tmp_path)
    producer = tmp_path / "no-write-fail-producer.sh"
    producer.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    producer.chmod(0o755)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "bundle-impl-coverage",
                        "script": str(producer),
                        "argsRecipe": "{ref_dir}",
                        "produces": "bundle-impl-coverage.json",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "old-artifact-sidecar-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert json.loads(artifact.read_text(encoding="utf-8"))["status"] == "pass"
    assert sidecar_path(ref, "bundle-impl-coverage").read_text(
        encoding="utf-8"
    ) != compute_check_input_hash(impl, ref, "bundle-impl-coverage")
    assert _registered_check_is_stale(
        ref,
        impl,
        "bundle-impl-coverage",
        artifact,
    )


def test_run_required_checks_keeps_fresh_error_artifact_stale(
    tmp_path: Path,
) -> None:
    from ui_clone.gates.verification_plan import _registered_check_is_stale

    root = _project_root()
    ref, impl, _bundle_map, artifact = _seed_bundle_check_with_stale_pass(tmp_path)
    producer = tmp_path / "fresh-error-producer.sh"
    producer.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "printf '%s\\n' "
        "'{\"schemaVersion\":1,\"status\":\"error\",\"violations\":[{\"kind\":\"crash\"}]}' "
        "> \"$1/bundle-impl-coverage.json\"\n"
        "exit 2\n",
        encoding="utf-8",
    )
    producer.chmod(0o755)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "bundle-impl-coverage",
                        "script": str(producer),
                        "argsRecipe": "{ref_dir}",
                        "produces": "bundle-impl-coverage.json",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "fresh-error-sidecar-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert json.loads(artifact.read_text(encoding="utf-8"))["status"] == "error"
    assert sidecar_path(ref, "bundle-impl-coverage").read_text(
        encoding="utf-8"
    ) != compute_check_input_hash(impl, ref, "bundle-impl-coverage")
    assert _registered_check_is_stale(
        ref,
        impl,
        "bundle-impl-coverage",
        artifact,
    )


def test_run_required_checks_accepts_and_caches_valid_partial_hover(
    tmp_path: Path,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    _make_impl_root(impl)
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    (ref / "regions.json").write_text("{}\n", encoding="utf-8")
    (ref / "hover-css-rules.json").write_text("{}\n", encoding="utf-8")
    (ref / "asset-substitution.json").write_text("{}\n", encoding="utf-8")
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "00-auto-hover-2",
                        "trigger": "hover",
                        "target": ".nav-link-arrow",
                    },
                    {
                        "id": "01-auto-hover-3",
                        "trigger": "hover",
                        "target": ".nav-link",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    producer = tmp_path / "partial-hover-producer.sh"
    producer.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        'ref="$1"\n'
        'mode="$2"\n'
        'mkdir -p "$ref/transitions"\n'
        'case "$mode" in\n'
        "  fires)\n"
        "    cat > \"$ref/transition-fires.json\" <<'EOF'\n"
        '{"status":"pass","total":2,"fired":2,"known_skip":0,'
        '"failed":0,"unmeasurable":0,"entries":['
        '{"id":"00-auto-hover-2","trigger":"hover","type":"css-hover",'
        '"kind":"hover","status":"pass"},'
        '{"id":"01-auto-hover-3","trigger":"hover","type":"css-hover",'
        '"kind":"hover","status":"pass"}]}\n'
        "EOF\n"
        "    ;;\n"
        "  compare)\n"
        "    cat > \"$ref/transitions/result.txt\" <<'EOF'\n"
        "Transition compare: 2 PASS, 0 FAIL\n"
        "✅ PASS .first\n"
        "✅ PASS .second\n"
        "EOF\n"
        "    ;;\n"
        "  hover)\n"
        "    cat > \"$ref/transitions/hover-state-result.txt\" <<'EOF'\n"
        "# hover-state-compare\n"
        "## auto-hover-2 (hover) [single]\n"
        "selector: .nav-link-arrow\n"
        "## auto-hover-3 (hover) [single]\n"
        "selector: .nav-link\n"
        "✅ auto-hover-2 clean [single]\n"
        "⚠️ auto-hover-3 unmeasurable-after-retry [single] — status 2\n"
        "hover-fallback: status=pass verified=0 static=4 failed=0\n"
        "# coverage: measured=2 failed=0 unmeasurable=1 fallbackFailed=0\n"
        "⚠️ 1/2 hover target-run(s) unmeasurable\n"
        "EOF\n"
        '    printf "run\\n" >> "$ref/hover-runs.log"\n'
        "    exit 2\n"
        "    ;;\n"
        "  proof)\n"
        '    printf \'%s\\n\' \'{"schemaVersion":1,"status":"pass"}\' '
        '> "$ref/transition-proof.json"\n'
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    producer.chmod(0o755)
    rows = [
        ("transition-fires", "transition-fires.json", "fires"),
        ("transition-compare", "transitions/result.txt", "compare"),
        (
            "hover-state-compare",
            "transitions/hover-state-result.txt",
            "hover",
        ),
        ("transition-proof", "transition-proof.json", "proof"),
    ]
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": check_id,
                        "script": str(producer),
                        "argsRecipe": f"{{ref_dir}} {mode}",
                        "produces": produces,
                        "severity": "block",
                    }
                    for check_id, produces, mode in rows
                ],
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    command = [
        "bash",
        str(root / "scripts" / "verify" / "run-required-checks.sh"),
        "partial-hover-dispatch-test",
        "https://example.test",
        "http://127.0.0.1:1",
        str(ref),
    ]
    first = subprocess.run(
        command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    second = subprocess.run(
        command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert "valid partial evidence" in first.stdout
    assert (ref / "hover-runs.log").read_text(encoding="utf-8") == "run\n"
    expected_hash = compute_check_input_hash(impl, ref, "hover-state-compare")
    assert expected_hash
    assert sidecar_path(ref, "hover-state-compare").read_text(
        encoding="utf-8"
    ) == expected_hash


def test_hover_state_cache_helper_accepts_complete_semantic_result(
    tmp_path: Path,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    artifact = ref / "transitions" / "hover-state-result.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        "# hover-state-compare\n"
        "## primary-button (hover) [single]\n"
        "selector: .primary-button\n"
        "✅ primary-button clean [single]\n"
        "hover-fallback: status=pass verified=0 static=0 failed=0\n"
        "# coverage: measured=1 failed=0 unmeasurable=0 fallbackFailed=0\n"
        "✅ all 1 measured hover target-run(s) within SSIM threshold; "
        "fallback probe covered the rest\n",
        encoding="utf-8",
    )
    helper = root / "scripts" / "verify" / "run_required_helpers.py"

    complete = subprocess.run(
        [sys.executable, str(helper), "hover-state-valid", str(ref), str(artifact)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    partial_only = subprocess.run(
        [
            sys.executable,
            str(helper),
            "hover-state-partial-valid",
            str(ref),
            str(artifact),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert complete.returncode == 0, complete.stdout + complete.stderr
    assert "PASS 1/1" in complete.stdout
    assert partial_only.returncode == 1


def test_required_text_cache_helper_uses_canonical_plan_gate(
    tmp_path: Path,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    artifact = ref / "transitions" / "result.txt"
    artifact.parent.mkdir(parents=True)
    _make_impl_root(impl)
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "00-card-hover",
                        "trigger": "hover",
                        "target": ".card",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "transition-compare",
                        "script": (
                            "skills/visual-debug/scripts/transition-compare.sh"
                        ),
                        "produces": "transitions/result.txt",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    artifact.write_text(
        "Transition compare: 1 PASS, 0 FAIL\n"
        "✅ PASS .card\n",
        encoding="utf-8",
    )
    fingerprint = compute_check_input_hash(impl, ref, "transition-compare")
    assert fingerprint
    sidecar_path(ref, "transition-compare").write_text(
        fingerprint,
        encoding="utf-8",
    )
    helper = root / "scripts" / "verify" / "run_required_helpers.py"

    proc = subprocess.run(
        [
            sys.executable,
            str(helper),
            "required-check-reusable",
            str(ref),
            "transition-compare",
            str(artifact),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("pass\t")


def test_section_cache_helper_rejects_missing_footer(
    tmp_path: Path,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    artifact = ref / "sections" / "result.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---:|---:|---|---|\n"
        "| header | 0 | 0 | ok | ✅ |\n"
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 0 UNMEASURED**\n",
        encoding="utf-8",
    )
    helper = root / "scripts" / "verify" / "run_required_helpers.py"
    command = [
        sys.executable,
        str(helper),
        "section-compare-reusable",
        str(ref),
        str(artifact),
    ]

    valid = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    artifact.write_text(
        "# section-compare multi-viewport result\nviewport: 375x812\n",
        encoding="utf-8",
    )
    truncated = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert valid.stdout.startswith("pass\t")
    assert truncated.returncode == 1


def test_run_required_checks_materializes_text_and_dom_artifacts(tmp_path: Path) -> None:
    """Dispatcher must pass --out for stdout-capable text/dom scripts."""
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"dependencies": {}}))
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <main>Real Studio Work</main>}\n",
        encoding="utf-8",
    )
    (ref / ".impl-root").write_text(str(impl) + "\n")
    (ref / "dom-scaffold.json").write_text(
        json.dumps(
            {
                "tree": {
                    "tag": "main",
                    "text": "Real Studio Work",
                    "children": [],
                },
            }
        )
    )
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "text-fidelity-check",
                        "script": "skills/visual-debug/scripts/text-fidelity-check.sh",
                        "produces": "text-fidelity-check.json",
                        "reason": "test",
                        "severity": "block",
                    },
                    {
                        "id": "dom-mirror-check",
                        "script": "skills/visual-debug/scripts/dom-mirror-check.sh",
                        "produces": "dom-mirror-check.json",
                        "reason": "test",
                        "severity": "warn",
                    },
                ],
            }
        )
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "text-dom-dispatch-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "unbound variable" not in proc.stderr
    assert (ref / "text-fidelity-check.json").is_file(), proc.stdout + proc.stderr
    assert (ref / "dom-mirror-check.json").is_file(), proc.stdout + proc.stderr


def test_run_required_checks_materializes_capacity_and_impl_url_guard(tmp_path: Path) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"dependencies": {}}))
    (impl / "src" / "App.tsx").write_text("export default function App(){return null}\n")
    (ref / ".impl-root").write_text(str(impl) + "\n")
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "capacity-probe",
                        "script": "scripts/verify/capacity-check.sh",
                        "produces": "capacity-report.json",
                        "reason": "test",
                        "severity": "block",
                    },
                    {
                        "id": "impl-url-guard",
                        "script": "scripts/verify/impl-url-guard.sh",
                        "produces": "impl-url-guard.json",
                        "reason": "test",
                        "severity": "block",
                    },
                ],
            }
        )
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "guard-dispatch-test",
            "https://example.test",
            "https://impl.example.test",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((ref / "capacity-report.json").read_text())["status"] == "pass"
    assert json.loads((ref / "impl-url-guard.json").read_text())["status"] == "skip"


def test_run_required_checks_skips_dependents_after_failed_dependency(tmp_path: Path) -> None:
    """A failed prerequisite must suppress browser-backed dependent rows.

    This protects the canonical flow from producing misleading parity artifacts
    after impl-url-guard/runtime-env has already proven the target is invalid.
    """
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"dependencies": {}}))
    (impl / "src" / "App.tsx").write_text("export default function App(){return null}\n")
    (ref / ".impl-root").write_text(str(impl) + "\n")
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "impl-url-guard",
                        "script": "scripts/verify/impl-url-guard.sh",
                        "produces": "impl-url-guard.json",
                        "reason": "test",
                        "severity": "block",
                    },
                    {
                        "id": "runtime-dom-parity",
                        "script": "skills/visual-debug/scripts/runtime-dom-parity-check.sh",
                        "produces": "runtime-dom-parity.json",
                        "reason": "test",
                        "severity": "block",
                        "dependsOn": ["impl-url-guard"],
                    },
                ],
            }
        )
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "dependency-skip-test",
            "https://example.test",
            "http://127.0.0.1:9",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "runtime-dom-parity: SKIPPED_DEP" in proc.stdout
    assert "depends on failed: impl-url-guard" in proc.stdout
    assert json.loads((ref / "impl-url-guard.json").read_text())["status"] == "fail"
    assert not (ref / "runtime-dom-parity.json").exists()



def test_run_required_checks_has_hero_composite_signature() -> None:
    """codex-18 (2026-05-22) discovered hero-composite-check.sh was added to
    verification-plan.sh as a required row but never wired into the dispatcher
    SIGNATURES table — dispatcher emitted NOSIG and skipped, forcing codex to
    invoke the gate manually. Regression: every script referenced by
    verification-plan rows MUST have a SIGNATURES entry, or the dispatcher
    silently drops the gate.
    """
    text = _dispatcher_source()
    assert '"hero-composite-check.sh"' in text, (
        "hero-composite-check.sh missing from dispatcher SIGNATURES — "
        "dispatcher will NOSIG-skip and the gate won't run automatically."
    )


def test_run_required_checks_has_runtime_text_sequence_signature() -> None:
    """Runtime text parity needs both live URLs and its own browser session."""
    dispatcher = _dispatcher_source()
    assert (
        '"runtime-text-sequence-check.sh":\n'
        '        "{session}-rts {ref_url} {impl_url} {ref_dir}",'
    ) in dispatcher


def test_run_required_checks_shell_has_no_python_heredocs() -> None:
    root = _project_root()
    shell = (root / "scripts" / "verify" / "run-required-checks.sh").read_text()
    dispatch_helper = root / "scripts" / "verify" / "build_required_dispatch.py"
    runtime_helper = root / "scripts" / "verify" / "run_required_helpers.py"
    assert "<<'PY'" not in shell
    assert "build_required_dispatch.py" in shell
    assert "run_required_helpers.py" in shell
    assert dispatch_helper.is_file()
    assert runtime_helper.is_file()
    assert "SIGNATURES = {" in dispatch_helper.read_text()


def test_dispatcher_contains_large_child_heredocs_on_modern_bash(
    tmp_path: Path,
) -> None:
    """Bash 5.1+ child checks use the official tempfile-backed heredoc mode."""
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text('{"name":"impl"}\n', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <main>Copy</main>}\n",
        encoding="utf-8",
    )
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")

    producer = tmp_path / "large-heredoc-producer.sh"
    padding = "# heredoc pipe-capacity regression padding\n" * 8192
    producer.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [ \"${BASH_VERSINFO[0]}\" -gt 5 ] "
        "|| { [ \"${BASH_VERSINFO[0]}\" -eq 5 ] "
        "&& [ \"${BASH_VERSINFO[1]}\" -ge 1 ]; }; then\n"
        "  [ \"${BASH_COMPAT:-}\" = \"5.0\" ] || exit 91\n"
        "fi\n"
        "[ \"${PROBE_ENV:-}\" = \"1\" ] || exit 92\n"
        "python3 - \"$1/heredoc-compat-probe.json\" <<'PY'\n"
        + padding
        + "import json, sys\n"
        + "with open(sys.argv[1], 'w', encoding='utf-8') as fh:\n"
        + "    json.dump({'schemaVersion': 1, 'status': 'pass'}, fh)\n"
        + "PY\n",
        encoding="utf-8",
    )
    producer.chmod(0o755)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "tier": "quick",
                "requiredChecks": [
                    {
                        "id": "heredoc-compat-probe",
                        "script": str(producer),
                        "argsRecipe": "ENV:PROBE_ENV=1 -- {ref_dir}",
                        "produces": "heredoc-compat-probe.json",
                        "severity": "block",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    version = subprocess.run(
        ["bash", "-c", "printf '%s %s' \"${BASH_VERSINFO[0]}\" \"${BASH_VERSINFO[1]}\""],
        check=True,
        capture_output=True,
        text=True,
    )
    major, minor = (int(part) for part in version.stdout.split())
    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    env["RUN_REQUIRED_CHECK_TIMEOUT_SEC"] = "10"
    if (major, minor) >= (5, 1):
        # Prove the dispatcher replaces a pipe-backed parent compatibility mode.
        env["BASH_COMPAT"] = f"{major}.{minor}"
    else:
        env.pop("BASH_COMPAT", None)

    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "heredoc-compat-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "heredoc-compat-probe.json").read_text())
    assert artifact["status"] == "pass"


def test_dispatcher_never_reuses_or_seeds_status_error_artifact(
    tmp_path: Path,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "public").mkdir()
    (impl / "package.json").write_text('{"name":"impl"}\n', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <main>Copy</main>}\n",
        encoding="utf-8",
    )
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    count_path = tmp_path / "dispatch-count"
    producer = tmp_path / "runtime-text-error-producer.sh"
    error_artifact = json.dumps(
        {
            "schemaVersion": 1,
            "status": "error",
            "ref": {"blockCount": 0, "blocks": []},
            "impl": {"blockCount": 0, "blocks": []},
            "comparison": {
                "lcsLength": 0,
                "missingCount": 0,
                "extraCount": 0,
            },
            "violations": [{"kind": "ref-browser-failed"}],
        },
        separators=(",", ":"),
    )
    producer.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        f"count_file={str(count_path)!r}\n"
        "count=0\n"
        "[ -f \"$count_file\" ] && count=$(cat \"$count_file\")\n"
        "printf '%s\\n' \"$((count + 1))\" > \"$count_file\"\n"
        f"printf '%s\\n' {error_artifact!r} "
        "> \"$1/runtime-text-sequence.json\"\n"
        "exit 2\n",
        encoding="utf-8",
    )
    producer.chmod(0o755)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "runtime-text-sequence",
                        "script": str(producer),
                        "argsRecipe": "{ref_dir}",
                        "produces": "runtime-text-sequence.json",
                        "severity": "warn",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    command = [
        "bash",
        str(root / "scripts" / "verify" / "run-required-checks.sh"),
        "error-cache-test",
        "https://example.test",
        "https://impl.example.test",
        str(ref),
    ]
    first = subprocess.run(
        command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    second = subprocess.run(
        command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert count_path.read_text(encoding="utf-8").strip() == "2"
    assert not sidecar_path(ref, "runtime-text-sequence").exists()
    assert "▶ runtime-text-sequence" in first.stdout
    assert "▶ runtime-text-sequence" in second.stdout


def test_dispatcher_runtime_text_url_mismatch_redispatches_without_sidecar(
    tmp_path: Path,
) -> None:
    """File hashes cannot reuse or seed a capture made against another URL."""
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "public").mkdir()
    (impl / "package.json").write_text('{"name":"impl"}\n', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <main>Copy</main>}\n",
        encoding="utf-8",
    )
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    (ref / "dom-scaffold.json").write_text('{"tree":{}}\n', encoding="utf-8")
    (ref / "runtime-text.json").write_text(
        '{"blocks":["Copy"]}\n',
        encoding="utf-8",
    )
    current_ref = "https://Ref.Example.Test:443/path?mode=1#hero"
    current_impl = "http://Impl.Example.Test:80/app"
    wrong_actual_ref = "https://redirected.example.test/path?mode=1#hero"
    mismatched_artifact = {
        "schemaVersion": 1,
        "status": "pass",
        "refUrl": current_ref,
        "implUrl": current_impl,
        "actualRefUrl": wrong_actual_ref,
        "actualImplUrl": current_impl,
        "captureReceipt": {
            "ref": {
                "requestedUrl": current_ref,
                "openUrl": current_ref,
                "actualUrl": wrong_actual_ref,
                "analysisUrl": wrong_actual_ref,
                "analysisOrigin": "https://redirected.example.test",
            },
            "impl": {
                "requestedUrl": current_impl,
                "openUrl": current_impl,
                "actualUrl": current_impl,
                "analysisUrl": current_impl,
                "analysisOrigin": "http://impl.example.test",
            },
        },
        "violations": [],
    }
    artifact_path = ref / "runtime-text-sequence.json"
    artifact_path.write_text(json.dumps(mismatched_artifact), encoding="utf-8")

    fingerprint = compute_check_input_hash(impl, ref, "runtime-text-sequence")
    assert fingerprint
    fingerprint_path = sidecar_path(ref, "runtime-text-sequence")
    fingerprint_path.write_text(fingerprint, encoding="utf-8")

    count_path = tmp_path / "url-mismatch-dispatch-count"
    producer = tmp_path / "runtime-text-mismatched-url-producer.sh"
    producer.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        f"count_file={str(count_path)!r}\n"
        "count=0\n"
        '[ -f "$count_file" ] && count=$(cat "$count_file")\n'
        'printf "%s\\n" "$((count + 1))" > "$count_file"\n'
        f"printf '%s\\n' {json.dumps(mismatched_artifact, separators=(',', ':'))!r} "
        '> "$1/runtime-text-sequence.json"\n',
        encoding="utf-8",
    )
    producer.chmod(0o755)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "runtime-text-sequence",
                        "script": str(producer),
                        "argsRecipe": "{ref_dir}",
                        "produces": "runtime-text-sequence.json",
                        "severity": "warn",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    command = [
        "bash",
        str(root / "scripts" / "verify" / "run-required-checks.sh"),
        "url-cache-test",
        current_ref,
        current_impl,
        str(ref),
    ]
    first = subprocess.run(
        command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    second = subprocess.run(
        command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert count_path.read_text(encoding="utf-8").strip() == "2"
    assert not fingerprint_path.exists()
    assert not (ref / "runtime-text-sequence.provenance.json").exists()
    assert "▶ runtime-text-sequence" in first.stdout
    assert "▶ runtime-text-sequence" in second.stdout


def test_dispatcher_runtime_text_provenance_binds_each_fresh_artifact(
    tmp_path: Path,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "public").mkdir()
    (impl / "package.json").write_text('{"name":"impl"}\n', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <main>Copy</main>}\n",
        encoding="utf-8",
    )
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    (ref / "dom-scaffold.json").write_text('{"tree":{}}\n', encoding="utf-8")
    (ref / "runtime-text.json").write_text(
        '{"blocks":["Copy"]}\n',
        encoding="utf-8",
    )
    ref_url = "https://Ref.Example.Test:443/path"
    impl_url = "http://Impl.Example.Test:80/app"
    artifact = _valid_runtime_text_artifact(ref_url, impl_url)
    count_path = tmp_path / "provenance-dispatch-count"
    producer = tmp_path / "runtime-text-valid-producer.sh"
    producer.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        f"count_file={str(count_path)!r}\n"
        "count=0\n"
        '[ -f "$count_file" ] && count=$(cat "$count_file")\n'
        'printf "%s\\n" "$((count + 1))" > "$count_file"\n'
        f"printf '%s\\n' {json.dumps(artifact, separators=(',', ':'))!r} "
        '> "$1/runtime-text-sequence.json"\n',
        encoding="utf-8",
    )
    producer.chmod(0o755)
    (ref / "verification-plan.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "requiredChecks": [{
                "id": "runtime-text-sequence",
                "script": str(producer),
                "argsRecipe": "{ref_dir}",
                "produces": "runtime-text-sequence.json",
                "severity": "warn",
            }],
        }),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    command = [
        "bash",
        str(root / "scripts" / "verify" / "run-required-checks.sh"),
        "provenance-cache-test",
        ref_url,
        impl_url,
        str(ref),
    ]

    first = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, timeout=120
    )
    second = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, timeout=120
    )

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert count_path.read_text(encoding="utf-8").strip() == "2"
    artifact_path = ref / "runtime-text-sequence.json"
    provenance = json.loads(
        (ref / "runtime-text-sequence.provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["refUrl"] == "https://ref.example.test/path"
    assert provenance["implUrl"] == "http://impl.example.test/app"
    assert provenance["artifactSha256"] == hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()
    assert "▶ runtime-text-sequence" in first.stdout
    assert "▶ runtime-text-sequence" in second.stdout


@pytest.mark.parametrize("strict_source", ("plan", "env"))
def test_dispatcher_strict_failure_is_not_cached(
    tmp_path: Path,
    strict_source: str,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "public").mkdir()
    (impl / "package.json").write_text('{"name":"impl"}\n', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <main>Copy</main>}\n",
        encoding="utf-8",
    )
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    (ref / "dom-scaffold.json").write_text('{"tree":{}}\n', encoding="utf-8")
    (ref / "runtime-text.json").write_text(
        '{"blocks":["Copy"]}\n',
        encoding="utf-8",
    )
    count_path = tmp_path / "strict-failure-dispatch-count"
    producer = tmp_path / "runtime-text-fail-producer.sh"
    producer.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        f"count_file={str(count_path)!r}\n"
        "count=0\n"
        "[ -f \"$count_file\" ] && count=$(cat \"$count_file\")\n"
        "printf '%s\\n' \"$((count + 1))\" > \"$count_file\"\n"
        "printf '%s\\n' "
        "'{\"schemaVersion\":1,\"status\":\"fail\","
        "\"violations\":[{\"kind\":\"missing-text\"}]}' "
        "> \"$1/runtime-text-sequence.json\"\n"
        "exit 2\n",
        encoding="utf-8",
    )
    producer.chmod(0o755)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "strictWarnings": strict_source == "plan",
                "requiredChecks": [
                    {
                        "id": "runtime-text-sequence",
                        "script": str(producer),
                        "argsRecipe": "{ref_dir}",
                        "produces": "runtime-text-sequence.json",
                        "severity": "warn",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    if strict_source == "env":
        env["UI_CLONE_STRICT_WARNINGS"] = "1"
    command = [
        "bash",
        str(root / "scripts" / "verify" / "run-required-checks.sh"),
        "strict-failure-cache-test",
        "https://example.test",
        "https://impl.example.test",
        str(ref),
    ]
    first = subprocess.run(
        command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    second = subprocess.run(
        command,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert first.returncode == 1, first.stdout + first.stderr
    assert second.returncode == 1, second.stdout + second.stderr
    assert count_path.read_text(encoding="utf-8").strip() == "2"
    assert not sidecar_path(ref, "runtime-text-sequence").exists()
    assert "▶ runtime-text-sequence" in first.stdout
    assert "▶ runtime-text-sequence" in second.stdout


@pytest.mark.parametrize("strict_source", ("plan", "env"))
def test_dispatcher_strict_warnings_redispatches_non_pass_artifact(
    tmp_path: Path,
    strict_source: str,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src").mkdir(parents=True)
    (impl / "public").mkdir()
    (impl / "package.json").write_text('{"name":"impl"}\n', encoding="utf-8")
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <main>Copy</main>}\n",
        encoding="utf-8",
    )
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    (ref / "dom-scaffold.json").write_text('{"tree":{}}\n', encoding="utf-8")
    (ref / "runtime-text.json").write_text(
        '{"blocks":["Copy"]}\n',
        encoding="utf-8",
    )
    (ref / "runtime-text-sequence.json").write_text(
        json.dumps({"status": "fail", "violations": [{"kind": "missing-text"}]}),
        encoding="utf-8",
    )
    count_path = tmp_path / "strict-dispatch-count"
    producer = tmp_path / "runtime-text-pass-producer.sh"
    producer.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        f"printf '1\\n' > {str(count_path)!r}\n"
        "printf '%s\\n' '{\"status\":\"pass\",\"violations\":[]}' "
        "> \"$1/runtime-text-sequence.json\"\n",
        encoding="utf-8",
    )
    producer.chmod(0o755)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "strictWarnings": strict_source == "plan",
                "requiredChecks": [
                    {
                        "id": "runtime-text-sequence",
                        "script": str(producer),
                        "argsRecipe": "{ref_dir}",
                        "produces": "runtime-text-sequence.json",
                        "severity": "warn",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    if strict_source == "env":
        env["UI_CLONE_STRICT_WARNINGS"] = "1"
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "strict-warning-cache-test",
            "https://example.test",
            "https://impl.example.test",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert count_path.read_text(encoding="utf-8").strip() == "1"
    assert "▶ runtime-text-sequence" in proc.stdout
    artifact = json.loads((ref / "runtime-text-sequence.json").read_text())
    assert artifact["status"] == "pass"


def test_run_required_checks_has_anti_cheat_signatures() -> None:
    """Every required anti-cheat row emitted by verification-plan.sh must
    have a dispatcher signature. Missing signatures make the one-shot
    verifier stop before producing the artifacts gate.py expects.
    """
    root = _project_root()
    dispatcher = _dispatcher_source()
    plan = (root / "skills" / "visual-debug" / "scripts" / "verification-plan.sh").read_text()
    for script_path, script, artifact in (
        ("scripts/verify/capacity-check.sh", "capacity-check.sh", "capacity-report.json"),
        ("scripts/verify/impl-url-guard.sh", "impl-url-guard.sh", "impl-url-guard.json"),
        ("skills/visual-debug/scripts/blank-viewport-check.sh", "blank-viewport-check.sh", "blank-viewport.json"),
        ("skills/visual-debug/scripts/bundle-paste-check.sh", "bundle-paste-check.sh", "bundle-paste-check.json"),
        ("skills/visual-debug/scripts/geometry-sanity-check.sh", "geometry-sanity-check.sh", "geometry-sanity.json"),
        ("skills/visual-debug/scripts/hover-tree-diff.sh", "hover-tree-diff.sh", "hover-tree-diff.md"),
        ("skills/visual-debug/scripts/live-parity-sweep.sh", "live-parity-sweep.sh", "live-parity.json"),
        ("skills/visual-debug/scripts/mobile-responsive-coverage-check.sh", "mobile-responsive-coverage-check.sh", "mobile-responsive-coverage.json"),
    ):
        assert (root / script_path).is_file(), (
            f"{script} missing on disk — dispatcher would NOSCRIPT-skip it."
        )
        assert f'"{script}"' in dispatcher, (
            f"{script} missing from dispatcher SIGNATURES — dispatcher will NOSIG-skip it."
        )
        assert f'"{script_path}"' in plan, (
            f"{script} missing from verification-plan.sh — row will never be dispatched."
        )
        assert f'"{artifact}"' in plan, (
            f"{artifact} missing from verification-plan.sh — gate output contract is unwired."
        )


def test_run_required_checks_synthesizes_section_compare_row() -> None:
    """post-implement requires sections/result.txt even though verification-plan
    rows cover only requiredChecks. The dispatcher must add section-compare for
    full reference captures so agents do not finish with missing section
    evidence after a one-shot required-check run.
    """
    dispatcher = _dispatcher_source()
    assert '"section-compare"' in dispatcher
    assert "section-compare.sh" in dispatcher
    assert "sections/result.txt" in dispatcher


def test_section_compare_dispatches_before_alignment_consumers(
    tmp_path: Path,
) -> None:
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (ref / "static" / "ref").mkdir(parents=True)
    (ref / "static" / "ref" / "desktop.png").write_bytes(b"png")
    _make_impl_root(impl)
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requiredChecks": [
                    {
                        "id": "alignment-parity",
                        "script": (
                            "skills/visual-debug/scripts/"
                            "alignment-parity-check.sh"
                        ),
                        "produces": "alignment-parity.json",
                        "severity": "block",
                    },
                    {
                        "id": "alignment-sweep",
                        "script": (
                            "skills/visual-debug/scripts/"
                            "alignment-sweep-check.sh"
                        ),
                        "produces": "alignment-sweep.json",
                        "severity": "block",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "PLUGIN_ROOT": str(root),
            "UI_CLONE_DISPATCH_DRY": "1",
            "UI_CLONE_IMPL_ROOT": str(impl),
            "UI_CLONE_VERIFY_TIER": "quick",
        }
    )
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "section-order-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    rows = [line for line in proc.stdout.splitlines() if line.startswith("DRY|")]
    section_index = next(i for i, row in enumerate(rows) if "|section-compare|" in row)
    alignment_indices = [
        i
        for i, row in enumerate(rows)
        if "|alignment-parity|" in row or "|alignment-sweep|" in row
    ]
    assert len(alignment_indices) == 2, rows
    assert section_index < min(alignment_indices), rows


def test_section_compare_verify_path_tier_gated_frozen_wrapper() -> None:
    """Capture-variance determinism (loop-16): the comprehensive tier dispatches
    the 3-pass frozen-ref wrapper (same-frame capture, strict AE KEPT) with its
    own ROW_TIMEOUT_SEC budget; quick/standard tiers keep the fast single-pass
    section-compare with multi-viewport enforcement. The rejected ref-path
    SECTION_REF_CALIB wiring must be gone.
    """
    dispatcher = _dispatcher_source()
    # tier gating selects the frozen wrapper for comprehensive
    assert 'UI_CLONE_VERIFY_TIER' in dispatcher
    assert 'section-compare-frozen.sh' in dispatcher
    assert 'if tier == "comprehensive"' in dispatcher
    # frozen row carries its own timeout (review F3), parsed by the consumer
    assert 'ROW_TIMEOUT_SEC=' in dispatcher
    assert 'row_timeout="${_kv#ROW_TIMEOUT_SEC=}"' in dispatcher
    # multi-viewport enforcement preserved on BOTH paths (the frozen wrapper is
    # viewport-aware): VIEWPORTS is computed once and composed into the frozen ENV
    assert 'viewport_env = [f"VIEWPORTS={\',\'.join(vps)}"]' in dispatcher
    assert '*viewport_env' in dispatcher  # frozen path composes VIEWPORTS in
    # the rejected ref-path quick-calib wiring is removed
    assert 'SECTION_REF_CALIB=1' not in dispatcher


def test_section_compare_frozen_wrapper_exists_and_valid() -> None:
    """The frozen wrapper script must exist, be a 3-pass orchestrator (freeze ref,
    impl-path calib, measure real impl), and surface the F4 missing-calib marker.
    """
    root = _project_root()
    frozen = root / "skills" / "visual-debug" / "scripts" / "section-compare-frozen.sh"
    assert frozen.is_file(), "section-compare-frozen.sh missing"
    body = frozen.read_text()
    assert "RECATCH_REF=1" in body  # pass 1 freeze
    assert "SECTION_SKIP_IMPL_RESIZE=1" in body  # pass 2a calib
    assert "ref-calib-missing.txt" in body  # F4 marker, no silent revert
    # pass 2b measures the REAL impl-url (generalization vs ref/ref selfpass)
    assert '"$IMPL_URL"' in body


def test_dispatcher_reaps_owned_sessions_after_each_row() -> None:
    """Each check owns its viewport/session. Creating a bare shared session only
    to reset its viewport leaks a browser across the entire dispatcher and can
    poison late capture rows. Reap the live unique-prefix family after each row
    instead, using list-first cleanup so absent names are never closed."""
    dispatcher = _dispatcher_source()
    cleanup_call = "cleanup_browser_sessions"
    assert 'bash "$_SCRIPT_DIR/cleanup-sessions.sh" "$SESSION"' in dispatcher
    assert 'agent-browser --session "$SESSION" set viewport' not in dispatcher
    # The final call is inside the dispatch loop (before `done < .dispatch.txt`).
    assert dispatcher.rindex(cleanup_call) < dispatcher.index(
        'done < "$REF_DIR/.run-required-checks-dispatch.txt"'
    )


def _write_cleanup_agent_browser_stub(
    tmp_path: Path,
    *,
    close_removes_session: bool,
) -> Path:
    stub = tmp_path / "agent-browser"
    close_body = 'rm -f "$state"\n' if close_removes_session else ": # keep session\n"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'state="${STUB_STATE:?}"\n'
        'if [ "$1" = "session" ] && [ "$2" = "list" ]; then\n'
        '  if [ -f "$state" ]; then\n'
        '    echo "Active sessions:"\n'
        '    cat "$state"\n'
        "  else\n"
        '    echo "No active sessions."\n'
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "--session" ] && [ "$3" = "close" ]; then\n'
        f"  {close_body}"
        "  exit 1\n"
        "fi\n"
        "exit 99\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def test_cleanup_sessions_accepts_nonzero_close_when_session_vanishes(
    tmp_path: Path,
) -> None:
    """A close failure is provisional if the exact owned prefix disappears."""
    root = _project_root()
    _write_cleanup_agent_browser_stub(tmp_path, close_removes_session=True)
    state = tmp_path / "sessions"
    state.write_text("  race-owned\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    env["STUB_STATE"] = str(state)
    env["UI_CLONE_SESSION_SETTLE_SEC"] = "0"

    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "cleanup-sessions.sh"),
            "race",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nonzero close response settled" in proc.stdout


def test_cleanup_sessions_fails_nonzero_close_when_session_remains(
    tmp_path: Path,
) -> None:
    """A close failure is real if the exact owned prefix remains after settle."""
    root = _project_root()
    _write_cleanup_agent_browser_stub(tmp_path, close_removes_session=False)
    state = tmp_path / "sessions"
    state.write_text("  race-owned\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    env["STUB_STATE"] = str(state)
    env["UI_CLONE_SESSION_SETTLE_SEC"] = "0"

    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "cleanup-sessions.sh"),
            "race",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "session cleanup did not settle for prefix 'race'" in proc.stderr
    assert "race-owned" in proc.stderr


def test_cleanup_sessions_treats_prefix_as_literal(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    close_log = tmp_path / "closed.txt"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    agent_browser = bin_dir / "agent-browser"
    agent_browser.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${1:-}\" = session ] && [ \"${2:-}\" = list ]; then\n"
        "  echo 'Active sessions:'\n"
        "  for name in run.a-child run.a-nested-ref runXa-child other; do\n"
        f"    [ -e {str(state_dir)!r}/$name.closed ] || printf '  %s\\n' \"$name\"\n"
        "  done\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"${1:-}\" = --session ] && [ \"${3:-}\" = close ]; then\n"
        f"  printf '%s\\n' \"$2\" >> {str(close_log)!r}\n"
        f"  touch {str(state_dir)!r}/$2.closed\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    agent_browser.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["UI_CLONE_SESSION_SETTLE_SEC"] = "0"
    cleanup = _project_root() / "scripts" / "verify" / "cleanup-sessions.sh"
    proc = subprocess.run(
        ["bash", str(cleanup), "run.a"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert close_log.read_text(encoding="utf-8").splitlines() == [
        "run.a-child",
        "run.a-nested-ref",
    ]


def test_dispatcher_gives_browser_sweep_rows_scoped_timeouts() -> None:
    """D20 (loop-nvti-0): frame-compare rows scale with page height; on a
    25k-px page transition-compare/hover-state-compare were killed at the
    shared 180s and their FAILs were timeouts, not verdicts. Hover may capture
    five targets, so its larger budget must not weaken the other heavy rows."""
    dispatcher = _dispatcher_source()
    assert "hover-state-compare)" in dispatcher
    assert 'RUN_REQUIRED_HOVER_TIMEOUT_SEC:-1800' in dispatcher
    assert "transition-compare|click-state-compare|video-motion-compare" in dispatcher
    assert 'RUN_REQUIRED_HEAVY_TIMEOUT_SEC:-540' in dispatcher
    # explicit per-row ENV override must still win: the ENV scan runs AFTER
    # both scoped defaults are applied
    hover_at = dispatcher.index("RUN_REQUIRED_HOVER_TIMEOUT_SEC")
    heavy_at = dispatcher.index("RUN_REQUIRED_HEAVY_TIMEOUT_SEC")
    env_scan_at = dispatcher.index('ROW_TIMEOUT_SEC=*) row_timeout=')
    assert hover_at < env_scan_at and heavy_at < env_scan_at


def test_section_compare_ref_root_fallback_for_section_map() -> None:
    """D23 (loop-nvti-1): section-map.json lives only at the ref root; in the
    VIEWPORTS fan-out $DIR is sections/viewports/<WxH>/ so the ground-truth
    override silently skipped and the raw pin-released enumeration became the
    frozen baseline (10 phantom sections, every impl crop mis-mapped). Both
    consumers must carry the REF_ROOT_DIR sibling fallback that
    transition-spec/asset-substitution already have."""
    root = _project_root()
    sc = (root / "skills" / "visual-debug" / "scripts" / "section-compare.sh").read_text()
    fallback = '[ -f "${REF_ROOT_DIR}/section-map.json" ]'
    assert sc.count(fallback) >= 2, (
        "both section-map.json consumers (override + template coverage gate) "
        "need the REF_ROOT_DIR fallback"
    )


def test_frozen_section_row_budget_includes_pass_factor() -> None:
    """L-MEA-9 (loop-ebpb-0): the frozen 3-pass wrapper fans every pass over
    all N viewports; a budget of 600*N (comment said '3 passes x N viewports'
    but the formula encoded only N) process-group-killed the row mid-run on a
    5-viewport comprehensive dispatch, three runs in a row."""
    dispatcher = _dispatcher_source()
    assert "str(800 * 3 * max(1, len(vps)))" in dispatcher
