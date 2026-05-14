#!/usr/bin/env bash
# asset-transfer-check.sh — verify ref site assets actually transferred to
# impl/public/. Closes the failure class:
#   "Agent extracted visible-images.json (cataloged) but skipped the actual
#    file download to impl/public/ — page.tsx then renders gradient placeholders
#    where photos should be, section-compare AE explodes to 1M+ even though
#    DOM structure is correct."
#
# This is a stronger check than image-fidelity-check.sh (which only verifies
# the impl SOURCE references the URLs). Here we verify the actual FILES exist
# in impl/public/ so Next.js can serve them.
#
# Usage: asset-transfer-check.sh <ref-dir> [<impl-public-dir>]
#   ref-dir            tmp/ref/<component> with visible-images.json
#   impl-public-dir    impl/public/ directory; auto-detected from common
#                      locations if omitted (benchmark/work/<sha>/impl/public,
#                      apps/<comp>/public, app/public, public)
#
# Schema (output JSON `<ref-dir>/asset-transfer.json`):
#   {
#     "schemaVersion": 1,
#     "status": "pass" | "fail" | "skip",
#     "total": N,            # total non-substituted visible-image entries
#     "transferred": M,      # of those, M found in impl/public
#     "missing": [...basenames...],
#     "substituted": K,      # entries skipped because declared in asset-substitution.json
#     "implPublicDir": "..." # what we actually checked
#   }
#
# Status rules:
#   skip  — visible-images.json absent (nothing to check)
#   pass  — every non-substituted entry has a matching basename in impl/public/
#   fail  — at least one missing
#
# Exit: 0 on pass/skip, 1 on fail.

set -euo pipefail

REF_DIR="${1:?Usage: asset-transfer-check.sh <ref-dir> [<impl-public-dir>]}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

VISIBLE="$REF_DIR/visible-images.json"
SUBST="$REF_DIR/asset-substitution.json"
OUT="$REF_DIR/asset-transfer.json"

# Auto-detect impl/public/ if not provided
IMPL_PUB="${2:-}"
if [ -z "$IMPL_PUB" ]; then
  CANDIDATES=(
    "$(dirname "$REF_DIR")/../impl/public"   # benchmark/work/<sha>/{ref,impl}/public
    "apps/$(basename "$REF_DIR")/public"
    "app/public"
    "public"
  )
  for c in "${CANDIDATES[@]}"; do
    if [ -d "$c" ]; then IMPL_PUB="$c"; break; fi
  done
fi

if [ ! -f "$VISIBLE" ]; then
  python3 -c "
import json
json.dump({'schemaVersion': 1, 'status': 'skip', 'total': 0, 'transferred': 0, 'missing': [], 'substituted': 0, 'implPublicDir': '$IMPL_PUB', 'reason': 'visible-images.json absent'}, open('$OUT','w'), indent=2)
"
  echo "▸ asset-transfer: SKIP (no visible-images.json)"
  exit 0
fi

if [ -z "$IMPL_PUB" ] || [ ! -d "$IMPL_PUB" ]; then
  python3 -c "
import json
json.dump({'schemaVersion': 1, 'status': 'fail', 'total': -1, 'transferred': 0, 'missing': [], 'substituted': 0, 'implPublicDir': '$IMPL_PUB', 'reason': 'impl public dir not found — pass it explicitly or scaffold impl first'}, open('$OUT','w'), indent=2)
"
  echo "✗ asset-transfer: FAIL — impl public dir not found" >&2
  exit 1
fi

python3 - "$VISIBLE" "$SUBST" "$IMPL_PUB" "$OUT" <<'PY'
import json, os, re, sys
from pathlib import Path
from urllib.parse import urlparse, unquote

visible_path, subst_path, impl_pub, out_path = sys.argv[1:5]
visible = json.loads(Path(visible_path).read_text(encoding='utf-8'))
# visible-images.json schema varies. Common shapes:
#   list of dicts with 'src'/'originalSrc' (and maybe width/height)
#   dict with 'images': [...]
if isinstance(visible, dict):
    entries = visible.get('images') or visible.get('items') or []
elif isinstance(visible, list):
    entries = visible
else:
    entries = []

# Substitutions — skip these entries
substituted_originals = set()
if Path(subst_path).is_file():
    try:
        s = json.loads(Path(subst_path).read_text(encoding='utf-8'))
        for img in (s.get('images') or []):
            for k in ('originalSrc', 'original'):
                v = img.get(k)
                if isinstance(v, str):
                    substituted_originals.add(Path(urlparse(unquote(v)).path).name.lower())
    except Exception:
        pass

# Index impl/public/ basenames (case-insensitive)
public_basenames = set()
for p in Path(impl_pub).rglob('*'):
    if p.is_file():
        public_basenames.add(p.name.lower())
        # Also stem-only (no extension) for cases where impl uses .webp instead of .jpg
        public_basenames.add(p.stem.lower())

total = 0
transferred = 0
substituted_count = 0
missing = []

for e in entries:
    src = e.get('src') if isinstance(e, dict) else (e if isinstance(e, str) else None)
    if not src or not isinstance(src, str):
        continue
    # Strip query/fragment, take basename
    parsed = urlparse(unquote(src))
    bn = Path(parsed.path).name.lower()
    if not bn:
        continue
    # data: URLs / about:blank — skip (not transferable)
    if parsed.scheme in ('data', 'about', 'blob'):
        continue
    total += 1
    if bn in substituted_originals:
        substituted_count += 1
        continue
    stem = Path(bn).stem.lower()
    if bn in public_basenames or stem in public_basenames:
        transferred += 1
    else:
        missing.append(bn)

# Status: pass if every NON-substituted entry is present
non_sub_total = total - substituted_count
status = 'pass' if non_sub_total == transferred else 'fail'

json.dump({
    'schemaVersion': 1,
    'status': status,
    'total': total,
    'transferred': transferred,
    'substituted': substituted_count,
    'missing': missing[:50],   # cap the list
    'missingCount': len(missing),
    'implPublicDir': impl_pub,
}, open(out_path, 'w'), indent=2)

if status == 'pass':
    print(f"✓ asset-transfer: PASS — {transferred}/{non_sub_total} non-substituted entries present in {impl_pub} ({substituted_count} substituted)")
    sys.exit(0)
else:
    sample = ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else "")
    print(f"✗ asset-transfer: FAIL — {transferred}/{non_sub_total} present, {len(missing)} missing in {impl_pub}", file=sys.stderr)
    print(f"  missing examples: {sample}", file=sys.stderr)
    print(f"  fix: bash scripts/extract/extract-assets.sh <session> {os.path.dirname(visible_path) or '.'} {impl_pub}", file=sys.stderr)
    sys.exit(1)
PY
