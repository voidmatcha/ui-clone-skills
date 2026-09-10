#!/usr/bin/env python3
"""Immutable, expiring reference/calibration snapshots; never cache clone verdicts."""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REFERENCE_FILES = (
    'extracted.json', 'transition-spec.json', 'runtime-spec.json', 'section-map.json',
    'asset-substitution.json', 'paid-features.json', 'responsive-spec.json',
    'capture-inventory.json', 'dom.html', 'styles.css', 'computed-styles.json',
    'ref-screenshot-asset.json',
)
REFERENCE_OUTPUTS = (
    'ref-sections.json', 'ref-runtime-sections.json', 'ref-scroll-positions.json',
    'ref-semantic-candidates.json',
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def cache_key(out: Path, url: str, env: Mapping[str, str]) -> str:
    roots = {out.resolve()}
    if env.get('REF_ROOT_DIR'):
        roots.add(Path(env['REF_ROOT_DIR']).resolve())
    elif '/sections/viewports/' in str(out.resolve()):
        roots.add(Path(str(out.resolve()).split('/sections/viewports/')[0]))
    inputs = {}
    for root in sorted(roots):
        for name in REFERENCE_FILES:
            path = root / name
            inputs[str(path)] = digest(path) if path.is_file() else None
        for pattern in ('*.html', '*.css', 'reference_frames/*.png'):
            for path in sorted(root.glob(pattern)):
                if path.is_file():
                    inputs[str(path)] = digest(path)
        # Only captured-source directories are recursive. In particular,
        # impl/src/ref-css is generated output and must not invalidate this key.
        for folder in ('ref-css', 'css', 'resources', 'bundles', 'assets', 'reference_frames'):
            for path in sorted((root / folder).rglob('*')):
                if path.is_file():
                    inputs[str(path)] = digest(path)
    # Hash capture/comparison code too: same settings with different mechanics
    # are different baselines. This excludes all generated implementation code.
    for directory in (ROOT / 'ui_clone', ROOT / 'skills/visual-debug/scripts'):
        for path in sorted(directory.rglob('*')):
            if path.is_file() and path.suffix in ('.py', '.sh', '.js'):
                inputs[str(path.relative_to(ROOT))] = digest(path)
    settings = {key: value for key, value in env.items()
                if key.startswith(('VIEW', 'SECTION_', 'WAIT_', 'SKIP_', 'AGENT_BROWSER_',
                                   'DYNAMIC_', 'NO_', 'UI_CLONE_', 'MOTION_', 'PRESCROLL',
                                   'SCROLL_', 'AE_', 'DSSIM_', 'AUTO_MOTION_'))
                and key not in ('SECTION_FROZEN_RUN_NONCE', 'SECTION_REFERENCE_CACHE',
                                'UI_CLONE_REF_FROZEN_TTL_SEC')}
    settings['EXCLUDE_DYNAMIC'] = env.get('EXCLUDE_DYNAMIC', '1')
    ttl = min(86400, max(1, int(env.get('UI_CLONE_REF_FROZEN_TTL_SEC', '3600'))))
    payload = {'schema': 1, 'url': url, 'inputs': inputs, 'settings': settings,
               'expiryWindow': int(time.time() // ttl), 'ttl': ttl}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def allowed(name: str) -> bool:
    parts = Path(name).parts
    if not parts or parts[0] != 'sections' or '..' in parts:
        return False
    local = parts[1:]
    if len(local) >= 3 and local[0] == 'viewports':
        if not re.fullmatch(r'\d+x\d+', local[1]) or local[2] != 'sections':
            return False
        local = local[3:]
    return ((len(local) == 1 and local[0] in REFERENCE_OUTPUTS)
            or (len(local) == 2 and local[0] in ('ref', 'ref-calib')
                and local[1].endswith('.png')))


def complete(files: Iterable[str]) -> bool:
    groups: dict[str, set[str]] = {}
    for name in files:
        path = Path(name)
        group = path.parent.parent if path.suffix == '.png' else path.parent
        groups.setdefault(str(group), set()).add(name)
    return bool(groups) and all(
        f'{group}/ref-sections.json' in names
        and any(name.startswith(f'{group}/ref/') for name in names)
        and {Path(name).name for name in names if name.startswith(f'{group}/ref/')}
        == {Path(name).name for name in names if name.startswith(f'{group}/ref-calib/')}
        for group, names in groups.items())


def save(out: Path, key: str) -> bool:
    base = out / '.section-reference-cache'
    base.mkdir(exist_ok=True)
    target = base / key
    if target.exists():
        return False  # Existing entries are never refreshed or overwritten.
    files = {str(path.relative_to(out)): digest(path)
             for path in (out / 'sections').rglob('*')
             if path.is_file() and not path.is_symlink()
             and allowed(str(path.relative_to(out)))}
    if not complete(files):
        return False
    temporary = Path(tempfile.mkdtemp(prefix='.pending-', dir=base))
    try:
        for name in files:
            dest = temporary / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out / name, dest)
        (temporary / 'manifest.json').write_text(json.dumps(
            {'schema': 1, 'key': key, 'created': time.time(), 'files': files}))
        temporary.rename(target)
        return True
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def restore(out: Path, key: str, ttl: int) -> bool:
    target = out / '.section-reference-cache' / key
    try:
        if target.is_symlink() or (target / 'manifest.json').is_symlink():
            return False
        manifest = json.loads((target / 'manifest.json').read_text())
        age = time.time() - manifest['created']
        if manifest.get('schema') != 1 or manifest['key'] != key or not 0 <= age < ttl:
            return False
        files = manifest['files']
        if not isinstance(files, dict) or not complete(files):
            return False
        for name, checksum in files.items():
            path = target / name
            if (not allowed(name) or any(p.is_symlink() for p in (path, *path.parents))
                    or not path.is_file() or digest(path) != checksum):
                return False
            if path.suffix == '.json':
                json.loads(path.read_text())
            dest = out / name
            if any(p.is_symlink() for p in (dest, *dest.parents)):
                return False
        # Validation completes before altering output. Remove stale reference
        # files only; clone crops, matches and verdicts are never restored.
        for path in (out / 'sections').rglob('*'):
            if path.is_file() and allowed(str(path.relative_to(out))):
                path.unlink()
        for name in files:
            dest = out / name
            if any(p.is_symlink() for p in (dest, *dest.parents)):
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target / name, dest)
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main() -> int:
    mode, directory, value = sys.argv[1:4]
    out = Path(directory).resolve()
    if mode == 'key':
        print(cache_key(out, value, os.environ))
        return 0
    if mode == 'save':
        try:
            return 0 if save(out, value) else 1
        except OSError:
            return 1
    ttl = min(86400, max(0, int(os.environ.get('UI_CLONE_REF_FROZEN_TTL_SEC', '3600'))))
    return 0 if restore(out, value, ttl) else 1


if __name__ == '__main__':
    sys.exit(main())
