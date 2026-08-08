"""D24 (loop-nvti-1): junk-token-check.sh hung (superlinear CSS_DECL_RE) on a
2.6MB single-line minified reference CSS copied into impl/src/ref-css/ — the
row was process-group-killed and the FAIL was a hang, not a verdict. Guards:
ref-css/** is reference-sourced vendor bytes (out of scope for a generation
-junk check) and over-cap lines are skipped from regex scanning, both COUNTED
in the artifact (no silent cap)."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1]
          / "skills" / "visual-debug" / "scripts" / "junk-token-check.sh")


def _run(ref: Path, impl_src: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl_src)],
        capture_output=True, text=True, timeout=120,
    )
    artifact = json.loads((ref / "junk-token.json").read_text(encoding="utf-8"))
    return proc, artifact


def test_ref_css_vendor_bytes_are_excluded_and_counted(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    src = tmp_path / "impl" / "src"
    (src / "ref-css").mkdir(parents=True)
    # junk INSIDE ref-css must not fail the check (reference-sourced bytes)
    (src / "ref-css" / "vendor.css").write_text(
        ".x { color: undefined; }", encoding="utf-8"
    )
    (src / "App.tsx").write_text("export default () => null\n", encoding="utf-8")
    proc, artifact = _run(ref, src)
    assert artifact["status"] == "pass", artifact
    assert artifact["skippedRefCssFiles"] >= 1
    assert proc.returncode == 0


def test_minified_long_line_skipped_fast_and_counted(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    src = tmp_path / "impl" / "src"
    src.mkdir(parents=True)
    # one ~400k-char minified line OUTSIDE ref-css — pre-guard this class
    # scanned superlinearly (the D24 hang); post-guard it is skipped+counted.
    (src / "site.css").write_text(
        ".a{color:red}" * 30000 + "\n.ok { color: blue; }\n", encoding="utf-8"
    )
    start = time.monotonic()
    proc, artifact = _run(ref, src)
    elapsed = time.monotonic() - start
    assert elapsed < 60, f"scan took {elapsed:.1f}s — long-line guard not effective"
    assert artifact["skippedLongLines"] >= 1, artifact
    assert proc.returncode == 0, proc.stderr


def test_generated_css_junk_still_fails(tmp_path: Path) -> None:
    # The guards must not blunt the check itself: junk in GENERATED css
    # (outside ref-css, normal-length line) still fails.
    ref = tmp_path / "ref"
    ref.mkdir()
    src = tmp_path / "impl" / "src"
    src.mkdir(parents=True)
    (src / "styles.css").write_text(".x { width: NaNpx; color: undefined; }\n",
                                    encoding="utf-8")
    proc, artifact = _run(ref, src)
    assert artifact["status"] == "fail", artifact
    assert proc.returncode == 1
