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


def test_gate_section_compare_warns_when_structural_only_is_broad_but_below_cap(
    tmp_path: Path,
) -> None:
    """STRUCTURAL_ONLY below the hard 50% cap can still explain why pixel
    polishing appears not to run. Surface that as an actionable warning
    before it becomes a hard bypass failure.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    pass_rows = "\n".join(
        f"| sec-{i} | 100 | 50 | ok | ✅ |"
        for i in range(5)
    )
    subst_rows = "\n".join(
        f"| sec-sub-{i} | — | — | substituted | 🔁 STRUCTURAL_ONLY |"
        for i in range(4)
    )
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        + pass_rows + "\n" + subst_rows + "\n"
        "\n**Result: 9 PASS, 0 FAIL, 0 SKIP, 4 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )

    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    warnings = [r for r in results if r.status == "warn"]

    assert not any(r.label == "structural-only excess" for r in failures)
    assert any(r.label == "structural-only broad coverage" for r in warnings), (
        f"broad STRUCTURAL_ONLY coverage must warn; got: {results}"
    )
    combined = " ".join(f"{r.message} {r.fix}" for r in warnings)
    assert "4/9" in combined
    assert "pixel AE polishing skipped" in combined
    assert "Narrow asset-substitution.json" in combined



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



# ── Canvas-replay AE relief (v0.7.0) ────────────────────────────────────
# Three conditions must ALL hold for relief to fire on a row:
#   (a) pipeline-state.json has closeoutPolicy="canvas-replay"
#   (b) canvas-replay-attestation.json exists in the ref dir
#   (c) the section is tagged kind="canvas" in section-map.json
# When all three hold, the section's critical AE/Mpx band widens from
# >20000 to >40000. AE/Mpx ≤ 40000 on a canvas section downgrades from
# FAIL to PASS; AE/Mpx > 40000 still fails. Non-canvas sections, missing
# attestation, or canonical policy = no relief.


def _wire_canvas_replay(ref: Path, sections: list[dict] | None = None) -> None:
    """Write the 3 files that activate canvas-replay relief."""
    (ref / "pipeline-state.json").write_text(
        json.dumps({"component": ref.name, "closeoutPolicy": "canvas-replay"}),
        encoding="utf-8",
    )
    (ref / "canvas-replay-attestation.json").write_text(
        json.dumps({
            "license": "MIT",
            "disclaimer": "test",
            "attestedBy": "op",
            "attestedAt": "2026-05-25T08:00:00Z",
            "ref_canvas_sources": ["https://example.com/canvas.js"],
        }),
        encoding="utf-8",
    )
    (ref / "section-map.json").write_text(
        json.dumps({"sections": sections or []}),
        encoding="utf-8",
    )


def test_canvas_replay_downgrades_canvas_section_within_2x_ceiling(tmp_path: Path) -> None:
    """A canvas-tagged section with AE/Mpx in (20000, 40000] FAILs without
    relief and PASSes with relief active. This is the core unlock."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    # AE/Mpx 35000 is critical at canonical band (>20000) but below 2x ceiling (40000).
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        "| music-sphere | 70000 | 35000 | critical | ❌ |\n"
        "\n**Result: 0 PASS, 1 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    _wire_canvas_replay(ref, sections=[
        {"index": 0, "kind": "canvas", "name": "music-sphere"},
    ])
    gate = Gate(ref)
    results = gate.gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, (
        f"canvas section within 2x ceiling must PASS under relief; got: "
        f"{[(r.label, r.message) for r in results]}"
    )


def test_canvas_replay_does_not_relax_above_2x_ceiling(tmp_path: Path) -> None:
    """AE/Mpx > 40000 on a canvas section still FAILs — relief widens the
    band, doesn't bypass it."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        "| music-sphere | 200000 | 80000 | critical | ❌ |\n",
        encoding="utf-8",
    )
    _wire_canvas_replay(ref, sections=[
        {"index": 0, "kind": "canvas", "name": "music-sphere"},
    ])
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert failures, "AE/Mpx 80000 must still fail under 2x relief"


def test_canvas_replay_does_not_affect_non_canvas_sections(tmp_path: Path) -> None:
    """A non-canvas section still fails at AE/Mpx=35000 even when the
    policy is fully active for OTHER sections. Boundary integrity check."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        "| music-sphere | 70000 | 35000 | critical | ❌ |\n"
        "| text-block   | 65000 | 35000 | critical | ❌ |\n",
        encoding="utf-8",
    )
    _wire_canvas_replay(ref, sections=[
        {"index": 0, "kind": "canvas", "name": "music-sphere"},
        # text-block intentionally omitted from section-map (or kind != canvas)
    ])
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert any("section" in r.message.lower() for r in failures), (
        f"non-canvas text-block must still surface as fail; got: {failures}"
    )
    # The failure message must reference the remaining non-canvas failure,
    # not all original fails (music-sphere is relieved).
    combined = " ".join(r.message for r in failures)
    assert "1 section(s) FAILED" in combined or "1 section" in combined


def test_canvas_replay_inactive_without_attestation(tmp_path: Path) -> None:
    """Policy field set but no attestation = no relief."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        "| music-sphere | 70000 | 35000 | critical | ❌ |\n",
        encoding="utf-8",
    )
    (ref / "pipeline-state.json").write_text(
        json.dumps({"component": ref.name, "closeoutPolicy": "canvas-replay"}),
        encoding="utf-8",
    )
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "kind": "canvas", "name": "music-sphere"}]}),
        encoding="utf-8",
    )
    # Note: NO canvas-replay-attestation.json
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert failures, "Relief must NOT fire without attestation; row must still fail"


def test_canvas_replay_inactive_when_policy_canonical(tmp_path: Path) -> None:
    """Default canonical policy = no relief, even with attestation/kind=canvas."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        "| music-sphere | 70000 | 35000 | critical | ❌ |\n",
        encoding="utf-8",
    )
    # closeoutPolicy=canonical (or absent) — attestation file is irrelevant
    (ref / "canvas-replay-attestation.json").write_text(
        json.dumps({"license": "MIT", "ref_canvas_sources": []}),
        encoding="utf-8",
    )
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": 0, "kind": "canvas", "name": "music-sphere"}]}),
        encoding="utf-8",
    )
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert failures, "Relief must NOT fire when policy is canonical"


def test_canvas_replay_does_not_relax_structural_only_critical_override(tmp_path: Path) -> None:
    """Canvas-replay relief is scoped to AE/Mpx band widening for FAIL rows.
    It MUST NOT relax the STRUCTURAL_ONLY+critical-structure-diff guard
    (codex boundary: text fidelity, font parity, structural integrity stay
    strict). Layout regression on a canvas section is still a regression."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| music-sphere | — | — | substituted | 🔁 STRUCTURAL_ONLY |\n"
        "\n**Result: 1 PASS, 0 FAIL, 0 SKIP, 1 STRUCTURAL_ONLY**\n",
        encoding="utf-8",
    )
    (sections / "structure-diff.json").write_text(json.dumps([
        {
            "section": "music-sphere",
            "issues": ["DISPLAY_MISMATCH: ref=block, impl=none"],
            "severity": "critical",
            "score": 0.9,
        }
    ]))
    _wire_canvas_replay(ref, sections=[
        {"index": 0, "kind": "canvas", "name": "music-sphere"},
    ])
    gate = Gate(ref)
    failures = [r for r in gate.gate_section_compare() if r.status == "fail"]
    assert any(r.label == "structural-only critical override" for r in failures), (
        f"canvas-replay must NOT relax structural-only critical override; got: {failures}"
    )


def test_canvas_replay_message_advertises_relief_count(tmp_path: Path) -> None:
    """The PASS message should make relief visible so reviewers know
    why fails became passes."""
    ref = tmp_path / "ref"
    ref.mkdir()
    sections = ref / "sections"
    sections.mkdir()
    (sections / "result.txt").write_text(
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---|---|---|---|---|\n"
        "| music-sphere | 70000 | 35000 | critical | ❌ |\n"
        "| bg-canvas    | 60000 | 30000 | critical | ❌ |\n",
        encoding="utf-8",
    )
    _wire_canvas_replay(ref, sections=[
        {"index": 0, "kind": "canvas", "name": "music-sphere"},
        {"index": 1, "kind": "canvas", "name": "bg-canvas"},
    ])
    gate = Gate(ref)
    results = gate.gate_section_compare()
    msgs = " ".join(r.message for r in results)
    assert "canvas-replay" in msgs.lower(), (
        f"Relief activity must be visible in gate output; got: {msgs}"
    )


def test_gate_section_compare_accessible_via_run(tmp_path: Path) -> None:
    """section-compare gate must be callable through Gate.run()."""
    ref = tmp_path / "ref"
    ref.mkdir()
    gate = Gate(ref)
    # No result.txt → BLOCKED (exit code 1)
    exit_code = gate.run("section-compare", json_output=True)
    assert exit_code == 1
