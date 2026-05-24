"""Tests for the class-signature CSS-coverage gate.

Catches the L64 metric/visual decoupling: impl preserves class NAMES
at 95.5% but only ~80% of those names have a matching CSS rule, with
the critical splash/animation classes in the unstyled set → renders
as a single solid color despite high signature-preservation pass.

This gate pairs with class-signature-preservation-check.sh: that gate
verifies NAME presence; this one verifies STYLE presence for those names.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "class-signature-css-coverage-check.sh"


def _make_ref_html(ref: Path, signatures: list[str]) -> None:
    html = ref / "html"
    html.mkdir(parents=True, exist_ok=True)
    body = '<section class="' + " ".join(signatures) + '">x</section>'
    (html / "section-0.json").write_text(
        json.dumps({"section": 0, "html": body}), encoding="utf-8"
    )


def _make_impl_with_classnames(impl: Path, signatures: list[str], css_rules: list[str]) -> None:
    src = impl / "src"
    src.mkdir(parents=True, exist_ok=True)
    classes = " ".join(signatures)
    (src / "X.tsx").write_text(
        f'export const X = () => <div className="{classes}" />;\n'
    )
    if css_rules:
        css_dir = impl / "src" / "styles"
        css_dir.mkdir(parents=True, exist_ok=True)
        (css_dir / "main.css").write_text("\n".join(css_rules))


def _write_sig_artifact(ref: Path) -> None:
    """Write a stub class-signature-preservation.json so the coverage gate
    knows the prerequisite ran."""
    (ref / "class-signature-preservation.json").write_text(json.dumps({
        "status": "pass",
        "refSignatureCount": 0,
        "implSignatureCount": 0,
        "preservedCount": 0,
        "coverage": 0.0,
    }))


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_zero_css_rules_fails(tmp_path: Path) -> None:
    """L64 worst-case shape: all class signatures preserved in JSX,
    none have CSS rules → fail."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    sigs = [f"dga_section_{i}__hash{i:02d}A" for i in range(10)]
    _make_ref_html(ref, sigs)
    _make_impl_with_classnames(impl, sigs, css_rules=[])
    _write_sig_artifact(ref)

    proc = _run(ref, impl)
    assert proc.returncode == 1, f"zero CSS coverage must fail: {proc.stdout}"
    data = json.loads((ref / "class-signature-css-coverage.json").read_text())
    assert data["status"] == "fail"
    assert data["styledCount"] == 0
    assert data["preservedCount"] >= 10
    assert data["styleCoverage"] == 0.0
    assert len(data["unstyledSample"]) > 0


def test_full_css_coverage_passes(tmp_path: Path) -> None:
    """All preserved signatures have CSS rules → pass."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    sigs = [f"hero_logo__hash{i:02d}B" for i in range(10)]
    _make_ref_html(ref, sigs)
    css_rules = [f".{s} {{ color: red; }}" for s in sigs]
    _make_impl_with_classnames(impl, sigs, css_rules=css_rules)
    _write_sig_artifact(ref)

    proc = _run(ref, impl)
    assert proc.returncode == 0, f"full coverage must pass: {proc.stdout}"
    data = json.loads((ref / "class-signature-css-coverage.json").read_text())
    assert data["status"] == "pass"
    assert data["styledCount"] == data["preservedCount"]
    assert data["styleCoverage"] == 1.0


def test_partial_coverage_above_threshold_passes(tmp_path: Path) -> None:
    """≥30% coverage clears the threshold."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    sigs = [f"section_{i}__hash{i:02d}C" for i in range(10)]
    _make_ref_html(ref, sigs)
    # Style only 4 of 10 — 40% coverage, above threshold
    css_rules = [f".{s} {{ color: red; }}" for s in sigs[:4]]
    _make_impl_with_classnames(impl, sigs, css_rules=css_rules)
    _write_sig_artifact(ref)

    proc = _run(ref, impl)
    assert proc.returncode == 0
    data = json.loads((ref / "class-signature-css-coverage.json").read_text())
    assert data["status"] == "pass"
    assert data["styleCoverage"] >= 0.30


def test_partial_coverage_below_threshold_fails(tmp_path: Path) -> None:
    """<30% coverage trips the gate."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    sigs = [f"section_{i}__hash{i:02d}D" for i in range(10)]
    _make_ref_html(ref, sigs)
    # Style only 2 of 10 — 20% coverage, below threshold
    css_rules = [f".{s} {{ color: red; }}" for s in sigs[:2]]
    _make_impl_with_classnames(impl, sigs, css_rules=css_rules)
    _write_sig_artifact(ref)

    proc = _run(ref, impl)
    assert proc.returncode == 1
    data = json.loads((ref / "class-signature-css-coverage.json").read_text())
    assert data["status"] == "fail"
    assert data["styleCoverage"] < 0.30


def test_attribute_selector_counts_as_styled(tmp_path: Path) -> None:
    """[class*="signature"] attribute selectors are valid CSS rules."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    sigs = [f"hero_btn__hash{i:02d}E" for i in range(10)]
    _make_ref_html(ref, sigs)
    # Half plain class, half attribute
    css_rules = (
        [f".{s} {{ color: red; }}" for s in sigs[:5]]
        + [f'[class*="{s}"] {{ color: blue; }}' for s in sigs[5:]]
    )
    _make_impl_with_classnames(impl, sigs, css_rules=css_rules)
    _write_sig_artifact(ref)

    proc = _run(ref, impl)
    assert proc.returncode == 0
    data = json.loads((ref / "class-signature-css-coverage.json").read_text())
    assert data["styledCount"] == 10
    assert data["styleCoverage"] == 1.0


def test_skip_when_no_sig_artifact(tmp_path: Path) -> None:
    """Prerequisite gate (class-signature-preservation) hasn't run → skip."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    proc = _run(ref, impl)
    assert proc.returncode == 0
    data = json.loads((ref / "class-signature-css-coverage.json").read_text())
    assert data["status"] == "skip"


def test_skip_when_preserved_too_small(tmp_path: Path) -> None:
    """preserved < 5 → skip (low-signal floor)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    sigs = ["foo__a1b2", "bar__c3d4"]  # 2 sigs, below floor of 5
    _make_ref_html(ref, sigs)
    _make_impl_with_classnames(impl, sigs, css_rules=[])
    _write_sig_artifact(ref)

    proc = _run(ref, impl)
    assert proc.returncode == 0
    data = json.loads((ref / "class-signature-css-coverage.json").read_text())
    assert data["status"] == "skip"


def test_missing_impl_skips(tmp_path: Path) -> None:
    """No impl dir → skip."""
    ref = tmp_path / "ref"
    ref.mkdir()
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(tmp_path / "no-impl")],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    data = json.loads((ref / "class-signature-css-coverage.json").read_text())
    assert data["status"] == "skip"


def test_setup_error_on_bad_ref(tmp_path: Path) -> None:
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path / "no-ref")],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2


def test_includes_in_verification_plan(tmp_path: Path) -> None:
    """Registered in the dispatch table."""
    ref = tmp_path / "ref"
    ref.mkdir()
    plan_script = ROOT / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    proc = subprocess.run(
        ["bash", str(plan_script), str(ref), "--tier=quick"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    plan = json.loads((ref / "verification-plan.json").read_text())
    ids = {c["id"] for c in plan["requiredChecks"]}
    assert "class-signature-css-coverage" in ids
