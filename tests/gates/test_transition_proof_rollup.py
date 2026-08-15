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

import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ._helpers import _project_root

_SCRIPT = _project_root() / "skills" / "visual-debug" / "scripts" / "transition-proof-rollup.sh"
_DRIVER = _SCRIPT.with_name("transition_proof_rollup.py")

def test_python_driver_is_standalone() -> None:
    shell = _SCRIPT.read_text(encoding="utf-8")
    assert "<<'PY'" not in shell
    assert 'python3 "$SCRIPTS_DIR/transition_proof_rollup.py"' in shell
    ast.parse(_DRIVER.read_text(encoding="utf-8"), filename=str(_DRIVER))


def test_transition_proof_rollup_runs_under_macos_system_python(
    tmp_path: Path,
) -> None:
    """The dispatcher may invoke this rollup through macOS /usr/bin/python3."""
    host_python = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else shutil.which("python3")
    if not host_python:
        pytest.skip("python3 not available")

    ref = tmp_path / "ref"
    ref.mkdir()
    out = ref / "transition-proof.json"
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "hero-hover", "trigger": "hover"}]}),
        encoding="utf-8",
    )
    (ref / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [".hero"]}),
        encoding="utf-8",
    )
    (ref / "transition-fires.json").write_text(
        json.dumps({
            "status": "pass",
            "total": 1,
            "fired": 0,
            "known_skip": 1,
            "failed": 0,
            "unmeasurable": 0,
            "entries": [{"id": "hero-hover", "status": "known-skip"}],
        }),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [host_python, str(_DRIVER), str(ref), str(out)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode in {0, 1}, proc.stdout + proc.stderr
    assert "unsupported operand type(s) for |" not in proc.stdout + proc.stderr
    assert out.is_file()


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
                    "severity": "block",
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
                    "severity": "block",
                }
            ]
        }
    )


def _plan_with_partial_hover_support() -> str:
    return json.dumps(
        {
            "requiredChecks": [
                {
                    "id": "hover-state-compare",
                    "produces": "transitions/hover-state-result.txt",
                    "severity": "block",
                },
                {
                    "id": "transition-fires",
                    "produces": "transition-fires.json",
                    "severity": "block",
                },
                {
                    "id": "transition-compare",
                    "produces": "transitions/result.txt",
                    "severity": "block",
                },
                {
                    "id": "transition-proof",
                    "produces": "transition-proof.json",
                    "severity": "block",
                },
            ]
        }
    )


def _write_partial_hover_support(ref: Path) -> None:
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "00-auto-hover-2",
                        "trigger": "hover",
                        "target": ".nav-link",
                    },
                    {
                        "id": "01-auto-hover-3",
                        "trigger": "hover",
                        "target": ".nav-link",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": 2,
                "fired": 2,
                "known_skip": 0,
                "failed": 0,
                "unmeasurable": 0,
                "entries": [
                    {
                        "id": "00-auto-hover-2",
                        "trigger": "hover",
                        "type": "css-hover",
                        "kind": "hover",
                        "status": "pass",
                    },
                    {
                        "id": "01-auto-hover-3",
                        "trigger": "hover",
                        "type": "css-hover",
                        "kind": "hover",
                        "status": "pass",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (ref / "transitions").mkdir(parents=True, exist_ok=True)
    (ref / "transitions" / "result.txt").write_text(
        "Transition compare: 2 PASS, 0 FAIL\n"
        "✅ PASS .first\n"
        "✅ PASS .second\n",
        encoding="utf-8",
    )


def _partial_hover_body(
    *,
    clean_verdict: str = "clean",
    unmeasurable: int = 1,
    fallback_failed: int = 0,
    include_clean: bool = True,
) -> str:
    clean_line = (
        f"✅ auto-hover-2 {clean_verdict} [single]\n"
        if include_clean
        else ""
    )
    unmeasurable_lines = "".join(
        f"⚠️ auto-hover-{index + 3} unmeasurable-after-retry [single] — status 2\n"
        for index in range(unmeasurable)
    )
    measured = unmeasurable + (1 if include_clean else 1)
    return (
        "# hover-state-compare\n"
        "## auto-hover-2 (hover) [single]\n"
        "selector: .nav-link-arrow\n"
        "## auto-hover-3 (hover) [single]\n"
        "selector: .nav-link\n"
        f"{clean_line}"
        f"{unmeasurable_lines}"
        "hover-fallback: status=pass verified=0 static=4 failed="
        f"{fallback_failed}\n"
        f"# coverage: measured={measured} failed=0 "
        f"unmeasurable={unmeasurable} fallbackFailed={fallback_failed}\n"
        f"⚠️ {unmeasurable}/{measured} hover target-run(s) unmeasurable\n"
    )


def _write_hover_result(ref: Path, body: str) -> None:
    (ref / "transitions").mkdir(parents=True, exist_ok=True)
    (ref / "transitions" / "hover-state-result.txt").write_text(body, encoding="utf-8")


def _run(ref: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_SCRIPT), str(ref)],
        capture_output=True,
        text=True,
        timeout=120,
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
    (ref / "verification-plan.json").write_text(_plan_without_hover(), encoding="utf-8")
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


# ---------------------------------------------------------------------------
# GAP 2 (e2e-7 closeout, 2026-06-11) — all-known-skip hover runs are NOT
# vacuous. Sites whose every extracted :hover rule targets mount-gated overlay
# UI (mobile-nav, video-player-modal, lightbox, .pac-item, toasts) legitimately
# execute 0 hover target-runs: each target line carries a documented
# `known-skip:` reason (asset-substitution skips[] or absence parity verified
# live). The vacuous rule must only fire when 0 runs AND 0 documented skips.
# ---------------------------------------------------------------------------

# Real all-known-skip body shape emitted by hover-state-compare.sh on
# realfood-e2e-7 (tmp/ref/realfood-e2e-7/transitions/hover-state-result.txt).
_ALL_KNOWN_SKIP_BODY = (
    "# hover-state-compare\n"
    "# generated: 2026-06-11T10:09:32Z\n"
    "# max targets: 5\n"
    "# exit capture: 0 (mode: hover:<sel>)\n"
    "\n"
    "## .lightbox_nav_button__oC4Mw (synth-hover-css) — known-skip: documented"
    " conditionally-mounted overlay target (asset-substitution skips[])\n"
    "## .mobile-nav_menu_item__pzL21 (synth-hover-css) — known-skip: documented"
    " conditionally-mounted overlay target (asset-substitution skips[])\n"
    "# viewports: <single (caller VIEW_W/VIEW_H)>\n"
    "\n"
    "## .pac-item (synth-hover-css) [single] — known-skip: selector absent on"
    " BOTH ref and impl at idle (mount-gated UI; absence parity verified by"
    " live probe)\n"
    "\n"
    "✅ all 0 hover target-run(s) within SSIM threshold\n"
)


def test_hover_all_targets_known_skip_is_valid(tmp_path: Path) -> None:
    """0 executed runs is the CORRECT outcome when every selected hover target
    is a documented known-skip — the rollup must treat the artifact as valid
    (real coverage is carried by transition-compare's hover rows)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(_plan_with_hover(), encoding="utf-8")
    _write_hover_result(ref, _ALL_KNOWN_SKIP_BODY)

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")
    assert comp["valid"] is True, comp
    assert "known-skip" in comp["note"], comp
    payload = json.loads((ref / "transition-proof.json").read_text())
    assert not any("hover-state" in r for r in payload["reasons"]), payload["reasons"]
    assert proc.returncode == 0, (
        f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    )


def test_hover_zero_runs_without_documented_skips_stays_vacuous(
    tmp_path: Path,
) -> None:
    """Anti-bypass: a 0-run tally WITHOUT documented known-skip target lines is
    still vacuous — target selection silently produced nothing and the motion
    arc was never measured."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(_plan_with_hover(), encoding="utf-8")
    _write_hover_result(
        ref,
        "# hover-state-compare\n\n✅ all 0 hover target-run(s) within SSIM threshold\n",
    )

    proc = _run(ref)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    )
    comp = _component(ref, "transitions/hover-state-result.txt")
    assert comp["valid"] is False, comp
    assert "vacuous" in comp["note"], comp


def test_hover_measured_wording_from_per_entry_accounting_is_parsed(
    tmp_path: Path,
) -> None:
    """Review-2 per-entry-accounting hardening changed hover-state-compare.sh's
    pass tally to 'all N measured hover target-run(s) ... ; fallback probe
    covered the rest'. The rollup regex must accept both wordings — a writer/
    parser drift here invalidates every post-hardening hover artifact
    (e2e-11 regression, 2026-06-12)."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(_plan_with_hover(), encoding="utf-8")
    _write_hover_result(
        ref,
        _ALL_KNOWN_SKIP_BODY.replace(
            "✅ all 0 hover target-run(s) within SSIM threshold\n",
            "hover-fallback: status=pass verified=2 static=4 failed=0\n"
            "\n"
            "# coverage: measured=0 failed=0 fallbackFailed=0\n"
            "✅ all 0 measured hover target-run(s) within SSIM threshold;"
            " fallback probe covered the rest\n",
        ),
    )

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")
    assert comp["valid"] is True, comp
    assert proc.returncode == 0, (
        f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    )


@pytest.mark.parametrize("clean_verdict", ["clean", "pass-after-retry"])
def test_hover_partial_with_explicit_clean_target_and_orthogonal_proof_is_valid(
    tmp_path: Path,
    clean_verdict: str,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        _plan_with_partial_hover_support(),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)
    _write_hover_result(
        ref,
        _partial_hover_body(clean_verdict=clean_verdict),
    )

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert comp["valid"] is True, comp
    assert "PARTIAL" in comp["note"], comp


def test_hover_partial_binds_deduped_generated_identity_by_exact_selector(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        _plan_with_partial_hover_support(),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)

    spec = json.loads((ref / "transition-spec.json").read_text())
    spec["transitions"] = [
        {
            "id": "00-header-nav-link",
            "trigger": "hover",
            "target": ".nav__item",
        },
        {
            "id": "00-header-nav-link-2",
            "trigger": "hover",
            "target": "a.nclick-target.nav__link",
        },
    ]
    (ref / "transition-spec.json").write_text(
        json.dumps(spec),
        encoding="utf-8",
    )
    fires = json.loads((ref / "transition-fires.json").read_text())
    fires["entries"][0]["id"] = "00-header-nav-link"
    fires["entries"][1]["id"] = "00-header-nav-link-2"
    (ref / "transition-fires.json").write_text(
        json.dumps(fires),
        encoding="utf-8",
    )
    _write_hover_result(
        ref,
        "# hover-state-compare\n"
        "## header-nav-item (hover) [single]\n"
        "selector: .nav__item\n"
        "## header-nav-link (hover) [single]\n"
        "selector: a.nclick-target.nav__link\n"
        "✅ header-nav-item clean [single]\n"
        "⚠️ header-nav-link unmeasurable-after-retry [single] — status 2\n"
        "hover-fallback: status=pass verified=0 static=4 failed=0\n"
        "# coverage: measured=2 failed=0 unmeasurable=1 fallbackFailed=0\n"
        "⚠️ 1/2 hover target-run(s) unmeasurable\n",
    )

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert comp["valid"] is True, comp
    assert "PARTIAL" in comp["note"], comp


def test_hover_partial_does_not_strip_numeric_identity_without_collision_base(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        _plan_with_partial_hover_support(),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)
    spec = json.loads((ref / "transition-spec.json").read_text())
    spec["transitions"][1] = {
        "id": "01-card-2024",
        "trigger": "hover",
        "target": ".nav-link",
    }
    (ref / "transition-spec.json").write_text(
        json.dumps(spec),
        encoding="utf-8",
    )
    fires = json.loads((ref / "transition-fires.json").read_text())
    fires["entries"][1]["id"] = "01-card-2024"
    (ref / "transition-fires.json").write_text(
        json.dumps(fires),
        encoding="utf-8",
    )
    _write_hover_result(
        ref,
        _partial_hover_body().replace("auto-hover-3", "card"),
    )

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp
    assert "matches=0" in comp["note"], comp


def test_hover_partial_all_unmeasurable_is_invalid(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        _plan_with_partial_hover_support(),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)
    _write_hover_result(
        ref,
        (
            "# hover-state-compare\n"
            "⚠️ auto-hover-2 unmeasurable-after-retry [single] — status 2\n"
            "⚠️ auto-hover-3 unmeasurable-after-retry [single] — status 2\n"
            "hover-fallback: status=pass verified=0 static=4 failed=0\n"
            "# coverage: measured=2 failed=0 unmeasurable=2 fallbackFailed=0\n"
            "⚠️ 2/2 hover target-run(s) unmeasurable\n"
        ),
    )

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp
    assert "all 2" in comp["note"], comp


def test_hover_partial_without_explicit_clean_marker_is_invalid(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        _plan_with_partial_hover_support(),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)
    _write_hover_result(
        ref,
        _partial_hover_body(include_clean=False),
    )

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp
    assert "explicit clean" in comp["note"], comp


def test_hover_partial_requires_planned_orthogonal_proof(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        _plan_with_hover(),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)
    _write_hover_result(ref, _partial_hover_body())

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp
    assert "blocking companion" in comp["note"], comp


def test_hover_partial_requires_target_bound_transition_fire(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        _plan_with_partial_hover_support(),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)
    fires = json.loads((ref / "transition-fires.json").read_text())
    fires["entries"][1]["id"] = "01-auto-hover-other"
    fires["fired"] = 2
    (ref / "transition-fires.json").write_text(
        json.dumps(fires),
        encoding="utf-8",
    )
    spec = json.loads((ref / "transition-spec.json").read_text())
    spec["transitions"][1]["id"] = "01-auto-hover-other"
    (ref / "transition-spec.json").write_text(
        json.dumps(spec),
        encoding="utf-8",
    )
    _write_hover_result(ref, _partial_hover_body())

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp
    assert "exactly one hover spec" in comp["note"], comp


@pytest.mark.parametrize(
    ("tamper", "expected_note"),
    [
        ("wrong-selector", "same generated identity and selector"),
        ("degraded-fire", "exact passing hover fire receipt"),
        ("non-hover-fire", "exact passing hover fire receipt"),
    ],
)
def test_hover_partial_rejects_inexact_target_fire_receipts(
    tmp_path: Path,
    tamper: str,
    expected_note: str,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        _plan_with_partial_hover_support(),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)
    if tamper == "wrong-selector":
        spec = json.loads((ref / "transition-spec.json").read_text())
        spec["transitions"][1]["target"] = ".unrelated"
        (ref / "transition-spec.json").write_text(
            json.dumps(spec),
            encoding="utf-8",
        )
    else:
        fires = json.loads((ref / "transition-fires.json").read_text())
        if tamper == "degraded-fire":
            fires["entries"][1]["status"] = "degraded"
        else:
            fires["entries"][1]["trigger"] = "scroll"
            fires["entries"][1]["type"] = "scroll"
            fires["entries"][1]["kind"] = "scroll"
        (ref / "transition-fires.json").write_text(
            json.dumps(fires),
            encoding="utf-8",
        )
    _write_hover_result(ref, _partial_hover_body())

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp
    assert expected_note in comp["note"], comp


def test_hover_partial_divergence_remains_invalid(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        _plan_with_partial_hover_support(),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)
    _write_hover_result(
        ref,
        _partial_hover_body()
        + "❌ 1/2 hover target-run(s) diverged\n",
    )

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp
    assert "diverged" in comp["note"], comp


def test_hover_partial_fallback_failure_remains_invalid(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        _plan_with_partial_hover_support(),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)
    _write_hover_result(
        ref,
        _partial_hover_body(fallback_failed=1),
    )

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp
    assert "fallback" in comp["note"], comp


def test_hover_partial_legacy_coverage_cannot_hide_unmeasurable_verdict(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "verification-plan.json").write_text(
        _plan_with_partial_hover_support(),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)
    body = _partial_hover_body().replace(" unmeasurable=1", "")
    _write_hover_result(ref, body)

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp
    assert "omits unmeasurable" in comp["note"], comp


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "unrelated-check"),
        ("produces", "transitions/unrelated.txt"),
        ("severity", "warn"),
    ],
)
def test_hover_partial_requires_exact_blocking_companion_rows(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    plan = json.loads(_plan_with_partial_hover_support())
    transition_compare = next(
        row
        for row in plan["requiredChecks"]
        if row["id"] == "transition-compare"
    )
    transition_compare[field] = value
    (ref / "verification-plan.json").write_text(
        json.dumps(plan),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)
    _write_hover_result(ref, _partial_hover_body())

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp
    assert "blocking companion" in comp["note"], comp


def test_hover_partial_rejects_duplicate_companion_plan_row(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    plan = json.loads(_plan_with_partial_hover_support())
    plan["requiredChecks"].append(
        {
            "id": "transition-compare",
            "produces": "transitions/result.txt",
            "severity": "block",
        }
    )
    (ref / "verification-plan.json").write_text(
        json.dumps(plan),
        encoding="utf-8",
    )
    _write_partial_hover_support(ref)
    _write_hover_result(ref, _partial_hover_body())

    proc = _run(ref)
    comp = _component(ref, "transitions/hover-state-result.txt")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp
    assert "blocking companion" in comp["note"], comp


def test_transition_compare_hover_unverified_is_partial_not_full_parity(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    (ref / "transitions").mkdir(parents=True)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "requiredChecks": [
                    {
                        "id": "transition-compare",
                        "produces": "transitions/result.txt",
                        "severity": "block",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "transitions" / "result.txt").write_text(
        "Transition compare: 2 PASS, 0 FAIL\n"
        "✅ PASS .verified\n"
        "✅ PASS .abstained\n"
        "    ⚠ HOVER_UNVERIFIED: pointer did not reach target\n",
        encoding="utf-8",
    )

    proc = _run(ref)
    comp = _component(ref, "transitions/result.txt")

    assert proc.returncode == 0
    assert comp["valid"] is True, comp
    assert "PARTIAL" in comp["note"], comp


@pytest.mark.parametrize(
    "body",
    [
        (
            "Transition compare: 2 PASS, 0 FAIL\n"
            "✅ PASS .only-one\n"
        ),
        (
            "Transition compare: 1 PASS, 0 FAIL\n"
            "Transition compare: 1 PASS, 0 FAIL\n"
            "✅ PASS .duplicate-summary\n"
        ),
        "Transition compare: 0 PASS, 0 FAIL\n",
    ],
)
def test_transition_compare_rejects_unreconciled_text_receipts(
    tmp_path: Path,
    body: str,
) -> None:
    ref = tmp_path / "ref"
    (ref / "transitions").mkdir(parents=True)
    (ref / "verification-plan.json").write_text(
        json.dumps(
            {
                "requiredChecks": [
                    {
                        "id": "transition-compare",
                        "produces": "transitions/result.txt",
                        "severity": "block",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "transitions" / "result.txt").write_text(body, encoding="utf-8")

    proc = _run(ref)
    comp = _component(ref, "transitions/result.txt")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp


# ---------------------------------------------------------------------------
# F9 — the reset-only-hover skip must not mask an EXISTING artifact's failure.
# ---------------------------------------------------------------------------


def _all_known_skip_fires(ref: Path, total: int = 1) -> None:
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": total,
                "fired": 0,
                "known_skip": total,
                "failed": 0,
                "unmeasurable": 0,
                "entries": [{"id": f"e{index}", "status": "known-skip"} for index in range(total)],
            }
        ),
        encoding="utf-8",
    )


def _spec(ref: Path, trigger: str) -> None:
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "e0", "trigger": trigger, "target": ".x"}]}),
        encoding="utf-8",
    )


def test_video_motion_failure_not_masked_when_a_scroll_entry_was_known_skipped(
    tmp_path: Path,
) -> None:
    """F9: an all-known-skip tally is NOT proof the specs were reset-only hovers —
    an entry can be known-skip because it is absent from the ref page or listed in
    asset-substitution skips[]. When a SCROLL entry was known-skipped, video-motion
    is a relevant probe and an existing ❌ verdict must NOT be masked as
    'reset-only hover specs'."""
    ref = tmp_path / "ref"
    (ref / "transitions").mkdir(parents=True)
    _all_known_skip_fires(ref)
    _spec(ref, "scroll-scrub")
    (ref / "transitions" / "video-motion-result.txt").write_text(
        "# video-motion-compare\nPass: 0 Fail: 3\n# video-motion-compare: COMPLETE\n",
        encoding="utf-8",
    )
    _run(ref)
    comp = _component(ref, "transitions/video-motion-result.txt")
    assert comp["valid"] is False, (
        f"a scroll entry that was known-skipped must not let a video-motion FAIL be "
        f"masked as reset-only-hover; got {comp}"
    )
    assert "fail" in comp["note"].lower(), comp


def test_video_motion_ansi_colored_splash_failure_not_masked_by_plain_scroll_pass(
    tmp_path: Path,
) -> None:
    """B3 (proven on realfood-e2e-11): video-transition-compare prints the SSIM
    tally colored ('Pass: <GREEN>N</>, Fail: <RED>M</>') with no TTY guard, so
    on a multi-mode run the colored splash tally is invisible to a \\s*\\d+
    regex. A single re.search then matched only the later PLAIN scroll tally and
    rolled up a splash mode with 126 failing frames as pass. Strip ANSI, honor
    the multi-mode summary line, and sum every mode's tally."""
    ref = tmp_path / "ref"
    (ref / "transitions").mkdir(parents=True)
    _all_known_skip_fires(ref)
    _spec(ref, "scroll-scrub")
    (ref / "transitions" / "video-motion-result.txt").write_text(
        "# video-motion-compare\n"
        "▸ splash mode\n"
        "Pass: \x1b[0;32m234\x1b[0m, Fail: \x1b[0;31m126\x1b[0m\n"
        "▸ scroll mode\n"
        "Pass: 25, Fail: 0\n"
        "❌ 1/2 mode(s) diverged — tighten easing / threshold params\n"
        "# video-motion-compare: COMPLETE\n",
        encoding="utf-8",
    )
    _run(ref)
    comp = _component(ref, "transitions/video-motion-result.txt")
    assert comp["valid"] is False, (
        f"126 failing splash frames must not be masked by a plain scroll pass; got {comp}"
    )


def test_video_motion_calibrated_provisional_arc_is_valid(tmp_path: Path) -> None:
    """A refcal-rescued strict arc mismatch is diagnostic, not a final failure."""
    ref = tmp_path / "ref"
    (ref / "transitions").mkdir(parents=True)
    _all_known_skip_fires(ref)
    _spec(ref, "scroll-scrub")
    (ref / "transitions" / "video-motion-result.txt").write_text(
        "# video-motion-compare\n"
        "⚠ provisional arc timing: first-to-last-change duration differs by 114 frames (>18)\n"
        "▸ Splash strict comparison pending calibration "
        "(arc=outside-static-bound, ssimSubthresholdRows=162)\n"
        "✓ arc within the live ref-vs-ref noise floor — arc pass-by-calibration\n"
        "Pass: \x1b[0;32m259\x1b[0m, Fail: \x1b[0;31m0\x1b[0m\n"
        "ALL PASS — transition matches original\n"
        "✅ all 1 mode(s) within SSIM threshold\n"
        "# video-motion-compare: COMPLETE\n",
        encoding="utf-8",
    )

    _run(ref)
    comp = _component(ref, "transitions/video-motion-result.txt")
    payload = json.loads((ref / "transition-proof.json").read_text())

    assert comp["valid"] is True, comp
    assert "259 pass / 0 fail" in comp["note"], comp
    assert not any(
        "video-motion-result.txt" in reason for reason in payload["reasons"]
    ), payload["reasons"]


def test_video_motion_provisional_arc_does_not_mask_final_nonzero_fail(
    tmp_path: Path,
) -> None:
    """The neutral provisional marker must not override the final frame tally."""
    ref = tmp_path / "ref"
    (ref / "transitions").mkdir(parents=True)
    _all_known_skip_fires(ref)
    _spec(ref, "scroll-scrub")
    (ref / "transitions" / "video-motion-result.txt").write_text(
        "# video-motion-compare\n"
        "⚠ provisional arc timing: first-to-last-change duration differs by 114 frames (>18)\n"
        "Pass: 258, Fail: 1\n"
        "# video-motion-compare: COMPLETE\n",
        encoding="utf-8",
    )

    proc = _run(ref)
    comp = _component(ref, "transitions/video-motion-result.txt")
    payload = json.loads((ref / "transition-proof.json").read_text())

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert comp["valid"] is False, comp
    assert "258 pass / 1 fail" in comp["note"], comp
    assert any(
        "video-motion-result.txt" in reason for reason in payload["reasons"]
    ), payload["reasons"]


def test_video_motion_failure_masked_only_for_all_hover_reset_only_specs(
    tmp_path: Path,
) -> None:
    """The legitimate case is preserved: when EVERY spec entry is a hover trigger
    (a page with no scroll/splash motion), a video-motion 'failure' is measurement
    noise and is correctly skipped as reset-only-hover."""
    ref = tmp_path / "ref"
    (ref / "transitions").mkdir(parents=True)
    _all_known_skip_fires(ref)
    _spec(ref, "hover")
    (ref / "transitions" / "video-motion-result.txt").write_text(
        "# video-motion-compare\nPass: 0 Fail: 3\n# video-motion-compare: COMPLETE\n",
        encoding="utf-8",
    )
    _run(ref)
    comp = _component(ref, "transitions/video-motion-result.txt")
    assert comp["valid"] is True, (
        f"all-hover reset-only specs should still mask video-motion noise; got {comp}"
    )
    assert "reset-only" in comp["note"], comp


def test_declaration_only_coverage_requires_actually_fired_motion(tmp_path: Path) -> None:
    """VERIFY-M1: a Phase-6d transition-coverage.json with declaration-only
    elements (no >=2-sample runtime probe) leans on runtime_proof_sources(). A
    transition-fires run that PASSED but where NOTHING fired ('0/5 fired (5
    unmeasurable)') must NOT count as runtime proof — the note starts with a
    digit, but zero motion was measured. Otherwise declaration-only inventory is
    certified 'runtime proof carried by transition-fires' with no motion at all."""
    ref = tmp_path / "ref"
    (ref / "transitions").mkdir(parents=True)
    (ref / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [{"selector": ".hero", "samples": []}]}), encoding="utf-8"
    )
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": 5,
                "fired": 0,
                "known_skip": 0,
                "failed": 0,
                "unmeasurable": 5,
            }
        ),
        encoding="utf-8",
    )
    _run(ref)
    comp = _component(ref, "transition-coverage.json")
    assert comp["valid"] is False, (
        f"declaration-only coverage must not pass on 0-fired transition-fires; got {comp}"
    )


def test_partial_transition_compare_cannot_supply_runtime_proof(
    tmp_path: Path,
) -> None:
    ref = tmp_path / "ref"
    (ref / "transitions").mkdir(parents=True)
    (ref / "transition-coverage.json").write_text(
        json.dumps(
            {
                "animatedElements": [
                    {
                        "selector": ".x:hover",
                        "trigger": "hover",
                        "samples": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    _all_known_skip_fires(ref)
    _spec(ref, "hover")
    (ref / "transitions" / "result.txt").write_text(
        "Transition compare: 1 PASS, 0 FAIL\n"
        "✅ PASS .abstained\n"
        "    ⚠ HOVER_UNVERIFIED: pointer did not reach target\n",
        encoding="utf-8",
    )

    proc = _run(ref)
    comp = _component(ref, "transition-coverage.json")

    assert proc.returncode == 1
    assert comp["valid"] is False, comp
    assert "no runtime proof artifact passed" in comp["note"]


@pytest.mark.parametrize(
    ("malformed_sample", "expected_type"),
    [
        pytest.param("not-an-object", "str", id="string"),
        pytest.param(["nested-array"], "list", id="array"),
        pytest.param(7, "int", id="number"),
        pytest.param(None, "NoneType", id="null"),
    ],
)
def test_transition_coverage_malformed_sample_fails_closed(
    tmp_path: Path,
    malformed_sample: object,
    expected_type: str,
) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-coverage.json").write_text(
        json.dumps(
            {
                "animatedElements": [
                    {
                        "selector": ".hero",
                        "samples": [
                            malformed_sample,
                            {"scrollY": 800, "opacity": "1"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = _run(ref)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    comp = _component(ref, "transition-coverage.json")
    assert comp["valid"] is False
    assert comp["note"] == (
        "animated element 0 sample 0 must be an object, "
        f"got {expected_type}"
    )


def test_all_unmeasurable_transition_fires_cannot_produce_composite_pass(
    tmp_path: Path,
) -> None:
    """An all-unmeasurable fire probe is an honest low-level abstention, not
    positive transition proof. Even without declaration-only coverage for
    another component to reject, the composite must fail closed when no motion
    was measured."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": 5,
                "fired": 0,
                "known_skip": 0,
                "failed": 0,
                "unmeasurable": 5,
            }
        ),
        encoding="utf-8",
    )

    proc = _run(ref)
    payload = json.loads((ref / "transition-proof.json").read_text())
    fires = _component(ref, "transition-fires.json")

    assert fires["valid"] is True, (
        f"transition-fires must preserve honest abstention semantics; got {fires}"
    )
    assert proc.returncode == 1, (
        f"zero measured motion must fail closed, got {proc.returncode}: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    assert payload["status"] == "fail", payload
    assert any("zero measured motion" in reason for reason in payload["reasons"]), payload[
        "reasons"
    ]


def test_zero_total_transition_fires_fails_when_spec_has_entries(
    tmp_path: Path,
) -> None:
    """A pass artifact with a zero denominator cannot prove a non-empty spec.

    This catches probe/setup drift where transition-fires runs but silently
    reports 0/0 even though the transition specification contains measurable
    work.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    _spec(ref, "scroll-scrub")
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": 0,
                "fired": 0,
                "known_skip": 0,
                "failed": 0,
                "unmeasurable": 0,
            }
        ),
        encoding="utf-8",
    )

    proc = _run(ref)
    payload = json.loads((ref / "transition-proof.json").read_text())
    fires = _component(ref, "transition-fires.json")

    assert proc.returncode == 1, (
        f"0/0 transition proof must fail for a non-empty spec: {proc.stdout}\n{proc.stderr}"
    )
    assert payload["status"] == "fail", payload
    assert fires["valid"] is False, fires
    assert "0 transition(s) probed" in fires["note"], fires


def test_transition_fires_denominator_must_cover_every_spec_entry(
    tmp_path: Path,
) -> None:
    """A partial probe denominator cannot certify the unprobed transitions."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {"id": "hero-load", "trigger": "page-load"},
                    {"id": "hero-scroll", "trigger": "scroll-scrub"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": 1,
                "fired": 1,
                "known_skip": 0,
                "failed": 0,
                "unmeasurable": 0,
            }
        ),
        encoding="utf-8",
    )

    proc = _run(ref)
    fires = _component(ref, "transition-fires.json")

    assert proc.returncode == 1
    assert fires["valid"] is False, fires
    assert "1/2" in fires["note"], fires


@pytest.mark.parametrize(
    "entries",
    [
        [{"id": "hero-load", "status": "pass"}],
        [
            {"id": "hero-load", "status": "pass"},
            {"id": "hero-load", "status": "pass"},
        ],
        [
            {"id": "hero-load", "status": "pass"},
            {"id": "hero-other", "status": "pass"},
        ],
    ],
    ids=["missing-id", "duplicate-id", "wrong-id"],
)
def test_transition_fires_requires_exact_spec_id_multiset(
    tmp_path: Path,
    entries: list[dict[str, str]],
) -> None:
    """Matching denominators cannot hide missing, duplicate, or foreign IDs."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {"id": "hero-load", "trigger": "page-load"},
                    {"id": "hero-scroll", "trigger": "scroll-scrub"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": 2,
                "fired": 2,
                "known_skip": 0,
                "failed": 0,
                "unmeasurable": 0,
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )

    proc = _run(ref)
    fires = _component(ref, "transition-fires.json")

    assert proc.returncode == 1
    assert fires["valid"] is False, fires
    assert "identit" in fires["note"].lower(), fires


def test_transition_fires_recomputes_status_tallies_from_entries(
    tmp_path: Path,
) -> None:
    """Self-reported summary fields must agree with the per-entry evidence."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {"id": "hero-load", "trigger": "page-load"},
                    {"id": "hero-scroll", "trigger": "scroll-scrub"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": 2,
                "fired": 2,
                "known_skip": 0,
                "failed": 0,
                "unmeasurable": 0,
                "entries": [
                    {"id": "hero-load", "status": "pass"},
                    {"id": "hero-scroll", "status": "known-skip"},
                ],
            }
        ),
        encoding="utf-8",
    )

    proc = _run(ref)
    fires = _component(ref, "transition-fires.json")

    assert proc.returncode == 1
    assert fires["valid"] is False, fires
    assert "tall" in fires["note"].lower(), fires


def test_transition_fires_accepts_exact_ids_and_recomputed_tallies(
    tmp_path: Path,
) -> None:
    """A correctly identified artifact retains the existing passing behavior."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {"id": "hero-load", "trigger": "page-load"},
                    {"id": "hero-scroll", "trigger": "scroll-scrub"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": 2,
                "fired": 1,
                "known_skip": 1,
                "failed": 0,
                "unmeasurable": 0,
                "entries": [
                    {"id": "hero-scroll", "status": "known-skip"},
                    {"id": "hero-load", "status": "pass"},
                ],
            }
        ),
        encoding="utf-8",
    )

    proc = _run(ref)
    fires = _component(ref, "transition-fires.json")
    payload = json.loads((ref / "transition-proof.json").read_text())

    assert proc.returncode == 1  # unrelated required static proof is intentionally absent
    assert fires["valid"] is True, fires
    assert payload["evidence"]["specEntryIds"] == ["hero-load", "hero-scroll"]
    assert payload["evidence"]["transitionFireEntryIds"] == [
        "hero-scroll",
        "hero-load",
    ]
    assert payload["evidence"]["transitionFireTallies"] == {
        "total": 2,
        "fired": 1,
        "known_skip": 1,
        "failed": 0,
        "unmeasurable": 0,
    }


def test_transition_fires_skip_cannot_bypass_nonempty_spec(
    tmp_path: Path,
) -> None:
    """A skipped fire probe is not evidence for declared transition work."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _spec(ref, "scroll-scrub")
    (ref / "transition-fires.json").write_text(
        json.dumps({"status": "skip", "total": 0}),
        encoding="utf-8",
    )

    proc = _run(ref)
    fires = _component(ref, "transition-fires.json")

    assert proc.returncode == 1
    assert fires["valid"] is False, fires
    assert "skip" in fires["note"], fires


def test_declaration_only_coverage_passes_when_motion_actually_fired(tmp_path: Path) -> None:
    """The legitimate case still passes: declaration-only coverage carried by a
    transition-fires run where motion DID fire."""
    ref = tmp_path / "ref"
    (ref / "transitions").mkdir(parents=True)
    (ref / "transition-coverage.json").write_text(
        json.dumps({"animatedElements": [{"selector": ".hero", "samples": []}]}), encoding="utf-8"
    )
    (ref / "transition-fires.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "total": 5,
                "fired": 4,
                "known_skip": 0,
                "failed": 0,
                "unmeasurable": 1,
            }
        ),
        encoding="utf-8",
    )
    _run(ref)
    comp = _component(ref, "transition-coverage.json")
    assert comp["valid"] is True, f"real fired motion must carry coverage; got {comp}"
