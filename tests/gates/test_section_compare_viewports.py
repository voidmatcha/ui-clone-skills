"""Multi-viewport section-compare enforcement.

A responsive site (detected-breakpoints.json present) verified at a single
viewport hides every breakpoint-specific defect: mobile-swap sections never
render, vw-sized layouts only reflow at other widths, and clamp() expressions
are exercised at exactly one point. verification-plan.json declares the
canonical viewport set (375/1280/1600/1920); section-compare.sh already
supports a VIEWPORTS fan-out that writes a multi-viewport result.txt. This
suite locks the ENFORCEMENT: the gate requires the fan-out evidence whenever
the site is responsive, and the dispatcher sets VIEWPORTS from the plan.
"""

import json
import os
import subprocess
from pathlib import Path

from ui_clone.gate import Gate

from ._helpers import _project_root

PLAN_VIEWPORTS = [
    {"w": 375, "h": 812, "label": "mobile"},
    {"w": 1280, "h": 800, "label": "laptop"},
    {"w": 1600, "h": 900, "label": "desktop-mid"},
    {"w": 1920, "h": 1080, "label": "desktop-large"},
]


def _responsive_ref(tmp_path: Path) -> Path:
    ref = tmp_path / "ref"
    (ref / "sections").mkdir(parents=True)
    (ref / "detected-breakpoints.json").write_text(
        json.dumps({"breakpoints": [768, 1024]})
    )
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": [], "viewports": PLAN_VIEWPORTS})
    )
    return ref


def test_gate_fails_single_viewport_result_on_responsive_site(tmp_path: Path) -> None:
    ref = _responsive_ref(tmp_path)
    (ref / "sections" / "result.txt").write_text(
        "| Hero | 12 | 10 | ok | ✅ |\n| Footer | 3 | 2 | ok | ✅ |\n"
    )
    results = Gate(ref).gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures, (
        "responsive site + plan viewports + single-viewport result.txt must fail"
    )
    combined = " ".join(f"{r.message} {r.fix}" for r in failures)
    assert "VIEWPORTS" in combined or "viewport" in combined.lower()


def test_gate_accepts_multi_viewport_result(tmp_path: Path) -> None:
    ref = _responsive_ref(tmp_path)
    (ref / "sections" / "result.txt").write_text(
        "# section-compare multi-viewport result\n"
        "viewports: 375x812,1280x800,1600x900,1920x1080\n\n"
        "viewport: 375x812\n| [375x812] Hero | 12 | 10 | ok | ✅ |\n[375x812] exit: 0\n"
        "viewport: 1280x800\n| [1280x800] Hero | 12 | 10 | ok | ✅ |\n[1280x800] exit: 0\n"
        "viewport: 1600x900\n| [1600x900] Hero | 12 | 10 | ok | ✅ |\n[1600x900] exit: 0\n"
        "viewport: 1920x1080\n| [1920x1080] Hero | 12 | 10 | ok | ✅ |\n[1920x1080] exit: 0\n"
    )
    results = Gate(ref).gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"complete multi-viewport result must pass: {failures}"


def test_gate_fails_viewport_with_exit0_but_no_rows(tmp_path: Path) -> None:
    """id8/40: the completion check counted section rows GLOBALLY, so a viewport
    block with `exit: 0` and ZERO measured rows passed as long as a SIBLING
    viewport had rows. Each present viewport must carry >=1 measured row of its
    own — an empty-but-exit-0 block certifies that viewport on no evidence."""
    ref = _responsive_ref(tmp_path)
    (ref / "sections" / "result.txt").write_text(
        "# section-compare multi-viewport result\n"
        "viewports: 375x812,1280x800,1600x900,1920x1080\n\n"
        "viewport: 375x812\n| [375x812] Hero | 12 | 10 | ok | ✅ |\n[375x812] exit: 0\n"
        "viewport: 1280x800\n| [1280x800] Hero | 12 | 10 | ok | ✅ |\n[1280x800] exit: 0\n"
        "viewport: 1600x900\n| [1600x900] Hero | 12 | 10 | ok | ✅ |\n[1600x900] exit: 0\n"
        "viewport: 1920x1080\n[1920x1080] exit: 0\n"  # exit 0 but ZERO measured rows
    )
    results = Gate(ref).gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "a viewport with exit:0 but no measured rows must FAIL"
    combined = " ".join(f"{r.message} {r.fix}" for r in failures).lower()
    assert "1920x1080" in combined or "row" in combined


def test_gate_fails_on_missing_plan_viewport(tmp_path: Path) -> None:
    """Covering only 2 of the 4 plan viewports is not enforcement."""
    ref = _responsive_ref(tmp_path)
    (ref / "sections" / "result.txt").write_text(
        "# section-compare multi-viewport result\n"
        "viewports: 375x812,1280x800\n\n"
        "viewport: 375x812\n| [375x812] Hero | 12 | 10 | ok | ✅ |\n"
        "viewport: 1280x800\n| [1280x800] Hero | 12 | 10 | ok | ✅ |\n"
    )
    results = Gate(ref).gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures
    combined = " ".join(f"{r.message} {r.fix}" for r in failures)
    assert "1600x900" in combined or "1920x1080" in combined


def test_gate_fails_on_truncated_trailing_viewport(tmp_path: Path) -> None:
    """All viewport headers present but the trailing ones are truncated (header
    written, no rows/exit) must FAIL — section-compare.sh writes the aggregate
    incrementally, so a crash leaves zero negative evidence and the gate used to
    read it as 'All sections PASS' (codex + multi-agent review blocker)."""
    ref = _responsive_ref(tmp_path)
    (ref / "sections" / "result.txt").write_text(
        "# section-compare multi-viewport result\n"
        "viewports: 375x812,1280x800,1600x900,1920x1080\n\n"
        "viewport: 375x812\n| [375x812] Hero | 12 | 10 | ok | ✅ |\n[375x812] exit: 0\n"
        "viewport: 1280x800\n| [1280x800] Hero | 12 | 10 | ok | ✅ |\n[1280x800] exit: 0\n"
        "viewport: 1600x900\n"   # truncated — header only, no rows / no exit
        "viewport: 1920x1080\n"  # truncated — header only, no rows / no exit
    )
    results = Gate(ref).gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "truncated trailing viewports must fail (no exit line)"
    combined = " ".join(f"{r.message} {r.fix}" for r in failures).lower()
    assert "1600x900" in combined or "incomplete" in combined or "truncat" in combined


def test_gate_fails_on_crashed_viewport_nonzero_exit(tmp_path: Path) -> None:
    """A viewport that exited nonzero (crash/failure) with no ❌ row must FAIL —
    the exit code is the honest signal the row-parser misses."""
    ref = _responsive_ref(tmp_path)
    (ref / "sections" / "result.txt").write_text(
        "# section-compare multi-viewport result\n"
        "viewports: 375x812,1280x800,1600x900,1920x1080\n\n"
        "viewport: 375x812\n| [375x812] Hero | 12 | 10 | ok | ✅ |\n[375x812] exit: 0\n"
        "viewport: 1280x800\n| [1280x800] Hero | 12 | 10 | ok | ✅ |\n[1280x800] exit: 0\n"
        "viewport: 1600x900\n| [1600x900] Hero | 12 | 10 | ok | ✅ |\n[1600x900] exit: 0\n"
        "viewport: 1920x1080\n[1920x1080] exit: 1\n"  # crashed: nonzero exit, no rows
    )
    results = Gate(ref).gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "viewport with nonzero exit must fail even without ❌ rows"


def test_gate_keeps_single_viewport_for_non_responsive_site(tmp_path: Path) -> None:
    """No detected-breakpoints.json → single-viewport result stays valid."""
    ref = tmp_path / "ref"
    (ref / "sections").mkdir(parents=True)
    (ref / "verification-plan.json").write_text(
        json.dumps({"schemaVersion": 1, "requiredChecks": [], "viewports": PLAN_VIEWPORTS})
    )
    (ref / "sections" / "result.txt").write_text("| Hero | 12 | 10 | ok | ✅ |\n")
    results = Gate(ref).gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"non-responsive single-viewport result must pass: {failures}"


# ── fan-out spec resolution (loop-e2e-9 viewport-fanout-mask-gap) ───────────
#
# tmp/ref/realfood-e2e-9/brief/viewport-fanout-mask-gap.json: the VIEWPORTS
# wrapper re-invokes the script with DIR=sections/viewports/<WxH>/ where no
# transition-spec.json exists, so every `dynamic: true` target silently
# dropped out of DYNAMIC_SELECTORS (mask-elements.json == [] at every
# viewport) and timer-carousel sections failed at ~372k AE on every fan-out
# run while the single-viewport run passed. Same hole for
# asset-substitution.json. The wrapper must pass REF_ROOT_DIR; the inner run
# resolves spec/substitution artifacts from the ref root when the viewport
# subdir has none.


def test_fanout_wrapper_passes_ref_root_dir_to_inner(tmp_path: Path) -> None:
    """The wrapper must export REF_ROOT_DIR=<ref root> to each inner
    per-viewport invocation (probed via a stub SECTION_COMPARE_INNER_CMD)."""
    root = _project_root()
    ref = tmp_path / "ref"
    (ref / "sections").mkdir(parents=True)
    stub = tmp_path / "inner-stub.sh"
    probe = tmp_path / "probe.txt"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "REF_ROOT_DIR=${{REF_ROOT_DIR:-<unset>}} DIR=$4" >> "{probe}"\n'
        'mkdir -p "$4/sections"\n'
        'echo "| stub | 0 | 0 | ok | ✅ |" > "$4/sections/result.txt"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    env = os.environ.copy()
    env["VIEWPORTS"] = "375x812,1280x800"
    env["SECTION_COMPARE_INNER_CMD"] = str(stub)
    proc = subprocess.run(
        [
            "bash",
            str(root / "skills" / "visual-debug" / "scripts" / "section-compare.sh"),
            "https://ref.test",
            "http://impl.test",
            "fanout-refroot-test",
            str(ref),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = probe.read_text().strip().splitlines()
    assert len(lines) == 2, lines
    for line in lines:
        assert f"REF_ROOT_DIR={ref}" in line, (
            f"inner invocation must receive REF_ROOT_DIR=<ref root>: {line}"
        )


def test_fanout_failure_preserves_prior_canonical_results(tmp_path: Path) -> None:
    """An interrupted outer sweep must not publish a partial aggregate.

    The Stop hook intentionally treats a header-only result.txt as untrusted.
    Preserve the canonical text commit marker and its prior JSON helper until
    every requested viewport finishes and the replacement helper is rendered.
    """
    root = _project_root()
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True)
    prior_txt = (
        "# prior canonical result\n"
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "| Hero | 0 | 0 | ok | ✅ |\n"
        "\n**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
    )
    prior_json = '{"sentinel": true}\n'
    (sections / "result.txt").write_text(prior_txt, encoding="utf-8")
    (sections / "result.json").write_text(prior_json, encoding="utf-8")

    stub = tmp_path / "inner-stub.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'mkdir -p "$4/sections"\n'
        "cat > \"$4/sections/result.txt\" <<'EOF'\n"
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "| Hero | 0 | 0 | ok | ✅ |\n"
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
        "EOF\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    env = {
        **os.environ,
        "VIEWPORTS": "375x812,bad-entry",
        "SECTION_COMPARE_INNER_CMD": str(stub),
        "UI_CLONE_SESSION_SETTLE_SEC": "0",
    }
    proc = subprocess.run(
        [
            "bash",
            str(root / "skills" / "visual-debug" / "scripts" / "section-compare.sh"),
            "https://ref.test",
            "http://impl.test",
            "atomic-fanout-test",
            str(ref),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "malformed VIEWPORTS entry" in proc.stderr
    assert (sections / "result.txt").read_text(encoding="utf-8") == prior_txt
    assert (sections / "result.json").read_text(encoding="utf-8") == prior_json
    assert not list(sections.glob(".result.*.tmp.*"))


def test_fanout_rejects_viewport_list_without_valid_entries(tmp_path: Path) -> None:
    """Bash 3.2 empty arrays must not turn invalid input into exit zero."""
    root = _project_root()
    ref = tmp_path / "ref"
    sections = ref / "sections"
    sections.mkdir(parents=True)
    prior_txt = "prior canonical text\n"
    prior_json = '{"prior": true}\n'
    (sections / "result.txt").write_text(prior_txt, encoding="utf-8")
    (sections / "result.json").write_text(prior_json, encoding="utf-8")

    env = {**os.environ, "VIEWPORTS": ", , "}
    proc = subprocess.run(
        [
            "bash",
            str(root / "skills" / "visual-debug" / "scripts" / "section-compare.sh"),
            "https://ref.test",
            "http://impl.test",
            "empty-fanout-test",
            str(ref),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "did not contain any WIDTHxHEIGHT entries" in proc.stderr
    assert (sections / "result.txt").read_text(encoding="utf-8") == prior_txt
    assert (sections / "result.json").read_text(encoding="utf-8") == prior_json
    assert not list(sections.glob(".result.*.tmp.*"))


def _decode(stream: object) -> str:
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", "replace")
    return str(stream)


def _run_section_compare(
    args: list[str],
    env: dict[str, str],
    agent_browser_stub_dir: Path,
    *,
    timeout: int = 20,
) -> str:
    """Run section-compare.sh and return its stdout+stderr.

    These tests assert only on the ``masking:`` echo, which section-compare emits
    BEFORE any browser call. If agent-browser later stalls on the unreachable ref
    URL (``https://ref.test`` is NXDOMAIN; some agent-browser builds block on the
    failed navigation rather than erroring fast), the early output is still
    captured — return it instead of hanging the test on the downstream browser
    stall (was a 60s timeout → flaky FAIL).
    """
    agent_browser_stub_dir.mkdir(parents=True, exist_ok=True)
    agent_browser_stub = agent_browser_stub_dir / "agent-browser"
    agent_browser_stub.write_text("#!/usr/bin/env bash\nexit 1\n")
    agent_browser_stub.chmod(0o755)
    scoped_env = env.copy()
    scoped_env["PATH"] = (
        f"{agent_browser_stub_dir}:{env.get('PATH', '')}"
    )
    try:
        proc = subprocess.run(
            args, env=scoped_env, capture_output=True, text=True, timeout=timeout
        )
        return proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        return _decode(exc.stdout) + _decode(exc.stderr)


def test_inner_run_resolves_dynamic_spec_from_ref_root(tmp_path: Path) -> None:
    """An inner per-viewport run (DIR=sections/viewports/<WxH>/ with no local
    transition-spec.json) must pull `dynamic: true` targets from
    REF_ROOT_DIR's spec into DYNAMIC_SELECTORS (visible in the masking echo)."""
    root = _project_root()
    ref = tmp_path / "ref"
    vp_dir = ref / "sections" / "viewports" / "375x812"
    vp_dir.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "eatreal-food-carousel", "dynamic": True, "target": ".dga-carousel-target"},
        ]
    }))
    env = os.environ.copy()
    env["SECTION_COMPARE_INNER"] = "1"
    env["REF_ROOT_DIR"] = str(ref)
    env["VIEW_W"] = "375"
    env["VIEW_H"] = "812"
    # Empty PATH entry trick is unnecessary: the masking echo happens before
    # any browser call; the run then dies on agent-browser, which is fine.
    combined = _run_section_compare(
        [
            "bash",
            str(root / "skills" / "visual-debug" / "scripts" / "section-compare.sh"),
            "https://ref.test",
            "http://impl.test",
            "fanout-spec-test",
            str(vp_dir),
        ],
        env,
        tmp_path / "agent-browser-stub",
    )
    mask_lines = [ln for ln in combined.splitlines() if "masking:" in ln]
    assert mask_lines, f"masking echo missing from inner run output: {combined[:2000]}"
    assert ".dga-carousel-target" in mask_lines[0], (
        "inner run must resolve dynamic targets from REF_ROOT_DIR when the "
        f"viewport subdir has no transition-spec.json: {mask_lines[0]}"
    )


def test_direct_viewport_run_infers_ref_root_for_dynamic_spec(tmp_path: Path) -> None:
    """A direct run against ``<ref>/sections/viewports/<WxH>`` must not drop
    dynamic masks merely because the fan-out wrapper did not set REF_ROOT_DIR."""
    root = _project_root()
    ref = tmp_path / "ref"
    vp_dir = ref / "sections" / "viewports" / "1920x1080"
    vp_dir.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [
            {"id": "eatreal-food-carousel", "dynamic": True, "target": ".dga-carousel-target"},
        ]
    }))
    env = os.environ.copy()
    env.pop("REF_ROOT_DIR", None)
    env["SECTION_COMPARE_INNER"] = "1"
    env["VIEW_W"] = "1920"
    env["VIEW_H"] = "1080"
    combined = _run_section_compare(
        [
            "bash",
            str(root / "skills" / "visual-debug" / "scripts" / "section-compare.sh"),
            "https://ref.test",
            "http://impl.test",
            "direct-viewport-spec-test",
            str(vp_dir),
        ],
        env,
        tmp_path / "agent-browser-stub",
    )
    mask_lines = [ln for ln in combined.splitlines() if "masking:" in ln]
    assert mask_lines, f"masking echo missing from direct viewport run: {combined[:2000]}"
    assert ".dga-carousel-target" in mask_lines[0], (
        "direct viewport runs must infer the ref root before loading dynamic "
        f"targets: {mask_lines[0]}"
    )


def test_single_viewport_spec_resolution_unchanged(tmp_path: Path) -> None:
    """No REF_ROOT_DIR, spec in $DIR: behavior identical to before."""
    root = _project_root()
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "x", "dynamic": True, "target": ".local-spec-target"}]
    }))
    env = os.environ.copy()
    env.pop("REF_ROOT_DIR", None)
    combined = _run_section_compare(
        [
            "bash",
            str(root / "skills" / "visual-debug" / "scripts" / "section-compare.sh"),
            "https://ref.test",
            "http://impl.test",
            "single-vp-spec-test",
            str(ref),
        ],
        env,
        tmp_path / "agent-browser-stub",
    )
    mask_lines = [ln for ln in combined.splitlines() if "masking:" in ln]
    assert mask_lines, f"masking echo missing: {combined[:2000]}"
    assert ".local-spec-target" in mask_lines[0]


def test_dispatcher_sets_viewports_env_for_responsive_site(tmp_path: Path) -> None:
    """UI_CLONE_DISPATCH_DRY=1 prints rows without executing; the synthesized
    section-compare row must carry ENV:VIEWPORTS from the plan when the site
    is responsive."""
    root = _project_root()
    ref = _responsive_ref(tmp_path)
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"dependencies": {}}))
    (ref / ".impl-root").write_text(str(impl) + "\n")
    (ref / "static" / "ref").mkdir(parents=True)
    (ref / "static" / "ref" / "shot-0.png").write_bytes(b"png")

    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(root)
    env["UI_CLONE_DISPATCH_DRY"] = "1"
    proc = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "verify" / "run-required-checks.sh"),
            "vp-dispatch-test",
            "https://example.test",
            "http://127.0.0.1:1",
            str(ref),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    section_lines = [
        ln for ln in proc.stdout.splitlines() if "section-compare" in ln and "DRY" in ln
    ]
    assert section_lines, f"dry mode must print the section-compare row: {proc.stdout}"
    assert "VIEWPORTS=375x812,1280x800,1600x900,1920x1080" in section_lines[0]
