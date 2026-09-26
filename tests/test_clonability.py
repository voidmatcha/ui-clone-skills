"""Step 5c-d clonability risk report: detectors, CLI, decisions, gate check."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ui_clone import clonability as clon
from ui_clone.gates.clonability_report import check_clonability_report
from ui_clone.state import GATE_ORDER, POST_IMPL_VERIFY_GATES, PipelineState


def _write(ref: Path, rel: str, data: object) -> None:
    path = ref / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _intro_contract(duration: int | None = 3767) -> dict:
    return {
        "schemaVersion": 1,
        "detected": True,
        "overlay": {
            "selector": "div.w-full.h-screen.flex",
            "maxCoverage": 1.0,
            "everVisible": True,
            "exitObserved": duration is not None,
        },
        "exitTiming": {"fromMs": 0, "toMs": duration, "durationMs": duration},
    }


def _ids(report: dict) -> dict[str, str]:
    return {r["id"]: r["severity"] for r in report["risks"]}


def _set_state(ref: Path, completed: list[str]) -> None:
    _write(
        ref,
        "pipeline-state.json",
        {
            "component": ref.name,
            "completed_steps": completed,
            "current_gate": GATE_ORDER[len(completed)] if len(completed) < len(GATE_ORDER) else "done",
            "unclonable_reasons": [],
        },
    )


# ── detection ──────────────────────────────────────────────────────────────


def test_no_evidence_reports_no_risk(tmp_path: Path) -> None:
    report = clon.build_report(tmp_path)
    assert report["risks"] == []
    assert report["status"] == "ok"
    assert report["verification"]["introSettleMs"] is None
    assert "no risks" in report["summary"]


def test_intro_overlay_with_measured_exit_is_caution_with_settle(tmp_path: Path) -> None:
    _write(tmp_path, "states/splash/contract.json", _intro_contract(3767))
    report = clon.build_report(tmp_path)
    (risk,) = report["risks"]
    assert risk["id"] == "intro-overlay" and risk["severity"] == "caution"
    assert risk["introExitMs"] == 3767
    assert "3767" in risk["mitigation"]
    assert any("exitTiming.durationMs=3767" in e for e in risk["evidence"])
    assert report["verification"]["introSettleMs"] == 3767 + 500
    assert report["status"] == "ok"


def test_intro_absent_or_small_overlay_is_not_reported(tmp_path: Path) -> None:
    contract = _intro_contract(1200)
    contract["overlay"]["maxCoverage"] = 0.2
    _write(tmp_path, "states/splash/contract.json", contract)
    assert clon.build_report(tmp_path)["risks"] == []
    _write(tmp_path, "states/splash/contract.json", {"detected": False})
    assert clon.build_report(tmp_path)["risks"] == []


def test_preloader_flag_without_contract_is_caution_without_duration(tmp_path: Path) -> None:
    _write(tmp_path, "interactions-detected.json", {"hasPreloader": True})
    report = clon.build_report(tmp_path)
    assert _ids(report) == {"intro-overlay": "caution"}
    assert report["verification"]["introSettleMs"] is None


def test_webgl_canvas_is_caution_with_library_evidence(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "canvas-webgl-detection.json",
        {
            "primaryRenderType": "webgl",
            "hasCanvas": True,
            "hasWebGL": True,
            "canvasCount": 1,
            "canvases": [{"width": 1438, "height": 898, "area": 1291324, "hasWebGL": True}],
        },
    )
    (tmp_path / "bundles").mkdir()
    (tmp_path / "bundles" / "a.js").write_text("class WebGLRenderer{}", encoding="utf-8")
    (risk,) = clon.build_report(tmp_path)["risks"]
    assert risk["id"] == "webgl-canvas" and risk["severity"] == "caution"
    assert "not pixels" in risk["mitigation"]
    assert any("bundles/a.js" in e for e in risk["evidence"])


def test_small_2d_canvas_is_not_reported(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "canvas-webgl-detection.json",
        {"hasCanvas": True, "hasWebGL": False, "canvases": [{"width": 40, "height": 40, "area": 1600}]},
    )
    assert clon.build_report(tmp_path)["risks"] == []


def test_hero_video_and_scroll_motion_volume(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "runtime-media.json",
        {"videos": [{"phase": "initial", "src": "hero.mp4", "rect": {"x": 0, "y": 0, "w": 1440, "h": 800}}]},
    )
    _write(tmp_path, "animation-runtime-dump.json", {"scrollLinkedStyles": [{}] * 8})
    _write(
        tmp_path,
        "transition-spec.json",
        {"transitions": [{"trigger": "viewport enter: whileInView once"}] * 3 + [{"trigger": "hover"}]},
    )
    ids = _ids(clon.build_report(tmp_path))
    assert ids == {"video-hero": "caution", "scroll-motion-heavy": "caution"}


def test_placeholder_spec_does_not_count_toward_motion(tmp_path: Path) -> None:
    _write(tmp_path, "animation-runtime-dump.json", {"scrollLinkedStyles": [{}] * 5})
    _write(
        tmp_path,
        "transition-spec.json",
        {"placeholder": True, "transitions": [{"trigger": "scroll"}] * 20},
    )
    assert clon.build_report(tmp_path)["risks"] == []


def test_paid_fonts_are_cautions_never_blockers(tmp_path: Path) -> None:
    # Step 5c-c: the agent sets paid-features.json decision by rule (the
    # paid-features gate fails while it is null), so an undecided paid font is
    # a caution; blocker decisions come only from the user.
    _write(
        tmp_path,
        "paid-features.json",
        {
            "paidFonts": [
                {"family": "Proxima", "cdn": "use.typekit.net", "decision": None},
                {"family": "Gotham", "cdn": "cloud.typography.com", "decision": "use"},
                {"family": None, "cdn": "use.typekit.net", "decision": "skip"},
            ]
        },
    )
    report = clon.build_report(tmp_path)
    ids = _ids(report)
    assert sorted(ids.values()) == ["caution", "caution"]
    assert any(i.startswith("paid-font-undecided-") for i in ids)
    assert any(i.startswith("paid-font-license-") for i in ids)
    assert report["status"] == "ok" and clon.open_blockers(report) == []
    assert "needs your decision" not in report["summary"]
    # Blockers still sort first.
    _write(tmp_path, "head.json", {"title": "Just a moment..."})
    assert clon.build_report(tmp_path)["risks"][0]["id"] == "bot-challenge"


def test_bot_challenge_title_is_blocker(tmp_path: Path) -> None:
    _write(tmp_path, "head.json", {"title": "Just a moment..."})
    assert _ids(clon.build_report(tmp_path)) == {"bot-challenge": "blocker"}
    _write(tmp_path, "head.json", {"title": "FEConf 2025"})
    assert clon.build_report(tmp_path)["risks"] == []


def test_recorded_unclonable_reason_surfaces_as_blocker(tmp_path: Path) -> None:
    state = PipelineState(component=tmp_path.name)
    state.record_unclonable("extraction", "login wall", tmp_path, category="auth-gated")
    report = clon.build_report(tmp_path)
    (risk,) = report["risks"]
    assert risk["severity"] == "blocker" and risk["fromState"] is True
    assert risk["id"].startswith("unclonable-auth-gated")


def test_iframe_embeds_are_classified(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "structure.json",
        {
            "tag": "body",
            "children": [
                {"tag": "iframe", "src": "https://www.google.com/maps/embed?pb=1"},
                {"tag": "div", "children": [{"tag": "iframe", "src": "https://www.youtube.com/embed/x"}]},
            ],
        },
    )
    (risk,) = clon.build_report(tmp_path)["risks"]
    assert risk["id"] == "third-party-embeds"
    assert "map" in risk["title"] and "video" in risk["title"]


# ── CLI + decisions ────────────────────────────────────────────────────────

USER = {"source": "user-terminal"}


def _tty(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    """Simulate the user's own terminal (True) or an agent tool shell's
    missing stdin terminal (False). The user's terminal has no agent-host
    markers, so they are cleared (the suite itself may run under an agent)."""
    monkeypatch.setattr(clon.sys.stdin, "isatty", lambda: value, raising=False)
    for name in clon.AGENT_HOST_ENV_MARKERS:
        monkeypatch.delenv(name, raising=False)


def _blocker(report: dict, prefix: str) -> str:
    return str(next(r["id"] for r in report["risks"] if r["id"].startswith(prefix)))


def test_cli_writes_report_and_prints_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "states/splash/contract.json", _intro_contract(2000))
    assert clon.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "intro-overlay" in out and "status: ok" in out
    report = json.loads((tmp_path / clon.REPORT_NAME).read_text())
    assert report["provenance"]["source"] == clon.PRODUCER
    assert report["provenance"]["sourceHashes"]["states/splash/contract.json"]
    assert "states/splash/contract.json" in report["inputsPresent"]
    assert clon.main([str(tmp_path / "missing")]) == 2


def test_blocker_exit_code_and_proceed_decision_survives_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "head.json", {"title": "Attention Required! | Cloudflare"})
    assert clon.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "ui-clone decide bot-challenge proceed" in out and "yourself" in out
    decide = [str(tmp_path), "--decide", "bot-challenge", "--decision", "proceed"]
    # No terminal on stdin (an agent's Bash tool): refused.
    _tty(monkeypatch, False)
    assert clon.main([*decide, "--note", "recapture ok"]) == 2
    assert "own terminal" in capsys.readouterr().err
    _tty(monkeypatch, True)
    assert clon.main(decide) == 2  # --note required
    assert clon.main([*decide, "--note", "recapture ok"]) == 0
    assert clon.main([str(tmp_path)]) == 0  # decision kept across a rerun
    report = clon.load_report(tmp_path)
    assert report is not None
    decision = report["decisions"]["bot-challenge"]
    assert decision["note"] == "recapture ok"
    assert decision["provenance"]["source"] == "user-terminal"


def test_stop_decision_records_canonical_unclonable_reason(tmp_path: Path) -> None:
    _write(tmp_path, "head.json", {"title": "Just a moment..."})
    report = clon.write_report(tmp_path)
    risk_id = _blocker(report, "bot-challenge")
    clon.record_decision(tmp_path, risk_id, "stop", "no access", provenance=USER)
    state = PipelineState.load(tmp_path)
    (reason,) = state.unclonable_reasons
    assert reason["gate"] == "extraction" and reason["category"] == "bot-challenge"
    # The reason the stop recorded neither re-surfaces as a new blocker nor
    # makes the report stale; the gate reports the stop.
    rerun = clon.write_report(tmp_path)
    assert [r["id"] for r in rerun["risks"] if r["severity"] == "blocker"] == [risk_id]
    result = check_clonability_report(tmp_path)
    assert result.status == "fail" and "decided to stop" in result.message


def test_caution_and_state_blocker_reject_decisions(tmp_path: Path) -> None:
    _write(tmp_path, "states/splash/contract.json", _intro_contract())
    state = PipelineState(component=tmp_path.name)
    state.record_unclonable("extraction", "login wall", tmp_path, category="auth-gated")
    report = clon.write_report(tmp_path)
    blocker = next(r["id"] for r in report["risks"] if r["severity"] == "blocker")
    with pytest.raises(ValueError, match="caution"):
        clon.record_decision(tmp_path, "intro-overlay", "proceed", "x", provenance=USER)
    with pytest.raises(ValueError, match="ui_clone.state recover"):
        clon.record_decision(tmp_path, blocker, "proceed", "x", provenance=USER)


def test_decision_requires_user_provenance_and_current_report(tmp_path: Path) -> None:
    _write(tmp_path, "head.json", {"title": "Just a moment..."})
    clon.write_report(tmp_path)
    with pytest.raises(ValueError, match="from the user"):
        clon.record_decision(tmp_path, "bot-challenge", "proceed", "x", provenance={"source": "agent"})
    # A decision written without user provenance (e.g. an older agent-recorded
    # one) does not count: the blocker stays open.
    report = clon.load_report(tmp_path)
    assert report is not None
    report["decisions"] = {"bot-challenge": {"decision": "proceed", "note": "agent", "identity": "x"}}
    (tmp_path / clon.REPORT_NAME).write_text(json.dumps(report), encoding="utf-8")
    assert [r["id"] for r in clon.open_blockers(report)] == ["bot-challenge"]
    # Refuses to record against a stale report.
    _write(tmp_path, "head.json", {"title": "Just a moment... (2)"})
    with pytest.raises(ValueError, match="stale"):
        clon.record_decision(tmp_path, "bot-challenge", "proceed", "x", provenance=USER)
    # Atomic write leaves no temp file behind.
    clon.write_report(tmp_path)
    clon.record_decision(tmp_path, "bot-challenge", "proceed", "x", provenance=USER)
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_risk_ids_are_stable_and_decisions_follow_the_evidence(tmp_path: Path) -> None:
    fonts = [
        {"family": "Proxima", "cdn": "use.typekit.net", "decision": None},
        {"family": "Gotham", "cdn": "cloud.typography.com", "decision": None},
    ]
    _write(tmp_path, "paid-features.json", {"paidFonts": fonts})
    _write(tmp_path, "head.json", {"title": "Just a moment..."})
    report = clon.write_report(tmp_path)
    by_title = {r["title"]: r["id"] for r in report["risks"]}
    clon.record_decision(tmp_path, "bot-challenge", "proceed", "recaptured", provenance=USER)
    # Re-extracted list in another order: the ids stay with the font, and the
    # decision stays with its evidence.
    _write(tmp_path, "paid-features.json", {"paidFonts": list(reversed(fonts))})
    rerun = clon.write_report(tmp_path)
    assert {r["title"]: r["id"] for r in rerun["risks"]} == by_title
    assert set(rerun["decisions"]) == {"bot-challenge"}
    assert clon.open_blockers(rerun) == []
    # Same id but different evidence identity: the decision is not carried.
    rerun["decisions"]["bot-challenge"]["identity"] = "bot-challenge|Other"
    (tmp_path / clon.REPORT_NAME).write_text(json.dumps(rerun), encoding="utf-8")
    assert clon.write_report(tmp_path)["decisions"] == {}


def test_state_blockers_have_stable_ids_and_feed_staleness(tmp_path: Path) -> None:
    state = PipelineState(component=tmp_path.name)
    state.record_unclonable("extraction", "login wall", tmp_path, category="auth-gated")
    first = clon.write_report(tmp_path)
    assert check_clonability_report(tmp_path).status == "fail"
    state = PipelineState.load(tmp_path)
    state.record_unclonable("extraction", "drm video", tmp_path, category="drm-canvas")
    # A newly recorded (or cleared) state blocker marks the report stale.
    stale = check_clonability_report(tmp_path)
    assert stale.status == "fail" and stale.stale
    assert clon.STATE_BLOCKERS_INPUT in stale.message
    second = clon.write_report(tmp_path)
    first_ids = {r["id"] for r in first["risks"]}
    assert first_ids < {r["id"] for r in second["risks"]}  # old id unchanged by the new entry


# ── gate check ─────────────────────────────────────────────────────────────


def test_gate_missing_report_fails_before_pre_generate(tmp_path: Path) -> None:
    _set_state(tmp_path, GATE_ORDER[: GATE_ORDER.index("pre-generate")])
    result = check_clonability_report(tmp_path)
    assert result.status == "fail" and "MISSING" in result.message
    assert "ui_clone.clonability" in result.fix


def test_gate_passes_current_report_and_fails_stale_or_undecided(tmp_path: Path) -> None:
    _write(tmp_path, "states/splash/contract.json", _intro_contract())
    clon.write_report(tmp_path)
    assert check_clonability_report(tmp_path).status == "pass"
    _write(tmp_path, "states/splash/contract.json", _intro_contract(4000))
    stale = check_clonability_report(tmp_path)
    assert stale.status == "fail" and stale.stale and "states/splash/contract.json" in stale.message
    _write(tmp_path, "head.json", {"title": "Just a moment..."})
    clon.write_report(tmp_path)
    undecided = check_clonability_report(tmp_path)
    assert undecided.status == "fail" and "bot-challenge" in undecided.message
    assert "ui-clone decide bot-challenge" in undecided.fix
    clon.record_decision(tmp_path, "bot-challenge", "proceed", "user re-captured", provenance=USER)
    assert check_clonability_report(tmp_path).status == "pass"


def test_gate_state_blocker_fix_points_to_recover_not_decide(tmp_path: Path) -> None:
    state = PipelineState(component=tmp_path.name)
    state.record_unclonable("extraction", "login wall", tmp_path, category="auth-gated")
    clon.write_report(tmp_path)
    result = check_clonability_report(tmp_path)
    assert result.status == "fail"
    assert "python -m ui_clone.state recover" in result.fix
    assert "--decide" not in result.fix


def test_gate_stop_decision_wins_over_staleness(tmp_path: Path) -> None:
    _write(tmp_path, "head.json", {"title": "Just a moment..."})
    clon.write_report(tmp_path)
    clon.record_decision(tmp_path, "bot-challenge", "stop", "give up", provenance=USER)
    _write(tmp_path, "head.json", {"title": "Just a moment... again"})
    result = check_clonability_report(tmp_path)
    assert result.status == "fail" and "decided to stop" in result.message and not result.stale


def test_gate_rejects_foreign_report(tmp_path: Path) -> None:
    _write(tmp_path, clon.REPORT_NAME, {"schemaVersion": 1, "risks": [], "provenance": {"source": "agent"}})
    assert check_clonability_report(tmp_path).status == "fail"


def test_gate_is_soft_only_for_a_missing_report_after_pre_generate(tmp_path: Path) -> None:
    _set_state(tmp_path, GATE_ORDER[: GATE_ORDER.index("post-implement")])
    missing = check_clonability_report(tmp_path)
    assert missing.status == "warn" and "not enforced" in missing.message
    # Once a report exists it is enforced in full, even past pre-generate.
    _write(tmp_path, "states/splash/contract.json", _intro_contract())
    clon.write_report(tmp_path)
    assert check_clonability_report(tmp_path).status == "pass"
    _write(tmp_path, "states/splash/contract.json", _intro_contract(9000))
    assert check_clonability_report(tmp_path).status == "fail"
    _write(tmp_path, "head.json", {"title": "Just a moment..."})
    clon.write_report(tmp_path)
    assert check_clonability_report(tmp_path).status == "fail"
    # Closeout never re-runs pre-generate.
    assert "pre-generate" not in POST_IMPL_VERIFY_GATES


# ── user-prompt decisions ──────────────────────────────────────────────────


def _project_with_blocker(tmp_path: Path, name: str = "hero") -> Path:
    ref = tmp_path / "tmp" / "ref" / name
    _write(ref, "head.json", {"title": "Just a moment..."})
    clon.write_report(ref)
    return ref


def test_prompt_decision_is_recorded_with_user_prompt_provenance(tmp_path: Path) -> None:
    ref = _project_with_blocker(tmp_path)
    assert clon.apply_prompt_decisions(tmp_path, "s1", "hello, keep going") is None
    msg = clon.apply_prompt_decisions(
        tmp_path, "s1", "ok\nui-clone decide bot-challenge proceed I re-captured it\n"
    )
    assert msg is not None and "Recorded the user's decision 'proceed'" in msg
    report = clon.load_report(ref)
    assert report is not None and report["status"] == "ok"
    prov = report["decisions"]["bot-challenge"]["provenance"]
    assert prov["source"] == "user-prompt" and prov["sessionId"] == "s1"
    assert report["decisions"]["bot-challenge"]["note"] == "I re-captured it"


def test_prompt_decision_ambiguity_and_unknown_id(tmp_path: Path) -> None:
    _project_with_blocker(tmp_path, "a")
    b = _project_with_blocker(tmp_path, "b")
    ambiguous = clon.apply_prompt_decisions(tmp_path, "s", "ui-clone decide bot-challenge stop no")
    assert ambiguous is not None and "NOT recorded" in ambiguous and "b/bot-challenge" not in ambiguous
    assert "<component>/bot-challenge" in ambiguous
    unknown = clon.apply_prompt_decisions(tmp_path, "s", "ui-clone decide nope proceed fine")
    assert unknown is not None and "NOT recorded" in unknown
    ok = clon.apply_prompt_decisions(tmp_path, "s", "ui-clone decide b/bot-challenge proceed fine")
    assert ok is not None and "Recorded" in ok
    report = clon.load_report(b)
    assert report is not None and report["status"] == "ok"


@pytest.mark.parametrize(
    "prompt, reason",
    [
        # A quoted line is the user quoting the instructions, not a decision.
        ("> ui-clone decide bot-challenge proceed I re-captured it", None),
        ("ui-clone decide bot-challenge proceed", "empty"),
        ("ui-clone decide bot-challenge proceed <note>", "placeholder"),
        ("ui-clone decide bot-challenge stop <id>", "placeholder"),
        (
            "ui-clone decide bot-challenge proceed fine\nui-clone decide bot-challenge stop no",
            "both proceed and stop",
        ),
    ],
)
def test_prompt_rejects_quotes_placeholders_empty_notes_and_conflicts(
    tmp_path: Path, prompt: str, reason: str | None
) -> None:
    ref = _project_with_blocker(tmp_path)
    msg = clon.apply_prompt_decisions(tmp_path, "s", prompt)
    if reason is None:
        assert msg is None
    else:
        assert msg is not None and "NOT recorded" in msg and reason in msg
        assert "Recorded" not in msg
    report = clon.load_report(ref)
    assert report is not None and report["decisions"] == {} and report["status"] == "blocked"


def test_cli_rejects_abbreviated_decide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `script -q /dev/null ...` gives a pty; an abbreviation must not reach --decide.
    _write(tmp_path, "head.json", {"title": "Just a moment..."})
    clon.write_report(tmp_path)
    _tty(monkeypatch, True)
    for opt in ("--decid", "--deci", "--dec"):
        with pytest.raises(SystemExit):
            clon.main([str(tmp_path), opt, "bot-challenge", "--decision", "proceed", "--note", "x"])
    capsys.readouterr()
    report = clon.load_report(tmp_path)
    assert report is not None and report["decisions"] == {}


def test_deleted_report_is_not_legacy_once_recorded(tmp_path: Path) -> None:
    # The producer records the report in pipeline state; deleting the report
    # after pre-generate then fails instead of dropping to warn-only.
    _set_state(tmp_path, GATE_ORDER[: GATE_ORDER.index("pre-generate")])
    clon.write_report(tmp_path)
    assert PipelineState.load(tmp_path).clonability_report_at
    state = PipelineState.load(tmp_path)
    state.mark_passed("pre-generate", tmp_path)
    assert PipelineState.load(tmp_path).clonability_report_at  # survives other writes
    (tmp_path / clon.REPORT_NAME).unlink()
    result = check_clonability_report(tmp_path)
    assert result.status == "fail" and "MISSING" in result.message


def test_passing_check_records_an_in_flight_report(tmp_path: Path) -> None:
    _set_state(tmp_path, GATE_ORDER[: GATE_ORDER.index("pre-generate")])
    _write(tmp_path, "states/splash/contract.json", _intro_contract())
    report = clon.build_report(tmp_path)  # written by an older producer: no record
    _write(tmp_path, clon.REPORT_NAME, report)
    assert not PipelineState.load(tmp_path).clonability_report_at
    assert check_clonability_report(tmp_path).status == "pass"
    assert PipelineState.load(tmp_path).clonability_report_at


def test_producer_never_creates_pipeline_state(tmp_path: Path) -> None:
    clon.write_report(tmp_path)
    assert not (tmp_path / "pipeline-state.json").exists()
    assert not (tmp_path / "pipeline-state.json.lock").exists()


# ── verification wiring ────────────────────────────────────────────────────


def _settle(ref: Path, floor: int, env: dict[str, str] | None = None) -> str:
    import os
    import subprocess

    lib = Path(__file__).resolve().parents[1] / "skills/visual-debug/scripts/lib/intro-settle.sh"
    run_env = {k: v for k, v in os.environ.items() if k != "UI_CLONE_INTRO_SETTLE_MS"}
    run_env.update(env or {})
    return subprocess.run(
        ["bash", "-c", f'. "{lib}"; intro_settle_wait_ms "$1" {floor}', "_", str(ref)],
        capture_output=True, text=True, check=True, env=run_env, timeout=30,
    ).stdout.strip()


def test_intro_settle_helper_reads_report_with_floor_and_clamp(tmp_path: Path) -> None:
    assert _settle(tmp_path, 2500) == "2500"  # no report keeps the old fixed wait
    _write(tmp_path, "states/splash/contract.json", _intro_contract(3767))
    clon.write_report(tmp_path)
    assert _settle(tmp_path, 2500) == "4267"
    assert _settle(tmp_path, 6000) == "6000"
    assert _settle(tmp_path, 2500, {"UI_CLONE_INTRO_SETTLE_MS": "99999"}) == "15000"


@pytest.mark.parametrize("marker", clon.AGENT_HOST_ENV_MARKERS)
def test_cli_decide_refused_under_agent_host_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], marker: str
) -> None:
    # A faked TTY (`script`) does not help: the marker alone refuses, even empty.
    _write(tmp_path, "head.json", {"title": "Just a moment..."})
    clon.write_report(tmp_path)
    _tty(monkeypatch, True)
    monkeypatch.setenv(marker, "")
    decide = [str(tmp_path), "--decide", "bot-challenge", "--decision", "proceed", "--note", "x"]
    assert clon.main(decide) == 2
    err = capsys.readouterr().err
    assert marker in err and "ui-clone decide" in err and "own terminal" in err
    report = clon.load_report(tmp_path)
    assert report is not None and report["decisions"] == {}


def test_agent_host_marker_ignores_user_shell_variables() -> None:
    assert clon.agent_host_marker({"CODEX_HOME": "/x", "ORCA_TERMINAL_ID": "1"}) is None
    assert clon.agent_host_marker({"CLAUDECODE": "1"}) == "CLAUDECODE"
    assert clon.agent_host_marker({"CODEX_THREAD_ID": "t"}) == "CODEX_THREAD_ID"


def _script_pty_argv(program: str) -> list[str]:
    """Run `program` under `script` so the CLI sees a pty on stdin."""
    if shutil.which("script") is None:
        pytest.skip("script(1) not available")
    if sys.platform == "darwin":
        return ["script", "-q", "/dev/null", "bash", "-c", program]
    return ["script", "-qec", shlex.join(["bash", "-c", program]), "/dev/null"]


@pytest.mark.parametrize(
    "program",
    [
        # Confirmed pre-bash bypasses: backslash-newline and variable indirection.
        '"$PY" -m ui_clone.clonability "$REF" --\\\ndecide bot-challenge --decision proceed --note forged',
        'M=ui_clone.clonability; "$PY" -m $M "$REF" --decide bot-challenge --decision proceed --note forged',
    ],
)
def test_cli_decide_refused_end_to_end_under_agent_marker_with_script_pty(tmp_path: Path, program: str) -> None:
    _write(tmp_path, "head.json", {"title": "Just a moment..."})
    clon.write_report(tmp_path)
    repo = Path(clon.__file__).resolve().parents[1]
    base = {k: v for k, v in os.environ.items() if k not in clon.AGENT_HOST_ENV_MARKERS}
    base.update(PY=sys.executable, REF=str(tmp_path), PYTHONPATH=str(repo))
    argv = _script_pty_argv(program)

    def run(env: dict[str, str]) -> str:
        proc = subprocess.run(
            argv, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60
        )
        return proc.stdout + proc.stderr

    out = run({**base, "CLAUDECODE": "1"})
    assert "--decide refused" in out and "CLAUDECODE" in out, out
    report = clon.load_report(tmp_path)
    assert report is not None and report["decisions"] == {}

    # The user's own terminal (no marker, a real pty) still records.
    out = run(base)
    report = clon.load_report(tmp_path)
    assert report is not None, out
    assert report["decisions"]["bot-challenge"]["provenance"]["source"] == "user-terminal", out


def test_prompt_conflict_detected_across_ref_prefixed_and_bare_forms(tmp_path: Path) -> None:
    ref = _project_with_blocker(tmp_path)
    prompt = "ui-clone decide hero/bot-challenge proceed fine\nui-clone decide bot-challenge stop no"
    msg = clon.apply_prompt_decisions(tmp_path, "s", prompt)
    assert msg is not None and "both proceed and stop" in msg and "Recorded" not in msg
    report = clon.load_report(ref)
    assert report is not None and report["decisions"] == {}
