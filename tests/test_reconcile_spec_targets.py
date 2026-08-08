"""Browser-free fixture tests for spec-target DOM reconciliation.

The classify + merge halves are pure JSON, so they are unit-testable without a
live browser. classify finds spec/hover targets absent from structure.json; merge
splices revealed subtrees under their observed parent into a COPY of the tree
(structure.json is provenance-stamped and never mutated) and accounts for every
unresolved target as a missingSpecTarget.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "extract" / "_reconcile_spec_targets.py"

_spec = importlib.util.spec_from_file_location("_reconcile_spec_targets", HELPER)
assert _spec and _spec.loader
recon = importlib.util.module_from_spec(_spec)
sys.modules["_reconcile_spec_targets"] = recon
_spec.loader.exec_module(recon)


def _write(ref: Path, name: str, obj: object) -> None:
    (ref / name).write_text(json.dumps(obj), encoding="utf-8")


def test_classify_flags_absent_targets(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    _write(ref, "structure.json", {"tag": "body", "class": "", "children": [
        {"tag": "div", "class": "style_hero__PRES", "children": [
            {"tag": "a", "class": "style_link__ONPAGE", "children": []},
        ]},
    ]})
    _write(ref, "transition-spec.json", {"transitions": [
        {"id": "present", "target": ".style_link__ONPAGE"},
        {"id": "absent-cta", "target": ".style_cta_button__mcr8C:not(:disabled)"},
        {"id": "runtime", "target": ".swiper-slide"},   # runtime-injected → skipped
        {"id": "tagonly", "target": "button"},          # tag-only → skipped
    ]})
    _write(ref, "hover-css-rules.json", {"rules": [
        {"selector": ".style_share__taIWJ:hover"},      # absent
        {"selector": ".style_hero__PRES:hover"},        # present (hash base)
    ]})

    result = recon.classify(ref)
    assert result["present"] >= 2  # style_link + style_hero
    missing_leaves = {m["tokens"][-1] for m in result["missing"]}
    assert "style_cta_button__mcr8C" in missing_leaves
    assert "style_share__taIWJ" in missing_leaves
    # runtime-injected and tag-only targets are not reconciliation candidates
    assert not any("swiper" in m["selector"] for m in result["missing"])
    assert all(m["tokens"] for m in result["missing"])


def _base_structure() -> dict:
    return {"tag": "body", "class": "", "children": [
        {"tag": "nav", "class": "style_nav__WRAP", "children": [
            {"tag": "ul", "class": "style_navList__L1", "children": []},
        ]},
        {"tag": "main", "class": "style_main__M1", "children": []},
    ]}


def test_merge_splices_under_matched_parent_without_mutating_structure(tmp_path: Path) -> None:
    structure = _base_structure()
    revealed = [{
        "selector": ".style_menuCta__X", "foundVia": "hover-nav",
        "subtree": {"tag": "a", "class": "style_menuCta__X", "children": [
            {"tag": "span", "class": "style_label__y", "children": [], "text": "Go"}]},
        "ancestors": [{"classes": ["style_navList__L1"]}, {"classes": ["style_nav__WRAP"]}],
    }]
    result = recon.merge(structure, revealed)
    # original untouched (deep copy)
    assert structure["children"][0]["children"][0]["children"] == []
    # spliced under navList
    nav_list = result["tree"]["children"][0]["children"][0]
    assert len(nav_list["children"]) == 1
    assert nav_list["children"][0]["class"] == "style_menuCta__X"
    assert len(result["mergedTargets"]) == 1
    assert result["mergedTargets"][0]["placedUnder"] == "style_navList__L1"
    assert result["missingSpecTargets"] == []


def test_merge_reports_unplaceable_parent(tmp_path: Path) -> None:
    structure = _base_structure()
    revealed = [{
        "selector": ".style_modalBtn__Z", "foundVia": "click",
        "subtreeHtml": "<button class='style_modalBtn__Z'>x</button>",
        "subtree": {"tag": "button", "class": "style_modalBtn__Z", "children": []},
        # ancestors live in an interaction-only overlay absent from the capture
        "ancestors": [{"classes": ["style_dialog__NOPE"]}, {"classes": ["style_overlay__NOPE"]}],
    }]
    result = recon.merge(structure, revealed)
    assert result["mergedTargets"] == []
    assert len(result["missingSpecTargets"]) == 1
    m = result["missingSpecTargets"][0]
    assert m["reason"] == "parent-not-in-structure"
    assert m["subtreeHtml"].startswith("<button")


def test_merge_idempotent_when_already_present(tmp_path: Path) -> None:
    structure = {"tag": "body", "class": "", "children": [
        {"tag": "a", "class": "style_already__HERE", "children": []},
    ]}
    revealed = [{
        "selector": ".style_already__HERE", "foundVia": "rest",
        "subtree": {"tag": "a", "class": "style_already__HERE", "children": []},
        "ancestors": [{"classes": ["nonexistent"]}],
    }]
    result = recon.merge(structure, revealed)
    # top token already in the tree → recorded as already-present, not re-spliced
    assert len(result["tree"]["children"]) == 1
    assert result["mergedTargets"][0]["placedUnder"] == "already-present"
    assert result["missingSpecTargets"] == []


def test_cli_merge_completes_missing_accounting(tmp_path: Path) -> None:
    """The CLI merge folds classify-missing rows into a COMPLETE missingSpecTargets
    set: placed → dropped, revealed-unplaceable → parent-not-in-structure,
    never-revealed → not-revealed-by-stimulation."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _write(ref, "structure.json", _base_structure())
    _write(ref, "spec-targets-missing.json", {"missing": [
        {"id": "a", "selector": ".style_menuCta__X:not(:disabled)", "tokens": ["style_menuCta__X"]},
        {"id": "b", "selector": ".style_modalBtn__Z:hover", "tokens": ["style_modalBtn__Z"]},
        {"id": "c", "selector": ".style_never__Q", "tokens": ["style_never__Q"]},
    ]})
    _write(ref, "revealed.json", {"targets": [
        {"selector": ".style_menuCta__X", "foundVia": "hover-nav",
         "subtree": {"tag": "a", "class": "style_menuCta__X", "children": []},
         "ancestors": [{"classes": ["style_navList__L1"]}]},
        {"selector": ".style_modalBtn__Z", "foundVia": "click",
         "subtreeHtml": "<button/>",
         "subtree": {"tag": "button", "class": "style_modalBtn__Z", "children": []},
         "ancestors": [{"classes": ["style_dialog__NOPE"]}]},
    ]})
    proc = subprocess.run(
        [sys.executable, str(HELPER), "merge", str(ref), str(ref / "revealed.json")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads((ref / "reconcile-report.json").read_text())
    assert report["merged"] == 1  # menuCta placed under navList
    by_sel = {m["selector"]: m for m in report["missingSpecTargets"]}
    # menuCta placed → NOT in missing
    assert ".style_menuCta__X:not(:disabled)" not in by_sel
    # modalBtn revealed but unplaceable
    assert by_sel[".style_modalBtn__Z:hover"]["reason"] == "parent-not-in-structure"
    # never-revealed target
    assert by_sel[".style_never__Q"]["reason"] == "not-revealed-by-stimulation"
    # structure.merged.json written, structure.json untouched
    merged = json.loads((ref / "structure.merged.json").read_text())
    assert merged["children"][0]["children"][0]["children"][0]["class"] == "style_menuCta__X"
    assert json.loads((ref / "structure.json").read_text())["children"][0]["children"][0]["children"] == []
