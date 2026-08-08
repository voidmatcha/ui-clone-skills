"""EXTRACT-M2: _ref_targets must bucket viewport-entry reveal triggers.

_ref_targets bucketed only 'hover' and 'scroll' triggers; intersection /
page-load / enter-reveal / in-view triggers fell through and got no
computed-style sampling. A CSS class-toggle reveal (no WAAPI object) then read
as false missing-motion. Those triggers are all sampled by the scroll sweep, so
they belong in the scroll bucket.
"""

from __future__ import annotations

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType

SCRIPT = (Path(__file__).resolve().parents[1]
          / "skills" / "visual-debug" / "scripts" / "transition-spec-extract")


def _load() -> ModuleType:
    loader = SourceFileLoader("transition_spec_extract", str(SCRIPT))
    spec = importlib.util.spec_from_loader("transition_spec_extract", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


_MOD = _load()


def _spec(tmp_path: Path, transitions: list) -> str:
    p = tmp_path / "transition-spec.json"
    p.write_text(json.dumps({"transitions": transitions}), encoding="utf-8")
    return str(p)


def test_intersection_and_reveal_triggers_bucket_as_scroll(tmp_path: Path) -> None:
    path = _spec(tmp_path, [
        {"target": ".fade-in", "trigger": "intersection", "animation": {"type": "fade"}},
        {"target": ".slide-up", "trigger": "enter-reveal", "animation": {}},
        {"target": ".on-load", "trigger": "page load (splash)", "animation": {}},
        {"target": ".hovers", "trigger": "hover", "animation": {}},
        {"target": ".scrolls", "trigger": "scroll-scrub", "animation": {}},
    ])
    hover, scroll = _MOD._ref_targets(path)
    assert ".hovers" in hover
    assert ".scrolls" in scroll
    # The three viewport-entry reveals must be sampled by the scroll sweep.
    assert ".fade-in" in scroll, "intersection trigger must bucket as scroll"
    assert ".slide-up" in scroll, "enter-reveal trigger must bucket as scroll"
    assert ".on-load" in scroll, "page-load trigger must bucket as scroll"


def test_no_targets_from_empty_or_missing_spec() -> None:
    assert _MOD._ref_targets(None) == ([], [])


def test_hover_still_takes_precedence(tmp_path: Path) -> None:
    # A trigger that is a hover reveal must not double-bucket into scroll.
    path = _spec(tmp_path, [
        {"target": ".x", "trigger": "hover", "animation": {"type": "reveal"}},
    ])
    hover, scroll = _MOD._ref_targets(path)
    assert ".x" in hover and ".x" not in scroll
