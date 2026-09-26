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
    CMD_WRAPPED_POSITION_PREFIX,
    is_ad_hoc_ref_artifact,
    is_component_file,
    sanitize_command_for_deny,
)

_BASH_WRITE_PATTERNS = [
    # `cmd > file` or `cmd >> file` — any redirect to a path. Excludes process
    # substitutions (>(...)), fd duplications (>&N), and /dev/* sinks.
    # `>|` (noclobber override) is the same write.
    re.compile(r">>?\|?\s*(?![&(])\s*([^\s|;&<>()]+)"),
    # `cp|install|rsync|mv|ln <src...> <dest>` — the final positional argument
    # of the pipeline stage is the destination; is_component_file() filters it.
    re.compile(
        r"\b(?:cp|install|rsync|mv|ln)\b[^|;&\n]*?\s([^\s|;&<>()]+)"
        r"(?=\s*(?:$|[|;&\n)]))"
    ),
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
# .ui-re-sessions/* (session ownership records — hooks/shim.sh runs the hook stack
# for a session only while its record exists, so deleting it switches every guard
# off for that session), and .ui-re-active (the fresh-active session marker the
# Stop hook scans). A
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
# ⛔ FROZEN VERB SET (settled by review): do NOT keep adding
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
    r"(?i:\.gate-skip-log|\.ui-re-external-browse|\.ui-re-sessions|\.ui-re-active"
    r"|verify-stamp\.json|pipeline-state\.json"
    # generation-plan.json carries sourceHashes/generatedAt provenance consumed by
    # downstream gates. The canonical writer is scripts/extract/generation-plan.sh,
    # which names the script/ref dir on the command line, not the artifact. Match
    # the exact basename only, not not-generation-plan.json or .bak siblings.
    r"|(?<![^/\s'\"])generation-plan\.json(?![^\s|;&<>()'\"])"
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
    r"|\.driver-session\.id"
    # .ui-re-stop-attempts.json is the Stop retry-cap ledger; deleting or
    # emptying it resets the cap. (.ui-re-stop-shown.json is deliberately NOT
    # listed: losing it only re-shows the full Stop text, never releases one.)
    r"|\.ui-re-stop-attempts\.json"
    # element-target.json is the scoped-clone record that exempts component
    # writes from page-level gates. Its producer, element-evidence.sh, takes the
    # path as a plain argument (no verb/redirect), so only a forge is caught.
    r"|(?<![^/\s'\"])element-target\.json(?![^\s|;&<>()'\"])"
    # Scoped-clone completion evidence that scoped_check trusts only with
    # producer provenance: frames/<side>/capture-manifest.json (written by
    # element-state-capture.sh via ui_clone.element_capture), pixel-perfect-diff.json
    # (written by `python -m ui_clone.scoped_diff <ref-dir>`), and the checker's
    # own sequence-verdict cache. All three producers name the ref dir, never
    # the file, on the command line, so only a direct forge is caught.
    r"|(?<![^/\s'\"])capture-manifest\.json(?![^\s|;&<>()'\"])"
    r"|(?<![^/\s'\"])pixel-perfect-diff\.json(?![^\s|;&<>()'\"])"
    r"|\.scoped-check-cache\.json"
    # .scoped-evidence-ledger.json is the PostToolUse hook's record of the
    # evidence hashes the canonical producers wrote (ui_clone.scoped_ledger);
    # scoped_check accepts no evidence file whose hash it does not carry.
    r"|\.scoped-evidence-ledger\.json"
    # .scoped-ledger-pending.json holds the PreToolUse start time of each
    # producer command; the ledger only records files written after it.
    r"|\.scoped-ledger-pending\.json)"
)

# `ui_clone.element_capture` is the recorder element-state-capture.sh drives
# with a validated agent-browser envelope on stdin. Invoked directly, an agent
# could feed it a hand-made envelope and stamp any bytes as an implementation
# capture, so a Bash command naming the module is denied; the script names
# only itself on the command line and is unaffected.
_SCOPED_RECORDER_RE = re.compile(r"ui_clone[./\\]element_capture\b")
# The scoped evidence producers may only run through their CLIs
# (`python -m ui_clone.scoped_diff <ref-dir>`; the recorder via the script).
# Importing them from an inline `python -c`, a `python - <<EOF` heredoc, or
# running the module file lets an agent call build()/record_clip()/
# _save_manifest() with hand-made inputs and write evidence that carries the
# producer's name. Matched on the RAW command (the sanitized view strips the
# quoted/heredoc program text where the import lives); the patterns need
# import syntax, so a prose mention of a module name never trips them, and
# read-only search verbs at command position are exempt. A custom script file
# that imports them is not visible here (threat model: lazy/over-eager agent,
# not a determined one; scoped_check's producer records are the second line).
_SCOPED_PRODUCER_MODULES = r"(?:element_capture|scoped_diff|scoped_frames)"
_SCOPED_PRODUCER_IMPORT_RE = re.compile(
    rf"(?:\bfrom\s+ui_clone\s+import\b[^\n;]*\b{_SCOPED_PRODUCER_MODULES}\b"
    rf"|\bfrom\s+ui_clone\.{_SCOPED_PRODUCER_MODULES}\s+import\b"
    rf"|\bimport\s+ui_clone\.{_SCOPED_PRODUCER_MODULES}\b"
    rf"|\b(?:import_module|run_module|run_path)\s*\(\s*['\"](?:ui_clone[./]){_SCOPED_PRODUCER_MODULES}\b"
    rf"|(?<![\w-])ui_clone/{_SCOPED_PRODUCER_MODULES}\.py\b)"
)
_SEARCH_VERB_RE = re.compile(
    CMD_POSITION_PREFIX + r"(?:grep|rg|ag|ack|git\s+(?:grep|log|commit))\b"
)
# `python -m ui_clone.scoped_producers --write` regenerates the release hash
# manifest scoped_check compares evidence and installed producers against.
# In a clone project that is the last step of a producer forge; only a
# checkout of this plugin (maintainer work) may run it. `--check` stays free.
_SCOPED_PRODUCERS_WRITE_RE = re.compile(r"ui_clone[./\\]scoped_producers\b[^|;&\n]*--write\b")


def _bash_scoped_producers_write_target(cmd: str) -> str | None:
    """Return the match when a Bash command regenerates the scoped producers
    release manifest (search verbs at command position exempt)."""
    if not cmd or _SEARCH_VERB_RE.match(cmd.lstrip()):
        return None
    m = _SCOPED_PRODUCERS_WRITE_RE.search(sanitize_command_for_deny(cmd))
    return m.group(0).strip() if m else None


def _bash_scoped_recorder_target(cmd: str) -> str | None:
    """Return the matched producer reference when a Bash command drives
    `ui_clone.element_capture` directly (outside element-state-capture.sh) or
    imports a scoped evidence producer instead of running its CLI."""
    if not cmd:
        return None
    m = _SCOPED_RECORDER_RE.search(sanitize_command_for_deny(cmd))
    if m:
        return m.group(0)
    if _SEARCH_VERB_RE.match(cmd.lstrip()):
        return None
    m = _SCOPED_PRODUCER_IMPORT_RE.search(cmd)
    return m.group(0).strip() if m else None


# Command position + optional command wrappers an agent reaches for (`command rm`,
# `\rm`, `sudo rm`, `builtin/exec/nice/time rm`). CMD_POSITION_PREFIX itself only
# consumes leading `env`/`KEY=VAL`, so without this a wrapper would shift the verb
# off command position and bypass the guard.
_ENFORCEMENT_VERB_PREFIX = CMD_WRAPPED_POSITION_PREFIX

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
_ENFORCEMENT_PY_LITERAL_RE = re.compile(rf"['\"]([^'\"]*{_ENFORCEMENT_STATE_RE}[^'\"]*)['\"]")

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

# Shell escapes an agent puts in front of a quote to nest a program inside a
# double-quoted argument: `python3 -c "open(\"...pixel-perfect-diff.json\",\"w\")"`.
# The literal-matching patterns above see `\"` where they expect a quote and
# miss the form, while the single-quote form is denied. Every literal matcher
# therefore runs on the raw text AND on this unescaped view (`\"` -> `"`,
# `\'` -> `'`, `\\` -> `\`, `\$` -> `$`), which is what the inner program sees.
_SHELL_ESCAPE_RE = re.compile(r"\\([\"'\\$`])")
# Interpreters an agent hands an inline program to (`-c` / `-e` / `--eval` /
# stdin). An inline program that mentions an enforcement file at all is denied,
# whatever API or quoting it uses: no legitimate flow feeds these hook-managed
# files to an inline program (the sanctioned reads are cat/jq/grep and the
# status CLIs). Matched on the quote-stripped view so an interpreter name inside
# a commit message or grep pattern is not an invocation.
_INLINE_INTERPRETER_RE = re.compile(
    _ENFORCEMENT_VERB_PREFIX
    + r"(?:uv\s+run\s+(?:--\S+\s+)*)?"
    + r"(?:python[0-9.]*|pypy[0-9]*|node|nodejs|deno|bun|perl|ruby|php)\b"
)
# `bash -c '<program>'` / `sh -c "<program>"` / `eval '<program>'`: the nested
# program is a full command line the outer matchers only see as one quoted
# argument; it is re-scanned as a command of its own (recursively).
_NESTED_SHELL_RE = re.compile(_ENFORCEMENT_VERB_PREFIX + r"(?:(?:ba|z|da|k)?sh|eval)\b")


_ENFORCEMENT_MENTION_RE = re.compile(rf"\S*{_ENFORCEMENT_STATE_RE}\S*")


def _unescape_shell(cmd: str) -> str:
    return _SHELL_ESCAPE_RE.sub(r"\1", cmd)


def _shell_words(cmd: str) -> list[str]:
    """POSIX shell words of `cmd` (escapes and quotes resolved), best effort:
    an unterminated quote falls back to whitespace splitting of the
    unescaped text so a malformed command is still inspected."""
    import shlex

    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return _unescape_shell(cmd).split()


def _nested_shell_programs(cmd: str) -> list[str]:
    """Programs handed to `bash|sh|zsh -c` / `eval` anywhere in `cmd`."""
    if not _NESTED_SHELL_RE.search(sanitize_command_for_deny(cmd)):
        return []
    words = _shell_words(cmd)
    out: list[str] = []
    for i, word in enumerate(words):
        base = word.rsplit("/", 1)[-1]
        if base == "eval":
            program = " ".join(words[i + 1 :])
            if program:
                out.append(program)
            continue
        if base in {"bash", "sh", "zsh", "dash", "ksh"}:
            for j in range(i + 1, len(words)):
                arg = words[j]
                if arg == "-c" and j + 1 < len(words):
                    out.append(words[j + 1])
                    break
                if not arg.startswith("-"):
                    break
    return out


def _inline_program_mention(cmd: str) -> str | None:
    """The enforcement file an inline interpreter program (`python -c`,
    `node -e`, `perl -e`, `python - <<EOF`, `echo ... | python3`) mentions,
    else None. The mention is looked up in the resolved shell words (escapes
    and quotes gone) and in heredoc bodies, so no quoting style hides it."""
    if not _INLINE_INTERPRETER_RE.search(sanitize_command_for_deny(cmd)):
        return None
    views = _shell_words(cmd) + [_unescape_shell(cmd)]
    for view in views:
        m = _ENFORCEMENT_MENTION_RE.search(view)
        if m:
            return m.group(0)
    return None


def _enforcement_state_target_in(cmd: str) -> str | None:
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


def _bash_enforcement_state_target(cmd: str, _depth: int = 0) -> str | None:
    """Return the enforcement-state path a Bash command deletes/truncates/
    overwrites/edits, else None. Catches disabling a guard by destroying its own
    state file via rm/mv/cp/tee/ln/find/dd/truncate/install/rsync/sed -i/perl -i/ex,
    python file APIs, or `>`/`>>`/heredoc redirect clobber (command-wrapper
    prefixes like `command`/`sudo`/`\\` included). The literal matchers also run
    on the shell-unescaped view (`\\"` -> `"`), an inline interpreter program
    (`python -c` / `node -e` / `perl -e` / stdin) that mentions an enforcement
    file is denied outright, and a `bash -c '<program>'` / `eval` argument is
    re-scanned as a command."""
    if not cmd:
        return None
    target = _enforcement_state_target_in(cmd)
    if target is None:
        unescaped = _unescape_shell(cmd)
        if unescaped != cmd:
            target = _enforcement_state_target_in(unescaped)
    if target is None:
        target = _inline_program_mention(cmd)
    if target is None and _depth < 3:
        for program in _nested_shell_programs(cmd):
            target = _bash_enforcement_state_target(program, _depth + 1)
            if target is not None:
                break
    return target


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
