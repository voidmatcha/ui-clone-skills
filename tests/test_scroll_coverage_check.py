import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "scroll-coverage-check.sh"


def test_scroll_coverage_uses_section_map_before_single_region_skip(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(
        json.dumps({
            "sourceUrl": "https://example.com",
            "regions": [{"name": "full-page", "x": 0, "y": 0, "width": 1280, "height": 6000}],
        }),
        encoding="utf-8",
    )
    (ref / "section-map.json").write_text(
        json.dumps({"sections": [{"index": i, "top": i * 800, "height": 800} for i in range(8)]}),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            str(ref),
            "https://example.com",
            "http://127.0.0.1:9",
            "scroll-coverage-test",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0
    status = json.loads((ref / "scroll-coverage.json").read_text(encoding="utf-8"))
    assert status["status"] == "skip"
    assert "impl URL not reachable" in status["reason"]
    assert "only 1" not in status["reason"]


def test_scroll_coverage_still_skips_true_single_region_page(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "regions.json").write_text(
        json.dumps({
            "sourceUrl": "https://example.com",
            "regions": [{"name": "full-page", "x": 0, "y": 0, "width": 1280, "height": 1200}],
        }),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), "https://example.com", "http://127.0.0.1:9"],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0
    status = json.loads((ref / "scroll-coverage.json").read_text(encoding="utf-8"))
    assert status["status"] == "skip"
    assert "only 1 regions/sections" in status["reason"]
