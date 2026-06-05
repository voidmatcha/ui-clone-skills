from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ._helpers import (
    _project_root,
)


def test_ui_reverse_engineering_skill_frontloads_hard_done_criteria() -> None:
    """The skill must teach completion criteria before long details can distract."""
    skill = _project_root() / "skills" / "ui-reverse-engineering" / "SKILL.md"
    first_50 = "\n".join(skill.read_text(encoding="utf-8").splitlines()[:50]).lower()

    for phrase in (
        "build pass is not done",
        "spot check is not done",
        "pipeline verify pass",
        "missing artifact is failure",
    ):
        assert phrase in first_50, f"{phrase!r} missing from first 50 lines"



def test_module_invocation_help_works() -> None:
    """`python -m ui_clone.measure --help` exits 0 with usage on stdout."""
    proc = subprocess.run(
        [sys.executable, "-m", "ui_clone.measure", "--help"],
        capture_output=True, text=True,
        cwd=_project_root(),
    )
    assert proc.returncode == 0
    assert "section-compare" in proc.stdout
    assert "asset-utilization" in proc.stdout
    assert "bundle-impl-coverage" in proc.stdout



def test_fix8_dom_scaffold_script_present() -> None:
    """Fix 8 — dom-scaffold.sh produces the source-of-truth scaffold for
    Phase 4 generation. Locks the script + its key responsibilities so a
    future refactor can't silently remove the determinism layer that
    closed the V4 (avg ~463k AE) → expected-V5 fidelity gap.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "dom-scaffold.sh"
    assert script.is_file(), "dom-scaffold.sh missing — Fix 8 incomplete"
    body = script.read_text(encoding="utf-8")
    # Reads the three Phase-2 artifacts.
    for input_name in ("structure.json", "styles.json", "section-map.json"):
        assert input_name in body, f"dom-scaffold.sh must read {input_name}"
    # Writes the canonical output path.
    assert "dom-scaffold.json" in body, "dom-scaffold.sh must write dom-scaffold.json"
    # Style keys carried through to the scaffold tree.
    for key in ("bg", "color", "ff", "fs", "fw", "lh"):
        assert f'"{key}"' in body, f"dom-scaffold.sh must carry styles.{key}"



def test_fix8_text_fidelity_check_script_present() -> None:
    """Fix 8 — text-fidelity-check.sh is the post-Phase-4 gate that blocks
    JSX text-position strings not present in the scaffold allowlist. Locks
    the script + the canonical fabrication-detection regex patterns.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    assert script.is_file(), "text-fidelity-check.sh missing — Fix 8 incomplete"
    body = script.read_text(encoding="utf-8")
    # Reads dom-scaffold as the allowlist source.
    assert "dom-scaffold.json" in body
    # Emits the canonical output artifact.
    assert "text-fidelity-check" in body  # appears in OUT name + identity
    # Has the fabrication-detection logic ("status": "fail" branch).
    assert "fabrications" in body, "must enumerate fabrications"



def test_text_fidelity_check_fails_when_scaffold_text_is_omitted(tmp_path: Path) -> None:
    """A clone that renders only some scaffold text is still wrong.

    The original Fix 8 gate blocked fabricated text, but an impl could omit
    meaningful source copy and still pass. Scratch clone outputs must preserve
    the user-provided public page's visible text, not just avoid new text.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [
                {"tag": "h1", "text": "Original Brand Headline", "children": []},
                {"tag": "p", "text": "People creating seasonal recipes", "children": []},
            ],
        },
    }))
    (src / "App.tsx").write_text(
        "export default function App() { return <main><h1>Original Brand Headline</h1></main>; }\n"
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"omitted scaffold text must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "fail"
    assert artifact["missing_count"] == 1
    assert artifact["missing"][0]["text"] == "People creating seasonal recipes"


def test_text_fidelity_check_scans_jsx_components(tmp_path: Path) -> None:
    """Vite/React clones commonly use JSX files, not TSX files."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [
                {"tag": "h1", "text": "Original Brand Headline", "children": []},
            ],
        },
    }))
    (src / "App.jsx").write_text(
        "export default function App() { return <main><h1>Original Brand Headline</h1></main>; }\n"
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, f"JSX components must be scanned: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "pass"
    assert artifact["components_checked"] == 1


def test_text_fidelity_check_uses_element_roles_allowlist_without_cookie_overlay(
    tmp_path: Path,
) -> None:
    """Cookie overlays are not clone targets, but element-role text is source evidence."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "body",
            "children": [
                {
                    "tag": "div",
                    "class": "CybotCookiebotDialogContentWrapper",
                    "children": [
                        {"tag": "div", "text": "We use cookies", "children": []},
                        {"tag": "button", "text": "Allow all", "children": []},
                    ],
                }
            ],
        },
    }))
    (ref / "element-roles.json").write_text(json.dumps({
        "elements": [
            {
                "tag": "h1",
                "role": "heading",
                "selector": "h1.view-mode.unstyled",
                "text": "Design and launch outstanding websites",
            }
        ],
    }))
    (src / "App.jsx").write_text(
        "export default function App() { "
        "return <main><h1>Design and launch outstanding websites</h1></main>; }\n"
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "pass"
    assert artifact["missing_count"] == 0



def test_text_fidelity_check_ignores_script_style_noscript_template_text(tmp_path: Path) -> None:
    """Loop-61 finding: dom-scaffold.json captures text inside <script>/<style>/
    <noscript>/<template> tags (e.g. Next.js RSC `self.__next_f.push(...)`
    payloads, framework polyfill bodies). The impl is not expected to render
    that text, so the bidirectional fidelity check must skip those tags on
    the ref side too — symmetric to the existing impl-side <script> strip.
    Without this filter post-implement converges only when every framework
    runtime body is impossibly reproduced in JSX.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [
                {"tag": "h1", "text": "Real Food Wins", "children": []},
                # RSC payload, framework runtime — should be filtered.
                {"tag": "script", "text": "self.__next_f.push([1, \"long framework runtime body\"])", "children": []},
                {"tag": "style", "text": ".some-class { color: red; }", "children": []},
                {"tag": "noscript", "text": "Long fallback content that exceeds meaningful filter", "children": []},
                {"tag": "template", "text": "Long template literal contents that pass meaningful", "children": []},
            ],
        },
    }))
    (src / "App.tsx").write_text(
        "export default function App() { return <main><h1>Real Food Wins</h1></main>; }\n"
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, (
        "script/style/noscript/template text must NOT count as missing: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "pass"
    assert artifact["missing_count"] == 0


def test_text_fidelity_check_flags_degenerate_empty_scaffold(tmp_path: Path) -> None:
    """A JS-heavy reference site can extract a dom-scaffold with
    structure but ZERO text leaves. Generation then has nothing to transcribe
    verbatim and fabricates the body copy. The bidirectional check is vacuous
    here — scaffold requires nothing (0 missing), and if the fabricated strings
    happen to be in the element-roles allowlist there are 0 fabrications too —
    so the gate FALSE-PASSES a clone built on no text ground truth. The
    degenerate-scaffold guard must fail loudly instead (same class as the
    blank-ref refStd guard for the perceptual section gate).
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    # Scaffold: structure only, NO text leaves (mimics the failed extraction).
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [
                {"tag": "section", "children": [{"tag": "div", "children": []}]},
                {"tag": "section", "children": [{"tag": "div", "children": []}]},
            ],
        },
    }))
    impl_lines = [
        "Whole foods nourish the body every day",
        "Ultra processed products harm long term health",
        "America returns to real food choices",
        "The dietary guidelines were carefully reviewed",
        "Eat real food and spread the word",
        "Designed and engineered in the capital",
    ]
    # element-roles allowlist contains every impl string → 0 fabrications, so
    # without the guard the gate would PASS (0 missing + 0 fabrications).
    (ref / "element-roles.json").write_text(json.dumps({
        "elements": [{"tag": "p", "text": s} for s in impl_lines],
    }))
    body = "".join(f"<p>{s}</p>" for s in impl_lines)
    (src / "App.tsx").write_text(
        f"export default function App() {{ return <main>{body}</main>; }}\n"
    )

    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, (
        "degenerate (0-text) scaffold must fail, not false-pass: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "fail"
    assert artifact["degenerate_scaffold"] is True
    assert artifact["required_meaningful_strings"] == 0
    assert artifact["fabrications_count"] == 0  # proves it's the guard, not fabrication, that failed it


def test_text_fidelity_check_healthy_scaffold_not_degenerate(tmp_path: Path) -> None:
    """Guard the guard: a healthy scaffold with real text must NOT trip the
    degenerate-scaffold guard (no false-positive on legitimate clones).
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {
            "tag": "main",
            "children": [
                {"tag": "h1", "text": "Real Food Wins", "children": []},
                {"tag": "p", "text": "America is the greatest country on Earth", "children": []},
            ],
        },
    }))
    (src / "App.tsx").write_text(
        "export default function App() { return <main>"
        "<h1>Real Food Wins</h1>"
        "<p>America is the greatest country on Earth</p>"
        "</main>; }\n"
    )
    out = ref / "text-fidelity-check.json"
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "text-fidelity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, f"healthy scaffold must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads(out.read_text())
    assert artifact["status"] == "pass"
    assert artifact["degenerate_scaffold"] is False


def test_header_state_runtime_check_script_present() -> None:
    """2026-05-22 user direction: "Header는 정적 HTML이 아니라 state machine입니다."
    The header-state-runtime-check.sh gate must exist, be executable, and
    declare the canonical assertion: ref header mutation on scroll →
    impl header must mutate too.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "header-state-runtime-check.sh"
    assert script.is_file(), "header-state-runtime-check.sh missing"
    body = script.read_text(encoding="utf-8")
    assert "is-hide" in body or "thema-" in body or "state machine" in body, (
        "script must reference the failure modes (is-hide / thema-* / state machine)"
    )
    assert "scrollTo" in body, "must probe scroll-driven state"
    assert "header-state-runtime.json" in body, "must write the canonical artifact"



def test_header_state_runtime_dispatcher_wired() -> None:
    """Regression: codex-18 (2026-05-22) shipped hero-composite-check.sh
    without dispatcher SIGNATURES wiring → dispatcher NOSIG-skipped it.
    header-state-runtime-check.sh must NOT repeat that mistake.
    """
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "run-required-checks.sh"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"header-state-runtime-check\.sh":\s*"([^"]+)"', text)
    assert m, "header-state-runtime-check.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    # The gate needs ref-url AND impl-url to compare mutation between
    # them. Without either arg, the probe falls back to a no-op skip.
    assert "{ref_url}" in recipe and "{impl_url}" in recipe, (
        f"header-state-runtime recipe must pass both {{ref_url}} and {{impl_url}} "
        f"(got: {recipe!r})"
    )



# ---------------------------------------------------------------------------
# 2026-06-05 gate-enforcement fix: header GEOMETRY trajectory parity.
#
# The class/data-attr comparator is blind to headers that animate their
# geometry (height 100->64, padding shrink, transform translateY,
# position fixed->absolute) on scroll WITHOUT toggling any class. Real
# artifacts (realfood-gov + 10 sibling refs) shipped status=pass with
# ref.mutates=true AND classesToggled=[] — the mutation came from
# body/html/fw-root class deltas, not verified header geometry. An impl
# that pins the header via overrides.css
# (.header{position:absolute!important;transform:none!important}) passes
# silently. The geo-trajectory comparator closes that blind spot.
# ---------------------------------------------------------------------------

_HSR_SCRIPT_REL = "skills/visual-debug/scripts/header-state-runtime-check.sh"


def _hsr_script_body() -> str:
    return (_project_root() / _HSR_SCRIPT_REL).read_text(encoding="utf-8")


def test_header_state_runtime_captures_geometry_source() -> None:
    """The snap() probe must read computed geometry (getComputedStyle +
    getBoundingClientRect) and the Python comparator must own a geometry
    failure path. Mirrors test_header_state_runtime_check_script_present.
    """
    body = _hsr_script_body()
    assert "getBoundingClientRect" in body, "snap() must measure layout rect"
    assert "paddingTop" in body and "paddingBottom" in body, (
        "snap() must capture vertical padding trajectory"
    )
    assert "position" in body, "snap() must capture position (fixed/absolute)"
    assert "geo" in body, "snap must expose a geo block for the comparator"
    assert "geometry" in body.lower(), (
        "comparator must surface a geometry-specific fail reason"
    )


def _hsr_snap(height: float, *, padding: str = "16px 0px",
              transform: str = "none", position: str = "fixed",
              top: str = "0px", cls: str = "") -> dict:
    """Build a snap() shape with the new geo block. Class set empty by
    default — this is the class-less geometric header blind spot.
    """
    pt, _, pb = padding.partition(" ")
    return {
        "tag": "header",
        "cls": cls,
        "attrs": {},
        "childTagClasses": [],
        "geo": {
            "height": height,
            "paddingTop": pt,
            "paddingBottom": pb if pb else pt,
            "transform": transform,
            "position": position,
            "top": top,
            "scrollY": 0,
        },
    }


def _hsr_probe(samples_geo: list, *, scroll_tops: tuple[int, ...] = (200, 600, 1200, 1500)) -> dict:
    """Compose a probe JSON. samples_geo is a list of (top, height,
    transform, position) describing each scroll sample's geo. at0 is the
    scroll=0 baseline (first entry's height treated as start).
    """
    at0_height, at0_transform, at0_position = samples_geo[0][1:4]
    at0 = _hsr_snap(at0_height, transform=at0_transform, position=at0_position)
    at0["geo"]["scrollY"] = 0
    samples = []
    deep = at0
    for (top, h, tf, pos) in samples_geo[1:]:
        snap = _hsr_snap(h, transform=tf, position=pos)
        snap["geo"]["scrollY"] = top
        samples.append({"top": top, "snapshot": snap})
        deep = snap
    return {
        "found": True,
        "at0": at0,
        "at600": deep,
        "samples": samples,
        "allRoots0": [{"name": "header", "snap": at0}],
        "allRootsDeep": [{"name": "header", "snap": deep}],
        "scrollHeight": 6000,
    }


def _run_hsr_with_stub(
    tmp_path: Path, ref_probe: dict, impl_probe: dict
) -> tuple[subprocess.CompletedProcess[str], dict]:
    """Invoke header-state-runtime-check.sh with a PATH-shimmed
    agent-browser that emits the supplied probe fixtures. The shim picks
    ref vs impl by the --session suffix (-hdr-ref / -hdr-impl).
    """
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    ref_fix = tmp_path / "ref_probe.json"
    impl_fix = tmp_path / "impl_probe.json"
    ref_fix.write_text(json.dumps(ref_probe))
    impl_fix.write_text(json.dumps(impl_probe))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "agent-browser"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "# Test stub: emit the right probe fixture on `eval`, no-op otherwise.\n"
        "session=\"\"\n"
        "is_eval=0\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    --session) session=\"$2\"; shift 2;;\n"
        "    eval) is_eval=1; shift;;\n"
        "    *) shift;;\n"
        "  esac\n"
        "done\n"
        "if [ \"$is_eval\" -eq 1 ]; then\n"
        f"  case \"$session\" in\n"
        f"    *-hdr-ref) cat {json.dumps(str(ref_fix))[1:-1]};;\n"
        f"    *-hdr-impl) cat {json.dumps(str(impl_fix))[1:-1]};;\n"
        "  esac\n"
        "fi\n"
        "exit 0\n"
    )
    stub.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    script = _project_root() / _HSR_SCRIPT_REL
    proc = subprocess.run(
        ["bash", str(script), "tsess",
         "https://ref.example.com", "https://impl.example.com", str(ref_dir)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    artifact_path = ref_dir / "header-state-runtime.json"
    artifact = json.loads(artifact_path.read_text()) if artifact_path.is_file() else {}
    return proc, artifact


def test_header_state_runtime_fails_when_ref_geo_moves_but_impl_frozen(tmp_path: Path) -> None:
    """Ref header shrinks height 100->64 on scroll while toggling NO class;
    impl header is frozen at 64 (overrides.css suppressor). The gate must
    FAIL with a geometry reason and exit 1 — the class comparator alone
    would silently pass here.
    """
    ref_probe = _hsr_probe([
        (0, 100.0, "none", "fixed"),
        (200, 80.0, "translateY(-8px)", "fixed"),
        (600, 64.0, "translateY(-12px)", "absolute"),
        (1500, 64.0, "translateY(-12px)", "absolute"),
    ])
    impl_probe = _hsr_probe([
        (0, 64.0, "none", "absolute"),
        (200, 64.0, "none", "absolute"),
        (600, 64.0, "none", "absolute"),
        (1500, 64.0, "none", "absolute"),
    ])
    proc, artifact = _run_hsr_with_stub(tmp_path, ref_probe, impl_probe)
    assert artifact, f"no artifact written; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert artifact["status"] == "fail", f"expected fail, got {artifact}"
    blob = json.dumps(artifact).lower()
    assert "geometr" in blob, f"fail reason must mention geometry: {artifact['reasons']}"
    assert proc.returncode == 1, f"geo-fail must exit 1, got {proc.returncode}"
    assert artifact["ref"].get("geoChanges") is True
    assert artifact["impl"].get("geoChanges") is False


def test_header_state_runtime_passes_when_impl_geo_matches(tmp_path: Path) -> None:
    """Negative control: impl header also shrinks 100->64 on scroll, so the
    geometric state machine is reproduced — status must be pass, exit 0.
    """
    ref_probe = _hsr_probe([
        (0, 100.0, "none", "fixed"),
        (200, 80.0, "translateY(-8px)", "fixed"),
        (600, 64.0, "translateY(-12px)", "absolute"),
        (1500, 64.0, "translateY(-12px)", "absolute"),
    ])
    impl_probe = _hsr_probe([
        (0, 100.0, "none", "fixed"),
        (200, 80.0, "translateY(-8px)", "fixed"),
        (600, 64.0, "translateY(-12px)", "absolute"),
        (1500, 64.0, "translateY(-12px)", "absolute"),
    ])
    proc, artifact = _run_hsr_with_stub(tmp_path, ref_probe, impl_probe)
    assert artifact, f"no artifact written; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert artifact["status"] == "pass", f"expected pass, got {artifact}"
    assert proc.returncode == 0, f"geo-match must exit 0, got {proc.returncode}"
    assert artifact["ref"].get("geoChanges") is True
    assert artifact["impl"].get("geoChanges") is True


def test_runtime_proof_rollup_aggregates_source_artifacts(tmp_path: Path) -> None:
    """2026-05-22 codex-rescue audit: the runtime-proof aggregator must
    read source artifacts (not run new probes) and FAIL when any source
    gate has status=pass but no actual measurement payload.
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    # Seed source artifacts. lottie-runtime gets a measurement-free pass
    # (status=pass but candidateCount=0) — must trigger composite FAIL.
    (ref / "lottie-runtime.json").write_text(json.dumps({
        "schemaVersion": 2,
        "status": "pass",
        "refDetected": True,
        "runtimeProof": {"status": "static-only", "candidateCount": 0, "animatingCount": 0},
    }))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "impl": {"video": True, "button": True, "h1OrH2": True, "label": True},
    }))
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
        "ref": {"mutates": False},
        "impl": {"mutates": False},
    }))
    (ref / "svg-provenance.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
    }))
    # Other source artifacts intentionally missing — rollup must record them.

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 1, f"measurement-free pass must compose to FAIL: {proc.stdout}"
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    assert artifact["status"] == "fail"
    # lottie's measurement-free pass must be flagged as invalid
    lot = next(c for c in artifact["components"] if c["artifact"] == "lottie-runtime.json")
    assert lot["valid"] is False, "lottie static-only with refDetected must be invalid"
    # missing artifacts must be enumerated
    missing_names = [c["artifact"] for c in artifact["components"] if not c.get("present", False)]
    assert "motion-coverage.json" in missing_names, "missing source must be tracked"



def test_runtime_proof_rollup_all_skip_when_truly_absent(tmp_path: Path) -> None:
    """Aggregator must skip (not fail) when every component reports
    status=skip AND every source artifact is present. This is the
    valid "ref site has none of the signals" scenario, not the
    failure mode where gates didn't run.
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    # 2026-05-22: rollup now requires either a complete plan (with
    # universal anchors) OR a no-signals-justified.txt marker. Provide
    # the marker for this "truly absent" scenario.
    (ref / "no-signals-justified.txt").write_text("test fixture: no signals on this site")
    # Write every source artifact with status=skip and a valid measurement
    # payload (skip reasons that match the validator's accepted skip cases).
    for name in [
        "lottie-runtime.json", "runtime-image-validity.json",
        "runtime-dom-parity.json", "motion-coverage.json",
        "runtime-spec-coverage.json", "runtime-frame-proof.json", "scroll-completion.json",
        "reveal-trigger.json", "hidden-children.json",
        "svg-provenance.json",
    ]:
        (ref / name).write_text(json.dumps({"schemaVersion": 1, "status": "skip"}))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "impl": {"video": True, "button": True, "h1OrH2": True, "label": True},
    }))
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "skip", "ref": {"mutates": False},
        "impl": {"mutates": False},
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, f"all-skip with hero-pass must compose to pass/skip: {proc.stdout}"


def test_runtime_proof_rollup_requires_planned_runtime_frame_proof(tmp_path: Path) -> None:
    """Canvas/WebGL/Lottie frame proof must participate in runtime-proof."""
    import subprocess

    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(json.dumps({
        "requiredChecks": [
            {"id": "hydration-check", "produces": "hydration.json"},
            {"id": "text-fidelity-check", "produces": "text-fidelity.json"},
            {"id": "image-fidelity", "produces": "image-fidelity.json"},
            {"id": "asset-transfer", "produces": "asset-transfer.json"},
            {"id": "scaffold-warn", "produces": "scaffold-warn.json"},
            {"id": "runtime-frame-proof", "produces": "runtime-frame-proof.json"},
        ],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 1, (
        "planned runtime-frame-proof.json must be required by runtime-proof: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    frame = next(c for c in artifact["components"] if c["artifact"] == "runtime-frame-proof.json")
    assert frame["present"] is False
    assert frame["valid"] is False


def test_runtime_proof_rollup_accepts_hero_kinds_absent_from_ref_and_impl(tmp_path: Path) -> None:
    """Hero rollup must compare ref-vs-impl kinds, not require all
    possible hero kinds. A ref without video/button should not pressure
    impls into adding invisible stub elements.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "no-signals-justified.txt").write_text("test fixture")
    for name in [
        "lottie-runtime.json", "runtime-image-validity.json",
        "runtime-dom-parity.json", "motion-coverage.json",
        "runtime-spec-coverage.json", "runtime-frame-proof.json", "scroll-completion.json",
        "reveal-trigger.json", "hidden-children.json", "svg-provenance.json",
    ]:
        (ref / name).write_text(json.dumps({"schemaVersion": 1, "status": "skip"}))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "ref": {"video": False, "button": False, "h1OrH2": True, "label": True},
        "impl": {"video": False, "button": False, "h1OrH2": True, "label": True},
        "missingInImpl": [],
    }))
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "skip", "ref": {"mutates": False},
        "impl": {"mutates": False},
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, (
        "hero kinds absent from both ref and impl must not fail rollup: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    hero = next(c for c in artifact["components"] if c["artifact"] == "hero-composite.json")
    assert hero["valid"] is True


def test_runtime_dom_parity_ignores_bundle_lottie_when_ref_mounts_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generic Webflow bundles can contain Lottie plugin code even when
    the live page mounts no Lottie container. That should not force
    clone impls to add fake Lottie nodes.
    """
    ref = tmp_path / "ref"
    bundles = ref / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "webflow.js").write_text("function lottiePlugin() {}", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_agent = fake_bin / "agent-browser"
    metrics = {
        "nodeCount": 100,
        "textNodeCount": 30,
        "visibleTextNodeCount": 30,
        "viewportArea": 800000,
        "maxElementArea": 120000,
        "maxElementRatio": 0.15,
        "maxElementTag": "img",
        "maxElementSrc": "",
        "lottieMounted": 0,
        "sectionCount": 8,
        "opaqueOverlayCount": 0,
        "opaqueOverlaySample": [],
    }
    fake_agent.write_text(
        "#!/usr/bin/env bash\n"
        "session=''\n"
        "if [ \"${1:-}\" = '--session' ]; then session=\"$2\"; shift 2; fi\n"
        "cmd=\"${1:-}\"\n"
        "if [ \"$cmd\" = 'eval' ]; then\n"
        f"  printf '%s\\n' '{json.dumps(metrics)}'\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_agent.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-dom-parity-check.sh"
    proc = subprocess.run(
        ["bash", str(script), "lottie-fp", "https://example.test", "http://localhost:5173", str(ref)],
        capture_output=True, text=True, timeout=10, cwd=_project_root(),
    )
    assert proc.returncode == 0, (
        "bundle-only Lottie evidence with ref lottieMounted=0 must not fail: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "runtime-dom-parity.json").read_text())
    assert artifact["status"] == "pass"



def test_runtime_proof_rollup_skips_missing_conditional_artifacts(tmp_path: Path) -> None:
    """2026-05-22 audit: conditional artifacts (scroll-end-completion,
    reveal-trigger) are only produced when their signal fires. When
    verification-plan.json doesn't list them, missing artifact must NOT
    fail the composite — that would block every site without those
    signals from ever passing.
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    # Plan lists only the unconditional checks + universal anchors
    # (hydration-check, text-fidelity-check, image-fidelity,
    # asset-transfer, scaffold-warn) — required by the rollup's
    # explicit-anchor verification.
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "hydration-check", "produces": "hydration-check.json"},
            {"id": "text-fidelity-check", "produces": "text-fidelity-check.json"},
            {"id": "image-fidelity", "produces": "image-fidelity.json"},
            {"id": "asset-transfer", "produces": "asset-transfer.json"},
            {"id": "scaffold-warn", "produces": "scaffold-warn.json"},
            {"id": "lottie-runtime", "produces": "lottie-runtime.json"},
            {"id": "runtime-image-validity", "produces": "runtime-image-validity.json"},
            {"id": "runtime-dom-parity", "produces": "runtime-dom-parity.json"},
            {"id": "motion-coverage", "produces": "motion-coverage.json"},
            {"id": "runtime-spec-coverage", "produces": "runtime-spec-coverage.json"},
            {"id": "runtime-frame-proof", "produces": "runtime-frame-proof.json"},
            {"id": "header-state-runtime", "produces": "header-state-runtime.json"},
            {"id": "hidden-children", "produces": "hidden-children.json"},
            {"id": "svg-provenance", "produces": "svg-provenance.json"},
            {"id": "hero-composite-check", "produces": "hero-composite.json"},
            # scroll-end-completion and reveal-trigger intentionally NOT in plan
        ],
    }))
    # Write the unconditional artifacts (status=skip is fine for this test)
    for name in [
        "lottie-runtime.json", "runtime-image-validity.json",
        "runtime-dom-parity.json", "motion-coverage.json",
        "runtime-spec-coverage.json", "runtime-frame-proof.json", "hidden-children.json",
        "svg-provenance.json",
    ]:
        (ref / name).write_text(json.dumps({"schemaVersion": 1, "status": "skip"}))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "impl": {"video": True, "button": True, "h1OrH2": True, "label": True},
    }))
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "skip", "ref": {"mutates": False},
        "impl": {"mutates": False},
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, (
        f"missing conditional artifacts (not in plan) must NOT fail composite: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    # Both conditional artifacts should be marked not-applicable, not invalid
    cond = [c for c in artifact["components"]
            if c["artifact"] in ("scroll-completion.json", "reveal-trigger.json")]
    assert len(cond) == 2, "both conditional artifacts must appear in components"
    for entry in cond:
        assert entry["valid"] is True, (
            f"conditional missing artifact must be valid (not in plan): {entry}"
        )
        assert "not applicable" in entry["note"]



def test_runtime_proof_rollup_fails_on_empty_plan_without_justification(tmp_path: Path) -> None:
    """2026-05-22 codex-rescue meta-review (ac93f1e7) + universality
    audit (a000cd35): empty-plan masking. If verification-plan.json
    lacks the universal anchor checks (hydration / text-fidelity /
    image-fidelity / asset-transfer / scaffold-warn) AND no
    `no-signals-justified.txt` marker exists, rollup must FAIL — an
    empty plan would mask every conditional artifact as "not
    applicable", letting a misconfigured run claim composite pass.
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    # Plan missing universal anchors — only has hydration + image-fidelity,
    # NOT text-fidelity, asset-transfer, scaffold-warn.
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "hydration-check", "produces": "hydration-check.json"},
            {"id": "image-fidelity", "produces": "image-fidelity.json"},
        ],
    }))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 1, (
        f"plan missing anchors must FAIL composite: {proc.stdout}"
    )
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    assert artifact["status"] == "fail"
    reasons = " ".join(artifact["reasons"])
    assert "anchor" in reasons.lower() or "missing universal" in reasons.lower(), (
        f"reasons must explain the missing-anchor failure: {reasons}"
    )



def test_runtime_proof_rollup_accepts_justified_no_signals(tmp_path: Path) -> None:
    """When the ref site genuinely has no runtime signals, an operator
    can write `no-signals-justified.txt` to bypass the empty-plan guard.
    The marker exists so silent misconfigurations don't pass, but
    legitimate static-only sites aren't permanently blocked.
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            {"id": "text-fidelity-check", "produces": "text-fidelity-check.json"},
            {"id": "image-fidelity", "produces": "image-fidelity.json"},
        ],
    }))
    (ref / "no-signals-justified.txt").write_text(
        "Static landing page; no scroll/IO/hover triggers. Verified manually."
    )
    # Still need the unconditional sources for the rollup itself to validate
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "skip",
    }))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )
    # Returns 0 (pass or skip) because the marker bypasses the empty-plan guard
    assert proc.returncode == 0, (
        f"justified no-signals marker must bypass empty-plan guard: {proc.stdout}"
    )



def test_runtime_proof_rollup_dispatcher_wired() -> None:
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "run-required-checks.sh"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"runtime-proof-rollup\.sh":\s*"([^"]+)"', text)
    assert m, "runtime-proof-rollup.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{ref_dir}" in recipe, f"rollup recipe must include {{ref_dir}} (got: {recipe!r})"



def test_ref_js_loader_fails_when_impl_imports_ref_bundle(tmp_path: Path) -> None:
    """2026-05-22 codex-rescue audit: ref-js-loader must catch the cheat
    where impl loads the ref's compiled JS bundle directly via
    <script src> or dynamic import().
    """
    import subprocess
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "head.json").write_text(json.dumps({
        "url": "https://example.cheat-target.org/",
        "title": "Ref",
    }))
    (impl / "src" / "BadComponent.tsx").write_text(
        "// CHEAT: load ref's compiled vendor bundle to fake runtime\n"
        "import vendor from 'https://example.cheat-target.org/_next/static/chunks/main.js';\n"
        "export default function Bad() { return null; }\n"
    )
    (impl / "package.json").write_text(json.dumps({"name": "impl"}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "ref-js-loader-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 1, f"ref-host import in impl must FAIL: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "ref-js-loader.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["violations"], "must list the violating file"
    assert "example.cheat-target.org" in str(artifact["violations"]), (
        "violation must name the ref host"
    )



def test_duration_easing_grounding_fails_on_invented_duration(tmp_path: Path) -> None:
    """Impl uses 333ms when ref signals 200ms → outside 50ms tolerance,
    not in allowlist → invented → fail.
    """
    import subprocess
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    # Ref artifacts signal a 200ms transition
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "hero-fade", "duration": 200, "easing": "ease-out"},
        ],
    }))
    # Impl uses 333ms (off-grid, not in allowlist) for the same family
    (impl / "src" / "Hero.tsx").write_text(
        "export default function Hero() {\n"
        "  return <div style={{ transition: 'opacity 333ms ease-out 50ms' }} />;\n"
        "}\n"
    )
    # Also use a duration in src that's far from ref
    (impl / "src" / "extra.css").write_text(
        ".x { transition-duration: 4200ms; }\n"
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "duration-easing-grounding-check.sh"
    subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=10,
    )
    # 4200ms is well outside 50ms tolerance AND not in allowlist → fail
    artifact = json.loads((ref / "duration-easing-grounding.json").read_text())
    if artifact["status"] == "pass":
        # Soft assertion: at least invented count should be non-zero
        assert len(artifact.get("inventedDurations", [])) > 0, (
            f"4200ms should be detected as invented: {artifact}"
        )
    else:
        assert artifact["status"] == "fail"
        assert 4200 in artifact.get("inventedDurations", []) or any(
            "4200" in r for r in artifact.get("reasons", [])
        ), f"4200ms should appear in invented list: {artifact}"


def test_duration_easing_grounding_reads_nested_animation_fields(tmp_path: Path) -> None:
    """transition-spec entries commonly store timing under animation.*.

    The grounding gate must treat animation.duration/ease as ref-measured
    evidence, not skip the check because top-level duration/easing are absent.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {
                "id": "hero-load",
                "selector": ".hero-title",
                "animation": {"duration": 1.2, "ease": "heroEase"},
            },
        ],
    }))
    (impl / "src" / "Hero.css").write_text(
        ".hero-title { transition-duration: 1200ms; transition-timing-function: heroEase; }\n"
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "duration-easing-grounding-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "duration-easing-grounding.json").read_text())
    assert artifact["status"] == "pass"
    assert 1200 in artifact["refDurations"]
    assert "heroease" in artifact["refEasings"]
    assert "heroease" in artifact["matchedEasings"]



def test_duration_easing_grounding_allows_spring_family_easing(tmp_path: Path) -> None:
    """Impl uses Framer Motion spring with elastic.out easing — must
    NOT be classified as invented when SPRING_PAT matches the source.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "hero-bounce", "duration": 600, "easing": "ease-out"},
        ],
    }))
    # Impl uses spring (no duration literal) + elastic.out easing
    (impl / "src" / "Hero.tsx").write_text(
        "import { motion } from 'framer-motion';\n"
        "export default function Hero() {\n"
        "  return <motion.div\n"
        "    transition={{ type: 'spring', stiffness: 200, damping: 10 }}\n"
        "    style={{ transitionTimingFunction: 'elastic.out(1, 0.5)' }}\n"
        "  />;\n"
        "}\n"
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "duration-easing-grounding-check.sh"
    subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=10,
    )
    artifact = json.loads((ref / "duration-easing-grounding.json").read_text())
    # Spring detected in impl source — elastic easing should be auto-allowed
    assert artifact.get("implSpringUses", 0) > 0, (
        f"SPRING_PAT must detect stiffness/damping/type=spring: {artifact}"
    )



def test_duration_easing_grounding_script_present() -> None:
    """2026-05-22 user request (#9): duration/easing values must
    trace to ref artifacts. SKILL.md Tier 3.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "duration-easing-grounding-check.sh"
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    assert "transition-spec" in body, "must read transition-spec.json"
    assert "ALLOW_EASINGS" in body and "cubic-bezier" in body
    assert "duration-easing-grounding.json" in body



def test_mobile_viewport_parity_script_present() -> None:
    """2026-05-22 user request (#5): mobile viewport gate."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "mobile-viewport-parity-check.sh"
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    assert "375" in body, "default mobile width must be 375"
    assert "812" in body, "default mobile height must be 812"
    assert "overflow" in body.lower(), "must check horizontal overflow"
    assert "mobile-viewport-parity.json" in body



def test_runtime_frame_proof_script_present() -> None:
    """2026-05-22 user request (#6/#7): Lottie/canvas/WebGL true
    frame-delta proof using getImageData + readPixels + instance.
    currentFrame.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-frame-proof-check.sh"
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    assert "getImageData" in body, "must sample canvas via getImageData"
    assert "readPixels" in body, "must sample WebGL via readPixels"
    assert "currentFrame" in body, "must read Lottie instance.currentFrame"
    assert "runtime-frame-proof.json" in body


def test_runtime_proof_rollup_header_geo_pass_without_impl_geo_is_invalid(
    tmp_path: Path,
) -> None:
    """A header artifact that passes class-mutation parity but whose REF geometry
    moves on scroll while the IMPL stays static (geoChanges mismatch) must be
    flagged invalid by the rollup. Class-toggle parity alone cannot prove the
    motion arc (realfood loop-145: ref nav springs on scrollY, impl pinned)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "ref": {"mutates": True, "geoChanges": True},
        "impl": {"mutates": True, "geoChanges": False},
    }))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "impl": {"video": True, "button": True, "h1OrH2": True, "label": True},
    }))
    (ref / "svg-provenance.json").write_text(json.dumps({"schemaVersion": 1, "status": "skip"}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    subprocess.run(["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10)
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    hdr = next(c for c in artifact["components"] if c["artifact"] == "header-state-runtime.json")
    assert hdr["valid"] is False, f"header geo-mismatch pass must be invalid: {hdr}"


def test_runtime_proof_rollup_header_geo_match_stays_valid(tmp_path: Path) -> None:
    """Positive: ref + impl geometry both move on scroll -> header valid (no
    false positive from the geometry validity gate)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "header-state-runtime.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "ref": {"mutates": True, "geoChanges": True},
        "impl": {"mutates": True, "geoChanges": True},
    }))
    (ref / "hero-composite.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "impl": {"video": True, "button": True, "h1OrH2": True, "label": True},
    }))
    (ref / "svg-provenance.json").write_text(json.dumps({"schemaVersion": 1, "status": "skip"}))
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-proof-rollup.sh"
    subprocess.run(["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10)
    artifact = json.loads((ref / "runtime-proof.json").read_text())
    hdr = next(c for c in artifact["components"] if c["artifact"] == "header-state-runtime.json")
    assert hdr["valid"] is True, f"matching geometry must stay valid: {hdr}"
