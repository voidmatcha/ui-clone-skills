"""Declaration-of-done command detection.

Patterns blocked (anchored at start-of-command, after optional whitespace):
  - git commit ...
  - git push ...
  - gh pr create ...
  - gh pr merge ...
  - gh pr close ... (declaring abandonment is also a 'done' state we want to verify)

Not blocked: `git status`, `git diff`, `git log`, `gh pr view`, etc. — those are
read-only inspection.
"""

from __future__ import annotations

import re

_BLOCK_PATTERNS = re.compile(
    r"^\s*(?:"
    r"git\s+commit\b"
    r"|git\s+push\b"
    r"|gh\s+pr\s+(?:create|merge|close)\b"
    r")"
)


def _is_declaration_command(cmd: str) -> bool:
    if not cmd:
        return False
    return bool(_BLOCK_PATTERNS.search(cmd))
