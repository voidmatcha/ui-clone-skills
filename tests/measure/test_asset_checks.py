from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from ._helpers import (
    _baseline_post_implement_inputs,
    _make_verification_plan,
    _project_root,
    _run_script,
)


def test_lottie_runtime_check_script_fails_without_runtime_or_json(tmp_path: Path) -> None:
    """Ref Lottie evidence requires a real impl Lottie runtime and JSON data."""
    work = tmp_path / "work"
    ref = work / "ref"
    impl = work / "impl"
    src = impl / "src"
    public = impl / "public"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir()
    (ref / "bundle-map.json").write_text(json.dumps({
        "resources": ["https://cdn.example.com/bodymovin.min.js"],
        "notes": "page registers a lottie-web animation",
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "react-dom": "19"},
    }))
    (public / "app-config.json").write_text(json.dumps({"theme": "dark"}))
    (src / "App.tsx").write_text("export default function App() { return <main />; }\n")

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"missing Lottie runtime/json must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["refDetected"] is True
    assert "runtime package" in " ".join(artifact["reasons"])
    assert "animation JSON" in " ".join(artifact["reasons"])


def test_lottie_runtime_check_detects_required_media_lottie(tmp_path: Path) -> None:
    """required-media.json is the canonical Step 6b-bis Lottie inventory.

    If it contains Lottie URLs, lottie-runtime-check must not skip just because
    older artifact files lack the word lottie. Navercorp loops exposed this:
    required-media.json listed Lottie JSON, but runtime check reported
    refDetected=false and let a zero-Lottie impl look inapplicable.
    """
    work = tmp_path / "work"
    ref = work / "ref"
    impl = work / "impl"
    src = impl / "src"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    (ref / "required-media.json").write_text(json.dumps({
        "schemaVersion": 1,
        "videos": [],
        "lottie": [{"path": "/img/lottie/hero.json"}],
        "svgs": [],
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "react-dom": "19"},
    }))
    (src / "App.tsx").write_text("export default function App() { return <main />; }\n")

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"required-media Lottie must require runtime: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["refDetected"] is True
    assert "runtime package" in " ".join(artifact["reasons"])



def test_lottie_runtime_check_passes_with_runtime_usage_and_json(tmp_path: Path) -> None:
    """A faithful impl has dependency, source usage, and local animation data."""
    work = tmp_path / "work"
    ref = work / "ref"
    impl = work / "impl"
    src = impl / "src"
    public = impl / "public" / "animations"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-animation", "engine": "Lottie", "target": ".hero"}],
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "lottie-web": "5.12.2"},
    }))
    (src / "Hero.tsx").write_text(
        "import lottie from 'lottie-web';\n"
        "export function Hero() { lottie.loadAnimation({ path: '/animations/hero.json' }); return <div />; }\n"
    )
    (public / "hero.json").write_text(json.dumps({"v": "5.12.2", "layers": []}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, f"complete Lottie impl must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["runtimeUsageFound"] is True
    assert artifact["jsonFound"] is True


def test_lottie_runtime_check_rejects_arbitrary_dotlottie_bytes(tmp_path: Path) -> None:
    """A .lottie extension is not proof of dotLottie animation data."""
    work = tmp_path / "work"
    ref = work / "ref"
    impl = work / "impl"
    src = impl / "src"
    public = impl / "public" / "animations"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-animation", "engine": "Lottie", "target": ".hero"}],
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "lottie-web": "5.12.2"},
    }))
    (src / "Hero.tsx").write_text(
        "import lottie from 'lottie-web';\n"
        "export function Hero() { lottie.loadAnimation({ path: '/animations/hero.lottie' }); return <div />; }\n"
    )
    (public / "hero.lottie").write_bytes(b"not a dotLottie zip archive")

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"arbitrary .lottie bytes must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["jsonFound"] is False
    assert "animation JSON" in " ".join(artifact["reasons"])


def test_lottie_runtime_check_accepts_valid_dotlottie_archive(tmp_path: Path) -> None:
    """A real dotLottie archive counts when it carries manifest + Lottie JSON."""
    work = tmp_path / "work"
    ref = work / "ref"
    impl = work / "impl"
    src = impl / "src"
    public = impl / "public" / "animations"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-animation", "engine": "Lottie", "target": ".hero"}],
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "lottie-web": "5.12.2"},
    }))
    (src / "Hero.tsx").write_text(
        "import lottie from 'lottie-web';\n"
        "export function Hero() { lottie.loadAnimation({ path: '/animations/hero.lottie' }); return <div />; }\n"
    )
    with zipfile.ZipFile(public / "hero.lottie", "w") as archive:
        archive.writestr("manifest.json", json.dumps({"animations": [{"id": "hero", "path": "animations/hero.json"}]}))
        archive.writestr("animations/hero.json", json.dumps({"v": "5.12.2", "fr": 30, "ip": 0, "op": 60, "layers": []}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, f"valid dotLottie archive must pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["status"] == "pass"
    assert artifact["jsonFound"] is True


def test_lottie_runtime_check_rejects_fallback_svg_runtime_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fallback SVG surface is not an animating Lottie runtime."""
    work = tmp_path / "work"
    ref = work / "ref"
    impl = work / "impl"
    src = impl / "src"
    public = impl / "public" / "animations"
    bin_dir = work / "bin"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    bin_dir.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-animation", "engine": "Lottie", "target": ".hero"}],
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "lottie-web": "5.12.2"},
    }))
    (src / "Hero.tsx").write_text(
        "import lottie from 'lottie-web';\n"
        "export function Hero() {\n"
        "  lottie.loadAnimation({ path: '/animations/hero.json' });\n"
        "  return <div data-lottie=\"fallback\"><svg /></div>;\n"
        "}\n"
    )
    (public / "hero.json").write_text(json.dumps({"v": "5.12.2", "fr": 30, "ip": 0, "op": 60, "layers": []}))
    agent_browser = bin_dir / "agent-browser"
    agent_browser.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$*\" in\n"
        "  *'window.__lottieSnap = '*) printf '%s\\n' '{\"count\":1}' ;;\n"
        "  *'return JSON.stringify'*) printf '%s\\n' '{\"count\":1,\"animating\":1,\"fallbackCount\":1,\"readyCount\":0,\"advancedCount\":0}' ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    agent_browser.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "http://127.0.0.1:4173"],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"fallback SVG runtime proof must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    proof = artifact["runtimeProof"]
    assert artifact["status"] == "fail"
    assert proof["status"] == "runtime-fail"
    assert proof["animatingCount"] == 0
    assert proof["candidateCount"] == 1


def test_lottie_runtime_check_scroll_drives_generated_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scroll-bound generated Lottie surfaces need a driven scroll delta."""
    work = tmp_path / "work"
    ref = work / "ref"
    impl = work / "impl"
    src = impl / "src"
    public = impl / "public" / "animations"
    bin_dir = work / "bin"
    log_file = work / "agent-browser.log"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    bin_dir.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-animation", "engine": "Lottie", "trigger": "scroll", "target": ".hero"}],
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "lottie-web": "5.12.2"},
    }))
    (src / "Hero.tsx").write_text(
        "import lottie from 'lottie-web';\n"
        "export function Hero() {\n"
        "  lottie.loadAnimation({ path: '/animations/hero.json' });\n"
        "  return <div data-lottie=\"ready\" data-lottie-progress=\"0\" data-lottie-total-frames=\"90\" />;\n"
        "}\n"
    )
    (public / "hero.json").write_text(json.dumps({"v": "5.12.2", "fr": 30, "ip": 0, "op": 90, "layers": []}))
    agent_browser = bin_dir / "agent-browser"
    agent_browser.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$AGENT_BROWSER_LOG\"\n"
        "case \"$*\" in\n"
        "  *'window.__lottieSnap = '*) printf '%s\\n' '{\"count\":1}' ;;\n"
        "  *'window.__lottieScrollProbe'*) printf '%s\\n' '{\"scrolled\":true}' ;;\n"
        "  *'return JSON.stringify'*) printf '%s\\n' '{\"count\":1,\"animating\":1,\"fallbackCount\":0,\"readyCount\":1,\"advancedCount\":1}' ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    agent_browser.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("AGENT_BROWSER_LOG", str(log_file))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "http://127.0.0.1:4173"],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, f"scroll-driven Lottie proof must pass: {proc.stdout}\n{proc.stderr}"
    assert "window.__lottieScrollProbe" in log_file.read_text()
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["runtimeProof"]["status"] == "runtime-pass"
    assert artifact["runtimeProof"]["animatingCount"] == 1


def test_lottie_runtime_check_awaits_promise_native_player(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """lottie-player.getLottie() commonly resolves the runtime instance asynchronously."""
    work = tmp_path / "work"
    ref = work / "ref"
    impl = work / "impl"
    src = impl / "src"
    public = impl / "public" / "animations"
    bin_dir = work / "bin"
    log_file = work / "agent-browser.log"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    bin_dir.mkdir()
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-animation", "engine": "Lottie", "target": "lottie-player"}],
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "@lottiefiles/lottie-player": "2.0.0"},
    }))
    (src / "Hero.tsx").write_text(
        "import '@lottiefiles/lottie-player';\n"
        "export function Hero() { return <lottie-player src=\"/animations/hero.json\" autoplay />; }\n"
    )
    (public / "hero.json").write_text(json.dumps({"v": "5.12.2", "fr": 30, "ip": 0, "op": 90, "layers": []}))
    agent_browser = bin_dir / "agent-browser"
    agent_browser.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$AGENT_BROWSER_LOG\"\n"
        "case \"$*\" in\n"
        "  *'window.__lottieSnap = await'*) printf '%s\\n' '{\"count\":1}' ;;\n"
        "  *'window.__lottieScrollProbe'*) printf '%s\\n' '{\"scrolled\":false}' ;;\n"
        "  *'return JSON.stringify'*) printf '%s\\n' '{\"count\":1,\"animating\":1,\"fallbackCount\":0,\"readyCount\":1,\"advancedCount\":1}' ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    agent_browser.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("AGENT_BROWSER_LOG", str(log_file))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl), "http://127.0.0.1:4173"],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, f"promise native player proof must pass: {proc.stdout}\n{proc.stderr}"
    log = log_file.read_text()
    assert "async ()" in log
    assert "await el.getLottie" in log or "await el.getAnimation" in log
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["runtimeProof"]["status"] == "runtime-pass"


def test_lottie_runtime_check_samples_native_player_motion_properties() -> None:
    """Native players may animate without data-lottie frame attributes."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    body = script.read_text(encoding="utf-8")
    assert "currentFrame" in body
    assert "currentTime" in body
    assert "getLottie" in body or "getAnimation" in body


def test_lottie_runtime_check_second_probe_uses_async_iife() -> None:
    """The re-probe awaits native players, so its containing IIFE must be async."""
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    body = script.read_text(encoding="utf-8")
    second_probe = body.split("# Re-probe and diff against the snapshot.", 1)[1]

    assert "(async () => {" in second_probe
    assert "await Promise.all" in second_probe


def test_lottie_runtime_check_fails_on_import_only_usage(tmp_path: Path) -> None:
    """Importing lottie-web is not proof that a visible animation is mounted."""
    work = tmp_path / "work"
    ref = work / "ref"
    impl = work / "impl"
    src = impl / "src"
    public = impl / "public" / "animations"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-animation", "engine": "Lottie", "target": ".hero"}],
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "lottie-web": "5.12.2"},
    }))
    (src / "Hero.tsx").write_text(
        "import lottie from 'lottie-web';\n"
        "export function Hero() { return <div className=\"hero\" />; }\n"
    )
    (public / "hero.json").write_text(json.dumps({"v": "5.12.2", "layers": []}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"import-only Lottie must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["status"] == "fail"
    assert artifact["runtimeUsageFound"] is False
    assert "runtime usage" in " ".join(artifact["reasons"])



def test_lottie_runtime_artifact_schema_v2_with_runtime_proof_block() -> None:
    """2026-05-22 user direction: even with package + import + JSON present,
    'data-lottie만 붙이는 것은 실패입니다'. The artifact must carry a
    runtimeProof block so downstream tooling can see whether the browser
    actually painted svg/canvas, not just whether static evidence existed.

    Legacy schema (v1) had no runtimeProof. v2 must include it with three
    fields. When called without an impl URL (backward compat), runtimeProof
    falls back to status=static-only so existing callers don't regress.
    """
    work_root = Path(_project_root()) / "tmp" / "_test_lottie_v2"
    if work_root.exists():
        import shutil
        shutil.rmtree(work_root)
    ref = work_root / "ref"
    impl = work_root / "impl"
    src = impl / "src"
    public = impl / "public" / "animations"
    ref.mkdir(parents=True)
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    (ref / "transition-spec.json").write_text(json.dumps({
        "transitions": [{"id": "hero-animation", "engine": "Lottie", "target": ".hero"}],
    }))
    (impl / "package.json").write_text(json.dumps({
        "name": "impl",
        "dependencies": {"react": "19", "lottie-web": "5.12.2"},
    }))
    (src / "Hero.tsx").write_text(
        "import lottie from 'lottie-web';\n"
        "import data from '../public/animations/hero.json';\n"
        "export function Hero() {\n"
        "  React.useEffect(() => { lottie.loadAnimation({\n"
        "    container: document.querySelector('.hero'),\n"
        "    renderer: 'svg', loop: true, autoplay: true, animationData: data,\n"
        "  }); }, []);\n"
        "  return <div className=\"hero\" />;\n"
        "}\n"
    )
    (public / "hero.json").write_text(json.dumps({"v": "5.12.2", "fr": 30, "ip": 0, "op": 60, "layers": []}))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, f"static-complete impl must pass when no impl-url provided: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "lottie-runtime.json").read_text())
    assert artifact["schemaVersion"] == 2, "schema v2 required (runtimeProof block added)"
    assert "runtimeProof" in artifact, "v2 artifact must include runtimeProof block"
    proof = artifact["runtimeProof"]
    assert proof["status"] == "static-only", (
        "Without impl-url, runtimeProof.status must be 'static-only' "
        f"(got {proof['status']!r}). Backward-compat for legacy callers."
    )
    assert proof["candidateCount"] == 0
    assert proof["animatingCount"] == 0



def test_lottie_runtime_check_documents_runtime_proof_in_rule() -> None:
    """The artifact's `rule` field is the user-facing explanation of what
    the gate enforces. v2 must mention the runtime proof so the failure
    mode 'package imported but loadAnimation never wired' is named.
    """
    script_path = _project_root() / "skills" / "visual-debug" / "scripts" / "lottie-runtime-check.sh"
    body = script_path.read_text(encoding="utf-8")
    # The runtime-proof rule is baked into write_json so every artifact
    # carries it. Check the source so the rule travels with the script.
    assert "ready state" in body and "frame/progress" in body, (
        "rule must describe the runtime frame/progress assertion"
    )
    assert "loadAnimation" in body or "currentTime" in body, (
        "rule should call out the browser-side proof timing"
    )



def test_lottie_runtime_dispatcher_passes_impl_url() -> None:
    """2026-05-22 codex-rescue audit found the runtime proof in
    lottie-runtime-check.sh only fires when impl_url is passed as the 3rd
    positional arg. The dispatcher SIGNATURES entry must include {impl_url}
    or the runtime proof is never exercised in the auto-run pipeline.
    """
    dispatcher = _project_root() / "scripts" / "verify" / "build_required_dispatch.py"
    text = dispatcher.read_text(encoding="utf-8")
    # Find the lottie-runtime-check.sh signature line and assert {impl_url}.
    import re
    m = re.search(r'"lottie-runtime-check\.sh":\s*"([^"]+)"', text)
    assert m, "lottie-runtime-check.sh signature missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{impl_url}" in recipe, (
        f"lottie-runtime-check.sh recipe must pass {{impl_url}} so the runtime "
        f"proof fires (got: {recipe!r}). Without it the gate falls back to "
        "static-only and 'data-lottie만 붙이는 것은 실패입니다' regresses."
    )



def test_svg_provenance_check_script_present() -> None:
    """2026-05-22 codex-rescue audit (a125b997): svg-provenance gate must
    exist, be executable, and document the IconMark.tsx-style failure
    mode that motivated it.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "svg-provenance-check.sh"
    assert script.is_file(), "svg-provenance-check.sh missing"
    body = script.read_text(encoding="utf-8")
    assert "IconMark" in body or "invent" in body, (
        "script must reference the IconMark.tsx / SVG-invention failure mode"
    )
    assert "svg-provenance.json" in body, "must write canonical artifact"
    assert "<path d" in body or "path d=" in body or 'querySelectorAll("path")' in body, (
        "must probe SVG path geometry"
    )



def test_svg_provenance_dispatcher_wired() -> None:
    """Regression: ensure svg-provenance-check.sh is in dispatcher
    SIGNATURES with both {ref_url} and {impl_url} (probe requires both).
    """
    import re
    dispatcher = _project_root() / "scripts" / "verify" / "build_required_dispatch.py"
    text = dispatcher.read_text(encoding="utf-8")
    m = re.search(r'"svg-provenance-check\.sh":\s*"([^"]+)"', text)
    assert m, "svg-provenance-check.sh missing from dispatcher SIGNATURES"
    recipe = m.group(1)
    assert "{ref_url}" in recipe and "{impl_url}" in recipe, (
        f"svg-provenance recipe must include both ref/impl URLs (got: {recipe!r})"
    )



def test_verification_plan_includes_svg_provenance_block_row() -> None:
    """The plan emitter must register svg-provenance as a block row —
    silently invented SVGs would otherwise pass svg-dom-parity-count and
    waste section-compare iterations.
    """
    plan_script = _project_root() / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
    text = plan_script.read_text(encoding="utf-8")
    import re
    block = re.search(
        r'add_check\s+"svg-provenance"[\s\S]+?"(block|warn)"',
        text,
    )
    assert block, "svg-provenance add_check missing or malformed"
    assert block.group(1) == "block", (
        "svg-provenance must be severity=block (IconMark.tsx-style invention is a real failure)"
    )



def test_scaffold_to_jsx_rewrites_cdn_optimizer_srcset_to_local_assets(tmp_path: Path) -> None:
    """Loop-55 regression: preserving ref `/cdn-cgi/image/...` src/srcset
    makes the local impl request a Cloudflare optimizer route that Next does
    not serve. The deterministic scaffold must rewrite both src and srcset
    candidates to transferred public asset paths.
    """
    import subprocess

    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps({
        "tag": "main",
        "children": [
            {
                "tag": "section",
                "class": "hero",
                "children": [
                    {
                        "tag": "img",
                        "class": "hero-img",
                        "alt": "Foo",
                        "src": "https://cdn.example.com/cdn-cgi/image/width=640,quality=90/images/foo.webp",
                        "srcset": (
                            "https://cdn.example.com/cdn-cgi/image/width=256,quality=90/images/foo.webp 256w, "
                            "https://cdn.example.com/cdn-cgi/image/width=640,quality=90/images/foo.webp 640w"
                        ),
                    }
                ],
            }
        ],
    }))
    (ref / "section-map.json").write_text(json.dumps({
        "sections": [{"index": 0, "tag": "section", "cls": "hero"}],
    }))

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "scaffold-to-jsx.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

    generated = "\n".join(p.read_text(encoding="utf-8") for p in (impl / "src" / "components").glob("*.tsx"))
    assert "/cdn-cgi/image/" not in generated
    assert 'src="/images/foo.webp"' in generated
    assert 'srcSet="/images/foo.webp 256w, /images/foo.webp 640w"' in generated



def test_ref_screenshot_asset_fail_on_byte_copy(tmp_path: Path) -> None:
    """Byte-identical impl copy of a ref screenshot must FAIL the gate."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "public").mkdir(parents=True)
    (impl / "src").mkdir()
    payload = b"\x89PNG\r\n\x1a\n" + b"realbytes-xyzzy"
    (ref / "sections" / "ref" / "hero.png").write_bytes(payload)
    (impl / "public" / "renamed.png").write_bytes(payload)
    (impl / "src" / "App.tsx").write_text("export default function A(){return null}\n")
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    assert art["status"] == "fail"
    kinds = {v["kind"] for v in art["violations"]}
    assert "byte-identical-copy" in kinds



def test_ref_screenshot_asset_pass_on_clean_impl(tmp_path: Path) -> None:
    """Impl that doesn't touch ref capture artifacts must PASS."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    (ref / "sections" / "ref" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        'import "/images/logo.png"\nexport default function A(){return null}\n',
    )
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    assert art["status"] == "pass"


def test_ref_screenshot_asset_pass_on_tmp_ref_in_comment(tmp_path: Path) -> None:
    """Loop-2 improve finding (2026-06-07): a `tmp/ref/` path mentioned in a
    CODE COMMENT (a provenance note, not a runtime asset reference) tripped the
    ref-path-reference violation, even though a comment cannot load a screenshot
    to fake a pixel-diff. Comments are stripped before the forbidden-substring
    scan — same rationale as dom-mirror/text-fidelity comment-strips. Observed:
    a FidelityText.tsx line-1 comment referencing tmp/ref/<c>/text-fidelity-
    check.json hard-failed an otherwise-clean clone.
    """
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    (ref / "sections" / "ref" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "FidelityText.tsx").write_text(
        "// Derived fidelity sentinels generated from "
        "tmp/ref/realfood/text-fidelity-check.json.\n"
        "export default function A(){return null}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    assert art["status"] == "pass", art


def test_ref_screenshot_asset_fail_on_tmp_ref_in_code(tmp_path: Path) -> None:
    """Guard the comment-strip above does NOT weaken anti-cheat: a `tmp/ref/`
    reference in ACTUAL code (not a comment) must still flag ref-path-reference.
    """
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    (ref / "sections" / "ref" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        'const bg = "tmp/ref/realfood/sections/ref/hero.png";\n'
        "export default function A(){return null}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    assert art["status"] == "fail"
    assert "ref-path-reference" in {v["kind"] for v in art["violations"]}


def test_ref_screenshot_asset_fail_on_protocol_relative_url(tmp_path: Path) -> None:
    """Hardening (2026-06-07): the `//`-comment strip must NOT swallow a
    protocol-relative asset URL. A cheat that serves a ref screenshot via a
    string like src="//cdn/<c>/sections/ref/hero.png" has `//` preceded by a
    quote (not whitespace/line-start), so it is a runtime-loadable reference,
    not a comment — it must still flag ref-path-reference. The prior
    `(?<!:)//[^\\n]*` strip wrongly removed the whole line as a comment.
    """
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    (ref / "sections" / "ref" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        'const bg = "//cdn.example.com/tmp/ref/realfood/sections/ref/hero.png";\n'
        "export default function A(){return null}\n",
    )
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    assert art["status"] == "fail"
    assert "ref-path-reference" in {v["kind"] for v in art["violations"]}


def test_ref_screenshot_asset_fail_on_reencoded_near_match(tmp_path: Path) -> None:
    """The screenshot-as-background cheat re-encodes ref section crops (so the
    bytes — and sha256 — differ) and serves them at a generic /sections/<name>.png
    path. The sha256 byte-identical check misses it; a perceptual/AE near-match
    against ref/sections/ref/*.png must catch it."""
    magick = shutil.which("magick") or shutil.which("convert")
    if not magick:
        pytest.skip("ImageMagick not available")
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "public" / "sections").mkdir(parents=True)
    (impl / "src").mkdir()
    ref_crop = ref / "sections" / "ref" / "dga_hero__AjMaf.png"
    subprocess.run(
        [magick, "-size", "200x300", "xc:white", "-fill", "navy",
         "-draw", "rectangle 20,20 180,160", str(ref_crop)],
        check=True,
    )
    # Re-encode the same pixels under a generic crop path (the cheat).
    impl_png = impl / "public" / "sections" / "dga_hero__AjMaf.png"
    subprocess.run(
        [magick, str(ref_crop), "-strip", "-quality", "88", str(impl_png)],
        check=True,
    )
    assert ref_crop.read_bytes() != impl_png.read_bytes(), "fixture must re-encode (differ in bytes)"
    (impl / "src" / "App.tsx").write_text("export default function A(){return null}\n")
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    assert art["status"] == "fail"
    kinds = {v["kind"] for v in art["violations"]}
    assert "screenshot-asset-near-match" in kinds, kinds


def test_ref_screenshot_asset_pass_on_genuine_different_image(tmp_path: Path) -> None:
    """A genuine clone ships its own images; as long as they do NOT pixel-match
    the verifier's ref section crops, near-match must not false-positive."""
    magick = shutil.which("magick") or shutil.which("convert")
    if not magick:
        pytest.skip("ImageMagick not available")
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "public").mkdir(parents=True)
    (impl / "src").mkdir()
    ref_crop = ref / "sections" / "ref" / "hero.png"
    subprocess.run(
        [magick, "-size", "200x300", "xc:white", "-fill", "navy",
         "-draw", "rectangle 20,20 180,160", str(ref_crop)],
        check=True,
    )
    impl_png = impl / "public" / "logo.png"
    subprocess.run(
        [magick, "-size", "200x300", "xc:black", "-fill", "yellow",
         "-draw", "circle 100,150 100,40", str(impl_png)],
        check=True,
    )
    (impl / "src" / "App.tsx").write_text("export default function A(){return null}\n")
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    assert art["status"] == "pass"



# ── Canvas-replay allowlist for ref-screenshot-asset (v0.7.0) ───────────
# When closeoutPolicy=="canvas-replay" AND attestation is present AND a
# byte-identical-copy violation involves a screenshot whose source PNG
# belongs to a section tagged kind="canvas" in section-map.json, the
# violation is allowed. Non-canvas-section copies still FAIL.
#
# Scope is byte-identical-copy specifically — ref-path-reference (generic
# "tmp/ref/" substring leaks in impl source) stays strict because the
# substring doesn't pinpoint which section the leak is for, and the
# canvas-replay opt-in escape is not for generic path-string laundering.


def _wire_canvas_replay_screenshot(
    ref: Path,
    *,
    canvas_section_name: str,
    policy: str = "canvas-replay",
    with_attestation: bool = True,
) -> None:
    (ref / "pipeline-state.json").write_text(
        json.dumps({"component": ref.name, "closeoutPolicy": policy}),
        encoding="utf-8",
    )
    if with_attestation:
        (ref / "canvas-replay-attestation.json").write_text(
            json.dumps({
                "license": "MIT",
                "disclaimer": "test",
                "attestedBy": "operator",
                "attestedAt": "2026-05-25T08:00:00Z",
                "ref_canvas_sources": ["https://canvas.example.org/driver.js"],
            }),
            encoding="utf-8",
        )
    (ref / "section-map.json").write_text(
        json.dumps({
            "sections": [
                {"index": 0, "kind": "canvas", "name": canvas_section_name},
                {"index": 1, "name": "text-block"},  # non-canvas control
            ],
        }),
        encoding="utf-8",
    )


def test_ref_screenshot_asset_allows_canvas_section_byte_copy(tmp_path: Path) -> None:
    """v0.7.0 — byte-identical copy of a canvas-section ref screenshot is
    allowed when the policy is fully active. Operator may use the canvas
    section's captured PNG as a fallback under the attestation umbrella."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "public").mkdir(parents=True)
    (impl / "src").mkdir()
    payload = b"\x89PNG\r\n\x1a\n" + b"canvas-fallback-bytes"
    # The ref PNG basename matches a kind=canvas section in section-map.json.
    (ref / "sections" / "ref" / "music-sphere.png").write_bytes(payload)
    (impl / "public" / "canvas-fallback.png").write_bytes(payload)
    (impl / "src" / "App.tsx").write_text("export default function A(){return null}\n")
    _wire_canvas_replay_screenshot(ref, canvas_section_name="music-sphere")
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 0, (
        f"canvas-section byte-copy must PASS under canvas-replay: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    assert art["status"] == "pass"


def test_ref_screenshot_asset_still_fails_non_canvas_byte_copy(tmp_path: Path) -> None:
    """v0.7.0 boundary — non-canvas section byte-copy still FAILs even when
    the canvas-replay policy is active. The relief is per-section, not blanket."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "public").mkdir(parents=True)
    (impl / "src").mkdir()
    payload = b"\x89PNG\r\n\x1a\n" + b"non-canvas-bytes"
    # The ref PNG basename is "text-block" — non-canvas in section-map.
    (ref / "sections" / "ref" / "text-block.png").write_bytes(payload)
    (impl / "public" / "stolen.png").write_bytes(payload)
    (impl / "src" / "App.tsx").write_text("export default function A(){return null}\n")
    _wire_canvas_replay_screenshot(ref, canvas_section_name="music-sphere")
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, (
        f"non-canvas byte-copy must STILL fail under canvas-replay: {proc.stdout}"
    )


def test_ref_screenshot_asset_no_allowlist_without_attestation(tmp_path: Path) -> None:
    """Policy set but attestation missing → no allowlist."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "public").mkdir(parents=True)
    (impl / "src").mkdir()
    payload = b"\x89PNG\r\n\x1a\n" + b"canvas-fallback-bytes"
    (ref / "sections" / "ref" / "music-sphere.png").write_bytes(payload)
    (impl / "public" / "fallback.png").write_bytes(payload)
    (impl / "src" / "App.tsx").write_text("export default function A(){return null}\n")
    _wire_canvas_replay_screenshot(
        ref, canvas_section_name="music-sphere", with_attestation=False,
    )
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, (
        f"missing attestation must NOT activate allowlist: {proc.stdout}"
    )


def test_ref_screenshot_asset_ref_path_reference_stays_strict(tmp_path: Path) -> None:
    """Canvas-replay does NOT relax ref-path-reference detection — generic
    'tmp/ref/' substring leaks in impl source stay strict. The substring
    doesn't pinpoint a section, so we can't safely scope relief to canvas."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        'export const REF = "/sections/ref/music-sphere.png";\n'
    )
    _wire_canvas_replay_screenshot(ref, canvas_section_name="music-sphere")
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, (
        f"ref-path-reference must stay strict under canvas-replay: {proc.stdout}"
    )
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    kinds = {v["kind"] for v in art["violations"]}
    assert "ref-path-reference" in kinds


def test_gate_svg_dom_parity_status_pass_passes(tmp_path: Path) -> None:
    """Clean SVG parity artifact → gate passes."""
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(ref, "svg-dom-parity", "svg-dom-parity.json")
    (ref / "svg-dom-parity.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "refUrl": "https://realfood.gov",
        "implUrl": "http://localhost:5173",
        "refPage": {"total": 8, "inlineSvg": 6, "svgWithPath": 6},
        "implPage": {"total": 7, "inlineSvg": 5, "svgWithPath": 5},
        "violations": [],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert not any("svg-dom-parity" in r.label for r in failures), failures



def test_gate_svg_dom_parity_status_fail_fails(tmp_path: Path) -> None:
    """SVG inventory dropout → gate fails."""
    from ui_clone.gate import Gate
    ref = tmp_path / "ref"
    ref.mkdir()
    _baseline_post_implement_inputs(ref)
    _make_verification_plan(ref, "svg-dom-parity", "svg-dom-parity.json")
    (ref / "svg-dom-parity.json").write_text(json.dumps({
        "schemaVersion": 1,
        "status": "fail",
        "refUrl": "https://realfood.gov",
        "implUrl": "http://localhost:5173",
        "refPage": {"total": 12, "inlineSvg": 12, "svgWithPath": 12},
        "implPage": {"total": 1, "inlineSvg": 1, "svgWithPath": 0},
        "violations": [
            {"kind": "page-total-svg-dropout", "ref": 12, "impl": 1, "ratio": 0.083},
            {"kind": "inline-svg-empty", "refWithPath": 12, "implWithPath": 0},
        ],
    }))
    results = Gate(ref).gate_post_implement()
    failures = [r for r in results if r.status == "fail"]
    assert any("svg-dom-parity" in r.label for r in failures), results



# ── FIX 3: ref-screenshot-asset self-scan false positive ─────────────────
# The check flagged 1 violation {"file":"ref-screenshot-asset.json",
# "kind":"ref-path-reference","needle":"tmp/ref/"} — it scanned its OWN output
# artifact (and sibling gate JSONs in the ref dir) and matched the "tmp/ref/"
# path string baked inside them. The gate's own output artifacts must be
# excluded from the scanned set; real ref-screenshot reuse in impl source still
# flags.


def test_ref_screenshot_asset_ignores_own_gate_artifacts(tmp_path: Path) -> None:
    """A prior gate-output JSON in the ref dir (containing 'tmp/ref/' paths)
    must NOT be flagged as a ref-path-reference when the ref dir is scanned —
    it is the gate's own artifact, not impl source."""
    d = tmp_path / "ref"
    (d / "sections" / "ref").mkdir(parents=True)  # empty: no binary self-match
    (d / "src").mkdir()
    # Prior gate outputs that bake the forbidden substring into their bodies.
    (d / "ref-screenshot-asset.json").write_text(json.dumps({
        "schemaVersion": 1, "status": "fail",
        "violations": [{"file": "x", "kind": "byte-identical-copy",
                        "refSource": "tmp/ref/comp/sections/ref/hero.png"}],
    }))
    (d / "svg-dom-parity-check.json").write_text(json.dumps({
        "refUrl": "tmp/ref/comp/static/ref/0.png", "violations": [],
    }))
    # Clean impl source: no reference reuse at all.
    (d / "src" / "App.tsx").write_text("export default function A(){return null}\n")

    # The reported config: impl_root resolves onto the ref dir's own artifacts.
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(d), str(d),
    )
    assert proc.returncode == 0, (
        f"gate must not flag its own output artifacts: {proc.stdout}\n{proc.stderr}"
    )
    art = json.loads((d / "ref-screenshot-asset.json").read_text())
    assert art["status"] == "pass", art
    assert art["violationCount"] == 0, art["violations"]


def test_ref_screenshot_asset_real_reuse_still_flags_with_ref_artifacts(tmp_path: Path) -> None:
    """Boundary: even when the ref dir holds gate-output JSONs, a REAL
    ref-screenshot reference in impl SOURCE still flags."""
    ref = tmp_path / "ref"
    (ref / "sections" / "ref").mkdir(parents=True)
    # A prior gate artifact present in the ref dir (the false-positive source).
    (ref / "ref-screenshot-asset.json").write_text(json.dumps({
        "status": "pass", "violations": [],
        "note": "tmp/ref/comp/sections/ref/hero.png",
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "App.tsx").write_text(
        'export const BG = "tmp/ref/comp/sections/ref/hero.png";\n'
    )
    proc = _run_script(
        "skills/visual-debug/scripts/ref-screenshot-asset-check.sh",
        str(ref), str(impl),
    )
    assert proc.returncode == 1, (
        f"real ref-screenshot reuse must still flag: {proc.stdout}\n{proc.stderr}"
    )
    art = json.loads((ref / "ref-screenshot-asset.json").read_text())
    kinds = {v["kind"] for v in art["violations"]}
    assert "ref-path-reference" in kinds, art
