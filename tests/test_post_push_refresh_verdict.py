"""Push-verdict detection in scripts/hooks/post-push-refresh.sh.

Extracts the embedded python verdict snippet directly (rather than running the
full bash script, which always tails into a real `scripts/ci/review.sh` run
against this repo regardless of UI_CLONE_SKIP_POST_PUSH_REFRESH) so these
tests stay fast and target exactly the logic that was fixed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


SCRIPT = _project_root() / "scripts" / "hooks" / "post-push-refresh.sh"


def _extract_verdict_snippet() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"_verdict=\$\(HOOK_INPUT=\"\$input\" python3 -c '\n(.*?)\n'", text, re.S)
    assert match, "verdict python snippet not found — update this test's regex"
    return match.group(1)


def _verdict(payload: dict) -> str:
    snippet = _extract_verdict_snippet()
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"HOOK_INPUT": json.dumps(payload)},
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_multiline_command_with_push_on_a_later_line_is_still_detected() -> None:
    # fable-20260910 follow-up (Codex dev-hook parity review, finding 3): an
    # earlier version printed the raw (possibly multi-line) command on its
    # own line and let bash `sed -n '1p'/'2p'` split it — a command like
    # "git commit ...\ngit push ..." had its push hidden past line 1 and was
    # silently treated as a non-push. The verdict is now a single word
    # computed entirely in python, so this can no longer happen.
    payload = {
        "tool_input": {"command": "git commit -q -m x\ngit push origin main"},
        "exit_code": 0,
    }
    assert _verdict(payload) == "push-ok"


def test_non_push_command_is_not_push() -> None:
    assert _verdict({"tool_input": {"command": "ls -la"}, "exit_code": 0}) == "not-push"


def test_push_with_nonzero_exit_is_unconfirmed() -> None:
    payload = {"tool_input": {"command": "git push origin main"}, "exit_code": 1}
    assert _verdict(payload) == "push-unconfirmed"


def test_push_with_missing_exit_code_is_unconfirmed() -> None:
    # Fail-closed: an unrecognized/absent exit_code field must NOT default to
    # "assume success" (this file's own header: "Triggers only when the tool
    # input was a successful git push").
    payload = {"tool_input": {"command": "git push origin main"}}
    assert _verdict(payload) == "push-unconfirmed"


def test_push_with_nested_tool_response_exit_code() -> None:
    payload = {
        "tool_input": {"command": "git push origin main"},
        "tool_response": {"exit_code": 0},
    }
    assert _verdict(payload) == "push-ok"


# ─── install-source slug derivation ─────────────────────────────────────────


def _extract_slug_helpers() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"# repo-slug-helpers: begin\n(.*?)\n# repo-slug-helpers: end", text, re.S)
    assert match, "repo-slug helper block not found — update this test's markers"
    return match.group(1)


def _resolve_slug(url: str) -> subprocess.CompletedProcess[str]:
    script = f'{_extract_slug_helpers()}\n_resolve_repo_slug "$1"\n'
    return subprocess.run(
        ["bash", "-c", script, "resolve", url],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/fork-owner/ui-clone-skills.git",
        "https://github.com/fork-owner/ui-clone-skills",
        "https://github.com/fork-owner/ui-clone-skills/",
        "http://github.com/fork-owner/ui-clone-skills.git",
        "git@github.com:fork-owner/ui-clone-skills.git",
        "git@github.com:fork-owner/ui-clone-skills",
        "ssh://git@github.com/fork-owner/ui-clone-skills.git",
        "ssh://git@github.com/fork-owner/ui-clone-skills",
    ],
)
def test_repo_slug_is_derived_from_github_url_forms(url: str) -> None:
    proc = _resolve_slug(url)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "fork-owner/ui-clone-skills"
    assert proc.stderr == "", "a derivable GitHub URL must not print the fallback notice"


@pytest.mark.parametrize(
    "url",
    [
        "git@gitlab.example.com:team/ui-clone-skills.git",
        "ssh://git@gitlab.example.com/team/ui-clone-skills.git",
        "https://example.com/team/ui-clone-skills.git",
        "https://github.com/only-owner",
        "https://github.com/owner/repo/extra",
        "/srv/git/ui-clone-skills.git",
    ],
)
def test_non_github_remote_falls_back_to_upstream_with_stderr_notice(url: str) -> None:
    proc = _resolve_slug(url)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "voidmatcha/ui-clone-skills"
    notice_lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert len(notice_lines) == 1, proc.stderr
    assert "falling back to the canonical upstream installer" in notice_lines[0]
    assert url in notice_lines[0]


def test_empty_remote_uses_upstream_default_silently() -> None:
    # No origin at all is the documented last-resort path, not an odd URL.
    proc = _resolve_slug("")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "voidmatcha/ui-clone-skills"
    assert proc.stderr == ""


def test_slug_validation_stays_strict() -> None:
    proc = _resolve_slug("https://github.com/owner/repo;rm -rf x")
    assert proc.stdout.strip() == "voidmatcha/ui-clone-skills"
    assert "falling back" in proc.stderr


# ─── installed-version lookup: marketplace-derived plugin key ───────────────


def _extract_installed_version_snippet() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r'_read_installed_version\(\) \{\n  python3 -c "\n(.*?)\n" "\$1" "\$2"', text, re.S)
    assert match, "_read_installed_version snippet not found — update this test's regex"
    return match.group(1)


def _installed_version(installed_json: Path, repo_root: Path) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", _extract_installed_version_snippet(), str(installed_json), str(repo_root)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _write_installed(path: Path, key: str, version: str) -> None:
    path.write_text(json.dumps({"plugins": {key: [{"version": version}]}}), encoding="utf-8")


def test_installed_version_key_uses_marketplace_name(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "fork-market", "plugins": []}), encoding="utf-8"
    )
    installed = tmp_path / "installed_plugins.json"
    _write_installed(installed, "ui-clone-skills@fork-market", "1.2.3")
    assert _installed_version(installed, root) == "1.2.3"
    # The upstream key is no longer consulted once a marketplace name exists.
    _write_installed(installed, "ui-clone-skills@voidmatcha", "9.9.9")
    assert _installed_version(installed, root) == ""


def test_installed_version_key_falls_back_to_upstream_marketplace(tmp_path: Path) -> None:
    installed = tmp_path / "installed_plugins.json"
    _write_installed(installed, "ui-clone-skills@voidmatcha", "0.8.17")
    # No marketplace.json at all, and one without a usable name: same fallback.
    assert _installed_version(installed, tmp_path / "missing-root") == "0.8.17"
    root = tmp_path / "repo"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"name": "  "}), encoding="utf-8")
    assert _installed_version(installed, root) == "0.8.17"
