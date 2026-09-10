"""Evidence-bounded desktop verification scope, separate from cost tiers."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

MOBILE_CHECKS = {'mobile-viewport-parity', 'mobile-responsive-coverage', 'resize-behavior'}


def resolve_scope(ref_dir: Path, mode: str = 'desktop') -> dict[str, Any]:
    if mode not in {'desktop', 'all'}:
        raise ValueError(f'Invalid verification scope: {mode}')
    cuts: set[int] = set()
    evidence: list[str] = []
    detected = ref_dir / 'detected-breakpoints.json'
    if detected.exists():
        data = json.loads(detected.read_text())
        values = data.get('breakpoints', []) if isinstance(data, dict) else data
        for value in values if isinstance(values, list) else []:
            if isinstance(value, dict):
                value = value.get('width', value.get('value'))
            match = re.fullmatch(r'(\d+)(?:px)?', str(value))
            if match and 240 <= int(match[1]) <= 7680:
                point = int(match[1])
                # A detector without min/max semantics cannot disambiguate the
                # boundary pixel, so keep both sides out of the inferred band.
                cuts.update((point, point + 1))
        if cuts:
            evidence.append('detected-breakpoints.json')
    for folder in ('ref-css', 'css', 'resources', 'bundles'):
        for path in (ref_dir / folder).rglob('*.css'):
            text = path.read_text(errors='replace')
            found = False
            for kind, raw, unit in re.findall(r'\(\s*(min|max)-width\s*:\s*([\d.]+)(px|em|rem)\s*\)', text):
                number = float(raw) * (16 if unit != 'px' else 1)
                point = int(number) + (1 if kind == 'max' else 0)
                if 240 <= point <= 7680:
                    cuts.add(point)
                    found = True
            for operator, raw, unit in re.findall(r'\(\s*width\s*(<=|>=|<|>)\s*([\d.]+)(px|em|rem)\s*\)', text):
                number = float(raw) * (16 if unit != 'px' else 1)
                point = int(number) + (1 if operator in {'<=', '>'} else 0)
                if 240 <= point <= 7680:
                    cuts.add(point)
                    found = True
            if found:
                evidence.append(str(path.relative_to(ref_dir)))
    if cuts:
        low = max((v for v in cuts if v <= 1440), default=1440)
        high = min((v - 1 for v in cuts if v > 1440), default=1920)
    else:
        low = high = 1440
    bounds = [{'w': w, 'h': 900, 'label': 'desktop-boundary'} for w in sorted({low, high} - {1440})]
    return {
        'schemaVersion': 1, 'mode': mode,
        'completionLabel': 'desktop-only' if mode == 'desktop' else 'all-requested-layouts',
        'representative': {'w': 1440, 'h': 900, 'label': 'capture'},
        'range': {'minWidth': low, 'maxWidth': high},
        'rangeEvidence': 'css-or-detected-breakpoints' if cuts else 'representative-only',
        'evidenceFiles': sorted(set(evidence)),
        'boundaryViewports': bounds if mode == 'desktop' else [],
        'availableOtherLayouts': [{'boundaryWidth': v, 'status': 'unverified'} for v in sorted(cuts) if v <= low or v > high],
        'rangeRequiresRuntimeConfirmation': True,
    }


def apply_scope(plan: dict[str, Any], ref_dir: Path, mode: str = 'desktop') -> None:
    scope = resolve_scope(ref_dir, mode)
    plan['verificationScope'] = scope
    if mode == 'all':
        return
    plan['viewports'] = [scope['representative']]
    excluded: list[dict[str, Any]] = []
    for key in ('requiredChecks', 'deferredChecks'):
        rows = plan.get(key, [])
        excluded.extend(row for row in rows if row.get('id') in MOBILE_CHECKS)
        plan[key] = [row for row in rows if row.get('id') not in MOBILE_CHECKS]
    plan['outOfScopeChecks'] = excluded
    # One live DOM-geometry check at the representative and band edges replaces
    # expensive pixel baselines at every nearby width.
    #
    # This row is ALSO defined via `add_check "desktop-band-fluidity"` in
    # skills/visual-debug/scripts/verification-plan.sh (evidence-gated: only
    # when detected-breakpoints.json has >=2 breakpoints, so it can still fire
    # under --scope=all). The `not any(...)` guard below avoids a literal
    # duplicate row when that gate already added it. The differing gating is
    # intentional (desktop scope always wants this cheap check; `all` scope
    # only wants it with real evidence) — but script/produces/severity/tier/
    # dependsOn/argsRecipe MUST stay identical between the two definitions,
    # since both eventually execute the same dispatch row. Keep them in sync;
    # tests/test_verification_scope.py::test_desktop_band_fluidity_definitions_stay_in_lockstep
    # fails loudly if they drift.
    checks = plan.setdefault('requiredChecks', [])
    if plan.get('tier', 'comprehensive') != 'quick' and not any(r.get('id') == 'desktop-band-fluidity' for r in checks):
        checks.append({'id': 'desktop-band-fluidity', 'script': 'skills/visual-debug/scripts/desktop-band-fluidity-check.sh',
                       'produces': 'desktop-band-fluidity.json', 'severity': 'block', 'tier': 'standard',
                       'reason': 'Cheap desktop scope boundary geometry and overflow parity',
                       'dependsOn': ['runtime-env'],
                       'argsRecipe': '{session} {ref_url} {impl_url} {ref_dir}'})


def probe_widths(ref_dir: Path) -> list[int] | None:
    """None preserves legacy/all-scope behavior; desktop returns bounded probes."""
    path = ref_dir / 'verification-plan.json'
    if not path.exists():
        return None
    scope = json.loads(path.read_text()).get('verificationScope', {})
    if scope.get('mode') != 'desktop':
        return None
    return sorted({scope['representative']['w'], *(v['w'] for v in scope['boundaryViewports'])})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('ref_dir', type=Path)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--scope', choices=('desktop', 'all'), default='desktop')
    args = parser.parse_args()
    data = json.loads(args.plan.read_text())
    apply_scope(data, args.ref_dir, args.scope)
    args.plan.write_text(json.dumps(data, indent=2) + '\n')


if __name__ == '__main__':
    main()
