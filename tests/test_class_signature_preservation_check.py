"""Tests for the class-signature-preservation anti-cheat gate.

Catches the L62 cheat pattern: impl freehands utility classes / vanilla CSS
while discarding the ref's captured CSS-Modules class signatures. The check
greps both sides for /componentName__hash/ and /prefix_hash/ tokens and
fails when impl coverage of ref signatures is below 30%.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "class-signature-preservation-check.sh"


def _make_ref_html(ref: Path, signatures: list[str]) -> None:
    """Write a synthetic <ref>/html/section-0.json containing the signatures."""
    html_dir = ref / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    classes = " ".join(signatures)
    # Embed in a class attribute the way the captured DOM does.
    body = f'<section class="{classes}"><div class="{classes}">x</div></section>'
    (html_dir / "section-0.json").write_text(
        json.dumps({"section": 0, "html": body}), encoding="utf-8"
    )


def _make_impl_component(impl: Path, signatures: list[str]) -> None:
    """Write a synthetic impl TSX file referencing the given signatures."""
    src = impl / "src"
    src.mkdir(parents=True, exist_ok=True)
    classes = " ".join(signatures)
    content = f'export function X() {{ return <div className="{classes}" />; }}\n'
    (src / "X.tsx").write_text(content, encoding="utf-8")


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_zero_preservation_fails(tmp_path: Path) -> None:
    """L62 cheat shape: ref has many signatures, impl has none → fail."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref_sigs = [f"dga_section_{i}__hash{i:02d}A" for i in range(20)]
    _make_ref_html(ref, ref_sigs)
    # Impl uses only Tailwind utility classes — no ref signatures preserved.
    _make_impl_component(impl, ["flex", "items-center", "p-4", "bg-white"])

    proc = _run(ref, impl)
    assert proc.returncode == 1, f"zero preservation must fail: {proc.stdout}\n{proc.stderr}"
    data = json.loads((ref / "class-signature-preservation.json").read_text())
    assert data["status"] == "fail"
    assert data["refSignatureCount"] >= 20
    assert data["preservedCount"] == 0
    assert data["coverage"] == 0.0
    assert len(data["missingSample"]) > 0


def test_full_preservation_passes(tmp_path: Path) -> None:
    """Impl that preserves all ref signatures passes."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref_sigs = [f"header_logo__hash{i:02d}B" for i in range(15)]
    _make_ref_html(ref, ref_sigs)
    _make_impl_component(impl, ref_sigs)

    proc = _run(ref, impl)
    assert proc.returncode == 0, f"full preservation must pass: {proc.stdout}\n{proc.stderr}"
    data = json.loads((ref / "class-signature-preservation.json").read_text())
    assert data["status"] == "pass"
    assert data["preservedCount"] == data["refSignatureCount"]
    assert data["coverage"] == 1.0


def test_partial_preservation_above_threshold_passes(tmp_path: Path) -> None:
    """≥30% coverage clears the threshold."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref_sigs = [f"banner_text__hash{i:02d}C" for i in range(20)]
    _make_ref_html(ref, ref_sigs)
    # Preserve 8/20 = 40% — above threshold.
    _make_impl_component(impl, ref_sigs[:8])

    proc = _run(ref, impl)
    assert proc.returncode == 0, f"partial preservation above threshold must pass: {proc.stdout}\n{proc.stderr}"
    data = json.loads((ref / "class-signature-preservation.json").read_text())
    assert data["status"] == "pass"
    assert data["coverage"] >= 0.30


def test_partial_preservation_below_threshold_fails(tmp_path: Path) -> None:
    """<30% coverage trips the gate."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref_sigs = [f"animation_overlay__hash{i:02d}D" for i in range(20)]
    _make_ref_html(ref, ref_sigs)
    # Preserve 4/20 = 20% — below threshold.
    _make_impl_component(impl, ref_sigs[:4])

    proc = _run(ref, impl)
    assert proc.returncode == 1, f"sub-threshold preservation must fail: {proc.stdout}\n{proc.stderr}"
    data = json.loads((ref / "class-signature-preservation.json").read_text())
    assert data["status"] == "fail"
    assert data["coverage"] < 0.30


def test_theft_asymmetry_zero_ref_many_impl_fails(tmp_path: Path) -> None:
    """L41 cheat: ref=0 captured signatures but impl=many → fail (theft asymmetry).

    This catches the wholesale-bundle-paste shape where the agent skipped
    Phase 1-6 extraction (so ref harvested 0 signatures) but pasted compiled
    CSS bundles into impl/public/ (so impl source greps thousands of
    signatures). The previous version's `ref<10 → skip` branch silently
    let this through.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    # Ref has NO captured HTML / dom-scaffold — empty extraction.
    # Impl has many signatures (simulating pasted bundle CSS).
    src = impl / "src"
    src.mkdir(parents=True)
    impl_sigs = [f"section_{i}__hash{i:02d}X" for i in range(15)]
    classes = " ".join(impl_sigs)
    (src / "X.tsx").write_text(f'export const X = () => <div className="{classes}" />;')

    proc = _run(ref, impl)
    assert proc.returncode == 1, f"theft asymmetry must fail: {proc.stdout}\n{proc.stderr}"
    data = json.loads((ref / "class-signature-preservation.json").read_text())
    assert data["status"] == "fail"
    assert data["refSignatureCount"] == 0
    assert data["implSignatureCount"] >= 15
    assert "bulk-pasted compiled CSS bundles" in data["reason"]


def test_few_ref_signatures_skips(tmp_path: Path) -> None:
    """Sites without enough captured CSS-Module signatures gracefully skip."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    # Only 3 signatures — below the |ref|<10 floor.
    _make_ref_html(ref, ["foo__abcd12", "bar__efgh34", "baz__ijkl56"])
    _make_impl_component(impl, ["flex"])

    proc = _run(ref, impl)
    assert proc.returncode == 0, f"low-signal ref must skip, not fail: {proc.stdout}\n{proc.stderr}"
    data = json.loads((ref / "class-signature-preservation.json").read_text())
    assert data["status"] == "skip"


def test_missing_impl_skips(tmp_path: Path) -> None:
    """No impl directory → skip, not crash."""
    ref = tmp_path / "ref"
    ref_sigs = [f"section_{i}__hash{i:02d}E" for i in range(15)]
    _make_ref_html(ref, ref_sigs)

    # Pass a non-existent impl path explicitly.
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(tmp_path / "no-such-impl")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"missing impl must skip: {proc.stdout}\n{proc.stderr}"
    data = json.loads((ref / "class-signature-preservation.json").read_text())
    assert data["status"] == "skip"
    assert "impl_root not found" in data["reason"]


def test_setup_error_on_bad_ref(tmp_path: Path) -> None:
    """Non-existent ref dir → exit 2 (setup error)."""
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path / "no-such-ref")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2


def test_includes_in_verification_plan(tmp_path: Path) -> None:
    """The check must be registered in the verification-plan dispatch table."""
    ref = tmp_path / "ref"
    ref.mkdir()
    plan_script = ROOT / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    proc = subprocess.run(
        ["bash", str(plan_script), str(ref), "--tier=quick"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"plan build failed: {proc.stdout}\n{proc.stderr}"
    plan = json.loads((ref / "verification-plan.json").read_text())
    ids = [c["id"] for c in plan["requiredChecks"]]
    assert "class-signature-preservation" in ids, f"not in plan; got: {ids}"
    # Confirm it lives in the universal anti-cheat baseline (tier=quick, block).
    entry = next(c for c in plan["requiredChecks"] if c["id"] == "class-signature-preservation")
    assert entry["severity"] == "block"
    assert entry["tier"] == "quick"
