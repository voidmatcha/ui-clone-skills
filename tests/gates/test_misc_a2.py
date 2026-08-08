import json
import os
from pathlib import Path

from ui_clone.gate import Gate

from ._helpers import (
    _RESULT_TABLE_TEMPLATE,
    _build_renamed_impl,
    _make_stub_compare,
    _post_implement_baseline,
    _project_root,
)


def test_find_impl_root_skips_skip_dir_names(tmp_path: Path) -> None:
    """Codex L38 issue 11 — adversarial rename negative path.

    A sub-agent renaming impl/ to a name in the resolver's skip-list
    (`dist`, `node_modules`, `.next`, `.git`, `benchmark`, `tmp`, `scratch`,
    `scripts`) MUST NOT escape detection by sneaking through the heuristic
    — instead, the resolver should return None so downstream gates that
    require impl_root cannot silently no-op. The right UX is "we cannot
    find impl/" → gate fails loudly, not "looks fine, nothing to check".
    """
    loop_root = tmp_path / "scratch" / "loop-Y"
    ref = loop_root / "tmp" / "ref" / "realfood-main"
    ref.mkdir(parents=True)
    _build_renamed_impl(loop_root, "dist", page_loc=220)
    gate = Gate(ref)
    assert gate._find_impl_root() is None, (
        "resolver must not return a skip-dir-named candidate"
    )



def test_find_impl_root_disambiguates_multiple_candidates(tmp_path: Path) -> None:
    """When two impl-shaped directories exist, the resolver should fail
    with AMBIGUOUS rather than picking arbitrarily — this prevents a
    sub-agent from making a second clone to hide the broken first one.
    """
    loop_root = tmp_path / "scratch" / "loop-Z"
    ref = loop_root / "tmp" / "ref" / "realfood-main"
    ref.mkdir(parents=True)
    _build_renamed_impl(loop_root, "realfood-clone-a", page_loc=50)
    _build_renamed_impl(loop_root, "realfood-clone-b", page_loc=50)
    gate = Gate(ref)
    # Resolver script exits 2 with AMBIGUOUS message when neither has a
    # framework config marker (next.config / vite.config) — gate returns
    # None on non-zero exit.
    assert gate._find_impl_root() is None, (
        "resolver must refuse to pick between two impl-shaped siblings"
    )


def test_find_impl_root_ignores_other_scratch_run_state(tmp_path: Path) -> None:
    """State/marker wires into scratch/<different-run> are stale, not explicit.

    Env override remains the supported escape hatch for intentionally arbitrary
    impl locations; local resolver state must not bind one ref to another
    scratch clone and then let gates pass/fail against the wrong source tree.
    """
    repo = tmp_path / "repo"
    ref = repo / "tmp" / "ref" / "project-a-main"
    ref.mkdir(parents=True)
    stale_impl = repo / "scratch" / "project-a-sustainability-04"
    (stale_impl / "src").mkdir(parents=True)
    (stale_impl / "src" / "App.tsx").write_text(
        "export default function App(){return null}\n", encoding="utf-8",
    )
    (stale_impl / "package.json").write_text("{}", encoding="utf-8")
    (repo / "impl").symlink_to(stale_impl, target_is_directory=True)
    (ref / ".impl-root").write_text(str(stale_impl) + "\n", encoding="utf-8")
    (ref / "pipeline-state.json").write_text(
        json.dumps({"component": ref.name, "implRoot": str(stale_impl)}),
        encoding="utf-8",
    )

    gate = Gate(ref)
    assert gate._find_impl_root() is None


def test_find_impl_root_trusts_cross_scratch_marker_with_backlink(
    tmp_path: Path,
) -> None:
    """Mutual handshake: a cross-scratch .impl-root marker is trusted when the
    impl dir contains a .ref-dir backlink resolving to THIS ref dir.

    User-chosen impl layouts (e.g. scratch/loop-e2e-1 for component
    realfood-e2e-1) are intentional, not stale; the backlink proves intent.
    Stale markers still fail (old impl has no backlink or one pointing at its
    own ref dir).
    """
    repo = tmp_path / "repo"
    ref = repo / "tmp" / "ref" / "realfood-e2e-1"
    ref.mkdir(parents=True)
    impl = repo / "scratch" / "loop-e2e-1"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return null}\n", encoding="utf-8",
    )
    (impl / "package.json").write_text("{}", encoding="utf-8")
    (ref / ".impl-root").write_text(str(impl) + "\n", encoding="utf-8")

    gate = Gate(ref)
    # Without the backlink: rejected (stale-marker protection intact).
    assert gate._find_impl_root() is None

    (impl / ".ref-dir").write_text(str(ref.resolve()) + "\n", encoding="utf-8")
    assert gate._find_impl_root() == impl.resolve()

    # Backlink pointing at a DIFFERENT ref dir does not unlock the marker.
    (impl / ".ref-dir").write_text(
        str((repo / "tmp" / "ref" / "other-run").resolve()) + "\n",
        encoding="utf-8",
    )
    assert gate._find_impl_root() is None


def test_componentization_gate_skipped_when_page_small(tmp_path: Path) -> None:
    """page.tsx ≤ 200 LOC → silent skip even if components/ is empty.
    A small monolith is still legible and the split forcing is unnecessary.
    """
    work = tmp_path / "benchmark" / "work" / "deadbee"
    ref = work / "ref"
    impl = work / "impl"
    ref.mkdir(parents=True)
    _post_implement_baseline(ref)
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "src" / "app" / "page.tsx").write_text(
        "\n".join(f"// line {i}" for i in range(150)) + "\n", encoding="utf-8"
    )
    gate = Gate(ref)
    failures = [r for r in gate.gate_post_implement() if r.status == "fail"]
    assert not any(r.label == "componentization" for r in failures)



def test_spec_implementation_coverage_fails_when_motion_missing(tmp_path: Path) -> None:
    """The script must exit non-zero when an entry's selector is matched in
    impl source but the matched file contains no motion declaration.

    Reproduces the silent-killer: spec author wrote a scroll-driven entry,
    transition-spec-coverage passes (selector hits the impl), but the impl
    component returns a static element with no transition / animation /
    framer-motion / useScroll / IntersectionObserver wiring.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-fade", "trigger": "scroll", "type": "scroll-driven", "selector": ".hero"}]
    }))
    (impl / "src" / "Hero.tsx").write_text(
        "export function Hero() { return <section className=\"hero\">static</section>; }\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["presenceOnly"] == 1


def test_spec_implementation_coverage_matches_base_class_before_functional_pseudo(
    tmp_path: Path,
) -> None:
    """Functional pseudo syntax must not become part of the selector needle."""
    import subprocess

    cases = [
        (".flash-close:not(.Banner-close)", "flash-close"),
        (
            '.prc-Button-ButtonBase-9n-Xk:where([data-variant="invisible"])',
            "prc-Button-ButtonBase-9n-Xk",
        ),
    ]
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "spec-implementation-coverage.sh"
    )

    for index, (selector, class_name) in enumerate(cases):
        comp = tmp_path / f"comp-{index}"
        impl = tmp_path / f"impl-{index}"
        comp.mkdir()
        (impl / "src").mkdir(parents=True)
        (comp / "transition-spec.json").write_text(
            json.dumps({
                "transitions": [{
                    "id": f"functional-pseudo-hover-{index}",
                    "trigger": "hover",
                    "type": "css-hover",
                    "selector": selector,
                }]
            }),
            encoding="utf-8",
        )
        (impl / "src" / "Button.tsx").write_text(
            "export function Button() {\n"
            f"  return <button className=\"{class_name} transition-transform "
            "hover:scale-95\">Close</button>;\n"
            "}\n",
            encoding="utf-8",
        )

        proc = subprocess.run(
            ["bash", str(script), str(comp), str(impl)],
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert proc.returncode == 0, (
            f"expected selector {selector!r} to match its base class: "
            f"{proc.stdout}\n{proc.stderr}"
        )
        artifact = json.loads(
            (comp / "spec-implementation-coverage.json").read_text()
        )
        assert artifact["status"] == "pass"
        assert artifact["withMotion"] == 1


def test_spec_implementation_coverage_ignores_functional_pseudo_arguments(
    tmp_path: Path,
) -> None:
    """A class inside :not() must not hide an absent selector base class."""
    import subprocess

    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    comp.mkdir()
    (impl / "src").mkdir(parents=True)
    (comp / "transition-spec.json").write_text(
        json.dumps({
            "transitions": [{
                "id": "absent-functional-pseudo-hover",
                "trigger": "hover",
                "type": "css-hover",
                "selector": ".missing-close:not(.flash-close)",
            }]
        }),
        encoding="utf-8",
    )
    (impl / "src" / "styles.css").write_text(
        ".flash-close:hover { "
        "transition: transform 150ms ease; transform: scale(.95); }\n",
        encoding="utf-8",
    )
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "spec-implementation-coverage.sh"
    )

    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 1, (
        f"pseudo argument must not satisfy an absent base selector: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads(
        (comp / "spec-implementation-coverage.json").read_text()
    )
    assert artifact["status"] == "fail"
    assert artifact["missingEntirely"] == 1


def test_spec_implementation_coverage_accepts_plain_css_sticky_without_listener(
    tmp_path: Path,
) -> None:
    """Native sticky positioning does not require a scroll event controller."""
    import subprocess

    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    comp.mkdir()
    (impl / "src").mkdir(parents=True)
    (comp / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "docs-sticky-header",
                        "trigger": "scroll",
                        "type": "css-sticky",
                        "selector": "header.position-sticky",
                        "animation": {
                            "type": "css-sticky",
                            "changedProperties": ["position", "top"],
                            "duration": "0s",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (impl / "src" / "Header.tsx").write_text(
        "export function Header() {\n"
        '  return <header className="position-sticky" '
        'style={{ position: "sticky", top: 0 }}>Docs</header>;\n'
        "}\n",
        encoding="utf-8",
    )
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "spec-implementation-coverage.sh"
    )

    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads(
        (comp / "spec-implementation-coverage.json").read_text()
    )
    assert artifact["status"] == "pass"
    assert artifact["withMotion"] == 1
    assert "position: sticky" in proc.stdout


def test_spec_implementation_coverage_skips_reset_only_hover(tmp_path: Path) -> None:
    import subprocess

    comp = tmp_path / "component"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "auto-hover-0",
            "trigger": "hover",
            "type": "css-hover",
            "target": "a",
            "selector": "a",
            "animation": {
                "type": "css-hover",
                "cssText": "a:hover {text-decoration:none}",
            },
        }]
    }))
    (impl / "src" / "App.tsx").write_text(
        "export function App() { return <a href='/'>Home</a>; }\n"
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["total"] == 0
    assert "reset-only hover" in proc.stdout


def test_spec_implementation_coverage_ignores_generated_ref_css_motion(tmp_path: Path) -> None:
    """ref-css is generated reference material; its CSS transitions must not
    satisfy the implementation-motion gate.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src" / "ref-css").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-fade", "trigger": "scroll", "type": "scroll-driven", "selector": ".hero"}]
    }))
    (impl / "src" / "Hero.tsx").write_text(
        "export function Hero() { return <section className=\"hero\">static</section>; }\n",
        encoding="utf-8",
    )
    (impl / "src" / "ref-css" / "page.css").write_text(
        ".hero { transition: opacity 300ms ease; animation: heroFade 1s ease; }\n",
        encoding="utf-8",
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl / "src")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, f"generated ref-css must not satisfy impl motion: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["presenceOnly"] == 1


def test_spec_implementation_coverage_accepts_imported_generated_ref_css_hover(
    tmp_path: Path,
) -> None:
    """Imported generated CSS is executable evidence when the DOM target exists."""
    import subprocess

    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src" / "ref-css").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "nav-link-hover",
            "trigger": "hover",
            "type": "css-hover",
            "selector": ".nav-link",
        }]
    }))
    (impl / "src" / "App.tsx").write_text(
        'import "./ref-css/page.css";\n'
        "export function App() { return <a className=\"nav-link\">Docs</a>; }\n",
        encoding="utf-8",
    )
    (impl / "src" / "ref-css" / "page.css").write_text(
        ".nav-link:hover::after { "
        "transition: width 160ms ease; width: 100%; }\n",
        encoding="utf-8",
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl / "src")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["withMotion"] == 1


def test_spec_implementation_coverage_rejects_unimported_css_hover(
    tmp_path: Path,
) -> None:
    """A stylesheet under src is dead evidence until an executable source
    module imports it.
    """
    import subprocess

    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src" / "styles").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "nav-link-hover",
            "trigger": "hover",
            "type": "css-hover",
            "selector": ".nav-link",
        }]
    }))
    (impl / "src" / "App.tsx").write_text(
        "export function App() { return <a className=\"nav-link\">Docs</a>; }\n",
        encoding="utf-8",
    )
    (impl / "src" / "styles" / "dead.css").write_text(
        ".nav-link:hover::after { transition: width 160ms ease; width: 100%; }\n",
        encoding="utf-8",
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl / "src")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["presenceOnly"] == 1


def test_spec_implementation_coverage_accepts_transitive_imported_ref_css_hover(
    tmp_path: Path,
) -> None:
    """Generated CSS can be executable through a local TS barrel import."""
    import subprocess

    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src" / "ref-css").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "nav-link-hover",
            "trigger": "hover",
            "type": "css-hover",
            "selector": ".nav-link",
        }]
    }))
    (impl / "src" / "main.tsx").write_text(
        'import "./ref-css/index";\n'
        "export function App() { return <a className=\"nav-link\">Docs</a>; }\n",
        encoding="utf-8",
    )
    (impl / "src" / "ref-css" / "index.ts").write_text(
        'import "./page.css";\n',
        encoding="utf-8",
    )
    (impl / "src" / "ref-css" / "page.css").write_text(
        ".nav-link:hover::after { "
        "transition: width 160ms ease; width: 100%; }\n",
        encoding="utf-8",
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl / "src")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["withMotion"] == 1


def test_spec_implementation_coverage_rejects_imported_css_without_dom_target(
    tmp_path: Path,
) -> None:
    """Imported CSS must not satisfy coverage when no implementation renders the selector."""
    import subprocess

    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src" / "ref-css").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "missing-nav-link-hover",
            "trigger": "hover",
            "type": "css-hover",
            "selector": ".nav-link",
        }]
    }))
    (impl / "src" / "App.tsx").write_text(
        'import "./ref-css/page.css";\n'
        "export function App() { return <main>Docs</main>; }\n",
        encoding="utf-8",
    )
    (impl / "src" / "ref-css" / "page.css").write_text(
        ".nav-link:hover { transition: color 160ms ease; color: red; }\n",
        encoding="utf-8",
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl / "src")],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingEntirely"] == 1


def test_spec_implementation_coverage_uses_configured_generated_evidence_dirs(tmp_path: Path) -> None:
    """Generated evidence dir names are configurable, not hardwired to ref-css."""
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    generated_dir = impl / "src" / "reference-css"
    generated_dir.mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "card-hover", "trigger": "hover", "type": "hover", "selector": ".card"}]
    }))
    (impl / "src" / "Card.tsx").write_text(
        "export function Card() { return <article className=\"card\">static</article>; }\n",
        encoding="utf-8",
    )
    (generated_dir / "page.css").write_text(
        ".card { transition: transform 250ms ease; }\n",
        encoding="utf-8",
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl / "src")],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "UI_CLONE_GENERATED_EVIDENCE_DIRS": "reference-css"},
    )
    assert proc.returncode == 1, f"configured generated dir must not satisfy impl motion: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["presenceOnly"] == 1



def test_spec_implementation_coverage_passes_when_motion_declared(tmp_path: Path) -> None:
    """The script must exit 0 when every covered entry's matched file has at
    least one motion-declaration keyword (transition / framer-motion / useScroll
    / IntersectionObserver / animate-* / etc.).

    Catches the inverse failure mode: a too-strict matcher would false-fail
    valid impls and force callers to disable the gate. The needle list in
    spec-implementation-coverage.sh is intentionally permissive so common
    framer-motion + Tailwind impls register without configuration.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-fade", "trigger": "scroll", "type": "scroll-driven", "selector": ".hero"}]
    }))
    (impl / "src" / "Hero.tsx").write_text(
        "import { useScroll, useTransform } from \"framer-motion\";\n"
        "export function Hero() {\n"
        "  const { scrollYProgress } = useScroll();\n"
        "  const opacity = useTransform(scrollYProgress, [0, 1], [0, 1]);\n"
        "  return <section className=\"hero\">animated</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["withMotion"] == 1



def test_spec_implementation_coverage_fails_marker_only_trigger_hooks(tmp_path: Path) -> None:
    """Loop-56 regression: hidden marker strings and generic useScroll text
    must not count as real trigger-specific transition implementations.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src" / "app").mkdir(parents=True)
    (impl / "src" / "components").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "page-load-reveal", "trigger": "load", "type": "reveal", "selector": "main section"},
            {"id": "smooth-scroll-lenis", "trigger": "scroll", "type": "scroll", "selector": "html"},
            {"id": "nav-dot-hover", "trigger": "hover", "type": "hover", "selector": ".nav_dot_button__kZB4V"},
            {"id": "faq-click-state", "trigger": "click", "type": "accordion", "selector": "section"},
        ]
    }))
    (impl / "src" / "app" / "page.tsx").write_text(
        "export default function Page() {\n"
        "  return <main data-transition=\"page-load-reveal smooth-scroll-lenis nav-dot-hover faq-click-state\">\n"
        "    <section data-scroll-hook=\"Lenis useScroll scroll(\" data-hover-hook=\":hover onPointerEnter\">static</section>\n"
        "  </main>;\n"
        "}\n"
    )
    (impl / "src" / "components" / "TransitionHooks.tsx").write_text(
        "export function TransitionHooks() {\n"
        "  const hooks = [\n"
        "    'page-load-reveal',\n"
        "    'smooth-scroll-lenis',\n"
        "    'nav-dot-hover',\n"
        "    'faq-click-state',\n"
        "    'main section',\n"
        "    '.nav_dot_button__kZB4V',\n"
        "    'Lenis',\n"
        "    'useScroll',\n"
        "    ':hover',\n"
        "    'onPointerEnter',\n"
        "  ];\n"
        "  return <span hidden data-transition-hooks={hooks.join(' ')} />;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["presenceOnly"] == 4
    assert artifact["markerOnly"] == 4



def test_spec_implementation_coverage_fails_unrelated_generic_motion(tmp_path: Path) -> None:
    """A generic motion hook in a matched file must not satisfy a different
    trigger family such as click/accordion.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "faq-click-state",
            "trigger": "click",
            "type": "accordion",
            "selector": ".faq",
        }]
    }))
    (impl / "src" / "Faq.tsx").write_text(
        "import { useScroll } from 'framer-motion';\n"
        "export function Faq() {\n"
        "  const scroll = useScroll();\n"
        "  return <section className=\"faq\" data-scroll={String(scroll)}>static</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["triggerStatic"] == 1



def test_spec_implementation_coverage_passes_trigger_specific_impls(tmp_path: Path) -> None:
    """Trigger-specific implementations should pass without relying on
    unrelated generic motion keywords.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "nav-dot-hover", "trigger": "hover", "type": "hover", "selector": ".nav-dot"},
            {"id": "faq-click-state", "trigger": "click", "type": "accordion", "selector": ".faq"},
            {"id": "smooth-scroll-lenis", "trigger": "scroll", "type": "smooth-scroll", "selector": "html"},
        ]
    }))
    (impl / "src" / "Interactions.tsx").write_text(
        "import Lenis from 'lenis';\n"
        "import { useState } from 'react';\n"
        "export function Interactions() {\n"
        "  const [open, setOpen] = useState(false);\n"
        "  const lenis = new Lenis({ smoothWheel: true });\n"
        # data-transition wires the smooth-scroll-lenis entry to this file so
        # FIX 3's missing-entirely accounting can locate it (Lenis targets the
        # whole page, so the spec selector is "html" with no class hook).
        "  return <main data-transition=\"smooth-scroll-lenis\">\n"
        "    <button className=\"nav-dot transition-transform hover:scale-105\" onPointerEnter={() => lenis.raf(performance.now())}>dot</button>\n"
        "    <section className=\"faq\" aria-expanded={open} onClick={() => setOpen(!open)} style={{ transition: 'height .3s' }}>faq</section>\n"
        "  </main>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["triggerStatic"] == 0


def test_spec_implementation_coverage_does_not_treat_class_toggle_as_click(
    tmp_path: Path,
) -> None:
    """Scroll and intersection class toggles are not click interactions."""
    import subprocess

    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "hero-enter-leave-state",
                "trigger": "scroll",
                "type": "scrolltrigger-class-toggle",
                "selector": ".hero",
            },
            {
                "id": "effect-data-flip-reveal",
                "trigger": "intersection",
                "type": "intersectionobserver-class-toggle",
                "selector": ".effect-data",
            },
        ]
    }))
    (impl / "src" / "Interactions.tsx").write_text(
        "import { useEffect } from 'react';\n"
        "export function Interactions() {\n"
        "  useEffect(() => {\n"
        "    const hero = document.querySelector('.hero');\n"
        "    const reveal = document.querySelector('.effect-data');\n"
        "    const onScroll = () => hero?.classList.toggle('enter', hero.getBoundingClientRect().top < 0);\n"
        "    window.addEventListener('scroll', onScroll);\n"
        "    const observer = new IntersectionObserver(([entry]) => reveal?.classList.toggle('active', entry.isIntersecting));\n"
        "    if (reveal) observer.observe(reveal);\n"
        "    return () => { window.removeEventListener('scroll', onScroll); observer.disconnect(); };\n"
        "  }, []);\n"
        "  return <><section className=\"hero\" style={{ transition: 'opacity .2s' }} />"
        "<div className=\"effect-data\" style={{ transition: 'transform .4s' }} /></>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["triggerStatic"] == 0



def test_spec_implementation_coverage_fails_scroll_scrub_css_only(tmp_path: Path) -> None:
    """Loop-55 regression: a scroll-scrub entry must not pass just because
    the selector appears next to a CSS transition. Pinned scrollytelling needs
    a scroll progress source and a sticky/pin structure.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "line-pin",
            "trigger": "scroll",
            "type": "scroll-scrub",
            "selector": ".line",
        }]
    }))
    (impl / "src" / "Line.tsx").write_text(
        "export function Line() {\n"
        "  return <section className=\"line\" style={{ transition: 'opacity .45s, transform .45s' }}>static</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["scrollScrubStatic"] == 1


def test_spec_implementation_coverage_passes_scroll_scrub_with_progress_without_pin(
    tmp_path: Path,
) -> None:
    """Non-pinned scroll scrub requires progress wiring but not sticky structure."""
    import subprocess

    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "framer-grid-scrub",
            "trigger": "scroll",
            "type": "scroll-scrub",
            "selector": ".scroll-grid",
            "animation": {"type": "scroll-scrub", "pin": False},
        }]
    }))
    (impl / "src" / "Grid.tsx").write_text(
        "import { useScroll, useTransform } from \"framer-motion\";\n"
        "export function Grid() {\n"
        "  const { scrollYProgress } = useScroll();\n"
        "  const opacity = useTransform(scrollYProgress, [0, 1], [0, 1]);\n"
        "  return <section className=\"scroll-grid\" style={{ opacity }}>animated</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["scrollScrubStatic"] == 0


def test_spec_implementation_coverage_passes_modular_scroll_driver_wiring(
    tmp_path: Path,
) -> None:
    """Selector data can live in one module while progress wiring lives in its driver."""
    import subprocess

    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "framer-grid-scrub",
            "trigger": "scroll",
            "type": "scroll-scrub",
            "selector": ".scroll-grid",
            "animation": {"type": "scroll-scrub", "pin": False},
        }]
    }))
    (impl / "src" / "main.tsx").write_text(
        'import { ScrollLinkedStyleDriver } from "./ScrollLinkedStyleDriver";\n'
        "export function App() {\n"
        "  return <><section className=\"scroll-grid\">animated</section><ScrollLinkedStyleDriver /></>;\n"
        "}\n",
        encoding="utf-8",
    )
    (impl / "src" / "scrollLinkedStyleSites.ts").write_text(
        "export const scrollLinkedStyleSites = [{ selector: '.scroll-grid' }];\n",
        encoding="utf-8",
    )
    (impl / "src" / "ScrollLinkedStyleDriver.tsx").write_text(
        'import { scrollLinkedStyleSites } from "./scrollLinkedStyleSites";\n'
        "export function ScrollLinkedStyleDriver() {\n"
        "  requestAnimationFrame(() => scrollLinkedStyleSites.length);\n"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["scrollScrubStatic"] == 0


def test_spec_implementation_coverage_rejects_unrelated_sibling_scroll_driver(
    tmp_path: Path,
) -> None:
    """A driver imported beside a static target is not target wiring."""
    import subprocess

    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "framer-grid-scrub",
            "trigger": "scroll",
            "type": "scroll-scrub",
            "selector": ".scroll-grid",
            "animation": {"type": "scroll-scrub", "pin": False},
        }]
    }))
    (impl / "src" / "App.tsx").write_text(
        'import { Grid } from "./Grid";\n'
        'import { UnrelatedDriver } from "./UnrelatedDriver";\n'
        "export function App() { return <><Grid /><UnrelatedDriver /></>; }\n",
        encoding="utf-8",
    )
    (impl / "src" / "Grid.tsx").write_text(
        'export function Grid() { return <section className="scroll-grid">static</section>; }\n',
        encoding="utf-8",
    )
    (impl / "src" / "UnrelatedDriver.tsx").write_text(
        "export function UnrelatedDriver() {\n"
        "  requestAnimationFrame(() => document.body.dataset.ready = 'true');\n"
        "  return null;\n"
        "}\n",
        encoding="utf-8",
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["scrollScrubStatic"] == 1


def test_spec_implementation_coverage_requires_pin_when_scroll_scrub_declares_pin(
    tmp_path: Path,
) -> None:
    """Pinned scroll scrub still requires sticky/pin structure."""
    import subprocess

    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "line-pin",
            "trigger": "scroll",
            "type": "scroll-scrub",
            "selector": ".line",
            "animation": {"type": "scroll-scrub", "pin": True},
        }]
    }))
    (impl / "src" / "Line.tsx").write_text(
        "import { useScroll, useTransform } from \"framer-motion\";\n"
        "export function Line() {\n"
        "  const { scrollYProgress } = useScroll();\n"
        "  const opacity = useTransform(scrollYProgress, [0, 1], [0, 1]);\n"
        "  return <section className=\"line\" style={{ opacity }}>animated</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["scrollScrubStatic"] == 1



def test_spec_implementation_coverage_passes_scroll_scrub_with_progress_and_pin(tmp_path: Path) -> None:
    """scroll-scrub passes when matched source has both scroll progress wiring
    and sticky/pin structure.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "line-pin",
            "trigger": "scroll",
            "type": "scroll-scrub",
            "selector": ".line",
        }]
    }))
    (impl / "src" / "Line.tsx").write_text(
        "import { useScroll, useTransform } from \"framer-motion\";\n"
        "export function Line() {\n"
        "  const { scrollYProgress } = useScroll();\n"
        "  const opacity = useTransform(scrollYProgress, [0, 1], [0, 1]);\n"
        "  return <section className=\"line\" style={{ position: 'sticky', top: 0, opacity }}>animated</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["scrollScrubStatic"] == 0



def test_spec_implementation_coverage_fails_intersection_reveal_css_only(tmp_path: Path) -> None:
    """Intersection reveal needs viewport/observer wiring. A CSS transition on
    the selector is only a style declaration, not an in-view implementation.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "pyramid-reveal",
            "trigger": "intersection",
            "type": "intersection-reveal",
            "selector": ".pyramid",
        }]
    }))
    (impl / "src" / "Pyramid.tsx").write_text(
        "export function Pyramid() {\n"
        "  return <section className=\"pyramid\" style={{ transition: 'opacity .45s, transform .45s' }}>static</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["intersectionStatic"] == 1



def test_spec_implementation_coverage_fails_intersection_reveal_data_attr_css_only(tmp_path: Path) -> None:
    """A data-in-view CSS state is not observer wiring by itself."""
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "pyramid-reveal",
            "trigger": "intersection",
            "type": "intersection-reveal",
            "selector": ".pyramid",
        }]
    }))
    (impl / "src" / "styles.css").write_text(
        ".pyramid { transition: opacity .45s, transform .45s; }\n"
        ".pyramid[data-in-view=\"true\"] { opacity: 1; transform: none; }\n"
    )
    (impl / "src" / "Pyramid.tsx").write_text(
        'import "./styles.css";\n'
        'export function Pyramid() { return <div className="pyramid">x</div>; }\n',
        encoding="utf-8",
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["intersectionStatic"] == 1



def test_spec_implementation_coverage_passes_intersection_reveal_with_observer(tmp_path: Path) -> None:
    """Intersection reveal passes when matched source has viewport observer
    wiring.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [{
            "id": "pyramid-reveal",
            "trigger": "intersection",
            "type": "intersection-reveal",
            "selector": ".pyramid",
        }]
    }))
    (impl / "src" / "Pyramid.tsx").write_text(
        "export function Pyramid() {\n"
        "  const observer = new IntersectionObserver(() => {});\n"
        "  return <section className=\"pyramid\" style={{ transition: 'opacity .45s, transform .45s' }}>animated</section>;\n"
        "}\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["intersectionStatic"] == 0



def test_known_artifacts_downgrades_valid_entry(tmp_path: Path) -> None:
    """Valid known-artifacts entry downgrades matching ❌ to PASS in gate output."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(_RESULT_TABLE_TEMPLATE)
    (ref / "known-artifacts.json").write_text(json.dumps({
        "schemaVersion": 1,
        "sections": [
            {
                "name": "hero",
                "verifiedBy": "readPixels",
                "evidence": "frame match",
                "aeThresholdCeiling": 1800,
                "verifiedAt": "2026-05-11T00:00:00Z",
            }
        ],
    }))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    # 'hero' downgraded → 1 effective fail (footer), not 2
    fail_msgs = " ".join(r.message for r in failures)
    assert "1 section(s) FAILED" in fail_msgs
    passes = [r for r in results if r.status == "pass"]
    assert any("downgraded" in r.message for r in passes)



def test_known_artifacts_rejects_entry_when_ae_grew(tmp_path: Path) -> None:
    """AE exceeds ceiling × 1.5 → entry rejected, FAIL stays."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(_RESULT_TABLE_TEMPLATE)
    (ref / "known-artifacts.json").write_text(json.dumps({
        "schemaVersion": 1,
        "sections": [
            {
                "name": "footer",
                "verifiedBy": "readPixels",
                "evidence": "frame match",
                "aeThresholdCeiling": 500,  # current 30000 ≫ 500 * 1.5
                "verifiedAt": "2026-05-11T00:00:00Z",
            }
        ],
    }))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    warns = [r for r in results if r.status == "warn"]
    fail_msgs = " ".join(r.message for r in failures)
    assert "2 section(s) FAILED" in fail_msgs
    assert any("bug got worse" in r.message for r in warns)



def test_known_artifacts_rejects_missing_required_fields(tmp_path: Path) -> None:
    """Entry without `evidence`/`aeThresholdCeiling`/etc. → ignored, FAIL stays."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(_RESULT_TABLE_TEMPLATE)
    (ref / "known-artifacts.json").write_text(json.dumps({
        "schemaVersion": 1,
        "sections": [
            {"name": "hero", "verifiedBy": "readPixels"}  # missing fields
        ],
    }))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    fail_msgs = " ".join(r.message for r in failures)
    assert "2 section(s) FAILED" in fail_msgs



def test_known_artifacts_rejects_unknown_verified_by(tmp_path: Path) -> None:
    """`verifiedBy` not in the allowed enum → ignored."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(_RESULT_TABLE_TEMPLATE)
    (ref / "known-artifacts.json").write_text(json.dumps({
        "schemaVersion": 1,
        "sections": [
            {
                "name": "hero",
                "verifiedBy": "vibes",
                "evidence": "looks fine",
                "aeThresholdCeiling": 9999,
                "verifiedAt": "2026-05-11T00:00:00Z",
            }
        ],
    }))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    warns = [r for r in results if r.status == "warn"]
    assert any("unknown verifiedBy" in r.message for r in warns)



def test_known_artifacts_missing_keeps_legacy_behavior(tmp_path: Path) -> None:
    """No known-artifacts.json → existing FAIL counts unchanged."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(_RESULT_TABLE_TEMPLATE)
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    fail_msgs = " ".join(r.message for r in failures)
    assert "2 section(s) FAILED" in fail_msgs



def test_section_count_mismatch_warns(tmp_path: Path) -> None:
    """section-map totalCount=3 vs component-map sectionCount=0 must produce a warn."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"tag": "s1"}, {"tag": "s2"}, {"tag": "s3"}], "totalCount": 3})
    )
    (ref / "component-map.json").write_text(json.dumps({"sections": [], "sectionCount": 0}))
    gate = Gate(ref)
    results = gate._check_section_counts(
        json.loads((ref / "section-map.json").read_text()),
        json.loads((ref / "component-map.json").read_text()),
    )
    warns = [r for r in results if r.status == "warn" and "section count" in r.label.lower()]
    assert warns, "section-map=3 vs component-map=0 must produce a warn"



def test_section_count_both_zero_passes(tmp_path: Path) -> None:
    """section-map totalCount=0 vs component-map sectionCount=0 must pass (not silently skip)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    results = gate._check_section_counts(
        {"sections": [], "totalCount": 0},
        {"sections": [], "sectionCount": 0},
    )
    passes = [r for r in results if r.status == "pass" and "section count" in r.label.lower()]
    assert passes, "Both counts=0 must produce a pass result"



def test_hover_state_compare_fans_out_per_viewport(tmp_path: Path) -> None:
    """VIEWPORTS=\"375x812,1920x1080\" → result.txt names both viewports and the
    per-viewport subdirs exist under transitions/hover-state/.

    Locks in the fan-out output layout: <ref-dir>/transitions/hover-state/
    <WxH>/<safe-name>/ — the per-viewport subdir is what lets diff inspection
    distinguish a desktop pass from a mobile fail (otherwise both write to
    the same target dir and the second clobbers the first).
    """
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(json.dumps({
        "hover": [{"name": "btn", "triggerType": "hover", "selector": ".btn"}]
    }))
    plugin_root = tmp_path / "fake-plugin-root"
    _make_stub_compare(plugin_root)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hover-state-compare.sh"
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root), "VIEWPORTS": "375x812,1920x1080"}
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "test-session", str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 0, f"hover fan-out failed: {proc.stdout}\n{proc.stderr}"
    result = (ref / "transitions" / "hover-state-result.txt").read_text()
    assert "viewports: 375x812,1920x1080" in result
    assert "viewport: 375x812" in result
    assert "viewport: 1920x1080" in result
    assert "[375x812]" in result and "[1920x1080]" in result
    # Per-viewport subdirs must exist (target name is "btn" → safe name "btn")
    assert (ref / "transitions" / "hover-state" / "375x812" / "btn").is_dir()
    assert (ref / "transitions" / "hover-state" / "1920x1080" / "btn").is_dir()



def test_spec_implementation_coverage_counts_missing_entirely_entries(tmp_path: Path) -> None:
    """FIX 3 (rank235): a transition-spec entry whose selector/id matches NO
    impl file is a real coverage gap, not an exemption. Previously this case
    printed a ⚠️ row and `continue`d without incrementing UNCOVERED, so a
    transition that was never implemented at all was invisible to this gate's
    pass/fail. The gate must now exit 1 and count it under missingEntirely.
    """
    import subprocess
    comp = tmp_path / "comp"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    comp.mkdir()
    (comp / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "ghost-reveal", "trigger": "scroll", "type": "scroll-driven",
             "selector": ".totally-absent-section"}
        ]
    }))
    # Impl has an unrelated component — the entry's id/selector match nothing.
    (impl / "src" / "Footer.tsx").write_text(
        "export function Footer() { return <footer>contact</footer>; }\n"
    )
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "spec-implementation-coverage.sh"
    proc = subprocess.run(
        ["bash", str(script), str(comp), str(impl)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((comp / "spec-implementation-coverage.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["missingEntirely"] == 1
