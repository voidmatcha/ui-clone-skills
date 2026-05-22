#!/usr/bin/env bash
# bundle-extraction.sh — parse JS bundles for animation/scroll library
# parameters. Deterministic Python bounded parsing, no LLM judgment needed.
#
# Replaces the deleted .claude-plugin/agents/bundle-analyzer.md sub-agent
#
# Input:  tmp/ref/<component>/ — must contain bundles/ directory + bundle-map.json
# Output: tmp/ref/<component>/bundle-extraction.json
#
# Usage: bundle-extraction.sh <ref-dir>
set -euo pipefail

REF_DIR="${1:-}"
if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: $0 <ref-dir>" >&2
  exit 2
fi

BUNDLES_DIR="$REF_DIR/bundles"
if [ ! -d "$BUNDLES_DIR" ]; then
  echo "▸ bundle-extraction: SKIP — no bundles/ directory in $REF_DIR"
  exit 0
fi

OUT="$REF_DIR/bundle-extraction.json"

python3 - "$REF_DIR" "$OUT" <<'PY'
import json
import re
import sys
from pathlib import Path


ref_dir = Path(sys.argv[1])
out_path = Path(sys.argv[2])
bundles_dir = ref_dir / "bundles"

bundle_map_path = ref_dir / "bundle-map.json"
bundle_map = {}
if bundle_map_path.exists():
    try:
        bundle_map = json.loads(bundle_map_path.read_text())
    except (OSError, json.JSONDecodeError):
        pass


def detect_in_text(text: str, marker: str) -> bool:
    return marker.lower() in text.lower()


# Concatenate all bundle text for grep efficiency. Real-world bundles are
# minified into a few MB — small enough to load into memory.
js_files = sorted(bundles_dir.rglob("*.js"))
all_text_parts: list[str] = []
file_offsets: list[tuple[str, int]] = []
offset = 0
for jf in js_files:
    try:
        t = jf.read_text(errors="ignore")
    except OSError:
        continue
    file_offsets.append((str(jf.relative_to(ref_dir)), offset))
    all_text_parts.append(t)
    offset += len(t)
all_text = "\n".join(all_text_parts)


def find_file_for_offset(off: int) -> str:
    fname = file_offsets[0][0] if file_offsets else "?"
    for f, o in file_offsets:
        if o <= off:
            fname = f
    return fname


extractions: dict = {}
unresolved: list = []


# ── Lenis ────────────────────────────────────────────────────────
if detect_in_text(all_text, "new Lenis(") or detect_in_text(all_text, "lerp:"):
    lenis_extracts = []
    for m in re.finditer(r"new\s+Lenis\s*\(\s*(\{[^{}]{0,500}\})", all_text):
        opts_raw = m.group(1)
        opts: dict = {}
        for key in ("lerp", "duration", "smoothWheel", "smoothTouch", "touchMultiplier", "direction", "easing"):
            km = re.search(rf"{key}\s*:\s*([^,}}\n]+)", opts_raw)
            if km:
                opts[key] = km.group(1).strip()
        lenis_extracts.append({
            "source": find_file_for_offset(m.start()),
            "options": opts,
            "confidence": "high" if opts else "low",
        })
    if lenis_extracts:
        extractions["lenis"] = lenis_extracts


# ── GSAP ─────────────────────────────────────────────────────────
gsap_calls: list = []
for pattern, kind in [
    (r"gsap\.timeline\s*\(\s*(\{[^{}]{0,300}\})?", "timeline"),
    (r"gsap\.(?:to|from|fromTo)\s*\(\s*([^,]+)\s*,\s*(\{[^{}]{0,500}\})", "tween"),
    (r"ScrollTrigger\.create\s*\(\s*(\{[^{}]{0,500}\})", "scrollTrigger"),
]:
    for m in re.finditer(pattern, all_text):
        gsap_calls.append({
            "kind": kind,
            "source": find_file_for_offset(m.start()),
            "raw": m.group(0)[:200],
            "confidence": "medium",  # minified args hard to fully parse
        })
if gsap_calls:
    extractions["gsap"] = gsap_calls


# ── Framer Motion ────────────────────────────────────────────────
fm_uses: list = []
for pattern, kind in [
    (r"useScroll\s*\(\s*(\{[^{}]{0,200}\})?", "useScroll"),
    (r"useTransform\s*\(\s*[^,]+,\s*(\[[^\]]+\])\s*,\s*(\[[^\]]+\])", "useTransform"),
    (r"useInView\s*\(\s*[^,]+,\s*(\{[^{}]{0,200}\})", "useInView"),
]:
    for m in re.finditer(pattern, all_text):
        fm_uses.append({
            "kind": kind,
            "source": find_file_for_offset(m.start()),
            "raw": m.group(0)[:200],
            "confidence": "medium",
        })
if fm_uses:
    extractions["framerMotion"] = fm_uses


# ── Anime.js ─────────────────────────────────────────────────────
anime_calls: list = []
for m in re.finditer(r"anime\s*\(\s*(\{[^{}]{0,500}\})", all_text):
    anime_calls.append({
        "source": find_file_for_offset(m.start()),
        "raw": m.group(0)[:200],
        "confidence": "medium",
    })
if anime_calls:
    extractions["animeJs"] = anime_calls


# ── Webflow IX2 ──────────────────────────────────────────────────
if detect_in_text(all_text, "actionTypeId") or detect_in_text(all_text, "ix2"):
    ix2_actions: list = []
    for m in re.finditer(r"actionTypeId\s*:\s*['\"]([^'\"]+)['\"]", all_text):
        ix2_actions.append({
            "actionType": m.group(1),
            "source": find_file_for_offset(m.start()),
            "confidence": "high",  # actionTypeId is a clear marker
        })
    if ix2_actions:
        extractions["webflowIX2"] = {
            "actions": ix2_actions[:50],  # cap to avoid huge output
            "totalActions": len(ix2_actions),
        }


# ── Output ───────────────────────────────────────────────────────
total_size_kb = sum(len(p) for p in all_text_parts) // 1024
plan = {
    "schemaVersion": 1,
    "bundlesScanned": len(js_files),
    "totalSizeKB": total_size_kb,
    "extractions": extractions,
    "unresolved": unresolved,
}

out_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
print(f"✓ bundle-extraction.json written → {out_path}")
print(f"  bundles scanned: {len(js_files)} ({total_size_kb} KB)")
for lib in sorted(extractions.keys()):
    count = len(extractions[lib]) if isinstance(extractions[lib], list) else extractions[lib].get("totalActions", "?")
    print(f"  {lib}: {count} extractions")
if not extractions:
    print("  no library construction sites detected")
PY
