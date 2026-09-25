"""`ui_clone/scoped_producers.sha256.json` — the release hash manifest of the
scoped-evidence producers — must match the checked-in producer files, and
`python -m ui_clone.scoped_producers --check` is the CI gate for it."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ui_clone import scoped_producers
from ui_clone.scoped_provenance import driver_script_sha256, module_sha256

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_matches_checked_in_producers() -> None:
    """Fails when a producer changed without `python -m ui_clone.scoped_producers --write`."""
    assert scoped_producers.problems() == [], (
        "scoped producer files changed; regenerate the manifest with "
        "`python -m ui_clone.scoped_producers --write`"
    )
    shipped = scoped_producers.load_manifest()
    assert shipped is not None and set(shipped) == set(scoped_producers.PRODUCER_FILES)
    # The hashes evidence records carry are the manifest's.
    assert shipped[scoped_producers.CAPTURE_MODULE] == module_sha256("ui_clone.element_capture")
    assert shipped[scoped_producers.DIFF_MODULE] == module_sha256("ui_clone.scoped_diff")
    assert shipped[scoped_producers.DRIVER_SCRIPT] == driver_script_sha256()


def test_manifest_is_content_based(tmp_path: Path) -> None:
    """Hashes are of file content: a copy of the producers under another root
    (a plugin cache path, a bumped version) matches the same manifest."""
    for rel in scoped_producers.PRODUCER_FILES:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / rel).read_bytes())
    assert scoped_producers.problems(tmp_path) == []
    (tmp_path / scoped_producers.CAPTURE_MODULE).write_text("# edited\n", encoding="utf-8")
    assert scoped_producers.problems(tmp_path) == [
        f"{scoped_producers.CAPTURE_MODULE}: installed file differs from {scoped_producers.MANIFEST_NAME}"
    ]
    (tmp_path / scoped_producers.DRIVER_SCRIPT).unlink()
    assert f"{scoped_producers.DRIVER_SCRIPT}: not installed" in scoped_producers.problems(tmp_path)


def test_manifest_shape_problems(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text("{", encoding="utf-8")
    assert scoped_producers.load_manifest(path) is None
    assert "missing or malformed" in scoped_producers.problems(path=path)[0]
    shipped = scoped_producers.load_manifest()
    assert shipped is not None
    path.write_text(json.dumps({"schemaVersion": 1, "files": {**shipped, "ui_clone/other.py": "0" * 64}}), encoding="utf-8")
    assert scoped_producers.problems(path=path) == [
        "ui_clone/other.py: listed in m.json but not a producer file"
    ]
    assert scoped_producers.shipped_sha256(scoped_producers.DIFF_MODULE, {}) is None


def test_write_regenerates_from_installed_files(tmp_path: Path) -> None:
    out = tmp_path / "m.json"
    scoped_producers.write(out)
    assert scoped_producers.load_manifest(out) == scoped_producers.load_manifest()


@pytest.mark.parametrize("args, code", [(["--check"], 0), ([], 2), (["--check", "--write"], 2)])
def test_cli_exit_codes(args: list[str], code: int) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "ui_clone.scoped_producers", *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )
    assert proc.returncode == code, proc.stdout + proc.stderr
    if code == 0:
        assert "match scoped_producers.sha256.json" in proc.stdout
