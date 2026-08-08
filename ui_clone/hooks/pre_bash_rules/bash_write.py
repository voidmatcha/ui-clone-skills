"""Bash-redirect target detection.

Bash redirects/streams that write to a file. Each pattern captures the
target path. Designed to catch the common ways an agent could bypass the
PreToolUse Edit/Write hook (pre_generate.py): `cat > file`, `tee file`,
`sed -i ... file`, plus later file-API additions:
`python3 -c "open(...).write(...)"`, `cp source target`, `mv source target`.
Bash redirect was the original bypass; v0.6 → v0.7 closed `>`/`tee`/`sed`;
v0.8 closes the file-API bypass after a natural-prompt nested agent
invented `initial-survey.json` / `style-survey.json` via `python3 -c` to
skirt the redirect deny.
"""

from __future__ import annotations

import re

from ui_clone.hooks._common import (
    CMD_POSITION_PREFIX,
    is_ad_hoc_ref_artifact,
    is_component_file,
    sanitize_command_for_deny,
)

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


# Enforcement-state files whose targeted deletion / truncation / overwrite / move
# silently disables a guard: .gate-skip-log (fail-LOUD ledger — gate_skip_blocker
# early-returns when the ledger is absent OR empty, so destroying OR emptying it
# releases an un-enforced run), .ui-re-external-browse/* (off-pipeline activation
# crumbs — the off-pipeline Stop/declaration blocker fires only when these exist),
# and .ui-re-active (the fresh-active session marker the Stop hook scans). A
# whole-dir reset (`rm -rf tmp/ref/<c>`) does NOT name these files, so it is not
# matched — only an operation that singles out an enforcement file is. Beyond
# rm/mv/find, this covers the cheap ways to empty/overwrite the ledger:
# cp/tee/dd/truncate/install/rsync/sed -i/ex, python file-write/delete APIs, and
# `cat >`/`>>` redirect clobber.
#
# Command-verb patterns run on the RAW command, anchored at command position
# (incl. wrapper prefixes like `command`/`sudo`/`\`) so a QUOTED destination path
# (`dd of="...path..."`) is still caught while a quoted prose mention of the verb
# in a commit message is not — at the cost of a rare FP if a message literally
# contains `<connector> <destroy-verb> <ledger-path>`. The ex/perl/redirect
# matchers run on the quote/heredoc-stripped view so an editor script's own `|`/`;`
# and quoted prose can't interfere; the heredoc-opener redirect (`cat <<EOF
# >ledger`) is scanned raw because that view drops the opener line. The guard
# raises the cost of destruction across the natural tools; it is not a sandbox, so
# determined obfuscation (path built from variables, exotic tools) is out of scope.
#
# ⛔ FROZEN VERB SET (Fable + Codex review, 2026-07): do NOT keep adding
# destructive-verb closures (busybox/setfacl/rename-class residuals are known-open
# and stay open). The threat model is a LAZY agent that stops at the first
# deny-with-instructions, not an adversary defeating a sandbox — each extra verb
# now buys ~zero behavior change at real maintenance cost. The boundary is
# deliberate: natural-tool speed-bump, not airtight. Add a verb ONLY if a real
# incident shows a NATURAL tool (one an honest agent reaches for) slipping through.
# N1: verify-stamp.json and pipeline-state.json are CLOSEOUT PROVENANCE — the Stop
# hook trusts pipeline-state.json's terminalState.writtenBy and the verify-stamp as
# evidence the gates ran. If the agent can hand-write either, it can forge a
# `writtenBy:"pipeline"` terminal or a gates-covering verify-stamp and release the
# Stop with zero gates. Guard them like the other enforcement state so a direct
# bash write (echo>/cp/tee/python open) is denied. The canonical writers are the
# in-process `python -m ui_clone.pipeline|state ...` CLIs, which name the MODULE,
# not the file, on the command line — so they are unaffected (the hook only sees
# the top-level command, never a script/CLI's internal file writes).
# The filename group is CASE-INSENSITIVE (scoped `(?i:...)`): on case-insensitive
# filesystems (macOS APFS, Windows NTFS) `> Pipeline-State.JSON` clobbers the real
# `pipeline-state.json`, so a case-sensitive match would let a one-character rename
# bypass the guard. Scoped to the filename only — the destructive verbs (rm/cp/...)
# stay case-sensitive, matching how the shell resolves commands.
_ENFORCEMENT_STATE_RE = (
    r"(?i:\.gate-skip-log|\.ui-re-external-browse|\.ui-re-active"
    r"|verify-stamp\.json|pipeline-state\.json"
    # sections/result.txt(+.json) is the sha256-stamped section verdict the
    # post-implement gate trusts. PATH-QUALIFIED (`sections/result.*`, not a bare
    # `result.txt`) so the real producer — section-compare, which builds the path
    # from a $VAR and never writes a literal `sections/result.txt` on the command
    # line — and an unrelated build/result.txt are both untouched, while a direct
    # agent forge (`echo PASS > .../sections/result.txt`) is caught.
    r"|sections/result\.(?:txt|json)"
    # structural-convergence-stamp.json / canvas-replay-stamp.json release the Stop
    # gate; .driver-session.id is the registered-driver identity. All are produced
    # by scripts/modules (check-converged.sh / check-canvas-replay.sh /
    # register-driver-session.sh — which name themselves, not the file, on the
    # command line), so filename-blocking only catches a direct agent forge.
    r"|structural-convergence-stamp\.json|canvas-replay-stamp\.json"
    r"|\.driver-session\.id)"
)

# Command position + optional command wrappers an agent reaches for (`command rm`,
# `\rm`, `sudo rm`, `builtin/exec/nice/time rm`). CMD_POSITION_PREFIX itself only
# consumes leading `env`/`KEY=VAL`, so without this a wrapper would shift the verb
# off command position and bypass the guard.
_ENFORCEMENT_VERB_PREFIX = (
    CMD_POSITION_PREFIX + r"(?:\\?(?:command|builtin|exec|sudo|nice|time)\s+)*\\?"
)

# cp/tee/install/rsync/mv/ln match the enforcement token ANYWHERE after the verb.
# This conservatively also blocks the rare copy/move/link FROM the ledger (a
# backup): enforcement state should not be shuffled around, and the deny message
# routes to a whole-dir reset. `[^|;&\n]` keeps each match inside one pipeline
# stage so a later read after `|` is not captured.
_ENFORCEMENT_RM_PATTERNS = [
    re.compile(
        _ENFORCEMENT_VERB_PREFIX
        + rf"(?:rm|rmdir|unlink|shred|trash)\b[^|;&\n]*?(\S*{_ENFORCEMENT_STATE_RE}\S*)"
    ),
    # chmod/chown/chgrp/chattr/chflags on an enforcement file OR its crumb DIR:
    # locking perms (chmod 000) or setting immutable (chattr +i / chflags uchg)
    # silences future appends/writes to the ledger as effectively as deleting it.
    # Token-anchored, so a chmod of any non-enforcement path is untouched.
    re.compile(
        _ENFORCEMENT_VERB_PREFIX
        + rf"(?:chmod|chown|chgrp|chattr|chflags)\b[^|;&\n]*?(\S*{_ENFORCEMENT_STATE_RE}\S*)"
    ),
    # mv/cp/ln: match anywhere so the ledger as either source (moved/linked away)
    # or destination (overwritten/replaced) is caught.
    re.compile(_ENFORCEMENT_VERB_PREFIX + rf"mv\b[^|;&\n]*?(\S*{_ENFORCEMENT_STATE_RE}\S*)"),
    re.compile(_ENFORCEMENT_VERB_PREFIX + rf"cp\b[^|;&\n]*?(\S*{_ENFORCEMENT_STATE_RE}\S*)"),
    # ln -sf /dev/null <ledger> replaces it with a sink (future appends discarded).
    re.compile(_ENFORCEMENT_VERB_PREFIX + rf"ln\b[^|;&\n]*?(\S*{_ENFORCEMENT_STATE_RE}\S*)"),
    # tee writes EVERY file argument, so the ledger as any target is a clobber.
    re.compile(_ENFORCEMENT_VERB_PREFIX + rf"tee\b[^|;&\n]*?(\S*{_ENFORCEMENT_STATE_RE}\S*)"),
    re.compile(_ENFORCEMENT_VERB_PREFIX + rf"install\b[^|;&\n]*?(\S*{_ENFORCEMENT_STATE_RE}\S*)"),
    re.compile(_ENFORCEMENT_VERB_PREFIX + rf"rsync\b[^|;&\n]*?(\S*{_ENFORCEMENT_STATE_RE}\S*)"),
    # dd of=<enforcement file> — `of=` pins the destination.
    re.compile(_ENFORCEMENT_VERB_PREFIX + rf"dd\b[^|;&\n]*?\bof=(\S*{_ENFORCEMENT_STATE_RE}\S*)"),
    # truncate(1) -s N <file> — the GNU command (distinct from the shell `>`).
    re.compile(_ENFORCEMENT_VERB_PREFIX + rf"truncate\b[^|;&\n]*?(\S*{_ENFORCEMENT_STATE_RE}\S*)"),
    # sed -i ... <file> — in-place edit (e.g. `sed -i '/.*/d'` empties it).
    re.compile(
        _ENFORCEMENT_VERB_PREFIX + rf"sed\b[^|;&\n]*?\s-i[^|;&\n]*?(\S*{_ENFORCEMENT_STATE_RE}\S*)"
    ),
    # find ... <enforcement file> ... -delete / -exec rm {} +
    re.compile(
        _ENFORCEMENT_VERB_PREFIX
        + rf"find\b[^|;&\n]*?{_ENFORCEMENT_STATE_RE}[^|;&\n]*?(?:-delete\b|-exec\s+(?:rm|rmdir|unlink|shred|trash|chmod|chown|chgrp|chattr|chflags)\b)"
    ),
]

# A heredoc whose OPENER line also redirects to an enforcement file
# (`cat <<EOF >ledger` / `cat <<EOF>ledger` / `cat <<'EOF' >ledger`).
# sanitize_command_for_deny drops the whole opener line (delimiter + trailing
# redirect) with the body, so the sanitized redirect matcher would miss this —
# hence a raw scan. It is anchored at command position (a real command token
# before `<<`, wrapper prefixes included) so a quoted prose mention of the
# heredoc syntax in a commit/grep argument does NOT trip it, while a quoted
# heredoc DELIMITER (`<<'EOF'`) is still handled (raw view preserves it).
_ENFORCEMENT_HEREDOC_REDIRECT_RE = re.compile(
    _ENFORCEMENT_VERB_PREFIX
    + rf"[^\s<>|;&]+\s*<<-?\s*['\"]?\w+['\"]?[^\n]*?>>?\|?\s*(\S*{_ENFORCEMENT_STATE_RE}\S*)"
)

# python file-write/delete naming the enforcement file. FIRST-ARG forms
# (open('X',...), pathlib.Path('X')..., os.truncate/remove/unlink/rename('X'),
# shutil.rmtree('X'), os.rmdir/chmod/chown('X',...)) are matched conservatively —
# a read like open('X')/Path('X').read_text() is over-blocked too, which is
# acceptable: no legitimate flow python-touches these hook-managed dotfiles, and
# distinguishing read from write reopens mode-keyword bypasses
# (`open('X','r+').truncate()`, etc.). rmtree/rmdir close the crumb-DIR removal
# hole; chmod/chown close the perms-lock/immutable silencing hole. The bare-verb
# match also catches the shutil./os. qualified forms (the substring `rmtree(`
# etc. appears regardless of the module prefix). pathlib `Path('X').rmdir()/.chmod()`
# is already covered by the `Path(` alternation.
_ENFORCEMENT_PY_FIRSTARG_RE = re.compile(
    rf"(?:open|Path|truncate|remove|unlink|rename|rmtree|rmdir|chmod|chown)\s*\(\s*"
    rf"['\"]([^'\"]*{_ENFORCEMENT_STATE_RE}[^'\"]*)['\"]"
)
# copy/move/replace where the file is the (often 2nd-arg) DESTINATION. These have
# no read variant, so require BOTH a destructive API and the file as a literal.
_ENFORCEMENT_PY_DEST_API_RE = re.compile(
    r"(?:shutil\.(?:copy|copyfile|copy2|move)|os\.replace)\s*\("
)
_ENFORCEMENT_PY_LITERAL_RE = re.compile(
    rf"['\"]([^'\"]*{_ENFORCEMENT_STATE_RE}[^'\"]*)['\"]"
)

# ex(1) / perl -i in-place edits + `>`/`>>` redirect clobber, all matched on the
# quote/heredoc-stripped view so an editor script's own `|`/`;` (dropped with its
# quotes) cannot widen the match across a real pipe into a later read
# (`ex --version | cat <ledger>`), and a quoted prose mention (a commit message)
# cannot trip the redirect matcher.
_ENFORCEMENT_EX_RE = re.compile(
    _ENFORCEMENT_VERB_PREFIX + rf"ex\b[^|;&\n]*?(\S*{_ENFORCEMENT_STATE_RE}\S*)"
)
_ENFORCEMENT_PERL_RE = re.compile(
    _ENFORCEMENT_VERB_PREFIX + rf"perl\b[^|;&\n]*?\s-i[^|;&\n]*?(\S*{_ENFORCEMENT_STATE_RE}\S*)"
)
# `\d*>>?\|?` models `>`, `>>`, fd-prefixed `1>`/`2>`, and the noclobber-override
# `>|` (force-truncate past `set -o noclobber`); the optional `\|` is consumed so
# it does not leak into the captured path.
_ENFORCEMENT_REDIRECT_RE = re.compile(rf"\d*>>?\|?\s*(\S*{_ENFORCEMENT_STATE_RE}\S*)")
_ENFORCEMENT_SANITIZED_PATTERNS = [
    _ENFORCEMENT_EX_RE,
    _ENFORCEMENT_PERL_RE,
    _ENFORCEMENT_REDIRECT_RE,
]


def _bash_enforcement_state_target(cmd: str) -> str | None:
    """Return the enforcement-state path a Bash command deletes/truncates/
    overwrites/edits, else None. Catches disabling a guard by destroying its own
    state file via rm/mv/cp/tee/ln/find/dd/truncate/install/rsync/sed -i/perl -i/ex,
    python file APIs, or `>`/`>>`/heredoc redirect clobber (command-wrapper
    prefixes like `command`/`sudo`/`\\` included)."""
    if not cmd:
        return None
    for pat in _ENFORCEMENT_RM_PATTERNS:
        m = pat.search(cmd)
        if m:
            return m.group(1).strip("\"'") if m.groups() else "enforcement-state file"
    # heredoc opener line that ALSO redirects into an enforcement file — scanned
    # raw because the sanitized view drops the whole opener line.
    m = _ENFORCEMENT_HEREDOC_REDIRECT_RE.search(cmd)
    if m:
        return m.group(1).strip("\"'")
    # python first-arg write/delete APIs (conservative: a read of the file is
    # over-blocked too — see the pattern comment).
    m = _ENFORCEMENT_PY_FIRSTARG_RE.search(cmd)
    if m:
        return m.group(1).strip("\"'")
    # python copy/move/replace with the file as a (possibly 2nd-arg) destination.
    if _ENFORCEMENT_PY_DEST_API_RE.search(cmd):
        lit = _ENFORCEMENT_PY_LITERAL_RE.search(cmd)
        if lit:
            return lit.group(1).strip("\"'")
    # ex/perl in-place edits + `>`/`>>` redirect clobber, on the quote/heredoc-
    # stripped view so quoted prose (a commit message) and an editor script's own
    # `|`/`;` cannot false-fire / widen the match.
    sanitized = sanitize_command_for_deny(cmd)
    for pat in _ENFORCEMENT_SANITIZED_PATTERNS:
        m = pat.search(sanitized)
        if m:
            return m.group(1).strip("\"'")
    return None


# gateSkipAck/deferredAck in verification-plan.json release closeout blockers
# (gate_skip_blocker / deferred_checks_blocker). verification-plan.json itself is
# NOT filename-blocked (the canonical writer verification-plan.sh legitimately
# regenerates it), so the guard is CONTENT-scoped: a Bash command that BOTH writes
# verification-plan.json AND carries an ack key. The .sh writer builds the path
# from its ref-dir arg and never names the file or an ack key on the command line,
# so it is unaffected; a read (`grep gateSkipAck .../verification-plan.json`) is
# not a write target and is likewise untouched.
_VPLAN_ACK_RE = re.compile(r"gateSkipAck|deferredAck")
_VPLAN_PY_WRITE_RE = re.compile(
    r"(?:open|Path|truncate|rename)\s*\(\s*['\"]([^'\"]*verification-plan\.json)['\"]"
)


def _bash_verification_plan_ack_target(cmd: str) -> str | None:
    """Return the verification-plan.json path a Bash command writes while ALSO
    setting an ack key (gateSkipAck/deferredAck), else None."""
    if not cmd or not _VPLAN_ACK_RE.search(cmd):
        return None
    for pat in _BASH_WRITE_PATTERNS:
        for m in pat.finditer(cmd):
            target = m.group(1).strip("\"'")
            if target.endswith("verification-plan.json"):
                return target
    # python first-arg write forms (open(...,'w') is covered above; this also
    # catches pathlib.Path('...verification-plan.json').write_text(...)).
    py_match = _VPLAN_PY_WRITE_RE.search(cmd)
    if py_match:
        return py_match.group(1).strip("\"'")
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
