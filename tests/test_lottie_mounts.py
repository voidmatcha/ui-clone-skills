"""Fixture tests for the deterministic Lottie mount emitter + slot-identity gate.

emit-lottie-mounts.sh turns transition-spec.json lottie entries into a
deterministic impl/src/generated/lottie-mounts.ts (exact container/path/loop/
autoplay), and lottie-slot-identity-check.sh fails when an impl's mount call
sites do not match the spec (missing slot, wrong container, inverted flags).
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMITTER = ROOT / "scripts" / "extract" / "emit-lottie-mounts.sh"
GATE = ROOT / "skills" / "visual-debug" / "scripts" / "lottie-slot-identity-check.sh"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), *args], capture_output=True, text=True, timeout=60
    )


def _spec(ref: Path, transitions: list[dict]) -> None:
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": transitions}), encoding="utf-8"
    )


def _lottie(eid: str, target: str, path: str, *, loop: bool, autoplay: bool,
            trigger: str = "load", mobile: str | None = None,
            anim_trigger: str | None = None) -> dict:
    anim: dict = {"type": "lottie", "library": "bodymovin", "renderer": "svg",
                  "path": path, "loop": loop, "autoplay": autoplay}
    if mobile:
        anim["mobilePath"] = mobile
    if anim_trigger:
        anim["trigger"] = anim_trigger
    return {"id": eid, "trigger": trigger, "target": target, "animation": anim}


INTRO = _lottie("intro-lottie", "#introLottie", "/img/lottie/intro.json",
                loop=True, autoplay=False, anim_trigger="play() on intro event")
OUTRO = _lottie("outro-lottie", "#outroLottie", "/img/lottie/outro-pc.json",
                loop=False, autoplay=False, trigger="scroll",
                mobile="/img/lottie/outro-mo.json",
                anim_trigger="goToAndStop on scroll state")


def test_emitter_emits_exact_paths_and_flags(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    impl.mkdir()
    _spec(ref, [INTRO, OUTRO])

    proc = _run(EMITTER, str(ref), str(impl))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    module = (impl / "src" / "generated" / "lottie-mounts.ts").read_text()
    # exact intro binding
    assert "document.querySelector<HTMLElement>('#introLottie')" in module
    assert "mounts['intro-lottie'] = lottie.loadAnimation({" in module
    assert "loop: true," in module and "autoplay: false," in module
    assert "path: '/img/lottie/intro.json'," in module
    # outro: scroll-scrub with mobile/pc matchMedia variant + driver skeleton
    assert "window.matchMedia(MOBILE_QUERY).matches" in module
    assert "'/img/lottie/outro-mo.json'" in module
    assert "'/img/lottie/outro-pc.json'" in module
    assert "goToAndStop" in module
    assert "export function driveOutroLottieOnScroll" in module
    # intro is autoplay:false + play-on-event -> exported play handle
    assert "export function playIntroLottie" in module

    report = json.loads((ref / "lottie-mounts-emitted.json").read_text())
    assert len(report["mounted"]) == 2
    kinds = {m["id"]: m["triggerKind"] for m in report["mounted"]}
    assert kinds["intro-lottie"] == "manual-play"
    assert kinds["outro-lottie"] == "scroll-scrub"
    assert report["skipped"] == []


def test_emitter_no_lottie_is_noop(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    impl.mkdir()
    _spec(ref, [{"id": "hov", "trigger": "hover", "type": "css-hover", "target": ".btn"}])

    proc = _run(EMITTER, str(ref), str(impl))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (impl / "src" / "generated" / "lottie-mounts.ts").exists()
    report = json.loads((ref / "lottie-mounts-emitted.json").read_text())
    assert report["mounted"] == [] and report["skipped"] == []


def test_gate_passes_on_emitted_module(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    impl.mkdir()
    _spec(ref, [INTRO, OUTRO])
    assert _run(EMITTER, str(ref), str(impl)).returncode == 0

    proc = _run(GATE, str(ref), str(impl))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads((ref / "lottie-slot-identity.json").read_text())
    assert data["status"] == "pass"
    assert data["total"] == 2 and data["matched"] == 2
    assert data["problems"] == []


def _hand_written_intro(impl: Path, *, autoplay: bool) -> None:
    """A hand-authored intro mount (bypassing the emitter) with the given flag."""
    lib = impl / "src" / "lib"
    lib.mkdir(parents=True)
    (lib / "lottie.ts").write_text(
        "import lottie from 'lottie-web';\n"
        "const el = document.querySelector<HTMLElement>('#introLottie');\n"
        "if (el) lottie.loadAnimation({\n"
        "  container: el, renderer: 'svg', loop: true, "
        f"autoplay: {'true' if autoplay else 'false'}, "
        "path: '/img/lottie/intro.json',\n"
        "});\n",
        encoding="utf-8",
    )


def test_gate_fails_on_inverted_flags(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    impl.mkdir()
    _spec(ref, [INTRO])  # spec: intro loop:true autoplay:false
    _hand_written_intro(impl, autoplay=True)  # impl: autoplay:true (inverted)

    proc = _run(GATE, str(ref), str(impl))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    data = json.loads((ref / "lottie-slot-identity.json").read_text())
    assert data["status"] == "fail"
    problem = data["problems"][0]
    assert problem["id"] == "intro-lottie"
    assert problem["reason"] == "flag-mismatch"
    assert "autoplay=false" in problem["detail"] and "autoplay=true" in problem["detail"]


def test_gate_fails_on_missing_slot(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    impl.mkdir()
    _spec(ref, [INTRO, OUTRO])  # two slots
    _hand_written_intro(impl, autoplay=False)  # only intro mounted, correctly

    proc = _run(GATE, str(ref), str(impl))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    data = json.loads((ref / "lottie-slot-identity.json").read_text())
    by_id = {p["id"]: p for p in data["problems"]}
    assert "outro-lottie" in by_id and by_id["outro-lottie"]["reason"] == "no-mount"
    assert "intro-lottie" not in by_id  # intro is correctly mounted


def test_gate_ignores_mirror_css_container(tmp_path: Path) -> None:
    """A container id present only in the mirrored ref CSS is not a mount."""
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src" / "ref-css").mkdir(parents=True)
    (impl / "src" / "ref-css" / "site.css").write_text(
        "#introLottie{position:fixed}\n", encoding="utf-8"
    )
    _spec(ref, [INTRO])

    proc = _run(GATE, str(ref), str(impl))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    data = json.loads((ref / "lottie-slot-identity.json").read_text())
    assert data["problems"][0]["reason"] == "no-mount"


def test_gate_skips_when_no_lottie(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    impl.mkdir()
    _spec(ref, [{"id": "hov", "trigger": "hover", "type": "css-hover", "target": ".btn"}])

    proc = _run(GATE, str(ref), str(impl))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads((ref / "lottie-slot-identity.json").read_text())
    assert data["status"] == "skip" and data["total"] == 0
