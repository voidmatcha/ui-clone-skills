"""Bash-redirect target detection.

Bash redirects/streams that write to a file. Each pattern captures the
target path. Designed to catch the common ways an agent could bypass the
PreToolUse Edit/Write hook (pre_generate.py): `cat > file`, `tee file`,
`sed -i ... file`, and Codex-flagged v0.8 additions:
`python3 -c "open(...).write(...)"`, `cp source target`, `mv source target`.
Bash redirect was the original bypass; v0.6 → v0.7 closed `>`/`tee`/`sed`;
v0.8 closes the file-API bypass after a natural-prompt nested agent
invented `initial-survey.json` / `style-survey.json` via `python3 -c` to
skirt the redirect deny.
"""

from __future__ import annotations

import re

from ui_clone.hooks._common import is_ad_hoc_ref_artifact, is_component_file

_BASH_WRITE_PATTERNS = [
    # `cmd > file` or `cmd >> file` — any redirect to a path. Excludes process
    # substitutions (>(...)), fd duplications (>&N), and /dev/* sinks.
    re.compile(r">>?\s*(?![&(])\s*([^\s|;&<>()]+)"),
    # `tee file` / `tee -a file` — also blocks `tee --append`.
    re.compile(r"\btee\b\s+(?:-a\s+|--append\s+)?([^\s|;&<>()]+)"),
    # `sed -i ... file` — in-place edit. Match the file argument that follows
    # the sed expression. Conservative: requires the target to literally end
    # in a recognised source extension to avoid false positives on inline scripts.
    re.compile(
        r"\bsed\b[^|;&]*?\s-i(?:\.\S+)?\s[^|;&]*?\s([^\s|;&<>()]+\.(?:tsx|jsx|ts|js|css|scss|svelte|vue))\b"
    ),
    # `python -c "open('path','w').write(...)"` / `python3 -c "..."` /
    # `python -c "with open('path', 'w') as f: ..."`. Matches both quoted
    # styles. Captures only paths ending in .json (the only artifact class
    # we care about under tmp/ref/<c>/) to avoid false-positives on
    # legitimate Python that writes .txt logs etc.
    re.compile(r"open\s*\(\s*['\"]([^'\"]+\.json)['\"]\s*,\s*['\"]w(?:b|t)?['\"]"),
    # `cp source target.json` / `cp -r source target.json` — final positional
    # arg is the destination. Conservative: target must end in .json.
    re.compile(r"\bcp\b\s+(?:-[a-zA-Z]+\s+)*\S+\s+([^\s|;&<>()]+\.json)\b"),
    # `mv source target.json` — same shape.
    re.compile(r"\bmv\b\s+(?:-[a-zA-Z]+\s+)*\S+\s+([^\s|;&<>()]+\.json)\b"),
]


def _bash_write_target(cmd: str) -> str | None:
    """Return the first component-file target this Bash command writes to, else None.

    Skips writes to /dev/null, /tmp, /var/tmp, .stale paths and the like —
    they're never component files anyway, but the early-out reduces regex work.
    """
    if not cmd:
        return None
    if ">/dev/null" in cmd or ">/tmp/" in cmd:
        # Common no-op redirects; quick reject before regex sweep.
        pass  # don't return — there may still be a real component-file write later in the cmd
    for pat in _BASH_WRITE_PATTERNS:
        for m in pat.finditer(cmd):
            target = m.group(1).strip("\"'")
            if not target or target.startswith("&") or target == "/dev/null":
                continue
            if is_component_file(target):
                return target
    return None


def _bash_scratch_nested_ref_target(cmd: str) -> str | None:
    """Return the target path when a Bash redirect writes to
    `<anywhere>/scratch/<dir>/tmp/ref/...`, else None.

    Scratch-nested ref bypass: an agent writes canonical extraction
    artifacts (regions.json, structure.json, section-map.json, etc.) to
    `<anywhere>/scratch/<dir>/tmp/ref/<component>/` instead of
    `<repo>/tmp/ref/<component>/`. The `_bash_adhoc_ref_target` check
    passes because the filenames are canonical; the bypass is the
    LOCATION. The Stop hook's verify-stamp gate scans `<repo>/tmp/ref/`
    for active dirs and misses this nested layout entirely.

    Pattern: ANY path that traverses `scratch/<something>/tmp/ref/` is
    a nested ref tree and not canonical. The canonical location is
    `<repo>/tmp/ref/<component>/` directly under the repo root.
    """
    if not cmd:
        return None
    for pat in _BASH_WRITE_PATTERNS:
        for m in pat.finditer(cmd):
            target = m.group(1).strip("\"'")
            if not target or target.startswith("&") or target == "/dev/null":
                continue
            # Normalize for the substring check; tolerate `./` or absolute paths.
            if "/scratch/" in f"/{target}" and "/tmp/ref/" in target:
                # Confirm scratch precedes tmp/ref in the path
                scratch_idx = target.find("scratch/")
                tmpref_idx = target.find("tmp/ref/")
                if scratch_idx != -1 and tmpref_idx != -1 and scratch_idx < tmpref_idx:
                    return target
    return None


def _bash_adhoc_ref_target(cmd: str) -> tuple[str, str] | None:
    """Return (target_path, suggested_canonical) for the first Bash redirect
    that writes to an ad-hoc *.json under any `tmp/ref/<c>/`, else None.

    Closes the v0.6 bypass observed during natural-prompt fresh runs: the
    pre_generate Write/Edit hook denies invented artifact names, but
    nested agents fall back to `bash -c '... > sections-map.json'`. This
    catches `cat > file.json`, `echo > file.json`, `tee file.json`,
    `agent-browser eval ... > file.json`, etc. — the same redirect set
    already parsed for component-file enforcement.
    """
    if not cmd:
        return None
    for pat in _BASH_WRITE_PATTERNS:
        for m in pat.finditer(cmd):
            target = m.group(1).strip("\"'")
            if not target or target.startswith("&") or target == "/dev/null":
                continue
            is_adhoc, suggested = is_ad_hoc_ref_artifact(target)
            if is_adhoc:
                return target, suggested
    return None
