"""scaffold-to-jsx header scroll-state descendant compaction (navercorp #3).

A fixed page header that shrinks on scroll already gets a synthetic
`.ui-clone-header-scroll.is-compact header{height:64px!important}` wrapper, and a
runtime parity gate confirms the header HEIGHT animates 100->64 like the ref.

But the ref also shrinks the *logo* (and other header descendants) via CSS gated
on scroll-state classes carried by the ROOT `.navercorp.main` host, e.g.
`.navercorp.is-scroll-up.main .header .header__logo{width:104px;height:20px}`.
The transpiler drops that host class (the impl root is anonymous), so none of
those `.navercorp`-scoped rules match, and the logo stays frozen at its baked
inline `width:292px`. Restoring the host is unacceptable (it would activate
thousands of dormant `.navercorp`-scoped rules under the inline styles).

Fix: the emitter already loads the ref CSS. Parse the ref's scroll-state compact
rules for header-descendant selectors that are present in the captured header
subtree, and re-emit their declarations under the synthetic compact wrapper as
`.ui-clone-header-scroll.is-compact <descendant>{...!important}`. The `!important`
beats the baked inline `width:292px` without touching the host cascade.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"

# The ref's real header CSS: expanded logo at rest, compact logo under any of the
# scroll-state classes toggled on the `.navercorp.main` root.
_REF_CSS = (
    ".header{position:fixed;top:0;left:0;z-index:230;width:100%;padding:12px 0}\n"
    ".header__logo{position:relative;width:104px;height:20px;margin:10px 0}\n"
    ".navercorp.main .header__logo{width:292px;height:56px}\n"
    ".navercorp.is-scroll-up.main .header .header__logo"
    "{width:104px;height:20px;transition:all .4s var(--bon-ease-Out)}\n"
    ".navercorp.is-scroll-down.main .header .header__logo"
    "{width:104px;height:20px;transition:all .4s var(--bon-ease-Out)}\n"
)


def _run(tmp_path: Path, header_node: dict, ref_css: str = _REF_CSS) -> str:
    ref, impl = tmp_path / "ref", tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "css").mkdir()
    (ref / "css" / "navercorp.css").write_text(ref_css, encoding="utf-8")
    (ref / "structure.json").write_text(
        json.dumps({"tag": "body", "class": "", "styles": {},
                    "children": [header_node]}),
        encoding="utf-8")
    (ref / "section-map.json").write_text(json.dumps({"sections": [
        {"index": 0, "tag": header_node["tag"], "cls": header_node["class"]}]}),
        encoding="utf-8")
    (impl / "package.json").write_text('{"name":"i","dependencies":{}}', encoding="utf-8")
    proc = subprocess.run(["bash", str(SCRIPT), str(ref), str(impl)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted((impl / "src" / "components").glob("*.tsx")))


def _header_with_logo() -> dict:
    return {
        "tag": "header", "class": "header",
        "styles": {"position": "fixed", "bottom": "800px", "height": "100px",
                   "min-height": "100px", "z-index": "230"},
        "children": [
            {"tag": "h1", "class": "header__logo",
             "styles": {"position": "relative", "width": "292px",
                        "height": "56px", "margin": "10px 0px"},
             "children": [{"tag": "span", "class": "blind", "text": "logo"}]},
        ],
    }


def test_logo_shrinks_under_compact_from_ref_scroll_state_rule(tmp_path: Path) -> None:
    # The synthetic compact wrapper must carry a rule that shrinks the logo to the
    # ref's compact dims (104x20) with !important so it beats the baked inline 292.
    blob = _run(tmp_path, _header_with_logo())
    assert "ui-clone-header-scroll" in blob, blob
    compact = [ln for ln in blob.splitlines()
               if "is-compact" in ln and "header__logo" in ln]
    assert compact, f"no compact logo rule emitted:\n{blob}"
    joined = "\n".join(compact)
    assert "104px" in joined, joined
    assert "!important" in joined, joined


def test_compact_logo_rule_absent_without_ref_scroll_state_rule(tmp_path: Path) -> None:
    # If the ref CSS has no scroll-state compact rule for a descendant, the emitter
    # must NOT invent one — only the header-height synthetic block is emitted.
    css_no_compact = (
        ".header{position:fixed;top:0;width:100%}\n"
        ".header__logo{position:relative;width:292px;height:56px}\n"
    )
    blob = _run(tmp_path, _header_with_logo(), ref_css=css_no_compact)
    compact = [ln for ln in blob.splitlines()
               if "is-compact" in ln and "header__logo" in ln]
    assert not compact, f"unexpected compact logo rule:\n{compact}"


def test_media_nested_compact_rule_stays_media_wrapped(tmp_path: Path) -> None:
    # A compact rule nested inside @media (a breakpoint-specific size) must be
    # re-emitted WRAPPED in the same media query — never lifted unconditionally,
    # so the mobile size cannot apply at desktop width.
    css = _REF_CSS + (
        "@media (max-width:768px){"
        ".navercorp.is-scroll-up.main .header .header__logo{width:80px;height:16px}"
        "}\n"
    )
    blob = _run(tmp_path, _header_with_logo(), ref_css=css)
    style = blob.split("__html: `")[1].split("`")[0]
    assert "104px" in style, style  # top-level desktop rule, unwrapped
    # the 80px mobile rule appears only inside its @media guard
    import re as _re
    naked = _re.sub(r"@media[^{]*\{[^{}]*\{[^}]*\}\}", "", style)
    assert "80px" not in naked, f"mobile compact size leaked unguarded:\n{naked}"
    assert "@media (max-width:768px)" in style, style


def test_desktop_media_scoped_compact_rule_preserved(tmp_path: Path) -> None:
    # Codex P2: a compact rule that lives ONLY inside a desktop media query must
    # still be emitted (wrapped), not dropped — else the descendant stays frozen
    # at desktop, the exact bug this fix targets.
    css = (
        ".header{position:fixed;top:0;width:100%}\n"
        ".header__logo{position:relative;width:292px;height:56px}\n"
        "@media (min-width:1024px){"
        ".navercorp.is-scroll-up.main .header .header__logo{width:104px;height:20px}"
        "}\n"
    )
    blob = _run(tmp_path, _header_with_logo(), ref_css=css)
    style = blob.split("__html: `")[1].split("`")[0]
    assert "@media (min-width:1024px)" in style, style
    assert "104px" in style and "!important" in style, style


def test_spaceless_combinator_selector_targets_descendant(tmp_path: Path) -> None:
    # Minified CSS emits space-less combinators. The last compound must still be
    # resolved to .header__logo (not the whole host-including selector), else the
    # rule silently no-ops on compiled bundles.
    css = (
        ".header{position:fixed;top:0;width:100%}\n"
        ".header__logo{width:292px;height:56px}\n"
        ".navercorp.is-scroll-up.main>.header>.header__logo{width:104px;height:20px}\n"
    )
    blob = _run(tmp_path, _header_with_logo(), ref_css=css)
    style = blob.split("__html: `")[1].split("`")[0]
    assert ".is-compact .header__logo" in style, style
    assert "104px" in style and "!important" in style, style


def test_merge_keeps_distinct_declarations_for_same_target(tmp_path: Path) -> None:
    # Two scroll-state rules for the logo carrying DIFFERENT props must union, not
    # clobber: both width (104) and the opacity should survive.
    css = _REF_CSS + (
        ".navercorp.is-scroll-down.main .header .header__logo{opacity:0.8}\n"
    )
    blob = _run(tmp_path, _header_with_logo(), ref_css=css)
    style = blob.split("__html: `")[1].split("`")[0]
    logo_rule = [seg for seg in style.split("}") if ".is-compact .header__logo" in seg]
    joined = "}".join(logo_rule)
    assert "104px" in joined, joined
    assert "opacity:0.8" in joined, joined


def test_token_boundary_avoids_false_trigger(tmp_path: Path) -> None:
    # A class that merely CONTAINS the trigger substring (is-scroll-upsell) must
    # not trigger lifting.
    css = (
        ".header{position:fixed;top:0;width:100%}\n"
        ".header__logo{width:292px;height:56px}\n"
        ".navercorp.is-scroll-upsell.main .header .header__logo{width:104px}\n"
    )
    blob = _run(tmp_path, _header_with_logo(), ref_css=css)
    tail = blob.split("ui-clone-header-scroll")[-1]
    assert "104px" not in tail, tail


def test_datauri_semicolon_not_split(tmp_path: Path) -> None:
    # A declaration with a data: URI containing `;` must stay intact (no !important
    # injected mid-URL, no broken split).
    css = _REF_CSS.replace(
        ".navercorp.is-scroll-up.main .header .header__logo"
        "{width:104px;height:20px;transition:all .4s var(--bon-ease-Out)}",
        ".navercorp.is-scroll-up.main .header .header__logo"
        "{width:104px;background:url(\"data:image/svg+xml;base64,ABC==\")}")
    blob = _run(tmp_path, _header_with_logo(), ref_css=css)
    style = blob.split("__html: `")[1].split("`")[0]
    assert "data:image/svg+xml;base64,ABC==" in style, style
    assert "base64,ABC== !important" not in style, style  # not split mid-URL


def test_height_gets_min_height_companion(tmp_path: Path) -> None:
    # The transpiler bakes captured height as inline min-height, so a lifted
    # `height:20px!important` alone is clamped back up. A min-height companion
    # must be emitted so the descendant actually shrinks.
    blob = _run(tmp_path, _header_with_logo())
    style = blob.split("__html: `")[1].split("`")[0]
    logo = [seg for seg in style.split("}") if ".is-compact .header__logo" in seg]
    joined = "}".join(logo)
    assert "height:20px !important" in joined, joined
    assert "min-height:20px !important" in joined, joined
    assert "min-width:104px !important" in joined, joined  # width companion too


def test_at_in_url_does_not_corrupt_region_scan(tmp_path: Path) -> None:
    # A bare `@` inside a url() (retina `@2x` asset) must not be mistaken for an
    # at-rule; the adjacent logo compact rule must still extract cleanly.
    css = (
        ".header{position:fixed;top:0;width:100%}\n"
        ".brand{background:url(logo@2x.png) no-repeat}\n"
        ".header__logo{width:292px;height:56px}\n"
        ".navercorp.is-scroll-up.main .header .header__logo{width:104px;height:20px}\n"
    )
    blob = _run(tmp_path, _header_with_logo(), ref_css=css)
    style = blob.split("__html: `")[1].split("`")[0]
    assert ".is-compact .header__logo" in style and "104px" in style, style


def test_compact_only_targets_descendants_present_in_header(tmp_path: Path) -> None:
    # A scroll-state rule for a descendant NOT in the captured header subtree
    # (.header__utils) must not be emitted; only .header__logo (present) is.
    css = _REF_CSS + (
        ".navercorp.is-scroll-up.main .header .header__utils{opacity:0.5}\n"
    )
    blob = _run(tmp_path, _header_with_logo(), ref_css=css)
    assert "header__utils" not in blob.split("ui-clone-header-scroll")[-1], blob
