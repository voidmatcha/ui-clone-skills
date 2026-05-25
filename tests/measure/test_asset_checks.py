from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ._helpers import (
    _baseline_post_implement_inputs,
    _make_verification_plan,
    _project_root,
    _run_script,
)


def test_lottie_runtime_check_script_fails_without_runtime_or_json(tmp_path: Path) -> None:
    """Ref Lottie evidence requires a real impl Lottie runtime and JSON data."""
    work = tmp_path / "work"
    ref = work / "ref"
    impl = work / "impl"
    src = impl / "src"
    public = impl / "public"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "resources": ["https://cdn.example.com/bodymovin.min.js"],
        "notes": "page registers a lottie-web animation",
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "react-dom": "19"},
    }))
    (public / "app-config.json").write_text(json.dumps({"theme": "dark"}))
    (src / "App.tsx").write_text("export default function App() { return <main />; }\n")

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"missing Lottie runtime/json must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["refDetected"] is True
    assert "runtime package" in " ".join(artifact["reasons"])
    assert "animation JSON" in " ".join(artifact["reasons"])



def test_lottie_runtime_check_passes_with_runtime_usage_and_json(tmp_path: Path) -> None:
    """A faithful impl has dependency, source usage, and local animation data."""
    work = tmp_path / "work"
    ref = work / "ref"
    impl = work / "impl"
    src = impl / "src"
    public = impl / "public" / "animations"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-animation", "engine": "Lottie", "target": ".hero"}],
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "lottie-web": "5.12.2"},
    }))
    (src / "Hero.tsx").write_text(
        "import lottie from 'lottie-web';\n"
        "export function Hero() { lottie.loadAnimation({ path: '/animations/hero.json' }); return <div />; }\n"
    )
    (public / "hero.json").write_text(json.dumps({"v": "5.12.2", "layers": []}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, f"complete Lottie impl must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["runtimeUsageFound"] is True
    assert artifact["jsonFound"] is True



def test_lottie_runtime_check_fails_on_import_only_usage(tmp_path: Path) -> None:
    """Importing lottie-web is not proof that a visible animation is mounted."""
    work = tmp_path / "work"
    ref = work / "ref"
    impl = work / "impl"
    src = impl / "src"
    public = impl / "public" / "animations"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-animation", "engine": "Lottie", "target": ".hero"}],
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "lottie-web": "5.12.2"},
    }))
    (src / "Hero.tsx").write_text(
        "import lottie from 'lottie-web';\n"
        "export function Hero() { return <div className=\"hero\" />; }\n"
    )
    (public / "hero.json").write_text(json.dumps({"v": "5.12.2", "layers": []}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"import-only Lottie must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["runtimeUsageFound"] is False
    assert "runtime usage" in " ".join(artifact["reasons"])



def test_lottie_runtime_artifact_schema_v2_with_runtime_proof_block() -> None:
    """2026-05-22 user direction: even with package + import + JSON present,
    'data-lottie만 붙이는 것은 실패입니다'. The artifact must carry a
    runtimeProof block so downstream tooling can see whether the browser
    actually painted svg/canvas, not just whether static evidence existed.

    Legacy schema (v1) had no runtimeProof. v2 must include it with three
    fields. When called without an impl URL (backward compat), runtimeProof
    falls back to status=static-only so existing callers don't regress.
    """
    work_root = Path(_project_root()) / "tmp" / "_test_lottie_v2"
    if work_root.exists():
        import shutil
        shutil.rmtree(work_root)
    ref = work_root / "ref"
    impl = work_root / "impl"
    src = impl / "src"
    public = impl / "public" / "animations"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-animation", "engine": "Lottie", "target": ".hero"}],
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "lottie-web": "5.12.2"},
    }))
    (src / "Hero.tsx").write_text(
        "import lottie from 'lottie-web';\n"
        "import data from '../public/animations/hero.json';\n"
        "export function Hero() {\n"
        "  React.useEffect(() => { lottie.loadAnimation({\n"
        "    container: document.querySelector('.hero'),\n"
        "    renderer: 'svg', loop: true, autoplay: true, animationData: data,\n"
        "  }); }, []);\n"
        "  return <div className=\"hero\" />;\n"
        "}\n"
    )
    (public / "hero.json").write_text(json.dumps({"v": "5.12.2", "fr": 30, "ip": 0, "op": 60, "layers": []}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, f"static-complete impl must pass when no impl-url provided: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["schemaVersion"] == 2, "schema v2 required (runtimeProof block added)"
    assert "runtimeProof" in artifact, "v2 artifact must include runtimeProof block"
    proof = artifact["runtimeProof"]
    assert proof["status"] == "static-only", (
        "Without impl-url, runtimeProof.status must be 'static-only' "
        f"(got {proof['status']!r}). Backward-compat for legacy callers."
    )
    assert proof["candidateCount"] == 0
    assert proof["animatingCount"] == 0



def test_lottie_runtime_check_documents_runtime_proof_in_rule() -> None:
    """The artifact's `rule` field is the user-facing explanation of what
    the gate enforces. v2 must mention the runtime proof so the failure
    mode 'package imported but loadAnimation never wired' is named.
    """
    script_path = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    body = script_path.read_text(encoding="utf-8")
    # The runtime-proof rule is baked into write_json so every artifact
    # carries it. Check the source so the rule travels with the script.
    assert "paint" in body and "svg/canvas" in body, (
        "rule must describe the runtime-paint assertion"
    )
    assert "loadAnimation" in body or "1.5s" in body, (
        "rule should call out the browser-side proof timing"
    )



def test_lottie_runtime_dispatcher_passes_impl_url() -> None:
    """2026-05-22 codex-rescue audit found the runtime proof in
    lottie-runtime-check.sh only fires when impl_url is passed as the 3rd
    positional arg. The dispatcher SIGNATURES entry must include {impl_url}
    or the runtime proof is never exercised in the auto-run pipeline.
    """
    dispatcher = _project_root() / "scripts" / "verify" / "run-required-checks.sh"
    text = dispatcher.read_text(encoding="utf-8")
    # Find the lottie-runtime-check.sh signature line and assert {impl_url}.
    import re
    m = re.search(r'"lottie-runtime-check\.sh":\s*"([^"]+)"', text)
    assert m, "lottie-runtime-check.sh signature missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{impl_url}" in recipe, (
        f"lottie-runtime-check.sh recipe must pass {{impl_url}} so the runtime "
        f"proof fires (got: {recipe!r}). Without it the gate falls back to "
        "static-only and 'data-lottie만 붙이는 것은 실패입니다' regresses."
    )



def test_svg_provenance_check_script_present() -> None:
    """2026-05-22 codex-rescue audit (a125b997): svg-provenance gate must
    exist, be executable, and document the IconMark.tsx-style failure
    mode that motivated it.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "svg-provenance-check.sh"
    assert script.is_file(), "svg-provenance-check.sh missing"
    body = script.read_text(encoding="utf-8")
    assert "IconMark" in body or "invent" in body, (
        "script must reference the IconMark.tsx / SVG-invention failure mode"
    )
    assert "svg-provenance.json" in body, "must write canonical artifact"
    assert "<path d" in body or "path d=" in body or 'querySelectorAll("path")' in body, (
        "must probe SVG path geometry"
    )



def test_svg_provenance_dispatcher_wired() -> None:
    """Regression: ensure svg-provenance-check.sh is in dispatcher
    SIGNATURES with both {ref_url} and {impl_url} (probe requires both).
    """
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "run-required-checks.sh"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"svg-provenance-check\.sh":\s*"([^"]+)"', text)
    assert m, "svg-provenance-check.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{ref_url}" in recipe and "{impl_url}" in recipe, (
        f"svg-provenance recipe must include both ref/impl URLs (got: {recipe!r})"
    )



def test_verification_plan_includes_svg_provenance_block_row() -> None:
    """The plan emitter must register svg-provenance as a block row —
    silently invented SVGs would otherwise pass svg-dom-parity-count and
    waste section-compare iterations.
    """
    plan_script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    text = plan_script.read_text(encoding="utf-8")
    import re
    block = re.search(
        r'add_check\s+"svg-provenance"[\s\S]+?"(block|warn)"',
        text,
    )
    assert block, "svg-provenance add_check missing or malformed"
    assert block.group(1) == "block", (
        "svg-provenance must be severity=block (IconMark.tsx-style invention is a real failure)"
    )



def test_scaffold_to_jsx_rewrites_cdn_optimizer_srcset_to_local_assets(tmp_path: Path) -> None:
    """Loop-55 regression: preserving ref `/cdn-cgi/image/...` src/srcset
    makes the local impl request a Cloudflare optimizer route that Next does
    not serve. The deterministic scaffold must rewrite both src and srcset
    candidates to transferred public asset paths.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "main",
        "children": [
            {
                "tag": "section",
                "class": "hero",
                "children": [
                    {
                        "tag": "img",
                        "class": "hero-img",
                        "alt": "Foo",
                        "src": "https://cdn.example.com/cdn-cgi/image/width=640,quality=90/images/foo.webp",
                        "srcset": (
                            "https://cdn.example.com/cdn-cgi/image/width=256,quality=90/images/foo.webp 256w, "
                            "https://cdn.example.com/cdn-cgi/image/width=640,quality=90/images/foo.webp 640w"
                        ),
                    }
                ],
            }
        ],
    }))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 0, "tag": "section", "cls": "hero"}],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr

    generated = "\n".join(p.read_text(encoding="utf-8") for p in (impl / "src" / "components").glob("*.tsx"))
    assert "/cdn-cgi/image/" not in generated
    assert 'src="/images/foo.webp"' in generated
    assert 'srcSet="/images/foo.webp 256w, /images/foo.webp 640w"' in generated



def test_ref_screenshot_asset_fail_on_byte_copy(tmp_path: Path) -> None:
    """Byte-identical impl copy of a ref screenshot must FAIL the gate."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "public").mkdir(parents=True)
    (impl / "src").mkdir()
    payload = b"\x89PNG\r\n\x1a\n" + b"realbytes-xyzzy"
    (ref / "sections" / "ref" / "hero.png").write_bytes(payload)
    (impl / "public" / "renamed.png").write_bytes(payload)
    (impl / "src" / "App.tsx").write_text("export default function A(){return null}\n")
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    assert art["status"] == "fail"
    kinds = {v["kind"] for v in art["violations"]}
    assert "byte-identical-copy" in kinds



def test_ref_screenshot_asset_pass_on_clean_impl(tmp_path: Path) -> None:
    """Impl that doesn't touch ref capture artifacts must PASS."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    (ref / "sections" / "ref" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        'import "/images/logo.png"\nexport default function A(){return null}\n',
    )
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    assert art["status"] == "pass"



# ── Canvas-replay allowlist for ref-screenshot-asset (v0.7.0) ───────────
# When closeoutPolicy=="canvas-replay" AND attestation is present AND a
# byte-identical-copy violation involves a screenshot whose source PNG
# belongs to a section tagged kind="canvas" in section-map.json, the
# violation is allowed. Non-canvas-section copies still FAIL.
#
# Scope is byte-identical-copy specifically — ref-path-reference (generic
# "tmp/ref/" substring leaks in impl source) stays strict because the
# substring doesn't pinpoint which section the leak is for, and the
# canvas-replay opt-in escape is not for generic path-string laundering.


def _wire_canvas_replay_screenshot(
    ref: Path,
    *,
    canvas_section_name: str,
    policy: str = "canvas-replay",
    with_attestation: bool = True,
) -> None:
    (ref / "pipeline-state.json").write_text(
        json.dumps({"component": ref.name, "closeoutPolicy": policy}),
        encoding="utf-8",
    )
    if with_attestation:
        (ref / "canvas-replay-attestation.json").write_text(
            json.dumps({
                "license": "MIT",
                "disclaimer": "test",
                "attestedBy": "operator",
                "attestedAt": "2026-05-25T08:00:00Z",
                "ref_canvas_sources": ["https://canvas.example.org/driver.js"],
            }),
            encoding="utf-8",
        )
    (ref / "section-map.json").write_text(
        json.dumps({
            "sections": [
                {"index": 0, "kind": "canvas", "name": canvas_section_name},
                {"index": 1, "name": "text-block"},  # non-canvas control
            ],
        }),
        encoding="utf-8",
    )


def test_ref_screenshot_asset_allows_canvas_section_byte_copy(tmp_path: Path) -> None:
    """v0.7.0 — byte-identical copy of a canvas-section ref screenshot is
    allowed when the policy is fully active. Operator may use the canvas
    section's captured PNG as a fallback under the attestation umbrella."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "public").mkdir(parents=True)
    (impl / "src").mkdir()
    payload = b"\x89PNG\r\n\x1a\n" + b"canvas-fallback-bytes"
    # The ref PNG basename matches a kind=canvas section in section-map.json.
    (ref / "sections" / "ref" / "music-sphere.png").write_bytes(payload)
    (impl / "public" / "canvas-fallback.png").write_bytes(payload)
    (impl / "src" / "App.tsx").write_text("export default function A(){return null}\n")
    _wire_canvas_replay_screenshot(ref, canvas_section_name="music-sphere")
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, (
        f"canvas-section byte-copy must PASS under canvas-replay: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    assert art["status"] == "pass"


def test_ref_screenshot_asset_still_fails_non_canvas_byte_copy(tmp_path: Path) -> None:
    """v0.7.0 boundary — non-canvas section byte-copy still FAILs even when
    the canvas-replay policy is active. The relief is per-section, not blanket."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "public").mkdir(parents=True)
    (impl / "src").mkdir()
    payload = b"\x89PNG\r\n\x1a\n" + b"non-canvas-bytes"
    # The ref PNG basename is "text-block" — non-canvas in section-map.
    (ref / "sections" / "ref" / "text-block.png").write_bytes(payload)
    (impl / "public" / "stolen.png").write_bytes(payload)
    (impl / "src" / "App.tsx").write_text("export default function A(){return null}\n")
    _wire_canvas_replay_screenshot(ref, canvas_section_name="music-sphere")
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, (
        f"non-canvas byte-copy must STILL fail under canvas-replay: {proc.stdout}"
    )


def test_ref_screenshot_asset_no_allowlist_without_attestation(tmp_path: Path) -> None:
    """Policy set but attestation missing → no allowlist."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "public").mkdir(parents=True)
    (impl / "src").mkdir()
    payload = b"\x89PNG\r\n\x1a\n" + b"canvas-fallback-bytes"
    (ref / "sections" / "ref" / "music-sphere.png").write_bytes(payload)
    (impl / "public" / "fallback.png").write_bytes(payload)
    (impl / "src" / "App.tsx").write_text("export default function A(){return null}\n")
    _wire_canvas_replay_screenshot(
        ref, canvas_section_name="music-sphere", with_attestation=False,
    )
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, (
        f"missing attestation must NOT activate allowlist: {proc.stdout}"
    )


def test_ref_screenshot_asset_ref_path_reference_stays_strict(tmp_path: Path) -> None:
    """Canvas-replay does NOT relax ref-path-reference detection — generic
    'tmp/ref/' substring leaks in impl source stay strict. The substring
    doesn't pinpoint a section, so we can't safely scope relief to canvas."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        'export const REF = "/sections/ref/music-sphere.png";\n'
    )
    _wire_canvas_replay_screenshot(ref, canvas_section_name="music-sphere")
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, (
        f"ref-path-reference must stay strict under canvas-replay: {proc.stdout}"
    )
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    kinds = {v["kind"] for v in art["violations"]}
    assert "ref-path-reference" in kinds


def test_gate_svg_dom_parity_status_pass_passes(tmp_path: Path) -> None:
    """Clean SVG parity artifact → gate passes."""
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(ref, "svg-dom-parity", "svg-dom-parity.json")
    (ref / "svg-dom-parity.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "refUrl": "https://realfood.gov",
        "implUrl": "http://localhost:5173",
        "refPage": {"total": 8, "inlineSvg": 6, "svgWithPath": 6},
        "implPage": {"total": 7, "inlineSvg": 5, "svgWithPath": 5},
        "violations": [],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert not any("svg-dom-parity" in r.label for r in failures), failures



def test_gate_svg_dom_parity_status_fail_fails(tmp_path: Path) -> None:
    """SVG inventory dropout → gate fails."""
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(ref, "svg-dom-parity", "svg-dom-parity.json")
    (ref / "svg-dom-parity.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "refUrl": "https://realfood.gov",
        "implUrl": "http://localhost:5173",
        "refPage": {"total": 12, "inlineSvg": 12, "svgWithPath": 12},
        "implPage": {"total": 1, "inlineSvg": 1, "svgWithPath": 0},
        "violations": [
            {"kind": "page-total-svg-dropout", "ref": 12, "impl": 1, "ratio": 0.083},
            {"kind": "inline-svg-empty", "refWithPath": 12, "implWithPath": 0},
        ],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any("svg-dom-parity" in r.label for r in failures), results

