"""Tests for ui_clone.visual_judge_dispatcher (D from
docs/visual-judge-dispatcher-design.md + codex review 2026-05-25).

All tests use subprocess.run monkeypatch — no live `claude --print`.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from ui_clone import visual_judge_dispatcher as vjd

# ── helpers ──────────────────────────────────────────────────────────


def _png_bytes(seed: int) -> bytes:
    """Return distinct bytes per seed so different inputs hash differently."""
    return b"\x89PNG\r\n\x1a\n" + bytes([seed % 256]) * 64


def _setup_ref_with_pngs(tmp_path: Path, label: str = "hero") -> dict[str, Path]:
    """Build a minimal ref_dir with sections/ref + sections/impl PNGs."""
    ref_dir = tmp_path / "ref"
    sections = ref_dir / "sections"
    (sections / "ref").mkdir(parents=True)
    (sections / "impl").mkdir()
    ref_png = sections / "ref" / f"{label}.png"
    impl_png = sections / "impl" / f"{label}.png"
    ref_png.write_bytes(_png_bytes(1))
    impl_png.write_bytes(_png_bytes(2))
    return {"ref_dir": ref_dir, "ref_png": ref_png, "impl_png": impl_png}


def _fake_subprocess_writer(payload: dict, returncode: int = 0):  # type: ignore[no-untyped-def]
    """Return a fake subprocess.run that writes payload to the --out path
    and returns CompletedProcess with the given returncode."""

    def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        # cmd shape: ["bash", visual_judge_sh, ref_png, impl_png, "--out", tmp, "--label", label]
        try:
            out_index = cmd.index("--out")
            out_path = Path(cmd[out_index + 1])
        except (ValueError, IndexError):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode, "", "fake stderr")

    return fake_run


# ── tests ────────────────────────────────────────────────────────────


def test_cache_hit_returns_cached_without_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing cache file → dispatcher returns parsed dict, subprocess
    never invoked."""
    s = _setup_ref_with_pngs(tmp_path)
    # Pre-write cache at the computed key path
    key = vjd._cache_key(s["ref_png"], s["impl_png"], "hero")
    cache_dir = s["ref_dir"] / "sections" / "visual-judge-cache"
    cache_dir.mkdir()
    cached_payload = {"priority_fix": "use grid", "findings": []}
    (cache_dir / f"{key}.json").write_text(
        json.dumps(cached_payload), encoding="utf-8"
    )

    called = [0]

    def fake_run(*a, **kw):  # type: ignore[no-untyped-def]
        called[0] += 1
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = vjd.dispatch_visual_judge(
        s["ref_dir"], "hero", s["ref_png"], s["impl_png"]
    )
    assert result == cached_payload
    assert called[0] == 0, "subprocess must not be called on cache hit"


def test_cache_miss_runs_subprocess_and_publishes_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache miss → subprocess runs → tmp written → atomic replace → cached
    payload returned."""
    s = _setup_ref_with_pngs(tmp_path)
    payload = {"priority_fix": "rename selector", "findings": ["a", "b"]}
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_writer(payload))

    result = vjd.dispatch_visual_judge(
        s["ref_dir"], "hero", s["ref_png"], s["impl_png"]
    )
    assert result == payload

    # Cache file persisted at the expected path
    key = vjd._cache_key(s["ref_png"], s["impl_png"], "hero")
    cache_path = (
        s["ref_dir"] / "sections" / "visual-judge-cache" / f"{key}.json"
    )
    assert cache_path.is_file()
    assert json.loads(cache_path.read_text()) == payload


def test_non_zero_returncode_raises_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review item (e): non-zero exit must raise VisualJudgeError
    carrying returncode + stderr, NOT silently return None."""
    s = _setup_ref_with_pngs(tmp_path)
    # visual-judge.sh exit 3 == claude CLI missing
    monkeypatch.setattr(
        subprocess, "run", _fake_subprocess_writer({}, returncode=3)
    )

    with pytest.raises(vjd.VisualJudgeError) as exc:
        vjd.dispatch_visual_judge(
            s["ref_dir"], "hero", s["ref_png"], s["impl_png"]
        )
    assert exc.value.returncode == 3
    assert "fake stderr" in exc.value.stderr


def test_invalid_json_response_raises_and_does_not_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subprocess writes non-JSON → dispatcher raises, no cache published."""
    s = _setup_ref_with_pngs(tmp_path)

    def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        out_index = cmd.index("--out")
        out_path = Path(cmd[out_index + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("not json{{{", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(vjd.VisualJudgeError):
        vjd.dispatch_visual_judge(
            s["ref_dir"], "hero", s["ref_png"], s["impl_png"]
        )

    # No cache published because validation failed
    key = vjd._cache_key(s["ref_png"], s["impl_png"], "hero")
    cache_path = (
        s["ref_dir"] / "sections" / "visual-judge-cache" / f"{key}.json"
    )
    assert not cache_path.is_file()


def test_per_key_lock_blocks_concurrent_same_key_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two threads with same cache key → only one subprocess invocation.
    Second thread acquires lock after first releases and sees cache hit."""
    s = _setup_ref_with_pngs(tmp_path)
    invocations: list[int] = []
    sub_lock = threading.Lock()

    def slow_subprocess(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        with sub_lock:
            invocations.append(1)
        # Slow enough that the other thread is definitely waiting on lock
        time.sleep(0.2)
        out_index = cmd.index("--out")
        out_path = Path(cmd[out_index + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({"k": "v"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", slow_subprocess)

    results: list[dict | None] = [None, None]

    def worker(idx: int) -> None:
        results[idx] = vjd.dispatch_visual_judge(
            s["ref_dir"], "hero", s["ref_png"], s["impl_png"]
        )

    t1 = threading.Thread(target=worker, args=(0,))
    t2 = threading.Thread(target=worker, args=(1,))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert results[0] == {"k": "v"}
    assert results[1] == {"k": "v"}
    assert len(invocations) == 1, (
        f"per-key lock must serialize: expected 1 subprocess call, "
        f"got {len(invocations)}"
    )


def test_cache_key_sensitivity(tmp_path: Path) -> None:
    """Codex review item (b): cache key must change when any of {ref PNG
    bytes, impl PNG bytes, label, prompt content, script content} changes."""
    s = _setup_ref_with_pngs(tmp_path)
    baseline = vjd._cache_key(s["ref_png"], s["impl_png"], "hero")

    # Different label → different key
    other_label = vjd._cache_key(s["ref_png"], s["impl_png"], "footer")
    assert other_label != baseline

    # Different impl PNG bytes → different key
    s["impl_png"].write_bytes(_png_bytes(99))
    other_impl = vjd._cache_key(s["ref_png"], s["impl_png"], "hero")
    assert other_impl != baseline

    # Different ref PNG bytes → different key
    s["impl_png"].write_bytes(_png_bytes(2))  # restore
    s["ref_png"].write_bytes(_png_bytes(99))
    other_ref = vjd._cache_key(s["ref_png"], s["impl_png"], "hero")
    assert other_ref != baseline
