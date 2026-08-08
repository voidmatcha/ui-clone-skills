#!/usr/bin/env bash
# required-media.sh — produce <ref-dir>/required-media.json by merging
# video/Lottie evidence from per-section HTML captures, runtime DOM
# captures, and bundle JS scan.
#
#
# This artifact promotes:
#   - <video src=...> and <video><source src=...> URLs collected per
#     section by extract-section-html.sh (html/<name>.json.media[])
#   - runtime-created <video> nodes captured by runtime-media.sh
#   - Lottie/bodymovin path strings inside bundles/*.js using the
#     loadAnimation({ path: "..." }) PCRE pattern.
#
# Section binding: media entries from html/<name>.json are already
# scoped to a section name; Lottie paths from the bundle are global
# (no section info) until the impl side maps them by usage. Both
# kinds are emitted; the coverage gate checks transfer + reference.
#
# Inputs:
#   <ref-dir>/html/*.json       — per-section captures with media[]
#   <ref-dir>/runtime-media.json — live JS-created media capture
#   <ref-dir>/bundles/*.js      — downloaded JS bundles (extract-bundles)
#   <ref-dir>/external-sdks.json — fallback Lottie evidence
#
# Output: <ref-dir>/required-media.json
#   {
#     schemaVersion: 1,
#     videos: [{section, src, type?, poster?, w?, h?, evidenceKind?}],
#     lottie: [{path, evidenceFile, line}],
#     totals: {video, lottie}
#   }
#
# Exit: 0 success, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: required-media.sh <ref-dir>" >&2
  exit 2
fi

OUT_PATH="$REF_DIR/required-media.json"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/required_media.py" "$REF_DIR" "$OUT_PATH"
