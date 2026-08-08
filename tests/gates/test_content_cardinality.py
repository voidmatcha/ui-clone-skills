"""content-cardinality — repeated-group count parity (omx postmortem).

The scratch clone shipped 9 hardcoded storyCards where the ref rendered the
full list — AE/section masks can hide a short repeated list, and no gate
counted rendered group members. Signatures derive from REF ground truth
(dom-scaffold.json sibling groups >=3 sharing tag+class), counts come from
the impl's RENDERED runtime DOM with a visible-box filter (source arrays,
metadata strings, or hidden duplicate DOM must not satisfy it).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ui_clone.content_cardinality import (
    cardinality_verdict,
    repeated_group_signatures,
    with_live_reference_counts,
)

from ._helpers import _project_root, _run_verification_plan


def _node(tag: str, cls: str, children: list | None = None) -> dict:
    return {"tag": tag, "class": cls, "children": children or []}


def test_repeated_group_signatures_finds_sibling_groups() -> None:
    tree = _node(
        "div",
        "page",
        [
            _node(
                "section",
                "stories",
                [_node("article", "storyCard") for _ in range(9)]
                + [_node("h2", "title")],
            ),
            _node("section", "hero", [_node("div", "x"), _node("div", "x")]),
        ],
    )
    sigs = repeated_group_signatures({"tree": tree})
    assert len(sigs) == 1, sigs
    s = sigs[0]
    assert s["childTag"] == "article"
    assert s["childClass"] == "storyCard"
    assert s["parentClass"] == "stories"
    assert s["refCount"] == 9


def test_groups_below_three_are_ignored() -> None:
    tree = _node("div", "page", [_node("li", "item"), _node("li", "item")])
    assert repeated_group_signatures({"tree": tree}) == []


def test_cardinality_verdict_fails_short_group() -> None:
    sigs = [
        {
            "parentClass": "stories",
            "childTag": "article",
            "childClass": "storyCard",
            "refCount": 9,
        }
    ]
    out = cardinality_verdict(sigs, {"stories|article|storyCard": 3}, tolerance=0)
    assert out["status"] == "fail"
    assert out["groups"][0]["implCount"] == 3


def test_cardinality_verdict_duplication_is_advisory_not_fail() -> None:
    """Carousel/virtualized duplication (impl > ref) is noted, never a fail."""
    sigs = [
        {
            "parentClass": "rail",
            "childTag": "div",
            "childClass": "slide",
            "refCount": 5,
        }
    ]
    out = cardinality_verdict(sigs, {"rail|div|slide": 10}, tolerance=0)
    assert out["status"] == "pass"
    assert out["groups"][0].get("advisory") == "duplication"


def test_cardinality_verdict_tolerance() -> None:
    sigs = [
        {
            "parentClass": "g",
            "childTag": "li",
            "childClass": "i",
            "refCount": 10,
        }
    ]
    assert (
        cardinality_verdict(sigs, {"g|li|i": 9}, tolerance=1)["status"] == "pass"
    )
    assert (
        cardinality_verdict(sigs, {"g|li|i": 8}, tolerance=1)["status"] == "fail"
    )


def test_live_reference_counts_exclude_hidden_responsive_alternates() -> None:
    sigs = [
        {
            "parentClass": "controls",
            "childTag": "div",
            "childClass": "control",
            "refCount": 4,
        }
    ]
    adjusted = with_live_reference_counts(
        sigs,
        {"controls|div|control": 3},
    )
    assert adjusted[0]["refCount"] == 3
    assert adjusted[0]["scaffoldRefCount"] == 4
    assert cardinality_verdict(
        adjusted,
        {"controls|div|control": 3},
    )["status"] == "pass"


def test_verification_plan_emits_cardinality_row(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "dom-scaffold.json").write_text('{"tree": {}}')
    plan = _run_verification_plan(ref)
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "content-cardinality" in rows, sorted(rows)
    row = rows["content-cardinality"]
    assert row["produces"] == "content-cardinality.json"
    assert row["severity"] == "block"
    assert row["tier"] == "standard"


def test_run_required_checks_has_cardinality_signature() -> None:
    dispatcher = _project_root() / "scripts" / "verify" / "build_required_dispatch.py"
    assert "content-cardinality-check.sh" in dispatcher.read_text(encoding="utf-8")


# ── runtime counting criterion (loop-e2e-9 self-fail evidence) ──────────────
#
# tmp/ref/realfood-e2e-9/brief/new-gate-self-fail-evidence.json: the visible-
# box floor `r.width>2 && r.height>2` makes a faithful 1px-tall
# <hr.dga_rfw_divider> permanently uncountable — the LIVE REF failed against
# its own dom-scaffold ground truth (4 -> 0). For thin elements
# (boxHeight<=2) the criterion must be presence (display!=none &&
# visibility!=hidden, real width) instead of the box floor, while hidden /
# zero-width stubs stay excluded everywhere (anti-cheat).


def _script_path() -> Path:
    return (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "content-cardinality-check.sh"
    )


def _run_count_js(members: list[dict], tmp_path: Path) -> int:
    """Extract COUNT_JS from the script and run it under node with DOM stubs.

    Each member: {"rect": {"width": W, "height": H}, "style": {"display": D,
    "visibility": V}}. Returns the count the script's criterion produces for
    a single signature group.
    """
    body = _script_path().read_text(encoding="utf-8")
    start = body.index('COUNT_JS="') + len('COUNT_JS="')
    end = body.index('})()"', start) + len("})()")
    js = body[start:end]
    sigs = [{"parentClass": "grp", "childTag": "hr", "childClass": "divider"}]
    js = js.replace("${SIGS_JSON}", json.dumps(sigs))
    count_file = tmp_path / "count.js"
    count_file.write_text(js, encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(
        """
const fs = require('fs');
const members = JSON.parse(process.argv[3]).map((m) => ({
  getBoundingClientRect: () => m.rect,
  __style: m.style,
}));
globalThis.CSS = { escape: (s) => s };
globalThis.window = { CSS: globalThis.CSS };
globalThis.getComputedStyle = (el) => el.__style;
const parent = { querySelectorAll: () => members };
globalThis.document = { body: parent, querySelectorAll: () => [parent] };
const out = eval(fs.readFileSync(process.argv[2], 'utf8'));
console.log(out);
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", str(harness), str(count_file), json.dumps(members)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    counts = json.loads(proc.stdout.strip())
    if isinstance(counts, str):
        counts = json.loads(counts)
    return int(counts["grp|hr|divider"])


def _member(w: float, h: float, display: str = "block", visibility: str = "visible") -> dict:
    return {"rect": {"width": w, "height": h}, "style": {"display": display, "visibility": visibility}}


def test_count_js_counts_faithful_1px_dividers(tmp_path: Path) -> None:
    """The e2e-9 case: four full-width 1px-tall <hr> dividers must count."""
    members = [_member(300, 1)] * 4
    assert _run_count_js(members, tmp_path) == 4


def test_count_js_still_excludes_hidden_thin_elements(tmp_path: Path) -> None:
    """Anti-cheat: display:none / visibility:hidden never count, thin or not."""
    members = [
        _member(300, 1, display="none"),
        _member(300, 1, visibility="hidden"),
        _member(300, 100, display="none"),
    ]
    assert _run_count_js(members, tmp_path) == 0


def test_count_js_still_excludes_zero_width_stubs(tmp_path: Path) -> None:
    """Anti-cheat: a 0x0 (or zero-width) stub is not a rendered member."""
    members = [_member(0, 0), _member(0, 1), _member(2, 1)]
    assert _run_count_js(members, tmp_path) == 0


def test_count_js_counts_normal_visible_boxes(tmp_path: Path) -> None:
    members = [_member(200, 150)] * 3 + [_member(200, 150, visibility="hidden")]
    assert _run_count_js(members, tmp_path) == 3


def test_cardinality_script_contract() -> None:
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "content-cardinality-check.sh"
    )
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    assert "agent-browser" in body and "--session" in body
    assert "(() =>" in body or "(()=>" in body, "evals must be IIFE"
    assert "getBoundingClientRect" in body, "visible-box check required"
    assert '"status"' in body or "status" in body
