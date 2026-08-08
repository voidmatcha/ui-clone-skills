"""scaffold-to-jsx consumes responsive/sizing-expressions.json (design fix #4).

The transpiler used to bake the captured single-viewport px as inline styles —
the px-baking loss point that freezes clones at the desktop capture width
because inline styles beat the mirrored @media rules. These tests pin the
consumption: vw/calc/linear replace the inline value, breakpoint-jump keeps the
inline base but emits per-breakpoint !important @media overrides, fixed-px is
untouched, forensic className-only mode drops inline box-model for ref-CSS-
covered nodes, and the whole thing is a strict no-op without the artifact.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _blob(impl: Path) -> str:
    src = impl / "src"
    return "".join(p.read_text(encoding="utf-8") for p in src.rglob("*.tsx")) if src.is_dir() else ""


def _run(ref: Path, impl: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess[str]:
    (impl / "src").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120, env=env,
    )


def _ref(tmp_path: Path, structure: dict, sections: list[dict],
         sizing: dict | None = None, plan: dict | None = None,
         css: str | None = None) -> Path:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": sections}), encoding="utf-8")
    if sizing is not None:
        (ref / "responsive").mkdir()
        (ref / "responsive" / "sizing-expressions.json").write_text(
            json.dumps(sizing), encoding="utf-8")
    if plan is not None:
        (ref / "generation-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    if css is not None:
        (ref / "css").mkdir()
        (ref / "css" / "main.css").write_text(css, encoding="utf-8")
    return ref


def _hero_structure(hero_styles: dict) -> dict:
    return {
        "tag": "body", "styles": {},
        "children": [
            {"tag": "section", "class": "hero",
             "styles": {"background-color": "rgb(255,255,255)", **hero_styles},
             "children": [{"tag": "h1", "text": "Title"}]},
        ],
    }


HERO_SECTIONS = [{"index": 0, "tag": "section", "cls": "hero"}]


def test_vw_calc_linear_replace_inline_px(tmp_path: Path) -> None:
    ref = _ref(
        tmp_path,
        {"tag": "body", "styles": {}, "children": [
            {"tag": "section", "class": "hero",
             "styles": {"background-color": "rgb(255,255,255)", "width": "1280px", "font-size": "48px"},
             "children": [{"tag": "h1", "text": "Title"}]},
            {"tag": "section", "class": "cta",
             "styles": {"background-color": "rgb(255,255,255)", "width": "1000px"},
             "children": [{"tag": "p", "text": "Join"}]},
        ]},
        [{"index": 0, "tag": "section", "cls": "hero"},
         {"index": 1, "tag": "section", "cls": "cta"}],
        sizing={
            ".hero": {
                "width": {"type": "vw", "value": "83.3vw", "samples": {"768": 640, "1280": 1067, "1440": 1200}},
                "fontSize": {"type": "linear", "value": "calc(2.5vw + 28px)", "samples": {"768": 47, "1280": 60, "1440": 64}},
            },
            ".cta": {"width": {"type": "calc", "value": "calc(100vw - 280px)", "samples": {"768": 488, "1280": 1000, "1440": 1160}}},
        },
    )
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = _blob(impl)
    assert 'width: "83.3vw"' in blob, blob
    assert 'fontSize: "calc(2.5vw + 28px)"' in blob, blob
    assert 'width: "calc(100vw - 280px)"' in blob, blob
    # The baked desktop px must be gone for those overridden properties.
    assert 'width: "1280px"' not in blob
    assert 'width: "1000px"' not in blob
    assert 'fontSize: "48px"' not in blob


def test_substring_selector_does_not_project_one_width_onto_every_match(
    tmp_path: Path,
) -> None:
    """Responsive sweep buckets are not element identities.

    ``[class*=container]`` may sample a wide media container first; consuming
    that result for every CSS-module class containing "container" stretches
    unrelated fixed-width carousel rows.
    """
    ref = _ref(
        tmp_path,
        {
            "tag": "body",
            "styles": {},
            "children": [
                {
                    "tag": "section",
                    "class": "hero",
                    "styles": {"background-color": "rgb(255,255,255)"},
                    "children": [
                        {
                            "tag": "div",
                            "class": "media_container__wide",
                            "styles": {"width": "901px"},
                            "children": [],
                        },
                        {
                            "tag": "div",
                            "class": "item_title_container__narrow",
                            "styles": {"width": "242px"},
                            "children": [{"tag": "span", "text": "Logo"}],
                        },
                    ],
                },
            ],
        },
        HERO_SECTIONS,
        sizing={
            "[class*=container]": {
                "width": {
                    "type": "linear",
                    "value": "calc(66.7vw + -59px)",
                    "samples": {"768": 453.34, "1280": 794.67, "1440": 901.34},
                },
            },
        },
    )
    impl = tmp_path / "impl"

    proc = _run(ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "calc(66.7vw + -59px)" not in _blob(impl)


def test_no_op_without_artifact(tmp_path: Path) -> None:
    ref = _ref(tmp_path, _hero_structure({"width": "1280px"}), HERO_SECTIONS)  # no sizing
    impl = tmp_path / "impl"
    proc = _run(ref, impl)
    assert proc.returncode == 0
    blob = _blob(impl)
    assert "vw" not in blob or "83.3vw" not in blob  # no expression injected
    stamp = json.loads((ref / "scaffold-base-stamp.json").read_text())
    assert stamp["sizingExpressionsSha256"] is None
    assert stamp["sizingExpressionsConsumed"] is False


def test_sentinel_map_is_not_consumed(tmp_path: Path) -> None:
    # An unfilled finalizer sentinel must be ignored (treated as absent).
    ref = _ref(
        tmp_path, _hero_structure({"width": "1280px"}), HERO_SECTIONS,
        sizing={"schemaVersion": 1, "sentinel": True, "expressions": [],
                "observation": "single-viewport-sizing-summary"},
    )
    impl = tmp_path / "impl"
    _run(ref, impl)
    blob = _blob(impl)
    assert "83.3vw" not in blob
    stamp = json.loads((ref / "scaffold-base-stamp.json").read_text())
    assert stamp["sizingExpressionsConsumed"] is False


def test_breakpoint_jump_emits_media_and_keeps_base(tmp_path: Path) -> None:
    ref = _ref(
        tmp_path,
        {"tag": "body", "styles": {}, "children": [
            {"tag": "section", "class": "hero", "styles": {"background-color": "rgb(255,255,255)"},
             "children": [
                 # Real elements carry a semantic module class alongside utilities;
                 # the @media selector must be a specific COMPOUND, not a lone `.grid`.
                 {"tag": "div", "class": "cards-grid grid", "styles": {"width": "1120px", "padding-left": "40px"},
                  "children": [{"tag": "span", "text": "cards"}]},
             ]},
        ]},
        HERO_SECTIONS,
        sizing={".grid": {
            "width": {"type": "breakpoint-jump", "value": None, "samples": {"768": 700, "1280": 1120, "1440": 1120}},
            "paddingLeft": {"type": "fixed-px", "value": "40px", "samples": {"768": 40, "1280": 40, "1440": 40}},
        }},
    )
    impl = tmp_path / "impl"
    _run(ref, impl)
    blob = _blob(impl)
    assert "@media (max-width: 768px) { .cards-grid.grid { width: 700px !important; } }" in blob, blob
    assert "@media (min-width: 769px) and (max-width: 1280px) { .cards-grid.grid { width: 1120px !important; } }" in blob
    # The rule must NOT be keyed on the bare generic `.grid` (would poison the page).
    assert "{ .grid { width: 700px !important; }" not in blob
    # fixed-px stays inline, unaltered.
    assert 'paddingLeft: "40px"' in blob


def test_breakpoint_jump_only_overrides_captured_px_values(tmp_path: Path) -> None:
    ref = _ref(
        tmp_path,
        {"tag": "body", "styles": {}, "children": [
            {"tag": "section", "class": "hero",
             "styles": {"background-color": "rgb(255,255,255)"},
             "children": [
                 {"tag": "div", "class": "footer-fluid",
                  "styles": {"width": "100%"},
                  "children": [{"tag": "span", "text": "fluid"}]},
                 {"tag": "div", "class": "footer-fixed",
                  "styles": {"width": "1120px"},
                  "children": [{"tag": "span", "text": "fixed"}]},
             ]},
        ]},
        HERO_SECTIONS,
        sizing={
            ".footer-fluid": {
                "width": {
                    "type": "breakpoint-jump",
                    "value": None,
                    "samples": {"768": 768, "1280": 1280, "1440": 1440},
                },
            },
            ".footer-fixed": {
                "width": {
                    "type": "breakpoint-jump",
                    "value": None,
                    "samples": {"768": 700, "1280": 1120, "1440": 1120},
                },
            },
        },
    )
    impl = tmp_path / "impl"
    _run(ref, impl)
    blob = _blob(impl)

    assert 'width: "100%"' in blob
    assert ".footer-fluid { width: 768px !important;" not in blob
    assert ".footer-fluid { width: 1280px !important;" not in blob
    assert (
        "@media (max-width: 768px) { .footer-fixed "
        "{ width: 700px !important; } }"
    ) in blob
    assert (
        "@media (min-width: 769px) and (max-width: 1280px) { .footer-fixed "
        "{ width: 1120px !important; } }"
    ) in blob


def test_breakpoint_generic_only_class_does_not_poison(tmp_path: Path) -> None:
    """Regression for the eBay grid-tile-oversize bug: a node whose only class is a
    generic utility (`flex`) must NOT emit a global `.flex { width: N !important }`
    rule — that rule lives in the page-wide <style> block and would force every
    flex element (product tiles, etc.) to this element's width. The rule is dropped;
    the inline px base survives."""
    ref = _ref(
        tmp_path,
        {"tag": "body", "styles": {}, "children": [
            {"tag": "section", "class": "hero", "styles": {"background-color": "rgb(255,255,255)"},
             "children": [
                 {"tag": "div", "class": "flex", "styles": {"width": "470px"},
                  "children": [{"tag": "span", "text": "banner"}]},
             ]},
        ]},
        HERO_SECTIONS,
        sizing={".flex": {
            "width": {"type": "breakpoint-jump", "value": None, "samples": {"768": 278, "1280": 470, "1440": 470}},
        }},
    )
    impl = tmp_path / "impl"
    _run(ref, impl)
    blob = _blob(impl)
    # No poisonous global rule keyed on the bare generic utility.
    assert ".flex { width:" not in blob, blob
    assert "width: 470px !important" not in blob
    assert "width: 278px !important" not in blob


def test_forensic_plan_activates_classname_only_boxmodel(tmp_path: Path) -> None:
    structure = _hero_structure({
        "display": "grid",
        "width": "1280px",
        "padding-left": "64px",
        "padding": "80px 48px",
        "margin": "0px -24px",
        "gap": "80px 48px",
        "row-gap": "80px",
        "column-gap": "48px",
        "grid-template-columns": "264px 1fr",
        "grid-template-rows": "120px 1fr",
        "color": "rgb(0,0,0)",
    })
    plan = {"forensicPreservation": {"required": True, "strategy": "ref-derived-jsx-with-local-css"}}
    css = (
        ".hero{width:1280px;padding:80px 48px;margin:0 -24px;"
        "gap:80px 48px;grid-template-columns:264px 1fr;"
        "grid-template-rows:120px 1fr}"
        "@media(max-width:768px){.hero{width:100%}}"
    )
    ref = _ref(tmp_path, structure, HERO_SECTIONS, plan=plan, css=css)

    # The plan is the default activation contract: mirrored CSS owns layout.
    impl_on = tmp_path / "impl-on"
    _run(ref, impl_on)
    on = _blob(impl_on)
    assert "paddingLeft" not in on
    assert 'padding: "80px 48px"' not in on
    assert 'margin: "0px -24px"' not in on
    assert 'gap: "80px 48px"' not in on
    assert 'rowGap: "80px"' not in on
    assert 'columnGap: "48px"' not in on
    assert 'gridTemplateColumns: "264px 1fr"' not in on
    assert 'gridTemplateRows: "120px 1fr"' not in on
    assert 'width: "' not in on or "100%" not in on  # no inline width baked
    assert 'color: "rgb(0,0,0)"' in on  # typography still inline

    # Explicit kill switch: flow props outside the narrow default un-bake stay.
    impl_off = tmp_path / "impl-off"
    _run(ref, impl_off, {"UI_CLONE_FORENSIC_CLASSNAME_ONLY": "0"})
    off = _blob(impl_off)
    assert 'margin: "0px -24px"' in off
    assert 'gap: "80px 48px"' in off
    assert 'rowGap: "80px"' in off
    assert 'columnGap: "48px"' in off
    assert 'color: "rgb(0,0,0)"' in off


def test_forensic_classname_only_keeps_author_inline_flow_props(
    tmp_path: Path,
) -> None:
    structure = _hero_structure({
        "margin": "0px -24px",
        "padding": "80px 48px",
        "gap": "80px 48px",
    })
    structure["children"][0]["inlineProps"] = ["margin", "gap", "padding-top"]
    plan = {
        "forensicPreservation": {
            "required": True,
            "strategy": "ref-derived-jsx-with-local-css",
        },
    }
    ref = _ref(
        tmp_path,
        structure,
        HERO_SECTIONS,
        plan=plan,
        css=".hero{margin:0;padding:0;gap:1rem}",
    )
    impl = tmp_path / "impl"
    _run(ref, impl)
    blob = _blob(impl)
    assert 'margin: "0px -24px"' in blob
    assert 'gap: "80px 48px"' in blob
    assert 'padding: "80px 48px"' not in blob
    assert 'paddingTop: "80px"' in blob


def test_forensic_classname_only_strips_classless_sole_child_wrapper(
    tmp_path: Path,
) -> None:
    structure = {
        "tag": "body",
        "styles": {},
        "children": [{
            "tag": "section",
            "class": "products",
            "styles": {},
            "children": [{
                "tag": "div",
                "class": "",
                "styles": {"width": "69.203px", "min-height": "30px"},
                "children": [{
                    "tag": "h2",
                    "class": "h3",
                    "text": "Actions",
                    "styles": {"font-size": "18px", "line-height": "27px"},
                    "children": [],
                }],
            }],
        }],
    }
    plan = {
        "forensicPreservation": {
            "required": True,
            "strategy": "ref-derived-jsx-with-local-css",
        },
    }
    ref = _ref(
        tmp_path,
        structure,
        [{"index": 0, "tag": "section", "cls": "products"}],
        plan=plan,
        css=".products{display:grid}.h3{font-size:18px}",
    )
    impl = tmp_path / "impl"
    _run(ref, impl, {"UI_CLONE_FORENSIC_CLASSNAME_ONLY": "1"})
    blob = _blob(impl)
    assert 'width: "69.203px"' not in blob, blob
    assert 'minHeight: "30px"' not in blob, blob
    assert "<div>" in blob, "classless structural wrapper must remain in JSX"
    assert "Actions" in blob


def test_forensic_flag_without_strategy_is_inactive(tmp_path: Path) -> None:
    # Flag set but plan strategy is standard rebuild → forensic mode stays off.
    structure = _hero_structure({"width": "1280px", "margin": "0px -24px"})
    plan = {"forensicPreservation": {"required": False, "strategy": "standard-react-rebuild"}}
    css = ".hero{width:1280px;margin:0 -24px}"
    ref = _ref(tmp_path, structure, HERO_SECTIONS, plan=plan, css=css)
    impl = tmp_path / "impl"
    _run(ref, impl, {"UI_CLONE_FORENSIC_CLASSNAME_ONLY": "1"})
    blob = _blob(impl)
    assert 'margin: "0px -24px"' in blob  # not dropped — strategy gates it


def test_stamp_records_sizing_sha_when_present(tmp_path: Path) -> None:
    ref = _ref(
        tmp_path, _hero_structure({"width": "1280px"}), HERO_SECTIONS,
        sizing={".hero": {"width": {"type": "vw", "value": "83.3vw", "samples": {"768": 640, "1280": 1067, "1440": 1200}}}},
    )
    impl = tmp_path / "impl"
    _run(ref, impl)
    stamp = json.loads((ref / "scaffold-base-stamp.json").read_text())
    assert isinstance(stamp["sizingExpressionsSha256"], str) and len(stamp["sizingExpressionsSha256"]) == 64
    assert stamp["sizingExpressionsConsumed"] is True


# ── base-file resolution (addendum: prefer structure.merged.json) ─────────────

def test_falls_back_to_structure_json(tmp_path: Path) -> None:
    ref = _ref(tmp_path, _hero_structure({}), HERO_SECTIONS)  # only structure.json
    impl = tmp_path / "impl"
    _run(ref, impl)
    stamp = json.loads((ref / "scaffold-base-stamp.json").read_text())
    assert stamp["baseFile"] == "structure.json"
    # baseFileSha256 mirrors structureSha256 (the consumed file's sha).
    assert stamp["baseFileSha256"] == stamp["structureSha256"]


def test_prefers_structure_merged_json_when_present(tmp_path: Path) -> None:
    ref = _ref(tmp_path, _hero_structure({}), HERO_SECTIONS)  # structure.json
    # A reconciled merged base with a section only IT carries.
    merged = {
        "tag": "body", "styles": {},
        "children": [
            {"tag": "section", "class": "hero", "styles": {"background-color": "rgb(255,255,255)"},
             "children": [{"tag": "h1", "text": "Title"}]},
            {"tag": "section", "class": "reconciled",
             "styles": {"background-color": "rgb(255,255,255)"},
             "children": [{"tag": "p", "text": "MERGED_ONLY_MARKER"}]},
        ],
    }
    (ref / "structure.merged.json").write_text(json.dumps(merged), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": "section", "cls": "hero"},
        {"index": 1, "tag": "section", "cls": "reconciled"},
    ]}), encoding="utf-8")
    import hashlib
    merged_sha = hashlib.sha256((ref / "structure.merged.json").read_bytes()).hexdigest()

    impl = tmp_path / "impl"
    _run(ref, impl)
    blob = _blob(impl)
    stamp = json.loads((ref / "scaffold-base-stamp.json").read_text())
    assert stamp["baseFile"] == "structure.merged.json", stamp
    assert stamp["structureSha256"] == merged_sha  # consumed-file sha == merged
    assert stamp["baseFileSha256"] == merged_sha
    # The merged-only content must have been transpiled (proves it was consumed).
    assert "MERGED_ONLY_MARKER" in blob
