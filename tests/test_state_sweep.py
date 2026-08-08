"""Operator `state sweep` bulk-abandon of stale WIP refs (fix #3).

Dry-run by default; refuses success-shaped result.txt; only touches refs whose
last_updated is older than the threshold and that are not already terminal or
verified.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from ui_clone.state import sweep_stale_refs


def _iso(days_ago: float) -> str:
    return (_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _ref(root: Path, name: str, *, days_ago: float, result_txt: str | None = None,
         terminal: bool = False, verified: bool = False) -> Path:
    d = root / name
    d.mkdir(parents=True)
    state: dict[str, object] = {
        "component": name,
        "current_gate": "post-implement",
        "last_updated": _iso(days_ago),
    }
    if terminal:
        state["terminalState"] = {"status": "failed", "category": "x", "reason": "y"}
    (d / "pipeline-state.json").write_text(json.dumps(state), encoding="utf-8")
    if verified:
        (d / "verify-stamp.json").write_text("{}", encoding="utf-8")
    if result_txt is not None:
        (d / "sections").mkdir()
        (d / "sections" / "result.txt").write_text(result_txt, encoding="utf-8")
    return d


def _actions(rows: list[dict[str, object]]) -> dict[str, str]:
    return {Path(str(r["ref"])).name: str(r["action"]) for r in rows}


def test_dry_run_lists_stale_fail_ref(tmp_path: Path) -> None:
    root = tmp_path / "tmp" / "ref"
    _ref(root, "stale-fail", days_ago=5, result_txt="evo-grid | ❌ FAIL\n")
    rows = sweep_stale_refs(root, 3, execute=False)
    assert _actions(rows) == {"stale-fail": "would-abandon"}
    # dry-run must NOT write a terminal state
    st = json.loads((root / "stale-fail" / "pipeline-state.json").read_text())
    assert "terminalState" not in st


def test_execute_marks_abandoned(tmp_path: Path) -> None:
    root = tmp_path / "tmp" / "ref"
    _ref(root, "stale-fail", days_ago=5,
         result_txt="| evo-grid | — | ❌ FAIL |\n**Result: 1 PASS, 2 FAIL**\n")
    rows = sweep_stale_refs(root, 3, execute=True)
    assert _actions(rows)["stale-fail"] == "abandoned"
    st = json.loads((root / "stale-fail" / "pipeline-state.json").read_text())
    assert st["terminalState"]["status"] == "abandoned"
    assert st["terminalState"]["category"] == "stale-reaped"


def test_fresh_ref_skipped(tmp_path: Path) -> None:
    root = tmp_path / "tmp" / "ref"
    _ref(root, "fresh", days_ago=0, result_txt="❌ FAIL\n")
    rows = sweep_stale_refs(root, 3, execute=True)
    assert _actions(rows) == {}
    st = json.loads((root / "fresh" / "pipeline-state.json").read_text())
    assert "terminalState" not in st


def test_success_shaped_result_refused(tmp_path: Path) -> None:
    root = tmp_path / "tmp" / "ref"
    # Uppercase "0 FAIL" success summary — the naive `"FAIL" in txt` guard would
    # have wrongly swept this; the ❌/MISSING glyph check correctly refuses it.
    _ref(root, "stale-success", days_ago=9,
         result_txt="| evo-grid | ✅ |\n**Result: 12 PASS, 0 FAIL**\n")
    rows = sweep_stale_refs(root, 3, execute=True)
    assert _actions(rows) == {"stale-success": "refused-success-shaped"}
    st = json.loads((root / "stale-success" / "pipeline-state.json").read_text())
    assert "terminalState" not in st


def test_malformed_ref_does_not_abort_sweep(tmp_path: Path) -> None:
    root = tmp_path / "tmp" / "ref"
    # A ref with a legacy/malformed unclonable_reasons (list of strings, not
    # dicts) that mark_terminal's _mirror_from cannot coerce.
    bad = root / "aaa-malformed"
    bad.mkdir(parents=True)
    (bad / "pipeline-state.json").write_text(
        json.dumps({
            "component": "aaa-malformed",
            "current_gate": "post-implement",
            "last_updated": _iso(9),
            "unclonable_reasons": ["not-a-dict-legacy-string"],
        }),
        encoding="utf-8",
    )
    (bad / "sections").mkdir()
    (bad / "sections" / "result.txt").write_text("❌ FAIL\n", encoding="utf-8")
    # A healthy stale ref that sorts AFTER the bad one — must still be abandoned.
    _ref(root, "zzz-good", days_ago=9, result_txt="❌ FAIL\n")

    rows = sweep_stale_refs(root, 3, execute=True)
    actions = _actions(rows)
    assert actions["aaa-malformed"] == "failed"
    assert actions["zzz-good"] == "abandoned"
    good_state = json.loads((root / "zzz-good" / "pipeline-state.json").read_text())
    assert good_state["terminalState"]["status"] == "abandoned"


def test_already_terminal_and_verified_skipped(tmp_path: Path) -> None:
    root = tmp_path / "tmp" / "ref"
    _ref(root, "already-terminal", days_ago=9, terminal=True)
    _ref(root, "already-verified", days_ago=9, verified=True, result_txt="❌ FAIL\n")
    rows = sweep_stale_refs(root, 3, execute=True)
    assert _actions(rows) == {}
