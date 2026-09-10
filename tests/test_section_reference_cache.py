"""Reference caches must never bless or reuse implementation results."""
import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).parents[1] / 'skills/visual-debug/scripts/section-reference-cache.py'


def helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location('reference_cache', SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(tmp_path: Path) -> Path:
    sections = tmp_path / 'sections'
    for name in ('ref', 'ref-calib', 'impl'):
        (sections / name).mkdir(parents=True)
        (sections / name / 'hero.png').write_bytes(b'pixels')
    (sections / 'ref-sections.json').write_text('[{"id":"hero"}]')
    (sections / 'result.txt').write_text('PASS')
    return sections


def test_restore_references_only_and_detect_corruption(tmp_path: Path) -> None:
    cache = helper()
    sections = fixture(tmp_path)
    key = cache.cache_key(tmp_path, 'https://example.org', {})
    assert cache.save(tmp_path, key)
    (sections / 'ref/hero.png').write_bytes(b'changed')
    (sections / 'impl/hero.png').write_bytes(b'new implementation')
    assert cache.restore(tmp_path, key, 3600)
    assert (sections / 'ref/hero.png').read_bytes() == b'pixels'
    assert (sections / 'impl/hero.png').read_bytes() == b'new implementation'
    stored = tmp_path / '.section-reference-cache' / key
    assert not (stored / 'sections/result.txt').exists()
    (stored / 'sections/ref/hero.png').write_bytes(b'corrupt')
    assert not cache.restore(tmp_path, key, 3600)


def test_key_invalidates_source_viewport_url_and_settings_not_impl(tmp_path: Path) -> None:
    cache = helper()
    (tmp_path / 'dom.html').write_text('reference')
    original = cache.cache_key(tmp_path, 'https://example.org', {})
    (tmp_path / 'impl.tsx').write_text('new implementation')
    assert original == cache.cache_key(tmp_path, 'https://example.org', {})
    for env in ({'VIEW_W': '1280'}, {'VIEWPORTS': '375x812'}, {'WAIT_REF': '9000'}):
        assert original != cache.cache_key(tmp_path, 'https://example.org', env)
    assert original != cache.cache_key(tmp_path, 'https://other.org', {})
    (tmp_path / 'dom.html').write_text('different reference')
    assert original != cache.cache_key(tmp_path, 'https://example.org', {})


def test_expired_missing_and_symlink_cache_refused(tmp_path: Path) -> None:
    cache = helper()
    fixture(tmp_path)
    key = cache.cache_key(tmp_path, 'https://example.org', {})
    assert cache.save(tmp_path, key)
    assert not cache.restore(tmp_path, key, 0)
    stored = tmp_path / '.section-reference-cache' / key
    target = stored / 'sections/ref-calib/hero.png'
    target.unlink()
    target.symlink_to(tmp_path / 'sections/ref/hero.png')
    assert not cache.restore(tmp_path, key, 3600)


def test_wrapper_reuses_only_reference_passes(tmp_path: Path) -> None:
    import os
    import shutil
    import subprocess

    scripts = tmp_path / 'skills/visual-debug/scripts'
    scripts.mkdir(parents=True)
    for name in ('section-reference-cache.py', 'section-compare-frozen.sh'):
        shutil.copyfile(SCRIPT.parent / name, scripts / name)
    calls = tmp_path / 'calls'
    (scripts / 'section-compare.sh').write_text(
        '#!/bin/bash\n'
        'echo "$2" >> "$CALLS"\n'
        'mkdir -p "$4/sections/ref" "$4/sections/impl"\n'
        'echo pixels > "$4/sections/ref/hero.png"\n'
        'echo pixels > "$4/sections/impl/hero.png"\n'
        'echo \'[{"id":"hero"}]\' > "$4/sections/ref-sections.json"\n'
        'exit 0\n'
    )
    out = tmp_path / 'output'
    env = {**os.environ, 'CALLS': str(calls)}
    command = ['bash', str(scripts / 'section-compare-frozen.sh'),
               'https://example.org', 'http://localhost:3000', 'proof', str(out)]
    for _ in range(2):
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    assert calls.read_text().splitlines() == [
        'https://example.org', 'https://example.org',
        'http://localhost:3000', 'http://localhost:3000']
    assert 'cache hit' in result.stdout


def test_nested_capture_sources_invalidate_but_impl_sources_do_not(tmp_path: Path) -> None:
    cache = helper()
    for folder in ('ref-css', 'css', 'resources', 'bundles', 'assets'):
        nested = tmp_path / folder / 'nested/source.bin'
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b'reference source')
        before = cache.cache_key(tmp_path, 'https://example.org', {})
        nested.write_bytes(b'changed source')
        assert before != cache.cache_key(tmp_path, 'https://example.org', {}), folder
    before = cache.cache_key(tmp_path, 'https://example.org', {})
    implementation = tmp_path / 'impl/src/ref-css/styles.css'
    implementation.parent.mkdir(parents=True)
    implementation.write_text('body {color:red}')
    assert before == cache.cache_key(tmp_path, 'https://example.org', {})
    for name in ('SCROLL_PHASE_TOL_PX', 'AE_SATURATION', 'AUTO_MOTION_STRUCTURAL_ONLY_PATTERNS'):
        assert before != cache.cache_key(tmp_path, 'https://example.org', {name: '42'})
