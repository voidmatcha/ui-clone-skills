"""ui_clone.scroll_chunk_manifest — resume manifest for the chunked scroll-
position recording sweep (batch-4 item 2, stall-exposure reduction).

The scroll-mode video-motion sweep captures N+1 position screenshots per side;
a single >8min invocation that loses its completion wake-up loses the whole
sweep (2 confirmed stall incidents). Splitting the sweep into <8min,
idempotent position-group chunks with a persisted manifest means a re-invocation
resumes from the manifest and re-captures at most one in-flight chunk. The
comparison still reads every persisted frame, so the verdict is identical to the
monolithic run.
"""

from __future__ import annotations

import json
from pathlib import Path

from ui_clone.scroll_chunk_manifest import (
    is_side_complete,
    load_manifest,
    next_chunk,
    record_captured,
    resumable,
    save_manifest,
)

_REPO = Path(__file__).resolve().parent.parent
_VTC = _REPO / "scripts" / "verify" / "video-transition-compare.sh"


def _touch_frames(frames_dir: Path, indices: list[int]) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in indices:
        (frames_dir / f"pos-{i:03d}.png").write_bytes(b"x")


def test_first_chunk_is_leading_positions(tmp_path: Path) -> None:
    m = load_manifest(tmp_path / "m.json", samples=10)
    chunk = next_chunk(m, "ref", chunk_size=4, frames_dir=tmp_path / "ref")
    assert chunk == [0, 1, 2, 3]


def test_resume_skips_recorded_positions(tmp_path: Path) -> None:
    frames = tmp_path / "ref"
    _touch_frames(frames, [0, 1, 2, 3])
    m = load_manifest(tmp_path / "m.json", samples=10)
    for i in (0, 1, 2, 3):
        record_captured(m, "ref", i)
    chunk = next_chunk(m, "ref", chunk_size=4, frames_dir=frames)
    assert chunk == [4, 5, 6, 7]


def test_recorded_but_missing_frame_is_recaptured(tmp_path: Path) -> None:
    # Idempotence: a manifest entry whose frame was lost (interrupted write)
    # must be re-captured, never trusted blindly.
    frames = tmp_path / "ref"
    frames.mkdir()
    m = load_manifest(tmp_path / "m.json", samples=10)
    record_captured(m, "ref", 0)  # recorded but no frame on disk
    chunk = next_chunk(m, "ref", chunk_size=4, frames_dir=frames)
    assert 0 in chunk


def test_side_complete_when_all_positions_captured(tmp_path: Path) -> None:
    frames = tmp_path / "ref"
    _touch_frames(frames, list(range(11)))  # 0..10 inclusive
    m = load_manifest(tmp_path / "m.json", samples=10)
    for i in range(11):
        record_captured(m, "ref", i)
    assert is_side_complete(m, "ref", samples=10, frames_dir=frames)
    assert next_chunk(m, "ref", chunk_size=4, frames_dir=frames) == []


def test_manifest_round_trips_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    m = load_manifest(path, samples=10)
    record_captured(m, "ref", 0)
    record_captured(m, "impl", 0)
    save_manifest(path, m)
    reloaded = load_manifest(path, samples=10)
    assert 0 in reloaded["ref"]
    assert 0 in reloaded["impl"]
    assert reloaded["samples"] == 10


def test_samples_mismatch_resets_manifest(tmp_path: Path) -> None:
    # A stale manifest from a different SCROLL_SAMPLES must not silently reuse
    # partial state (the position fractions differ) — resetting forces a clean
    # recapture rather than a verdict that mixes two sample grids.
    path = tmp_path / "m.json"
    m = load_manifest(path, samples=10)
    record_captured(m, "ref", 5)
    save_manifest(path, m)
    reloaded = load_manifest(path, samples=20)
    assert reloaded["ref"] == []
    assert reloaded["samples"] == 20


# ── replay-attack guard (batch-4 review MAJOR 1) ─────────────────────────────
#
# Keying resume identity on `samples` alone lets fully-captured pos-*.png frames
# from a PRIOR url/viewport/action/mask run with the same sample count be reused
# (a replay attack: a verdict minted from another page's frames). The manifest
# stores+validates a run-identity (orig/impl URL, viewport, action, mask-selector
# hash, script version); a mismatch resets the manifest so the stale frames are
# re-captured, never trusted.


def test_identity_mismatch_resets_manifest(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    m = load_manifest(path, samples=10, identity="https://a|https://x|1440x900|scroll|h1|v1")
    record_captured(m, "ref", 5)
    save_manifest(path, m)
    # same sample count, DIFFERENT run identity (e.g. a different impl URL)
    reloaded = load_manifest(path, samples=10, identity="https://a|https://y|1440x900|scroll|h1|v1")
    assert reloaded["ref"] == [], "stale partial state from a different run must be reset"
    assert reloaded["identity"] == "https://a|https://y|1440x900|scroll|h1|v1"


def test_identity_match_preserves_state(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    ident = "https://a|https://x|1440x900|scroll|h1|v1"
    m = load_manifest(path, samples=10, identity=ident)
    record_captured(m, "ref", 5)
    save_manifest(path, m)
    reloaded = load_manifest(path, samples=10, identity=ident)
    assert 5 in reloaded["ref"], "matching identity must resume from the manifest"


def test_identity_none_is_backward_compatible(tmp_path: Path) -> None:
    # Legacy callers that pass no identity keep the samples-only behavior.
    path = tmp_path / "m.json"
    m = load_manifest(path, samples=10)
    record_captured(m, "ref", 5)
    save_manifest(path, m)
    reloaded = load_manifest(path, samples=10)
    assert 5 in reloaded["ref"]


def test_replay_attack_different_identity_not_resumable(tmp_path: Path) -> None:
    """The exact replay attack: a PRIOR run (url X) fully captured all positions
    and left its frames + manifest. A NEW run (url Y, same sample count) must NOT
    be resumable — else it mints a verdict from url X's frames."""
    path = tmp_path / "m.json"
    frames = tmp_path / "ref"
    _touch_frames(frames, list(range(11)))
    id_x = "https://a|https://x|1440x900|scroll|h1|v1"
    id_y = "https://a|https://y|1440x900|scroll|h1|v1"
    m = load_manifest(path, samples=10, identity=id_x)
    for i in range(11):
        record_captured(m, "ref", i)
    save_manifest(path, m)
    assert resumable(path, samples=10, identity=id_x) is True
    assert resumable(path, samples=10, identity=id_y) is False
    # and loading under the new identity wipes the stale capture record
    m2 = load_manifest(path, samples=10, identity=id_y)
    assert m2["ref"] == []
    assert is_side_complete(m2, "ref", samples=10, frames_dir=frames) is False


def test_resumable_false_on_samples_or_missing(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    ident = "https://a|https://x|1440x900|scroll|h1|v1"
    assert resumable(path, samples=10, identity=ident) is False  # no manifest yet
    m = load_manifest(path, samples=10, identity=ident)
    record_captured(m, "ref", 0)
    save_manifest(path, m)
    assert resumable(path, samples=10, identity=ident) is True
    assert resumable(path, samples=20, identity=ident) is False  # samples changed


def test_script_wires_run_identity_into_scroll_resume() -> None:
    body = _VTC.read_text(encoding="utf-8")
    assert "SCROLL_RUN_IDENTITY" in body, "scroll resume must build a run identity"
    assert "resumable" in body, "resume must validate run identity, not just sample count"
    # the identity is persisted via the record checkpoint; frames_dir is passed
    # so the per-frame content fingerprint can be recorded (batch-6 ITEM 6).
    assert 'record "$SCROLL_CHUNK_MANIFEST" "$label" "$SCROLL_SAMPLES" "$i" "$frames_dir" "$SCROLL_RUN_IDENTITY"' in body


# ── per-frame content fingerprint (tools batch-6 ITEM 6) ─────────────────────
# Completeness (delete/truncate/forged-claim) is already sound — disk existence
# +size is authoritative. The remaining hole: the manifest bound existence/size
# but NOT content, so a frame overwritten with different nonzero bytes (B1d)
# minted a green verdict. Record a per-frame content hash at capture and verify
# it, so a content-swapped frame no longer counts as captured.


def test_content_swapped_frame_is_recaptured(tmp_path: Path) -> None:
    frames = tmp_path / "ref"
    frames.mkdir()
    (frames / "pos-005.png").write_bytes(b"original-frame-bytes-aaaa")
    m = load_manifest(tmp_path / "m.json", samples=10)
    record_captured(m, "ref", 5, frames_dir=frames)  # records the original hash
    # swap the frame content (same path + size class, different nonzero bytes)
    (frames / "pos-005.png").write_bytes(b"DIFFERENT-frame-bytes-zz")
    chunk = next_chunk(m, "ref", chunk_size=11, frames_dir=frames)
    assert 5 in chunk, "content-swapped frame must be re-pending, not trusted"
    assert not is_side_complete(m, "ref", samples=10, frames_dir=frames)


def test_unswapped_frame_stays_captured(tmp_path: Path) -> None:
    frames = tmp_path / "ref"
    frames.mkdir()
    (frames / "pos-005.png").write_bytes(b"original-frame-bytes-aaaa")
    m = load_manifest(tmp_path / "m.json", samples=10)
    record_captured(m, "ref", 5, frames_dir=frames)
    assert 5 not in next_chunk(m, "ref", chunk_size=11, frames_dir=frames)


def test_frame_hashes_round_trip_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    frames = tmp_path / "ref"
    frames.mkdir()
    (frames / "pos-000.png").write_bytes(b"frame-zero")
    m = load_manifest(path, samples=10)
    record_captured(m, "ref", 0, frames_dir=frames)
    save_manifest(path, m)
    reloaded = load_manifest(path, samples=10)
    # the recorded hash survives a reload, so the swap check holds across the
    # multi-invocation resume path
    (frames / "pos-000.png").write_bytes(b"swapped-after-reload")
    assert 0 in next_chunk(reloaded, "ref", chunk_size=11, frames_dir=frames)


# ── tools batch-7 ITEM 6: gate-side verdict-integrity ─────────────────────
# The GATE re-derives count + distinctness FROM DISK (manifest hashes advisory)
# and binds resume to a live page-region digest. Recreates /tmp/adv2-vmc e2e2
# (partial-run padding) and e2e3 (identity spoof / cross-page replay).

from ui_clone.scroll_chunk_manifest import resume_allowed, verify_side  # noqa: E402


def _distinct_frames(frames_dir: Path, n: int) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n + 1):
        (frames_dir / f"pos-{i:03d}.png").write_bytes(b"frame-" + bytes([i]) + b"-distinct")


def test_duplicate_frame_padding_fails_verify_side(tmp_path: Path) -> None:
    frames = tmp_path / "ref"
    frames.mkdir()
    (frames / "pos-000.png").write_bytes(b"A")
    (frames / "pos-001.png").write_bytes(b"B")
    for i in range(2, 11):  # 9 identical padded frames across distinct positions
        (frames / f"pos-{i:03d}.png").write_bytes(b"DUP")
    # forged self-consistent frameHashes must NOT help — verify_side re-derives
    # from disk and catches the duplicate regardless of what the manifest claims.
    manifest = {"samples": 10, "frameHashes": {"ref": {str(i): "forged" for i in range(11)}}}
    ok, reason = verify_side(frames, manifest, "ref", 10)
    assert ok is False, reason
    assert "padding" in reason or "duplicate" in reason


def test_distinct_frames_pass_verify_side(tmp_path: Path) -> None:
    frames = tmp_path / "ref"
    _distinct_frames(frames, 10)
    ok, reason = verify_side(frames, {"samples": 10}, "ref", 10)
    assert ok is True, reason


def test_verify_side_count_short_fails(tmp_path: Path) -> None:
    frames = tmp_path / "ref"
    _distinct_frames(frames, 5)  # only 0..5 of expected 0..10
    ok, reason = verify_side(frames, {"samples": 10}, "ref", 10)
    assert ok is False, reason
    assert "count" in reason


def test_non_scrolling_page_duplicates_allowed(tmp_path: Path) -> None:
    # a page with no scroll range clamps every scrollTo to top => identical
    # frames are legitimate; distinctness is waived (false-positive guard).
    frames = tmp_path / "ref"
    frames.mkdir()
    for i in range(11):
        (frames / f"pos-{i:03d}.png").write_bytes(b"SAME")
    ok, reason = verify_side(frames, {"samples": 10, "hasScrollRange": False}, "ref", 10)
    assert ok is True, reason


def test_padding_at_distinct_offsets_still_fails(tmp_path: Path) -> None:
    # batch-8 minor: identical frames at genuinely DISTINCT achieved scroll
    # offsets on a scrollable page is real partial-run padding — must still fail
    # even with scrollPositions metadata present.
    frames = tmp_path / "ref"
    frames.mkdir()
    (frames / "pos-000.png").write_bytes(b"A")
    (frames / "pos-001.png").write_bytes(b"B")
    for i in range(2, 11):
        (frames / f"pos-{i:03d}.png").write_bytes(b"DUP")
    manifest = {
        "samples": 10, "hasScrollRange": True,
        "scrollPositions": {"ref": {str(i): i * 200 for i in range(11)}},
    }
    ok, reason = verify_side(frames, manifest, "ref", 10)
    assert ok is False, reason
    assert "padding" in reason or "duplicate" in reason


def test_static_sticky_region_at_same_offset_passes(tmp_path: Path) -> None:
    # batch-8 minor: a long scrollable page whose sticky region does NOT move
    # across a span of positions produces byte-identical frames at the SAME
    # achieved scroll offset — legitimate, not padding. Pre-fix this false-failed
    # on the >=3-duplicate rule regardless of offsets.
    frames = tmp_path / "ref"
    frames.mkdir()
    (frames / "pos-000.png").write_bytes(b"TOP")
    (frames / "pos-001.png").write_bytes(b"MID")
    for i in range(2, 11):  # sticky region: identical pixels...
        (frames / f"pos-{i:03d}.png").write_bytes(b"STICKY")
    manifest = {
        "samples": 10, "hasScrollRange": True,
        # ...because the achieved scrollY is clamped/identical across pos 2..10
        "scrollPositions": {"ref": {str(i): (i * 100 if i < 2 else 1000) for i in range(11)}},
    }
    ok, reason = verify_side(frames, manifest, "ref", 10)
    assert ok is True, reason


def test_resume_requires_matching_live_region_digest(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    ident = "https://x|https://x|1440x900|scroll||v1"
    path.write_text(json.dumps({
        "samples": 10, "ref": [], "impl": [], "identity": ident, "regionDigest": "pageX",
    }), encoding="utf-8")
    # EVASION 3: identity matches but the live page (Y) digest differs.
    assert resume_allowed(path, 10, ident, "pageY") is False
    assert resume_allowed(path, 10, ident, "pageX") is True
    assert resume_allowed(path, 10, ident, None) is True  # legacy identity-only
    assert resume_allowed(path, 10, "other-identity", "pageX") is False


def test_script_wires_verdict_integrity_gate() -> None:
    body = _VTC.read_text(encoding="utf-8")
    # the verdict re-derives count + distinctness from disk before SSIM
    assert 'vmc_manifest_py verify "$SCROLL_CHUNK_MANIFEST"' in body
    # resume is bound to a live page-region digest (EVASION 3)
    assert "LIVE_REGION_DIGEST" in body
    assert 'resumable "$SCROLL_CHUNK_MANIFEST" "$SCROLL_SAMPLES" "$SCROLL_RUN_IDENTITY" "$LIVE_REGION_DIGEST"' in body
    assert "regionDigest" in body and "hasScrollRange" in body
    assert 'performance.getEntriesByType("resource")' in body
