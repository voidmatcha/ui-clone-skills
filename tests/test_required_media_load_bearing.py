"""Load-bearing reference hardening for required-media-coverage-check.sh.

The raw-substring reference scan let a clone satisfy coverage with a dead
`void LOTTIE.outroPc` expression statement or a comment naming the path
(observed verbatim in a navercorp run: `// referenced so
required-media-coverage passes`). These tests pin the hardened behavior:
a media path counts as referenced only when it is wired into runtime — a
direct load-bearing site, or a binding that is actually consumed by a call
or JSX attribute. Comment-only, void, and dead-const references must fail.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "required-media-coverage-check.sh"

LOTTIE_PATH = "/img/lottie/intro.json"
VIDEO_PATH = "/video/hero.mp4"


def _run(ref: Path, impl: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("BASH_COMPAT", None)
    return subprocess.run(
        ["bash", str(SCRIPT), str(ref), str(impl)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _artifact(ref: Path) -> dict:
    data: dict = json.loads((ref / "required-media-coverage.json").read_text())
    return data


def test_required_media_coverage_wrapper_avoids_large_python_heredoc() -> None:
    """Keep the large classifier out of Bash's pipe-backed heredoc path."""
    body = SCRIPT.read_text(encoding="utf-8")
    helper = SCRIPT.parent / "lib" / "required_media_coverage.py"

    assert "<<" not in body
    assert "python3 -" not in body
    assert 'python3 "$SCRIPT_DIR/lib/required_media_coverage.py"' in body
    assert helper.stat().st_size > 16_384


def test_required_media_coverage_default_bash_finishes_without_compat_env(
    tmp_path: Path,
) -> None:
    """The default Bash must not need inherited heredoc compatibility state."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "required-media.json").write_text(json.dumps({
        "schemaVersion": 1,
        "videos": [],
        "lottie": [],
        "svgs": [],
        "totals": {"video": 0, "lottie": 0, "svg": 0},
    }))
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)

    proc = _run(ref, impl)
    art = _artifact(ref)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"


def _lottie_ref(tmp_path: Path, src_body: str) -> tuple[Path, Path]:
    """Ref requires one Lottie whose .json is present in impl/public and whose
    runtime package is installed — so the ONLY variable under test is whether
    the path is load-bearingly referenced in `media.ts`."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "required-media.json").write_text(json.dumps({
        "schemaVersion": 1,
        "videos": [],
        "lottie": [{"path": LOTTIE_PATH, "evidenceFile": "bundles/app.js"}],
        "svgs": [],
        "totals": {"video": 0, "lottie": 1, "svg": 0},
    }))
    impl = tmp_path / "impl"
    lottie_dir = impl / "public" / "img" / "lottie"
    lottie_dir.mkdir(parents=True)
    (lottie_dir / "intro.json").write_text("{}")
    (impl / "package.json").write_text(json.dumps({
        "dependencies": {"lottie-web": "^5.12.2"},
    }))
    src = impl / "src"
    src.mkdir()
    (src / "media.ts").write_text(src_body)
    return ref, impl


def _video_ref(tmp_path: Path, src_body: str) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "required-media.json").write_text(json.dumps({
        "schemaVersion": 1,
        "videos": [{"section": "hero", "src": VIDEO_PATH, "type": "video/mp4"}],
        "lottie": [],
        "svgs": [],
        "totals": {"video": 1, "lottie": 0, "svg": 0},
    }))
    impl = tmp_path / "impl"
    video_dir = impl / "public" / "video"
    video_dir.mkdir(parents=True)
    (video_dir / "hero.mp4").write_text("binary")
    (impl / "package.json").write_text(json.dumps({"dependencies": {"react": "19"}}))
    src = impl / "src"
    src.mkdir()
    (src / "Hero.tsx").write_text(src_body)
    return ref, impl


def test_void_reference_is_rejected(tmp_path: Path) -> None:
    """The exact navercorp gaming: the path is a live const-map entry, but the
    binding is only ever `void`-ed. Coverage must now fail."""
    body = (
        "import lottie from 'lottie-web';\n"
        "export const LOTTIE = {\n"
        f"  intro: '{LOTTIE_PATH}',\n"
        "};\n"
        "export function init(): void {\n"
        "  // referenced so required-media-coverage passes\n"
        "  void LOTTIE.intro;\n"
        "}\n"
    )
    ref, impl = _lottie_ref(tmp_path, body)
    proc = _run(ref, impl)
    art = _artifact(ref)
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    assert art["totals"]["lottieMissing"] == 1
    miss = art["missing"]["lottie"][0]
    assert miss["kind"] == "not-referenced-in-src", miss
    assert miss["refRejectReason"] == "binding-unused:intro", miss


def test_comment_only_reference_is_rejected(tmp_path: Path) -> None:
    """A path named only inside a comment is not runtime wiring."""
    body = (
        "import lottie from 'lottie-web';\n"
        "export function init(): void {\n"
        f"  // splash uses {LOTTIE_PATH} but it is never mounted\n"
        "  return;\n"
        "}\n"
    )
    ref, impl = _lottie_ref(tmp_path, body)
    proc = _run(ref, impl)
    art = _artifact(ref)
    assert proc.returncode == 1, art
    assert art["status"] == "fail"
    miss = art["missing"]["lottie"][0]
    assert miss["refRejectReason"] == "only-in-comment", miss


def test_load_animation_call_is_accepted(tmp_path: Path) -> None:
    """The path literal sitting directly in a loadAnimation({path}) call is the
    canonical load-bearing site."""
    body = (
        "import lottie from 'lottie-web';\n"
        "export function init(el: HTMLElement): void {\n"
        "  lottie.loadAnimation({\n"
        "    container: el,\n"
        "    renderer: 'svg',\n"
        f"    path: '{LOTTIE_PATH}',\n"
        "    loop: true,\n"
        "  });\n"
        "}\n"
    )
    ref, impl = _lottie_ref(tmp_path, body)
    proc = _run(ref, impl)
    art = _artifact(ref)
    assert proc.returncode == 0, art
    assert art["status"] == "pass"
    assert art["totals"]["lottieMissing"] == 0


def test_const_map_consumed_by_call_is_accepted(tmp_path: Path) -> None:
    """The legitimate const-map idiom: the path lives in a central object and
    the binding is passed to a real call. Must still pass so the hardening does
    not breed a new false-failure (and new gaming pressure)."""
    body = (
        "import lottie from 'lottie-web';\n"
        f"export const LOTTIE = {{ intro: '{LOTTIE_PATH}' }};\n"
        "function mount(p: string) { return lottie.loadAnimation({ path: p }); }\n"
        "export function init(): void { mount(LOTTIE.intro); }\n"
    )
    ref, impl = _lottie_ref(tmp_path, body)
    proc = _run(ref, impl)
    art = _artifact(ref)
    assert proc.returncode == 0, art
    assert art["status"] == "pass"
    assert art["totals"]["lottieMissing"] == 0


def test_jsx_src_attribute_is_accepted(tmp_path: Path) -> None:
    """A <video src="..."> JSX attribute is a runtime wiring site."""
    body = (
        "export const Hero = () => (\n"
        f'  <video src="{VIDEO_PATH}" autoPlay muted loop playsInline />\n'
        ");\n"
    )
    ref, impl = _video_ref(tmp_path, body)
    proc = _run(ref, impl)
    art = _artifact(ref)
    assert proc.returncode == 0, art
    assert art["status"] == "pass"
    assert art["totals"]["videoMissing"] == 0
