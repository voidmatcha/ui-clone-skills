"""AST-based JS object-literal extraction with regex fallback.

Step D of the positioning rollout: introduce the durability foundation
for replacing regex-driven JS bundle parsing in
`_bundle_extraction.py`'s extractors with a real AST pass. The full
migration is incremental — this module establishes the contract that
new and existing extractors can adopt one call site at a time.

Why this matters:
  Regex like `re.finditer(r"new\\s+Lenis\\s*\\(\\s*(\\{[^{}]{0,500}\\})", text)`
  silently fails on:
    - nested object literals deeper than the bracket char-class allows
    - object literals split across line breaks the .{0,500} doesn't see
    - minified single-line bundles where literals contain `}` in strings
  The brittleness is invisible — failed extractions just become missing
  motion parameters in transition-spec.json, which downstream gates
  cannot distinguish from "site has no motion".

Why opt-in instead of hard dep:
  esprima is a pure-Python port (~1MB) but adding it to the core
  dependency surface forces every public user of ui-clone-skills to
  install it. With graceful fallback to regex, esprima becomes a
  "durability upgrade" the user enables via `uv sync --group ast`
  when they hit a regex extraction false-negative.

Public API:
    extract_js_call_args(source, callee) -> list[dict | None]
    extract_assign_value(source, identifier) -> str | None
    has_ast_backend() -> bool
"""

from __future__ import annotations

import re
from typing import Any

try:
    import esprima  # type: ignore[import-untyped]
    _ESPRIMA_AVAILABLE = True
except Exception:  # noqa: BLE001
    esprima = None  # type: ignore[assignment]
    _ESPRIMA_AVAILABLE = False


def has_ast_backend() -> bool:
    return _ESPRIMA_AVAILABLE


def _esprima_parse(source: str) -> Any | None:
    if not _ESPRIMA_AVAILABLE:
        return None
    try:
        return esprima.parseScript(source, options={"tolerant": True, "loc": False})
    except Exception:  # noqa: BLE001
        return None


def _node_to_python(node: Any) -> Any:
    if node is None:
        return None
    t = getattr(node, "type", None)
    if t == "Literal":
        return getattr(node, "value", None)
    if t == "UnaryExpression":
        operator = getattr(node, "operator", "")
        arg = _node_to_python(getattr(node, "argument", None))
        if isinstance(arg, int | float) and operator == "-":
            return -arg
        if isinstance(arg, int | float) and operator == "+":
            return arg
        return None
    if t == "ObjectExpression":
        out: dict[str, Any] = {}
        for prop in getattr(node, "properties", []):
            key_node = getattr(prop, "key", None)
            key = (
                getattr(key_node, "name", None)
                or getattr(key_node, "value", None)
            )
            if not isinstance(key, str):
                continue
            out[key] = _node_to_python(getattr(prop, "value", None))
        return out
    if t == "ArrayExpression":
        return [_node_to_python(el) for el in getattr(node, "elements", [])]
    if t == "Identifier":
        return {"__identifier__": getattr(node, "name", "")}
    return None


def _callee_chain(node: Any) -> str:
    if node is None:
        return ""
    if getattr(node, "type", None) == "Identifier":
        return getattr(node, "name", "") or ""
    if getattr(node, "type", None) == "MemberExpression":
        obj = _callee_chain(getattr(node, "object", None))
        prop = getattr(getattr(node, "property", None), "name", "") or ""
        return f"{obj}.{prop}" if obj else prop
    return ""


def extract_js_call_args(source: str, callee: str) -> list[dict[str, Any] | None]:
    tree = _esprima_parse(source)
    if tree is None:
        return _regex_extract_args(source, callee)

    results: list[dict[str, Any] | None] = []

    def _walk(node: Any) -> None:
        if node is None or not hasattr(node, "type"):
            return
        if node.type in ("CallExpression", "NewExpression"):
            if _callee_chain(getattr(node, "callee", None)) == callee:
                args = getattr(node, "arguments", [])
                if args:
                    results.append(_node_to_python(args[0]))
        for attr in (
            "body", "argument", "init", "left", "right",
            "test", "consequent", "alternate", "expression", "callee",
            "declarations", "arguments", "properties", "elements",
        ):
            child = getattr(node, attr, None)
            if isinstance(child, list):
                for c in child:
                    _walk(c)
            else:
                _walk(child)

    _walk(tree)
    return results


def _regex_extract_args(source: str, callee: str) -> list[dict[str, Any] | None]:
    results: list[dict[str, Any] | None] = []
    pattern = re.compile(
        rf"(?:new\s+)?{re.escape(callee)}\s*\(\s*(\{{[^{{}}]{{0,500}}\}})"
    )
    for m in pattern.finditer(source):
        results.append(_try_parse_object_literal(m.group(1)))
    return results


_KV = re.compile(r"['\"]?(\w+)['\"]?\s*:\s*([^,}\n]+)")


def _try_parse_object_literal(raw: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    inner = raw.strip().lstrip("{").rstrip("}")
    for m in _KV.finditer(inner):
        key = m.group(1)
        val = m.group(2).strip().rstrip(",").strip()
        if (val.startswith("'") and val.endswith("'")) or (
            val.startswith('"') and val.endswith('"')
        ):
            out[key] = val[1:-1]
            continue
        if val in ("true", "false"):
            out[key] = val == "true"
            continue
        if val == "null":
            out[key] = None
            continue
        try:
            if "." in val:
                out[key] = float(val)
            else:
                out[key] = int(val)
            continue
        except ValueError:
            pass
        out[key] = val
    return out


def extract_assign_value(source: str, identifier: str) -> str | None:
    tree = _esprima_parse(source)
    if tree is None:
        m = re.search(
            rf"\b{re.escape(identifier)}\s*=\s*([^;\n]+)", source
        )
        return m.group(1).strip() if m else None

    found: list[str] = []

    def _walk(node: Any) -> None:
        if node is None or not hasattr(node, "type"):
            return
        if node.type == "VariableDeclarator":
            id_node = getattr(node, "id", None)
            if getattr(id_node, "name", None) == identifier:
                val = _node_to_python(getattr(node, "init", None))
                if val is not None:
                    found.append(str(val))
        if node.type == "AssignmentExpression":
            left = getattr(node, "left", None)
            if getattr(left, "name", None) == identifier:
                val = _node_to_python(getattr(node, "right", None))
                if val is not None:
                    found.append(str(val))
        for attr in (
            "body", "argument", "init", "left", "right",
            "test", "consequent", "alternate", "expression", "callee",
            "declarations", "arguments", "properties", "elements",
        ):
            child = getattr(node, attr, None)
            if isinstance(child, list):
                for c in child:
                    _walk(c)
            else:
                _walk(child)

    _walk(tree)
    return found[0] if found else None
