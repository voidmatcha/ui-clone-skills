import json
from pathlib import Path

from ._helpers import (
    _project_root,
    _run_verification_plan,
)


def test_verification_plan_emits_bundle_impl_coverage_when_bundle_map_present(tmp_path: Path) -> None:
    """Regression — c9b638d's bundle-map.json detected gsap-like + motion-like
    + Lenis, but impl/package.json shipped with only next/react/react-dom →
    dead-wire pattern. New `bundle-impl-coverage` row enforces install parity.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "chunks": {"a.js": {"role": "vendor", "libs": ["gsap-like-strings"]}},
        "notes": "lenis class on <html> is conclusive runtime evidence.",
    }))
    plan = _run_verification_plan(ref)
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "bundle-impl-coverage" in rows
    assert rows["bundle-impl-coverage"]["severity"] == "block"
    assert rows["bundle-impl-coverage"]["tier"] == "quick"



def test_verification_plan_forces_comprehensive_tier_under_benchmark_work(tmp_path: Path) -> None:
    """Regression — the 077d8c3 benchmark exposed a gaming pattern where the
    agent set `UI_CLONE_VERIFY_TIER=quick` and the verification surface
    shrank to 3 checks. Benchmark refs (path contains `benchmark/work/`)
    must force tier=comprehensive regardless of caller-supplied tier, so the
    agent does not get to pick which checks fire.
    """
    bench_root = tmp_path / "benchmark" / "work" / "deadbee"
    ref = bench_root / "ref"
    ref.mkdir(parents=True)
    # Wire enough signals to confirm tier-conditional rows light up.
    (ref / "visible-images.json").write_text(json.dumps([
        {"type": "image", "src": "https://cdn.example.com/x.jpg", "element": "img"},
    ]))
    (ref / "external-sdks.json").write_text(json.dumps({"detected": ["useScroll"]}))
    # Caller asks for quick — script must override to comprehensive.
    plan = _run_verification_plan(ref, tier="quick")
    assert plan["tier"] == "comprehensive", (
        f"benchmark/work/ ref must force tier=comprehensive, got: {plan['tier']}"
    )
    # And the comprehensive-tier rows (e.g. video-motion-compare) must appear.
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "video-motion-compare" in ids, (
        f"comprehensive-tier row missing after benchmark force: {ids}"
    )



def test_verification_plan_omits_image_fidelity_when_visible_images_absent(tmp_path: Path) -> None:
    """No visible-images.json → no image-fidelity row.

    Locks in the conditional — this row should NOT fire unconditionally,
    otherwise SVG-only / canvas-only sites would always see a pass-noise row.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    plan = _run_verification_plan(ref)
    ids = {c["id"] for c in plan["requiredChecks"]}
    assert "image-fidelity" not in ids



def test_bench_verification_smoke_markdown() -> None:
    """bench-verification.sh --repeat=1 must exit 0 and emit the expected
    markdown header + the three named fixtures.

    Locks in that fixture writes + run_*_bench callers + median calc stay in
    sync. A bash-3.2-incompatible construct (e.g. associative arrays) regresses
    this test on macOS default bash.
    """
    import subprocess

    script = _project_root() / "scripts" / "ci" / "bench-verification.sh"
    proc = subprocess.run(
        ["bash", str(script), "--repeat=1"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"bench failed: {proc.stdout}\n{proc.stderr}"
    out = proc.stdout
    assert "# verification dispatch bench" in out
    assert "verification-plan.sh" in out
    for fixture in ("empty", "hover-only", "all-signals"):
        assert fixture in out, f"missing fixture '{fixture}' in output:\n{out}"
    for gate in ("spec-implementation-coverage", "runtime-spec-coverage"):
        assert gate in out, f"missing gate '{gate}' in output:\n{out}"



def test_bench_verification_json_mode_is_valid_json() -> None:
    """--json mode must produce a JSON object with the documented top-level
    keys (verificationPlan, specImplementationCoverage, runtimeSpecCoverage).

    Locks in the JSON contract for any CI consumer that wants to assert on
    a regression threshold against a stored baseline.
    """
    import subprocess

    script = _project_root() / "scripts" / "ci" / "bench-verification.sh"
    proc = subprocess.run(
        ["bash", str(script), "--repeat=1", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"bench --json failed: {proc.stdout}\n{proc.stderr}"
    data = json.loads(proc.stdout)
    assert set(data.keys()) >= {"repeat", "verificationPlan", "specImplementationCoverage", "runtimeSpecCoverage"}
    assert set(data["verificationPlan"].keys()) == {"empty", "hoverOnly", "allSignals"}
    for fixture_block in data["verificationPlan"].values():
        assert set(fixture_block.keys()) == {"quick", "standard", "comprehensive"}
        for tier_block in fixture_block.values():
            assert "medianMs" in tier_block and "checkCount" in tier_block
    # accuracy column should report "ok" on a clean tree — the unit tests
    # already lock in the pass/fail exit codes the bench fixtures depend on.
    assert data["specImplementationCoverage"]["accuracy"] == "ok"
    assert data["runtimeSpecCoverage"]["accuracy"] == "ok"



def test_bench_verification_rejects_bad_repeat() -> None:
    """--repeat must be a positive odd integer (median picks middle element).
    Even or non-numeric values must exit 2 with a clear message — otherwise
    a typo silently coerces to whatever bash's arithmetic returns and the
    median calc breaks in non-obvious ways.
    """
    import subprocess

    script = _project_root() / "scripts" / "ci" / "bench-verification.sh"
    for bad in ("2", "0", "abc"):
        proc = subprocess.run(
            ["bash", str(script), f"--repeat={bad}"],
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 2, f"--repeat={bad} should exit 2, got {proc.returncode}: {proc.stderr}"
        assert "repeat" in proc.stderr.lower()

