"""Cross-host hook-manifest parity (AGENTS.md "Hook parity" + "Version parity").

The Claude host (``hooks/hooks.json``) and the Codex host
(``hooks/codex-hooks.json``) are allowed to differ by manifest shape, matcher
vocabulary, status messages, and timeouts — but per AGENTS.md *every command
that calls shim.sh must call ``hooks/shim.sh`` and route to the same
``ui_clone.hooks.*`` modules unless a host lacks that lifecycle event*.

Nothing at the pytest tier enforced this before: the only manifest guards were
JSON-validity (pre-push-security.sh) and a release-only version-sync shell guard
(pre-push-guard.sh, main/master pushes only). On a feature branch a routing
divergence — a host pointed at a different module, a dropped enforcement route,
or a raw ``python -m`` inlined past the shim — would ship silently, disabling
that host's pipeline enforcement while the other host still looked protected.
These tests close that gap on every branch via ci-local's pytest sweep.

The route map is pinned ABSOLUTELY (EXPECTED_ROUTES) rather than only
relatively (Claude == Codex): a relative-only check passes if *both* manifests
symmetrically drop a required route (e.g. both lose Stop -> section_gate) or
both gain a bogus hook. The golden map is the authoritative enforcement-surface
contract; the relative checks remain as clearer per-event drift diagnostics.
"""

from __future__ import annotations

import importlib
import json
import os
import re
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
CLAUDE = ROOT / "hooks" / "hooks.json"
CODEX = ROOT / "hooks" / "codex-hooks.json"
SHIM = ROOT / "hooks" / "shim.sh"

# The authoritative enforcement topology: every lifecycle event -> the ordered
# ui_clone.hooks.* modules it must route to. Claude carries the full map; Codex
# carries it MINUS the events it legitimately lacks (see CLAUDE_ONLY_EVENTS).
# Order is asserted because same-matcher multi-hook events run in sequence
# (PostToolUse fires post_verify before devtools_errors on the same Bash event).
EXPECTED_ROUTES: dict[str, list[str]] = {
    "PreToolUse": ["pre_generate", "pre_bash"],
    "PostToolUse": ["post_verify", "devtools_errors"],
    "Stop": ["section_gate"],
    "SessionStart": ["session_resume"],
    "PostCompact": ["session_resume"],
}

# The only lifecycle event Claude has that Codex legitimately lacks (Codex has
# no PostCompact event). This is the AGENTS.md "unless a host lacks that
# lifecycle event" carve-out — a policy ratchet, not a bug: if Codex ever gains
# a PostCompact equivalent, drop it from here so the topology must include it.
CLAUDE_ONLY_EVENTS = {"PostCompact"}

# Matcher-intent contract (H2). The topology map above pins event -> module
# routing but NOT the matcher — the tool/event a hook actually fires on. _routes()
# never reads entry["matcher"], so swapping the Bash <-> Write matchers (the
# bash-enforcement hook stops firing on Bash, the write-guard stops firing on
# Write) passes every routing assertion while silently disabling enforcement.
# Pin each enforcement module to its matcher INTENT class, tolerant of per-host
# vocabulary (Claude Write|Edit|MultiEdit vs Codex apply_patch|Edit|Write; Bash vs
# Bash|exec_command). Adding a genuinely new tool matcher means extending a class
# here — a deliberate ratchet, like CLAUDE_ONLY_EVENTS.
_BASH_MATCHER_TOKENS = {"Bash", "exec_command"}
_WRITE_MATCHER_TOKENS = {"Write", "Edit", "MultiEdit", "apply_patch"}
MODULE_MATCHER_INTENT: dict[str, set[str]] = {
    "pre_bash": _BASH_MATCHER_TOKENS,
    "post_verify": _BASH_MATCHER_TOKENS,
    "devtools_errors": _BASH_MATCHER_TOKENS,
    "pre_generate": _WRITE_MATCHER_TOKENS,
}
# Tool events whose hooks MUST name a non-empty, in-class matcher. Stop /
# SessionStart / PostCompact fire on a lifecycle event, not a tool, so they carry
# no tool matcher (Stop matcher=None is correct, not a dropped route).
MATCHER_REQUIRED_EVENTS = {"PreToolUse", "PostToolUse"}

# Capture the module token that IMMEDIATELY follows the resolved shim path —
# that is the shim contract (hooks/shim.sh:2 `bash shim.sh <module> [args]`).
# Anchoring on `/hooks/shim.sh` (not "first ui_clone.hooks.* anywhere in the
# command") means a stray module string elsewhere in the command cannot be
# mistaken for the route. The capture is the FULL dotted token the shim would
# hand to `python -m` (`[\w.]+` naturally stops at the next shell boundary —
# whitespace/;/"/|/&). Capturing the whole token (not a single \w+ segment) is
# deliberate: a route like `ui_clone.hooks.pre_bash.extra` is captured in full
# so it mismatches EXPECTED_ROUTES and fails import — rather than being silently
# truncated to a valid-looking `pre_bash` while the shim runs a different module.
_SHIM_ROUTE_RE = re.compile(r"/hooks/shim\.sh\"?\s+ui_clone\.hooks\.([\w.]+)")


def _load(path: Path) -> dict:
    return cast(dict, json.loads(path.read_text(encoding="utf-8")))


def _module_for(command: str) -> str | None:
    """The ui_clone.hooks.* module a command routes to (the token right after
    the shim path), or None if the command does not invoke the shim with a
    module argument."""
    match = _SHIM_ROUTE_RE.search(command)
    return match.group(1) if match else None


def _routes(manifest: dict) -> dict[str, list[str]]:
    """Map each lifecycle event to the ordered list of ui_clone.hooks.* modules
    its commands route to."""
    out: dict[str, list[str]] = {}
    for event, entries in (manifest.get("hooks") or {}).items():
        mods: list[str] = []
        for entry in entries:
            for hook in entry.get("hooks", []):
                mod = _module_for(hook.get("command", ""))
                if mod:
                    mods.append(mod)
        out[event] = mods
    return out


def _routed_matchers(manifest: dict) -> list[tuple[str, str, str | None]]:
    """(event, module, entry-matcher) for every shim-routed hook command — the
    matcher axis _routes() drops."""
    out: list[tuple[str, str, str | None]] = []
    for event, entries in (manifest.get("hooks") or {}).items():
        for entry in entries:
            matcher = entry.get("matcher")
            for hook in entry.get("hooks", []):
                mod = _module_for(hook.get("command", ""))
                if mod:
                    out.append((event, mod, matcher))
    return out


def _matcher_intent_problem(event: str, module: str, matcher: str | None) -> str | None:
    """None if (event, module, matcher) honors the matcher-intent contract, else a
    human-readable reason. Shared by the manifest check and its efficacy test."""
    if event not in MATCHER_REQUIRED_EVENTS:
        return None
    expected = MODULE_MATCHER_INTENT.get(module)
    if expected is None:
        return (
            f"{module} routes on matcher-required event {event} but has no declared "
            f"matcher intent — extend MODULE_MATCHER_INTENT"
        )
    tokens = {t for t in (matcher or "").split("|") if t}
    if not tokens:
        return (
            f"{event}->{module} has an empty/absent matcher; a matcher-required tool "
            f"event must name its tool(s)"
        )
    stray = tokens - expected
    if stray:
        return (
            f"{event}->{module} matcher {matcher!r} carries {sorted(stray)} outside "
            f"its {sorted(expected)} intent class — a swapped/bogus matcher silently "
            f"stops the hook firing on its intended tool"
        )
    return None


def _commands(manifest: dict) -> list[str]:
    return [
        hook.get("command", "")
        for entries in (manifest.get("hooks") or {}).values()
        for entry in entries
        for hook in entry.get("hooks", [])
    ]


def test_both_manifests_are_valid_json_objects() -> None:
    for path in (CLAUDE, CODEX):
        assert path.is_file(), f"{path} is missing"
        data = _load(path)
        assert isinstance(data, dict) and isinstance(
            data.get("hooks"), dict
        ), f"{path} missing a dict 'hooks' key"


def test_manifests_match_the_expected_enforcement_topology() -> None:
    # Absolute contract: closes the symmetric-drop / symmetric-add hole that a
    # relative (Claude == Codex) check cannot see, and pins every route
    # including the carved-out PostCompact -> session_resume on Claude.
    claude = _routes(_load(CLAUDE))
    assert claude == EXPECTED_ROUTES, (
        f"Claude manifest topology drifted from the expected enforcement map.\n"
        f"  expected: {EXPECTED_ROUTES}\n  actual:   {claude}"
    )
    expected_codex = {
        event: mods
        for event, mods in EXPECTED_ROUTES.items()
        if event not in CLAUDE_ONLY_EVENTS
    }
    codex = _routes(_load(CODEX))
    assert codex == expected_codex, (
        f"Codex manifest topology drifted from the expected enforcement map "
        f"(should be the full map minus {sorted(CLAUDE_ONLY_EVENTS)}).\n"
        f"  expected: {expected_codex}\n  actual:   {codex}"
    )


def test_enforcement_modules_sit_behind_their_matcher_intent() -> None:
    # H2: pin the matcher (the tool a hook fires on), not just event->module
    # routing. Both manifests must place each enforcement module behind a matcher
    # whose tokens are entirely within that module's intent class.
    for path in (CLAUDE, CODEX):
        for event, module, matcher in _routed_matchers(_load(path)):
            problem = _matcher_intent_problem(event, module, matcher)
            assert problem is None, f"{path.name}: {problem}"


def test_matcher_intent_check_catches_a_swapped_or_empty_matcher() -> None:
    # Efficacy mutation: the real manifests pass above, but the contract MUST flag
    # the reproduced H2 exploit (swap Bash<->Write so enforcement stops firing) and
    # empty/unknown matchers, while still accepting per-host vocabulary.
    assert _matcher_intent_problem("PreToolUse", "pre_bash", "Bash") is None
    assert _matcher_intent_problem("PreToolUse", "pre_bash", "Bash|exec_command") is None
    assert _matcher_intent_problem("PreToolUse", "pre_generate", "apply_patch|Edit|Write") is None
    # swap: pre_bash behind a write matcher → flagged
    assert _matcher_intent_problem("PreToolUse", "pre_bash", "Write|Edit|MultiEdit") is not None
    # swap: write-guard behind Bash → flagged
    assert _matcher_intent_problem("PreToolUse", "pre_generate", "Bash") is not None
    # empty / unknown / partial-stray matchers → flagged
    assert _matcher_intent_problem("PostToolUse", "post_verify", "") is not None
    assert _matcher_intent_problem("PostToolUse", "post_verify", None) is not None
    assert _matcher_intent_problem("PreToolUse", "pre_bash", "Bash|Write") is not None
    assert _matcher_intent_problem("PreToolUse", "pre_bash", "Foobar") is not None
    # lifecycle (non-tool) events carry no matcher and are exempt
    assert _matcher_intent_problem("Stop", "section_gate", None) is None


def test_shared_events_route_to_identical_module_lists() -> None:
    claude = _routes(_load(CLAUDE))
    codex = _routes(_load(CODEX))
    shared = set(claude) & set(codex)
    assert shared, "no shared lifecycle events between the two manifests"
    for event in sorted(shared):
        assert claude[event] == codex[event], (
            f"{event}: Claude routes {claude[event]} but Codex routes {codex[event]}"
        )


def test_event_divergence_limited_to_host_specific_lifecycle() -> None:
    claude_events = set(_routes(_load(CLAUDE)))
    codex_events = set(_routes(_load(CODEX)))
    unexpected_claude_only = claude_events - codex_events - CLAUDE_ONLY_EVENTS
    assert not unexpected_claude_only, (
        f"unexpected Claude-only lifecycle events (Codex dropped a route?): "
        f"{sorted(unexpected_claude_only)}"
    )
    assert codex_events - claude_events == set(), (
        f"Codex has lifecycle events Claude lacks — both hosts must share all "
        f"non-{sorted(CLAUDE_ONLY_EVENTS)} events: {sorted(codex_events - claude_events)}"
    )


def test_every_command_invokes_hooks_shim() -> None:
    for path in (CLAUDE, CODEX):
        for cmd in _commands(_load(path)):
            # Leading slash rejects an inlined `python -m` or a bare relative
            # shim name; the resolved-root preamble always yields `$_R/hooks/shim.sh`.
            assert "/hooks/shim.sh" in cmd, (
                f"{path.name}: command does not call .../hooks/shim.sh: {cmd}"
            )


def test_every_command_routes_to_an_importable_ui_clone_hooks_module() -> None:
    # _module_for only matches a module IMMEDIATELY after the shim path, so this
    # doubles as the shim-contract check (module is the first positional arg).
    modules: set[str] = set()
    for path in (CLAUDE, CODEX):
        for cmd in _commands(_load(path)):
            mod = _module_for(cmd)
            assert mod, (
                f"{path.name}: command does not route a ui_clone.hooks.* module "
                f"as the first arg to shim.sh: {cmd}"
            )
            modules.add(mod)
    # Binds the manifests to the real package: a module rename in code without a
    # manifest update (or a typo'd route) fails here. All hook modules guard
    # their work behind `if __name__ == '__main__'`, so import is side-effect-free.
    for mod in sorted(modules):
        importlib.import_module(f"ui_clone.hooks.{mod}")


def test_shim_script_exists_and_is_executable() -> None:
    # Every routed command shells out to this one file; a deleted/renamed/
    # non-executable shim would disable enforcement on BOTH hosts at once.
    assert SHIM.is_file(), "hooks/shim.sh is missing"
    assert os.access(SHIM, os.X_OK), "hooks/shim.sh is not executable"


def test_plugin_manifest_version_triple_synchronized() -> None:
    # The three AGENTS.md "Version parity" manifest files must agree on every
    # branch. Scoped to these three only — the broader 6-file sync
    # (package.json / pyproject.toml / ui_clone/__init__.py) stays owned by the
    # release-tier shell guard in pre-push-security.sh / pre-push-guard.sh; this
    # is deliberately not a duplicate of that wider check.
    claude_v = _load(ROOT / ".claude-plugin" / "plugin.json")["version"]
    market_v = _load(ROOT / ".claude-plugin" / "marketplace.json")["plugins"][0][
        "version"
    ]
    codex_v = _load(ROOT / ".codex-plugin" / "plugin.json")["version"]
    assert claude_v == market_v == codex_v, (
        f"plugin-version triple desync: claude-plugin={claude_v} "
        f"marketplace={market_v} codex-plugin={codex_v}"
    )
