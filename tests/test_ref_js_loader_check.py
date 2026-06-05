from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "ref-js-loader-check.sh"


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=30,
    )


def _scaffold(tmp_path: Path) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    ref.mkdir()
    return ref, impl


def test_mailto_link_is_not_a_ref_js_violation(tmp_path: Path) -> None:
    """A mailto: contact link copied from the ref is not loading ref JS — the
    gate collected `mailto:...@host` as a bare ref host and flagged the impl's
    legitimate email link."""
    ref, impl = _scaffold(tmp_path)
    (ref / "head.json").write_text(
        json.dumps({"href": "mailto:dietaryguidelines@usda.gov"}), encoding="utf-8",
    )
    (impl / "src" / "Contact.tsx").write_text(
        'export const C = () => <a href="mailto:dietaryguidelines@usda.gov">email</a>;\n',
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "ref-js-loader.json").read_text())
    assert proc.returncode == 0, f"mailto must not be flagged: {art.get('violations')}"
    assert art["status"] != "fail"
    assert not art.get("violations")


def test_real_ref_script_hotlink_still_fails(tmp_path: Path) -> None:
    """A genuine <script src> pointing at a ref host must still fail."""
    ref, impl = _scaffold(tmp_path)
    (ref / "head.json").write_text(
        json.dumps({"host": "cdn.realfood.example"}), encoding="utf-8",
    )
    (impl / "src" / "App.tsx").write_text(
        'export const A = () => <script src="https://cdn.realfood.example/app.js" />;\n',
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "ref-js-loader.json").read_text())
    assert proc.returncode == 1, f"real ref-JS hotlink must fail: {art}"
    assert art["status"] == "fail"
