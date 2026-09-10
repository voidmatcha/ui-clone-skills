"""Desktop scope must not earn a pass from an unrequested mobile retry."""
import json
from pathlib import Path

from pytest import MonkeyPatch

from ui_clone.gates import transition_fires as fires


def test_desktop_scope_disables_mobile_retry_without_skipping_failures(tmp_path: Path) -> None:
    (tmp_path / 'verification-plan.json').write_text(json.dumps(
        {'verificationScope': {'mode': 'desktop'}}))
    assert fires.mobile_retry_allowed(tmp_path) is False
    base = {'verificationScope': {'mode': 'desktop'}, 'failed': 1,
            'status': 'fail', 'entries': [{'id': 'missing', 'status': 'fail'}]}
    override = {'entries': [{'id': 'missing', 'status': 'pass'}]}
    merged = fires.merge_viewport_artifacts(base, [override])
    assert merged['failed'] == 1
    assert merged['entries'][0]['status'] == 'fail'


def test_explicit_all_and_legacy_preserve_retry(tmp_path: Path) -> None:
    assert fires.mobile_retry_allowed(tmp_path)
    (tmp_path / 'verification-plan.json').write_text(json.dumps(
        {'verificationScope': {'mode': 'all'}}))
    assert fires.mobile_retry_allowed(tmp_path)


def test_shell_checks_scope_before_any_mobile_navigation() -> None:
    source = (Path(__file__).parents[1] / 'skills/visual-debug/scripts/transition-fires-check.sh').read_text()
    assert '--mobile-retry-allowed "$REF_DIR"' in source
    assert source.index('--mobile-retry-allowed "$REF_DIR"') < source.index('set viewport 375 812')


def test_cli_stamps_desktop_failures_and_reports_applicability(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    (tmp_path / 'verification-plan.json').write_text(json.dumps(
        {'verificationScope': {'mode': 'desktop'}}))
    monkeypatch.setattr(fires, 'evaluate', lambda *args, **kwargs: {
        'failed': 1, 'status': 'fail', 'entries': [
            {'id': 'missing', 'status': 'fail', 'kind': 'hover', 'observed': 'not found'}]})
    output = tmp_path / 'transition-fires.json'
    assert fires.main([str(tmp_path / 'transition-spec.json'), str(tmp_path / 'obs.json'),
                       str(tmp_path / 'assets.json'), str(output)]) == 1
    artifact = json.loads(output.read_text())
    assert artifact['verificationScope']['mode'] == 'desktop'
    assert 'reference-backed applicability' in artifact['scopeApplicabilityNote']
    assert artifact['failed'] == 1


def test_corrupt_scope_cannot_enable_mobile_retry(tmp_path: Path) -> None:
    (tmp_path / 'verification-plan.json').write_text('{broken')
    assert fires.main(['--mobile-retry-allowed', str(tmp_path)]) == 2
