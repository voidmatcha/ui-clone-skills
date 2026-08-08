"""transfer-fonts.sh + emit-preflight-neutralize.sh.

Ground truth these deterministic emitters fix: a clone can mirror every
@font-face rule yet ship zero font binaries under impl/public (every custom
face 404s to a system fallback), and Tailwind's Preflight resets UA heading
weights so a ref relying on the browser-default bold <h1> collapses 700→400.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSFER = ROOT / "scripts" / "extract" / "transfer-fonts.sh"
PREFLIGHT = ROOT / "scripts" / "extract" / "emit-preflight-neutralize.sh"


def _run(script: Path, *args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *[str(a) for a in args]],
        capture_output=True, text=True, timeout=120,
    )


# ── transfer-fonts.sh ─────────────────────────────────────────────────────────

def _font_ref(tmp_path: Path, css: str, resource_files: dict[str, bytes]) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    (ref / "css").mkdir(parents=True)
    (ref / "css" / "main.css").write_text(css, encoding="utf-8")
    for rel, content in resource_files.items():
        p = ref / "resources" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "public").mkdir()
    return ref, impl


def test_root_relative_font_is_transferred_preserving_url_path(tmp_path: Path) -> None:
    css = '@font-face { font-family: "X"; src: url("/font/Pretendard-Regular.woff"); }'
    ref, impl = _font_ref(
        tmp_path, css,
        {"navercorp.com/font/Pretendard-Regular.woff": b"WOFF-binary-bytes"},
    )
    proc = _run(TRANSFER, ref, impl)
    art = json.loads((ref / "font-transfer.json").read_text())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"
    assert art["totals"]["transferred"] == 1
    dest = impl / "public" / "font" / "Pretendard-Regular.woff"
    assert dest.is_file(), art
    assert dest.read_bytes() == b"WOFF-binary-bytes"


def test_relative_font_url_is_normalized_and_transferred(tmp_path: Path) -> None:
    css = (
        "@font-face { font-family: Human; "
        "src: url('../../../font/NanumHumanRegular.otf') format('opentype'); }"
    )
    ref, impl = _font_ref(
        tmp_path,
        css,
        {"navercorp.com/font/NanumHumanRegular.otf": b"otf-bytes"},
    )
    proc = _run(TRANSFER, ref, impl)
    art = json.loads((ref / "font-transfer.json").read_text())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"
    assert art["totals"]["referenced"] == 1
    assert art["totals"]["transferred"] == 1
    assert not any(
        s["reason"] == "relative-url-unresolved" for s in art["skipped"]
    ), art
    dest = impl / "public" / "font" / "NanumHumanRegular.otf"
    assert dest.read_bytes() == b"otf-bytes"


def test_nextjs_media_path_structure_is_preserved(tmp_path: Path) -> None:
    css = '@font-face { src: url("/_next/static/media/inter.abc123.woff2") format("woff2"); }'
    ref, impl = _font_ref(
        tmp_path, css,
        {"site.example/_next/static/media/inter.abc123.woff2": b"w2"},
    )
    _run(TRANSFER, ref, impl)
    assert (impl / "public" / "_next" / "static" / "media" / "inter.abc123.woff2").is_file()


def test_referenced_font_missing_from_resources_is_reported(tmp_path: Path) -> None:
    css = '@font-face { src: url("/font/NotDownloaded.woff2"); }'
    ref, impl = _font_ref(tmp_path, css, {})  # resources empty
    proc = _run(TRANSFER, ref, impl)
    art = json.loads((ref / "font-transfer.json").read_text())
    # The transfer action itself is clean (no copy error), but the gap is surfaced.
    assert proc.returncode == 0
    assert art["totals"]["transferred"] == 0
    assert art["totals"]["missing"] == 1
    assert art["missing"][0]["basename"] == "notdownloaded.woff2"


def test_font_referenced_only_in_js_bundle_is_transferred(tmp_path: Path) -> None:
    """Next.js emits font URLs from JS font-loader chunks, not always a .css
    file. A bare quoted root-relative font path in bundles/ must still transfer."""
    ref = tmp_path / "ref"
    (ref / "bundles").mkdir(parents=True)
    (ref / "bundles" / "app.min.js").write_text(
        'var p={src:"/_next/static/media/hash-s.woff2",weight:"400"};',
        encoding="utf-8",
    )
    binpath = ref / "resources" / "site.example" / "_next" / "static" / "media" / "hash-s.woff2"
    binpath.parent.mkdir(parents=True)
    binpath.write_bytes(b"w2")
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "public").mkdir()
    proc = _run(TRANSFER, ref, impl)
    art = json.loads((ref / "font-transfer.json").read_text())
    assert proc.returncode == 0
    assert art["totals"]["transferred"] == 1, art
    assert (impl / "public" / "_next" / "static" / "media" / "hash-s.woff2").is_file()


def test_absolute_cdn_font_url_is_left_alone(tmp_path: Path) -> None:
    css = '@font-face { src: url("https://cdn.example.com/fonts/Remote.woff2"); }'
    # Even if a same-basename binary happens to sit in resources, an absolute URL
    # loads from its own origin — copying to public/ would be dead weight.
    ref, impl = _font_ref(
        tmp_path, css,
        {"cdn.example.com/fonts/Remote.woff2": b"remote"},
    )
    _run(TRANSFER, ref, impl)
    art = json.loads((ref / "font-transfer.json").read_text())
    assert art["totals"]["transferred"] == 0
    assert any(s["reason"] == "absolute-url-loads-from-origin" for s in art["skipped"])
    assert not (impl / "public" / "fonts" / "Remote.woff2").exists()


def test_already_present_identical_font_is_skipped(tmp_path: Path) -> None:
    css = '@font-face { src: url("/font/Keep.woff"); }'
    ref, impl = _font_ref(tmp_path, css, {"h/font/Keep.woff": b"same-bytes"})
    dest = impl / "public" / "font" / "Keep.woff"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"same-bytes")  # already there, identical
    _run(TRANSFER, ref, impl)
    art = json.loads((ref / "font-transfer.json").read_text())
    assert art["totals"]["transferred"] == 0
    assert any(s["reason"] == "already-present-in-public" for s in art["skipped"])


# ── emit-preflight-neutralize.sh ─────────────────────────────────────────────

def _impl_with_index_css(tmp_path: Path, index_css: str) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)
    (impl / "src" / "index.css").write_text(index_css, encoding="utf-8")
    return ref, impl


def test_preflight_injects_layer_base_after_tailwind_base(tmp_path: Path) -> None:
    ref, impl = _impl_with_index_css(
        tmp_path, "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n")
    proc = _run(PREFLIGHT, ref, impl)
    art = json.loads((ref / "preflight-neutralize.json").read_text())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert art["status"] == "pass"
    assert "src/index.css" in art["injectedInto"]
    css = (impl / "src" / "styles" / "from-ref" / "preflight-neutralize.css").read_text()
    assert "font-weight: bold" in css
    entry = (impl / "src" / "index.css").read_text()
    # The @layer base block must land AFTER @tailwind base (so it beats Preflight).
    assert entry.index("@tailwind base;") < entry.index("@layer base"), entry
    assert "h1 { font-size: 2em; font-weight: bold" in entry


def test_preflight_injection_is_idempotent(tmp_path: Path) -> None:
    ref, impl = _impl_with_index_css(tmp_path, "@tailwind base;\n")
    _run(PREFLIGHT, ref, impl)
    _run(PREFLIGHT, ref, impl)
    entry = (impl / "src" / "index.css").read_text()
    assert entry.count("preflight-neutralize:start") == 1, entry
    art = json.loads((ref / "preflight-neutralize.json").read_text())
    assert "src/index.css" in art["alreadyPresent"]


def test_preflight_writes_css_even_without_tailwind_entry(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    impl = tmp_path / "impl"
    (impl / "src").mkdir(parents=True)  # no index.css with @tailwind base
    proc = _run(PREFLIGHT, ref, impl)
    art = json.loads((ref / "preflight-neutralize.json").read_text())
    assert proc.returncode == 0
    assert (impl / "src" / "styles" / "from-ref" / "preflight-neutralize.css").is_file()
    assert art["injectedInto"] == []


def test_parent_relative_font_url_is_transferred_to_its_resolved_root_path(
    tmp_path: Path,
) -> None:
    """A Next.js chunk stylesheet references its face as url(../media/x.woff2).
    The built impl serves that stylesheet from assets/, so the browser resolves
    the reference to /media/x.woff2. Declaring it unresolvable ships zero bytes
    for a face the ref genuinely loads — and because a dev server answers the
    miss with index.html at HTTP 200, nothing downstream ever sees a 404.
    """
    css = '@font-face { font-family: "Geist Mono"; src: url(../media/geist-latin.woff2); }'
    ref, impl = _font_ref(
        tmp_path, css,
        {"example.com/next/static/media/geist-latin.woff2": b"WOFF2-binary-bytes"},
    )
    proc = _run(TRANSFER, ref, impl)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (impl / "public" / "media" / "geist-latin.woff2").read_bytes() == (
        b"WOFF2-binary-bytes"
    )
    art = json.loads((ref / "font-transfer.json").read_text())
    assert not [s for s in art["skipped"] if "geist-latin" in s["url"]], art["skipped"]
