import json
import os
import subprocess
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


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
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [{
            "id": "bundle-impl-coverage",
            "script": "skills/visual-debug/scripts/bundle-impl-coverage-check.sh",
            "produces": "bundle-impl-coverage.json",
            "reason": "test",
            "severity": "block",
        }],
    }))

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
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
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "bundle-impl-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["implPkgJson"] == str(impl / "package.json")


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
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "text": "Real Studio Work",
            "children": [],
        },
    }))
    (ref / "verification-plan.json").write_text(json.dumps({
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
    }))

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
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "unbound variable" not in proc.stderr
    assert (ref / "text-fidelity-check.json").is_file(), proc.stdout + proc.stderr
    assert (ref / "dom-mirror-check.json").is_file(), proc.stdout + proc.stderr


def test_run_required_checks_has_hero_composite_signature() -> None:
    """codex-18 (2026-05-22) discovered hero-composite-check.sh was added to
    verification-plan.sh as a required row but never wired into the dispatcher
    SIGNATURES table — dispatcher emitted NOSIG and skipped, forcing codex to
    invoke the gate manually. Regression: every script referenced by
    verification-plan rows MUST have a SIGNATURES entry, or the dispatcher
    silently drops the gate.
    """
    root = _project_root()
    text = (root / "scripts" / "verify" / "run-required-checks.sh").read_text()
    assert '"hero-composite-check.sh"' in text, (
        "hero-composite-check.sh missing from dispatcher SIGNATURES — "
        "dispatcher will NOSIG-skip and the gate won't run automatically."
    )


def test_run_required_checks_has_anti_cheat_signatures() -> None:
    """Every required anti-cheat row emitted by verification-plan.sh must
    have a dispatcher signature. Missing signatures make the one-shot
    verifier stop before producing the artifacts gate.py expects.
    """
    root = _project_root()
    text = (root / "scripts" / "verify" / "run-required-checks.sh").read_text()
    for script in (
        "bundle-paste-check.sh",
    ):
        assert f'"{script}"' in text, f"{script} missing from dispatcher SIGNATURES"
