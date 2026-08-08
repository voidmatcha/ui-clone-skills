import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_generation_plan_shell_has_no_heredocs() -> None:
    """Keep the large Python program out of Bash's pipe-backed heredoc path."""
    shell = (
        _project_root() / "scripts" / "extract" / "generation-plan.sh"
    ).read_text(encoding="utf-8")
    assert "<<" not in shell
    assert 'python3 "$_SCRIPT_DIR/generation_plan.py" "$REF_DIR" "$OUT"' in shell


def test_generation_plan_completes_on_current_bash_without_compat(
    tmp_path: Path,
) -> None:
    """The default Bash must not need inherited heredoc compatibility state."""
    ref = tmp_path / "ref"
    ref.mkdir()
    env = os.environ.copy()
    env.pop("BASH_COMPAT", None)
    bash = shutil.which("bash")
    assert bash is not None

    proc = subprocess.run(
        [
            bash,
            str(_project_root() / "scripts" / "extract" / "generation-plan.sh"),
            str(ref),
        ],
        capture_output=True,
        env=env,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (ref / "generation-plan.json").is_file()


def test_sanitize_ref_css_shell_has_no_python_heredoc() -> None:
    """Keep CSS sanitization out of Bash's pipe-backed heredoc path."""
    shell = (
        _project_root() / "scripts" / "extract" / "sanitize-ref-css.sh"
    ).read_text(encoding="utf-8")
    assert "<<" not in shell
    assert (
        'python3 "$_SCRIPT_DIR/sanitize_ref_css.py" '
        '"$REF_DIR" "$IMPL_ROOT" "$COPY_TO"'
    ) in shell


def test_sanitize_ref_css_completes_on_current_bash_without_compat(
    tmp_path: Path,
) -> None:
    """The default Bash must complete CSS sanitization without BASH_COMPAT."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "app.css").write_text(
        '.hero{background-image:var("/hero.webp")}\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("BASH_COMPAT", None)
    bash = shutil.which("bash")
    assert bash is not None

    proc = subprocess.run(
        [
            bash,
            str(_project_root() / "scripts" / "extract" / "sanitize-ref-css.sh"),
            str(ref),
            str(impl),
        ],
        capture_output=True,
        env=env,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (ref / "ref-css-sanitize-report.json").is_file()
    assert (impl / "src" / "ref-css" / "app.css").is_file()


def test_generation_plan_requires_forensic_preservation_for_css_modules(tmp_path: Path) -> None:
    """CSS-module-heavy refs should plan a ref-derived JSX + local CSS path."""
    ref = tmp_path / "ref" / "realfood"
    (ref / "css").mkdir(parents=True)

    sections = []
    for idx in range(12):
        sections.append(
            {
                "index": idx,
                "tag": "section",
                "id": f"section-{idx}",
                "className": f"dga_section_{idx}__AbC{idx:02d}",
                "height": 600,
            }
        )
    (ref / "section-map.json").write_text(json.dumps(sections))
    (ref / "css" / "app.css").write_text(
        "\n".join(f".dga_section_{idx}__AbC{idx:02d}{{display:block}}" for idx in range(12))
        + "\n"
        + ("/* copied site css */\n" * 900)
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    preservation = plan.get("forensicPreservation")
    assert preservation and preservation["required"] is True
    assert preservation["strategy"] == "ref-derived-jsx-with-local-css"
    assert preservation["classSignatureCount"] >= 12
    assert preservation["cssBytes"] > 10_000
    assert "preserve original CSS-module className tokens" in preservation["rules"]
    assert preservation["copyCssCommand"] == (
        "bash scripts/extract/sanitize-ref-css.sh <ref-dir> <impl-root>"
    )


def test_sanitize_ref_css_rewrites_urlish_var_tokens(tmp_path: Path) -> None:
    """Captured prod CSS can contain browser-tolerated tokens Vite rejects."""
    ref = tmp_path / "tmp" / "ref" / "project-a"
    impl = tmp_path / "scratch" / "project-a" / "impl"
    (ref / "css").mkdir(parents=True)
    css = ref / "css" / "project-a.css"
    css.write_text(
        '.header__AbC12{background-image: var("/img/common/ic-search-delete.png")}\n'
        ".hero__Def34{background: var(--ok, #fff)}\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            "bash",
            str(_project_root() / "scripts/extract/sanitize-ref-css.sh"),
            str(ref),
            str(impl),
        ],
        check=True,
    )

    copied = impl / "src" / "ref-css" / "project-a.css"
    text = copied.read_text(encoding="utf-8")
    assert 'background-image: url("/img/common/ic-search-delete.png")' in text
    assert "background: var(--ok, #fff)" in text

    report = json.loads((ref / "ref-css-sanitize-report.json").read_text())
    assert report["fileCount"] == 1
    assert report["changedFileCount"] == 1
    assert report["replacementCount"] == 1


def test_sanitize_ref_css_rewrites_relative_static_urls_to_public_root(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "tmp" / "ref" / "font-site"
    impl = tmp_path / "scratch" / "font-site" / "impl"
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "site.css").write_text(
        "@font-face{font-family:Human;"
        "src:url('../../../font/NanumHumanRegular.otf') format('opentype')}"
        ".hero{background-image:url('../../../img/hero/main.webp?size=large')}"
        "@font-face{src:url('/font/Keep.woff2')}"
        "@font-face{src:url('https://cdn.example.com/font/Remote.woff2')}",
        encoding="utf-8",
    )

    subprocess.run(
        [
            "bash",
            str(_project_root() / "scripts/extract/sanitize-ref-css.sh"),
            str(ref),
            str(impl),
        ],
        check=True,
    )

    text = (impl / "src" / "ref-css" / "site.css").read_text(encoding="utf-8")
    assert 'url("/font/NanumHumanRegular.otf")' in text
    assert 'url("/img/hero/main.webp?size=large")' in text
    assert "url('/font/Keep.woff2')" in text
    assert "url('https://cdn.example.com/font/Remote.woff2')" in text

    report = json.loads((ref / "ref-css-sanitize-report.json").read_text())
    assert report["replacementCount"] == 2
    kinds = [r["kind"] for r in report["files"][0]["replacements"]]
    assert kinds == [
        "relative-static-url-to-public-root",
        "relative-static-url-to-public-root",
    ]


def test_sanitize_ref_css_repairs_one_dash_var_custom_property(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "tmp" / "ref" / "var-site"
    impl = tmp_path / "scratch" / "var-site" / "impl"
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "site.css").write_text(
        ".bad{color:var(-color-gray-950)}"
        ".fallback{background:var(-surface, #fff)}"
        ".good{color:var(--color-gray-950)}",
        encoding="utf-8",
    )

    subprocess.run(
        [
            "bash",
            str(_project_root() / "scripts/extract/sanitize-ref-css.sh"),
            str(ref),
            str(impl),
        ],
        check=True,
    )

    text = (impl / "src" / "ref-css" / "site.css").read_text(encoding="utf-8")
    assert "color:var(--color-gray-950)" in text
    assert "background:var(--surface, #fff)" in text
    assert ".good{color:var(--color-gray-950)}" in text

    report = json.loads((ref / "ref-css-sanitize-report.json").read_text())
    assert report["replacementCount"] == 2
    assert {
        r["kind"] for r in report["files"][0]["replacements"]
    } == {"one-dash-var-custom-property-to-css-var"}


def test_sanitize_ref_css_removes_invalid_pseudo_descendant_rules(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "tmp" / "ref" / "pseudo-site"
    impl = tmp_path / "scratch" / "pseudo-site" / "impl"
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "site.css").write_text(
        ".bad:before .en{font-weight:530}"
        ".good::before{content:'ok'}"
        "@media(min-width:1px){.nested:after .label{color:red}.keep{color:blue}}",
        encoding="utf-8",
    )

    subprocess.run(
        [
            "bash",
            str(_project_root() / "scripts/extract/sanitize-ref-css.sh"),
            str(ref),
            str(impl),
        ],
        check=True,
    )

    text = (impl / "src" / "ref-css" / "site.css").read_text(encoding="utf-8")
    assert ":before .en" not in text
    assert ":after .label" not in text
    assert ".good::before{content:'ok'}" in text
    assert ".keep{color:blue}" in text

    report = json.loads((ref / "ref-css-sanitize-report.json").read_text())
    assert report["replacementCount"] == 2
    assert {
        replacement["kind"]
        for replacement in report["files"][0]["replacements"]
    } == {"invalid-pseudo-descendant-rule-removed"}


def test_sanitize_ref_css_repairs_known_invalid_selector_tokens(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "tmp" / "ref" / "selector-site"
    impl = tmp_path / "scratch" / "selector-site" / "impl"
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "site.css").write_text(
        ".arrow:beofre{content:''}"
        ".next:swiper-button-disabled{opacity:.2}"
        ".valid::before{content:'ok'}",
        encoding="utf-8",
    )

    subprocess.run(
        [
            "bash",
            str(_project_root() / "scripts/extract/sanitize-ref-css.sh"),
            str(ref),
            str(impl),
        ],
        check=True,
    )

    text = (impl / "src" / "ref-css" / "site.css").read_text(encoding="utf-8")
    assert ".arrow:before{content:''}" in text
    assert ".next.swiper-button-disabled{opacity:.2}" in text
    assert ".valid::before{content:'ok'}" in text

    report = json.loads((ref / "ref-css-sanitize-report.json").read_text())
    assert report["replacementCount"] == 2
    assert {
        replacement["kind"]
        for replacement in report["files"][0]["replacements"]
    } == {
        "misspelled-before-selector-repaired",
        "swiper-disabled-pseudo-to-class",
    }


def test_sanitize_ref_css_reports_runtime_unlock_hint(tmp_path: Path) -> None:
    """Copied root/body-hidden CSS needs a local ready/unlock controller."""
    ref = tmp_path / "tmp" / "ref" / "loader-site"
    impl = tmp_path / "scratch" / "loader-site" / "impl"
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "site.css").write_text(
        "body{opacity:0}.ready body{opacity:1} #root{visibility:hidden}\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            "bash",
            str(_project_root() / "scripts/extract/sanitize-ref-css.sh"),
            str(ref),
            str(impl),
        ],
        check=True,
    )

    report = json.loads((ref / "ref-css-sanitize-report.json").read_text())
    assert report["requiresRuntimeUnlock"] is True
    hints = report["runtimeUnlockHints"]
    assert any(h["selector"] == "body" and "opacity" in h["declaration"] for h in hints)
    assert any("#root" in h["selector"] and "visibility" in h["declaration"] for h in hints)
    assert report["files"][0]["requiresRuntimeUnlock"] is True


def test_generation_plan_surfaces_runtime_unlock_hint_from_ref_css(tmp_path: Path) -> None:
    ref = tmp_path / "ref" / "loader-site"
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "site.css").write_text(
        "html.is-loading{visibility:hidden} body{opacity:0}\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    preservation = plan["forensicPreservation"]
    assert preservation["requiresRuntimeUnlock"] is True
    assert any(h["selector"].startswith("html") for h in preservation["runtimeUnlockHints"])
    assert any("ready/unlock" in rule for rule in preservation["rules"])


def test_generation_plan_blocks_standard_rebuild_when_css_modules_lack_css_artifacts(
    tmp_path: Path,
) -> None:
    """CSS-module signatures alone should block freehand rebuilds.

    RealFood incident: the capture exposed many CSS-module class signatures but
    no css/*.css chunks, which previously downgraded the plan to
    standard-react-rebuild and let agents recreate the site from vibes.
    """
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)

    sections = []
    for idx in range(30):
        sections.append(
            {
                "index": idx,
                "tag": "section",
                "id": f"section-{idx}",
                "className": f"dga_section_{idx}__AbC{idx:02d}",
                "height": 600,
            }
        )
    (ref / "section-map.json").write_text(json.dumps(sections))

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    preservation = plan.get("forensicPreservation")
    assert preservation and preservation["required"] is True
    assert preservation["strategy"] == "ref-derived-jsx-with-local-css"
    assert preservation["classSignatureCount"] >= 30
    assert preservation["cssBytes"] == 0
    assert preservation["cssArtifactStatus"] == "missing"
    assert preservation["missingCssArtifacts"] is True
    assert preservation["copyCssTo"] == "src/ref-css"
    assert any("do not use standard-react-rebuild" in rule for rule in preservation["rules"])


def test_generation_plan_recovers_stylesheet_links_before_forensic_decision(
    tmp_path: Path,
) -> None:
    """Linked CSS should be copied into ref/css before deciding the strategy."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    source_css = tmp_path / "source" / "app.css"
    source_css.parent.mkdir()
    source_css.write_text(
        "\n".join(f".dga_section_{idx}__AbC{idx:02d}{{display:block}}" for idx in range(30))
        + "\n"
        + ("/* recovered site css */\n" * 900),
        encoding="utf-8",
    )

    sections = []
    for idx in range(30):
        sections.append(
            {
                "index": idx,
                "tag": "section",
                "id": f"section-{idx}",
                "className": f"dga_section_{idx}__AbC{idx:02d}",
                "height": 600,
            }
        )
    (ref / "section-map.json").write_text(json.dumps(sections))
    (ref / "head.json").write_text(
        json.dumps(
            {
                "links": [
                    {
                        "rel": "stylesheet",
                        "href": source_css.resolve().as_uri(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["UI_CLONE_CSS_DOWNLOAD_ALLOW_FILE"] = "1"
    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
        env=env,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    preservation = plan.get("forensicPreservation")
    assert preservation and preservation["required"] is True
    assert preservation["strategy"] == "ref-derived-jsx-with-local-css"
    assert preservation["cssBytes"] > 10_000
    assert preservation["cssFiles"] == ["css/app.css"]
    assert preservation["cssArtifactStatus"] == "present"
    assert preservation["missingCssArtifacts"] is False
    assert (ref / "css" / "app.css").is_file()


def test_generation_plan_sticky_records_containing_block(tmp_path: Path) -> None:
    """A css-sticky element must carry its relative containing-block ancestor
    (selector + height) and renderAt that wrapper — not flat at App — so the
    clone's sticky releases at the section end instead of pinning to the page
    body for the whole scroll."""
    ref = tmp_path / "ref" / "realfood"
    (ref / "css").mkdir(parents=True)
    structure = {
        "tag": "body",
        "children": [
            {
                "tag": "section",
                "class": "dga_resources_section__VAZi",
                "styles": {"position": "relative", "height": "2700px"},
                "children": [
                    {
                        "tag": "div",
                        "class": "dga_resources_sticky__EiBuU",
                        "styles": {"position": "sticky", "top": "0px"},
                        "children": [{"tag": "p", "text": "Resources"}],
                    }
                ],
            }
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure))
    (ref / "dom-scaffold.json").write_text(json.dumps(structure))
    (ref / "section-map.json").write_text(
        json.dumps(
            {"sections": [{"id": 0, "tag": "section", "cls": "dga_resources_section__VAZi"}]}
        )
    )
    (ref / "sticky-elements.json").write_text(
        json.dumps(
            {
                "elements": [
                    {
                        "tag": "div",
                        "className": "dga_resources_sticky__EiBuU",
                        "position": "sticky",
                        "stickyTop": "0px",
                    }
                ]
            }
        )
    )
    (ref / "css" / "x.css").write_text(".dga_resources_sticky__EiBuU{position:sticky}\n")

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    sticky = plan["stickyStrategy"]
    assert len(sticky) == 1
    entry = sticky[0]
    assert entry["mechanism"] == "css-sticky"
    assert entry["top"] == "0px", "stickyTop must map to top (field-name fix)"
    cb = entry["containingBlock"]
    assert cb is not None
    assert "dga_resources_section__VAZi" in cb["selector"]
    assert cb["height"] == "2700px"
    assert cb["position"] == "relative"
    assert entry["renderAt"] == cb["selector"], "css-sticky must render inside its containing block"


def test_generation_plan_smoothscroll_carries_lenis_config(tmp_path: Path) -> None:
    """When scroll-engine.json declares Lenis with concrete options, the
    smoothScroll plan must thread those options into a `config` block so the
    generated SmoothScroll.tsx uses the site's real lerp/duration/easing/
    wheelMultiplier instead of Lenis library defaults."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)

    (ref / "section-map.json").write_text(
        json.dumps(
            [
                {
                    "index": i,
                    "tag": "section",
                    "id": f"s-{i}",
                    "className": f"sec_{i}",
                    "height": 600,
                }
                for i in range(4)
            ]
        )
    )
    (ref / "scroll-engine.json").write_text(
        json.dumps(
            {
                "engine": "lenis",
                "custom": False,
                "smoothScroll": True,
                "evidence": "html.lenis class + lerp:/wheelMultiplier in bundles",
                "options": {
                    "lerp": 0.1,
                    "duration": 1.2,
                    "wheelMultiplier": 1,
                    "easing": "expoOut",
                },
            }
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    ss = plan["smoothScroll"]
    assert ss["required"] is True
    assert ss["library"] == "lenis"
    cfg = ss["config"]
    assert cfg["lerp"] == 0.1
    assert cfg["duration"] == 1.2
    assert cfg["wheelMultiplier"] == 1
    assert cfg["easing"] == "expoOut"


def test_generation_plan_smoothscroll_config_empty_when_no_options(tmp_path: Path) -> None:
    """Lenis detected but no concrete options → config is an empty dict, never
    fabricated defaults (the generator falls back to Lenis defaults itself)."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps(
            [{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)]
        )
    )
    (ref / "scroll-engine.json").write_text(
        json.dumps({"engine": "lenis", "smoothScroll": True, "evidence": "html.lenis class"})
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    ss = plan["smoothScroll"]
    assert ss["required"] is True
    assert ss["config"] == {}


def test_generation_plan_smoothscroll_for_realfood_lenis(tmp_path: Path) -> None:
    """Regression (Fix 41 must NOT over-reject): realfood's actual
    scroll-engine.json (engine=lenis, smoothScroll true, lenis evidence) MUST
    still require smooth scroll. Pairs with the native(no-lenis) test below."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps(
            [{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)]
        )
    )
    # Faithful to tmp/ref/realfood-gov-l63/scroll-engine.json.
    (ref / "scroll-engine.json").write_text(
        json.dumps(
            {
                "engine": "lenis",
                "custom": False,
                "smoothScroll": True,
                "evidence": "html.lenis class + lerp:/wheelMultiplier in bundles",
                "options": {"wheelMultiplier": 1},
            }
        )
    )
    (ref / "external-sdks.json").write_text(
        json.dumps(
            {
                "sdks": [
                    {
                        "name": "lenis",
                        "type": "smooth-scroll",
                        "evidence": "html.lenis class",
                        "clone": "use",
                    },
                ]
            }
        )
    )
    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )
    plan = json.loads((ref / "generation-plan.json").read_text())
    assert plan["smoothScroll"]["required"] is True, (
        "realfood (engine=lenis) must keep smooth scroll"
    )
    assert plan["smoothScroll"]["library"] == "lenis"
    assert "lenis" in plan["libraries"]["required"]


def test_generation_plan_no_smoothscroll_for_native_engine(tmp_path: Path) -> None:
    """Regression (codex review): has_smooth_scroll matched any 'smooth'
    substring, so {"engine":"native","smoothScroll":false} still injected Lenis.
    Native scroll with smoothScroll false must NOT require smooth scroll."""
    ref = tmp_path / "ref" / "site"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps(
            [{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)]
        )
    )
    (ref / "scroll-engine.json").write_text(
        json.dumps({"engine": "native", "smoothScroll": False, "evidence": "no smooth-scroll lib"})
    )
    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )
    plan = json.loads((ref / "generation-plan.json").read_text())
    assert plan["smoothScroll"]["required"] is False
    assert plan["smoothScroll"]["library"] is None
    assert plan["smoothScroll"]["config"] == {}
    assert "lenis" not in plan["libraries"]["required"]


def test_generation_plan_surfaces_framer_scroll_driven_block(tmp_path: Path) -> None:
    """When scroll-engine.json declares a framer-motion scrollDriven block
    (useScroll / useTransform / scrollYProgress), the plan must surface it as a
    `scrollDriven` contract so the generator wires the real scroll-progress
    reveal mechanism — not just a generic scroll listener."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps(
            [{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)]
        )
    )
    (ref / "scroll-engine.json").write_text(
        json.dumps(
            {
                "engine": "lenis",
                "smoothScroll": True,
                "scrollDriven": {
                    "library": "framer-motion",
                    "hooks": ["useScroll", "useTransform", "scrollYProgress"],
                    "evidence": "21 scrollYProgress refs in bundles",
                },
            }
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    sd = plan["scrollDriven"]
    assert sd["required"] is True
    assert sd["library"] == "framer-motion"
    assert sd["hooks"] == ["useScroll", "useTransform", "scrollYProgress"]
    assert "21 scrollYProgress" in sd["evidence"]
    # must reconcile with smooth-scroll: progress comes from the Lenis source,
    # not a raw window 'scroll' listener.
    assert "lenis" in sd["note"].lower() or "smooth" in sd["note"].lower()


def test_generation_plan_surfaces_scroll_scrub_from_bundle_extraction(tmp_path: Path) -> None:
    """bundle-extraction.json's framer useScroll/useTransform tables must surface
    as a concrete `scrollScrub` plan (offset + input/output ranges per site) so the
    generator emits real scroll-scrub motion, not a generic opacity fade. Sites with
    no transform table are detection-only and must NOT become scrub contracts."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps(
            [{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)]
        )
    )
    (ref / "bundle-extraction.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "extractions": {
                    "framerMotion": [
                        {
                            "kind": "useScroll",
                            "progressVar": "e",
                            "target": "e",
                            "offset": '["start end","end start"]',
                            "transforms": [{"input": "[0,1]", "output": "[1,1.2]"}],
                            "transformCount": 1,
                            "source": "bundles/page.js",
                        },
                        {
                            "kind": "useScroll",
                            "progressVar": "w",
                            "target": "w",
                            "offset": '["start end","end end"]',
                            "transforms": [],
                            "transformCount": 0,
                            "source": "bundles/page.js",
                        },
                        {"kind": "useMotionValueEvent", "valueVar": "w", "event": "change"},
                    ]
                },
            }
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    ss = plan["scrollScrub"]
    assert ss["required"] is True
    assert ss["library"] == "framer-motion"
    # only the site WITH a transform table becomes a scrub contract
    assert ss["count"] == 1
    site = ss["sites"][0]
    assert site["offset"] == '["start end","end start"]'
    assert site["transforms"] == [{"input": "[0,1]", "output": "[1,1.2]"}]
    assert "lenis" in ss["note"].lower() or "scroll source" in ss["note"].lower()


def test_generation_plan_scroll_scrub_from_construction_sites(tmp_path: Path) -> None:
    """The hand-curated ui_clone.bundle_extraction schema carries the scroll-scrub
    tables under constructionSites[] (no `extractions` key). generation-plan.sh must
    translate each trigger=="scroll-scrub" site's mappings[] (property/inputRange/
    outputRange/scrollOffset) into the same {offset(JSON-string), transforms:[{property,
    input,output}]} contract emit-scroll-helpers.sh parses. Non-scrub triggers, symbolic
    (non-numeric) ranges, dup properties (first-wins), and compound/unsupported property
    labels are dropped so the plan stays honest and the emitter never over-fires."""
    ref = tmp_path / "ref" / "rf"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps(
            [{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)]
        )
    )
    (ref / "bundle-extraction.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "source": "ui_clone.bundle_extraction",
                "constructionSites": [
                    {  # not a scrub -> ignored
                        "id": "nav-state-machine",
                        "trigger": "scroll-state-machine",
                        "offset": 21453,
                        "mappings": [
                            {"property": "top (header offset px)",
                             "inputRange": [0, 100], "outputRange": [56, 20]},
                        ],
                    },
                    {
                        "id": "erf-card-reveal",
                        "trigger": "scroll-scrub",
                        "offset": 768080,
                        "scrollOffset": ["start end", "end start"],
                        "mappings": [
                            {"property": "y (title/card 1)",
                             "inputRange": [0, 0.2], "outputRange": [80, 0]},
                            {"property": "y (card 2)",  # dup prop -> first-wins drop
                             "inputRange": [0.12, 0.26], "outputRange": [40, 0]},
                            {"property": "title-pin condition",  # not a transform
                             "inputRange": None, "outputRange": None},
                        ],
                    },
                    {
                        "id": "cta-reveal",
                        "trigger": "scroll-scrub",
                        "offset": 794747,
                        "scrollOffset": ["start end", "end start"],
                        "mappings": [
                            {"property": "scale",
                             "inputRange": [0, 0.1, 0.75, 0.9], "outputRange": [0.9, 1, 1, 1]},
                            {"property": "opacity-coupled offset",  # compound label -> dropped
                             "inputRange": [0, 0.1, 0.75, 0.9], "outputRange": [0, 0, 0, 0]},
                        ],
                    },
                    {
                        "id": "read-along",
                        "trigger": "scroll-scrub",
                        "offset": 757215,
                        "scrollOffset": ["start end", "end start"],
                        "mappings": [  # symbolic 'P' token -> dropped, site has no valid xf
                            {"property": "opacity",
                             "inputRange": ["P", 0.3, 0.55, 0.7], "outputRange": [0.4, 1, 1, 0]},
                        ],
                    },
                ],
            }
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    ss = plan["scrollScrub"]
    assert ss["required"] is True
    assert ss["library"] == "framer-motion"
    # erf (y) + cta (scale) survive; nav (not scrub) + read-along (symbolic) dropped
    assert ss["count"] == 2
    by_src = {s.get("source"): s for s in ss["sites"]}
    assert set(by_src) == {"erf-card-reveal", "cta-reveal"}
    erf = by_src["erf-card-reveal"]
    assert erf["offset"] == '["start end", "end start"]'
    # first-wins per property (only the first y), title-pin dropped
    assert erf["transforms"] == [{"property": "y", "input": "[0, 0.2]", "output": "[80, 0]"}]
    cta = by_src["cta-reveal"]
    # the compound 'opacity-coupled offset' label must NOT become a phantom opacity:0 band
    assert cta["transforms"] == [
        {"property": "scale", "input": "[0, 0.1, 0.75, 0.9]", "output": "[0.9, 1, 1, 1]"}
    ]


def test_generation_plan_scroll_scrub_absent_when_no_bundle_extraction(tmp_path: Path) -> None:
    """No bundle-extraction.json (or no framer transform tables) → scrollScrub not
    required, empty sites — generation stays unaffected on non-scroll-scrub sites."""
    ref = tmp_path / "ref" / "plain"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps(
            [{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(3)]
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    ss = plan["scrollScrub"]
    assert ss["required"] is False
    assert ss["count"] == 0
    assert ss["sites"] == []


def test_generation_plan_scroll_driven_absent_when_no_block(tmp_path: Path) -> None:
    """No scrollDriven evidence → required is False and hooks empty."""
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps(
            [{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)]
        )
    )
    (ref / "scroll-engine.json").write_text(json.dumps({"engine": "native", "smoothScroll": False}))

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    sd = plan["scrollDriven"]
    assert sd["required"] is False
    assert sd["hooks"] == []


def test_component_generation_docs_consume_scroll_plan_fields() -> None:
    """The generator doc must tell implementers to consume the smoothScroll.config
    (real Lenis options) and scrollDriven (Framer scroll-progress reveal) plan
    fields, so Fix 28/29's threaded data is not silently ignored at generation."""
    root = _project_root()
    doc = (root / "skills/ui-reverse-engineering/component-generation.md").read_text()
    assert "smoothScroll.config" in doc
    assert "scrollDriven" in doc
    assert "scrollYProgress" in doc
    # Warn against the common downgrade error: turning a continuous scroll-scrub
    # reveal into a one-shot IntersectionObserver fade.
    assert "IntersectionObserver" in doc and "one-shot" in doc


def test_component_generation_documents_canonical_scroll_driven_snippet() -> None:
    """The generator doc must carry the render-verified canonical scrollDriven
    reveal pattern (useScroll(target,offset) + useTransform) and must NOT claim
    framer-motion useScroll fails under Lenis — verified false by a real render:
    opacity interpolated 0->1 with scroll while html.lenis was active."""
    root = _project_root()
    doc = (root / "skills/ui-reverse-engineering/component-generation.md").read_text()
    # Render-verified canonical pattern is documented.
    assert "useScroll({ target" in doc
    assert "useTransform(scrollYProgress" in doc
    # The false blanket claim is gone.
    assert "will NOT receive events" not in doc
    # Positive correction: framer useScroll tracks Lenis-driven scroll.
    assert "tracks Lenis" in doc
    # Preserve the contained-scroll caveat (custom wrapper -> useScroll container).
    assert "container: wrapperRef" in doc


def test_ui_reverse_engineering_docs_promote_forensic_preservation() -> None:
    """Step 7 docs should route CSS-module-heavy refs to the preserved scaffold path."""
    root = _project_root()
    skill = (root / "skills/ui-reverse-engineering/SKILL.md").read_text()
    component_generation = (
        root / "skills/ui-reverse-engineering/component-generation.md"
    ).read_text()
    site_detection = (root / "skills/ui-reverse-engineering/site-detection.md").read_text()

    for text in (skill, component_generation, site_detection):
        assert "forensicPreservation" in text
        assert "ref-derived JSX" in text
        assert "local CSS" in text


def test_scroll_driven_not_inferred_from_framer_library_only(tmp_path: Path) -> None:
    """Framer detection alone is not actionable scroll-driven evidence.

    The same library can drive hover, mount, layout, or class-state animation.
    Without a scrollDriven block, scrollScrub bands, runtime samples, or a true
    transition-spec reveal, the plan must not ask the scaffold to add generic
    ScrollReveal wrappers.
    """
    ref = tmp_path / "ref" / "realfood"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps(
            [{"index": i, "tag": "section", "id": f"s-{i}", "height": 600} for i in range(4)]
        )
    )
    (ref / "scroll-engine.json").write_text(json.dumps({"engine": "lenis", "smoothScroll": True}))
    (ref / "external-sdks.json").write_text(
        json.dumps(
            {
                "sdks": [
                    {
                        "name": "framer-motion",
                        "type": "animation",
                        "evidence": "framer in bundles",
                        "clone": "use",
                    },
                ]
            }
        )
    )
    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )
    plan = json.loads((ref / "generation-plan.json").read_text())
    assert plan["scrollDriven"]["required"] is False
    assert plan["scrollDriven"]["library"] is None
    assert "framer-motion" in plan["libraries"]["required"]


def test_generation_plan_mines_scrollkeyframes_into_scrub_sites(tmp_path: Path) -> None:
    """Path 3: a runtime-measured scroll-scrub curve in transition-spec.json
    scrollKeyframes must flow into scrollScrub.sites as numeric useTransform
    bands — the seam that carries the captured (often back-loaded) curve into
    the already-wired emit-scroll-helpers.sh, instead of a flat lerp."""
    ref = tmp_path / "ref" / "framer-site"
    ref.mkdir(parents=True)
    spec = {
        "source": "ui_clone.extraction_artifacts",
        "transitions": [
            {
                "id": "auto-scroll-scrub-0",
                "trigger": "scroll-scrub",
                "target": "svg",
                "selector": "svg",
                "animation": {
                    "type": "scroll-scrub",
                    "mechanism": "observed — animation-runtime-dump.json scrollLinkedStyles",
                    "scrollKeyframes": {
                        "input": [0, 0.5, 1],
                        "outputs": {"opacity": ["0", "0.35", "1"]},
                        "settleProgress": 1,
                        "easing": "measured-nonlinear",
                    },
                },
            },
            {
                "id": "auto-scroll-scrub-1",
                "trigger": "scroll-scrub",
                "target": "g#even",
                "selector": "g#even",
                "animation": {
                    "type": "scroll-scrub",
                    "mechanism": "observed",
                    "scrollKeyframes": {
                        "input": [0, 0.5, 1],
                        "outputs": {"transform": ["scale(0.425)", "scale(0.7)", "scale(1)"]},
                        "settleProgress": 1,
                        "easing": "measured",
                    },
                },
            },
        ],
    }
    (ref / "transition-spec.json").write_text(json.dumps(spec))

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    scrub = plan.get("scrollScrub") or {}
    assert scrub.get("required") is True
    by_selector = {s.get("selector"): s for s in (scrub.get("sites") or [])}

    # opacity curve preserved as a 3-point numeric band (back-loaded midpoint 0.35)
    svg = by_selector.get("svg")
    assert svg is not None, "scrollKeyframes svg opacity scrub not mined into sites"
    opacity = next(t for t in svg["transforms"] if t["property"] == "opacity")
    assert json.loads(opacity["input"]) == [0.0, 0.5, 1.0]
    assert json.loads(opacity["output"]) == [0.0, 0.35, 1.0]
    assert svg["source"].startswith("transition-spec.scrollKeyframes:")

    # transform string decomposed into a numeric scale band
    g = by_selector.get("g#even")
    assert g is not None, "scrollKeyframes transform scrub not mined into sites"
    scale = next(t for t in g["transforms"] if t["property"] == "scale")
    assert json.loads(scale["output"]) == [0.425, 0.7, 1.0]


def test_generation_plan_mines_documented_input_range_scrub_shape(tmp_path: Path) -> None:
    """A bundle-decompiled scroll scrub written in the shape the skill docs
    teach — ``useTransform(progress, inputRange, outputRange)`` plus a
    ``<channel>OutputRange`` — must reach scrollScrub.sites. Only the
    undocumented ``scrollKeyframes`` shape was mined before, so decompiled
    motion params were silently dropped and replay fell back to coarse
    runtime samples."""
    ref = tmp_path / "ref" / "framer-site"
    ref.mkdir(parents=True)
    spec = {
        "source": "ui_clone.extraction_artifacts",
        "transitions": [
            {
                "id": "hero-video-scroll-progress",
                "trigger": "scroll (hero section)",
                "target": "div#hero-video",
                "selector": "div#hero-video",
                "animation": {
                    "type": "scroll-scrub",
                    "property": "y",
                    "inputRange": [0, 0.3],
                    "yOutputRange": [80, 0],
                    "opacityOutputRange": [0.5, 1],
                    "spring": {"stiffness": 900, "damping": 60},
                },
            },
        ],
    }
    (ref / "transition-spec.json").write_text(json.dumps(spec))

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    scrub = plan.get("scrollScrub") or {}
    assert scrub.get("required") is True
    by_selector = {s.get("selector"): s for s in (scrub.get("sites") or [])}

    hero = by_selector.get("div#hero-video")
    assert hero is not None, "documented inputRange scrub shape not mined into sites"
    y = next(t for t in hero["transforms"] if t["property"] == "y")
    assert json.loads(y["input"]) == [0.0, 0.3]
    assert json.loads(y["output"]) == [80.0, 0.0]
    opacity = next(t for t in hero["transforms"] if t["property"] == "opacity")
    assert json.loads(opacity["output"]) == [0.5, 1.0]


def test_generation_plan_carries_spec_scroll_offset_into_scrub_site(tmp_path: Path) -> None:
    """scrollScrub's own note tells the generator to emit
    ``useScroll({ target, offset })``, but the documented inputRange shape
    dropped ``animation.offset`` on the floor, so every spec-shape site landed
    with no offset window and replay fell back to raw document progress. The
    resolved ``["start start", "end end"]`` pair must reach the site in the same
    JSON-string form the constructionSites path already emits."""
    ref = tmp_path / "ref" / "framer-site"
    ref.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "source": "bundle-analyzer",
                "transitions": [
                    {
                        "id": "hero-scrub",
                        "trigger": "scroll (hero)",
                        "target": "div#hero",
                        "selector": "div#hero",
                        "animation": {
                            "type": "scroll-scrub",
                            "offset": ["start start", "end end"],
                            "inputRange": [0, 0.3],
                            "scaleOutputRange": [0.9, 1.0],
                        },
                    },
                ],
            }
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    sites = {s.get("selector"): s for s in (plan.get("scrollScrub") or {}).get("sites") or []}
    hero = sites.get("div#hero")
    assert hero is not None, "documented inputRange scrub shape not mined into sites"
    assert hero.get("offset") == '["start start", "end end"]'


def test_generation_plan_drops_scrub_band_whose_input_is_scroll_pixels(tmp_path: Path) -> None:
    """A scroll-scrub inputRange is a scrollYProgress FRACTION in [0,1]
    (js-animation-extraction.md). A decompile that captured raw scrollY pixels
    instead — the nav state-machine shape, ``useTransform(scrollY,[0,100],
    [56,20])`` — has a different domain, and nothing checked it. Replayed
    against progress the band covers 1% of its own domain: 56px travels 0.36px
    and the element looks frozen, silently dead rather than visibly broken.
    An out-of-domain band must be dropped, not emitted."""
    ref = tmp_path / "ref" / "framer-site"
    ref.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "source": "bundle-analyzer",
                "transitions": [
                    {
                        "id": "nav-pixel-domain",
                        "trigger": "scroll",
                        "target": "nav",
                        "selector": "nav",
                        "animation": {
                            "type": "scroll-scrub",
                            "inputRange": [0, 100],
                            "yOutputRange": [56, 20],
                        },
                    },
                    {
                        "id": "hero-progress-domain",
                        "trigger": "scroll",
                        "target": "div#hero",
                        "selector": "div#hero",
                        "animation": {
                            "type": "scroll-scrub",
                            "inputRange": [0, 0.3],
                            "scaleOutputRange": [0.9, 1.0],
                        },
                    },
                ],
            }
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    sites = {s.get("selector"): s for s in (plan.get("scrollScrub") or {}).get("sites") or []}
    assert "nav" not in sites, "pixel-domain inputRange emitted as a progress band"
    # the well-formed neighbour must still survive — this is a targeted drop
    assert "div#hero" in sites


def test_generation_plan_carries_spec_spring_into_scrub_site(tmp_path: Path) -> None:
    """scrollScrub's note tells the generator to ``wrap output in useSpring only
    where the bundle did`` — which it cannot know, because the decompiled
    ``animation.spring`` params were dropped. A site whose bundle branch springs
    the output must carry those params so replay smooths the band instead of
    snapping it to a bare lerp."""
    ref = tmp_path / "ref" / "framer-site"
    ref.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "source": "bundle-analyzer",
                "transitions": [
                    {
                        "id": "hero-scrub",
                        "trigger": "scroll (hero)",
                        "target": "div#hero",
                        "selector": "div#hero",
                        "animation": {
                            "type": "scroll-scrub",
                            "spring": {"stiffness": 900, "damping": 60},
                            "inputRange": [0, 0.3],
                            "scaleOutputRange": [0.9, 1.0],
                        },
                    },
                ],
            }
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    sites = {s.get("selector"): s for s in (plan.get("scrollScrub") or {}).get("sites") or []}
    hero = sites.get("div#hero")
    assert hero is not None, "documented inputRange scrub shape not mined into sites"
    assert hero.get("spring") == {"stiffness": 900, "damping": 60}


def test_generation_plan_routes_latched_rows_away_from_scrub_bands(tmp_path: Path) -> None:
    """A latched row is a discrete state, not a curve. Interpolating it across
    a progress band renders every state permanently half-applied, so it must
    leave scrollScrub and arrive as a scrollLatch site keyed by progress
    fraction (not capture-session pixels) with the settled end state."""
    ref = tmp_path / "ref" / "framer-site"
    ref.mkdir(parents=True)
    (ref / "animation-runtime-dump.json").write_text(
        json.dumps(
            {
                "scrollLinkedStyles": [
                    {
                        "selector": "nav .label",
                        "selectorIndex": 0,
                        "varies": ["opacity"],
                        "latched": True,
                        "byScroll": {
                            "0": {"opacity": "0"},
                            "0.1": {"opacity": "1"},
                            "0.2": {"opacity": "1"},
                        },
                    },
                    {
                        "selector": ".hero",
                        "selectorIndex": 0,
                        "varies": ["opacity"],
                        "latched": False,
                        "byScroll": {
                            "0": {"opacity": "0"},
                            "0.1": {"opacity": "0.5"},
                            "0.2": {"opacity": "1"},
                        },
                    },
                ]
            }
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    scrub_selectors = {
        s.get("selector") for s in ((plan.get("scrollScrub") or {}).get("sites") or [])
    }
    assert "nav .label" not in scrub_selectors, "latched row replayed as a scrub band"
    assert ".hero" in scrub_selectors, "genuine scroll-linked row was dropped"

    latch_sites = (plan.get("scrollLatch") or {}).get("sites") or []
    latch = next((s for s in latch_sites if s.get("selector") == "nav .label"), None)
    assert latch is not None, "latched row was dropped instead of routed"
    assert latch["progress"] == 0.1
    assert latch["endState"] == {"opacity": "1"}


def test_generation_plan_mines_breakpoint_scoped_input_range(tmp_path: Path) -> None:
    """Specs that decompile a per-breakpoint useTransform record the domain as
    inputRangeDesktop/inputRangeMobile rather than a bare inputRange. The
    desktop domain must still be mined, otherwise a fully parameterised
    decompiled curve is dropped for a naming difference alone."""
    ref = tmp_path / "ref" / "framer-site"
    ref.mkdir(parents=True)
    spec = {
        "source": "ui_clone.extraction_artifacts",
        "transitions": [
            {
                "id": "pyramid-card-background-scroll-scale",
                "trigger": "scroll (pyramid section)",
                "target": ".card_bg",
                "selector": ".card_bg",
                "animation": {
                    "type": "scroll-scrub",
                    "property": "scale",
                    "inputRangeDesktop": [0, 0.1, 0.75, 0.9],
                    "inputRangeMobile": [0, 0.05, 0.75, 0.9],
                    "outputRange": [0.9, 1, 1, 1],
                    "spring": {"stiffness": 700, "damping": 60},
                },
            },
        ],
    }
    (ref / "transition-spec.json").write_text(json.dumps(spec))

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    sites = (plan.get("scrollScrub") or {}).get("sites") or []
    card = next((s for s in sites if s.get("selector") == ".card_bg"), None)
    assert card is not None, "breakpoint-scoped inputRange scrub not mined into sites"
    scale = next(t for t in card["transforms"] if t["property"] == "scale")
    assert json.loads(scale["input"]) == [0.0, 0.1, 0.75, 0.9]
    assert json.loads(scale["output"]) == [0.9, 1.0, 1.0, 1.0]


def test_generation_plan_keeps_spec_scrub_sites_when_runtime_samples_overflow(
    tmp_path: Path,
) -> None:
    """scrollScrub.sites is capped, so ordering decides what survives. A
    decompiled spec curve models the motion; a runtime sample only records it
    at coarse document-progress points. When runtime rows alone exceed the
    cap, the spec-derived site must still be emitted."""
    ref = tmp_path / "ref" / "framer-site"
    ref.mkdir(parents=True)
    spec = {
        "source": "ui_clone.extraction_artifacts",
        "transitions": [
            {
                "id": "hero-video-scroll-progress",
                "trigger": "scroll (hero section)",
                "target": "div#hero-video",
                "selector": "div#hero-video",
                "animation": {
                    "type": "scroll-scrub",
                    "property": "y",
                    "inputRange": [0, 0.3],
                    "yOutputRange": [80, 0],
                    "spring": {"stiffness": 900, "damping": 60},
                },
            },
        ],
    }
    (ref / "transition-spec.json").write_text(json.dumps(spec))
    (ref / "animation-runtime-dump.json").write_text(
        json.dumps(
            {
                "scrollLinkedStyles": [
                    {
                        "selector": f".row-{_i}",
                        "selectorIndex": 0,
                        "varies": ["opacity"],
                        "byScroll": {
                            "0": {"opacity": "0"},
                            "0.5": {"opacity": "0.5"},
                            "1": {"opacity": "1"},
                        },
                    }
                    for _i in range(30)
                ]
            }
        )
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text())
    sites = (plan.get("scrollScrub") or {}).get("sites") or []
    spec_sites = [s for s in sites if str(s.get("source", "")).startswith("transition-spec.")]
    assert spec_sites, (
        "spec-derived scrub site was truncated away by runtime samples; "
        f"{len(sites)} sites emitted, none from transition-spec"
    )
    # A spec site must not silently displace a measured runtime row off the
    # end of the cap, and count must describe what was actually emitted.
    runtime_sites = [s for s in sites if s not in spec_sites]
    assert len(runtime_sites) == 24, (
        f"spec site displaced a measured runtime row: {len(runtime_sites)} runtime sites kept"
    )
    assert (plan.get("scrollScrub") or {}).get("count") == len(sites)


def _write_ebay_runtime_scroll_scrub_fixture(ref: Path) -> None:
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps([{"index": 0, "tag": "section", "id": "hero", "height": 1200}])
    )
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "source": "ui_clone.extraction_artifacts",
                "transitions": [
                    {
                        "id": "framer-motion-scroll-scrub",
                        "trigger": "scroll-scrub",
                        "target": ".style_scrollcontainer__Vup4r",
                        "selector": ".style_scrollcontainer__Vup4r",
                        "animation": {
                            "type": "scroll-scrub",
                            "offset": ["start start", "end end"],
                            "mechanism": (
                                "observed - animation-runtime-dump.json scrollLinkedStyles"
                            ),
                        },
                    }
                ],
            }
        )
    )
    (ref / "animation-runtime-dump.json").write_text(
        json.dumps(
            {
                "scrollLinkedStyles": [
                    {
                        "selector": ".style_scrollcontainer__Vup4r",
                        "selectorIndex": 0,
                        "varies": ["opacity"],
                        "byScroll": {
                            "0": {"opacity": "0.4"},
                            "0.1": {"opacity": "0.7"},
                            "0.2": {"opacity": "1"},
                        },
                    },
                    {
                        "selector": "svg",
                        "selectorIndex": 0,
                        "varies": ["opacity"],
                        "byScroll": {
                            "0": {"opacity": "0"},
                            "0.1": {"opacity": "0.35"},
                            "0.2": {"opacity": "1"},
                        },
                    },
                    {
                        "selector": "g#even",
                        "selectorIndex": 0,
                        "varies": ["transform", "opacity"],
                        "byScroll": {
                            "0": {"transform": "scale(0.425)", "opacity": "0.2"},
                            "0.1": {"transform": "scale(0.7)", "opacity": "0.6"},
                            "0.2": {"transform": "none", "opacity": "1"},
                        },
                    },
                    {
                        "selector": "g#even",
                        "selectorIndex": 1,
                        "varies": ["transform", "opacity"],
                        "byScroll": {
                            "0": {"transform": "scale(0.5)", "opacity": "0.1"},
                            "0.1": {"transform": "scale(0.75)", "opacity": "0.5"},
                            "0.2": {"transform": "scale(1)", "opacity": "1"},
                        },
                    },
                    {
                        "selector": "div",
                        "selectorIndex": 0,
                        "varies": ["width", "borderRadius"],
                        "byScroll": {
                            "0": {"width": "112px", "borderRadius": "24px"},
                            "0.1": {"width": "160px", "borderRadius": "16px"},
                            "0.2": {"width": "208px", "borderRadius": "8px"},
                        },
                    },
                ]
            }
        )
    )


def _ebay_runtime_scroll_scrub_plan(tmp_path: Path) -> dict[str, Any]:
    ref = tmp_path / "ref" / "ebay"
    _write_ebay_runtime_scroll_scrub_fixture(ref)
    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )
    plan = cast(
        dict[str, Any],
        json.loads((ref / "generation-plan.json").read_text()),
    )
    return cast(dict[str, Any], plan["scrollScrub"])


def test_generation_plan_runtime_scroll_scrub_keeps_duplicate_selector_indexes(
    tmp_path: Path,
) -> None:
    """Duplicate runtime selectors must remain separate by selectorIndex."""
    scrub = _ebay_runtime_scroll_scrub_plan(tmp_path)

    even_sites = [
        site for site in scrub["sites"]
        if site.get("source") == "animation-runtime-dump.json:scrollLinkedStyles"
        and site.get("selector") == "g#even"
    ]

    assert [site.get("selectorIndex") for site in even_sites] == [0, 1]


def test_generation_plan_runtime_scroll_scrub_decomposes_transform_scale(
    tmp_path: Path,
) -> None:
    """Runtime transform scale() strings must become numeric scale bands."""
    scrub = _ebay_runtime_scroll_scrub_plan(tmp_path)

    first_even = next(
        site for site in scrub["sites"]
        if site.get("source") == "animation-runtime-dump.json:scrollLinkedStyles"
        and site.get("selector") == "g#even"
    )
    scale = next(t for t in first_even["transforms"] if t["property"] == "scale")

    assert json.loads(scale["output"]) == [0.425, 0.7, 1.0]


def test_generation_plan_runtime_scroll_scrub_remaps_document_samples_to_target_offset(
    tmp_path: Path,
) -> None:
    """Document-fraction samples must replay against the captured target window.

    A clone's total document height may differ from the reference even when the
    scrub section itself has the correct geometry. Keeping document fractions
    makes the transition fire early or late; remapping the sampled values onto
    the reference target/offset interval preserves Framer ``useScroll``
    semantics and lets the runtime recompute against the implementation target.
    """
    ref = tmp_path / "ref" / "target-offset"
    ref.mkdir(parents=True)
    (ref / "section-map.json").write_text(
        json.dumps(
            {
                "docHeight": 3500,
                "sections": [
                    {
                        "index": 0,
                        "tag": "div",
                        "className": "style_scrollcontainer__Vup4r",
                        "top": 1000,
                        "height": 1500,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "featured-grid",
                        "trigger": "scroll",
                        "target": ".style_scrollcontainer__Vup4r",
                        "animation": {
                            "type": "framer-motion-scroll-scrub",
                            "offset": ["start start", "end end"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "animation-runtime-dump.json").write_text(
        json.dumps(
            {
                "viewport": {"width": 1440, "height": 1000},
                "documentScroll": {"scrollHeight": 3500, "maxScroll": 2500},
                "scrollLinkedStyles": [
                    {
                        "selector": "g#even",
                        "varies": ["transform"],
                        "byScroll": {
                            "0.2": {"transform": "scale(0.5)"},
                            "0.4": {"transform": "scale(0.5)"},
                            "0.5": {"transform": "scale(0.75)"},
                            "0.6": {"transform": "none"},
                            "0.8": {"transform": "none"},
                        },
                    },
                    {
                        "selector": ".responsive-panel",
                        "varies": ["width"],
                        "byScroll": {
                            "0.4": {"width": "100%"},
                            "0.5": {"width": "75%"},
                            "0.6": {"width": "50%"},
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    subprocess.run(
        ["bash", str(_project_root() / "scripts/extract/generation-plan.sh"), str(ref)],
        check=True,
    )

    plan = json.loads((ref / "generation-plan.json").read_text(encoding="utf-8"))
    site = next(
        item
        for item in plan["scrollScrub"]["sites"]
        if item.get("source") == "animation-runtime-dump.json:scrollLinkedStyles"
    )
    scale = next(item for item in site["transforms"] if item["property"] == "scale")

    assert site["progressSource"] == "target-offset"
    assert json.loads(site["offset"]) == ["start start", "end end"]
    assert json.loads(scale["input"]) == [0.0, 0.5, 1.0]
    assert json.loads(scale["output"]) == [0.5, 0.75, 1.0]
    responsive = next(
        item
        for item in plan["scrollScrub"]["sites"]
        if item.get("selector") == ".responsive-panel"
    )
    width = next(
        item for item in responsive["transforms"] if item["property"] == "width"
    )
    assert width["unit"] == "%"
    assert json.loads(width["output"]) == [100.0, 75.0, 50.0]


def test_generation_plan_runtime_scroll_scrub_preserves_svg_opacity_and_div_size_bands(
    tmp_path: Path,
) -> None:
    """Runtime opacity, width, and borderRadius channels must stay distinct."""
    scrub = _ebay_runtime_scroll_scrub_plan(tmp_path)
    runtime_sites = [
        site for site in scrub["sites"]
        if site.get("source") == "animation-runtime-dump.json:scrollLinkedStyles"
    ]
    by_selector = {site["selector"]: site for site in runtime_sites}

    opacity = next(t for t in by_selector["svg"]["transforms"] if t["property"] == "opacity")
    width = next(t for t in by_selector["div"]["transforms"] if t["property"] == "width")
    border_radius = next(
        t for t in by_selector["div"]["transforms"] if t["property"] == "borderRadius"
    )

    assert json.loads(opacity["output"]) == [0.0, 0.35, 1.0]
    assert json.loads(width["output"]) == [112.0, 160.0, 208.0]
    assert json.loads(border_radius["output"]) == [24.0, 16.0, 8.0]


def test_generation_plan_runtime_scroll_scrub_sites_carry_document_progress_target_scope(
    tmp_path: Path,
) -> None:
    """Runtime scrub sites must bind to the sole framer scroll target."""
    scrub = _ebay_runtime_scroll_scrub_plan(tmp_path)
    runtime_sites = [
        site for site in scrub["sites"]
        if site.get("source") == "animation-runtime-dump.json:scrollLinkedStyles"
    ]

    assert runtime_sites
    assert {site.get("progressSource") for site in runtime_sites} == {"document-progress"}
    assert {site.get("target") for site in runtime_sites} == {".style_scrollcontainer__Vup4r"}


def test_generation_plan_runtime_scroll_scrub_preserves_scoped_root_self_target(
    tmp_path: Path,
) -> None:
    """The eBay-shaped runtime plan must carry the scoped root as its own site."""
    scrub = _ebay_runtime_scroll_scrub_plan(tmp_path)
    root_site = next(
        site for site in scrub["sites"]
        if site.get("source") == "animation-runtime-dump.json:scrollLinkedStyles"
        and site.get("selector") == ".style_scrollcontainer__Vup4r"
    )

    assert root_site["selectorIndex"] == 0
    assert root_site["scope"] == ".style_scrollcontainer__Vup4r"
    assert root_site["target"] == ".style_scrollcontainer__Vup4r"
    opacity = next(t for t in root_site["transforms"] if t["property"] == "opacity")
    assert json.loads(opacity["output"]) == [0.4, 0.7, 1.0]
