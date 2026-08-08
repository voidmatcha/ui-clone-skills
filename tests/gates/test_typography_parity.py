"""typography-parity — per-element font-weight / letter-spacing / body-rule diff.

Evidence class (omx navercorp run): impl shipped without the ref's global
`letter-spacing: -0.5px` body rule and rendered headings at font-weight
400/600 where the ref uses 800/900. AE flags the pixel diff but buries the
cause; font-parity only compares the primary font *family*, so both gates
stayed silent about weight/tracking divergence.
"""

import json
import os
import subprocess
from pathlib import Path

from ._helpers import _project_root, _run_verification_plan


def test_verification_plan_emits_typography_parity_row(tmp_path: Path) -> None:
    """Universal row: every page renders text, so the check is always on.

    Tier=standard (one browser pair, same cost class as font-parity),
    severity=block — a missing global tracking rule or wrong heading weight
    is a generation defect, not a style choice.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    plan = _run_verification_plan(ref)
    rows = {c["id"]: c for c in plan["requiredChecks"]}
    assert "typography-parity" in rows, (
        f"typography-parity row missing from plan: {sorted(rows)}"
    )
    row = rows["typography-parity"]
    assert row["produces"] == "typography-parity.json"
    assert row["severity"] == "block"
    assert row["tier"] == "standard"
    assert row["script"].endswith("typography-parity-check.sh")


def test_run_required_checks_has_typography_parity_signature() -> None:
    """Every plan row needs a dispatcher SIGNATURES entry or it NOSIG-skips."""
    dispatcher = _project_root() / "scripts" / "verify" / "build_required_dispatch.py"
    text = dispatcher.read_text(encoding="utf-8")
    assert "typography-parity-check.sh" in text, (
        "typography-parity-check.sh missing from dispatcher SIGNATURES — "
        "dispatcher will NOSIG-skip it."
    )


def test_typography_parity_script_exists_and_emits_status_contract() -> None:
    """The script must exist and document the status pass/fail contract the
    generic verification-plan JSON enforcement keys on."""
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "typography-parity-check.sh"
    )
    assert script.is_file(), "typography-parity-check.sh not found"
    body = script.read_text(encoding="utf-8")
    assert '"status"' in body or "status" in body, (
        "script must emit a status field for the plan gate to enforce"
    )
    assert "letterSpacing" in body or "letter-spacing" in body
    assert "fontWeight" in body or "font-weight" in body


# ── duplicate-sig pairing (loop-e2e-9 self-fail evidence) ───────────────────
#
# tmp/ref/realfood-e2e-9/brief/new-gate-self-fail-evidence.json: the page has
# four `span|Real Food` elements (fw 400/600/700/700). The old pairing built
# `implBySig = new Map(...)` so duplicate sigs collapsed last-wins — every ref
# instance compared against the single LAST impl instance (fw700), and the
# REF FAILED AGAINST ITSELF. Pairing must zip the k-th ref instance with the
# k-th impl instance within each sig group.


def _el(sig: str, fw: str, ls: str = "0px") -> dict:
    return {"sig": sig, "fontWeight": fw, "letterSpacing": ls}


_BODY = {"fontWeight": "400", "letterSpacing": "0px"}


def _run_fixture(tmp_path: Path, ref_payload: dict, impl_payload: dict) -> dict:
    """Run the script in fixture mode (no browser): probe payloads from files."""
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir(exist_ok=True)
    ref_f = tmp_path / "ref-raw.json"
    impl_f = tmp_path / "impl-raw.json"
    ref_f.write_text(json.dumps(ref_payload))
    impl_f.write_text(json.dumps(impl_payload))
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "typography-parity-check.sh"
    )
    env = os.environ.copy()
    env["TYPO_PARITY_RAW_REF"] = str(ref_f)
    env["TYPO_PARITY_RAW_IMPL"] = str(impl_f)
    proc = subprocess.run(
        ["bash", str(script), "typo-fixture", "http://ref.test", "http://impl.test", str(ref_dir)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads((ref_dir / "typography-parity.json").read_text())  # type: ignore[no-any-return]


def test_duplicate_sigs_ref_vs_ref_passes(tmp_path: Path) -> None:
    """Self-test property: identical payloads on both sides must pass even
    when one sig appears at several different weights (the e2e-9 case)."""
    payload = {
        "body": _BODY,
        "els": [
            _el("span|Real Food", "400"),
            _el("span|Real Food", "600"),
            _el("span|Real Food", "700"),
            _el("span|Real Food", "700"),
        ],
    }
    out = _run_fixture(tmp_path, payload, payload)
    assert out["status"] == "pass", out
    assert out["pairedElements"] == 4


def test_duplicate_sigs_real_mismatch_still_fails(tmp_path: Path) -> None:
    """Index-zip must not absorb genuine weight divergence: ref 400/600 vs
    impl 700/700 is two real mismatches."""
    ref = {"body": _BODY, "els": [_el("span|Real Food", "400"), _el("span|Real Food", "600")]}
    impl = {"body": _BODY, "els": [_el("span|Real Food", "700"), _el("span|Real Food", "700")]}
    out = _run_fixture(tmp_path, ref, impl)
    assert out["status"] == "fail", out
    assert len(out["elementMismatches"]) == 2


def test_unequal_sig_group_sizes_pair_min(tmp_path: Path) -> None:
    """Extra ref instances with no positional impl partner stay unpaired
    (same semantic as a sig with no impl match at all)."""
    ref = {"body": _BODY, "els": [_el("p|hello world", "400")] * 3}
    impl = {"body": _BODY, "els": [_el("p|hello world", "400")] * 2}
    out = _run_fixture(tmp_path, ref, impl)
    assert out["status"] == "pass", out
    assert out["pairedElements"] == 2
