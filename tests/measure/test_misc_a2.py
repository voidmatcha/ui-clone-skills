from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from ._helpers import (
    _project_root,
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


_COMPLETION_REQUIRED_STATUS_ARTIFACTS = [
    "image-fidelity.json",
    "svg-dom-parity.json",
    "required-media-coverage.json",
    "hero-composite.json",
    "svg-provenance.json",
    "color-token-grounding.json",
    "ref-js-loader.json",
    "proxy-mirror.json",
    "html-paste.json",
    "ref-screenshot-asset.json",
    "impl-scope.json",
    "runtime-env.json",
]


def _write_completion_report_green_artifacts(
    ref: Path, *, current_gate: str, section_result: str
) -> None:
    (ref / "sections").mkdir(parents=True)
    (ref / "pipeline-state.json").write_text(
        json.dumps({"current_gate": current_gate, "completed_steps": []}),
        encoding="utf-8",
    )
    (ref / "runtime-proof.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    (ref / "transition-proof.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    (ref / "font-parity.json").write_text(json.dumps({"parity": "match"}), encoding="utf-8")
    for name in _COMPLETION_REQUIRED_STATUS_ARTIFACTS:
        (ref / name).write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    (ref / "sections" / "result.txt").write_text(section_result, encoding="utf-8")


def test_completion_report_check_mode_fails_dirty_closeout(tmp_path: Path) -> None:
    """`--check` makes the closeout report usable as an unattended loop gate."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    _write_completion_report_green_artifacts(
        ref,
        current_gate="reference",
        section_result=(
            "| Section | AE | Status |\n"
            "|---------|----|--------|\n"
            "| hero | 999 | ❌ |\n"
        ),
    )

    script = _project_root() / "scripts" / "verify" / "completion-report.sh"
    proc = subprocess.run(
        ["bash", str(script), "--check", str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1
    assert "current_gate is 'reference', not 'done'" in proc.stdout
    assert "section-compare dirty" in proc.stdout


def test_completion_report_check_mode_passes_green_closeout(tmp_path: Path) -> None:
    """Green closeout requires current_gate done, clean section rows, and proofs."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    _write_completion_report_green_artifacts(
        ref,
        current_gate="done",
        section_result=(
            "| Section | AE | Status |\n"
            "|---------|----|--------|\n"
            "| hero | 0 | ✅ |\n"
        ),
    )

    script = _project_root() / "scripts" / "verify" / "completion-report.sh"
    proc = subprocess.run(
        ["bash", str(script), "--check", str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stdout
    assert "all green per Hard Done Criteria" in proc.stdout


def test_completion_report_surfaces_broad_structural_only_advisory(tmp_path: Path) -> None:
    """A clean section summary can still hide skipped pixel AE rows.

    The report should make broad STRUCTURAL_ONLY coverage visible instead of
    summarizing it as an unqualified static visual pass.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (ref / "sections").mkdir(parents=True)
    impl.mkdir()
    (ref / "sections" / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---------|-----|--------|----------|--------|\n"
        "| section-0 | 0 | 0 | ok | ✅ |\n"
        "| section-1 | 0 | 0 | ok | ✅ |\n"
        "| section-2 | 0 | 0 | ok | ✅ |\n"
        "| section-3 | 0 | 0 | ok | ✅ |\n"
        "| section-4 | 0 | 0 | ok | ✅ |\n"
        "| section-5 | 0 | 0 | ok | ✅ |\n"
        "| section-6 | 0 | 0 | ok | ✅ |\n"
        "| section-7 | 0 | 0 | structural | 🔁 STRUCTURAL_ONLY |\n"
        "| section-8 | 0 | 0 | structural | 🔁 STRUCTURAL_ONLY |\n"
        "| section-9 | 0 | 0 | structural | 🔁 STRUCTURAL_ONLY |\n"
        "\n"
        "**Result: 7 PASS, 0 FAIL, 0 SKIP, 3 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )

    script = _project_root() / "scripts" / "verify" / "completion-report.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15,
    )

    assert proc.returncode == 0
    assert "section-compare: ✓ 10 pass / 0 fail" in proc.stdout
    assert "STRUCTURAL_ONLY coverage broad" in proc.stdout
    assert "pixel AE polishing skipped" in proc.stdout



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


def _init_impl_scope_git_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "ui_clone").mkdir()
    (repo / "ui_clone" / "gate.py").write_text("BASELINE = True\n", encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)
    ref = repo / "tmp" / "ref" / "site"
    impl = repo / "impl"
    ref.mkdir(parents=True)
    impl.mkdir()
    return repo, ref, impl


def test_impl_scope_check_ignores_unchanged_preexisting_parent_wip(tmp_path: Path) -> None:
    """Pre-existing dirty parent-repo work should not force clone agents
    to ask the user whether to stash/revert unrelated WIP.
    """
    repo, ref, impl = _init_impl_scope_git_repo(tmp_path)
    dirty_file = repo / "ui_clone" / "gate.py"
    dirty_file.write_text("BASELINE = False\n", encoding="utf-8")
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "impl-scope-check.sh"

    first = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert first.returncode == 0, f"baseline init must pass: {first.stdout}\n{first.stderr}"

    second = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert second.returncode == 0, (
        "unchanged pre-existing WIP must not fail impl-scope:\n"
        f"{second.stdout}\n{second.stderr}"
    )
    artifact = json.loads((ref / "impl-scope.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"
    assert any(
        row["path"] == "ui_clone/gate.py"
        and row["reason"] == "pre-existing-dirty-baseline"
        for row in artifact["allowed"]
    )



def test_impl_scope_check_expands_unchanged_untracked_directory_baseline(tmp_path: Path) -> None:
    """Untracked directory baselines must compare against later file-expanded diffs."""
    repo, ref, impl = _init_impl_scope_git_repo(tmp_path)
    bin_dir = repo / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "ui-clone"
    cli.write_text("#!/usr/bin/env node\nconsole.log('ui-clone')\n", encoding="utf-8")

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "impl-scope-check.sh"
    first = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert first.returncode == 0, f"baseline init must pass: {first.stdout}\n{first.stderr}"

    second = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert second.returncode == 0, (
        "unchanged untracked directory contents must not fail impl-scope:\n"
        f"{second.stdout}\n{second.stderr}"
    )
    artifact = json.loads((ref / "impl-scope.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"
    assert any(
        row["path"] == "bin/ui-clone" and row["reason"] == "pre-existing-dirty-baseline"
        for row in artifact["allowed"]
    )

def test_impl_scope_check_flags_preexisting_wip_modified_after_baseline(tmp_path: Path) -> None:
    """The baseline-dirty exemption is content-based, not a broad path
    whitelist. If the clone iteration edits that file further, fail.
    """
    repo, ref, impl = _init_impl_scope_git_repo(tmp_path)
    dirty_file = repo / "ui_clone" / "gate.py"
    dirty_file.write_text("BASELINE = False\n", encoding="utf-8")
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "impl-scope-check.sh"

    first = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert first.returncode == 0, f"baseline init must pass: {first.stdout}\n{first.stderr}"

    dirty_file.write_text("BASELINE = 'changed during iteration'\n", encoding="utf-8")
    second = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert second.returncode == 1
    artifact = json.loads((ref / "impl-scope.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "fail"
    assert any(row["path"] == "ui_clone/gate.py" for row in artifact["violations"])



def test_impl_scope_check_dispatcher_wired() -> None:
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "build_required_dispatch.py"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"impl-scope-check\.sh":\s*"([^"]+)"', text)
    assert m, "impl-scope-check.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{ref_dir}" in recipe and "{impl_root}" in recipe, (
        f"impl-scope recipe must include both ref_dir and impl_root (got: {recipe!r})"
    )



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
    dispatcher = _project_root() / "scripts" / "verify" / "build_required_dispatch.py"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"runtime-env-check\.sh":\s*"([^"]+)"', text)
    assert m, "runtime-env-check.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{ref_dir}" in recipe and "{impl_root}" in recipe and "{impl_url}" in recipe, (
        f"runtime-env recipe must include {{ref_dir}}, {{impl_root}}, {{impl_url}} "
        f"(got: {recipe!r})"
    )



def test_ref_js_loader_allows_shared_cdn_hosts(tmp_path: Path) -> None:
    """2026-05-22 codex-rescue meta-review (ac93f1e7): shared CDN hosts
    (Google Fonts, jsDelivr, etc.) appearing in ref artifacts must NOT
    flag the impl when impl also uses them legitimately. The cheat
    target is impl-loads-ref-OWNED-bundle, not impl-loads-shared-CDN.
    """
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



# ── Canvas-replay allowlist for ref-js-loader (v0.7.0) ──────────────────
# When closeoutPolicy=canvas-replay AND canvas-replay-attestation.json is
# signed, URLs listed in attestation.ref_canvas_sources[] are exact-string
# allowlisted. Other ref-bundle imports still FAIL. Allowlist is gated on
# both policy field AND attestation file (codex Q3: avoid origin/glob —
# exact URL equality keeps the gate effective for un-attested URLs).


def _write_canvas_replay_attestation(
    ref: Path,
    sources: list[str],
    policy: str = "canvas-replay",
) -> None:
    (ref / "pipeline-state.json").write_text(
        json.dumps({"component": ref.name, "closeoutPolicy": policy}),
        encoding="utf-8",
    )
    (ref / "canvas-replay-attestation.json").write_text(
        json.dumps({
            "license": "MIT — test fixture",
            "disclaimer": "test",
            "attestedBy": "operator",
            "attestedAt": "2026-05-25T08:00:00Z",
            "ref_canvas_sources": sources,
        }),
        encoding="utf-8",
    )


def test_ref_js_loader_allows_attested_canvas_source(tmp_path: Path) -> None:
    """v0.7.0 — impl loading the attested canvas-driver URL passes."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "head.json").write_text(json.dumps({
        "url": "https://canvas.example.org/",
    }))
    _write_canvas_replay_attestation(ref, sources=[
        "https://canvas.example.org/_canvas/driver.js",
    ])
    (impl / "src" / "CanvasLoader.tsx").write_text(
        "// Canvas-replay: attested ref bundle is required for visual fidelity\n"
        "import driver from 'https://canvas.example.org/_canvas/driver.js';\n"
        "export default function Canvas() { return driver.mount(); }\n"
    )
    (impl / "package.json").write_text(json.dumps({"name": "impl"}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "ref-js-loader-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, (
        f"attested canvas source must pass: {proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "ref-js-loader.json").read_text())
    assert artifact["status"] == "pass"


def test_ref_js_loader_fails_on_non_attested_url_same_host(tmp_path: Path) -> None:
    """v0.7.0 boundary — attestation only allows the URLs it explicitly lists.
    Loading a DIFFERENT bundle from the same host still fails. This is the
    "exact URL equality, not origin" guarantee codex called out (Q3)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "head.json").write_text(json.dumps({
        "url": "https://canvas.example.org/",
    }))
    _write_canvas_replay_attestation(ref, sources=[
        "https://canvas.example.org/_canvas/driver.js",
    ])
    # Same host, DIFFERENT URL — vendor bundle, not the attested canvas driver.
    (impl / "src" / "BadVendor.tsx").write_text(
        "// CHEAT: load a non-attested ref bundle, hide behind same host\n"
        "import vendor from 'https://canvas.example.org/_next/static/chunks/vendor.js';\n"
        "export default function Bad() { return vendor.run(); }\n"
    )
    (impl / "package.json").write_text(json.dumps({"name": "impl"}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "ref-js-loader-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 1, (
        f"non-attested URL on same host must STILL fail: {proc.stdout}\n{proc.stderr}"
    )
    artifact = json.loads((ref / "ref-js-loader.json").read_text())
    assert artifact["status"] == "fail"


def test_ref_js_loader_no_allowlist_without_attestation(tmp_path: Path) -> None:
    """Policy field set but operator forgot attestation → no allowlist."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "head.json").write_text(json.dumps({
        "url": "https://canvas.example.org/",
    }))
    # closeoutPolicy=canvas-replay BUT no canvas-replay-attestation.json
    (ref / "pipeline-state.json").write_text(
        json.dumps({"component": ref.name, "closeoutPolicy": "canvas-replay"}),
        encoding="utf-8",
    )
    (impl / "src" / "Canvas.tsx").write_text(
        "import driver from 'https://canvas.example.org/_canvas/driver.js';\n"
        "export default function Canvas() { return driver.mount(); }\n"
    )
    (impl / "package.json").write_text(json.dumps({"name": "impl"}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "ref-js-loader-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 1, (
        f"missing attestation must NOT activate allowlist: {proc.stdout}\n{proc.stderr}"
    )


def test_ref_js_loader_no_allowlist_when_policy_canonical(tmp_path: Path) -> None:
    """Default canonical policy: attestation file is ignored even if present."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    (ref / "head.json").write_text(json.dumps({
        "url": "https://canvas.example.org/",
    }))
    # closeoutPolicy=canonical with attestation present — must not allowlist
    _write_canvas_replay_attestation(
        ref,
        sources=["https://canvas.example.org/_canvas/driver.js"],
        policy="canonical",
    )
    (impl / "src" / "Canvas.tsx").write_text(
        "import driver from 'https://canvas.example.org/_canvas/driver.js';\n"
        "export default function Canvas() { return driver.mount(); }\n"
    )
    (impl / "package.json").write_text(json.dumps({"name": "impl"}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "ref-js-loader-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 1, (
        f"canonical policy must NOT activate allowlist: {proc.stdout}\n{proc.stderr}"
    )


def test_ref_js_loader_dispatcher_wired() -> None:
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "build_required_dispatch.py"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"ref-js-loader-check\.sh":\s*"([^"]+)"', text)
    assert m, "ref-js-loader-check.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{ref_dir}" in recipe and "{impl_root}" in recipe, (
        f"recipe must include {{ref_dir}} and {{impl_root}} (got: {recipe!r})"
    )



def test_hydration_check_fails_fatal_client_exceptions() -> None:
    """Hydration check must fail fatal client errors, not only text mismatches."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "hydration-check.sh"
    body = script.read_text(encoding="utf-8")
    assert "FATAL_PATTERNS" in body
    assert "fatalErrors" in body
    assert "crypto\\.randomUUID is not a function" in body
    assert "Minified React error #418" in body



def test_fix18_extract_dom_captures_pseudo_elements() -> None:
    """Fix 18 — extract-dom.sh must capture ::before / ::after computed
    styles + content per node so the transpiler can synthesize the
    pseudo-element layer that drives realfood.gov's glow rings, divider
    decorations, gradient overlays, etc. Without this the impl misses an
    entire visual layer — dominant cause of the "전체 레이아웃 못 잡는다"
    feedback after V15.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lib" / "extract-dom.js"
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
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "lib"
        / "scaffold_to_jsx.py"
    )
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
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "lib"
        / "scaffold_to_jsx.py"
    )
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
    assert re.search(
        r"find_subtree_for_section\(\s*structure,\s*sec,\s*consumed\b",
        body,
    ), (
        "section loop must pass consumed into find_subtree_for_section (Fix 16b; "
        "formatting may keep the call on one line or split it across lines)"
    )



def test_fix15_scaffold_to_jsx_emits_page_tsx() -> None:
    """Fix 15 — scaffold-to-jsx.sh must also emit impl/src/app/page.tsx that
    composes the generated section components. V11 (220c969) showed 3 sections
    (hero/lineInTheSand/stats) stuck at ~900k AE because agent-written
    page.tsx wrapped components incorrectly; transpiler-generated page.tsx
    eliminates that wiring drift by mirroring the ref structure root.
    """
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "lib"
        / "scaffold_to_jsx.py"
    )
    body = script.read_text(encoding="utf-8")
    assert 'page.tsx' in body, "scaffold-to-jsx.sh must write page.tsx (Fix 15)"
    # Mirrors structure.json root tag (not hardcoded to <main>).
    assert 'root_tag' in body, "page.tsx must use structure.json root tag dynamically"
    # Dedup component names — collisions across sections common when
    # ref has repeated class names (e.g., 4× dga_section).
    assert "seen_names" in body, "must dedup component names for unique imports"



def test_fix13_scaffold_to_jsx_script_present() -> None:
    """Fix 13 — scaffold-to-jsx.sh is the deterministic transpiler that
    replaces the LLM-interpretation step in Phase 4. Locks the script + its
    invocation contract.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"
    assert script.is_file(), "scaffold-to-jsx.sh missing — Fix 13 incomplete"
    helper = script.parent / "lib" / "scaffold_to_jsx.py"
    assert helper.is_file(), "scaffold_to_jsx.py helper missing — Fix 13 incomplete"
    shell = script.read_text(encoding="utf-8")
    assert 'python3 "$SCRIPT_DIR/lib/scaffold_to_jsx.py"' in shell
    body = helper.read_text(encoding="utf-8")
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


# ── FIX 1: dom-scaffold degenerate-sections guard ────────────────────────
# dom-scaffold.sh silently emitted a scaffold with `sections: []` when it ran
# while section-map.json was incomplete (a timing race: section-map later had
# 9/7 sections but the scaffold the generator consumed had 0 → the generator
# freehanded per-section layout). Mirror the text-fidelity degenerate-scaffold
# guard: if structure.json has substantial content but section extraction
# yielded 0 usable sections, fail loud instead of emitting a degenerate
# scaffold. Env escape hatch DOM_SCAFFOLD_ALLOW_NO_SECTIONS=1 for genuinely
# section-less pages.


def _real_structure() -> dict:
    """A structure.json with substantial node content (well above the floor)."""
    leaves = [
        {"tag": "p", "text": f"paragraph {i}", "children": []}
        for i in range(12)
    ]
    return {
        "tag": "main",
        "class": "page",
        "children": [
            {"tag": "header", "class": "site-head", "children": [
                {"tag": "h1", "text": "Brand Headline", "children": []},
                {"tag": "nav", "children": [
                    {"tag": "a", "text": "Home", "children": []},
                    {"tag": "a", "text": "About", "children": []},
                ]},
            ]},
            {"tag": "section", "class": "body", "children": leaves},
        ],
    }


def test_dom_scaffold_guards_degenerate_zero_sections(tmp_path: Path) -> None:
    """structure.json has real nodes but section-map yields 0 usable sections
    → dom-scaffold must EXIT NON-ZERO with a guard message and NOT emit a
    silent degenerate scaffold the generator would freehand on."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(_real_structure()))
    (ref / "styles.json").write_text(json.dumps({}))
    # section-map extraction incomplete: zero usable sections.
    (ref / "section-map.json").write_text(json.dumps({"sections": []}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "dom-scaffold.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode != 0, (
        f"degenerate (0-section) scaffold over a substantial structure.json "
        f"must fail loud, got rc=0:\n{proc.stdout}\n{proc.stderr}"
    )
    msg = proc.stdout + proc.stderr
    assert "0 usable sections" in msg, f"guard message missing: {msg}"
    assert "extract-section-map" in msg, f"remediation hint missing: {msg}"
    # Fail-loud, do-not-emit: the degenerate scaffold must not be written.
    assert not (ref / "dom-scaffold.json").exists(), (
        "guard must NOT emit a degenerate dom-scaffold.json"
    )


def test_dom_scaffold_passes_with_healthy_section_map(tmp_path: Path) -> None:
    """A healthy section-map (>=1 usable section) must still pass."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(_real_structure()))
    (ref / "styles.json").write_text(json.dumps({}))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [
            {"index": 0, "tag": "header", "cls": "site-head", "top": 0, "height": 80},
            {"index": 1, "tag": "section", "cls": "body", "top": 80, "height": 600},
        ],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "dom-scaffold.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, f"healthy section-map must pass:\n{proc.stdout}\n{proc.stderr}"
    doc = json.loads((ref / "dom-scaffold.json").read_text())
    assert len(doc["sections"]) == 2


def test_dom_scaffold_escape_hatch_allows_zero_sections(tmp_path: Path) -> None:
    """DOM_SCAFFOLD_ALLOW_NO_SECTIONS=1 opts genuinely section-less pages out
    of the guard — exits 0 and emits the (empty-section) scaffold."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(_real_structure()))
    (ref / "styles.json").write_text(json.dumps({}))
    (ref / "section-map.json").write_text(json.dumps({"sections": []}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "dom-scaffold.sh"
    env = dict(**os.environ, DOM_SCAFFOLD_ALLOW_NO_SECTIONS="1")
    proc = subprocess.run(
        ["bash", str(script), str(ref)],
        capture_output=True, text=True, timeout=120, env=env,
    )

    assert proc.returncode == 0, f"escape hatch must pass:\n{proc.stdout}\n{proc.stderr}"
    doc = json.loads((ref / "dom-scaffold.json").read_text())
    assert doc["sections"] == []
