from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "remote-asset-ref-check.sh"


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl / "src")],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _scaffold(tmp_path: Path) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    return ref, impl


def test_external_link_url_const_passes(tmp_path: Path) -> None:
    """A non-asset URL const used only as an outbound link href is NOT an
    asset hotlink and must pass — the old host-const rule false-failed it."""
    ref, impl = _scaffold(tmp_path)
    (impl / "src" / "Footer.tsx").write_text(
        "const ndStudioUrl = 'https://nd-studio.example';\n"
        "export function Footer(){ return <a href={ndStudioUrl}>ND Studio</a>; }\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    artifact = json.loads((ref / "remote-asset-ref.json").read_text())
    assert proc.returncode == 0, (
        f"external-link URL const must pass: {artifact.get('violations')}\n{proc.stdout}"
    )
    assert artifact["status"] == "pass"


def test_hotlinked_template_asset_const_still_fails(tmp_path: Path) -> None:
    """The real cheat — host const fed into an asset src template literal —
    must still fail."""
    ref, impl = _scaffold(tmp_path)
    (impl / "src" / "Hero.tsx").write_text(
        "const A = 'https://cdn.example.com';\n"
        "export function Hero(){ return <img src={`${A}/img/main.webp`} />; }\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    artifact = json.loads((ref / "remote-asset-ref.json").read_text())
    assert proc.returncode == 1, f"hotlinked template asset must fail: {proc.stdout}"
    assert artifact["status"] == "fail"


def test_direct_remote_img_src_still_fails(tmp_path: Path) -> None:
    """A literal remote asset URL in src= must still fail (unchanged path)."""
    ref, impl = _scaffold(tmp_path)
    (impl / "src" / "Card.tsx").write_text(
        'export function Card(){ return <img src="https://cdn.example.com/x.webp" />; }\n',
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    artifact = json.loads((ref / "remote-asset-ref.json").read_text())
    assert proc.returncode == 1, f"direct remote img src must fail: {proc.stdout}"
    assert artifact["status"] == "fail"
