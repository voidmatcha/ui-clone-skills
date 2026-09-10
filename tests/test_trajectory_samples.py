from pathlib import Path

from ui_clone.trajectory_samples import sample_points


def test_early_captured_width_range_adds_local_midpoints() -> None:
    runtime = {
        "scrollLinkedStyles": [
            {
                "selector": "#hero",
                "filter": ["width"],
                "byScroll": {
                    "0": {"width": "80vw"},
                    "0.025": {"width": "100vw"},
                    "1": {"width": "100vw"},
                },
            }
        ]
    }
    points, missing, unresolved = sample_points(["#hero"], runtime, [0, 25, 50, 75, 100])
    assert {0, 1.25, 2.5, 25, 50, 75, 100} <= set(points)
    assert not missing
    assert not unresolved


def test_missing_range_is_reported_not_fabricated() -> None:
    # No requires_evidence set passed -> conservative default: an unlinked
    # target with zero evidence is NOT contractually required to have a
    # runtime-dump row, so it is "unresolved" (informational), not "missing"
    # (hard exit-2). Callers that know a target IS runtime-dump-sourced pass
    # requires_evidence explicitly (see test below).
    points, missing, unresolved = sample_points(["#missing"], {}, [0, 25, 50, 75, 100])
    assert points == [0, 25, 50, 75, 100]
    assert missing == []
    assert unresolved == ["#missing"]


def test_missing_range_on_a_runtime_dump_sourced_target_is_a_hard_failure() -> None:
    points, missing, unresolved = sample_points(
        ["#hero"], {}, [0, 25, 50, 75, 100], requires_evidence={"#hero"}
    )
    assert points == [0, 25, 50, 75, 100]
    assert missing == ["#hero"]
    assert unresolved == []


def test_bundle_sourced_target_without_byscroll_evidence_does_not_block(
    tmp_path: Path,
) -> None:
    # fable-20260910 follow-up (F1 residual): a bundle-derived scroll-scrub
    # target (sourceArtifact: bundle-extraction.json) or a class-toggle
    # scroll-state-machine has NO inline-style byScroll row to ever capture —
    # main() must not exit 2 for it, only fall back to the global points.
    import json
    import subprocess
    import sys

    ref = tmp_path
    (ref / "animation-runtime-dump.json").write_text(json.dumps({}))
    (ref / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "target": ".bundle-scrub",
                        "sourceArtifact": "bundle-extraction.json",
                        "sourceId": "gsap-1",
                    }
                ]
            }
        )
    )
    proc = subprocess.run(
        [sys.executable, "-m", "ui_clone.trajectory_samples", str(ref), ".bundle-scrub", "0", "25", "50", "75", "100"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["0", "25", "50", "75", "100"]
    receipt = json.loads((ref / "transitions/trajectory-sampling.json").read_text())
    assert receipt["status"] == "warn"
    assert receipt["missingRangeTargets"] == []
    assert receipt["unresolvedLocalRangeTargets"] == [".bundle-scrub"]


def test_local_probe_exposes_width_mismatch_hidden_between_global_points(tmp_path: Path) -> None:
    import json
    import re
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / 'skills/visual-debug/scripts/transition-trajectory-compare.sh'
    match = re.search(r"python3 - \"\$TARGET_DIR\" \"\$REPORT\" \$TRAJECTORY_POINTS <<'PY'\n(.*?)\nPY", script.read_text(), re.S)
    assert match
    global_points = [0, 25, 50, 75, 100]
    runtime = {'scrollLinkedStyles': [{'selector': '#hero', 'filter': ['width'], 'byScroll': {'0': {'width': '80vw'}, '0.025': {'width': '100vw'}, '0.05': {'width': '100vw'}, '1': {'width': '100vw'}}}]}
    points, missing, unresolved = sample_points(['#hero'], runtime, global_points)
    assert not missing
    assert not unresolved
    for point in points:
        for side in ('ref', 'impl'):
            width = 1470 if side == 'ref' and 0 < point < 5 else 1182
            row = {'selector': '#hero', 'rendered': True, 'className': 'hero', 'top': 0, 'left': (1440-width)/2, 'width': width, 'height': 600, 'ty': 0}
            (tmp_path / f'{side}-{point}.json').write_text(json.dumps([row]))
    report = tmp_path / 'result.txt'
    old = subprocess.run([sys.executable, '-c', match.group(1), str(tmp_path), str(report), *map(str, global_points)], capture_output=True, text=True, timeout=10)
    assert old.returncode == 0, old.stderr
    new = subprocess.run([sys.executable, '-c', match.group(1), str(tmp_path), str(report), *map(str, points)], capture_output=True, text=True, timeout=10)
    assert new.returncode == 1, new.stderr
    assert '❌' in report.read_text()
