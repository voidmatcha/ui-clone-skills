#!/usr/bin/env bash
# font-binaries-present-check.sh — fail when the ref references root-relative
# font binaries but none of them actually landed in impl/public.
#
# Closes the navercorp failure class: transfer-fonts.sh records that N fonts are
# referenced by the ref CSS, but impl/public/font ships zero files, so
# Pretendard/Inter/etc. all 404 to system fonts — while asset-transfer reports
# "44/44 transferred" because it counts CSS references, not delivered binaries.
# This gate reads the transfer report AND verifies the files are on disk
# (belt-and-suspenders), so a report that claims transfers but ships nothing
# still fails.
#
# Usage: font-binaries-present-check.sh <ref-dir> [<impl-root>]
#   ref-dir     the canonical ref dir
#   impl-root   impl project root; the report's implPublicDir is preferred, else
#               <impl-root>/public, else find-impl-root.sh
#
# Reads:
#   <ref-dir>/font-transfer.json   — produced by scripts/extract/transfer-fonts.sh
#                                     ({ totals:{referenced,transferred,missing,
#                                     skipped}, transferred[], skipped[],
#                                     implPublicDir })
#
# Writes:
#   <ref-dir>/font-binaries-present.json — schemaVersion 1, status, totals,
#                                          presentOnDisk, implPublicDir, reason
#
# Pass criteria:
#   pass  — no font-transfer.json (transfer step not run / nothing to verify),
#           OR referenced == 0, OR at least one referenced font binary is present
#           under impl/public (missing[] entries with something present are an
#           advisory extraction gap, not a transfer failure)
#   fail  — referenced > 0 AND zero referenced font binaries are present on disk
#   (exit 0 pass, 1 fail)

set -uo pipefail

REF_DIR="${1:?Usage: font-binaries-present-check.sh <ref-dir> [<impl-root>]}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

IMPL_ROOT="${2:-}"
if [ -z "$IMPL_ROOT" ]; then
  RESOLVER="${PLUGIN_ROOT:-$(dirname "$(dirname "$(dirname "${BASH_SOURCE[0]}")")")}/scripts/extract/find-impl-root.sh"
  if [ -x "$RESOLVER" ]; then
    RESOLVED=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | sed -n '1p')
    [ -n "$RESOLVED" ] && [ -d "$RESOLVED" ] && IMPL_ROOT="$RESOLVED"
  fi
fi

OUT="$REF_DIR/font-binaries-present.json"

STATUS=$(python3 - "$REF_DIR/font-transfer.json" "$IMPL_ROOT" "$OUT" <<'PY'
import json
import os
import sys

report_path, impl_root, out_path = sys.argv[1:4]
FONT_EXTS = {".woff2", ".woff", ".ttf", ".otf", ".eot"}


def write(status, reason, present=0, totals=None, impl_public=""):
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({
            "schemaVersion": 1,
            "status": status,
            "totals": totals or {},
            "presentOnDisk": present,
            "implPublicDir": impl_public,
            "reason": reason,
        }, fh, indent=2)
    print(status)


if not os.path.exists(report_path):
    # transfer-fonts.sh has not run (e.g. non-deterministic path) — nothing to
    # verify. PASS (not skip): the dispatcher counts skip as failure, and an
    # absent transfer report is not itself a font-delivery failure.
    write("pass", "no font-transfer.json — transfer step not run, nothing to verify")
    sys.exit(0)

try:
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
except (OSError, ValueError) as exc:
    write("fail", f"font-transfer.json unreadable/malformed ({exc})")
    sys.exit(0)

totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
referenced = int(totals.get("referenced") or 0)

# Prefer the report's own implPublicDir; fall back to <impl-root>/public.
impl_public = report.get("implPublicDir") or ""
if not impl_public and impl_root:
    impl_public = os.path.join(impl_root, "public")

if referenced == 0:
    write("pass", "no root-relative font references — nothing to transfer",
          totals=totals, impl_public=impl_public)
    sys.exit(0)

# Collect every reported font path/basename. Exact paths remain authoritative,
# but implementations may intentionally rewrite CSS to another public
# subdirectory while preserving the downloaded font's basename.
font_entries = []
for group in ("transferred", "skipped", "missing"):
    for entry in report.get(group) or []:
        if isinstance(entry, dict):
            font_entries.append(entry)

url_paths = []
expected_basenames = set()
for entry in font_entries:
    up = entry.get("urlPath") or entry.get("url")
    if up:
        up = str(up)
        url_paths.append(up)
    basename = entry.get("basename")
    if not basename and up:
        basename = os.path.basename(up.split("?", 1)[0])
    if basename:
        basename = str(basename)
        if os.path.splitext(basename)[1].lower() in FONT_EXTS:
            expected_basenames.add(basename.lower())

present_files = set()
if impl_public and os.path.isdir(impl_public):
    # First count exact report paths.
    for up in url_paths:
        candidate = os.path.join(impl_public, up.lstrip("/"))
        if os.path.isfile(candidate):
            present_files.add(os.path.realpath(candidate))

    # Then accept only recursively discovered font binaries whose basename
    # matches one named by the report. An arbitrary font elsewhere in public
    # must not satisfy the gate.
    if expected_basenames:
        for root, _dirs, files in os.walk(impl_public):
            for filename in files:
                if (
                    os.path.splitext(filename)[1].lower() in FONT_EXTS
                    and filename.lower() in expected_basenames
                ):
                    present_files.add(os.path.realpath(os.path.join(root, filename)))

present = len(present_files)

if present == 0:
    write("fail",
          f"{referenced} root-relative font(s) referenced by ref CSS but ZERO "
          "font binaries are present under impl/public — fonts will 404 to "
          "system fallbacks. Run scripts/extract/transfer-fonts.sh and confirm "
          "impl/public carries the woff/woff2 files.",
          present=0, totals=totals, impl_public=impl_public)
    sys.exit(0)

missing = int(totals.get("missing") or 0)
if missing > 0:
    write("pass",
          f"{present} font binary(ies) present under impl/public; {missing} "
          "still missing (extraction gap — binary never downloaded, advisory).",
          present=present, totals=totals, impl_public=impl_public)
else:
    write("pass",
          f"{present} referenced font binary(ies) present under impl/public.",
          present=present, totals=totals, impl_public=impl_public)
sys.exit(0)
PY
)

case "$STATUS" in
  pass)
    echo "✓ font-binaries-present: PASS"
    exit 0
    ;;
  fail)
    REASON=$(python3 -c "import json; print(json.load(open('$OUT')).get('reason',''))" 2>/dev/null || true)
    echo "✗ font-binaries-present: FAIL — $REASON" >&2
    exit 1
    ;;
  *)
    echo "font-binaries-present: unexpected status '$STATUS'" >&2
    exit 2
    ;;
esac
