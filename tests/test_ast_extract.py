"""Tests for the Step D AST-extract helper."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTRACT_PATH = ROOT / "scripts" / "extract"
if str(EXTRACT_PATH) not in sys.path:
    sys.path.insert(0, str(EXTRACT_PATH))

import _ast_extract  # noqa: E402

HAS_ESPRIMA = importlib.util.find_spec("esprima") is not None


def test_has_ast_backend_matches_environment() -> None:
    assert _ast_extract.has_ast_backend() == HAS_ESPRIMA


def test_extract_lenis_options_via_regex_fallback() -> None:
    src = "instance = new Lenis({ smooth: true, duration: 1.2, wheelMultiplier: 1.0 });"
    results = _ast_extract._regex_extract_args(src, "Lenis")
    assert len(results) == 1
    assert results[0] == {"smooth": True, "duration": 1.2, "wheelMultiplier": 1.0}


@pytest.mark.skipif(not HAS_ESPRIMA, reason="esprima not installed")
def test_extract_lenis_options_via_ast() -> None:
    src = """
    const instance = new Lenis({ smooth: true, duration: 1.2, wheelMultiplier: 1.0 });
    """
    results = _ast_extract.extract_js_call_args(src, "Lenis")
    assert len(results) == 1
    assert results[0] == {"smooth": True, "duration": 1.2, "wheelMultiplier": 1.0}


@pytest.mark.skipif(not HAS_ESPRIMA, reason="esprima not installed")
def test_extract_anime_call_via_ast() -> None:
    src = "anime({ targets: '.box', translateX: 250, duration: 800 });"
    results = _ast_extract.extract_js_call_args(src, "anime")
    assert len(results) == 1
    assert results[0] == {"targets": ".box", "translateX": 250, "duration": 800}


@pytest.mark.skipif(not HAS_ESPRIMA, reason="esprima not installed")
def test_dotted_callee_via_ast() -> None:
    src = "const tl = anime.timeline({ easing: 'easeOutExpo' });"
    results = _ast_extract.extract_js_call_args(src, "anime.timeline")
    assert len(results) == 1
    assert results[0] == {"easing": "easeOutExpo"}


@pytest.mark.skipif(not HAS_ESPRIMA, reason="esprima not installed")
def test_ast_handles_nested_objects() -> None:
    src = "new Foo({ outer: { inner: { value: 42 } }, x: 1 });"
    results = _ast_extract.extract_js_call_args(src, "Foo")
    assert len(results) == 1
    assert results[0] == {"outer": {"inner": {"value": 42}}, "x": 1}


@pytest.mark.skipif(not HAS_ESPRIMA, reason="esprima not installed")
def test_extract_assign_value_via_ast() -> None:
    src = "const SCROLL_DURATION = 1200;"
    val = _ast_extract.extract_assign_value(src, "SCROLL_DURATION")
    assert val == "1200"


def test_extract_assign_value_via_regex_fallback() -> None:
    src = "broken syntax SCROLL_DURATION = 1200; more garbage {{{"
    val = _ast_extract.extract_assign_value(src, "SCROLL_DURATION")
    assert val is not None
    assert "1200" in val


def test_unknown_callee_returns_empty() -> None:
    src = "const x = otherFn({ a: 1 });"
    results = _ast_extract.extract_js_call_args(src, "Lenis")
    assert results == []


@pytest.mark.skipif(not HAS_ESPRIMA, reason="esprima not installed")
def test_node_to_python_handles_unary_negation() -> None:
    src = "new Foo({ delta: -5, ratio: -0.5 });"
    results = _ast_extract.extract_js_call_args(src, "Foo")
    assert len(results) == 1
    assert results[0] == {"delta": -5, "ratio": -0.5}


@pytest.mark.skipif(not HAS_ESPRIMA, reason="esprima not installed")
def test_node_to_python_handles_arrays() -> None:
    src = "new Foo({ list: [1, 2, 'three'], nested: [[1], [2]] });"
    results = _ast_extract.extract_js_call_args(src, "Foo")
    assert len(results) == 1
    assert results[0] == {"list": [1, 2, "three"], "nested": [[1], [2]]}
