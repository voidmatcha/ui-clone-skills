"""Every scoped producer command the docs tell an agent to run must be one the
PostToolUse ledger (`ui_clone.scoped_ledger.parse_producer_command`) records;
otherwise scoped_check fails the run with `evidence-unledgered` although the
agent followed the docs. Commands are extracted from the fenced bash blocks,
placeholders are filled for a temp clone project, and each is parsed as the
hook would parse it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ui_clone import scoped_ledger
from ui_clone.scoped_provenance import REPO_ROOT

DOCS = {
    "skills/ui-reverse-engineering/element-capture.md": 10,
    "skills/visual-debug/comparison-fix.md": 1,
    "skills/ui-reverse-engineering/closeout.md": 3,
    "skills/ui-capture/references/evidence-contracts.md": 1,
    "docs/agent-cli.md": 6,
}
_ROOT_VARS = ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "CODEX_PLUGIN_ROOT")
_PRODUCER_RE = re.compile(r"element-evidence\.sh|element-state-capture\.sh|scoped-diff|ui_clone\.scoped_diff")
_FENCE_RE = re.compile(r"^```bash\n(.*?)^```", re.M | re.S)
PLACEHOLDERS = {
    "<session>": "s",
    "<project>": "s",
    "<page-url>": "http://localhost:5173/",
    "<url>": "http://localhost:5173/",
    "<target-selector>": "section.hero",
    "<opened-selector>": "section.hero",
    "<css-selector-from-dom-evidence>": "section.hero",
    "<target>": "hero",
    "<effect-name>": "hero",
    "<ref-dir>": "tmp/ref/hero",
    "<ref|impl>": "impl",
    "<state>": "idle",
    "<clip>": "open",
    "<prefix>": "open",
}
EXPECTED_PRODUCER = {
    "element-evidence.sh": scoped_ledger.PRODUCER_TARGET,
    "element-state-capture.sh": scoped_ledger.PRODUCER_CAPTURE,
    "scoped-diff": scoped_ledger.PRODUCER_DIFF,
    "ui_clone.scoped_diff": scoped_ledger.PRODUCER_DIFF,
}


def documented_producer_commands(rel: str) -> list[str]:
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    out: list[str] = []
    for block in _FENCE_RE.findall(text):
        logical = block.replace("\\\n", " ")
        for line in logical.splitlines():
            line = line.split("   #", 1)[0].strip()
            if line.startswith("#") or not _PRODUCER_RE.search(line):
                continue
            if not re.match(r"^(bash|node|python3?|uv) ", line):
                continue
            out.append(line)
    return out


def _fill(cmd: str) -> str:
    cmd = re.sub(r" \[[^\]]*\]", "", cmd)  # optional `[--flag ...]` segments
    for key, value in PLACEHOLDERS.items():
        cmd = cmd.replace(key, value)
    assert "<" not in cmd and ">" not in cmd, f"unmapped placeholder in {cmd!r}"
    return cmd


@pytest.mark.parametrize("hook_env_root", [False, True], ids=["root-unset", "root-exported"])
@pytest.mark.parametrize("rel", sorted(DOCS))
def test_documented_producer_commands_are_ledgered(
    rel: str, hook_env_root: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in _ROOT_VARS:
        monkeypatch.delenv(key, raising=False)
    if hook_env_root:
        monkeypatch.setenv("PLUGIN_ROOT", str(REPO_ROOT))
    ref = tmp_path / "tmp" / "ref" / "hero"
    ref.mkdir(parents=True)
    commands = documented_producer_commands(rel)
    assert len(commands) >= DOCS[rel], f"{rel}: found only {commands}"
    for documented in commands:
        cmd = _fill(documented)
        run = scoped_ledger.parse_producer_command(cmd, base=tmp_path, project_root=tmp_path)
        assert run is not None, f"{rel}: ledger does not record documented command {documented!r}"
        kind = next(k for k in EXPECTED_PRODUCER if k in documented)
        assert run.producer == EXPECTED_PRODUCER[kind], documented
        assert run.ref_dir == ref.resolve(), documented


def test_clone_project_docs_use_plugin_root_paths() -> None:
    """A clone project has no `scripts/` or `bin/` of its own: producer
    commands in the skill docs must go through `$PLUGIN_ROOT`."""
    for rel in DOCS:
        if rel.startswith("docs/"):
            continue  # maintainer doc also lists the in-checkout forms
        for cmd in documented_producer_commands(rel):
            assert '"$PLUGIN_ROOT/' in cmd, f"{rel}: {cmd!r} is not runnable from a clone project"
