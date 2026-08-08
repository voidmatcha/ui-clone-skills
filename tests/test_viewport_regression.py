"""Regression guard: agent-browser `open` does not support --viewport/--wait.

agent-browser silently ignores unknown flags on `open`. `--viewport WxH`
shipped as a dead flag for months, leaving motion probes at the default
window size (1280x633) where vw-sized references reflow but px-baked impls
do not — trajectory compares then failed on every vw-heavy site (loop-145).
`--wait <ms>` is equally dead: scripts believed they were settling for N ms
and waited 0.

The supported sequence is `open` then `set viewport <w> <h>` (shared helper:
scripts/lib/viewport.sh `ab_open_at_viewport`, which also asserts
window.innerWidth post-open). This test bans the dead-flag pattern repo-wide
so it can never be reintroduced by copy-paste.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEAD_OPEN_FLAG = re.compile(r"\bopen\b[^\n|&;]*--(?:viewport|wait)\b")

SCAN_DIRS = ("skills", "scripts")

VIEWPORT_CRITICAL_SCRIPTS = (
    "skills/visual-debug/scripts/transition-trajectory-compare.sh",
    "skills/visual-debug/scripts/header-state-runtime-check.sh",
    "skills/visual-debug/scripts/mobile-viewport-parity-check.sh",
    "scripts/verify/video-transition-compare.sh",
)


def _iter_shell_scripts() -> list[Path]:
    files: list[Path] = []
    for d in SCAN_DIRS:
        files.extend((REPO_ROOT / d).rglob("*.sh"))
    return files


def test_no_dead_open_flags_anywhere() -> None:
    offenders: list[str] = []
    for sh in _iter_shell_scripts():
        for n, line in enumerate(sh.read_text().splitlines(), 1):
            code = line.split(" #", 1)[0]  # drop trailing comments
            stripped = code.lstrip()
            if stripped.startswith("#"):
                continue
            if "agent-browser" not in code and " open " not in f" {stripped}":
                continue
            if DEAD_OPEN_FLAG.search(code):
                offenders.append(f"{sh.relative_to(REPO_ROOT)}:{n}: {stripped[:100]}")
    assert not offenders, (
        "agent-browser `open` silently ignores --viewport/--wait; "
        "use scripts/lib/viewport.sh ab_open_at_viewport (or open + "
        "`set viewport` + sleep):\n" + "\n".join(offenders)
    )


def test_viewport_critical_scripts_set_viewport_explicitly() -> None:
    for rel in VIEWPORT_CRITICAL_SCRIPTS:
        text = (REPO_ROOT / rel).read_text()
        assert "ab_open_at_viewport" in text or "set viewport" in text, (
            f"{rel} drives motion probes but never sets the viewport; "
            "probes at the default window size produce false verdicts"
        )


def test_helper_asserts_inner_width() -> None:
    helper = (REPO_ROOT / "scripts/lib/viewport.sh").read_text()
    assert "innerWidth" in helper
    assert "set viewport" in helper
