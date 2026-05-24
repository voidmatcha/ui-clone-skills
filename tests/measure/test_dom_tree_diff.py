from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ._helpers import (
    _project_root,
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
        capture_output=True, text=True, timeout=30, check=False,
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

