#!/usr/bin/env bash
# required-media-coverage-check.sh — fail when ref's required video /
# Lottie assets are absent from impl public/ or unreferenced in impl src/.
#
#
# Inputs:
#   <ref-dir>/required-media.json    — produced by extract/required-media.sh
#
# Output: <ref-dir>/required-media-coverage.json
#   { status, implRoot, totals, missing: {video: [...], lottie: [...]} }
#
# Exit: 0 pass, 1 fail (missing transfers or references), 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: required-media-coverage-check.sh <ref-dir> [<impl-root>]" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/required-media-coverage.json"

REQUIRED="$REF_DIR/required-media.json"
if [ ! -f "$REQUIRED" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "pass",
  "reason": "required-media.json absent — extractor (Step 6b-bis) has not run; nothing to enforce at this gate",
  "missing": {"video": [], "lottie": [], "svg": []}
}
JSON
  echo "required-media-coverage: pass (no required-media.json — extractor not run)"
  exit 0
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

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl_root not found",
  "missing": {"video": [], "lottie": []}
}
JSON
  echo "required-media-coverage: skip (no impl)"
  exit 0
fi

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT_PATH" <<'PY'
# Python 3.9 compat for PEP 604 unions used below — defer
# annotation evaluation so `X | Y` is parsed as a string.
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ref_dir = Path(sys.argv[1])
impl_root = Path(sys.argv[2])
out_path = Path(sys.argv[3])

required_path = ref_dir / "required-media.json"
required = json.loads(required_path.read_text(encoding="utf-8"))

def _as_list(v) -> list:
    return v if isinstance(v, list) else []


videos = _as_list(required.get("videos"))
lottie_urls = _as_list(required.get("lottie"))
svg_urls = _as_list(required.get("svgs"))

# If ref has neither video, Lottie, nor SVG, this gate is a no-op.
if not videos and not lottie_urls and not svg_urls:
    out_path.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "pass",
        "implRoot": str(impl_root),
        "reason": "ref has no required video, Lottie, or SVG media",
        "totals": {"video": 0, "lottie": 0, "svg": 0},
        "missing": {"video": [], "lottie": [], "svg": []},
    }, indent=2) + "\n", encoding="utf-8")
    print("required-media-coverage: pass (no required media)")
    sys.exit(0)


# Build the basename → relative-path map for impl/public/ files.
public_files: dict[str, list[str]] = {}
public_dir = impl_root / "public"
if public_dir.is_dir():
    for p in public_dir.rglob("*"):
        if p.is_file():
            name = p.name.lower()
            rel = str(p.relative_to(impl_root))
            public_files.setdefault(name, []).append(rel)


# Collect impl source text for reference scanning.
SRC_EXCLUDE = {"node_modules", ".next", "dist", "build", ".turbo", ".cache"}
SRC_SUFFIXES = {".tsx", ".jsx", ".ts", ".js", ".mjs", ".cjs",
                ".css", ".scss", ".html", ".vue", ".svelte", ".json"}
src_blobs: dict[str, str] = {}
for sub in ("src", "app", "pages"):
    sub_dir = impl_root / sub
    if not sub_dir.is_dir():
        continue
    for p in sub_dir.rglob("*"):
        if not p.is_file() or p.suffix not in SRC_SUFFIXES:
            continue
        if any(part in SRC_EXCLUDE for part in p.parts):
            continue
        try:
            src_blobs[str(p.relative_to(impl_root))] = p.read_text(
                encoding="utf-8", errors="ignore",
            )
        except OSError:
            continue


def url_basename(u: str) -> str:
    parsed = urlparse(u)
    path = parsed.path or u
    name = path.rstrip("/").split("/")[-1].split("?")[0]
    return name.lower()


def is_in_public(basename: str) -> list[str]:
    return public_files.get(basename, [])


def is_referenced_in_src(needles: list[str]) -> tuple[bool, str | None]:
    for rel, blob in src_blobs.items():
        for needle in needles:
            if needle and needle in blob:
                return True, rel
    return False, None


missing_videos: list[dict] = []
for v in videos:
    src = v.get("src", "")
    if not src:
        continue
    basename = url_basename(src)
    public_hits = is_in_public(basename)
    # Build needle list — basename plus a normalized impl path
    # (everything Vite/Next would emit when import-pathed: /<sub>/name).
    needles = [basename, src.rsplit("/", 1)[-1].split("?")[0]]
    for hit in public_hits:
        # Reference can be either the basename or a path starting at
        # /<sub>/... (the public-served path).
        needles.append("/" + hit.split("public/", 1)[-1])
    needles = list({n for n in needles if n})
    ref_ok, ref_file = is_referenced_in_src(needles)
    if not public_hits or not ref_ok:
        missing_videos.append({
            "section": v.get("section"),
            "src": src,
            "basename": basename,
            "publicHit": public_hits[0] if public_hits else None,
            "referencedIn": ref_file,
            "kind": (
                "missing-from-public" if not public_hits
                else "not-referenced-in-src"
            ),
        })


missing_lottie: list[dict] = []
for l in lottie_urls:
    path = l.get("path", "")
    if not path:
        continue
    basename = url_basename(path)
    public_hits = is_in_public(basename)
    needles = [basename, path.rsplit("/", 1)[-1].split("?")[0]]
    for hit in public_hits:
        needles.append("/" + hit.split("public/", 1)[-1])
    needles = list({n for n in needles if n})
    ref_ok, ref_file = is_referenced_in_src(needles)
    if not public_hits or not ref_ok:
        missing_lottie.append({
            "path": path,
            "basename": basename,
            "evidenceFile": l.get("evidenceFile"),
            "publicHit": public_hits[0] if public_hits else None,
            "referencedIn": ref_file,
            "kind": (
                "missing-from-public" if not public_hits
                else "not-referenced-in-src"
            ),
        })


# Detect Lottie runtime package — even if URLs match, missing the
# runtime means the .json files just sit on disk. Reuse the
# lottie-runtime-check semantics minimally: parse impl/package.json.
lottie_pkg_ok = True
if lottie_urls:
    pkg_json = impl_root / "package.json"
    if pkg_json.is_file():
        try:
            pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
            all_deps: dict[str, str] = {}
            for k in ("dependencies", "devDependencies"):
                d = pkg_data.get(k) or {}
                if isinstance(d, dict):
                    all_deps.update({kk: str(vv) for kk, vv in d.items()})
            lottie_pkgs = {
                "lottie-web", "lottie-react", "@lottiefiles/react-lottie-player",
                "@lottiefiles/lottie-player", "@dotlottie/react-player",
                "@lottiefiles/dotlottie-react", "bodymovin",
            }
            lottie_pkg_ok = any(p in all_deps for p in lottie_pkgs)
        except (OSError, ValueError):
            lottie_pkg_ok = False
    else:
        lottie_pkg_ok = False


# SVG coverage — same transfer + reference check as video / Lottie.
missing_svgs: list[dict] = []
for s in svg_urls:
    src = s.get("src", "")
    if not src or src.startswith("data:"):
        continue
    basename = url_basename(src)
    public_hits = is_in_public(basename)
    needles = [basename, src.rsplit("/", 1)[-1].split("?")[0]]
    for hit in public_hits:
        needles.append("/" + hit.split("public/", 1)[-1])
    needles = list({n for n in needles if n})
    ref_ok, ref_file = is_referenced_in_src(needles)
    if not public_hits or not ref_ok:
        missing_svgs.append({
            "section": s.get("section"),
            "src": src,
            "basename": basename,
            "kind_origin": s.get("kind"),
            "evidenceFile": s.get("evidenceFile"),
            "publicHit": public_hits[0] if public_hits else None,
            "referencedIn": ref_file,
            "kind": (
                "missing-from-public" if not public_hits
                else "not-referenced-in-src"
            ),
        })


total_missing = (
    len(missing_videos) + len(missing_lottie) + len(missing_svgs)
)
runtime_missing = (lottie_urls and not lottie_pkg_ok)
status = "fail" if (total_missing or runtime_missing) else "pass"

result = {
    "schemaVersion": 1,
    "status": status,
    "implRoot": str(impl_root),
    "totals": {
        "videoRequired": len(videos),
        "lottieRequired": len(lottie_urls),
        "svgRequired": len(svg_urls),
        "videoMissing": len(missing_videos),
        "lottieMissing": len(missing_lottie),
        "svgMissing": len(missing_svgs),
    },
    "lottieRuntimePackageInstalled": lottie_pkg_ok,
    "missing": {
        "video": missing_videos[:30],
        "lottie": missing_lottie[:30],
        "svg": missing_svgs[:30],
    },
    "rule": (
        "Every entry in required-media.json (videos from html/*.json + "
        "Lottie paths from bundles/*.js + SVG URLs from <img>/<use>/CSS "
        "url(...svg) captures) must be transferred to impl/public/ AND "
        "referenced in impl source. If ref has Lottie URLs, "
        "impl/package.json must declare a Lottie runtime "
        "(lottie-web / lottie-react / @lottiefiles/* / etc)."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"required-media-coverage: "
    f"video {len(videos) - len(missing_videos)}/{len(videos)}, "
    f"lottie {len(lottie_urls) - len(missing_lottie)}/{len(lottie_urls)}, "
    f"svg {len(svg_urls) - len(missing_svgs)}/{len(svg_urls)}, "
    f"runtime-pkg={lottie_pkg_ok} → {status}"
)
sys.exit(0 if status == "pass" else 1)
PY
