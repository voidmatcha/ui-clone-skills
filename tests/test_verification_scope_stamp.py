import json
from pathlib import Path

from ui_clone.pipeline_phases.verify import build_verify_stamp, verify_stamp_evidence_problem


def test_scope_is_bound_to_completion_stamp(tmp_path: Path) -> None:
    plan: dict = {'verificationScope': {'mode': 'desktop', 'completionLabel': 'desktop-only'},
            'viewports': [{'w': 1440, 'h': 900}]}
    path = tmp_path / 'verification-plan.json'
    path.write_text(json.dumps(plan))
    stamp = build_verify_stamp(tmp_path, tmp_path / 'impl', [])
    assert stamp['verificationScope']['completionLabel'] == 'desktop-only'
    assert verify_stamp_evidence_problem(tmp_path, stamp) is None
    plan['verificationScope']['mode'] = 'all'
    path.write_text(json.dumps(plan))
    problem = verify_stamp_evidence_problem(tmp_path, stamp)
    assert problem is not None and 'scope' in problem.lower()


def test_scope_cannot_be_removed_from_stamped_plan(tmp_path: Path) -> None:
    path = tmp_path / 'verification-plan.json'
    path.write_text(json.dumps({'verificationScope': {'mode': 'desktop'}}))
    stamp = build_verify_stamp(tmp_path, tmp_path / 'impl', [])
    path.write_text('{}')
    assert verify_stamp_evidence_problem(tmp_path, stamp) is not None


def test_partial_iteration_blocks_both_closeout_paths(tmp_path: Path) -> None:
    from ui_clone.hooks._common import quick_tier_blocker

    receipt = tmp_path / "iteration-receipt.json"
    receipt.write_text('{"mode":"iteration"}')
    problem = quick_tier_blocker(tmp_path)
    assert problem is not None and "Partial iteration" in problem
    receipt.write_text('{"mode":"final","status":"running"}')
    assert quick_tier_blocker(tmp_path) is not None
    receipt.write_text('{"mode":"final","status":"completed"}')
    assert quick_tier_blocker(tmp_path) is None
    receipt.write_text('[]')
    assert quick_tier_blocker(tmp_path) is not None
