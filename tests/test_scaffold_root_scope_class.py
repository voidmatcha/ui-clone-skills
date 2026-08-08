"""scaffold-to-jsx must carry the page-root scoping class onto the App root.

Production stylesheets are almost always namespaced under a single page-root
wrapper class — navercorp ships `.navercorp .<x>{…}` for ~85% of its rules
(`.navercorp .main-contents{margin:0 auto}` centers the hero, etc.). That
wrapper is a CHILD of the capture root (`body`), so `structure["class"]` is the
empty body class and the transpiler emitted a class-less App root `<div>`. With
no `.navercorp` ancestor in the clone, EVERY `.navercorp `-scoped ref rule fails
to match — the imported CSS is silently nullified. The visible tip of that
iceberg is the hero `.main-contents` losing its `margin:0 auto` and shifting
~80px left (getComputedStyle froze the auto margin to 0 at capture, so Fix 127
had no symmetric-px signature to recover, and the CSS fallback that should have
restored it never matched).

The transpiler now adopts the principal content wrapper's class as the App
root's className when the capture root is itself class-less: it descends past
non-visual children (script/style/skip-nav) and picks the class-bearing child
with the largest subtree. That restores the `.navercorp` ancestor so scoped ref
CSS matches, without touching a capture root that already carries its own class.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"


def _run(tmp_path: Path, structure: dict, sections: list[str],
         css: str | None = None) -> str:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": i, "tag": "section", "cls": s} for i, s in enumerate(sections)]}),
        encoding="utf-8")
    # The scope-class guard only fires when the ref CSS actually scopes rules
    # under the wrapper class (a `.<wrapper> ` descendant combinator). Sites that
    # need the fix always ship such CSS; supply it so the guard is exercised.
    if css is not None:
        (ref / "css").mkdir()
        (ref / "css" / "ref.css").write_text(css, encoding="utf-8")
    (impl / "package.json").write_text('{"name":"i","dependencies":{}}', encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return (impl / "src" / "App.tsx").read_text(encoding="utf-8")


def _run_stderr(tmp_path: Path, structure: dict, sections: list[str],
                css: str) -> str:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": i, "tag": "section", "cls": s} for i, s in enumerate(sections)]}),
        encoding="utf-8")
    (ref / "css").mkdir()
    (ref / "css" / "ref.css").write_text(css, encoding="utf-8")
    (impl / "package.json").write_text('{"name":"i","dependencies":{}}', encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stderr


def _root_div_line(app: str) -> str:
    # first element after `return (` is the App root (a <div>, or <main>/<body>
    # when the capture root tag is preserved); return its opening-tag attributes.
    body = app.split("return (", 1)[1].lstrip()
    assert body.startswith("<"), body
    return body[1:].split(">", 1)[0]


def _wrapper(cls: str, sections: list[str],
             extra_children: list[dict] | None = None) -> dict:
    kids = [{"tag": "section", "class": s, "styles": {},
             "children": [{"tag": "p", "text": s}]} for s in sections]
    node = {"tag": "div", "class": cls, "styles": {"width": "1440px"}, "children": kids}
    children = [node] + list(extra_children or [])
    return {"tag": "body", "class": "", "styles": {"width": "1440px"}, "children": children}


def test_principal_wrapper_class_adopted_onto_classless_root(tmp_path: Path) -> None:
    # body(class="") → div.navercorp[sections]: the scoping class must reach root.
    app = _run(tmp_path, _wrapper("navercorp main", ["main-header", "main-news"]),
               ["main-header", "main-news"],
               css=".navercorp .main-header{margin:0 auto}")
    root = _root_div_line(app)
    assert 'className="navercorp main"' in root, root


def test_skip_nav_sibling_not_chosen_over_content_wrapper(tmp_path: Path) -> None:
    # body has a tiny skip-nav ul.skip AND the big div.navercorp content wrapper;
    # the principal (largest-subtree) class-bearing child wins, not the skip link.
    skip = {"tag": "ul", "class": "skip", "styles": {},
            "children": [{"tag": "li", "text": "skip to content"}]}
    struct = _wrapper("navercorp main", ["main-header", "main-news", "footer"],
                      extra_children=[skip])
    # put skip FIRST so document order can't accidentally pick the right one
    struct["children"] = [skip] + [c for c in struct["children"] if c is not skip]
    app = _run(tmp_path, struct, ["main-header", "main-news", "footer"],
               css=".navercorp .main-header{color:red}.skip{position:absolute}")
    root = _root_div_line(app)
    assert "navercorp" in root, root
    assert 'className="skip"' not in root, root


def test_non_visual_children_ignored(tmp_path: Path) -> None:
    # script/style siblings must never be mistaken for the content wrapper.
    noise = [{"tag": "script", "class": "", "children": []},
             {"tag": "style", "class": "", "children": []}]
    app = _run(tmp_path, _wrapper("app-shell", ["hero"], extra_children=noise), ["hero"],
               css=".app-shell .hero{padding:0}")
    root = _root_div_line(app)
    assert 'className="app-shell"' in root, root


def test_child_combinator_scope_is_adopted(tmp_path: Path) -> None:
    # Ref CSS may scope through the wrapper with a child combinator (`.navercorp>
    # .main-header`) or no space at all — that is still an ancestor scope, so the
    # class must be adopted (a literal-space-only guard would miss it).
    app = _run(tmp_path, _wrapper("navercorp main", ["main-header"]), ["main-header"],
               css=".navercorp>.main-header{margin:0 auto}")
    root = _root_div_line(app)
    assert 'className="navercorp main"' in root, root


def test_wrapper_not_used_as_css_scope_is_not_adopted(tmp_path: Path) -> None:
    # A lone content wrapper whose class scopes NOTHING in the ref CSS (`.solo{}`
    # has no `.solo ` descendant combinator) must NOT be stamped onto the root —
    # that is what broke a single-section fixture. Guard: adopt only real scopes.
    app = _run(tmp_path, _wrapper("solo", ["hero"]), ["hero"],
               css=".solo{display:block}.hero{color:blue}")
    root = _root_div_line(app)
    assert 'className="solo"' not in root, root
    assert "className=" not in root, root


def test_non_body_capture_root_does_not_borrow_descendant_class(tmp_path: Path) -> None:
    # A class-less <main> capture root IS the content root — even with a real
    # `.hero ` scope in the ref CSS it must NOT hoist the child section's class
    # onto itself (that would apply `.hero`'s own rules to the whole page).
    struct = _wrapper("hero", ["inner"])
    struct["tag"] = "main"
    app = _run(tmp_path, struct, ["inner"], css=".hero .inner{margin:0 auto}")
    root = _root_div_line(app)
    assert "className=" not in root, root


def test_root_with_own_class_is_not_overridden(tmp_path: Path) -> None:
    # when the capture root already carries a class, keep it — do not descend.
    struct = _wrapper("navercorp", ["hero"])
    struct["class"] = "page-root"
    app = _run(tmp_path, struct, ["hero"],
               css=".navercorp .hero{margin:0 auto}")
    root = _root_div_line(app)
    assert 'className="page-root"' in root, root
    assert "navercorp" not in root, root


def test_unmatched_dominant_scope_warns_on_stderr(tmp_path: Path) -> None:
    # A page-root namespace scoping the bulk of the CSS that never reaches an
    # emitted ancestor is the silent-failure signature Fix 130 targets. When it
    # can't be adopted (here the body already carries a non-scope class, so the
    # body/html adoption gate is skipped), emit a loud build-time warning.
    struct = _wrapper("navercorp main", ["main-header"])
    struct["class"] = "antialiased"  # own class → adoption gate skipped
    css = "".join(f".navercorp .r{i}{{color:red}}" for i in range(30))
    err = _run_stderr(tmp_path, struct, ["main-header"], css)
    assert "WARNING" in err and "navercorp" in err, err
    assert "will not match" in err, err


def test_adopted_scope_does_not_warn(tmp_path: Path) -> None:
    # When Fix 130 adopts the scope class onto the root, the rules DO match, so
    # the warning must stay silent (no false alarm).
    css = "".join(f".navercorp .r{i}{{color:red}}" for i in range(30))
    err = _run_stderr(tmp_path, _wrapper("navercorp main", ["main-header"]),
                      ["main-header"], css)
    assert "WARNING" not in err or "will not match" not in err, err


def test_classless_root_without_wrapper_stays_classless(tmp_path: Path) -> None:
    # no class-bearing content child → no scope class to adopt; unchanged behavior.
    struct = {"tag": "body", "class": "", "styles": {"width": "1440px"},
              "children": [{"tag": "section", "class": "", "styles": {},
                            "children": [{"tag": "p", "text": "hero"}]}]}
    app = _run(tmp_path, struct, [""], css=".whatever .x{color:red}")
    root = _root_div_line(app)
    assert "className=" not in root, root
