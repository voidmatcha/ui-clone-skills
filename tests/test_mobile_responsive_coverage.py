from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "mobile-responsive-coverage-check.sh"


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl / "src")],
        capture_output=True, text=True, timeout=120,
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
    assert art["implAuthoredSignals"] < art["floor"]


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


def test_mirror_only_responsiveness_does_not_count(tmp_path: Path) -> None:
    """The verbatim CSS mirror (src/styles/from-ref/) must NOT be credited as impl
    responsiveness — a clone that only COPIED the ref CSS is still frozen."""
    ref, impl = _responsive_ref(tmp_path)
    # All the impl's @media live in the mirror dir; the impl authored nothing.
    mirror = impl / "src" / "styles" / "from-ref"
    mirror.mkdir(parents=True)
    (mirror / "navercorp.css").write_text(
        "\n".join(f"@media (max-width:{w}px){{.a{{width:{w}px}}}}" for w in range(300, 400)),
        encoding="utf-8")
    (impl / "src" / "App.tsx").write_text(
        "export const App=()=> <div className='a'>x</div>;\n", encoding="utf-8")
    proc = _run(ref, impl)
    art = json.loads((ref / "mobile-responsive-coverage.json").read_text())
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    assert art["mirrorSignalsExcluded"] >= 50, "mirror @media must be counted as excluded"
    assert art["implAuthoredSignals"] < art["floor"]


def test_impl_authored_matchmedia_counts(tmp_path: Path) -> None:
    """A JS matchMedia + resize listener is impl-authored responsiveness."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (ref / "css").mkdir(parents=True)
    # Responsive ref via breakpoints, low CSS density → floor is the min (3).
    (ref / "detected-breakpoints.json").write_text(
        json.dumps({"breakpoints": ["480px", "768px", "1024px"]}), encoding="utf-8")
    (ref / "css" / "main.css").write_text("@media (max-width:768px){.a{width:50vw}}\n", encoding="utf-8")
    (impl / "src" / "responsive.ts").write_text(
        "const mq = window.matchMedia('(max-width:768px)');\n"
        "mq.addEventListener('change', rebuild);\n"
        "window.addEventListener('resize', onResize);\n"
        "new ResizeObserver(cb).observe(document.body);\n",
        encoding="utf-8")
    proc = _run(ref, impl)
    art = json.loads((ref / "mobile-responsive-coverage.json").read_text())
    assert proc.returncode == 0, art
    assert art["status"] == "pass"
    assert art["implJsSignals"] >= 3


def test_inline_px_on_responsive_class_flagged(tmp_path: Path) -> None:
    """Inline px baked into JSX on a class the ref declares responsively is the
    'inline px wins the cascade' defect — fail even if the mirror ships @media."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (ref / "css").mkdir(parents=True)
    (ref / "detected-breakpoints.json").write_text(
        json.dumps({"breakpoints": ["768px", "1024px", "1280px"]}), encoding="utf-8")
    (ref / "css" / "main.css").write_text(
        "@media (max-width:768px){.hero{width:100vw;height:40vw}}\n", encoding="utf-8")
    (impl / "src" / "Hero.tsx").write_text(
        "export const Hero=()=> <section className=\"hero\" style={{width:'320px',height:'160px'}}>x</section>;\n",
        encoding="utf-8")
    proc = _run(ref, impl)
    art = json.loads((ref / "mobile-responsive-coverage.json").read_text())
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    assert art["inlinePxOnResponsiveClass"] >= 1
    assert art["implCssMediaQueries"] == 0


def test_dense_static_signals_are_diagnostic_not_blocking(tmp_path: Path) -> None:
    ref, impl = _responsive_ref(tmp_path)
    (impl / "src" / "dummy.css").write_text(
        "\n".join(
            f"@media (max-width:{width}px){{.unused-{width}{{width:{width}px}}}}"
            for width in range(300, 400)
        ),
        encoding="utf-8",
    )
    static_proc = _run(ref, impl)
    assert static_proc.returncode == 0

    plan_proc = subprocess.run(
        [
            "bash",
            str(ROOT / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"),
            str(ref),
            "--tier=standard",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert plan_proc.returncode == 0, plan_proc.stdout + plan_proc.stderr
    plan = json.loads((ref / "verification-plan.json").read_text())
    rows = {row["id"]: row for row in plan["requiredChecks"]}
    assert rows["mobile-responsive-coverage"]["severity"] == "warn"
    assert rows["resize-behavior"]["severity"] == "block"
    assert rows["desktop-band-fluidity"]["severity"] == "block"
