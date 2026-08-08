"""An UNMEASURED section is absence of evidence — it must never read as success.

section-compare converts a section to UNMEASURED when the crop-evidence guard
fires (blank ref crop, symmetric-blank, >60% masked, colour-flattened pair). The
producer's own row text says "capture failure, not impl evidence". Those rows
were folded into SKIP_COUNT, and the SKIP branch of the verdict had no `exit 1`,
so five viewports of the eBay Playbook run each reported
`**Result: 20 PASS, 0 FAIL, 5 SKIP, 0 STRUCTURAL_ONLY**` with `exit: 0` while
pipeline-state.json recorded terminalState=failed.

This matters most exactly where the tool is supposed to be strongest: a blank ref
crop is most likely when the section is mid-reveal or mid-animation, so the
motion-bearing sections are the ones that silently go unmeasured. In that run
`style_slideshow_7xln1`, `style_playground_oXvoz` and `evo-grid-5` all carry
declared transitions in transition-spec.json and all went unmeasured.
"""

from pathlib import Path

from ui_clone.gate import Gate

_UNMEASURED_TABLE = """| Section | AE | AE/Mpx | Severity | Status |
|---------|-----|--------|----------|--------|
| hero | 0 | 0 | ok | ✅ |
| style_slideshow_7xln1 | — | — | unmeasured | ⚠️ UNMEASURED (blank-ref: ref crop std 0.0056 < 0.05 — capture failure, not impl evidence) |
| footer | 0 | 0 | ok | ✅ |

**Result: 2 PASS, 0 FAIL, 1 SKIP, 0 STRUCTURAL_ONLY**
"""


def _ref_with_result(tmp_path: Path, body: str) -> Path:
    ref = tmp_path / "ref"
    ref.mkdir(parents=True, exist_ok=True)
    sections = ref / "sections"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "result.txt").write_text(body, encoding="utf-8")
    return ref


def test_gate_fails_when_a_section_went_unmeasured(tmp_path: Path) -> None:
    ref = _ref_with_result(tmp_path, _UNMEASURED_TABLE)
    results = Gate(ref).gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert failures, "an UNMEASURED row must block the gate, not pass as SKIP"
    blob = " ".join(f"{r.message} {r.fix}" for r in failures)
    assert "unmeasured" in blob.lower()
    # The remedy is on the capture side. Telling the agent to fix the impl for a
    # blank REFERENCE crop sends it to iterate on the wrong artifact.
    assert "re-capture" in blob.lower() or "capture" in blob.lower()


def test_gate_still_passes_a_genuinely_clean_run(tmp_path: Path) -> None:
    clean = _UNMEASURED_TABLE.replace(
        "| style_slideshow_7xln1 | — | — | unmeasured | ⚠️ UNMEASURED (blank-ref: ref crop std 0.0056 < 0.05 — capture failure, not impl evidence) |\n",
        "| style_slideshow_7xln1 | 0 | 0 | ok | ✅ |\n",
    ).replace("2 PASS, 0 FAIL, 1 SKIP", "3 PASS, 0 FAIL, 0 SKIP")
    ref = _ref_with_result(tmp_path, clean)
    results = Gate(ref).gate_section_compare()
    failures = [r for r in results if r.status == "fail"]
    assert not failures, f"a fully measured all-pass run must still pass: {failures}"


def test_stop_hook_reads_unmeasured_run_as_honest_non_success(tmp_path: Path) -> None:
    """The Stop hook's honesty detector only looked at PASS/FAIL counts, so an
    all-PASS line above unmeasured rows read as a success claim. The producer and
    the detector shared one blind spot; closing it in the producer alone would
    leave the run unable to release its (honest) terminal failed state.

    Both routes must work: the canonical UNMEASURED field, and — for artifacts
    written before that field existed — the per-row admission."""
    from ui_clone.hooks.section_gate import _result_txt_claims_success

    canonical = _UNMEASURED_TABLE.replace(
        "**Result: 2 PASS, 0 FAIL, 1 SKIP, 0 STRUCTURAL_ONLY**",
        "**Result: 2 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 1 UNMEASURED**",
    )
    assert _result_txt_claims_success(_ref_with_result(tmp_path / "a", canonical)) is False
    # Legacy 4-field artifact: only the row is left to go on.
    assert _result_txt_claims_success(_ref_with_result(tmp_path / "b", _UNMEASURED_TABLE)) is False


def test_a_section_merely_named_unmeasured_does_not_flip_the_verdict(tmp_path: Path) -> None:
    # The row matcher is anchored on the severity cell. A loose match would let a
    # planted section name turn any success-claiming result.txt into releasable.
    from ui_clone.hooks.section_gate import _result_txt_claims_success

    body = (
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---------|-----|--------|----------|--------|\n"
        "| unmeasured-hero | 0 | 0 | ok | ✅ |\n"
        "\n**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY, 0 UNMEASURED**\n"
    )
    assert _result_txt_claims_success(_ref_with_result(tmp_path, body)) is True


def test_stop_hook_still_blocks_a_clean_success_claim(tmp_path: Path) -> None:
    from ui_clone.hooks.section_gate import _result_txt_claims_success

    ref = _ref_with_result(
        tmp_path,
        "| hero | 0 | 0 | ok | ✅ |\n\n**Result: 3 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n",
    )
    assert _result_txt_claims_success(ref) is True
