"""Declaration-of-done command detection.

Patterns blocked (at any command-chain position — start of string, or after
;, &&, ||, |, (, a newline, an env-var/`env` prefix, or `xargs`):
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

from .._common import CMD_WRAPPED_POSITION_PREFIX, sanitize_command_for_deny

# Anchored on CMD_POSITION_PREFIX rather than plain `^\s*`: every other deny
# matcher in this package uses that prefix precisely because a bare `^\s*`
# anchor only catches a declaration command when it is the FIRST command in
# the string. `git add -A && git commit -m x`, `cd impl && git push`, and
# `git add . ; git push origin main` all start with something else and were
# verified to slip past the old anchor — the declaration cascade never ran
# for the most common real-world chained forms, leaving only the Stop hook
# (which fires after the commit already landed) as a backstop.
#
# CMD_WRAPPED_POSITION_PREFIX (shared with the enforcement-state guard) also
# admits wrappers (`command|exec|time|nohup git push`), compound keywords
# (`if ..; then git push; fi`), `{ git push; }` groups and backticks. git's
# global options (`-C dir`, `-c k=v`, `--no-pager`, `--git-dir=...`) may sit
# between `git` and the subcommand.
_GIT_GLOBAL_OPTS = (
    r"(?:(?:-C|-c|--git-dir|--work-tree|--namespace|--exec-path)\s+\S+\s+"
    r"|--?[\w-]+(?:=\S+)?\s+)*"
)
_BLOCK_PATTERNS = re.compile(
    rf"{CMD_WRAPPED_POSITION_PREFIX}(?:"
    rf"git\s+{_GIT_GLOBAL_OPTS}(?:commit|push)\b"
    r"|gh\s+pr\s+(?:create|merge|close)\b"
    r")"
)


def _is_declaration_command(cmd: str) -> bool:
    if not cmd:
        return False
    # sanitize_command_for_deny strips heredoc bodies and blanks quoted
    # arguments, so a declaration keyword inside a commit message or quoted
    # string (e.g. `git commit -m "run git push later"`) cannot false-match.
    #
    # .lstrip(): CMD_POSITION_PREFIX's `^` alternative has no `\s*` after it
    # (unlike its `;`/`&&`/`||` alternatives, which do), so a command that is
    # first in the string but has LEADING whitespace — "  git push",
    # "\tgit commit -m x" — stopped matching when this switched from a bare
    # `^\s*` anchor to CMD_POSITION_PREFIX (fable review regression). Only
    # leading whitespace is stripped; a connector's own internal whitespace
    # is untouched, so chained-form detection is unaffected.
    return bool(_BLOCK_PATTERNS.search(sanitize_command_for_deny(cmd).lstrip()))
