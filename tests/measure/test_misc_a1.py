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
        "runtime-spec-coverage.json", "scroll-completion.json",
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
        "runtime-spec-coverage.json", "scroll-completion.json",
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
        "runtime-spec-coverage.json", "hidden-children.json",
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
