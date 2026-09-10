"""Bounded async transport must never restart a timed out browser probe."""
import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

PATH = Path(__file__).parents[1] / 'scripts/extract/browser-async-eval.py'


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location('browser_async_eval', PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def envelope(state: str, **extra: object) -> dict[str, object]:
    return {'success': True, 'data': {'origin': 'https://example.test', 'result': {'state': state, **extra}}}


def test_pending_probe_starts_once_and_returns_original_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load()
    calls: list[str] = []
    def command(session: str, script: str, timeout: float) -> dict[str, object]:
        calls.append(script)
        return envelope('pending') if len(calls) < 3 else envelope('done', value={'checked': True})
    monkeypatch.setattr(module, 'command', command)
    result = module.run('s', 'Promise.resolve(3)', 3000, poll_ms=1)
    assert result == {'success': True, 'data': {'origin': 'https://example.test', 'result': {'checked': True}}}
    assert sum('Promise.resolve().then' in script for script in calls) == 1


@pytest.mark.parametrize('state', ['missing', 'error'])
def test_lost_slot_or_probe_failure_never_restarts(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    module = load()
    calls: list[tuple[str, str, float]] = []
    def command(session: str, script: str, timeout: float) -> dict[str, object]:
        calls.append((session, script, timeout))
        return envelope('pending') if len(calls) == 1 else envelope(state, error='broken')
    monkeypatch.setattr(module, 'command', command)
    with pytest.raises(RuntimeError):
        module.run('s', 'Promise.resolve(3)', 200, poll_ms=1)
    assert len(calls) == 2


def test_total_deadline_and_no_cleanup_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load()
    calls: list[tuple[str, str, float]] = []
    def command(session: str, script: str, timeout: float) -> dict[str, object]:
        calls.append((session, script, timeout))
        return envelope('pending')
    monkeypatch.setattr(module, 'command', command)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        module.run('s', 'Promise.resolve(3)', 25, poll_ms=2)
    assert time.monotonic() - start < .3
    assert all(args[2] <= .025 for args in calls)
    assert sum('Promise.resolve().then' in args[1] for args in calls) == 1


def test_navigation_during_poll_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load()
    answers = [envelope('pending'), {'success': True, 'data': {'origin': 'about:blank', 'result': {'state': 'done', 'value': {}}}}]
    monkeypatch.setattr(module, 'command', lambda *args: answers.pop(0))
    with pytest.raises(RuntimeError, match='origin'):
        module.run('s', 'Promise.resolve(3)', 200, poll_ms=1)


def test_stalled_cli_is_killed_at_remaining_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import subprocess

    module = load()
    browser = tmp_path / 'agent-browser'
    marker = tmp_path / 'started'
    browser.write_text(
        f'#!{sys.executable}\n'
        'import time\n'
        'from pathlib import Path\n'
        f'Path({str(marker)!r}).write_text("started")\n'
        'time.sleep(10)\n'
    )
    browser.chmod(0o755)
    monkeypatch.setenv('PATH', f'{tmp_path}{os.pathsep}{os.environ["PATH"]}')
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        module.run('s', 'Promise.resolve(3)', 3000, poll_ms=1)
    assert time.monotonic() - start < 6
    assert marker.read_text() == 'started'
