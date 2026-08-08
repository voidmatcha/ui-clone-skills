#!/usr/bin/env bash
# transfer-fonts.sh — copy the ref's custom font binaries into impl/public so the
# mirrored @font-face url() references resolve instead of 404-ing to system
# fallbacks.
#
# Ground truth this fixes: a clone can mirror every @font-face rule byte-for-byte
# (via the css-mirror) yet ship ZERO font files under impl/public — every custom
# face then 404s and the browser silently substitutes a system font at the wrong
# weight, while asset-transfer reports "44/44 transferred" because fonts are
# outside its (visible-images) universe. This script closes that gap with a
# deterministic copy, not a prompt obligation.
#
# What it does: for every root-relative url() font reference in the ref CSS
# (e.g. url("/font/Pretendard-Regular.woff") or url("/_next/static/media/x.woff2")),
# plus source CSS relative font paths that normalize to /font/...
# (e.g. url("../../../font/NanumHumanRegular.otf")),
# find the matching downloaded binary under <ref-dir>/resources/<host>/<path> and
# copy it to <impl-public>/<path>, PRESERVING the exact URL path the CSS requests.
# Absolute (https://cdn/...) font URLs are left alone — the browser loads those
# from the CDN directly, so copying them into public/ would be dead weight.
#
# Usage: transfer-fonts.sh <ref-dir> [<impl-root-or-public-dir>]
#   Arg 2 may be the impl root (…/impl) or its public dir (…/impl/public); a
#   basename of "public" is treated as the public dir, otherwise "/public" is
#   appended. Omit it to resolve via scripts/extract/find-impl-root.sh.
#
# Output: <ref-dir>/font-transfer.json
#   { status, implPublicDir, totals:{referenced,transferred,missing,skipped},
#     transferred:[…], missing:[…], skipped:[…] }
#
# Exit: 0 on a clean run (report written), 2 on setup error. A run that transfers
# nothing because binaries were never downloaded is NOT a hard error here — the
# gap is surfaced in `missing[]` for the font-binaries presence check to fail on.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ARG="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: transfer-fonts.sh <ref-dir> [<impl-root-or-public-dir>]" >&2
  exit 2
fi

# Resolve the impl public dir.
if [ -z "$IMPL_ARG" ]; then
  PLUGIN_ROOT_CAND="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
  for cand_root in "$PLUGIN_ROOT_CAND" "$(cd "$(dirname "$0")/../.." && pwd)"; do
    [ -z "$cand_root" ] && continue
    RESOLVER="$cand_root/scripts/extract/find-impl-root.sh"
    if [ -f "$RESOLVER" ]; then
      IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
      [ -n "$IMPL_ROOT" ] && [ -d "$IMPL_ROOT" ] && IMPL_ARG="$IMPL_ROOT" && break
    fi
  done
fi

if [ -z "$IMPL_ARG" ]; then
  echo "▸ transfer-fonts: SKIP — impl root not found (pass it explicitly)" >&2
  cat > "$REF_DIR/font-transfer.json" <<'JSON'
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl root not found",
  "implPublicDir": "",
  "totals": {"referenced": 0, "transferred": 0, "missing": 0, "skipped": 0},
  "transferred": [],
  "missing": [],
  "skipped": []
}
JSON
  exit 0
fi

if [ "$(basename "$IMPL_ARG")" = "public" ]; then
  IMPL_PUBLIC="$IMPL_ARG"
else
  IMPL_PUBLIC="$IMPL_ARG/public"
fi

OUT_PATH="$REF_DIR/font-transfer.json"

python3 - "$REF_DIR" "$IMPL_PUBLIC" "$OUT_PATH" <<'PY'
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

ref_dir = Path(sys.argv[1])
impl_public = Path(sys.argv[2])
out_path = Path(sys.argv[3])

FONT_EXTS = ("woff2", "woff", "ttf", "otf", "eot")
_URL_RE = re.compile(
    r"""url\(\s*['"]?([^'")]+?\.(?:woff2|woff|ttf|otf|eot)(?:\?[^'")]*)?)['"]?\s*\)""",
    re.IGNORECASE,
)


def basename_of(p: str) -> str:
    return unquote(p.split("?")[0].rstrip("/").split("/")[-1]).lower()


def normalize_relative_font_url(raw: str) -> str | None:
    low = raw.lower()
    if low.startswith(("/", "http://", "https://", "//", "data:")):
        return None
    path, sep, query = raw.partition("?")
    normalized_path = path.replace("\\", "/")
    lowered_path = normalized_path.lower()
    if "/font/" in lowered_path:
        idx = lowered_path.rfind("/font/") + 1
    elif lowered_path.startswith("font/"):
        idx = 0
    elif normalized_path.startswith("../"):
        # A bundler chunk stylesheet (Next.js: _next/static/chunks/x.css) ships
        # under assets/ in the built impl, so the browser resolves its ../
        # references against the site root. Dropping these ships zero bytes for
        # a face the ref really loads, and a dev server answers the miss with
        # index.html at HTTP 200 — so no 404 ever surfaces the gap.
        idx = len(normalized_path) - len(normalized_path.lstrip("./"))
    else:
        return None
    normalized = "/" + normalized_path[idx:].lstrip("/")
    if sep:
        normalized = f"{normalized}?{query}"
    return normalized


# 1) Collect every font reference the browser could resolve — from the mirrored
#    ref CSS AND the JS bundles (Next.js emits font URLs like
#    "/_next/static/media/x.woff2" from its font-loader chunks, not a .css file).
#    Classify by how the browser resolves it: root-relative (same-origin → must
#    live in public/), absolute (loads from its own host → leave alone), or
#    relative (ambiguous).
scan_files: list[Path] = []
css_dir = ref_dir / "css"
if css_dir.is_dir():
    scan_files.extend(sorted(css_dir.rglob("*.css")))
bundles_dir = ref_dir / "bundles"
if bundles_dir.is_dir():
    scan_files.extend(sorted(
        p for p in bundles_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in (".js", ".mjs", ".cjs", ".css")
    ))

# Bare quoted root-relative font path, for JS bundles that reference a font
# without a css url() wrapper: "/_next/static/media/e9b4….woff2".
_QUOTED_FONT_RE = re.compile(
    r"""['"](/[^'"?\s]+?\.(?:woff2|woff|ttf|otf|eot))(?:\?[^'"]*)?['"]""",
    re.IGNORECASE,
)

# ref-relative-path -> reason for skipping / kind
root_refs: dict[str, dict] = {}   # urlpath -> {basename}
skipped_refs: list[dict] = []
_seen_skips: set[str] = set()


def note_skip(url: str, reason: str) -> None:
    key = f"{reason}:{url}"
    if key in _seen_skips:
        return
    _seen_skips.add(key)
    skipped_refs.append({"url": url, "reason": reason})


for src in scan_files:
    try:
        text = src.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for m in _URL_RE.finditer(text):
        raw = m.group(1).strip()
        low = raw.lower()
        if low.startswith(("http://", "https://", "//")):
            note_skip(raw, "absolute-url-loads-from-origin")
            continue
        if low.startswith("data:"):
            continue
        if raw.startswith("/"):
            path = raw.split("?")[0]
            root_refs.setdefault(path, {"basename": basename_of(path)})
        else:
            normalized = normalize_relative_font_url(raw)
            if normalized is not None:
                path = normalized.split("?")[0]
                root_refs.setdefault(path, {"basename": basename_of(path)})
            else:
                # Relative url() — resolution depends on the referencing file's own
                # location in impl; not deterministically placeable here.
                note_skip(raw, "relative-url-unresolved")
    # Bare quoted root-relative font paths (JS bundles).
    for m in _QUOTED_FONT_RE.finditer(text):
        path = m.group(1).split("?")[0]
        root_refs.setdefault(path, {"basename": basename_of(path)})

# 2) Index the downloaded font binaries under resources/<host>/<path>.
resources = ref_dir / "resources"
# suffix map: "/font/x.otf" (host-stripped path) -> resource Path
by_suffix: dict[str, Path] = {}
by_basename: dict[str, list[Path]] = {}
if resources.is_dir():
    for host_dir in resources.iterdir():
        if not host_dir.is_dir():
            continue
        for p in host_dir.rglob("*"):
            if not p.is_file() or p.suffix.lower().lstrip(".") not in FONT_EXTS:
                continue
            rel = "/" + str(p.relative_to(host_dir)).replace("\\", "/")
            by_suffix.setdefault(rel.lower(), p)
            by_basename.setdefault(p.name.lower(), []).append(p)


def find_source(urlpath: str, basename: str) -> Path | None:
    # Exact same-origin path (host-stripped) first, then a unique basename hit.
    hit = by_suffix.get(urlpath.lower())
    if hit is not None:
        return hit
    cands = by_basename.get(basename, [])
    if len(cands) == 1:
        return cands[0]
    # Ambiguous basename across multiple hosts: prefer one whose path tail matches.
    for c in cands:
        if str(c).lower().endswith(urlpath.lower()):
            return c
    return cands[0] if cands else None


# 3) Transfer each root-relative reference.
transferred: list[dict] = []
missing: list[dict] = []
for urlpath, meta in sorted(root_refs.items()):
    basename = meta["basename"]
    src = find_source(urlpath, basename)
    if src is None:
        missing.append({
            "urlPath": urlpath,
            "basename": basename,
            "reason": "referenced by CSS but no binary under resources/ (extraction gap)",
        })
        continue
    dest = impl_public / urlpath.lstrip("/")
    try:
        if dest.is_file() and dest.stat().st_size == src.stat().st_size:
            skipped_refs.append({"url": urlpath, "reason": "already-present-in-public"})
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        transferred.append({
            "urlPath": urlpath,
            "basename": basename,
            "from": str(src.relative_to(ref_dir)),
            "to": str(dest.relative_to(impl_public.parent)) if impl_public.parent in dest.parents else str(dest),
            "bytes": dest.stat().st_size,
        })
    except OSError as exc:
        missing.append({"urlPath": urlpath, "basename": basename, "reason": f"copy failed: {exc}"})

referenced = len(root_refs)
# The transfer action itself succeeds as long as no copy that had a source errored.
copy_errors = [m for m in missing if m["reason"].startswith("copy failed")]
status = "fail" if copy_errors else "pass"

result = {
    "schemaVersion": 1,
    "status": status,
    "implPublicDir": str(impl_public),
    "totals": {
        "referenced": referenced,
        "transferred": len(transferred),
        "missing": len(missing),
        "skipped": len(skipped_refs),
    },
    "transferred": transferred[:100],
    "missing": missing[:100],
    "skipped": skipped_refs[:100],
    "rule": (
        "Every root-relative url() font reference in the ref CSS, including "
        "relative font/ URLs normalized to /font/, must have its "
        "downloaded binary copied to impl/public at the same URL path. Absolute "
        "(CDN) font URLs load from their own origin and are intentionally left "
        "in place. missing[] entries are extraction gaps (binary never "
        "downloaded), surfaced for the font-binaries presence check."
    ),
}
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"transfer-fonts: referenced {referenced}, "
    f"transferred {len(transferred)}, missing {len(missing)}, "
    f"skipped {len(skipped_refs)} → {status}"
)
sys.exit(0)
PY
