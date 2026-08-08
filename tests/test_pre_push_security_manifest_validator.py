"""Tests for the standalone OpenAI agent-manifest validator."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "pre-push-security.sh"
HELPER = ROOT / "scripts" / "ci" / "validate_openai_agent_manifests.py"
SKILLS = ("ui-reverse-engineering", "ui-capture", "visual-debug")
VALID_MANIFEST = """\
interface:
  display_name: Example
  short_description: Example skill
  default_prompt: Use the skill
policy:
  allow_implicit_invocation: true
"""


def _write_manifests(root: Path) -> None:
    for skill in SKILLS:
        path = root / "skills" / skill / "agents" / "openai.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(VALID_MANIFEST, encoding="utf-8")


def test_pre_push_security_uses_standalone_manifest_validator() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "python3 - <<'PY'" not in source
    assert 'python3 "$REPO_ROOT/scripts/ci/validate_openai_agent_manifests.py"' in source


def test_manifest_validator_accepts_complete_public_manifests(tmp_path: Path) -> None:
    _write_manifests(tmp_path)

    proc = subprocess.run(
        ["python3", str(HELPER), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert proc.returncode == 0, proc.stderr


def test_manifest_validator_rejects_missing_required_field(tmp_path: Path) -> None:
    _write_manifests(tmp_path)
    path = (
        tmp_path
        / "skills"
        / "visual-debug"
        / "agents"
        / "openai.yaml"
    )
    path.write_text(
        VALID_MANIFEST.replace("  default_prompt: Use the skill\n", ""),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["python3", str(HELPER), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert proc.returncode == 1
    assert "missing interface.default_prompt" in proc.stderr
