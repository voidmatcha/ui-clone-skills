"""Tests for the bundle-paste anti-cheat gate.

Catches the L41/L44 cheat shape: impl bulk-pastes ref's compiled CSS bundles,
_next runtime, or rendered HTML via ?raw + dangerouslySetInnerHTML.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "bundle-paste-check.sh"


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_r1_hex_hash_css_bundles_fail(tmp_path: Path) -> None:
    """L41 cheat: public/clone-css/<hash>.css × 3+ → fail with R1."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    pub_css = impl / "public" / "clone-css"
    pub_css.mkdir(parents=True)
    for h in ("71f1680b1f14a89f", "8f865eaf0943426a", "d1b6765a79b04f09"):
        (pub_css / f"{h}.css").write_text(".x{color:red}")

    proc = _run(ref, impl)
    assert proc.returncode == 1, f"hex-hash CSS dump must fail: {proc.stdout}"
    data = json.loads((ref / "bundle-paste-check.json").read_text())
    assert data["status"] == "fail"
    kinds = {v["rule"] for v in data["violations"]}
    assert "R1" in kinds


def test_r1_renamed_dir_still_caught(tmp_path: Path) -> None:
    """L44 evasion: public/css/ instead of public/clone-css/ — same shape, still fails."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    pub_css = impl / "public" / "css"
    pub_css.mkdir(parents=True)
    for h in ("ccae248ab1e1b137", "210f9000aa3d4011", "8f865eaf0943426a"):
        (pub_css / f"{h}.css").write_text(".x{color:red}")

    proc = _run(ref, impl)
    assert proc.returncode == 1
    data = json.loads((ref / "bundle-paste-check.json").read_text())
    r1 = next((v for v in data["violations"] if v["rule"] == "R1"), None)
    assert r1 is not None
    assert "public/css" in r1["dir"]


def test_r2_next_runtime_mirror_fails(tmp_path: Path) -> None:
    """public/_next/ runtime mirror → fail with R2."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    next_dir = impl / "public" / "_next" / "static" / "chunks"
    next_dir.mkdir(parents=True)
    (next_dir / "main-abc.js").write_text("/*next chunk*/")

    proc = _run(ref, impl)
    assert proc.returncode == 1
    data = json.loads((ref / "bundle-paste-check.json").read_text())
    kinds = {v["rule"] for v in data["violations"]}
    assert "R2" in kinds


def test_r3_raw_html_import_with_dangerously_set_fails(tmp_path: Path) -> None:
    """L44 cheat smoking gun: import ./markup.html?raw + dangerouslySetInnerHTML."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    src.mkdir(parents=True)
    ref.mkdir()
    (src / "markup.html").write_text("<div>ref</div>")
    (src / "App.tsx").write_text(
        '''import markup from "./markup.html?raw";
        export function App() {
          return <div dangerouslySetInnerHTML={{ __html: markup }} />;
        }
        '''
    )

    proc = _run(ref, impl)
    assert proc.returncode == 1
    data = json.loads((ref / "bundle-paste-check.json").read_text())
    r3 = next((v for v in data["violations"] if v["rule"] == "R3"), None)
    assert r3 is not None


def test_r3_raw_import_without_dangerously_passes(tmp_path: Path) -> None:
    """?raw import alone (without dangerouslySetInnerHTML mount) — not a cheat."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    src.mkdir(parents=True)
    ref.mkdir()
    (src / "doc.html").write_text("<p>readme</p>")
    (src / "App.tsx").write_text(
        '''import doc from "./doc.html?raw";
        export function App() {
          return <pre>{doc.length}</pre>;
        }
        '''
    )

    _run(ref, impl)
    # Should pass (no dangerouslySetInnerHTML, so not a mount)
    data = json.loads((ref / "bundle-paste-check.json").read_text())
    r3s = [v for v in data["violations"] if v["rule"] == "R3"]
    assert not r3s


def test_r4_next_server_payload_paste_fails(tmp_path: Path) -> None:
    """public/<anywhere>/<file> starting with self.__next_f.push → fail with R4."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    pub = impl / "public"
    pub.mkdir(parents=True)
    ref.mkdir()
    (pub / "next-data.js").write_text(
        'self.__next_f.push([1, "0:[\\"$\\",\\"div\\"]"])'
    )

    proc = _run(ref, impl)
    assert proc.returncode == 1
    data = json.loads((ref / "bundle-paste-check.json").read_text())
    kinds = {v["rule"] for v in data["violations"]}
    assert "R4" in kinds


def test_clean_impl_passes(tmp_path: Path) -> None:
    """Normal Vite/React layout with handwritten styles → pass."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    src = impl / "src" / "components"
    src.mkdir(parents=True)
    (impl / "public").mkdir()
    (impl / "public" / "logo.svg").write_text("<svg/>")
    (src / "Hero.tsx").write_text("export const Hero = () => <h1>hi</h1>;")
    (impl / "src" / "styles.css").write_text(".hero{color:red}")

    proc = _run(ref, impl)
    assert proc.returncode == 0
    data = json.loads((ref / "bundle-paste-check.json").read_text())
    assert data["status"] == "pass"
    assert data["violationCount"] == 0


def test_two_hex_css_files_not_enough_to_fire(tmp_path: Path) -> None:
    """Only 2 hex-hash CSS files is below the R1 floor (3) — pass."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    pub_css = impl / "public" / "css"
    pub_css.mkdir(parents=True)
    for h in ("abc12345", "def67890"):
        (pub_css / f"{h}.css").write_text(".x{}")

    _run(ref, impl)
    data = json.loads((ref / "bundle-paste-check.json").read_text())
    r1s = [v for v in data["violations"] if v["rule"] == "R1"]
    assert not r1s, "2 hex-hash files is below the >=3 floor"


def test_missing_impl_skips(tmp_path: Path) -> None:
    """No impl dir → skip, not crash."""
    ref = tmp_path / "ref"
    ref.mkdir()
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(tmp_path / "no-such-impl")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    data = json.loads((ref / "bundle-paste-check.json").read_text())
    assert data["status"] == "skip"


def test_setup_error_on_bad_ref(tmp_path: Path) -> None:
    """Non-existent ref → exit 2."""
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path / "no-ref")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2


def test_includes_in_verification_plan(tmp_path: Path) -> None:
    """bundle-paste is registered in the plan dispatch table."""
    ref = tmp_path / "ref"
    ref.mkdir()
    plan_script = ROOT / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    proc = subprocess.run(
        ["bash", str(plan_script), str(ref), "--tier=quick"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    plan = json.loads((ref / "verification-plan.json").read_text())
    ids = {c["id"] for c in plan["requiredChecks"]}
    assert "bundle-paste" in ids
    entry = next(c for c in plan["requiredChecks"] if c["id"] == "bundle-paste")
    assert entry["severity"] == "block"
    assert entry["tier"] == "quick"
