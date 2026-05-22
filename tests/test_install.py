from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"


def _extract_shell_quote() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(r"^shell_quote\(\) \{\n(?:.*\n)*?^\}\n", text, re.MULTILINE)
    assert match is not None, "shell_quote helper not found in install.sh"
    return match.group(0)


def test_codex_install_does_not_register_working_repo_as_marketplace() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert 'codex plugin marketplace add "$REPO_ROOT"' not in text
    assert 'codex plugin marketplace add $(shell_quote "$REPO_ROOT")' not in text


def test_codex_install_uses_personal_projection_marketplace() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert 'CODEX_PERSONAL_MARKETPLACE="$HOME/.agents/plugins/marketplace.json"' in text
    assert 'CODEX_PLUGIN_DIR="$HOME/plugins/$PLUGIN_NAME"' in text
    assert 'CODEX_PLUGIN_SOURCE_PATH="./plugins/$PLUGIN_NAME"' in text
    assert 'codex plugin add "$PLUGIN_NAME@$CODEX_MARKETPLACE_NAME"' in text


def test_shell_quote_produces_copy_paste_safe_codex_command() -> None:
    helper = _extract_shell_quote()
    cases = {
        "/path/with spaces/repo": "codex plugin marketplace add '/path/with spaces/repo'",
        "/tmp/owner's repo; $(whoami) & data": "codex plugin marketplace add '/tmp/owner'\\''s repo; $(whoami) & data'",
    }

    for path, expected in cases.items():
        script = f"""
{helper}
REPO_ROOT={shlex.quote(path)}
printf "codex plugin marketplace add %s\\n" "$(shell_quote "$REPO_ROOT")"
"""
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout == f"{expected}\n"
