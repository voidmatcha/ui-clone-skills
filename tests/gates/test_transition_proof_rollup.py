"""Tests for transition-proof-rollup.sh hover-state validity gate.

GAP 1 (silent-pass): transition-proof-rollup.sh ingests
`transitions/hover-state-result.txt` into the `expected` set from the
verification-plan's `produces` rows, but never validates the artifact. When a
site's plan contains the `hover-state-compare` row and hover-state-compare.sh
emits `✅ no hover regions found in regions.json — nothing to compare` (exit 0),
the hover motion-arc check has silently skipped — yet the rollup composes PASS.

These tests invoke the script via subprocess against a tmp ref-dir (matching the
JSON-artifact gate tests in test_misc_a2.py) and assert exit code + the parsed
transition-proof.json status / reasons.
"""

import json
import subprocess
from pathlib import Path

from ._helpers import _project_root

_SCRIPT = (
    _project_root()
    / "skills"
    / "visual-debug"
    / "scripts"
    / "transition-proof-rollup.sh"
)

# Real silent-skip body emitted by hover-state-compare.sh line 126.
_SKIP_BODY = (
    "# hover-state-compare\n"
    "# generated: 2026-05-25T13:29:55Z\n"
    "# max targets: 5\n"
    "# exit capture: 0 (mode: hover:<sel>)\n"
    "\n"
    "✅ no hover regions found in regions.json — nothing to compare\n"
)


def _plan_with_hover() -> str:
    """A verification-plan whose requiredChecks include the hover row, so the
    rollup ingests `transitions/hover-state-result.txt` into `expected`."""
    return json.dumps(
        {
            "requiredChecks": [
                {
                    "id": "hover-state-compare",
                    "produces": "transitions/hover-state-result.txt",
                    "severity": "blocking",
                    "min_tier": "comprehensive",
                }
            ]
        }
    )


def _plan_without_hover() -> str:
    return json.dumps(
        {
            "requiredChecks": [
                {
                    "id": "transition-fires",
                    "produces": "transition-fires.json",
                    "severity": "blocking",
                }
            ]
        }
    )


def _write_hover_result(ref: Path, body: str) -> None:
    (ref / "transitions").mkdir(parents=True, exist_ok=True)
    (ref / "transitions" / "hover-state-result.txt").write_text(body, encoding="utf-8")


def _run(ref: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_SCRIPT), str(ref)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _component(ref: Path, artifact: str) -> dict:
    payload = json.loads((ref / "transition-proof.json").read_text())
    for c in payload["components"]:
        if c["artifact"] == artifact:
            assert isinstance(c, dict)
            return c
    raise AssertionError(
        f"{artifact} not found in transition-proof.json components: "
        f"{[c['artifact'] for c in payload['components']]}"
    )


# ---------------------------------------------------------------------------
# GAP 1 — hover-state silent skip while plan required the hover row.
# ---------------------------------------------------------------------------


def test_hover_silent_skip_fails_when_plan_required_it(tmp_path: Path) -> None:
    """Regression test for the gap: plan demanded hover-state-compare, but the
    artifact reads `no hover regions found ... nothing to compare` — the hover
    motion-arc check never ran, so the rollup MUST fail (not silently pass)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(_plan_with_hover(), encoding="utf-8")
    _write_hover_result(ref, _SKIP_BODY)

    proc = _run(ref)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    )
    payload = json.loads((ref / "transition-proof.json").read_text())
    assert payload["status"] == "fail", payload
    comp = _component(ref, "transitions/hover-state-result.txt")
    assert comp["valid"] is False
    assert any("hover-state" in r for r in payload["reasons"]), payload["reasons"]


def test_hover_real_pass_run_is_valid(tmp_path: Path) -> None:
    """Plan required hover and the artifact records a real clean run — the hover
    component must be valid (exit 0)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(_plan_with_hover(), encoding="utf-8")
    _write_hover_result(
        ref,
        "# hover-state-compare\n\n✅ all 5 hover target-run(s) within SSIM threshold\n",
    )

    proc = _run(ref)
    assert proc.returncode == 0, (
        f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    )
    comp = _component(ref, "transitions/hover-state-result.txt")
    assert comp["valid"] is True, comp


def test_hover_skip_valid_when_plan_did_not_require_it(tmp_path: Path) -> None:
    """No false positive: a `no hover regions` artifact is valid when the plan
    never added the hover row (produces key absent from `expected`)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        _plan_without_hover(), encoding="utf-8"
    )
    # transition-fires.json present + passing so that check doesn't fail us.
    (ref / "transition-fires.json").write_text(
        json.dumps({"status": "pass", "total": 1, "fired": 1}), encoding="utf-8"
    )
    _write_hover_result(ref, _SKIP_BODY)

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")
    assert comp["valid"] is True, comp
    # The hover component must not be the thing that fails the rollup.
    payload = json.loads((ref / "transition-proof.json").read_text())
    assert not any("hover-state" in r for r in payload["reasons"]), payload["reasons"]
    assert proc.returncode == 0, (
        f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    )


def test_hover_divergence_fails(tmp_path: Path) -> None:
    """A real run with diverged target-runs must fail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(_plan_with_hover(), encoding="utf-8")
    _write_hover_result(
        ref,
        "# hover-state-compare\n\n❌ 2/5 hover target-run(s) diverged\n",
    )

    proc = _run(ref)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    )
    comp = _component(ref, "transitions/hover-state-result.txt")
    assert comp["valid"] is False, comp
    assert "diverged" in comp["note"], comp


def test_hover_no_regions_skip_string_also_fails_when_required(tmp_path: Path) -> None:
    """The alternate skip string (`no regions.json — hover-state compare
    skipped`, line 104) is also a silent skip and must fail when required."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(_plan_with_hover(), encoding="utf-8")
    _write_hover_result(
        ref,
        "# hover-state-compare\n\n✅ no regions.json — hover-state compare skipped\n",
    )

    proc = _run(ref)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    )
    comp = _component(ref, "transitions/hover-state-result.txt")
    assert comp["valid"] is False, comp


def test_hover_artifact_missing_but_required_fails(tmp_path: Path) -> None:
    """Plan required the hover row but the artifact never got produced — fail."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(_plan_with_hover(), encoding="utf-8")
    # No hover-state-result.txt written.

    proc = _run(ref)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    )
    comp = _component(ref, "transitions/hover-state-result.txt")
    assert comp["valid"] is False, comp
    assert "missing" in comp["note"], comp
