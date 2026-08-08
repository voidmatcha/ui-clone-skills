"""Ref-dir token resolution must not invent directories from unexpanded shell syntax.

The PreBash hook sees the command string BEFORE the shell expands it. A token
like `REF=$PWD/tmp/ref/$COMPONENT;` therefore can never be resolved to a real
path — but `_maybe_ref_dir_from_token` used to accept it, and
`mark_ref_session`'s `mkdir(parents=True)` then materialised the literal string
as a directory tree. Observed fallout in this repo: `./$(pwd)/`, `./$PWD/`,
`./RD=$(pwd)/`, `./REF=$(pwd)/`, `./REF=$PWD/`.

That is not just clutter. The session marker written under the phantom ref dir
is what `should_enforce_ref_for_session` reads to decide whether the Stop gate
owns a clone, so a misresolved ref dir silently de-scopes a fail-closed gate.
"""

from pathlib import Path

from ui_clone.hooks._common import _maybe_ref_dir_from_token

BASE = Path("/repo")


def test_unexpanded_shell_tokens_are_not_ref_dirs() -> None:
    # Each of these produced a real phantom directory before the guard.
    unresolvable = [
        "REF=$PWD/tmp/ref/$COMPONENT;",
        "REF=$(pwd)/tmp/ref/navercorp",
        "RD=$(pwd)/tmp/ref/navercorp",
        "$(pwd)/tmp/ref/foo",
        "$PWD/tmp/ref/foo",
        "tmp/ref/$COMPONENT",
        "tmp/ref/${COMPONENT}",
        "`pwd`/tmp/ref/foo",
    ]
    for token in unresolvable:
        assert _maybe_ref_dir_from_token(token, BASE) is None, token


def test_trailing_semicolon_does_not_fork_a_second_clone() -> None:
    # `tmp/ref/navercorp;` and `tmp/ref/navercorp` are the same clone. Treating
    # them as distinct splits session bookkeeping across two marker dirs.
    assert _maybe_ref_dir_from_token("tmp/ref/navercorp;", BASE) == BASE / "tmp/ref/navercorp"
    assert _maybe_ref_dir_from_token("tmp/ref/navercorp", BASE) == BASE / "tmp/ref/navercorp"


def test_legitimate_tokens_still_resolve() -> None:
    assert _maybe_ref_dir_from_token("tmp/ref/real", BASE) == BASE / "tmp/ref/real"
    assert _maybe_ref_dir_from_token('"tmp/ref/real"', BASE) == BASE / "tmp/ref/real"
    assert _maybe_ref_dir_from_token("tmp/ref/real,", BASE) == BASE / "tmp/ref/real"
    assert (
        _maybe_ref_dir_from_token("/abs/tmp/ref/real", BASE) == Path("/abs/tmp/ref/real")
    )
    # A dollar sign further down the path must not disqualify the ancestor that
    # is itself a well-formed ref dir.
    assert _maybe_ref_dir_from_token("tmp/ref/real/sections", BASE) == BASE / "tmp/ref/real"


def test_non_ref_tokens_are_ignored() -> None:
    assert _maybe_ref_dir_from_token("--json", BASE) is None
    assert _maybe_ref_dir_from_token("scripts/verify/auto-verify.sh", BASE) is None
