#!/usr/bin/env bash
# emit-preflight-neutralize.sh — restore the UA typographic defaults that
# Tailwind's `@tailwind base` (Preflight) strips, so a ref that relied on the
# browser's default bold headings keeps them.
#
# Ground truth this fixes: Preflight resets `h1..h6 { font-size: inherit;
# font-weight: inherit; }` and zeroes margins. A ref like ebay ships ZERO
# explicit h1 font-weight rules (it relies on the UA default 700); once the impl
# pulls in @tailwind base, every heading collapses to the body weight (700→400)
# and typography-parity fails post-generation. This emits a deterministic
# restoration layer instead of leaving it to prompt discipline.
#
# It writes <impl>/src/styles/from-ref/preflight-neutralize.css (the canonical
# artifact) and idempotently injects the same rules INLINE, wrapped in
# `@layer base { … }`, immediately AFTER the `@tailwind base;` line in the impl's
# entry CSS. Two reasons the rules go in `@layer base` inline rather than via an
# `@import`:
#   * cascade — Preflight lives in `@layer base`; putting the restorations later
#     in the same layer makes them win over Preflight, while ANY unlayered ref
#     rule (the mirrored ref CSS is unlayered) still beats the whole base layer,
#     so the ref always wins where it declares a heading — the "unless the ref
#     overrides it" part is free, regardless of import order;
#   * robustness — a mid-file `@import` violates CSS ordering (postcss-import can
#     drop it), whereas an inline `@layer base` block works in any Tailwind v3 +
#     Vite/PostCSS setup with no plugin assumptions.
#
# Usage: emit-preflight-neutralize.sh <ref-dir> [<impl-root-or-src-dir>]
# Output: <impl>/src/styles/from-ref/preflight-neutralize.css
#         <ref-dir>/preflight-neutralize.json  (report)
# Exit: 0 on success (report written), 2 on setup error.

set -uo pipefail

REF_DIR="${1:-}"
IMPL_ARG="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: emit-preflight-neutralize.sh <ref-dir> [<impl-root-or-src-dir>]" >&2
  exit 2
fi

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

OUT_PATH="$REF_DIR/preflight-neutralize.json"

if [ -z "$IMPL_ARG" ]; then
  echo "▸ emit-preflight-neutralize: SKIP — impl root not found (pass it explicitly)" >&2
  cat > "$OUT_PATH" <<'JSON'
{
  "schemaVersion": 1,
  "status": "skip",
  "reason": "impl root not found",
  "cssPath": "",
  "injectedInto": [],
  "importStatement": ""
}
JSON
  exit 0
fi

python3 - "$REF_DIR" "$IMPL_ARG" "$OUT_PATH" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
impl_arg = Path(sys.argv[2])
out_path = Path(sys.argv[3])

# Resolve the impl src dir.
if impl_arg.name in {"src", "app", "pages"}:
    src_dir = impl_arg
else:
    src_dir = next(
        (impl_arg / n for n in ("src", "app", "pages") if (impl_arg / n).is_dir()),
        impl_arg / "src",
    )

styles_dir = src_dir / "styles" / "from-ref"
css_path = styles_dir / "preflight-neutralize.css"

MARK_START = "/* preflight-neutralize:start (auto) */"
MARK_END = "/* preflight-neutralize:end */"

# The UA typographic defaults Preflight strips, in `@layer base` so they beat
# Preflight (same layer, later) while any unlayered ref rule still beats the
# whole layer. Values are the WHATWG/CSS2 UA stylesheet defaults.
LAYER_BLOCK = """\
@layer base {
  h1 { font-size: 2em; font-weight: bold; margin-block: 0.67em; }
  h2 { font-size: 1.5em; font-weight: bold; margin-block: 0.83em; }
  h3 { font-size: 1.17em; font-weight: bold; margin-block: 1em; }
  h4 { font-size: 1em; font-weight: bold; margin-block: 1.33em; }
  h5 { font-size: 0.83em; font-weight: bold; margin-block: 1.67em; }
  h6 { font-size: 0.67em; font-weight: bold; margin-block: 2.33em; }
  b, strong { font-weight: bold; }
  em, i, cite, dfn, var, address { font-style: italic; }
  small { font-size: 80%; }
}
"""

NEUTRALIZE_CSS = (
    "/* preflight-neutralize.css — restores the UA typographic defaults that\n"
    " * Tailwind's Preflight (@tailwind base) removes. These live in @layer base\n"
    " * so they override Preflight but lose to the (unlayered) mirrored ref CSS,\n"
    " * i.e. the ref always wins where it declares a heading. The pipeline also\n"
    " * injects this block inline right after `@tailwind base;`; this file is the\n"
    " * canonical copy for reference / manual `@import ... layer(base)` use. */\n"
    + LAYER_BLOCK
)

styles_dir.mkdir(parents=True, exist_ok=True)
css_path.write_text(NEUTRALIZE_CSS, encoding="utf-8")

# Idempotently inject the @layer base block right after `@tailwind base;` in the
# impl's entry CSS (any *.css under src that pulls in @tailwind base).
_TW_BASE_RE = re.compile(r"@tailwind\s+base\s*;", re.IGNORECASE)
inline_block = MARK_START + "\n" + LAYER_BLOCK + MARK_END
injected_into: list[str] = []
already_present: list[str] = []
entry_candidates = [p for p in src_dir.rglob("*.css")
                    if "node_modules" not in p.parts and p != css_path]


def rel_label(entry: Path) -> str:
    return str(entry.relative_to(impl_arg)) if impl_arg in entry.parents else str(entry)


for entry in entry_candidates:
    try:
        text = entry.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if not _TW_BASE_RE.search(text):
        continue
    if MARK_START in text:
        already_present.append(rel_label(entry))
        continue
    new_text = _TW_BASE_RE.sub(lambda m: m.group(0) + "\n" + inline_block, text, count=1)
    entry.write_text(new_text, encoding="utf-8")
    injected_into.append(rel_label(entry))

result = {
    "schemaVersion": 1,
    "status": "pass",
    "cssPath": str(css_path),
    "injectedInto": injected_into,
    "alreadyPresent": already_present,
    "mechanism": "inline @layer base block after @tailwind base",
    "note": (
        "If injectedInto and alreadyPresent are both empty, no entry CSS with "
        "`@tailwind base;` was found yet — add `@import "
        "\"./styles/from-ref/preflight-neutralize.css\" layer(base);` right after "
        "@tailwind base once the scaffold exists."
    ),
}
out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(
    f"emit-preflight-neutralize: wrote {css_path.name}, "
    f"injected into {len(injected_into)} entry CSS file(s), "
    f"{len(already_present)} already had it"
)
sys.exit(0)
PY
