from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "color-token-grounding-check.sh"


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )


def test_colors_grounded_in_css_variables_txt_not_invented(tmp_path: Path) -> None:
    """Ref colors declared only in css/variables.txt must count as grounded —
    otherwise the gate over-reports them as 'invented' (refColorCount under-
    captured)."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (ref / "css").mkdir(parents=True)
    # Minimal styles.json so the gate has a palette and does not skip.
    (ref / "styles.json").write_text(
        json.dumps({"palette": "#000000 #ffffff"}), encoding="utf-8",
    )
    # Ref CSS variables — the colors the impl will use.
    (ref / "css" / "variables.txt").write_text(
        ":root{--a:#7a994c;--b:#8eb258;--c:#b7d96e;}\n", encoding="utf-8",
    )
    (impl / "src" / "global.css").write_text(
        ".x{color:#7a994c;background:#8eb258;border-color:#b7d96e;}\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "color-token-grounding.json").read_text())
    assert proc.returncode == 0, (
        f"variables.txt colors must be grounded, invented={art.get('invented')}"
    )
    assert art["status"] != "fail"
