from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "library-usage-check.sh"


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


def _art(ref: Path) -> dict:
    data: dict = json.loads((ref / "library-usage.json").read_text())
    return data


def test_declared_but_unimported_library_fails(tmp_path: Path) -> None:
    """The rAF-shim loophole: framer-motion is detected in the ref and installed
    in package.json, but impl source never imports it — must FAIL."""
    ref, impl = _scaffold(tmp_path)
    (ref / "external-sdks.json").write_text(
        json.dumps({"detected": {"useScroll": {"matches": 3}, "scrollYProgress": {"matches": 1}}}),
        encoding="utf-8",
    )
    (impl / "package.json").write_text(
        json.dumps({"dependencies": {"framer-motion": "^11", "react": "^18"}}),
        encoding="utf-8",
    )
    (impl / "src" / "Page.tsx").write_text(
        "export default function P(){const raf=()=>requestAnimationFrame(raf);return null}\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = _art(ref)
    assert proc.returncode == 1, f"unimported framer-motion must fail: {art}"
    assert art["status"] == "fail"
    assert "framer-motion" in art["detectedLibs"]
    unused = {u["name"]: u for u in art["unusedLibs"]}
    assert "framer-motion" in unused
    assert unused["framer-motion"]["installed"] is True


def test_imported_library_passes(tmp_path: Path) -> None:
    """Detected framer-motion is actually imported in impl source — PASS."""
    ref, impl = _scaffold(tmp_path)
    (ref / "external-sdks.json").write_text(
        json.dumps({"detected": {"useScroll": {"matches": 3}}}),
        encoding="utf-8",
    )
    (impl / "package.json").write_text(
        json.dumps({"dependencies": {"framer-motion": "^11"}}),
        encoding="utf-8",
    )
    (impl / "src" / "Page.tsx").write_text(
        'import { useScroll } from "framer-motion";\n'
        "export default function P(){ useScroll(); return null }\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = _art(ref)
    assert proc.returncode == 0, f"imported framer-motion must pass: {art}"
    assert art["status"] == "pass"
    assert art["importedLibs"] == ["framer-motion"]
    assert not art["unusedLibs"]


def test_subpath_and_dynamic_imports_count_as_used(tmp_path: Path) -> None:
    """gsap via a subpath import and lenis via a dynamic import both count as
    real usage — the scan must not require a bare top-level specifier."""
    ref, impl = _scaffold(tmp_path)
    (ref / "bundle-map.json").write_text(
        json.dumps({"libraries": {"gsap": True}, "notes": "lenis smooth scroll on <html>"}),
        encoding="utf-8",
    )
    (impl / "package.json").write_text(
        json.dumps({"dependencies": {"gsap": "^3", "lenis": "^1"}}),
        encoding="utf-8",
    )
    (impl / "src" / "motion.ts").write_text(
        'import gsap from "gsap/ScrollTrigger";\n'
        'export const load = () => import("lenis");\n'
        "gsap.to({}, {});\n",
        encoding="utf-8",
    )
    proc = _run(ref, impl)
    art = _art(ref)
    assert proc.returncode == 0, f"subpath + dynamic imports must pass: {art}"
    assert art["status"] == "pass"
    assert set(art["detectedLibs"]) == {"gsap", "lenis"}
    assert not art["unusedLibs"]


def test_no_animation_library_detected_passes(tmp_path: Path) -> None:
    """Ref evidence exists but declares only a non-animation SDK — PASS (not
    skip), because there is nothing motion-related to import."""
    ref, impl = _scaffold(tmp_path)
    (ref / "external-sdks.json").write_text(
        json.dumps({"detected": {"googleAnalytics": {"matches": 2}, "segment": {"matches": 1}}}),
        encoding="utf-8",
    )
    (impl / "package.json").write_text(json.dumps({"dependencies": {"react": "^18"}}))
    proc = _run(ref, impl)
    art = _art(ref)
    assert proc.returncode == 0, f"non-animation SDK must pass: {art}"
    assert art["status"] == "pass"
    assert art["detectedLibs"] == []


def test_no_ref_evidence_skips(tmp_path: Path) -> None:
    """Neither bundle-map.json nor external-sdks.json present — SKIP with exit 0
    (nothing to verify against)."""
    ref, impl = _scaffold(tmp_path)
    (impl / "package.json").write_text(json.dumps({"dependencies": {}}))
    proc = _run(ref, impl)
    art = _art(ref)
    assert proc.returncode == 0
    assert art["status"] == "skip"


def test_unimported_and_not_installed_reported(tmp_path: Path) -> None:
    """A detected lib that is neither imported nor installed still fails, and the
    remediation records installed=false so the operator knows to add both."""
    ref, impl = _scaffold(tmp_path)
    (ref / "bundle-map.json").write_text(
        json.dumps({"libraries": {"gsap": True}}), encoding="utf-8",
    )
    (impl / "package.json").write_text(json.dumps({"dependencies": {"react": "^18"}}))
    (impl / "src" / "Page.tsx").write_text("export default function P(){return null}\n")
    proc = _run(ref, impl)
    art = _art(ref)
    assert proc.returncode == 1
    assert art["status"] == "fail"
    unused = {u["name"]: u for u in art["unusedLibs"]}
    assert unused["gsap"]["installed"] is False
