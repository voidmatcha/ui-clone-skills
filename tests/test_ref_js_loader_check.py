from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "ref-js-loader-check.sh"


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
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


def test_anchor_href_to_ref_host_is_not_ref_js_violation(tmp_path: Path) -> None:
    """A copied navigation destination does not execute reference code."""
    ref, impl = _scaffold(tmp_path)
    (ref / "head.json").write_text(
        json.dumps({"url": "https://navercorp.com/main"}), encoding="utf-8",
    )
    (impl / "src" / "App.tsx").write_text(
        'export const App = () => <a href="https://navercorp.com/company">Company</a>;\n',
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = json.loads((ref / "ref-js-loader.json").read_text())
    assert proc.returncode == 0, f"navigation link must not be flagged: {art}"
    assert art["status"] == "pass"
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


def test_sanitized_ref_css_url_is_not_a_ref_js_violation(tmp_path: Path) -> None:
    """Forensically copied ref CSS can contain ref-host url(...) assets.

    The ref-JS gate should ignore exact files recorded by
    sanitize-ref-css.sh instead of treating generated evidence as an
    implementation-authored bundle hotlink.
    """
    ref, impl = _scaffold(tmp_path)
    (impl / "src" / "ref-css").mkdir(parents=True)
    css = 'body{background:url("https://www.example-ref.test/assets/hero") center/cover no-repeat}\n'
    css_path = impl / "src" / "ref-css" / "site.css"
    css_path.write_text(css, encoding="utf-8")
    digest = hashlib.sha256(css.encode("utf-8")).hexdigest()
    (ref / "head.json").write_text(
        json.dumps({"url": "https://www.example-ref.test/"}), encoding="utf-8",
    )
    (ref / "ref-css-sanitize-report.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "copyTo": "src/ref-css",
                "files": [
                    {
                        "source": "css/site.css",
                        "destination": "src/ref-css/site.css",
                        "destinationSha256": digest,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    proc = _run(ref, impl)

    art = json.loads((ref / "ref-js-loader.json").read_text())
    assert proc.returncode == 0, f"sanitized ref CSS must not fail: {art}"
    assert art["status"] == "pass"
    assert art["sanitizedRefCssSkipped"] == ["src/ref-css/site.css"]
    assert not art.get("violations")
