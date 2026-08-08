import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "skills" / "visual-debug" / "scripts" / "lib" / "dynamic-selectors.sh"


def run_loader(tmp_path: Path, base: str = "canvas") -> str:
    script = f'''
set -euo pipefail
source "{LIB}"
DYNAMIC_SELECTORS={base!r}
DIR={str(tmp_path / "out")!r}
REF_ROOT_DIR={str(tmp_path / "ref")!r}
mkdir -p "$DIR" "$REF_ROOT_DIR"
load_section_dynamic_selectors_config
printf '%s' "$DYNAMIC_SELECTORS"
'''
    return subprocess.check_output(["bash", "-c", script], cwd=tmp_path, text=True)


def test_loads_project_visual_debug_selectors(tmp_path: Path) -> None:
    cfg_dir = tmp_path / ".visual-debug"
    cfg_dir.mkdir()
    (cfg_dir / "section-dynamic-selectors.txt").write_text(
        "\n".join([
            "# full-line comments are ignored",
            ".main-header .time",
            ".main-header .stock, .main-header .swiper",
            "",
            "header.header",
        ]),
        encoding="utf-8",
    )

    assert run_loader(tmp_path) == (
        "canvas, .main-header .time, .main-header .stock, "
        ".main-header .swiper, header.header"
    )


def test_explicit_selector_file_takes_precedence(tmp_path: Path) -> None:
    project_dir = tmp_path / ".visual-debug"
    project_dir.mkdir()
    (project_dir / "section-dynamic-selectors.txt").write_text(".project-only", encoding="utf-8")
    explicit = tmp_path / "custom-selectors.txt"
    explicit.write_text(".explicit-one\n.explicit-two", encoding="utf-8")

    script = f'''
set -euo pipefail
source "{LIB}"
DYNAMIC_SELECTORS='canvas'
DIR={str(tmp_path / "out")!r}
REF_ROOT_DIR={str(tmp_path / "ref")!r}
SECTION_DYNAMIC_SELECTORS_FILE={str(explicit)!r}
load_section_dynamic_selectors_config
printf '%s' "$DYNAMIC_SELECTORS"
'''
    result = subprocess.check_output(["bash", "-c", script], cwd=tmp_path, text=True)

    assert result == "canvas, .explicit-one, .explicit-two"


def test_section_compare_invokes_project_selector_loader_before_mask_injection() -> None:
    script = (ROOT / "skills" / "visual-debug" / "scripts" / "section-compare.sh").read_text(encoding="utf-8")

    source_idx = script.index('source "$SCRIPTS_DIR/lib/dynamic-selectors.sh"')
    load_idx = script.index("load_section_dynamic_selectors_config")
    inject_idx = script.index('DYNAMIC_PAUSE_EXTRA=""')
    quote_check_idx = script.index('DYNAMIC_SELECTORS must not contain quote characters')

    assert source_idx < load_idx < inject_idx < quote_check_idx


def test_loads_fixed_overlay_selectors_separately(tmp_path: Path) -> None:
    cfg_dir = tmp_path / ".visual-debug"
    cfg_dir.mkdir()
    (cfg_dir / "section-dynamic-selectors.txt").write_text(".dynamic-one", encoding="utf-8")
    (cfg_dir / "section-fixed-overlay-selectors.txt").write_text(
        "header.header\n.mo-nav", encoding="utf-8"
    )

    script = f'''
set -euo pipefail
source "{LIB}"
DYNAMIC_SELECTORS='canvas'
SECTION_FIXED_OVERLAY_SELECTORS=''
DIR={str(tmp_path / "out")!r}
REF_ROOT_DIR={str(tmp_path / "ref")!r}
mkdir -p "$DIR" "$REF_ROOT_DIR"
load_section_dynamic_selectors_config
printf 'dynamic=%s\nfixed=%s\n' "$DYNAMIC_SELECTORS" "$SECTION_FIXED_OVERLAY_SELECTORS"
'''
    result = subprocess.check_output(["bash", "-c", script], cwd=tmp_path, text=True)

    assert result == "dynamic=canvas, .dynamic-one\nfixed=header.header, .mo-nav\n"


def test_section_compare_masks_fixed_overlays_only_after_scroll() -> None:
    script = (ROOT / "skills" / "visual-debug" / "scripts" / "section-compare.sh").read_text(encoding="utf-8")

    assert "SECTION_FIXED_OVERLAY_SELECTORS" in script
    assert 'html[data-section-compare-scrolled=1] ${SECTION_FIXED_OVERLAY_SELECTORS}' in script
    assert "document.documentElement.setAttribute('data-section-compare-scrolled', ($y > 0 ? '1' : '0'))" in script


def test_section_capture_sets_scrolled_attribute_for_fixed_overlay_masks() -> None:
    source = (ROOT / "ui_clone" / "section_capture.py").read_text(encoding="utf-8")

    assert "data-section-compare-scrolled" in source
    assert "({y} > 0 ? '1' : '0')" in source


def test_section_compare_forwards_fixed_overlay_selectors_to_capture_helper() -> None:
    script = (ROOT / "skills" / "visual-debug" / "scripts" / "section-compare.sh").read_text(encoding="utf-8")

    assert 'SECTION_CAPTURE_FIXED_OVERLAY_SELECTORS="${SECTION_FIXED_OVERLAY_SELECTORS:-}"' in script


def test_loads_section_ignore_selectors_separately(tmp_path: Path) -> None:
    cfg_dir = tmp_path / ".visual-debug"
    cfg_dir.mkdir()
    (cfg_dir / "section-ignore-selectors.txt").write_text(".mo-nav\n", encoding="utf-8")

    script = f'''
set -euo pipefail
source "{LIB}"
DYNAMIC_SELECTORS='canvas'
SECTION_IGNORE_SELECTORS=''
DIR={str(tmp_path / "out")!r}
REF_ROOT_DIR={str(tmp_path / "ref")!r}
mkdir -p "$DIR" "$REF_ROOT_DIR"
load_section_dynamic_selectors_config
printf 'ignore=%s\n' "$SECTION_IGNORE_SELECTORS"
'''
    result = subprocess.check_output(["bash", "-c", script], cwd=tmp_path, text=True)

    assert result == "ignore=.mo-nav\n"


def test_section_compare_hides_ignore_selectors_before_enumeration() -> None:
    script = (ROOT / "skills" / "visual-debug" / "scripts" / "section-compare.sh").read_text(encoding="utf-8")

    assert "SECTION_IGNORE_SELECTORS" in script
    assert "${SECTION_IGNORE_SELECTORS} { display: none !important; }" in script
