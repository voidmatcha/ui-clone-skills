from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_skill_docs_require_three_phase_scroll_state_machine_proof() -> None:
    visual_debug = (ROOT / "skills" / "visual-debug" / "SKILL.md").read_text(encoding="utf-8")
    reverse = (ROOT / "skills" / "ui-reverse-engineering" / "SKILL.md").read_text(encoding="utf-8")
    capture = (ROOT / "skills" / "ui-capture" / "SKILL.md").read_text(encoding="utf-8")

    for text in (visual_debug, reverse, capture):
        assert "initial → active/expanded → settled/returned" in text
        assert "window.scrollTo" in text
        assert "scrollYProgress" in text
        assert "setTimeout" in text
        assert "velocity" in text
        assert "guard ref" in text

    assert "single endpoint frame" in visual_debug
    assert "scroll state-machine" in reverse
    assert "settle/return artifacts" in capture


def test_verification_plan_registers_scroll_state_machine_check_from_bundle(tmp_path: Path) -> None:
    ref = tmp_path / "ref" / "scroll-footer"
    bundles = ref / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "app.js").write_text(
        "const p = scrollYProgress; "
        "setTimeout(() => window.scrollTo({top: 0, behavior: \"smooth\"}), 350); "
        "const velocity = p.getVelocity?.(); const guardRef = useRef(false);\n",
        encoding="utf-8",
    )
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "footer-wordmark", "trigger": "scroll"}]}),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["UI_CLONE_VERIFY_TIER"] = "standard"
    proc = subprocess.run(
        ["bash", str(ROOT / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"), str(ref)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    plan = json.loads((ref / "verification-plan.json").read_text(encoding="utf-8"))
    checks = {check["id"]: check for check in plan["requiredChecks"]}
    assert plan["signals"]["hasScrollStateMachine"] is True
    assert checks["scroll-state-machine"]["script"].endswith("scroll-state-machine-check.sh")
    assert checks["scroll-state-machine"]["produces"] == "scroll-state-machine.json"
    assert "initial → active/expanded → settled/returned" in checks["scroll-state-machine"]["reason"]


def test_verification_plan_registers_scroll_state_machine_for_scrolltrigger_pin_scrub(tmp_path: Path) -> None:
    ref = tmp_path / "ref" / "pin-scrub"
    ref.mkdir(parents=True)
    (ref / "scroll-engine.json").write_text(
        json.dumps({
            "library": "ScrollTrigger",
            "features": {"pin": True, "scrub": True},
        }),
        encoding="utf-8",
    )
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "cards-rail", "trigger": "sticky-scrub"}]}),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["UI_CLONE_VERIFY_TIER"] = "standard"
    proc = subprocess.run(
        ["bash", str(ROOT / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"), str(ref)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    plan = json.loads((ref / "verification-plan.json").read_text(encoding="utf-8"))
    checks = {check["id"]: check for check in plan["requiredChecks"]}
    assert plan["signals"]["hasScrollStateMachine"] is True
    assert "scroll-state-machine" in checks


def test_scroll_state_machine_check_is_wired_into_dispatcher_and_uses_iife() -> None:
    script = ROOT / "skills" / "visual-debug" / "scripts" / "scroll-state-machine-check.sh"
    dispatcher = (ROOT / "scripts" / "verify" / "run-required-checks.sh").read_text(encoding="utf-8")
    text = script.read_text(encoding="utf-8")

    assert '"scroll-state-machine-check.sh"' in dispatcher
    assert "agent-browser --session" in text
    assert "(async () =>" in text
    assert "window.scrollTo" in text
    assert "unwrapAgentBrowserResult" in text
    assert "data.result" in text
    assert "initial" in text
    assert "active" in text
    assert "settled" in text
