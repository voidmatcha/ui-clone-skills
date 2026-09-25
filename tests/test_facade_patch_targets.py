"""Guard the section_capture / section_compare_sections facades against drift.

Both modules re-export primitives from sibling modules. A function is resolved
by name in the module that *calls* it, so a test that patches
``ui_clone.section_capture.<name>`` only intercepts calls made from code that
lives in (or is looked up from) ``section_capture`` itself. This test fails
when:

* a name listed in a facade's ``__all__`` is no longer importable from it, or
* a name that tests monkeypatch on a facade is no longer defined in the facade
  module, nor looked up by name from a function body in the facade module —
  the point at which the patch would silently stop intercepting anything.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import ModuleType

import ui_clone.section_capture as section_capture
import ui_clone.section_compare_sections as section_compare_sections

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
FACADES: dict[str, ModuleType] = {
    "section_capture": section_capture,
    "section_compare_sections": section_compare_sections,
}
_STRING_TARGET = re.compile(r"^ui_clone\.(section_capture|section_compare_sections)\.(\w+)$")


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
