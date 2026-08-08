"""Resume manifest for the chunked scroll-position recording sweep.

Batch-4 item 2 (stall-exposure reduction). The scroll-mode video-motion sweep
records N+1 position screenshots per side (ref + impl). A single recording
invocation that exceeds ~8 minutes and then loses its background-shell
completion wake-up loses the whole sweep (two confirmed stall incidents;
mechanism: completion event -> failed API re-invocation -> no retry).

Splitting the sweep into <8min, idempotent position-group chunks with a
persisted manifest bounds the exposure: a re-invocation resumes from the
manifest and re-captures at most one in-flight chunk. Frames are the persisted
chunk artifacts; the manifest records which positions are captured. The
comparison still reads EVERY position frame, so the aggregated verdict is byte-
identical to the monolithic run — chunking changes only HOW frames are captured,
never WHAT is compared.

Idempotence: a manifest entry is trusted only when its frame is actually on
disk; a recorded-but-missing frame (interrupted write) is re-captured.

CLI (driven by video-transition-compare.sh):
    python -m ui_clone.scroll_chunk_manifest next  <manifest> <side> <samples> <chunk> <frames-dir>
        -> prints the next chunk's position indices (space-separated; empty when done)
    python -m ui_clone.scroll_chunk_manifest record <manifest> <side> <samples> <index> <frames-dir> [identity]
    python -m ui_clone.scroll_chunk_manifest complete <manifest> <side> <samples> <frames-dir>
        -> exit 0 when the side is fully captured, 1 otherwise
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

DEFAULT_CHUNK_POSITIONS = 6
_SIDES = ("ref", "impl")


def load_manifest(path: Path, samples: int, identity: str | None = None) -> dict[str, Any]:
    """Load the manifest, resetting it when the sample grid OR the run identity
    changed. A stale manifest from a different SCROLL_SAMPLES would mix two
    position grids; a stale manifest from a different run (orig/impl URL,
    viewport, action, mask selectors, script version — replay-attack class,
    batch-4 review MAJOR 1) would mint a verdict from another page's frames.
    Either mismatch forces a clean recapture. identity=None keeps the legacy
    samples-only behavior for callers that do not supply one."""
    data: dict[str, Any] = {}
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, json.JSONDecodeError):
        data = {}
    stale = data.get("samples") != samples or (
        identity is not None and data.get("identity") != identity
    )
    if stale:
        data = {"samples": samples, "ref": [], "impl": []}
    for side in _SIDES:
        vals = data.get(side)
        data[side] = sorted({int(v) for v in vals}) if isinstance(vals, list) else []
    data["samples"] = samples
    if identity is not None:
        data["identity"] = identity
    return data


def resumable(path: Path, samples: int, identity: str) -> bool:
    """True iff the on-disk manifest exists and matches BOTH the sample grid and
    the run identity — i.e. a re-invocation may keep the persisted pos-*.png
    frames. False (caller must wipe stale frames + start fresh) when the manifest
    is missing, unreadable, from a different sample grid, or from a different run
    (the replay-attack guard: another page's complete frames must never be
    reused just because the position count matches)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return data.get("samples") == samples and data.get("identity") == identity


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    for side in _SIDES:
        manifest[side] = sorted(set(manifest.get(side, [])))
    Path(path).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _frame_exists(frames_dir: Path, index: int) -> bool:
    f = Path(frames_dir) / f"pos-{index:03d}.png"
    try:
        return f.is_file() and f.stat().st_size > 0
    except OSError:
        return False


def _frame_hash(frames_dir: Path, index: int) -> str | None:
    """Content fingerprint of a position frame (not security-sensitive)."""
    f = Path(frames_dir) / f"pos-{index:03d}.png"
    try:
        return hashlib.sha256(f.read_bytes()).hexdigest()
    except OSError:
        return None


def _recorded_hash(manifest: dict[str, Any], side: str, index: int) -> str | None:
    fh = manifest.get("frameHashes")
    if not isinstance(fh, dict):
        return None
    side_map = fh.get(side)
    if not isinstance(side_map, dict):
        return None
    val = side_map.get(str(int(index)))
    return val if isinstance(val, str) else None


def record_captured(
    manifest: dict[str, Any],
    side: str,
    index: int,
    frames_dir: Path | None = None,
    scroll_pos: int | None = None,
) -> None:
    bucket = manifest.setdefault(side, [])
    if index not in bucket:
        bucket.append(int(index))
        bucket.sort()
    # Achieved scroll offset per frame (batch-8 minor): lets verify_side tell a
    # sticky/static region (identical frames at the SAME offset) from genuine
    # partial-run padding (identical frames at DISTINCT offsets).
    if scroll_pos is not None:
        sp = manifest.setdefault("scrollPositions", {})
        if not isinstance(sp, dict):
            sp = manifest["scrollPositions"] = {}
        sp_side = sp.setdefault(side, {})
        if not isinstance(sp_side, dict):
            sp_side = sp[side] = {}
        sp_side[str(int(index))] = int(scroll_pos)
    # Per-frame content fingerprint (batch-6 ITEM 6): record the captured
    # frame's content hash so a later content-swap (B1d: overwrite pos-NNN.png
    # with different nonzero bytes) no longer counts as captured. Recorded at
    # capture time, when the frame is authoritative.
    if frames_dir is not None:
        h = _frame_hash(frames_dir, index)
        if h is not None:
            fh = manifest.setdefault("frameHashes", {})
            if not isinstance(fh, dict):
                fh = manifest["frameHashes"] = {}
            side_map = fh.setdefault(side, {})
            if not isinstance(side_map, dict):
                side_map = fh[side] = {}
            side_map[str(int(index))] = h


def _captured(manifest: dict[str, Any], side: str, index: int, frames_dir: Path) -> bool:
    if index not in set(manifest.get(side, [])):
        return False
    if not _frame_exists(frames_dir, index):
        return False
    # When a content fingerprint was recorded at capture, the on-disk frame must
    # still match it — a swapped frame is treated as uncaptured (re-pending).
    recorded = _recorded_hash(manifest, side, index)
    if recorded is not None and _frame_hash(frames_dir, index) != recorded:
        return False
    return True


# ── verdict-integrity (batch-7 ITEM 6) ─────────────────────────────────
# The manifest is a self-vouching artifact: frameHashes/identity/membership are
# all checked against OTHER FIELDS IN THE SAME FILE. The GATE must instead
# re-establish the facts from disk + a live authority. verify_side re-derives
# count + per-frame fingerprints ITSELF (never trusting the manifest's recorded
# hashes) and rejects duplicate-frame padding; resume_allowed binds resume to a
# LIVE page-region digest so a prior run's frames cannot be relabeled.


def verify_side(
    frames_dir: Path, manifest: dict[str, Any], side: str, samples: int
) -> tuple[bool, str]:
    """Independently verify one side's frames FROM DISK (manifest hashes are a
    cache hint, never the verdict basis):
      (a) exactly samples+1 nonzero frames exist (count == expected positions);
      (b) frames DIFFER across distinct scroll positions — the SAME content
          spanning >=3 distinct positions is partial-run padding (EVASION 2).
    Distinctness is waived when the page has no scroll range (hasScrollRange
    false / maxScroll 0), where every window.scrollTo clamps to top and frames
    are legitimately identical (documented false-positive guard). When per-frame
    achieved scroll offsets are recorded (``scrollPositions``, batch-8 minor),
    identical frames are only padding when they sit at DISTINCT offsets — a
    sticky/pinned region that legitimately does not move across a span of
    positions (same achieved offset) is not failed.
    """
    fd = Path(frames_dir)
    n = int(samples)
    for i in range(n + 1):
        if not _frame_exists(fd, i):
            return False, f"count: pos-{i:03d} missing or empty (expected {n + 1} frames)"
    has_range = bool(manifest.get("hasScrollRange", True)) if isinstance(manifest, dict) else True
    if has_range and n >= 1:
        by_hash: dict[str, list[int]] = {}
        for i in range(n + 1):
            h = _frame_hash(fd, i)
            if h is None:
                return False, f"unreadable frame pos-{i:03d}"
            by_hash.setdefault(h, []).append(i)
        positions = manifest.get("scrollPositions") if isinstance(manifest, dict) else None
        side_pos = positions.get(side) if isinstance(positions, dict) else None

        def _distinct_offsets(idxs: list[int]) -> bool:
            # No recorded offsets => fall back to legacy index-distinctness
            # (still catches padding; preserves behaviour for older manifests).
            if not isinstance(side_pos, dict):
                return True
            seen: set[int] = set()
            for i in idxs:
                v = side_pos.get(str(i))
                if not isinstance(v, int | float) or isinstance(v, bool):
                    return True  # missing metadata => cannot prove static, treat distinct
                seen.add(round(float(v)))
            return len(seen) >= 2  # identical frames AT distinct offsets => padding

        dupes = [ps for ps in by_hash.values() if len(ps) >= 3 and _distinct_offsets(ps)]
        if dupes:
            worst = max(dupes, key=len)
            return False, (
                "duplicate frame content across distinct positions "
                f"{worst} — partial-run padding, not a real sweep"
            )
    return True, "ok"


def resume_allowed(
    path: Path, samples: int, identity: str, live_region_digest: str | None = None
) -> bool:
    """Whether persisted frames may be reused without re-capture. Requires the
    sample grid + run identity to match (resumable) AND, when a LIVE page-region
    digest is supplied, that it equals the manifest's stored regionDigest — a
    value that cannot be reproduced without actually visiting the target page,
    so a prior run's frames labeled with another page's identity (EVASION 3)
    fail the live-digest check and force a re-capture. live_region_digest=None
    keeps the legacy identity-only behaviour for callers that do not supply one.
    """
    if not resumable(path, samples, identity):
        return False
    if live_region_digest is None:
        return True
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("regionDigest") == live_region_digest


def pending_positions(
    manifest: dict[str, Any], side: str, frames_dir: Path
) -> list[int]:
    samples = int(manifest.get("samples", 0))
    return [
        i for i in range(samples + 1) if not _captured(manifest, side, i, frames_dir)
    ]


def next_chunk(
    manifest: dict[str, Any], side: str, chunk_size: int, frames_dir: Path
) -> list[int]:
    pending = pending_positions(manifest, side, frames_dir)
    size = max(1, int(chunk_size))
    return pending[:size]


def is_side_complete(
    manifest: dict[str, Any], side: str, samples: int, frames_dir: Path
) -> bool:
    return not pending_positions({**manifest, "samples": samples}, side, frames_dir)


def main(argv: list[str] | None = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    # next/record/complete take an OPTIONAL trailing run-identity arg (the
    # replay-attack guard); a missing identity keeps the legacy samples-only
    # behavior so older call sites still work.
    if args[:1] == ["next"] and len(args) in (6, 7):
        _, manifest_path, side, samples_s, chunk_s, frames_dir = args[:6]
        identity = args[6] if len(args) == 7 else None
        m = load_manifest(Path(manifest_path), int(samples_s), identity)
        print(" ".join(str(i) for i in next_chunk(m, side, int(chunk_s), Path(frames_dir))))
        return 0
    if args[:1] == ["record"] and len(args) in (6, 7, 8):
        _, manifest_path, side, samples_s, index_s, frames_dir = args[:6]
        identity = args[6] if len(args) >= 7 else None
        scroll_pos = int(args[7]) if len(args) == 8 else None
        m = load_manifest(Path(manifest_path), int(samples_s), identity)
        record_captured(m, side, int(index_s), Path(frames_dir), scroll_pos=scroll_pos)
        save_manifest(Path(manifest_path), m)
        return 0
    if args[:1] == ["complete"] and len(args) in (5, 6):
        _, manifest_path, side, samples_s, frames_dir = args[:5]
        identity = args[5] if len(args) == 6 else None
        m = load_manifest(Path(manifest_path), int(samples_s), identity)
        return 0 if is_side_complete(m, side, int(samples_s), Path(frames_dir)) else 1
    if args[:1] == ["resumable"] and len(args) in (4, 5):
        # optional trailing live-region digest binds resume to the live page
        # (batch-7 ITEM 6) — a prior run's frames labeled with another page's
        # identity fail the digest match and are not resumable.
        _, manifest_path, samples_s, identity = args[:4]
        live_digest = args[4] if len(args) == 5 else None
        ok = resume_allowed(Path(manifest_path), int(samples_s), identity, live_digest)
        return 0 if ok else 1
    if args[:1] == ["verify"] and len(args) == 5:
        # GATE-side verdict-integrity check: re-derive count + distinctness from
        # disk (manifest hashes are advisory). exit 0 = side verified.
        _, manifest_path, side, samples_s, frames_dir = args
        m = load_manifest(Path(manifest_path), int(samples_s), None)
        ok, reason = verify_side(Path(frames_dir), m, side, int(samples_s))
        if not ok:
            print(reason, file=__import__("sys").stderr)
        return 0 if ok else 1
    print(
        "usage: scroll_chunk_manifest next|record|complete|resumable|verify ...",
        file=__import__("sys").stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
