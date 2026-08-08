from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "body-opacity-unlock-check.sh"


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        capture_output=True, text=True, timeout=120,
    )


def _write_report(ref: Path, requires: bool, destinations: list[str] | None = None) -> None:
    ref.mkdir(parents=True, exist_ok=True)
    (ref / "ref-css-sanitize-report.json").write_text(
        json.dumps({
            "requiresRuntimeUnlock": requires,
            "runtimeUnlockHints": (
                [{"selector": "body", "declaration": "opacity:0"}] if requires else []
            ),
            "files": [{"destination": d} for d in (destinations or [])],
        }),
        encoding="utf-8",
    )


def _artifact(ref: Path) -> dict:
    data: dict = json.loads((ref / "body-opacity-unlock.json").read_text())
    return data


def test_skips_without_sanitize_report(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    impl.mkdir()
    proc = _run(ref, impl)
    assert proc.returncode == 0
    assert _artifact(ref)["status"] == "skip"


def test_fails_without_sanitize_report_when_impl_ref_css_locks_body(tmp_path: Path) -> None:
    """Manual CSS-copy loops can omit ref-css-sanitize-report.json.

    The gate should still catch preserved ref CSS that locks the root invisible.
    """
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src" / "ref-css").mkdir(parents=True)
    (impl / "src" / "ref-css" / "site.css").write_text("body{opacity:0}", encoding="utf-8")

    proc = _run(ref, impl)

    assert proc.returncode == 1
    art = _artifact(ref)
    assert art["status"] == "fail"
    assert art["requiresRuntimeUnlock"] is True


def test_passes_without_sanitize_report_when_impl_ref_css_lock_is_released(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (impl / "src" / "ref-css").mkdir(parents=True)
    (impl / "src" / "ref-css" / "site.css").write_text("body{opacity:0}", encoding="utf-8")
    (impl / "src" / "visibility-fix.css").write_text(
        "html body{opacity:1 !important;visibility:visible !important}",
        encoding="utf-8",
    )

    proc = _run(ref, impl)

    assert proc.returncode == 0
    art = _artifact(ref)
    assert art["status"] == "pass"
    assert art["evidence"][0]["file"] == "src/visibility-fix.css"


def test_passes_when_no_lock_required(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    impl.mkdir()
    _write_report(ref, requires=False)
    proc = _run(ref, impl)
    assert proc.returncode == 0
    assert _artifact(ref)["status"] == "pass"


def test_fails_when_lock_never_released(tmp_path: Path) -> None:
    """The loop A-06 unit-1 failure mode: ref CSS locks body{opacity:0}, the
    impl imports it verbatim, and nothing releases the lock — the page renders
    invisible. Must FAIL deterministically."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    _write_report(ref, requires=True, destinations=["src/ref-css/site.css"])
    (impl / "src" / "ref-css").mkdir(parents=True)
    (impl / "src" / "ref-css" / "site.css").write_text("body{opacity:0}", encoding="utf-8")
    proc = _run(ref, impl)
    assert proc.returncode == 1
    assert _artifact(ref)["status"] == "fail"


def test_js_style_unlock_counts_as_evidence(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    _write_report(ref, requires=True, destinations=["src/ref-css/site.css"])
    (impl / "src" / "ref-css").mkdir(parents=True)
    (impl / "src" / "ref-css" / "site.css").write_text("body{opacity:0}", encoding="utf-8")
    (impl / "src" / "App.tsx").write_text(
        'useEffect(() => { document.body.style.opacity = "1"; }, []);',
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    assert proc.returncode == 0
    art = _artifact(ref)
    assert art["status"] == "pass"
    assert art["evidence"][0]["kind"] == "js-body-style-unlock"


def test_css_override_unlock_counts_as_evidence(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    _write_report(ref, requires=True)
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "local-overrides.css").write_text(
        "body{opacity:1 !important}", encoding="utf-8",
    )
    proc = _run(ref, impl)
    assert proc.returncode == 0
    assert _artifact(ref)["status"] == "pass"


def test_sanitized_ref_css_is_not_unlock_evidence(tmp_path: Path) -> None:
    """The copied ref CSS itself may contain state-scoped body opacity:1 rules;
    the lock source cannot double as the unlock evidence."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    _write_report(ref, requires=True, destinations=["src/ref-css/site.css"])
    (impl / "src" / "ref-css").mkdir(parents=True)
    (impl / "src" / "ref-css" / "site.css").write_text(
        "body{opacity:0}body.is-show{opacity:1}", encoding="utf-8",
    )
    proc = _run(ref, impl)
    assert proc.returncode == 1
    assert _artifact(ref)["status"] == "fail"
