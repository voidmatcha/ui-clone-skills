"""Scope selection must reduce cost without claiming unmeasured layouts."""
import json
from pathlib import Path
from typing import Any

import pytest

from ui_clone.verification_scope import apply_scope, resolve_scope


def test_unknown_breakpoints_do_not_invent_desktop_band(tmp_path: Path) -> None:
    scope = resolve_scope(tmp_path)
    assert scope['mode'] == 'desktop'
    assert scope['range'] == {'minWidth': 1440, 'maxWidth': 1440}
    assert scope['boundaryViewports'] == []
    assert scope['rangeEvidence'] == 'representative-only'


def test_css_bounds_stay_inside_same_media_query_interval(tmp_path: Path) -> None:
    css = tmp_path / 'ref-css'
    css.mkdir()
    (css / 'main.css').write_text('@media (max-width: 1023px) {} @media (min-width: 1600px) {}')
    scope = resolve_scope(tmp_path)
    assert scope['range'] == {'minWidth': 1024, 'maxWidth': 1599}
    assert [v['w'] for v in scope['boundaryViewports']] == [1024, 1599]
    assert scope['availableOtherLayouts']


def test_desktop_omits_only_explicit_mobile_checks(tmp_path: Path) -> None:
    plan: dict[str, Any] = {'viewports': [{'w': 375}, {'w': 1440}], 'requiredChecks': [
        {'id': 'mobile-viewport-parity'}, {'id': 'runtime-frame-proof'}], 'deferredChecks': []}
    apply_scope(plan, tmp_path)
    assert [v['w'] for v in plan['viewports']] == [1440]
    assert [r['id'] for r in plan['requiredChecks']] == ['runtime-frame-proof', 'desktop-band-fluidity']
    assert plan['outOfScopeChecks'][0]['id'] == 'mobile-viewport-parity'
    assert plan['verificationScope']['completionLabel'] == 'desktop-only'


def test_all_scope_preserves_checks_and_viewports(tmp_path: Path) -> None:
    plan: dict[str, Any] = {'viewports': [{'w': 375}, {'w': 1440}], 'requiredChecks': [{'id': 'mobile-viewport-parity'}]}
    original = json.loads(json.dumps(plan))
    apply_scope(plan, tmp_path, 'all')
    assert plan['viewports'] == original['viewports']
    assert plan['requiredChecks'] == original['requiredChecks']


def test_detected_values_reject_booleans_and_use_actual_boundaries(tmp_path: Path) -> None:
    (tmp_path / 'detected-breakpoints.json').write_text(json.dumps({'breakpoints': [True, 1024, '1600px']}))
    scope = resolve_scope(tmp_path)
    assert scope['range'] == {'minWidth': 1025, 'maxWidth': 1599}


def test_plan_script_defaults_desktop_and_all_is_explicit(tmp_path: Path) -> None:
    import os
    import subprocess

    root = Path(__file__).resolve().parents[1]
    script = root / 'skills/visual-debug/scripts/verification-plan.sh'
    env = {k: v for k, v in os.environ.items() if k not in {'UI_CLONE_VERIFY_SCOPE', 'UI_CLONE_VERIFY_TIER'}}
    for mode in ('desktop', 'all'):
        ref = tmp_path / mode
        ref.mkdir()
        proc = subprocess.run(['bash', str(script), str(ref), '--tier=standard', f'--scope={mode}'],
                              env=env, capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        data = json.loads((ref / 'verification-plan.json').read_text())
        ids = {r['id'] for r in data['requiredChecks']}
        assert ('mobile-viewport-parity' in ids) == (mode == 'all')
        assert [v['w'] for v in data['viewports']] == ([1440] if mode == 'desktop' else [375, 1280, 1440, 1600, 1920])
        if mode == 'desktop':
            assert 'desktop-band-fluidity' in ids
            row = next(r for r in data['requiredChecks'] if r['id'] == 'desktop-band-fluidity')
            assert row['produces'] == 'desktop-band-fluidity.json'
            assert 'runtime-frame-proof' in ids


def test_modern_css_range_queries_bound_desktop(tmp_path: Path) -> None:
    css = tmp_path / 'ref-css'
    css.mkdir()
    (css / 'main.css').write_text('@media (width <= 1023px) {} @media (width >= 1600px) {}')
    assert resolve_scope(tmp_path)['range'] == {'minWidth': 1024, 'maxWidth': 1599}


def test_alignment_probe_uses_scope_edges_not_mobile_breakpoints(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from ui_clone.alignment_sweep import main
    (tmp_path / 'detected-breakpoints.json').write_text(json.dumps({'breakpoints': [375, 1024, 1600]}))
    plan: dict[str, Any] = {'viewports': []}
    apply_scope(plan, tmp_path)
    (tmp_path / 'verification-plan.json').write_text(json.dumps(plan))
    assert main(['--emit-widths', str(tmp_path)]) == 0
    widths = [int(line.split()[0]) for line in capsys.readouterr().out.splitlines()]
    assert widths == [1025, 1440, 1599]


def test_dispatcher_sets_single_viewport_for_all_desktop_rows(tmp_path: Path) -> None:
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    plan: dict[str, Any] = {'requiredChecks': [{'id': 'runtime-env', 'script': 'skills/visual-debug/scripts/runtime-env-check.sh',
                               'produces': 'runtime-env.json', 'severity': 'block'}]}
    apply_scope(plan, tmp_path)
    path = tmp_path / 'verification-plan.json'
    path.write_text(json.dumps(plan))
    proc = subprocess.run([sys.executable, str(root / 'scripts/verify/build_required_dispatch.py'),
                           str(path), str(tmp_path), str(root), str(tmp_path), str(tmp_path), str(tmp_path),
                           'https://ref.example', 'http://localhost:3000', 'scope-test'],
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    rows = [row.split('\t') for row in proc.stdout.splitlines() if row.startswith('DISPATCH\t')]
    assert rows
    assert all('VIEWPORTS=1440x900' in row[3] for row in rows)
    assert all('VIEW_W=1440 VIEW_H=900' in row[3] for row in rows)
    assert all('375x812' not in row[3] for row in rows)


def test_dispatcher_does_not_rewrite_manual_rows_under_desktop_scope(tmp_path: Path) -> None:
    # fable-20260910 follow-up review (HIGH): desktop scope's VIEWPORTS
    # injection used to rewrite EVERY DISPATCH row's args, including the
    # literal "MANUAL" sentinel scroll-anim-temporal-diff.sh carries. That
    # turned "MANUAL" into "ENV:VIEWPORTS=... -- MANUAL", which no longer
    # matches run-required-checks.sh's `[ "$args" = "MANUAL" ]` skip check —
    # the row was actually dispatched (with "MANUAL" as its only positional
    # arg) and always failed, blocking closeout under the default desktop
    # scope. MANUAL rows must be left untouched.
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    plan: dict[str, Any] = {
        'requiredChecks': [
            {
                'id': 'scroll-anim-temporal',
                'script': 'skills/visual-debug/scripts/scroll-anim-temporal-diff.sh',
                'produces': 'transitions/temporal-result.txt',
                'severity': 'warn',
            }
        ]
    }
    apply_scope(plan, tmp_path)
    path = tmp_path / 'verification-plan.json'
    path.write_text(json.dumps(plan))
    proc = subprocess.run(
        [
            sys.executable, str(root / 'scripts/verify/build_required_dispatch.py'),
            str(path), str(tmp_path), str(root), str(tmp_path), str(tmp_path), str(tmp_path),
            'https://ref.example', 'http://localhost:3000', 'scope-test',
        ],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [row.split('\t') for row in proc.stdout.splitlines() if row.startswith('DISPATCH\t')]
    manual_rows = [row for row in rows if row[1] == 'scroll-anim-temporal']
    assert manual_rows
    assert all(row[3] == 'MANUAL' for row in manual_rows), manual_rows


def test_desktop_band_fluidity_definitions_stay_in_lockstep() -> None:
    # fable-20260910 follow-up review (LOW): desktop-band-fluidity is defined
    # in TWO places — verification-plan.sh's add_check row (evidence-gated:
    # only when detected-breakpoints.json has >=2 breakpoints, so it also
    # fires under `--scope=all`) and apply_scope's unconditional desktop-scope
    # addition below (fires for ANY desktop-scope plan regardless of
    # breakpoint evidence — locked in by test_desktop_omits_only_explicit_
    # mobile_checks and test_plan_script_defaults_desktop_and_all_is_explicit
    # above, which both apply_scope with zero breakpoint evidence and still
    # expect the row). That gating divergence is intentional (desktop scope
    # always wants this cheap check; `all` scope only wants it with real
    # evidence), so this test does NOT force identical gating — it guards the
    # fields that MUST match for either row to dispatch and execute the same
    # way: script, produces, severity, tier, dependsOn, argsRecipe. A future
    # edit to only one side (e.g. bumping severity, changing the script path,
    # or the args template) would otherwise silently drift undetected.
    import re

    sh = (
        Path(__file__).resolve().parents[1]
        / 'skills' / 'visual-debug' / 'scripts' / 'verification-plan.sh'
    ).read_text(encoding='utf-8')
    match = re.search(
        r'add_check "desktop-band-fluidity" \\\s*\n'
        r'\s*"([^"]+)" \\\s*\n'
        r'\s*"([^"]+)" \\\s*\n'
        r'\s*"[^"]*" \\\s*\n'
        r'\s*"([^"]+)" \\\s*\n'
        r'\s*"([^"]+)" \\\s*\n'
        r'\s*"([^"]+)" \\\s*\n'
        r'\s*"([^"]+)"',
        sh,
    )
    assert match, 'add_check "desktop-band-fluidity" row not found/parseable — update this test\'s regex'
    sh_script, sh_produces, sh_severity, sh_tier, sh_deps, sh_args = match.groups()

    py = (
        Path(__file__).resolve().parents[1] / 'ui_clone' / 'verification_scope.py'
    ).read_text(encoding='utf-8')
    py_match = re.search(
        r"checks\.append\(\{'id': 'desktop-band-fluidity', 'script': '([^']+)',\s*"
        r"'produces': '([^']+)', 'severity': '([^']+)', 'tier': '([^']+)',\s*"
        r"'reason': '[^']*',\s*'dependsOn': \[([^\]]*)\],\s*"
        r"'argsRecipe': '([^']+)'\}\)",
        py,
    )
    assert py_match, 'apply_scope desktop-band-fluidity dict literal not found/parseable — update this test\'s regex'
    py_script, py_produces, py_severity, py_tier, py_deps_raw, py_args = py_match.groups()
    py_deps = py_deps_raw.strip().strip("'")

    assert sh_script == py_script
    assert sh_produces == py_produces
    assert sh_severity == py_severity
    assert sh_tier == py_tier
    assert sh_deps == py_deps
    assert sh_args == py_args
