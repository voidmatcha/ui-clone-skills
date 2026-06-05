#!/usr/bin/env bash
# color-token-grounding-check.sh — assert every impl color token traces
# to the ref's extracted color palette.
#
# Usage:
#   color-token-grounding-check.sh <ref-dir> <impl-root>
#
#
# How it works:
#   1. Load ref color palette from `<ref-dir>/styles.json` (Phase 2
#      design-tokens extraction). Fall back to `extracted.json`
#      `colors` array. Each ref token is normalized to lowercase
#      6-char hex (or rgb tuple).
#   2. Walk impl src/ and styles/ directories. For each *.css /
#      *.scss / *.tsx / *.jsx / *.ts / *.js file, extract every
#      hex (#aabbcc / #abc), rgb(...), rgba(...), hsl(...), hsla(...)
#      literal. Strip transparency / alpha.
#   3. For each impl token, check whether it matches a ref token
#      within a small color-distance tolerance (default Delta-E < 5).
#   4. FAIL when more than MAX_INVENTED colors don't match (default
#      scales with ref palette size: max(2, ref_count // 5)).
#
# Skips when:
#   - ref styles.json + extracted.json both missing colors data
#   - impl has no CSS/TSX files
#
# Writes:
#   <ref-dir>/color-token-grounding.json
#
# Exit 0 on pass/skip, 1 on too many invented colors, 2 on setup error.

set -uo pipefail

REF_DIR="${1:?Usage: color-token-grounding-check.sh <ref-dir> <impl-root>}"
IMPL_ROOT="${2:?impl-root required}"

[ -d "$REF_DIR" ]   || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }
[ -d "$IMPL_ROOT" ] || { echo "impl-root not found: $IMPL_ROOT" >&2; exit 2; }

OUT="$REF_DIR/color-token-grounding.json"
MAX_INVENTED="${COLOR_GROUNDING_MAX_INVENTED:-scale}"

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT" "$MAX_INVENTED" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ref_dir, impl_root, out_path, max_inv_arg = sys.argv[1:5]
ref_p = Path(ref_dir)
impl_p = Path(impl_root)
out_p = Path(out_path)

# ── Helpers ───────────────────────────────────────────────────────────
def normalize_hex(s: str) -> str | None:
    s = s.strip().lower()
    if s.startswith("#"):
        s = s[1:]
    if len(s) == 3 and all(c in "0123456789abcdef" for c in s):
        s = "".join(c * 2 for c in s)
    if len(s) == 6 and all(c in "0123456789abcdef" for c in s):
        return s
    if len(s) == 8 and all(c in "0123456789abcdef" for c in s):
        return s[:6]  # strip alpha
    return None

def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"{r:02x}{g:02x}{b:02x}"

def hex_to_rgb(h: str) -> tuple[int, int, int]:
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

def srgb_to_linear(c: float) -> float:
    """sRGB gamma decode. c in [0,1]."""
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4

def rgb_to_oklab(r: int, g: int, b: int) -> tuple[float, float, float]:
    """sRGB hex → OKLab. Perceptually-uniform color space.
    Implementation per https://bottosson.github.io/posts/oklab/.
    """
    lr = srgb_to_linear(r / 255.0)
    lg = srgb_to_linear(g / 255.0)
    lb = srgb_to_linear(b / 255.0)
    l_ = 0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb
    m_ = 0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb
    s_ = 0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb
    l = l_ ** (1 / 3)
    m = m_ ** (1 / 3)
    s = s_ ** (1 / 3)
    L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    a = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    b_lab = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return (L, a, b_lab)

def color_distance(h1: str, h2: str) -> float:
    """OKLab perceptual distance replaces previous Euclidean RGB. Two RGB-close-but-
    perceptually-distant colors (#ffeb00 yellow vs #ffaa00 orange,
    RGB-distance ~75 but visually obviously different) get a larger
    OKLab distance; two perceptually-close colors get smaller distance.
    OKLab unit ≈ JND step; typical tolerance: 0.03 (subtle change),
    0.07 (visible change), 0.15 (clearly different).
    """
    r1, g1, b1 = hex_to_rgb(h1)
    r2, g2, b2 = hex_to_rgb(h2)
    L1, a1, b1_lab = rgb_to_oklab(r1, g1, b1)
    L2, a2, b2_lab = rgb_to_oklab(r2, g2, b2)
    return ((L1 - L2) ** 2 + (a1 - a2) ** 2 + (b1_lab - b2_lab) ** 2) ** 0.5

HEX_RE = re.compile(r"#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
RGB_RE = re.compile(r"rgba?\(\s*(\d+)\s*,?\s*(\d+)\s*,?\s*(\d+)")
HSL_RE = re.compile(r"hsla?\(\s*(\d+(?:\.\d+)?)\s*,?\s*(\d+(?:\.\d+)?)%?\s*,?\s*(\d+(?:\.\d+)?)%?")

def hsl_to_rgb_hex(h: float, s: float, l: float) -> str:
    """Convert HSL to RGB hex. h: 0-360, s/l: 0-100."""
    s /= 100.0
    l /= 100.0
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs(((h / 60) % 2) - 1))
    m = l - c / 2
    sextant = int(h // 60) % 6
    if   sextant == 0: r, g, b = c, x, 0
    elif sextant == 1: r, g, b = x, c, 0
    elif sextant == 2: r, g, b = 0, c, x
    elif sextant == 3: r, g, b = 0, x, c
    elif sextant == 4: r, g, b = x, 0, c
    else:              r, g, b = c, 0, x
    return rgb_to_hex(int((r + m) * 255 + 0.5), int((g + m) * 255 + 0.5), int((b + m) * 255 + 0.5))

def extract_colors_from_text(text: str) -> set[str]:
    colors: set[str] = set()
    for m in HEX_RE.finditer(text):
        norm = normalize_hex(m.group(0))
        if norm:
            colors.add(norm)
    for m in RGB_RE.finditer(text):
        try:
            r, g, b = (int(x) for x in m.groups())
            if all(0 <= v <= 255 for v in (r, g, b)):
                colors.add(rgb_to_hex(r, g, b))
        except Exception:
            pass
    for m in HSL_RE.finditer(text):
        try:
            colors.add(hsl_to_rgb_hex(float(m.group(1)), float(m.group(2)), float(m.group(3))))
        except Exception:
            pass
    return colors

# ── Collect ref colors ────────────────────────────────────────────────
ref_colors: set[str] = set()
for name in ("styles.json", "extracted.json", "design-tokens.json",
             "css/variables.txt", "section-html/_colors.json"):
    p = ref_p / name
    if not p.exists():
        continue
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        continue
    # Two strategies: parse structured JSON if it has a colors field,
    # AND scan the raw text for color literals as a fallback.
    try:
        data = json.loads(text)
        # Recurse for any "color"/"colors"/"palette" field with strings
        stack: list = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for k, v in node.items():
                    kl = k.lower()
                    if kl in ("color", "colors", "palette", "fg", "bg", "fill", "stroke", "background") and isinstance(v, str):
                        ref_colors.update(extract_colors_from_text(v))
                    elif isinstance(v, (dict, list)):
                        stack.append(v)
                    elif isinstance(v, str) and len(v) < 80:
                        # Short strings — try to extract
                        ref_colors.update(extract_colors_from_text(v))
            elif isinstance(node, list):
                stack.extend(node)
    except Exception:
        pass
    # Raw scan as fallback / additional source
    ref_colors.update(extract_colors_from_text(text))

if not ref_colors:
    out_p.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
        "reasons": ["ref has no color palette data — gate does not apply"],
        "rule": (
            "Every impl color literal (hex/rgb/hsl) must trace to the ref's "
            "extracted color palette within a small color-distance tolerance. "
            "Catches the 'invent plausible-looking colors' failure mode. "
            "Skips when ref has no palette to compare against."
        ),
    }, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status": "skip", "out": str(out_p)}))
    sys.exit(0)

# ── Walk impl source ──────────────────────────────────────────────────
IMPL_EXT = {".css", ".scss", ".sass", ".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte"}
SCAN_DIRS = [impl_p / d for d in ("src", "app", "styles", "components", "lib")]
SCAN_DIRS = [d for d in SCAN_DIRS if d.exists()]
# Fallback: scan impl root non-recursively for direct CSS files
if not SCAN_DIRS:
    SCAN_DIRS = [impl_p]

impl_colors: set[str] = set()
files_scanned = 0
for d in SCAN_DIRS:
    for path in d.rglob("*"):
        if path.is_dir():
            continue
        if path.suffix.lower() not in IMPL_EXT:
            continue
        if "node_modules" in path.parts or ".next" in path.parts or "dist" in path.parts:
            continue
        files_scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        impl_colors.update(extract_colors_from_text(text))

COMMON_NEUTRALS = {
    "000000", "ffffff", "000001",
    # Tailwind gray scale (approximate / common picks)
    "f9fafb", "f3f4f6", "e5e7eb", "d1d5db", "9ca3af", "6b7280",
    "4b5563", "374151", "1f2937", "111827",
    # Tailwind slate
    "f8fafc", "f1f5f9", "e2e8f0", "cbd5e1", "94a3b8", "64748b",
    "475569", "334155", "1e293b", "0f172a",
    # Plain grays
    "888888", "999999", "aaaaaa", "bbbbbb", "cccccc", "dddddd", "eeeeee",
    "f0f0f0", "f5f5f5", "fafafa", "fefefe", "f8f8f8", "f6f6f6",
    "111111", "222222", "333333", "444444", "555555", "666666", "777777",
    # Common off-whites / off-blacks
    "fcfcfc", "fbfbfb", "f7f7f7", "010101", "020202", "0a0a0a",
    # Border / shadow gray standards
    "e0e0e0", "dadada", "d0d0d0", "c0c0c0", "b0b0b0",
}

TOLERANCE = float(__import__("os").environ.get("COLOR_GROUNDING_TOLERANCE", "0.08"))
matched: list[str] = []
invented: list[dict] = []
for c in sorted(impl_colors):
    if c in ref_colors:
        matched.append(c)
        continue
    if c in COMMON_NEUTRALS:
        matched.append(c)
        continue
    # Find nearest ref color
    best_dist = float("inf")
    best_ref = ""
    for r in ref_colors:
        d = color_distance(c, r)
        if d < best_dist:
            best_dist = d
            best_ref = r
    if best_dist <= TOLERANCE:
        matched.append(c)
    else:
        invented.append({"impl": c, "nearestRef": best_ref, "distance": round(best_dist, 1)})

# ── Tolerance ─────────────────────────────────────────────────────────
if max_inv_arg == "scale":
    max_invented = max(2, len(ref_colors) // 5)
else:
    try:
        max_invented = int(max_inv_arg)
    except ValueError:
        max_invented = 2

invented_count = len(invented)
reasons: list[str] = []
if invented_count > max_invented:
    status = "fail"
    sample = invented[:10]
    reasons.append(
        f"{invented_count} impl color literal(s) don't match any ref palette "
        f"entry within distance {TOLERANCE} (allowed: {max_invented}). "
        "Examples: " + "; ".join(
            f"#{v['impl']} (nearest ref: #{v['nearestRef']}, distance {v['distance']})"
            for v in sample
        )
    )
else:
    status = "pass"
    if invented_count > 0:
        reasons.append(f"informational: {invented_count} invented within tolerance ({invented_count} ≤ {max_invented})")

payload = {
    "schemaVersion": 1,
    "status": status,
    "refColorCount": len(ref_colors),
    "implColorCount": len(impl_colors),
    "matchedCount": len(matched),
    "inventedCount": invented_count,
    "tolerance": TOLERANCE,
    "maxInvented": max_invented,
    "filesScanned": files_scanned,
    "matched": sorted(matched)[:30],
    "invented": invented[:30],
    "reasons": reasons,
    "nextAction": (
        "Restore ref color tokens to design system / Tailwind theme so impl "
        "matches the ref palette. Invented colors usually come from copying "
        "values out of memory rather than checking the extracted palette."
        if reasons and status == "fail" else "color tokens grounded in ref palette"
    ),
    "rule": (
        "Every impl color literal (hex/rgb/hsl) must trace to the ref's "
        "extracted color palette within OKLab perceptual distance ≤ tolerance "
        "(default 0.08). Common UI neutrals (#000, #fff, Tailwind gray/slate "
        "scales, off-white/off-black) are allowlisted. Catches the 'invent "
        "plausible color' failure mode."
    ),
}

out_p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": status, "matched": len(matched), "invented": invented_count, "out": str(out_p)}, ensure_ascii=False))
sys.exit(0 if status in ("pass", "skip") else 1)
PY
