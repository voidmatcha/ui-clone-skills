#!/usr/bin/env bash
# css-mirror-check.sh — fail when impl CSS bulk-imports / bulk-pastes
# the reference site's compiled CSS bundle.
#
#
# Detection — three orthogonal signals on impl CSS files
# (impl/src/**/*.{css,scss,sass,less,module.css} + impl/index.css if
# present):
#
#   1. external @import to anything matching the ref's CSS bundle host
#      or filename pattern from bundle-map.json
#   2. byte-identical copy of any file under <ref>/bundles/*.css
#      (sha256 match)
#   3. impl CSS file >70% difflib quick_ratio similarity to any ref
#      CSS bundle (catches whitespace-stripped paste, single-letter
#      identifier rename, etc.)
#
# Tension with current guidance: skills/ui-reverse-engineering/
# css-first-generation.md tells agents to USE extracted ref CSS, but
# the intent is "use the tokens / per-section snippets", NOT "paste the
# entire 800KB compiled bundle". This gate enforces that distinction.
# Files explicitly under impl/src/styles/from-ref/ are allowlisted so
# legitimate per-section snippets aren't false-positives — agents
# wanting to reuse ref CSS must put it under that path or scope the
# import.
#
# Usage:
#   css-mirror-check.sh <ref-dir> [<impl-root>]
#
# Output: <ref-dir>/css-mirror.json
#
# Exit: 0 pass, 1 fail, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ROOT="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: css-mirror-check.sh <ref-dir> [<impl-root>]" >&2
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

OUT_PATH="$REF_DIR/css-mirror.json"

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  cat > "$OUT_PATH" <<JSON
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl_root not found",
  "violations": []
}
JSON
  echo "css-mirror: skip (no impl)"
  exit 0
fi

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT_PATH" <<'PY'
import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_root = Path(sys.argv[2])
out_path = Path(sys.argv[3])

violations: list[dict] = []


def read_safe(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


# Build the ref CSS bundle catalog.
ref_bundles_dir = ref_dir / "bundles"
ref_css_bodies: dict[str, str] = {}  # rel path → body
ref_css_hashes: dict[str, str] = {}  # sha256 → rel path
if ref_bundles_dir.is_dir():
    for css_path in ref_bundles_dir.glob("*.css"):
        body = read_safe(css_path)
        if len(body) < 500:  # ignore tiny shims
            continue
        rel = str(css_path.relative_to(ref_dir))
        ref_css_bodies[rel] = body
        ref_css_hashes[sha256_of_text(body)] = rel


# Watchlist: filenames + hostnames lifted from bundle-map.json that an
# @import / url() would resolve to.
bundle_map_path = ref_dir / "bundle-map.json"
ref_css_filenames: set[str] = set()
ref_css_hosts: set[str] = set()
if bundle_map_path.is_file():
    try:
        bm = json.loads(read_safe(bundle_map_path))
        urls: list[str] = []
        urls.extend(bm.get("cssUrls", []) or [])
        urls.extend(bm.get("stylesheetUrls", []) or [])
        for entry in bm.get("stylesheets", []) or []:
            if isinstance(entry, dict):
                href = entry.get("href")
                if href:
                    urls.append(href)
        for u in urls:
            if not isinstance(u, str):
                continue
            name = u.rstrip("/").split("/")[-1].split("?")[0].lower()
            if name.endswith(".css"):
                ref_css_filenames.add(name)
            m = re.match(r"https?://([^/\:\?\#]+)", u)
            if m:
                ref_css_hosts.add(m.group(1).lower())
    except (OSError, ValueError):
        pass


# Also fold any *.css filename present under <ref>/bundles into the
# watchlist (even when bundle-map.json doesn't enumerate URLs).
for rel in ref_css_bodies:
    ref_css_filenames.add(rel.rsplit("/", 1)[-1].lower())


# Collect impl CSS files.
CSS_SUFFIXES = {".css", ".scss", ".sass", ".less", ".module.css"}
ALLOWLIST_PREFIX = "src/styles/from-ref/"
EXCLUDE_DIRS = {"node_modules", ".next", ".svelte-kit", "dist", "build",
                ".turbo", ".cache", ".git"}

impl_files: list[Path] = []
for p in impl_root.rglob("*"):
    if not p.is_file() or p.suffix not in CSS_SUFFIXES:
        continue
    if any(part in EXCLUDE_DIRS for part in p.parts):
        continue
    try:
        rel_parts = p.relative_to(impl_root).parts
    except ValueError:
        continue
    rel_str = "/".join(rel_parts)
    if rel_str.startswith(ALLOWLIST_PREFIX):
        # Agent explicitly opted-in for ref CSS reuse under this path.
        continue
    impl_files.append(p)


# Patterns for external @import and url() to CSS files.
IMPORT_RE = re.compile(
    r"@import\s+(?:url\()?\s*[\"']?(https?://[^\"'\)\s]+|[^\"'\s\);]+\.css)",
    re.IGNORECASE,
)


scanned = 0
for path in impl_files:
    scanned += 1
    text = read_safe(path)
    if not text:
        continue
    rel = str(path.relative_to(impl_root))

    # Signal 1 — @import against ref hosts / filenames.
    # Universality audit HIGH FP: basename-only match would
    # flag any unrelated `style.css` / `main.css` / `app.css` import
    # since these names collide across the entire web. Only flag
    # basename matches when paired with EITHER (a) the ref host or
    # (b) a non-generic basename (length >= 8 OR contains a hash/
    # digit segment).
    GENERIC_NAMES = {
        "style.css", "styles.css", "main.css", "app.css", "index.css",
        "global.css", "globals.css", "common.css", "site.css",
        "reset.css", "normalize.css", "modern-normalize.css",
        "bootstrap.css", "bootstrap.min.css", "tailwind.css",
    }
    for m in IMPORT_RE.finditer(text):
        url = m.group(1).strip()
        host = ""
        if url.startswith(("http://", "https://")):
            mh = re.match(r"https?://([^/\:\?\#]+)", url)
            if mh:
                host = mh.group(1).lower()
        name = url.rstrip("/").split("/")[-1].split("?")[0].lower()
        if host and host in ref_css_hosts:
            violations.append({
                "kind": "import-from-ref-host",
                "file": rel,
                "url": url[:200],
            })
        elif name in ref_css_filenames and name not in GENERIC_NAMES:
            # Non-generic basename match — likely the ref's authored
            # bundle name (e.g. naver-main.min.css, dga-loginPage-abc123.css).
            violations.append({
                "kind": "import-of-ref-css-filename",
                "file": rel,
                "url": url[:200],
            })

    # Signal 2 — byte-identical copy by sha256.
    h = sha256_of_text(text)
    if h in ref_css_hashes:
        violations.append({
            "kind": "byte-identical-copy",
            "file": rel,
            "refSource": ref_css_hashes[h],
            "sha256": h[:12],
        })
        # If the file IS a byte-identical copy, similarity check is
        # redundant noise — skip the difflib pass.
        continue

    # Signal 3 — content similarity. Compare first 4000 chars against
    # each ref bundle. Cheap quick_ratio; threshold 0.70 mirrors
    # html-paste's CSS branch.
    #
    PREFLIGHT_BLOCK_RES = [
        # Tailwind preflight banner + universal selector block.
        re.compile(
            r"/\*!\s*tailwindcss[^*]*\*/.*?(?=\n[a-zA-Z0-9.#@\-:\*])",
            re.DOTALL,
        ),
        # normalize.css / modern-normalize banner.
        re.compile(
            r"/\*!\s*(?:normalize\.css|modern-normalize)[^*]*\*/.*?(?=\n[a-zA-Z0-9.#@\-:\*])",
            re.DOTALL | re.IGNORECASE,
        ),
        # Universal selector resets (used by Tailwind, Eric Meyer
        # reset, Bootstrap reboot — same shape, can't tell apart).
        re.compile(
            r"\*\s*,\s*::before\s*,\s*::after\s*\{[^}]*\}",
            re.DOTALL,
        ),
        re.compile(
            r"\*\s*,\s*\*::before\s*,\s*\*::after\s*\{[^}]*\}",
            re.DOTALL,
        ),
        # Meyer reset: huge list of HTML5-era selectors set to margin/
        # padding/border 0.
        re.compile(
            r"html\s*,\s*body\s*,\s*div\s*,\s*span\s*,[^{]{50,}?\{[^}]*\}",
            re.DOTALL,
        ),
        # Bootstrap reboot `:root` color-scheme block.
        re.compile(
            r":root\s*\{[^}]*--bs-[^}]*\}",
            re.DOTALL,
        ),
        # ::backdrop reset.
        re.compile(
            r"::backdrop\s*\{[^}]*\}",
            re.DOTALL,
        ),
        # html/body line-height reset (Tailwind preflight pattern).
        re.compile(
            r"\b(?:html|body)\s*\{[^}]*line-height[^}]*\}",
            re.DOTALL,
        ),
        # Block-element default margin reset (normalize-family).
        re.compile(
            r"\bh[1-6]\s*,\s*p\s*,[^{]{20,}?\{\s*margin:\s*0[^}]*\}",
            re.DOTALL | re.IGNORECASE,
        ),
    ]

    def strip_resets(s: str) -> str:
        for pat in PREFLIGHT_BLOCK_RES:
            s = pat.sub("", s)
        return s

    if len(text) >= 500:
        text_head = strip_resets(text[:8000])[:4000]
        for ref_rel, ref_body in ref_css_bodies.items():
            ref_head = strip_resets(ref_body[:8000])[:4000]
            if len(text_head) < 400 or len(ref_head) < 400:
                # After reset removal there isn't enough authored CSS
                # to make the similarity check meaningful.
                continue
            ratio = SequenceMatcher(None, text_head, ref_head).quick_ratio()
            if ratio >= 0.70:
                violations.append({
                    "kind": "content-similar-to-ref-bundle",
                    "file": rel,
                    "refSource": ref_rel,
                    "similarity": round(ratio, 3),
                })
                break  # one match per file is enough


# Dedup by (file, kind, refSource/url).
seen = set()
deduped = []
for v in violations:
    key = (
        v["file"], v["kind"],
        v.get("refSource") or v.get("url", ""),
    )
    if key in seen:
        continue
    seen.add(key)
    deduped.append(v)


status = "fail" if deduped else "pass"
result = {
    "schemaVersion": 1,
    "status": status,
    "implRoot": str(impl_root),
    "scannedCssFiles": scanned,
    "refBundleCount": len(ref_css_bodies),
    "refCssFilenameWatchlist": sorted(ref_css_filenames)[:30],
    "refCssHostWatchlist": sorted(ref_css_hosts)[:10],
    "violationCount": len(deduped),
    "violations": deduped[:50],
    "rule": (
        "Impl CSS must not @import the reference site's CSS host/filename, "
        "must not byte-identical-copy any file under <ref>/bundles/*.css, "
        "and must not be >=70% similar to any ref CSS bundle. Per-section "
        "snippets reused from ref CSS are allowed only under "
        "impl/src/styles/from-ref/."
    ),
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"css-mirror: {len(deduped)} violation(s) / "
    f"{scanned} impl CSS file(s) scanned → {status} → {out_path}"
)
sys.exit(0 if status == "pass" else 1)
PY
