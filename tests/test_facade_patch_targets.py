"""Guard the section_capture / section_compare_sections facades against drift.

Both modules re-export primitives from sibling modules. A function is resolved
by name in the module that *calls* it, so a test that patches
``ui_clone.section_capture.<name>`` only intercepts calls made from code that
lives in (or is looked up from) ``section_capture`` itself. This test fails
when:

* a name listed in a facade's ``__all__`` is no longer importable from it, or
* a name that tests monkeypatch on a facade is no longer defined in the facade
  module, nor looked up by name from a function body in the facade module —
  the point at which the patch would silently stop intercepting anything, or
* a function reachable from the facade, but defined in a sibling module, looks
  up a facade-patched name through its own module globals and that call site
  is not in ``KNOWN_SIBLING_LOOKUPS``. Patching the facade name does not
  intercept such a call; the known sites are safe only because no test relies
  on it (tests patch the enclosing composite, inject ``evaluator``/``setter``,
  or keep the env guard off).
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
import textwrap
from pathlib import Path
from types import FunctionType, ModuleType

import ui_clone.section_capture as section_capture
import ui_clone.section_compare_sections as section_compare_sections

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
FACADES: dict[str, ModuleType] = {
    "section_capture": section_capture,
    "section_compare_sections": section_compare_sections,
}
_STRING_TARGET = re.compile(r"^ui_clone\.(section_capture|section_compare_sections)\.(\w+)$")

# (defining module, function, looked-up name): sibling-module call sites that a
# facade patch of ``name`` does NOT intercept. Tests that drive these composites
# patch the composite itself on the facade (``_ensure_viewport``,
# ``_scroll_metrics``, ``_resolve_live_section_rect``, ``_run_agent_eval``) or
# pass ``evaluator=``/``setter=``. Adding an entry requires the same guarantee.
KNOWN_SIBLING_LOOKUPS: set[tuple[str, str, str]] = {
    ("ui_clone.section_capture_browser", "_ensure_viewport", "_run_agent_browser"),
    ("ui_clone.section_capture_browser", "_ensure_viewport", "_run_agent_eval_text"),
    ("ui_clone.section_capture_browser", "_resolve_live_section_rect", "_run_agent_eval_text"),
    ("ui_clone.section_capture_browser", "_run_agent_eval", "_run_agent_browser"),
    ("ui_clone.section_capture_browser", "_run_agent_eval_text", "_run_agent_browser"),
    ("ui_clone.section_capture_browser", "_scroll_metrics", "_run_agent_eval_text"),
}


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Map local alias -> facade short name for the two facade modules."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ui_clone."):
                    short = alias.name.removeprefix("ui_clone.")
                    if short in FACADES:
                        aliases[alias.asname or alias.name] = short
        elif isinstance(node, ast.ImportFrom) and node.module == "ui_clone":
            for alias in node.names:
                if alias.name in FACADES:
                    aliases[alias.asname or alias.name] = alias.name
    return aliases


def _patched_names() -> dict[str, set[str]]:
    """Collect ``monkeypatch.setattr(<facade>, "<name>", ...)`` targets from tests."""
    patched: dict[str, set[str]] = {name: set() for name in FACADES}
    for path in sorted(TESTS_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "setattr"):
                continue
            if not node.args:
                continue
            target = node.args[0]
            if (
                isinstance(target, ast.Name)
                and target.id in aliases
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                patched[aliases[target.id]].add(node.args[1].value)
            elif isinstance(target, ast.Constant) and isinstance(target.value, str):
                match = _STRING_TARGET.match(target.value)
                if match:
                    patched[match.group(1)].add(match.group(2))
    return patched


def _names_looked_up_in_function_bodies(module: ModuleType) -> set[str]:
    source = Path(str(module.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load):
                    names.add(inner.id)
    return names


def _body_loads(func: FunctionType) -> set[str]:
    try:
        source = textwrap.dedent(inspect.getsource(func))
    except (OSError, TypeError):
        return set()
    return {
        node.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _sibling_lookups(facade: ModuleType, names: set[str]) -> set[tuple[str, str, str]]:
    """Call sites reachable from the facade that resolve a patched name elsewhere.

    Entry points are every function reachable as a facade attribute (tests call
    re-exported helpers through the facade too). Callees are followed through
    the globals of the module that defines each function.
    """
    queue = [obj for obj in vars(facade).values() if isinstance(obj, FunctionType)]
    seen: set[FunctionType] = set()
    sites: set[tuple[str, str, str]] = set()
    while queue:
        func = queue.pop()
        if func in seen or not func.__module__.startswith("ui_clone."):
            continue
        seen.add(func)
        owner = sys.modules[func.__module__]
        for name in _body_loads(func):
            if name in names and owner is not facade:
                sites.add((func.__module__, func.__name__, name))
            callee = vars(owner).get(name)
            if isinstance(callee, FunctionType):
                queue.append(callee)
    return sites


def _defined_in_module(module: ModuleType, name: str) -> bool:
    obj = getattr(module, name, None)
    return getattr(obj, "__module__", None) == module.__name__


def test_facade_all_names_are_importable() -> None:
    for short, module in FACADES.items():
        exported = getattr(module, "__all__", None)
        assert exported, f"ui_clone.{short} must declare __all__"
        missing = [name for name in exported if not hasattr(module, name)]
        assert not missing, f"ui_clone.{short}.__all__ lists undefined names: {missing}"


def test_patched_facade_names_are_still_intercepted_by_the_facade() -> None:
    patched = _patched_names()
    # Sanity: the collector must see the patches the suite is known to make;
    # an empty set would mean the grep silently stopped guarding anything.
    assert patched["section_capture"] >= {"_run_screenshot", "_run_crop", "_run_agent_eval"}
    for short, names in patched.items():
        module = FACADES[short]
        looked_up = _names_looked_up_in_function_bodies(module)
        dead_patches = sorted(
            name
            for name in names
            if not hasattr(module, name)
            or not (_defined_in_module(module, name) or name in looked_up)
        )
        assert not dead_patches, (
            f"tests monkeypatch ui_clone.{short}.{dead_patches} but the facade no "
            "longer defines or looks those names up, so the patch intercepts nothing. "
            "Move the call site back into the facade or patch the module that "
            "performs the lookup."
        )


def test_sibling_call_sites_of_patched_names_are_known() -> None:
    """Patching a facade name cannot reach a lookup made inside a sibling module."""
    patched = _patched_names()
    sites: set[tuple[str, str, str]] = set()
    for short, names in patched.items():
        sites |= _sibling_lookups(FACADES[short], names)
    # Sanity: the walker must see the known sites, or it stopped guarding.
    assert sites & KNOWN_SIBLING_LOOKUPS, "sibling-lookup walker found nothing"
    new_sites = sorted(sites - KNOWN_SIBLING_LOOKUPS)
    assert not new_sites, (
        f"these sibling functions look up facade-patched names through their own "
        f"module, so monkeypatching the facade does not intercept them: {new_sites}. "
        "Keep the call site in the facade, or add it to KNOWN_SIBLING_LOOKUPS only "
        "after confirming every test that reaches it patches the enclosing function."
    )
    stale = sorted(KNOWN_SIBLING_LOOKUPS - sites)
    assert not stale, f"KNOWN_SIBLING_LOOKUPS lists call sites that no longer exist: {stale}"
