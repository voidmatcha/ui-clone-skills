#!/usr/bin/env bash
# asset-utilization-check.sh — fail when downloaded assets are not actually
# referenced from impl source.
#
# Closes the failure class observed on the c9b638d benchmark:
#   1. agent runs Phase 2.5 → downloads 45 images into impl/public/images/
#   2. agent writes a monolithic page.tsx that references 2 of them
#   3. section-compare AE stays catastrophic because 43 expected images render
#      as gradient placeholders / colored blocks
#   4. asset-transfer-check.sh PASSES (files exist on disk) and the orphan
#      ratio (~95%) is invisible to every other gate
#
# This check audits the OTHER direction: not "are files on disk" but
# "is the impl source actually referencing the files we downloaded?"
#
# Usage: asset-utilization-check.sh <ref-dir> [<impl-src-dir>]
#   ref-dir         the canonical ref dir (e.g. tmp/ref/<component>)
#   impl-src-dir    impl/src/ directory; auto-detected from common locations
#                   if omitted (benchmark/work/<sha>/impl/src, apps/<c>/src).
#
# Reads:
#   <ref-dir>/visible-images.json       — list of ref images (filtered by
#                                          src + element later via basename)
#   <ref-dir>/asset-substitution.json   — entries matching `images[]` patterns
#                                          are skipped from the count
#
# Writes:
#   <ref-dir>/asset-utilization.json    — schemaVersion 1, status,
#                                          downloaded, referenced, ratio,
#                                          orphans[], implSrcDir, reason
#
# Pass criteria:
#   pass  — at least 60% of non-substituted visible images are referenced
#           somewhere in impl/src/**/*.{tsx,ts,jsx,js,css,scss}
#   fail  — under 60% referenced (orphan ratio too high)
#   skip  — visible-images.json absent OR impl src absent OR fewer than 5
#           non-substituted images (statistical threshold too small)

set -euo pipefail

REF_DIR="${1:?Usage: asset-utilization-check.sh <ref-dir> [<impl-src-dir>]}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

VISIBLE="$REF_DIR/visible-images.json"
SUBST="$REF_DIR/asset-substitution.json"
OUT="$REF_DIR/asset-utilization.json"
THRESHOLD="${ASSET_UTILIZATION_THRESHOLD:-0.6}"
MIN_SAMPLE="${ASSET_UTILIZATION_MIN_SAMPLE:-5}"

# Auto-detect impl/src/ if not provided
IMPL_SRC="${2:-}"
if [ -z "$IMPL_SRC" ]; then
  CANDIDATES=(
    "$(dirname "$REF_DIR")/../impl/src"
    "$(dirname "$REF_DIR")/impl/src"
    "apps/$(basename "$REF_DIR")/src"
    "app/src"
    "src"
  )
  for c in "${CANDIDATES[@]}"; do
    if [ -d "$c" ]; then IMPL_SRC="$c"; break; fi
  done
fi

write_json() {
  python3 - "$OUT" "$@" <<'PY'
import json
import sys
out_path = sys.argv[1]
payload = {
    "schemaVersion": 1,
    "status": sys.argv[2],
    "downloaded": int(sys.argv[3]),
    "referenced": int(sys.argv[4]),
    "ratio": float(sys.argv[5]),
    "threshold": float(sys.argv[6]),
    "orphans": sys.argv[7].split("\n") if sys.argv[7] else [],
    "implSrcDir": sys.argv[8],
    "reason": sys.argv[9],
}
with open(out_path, "w") as fh:
    json.dump(payload, fh, indent=2)
PY
}

if [ ! -f "$VISIBLE" ]; then
  write_json skip 0 0 0.0 "$THRESHOLD" "" "${IMPL_SRC:-}" "visible-images.json absent"
  echo "▸ asset-utilization: SKIP (no visible-images.json)"
  exit 0
fi

if [ -z "$IMPL_SRC" ] || [ ! -d "$IMPL_SRC" ]; then
  write_json skip 0 0 0.0 "$THRESHOLD" "" "${IMPL_SRC:-}" "impl src dir not found"
  echo "▸ asset-utilization: SKIP (impl src dir not found)"
  exit 0
fi

# Collect basenames of non-substituted visible images.
# Schema-tolerant: `visible-images.json` may be either a top-level list
# `[{src, ...}]` (older shape) OR `{images: [{src, ...}]}` (newer shape).
# A newer schema variant ships `{images: [...]}` and the prior list-only
# parser silent-skipped with "too few samples". Flatten both shapes.
python3 - "$VISIBLE" "$SUBST" > "$REF_DIR/.asset-utilization.basenames.tmp" <<'PY'
import json
import os
import sys
visible_path, subst_path = sys.argv[1], sys.argv[2]
try:
    with open(visible_path) as fh:
        visible = json.load(fh)
except Exception:
    visible = []
# Normalize: accept [{...}] OR {images: [{...}]} OR {visible: [{...}]}
if isinstance(visible, dict):
    for key in ("images", "visible", "entries", "items"):
        if isinstance(visible.get(key), list):
            visible = visible[key]
            break
    else:
        visible = []
subst_patterns = []
if os.path.exists(subst_path):
    try:
        with open(subst_path) as fh:
            sd = json.load(fh)
        for entry in sd.get("images", []) or []:
            pat = entry.get("pattern") or entry.get("ref")
            if pat:
                subst_patterns.append(str(pat))
    except Exception:
        pass
seen = set()
out = []
for entry in visible if isinstance(visible, list) else []:
    if not isinstance(entry, dict):
        continue
    src = entry.get("src") or entry.get("url")
    if not isinstance(src, str):
        continue
    base = os.path.basename(src.split("?", 1)[0])
    if not base or base in seen:
        continue
    if any(p in src or p in base for p in subst_patterns):
        continue
    seen.add(base)
    out.append(base)
print("\n".join(out))
PY

BASENAMES_FILE="$REF_DIR/.asset-utilization.basenames.tmp"
BASENAME_COUNT=$(wc -l < "$BASENAMES_FILE" | tr -d ' ')

if [ "$BASENAME_COUNT" -lt "$MIN_SAMPLE" ]; then
  write_json skip "$BASENAME_COUNT" 0 0.0 "$THRESHOLD" "" "$IMPL_SRC" "fewer than $MIN_SAMPLE non-substituted images — sample too small"
  echo "▸ asset-utilization: SKIP (only $BASENAME_COUNT non-substituted images)"
  rm -f "$BASENAMES_FILE"
  exit 0
fi

HIDDEN_MANIFEST_FILE="$REF_DIR/.asset-utilization.hidden-manifest.tmp"
python3 - "$IMPL_SRC" "$BASENAMES_FILE" > "$HIDDEN_MANIFEST_FILE" <<'PY'
import os
import re
import sys
from pathlib import Path

impl_src = Path(sys.argv[1])
basenames_path = Path(sys.argv[2])
basenames = [line.strip() for line in basenames_path.read_text(encoding="utf-8").splitlines() if line.strip()]
text_exts = {".tsx", ".ts", ".jsx", ".js", ".css", ".scss", ".html", ".json", ".mdx"}
generated_dirs = {
    part.strip()
    for part in re.split(r"[:,]", os.environ.get("UI_CLONE_GENERATED_EVIDENCE_DIRS", "ref-css"))
    if part.strip()
}

def in_generated_evidence_dir(path: Path) -> bool:
    return bool(generated_dirs.intersection(path.parts))

matched = set()
for path in impl_src.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in text_exts:
        continue
    if in_generated_evidence_dir(path):
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    marker = f"{path.name}\n{text}".lower()
    if "reference-manifest" not in marker and "asset-manifest" not in marker:
        continue
    for base in basenames:
        stem = os.path.splitext(base)[0]
        if base in text or (len(stem) >= 4 and stem in text):
            matched.add(base)
for base in sorted(matched):
    print(base)
PY
HIDDEN_MANIFEST_COUNT=$(wc -l < "$HIDDEN_MANIFEST_FILE" | tr -d ' ')
if [ "$HIDDEN_MANIFEST_COUNT" -gt 0 ]; then
  HIDDEN_STR=$(cat "$HIDDEN_MANIFEST_FILE")
  write_json fail "$BASENAME_COUNT" 0 0.0 "$THRESHOLD" "$HIDDEN_STR" "$IMPL_SRC" "reference-manifest/asset-manifest contains ref image strings; hidden/static manifests are not rendered asset usage"
  echo "✗ asset-utilization: hidden reference-manifest strings are not rendered usage" >&2
  rm -f "$BASENAMES_FILE" "$HIDDEN_MANIFEST_FILE"
  exit 1
fi
rm -f "$HIDDEN_MANIFEST_FILE"

ASSET_RAIL_FILE="$REF_DIR/.asset-utilization.asset-rail.tmp"
python3 - "$IMPL_SRC" "$BASENAMES_FILE" > "$ASSET_RAIL_FILE" <<'PY'
import os
import re
import sys
from pathlib import Path

impl_src = Path(sys.argv[1])
basenames_path = Path(sys.argv[2])
basenames = [line.strip() for line in basenames_path.read_text(encoding="utf-8").splitlines() if line.strip()]
text_exts = {".tsx", ".ts", ".jsx", ".js", ".css", ".scss", ".html", ".mdx"}
generated_dirs = {
    part.strip()
    for part in re.split(r"[:,]", os.environ.get("UI_CLONE_GENERATED_EVIDENCE_DIRS", "ref-css"))
    if part.strip()
}

def in_generated_evidence_dir(path: Path) -> bool:
    return bool(generated_dirs.intersection(path.parts))

rail_name_re = re.compile(r"(?:asset|reference|manifest|image)[-_ ]?(?:rail|strip|manifest)", re.I)
low_opacity_re = re.compile(r"(?:opacity-(?:0|5|10|15|20)\b|opacity\s*[:=]\s*['\"]?0(?:\.\d+)?)", re.I)
non_content_re = re.compile(
    r"(?:aria-hidden|pointer-events-none|fixed\s+bottom|bottom-0|blur(?:-|\\b)|"
    r"offscreen|sr-only|display\s*:\s*none|visibility\s*:\s*hidden|w-\[?1px|h-\[?1px)",
    re.I,
)

suspicious = set()
for path in impl_src.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in text_exts:
        continue
    if in_generated_evidence_dir(path):
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    matched = []
    for base in basenames:
        stem = os.path.splitext(base)[0]
        if base in text or (len(stem) >= 4 and stem in text):
            matched.append(base)
    if len(matched) < 5:
        continue
    named_rail = bool(rail_name_re.search(text))
    low_opacity_noncontent = bool(low_opacity_re.search(text) and non_content_re.search(text))
    if named_rail or low_opacity_noncontent:
        suspicious.update(matched)

for base in sorted(suspicious):
    print(base)
PY
ASSET_RAIL_COUNT=$(wc -l < "$ASSET_RAIL_FILE" | tr -d ' ')
if [ "$ASSET_RAIL_COUNT" -gt 0 ]; then
  RAIL_STR=$(cat "$ASSET_RAIL_FILE")
  write_json fail "$BASENAME_COUNT" 0 0.0 "$THRESHOLD" "$RAIL_STR" "$IMPL_SRC" "bulk asset rail / low-opacity offscreen image strip contains ref image strings; images must be used in their original section/viewport positions"
  echo "✗ asset-utilization: bulk asset rail is not original-position asset usage" >&2
  rm -f "$BASENAMES_FILE" "$ASSET_RAIL_FILE"
  exit 1
fi
rm -f "$ASSET_RAIL_FILE"

REFERENCED=0
ORPHANS=()
while IFS= read -r base; do
  [ -z "$base" ] && continue
  # Match basename (with or without extension) in source files. .webp_ trailing
  # underscore quirks are tolerated by stripping non-alphanumeric tail.
  stem="${base%.*}"
  if grep -rqlF --exclude='*reference-manifest*' --exclude='*asset-manifest*' \
                --include='*.tsx' --include='*.ts' --include='*.jsx' --include='*.js' \
                --include='*.css' --include='*.scss' \
                "$base" "$IMPL_SRC" 2>/dev/null; then
    REFERENCED=$((REFERENCED + 1))
  elif grep -rqlF --exclude='*reference-manifest*' --exclude='*asset-manifest*' \
                  --include='*.tsx' --include='*.ts' --include='*.jsx' --include='*.js' \
                  --include='*.css' --include='*.scss' \
                  "$stem" "$IMPL_SRC" 2>/dev/null; then
    REFERENCED=$((REFERENCED + 1))
  else
    ORPHANS+=("$base")
  fi
done < "$BASENAMES_FILE"

rm -f "$BASENAMES_FILE"

RATIO=$(python3 -c "print(round($REFERENCED / $BASENAME_COUNT, 4))")
ORPHANS_STR=$(printf '%s\n' "${ORPHANS[@]:-}")

# Status decision
if python3 -c "import sys; sys.exit(0 if $RATIO >= $THRESHOLD else 1)"; then
  STATUS="pass"
  REASON="$REFERENCED of $BASENAME_COUNT non-substituted images referenced ($RATIO ≥ $THRESHOLD)"
else
  STATUS="fail"
  REASON="$REFERENCED of $BASENAME_COUNT non-substituted images referenced ($RATIO < $THRESHOLD); ${#ORPHANS[@]} orphans"
fi

write_json "$STATUS" "$BASENAME_COUNT" "$REFERENCED" "$RATIO" "$THRESHOLD" "$ORPHANS_STR" "$IMPL_SRC" "$REASON"

if [ "$STATUS" = "pass" ]; then
  echo "✓ asset-utilization: $REASON"
  exit 0
else
  echo "✗ asset-utilization: $REASON" >&2
  exit 1
fi
