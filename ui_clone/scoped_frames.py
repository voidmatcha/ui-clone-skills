"""Frame measurement shared by `ui_clone.scoped_diff` and `ui_clone.scoped_check`.

Two frame classes live under `tmp/ref/<target>/frames/{ref,impl}/`
(element-capture.md):

* resting-state clips (`idle.png`, `active.png`, `before.png`, `mid.png`,
  `after.png`, `open.png`): compared pixel-exact, AE 0 (comparison-fix.md
  "Frame Comparison — element scope");
* motion sequences (`frame-0001.png`, `open-0001.png`, `close-0001.png`, ...
  extracted at 60fps): two independent recordings never line up frame for
  frame, so they are judged with the page-level video criteria from
  `scripts/verify/video-transition-compare.sh` + `scripts/verify/lib/frame-align.sh`:
  first-change alignment (`analyze_timing`: a frame changes when more than
  min(pixels/20, 5000) pixels move by more than 0.5% of the channel range),
  the arc-timing verdict (`arc_timing_verdict`, first-to-last-change lengths
  within 18 frames, one-side-no-motion fails), and per-frame SSIM at
  `SSIM_THRESHOLD` 0.90 with `UI_CLONE_VMC_JITTER_FRAMES` = 1 neighbor retry
  (`_best_frame_ssim`). None of the numbers here are new; they are the ones
  those scripts use and document. The splash distribution calibration and
  the ref-vs-refcal re-recording paths are not ported (they need a live
  browser), so this judge is never looser than the page-level one.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

FRAME_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
# Resting-state clip names (element-capture.md, comparison-fix.md triggerType table).
RESTING_STATES = ("idle", "active", "before", "mid", "after", "open")
SEQUENCE_RE = re.compile(r"^(?P<prefix>[a-z][a-z0-9_]*)-(?P<idx>\d{2,})$")

# video-transition-compare.sh: SSIM_THRESHOLD="${SSIM_THRESHOLD:-0.90}"
SSIM_THRESHOLD = 0.90
# video-transition-compare.sh: JITTER_FRAMES="${UI_CLONE_VMC_JITTER_FRAMES:-1}"
JITTER_FRAMES = 1
# frame-align.sh arc_timing_verdict default max_delta (frames).
ARC_MAX_DELTA = 18
# frame-align.sh _frame_changed_pixel_count: FRAME_CHANGE_PIXEL_DELTA_PERCENT=0.5
CHANGE_PIXEL_DELTA_PERCENT = 0.5
# frame-align.sh analyze_timing: threshold = clamp(pixels / 20, 1, 5000)
CHANGE_AREA_DIVISOR = 20
CHANGE_THRESHOLD_CEILING = 5000
# Criteria fingerprint recorded with cached verdicts; bump when any rule above changes.
CRITERIA_VERSION = "motion-v1:ssim0.90:jitter1:arc18:change0.5pct"

SOURCE_EXTS = frozenset(
    {".tsx", ".ts", ".jsx", ".js", ".mjs", ".css", ".scss", ".vue", ".svelte", ".html"}
)
_SOURCE_SKIP_DIRS = frozenset(
    {"node_modules", ".git", ".next", "dist", "build", "out", "tmp", ".turbo", ".cache"}
)
_KEY_RE = re.compile(r"[^a-z0-9]+")


def target_key(name: str) -> str:
    return _KEY_RE.sub("", name.lower())


def list_frames(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    return {
        p.name: p
        for p in sorted(directory.iterdir())
        if p.suffix.lower() in FRAME_EXTS and (p.is_file() or p.is_symlink())
    }


def sequence_of(name: str) -> tuple[str, int] | None:
    """(`prefix`, index) for a motion frame such as `open-0007.png`, else None."""
    match = SEQUENCE_RE.match(Path(name).stem)
    if match is None:
        return None
    return match["prefix"], int(match["idx"])


def split_frames(frames: dict[str, Path]) -> tuple[dict[str, Path], dict[str, list[tuple[int, str]]]]:
    """Split into resting clips and `{prefix: [(index, name), ...]}` sequences."""
    static: dict[str, Path] = {}
    sequences: dict[str, list[tuple[int, str]]] = {}
    for name, path in frames.items():
        seq = sequence_of(name)
        if seq is None:
            static[name] = path
        else:
            sequences.setdefault(seq[0], []).append((seq[1], name))
    for items in sequences.values():
        items.sort()
    return static, sequences


def auto_sources(impl_root: Path, target: str) -> list[Path]:
    """Source files named after the target (`pricing-modal` -> PricingModal.tsx)."""
    key = target_key(target)
    if not key or not impl_root.is_dir():
        return []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(impl_root):
        dirnames[:] = [d for d in dirnames if d not in _SOURCE_SKIP_DIRS]
        base = Path(dirpath)
        rel_parts = base.relative_to(impl_root).parts
        if "src" in rel_parts:
            rel_parts = rel_parts[len(rel_parts) - rel_parts[::-1].index("src") :]
        dir_match = any(target_key(part) == key for part in rel_parts)
        for name in filenames:
            path = base / name
            if path.suffix.lower() not in SOURCE_EXTS:
                continue
            if dir_match or target_key(path.stem) == key:
                found.append(path)
    return sorted(found)


# ── pixel measurements ────────────────────────────────────────────────────


def _load(path: Path, mode: str) -> Any:
    import numpy as np
    from PIL import Image

    with Image.open(path) as image:
        return np.asarray(image.convert(mode))


def pixel_ae(ref: Path, impl: Path) -> int | None:
    """Count of differing pixels (ImageMagick `compare -metric AE`, no fuzz).

    Returns None when the images differ in size (not comparable).
    """
    a = _load(ref, "RGBA")
    b = _load(impl, "RGBA")
    if a.shape != b.shape:
        return None
    return int((a != b).any(axis=-1).sum())


def gray_ssim(a: Any, b: Any) -> float:
    """SSIM of two equal-shape uint8 grayscale arrays (skimage, 7px window)."""
    from skimage.metrics import structural_similarity

    min_dim = min(a.shape[0], a.shape[1])
    if min_dim < 3:
        return 1.0 if (a == b).all() else 0.0
    win = min(7, min_dim if min_dim % 2 == 1 else min_dim - 1)
    return float(structural_similarity(a, b, data_range=255, win_size=win))


def _changed_pixels(prev: Any, cur: Any) -> int:
    import numpy as np

    delta = np.abs(prev.astype(np.int16) - cur.astype(np.int16)).max(axis=-1)
    return int((delta > 255 * CHANGE_PIXEL_DELTA_PERCENT / 100).sum())


def change_threshold(width: int, height: int) -> int:
    return max(1, min(CHANGE_THRESHOLD_CEILING, (width * height) // CHANGE_AREA_DIVISOR))


def first_last_change(paths: list[Path]) -> tuple[int, int]:
    """1-based first and last change-point indexes (frame-align.sh `analyze_timing`).

    A side with no change point reports (1, 1) — raw alignment and arc 0, so a
    motionless side can never self-align against a real arc.
    """
    if not paths:
        return 1, 1
    first = last = 0
    prev = _load(paths[0], "RGB")
    threshold = change_threshold(prev.shape[1], prev.shape[0])
    for index, path in enumerate(paths[1:], start=2):
        cur = _load(path, "RGB")
        if cur.shape == prev.shape and _changed_pixels(prev, cur) > threshold:
            if first == 0:
                first = index
            last = index
        prev = cur
    return (first or 1), (last or 1)


def compare_sequence(ref_paths: list[Path], impl_paths: list[Path]) -> dict[str, Any]:
    """Judge one motion sequence with the page-level video criteria.

    Returns `{"pass": bool, "reasons": [...], "aligned": n, "ref_arc": ..., "impl_arc": ...,
    "failing": [(k, ssim), ...], "min_ssim": ...}`.
    """
    reasons: list[str] = []
    ref_first, ref_last = first_last_change(ref_paths)
    impl_first, impl_last = first_last_change(impl_paths)
    ref_arc = ref_last - ref_first
    impl_arc = impl_last - impl_first
    if (ref_arc == 0) != (impl_arc == 0):
        reasons.append(
            "arc timing: one side has no detected motion (missing transition): "
            f"ref {ref_arc} frames, impl {impl_arc} frames"
        )
    elif abs(ref_arc - impl_arc) > ARC_MAX_DELTA:
        reasons.append(
            f"arc timing: first-to-last-change duration differs by {abs(ref_arc - impl_arc)} "
            f"frames (>{ARC_MAX_DELTA}): ref {ref_arc}, impl {impl_arc}"
        )
    ref_off = ref_first - 1
    impl_off = impl_first - 1
    aligned = min(len(ref_paths) - ref_off, len(impl_paths) - impl_off)
    failing: list[tuple[int, float]] = []
    min_ssim = 1.0
    if aligned < 1:
        reasons.append("no aligned frames after first-change alignment")
    else:
        ref_gray = [_load(p, "L") for p in ref_paths]
        impl_gray = [_load(p, "L") for p in impl_paths]
        for k in range(aligned):
            ref_frame = ref_gray[k + ref_off]
            base = k + impl_off
            ssim = _ssim_or_zero(ref_frame, impl_gray[base])
            if ssim < SSIM_THRESHOLD and JITTER_FRAMES > 0:
                for dj in range(1, JITTER_FRAMES + 1):
                    for alt in (base - dj, base + dj):
                        if 0 <= alt < len(impl_gray):
                            ssim = max(ssim, _ssim_or_zero(ref_frame, impl_gray[alt]))
            min_ssim = min(min_ssim, ssim)
            if ssim < SSIM_THRESHOLD:
                failing.append((k + 1, round(ssim, 4)))
        if failing:
            shown = ", ".join(f"#{k} {s}" for k, s in failing[:5])
            more = f" (+{len(failing) - 5} more)" if len(failing) > 5 else ""
            reasons.append(
                f"{len(failing)} of {aligned} aligned frame(s) below SSIM {SSIM_THRESHOLD} "
                f"(±{JITTER_FRAMES} frame jitter allowed): {shown}{more}"
            )
    return {
        "pass": not reasons,
        "reasons": reasons,
        "aligned": aligned,
        "ref_first": ref_first,
        "impl_first": impl_first,
        "ref_arc": ref_arc,
        "impl_arc": impl_arc,
        "failing": failing,
        "min_ssim": round(min_ssim, 4),
    }


def _ssim_or_zero(a: Any, b: Any) -> float:
    if a.shape != b.shape:
        return 0.0
    return gray_ssim(a, b)
