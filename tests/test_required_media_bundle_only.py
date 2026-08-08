"""D2 (loop-nvti-0): bundle JS is shared site-wide — a static string scan
promoted 5 homepage-only Lottie paths into the tech/innovation page's
requirements, then required-media-coverage failed "lottie 0/5" against media
the reference page never requests (resource-manifest census: 0 lottie hits).
Bundle-evidenced entries with no runtime request must be demoted to an
informational list; DOM/html-evidenced entries and manifest-less legacy
captures keep strict behavior."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract" / "required-media.sh"

LOTTIE_BUNDLE_JS = (
    'lottie.loadAnimation({path:"/img/lottie/naver-main-intro.json",loop:true});'
)


def _mk_ref(tmp_path: Path) -> Path:
    ref = tmp_path / "ref"
    (ref / "bundles").mkdir(parents=True)
    (ref / "bundles" / "site.min-abc.js").write_text(LOTTIE_BUNDLE_JS, encoding="utf-8")
    return ref


def _manifest(urls: list[str]) -> dict:
    return {
        "schemaVersion": 1,
        "status": "warn",
        "resources": [{"url": u, "kind": "other", "status": "downloaded"} for u in urls],
        "summary": {"candidates": len(urls)},
    }


def _run(ref: Path) -> dict:
    env = os.environ.copy()
    env.pop("BASH_COMPAT", None)
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(ref)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    data: dict = json.loads((ref / "required-media.json").read_text(encoding="utf-8"))
    return data


def test_required_media_wrapper_avoids_large_python_heredoc() -> None:
    """Homebrew Bash 5.1+ can block while piping a large heredoc to Python."""
    body = SCRIPT.read_text(encoding="utf-8")
    helper = SCRIPT.with_name("required_media.py")

    assert "<<" not in body
    assert "python3 -" not in body
    assert 'python3 "$SCRIPT_DIR/required_media.py"' in body
    assert helper.stat().st_size > 16_384


def test_required_media_default_bash_finishes_without_compat_env(
    tmp_path: Path,
) -> None:
    """The wrapper must complete under the default Bash with BASH_COMPAT unset."""
    ref = _mk_ref(tmp_path)
    out = _run(ref)

    assert out["schemaVersion"] == 1
    assert out["totals"]["lottie"] == 1


def test_bundle_only_lottie_without_runtime_request_is_demoted(tmp_path: Path) -> None:
    ref = _mk_ref(tmp_path)
    (ref / "resource-manifest.json").write_text(
        json.dumps(_manifest(["https://site.example/css/site.css"])), encoding="utf-8"
    )
    out = _run(ref)
    assert out["totals"]["lottie"] == 0, out["lottie"]
    demoted = out.get("bundleOnlyUnrequested", {}).get("lottie", [])
    assert len(demoted) == 1 and demoted[0]["path"].endswith("naver-main-intro.json")


def test_bundle_lottie_with_runtime_request_stays_required(tmp_path: Path) -> None:
    ref = _mk_ref(tmp_path)
    (ref / "resource-manifest.json").write_text(
        json.dumps(_manifest([
            "https://site.example/img/lottie/naver-main-intro.json?v=3",
        ])),
        encoding="utf-8",
    )
    out = _run(ref)
    assert out["totals"]["lottie"] == 1, out


def test_bundle_lottie_without_manifest_keeps_legacy_strictness(tmp_path: Path) -> None:
    # No resource-manifest.json (older captures): no loosening — the entry
    # stays required exactly as before.
    ref = _mk_ref(tmp_path)
    out = _run(ref)
    assert out["totals"]["lottie"] == 1, out
