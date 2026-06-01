from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "visual-debug" / "scripts"


def _write_impl(root: Path, source: str, css: str = "", package: dict[str, object] | None = None) -> Path:
    impl = root / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps(package or {"dependencies": {"react": "19"}}), encoding="utf-8")
    (impl / "src" / "App.jsx").write_text(source, encoding="utf-8")
    if css:
        (impl / "src" / "style.css").write_text(css, encoding="utf-8")
    return impl


def _run(script_name: str, *args: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS / script_name), *(str(arg) for arg in args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_forced_state_class_check_blocks_reveal_all_and_transition_none(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "panel-reveal", "trigger": "scroll"}]}),
        encoding="utf-8",
    )
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "app.js").write_text("classList.add('is-active'); classList.add('is-visible');", encoding="utf-8")
    impl = _write_impl(
        tmp_path,
        'export function App(){return <section className="card is-active is-visible is-show">Panel</section>}',
        ".card, .card.is-active { transition: none !important; opacity: 1 !important; transform: none !important; }",
    )

    proc = _run("forced-state-class-check.sh", ref, impl)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "forced-state-class.json").read_text(encoding="utf-8"))
    kinds = {issue["kind"] for issue in artifact["issues"]}
    assert "hardcoded-state-class" in kinds
    assert "forced-final-style" in kinds


def test_forced_state_class_check_allows_single_default_active_tab(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "tabs", "trigger": "scroll"}]}),
        encoding="utf-8",
    )
    impl = _write_impl(
        tmp_path,
        'export function App(){return <button className="tab is-active">Overview</button>}',
    )

    proc = _run("forced-state-class-check.sh", ref, impl)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "forced-state-class.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"


def test_forced_state_class_check_allows_unrelated_important_final_styles(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "panel-reveal", "trigger": "scroll"}]}),
        encoding="utf-8",
    )
    impl = _write_impl(
        tmp_path,
        "export function App(){return <section className=\"card\">Panel</section>}",
        ".utility-reset { opacity: 1 !important; transform: none !important; }",
    )

    proc = _run("forced-state-class-check.sh", ref, impl)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "forced-state-class.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"


def test_forced_state_class_check_blocks_blanket_final_state_without_important(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "again", "trigger": "scroll-scrub"}]}),
        encoding="utf-8",
    )
    bundles = ref / "bundles"
    bundles.mkdir()
    (bundles / "app.js").write_text(
        "ScrollTrigger.create({trigger:'.again', scrub:true, onUpdate(){el.classList.toggle('is-show')}});",
        encoding="utf-8",
    )
    impl = _write_impl(
        tmp_path,
        'export function App(){return <section className="again is-active is-show">Again</section>}',
        ".again.is-active, .again.is-show { opacity: 1; transform: none; transition: none; }\n",
    )

    proc = _run("forced-state-class-check.sh", ref, impl)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "forced-state-class.json").read_text(encoding="utf-8"))
    kinds = {issue["kind"] for issue in artifact["issues"]}
    assert "forced-final-style" in kinds
    assert "blanket-state-final-style" in kinds


def test_lottie_scroll_scrub_check_blocks_autoplay_loop_only(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    bundles = ref / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "app.js").write_text(
        "const anim = lottie.loadAnimation({}); ScrollTrigger.create({scrub:true,onUpdate:()=>anim.goToAndStop(10,true)});",
        encoding="utf-8",
    )
    impl = _write_impl(
        tmp_path,
        "import Lottie from 'lottie-react';\nexport function App(){return <Lottie animationData={timelineAnimation} autoplay loop />}",
        package={"dependencies": {"react": "19", "lottie-react": "2.4.0"}},
    )

    proc = _run("lottie-scroll-scrub-check.sh", ref, impl)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "lottie-scroll-scrub.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "fail"
    assert "goToAndStop" in artifact["requiredSignals"]


def test_lottie_scroll_scrub_check_passes_with_frame_control(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    bundles = ref / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "app.js").write_text("lottie.loadAnimation({}); scrollYProgress.on('change', onChange);", encoding="utf-8")
    impl = _write_impl(
        tmp_path,
        "import lottie from 'lottie-web';\nconst anim = lottie.loadAnimation({path:'/timeline.json'});\nexport function seek(p){ anim.goToAndStop(p * anim.totalFrames, true); }",
        package={"dependencies": {"react": "19", "lottie-web": "5.12.2"}},
    )

    proc = _run("lottie-scroll-scrub-check.sh", ref, impl)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "lottie-scroll-scrub.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"


def test_lottie_scroll_scrub_check_blocks_missing_expected_container_ids(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    bundles = ref / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "app.js").write_text(
        """
        const againLottie1 = lottie.loadAnimation({
          container: document.getElementById('againLottie1'),
          path: '/again-1.json'
        });
        const againLottie2 = lottie.loadAnimation({
          container: document.querySelector('#againLottie2'),
          path: '/again-2.json'
        });
        ScrollTrigger.create({scrub:true,onUpdate:self=>{
          againLottie1.goToAndStop(self.progress * againLottie1.totalFrames, true);
          againLottie2.goToAndStop(self.progress * againLottie2.totalFrames, true);
        }});
        """,
        encoding="utf-8",
    )
    impl = _write_impl(
        tmp_path,
        """
        import lottie from 'lottie-web';
        const anim = lottie.loadAnimation({container: document.getElementById('againLottie1'), path:'/again-1.json'});
        export function seek(p){ anim.goToAndStop(p * anim.totalFrames, true); }
        """,
        package={"dependencies": {"react": "19", "lottie-web": "5.12.2"}},
    )

    proc = _run("lottie-scroll-scrub-check.sh", ref, impl)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "lottie-scroll-scrub.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "fail"
    assert artifact["expectedContainers"] == ["againLottie1", "againLottie2"]
    assert any(issue["kind"] == "missing-expected-lottie-container" for issue in artifact["issues"])


def test_lottie_scroll_scrub_check_ignores_lottie_null_cms_fields(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "reference.html").write_text(
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"asset":{"lottie":null,"type":"video"},"copy":"scroll down"}'
        "</script>",
        encoding="utf-8",
    )
    impl = _write_impl(tmp_path, "export function App(){return <footer>Brand</footer>}")

    proc = _run("lottie-scroll-scrub-check.sh", ref, impl)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "lottie-scroll-scrub.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "skip"
    assert artifact["requiresScrollScrubbedLottie"] is False


def test_lottie_scroll_scrub_check_ignores_unrelated_lottie_and_scroll_files(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    bundles = ref / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "lottie-data.js").write_text("const asset = { lottie: { path: '/intro.json' } };", encoding="utf-8")
    (bundles / "scroll.js").write_text("window.addEventListener('scroll', onScroll);", encoding="utf-8")
    impl = _write_impl(tmp_path, "export function App(){return <main />}")

    proc = _run("lottie-scroll-scrub-check.sh", ref, impl)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "lottie-scroll-scrub.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "skip"
    assert artifact["hasLottieSignal"] is True
    assert artifact["coLocatedScrollLottieSignal"] is False


def test_lottie_scroll_scrub_check_ignores_copy_text_scroll_word(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    bundles = ref / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "app.js").write_text(
        "const asset = { lottie: { path: '/intro.json' }, copy: 'scroll down' };",
        encoding="utf-8",
    )
    impl = _write_impl(tmp_path, "export function App(){return <main />}")

    proc = _run("lottie-scroll-scrub-check.sh", ref, impl)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "lottie-scroll-scrub.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "skip"
    assert artifact["hasLottieSignal"] is True
    assert artifact["coLocatedScrollLottieSignal"] is False


def test_swiper_runtime_check_blocks_class_copy_without_runtime(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    bundles = ref / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "app.js").write_text("new Swiper('.card-rail', { spaceBetween: 24, slidesPerView: 'auto' });", encoding="utf-8")
    impl = _write_impl(
        tmp_path,
        'export function Cards(){return <div className="swiper-wrapper"><article className="swiper-slide">Card</article></div>}',
    )

    proc = _run("swiper-runtime-check.sh", ref, impl)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "swiper-runtime.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "fail"
    assert artifact["classOnly"] is True


def test_swiper_runtime_check_blocks_css_import_without_runtime(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    bundles = ref / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "app.js").write_text("new Swiper('.card-rail', { spaceBetween: 24 });", encoding="utf-8")
    impl = _write_impl(
        tmp_path,
        "import 'swiper/css';\nexport function Cards(){return <div className=\"swiper-wrapper\"><article className=\"swiper-slide\">Card</article></div>}",
        package={"dependencies": {"react": "19", "swiper": "11.0.0"}},
    )

    proc = _run("swiper-runtime-check.sh", ref, impl)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    artifact = json.loads((ref / "swiper-runtime.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "fail"
    assert artifact["hasRuntime"] is False


def test_swiper_runtime_check_allows_extracted_manual_sizing(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    bundles = ref / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "app.js").write_text("new Swiper('.card-rail', { spaceBetween: 24 });", encoding="utf-8")
    impl = _write_impl(
        tmp_path,
        'export function Cards(){return <div className="swiper-wrapper" style={{transform:"translate3d(-24px,0,0)"}}><article className="swiper-slide" style={{marginRight:24}}>Card</article></div>}',
    )

    proc = _run("swiper-runtime-check.sh", ref, impl)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    artifact = json.loads((ref / "swiper-runtime.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"
    assert artifact["hasSizingLogic"] is True


def test_dynamic_runtime_quality_checks_are_registered_and_documented(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    bundles = ref / "bundles"
    bundles.mkdir(parents=True)
    (bundles / "app.js").write_text(
        "lottie.loadAnimation({}); ScrollTrigger.create({scrub:true}); new Swiper('.cards', {}); window.scrollTo({top:0}); setTimeout(()=>{}, 1);",
        encoding="utf-8",
    )
    (ref / "transition-spec.json").write_text(
        json.dumps({"transitions": [{"id": "cards", "trigger": "scroll"}]}),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["UI_CLONE_VERIFY_TIER"] = "standard"
    proc = subprocess.run(
        ["bash", str(SCRIPTS / "verification-plan.sh"), str(ref)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    plan = json.loads((ref / "verification-plan.json").read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in plan["requiredChecks"]}
    for check_id, artifact in {
        "forced-state-class": "forced-state-class.json",
        "lottie-scroll-scrub": "lottie-scroll-scrub.json",
        "swiper-runtime": "swiper-runtime.json",
    }.items():
        assert rows[check_id]["produces"] == artifact
        assert rows[check_id]["severity"] == "block"

    dispatcher = (ROOT / "scripts" / "verify" / "run-required-checks.sh").read_text(encoding="utf-8")
    for script_name in ("forced-state-class-check.sh", "lottie-scroll-scrub-check.sh", "swiper-runtime-check.sh"):
        assert f'"{script_name}"' in dispatcher
    assert '"lottie-scroll-scrub-check.sh": "{ref_dir} {impl_root} {ref_url} {impl_url} {session}-lottie"' in dispatcher

    reverse = (ROOT / "skills" / "ui-reverse-engineering" / "SKILL.md").read_text(encoding="utf-8")
    assert "scroll-scrubbed Lottie frame control" in reverse
    assert "copied Swiper classes without Swiper runtime" in reverse
    assert "force `is-active` / `is-visible` / `is-show`" in reverse

    lottie_script = (SCRIPTS / "lottie-scroll-scrub-check.sh").read_text(encoding="utf-8")
    assert "scrollRatios" in lottie_script
    assert "0.25" in lottie_script
    assert "0.5" in lottie_script
    assert "0.75" in lottie_script


def _make_agent_browser_stub(bin_dir: Path, probe_json: str) -> dict[str, str]:
    """Write a fake `agent-browser` that emits `probe_json` for the eval
    subcommand and no-ops everything else. Returns an env dict with the stub
    on PATH so runtime-frame-proof-check.sh runs end-to-end offline.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "agent-browser"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "for a in \"$@\"; do\n"
        "  if [ \"$a\" = \"eval\" ]; then\n"
        f"    cat <<'JSON'\n{probe_json}\nJSON\n"
        "    exit 0\n"
        "  fi\n"
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return env


_ZERO_SURFACE_PROBE = json.dumps({
    "canvasTotal": 0, "canvasAdvanced": 0, "webglAdvanced": 0,
    "lottieInstances": 0, "lottieAdvanced": 0,
})


def test_runtime_frame_proof_fails_blank_webgl_hero_when_ref_has_canvas(tmp_path: Path) -> None:
    """FIX 2a (rank235): when the ref genuinely renders WebGL/canvas
    (canvas-webgl-detection.json canvasCount>0 / primaryRenderType=webgl) but
    the impl renders 0 canvases, the gate must FAIL — a blank WebGL hero is a
    real escape, not a detection false-positive. Previously this fell through
    to an informational PASS (fail-open).
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "canvas-webgl-detection.json").write_text(json.dumps({
        "schemaVersion": 1, "primaryRenderType": "webgl",
        "hasCanvas": True, "hasWebGL": True, "canvasCount": 1,
    }), encoding="utf-8")
    env = _make_agent_browser_stub(tmp_path / "bin", _ZERO_SURFACE_PROBE)
    proc = subprocess.run(
        ["bash", str(SCRIPTS / "runtime-frame-proof-check.sh"),
         "sess", "http://localhost:9", str(ref)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "runtime-frame-proof.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "fail"
    assert any("blank" in r.lower() or "0 canvas" in r.lower() for r in artifact["reasons"]), artifact["reasons"]


def test_runtime_frame_proof_passes_canvasless_ref_with_no_impl_surface(tmp_path: Path) -> None:
    """FIX 2a guard: a ref with NO genuine canvas/WebGL evidence (signal came
    from a lottie keyword, no canvas-webgl-detection canvasCount) and an impl
    with 0 surfaces must still PASS informationally — the fail is ref-evidence
    gated so genuinely canvas-less refs are unaffected.
    """
    ref = tmp_path / "ref"
    ref.mkdir()
    # REF_NEEDS triggers on the lottie keyword; no canvas-webgl-detection.json,
    # so there is no genuine canvas/WebGL evidence.
    (ref / "animations-detected.json").write_text(
        json.dumps({"engines": ["lottie"]}), encoding="utf-8"
    )
    env = _make_agent_browser_stub(tmp_path / "bin", _ZERO_SURFACE_PROBE)
    proc = subprocess.run(
        ["bash", str(SCRIPTS / "runtime-frame-proof-check.sh"),
         "sess", "http://localhost:9", str(ref)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "runtime-frame-proof.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "pass"
