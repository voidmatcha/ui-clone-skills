"""verify-H1: scroll-end-completion must FAIL CLOSED on an empty probe result.

The per-viewport probe is a monolithic async eval that snapshots the full DOM at
four viewports. On the ~25s eval budget it can time out and return empty stdout.
The downstream `JSON.parse(RAW || '{}')` then yielded stuck:[] -> STUCK_COUNT=0
-> "✅ settled" and exit 0, certifying a probe that never ran. Same class as the
F3 reveal-trigger fix. An empty/unparseable result must fail closed (exit 2), a
genuine "no stuck elements" run (valid JSON, stuck:[]) must still pass.

Mirrors the reveal-trigger harness: a fake agent-browser on PATH returns the
probe output we choose per subcommand.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1]
          / "skills" / "visual-debug" / "scripts" / "scroll-end-completion-check.sh")


def _fake_agent_browser(eval_output: str) -> str:
    return f"""#!/usr/bin/env bash
set -uo pipefail
if [ "${{1:-}}" = "--session" ]; then shift 2; fi
cmd="${{1:-}}"; shift || true
case "$cmd" in
  set|navigate|close) exit 0 ;;
  eval)
    printf '%s' {eval_output!r}
    exit 0
    ;;
esac
exit 0
"""


def _run(tmp_path: Path, eval_output: str) -> tuple[subprocess.CompletedProcess, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "agent-browser").write_text(_fake_agent_browser(eval_output), encoding="utf-8")
    (bin_dir / "sleep").chmod(0o755)
    (bin_dir / "agent-browser").chmod(0o755)

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["VIEWPORTS"] = "1280x800"
    env["WAIT_MS"] = "0"

    proc = subprocess.run(
        ["bash", str(SCRIPT), "scroll-end-test", "https://example.test/", str(ref_dir)],
        check=False, capture_output=True, env=env, text=True, timeout=120,
    )
    return proc, ref_dir / "scroll-completion.json"


def test_empty_probe_output_fails_closed(tmp_path: Path) -> None:
    proc, artifact = _run(tmp_path, eval_output="")
    assert proc.returncode != 0, (
        "empty scroll-end probe (eval timeout) must fail closed, not report settled; "
        f"stdout={proc.stdout!r}"
    )
    assert "settled" not in proc.stdout.split("\n")[-3:][0] if proc.stdout else True
    if artifact.exists():
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        assert payload["status"] != "pass"


@pytest.mark.parametrize("endpoint", [None, {"reached": False, "remaining": 6500}])
def test_unproven_document_end_cannot_pass(tmp_path: Path, endpoint: dict | None) -> None:
    out = json.dumps({"maxScroll": 5000, "candidates": 2, "stuck": [], "endpoint": endpoint})
    proc, artifact = _run(tmp_path, eval_output=out)
    assert proc.returncode == 2, proc.stdout
    assert json.loads(artifact.read_text())["status"] == "error"


def test_valid_probe_no_stuck_is_genuine_pass(tmp_path: Path) -> None:
    out = json.dumps({"endpoint": {"reached": True}, "maxScroll": 5000, "candidates": 2, "stuck": []})
    proc, artifact = _run(tmp_path, eval_output=out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"


def test_valid_probe_with_stuck_still_fails(tmp_path: Path) -> None:
    out = json.dumps({"endpoint": {"reached": True}, "maxScroll": 5000, "candidates": 2, "stuck": [".hero"]})
    proc, artifact = _run(tmp_path, eval_output=out)
    assert proc.returncode == 1, f"a real stuck element must fail (exit 1): {proc.stdout}"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "fail"


def test_temporal_motion_alone_is_not_the_inconclusive_signal_anymore(tmp_path: Path) -> None:
    # fable-20260910 follow-up review round 3 (LOW, now fixed): `temporalMotion`
    # entries by themselves no longer force "error"/inconclusive — the real JS
    # probe always either resolves them (confirmedTimeOnly:true, non-blocking)
    # or reflects an unresolved residual into `stuck` too (a measured FAIL,
    # see test_unresolved_residual_on_continuous_motion_still_blocks above).
    # Only `unmeasurableTargets` (a target that vanished mid-probe, no
    # residual computable at all) is the genuinely inconclusive signal now
    # (see test_unmeasurable_target_is_still_the_only_source_of_inconclusive_error).
    # A bare temporalMotion entry carrying neither is not a real JS output
    # shape, but must not be treated as a false blocker if it ever appears.
    out = json.dumps({"endpoint": {"reached": True}, "maxScroll": 5000, "candidates": 2, "stuck": [],
                      "temporalMotion": [{"selector": ".card"}]})
    proc, artifact = _run(tmp_path, eval_output=out)
    assert proc.returncode == 0, proc.stdout
    payload = json.loads(artifact.read_text())
    assert payload["status"] == "pass"
    assert payload["viewports"][0]["temporalMotion"][0]["selector"] == ".card"


def test_confirmed_time_only_motion_does_not_block_a_pass(tmp_path: Path) -> None:
    # F2 (fable-20260910): a continuously moving (timer/rAF) element whose
    # minus50->max delta is fully explained by its own fixed-position control
    # drift must not force the whole probe into "error" — only an UNRESOLVED
    # residual should.
    out = json.dumps({"endpoint": {"reached": True}, "maxScroll": 5000, "candidates": 1, "stuck": [],
                      "temporalMotion": [{"selector": ".ticker", "confirmedTimeOnly": True,
                                          "residual": {"opacity": 0, "tx": 0, "ty": 0}}]})
    proc, artifact = _run(tmp_path, eval_output=out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(artifact.read_text())
    assert payload["status"] == "pass"
    assert payload["viewports"][0]["temporalMotion"][0]["selector"] == ".ticker"


def test_unresolved_residual_on_continuous_motion_still_blocks(tmp_path: Path) -> None:
    # fable-20260910 follow-up review round 3 (LOW, now fixed): the real JS
    # probe always pushes an unresolved (confirmedTimeOnly:false) residual
    # into `stuck` too (same exceeds() predicate on the same residual
    # values) — it is a MEASURED, quantified defect, not merely inconclusive.
    # This must surface as a genuine FAIL (exit 1, status "fail"), not
    # "error"/exit 2 (which check_iteration.classify() mis-files as
    # "infrastructure" instead of "implementation").
    out = json.dumps({"endpoint": {"reached": True}, "maxScroll": 5000, "candidates": 1,
                      "stuck": [{"selector": ".mixed", "delta": {"opacity": 0, "tx": 0, "ty": 5}}],
                      "temporalMotion": [{"selector": ".mixed", "confirmedTimeOnly": False,
                                          "residual": {"opacity": 0, "tx": 0, "ty": 5}}]})
    proc, artifact = _run(tmp_path, eval_output=out)
    assert proc.returncode == 1, proc.stdout
    payload = json.loads(artifact.read_text())
    assert payload["status"] == "fail"


def test_unmeasurable_target_is_still_the_only_source_of_inconclusive_error(
    tmp_path: Path,
) -> None:
    # A target that vanished mid-probe (no residual could even be computed)
    # is the one case that stays genuinely inconclusive.
    out = json.dumps({"endpoint": {"reached": True}, "maxScroll": 5000, "candidates": 1, "stuck": [],
                      "unmeasurableTargets": [{"selector": ".gone"}]})
    proc, artifact = _run(tmp_path, eval_output=out)
    assert proc.returncode == 2, proc.stdout
    payload = json.loads(artifact.read_text())
    assert payload["status"] == "error"


def _execute_browser_probe(mode: str) -> dict:
    source = SCRIPT.read_text()
    start = source.index('eval "(async () => {') + len('eval ')
    end = source.index(' 2>/dev/null)', start)
    quoted = source[start:end]
    proc = subprocess.run(['bash', '-c', "printf '%s' " + quoted],
                          env=dict(os.environ, OPACITY_EPS='0.01', TRANSFORM_EPS_PX='1', SETTLE_MS='600'),
                          capture_output=True, text=True, check=True, timeout=10)
    harness = r'''
let ticks=0;
const el = {
 tagName:'DIV', id:'card', className:'', attrs:{},
 getBoundingClientRect:()=>({width:100,height:100}),
 setAttribute(k,v){this.attrs[k]=v;}, removeAttribute(k){delete this.attrs[k];}
};
const html = {tagName:'HTML',parentElement:null,scrollHeight:5800,clientHeight:800};
const body = {tagName:'BODY',parentElement:html,scrollHeight:5800,clientHeight:800,closest:()=>null};
const landmark = {
 tagName:'MAIN',id:'page',className:'',parentElement:body,
 matches:()=>false,getBoundingClientRect:()=>({width:1000,height:2000,bottom:2000})
};
global.window={innerHeight:800,scrollY:0,scrollTo({top}){this.scrollY=top;}};
global.document={documentElement:html,body,scrollingElement:html,
 querySelectorAll:s=>s.startsWith('main,') && (MODE==='root-overflow' || MODE==='fixed-landmark') ? [landmark]
   : s.includes('footer') ? [] : [el],
 querySelector:()=>MODE==='missing' && ticks>=4 ? null : el};
global.getComputedStyle=node=>{
 if (node===landmark) return {display:'block',position:MODE==='fixed-landmark'?'fixed':'static',overflowY:'visible'};
 if (node===body || node===html) return {display:'block',position:'static',overflowY:'auto'};
 const y=window.scrollY;
 const ty=MODE==='timer' ? ticks*10
   : MODE==='mixed' ? ticks*10 + (y===5000 ? 100 : 0)
   : MODE==='alias' ? (ticks % 2 === 1 ? 50 : -50) + (y===5000 ? 5 : 0)
   : y===0 ? 0 : MODE==='stuck' && y===5000 ? 20 : 10;
 return {display:'block',visibility:'visible',animationName:'none',opacity:'1',transform:`matrix(1,0,0,1,0,${ty})`};
};
global.setTimeout=f=>{
 ticks++;
 if (MODE==='late-growth' && ticks===12) document.documentElement.scrollHeight=6200;
 f();
};
Promise.resolve(PROBE).then(x=>console.log(x));
'''
    harness = harness.replace('MODE', json.dumps(mode)).replace('PROBE', proc.stdout)
    result = subprocess.run(['node', '-e', harness], capture_output=True, text=True, check=True, timeout=10)
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def test_fixed_position_motion_is_reported_separately() -> None:
    payload = _execute_browser_probe('timer')
    assert payload.get('temporalMotion'), payload
    assert not payload['stuck'], payload
    # F2 (fable-20260910): pure continuous motion, once its own control drift
    # fully explains the observed delta, must be confirmed time-only so the
    # dispatcher-level probe does not treat a healthy ticker as inconclusive.
    assert payload['temporalMotion'][0]['confirmedTimeOnly'] is True, payload


def test_scroll_driven_difference_still_fails() -> None:
    payload = _execute_browser_probe('stuck')
    assert payload['stuck'], payload
    assert not payload.get('temporalMotion'), payload


def test_mixed_continuous_and_scroll_motion_still_flags_the_residual() -> None:
    # Timer-driven drift PLUS a genuine unresolved scroll-endpoint jump: the
    # residual after subtracting time-explained drift must still surface as a
    # real, unresolved defect (not silently absorbed into "confirmed time-only").
    payload = _execute_browser_probe('mixed')
    assert payload.get('temporalMotion'), payload
    assert payload['temporalMotion'][0]['confirmedTimeOnly'] is False, payload
    assert payload['stuck'], payload


def test_alternating_continuous_motion_does_not_mask_a_real_stuck_delta() -> None:
    # fable-20260910 follow-up review (MEDIUM): the ORIGINAL residual formula
    # subtracted two ONE-SETTLE-interval control deltas (a<->controlAtMinus50,
    # b<->controlAtMax) from a raw delta that spans TWO settle intervals
    # (a->controlAtMinus50->b). For a genuinely OSCILLATING (not monotonic)
    # continuous motion whose period aliases against SETTLE — here, exactly
    # 2 sample-ticks — controlAtMinus50/controlAtMax land on opposite phase
    # peaks (+/-50), each reading a full-amplitude swing, while the real a->b
    # span (also 2 ticks apart) lands back in phase and nets only the true
    # stuck delta (5). The old formula would subtract ~100-200 from a real
    # delta of 5 and clamp to 0 -> a genuinely stuck element silently reported
    # "confirmed time-only" -> false pass. The duration-matched control
    # (controlAtMaxFull, spanning the same two-interval window as raw) must
    # not make this mistake: it lands back in phase with `b` too, so it reads
    # ~0 drift and the real stuck delta of 5 survives as the residual.
    payload = _execute_browser_probe('alias')
    assert payload['stuck'], payload
    stuck_row = payload['stuck'][0]
    assert stuck_row['delta']['ty'] == pytest.approx(5, abs=0.5), payload


def test_settled_scroll_driven_target_still_passes() -> None:
    payload = _execute_browser_probe('settled')
    assert not payload['stuck'], payload
    assert not payload.get('temporalMotion'), payload


def test_growth_triggered_after_sampling_cannot_certify_stale_endpoint() -> None:
    payload = _execute_browser_probe('late-growth')
    assert payload['endpoint']['reached'] is False, payload
    assert payload['endpoint']['reason'] == 'document-changed-after-sampling', payload
    assert payload['endpoint']['scrollHeight'] == 6200, payload


def test_root_scroller_does_not_hide_clipped_document_landmark() -> None:
    payload = _execute_browser_probe('root-overflow')
    assert payload['endpoint']['reached'] is False, payload
    assert payload['endpoint']['clippedLandmarks'] == ['main#page'], payload


def test_fixed_semantic_panel_is_not_a_document_endpoint_landmark() -> None:
    payload = _execute_browser_probe('fixed-landmark')
    assert payload['endpoint']['reached'] is True, payload
    assert payload['endpoint']['clippedLandmarks'] == [], payload


def test_missing_probe_target_cannot_disappear_into_a_pass() -> None:
    payload = _execute_browser_probe('missing')
    assert payload.get('unmeasurableTargets'), payload
    assert not payload['stuck'], payload


def test_missing_target_receipt_is_an_error(tmp_path: Path) -> None:
    out = json.dumps({"endpoint": {"reached": True}, "maxScroll": 5000, "candidates": 1, "stuck": [],
                      "unmeasurableTargets": [{"selector": ".card"}]})
    proc, artifact = _run(tmp_path, eval_output=out)
    assert proc.returncode == 2, proc.stdout
    assert json.loads(artifact.read_text())["status"] == "error"
