import json
from pathlib import Path

from ui_clone.gate import Gate


def test_gate_section_compare_fails_when_result_txt_missing(tmp_path: Path) -> None:
    """gate_section_compare must fail when sections/result.txt does not exist."""
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "Missing result.txt must produce a fail result"
    assert any("result.txt" in r.message or "result.txt" in r.fix for r in failures)
    combined_output = " ".join(f"{r.message} {r.fix}" for r in failures)
    assert "skills/visual-debug/scripts/section-compare.sh" in combined_output
    assert "MISSING (visual-debug/scripts/section-compare.sh" not in combined_output



def test_gate_section_compare_passes_when_all_sections_pass(tmp_path: Path) -> None:
    """gate_section_compare must pass when result.txt has only ✅ lines."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text("| Hero | ✅ PASS | 97% |\n| Footer | ✅ PASS | 99% |\n")
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"All-pass result.txt must not produce failures: {failures}"



def test_gate_section_compare_fails_when_section_failed(tmp_path: Path) -> None:
    """gate_section_compare must fail when result.txt contains ❌."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text("| Hero | ❌ FAIL | 55% |\n| Footer | ✅ PASS | 99% |\n")
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "❌ in result.txt must produce a fail result"
    assert any("FAILED" in r.message or "section" in r.message.lower() for r in failures)



def test_gate_section_compare_fails_when_section_missing(tmp_path: Path) -> None:
    """gate_section_compare must fail when result.txt contains ⚠️ MISSING impl."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text("| Hero | ✅ PASS | 97% |\n| Nav | ⚠️ MISSING impl |\n")
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "MISSING impl in result.txt must produce a fail result"



def test_gate_section_compare_caps_structural_only_ratio(tmp_path: Path) -> None:
    """Regression — 5199dd9 benchmark shipped a 9-section page with ALL 9
    marked STRUCTURAL_ONLY via asset-substitution.json. Cap is 50%.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    rows = "\n".join(
        f"| sec-{i} | — | — | substituted | 🔁 STRUCTURAL_ONLY |"
        for i in range(9)
    )
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        + rows + "\n"
        "\n**Result: 9 PASS, 0 FAIL, 0 SKIP, 9 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert any(r.label == "structural-only excess" for r in failures), (
        f"100% STRUCTURAL_ONLY must fail; got: {[(r.label, r.status) for r in failures]}"
    )
    assert any("9/9" in r.message and "100%" in r.message for r in failures)



def test_gate_section_compare_allows_minority_structural_only(tmp_path: Path) -> None:
    """Counterpart: a handful of substituted sections (commercial fonts etc)
    is legitimate. 2/9 (22%) should pass — below the 50% cap.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    pass_rows = "\n".join(
        f"| sec-{i} | 100 | 50 | ok | ✅ |"
        for i in range(7)
    )
    subst_rows = "\n".join(
        f"| sec-sub-{i} | — | — | substituted | 🔁 STRUCTURAL_ONLY |"
        for i in range(2)
    )
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        + pass_rows + "\n" + subst_rows + "\n"
        "\n**Result: 9 PASS, 0 FAIL, 0 SKIP, 2 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert not any(r.label == "structural-only excess" for r in failures)



def test_gate_section_compare_detects_threshold_gaming(tmp_path: Path) -> None:
    """Regression — d19e28d benchmark agent set SECTION_THRESHOLD=250000
    so AE/Mpx 88823 + 228325 (both nominally `critical` >20000) got
    re-classified as `minor` with ✅ PASS. New check: any row labeled
    ok/minor with AE/Mpx > 2000 (canonical bound) is flagged as gaming.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        "| footer | 279470 | 228325 | minor | ✅ |\n"
        "| section-0 | 109742 | 88823 | minor | ✅ |\n"
        "\n"
        "**Result: 2 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert any(r.label == "section-threshold gaming" for r in failures), (
        f"inflated SECTION_THRESHOLD must surface as gaming fail: {failures}"
    )
    assert any("228325" in r.message for r in failures)
    assert any("ui_clone.measure" in (r.fix or "") for r in failures)



def test_gate_section_compare_accepts_legitimate_minor_under_threshold(tmp_path: Path) -> None:
    """Counterpart: `minor` rows with AE/Mpx ≤ 2000 (canonical default) are
    legit. Don't false-positive trip the gaming detector.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        "| hero | 5000 | 800 | minor | ✅ |\n"
        "| footer | 3000 | 1500 | minor | ✅ |\n",
        encoding="utf-8",
    )
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert not any(r.label == "section-threshold gaming" for r in failures)



def test_gate_section_compare_overrides_structural_only_on_critical_diff(tmp_path: Path) -> None:
    """Regression — STRUCTURAL_ONLY rows must NOT silent-pass when the same
    section has severity=critical in structure-diff.json. The realfood.gov
    benchmark shipped a 638px-tall impl against a 19954px ref and the gate
    still reported "All sections PASS" because asset-substitution flipped
    every section to STRUCTURAL_ONLY. This test locks the override in.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| section-0 | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n"
        "| footer    | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n"
        "\n"
        "**Result: 2 PASS, 0 FAIL, 0 SKIP, 2 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    (sections / "structure-diff.json").write_text(json.dumps([
        {
            "section": "section-0",
            "issues": [
                "DISPLAY_MISMATCH: ref=block, impl=flex",
                "HEIGHT_MISMATCH: ref=19954px, impl=638px (ratio=0.03)",
            ],
            "severity": "critical",
            "score": 0.867,
        }
    ]))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert any(
        r.label == "structural-only critical override" for r in failures
    ), (
        "STRUCTURAL_ONLY with critical structure-diff must fail the gate; got: "
        f"{[(r.label, r.status, r.message) for r in results]}"
    )
    assert any("section-0" in r.message for r in failures), (
        "Failing section name must surface in the message"
    )



def test_gate_section_compare_overrides_structural_only_on_major_with_low_ratio(tmp_path: Path) -> None:
    """Regression — the 077d8c3 benchmark exposed `major` severity with
    HEIGHT_MISMATCH ratio=0.35 (impl is 35% of ref height) slipping past
    the `critical`-only guard. ratio<0.5 with severity=major means content
    is missing, not substituted — guard must catch it.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| section-0 | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n"
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 1 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    (sections / "structure-diff.json").write_text(json.dumps([
        {
            "section": "section-0",
            "issues": [
                "HEIGHT_MISMATCH: ref=19954px, impl=6955px (ratio=0.35)",
            ],
            "severity": "major",
            "score": 0.363,
        }
    ]))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert any(r.label == "structural-only critical override" for r in failures), (
        f"major + ratio=0.35 must fail STRUCTURAL_ONLY: {results}"
    )



def test_gate_section_compare_allows_major_with_acceptable_ratio(tmp_path: Path) -> None:
    """`major` severity with HEIGHT_MISMATCH ratio≥0.5 (impl reasonably close
    to ref height) — keep STRUCTURAL_ONLY PASS. The guard is targeted at
    "content disappeared," not "minor height delta."
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| section-0 | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n",
        encoding="utf-8",
    )
    (sections / "structure-diff.json").write_text(json.dumps([
        {
            "section": "section-0",
            "issues": ["HEIGHT_MISMATCH: ref=1000px, impl=750px (ratio=0.75)"],
            "severity": "major",
            "score": 0.2,
        }
    ]))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, (
        f"major with ratio≥0.5 must not fail STRUCTURAL_ONLY: {results}"
    )



def test_gate_section_compare_allows_structural_only_when_diff_not_critical(tmp_path: Path) -> None:
    """STRUCTURAL_ONLY rows still PASS when structure-diff.json carries
    only non-critical severities (warn / info) — the override is targeted
    at the layout-regression class, not at every minor structural delta.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| hero | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n",
        encoding="utf-8",
    )
    (sections / "structure-diff.json").write_text(json.dumps([
        {"section": "hero", "issues": ["minor"], "severity": "warn", "score": 0.1}
    ]))
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, (
        f"Non-critical structure-diff must not fail STRUCTURAL_ONLY: {results}"
    )



def test_gate_section_compare_accessible_via_run(tmp_path: Path) -> None:
    """section-compare gate must be callable through Gate.run()."""
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    # No result.txt → BLOCKED (exit code 1)
    exit_code = gate.run("section-compare", json_output=True)
    assert exit_code == 1

