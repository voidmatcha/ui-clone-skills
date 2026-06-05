from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "mobile-responsive-coverage-check.sh"


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl / "src")],
        capture_output=True, text=True, timeout=30,
    )


def _responsive_ref(tmp_path: Path) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (ref / "css").mkdir(parents=True)
    (ref / "detected-breakpoints.json").write_text(
        json.dumps([480, 768, 1024, 1280, 1536]), encoding="utf-8")
    # Dense responsive ref CSS.
    (ref / "css" / "main.css").write_text(
        "\n".join(f"@media (max-width:{w}px){{.a{{width:{w}px}}}}" for w in range(300, 400)),
        encoding="utf-8")
    return ref, impl


def test_non_responsive_impl_flagged(tmp_path: Path) -> None:
    ref, impl = _responsive_ref(tmp_path)
    (impl / "src" / "App.tsx").write_text(
        "export const App=()=> <div style={{width:1280}}>fixed</div>;\n", encoding="utf-8")
    proc = _run(ref, impl)
    art = json.loads((ref / "mobile-responsive-coverage.json").read_text())
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    assert art["implSignals"] < art["floor"]


def test_responsive_impl_passes(tmp_path: Path) -> None:
    ref, impl = _responsive_ref(tmp_path)
    (impl / "src" / "App.css").write_text(
        "\n".join(f"@media (max-width:{w}px){{.a{{width:{w}px}}}}" for w in range(300, 360)),
        encoding="utf-8")
    proc = _run(ref, impl)
    art = json.loads((ref / "mobile-responsive-coverage.json").read_text())
    assert proc.returncode == 0, art
    assert art["status"] == "pass"


def test_non_responsive_ref_skips(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    proc = _run(ref, impl)
    art = json.loads((ref / "mobile-responsive-coverage.json").read_text())
    assert proc.returncode == 0
    assert art["status"] == "skip"
