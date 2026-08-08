"""Tests for the Step E DTCG motion-token emitter."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract" / "transition-to-dtcg.sh"


def _make_ref(tmp_path: Path, spec: dict) -> Path:
    ref = tmp_path / "comp"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps(spec))
    return ref


def _run(ref: Path, out: Path | None = None) -> subprocess.CompletedProcess[str]:
    args = ["bash", str(SCRIPT), str(ref)]
    if out is not None:
        args.append(str(out))
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


def test_emits_dtcg_duration_tokens(tmp_path: Path) -> None:
    ref = _make_ref(tmp_path, {
        "site": "https://x.test",
        "transitions": [
            {"id": "a", "animation": {"duration": "150ms"}},
            {"id": "b", "animation": {"duration": "600ms"}},
        ],
    })
    out = tmp_path / "tokens.json"
    proc = _run(ref, out)
    assert proc.returncode == 0
    tokens = json.loads(out.read_text())
    assert "duration" in tokens
    durations = tokens["duration"]
    assert any(v["ms"] == 150 for v in durations.values())
    assert any(v["ms"] == 600 for v in durations.values())
    # DTCG canonical shape
    for entry in durations.values():
        assert entry["$type"] == "duration"
        assert entry["$value"].endswith("ms")


def test_emits_dtcg_easing_tokens(tmp_path: Path) -> None:
    ref = _make_ref(tmp_path, {
        "transitions": [
            {"id": "a", "animation": {
                "duration": "300ms",
                "easing": "cubic-bezier(0.4, 0, 0.2, 1)",
            }},
            {"id": "b", "animation": {
                "duration": "300ms",
                "easing": "cubic-bezier(0.68, -0.05, 0.265, 1.55)",
            }},
        ],
    })
    out = tmp_path / "tokens.json"
    _run(ref, out)
    tokens = json.loads(out.read_text())
    easings = tokens["easing"]
    assert len(easings) >= 2
    # Spring (overshoot) recognized
    spring_keys = [k for k, v in easings.items() if "spring" in k]
    assert spring_keys, f"expected spring token in {list(easings.keys())}"
    # All entries are DTCG cubicBezier
    for v in easings.values():
        assert v["$type"] == "cubicBezier"


def test_duration_bucket_names_stable(tmp_path: Path) -> None:
    """Same ms value should always get the same bucket name."""
    ref = _make_ref(tmp_path, {
        "transitions": [
            {"id": "fast", "animation": {"duration": "120ms"}},   # xs
            {"id": "snappy", "animation": {"duration": "250ms"}},  # sm
            {"id": "smooth", "animation": {"duration": "450ms"}},  # md
        ],
    })
    out = tmp_path / "tokens.json"
    _run(ref, out)
    tokens = json.loads(out.read_text())
    names = set(tokens["duration"].keys())
    assert "xs" in names
    assert "sm" in names
    assert "md" in names


def test_scroll_tied_duration_skipped(tmp_path: Path) -> None:
    """scroll-tied transitions don't have a temporal duration to tokenize."""
    ref = _make_ref(tmp_path, {
        "transitions": [
            {"id": "scrub", "animation": {"duration": "scroll-tied"}},
        ],
    })
    out = tmp_path / "tokens.json"
    _run(ref, out)
    tokens = json.loads(out.read_text())
    assert tokens["duration"] == {}


def test_meta_block_includes_motion_signature(tmp_path: Path) -> None:
    """If transition-spec.json has top-level motion_signature, it carries through."""
    ref = _make_ref(tmp_path, {
        "site": "https://x.test",
        "motion_signature": {
            "dominant_feel": "scrubbed",
            "scroll_linked": True,
            "has_spring": False,
        },
        "transitions": [
            {"id": "a", "animation": {"duration": "300ms", "easing": "ease-out"}},
        ],
    })
    out = tmp_path / "tokens.json"
    _run(ref, out)
    tokens = json.loads(out.read_text())
    meta = tokens["$meta"]
    assert meta["site"] == "https://x.test"
    assert meta["motion_signature"]["dominant_feel"] == "scrubbed"
    assert meta["transitions_count"] == 1


def test_default_output_path(tmp_path: Path) -> None:
    """When no output arg, writes to <ref-dir>/motion-tokens.json."""
    ref = _make_ref(tmp_path, {
        "transitions": [{"id": "a", "animation": {"duration": "300ms"}}],
    })
    _run(ref)
    out_path = ref / "motion-tokens.json"
    assert out_path.is_file()
    json.loads(out_path.read_text())  # valid JSON


def test_no_spec_skips_gracefully(tmp_path: Path) -> None:
    ref = tmp_path / "comp"
    ref.mkdir()
    proc = _run(ref)
    assert proc.returncode == 0


def test_setup_error_on_bad_ref(tmp_path: Path) -> None:
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path / "no-ref")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 2
