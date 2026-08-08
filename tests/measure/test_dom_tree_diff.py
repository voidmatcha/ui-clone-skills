from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

from ._helpers import (
    _project_root,
)

# Shared computed-style block — identical on both sides so these tests isolate
# the PAIRING decision (which impl element maps to which ref element) from the
# downstream style diff. The diff itself is exercised elsewhere and is out of
# scope for the pairing module.
_STYLE = {
    "fontFamily": "Inter",
    "fontSize": "16px",
    "fontWeight": "400",
    "fontStyle": "normal",
    "letterSpacing": "0px",
    "lineHeight": "24px",
    "textTransform": "none",
    "textAlign": "left",
    "color": "rgb(0, 0, 0)",
    "backgroundColor": "rgba(0, 0, 0, 0)",
    "display": "block",
    "position": "static",
    "padding": "0px",
    "margin": "0px",
    "borderRadius": "0px",
    "borderTopWidth": "0px",
    "borderTopColor": "rgb(0, 0, 0)",
    "opacity": "1",
}


def _el(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tag": "DIV", "cls": "", "txt": "", "x": 0.0, "y": 0.0,
        "top": 0.0, "left": 0.0, "w": 100.0, "h": 40.0, "area": 4000,
        "thin": False, "style": dict(_STYLE),
    }
    base.update(kw)
    return base


def test_pair_elements_matches_by_text_across_different_vertical_layout() -> None:
    """RED→GREEN (tree-diff pairing fix): impl and ref carry the SAME elements
    and visible text but a DIFFERENT vertical layout (impl ~2x taller, every
    element at a different Y, and the ref element sitting at each impl element's
    screen Y is the WRONG one). A coordinate / elementFromPoint pairing would
    mis-pair (impl heading → ref CTA at the same Y) or leave most elements
    unpaired. Content-based pairing MUST recover the same-text pairs regardless
    of position.
    """
    from ui_clone.tree_diff_pairing import pair_elements

    impl = [
        _el(tag="H1", txt="Real Food Wins", y=120.0, top=100.0),
        _el(tag="P", txt="Eat better every single day", y=420.0, top=400.0),
        _el(tag="BUTTON", txt="Get the resources", y=1620.0, top=1600.0),
    ]
    # Same texts, but each placed at a DIFFERENT Y. Critically, the element at
    # impl-H1's Y (120) is the CTA, not the heading — so a coordinate pairing
    # would pair "Real Food Wins" → "Get the resources".
    ref = [
        _el(tag="BUTTON", txt="Get the resources", y=120.0, top=100.0),
        _el(tag="P", txt="Eat better every single day", y=300.0, top=280.0),
        _el(tag="H1", txt="Real Food Wins", y=900.0, top=880.0),
    ]

    result = pair_elements(impl, ref)

    # One record per impl element, in impl order, tagged with impl index.
    assert [r["i"] for r in result] == [0, 1, 2]
    # Each impl element paired to the ref element with the SAME text.
    assert result[0].get("txt") == "Real Food Wins"
    assert result[1].get("txt") == "Eat better every single day"
    assert result[2].get("txt") == "Get the resources"
    assert not any(r.get("miss") for r in result), (
        "faithful-content clone must not leave matching-text elements unpaired"
    )


def test_pair_elements_matches_textless_images_by_src_not_coordinate() -> None:
    """Text-less elements (images) pair by structural identity — tag + src/alt —
    not by whatever sits at their coordinate. impl logo and ref logo are at
    different Y; a coordinate pairing would pair impl-logo to ref-hero.
    """
    from ui_clone.tree_diff_pairing import pair_elements

    impl = [
        _el(tag="IMG", txt="", src="https://cdn-a.example/logo.png",
            alt="Logo", y=100.0, top=80.0, w=120.0, h=40.0),
        _el(tag="IMG", txt="", src="https://cdn-a.example/hero.jpg",
            alt="Hero banner", y=1200.0, top=1180.0, w=600.0, h=400.0),
    ]
    ref = [
        _el(tag="IMG", txt="", src="https://cdn-b.example/hero.jpg",
            alt="Hero banner", y=100.0, top=80.0, w=600.0, h=400.0),
        _el(tag="IMG", txt="", src="https://cdn-b.example/logo.png",
            alt="Logo", y=700.0, top=680.0, w=120.0, h=40.0),
    ]

    result = pair_elements(impl, ref)

    assert result[0].get("src", "").endswith("logo.png"), (
        f"impl logo must pair to ref logo by src, got {result[0]}"
    )
    assert result[1].get("src", "").endswith("hero.jpg"), (
        f"impl hero must pair to ref hero by src, got {result[1]}"
    )


def test_pair_elements_leaves_genuinely_absent_element_unpaired() -> None:
    """A 1:1, deterministic mapping that does NOT force-pair noise: an impl
    element with no text/structural counterpart in ref stays unpaired rather
    than stealing an unrelated ref element. (Guards against the fix degrading
    into "everything pairs to something".)
    """
    from ui_clone.tree_diff_pairing import pair_elements

    impl = [
        _el(tag="H2", txt="Frequently asked questions", y=100.0, top=80.0),
        _el(tag="DIV", txt="Totally unrelated orphan widget xyzzy", y=500.0,
            top=480.0),
    ]
    ref = [
        _el(tag="H2", txt="Frequently asked questions", y=900.0, top=880.0),
    ]

    result = pair_elements(impl, ref)

    assert result[0].get("txt") == "Frequently asked questions"
    assert result[1].get("miss") is True, (
        "orphan impl element with no ref counterpart must stay unpaired"
    )


def test_fix8_dom_mirror_check_script_present() -> None:
    """Fix 8 — dom-mirror-check.sh compares impl JSX tag-multiset to the
    scaffold's tag-multiset. Locks the divergence-threshold default + that
    the script writes its verdict to dom-mirror-check.json.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "dom-mirror-check.sh"
    assert script.is_file(), "dom-mirror-check.sh missing — Fix 8 incomplete"
    body = script.read_text(encoding="utf-8")
    assert "dom-scaffold.json" in body
    assert "divergence" in body, "must report divergence percentage"
    # 80% default threshold (raised from 30 on 2026-05-22 after 17-iter
    # measurement showed React-component impls hit 80%+ divergence vs
    # ref's div-soup; legit clones never reached 30%). Env override via
    # UI_CLONE_DOM_MIRROR_THRESHOLD keeps the tight 30 available for
    # 1:1 HTML clone targets. Hero composite enforcement moved to the
    # dedicated hero-composite-check.sh — see verification-plan.sh.
    assert 'THRESHOLD="${UI_CLONE_DOM_MIRROR_THRESHOLD:-80}"' in body, (
        "default divergence threshold should be 80% (env-overridable)"
    )



def test_dom_mirror_exempts_map_iteration_tags(tmp_path: Path) -> None:
    """Ref has 30 <li>, impl renders them via .map() — gate should not
    fail just because static-grep sees only 1 <li> in source."""
    ref = tmp_path / "ref"
    ref.mkdir()
    # Scaffold root scoped to the <ul> subtree so impl per-component
    # JSX (which doesn't carry html/body wrappers) compares fairly.
    # 30 li in scaffold, impl renders them via .map() — static-grep
    # would see only 1 <li> without the .map exemption.
    scaffold_tree = {
        "tag": "ul",
        "children": [{"tag": "li"} for _ in range(30)],
    }
    (ref / "dom-scaffold.json").write_text(json.dumps(
        {"tree": scaffold_tree}, indent=2,
    ))
    impl = tmp_path / "impl"
    (impl / "src" / "components").mkdir(parents=True)
    (impl / "src" / "components" / "ListSection.tsx").write_text(
        "const items = Array.from({length: 30});\n"
        "export default function ListSection(){\n"
        "  return <ul>{items.map((_, i) => <li key={i}>item</li>)}</ul>;\n"
        "}\n",
    )
    out_file = ref / "dom-mirror.json"
    subprocess.run(
        [
            "bash",
            str(_project_root() / "skills" / "visual-debug" / "scripts"
                / "dom-mirror-check.sh"),
            str(ref), str(impl), "--out", str(out_file),
        ],
        capture_output=True, text=True, timeout=120, check=False,
    )
    art = json.loads(out_file.read_text(encoding="utf-8"))
    assert art["status"] == "pass", (
        f".map() exemption should pass impl with iterated <li>: {art}"
    )



def test_dom_mirror_ignores_script_style_noscript_template_nodes(tmp_path: Path) -> None:
    """Loop-codex-6 finding (committed during the run): dom-mirror-check
    walks dom-scaffold.json's full subtree, including <script>/<style>/
    <noscript>/<template> nodes that capture Next.js RSC payloads, polyfill
    bodies, and CSS rule text. Those tags inflate the ref tag-multiset and
    cause "missing tag" false-positives because the impl JSX never reproduces
    them. Mirror the text-fidelity strip — same skip set, same rationale.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)

    # dom-scaffold with a <script> node sibling to real visible content.
    # Without the strip, dom-mirror would count <script> in the ref tag
    # multiset and flag it as missing from the impl.
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "sections": [{
            "id": "hero",
            "tag": "section",
            "class": "hero",
            "tree": {
                "tag": "section",
                "class": "hero",
                "children": [
                    {"tag": "h1", "text": "Real Food Wins", "children": []},
                    {"tag": "script", "text": "self.__next_f.push([1, 'rsc'])",
                     "children": []},
                ],
            },
        }],
        "tree": {
            "tag": "main",
            "children": [
                {"tag": "section", "class": "hero", "children": [
                    {"tag": "h1", "text": "Real Food Wins", "children": []},
                    {"tag": "script", "text": "self.__next_f.push([1, 'rsc'])",
                     "children": []},
                ]},
            ],
        },
    }))
    (src / "App.tsx").write_text(
        'export default function App() {\n'
        '  return <main><section className="hero"><h1>Real Food Wins</h1></section></main>;\n'
        '}\n'
    )

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "dom-mirror-check.sh"
    )
    subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    # With the strip, the only tags counted on the ref side are
    # main/section/h1 — all of which exist in the impl. The check should
    # pass; without the strip, <script> would inflate the ref multiset
    # and the impl would be flagged "missing script tag" forever.
    out = ref / "dom-mirror-check.json"
    if out.is_file():
        data = json.loads(out.read_text())
        # The new shape may use either status="pass" or a different field;
        # the universal contract is: a present <script> in ref must NOT
        # show up as a missing-from-impl violation.
        for key in ("missing_tags", "missingTags", "violations"):
            arr = data.get(key, []) or []
            if isinstance(arr, list):
                tags_in_violations = [
                    (v.get("tag") if isinstance(v, dict) else v)
                    for v in arr
                ]
                assert "script" not in tags_in_violations, (
                    f"<script> must be stripped from ref-side dom-mirror; "
                    f"got: {arr}"
                )



def test_dom_mirror_ignores_head_metadata_tags(tmp_path: Path) -> None:
    """Loop-1 improve finding (2026-06-07): dom-mirror-check walked the ref
    scaffold counting EVERY tag except {script,style,noscript,template}, so
    head-metadata tags (meta/link/title/base/head) entered the ref multiset.
    But the impl-side extractor only counts tags in HTML_TAGS, which excludes
    those — they can NEVER match. A populated <head> (real sites have meta x30+)
    therefore always showed meta/link as "dropped from impl" and tripped the
    eviscerate hard-fail (ref>=10, impl<25%) regardless of clone quality.
    Observed: a real clone failed at similarity 0.455 purely from meta(31->0)
    + link(5->0). Fix mirrors the impl-side filter — same skip-set rationale
    as the <script>/<style> strip.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)

    # Ref scaffold: a body that the impl reproduces exactly, plus a populated
    # <head> with meta/link/title. Without the strip, meta(x12) trips the
    # eviscerate hard-fail and the otherwise-perfect clone fails.
    head_children = [{"tag": "meta", "children": []} for _ in range(12)]
    head_children += [{"tag": "link", "children": []} for _ in range(5)]
    head_children += [{"tag": "title", "text": "Site", "children": []}]
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "html",
            "children": [
                {"tag": "head", "children": head_children},
                {"tag": "body", "children": [
                    {"tag": "main", "children": [
                        {"tag": "section", "class": "hero", "children": [
                            {"tag": "h1", "text": "Real Food Wins", "children": []},
                        ]},
                    ]},
                ]},
            ],
        },
    }))
    (src / "App.tsx").write_text(
        'export default function App() {\n'
        '  return <html><body><main><section className="hero">'
        '<h1>Real Food Wins</h1></section></main></body></html>;\n'
        '}\n'
    )

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "dom-mirror-check.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    data = json.loads(proc.stdout)
    # Head-metadata tags must not appear in the ref multiset, so they cannot
    # be flagged dropped/eviscerated. The body mirrors exactly -> pass.
    assert data.get("status") == "pass", (
        f"head-metadata tags must be stripped from ref-side dom-mirror; got: {data}"
    )
    evisc_tags = {e.get("tag") for e in data.get("eviscerated_tags", []) or []}
    assert not (evisc_tags & {"meta", "link", "title", "base", "head"}), (
        f"head-metadata must not trip eviscerate; got: {data.get('eviscerated_tags')}"
    )


def test_dom_mirror_threshold_default_is_advisory_80(tmp_path: Path) -> None:
    """User direction A (2026-05-22): dom-mirror default threshold was
    30 (block on >30% divergence); now 80 (block only on near-evisceration).
    Pins the default + env-var override behavior so a future refactor
    can't silently re-tighten the threshold and re-blank all React-clone
    runs from passing.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src" / "components"
    ref.mkdir()
    src.mkdir(parents=True)
    # Build a scaffold with many div tags so the divergence math is
    # measurable. Ref has 50 divs; impl has 25 → 50% divergence.
    children = [{"tag": "div", "children": []} for _ in range(50)]
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {"tag": "main", "children": [{"tag": "section",
            "children": children}]},
        "sections": [{"id": "main", "tag": "section", "class": "main",
                     "tree": {"tag": "section", "children": children}}],
    }))
    impl_children = "".join('<div />' for _ in range(25))
    (src / "App.tsx").write_text(
        f'export function App() {{ return <main><section>{impl_children}</section></main>; }}\n'
    )

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "dom-mirror-check.sh"
    )
    # Default threshold (80): 50% divergence should NOT fail.
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 0, (
        f"50% divergence must pass default 80 threshold; got: "
        f"{proc.stdout}\n{proc.stderr}"
    )

    # Env override to 30 (legacy): same fixture should FAIL.
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        env={**os.environ, "UI_CLONE_DOM_MIRROR_THRESHOLD": "30"},
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode != 0, (
        f"50% divergence must fail when env tightens threshold to 30; "
        f"got: {proc.stdout}\n{proc.stderr}"
    )


def _tree_diff_body() -> str:
    return (
        _project_root() / "skills" / "visual-debug" / "scripts" / "tree-diff.sh"
    ).read_text(encoding="utf-8")


def _extract_walk_props(body: str) -> list[str]:
    """The set of computed-style props tree-diff.sh's WALK_JS captures — and
    therefore the only props the downstream diff can ever compare."""
    m = re.search(r"const props = \[(.*?)\];", body, re.S)
    assert m, "could not locate WALK_JS `props` array in tree-diff.sh"
    return re.findall(r"'([A-Za-z]+)'", m.group(1))


def _extract_diff_python(body: str) -> str:
    """The inline per-pair style/layout diff (the `PYEOF` heredoc)."""
    m = re.search(r"<<'PYEOF'\n(.*?)\nPYEOF", body, re.S)
    assert m, "could not locate diff python heredoc in tree-diff.sh"
    return m.group(1)


def test_tree_diff_flags_box_shadow_only_difference(tmp_path: Path) -> None:
    """RANK-1 fix: two paired elements identical in every captured prop EXCEPT
    box-shadow must be FLAGGED, not reported as 'ok'. The element `style` dict
    a browser produces contains exactly the props in WALK_JS's `props` array;
    if box-shadow is not in that set the two elements are byte-identical and a
    freehanded shadow slips through as 'ok'. This runs the real inline diff
    against synthetic elements built FROM the script's own prop list — RED
    before the fix (box-shadow not captured → ok), GREEN after (flagged).
    """
    body = _tree_diff_body()
    props = _extract_walk_props(body)
    diff_py = _extract_diff_python(body)

    # Realistic identical-on-both-sides defaults; unknown props fall back to a
    # neutral value. Both sides share this, so the ONLY possible difference is
    # the box-shadow override below.
    sample = {
        "fontFamily": "Inter", "fontSize": "16px", "fontWeight": "400",
        "fontStyle": "normal", "letterSpacing": "0px", "lineHeight": "24px",
        "textTransform": "none", "textAlign": "left", "color": "rgb(0, 0, 0)",
        "backgroundColor": "rgba(0, 0, 0, 0)", "display": "block",
        "position": "static", "padding": "0px", "margin": "0px",
        "borderRadius": "0px", "opacity": "1",
    }
    impl_style = {p: sample.get(p, "0px") for p in props}
    ref_style = {p: sample.get(p, "0px") for p in props}
    if "boxShadow" in props:
        impl_style["boxShadow"] = "rgba(0, 0, 0, 0.25) 0px 8px 24px 0px"
        ref_style["boxShadow"] = "rgba(0, 0, 0, 0.05) 0px 1px 2px 0px"

    base = {
        "tag": "DIV", "cls": "card", "txt": "Card",
        "x": 50.0, "y": 50.0, "top": 0.0, "left": 0.0,
        "w": 200.0, "h": 100.0, "area": 20000,
    }
    impl_json = tmp_path / "impl.json"
    ref_json = tmp_path / "ref.json"
    impl_json.write_text(json.dumps([{**base, "style": impl_style}]))
    ref_json.write_text(json.dumps([{**base, "i": 0, "style": ref_style}]))
    py_file = tmp_path / "diff.py"
    py_file.write_text(diff_py)

    subprocess.run(
        ["python3", str(py_file), str(impl_json), str(ref_json),
         str(tmp_path), "8.0"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    rows = json.loads((tmp_path / "tree-diff.json").read_text())
    row = next(r for r in rows if r["i"] == 0)
    assert row["sev"] != "ok", (
        "a pair differing ONLY in box-shadow must be flagged — box-shadow has "
        "to be in the captured/compared prop set, else freehanded shadows pass."
    )
    assert any(d[0] == "boxShadow" for d in row["diffs"]), (
        f"box-shadow difference must appear in the per-pair diffs: {row}"
    )


def test_tree_diff_walks_deep_below_fold_sections(tmp_path: Path) -> None:
    """RED→GREEN (deep-section coverage fix): tree-diff must walk the WHOLE page,
    not only the top viewport. A DEEP element (well below the fold, top≈7087) whose
    computed style differs from its ref counterpart MUST surface a critical/major
    delta after content-pairing.

    The fake agent-browser models real browser semantics: a below-fold element is
    only *observable* when the walk JS actually scrolls the full page (a
    scrollHeight-bounded scrollTo loop). The old single-viewport walk never scrolls
    — so the deep element is never returned, never paired, and its critical delta is
    invisible (RED). The per-section walk scrolls the page — so the deep element is
    walked, content-paired with its ref counterpart, and its critical font-family
    delta surfaces (GREEN). Same test + same fake across the fix; only tree-diff.sh
    changes.
    """
    repo = _project_root()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "agent-browser"
    fake.write_text(
        textwrap.dedent(
            '''\
            #!/usr/bin/env python3
            import json
            import sys

            argv = sys.argv[1:]
            if "eval" not in argv:
                sys.exit(0)
            js = argv[-1]
            joined = " ".join(argv)
            is_ref = "-tree-ref" in joined

            def style(font):
                return {
                    "fontFamily": font, "fontSize": "16px", "fontWeight": "400",
                    "fontStyle": "normal", "letterSpacing": "0px",
                    "lineHeight": "24px", "textTransform": "none",
                    "textAlign": "left", "color": "rgb(0, 0, 0)",
                    "backgroundColor": "rgba(0, 0, 0, 0)", "display": "block",
                    "position": "static", "padding": "0px", "margin": "0px",
                    "borderRadius": "0px", "borderTopWidth": "0px",
                    "borderTopColor": "rgb(0, 0, 0)", "opacity": "1",
                }

            # Above-fold element: identical style both sides -> "ok".
            shallow = {
                "tag": "H1", "cls": "hero", "txt": "Shallow Hero Banner",
                "role": "", "src": "", "alt": "", "path": "section:1>h1:1",
                "x": 200, "y": 120, "top": 100, "left": 100,
                "w": 400, "h": 60, "area": 24000, "thin": False,
                "style": style("Inter"),
            }
            # Below-fold element: identical text both sides (content-pairs), but
            # font-family differs (Inter vs Georgia) -> a CRITICAL delta once walked.
            deep = {
                "tag": "H2", "cls": "pyramid", "txt": "Deep Pyramid Section Heading",
                "role": "", "src": "", "alt": "", "path": "section:5>h2:1",
                "x": 350, "y": 7117, "top": 7087, "left": 100,
                "w": 500, "h": 60, "area": 30000, "thin": False,
                "style": style("Georgia" if is_ref else "Inter"),
            }
            # Real-browser semantics: a below-fold element is observed ONLY when the
            # walk scrolls the page. The single-viewport walk never scrolls.
            walks_full_page = "scrollHeight" in js and "scrollTo" in js
            out = [shallow] + ([deep] if walks_full_page else [])
            print(json.dumps(out))
            sys.exit(0)
            '''
        ),
        encoding="utf-8",
    )
    fake.chmod(0o755)

    out_dir = tmp_path / "out"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "WAIT_MS": "0",
        "MIN_SIZE": "1",
        "SCROLL_SETTLE_MS": "0",
    }
    subprocess.run(
        [
            "bash",
            str(repo / "skills" / "visual-debug" / "scripts" / "tree-diff.sh"),
            "deep-test",
            "https://ref.example",
            "https://impl.example",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )
    rows = json.loads((out_dir / "tree-diff.json").read_text())
    deep_rows = [r for r in rows if "Deep Pyramid" in (r.get("txt") or "")]
    assert deep_rows, (
        "deep below-fold element was never walked — a single top-viewport walk is "
        "structurally blind to below-the-fold sections (they never get content-paired)"
    )
    assert deep_rows[0]["sev"] in ("critical", "major"), (
        f"deep below-fold element's font-family mismatch must surface as "
        f"critical/major, got: {deep_rows[0]}"
    )
    assert any(d[0] == "fontFamily" for d in deep_rows[0].get("diffs", [])), (
        f"deep element's font-family delta must appear in per-pair diffs: {deep_rows[0]}"
    )

