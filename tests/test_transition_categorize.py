"""Tests for the Step H transition categorizer.

Enriches transition-spec.json with `fingerprint` + `feel` per transition,
and adds top-level `motion_signature` aggregate. Idempotent unless
--rebuild is passed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract" / "transition-categorize.sh"


def _make_ref(tmp_path: Path, spec: dict) -> Path:
    ref = tmp_path / "comp"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps(spec))
    return ref


def _run(ref: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_runs_with_python39_annotation_semantics(tmp_path: Path) -> None:
    """The script calls host python3, so inline Python must not require 3.10+."""
    ref = _make_ref(tmp_path, {
        "transitions": [{
            "id": "section-reveal",
            "trigger": "scroll",
            "animation": {
                "property": "opacity",
                "from": {"opacity": 0},
                "to": {"opacity": 1},
                "duration": "scroll-tied",
                "easing": "linear",
            },
        }],
    })
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "python3"
    shim.write_text(
        "#!" + sys.executable + "\n"
        "import subprocess\n"
        "import sys\n"
        "source = sys.stdin.read()\n"
        "future_lines = source.splitlines()[:10]\n"
        "if '| None' in source and 'from __future__ import annotations' not in future_lines:\n"
        "    sys.stderr.write(\"TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'\\n\")\n"
        "    raise SystemExit(1)\n"
        f"proc = subprocess.run([{sys.executable!r}, *sys.argv[1:]], input=source, text=True)\n"
        "raise SystemExit(proc.returncode)\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"}

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), "--rebuild"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"


def test_scroll_fade_up_recognized(tmp_path: Path) -> None:
    """Trigger=scroll + opacity + translateY → scroll-fade-up-scrubbed."""
    ref = _make_ref(tmp_path, {
        "transitions": [{
            "id": "section-reveal",
            "trigger": "scroll",
            "animation": {
                "property": "opacity, transform",
                "from": {"opacity": 0, "translateY": "40px"},
                "to":   {"opacity": 1, "translateY": "0"},
                "duration": "scroll-tied",
                "easing": "linear (scroll-tied)",
            },
        }],
    })
    proc = _run(ref, "--rebuild")
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    spec = json.loads((ref / "transition-spec.json").read_text())
    t = spec["transitions"][0]
    assert t["fingerprint"] == "scroll-fade-up-scrubbed"
    assert t["feel"] == "scrubbed"


def test_page_load_fade_up_recognized(tmp_path: Path) -> None:
    ref = _make_ref(tmp_path, {
        "transitions": [{
            "id": "hero",
            "trigger": "load",
            "animation": {
                "property": "opacity, transform",
                "from": {"opacity": 0, "translateY": "20px"},
                "to":   {"opacity": 1, "translateY": "0"},
                "duration": "600ms",
                "easing": "cubic-bezier(0.4, 0, 0.2, 1)",
            },
        }],
    })
    _run(ref, "--rebuild")
    t = json.loads((ref / "transition-spec.json").read_text())["transitions"][0]
    assert t["fingerprint"] == "page-load-fade-up"
    assert t["feel"] == "gentle"


def test_springy_easing_detected(tmp_path: Path) -> None:
    """Overshoot bezier (y > 1.0) → feel=springy."""
    ref = _make_ref(tmp_path, {
        "transitions": [{
            "id": "modal-pop",
            "trigger": "click",
            "animation": {
                "property": "transform",
                "from": {"scale": 0.8},
                "to":   {"scale": 1.0},
                "duration": "300ms",
                "easing": "cubic-bezier(0.68, -0.05, 0.265, 1.55)",
            },
        }],
    })
    _run(ref, "--rebuild")
    t = json.loads((ref / "transition-spec.json").read_text())["transitions"][0]
    assert t["feel"] == "springy"
    assert t["fingerprint"] == "click-pulse"


def test_hover_pop_recognized(tmp_path: Path) -> None:
    ref = _make_ref(tmp_path, {
        "transitions": [{
            "id": "btn-hover",
            "trigger": "hover",
            "animation": {
                "property": "transform",
                "from": {"scale": 1.0},
                "to":   {"scale": 1.05},
                "duration": "150ms",
                "easing": "ease-out",
            },
        }],
    })
    _run(ref, "--rebuild")
    t = json.loads((ref / "transition-spec.json").read_text())["transitions"][0]
    assert t["fingerprint"] == "hover-pop"


def test_video_autoplay_recognized(tmp_path: Path) -> None:
    ref = _make_ref(tmp_path, {
        "transitions": [{
            "id": "hero-video",
            "trigger": "load",
            "animation": {
                "property": "video.play()",
                "from": {"paused": True},
                "to":   {"playing": True},
                "duration": "loop",
                "easing": "n/a",
            },
        }],
    })
    _run(ref, "--rebuild")
    t = json.loads((ref / "transition-spec.json").read_text())["transitions"][0]
    assert t["fingerprint"] == "video-autoplay"


def test_motion_signature_aggregate(tmp_path: Path) -> None:
    ref = _make_ref(tmp_path, {
        "transitions": [
            {"id": "a", "trigger": "scroll",
             "animation": {"property": "opacity", "from": {"opacity": 0},
                           "to": {"opacity": 1}, "duration": "scroll-tied",
                           "easing": "linear"}},
            {"id": "b", "trigger": "load",
             "animation": {"property": "transform", "from": {"scale": 0.95},
                           "to": {"scale": 1}, "duration": "300ms",
                           "easing": "cubic-bezier(0.68, -0.05, 0.265, 1.55)"}},
        ],
    })
    _run(ref, "--rebuild")
    spec = json.loads((ref / "transition-spec.json").read_text())
    sig = spec["motion_signature"]
    assert sig["transitions_count"] == 2
    assert sig["scroll_linked"] is True
    assert sig["has_spring"] is True
    assert isinstance(sig["fingerprint_summary"], list)
    assert any("scroll" in f for f in sig["fingerprint_summary"])


def test_idempotent_without_rebuild(tmp_path: Path) -> None:
    """Existing fingerprint/feel values are preserved without --rebuild."""
    ref = _make_ref(tmp_path, {
        "transitions": [{
            "id": "x",
            "trigger": "scroll",
            "fingerprint": "custom-manual-label",
            "feel": "custom-feel",
            "animation": {"property": "opacity", "from": {"opacity": 0},
                          "to": {"opacity": 1}, "duration": "500ms",
                          "easing": "ease-out"},
        }],
    })
    _run(ref)  # no --rebuild
    t = json.loads((ref / "transition-spec.json").read_text())["transitions"][0]
    assert t["fingerprint"] == "custom-manual-label"
    assert t["feel"] == "custom-feel"


def test_rebuild_overrides_existing(tmp_path: Path) -> None:
    """--rebuild recomputes even when fields are populated."""
    ref = _make_ref(tmp_path, {
        "transitions": [{
            "id": "x",
            "trigger": "scroll",
            "fingerprint": "stale-label",
            "animation": {"property": "opacity, transform",
                          "from": {"opacity": 0, "translateY": "10px"},
                          "to":   {"opacity": 1, "translateY": "0"},
                          "duration": "scroll-tied",
                          "easing": "linear"},
        }],
    })
    _run(ref, "--rebuild")
    t = json.loads((ref / "transition-spec.json").read_text())["transitions"][0]
    assert t["fingerprint"] == "scroll-fade-up-scrubbed"


def test_no_transition_spec_skips(tmp_path: Path) -> None:
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


def test_categorize_upgrades_regions_from_real_transitions(tmp_path: Path) -> None:
    """Step H finalizes the spec, so it is also where regions.json is upgraded
    from the honest placeholder to the derived real regions (best-effort)."""
    ref = _make_ref(tmp_path, {
        "transitions": [{
            "id": "hero-scrub",
            "trigger": "scroll",
            "selector": ".dga-module__hero",
            "animation": {"property": "opacity", "from": {"opacity": 0},
                          "to": {"opacity": 1}, "duration": "scroll-tied",
                          "easing": "linear"},
        }],
    })
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 0, "top": 100, "height": 600,
                      "className": "dga-module__hero", "id": None}],
    }))
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": True, "detectionRan": False,
        "regions": [{"name": "full-page", "x": 0, "y": 0,
                     "width": 1440, "height": 5000}],
    }))
    proc = _run(ref)
    assert proc.returncode == 0, proc.stderr
    regions = json.loads((ref / "regions.json").read_text())
    assert regions["placeholder"] is False
    assert regions["regions"][0]["name"] == "hero-scrub"
    assert regions["regions"][0]["y"] == 100


def test_categorize_preserves_placeholder_without_real_transitions(tmp_path: Path) -> None:
    """No real transitions → the honest placeholder regions.json is preserved,
    never overwritten with a fabricated region."""
    ref = _make_ref(tmp_path, {"transitions": []})
    (ref / "regions.json").write_text(json.dumps({
        "placeholder": True, "detectionRan": False,
        "regions": [{"name": "full-page", "x": 0, "y": 0,
                     "width": 1440, "height": 5000}],
    }))
    proc = _run(ref)
    assert proc.returncode == 0, proc.stderr
    regions = json.loads((ref / "regions.json").read_text())
    assert regions["placeholder"] is True
