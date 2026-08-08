"""Capture-readiness signal: cross-artifact orphan-image detection.

Root cause under test (loop-claude-ebay): extract-dom.sh snapshots the reused
agent-browser session with no readiness gate, so a transient pre-settle / error
frame yields a structure.json missing the main content region — while
extract-asset-metadata.sh, running later against the SAME session after it
settled, records the rendered images (visibleImages) with positions. The two
artifacts disagree and the scaffold is built from the impoverished DOM.

The orphan-image count is the only signal empirically shown to fire on this case
(46/48 on the eBay capture) and is fully site-agnostic — no brand strings, no
selectors: it counts positioned images the page rendered whose distinctive source
token is absent from the DOM snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path

from ui_clone.capture_readiness import (
    _distinctive_token,
    orphan_image_count,
    readiness_verdict,
    score_capture,
)


def test_distinctive_token_prefers_cdn_hash_over_shared_filename() -> None:
    """eBay serves every product from the SAME filename (s-l1600.webp); the
    identifying token is the /images/g/<hash>/ path segment, so a bare-filename
    match would false-match across products. The distinctive token must be the
    long CDN hash, not the shared filename stem."""
    a = _distinctive_token("https://i.ebayimg.com/images/g/s9IAAeSwesFpusZk/s-l1600.webp")
    b = _distinctive_token("https://i.ebayimg.com/images/g/t1cAAeSwjwRpusMM/s-l1600.webp")
    assert a == "s9IAAeSwesFpusZk", a
    assert b == "t1cAAeSwjwRpusMM", b
    assert a != b  # different products -> different tokens


def test_distinctive_token_skips_data_uri_and_tokenless() -> None:
    assert _distinctive_token("data:image/png;base64,AAAA") is None
    assert _distinctive_token("") is None
    assert _distinctive_token(None) is None


def _ebay_structure_missing_grid() -> dict:
    """A DOM snapshot caught in the error frame: only tracking pixels, no product
    images (the 46 i.ebayimg product images are absent)."""
    return {
        "tag": "body",
        "children": [
            {"tag": "img", "src": "https://ir.ebaystatic.com/cr/v/c1/1x1.gif", "children": []},
            {"tag": "main", "children": [
                {"tag": "section", "class": "section-notice", "children": [
                    {"tag": "p", "text": "Something went wrong. Try again.", "children": []},
                ]},
            ]},
        ],
    }


def _ebay_visible_images(n: int = 3) -> list:
    hashes = ["s9IAAeSwesFpusZk", "t1cAAeSwjwRpusMM", "5-8AAeSwpvJotaqH", "PkIAAeSwZ05otaqK"]
    return [
        {"src": f"https://i.ebayimg.com/images/g/{h}/s-l1600.webp",
         "top": 161 + i * 80, "left": 6 + i * 300, "width": 210, "height": 210}
        for i, h in enumerate(hashes[:n])
    ]


def test_orphan_count_fires_on_error_frame_capture() -> None:
    """The whole product grid is missing from the DOM snapshot -> every rendered
    product image is an orphan. This is the eBay failure mode."""
    orphan, total = orphan_image_count(_ebay_structure_missing_grid(), _ebay_visible_images(3))
    assert (orphan, total) == (3, 3)


def test_orphan_count_zero_when_grid_captured() -> None:
    """When the DOM snapshot DID capture the grid, each rendered image's hash token
    is present in structure.json -> zero orphans, no false alarm."""
    vi = _ebay_visible_images(3)
    structure = {"tag": "body", "children": [
        {"tag": "img", "src": img["src"], "children": []} for img in vi
    ]}
    orphan, total = orphan_image_count(structure, vi)
    assert (orphan, total) == (0, 3)


def test_orphan_count_excludes_data_uri_and_tokenless() -> None:
    """Cross-uncheckable entries (data URIs, token-less srcs) are excluded from the
    denominator entirely — they can neither confirm nor deny capture."""
    vi = [
        {"src": "data:image/png;base64,AAAA"},
        {"src": "https://i.ebayimg.com/images/g/s9IAAeSwesFpusZk/s-l1600.webp"},
    ]
    orphan, total = orphan_image_count(_ebay_structure_missing_grid(), vi)
    assert total == 1  # data-uri dropped
    assert orphan == 1


def test_verdict_degraded_on_majority_orphan() -> None:
    v = readiness_verdict(orphan=46, total=48)
    assert v["status"] == "degraded"
    assert v["needsResnapshot"] is True
    assert v["orphanImages"] == 46
    assert v["checkableImages"] == 48


def test_verdict_ok_when_no_orphans() -> None:
    v = readiness_verdict(orphan=0, total=30)
    assert v["status"] == "ok"
    assert v["needsResnapshot"] is False


def test_verdict_min_orphan_floor_avoids_thin_site_noise() -> None:
    """A couple of orphans on a tiny page (lazy/virtualized edge cases) must NOT
    trip the recovery loop: require an absolute floor as well as a majority."""
    v = readiness_verdict(orphan=1, total=2)  # ratio 0.5 but only 1 missing
    assert v["status"] == "ok"
    assert v["needsResnapshot"] is False


def test_verdict_empty_is_ok_not_degraded() -> None:
    """No cross-checkable images -> we cannot judge; must not loop blindly."""
    v = readiness_verdict(orphan=0, total=0)
    assert v["status"] == "ok"
    assert v["needsResnapshot"] is False


def test_score_capture_reads_artifacts(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(_ebay_structure_missing_grid()))
    (ref / "extracted.json").write_text(json.dumps({"visibleImages": _ebay_visible_images(4)}))
    v = score_capture(ref)
    assert v["status"] == "degraded"
    assert v["needsResnapshot"] is True
    assert v["orphanImages"] == 4


def test_score_capture_reads_visible_images_at_phase_2_5(tmp_path: Path) -> None:
    """Regression for loop-claude-ebay-F-1: at the Phase 2.5 recovery consult point
    only ``visible-images.json`` (key ``images``) exists — ``extracted.json`` is
    assembled ~45s later at Step 6b. score_capture MUST read the rendered images from
    ``visible-images.json`` and return the real verdict, not 'unknown'. The pre-fix
    code read only ``extracted.json`` and so returned 'unknown' on every run, making
    the recovery loop dead code."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(_ebay_structure_missing_grid()))
    # No extracted.json yet — exactly the Phase 2.5 state.
    (ref / "visible-images.json").write_text(
        json.dumps({"schemaVersion": 1, "images": _ebay_visible_images(4)})
    )
    v = score_capture(ref)
    assert v["status"] == "degraded", v
    assert v["needsResnapshot"] is True
    assert v["orphanImages"] == 4
    assert v["checkableImages"] == 4


def test_score_capture_prefers_visible_images_over_extracted(tmp_path: Path) -> None:
    """When both exist, the early artifact (visible-images.json) is used — it is the
    one guaranteed present during recovery."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(_ebay_structure_missing_grid()))
    (ref / "visible-images.json").write_text(
        json.dumps({"images": _ebay_visible_images(4)})
    )
    (ref / "extracted.json").write_text(json.dumps({"visibleImages": _ebay_visible_images(2)}))
    v = score_capture(ref)
    assert v["checkableImages"] == 4  # from visible-images.json, not extracted's 2


def test_score_capture_structure_without_image_source_is_unknown(tmp_path: Path) -> None:
    """structure.json present but NO rendered-image artifact -> cannot cross-check ->
    'unknown', never resnapshot."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "structure.json").write_text(json.dumps(_ebay_structure_missing_grid()))
    v = score_capture(ref)
    assert v["status"] == "unknown"
    assert v["needsResnapshot"] is False


def test_score_capture_missing_artifacts_is_unknown(tmp_path: Path) -> None:
    """Absent/unreadable artifacts -> 'unknown', never resnapshot (we cannot
    cross-check, so looping would be blind)."""
    v = score_capture(tmp_path / "nope")
    assert v["status"] == "unknown"
    assert v["needsResnapshot"] is False
