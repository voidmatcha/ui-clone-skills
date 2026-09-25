from __future__ import annotations

from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_auto_diagnose_recognizes_static_scroll_layout() -> None:
    """`auto-diagnose.sh` derives PARENT_DIR from a diff image path to decide
    how to scroll the page before probing (see CHANGELOG 0.8.14). For a
    percentage-crop diff at `static/diff/<N>pct.png`, PARENT_DIR is
    "static". `batch-scroll.sh` moved its captures to
    `static/scroll/{ref,impl,diff}` so it stops clobbering capture.sh's
    `static/ref/section-*.png` baseline (see batch-scroll.sh) — for that
    layout the same diff lives at `static/scroll/diff/<N>pct.png` and
    PARENT_DIR is "scroll", not "static". Without both branches recognized,
    a diff crop from the new layout silently loses its scroll-to-percentage
    positioning and the probe runs at scrollY=0 regardless of the real crop
    position.
    """
    script = (
        _project_root()
        / "skills"
        / "visual-debug"
        / "scripts"
        / "auto-diagnose.sh"
    ).read_text(encoding="utf-8")

    assert (
        'elif [ "$PARENT_DIR" = "static" ] || [ "$PARENT_DIR" = "scroll" ]; then'
        in script
    ), "PARENT_DIR check must accept both the legacy static/ and the static/scroll/ layout"
