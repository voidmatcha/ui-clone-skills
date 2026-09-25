#!/usr/bin/env python3
"""Register a selector ROI against the recorded frame when scroll settles late.

The browser rectangle is measured during recording. A smooth-scroll runtime can
move the page before the WebM frame used for comparison, leaving the crop over
unrelated content. Registration is bounded and requires a strong visual match.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from skimage.feature import match_template


def frame(path: Path) -> np.ndarray:
    output = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", "0.15", "-i", str(path),
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        check=True, capture_output=True, timeout=30,
    ).stdout
    return np.asarray(Image.open(io.BytesIO(output)).convert("L"), dtype=np.float32)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register(plan_path: Path, ref_path: Path, impl_path: Path) -> tuple[str, str]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    ref_crop = plan["ref"]["crop"]
    impl_crop = plan["impl"]["crop"]
    ref_frame, impl_frame = frame(ref_path), frame(impl_path)
    width, height = int(ref_crop["width"]), int(ref_crop["height"])
    if (width, height) != (int(impl_crop["width"]), int(impl_crop["height"])):
        raise ValueError("reference and implementation ROI dimensions differ")
    ix, iy = int(impl_crop["x"]), int(impl_crop["y"])
    rx, ry = int(ref_crop["x"]), int(ref_crop["y"])
    template = impl_frame[iy:iy + height, ix:ix + width]
    if template.shape != (height, width) or float(template.std()) < 3:
        raise ValueError("implementation ROI has insufficient visual texture")

    radius_x, radius_y = 48, 180
    x0, y0 = max(0, rx - radius_x), max(0, ry - radius_y)
    x1 = min(ref_frame.shape[1], rx + width + radius_x)
    y1 = min(ref_frame.shape[0], ry + height + radius_y)
    scores = match_template(ref_frame[y0:y1, x0:x1], template)
    best_y, best_x = np.unravel_index(int(np.argmax(scores)), scores.shape)
    registered_x, registered_y = x0 + int(best_x), y0 + int(best_y)
    best_score = float(scores[best_y, best_x])
    expected_score = float(scores[ry - y0, rx - x0])
    offset_x, offset_y = registered_x - rx, registered_y - ry
    apply = (
        best_score >= 0.75
        and best_score - expected_score >= 0.15
        and abs(offset_x) <= radius_x
        and abs(offset_y) <= radius_y
        and (abs(offset_x) > 2 or abs(offset_y) > 2)
    )
    plan["videoRegistration"] = {
        "schemaVersion": 1,
        "method": "bounded-normalized-template-match",
        "sampleSeconds": 0.15,
        "referenceVideoSha256": digest(ref_path),
        "implementationVideoSha256": digest(impl_path),
        "expectedScore": round(expected_score, 6),
        "bestScore": round(best_score, 6),
        "offset": {"x": offset_x, "y": offset_y},
        "applied": apply,
    }
    if apply:
        ref_crop["x"], ref_crop["y"] = registered_x, registered_y
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"crop={width}:{height}:{ref_crop['x']}:{ref_crop['y']}\t"
          f"crop={width}:{height}:{ix}:{iy}")
    return str(offset_x), str(offset_y)


if __name__ == "__main__":
    try:
        register(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    except (IndexError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ROI video registration unavailable: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
