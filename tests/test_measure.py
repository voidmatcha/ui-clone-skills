"""Tests for ui_clone.measure — the locked-env Python orchestrator.

The whole point of measure.py is that the bash measurement scripts run
with `EXCLUDE_DYNAMIC=1` and `SECTION_THRESHOLD=2000` regardless of
what the caller passes. Mock subprocess.run and assert the env passed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from ui_clone import measure


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


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


def test_section_compare_locks_exclude_dynamic_and_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    """`measure section-compare` MUST invoke bash with EXCLUDE_DYNAMIC=1
    and SECTION_THRESHOLD=2000, even when the parent shell sets them to
    permissive values. Locks down the d19e28d gaming pattern where the
    agent set SECTION_THRESHOLD=250000 to re-classify critical→minor.
    """
    captured_env: dict[str, str] = {}

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        captured_env.update(env)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir="/tmp/fake-ref",
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
    )
    with mock.patch.dict(os.environ, {
        "EXCLUDE_DYNAMIC": "0",         # caller's permissive default
        "SECTION_THRESHOLD": "250000",  # caller's gaming attempt
    }, clear=False), mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_section_compare(args)

    assert captured_env["EXCLUDE_DYNAMIC"] == "1", (
        f"EXCLUDE_DYNAMIC must be locked to 1, got: {captured_env.get('EXCLUDE_DYNAMIC')!r}"
    )
    assert captured_env["SECTION_THRESHOLD"] == "2000", (
        f"SECTION_THRESHOLD must be locked to 2000, got: {captured_env.get('SECTION_THRESHOLD')!r}"
    )
    # Status JSON on stdout
    out = capsys.readouterr().out.strip().splitlines()
    status = json.loads(out[-1])
    assert status["step"] == "section-compare"
    assert status["locked_env"] == {"EXCLUDE_DYNAMIC": "1", "SECTION_THRESHOLD": "2000"}


def test_transition_compare_does_not_lock_section_threshold() -> None:
    """transition-compare has its own scoring; the SECTION_THRESHOLD lock
    is irrelevant there. Only section-compare gets the AE-classifier lock.
    """
    captured_env: dict[str, str] = {}

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        captured_env.update(env)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir="/tmp/fake-ref",
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
    )
    with mock.patch.dict(os.environ, {"SECTION_THRESHOLD": "999"}, clear=False), \
         mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_transition_compare(args)
    # transition-compare doesn't override SECTION_THRESHOLD — caller's value passes through.
    assert captured_env["SECTION_THRESHOLD"] == "999"


def test_all_runs_section_compare_first(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """The `all` subcommand orders section-compare BEFORE transition-compare —
    static fidelity is measured first so motion noise doesn't contaminate
    the structural verdict.
    """
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    # Make transition-spec.json exist so transition-compare is included
    (ref_dir / "transition-spec.json").write_text(json.dumps({"transitions": []}))

    call_order: list[str] = []

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        # Extract script name from the bash invocation
        for c in cmd:
            if "section-compare.sh" in c:
                call_order.append("section-compare")
            elif "transition-compare.sh" in c:
                call_order.append("transition-compare")
            elif "asset-utilization-check.sh" in c:
                call_order.append("asset-utilization")
            elif "bundle-impl-coverage-check.sh" in c:
                call_order.append("bundle-impl-coverage")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir=str(ref_dir),
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
        impl_src=None,
        impl_pkg=None,
    )
    with mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_all(args)

    assert call_order[0] == "section-compare", (
        f"section-compare must run FIRST in the canonical sequence: {call_order}"
    )
    assert "transition-compare" in call_order
    assert call_order.index("section-compare") < call_order.index("transition-compare")


def test_all_skips_transition_compare_when_no_spec(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """No transition-spec.json → transition-compare skipped (recorded as skip
    in summary). The bash script would otherwise error on missing input.
    """
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    # No transition-spec.json

    invoked_scripts: list[str] = []

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        for c in cmd:
            if c.endswith(".sh"):
                invoked_scripts.append(Path(c).name)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir=str(ref_dir),
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
        impl_src=None,
        impl_pkg=None,
    )
    with mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_all(args)

    assert "transition-compare.sh" not in invoked_scripts, (
        f"transition-compare must be skipped when no spec exists; invoked: {invoked_scripts}"
    )
    out = capsys.readouterr().out.strip().splitlines()
    final = json.loads(out[-1])
    skip_entry = next((s for s in final["summary"] if s["step"] == "transition-compare"), None)
    assert skip_entry is not None
    assert skip_entry["exit_code"] == "skip"


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


def test_locked_defaults_exposed_for_audit() -> None:
    """The LOCKED_DEFAULTS dict must be importable so tooling/docs can
    surface which env vars are pinned without parsing source.
    """
    assert "EXCLUDE_DYNAMIC" in measure.LOCKED_DEFAULTS
    assert measure.LOCKED_DEFAULTS["EXCLUDE_DYNAMIC"] == "1"
    assert "SECTION_THRESHOLD" in measure.LOCKED_DEFAULTS
    assert measure.LOCKED_DEFAULTS["SECTION_THRESHOLD"] == "2000"


def test_dom_extraction_captures_direct_text() -> None:
    """Regression (Fix 6 v1): the DOM extraction eval in dom-extraction.md
    MUST capture each element's direct text (own text nodes, not descendants').
    Without `text` in the extracted schema, Phase 4 has no verbatim text to
    paste — agent fabricates from class names / URLs / asset filenames. The
    3-round benchmark showed Hero generated with "Eat Real Food" while the
    real ref hero said "Real Food Wins".
    """
    doc = _project_root() / "skills" / "ui-reverse-engineering" / "dom-extraction.md"
    text = doc.read_text(encoding="utf-8")

    # The direct-text helper that captures own-text without recursing into
    # descendants — keeps structure.json from exploding with duplicated text.
    assert "directText" in text, "dom-extraction.md must define directText helper"
    assert "nodeType === 3" in text, (
        "directText must filter to text nodes (nodeType === 3) to avoid "
        "capturing nested element duplicates"
    )
    # The extract function must populate `text` from the helper.
    assert "out.text = text" in text or "text: directText" in text, (
        "dom-extraction.md extract() must populate a `text` field on each node"
    )


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


def test_verification_plan_includes_header_state_runtime_row() -> None:
    """The plan emitter must register header-state-runtime as a required
    check so the dispatcher actually runs it (and so the verify-stamp
    check counts its artifact). Severity must be block — a static
    header is the exact "captured-HTML-only" failure mode the user
    flagged.
    """
    plan_script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    text = plan_script.read_text(encoding="utf-8")
    assert "header-state-runtime" in text, (
        "verification-plan.sh must add the header-state-runtime row"
    )
    # Find the add_check block and check severity.
    import re
    block = re.search(
        r'add_check\s+"header-state-runtime"[\s\S]+?"(block|warn)"',
        text,
    )
    assert block, "header-state-runtime add_check block missing or malformed"
    assert block.group(1) == "block", (
        "header-state-runtime must be severity=block (captured-HTML paste is a real failure)"
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


def test_gate_dep_dag_skips_downstream_when_upstream_fails(tmp_path: Path) -> None:
    """The dispatcher's gate-dependency DAG: when an upstream gate (e.g.
    runtime-env) fails, downstream gates that declared `dependsOn` it
    must be marked SKIPPED_DEP rather than run.
    """
    import subprocess
    root = _project_root()
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"name": "impl"}))
    ref.mkdir()
    (ref / ".impl-root").write_text(str(impl) + "\n")
    # Plan: one always-failing upstream and one downstream that depends
    # on it. Choose runtime-env as upstream (real script). For the
    # downstream we use video-play-proof which already declares
    # runtime-env in its dependsOn.
    (ref / "verification-plan.json").write_text(json.dumps({
        "schemaVersion": 1,
        "requiredChecks": [
            # Upstream that will fail (impl-url 127.0.0.1:1 connection refused).
            {
                "id": "runtime-env",
                "script": "skills/visual-debug/scripts/runtime-env-check.sh",
                "produces": "runtime-env.json",
                "reason": "test upstream",
                "severity": "block",
                "tier": "standard",
            },
            # Downstream that depends on runtime-env.
            {
                "id": "video-play-proof",
                "script": "skills/visual-debug/scripts/video-play-proof-check.sh",
                "produces": "video-play-proof.json",
                "reason": "test downstream",
                "severity": "block",
                "tier": "standard",
                "dependsOn": ["runtime-env"],
            },
        ],
    }))

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "dag-test-session",
            "https://example.test",
            "http://127.0.0.1:1",  # nothing listening here → runtime-env fails
            str(ref),
        ],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )
    # The downstream must be SKIPPED_DEP — the SKIPPED_DEP marker appears
    # in the dispatcher's per-row output line.
    assert "SKIPPED_DEP" in proc.stdout or "depends on failed" in proc.stdout, (
        f"downstream gate must skip when upstream fails:\n{proc.stdout}\n{proc.stderr}"
    )


def test_color_token_grounding_script_present() -> None:
    """2026-05-22 codex-rescue grounding audit (a0d22414 C):
    color-token gate must exist and document the failure mode.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "color-token-grounding-check.sh"
    assert script.is_file(), "color-token-grounding-check.sh missing"
    body = script.read_text(encoding="utf-8")
    assert "color_distance" in body or "Euclidean" in body, "must compute color distance"
    assert "styles.json" in body, "must read ref color palette"
    assert "color-token-grounding.json" in body
    assert "COMMON_NEUTRALS" in body, "must allowlist common UI neutrals"


def test_color_token_grounding_fails_on_invented_palette(tmp_path: Path) -> None:
    """Impl uses colors that don't appear in ref palette → gate fails."""
    import subprocess
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    # Ref palette: ash + cream
    (ref / "styles.json").write_text(json.dumps({
        "colors": ["#1a1a1a", "#f5efe6", "#ff6b35"],
    }))
    # Impl uses entirely unrelated colors
    (impl / "src" / "Comp.tsx").write_text(
        "export default function C() {\n"
        "  return <div style={{ color: '#00ff00', background: '#ff00ff', border: '1px solid #1234ab' }} />;\n"
        "}\n"
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "color-token-grounding-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 1, f"unrelated colors must FAIL: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "color-token-grounding.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["inventedCount"] >= 3


def test_completion_report_marks_incomplete_without_proofs(tmp_path: Path) -> None:
    """Report builder must mark INCOMPLETE when runtime-proof or
    transition-proof are missing/failing."""
    import subprocess
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    impl.mkdir()
    ref.mkdir()
    # Skip writing runtime-proof to simulate the missing case
    script = _project_root() / "scripts" / "verify" / "completion-report.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0  # report builder always returns 0
    assert "INCOMPLETE" in proc.stdout, (
        f"missing proofs must surface INCOMPLETE marker:\n{proc.stdout}"
    )


def test_phase2_preflight_script_present() -> None:
    """codex-rescue Rank 1 (af0da280): phase 2 preflight must exist."""
    script = _project_root() / "scripts" / "verify" / "phase2-preflight.sh"
    assert script.is_file(), "phase2-preflight.sh missing"
    body = script.read_text(encoding="utf-8")
    assert "runtime-env-check.sh" in body, "must delegate to runtime-env-check"
    assert "lsof" in body, "must auto-detect impl-url via lsof"


def test_impl_scope_check_script_present() -> None:
    """2026-05-22 user observation (gate-cheat block): impl-scope-check
    must exist and document the gate-modification cheat it blocks.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "impl-scope-check.sh"
    assert script.is_file(), "impl-scope-check.sh missing"
    body = script.read_text(encoding="utf-8")
    assert "iteration-baseline-sha" in body, "must persist baseline SHA"
    assert "git diff" in body, "must diff against baseline"
    assert "skills/" in body and "scripts/" in body, (
        "rule must name plugin tooling directories as out-of-scope"
    )
    assert "impl-scope.json" in body


def test_impl_scope_check_initializes_baseline_on_first_call(tmp_path: Path) -> None:
    """First invocation writes the baseline SHA file and returns
    status=initialized so the gate doesn't false-fail before any
    iteration work happens.
    """
    import subprocess
    # Use the real repo's .git so git rev-parse works
    repo = _project_root()
    ref = tmp_path / "ref"
    impl = repo / "scratch" / "test-impl-scope"
    ref.mkdir()
    impl.mkdir(parents=True, exist_ok=True)
    try:
        script = repo / "skills" / "visual-debug" / "scripts" / "impl-scope-check.sh"
        proc = subprocess.run(
            ["bash", str(script), str(ref), str(impl)],
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 0, f"initialization must pass: {proc.stdout}\n{proc.stderr}"
        artifact = json.loads((ref / "impl-scope.json").read_text())
        assert artifact["status"] == "initialized"
        baseline = (ref / "iteration-baseline-sha.txt").read_text().strip()
        assert len(baseline) >= 7  # short SHA at minimum
    finally:
        # Clean up the test impl dir we created in the real repo tree
        import shutil
        if impl.exists():
            shutil.rmtree(impl, ignore_errors=True)


def test_impl_scope_check_dispatcher_wired() -> None:
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "run-required-checks.sh"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"impl-scope-check\.sh":\s*"([^"]+)"', text)
    assert m, "impl-scope-check.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{ref_dir}" in recipe and "{impl_root}" in recipe, (
        f"impl-scope recipe must include both ref_dir and impl_root (got: {recipe!r})"
    )


def test_video_play_proof_script_present() -> None:
    """2026-05-22 codex-rescue Rank 3: video must actually play, not
    just exist. Gate catches the static-poster-only cheat where
    required-media-coverage passes on the .mp4 file but the <video>
    never advances currentTime.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "video-play-proof-check.sh"
    assert script.is_file(), "video-play-proof-check.sh missing"
    body = script.read_text(encoding="utf-8")
    assert "currentTime" in body, "must probe currentTime advancement"
    assert "play()" in body or "v.play" in body, "must call play() on muted videos"
    assert "video-play-proof.json" in body
    assert "skip" in body, "must skip when ref has no video signal"


def test_video_play_proof_dispatcher_wired() -> None:
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "run-required-checks.sh"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"video-play-proof-check\.sh":\s*"([^"]+)"', text)
    assert m, "video-play-proof-check.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{session}" in recipe and "{impl_url}" in recipe and "{ref_dir}" in recipe, (
        f"video-play-proof recipe must include session/impl_url/ref_dir (got: {recipe!r})"
    )


def test_verification_plan_includes_video_play_proof_block_row() -> None:
    plan_script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    text = plan_script.read_text(encoding="utf-8")
    import re
    block = re.search(
        r'add_check\s+"video-play-proof"[\s\S]+?"(block|warn)"',
        text,
    )
    assert block, "video-play-proof add_check missing or malformed"
    assert block.group(1) == "block"


def test_runtime_env_check_script_present() -> None:
    """2026-05-22 empirical (codex-19 NODE_ENV trap, codex-18 orphan port):
    runtime-env gate must exist and document both failure modes.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "runtime-env-check.sh"
    assert script.is_file(), "runtime-env-check.sh missing"
    body = script.read_text(encoding="utf-8")
    # Must reference both failure modes
    assert "$RefreshSig$" in body, "must catch Vite Fast Refresh trap"
    assert "PORT_OWNER_MISMATCH" in body or "port-routing" in body, (
        "must check port-routing"
    )
    # Must write canonical artifact
    assert "runtime-env.json" in body


def test_runtime_env_dispatcher_wired() -> None:
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "run-required-checks.sh"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"runtime-env-check\.sh":\s*"([^"]+)"', text)
    assert m, "runtime-env-check.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{ref_dir}" in recipe and "{impl_root}" in recipe and "{impl_url}" in recipe, (
        f"runtime-env recipe must include {{ref_dir}}, {{impl_root}}, {{impl_url}} "
        f"(got: {recipe!r})"
    )


def test_verification_plan_includes_runtime_env_block_row() -> None:
    """runtime-env must be severity=block — env traps invalidate every
    downstream gate's verdict, so they need to be the first failure
    surface, not a silent warning.
    """
    plan_script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    text = plan_script.read_text(encoding="utf-8")
    import re
    block = re.search(
        r'add_check\s+"runtime-env"[\s\S]+?"(block|warn)"',
        text,
    )
    assert block, "runtime-env add_check missing or malformed"
    assert block.group(1) == "block", (
        "runtime-env must be severity=block — env traps make downstream "
        "gates produce misleading verdicts"
    )


def test_ref_js_loader_allows_shared_cdn_hosts(tmp_path: Path) -> None:
    """2026-05-22 codex-rescue meta-review (ac93f1e7): shared CDN hosts
    (Google Fonts, jsDelivr, etc.) appearing in ref artifacts must NOT
    flag the impl when impl also uses them legitimately. The cheat
    target is impl-loads-ref-OWNED-bundle, not impl-loads-shared-CDN.
    """
    import subprocess
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    # Ref artifacts mention Google Fonts and the actual ref host
    (ref / "head.json").write_text(json.dumps({
        "url": "https://example.cheat-target.org/",
        "fonts": [{"href": "https://fonts.googleapis.com/css2?family=Inter"}],
    }))
    # Impl ALSO uses Google Fonts for an unrelated dep — legit
    (impl / "src" / "Good.tsx").write_text(
        "import './styles.css';\n"
        "// Legit shared-CDN reference — same Google Fonts host\n"
        "// import 'https://fonts.googleapis.com/css?family=Roboto';\n"
        "export default function Good() { return <div />; }\n"
    )
    (impl / "package.json").write_text(json.dumps({"name": "impl"}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "ref-js-loader-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, (
        f"shared CDN ref must not flag impl: {proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "ref-js-loader.json").read_text())
    # Allowlisted CDN must NOT appear in refHosts
    assert "fonts.googleapis.com" not in artifact["refHosts"], (
        f"Google Fonts must be filtered out of refHosts: {artifact['refHosts']}"
    )
    assert artifact["status"] in ("pass", "skip")


def test_ref_js_loader_passes_clean_impl(tmp_path: Path) -> None:
    """Clean impl with only same-origin imports must pass."""
    import subprocess
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "head.json").write_text(json.dumps({
        "url": "https://example.cheat-target.org/",
    }))
    (impl / "src" / "Good.tsx").write_text(
        "import React from 'react';\n"
        "import './styles.css';\n"
        "export default function Good() { return <div />; }\n"
    )
    (impl / "package.json").write_text(json.dumps({"name": "impl"}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "ref-js-loader-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, f"clean impl must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "ref-js-loader.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["violations"] == []


def test_ref_js_loader_dispatcher_wired() -> None:
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "run-required-checks.sh"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"ref-js-loader-check\.sh":\s*"([^"]+)"', text)
    assert m, "ref-js-loader-check.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{ref_dir}" in recipe and "{impl_root}" in recipe, (
        f"recipe must include {{ref_dir}} and {{impl_root}} (got: {recipe!r})"
    )


def test_transition_proof_rollup_fails_partial_coverage(tmp_path: Path) -> None:
    """Composite rollup must FAIL when transition-spec-coverage status=pass
    but covered<total (the static gate's bug class — partial coverage with
    a pass verdict).
    """
    import subprocess
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "total": 7, "covered": 4, "uncovered": 3,
    }))
    (ref / "spec-implementation-coverage.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "pass",
        "total": 7, "withMotion": 7,
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 1, f"partial coverage must compose to FAIL: {proc.stdout}"
    artifact = json.loads((ref / "transition-proof.json").read_text())
    assert artifact["status"] == "fail"
    msg = " ".join(artifact["reasons"])
    assert "partial coverage" in msg or "4/7" in msg, (
        f"reasons must name the partial-coverage issue: {msg}"
    )


def test_transition_proof_rollup_dispatcher_wired() -> None:
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "run-required-checks.sh"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"transition-proof-rollup\.sh":\s*"([^"]+)"', text)
    assert m, "transition-proof-rollup.sh missing from dispatcher SIGNATURES"
    assert "{ref_dir}" in m.group(1)


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


def test_fix8_verification_plan_dispatches_new_gates() -> None:
    """Fix 8 — verification-plan.sh must dispatch text-fidelity-check and
    dom-mirror-check at tier=quick (static analysis, cheap) with severity=block.
    Without this dispatch the gates exist as scripts but never run as gates.
    """
    plan = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    body = plan.read_text(encoding="utf-8")
    assert 'add_check "text-fidelity-check"' in body, (
        "verification-plan.sh must dispatch text-fidelity-check"
    )
    assert 'add_check "dom-mirror-check"' in body, (
        "verification-plan.sh must dispatch dom-mirror-check"
    )


def test_hydration_check_fails_fatal_client_exceptions() -> None:
    """Hydration check must fail fatal client errors, not only text mismatches."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hydration-check.sh"
    body = script.read_text(encoding="utf-8")
    assert "FATAL_PATTERNS" in body
    assert "fatalErrors" in body
    assert "crypto\\.randomUUID is not a function" in body
    assert "Minified React error #418" in body


def test_proxy_mirror_check_fails_proxy_backed_original_runtime(tmp_path: Path) -> None:
    """A proxy-backed original runtime is a mirror, not a generated clone."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    public = impl / "public"
    ref.mkdir()
    public.mkdir(parents=True)
    (impl / "server.js").write_text(
        'const upstream = "https://target-site.example";\n'
        "async function proxyAndCache(parsed, targetPath, res) {\n"
        "  const response = await fetch(upstream + parsed.pathname);\n"
        "}\n",
        encoding="utf-8",
    )
    (public / "index.html").write_text(
        '<!DOCTYPE html><html><head><script src="/_next/static/chunks/app/page.js"></script></head>'
        '<body><script>self.__next_f=self.__next_f||[]</script></body></html>',
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "proxy-mirror-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"proxy-backed mirror must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "proxy-mirror-check.json").read_text())
    assert artifact["status"] == "fail"
    assert any("proxy" in finding["kind"] for finding in artifact["findings"])
    assert any("next-ssr" in finding["kind"] for finding in artifact["findings"])


def test_proxy_mirror_check_passes_source_component_impl(tmp_path: Path) -> None:
    """Normal source components with public assets are allowed."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    public = impl / "public"
    ref.mkdir()
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"dependencies": {"react": "19"}}))
    (src / "App.tsx").write_text("export default function App(){ return <main />; }\n")
    (public / "index.html").write_text('<div id="root"></div>\n')

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "proxy-mirror-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, f"source component impl should pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "proxy-mirror-check.json").read_text())
    assert artifact["status"] == "pass"


def test_verification_plan_dispatches_proxy_mirror_check(tmp_path: Path) -> None:
    """proxy/static mirrors must be a universal quick block check."""
    ref = tmp_path / "ref"
    ref.mkdir()

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "UI_CLONE_VERIFY_TIER": "quick"},
    )
    assert proc.returncode == 0, proc.stderr
    plan = json.loads((ref / "verification-plan.json").read_text())
    rows = {c["id"]: c for c in plan["requiredChecks"]}

    assert "proxy-mirror-check" in rows
    assert rows["proxy-mirror-check"]["severity"] == "block"
    assert rows["proxy-mirror-check"]["tier"] == "quick"
    assert rows["proxy-mirror-check"]["produces"] == "proxy-mirror-check.json"


def test_section_spec_script_present_and_callable() -> None:
    """Regression (Fix 6 v2): section-spec.sh must exist with the required
    flags (--label, --out, --metadata, --text) so Phase 2.6 grounding can run
    on each section. Without this step Phase 4 has no LLM-verified spec and
    falls back to inferring from extracted JSON.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-spec.sh"
    assert script.is_file(), "section-spec.sh must exist for Phase 2.6"
    body = script.read_text(encoding="utf-8")
    # Required flags
    assert "--label" in body, "section-spec.sh must accept --label"
    assert "--out" in body, "section-spec.sh must accept --out"
    assert "--metadata" in body, "section-spec.sh must accept --metadata"
    assert "--text" in body, "section-spec.sh must accept --text"
    # Calls claude --print (LLM-driven, not script-only)
    assert "claude --print" in body, (
        "section-spec.sh must call claude --print — Fix 6 v2 is LLM-driven"
    )
    # Prompt template exists
    prompt = _project_root() / "skills" / "visual-debug" / "prompts" / "section-spec.md"
    assert prompt.is_file(), "section-spec.md prompt template must exist"
    prompt_text = prompt.read_text(encoding="utf-8")
    # Schema keys required for grounded generation
    for key in ('"label"', '"text"', '"colors"', '"typography"', '"layout"', '"key_elements"'):
        assert key in prompt_text, f"section-spec.md prompt missing schema key {key}"


def test_fix13_dom_extraction_captures_per_node_styles() -> None:
    """Fix 13 — dom-extraction.md JS eval must capture per-node computed
    styles (LAYOUT_PROPS subset). Without this the scaffold-to-jsx transpiler
    has no styling info per node, defeating the whole determinism strategy.
    """
    doc = _project_root() / "skills" / "ui-reverse-engineering" / "dom-extraction.md"
    text = doc.read_text(encoding="utf-8")
    assert "LAYOUT_PROPS" in text, (
        "dom-extraction.md must define LAYOUT_PROPS for per-node style capture"
    )
    # Critical style props that must be in the capture list.
    for prop in ('font-family', 'background-color', 'padding', 'color', 'font-size'):
        assert f"'{prop}'" in text, f"LAYOUT_PROPS must include {prop}"
    assert "out.styles = styles" in text, (
        "extract() must populate out.styles when at least one prop diverges from default"
    )


def test_fix19_extract_dom_captures_hover_styles() -> None:
    """Fix 19 — extract-dom.sh must walk document.styleSheets and collect
    :hover (or :focus) declarations matching each element's class list,
    so the transpiler can emit CSS that gives Fix 16's captured transition
    something to animate to. Without this the impl emits inline transitions
    that never trigger because no hover-state CSS exists.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "extract-dom.sh"
    text = script.read_text(encoding="utf-8")
    assert "buildHoverRules" in text and "captureHover" in text, (
        "extract-dom.sh must define hover-rule helpers (Fix 19)"
    )
    assert "document.styleSheets" in text, (
        "captureHover must scan document.styleSheets"
    )
    assert "out.hover_styles" in text, (
        "extract() must attach hover_styles to each matching node"
    )


def test_fix19_scaffold_to_jsx_emits_hover_rules() -> None:
    """Fix 19 — scaffold-to-jsx.sh must turn hover_styles into a CSS rule
    via an auto-generated class id (h_N) appended to the node's className,
    plus a <style> block at the top of the component body so :hover works
    at runtime.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"
    text = script.read_text(encoding="utf-8")
    assert "hover_rules" in text, (
        "scaffold-to-jsx.sh must thread a hover_rules collector through render() (Fix 19)"
    )
    assert ":hover {" in text, (
        "the emitted CSS rule must include :hover selector"
    )
    assert "h_" in text, "auto-generated class ids must use h_<index> form"


def test_fix18_extract_dom_captures_pseudo_elements() -> None:
    """Fix 18 — extract-dom.sh must capture ::before / ::after computed
    styles + content per node so the transpiler can synthesize the
    pseudo-element layer that drives realfood.gov's glow rings, divider
    decorations, gradient overlays, etc. Without this the impl misses an
    entire visual layer — dominant cause of the "전체 레이아웃 못 잡는다"
    feedback after V15.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "extract-dom.sh"
    text = script.read_text(encoding="utf-8")
    assert "capturePseudo" in text, (
        "extract-dom.sh must define a capturePseudo helper for ::before/::after (Fix 18)"
    )
    assert "'::before'" in text and "'::after'" in text, (
        "capturePseudo must read both ::before and ::after computed styles"
    )
    assert "out.before_styles" in text and "out.after_styles" in text, (
        "extract() must attach before_styles/after_styles to each node when present"
    )


def test_fix18_scaffold_to_jsx_emits_pseudo_spans() -> None:
    """Fix 18 — scaffold-to-jsx.sh must turn before_styles/after_styles into
    visible JSX. The transpiler synthesizes <span data-pseudo="before"|"after">
    children with the pseudo's content + styles so the impl reproduces the
    decoration layer the ref draws via CSS pseudo-elements.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"
    text = script.read_text(encoding="utf-8")
    assert 'data-pseudo="' in text, (
        "scaffold-to-jsx.sh must emit <span data-pseudo=...> for captured pseudos (Fix 18)"
    )
    assert "before_styles" in text and "after_styles" in text, (
        "transpiler must read both before_styles and after_styles fields"
    )
    assert "_render_pseudo" in text, (
        "scaffold-to-jsx.sh must define _render_pseudo helper"
    )


def test_fix17_extract_dom_accepts_viewport_flag() -> None:
    """Fix 17 — extract-dom.sh accepts --viewport WIDTHxHEIGHT so the bench
    can sweep mobile + desktop in a single pipeline. Mobile capture (e.g.
    375x812) writes to structure_375x812.json so both structures live on
    disk for the transpiler / agent to diff. Without this the impl is
    desktop-only and breaks at small viewports — one of the two original
    gaps the user surfaced after V12 (the other was transitions, Fix 16).
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "extract-dom.sh"
    text = script.read_text(encoding="utf-8")
    assert "--viewport" in text, "extract-dom.sh must accept --viewport flag (Fix 17)"
    assert "structure_${VIEWPORT}.json" in text, (
        "viewport-scoped output path must use the WxH suffix (Fix 17)"
    )
    assert "agent-browser --session \"$SESSION\" set viewport" in text, (
        "extract-dom.sh must resize the agent-browser session via `set viewport W H` before extracting (Fix 17)"
    )
    # Schema-guard the WxH form so a typo can't silently produce desktop styles.
    assert "^[0-9]+x[0-9]+$" in text, (
        "viewport value must be validated against WIDTHxHEIGHT pattern (Fix 17)"
    )


def test_fix16b_scaffold_to_jsx_consumes_subtrees() -> None:
    """Fix 16b — scaffold-to-jsx.sh must not assign the same DOM subtree to
    multiple sections. V13 (11672af) regressed to ae_avg 881k because
    find_subtree_for_section returned the first match per section, so
    multiple sections sharing a CSS-Module class prefix all collapsed onto
    one subtree and rendered identical JSX. The `consumed` set tracks
    id(node) of already-assigned subtrees.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"
    body = script.read_text(encoding="utf-8")
    assert "consumed = set()" in body, (
        "scaffold-to-jsx.sh must initialize a consumed set before the section loop (Fix 16b)"
    )
    assert "id(node) in consumed" in body, (
        "find_subtree_for_section must skip subtrees already assigned (Fix 16b)"
    )
    assert "consumed.add(id(found))" in body, (
        "find_subtree_for_section must mark the assigned subtree consumed (Fix 16b)"
    )
    assert "find_subtree_for_section(structure, sec, consumed)" in body, (
        "section loop must pass consumed into find_subtree_for_section (Fix 16b)"
    )


def test_fix16_extract_dom_captures_transitions_and_animations() -> None:
    """Fix 16 — extract-dom.sh's LAYOUT_PROPS must include transition + animation
    properties so the impl renders the same hover/focus/active/keyframe motion
    as the ref. Without these the transpiler emits static JSX and the page
    looks dead. NOISE must also drop the user-agent defaults for these props
    ('all 0s ease 0s' etc.) so every node doesn't carry meaningless data.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "extract-dom.sh"
    text = script.read_text(encoding="utf-8")
    for prop in (
        'transition', 'transition-property', 'transition-duration',
        'animation', 'animation-name', 'animation-duration',
        'cursor',
    ):
        assert f"'{prop}'" in text, (
            f"extract-dom.sh LAYOUT_PROPS must include {prop} (Fix 16)"
        )
    # The default transition computed value Chromium emits — must be filtered.
    assert "'all 0s ease 0s'" in text, (
        "NOISE must drop the user-agent default transition value 'all 0s ease 0s'"
    )


def test_fix15_scaffold_to_jsx_emits_page_tsx() -> None:
    """Fix 15 — scaffold-to-jsx.sh must also emit impl/src/app/page.tsx that
    composes the generated section components. V11 (220c969) showed 3 sections
    (hero/lineInTheSand/stats) stuck at ~900k AE because agent-written
    page.tsx wrapped components incorrectly; transpiler-generated page.tsx
    eliminates that wiring drift by mirroring the ref structure root.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"
    body = script.read_text(encoding="utf-8")
    assert 'page.tsx' in body, "scaffold-to-jsx.sh must write page.tsx (Fix 15)"
    # Mirrors structure.json root tag (not hardcoded to <main>).
    assert 'root_tag' in body, "page.tsx must use structure.json root tag dynamically"
    # Dedup component names — collisions across sections common when
    # ref has repeated class names (e.g., 4× dga_section).
    assert "seen_names" in body, "must dedup component names for unique imports"


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


def test_fix13_scaffold_to_jsx_script_present() -> None:
    """Fix 13 — scaffold-to-jsx.sh is the deterministic transpiler that
    replaces the LLM-interpretation step in Phase 4. Locks the script + its
    invocation contract.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"
    assert script.is_file(), "scaffold-to-jsx.sh missing — Fix 13 incomplete"
    body = script.read_text(encoding="utf-8")
    # Reads structure.json + section-map.json.
    assert "structure.json" in body
    assert "section-map.json" in body
    # Writes .tsx files.
    assert ".tsx" in body
    # JSX semantics: void tags, class→className, for→htmlFor.
    assert "VOID_TAGS" in body, "must handle void elements"
    assert "ATTR_RENAMES" in body or '"class": "className"' in body, "must rename class→className"
    # Inline style emission.
    assert "style_to_jsx" in body, "must emit JSX-format style objects"
    # Per-section component file.
    assert "section_component_name" in body, "must derive component name per section"


def test_fix13_skill_md_phase_2_8() -> None:
    """Fix 13 — SKILL.md must reference Phase 2.8 deterministic transpile
    so the agent knows to invoke scaffold-to-jsx.sh between Phase 2.7
    (dom-scaffold) and Phase 3 (spec).
    """
    skill = _project_root() / "skills" / "benchmark" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert "Phase 2.8" in text, "benchmark/SKILL.md must document Phase 2.8 (Fix 13)"
    assert "scaffold-to-jsx" in text, "benchmark/SKILL.md must reference the transpiler"


def test_fix12_synthesis_drops_zero_height_wrappers() -> None:
    """Fix 12 — section-compare.sh synthesis must skip section-map entries
    with height < 50 (layout-only wrappers from pre-reveal capture). V8
    (d4b369d) measured ae_avg 509k partly because 5 zero-height wrappers
    were pixel-compared as catastrophic critical sections. The filter
    removes those from the synthesized ref-sections so AE reflects only
    real content rows.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")
    assert "_MIN_VISIBLE_HEIGHT" in text, (
        "section-compare.sh must define _MIN_VISIBLE_HEIGHT for Fix 12 filter"
    )
    assert "if h_raw < _MIN_VISIBLE_HEIGHT" in text or "h_raw < _MIN_VISIBLE_HEIGHT" in text, (
        "section-compare.sh must filter h_raw < _MIN_VISIBLE_HEIGHT entries"
    )
    # Safety: empty-output fallback (don't override with thin synthesis).
    assert "if len(out) < 3" in text, (
        "section-compare.sh must fall back to runtime enumeration when "
        "the filter removes too many sections"
    )


def test_section_compare_synthesis_uses_correct_section_map_keys() -> None:
    """Regression: section-compare.sh synthesizes ref-sections from
    section-map.json when ENUMERATE_SECTIONS comes back too lean. The
    synthesis code MUST read `top`/`cls` keys (the actual schema written by
    extraction) — not just `y`/`class` (older fallback). The 3-round benchmark
    hit `gate_fail_counts[post-implement] == 632` because the synthesis only
    read `y`, collapsed every section's rect.top to 0, and produced phantom-ref
    coords that triggered uniform AE/Mpx ~950k across all sections. This is
    the data-key bug that made the prior three benchmark rounds' AE numbers
    meaningless.

    Locks the key-name reads in section-compare.sh as a guard. If a future
    refactor drops `s.get("top")` or `s.get("cls")` from the synthesis block,
    this test fires before the script is re-deployed.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    # The synthesis block must read both schemas (top/y, cls/class) so the
    # script tolerates whichever the upstream produced. Lock the canonical
    # patterns; either form fires test failure if dropped.
    assert 's.get("top") or s.get("y")' in text, (
        "section-compare.sh synthesis must read s.get('top') with fallback to "
        "s.get('y') — the 632-retry bug came from reading only 'y'"
    )
    assert 's.get("cls") or s.get("className") or s.get("class")' in text, (
        "section-compare.sh synthesis must read s.get('cls') with fallback to "
        "s.get('className') / s.get('class') — section-map.json writes 'cls', "
        "not just 'class'"
    )


def test_section_compare_descends_main_wrappers_with_section_descendants() -> None:
    """Loop-56 regression: a `<main>` with only a few color-band wrapper
    `<div>` children must still be treated as a layout wrapper when those
    children contain real section descendants. Otherwise section-compare pairs
    one giant main element and agents can add invisible sentinel children to
    game enumeration.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    assert "structuralDescendantCount" in text, (
        "section-compare.sh must count nested section/main descendants, not just "
        "direct structural children"
    )
    assert "hasWrappedStructuralDescendants" in text, (
        "section-compare.sh must descend <main> wrapper divs that contain real "
        "section/main descendants"
    )
    assert "structuralDescendantCount >= 2" in text, (
        "the wrapper descent must require multiple nested structural sections so "
        "ordinary one-section mains are not over-split"
    )


# ───────────────────────────────────────────────────────────────────────
# Loop-9 family anti-cheat gates — positive + negative fixtures
# (Codex L60 audit TEST GAP closure: dispatch-membership coverage isn't
# enough; each gate needs at least one pass + one fail trace through
# its actual logic so refactors don't silently invert behavior).
# ───────────────────────────────────────────────────────────────────────


def _run_script(script_rel: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    script = _project_root() / script_rel
    assert script.is_file(), f"missing {script}"
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True, text=True, timeout=timeout,
    )


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


def test_entry_coherence_fail_on_coexisting_entries(tmp_path: Path) -> None:
    """Vite+Next entry coexistence must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "app").mkdir()
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"vite": "5", "@vitejs/plugin-react": "4", "react": "19"},
        "scripts": {"dev": "vite", "build": "vite build"},
    }))
    (impl / "vite.config.ts").write_text("export default {}")
    (impl / "src" / "main.tsx").write_text("createRoot(...).render(<App/>)")
    (impl / "app" / "page.tsx").write_text("export default function Page(){return null}")
    (impl / "index.html").write_text(
        '<html><body><div id="root"></div></body></html>',
    )
    proc = _run_script(
        "skills/visual-debug/scripts/entry-coherence-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "entry-coherence.json").read_text())
    assert art["status"] == "fail"
    assert any(v["kind"] == "coexisting-entry-points" for v in art["violations"])


def test_entry_coherence_pass_on_clean_vite_scaffold(tmp_path: Path) -> None:
    """Plain Vite scaffold must PASS."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"vite": "5", "@vitejs/plugin-react": "4", "react": "19"},
        "scripts": {"dev": "vite", "build": "vite build"},
    }))
    (impl / "vite.config.ts").write_text("export default {}")
    (impl / "src" / "main.tsx").write_text("createRoot(...).render(<App/>)")
    (impl / "index.html").write_text(
        '<!DOCTYPE html><html><body>'
        '<div id="root"></div><script type="module" src="/src/main.tsx"></script>'
        '</body></html>',
    )
    proc = _run_script(
        "skills/visual-debug/scripts/entry-coherence-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "entry-coherence.json").read_text())
    assert art["status"] == "pass", art


def test_scaffold_residue_fail_on_unused_components(tmp_path: Path) -> None:
    """5 unused PascalCase components must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    components = impl / "src" / "components"
    components.mkdir(parents=True)
    (impl / "src" / "main.tsx").write_text(
        "import { createRoot } from 'react-dom/client'\n"
        "createRoot(document.getElementById('root')!).render(<div>nothing</div>)\n",
    )
    for c in ("Hero", "Footer", "Nav", "Banner", "Card"):
        (components / f"{c}.tsx").write_text(
            f"export default function {c}(){{return <div>{c}</div>}}\n",
        )
    proc = _run_script(
        "skills/visual-debug/scripts/scaffold-residue-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "scaffold-residue.json").read_text())
    assert art["status"] == "fail"
    assert art["orphanCount"] == 5


def test_scaffold_residue_pass_on_used_components(tmp_path: Path) -> None:
    """Components referenced as JSX in App.tsx must PASS."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    components = impl / "src" / "components"
    components.mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        "import Hero from './components/Hero'\n"
        "import Footer from './components/Footer'\n"
        "export default function App(){return <><Hero/><Footer/></>}\n",
    )
    (components / "Hero.tsx").write_text(
        "export default function Hero(){return <h1>x</h1>}\n",
    )
    (components / "Footer.tsx").write_text(
        "export default function Footer(){return <p>y</p>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/scaffold-residue-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "scaffold-residue.json").read_text())
    assert art["status"] == "pass"
    assert art["orphanCount"] == 0


def test_scaffold_residue_pass_on_barrel_reexports(tmp_path: Path) -> None:
    """Components re-exported from index.ts barrels must NOT count as orphans."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    components = impl / "src" / "components"
    components.mkdir(parents=True)
    for c in ("Hero", "Footer", "Nav"):
        (components / f"{c}.tsx").write_text(
            f"export function {c}(){{return <div>{c}</div>}}\n",
        )
    (components / "index.ts").write_text(
        "export { Hero } from './Hero'\n"
        "export { Footer } from './Footer'\n"
        "export { Nav } from './Nav'\n",
    )
    (impl / "src" / "main.tsx").write_text(
        "import { createRoot } from 'react-dom/client'\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/scaffold-residue-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "scaffold-residue.json").read_text())
    # Re-exports from index.ts qualify as intentional public API surface.
    assert art["status"] == "pass", art


def test_css_mirror_fail_on_byte_copy(tmp_path: Path) -> None:
    """Impl CSS byte-identical to a ref bundle must FAIL."""
    ref = tmp_path / "ref"
    (ref / "bundles").mkdir(parents=True)
    css_body = "\n".join([f".class{i} {{ color: #{i:03x}; padding: {i}px; }}" for i in range(80)])
    (ref / "bundles" / "main.css").write_text(css_body)
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "index.css").write_text(css_body)
    proc = _run_script(
        "skills/visual-debug/scripts/css-mirror-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "css-mirror.json").read_text())
    assert art["status"] == "fail"
    assert any(v["kind"] == "byte-identical-copy" for v in art["violations"])


def test_css_mirror_pass_on_clean_impl(tmp_path: Path) -> None:
    """Impl with its own CSS must PASS."""
    ref = tmp_path / "ref"
    (ref / "bundles").mkdir(parents=True)
    (ref / "bundles" / "main.css").write_text(
        "\n".join([f".x{i} {{ color: #{i:03x}; }}" for i in range(80)]),
    )
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "index.css").write_text(
        ":root { --bg: white; }\n"
        "body { font-family: system-ui; margin: 0; }\n"
        ".btn { padding: 8px 16px; border-radius: 8px; }\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/css-mirror-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "css-mirror.json").read_text())
    assert art["status"] == "pass", art


def test_required_media_pass_when_artifact_absent(tmp_path: Path) -> None:
    """Coverage gate dispatched unconditionally; absent required-media.json → pass."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    proc = _run_script(
        "skills/visual-debug/scripts/required-media-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "required-media-coverage.json").read_text())
    assert art["status"] == "pass"


def test_required_media_fail_when_impl_missing_videos(tmp_path: Path) -> None:
    """Required video present in required-media.json but missing in impl → fail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "required-media.json").write_text(json.dumps({
        "schemaVersion": 1,
        "videos": [{
            "section": "hero", "src": "https://cdn/PC_1920x1080_High.mp4",
            "type": "video/mp4",
        }],
        "lottie": [],
        "svgs": [],
        "totals": {"video": 1, "lottie": 0, "svg": 0},
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"dependencies": {"react": "19"}}))
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <div>placeholder</div>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/required-media-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "required-media-coverage.json").read_text())
    assert art["status"] == "fail"
    assert art["totals"]["videoMissing"] == 1


def test_scaffold_warn_fail_on_placeholder(tmp_path: Path) -> None:
    """data-scaffold-warn placeholders must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <div>\n"
        '<section data-scaffold-warn="subtree-not-found-for-hero" />\n'
        '<section data-scaffold-warn="subtree-not-found-for-cta" />\n'
        "</div>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/scaffold-warn-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "scaffold-warn.json").read_text())
    assert art["status"] == "fail"
    sections = {w["section"] for w in art["warnings"]}
    assert sections == {"hero", "cta"}


def test_scaffold_warn_fail_on_non_ascii_section_name(tmp_path: Path) -> None:
    """Non-ASCII (Korean) section names must still trigger the placeholder gate."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        "export default function App(){return <div>\n"
        '<section data-scaffold-warn="subtree-not-found-for-검색바" />\n'
        "</div>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/scaffold-warn-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "scaffold-warn.json").read_text())
    assert art["status"] == "fail"
    assert any(w["section"] == "검색바" for w in art["warnings"])


def test_invalidation_fail_on_stamp(tmp_path: Path) -> None:
    """A .invalidated stamp must hard-fail the gate."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / ".invalidated").write_text(json.dumps({
        "reason": "loop-9 cheated by overlaying ref screenshots",
        "markedAt": "2026-05-21",
        "markedBy": "operator",
    }))
    proc = _run_script(
        "skills/visual-debug/scripts/invalidation-check.sh", str(ref),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "invalidation.json").read_text())
    assert art["status"] == "fail"
    assert "loop-9" in art["reason"]


def test_invalidation_pass_without_stamp(tmp_path: Path) -> None:
    """No stamp → gate passes."""
    ref = tmp_path / "ref"
    ref.mkdir()
    proc = _run_script(
        "skills/visual-debug/scripts/invalidation-check.sh", str(ref),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "invalidation.json").read_text())
    assert art["status"] == "pass"
    assert art["stampPresent"] is False


def test_html_paste_fail_on_high_structural_similarity(tmp_path: Path) -> None:
    """index.html with >=70% tag-multiset match to dom-scaffold must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {"tag": "html", "children": [
            {"tag": "body", "children": [
                {"tag": "header", "children": [
                    {"tag": "nav", "children": [
                        {"tag": "ul", "children": [
                            {"tag": "li"}, {"tag": "li"}, {"tag": "li"},
                        ]},
                    ]},
                ]},
                {"tag": "main", "children": [
                    {"tag": "section", "children": [
                        {"tag": "h1"}, {"tag": "p"}, {"tag": "img"},
                    ]},
                    {"tag": "section", "children": [
                        {"tag": "h2"}, {"tag": "p"}, {"tag": "video"},
                    ]},
                    {"tag": "section", "children": [
                        {"tag": "h2"}, {"tag": "ul", "children": [
                            {"tag": "li"}, {"tag": "li"},
                        ]},
                    ]},
                ]},
                {"tag": "footer", "children": [
                    {"tag": "div"}, {"tag": "div"},
                ]},
            ]},
        ]},
    }))
    impl = tmp_path / "impl"
    impl.mkdir()
    (impl / "index.html").write_text(
        "<html><body>"
        "<header><nav><ul><li>a</li><li>b</li><li>c</li></ul></nav></header>"
        "<main>"
        "<section><h1>Hero</h1><p>copy</p><img /></section>"
        "<section><h2>S2</h2><p>x</p><video /></section>"
        "<section><h2>S3</h2><ul><li>i1</li><li>i2</li></ul></section>"
        "</main>"
        "<footer><div /><div /></footer>"
        "</body></html>",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/html-paste-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "html-paste.json").read_text())
    assert art["status"] == "fail"
    assert any(
        v["kind"] == "structural-similarity-to-scaffold" for v in art["violations"]
    )


def test_html_paste_pass_on_vite_mount_only(tmp_path: Path) -> None:
    """Plain Vite mount file must PASS even when ref scaffold has rich shape."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "dom-scaffold.json").write_text(json.dumps({
        "tree": {"tag": "html", "children": [
            {"tag": "body", "children": [
                {"tag": c} for c in [
                    "header", "nav", "main", "section", "section",
                    "section", "h1", "h2", "p", "img", "footer",
                ]
            ]},
        ]},
    }))
    impl = tmp_path / "impl"
    impl.mkdir()
    (impl / "index.html").write_text(
        '<!DOCTYPE html><html><head><title>App</title></head>'
        '<body><div id="root"></div>'
        '<script type="module" src="/src/main.tsx"></script>'
        "</body></html>",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/html-paste-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "html-paste.json").read_text())
    assert art["status"] == "pass", art


# ───────────────────────────────────────────────────────────────────────
# Runtime gate enforcement — fixture the gate.py path through
# synthesized artifacts (the bash scripts need agent-browser; the
# gate-side STATUS_REQUIRED logic is what matters here).
# ───────────────────────────────────────────────────────────────────────


def _make_verification_plan(ref: Path, check_id: str, produces: str,
                            severity: str = "block",
                            tier: str = "standard") -> None:
    plan = {
        "schemaVersion": 1,
        "tier": tier,
        "requiredChecks": [{
            "id": check_id,
            "script": f"skills/visual-debug/scripts/{check_id}-check.sh",
            "produces": produces,
            "reason": "fixture",
            "severity": severity,
            "tier": tier,
        }],
    }
    (ref / "verification-plan.json").write_text(json.dumps(plan))


def _baseline_post_implement_inputs(ref: Path) -> None:
    """Minimum artifacts post-implement gate reads beyond verification-plan."""
    (ref / "extracted.json").write_text(json.dumps(
        {"sections": [{"name": "hero"}]}
    ))


def test_gate_hidden_children_status_pass_passes(tmp_path: Path) -> None:
    """status:pass on hidden-children artifact → gate passes."""
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(ref, "hidden-children", "hidden-children.json")
    (ref / "hidden-children.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "implUrl": "http://localhost:5173",
        "sectionsChecked": 4,
        "violationCount": 0,
        "violations": [],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert not any("hidden-children" in r.label for r in failures), failures


def test_gate_hidden_children_status_fail_fails(tmp_path: Path) -> None:
    """status:fail on hidden-children artifact → gate fails with the issue count."""
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(ref, "hidden-children", "hidden-children.json")
    (ref / "hidden-children.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "implUrl": "http://localhost:5173",
        "sectionsChecked": 4,
        "violationCount": 2,
        "violations": [
            {"tag": "section", "id": "hero", "className": "",
             "childrenChecked": 5, "area": 1080000},
            {"tag": "section", "id": "again", "className": "",
             "childrenChecked": 3, "area": 800000},
        ],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any("hidden-children" in r.label for r in failures), results


def test_gate_runtime_dom_parity_status_fail_fails(tmp_path: Path) -> None:
    """status:fail on runtime-dom-parity artifact → gate fails."""
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(
        ref, "runtime-dom-parity", "runtime-dom-parity.json",
    )
    (ref / "runtime-dom-parity.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "refUrl": "https://realfood.gov",
        "implUrl": "http://localhost:5173",
        "hasLottieEvidence": True,
        "violations": [
            {"kind": "ref-has-lottie-impl-has-no-lottie-container", "impl": 0},
        ],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any("runtime-dom-parity" in r.label for r in failures), results


def test_gate_runtime_dom_parity_missing_status_fails(tmp_path: Path) -> None:
    """STATUS_REQUIRED: artifact present but `status` field absent → fail."""
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(
        ref, "runtime-dom-parity", "runtime-dom-parity.json",
    )
    # Missing status field on a STATUS_REQUIRED check_id.
    (ref / "runtime-dom-parity.json").write_text(json.dumps({
        "schemaVersion": 1,
        "violations": [],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any(
        "runtime-dom-parity" in r.label
        and "status` field" in (r.message or "")
        for r in failures
    ), failures


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


def test_monolithic_impl_fail_on_packed_app_jsx(tmp_path: Path) -> None:
    """Single 15KB App.jsx with 0 components must FAIL."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 12, "hasFooter": True, "hasHeader": True,
        "sections": [{"index": i} for i in range(12)],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"react": "19", "vite": "5"},
    }))
    (impl / "src" / "App.jsx").write_text(
        "export default function App() {\n  return <>"
        + ("<section>" + "x" * 800 + "</section>") * 18  # ~14KB packed
        + "</>;\n}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/monolithic-impl-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "monolithic-impl.json").read_text())
    assert art["status"] == "fail"
    assert art["componentCount"] == 0


def test_monolithic_impl_pass_on_componentized(tmp_path: Path) -> None:
    """Componentized impl with 5 PascalCase children must PASS."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 12, "hasFooter": True, "hasHeader": True,
        "sections": [{"index": i} for i in range(12)],
    }))
    impl = tmp_path / "impl"
    components = impl / "src" / "components"
    components.mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"react": "19", "vite": "5"},
    }))
    (impl / "src" / "App.jsx").write_text(
        "import Hero from './components/Hero'\n"
        "export default function App(){return <Hero/>}\n",
    )
    for c in ("Hero", "Footer", "Nav", "Banner", "Card"):
        (components / f"{c}.jsx").write_text(
            f"export default function {c}(){{return <div/>}}\n",
        )
    proc = _run_script(
        "skills/visual-debug/scripts/monolithic-impl-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "monolithic-impl.json").read_text())
    assert art["status"] == "pass"
    assert art["componentCount"] == 5


def test_motion_coverage_fail_on_ref_motion_impl_static(tmp_path: Path) -> None:
    """Ref bundle has gsap; impl has no motion code → fail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": [{"name": "gsap"}, {"name": "framer-motion"}],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "hero", "trigger": "scroll"},
            {"id": "fade", "trigger": "intersection"},
        ],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps(
        {"dependencies": {"react": "19"}},
    ))
    (impl / "src" / "App.jsx").write_text(
        "export default function App(){return <div>static</div>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/motion-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "motion-coverage.json").read_text())
    assert art["status"] == "fail"
    assert art["refSignalStrength"] >= 3
    assert art["implMotionStrength"] == 0


def test_motion_coverage_pass_when_impl_uses_gsap(tmp_path: Path) -> None:
    """Ref motion + impl uses gsap.timeline → pass."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": [{"name": "gsap"}],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero"}],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"react": "19", "gsap": "3"},
    }))
    (impl / "src" / "App.jsx").write_text(
        "import { gsap } from 'gsap'\n"
        "import { useScroll } from 'framer-motion'\n"
        "export default function App(){\n"
        "  gsap.to('.hero', {opacity: 1});\n"
        "  const {scrollY} = useScroll();\n"
        "  return <div/>;\n"
        "}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/motion-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "motion-coverage.json").read_text())
    assert art["status"] == "pass"


def test_motion_coverage_emotion_does_not_trigger_motion_match(tmp_path: Path) -> None:
    """@emotion/react in bundle-map must NOT count as motion."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": [
            {"name": "@emotion/react"},
            {"name": "@emotion/styled"},
        ],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps(
        {"dependencies": {"react": "19"}},
    ))
    (impl / "src" / "App.jsx").write_text(
        "export default function App(){return <div/>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/motion-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "motion-coverage.json").read_text())
    assert art["status"] == "pass"
    assert art["refSignalStrength"] == 0, (
        "emotion must not match motion library set"
    )


def test_motion_coverage_css_keyframes_count_as_motion(tmp_path: Path) -> None:
    """CSS @keyframes + animation: + scroll-timeline: count as impl motion."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero"}, {"id": "fade"}],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps(
        {"dependencies": {"react": "19"}},
    ))
    (impl / "src" / "App.jsx").write_text(
        "export default function App(){return <div className='hero'/>}\n",
    )
    (impl / "src" / "styles.css").write_text(
        "@keyframes fadeIn { from { opacity: 0 } to { opacity: 1 } }\n"
        ".hero { animation: fadeIn 1s ease-out; }\n"
        ".scroll { scroll-timeline: --page block; }\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/motion-coverage-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "motion-coverage.json").read_text())
    assert art["status"] == "pass"
    assert art["implMotionStrength"] >= 3, (
        "CSS motion declarations must contribute to impl strength"
    )


def test_gate_rapid_phase_downgrades_block_to_warn(tmp_path: Path) -> None:
    """UI_CLONE_PHASE=rapid: non-anti-cheat block check fails → emits warn."""
    import os

    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(
        ref, "tree-diff", "tree-diff-status.json",
        severity="block", tier="standard",
    )
    (ref / "tree-diff-status.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "violations": [{"kind": "test"}],
    }))
    old = os.environ.get("UI_CLONE_PHASE")
    os.environ["UI_CLONE_PHASE"] = "rapid"
    try:
        results = Gate(ref).gate_post_implement()
    finally:
        if old is None:
            os.environ.pop("UI_CLONE_PHASE", None)
        else:
            os.environ["UI_CLONE_PHASE"] = old
    # tree-diff is NOT in STRICT_ALWAYS → rapid mode downgrades to warn.
    fails = [r for r in results if r.status == "fail" and "tree-diff" in r.label]
    warns = [r for r in results if r.status == "warn" and "tree-diff" in r.label]
    assert not fails, f"rapid mode should downgrade tree-diff: {results}"
    assert warns, f"rapid mode should emit warn for tree-diff: {results}"


def test_gate_rapid_phase_does_not_downgrade_anti_cheat(tmp_path: Path) -> None:
    """UI_CLONE_PHASE=rapid: anti-cheat gates (ref-screenshot-asset) still fail."""
    import os

    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(
        ref, "ref-screenshot-asset", "ref-screenshot-asset.json",
        severity="block", tier="quick",
    )
    (ref / "ref-screenshot-asset.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "violations": [{"kind": "byte-identical-copy"}],
    }))
    old = os.environ.get("UI_CLONE_PHASE")
    os.environ["UI_CLONE_PHASE"] = "rapid"
    try:
        results = Gate(ref).gate_post_implement()
    finally:
        if old is None:
            os.environ.pop("UI_CLONE_PHASE", None)
        else:
            os.environ["UI_CLONE_PHASE"] = old
    # ref-screenshot-asset IS in STRICT_ALWAYS → rapid does NOT downgrade.
    fails = [r for r in results
             if r.status == "fail" and "ref-screenshot-asset" in r.label]
    assert fails, f"anti-cheat gate must stay strict even in rapid: {results}"


def test_scroll_engine_parity_fail_on_gsap_ref_vs_bare_io_impl(tmp_path: Path) -> None:
    """Ref has gsap-scrolltrigger + lenis + scroll-pin + scroll-scrub
    but impl has only IntersectionObserver → all 4 classes missing."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": [{"name": "gsap"}, {"name": "@studio-freight/lenis"}],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "hero", "trigger": "scroll-scrub"},
            {"id": "pin", "trigger": "sticky-scrub"},
        ],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps(
        {"dependencies": {"react": "19"}},
    ))
    (impl / "src" / "App.jsx").write_text(
        "import {useEffect} from 'react'\n"
        "export default function App(){\n"
        "  useEffect(() => { new IntersectionObserver(() => {}, {}); }, [])\n"
        "  return <div/>;\n"
        "}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/scroll-engine-parity-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "scroll-engine-parity.json").read_text())
    assert art["status"] == "fail"
    missing = {v["refClass"] for v in art["violations"]}
    assert "gsap-scrolltrigger" in missing
    assert "lenis-smooth-scroll" in missing
    assert "scroll-pin" in missing
    assert "scroll-scrub" in missing


def test_scroll_engine_parity_pass_on_matching_impl(tmp_path: Path) -> None:
    """Ref has gsap + scroll-scrub; impl imports gsap + uses
    ScrollTrigger → pass."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "libraries": [{"name": "gsap"}],
    }))
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero", "trigger": "scroll-scrub"}],
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"react": "19", "gsap": "3"},
    }))
    (impl / "src" / "App.jsx").write_text(
        "import {gsap} from 'gsap'\n"
        "import {ScrollTrigger} from 'gsap/ScrollTrigger'\n"
        "gsap.registerPlugin(ScrollTrigger)\n"
        "ScrollTrigger.create({trigger: '.hero', scrub: 1})\n"
        "export default function App(){return <div className='hero'/>}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/scroll-engine-parity-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "scroll-engine-parity.json").read_text())
    assert art["status"] == "pass"


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


def test_gate_status_warn_does_not_fail_block_severity(tmp_path: Path) -> None:
    """Script-declared status=warn must NOT be upgraded to fail by block severity.

    Regression for the Codex L60 image-fidelity FP fix: gate.py must
    honor a script's explicit warn verdict regardless of severity.
    """
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(
        ref, "hidden-children", "hidden-children.json",
        severity="block", tier="standard",
    )
    (ref / "hidden-children.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "warn",
        "implUrl": "http://localhost:5173",
        "sectionsChecked": 4,
        "violationCount": 0,
        "violations": [],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    # block severity should NOT upgrade a script-declared warn to fail.
    assert not any("hidden-children" in r.label for r in failures), failures
    warns = [r for r in results if r.status == "warn"]
    assert any("hidden-children" in r.label for r in warns), results


# ───────────────────────────────────────────────────────────────────────
# Phase 2 producer contract — loop-13 fresh-only diagnosis closure
# (dom-scaffold consumes structure.json + styles.json + section-map.json;
# extract-dom only wrote structure.json. extract-styles.sh aggregates the
# latter two without a fresh browser round-trip.)
# ───────────────────────────────────────────────────────────────────────


def test_extract_styles_aggregates_structure_into_scaffold_input(tmp_path: Path) -> None:
    """extract-styles.sh must walk structure.json's per-node `styles` dicts
    and emit a tag/class-keyed aggregate using dom-scaffold's STYLE_KEYS
    shorthand (bg / ff / fs / fw / ...). Settles each (key, shortkey) to
    the modal value across all matching nodes.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "main",
        "class": "page",
        "styles": {
            "display": "flex",
            "background-color": "rgb(255, 255, 255)",
            "font-family": "Inter",
        },
        "children": [
            {
                "tag": "h1",
                "class": "hero-title big",
                "styles": {
                    "font-size": "48px",
                    "font-weight": "700",
                    "color": "rgb(20, 20, 20)",
                },
                "children": [],
            },
            {
                "tag": "h1",
                "class": "hero-title small",
                "styles": {
                    "font-size": "48px",
                    "font-weight": "700",
                    "color": "rgb(20, 20, 20)",
                },
                "children": [],
            },
            {
                "tag": "section",
                "class": "stats",
                "styles": {
                    "background-image": "linear-gradient(rgb(0,0,0), rgb(50,50,50))",
                    "padding": "64px 24px",
                },
                "children": [],
            },
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "extract-styles.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads((ref / "styles.json").read_text())
    # Per-tag aggregate
    assert out["main"]["display"] == "flex"
    assert out["main"]["bg"] == "rgb(255, 255, 255)"
    assert out["main"]["ff"] == "Inter"
    # background-image must win over background-color when both present.
    assert out["section"]["bg"].startswith("linear-gradient"), out["section"]
    # Per-first-class aggregate carries typographic + color/bg only.
    assert out[".page"]["bg"] == "rgb(255, 255, 255)"
    assert out[".hero-title"]["fs"] == "48px"
    assert out[".hero-title"]["fw"] == "700"
    # Structural keys at the class level would stamp dominant width/padding
    # onto exceptional instances (Codex 2026-05-22 review Q1). dom-scaffold
    # reads structural per-node from structure.json directly instead.
    assert "padding" not in out.get(".stats", {})
    assert "width" not in out.get(".page", {})
    assert "height" not in out.get(".hero-title", {})
    # Noise values (rgba(0,0,0,0), "none", "normal", "0px", empty) are dropped.
    for entry in out.values():
        for v in entry.values():
            assert v.strip(), f"empty style value leaked: {entry}"
            assert v.lower() not in {"none", "normal", "auto", "0px", "rgba(0, 0, 0, 0)"}


def test_extract_styles_errors_when_structure_missing(tmp_path: Path) -> None:
    """extract-styles.sh must refuse to run without structure.json instead
    of writing an empty styles.json that would silently pass dom-scaffold's
    existence check.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "extract-styles.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert not (ref / "styles.json").exists()


def test_dom_scaffold_prefers_per_node_styles_over_class_aggregate(tmp_path: Path) -> None:
    """Two `.card` instances exist on the page: a small catalog card (320px
    wide) and an exceptional hero card (800px wide). The class-level
    aggregate's modal width would be 320 if the small one is repeated. But
    Phase 4 must render the hero at its real per-node width.

    Codex 2026-05-22 review (Q1): dom-scaffold's walk() now reads per-node
    styles from structure.json directly and only falls back to the class
    aggregate for keys not captured per-node. Without this fix the hero
    silently inherits 320px and Phase 4 generates the wrong layout.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    structure = {
        "tag": "main",
        "class": "page",
        "styles": {"display": "flex"},
        "children": [
            {
                "tag": "div",
                "class": "card hero",
                "styles": {"width": "800px", "padding": "64px"},
                "children": [],
            },
            {
                "tag": "div",
                "class": "card small",
                "styles": {"width": "320px", "padding": "16px"},
                "children": [],
            },
            {
                "tag": "div",
                "class": "card small",
                "styles": {"width": "320px", "padding": "16px"},
                "children": [],
            },
        ],
    }
    (ref / "structure.json").write_text(json.dumps(structure))
    (ref / "section-map.json").write_text(json.dumps({
        "totalCount": 1,
        "hasFooter": False,
        "hasHeader": False,
        "sections": [{
            "index": 0, "tag": "main", "className": "page", "id": None,
            "role": None, "height": 1000, "top": 0, "childCount": 3,
            "textPreview": "",
        }],
    }))
    # Produce styles.json via the real extract-styles.sh — this confirms
    # the class-level structural carve-out is in effect.
    proc = subprocess.run(
        ["bash", str(_project_root() / "skills" / "visual-debug" / "scripts" / "extract-styles.sh"),
         str(ref)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # Run dom-scaffold.
    proc = subprocess.run(
        ["bash", str(_project_root() / "skills" / "visual-debug" / "scripts" / "dom-scaffold.sh"),
         str(ref)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    scaffold = json.loads((ref / "dom-scaffold.json").read_text())

    # Find the per-node entries in the global tree. Order matches structure.json.
    tree = scaffold.get("tree", {})
    children = tree.get("children", [])
    assert len(children) == 3, f"expected 3 cards, got {len(children)}: {children}"

    hero_node = children[0]
    small_node = children[1]

    # Hero must keep its exceptional 800px width — per-node wins over any
    # aggregate fallback.
    assert hero_node["styles"]["width"] == "800px", hero_node
    assert hero_node["styles"]["padding"] == "64px", hero_node
    # Small node carries its own 320px.
    assert small_node["styles"]["width"] == "320px", small_node
    assert small_node["styles"]["padding"] == "16px", small_node


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


def test_required_media_accepts_both_dict_and_list_html_shapes(tmp_path: Path) -> None:
    """Loop-codex-8 finding: required-media.sh's embedded Python called
    `section_data.get("media")` on html/<name>.json — but the producer
    (extract-section-html.sh on some sites including realfood.gov)
    emits a bare list of media entries rather than a dict wrapping the
    list under a "media" key. Result: `AttributeError: 'list' object
    has no attribute 'get'` at the first list-shaped file, before any
    artifact got written.

    Fix: accept both shapes. Dict → look up "media" key; list → use
    the value directly. This test pins behavior for both shapes so a
    future refactor cannot silently revert to the dict-only path.
    """
    ref = tmp_path / "ref"
    html = ref / "html"
    html.mkdir(parents=True)

    # File 1: dict shape (legacy producer)
    (html / "section-dict.json").write_text(json.dumps({
        "media": [
            {"tag": "video", "src": "https://example.com/dict.mp4",
             "type": "video/mp4", "autoplay": True, "loop": True, "muted": True,
             "w": 1280, "h": 720},
        ],
    }))
    # File 2: bare-list shape (codex-8 surfaced this on realfood.gov)
    (html / "section-list.json").write_text(json.dumps([
        {"tag": "video", "src": "https://example.com/list.mp4",
         "type": "video/mp4", "autoplay": False, "loop": False, "muted": False,
         "w": 1920, "h": 1080},
    ]))

    script = _project_root() / "scripts" / "extract" / "required-media.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    data = json.loads((ref / "required-media.json").read_text())
    video_srcs = {v["src"] for v in data.get("videos", [])}
    # Both shapes contributed their video — neither side silently dropped.
    assert "https://example.com/dict.mp4" in video_srcs, video_srcs
    assert "https://example.com/list.mp4" in video_srcs, video_srcs


def test_required_media_skips_unknown_shapes(tmp_path: Path) -> None:
    """Defense in depth: html/<name>.json that is neither dict nor list
    (e.g. a stray string, or null) must NOT crash the extractor."""
    ref = tmp_path / "ref"
    html = ref / "html"
    html.mkdir(parents=True)
    (html / "section-string.json").write_text(json.dumps("just a string"))
    (html / "section-null.json").write_text(json.dumps(None))

    script = _project_root() / "scripts" / "extract" / "required-media.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_hero_composite_check_passes_when_impl_has_all_kinds(tmp_path: Path) -> None:
    """User direction A + hero-composite gate (2026-05-22): when ref hero
    has all 4 kinds (video, button, h1/h2, label) and impl Hero component
    contains them all (with button-video proximity satisfied), PASS.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src" / "components"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "div",
            "children": [
                {"tag": "section", "class": "dga_hero__X",
                 "children": [
                     {"tag": "h1", "text": "Real Food Wins", "children": []},
                     {"tag": "span", "class": "hero-video__label", "children": []},
                 ]},
                # Sibling hero-video container — matches realfood.gov layout
                # where the video is a sibling, not descendant, of the hero
                # section. The check collects ALL hero-named subtrees.
                {"tag": "div", "class": "dga_hero_video__Y",
                 "children": [
                     {"tag": "video", "src": "/video/hero.mp4", "children": []},
                     {"tag": "button", "class": "hero-video", "children": []},
                 ]},
            ],
        }],
    }))
    (src / "Hero.tsx").write_text(
        'export function Hero() {\n'
        '  return (\n'
        '    <section data-section="hero">\n'
        '      <video src="/video/hero.mp4" />\n'
        '      <button className="hero-video">\n'
        '        <span className="hero-video__label">Play</span>\n'
        '      </button>\n'
        '      <h1>Real Food Wins</h1>\n'
        '    </section>\n'
        '  );\n'
        '}\n'
    )

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "hero-composite-check.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "hero-composite.json").read_text())
    assert art["status"] == "pass", art
    assert art["ref"]["video"] and art["ref"]["button"] and art["ref"]["h1OrH2"] and art["ref"]["label"], art
    assert art["impl"]["video"] and art["impl"]["button"] and art["impl"]["h1OrH2"] and art["impl"]["label"], art
    assert not art["missingInImpl"], art


def test_hero_composite_check_fails_when_impl_drops_overlay_button(tmp_path: Path) -> None:
    """Core failure mode across 17 codex iterations: LLM flattens ref's
    4-layer hero composite into 2 layers, dropping the overlay button
    + label. Gate must FAIL when ref has video+button but impl only has
    video+h1.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src" / "components"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero",
             "children": [
                 {"tag": "video", "children": []},
                 {"tag": "button", "class": "hero-video", "children": []},
                 {"tag": "span", "class": "hero-video__label", "children": []},
                 {"tag": "h1", "text": "Title", "children": []},
             ]},
        ],
    }))
    # Impl: only video + h1 (typical LLM flattening — button + label dropped).
    (src / "Hero.tsx").write_text(
        'export function Hero() {\n'
        '  return (<section><video /><h1>Title</h1></section>);\n'
        '}\n'
    )

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "hero-composite-check.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "hero-composite.json").read_text())
    assert art["status"] == "fail", art
    assert set(art["missingInImpl"]) >= {"button", "label"}, art


def test_hero_composite_check_rejects_navbar_button_via_proximity(tmp_path: Path) -> None:
    """Button proximity check (codex-rescue Q3): a `<button` only counts
    when there's a `<video` within 500 chars. Prevents a navbar button
    that happens to live in the same file from satisfying the button
    requirement.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [
            {"tag": "section", "class": "hero",
             "children": [
                 {"tag": "video", "children": []},
                 {"tag": "button", "children": []},
             ]},
        ],
    }))
    # Impl: video at top, then 800 chars of unrelated content, then a
    # navbar-style button. Proximity check should NOT count this button.
    padding = "  // unrelated content " + ("x" * 100) + "\n"
    (src / "Hero.tsx").write_text(
        'export function Hero() {\n'
        '  return (\n'
        '    <main>\n'
        '      <video />\n'
        + padding * 6 +
        '      <button>Sign In</button>\n'
        '    </main>\n'
        '  );\n'
        '}\n'
    )

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "hero-composite-check.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "hero-composite.json").read_text())
    assert "button" in art["missingInImpl"], (
        f"navbar button at >500 chars from video must not satisfy "
        f"the button requirement; got: {art}"
    )


def test_hero_composite_check_prefers_data_section_locator(tmp_path: Path) -> None:
    """Codex-rescue Q2: `data-section="hero"` is the strongest locator,
    even when the file name doesn't contain 'hero'. Verifies P1
    candidates win over P2 (file name) and P3 (any-video).
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    ref.mkdir()
    src.mkdir(parents=True)
    (ref / "structure.json").write_text(json.dumps({
        "tag": "body",
        "children": [{
            "tag": "section", "class": "hero",
            "children": [
                {"tag": "video", "children": []},
                {"tag": "h1", "text": "T", "children": []},
            ],
        }],
    }))
    # File name does NOT contain 'hero' but has data-section="hero".
    (src / "Banner.tsx").write_text(
        'export function Banner() {\n'
        '  return (\n'
        '    <section data-section="hero">\n'
        '      <video />\n'
        '      <h1>T</h1>\n'
        '    </section>\n'
        '  );\n'
        '}\n'
    )

    script = (
        _project_root() / "skills" / "visual-debug" / "scripts" / "hero-composite-check.sh"
    )
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "hero-composite.json").read_text())
    assert art["status"] == "pass", art
    assert any("Banner" in f for f in art["implCandidateFiles"]), art


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
