"""Regression tests for the CMP overlay removal in ui_clone.section_capture.

A persistent third-party consent overlay painted over the page occludes content
during capture and inflates EVERY section's AE uniformly. ``_pause_js`` strips a
vendor-generic set of these overlays during the capture settle, applied
identically to the reference AND the implementation, so it cannot favour a
faithful or a broken clone.

The dangerous failure is not a missing vendor -- it is a selector broad enough to
delete the page's own content, because the reference screenshot then no longer
shows what the page shows and every later fidelity check compares against a
doctored reference. These tests pin that invariant as a grammar over
``CMP_OVERLAY_SELECTORS`` rather than as a substring search over the emitted JS,
so a new entry has to be namespaced to a vendor to pass.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ui_clone.section_capture import CMP_OVERLAY_SELECTORS, _pause_js

# A vendor-namespaced container: an id, a class, or an attribute match whose
# value is long enough to be a vendor name rather than a generic word.
_SELECTOR_GRAMMAR = re.compile(
    r"^(?:"
    r"\#[A-Za-z][\w-]{3,}"
    r"|\.[A-Za-z][\w-]{3,}"
    r"|\[(?:id|class)[\^*~]?=[A-Za-z][\w-]{4,}\]"
    r")$"
)

# Values that name a UI role or a cookie, not a vendor.
_ROLE_TOKENS = frozenset(
    {
        "cookie",
        "cookies",
        "consent",
        "banner",
        "modal",
        "overlay",
        "popup",
        "dialog",
        "notice",
        "privacy",
        "gdpr",
    }
)

# Below this a namespace segment is an initialism that a site may well own
# itself, not a vendor identity.
_MIN_NAMESPACE_LEN = 3


_NOT_CLAUSE = re.compile(r":not\(([^)]*)\)")


def _split_exclusions(selector: str) -> tuple[str, list[str]]:
    """Split `base:not(.a):not(.b)` into its base and its excluded selectors."""
    excluded = _NOT_CLAUSE.findall(selector)
    return _NOT_CLAUSE.sub("", selector), excluded


def _value_of(selector: str) -> str:
    return selector.lstrip("#.").split("=")[-1].rstrip("]").lower()


def _is_substring_match(selector: str) -> bool:
    return "*=" in selector or "~=" in selector


def reject_reason(selector: str) -> str | None:
    """Return why this selector may delete a page's own content, else None.

    A :not() exclusion only ever narrows the match, so each excluded token is
    checked for well-formedness but not for breadth.
    """
    selector, exclusions = _split_exclusions(selector)
    for excluded in exclusions:
        if not _SELECTOR_GRAMMAR.match(excluded):
            return f"exclusion {excluded!r} is not a well-formed selector"
    if not _SELECTOR_GRAMMAR.match(selector):
        return "not a well-formed id/class/attribute container selector"

    value = _value_of(selector)
    segments = re.split(r"[-_]", value)

    if value in _ROLE_TOKENS:
        return f"keys on the bare role word {value!r}"

    # A substring match rooted at a role word spreads far past the vendor: it was
    # [class*=cookieconsent] that deleted Cookiebot's consent-gated embeds while
    # matching no CMP container at all. An exact id/class may still start with
    # such a word (#CookiebotWidget), because it matches that element only.
    if _is_substring_match(selector):
        for token in _ROLE_TOKENS:
            if value.startswith(token):
                return f"substring match rooted at the role word {token!r}"

    # A two-letter namespace plus a bare role word is not a vendor container --
    # .cc-banner and #uc-banner are shapes any site could own. A longer
    # namespace (.cky-overlay) or a further qualifier (.qc-cmp2-container) is.
    if (
        len(segments) == 2
        and len(segments[0]) < _MIN_NAMESPACE_LEN
        and segments[1] in _ROLE_TOKENS
    ):
        return (
            f"{segments[0]!r} is too short to be a vendor namespace and "
            f"{segments[1]!r} is a bare role word"
        )
    return None


@pytest.fixture(autouse=True)
def _no_env_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """_pause_js splices in an env var; keep these tests hermetic."""
    monkeypatch.delenv("SECTION_CAPTURE_DYNAMIC_PAUSE_EXTRA", raising=False)


@pytest.mark.parametrize("selector", CMP_OVERLAY_SELECTORS)
def test_selector_cannot_delete_page_content(selector: str) -> None:
    """Every entry must be a vendor-namespaced container, never a role word.

    The dangerous failure is not a missing vendor -- it is a selector broad
    enough to remove the page's own markup, because the reference screenshot
    then no longer shows what the page shows. Per-site needs belong in
    SECTION_FIXED_OVERLAY_SELECTORS.
    """
    reason = reject_reason(selector)
    assert reason is None, f"{selector!r} {reason}"


@pytest.mark.parametrize(
    "selector",
    [
        "[class*=cookieconsent]",
        "[class~=cookie]",
        "[class*=cookie_]",
        "#cookie",
        "[class*=popup]",
        "[class*=banner]",
        ".cc-banner",
        "#uc-banner",
        "[id^=foo",
        "*",
        "[class*=iubenda]:not(",
        "[class*=iubenda]:not(*)",
        "[class*=cookie]:not(.iubenda-embed)",
    ],
)
def test_grammar_rejects_known_bad_shapes(selector: str) -> None:
    """Guard the guard: a rule that accepts everything protects nothing.

    Each of these was either shipped in this repo or proposed for it.
    """
    assert reject_reason(selector) is not None, f"{selector!r} slipped through"


def test_no_duplicate_selectors() -> None:
    assert len(set(CMP_OVERLAY_SELECTORS)) == len(CMP_OVERLAY_SELECTORS)


def test_pause_js_emits_every_selector_exactly_once() -> None:
    js = _pause_js()
    emitted = json.dumps(", ".join(CMP_OVERLAY_SELECTORS))
    assert emitted in js
    for selector in CMP_OVERLAY_SELECTORS:
        assert selector in js


def test_pause_js_guards_the_removal() -> None:
    """A malformed selector must not abort the IIFE before it returns.

    querySelectorAll raises SyntaxError on an invalid list, and _run_agent_eval
    discards stdout/stderr with check=False -- so without the guard a bad entry
    would silently disable the pause for every capture with no visible signal.
    """
    js = _pause_js()
    removal = js.index("forEach(el => el.remove())")
    guard = js.rindex("try {", 0, removal)
    assert "catch" in js[removal:], "removal is not wrapped in try/catch"
    assert guard < removal


def test_print_cmp_selectors_matches_the_tuple() -> None:
    """section-compare.sh reads the list through this CLI; drift breaks ref-calib."""
    result = subprocess.run(
        ["python3", "-m", "ui_clone.section_capture", "--print-cmp-selectors"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.stdout.strip() == ", ".join(CMP_OVERLAY_SELECTORS)


def test_pause_js_removal_runs_against_a_stub_dom(tmp_path: Path) -> None:
    """Execute the emitted JS so a malformed selector list is caught here."""
    if shutil.which("node") is None:  # pragma: no cover - environment dependent
        pytest.skip("node required to execute the browser eval")
    harness = tmp_path / "pause.js"
    harness.write_text(
        "const seen = [];\n"
        "globalThis.document = {\n"
        "  getElementById: () => ({}),\n"
        "  createElement: () => ({}),\n"
        "  head: { appendChild: () => {} },\n"
        "  querySelectorAll: (sel) => { seen.push(sel); return [] },\n"
        "};\n"
        f"const out = {_pause_js()};\n"
        "if (out !== 'paused') { throw new Error('pause did not return: ' + out) }\n"
        "console.log(JSON.stringify(seen));\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    queried = json.loads(result.stdout.strip())
    assert ", ".join(CMP_OVERLAY_SELECTORS) in queried
