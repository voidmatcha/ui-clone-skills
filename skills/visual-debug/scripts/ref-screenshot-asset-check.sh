#!/usr/bin/env bash
# ref-screenshot-asset-check.sh — fail when impl uses ref's captured
# screenshots as background images or assets.
#
#
# Detection: scan impl/src/ and impl/public/ for any reference to
# the ref's screenshot artifacts:
#   - tmp/ref/<component>/sections/{ref,impl}/*.png  (per-section crops)
#   - tmp/ref/<component>/static/{ref,impl}/*.png    (full-page screenshots)
#   - tmp/ref/<component>/sections/diff/*.png        (AE diff images)
#   - tmp/ref/<component>/transitions/*.{png,webp,mp4}
#
# Also: scan impl/public/ for files byte-identical to anything under
# the ref's screenshot dirs (catches the "copy-and-rename" variant).
#
# Usage:
#   ref-screenshot-asset-check.sh <ref-dir> [<impl-root>]
#   ref-dir       canonical ref dir (tmp/ref/<component>)
#   impl-root     impl/ — auto-detected via find-impl-root.sh if omitted
#
# Output: <ref-dir>/ref-screenshot-asset.json
#   { schemaVersion: 1, status: "pass"|"fail", scanned, violations:[...] }
#
# Exit: 0 = pass, 1 = at least one violation, 2 = setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: ref-screenshot-asset-check.sh <ref-dir> [<impl-root>]" >&2
  exit 2
fi

if [ -z "$IMPL_ROOT" ]; then
  PLUGIN_ROOT_CAND="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
  for cand_root in "$PLUGIN_ROOT_CAND" "$(cd "$(dirname "$0")/../../.." && pwd)"; do
    [ -z "$cand_root" ] && continue
    RESOLVER="$cand_root/scripts/extract/find-impl-root.sh"
    if [ -f "$RESOLVER" ]; then
      IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
      [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT" ] && break
    fi
  done
fi

OUT_PATH="$REF_DIR/ref-screenshot-asset.json"

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl_root not found",
  "scanned": 0,
  "violations": []
}
JSON
  echo "ref-screenshot-asset: skip (no impl)"
  exit 0
fi

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT_PATH" <<'PY'
# Compat note: this embedded Python uses PEP 604 union syntax (`X | Y`)
# which needs Python 3.10+. macOS dev environments ship 3.9.6 by default;
# without this future-import the script raises SyntaxError before writing
# ref-screenshot-asset.json, blocking the dispatcher. Future-import defers
# annotation evaluation so 3.9 accepts the modern syntax as strings.
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_root = Path(sys.argv[2])
out_path = Path(sys.argv[3])

# ── Canvas-replay allowlist (v0.7.0) ──────────────────────────────────
# When closeoutPolicy=="canvas-replay" AND canvas-replay-attestation.json
# is present, byte-identical-copy violations whose refSource basename
# (e.g. "sections/ref/music-sphere.png") matches a section tagged
# kind="canvas" in section-map.json are allowed. Operator may use a
# captured canvas-section frame as a fallback under the attestation
# umbrella. Scope is byte-identical-copy ONLY — ref-path-reference
# (generic "tmp/ref/" substring leaks) stays strict because the
# substring doesn't pinpoint a section. Fail-closed: any missing
# condition (no policy, no attestation, no kind=canvas section)
# disables the allowlist.
canvas_replay_section_names: set[str] = set()
_state_p = ref_dir / "pipeline-state.json"
_att_p = ref_dir / "canvas-replay-attestation.json"
_map_p = ref_dir / "section-map.json"
if _state_p.is_file() and _att_p.is_file() and _map_p.is_file():
    try:
        _state = json.loads(_state_p.read_text(encoding="utf-8"))
        _policy = (
            isinstance(_state, dict)
            and (_state.get("closeoutPolicy") or _state.get("closeout_policy"))
        )
        if _policy == "canvas-replay":
            _map = json.loads(_map_p.read_text(encoding="utf-8"))
            if isinstance(_map, dict) and isinstance(_map.get("sections"), list):
                for _s in _map["sections"]:
                    if not isinstance(_s, dict):
                        continue
                    if _s.get("kind") != "canvas":
                        continue
                    for _key in ("name", "id", "className", "cls"):
                        _v = _s.get(_key)
                        if isinstance(_v, str) and _v.strip():
                            canvas_replay_section_names.add(_v.strip())
                    _idx = _s.get("index")
                    if isinstance(_idx, int):
                        canvas_replay_section_names.add(f"section-{_idx}")
    except (json.JSONDecodeError, OSError):
        pass

# ── Near-match detection (perceptual/AE) ──────────────────────────────
# The screenshot-as-background cheat re-encodes the ref's section crops (so the
# bytes differ — defeating the sha256 byte-identical check) and serves them at a
# generic path like public/sections/<name>.png. Compare each impl raster asset
# against the ref's own capture crops with magick AE (re-encode-tolerant via
# -fuzz); a near-pixel-identical match is the cheat. Genuine clones may reuse
# product images, but those do not pixel-match the verifier's full section crops.
_MAGICK = shutil.which("magick")


def _im(sub: str) -> "list[str] | None":
    if _MAGICK:
        return [_MAGICK, sub]
    legacy = shutil.which(sub)
    return [legacy] if legacy else None


_IDENTIFY = _im("identify")
_COMPARE = _im("compare")
NEAR_MATCH_ENABLED = bool(_IDENTIFY and _COMPARE)
NEAR_MATCH_MAX_FRACTION = 0.02  # <=2% of pixels differ (post-fuzz) => near-identical
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
ref_raster_crops: list = []  # (abs Path, rel str) of ref capture images
_dims_cache: dict = {}


def _dims(p: Path):
    key = str(p)
    if key in _dims_cache:
        return _dims_cache[key]
    val = None
    if _IDENTIFY:
        try:
            r = subprocess.run(_IDENTIFY + ["-format", "%w %h", str(p)],
                               capture_output=True, text=True, timeout=20)
            parts = r.stdout.strip().split()
            if len(parts) >= 2:
                val = (int(parts[0]), int(parts[1]))
        except (subprocess.SubprocessError, ValueError, OSError):
            val = None
    _dims_cache[key] = val
    return val


def _ae_fraction(ref_img: Path, rw: int, rh: int, impl_img: Path):
    if not _COMPARE or rw <= 0 or rh <= 0:
        return None
    cmd = _COMPARE + ["-metric", "AE", "-fuzz", "6%", str(ref_img),
                      "(", str(impl_img), "-resize", f"{rw}x{rh}!", ")", "null:"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return None
    raw = (r.stderr or r.stdout or "").strip()
    if not raw:
        return None
    try:
        ae = float(raw.split()[0].replace(",", ""))
    except (ValueError, IndexError):
        return None
    return ae / float(rw * rh)


def _near_match_ref(impl_img: Path):
    # Performance: only AE-compare against ref crops with the SAME basename stem.
    # The cheat reuses the verifier's crop names (e.g. <section>.png) to
    # wire ::before backgrounds, so same-stem + AE-verify catches it cheaply (one
    # compare per impl image, not an O(n^2) all-pairs sweep) while AE-verify
    # avoids false positives on coincidental name collisions (a real "footer.png"
    # that does not pixel-match the ref's footer crop is not flagged).
    if not NEAR_MATCH_ENABLED or impl_img.suffix.lower() not in IMAGE_SUFFIXES:
        return None
    stem = impl_img.stem
    for ref_path, ref_rel in ref_raster_crops:
        if ref_path.stem != stem:
            continue
        rdims = _dims(ref_path)
        if not rdims or rdims[0] <= 0 or rdims[1] <= 0:
            continue
        frac = _ae_fraction(ref_path, rdims[0], rdims[1], impl_img)
        if frac is not None and frac <= NEAR_MATCH_MAX_FRACTION:
            # Canvas-replay allowlist: a canvas-section fallback frame is allowed.
            if canvas_replay_section_names:
                cstem = Path(ref_rel).stem
                cstem_base = re.sub(r"-\d+$", "", cstem)
                if cstem in canvas_replay_section_names or cstem_base in canvas_replay_section_names:
                    return None
            return ref_rel
    return None


# 1. Build the forbidden-path set from ref's captured artifacts.
# These are paths the impl must never reference.
forbidden_substrings: set[str] = set()
ref_screenshot_files: dict[str, str] = {}  # sha256 → relative path

REF_SCREENSHOT_DIRS = [
    ref_dir / "sections" / "ref",
    ref_dir / "sections" / "impl",
    ref_dir / "sections" / "diff",
    ref_dir / "static" / "ref",
    ref_dir / "static" / "impl",
    ref_dir / "transitions",
    ref_dir / "scroll-video",
    ref_dir / "clip",
]

# Always-forbidden substrings — even literal references to these dirs in
# impl source counts as cheat.
forbidden_substrings.update({
    "tmp/ref/",
    "/sections/ref/",
    "/sections/impl/",
    "/sections/diff/",
    "/static/ref/",
    "/static/impl/",
    "/scroll-video/",
})


def sha256_of(p: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# Index ref screenshots by hash.
SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm")
for d in REF_SCREENSHOT_DIRS:
    if not d.is_dir():
        continue
    for p in d.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUFFIXES:
            h = sha256_of(p)
            if h:
                ref_screenshot_files[h] = str(p.relative_to(ref_dir))
            if p.suffix.lower() in IMAGE_SUFFIXES:
                ref_raster_crops.append((p, str(p.relative_to(ref_dir))))


# 2. Scan impl source tree for forbidden references.
SCAN_EXCLUDE = {
    "node_modules", ".next", ".nuxt", ".svelte-kit", "dist", "build",
    ".turbo", ".cache", "coverage", ".git", ".vite",
}
TEXT_SUFFIXES = {".tsx", ".jsx", ".ts", ".js", ".mjs", ".cjs",
                 ".css", ".scss", ".sass", ".less",
                 ".module.css", ".html", ".htm", ".vue", ".svelte",
                 ".json", ".md", ".mdx"}

violations = []
scanned_text = 0
scanned_binary = 0

# Self-scan guard: the gate writes its own JSON artifacts (ref-screenshot-
# asset.json and sibling *-check / gate JSONs) into the ref dir, and those
# artifacts legitimately bake "tmp/ref/" path strings into their bodies (e.g. a
# prior run's `refSource`). When impl_root overlaps the ref dir, rglob picks
# those JSONs up and matches the forbidden substring against the gate's OWN
# output — a false positive (observed: 1 self-ref `tmp/ref/` violation against
# ref-screenshot-asset.json). Exclude JSON files that live under the ref dir;
# real impl source JSON (under a non-overlapping impl_root) and impl .tsx/.css
# reuse are unaffected, so genuine ref-screenshot reuse still flags.
ref_dir_resolved = ref_dir.resolve()


def _is_ref_dir_artifact(p: Path) -> bool:
    if p.suffix.lower() != ".json":
        return False
    try:
        pr = p.resolve()
    except OSError:
        return False
    return pr == ref_dir_resolved or ref_dir_resolved in pr.parents


for p in impl_root.rglob("*"):
    if not p.is_file():
        continue
    try:
        rel_parts = p.relative_to(impl_root).parts
    except ValueError:
        continue
    if any(part in SCAN_EXCLUDE for part in rel_parts):
        continue
    if _is_ref_dir_artifact(p):
        continue
    if p.suffix in TEXT_SUFFIXES:
        scanned_text += 1
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Strip comments before the forbidden-substring scan: a ref path
        # mentioned in a CODE COMMENT (e.g. a provenance note like
        # "fidelity sentinels generated from tmp/ref/<c>/text-fidelity-check.json")
        # is documentation, not a runtime asset reference — a comment can't
        # load a screenshot to fake a pixel-diff, which is the only thing this
        # gate exists to catch. Mirrors the comment-strip in dom-mirror-check /
        # text-fidelity-check. Only strip a `//` line comment when the `//` is
        # preceded by line-start or whitespace — so a protocol-relative asset
        # URL inside a string (e.g. src="//cdn/<c>/sections/ref/x.png", a real
        # runtime-loadable screenshot-cheat reference) is NOT mistaken for a
        # comment and stripped before the scan. `https://` (// after ':') and a
        # genuine trailing ` // note` are both handled correctly.
        # (Observed false positive: a FidelityText.tsx line-1 provenance comment
        # referencing a JSON artifact path tripped ref-path-reference.)
        suffix = p.suffix.lower()
        if suffix in {".tsx", ".jsx", ".ts", ".js", ".mjs", ".cjs",
                      ".css", ".scss", ".sass", ".less", ".vue", ".svelte"}:
            scan_text = re.sub(r"/\*[\s\S]*?\*/", "", text)
            scan_text = re.sub(r"(?m)(^[ \t]*|[ \t]+)//[^\n]*$", r"\1", scan_text)
        elif suffix in {".html", ".htm", ".md", ".mdx"}:
            scan_text = re.sub(r"<!--[\s\S]*?-->", "", text)
        else:
            scan_text = text
        for needle in forbidden_substrings:
            if needle in scan_text:
                violations.append({
                    "file": str(p.relative_to(impl_root)),
                    "kind": "ref-path-reference",
                    "needle": needle,
                })
    elif p.suffix.lower() in SUFFIXES:
        scanned_binary += 1
        h = sha256_of(p)
        if h and h in ref_screenshot_files:
            ref_src = ref_screenshot_files[h]
            # Canvas-replay allowlist: skip when the ref source PNG belongs
            # to a section tagged kind="canvas" in section-map.json. Match
            # on basename stem (sections/ref/music-sphere.png → music-sphere)
            # against the canvas-section name aliases.
            if canvas_replay_section_names:
                ref_stem = Path(ref_src).stem
                # Disambiguation suffix (-2, -3, ...) added by capture script
                # for repeated className — strip to compare the base name.
                ref_stem_base = re.sub(r"-\d+$", "", ref_stem)
                if (
                    ref_stem in canvas_replay_section_names
                    or ref_stem_base in canvas_replay_section_names
                ):
                    continue
            violations.append({
                "file": str(p.relative_to(impl_root)),
                "kind": "byte-identical-copy",
                "refSource": ref_src,
                "sha256": h[:12],
            })
        else:
            # Not a byte-identical copy — check for a re-encoded near-match
            # against the ref's own capture crops (the screenshot cheat).
            near_src = _near_match_ref(p)
            if near_src is not None:
                violations.append({
                    "file": str(p.relative_to(impl_root)),
                    "kind": "screenshot-asset-near-match",
                    "refSource": near_src,
                })

# Dedup by (file, kind, needle/refSource).
seen = set()
deduped = []
for v in violations:
    key = (v["file"], v["kind"], v.get("needle") or v.get("refSource", ""))
    if key in seen:
        continue
    seen.add(key)
    deduped.append(v)

status = "fail" if deduped else "pass"
result = {
    "schemaVersion": 1,
    "status": status,
    "implRoot": str(impl_root),
    "scannedTextFiles": scanned_text,
    "scannedBinaryFiles": scanned_binary,
    "refScreenshotCount": len(ref_screenshot_files),
    "violationCount": len(deduped),
    "violations": deduped[:50],
    "rule": (
        "Impl must not reference or contain copies of reference screenshot "
        "artifacts (tmp/ref/*/sections/, tmp/ref/*/static/, transitions, "
        "scroll-video, clip). Using ref screenshots as impl backgrounds "
        "fakes pixel-diff agreement without implementing the actual UI."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(f"ref-screenshot-asset: {len(deduped)} violation(s) / "
      f"{scanned_text}T+{scanned_binary}B files scanned → {out_path}")
sys.exit(0 if status == "pass" else 1)
PY
