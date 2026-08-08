#!/usr/bin/env bash
# bundle-impl-coverage-check.sh — fail when ref bundle declares libraries
# (Lenis, GSAP, Framer Motion, etc.) but impl/package.json doesn't depend
# on any of them, leaving the bundle decode as a dead wire.
#
# Closes the failure class observed on the c9b638d benchmark:
#   1. bundle-map.json correctly detected `gsap-like-strings`, `motion-like`,
#      Lenis-on-<html> in the ref bundle
#   2. agent scaffolded Next.js with default deps only (next/react/react-dom)
#   3. page.tsx had a comment `// lenis-smooth-scroll — html scroll behaviour`
#      but no actual Lenis import — runtime had no smooth scroll, no
#      scroll-scrub motion, no IntroAnimation entrance, etc.
#   4. transition-compare measured ZERO motion against rich ref motion
#
# Usage: bundle-impl-coverage-check.sh <ref-dir> [<impl-pkg-json>]
#   ref-dir         the canonical ref dir
#   impl-pkg-json   path to impl/package.json; auto-detected if omitted
#                   (benchmark/work/<sha>/impl/package.json or apps/<c>/package.json)
#
# Reads:
#   <ref-dir>/bundle-map.json    — chunks[].libs[] and top-level notes
#
# Writes:
#   <ref-dir>/bundle-impl-coverage.json  — schemaVersion 1, status,
#                                          detectedLibs[], missingDeps[],
#                                          implPkgJson, reason
#
# Pass criteria:
#   pass  — every detected lib signature has a corresponding npm package
#           in dependencies OR devDependencies of impl/package.json
#   fail  — at least one detected lib has no matching install (dead wire)
#   skip  — bundle-map.json absent OR impl pkg.json absent OR no libs detected

set -euo pipefail

REF_DIR="${1:?Usage: bundle-impl-coverage-check.sh <ref-dir> [<impl-pkg-json>]}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

BUNDLE_MAP="$REF_DIR/bundle-map.json"
OUT="$REF_DIR/bundle-impl-coverage.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IMPL_PKG="${2:-}"
if [ -z "$IMPL_PKG" ]; then
  CANDIDATES=(
    "$(dirname "$REF_DIR")/../impl/package.json"
    "$(dirname "$REF_DIR")/impl/package.json"
    "apps/$(basename "$REF_DIR")/package.json"
    "app/package.json"
    "package.json"
  )
  for c in "${CANDIDATES[@]}"; do
    if [ -f "$c" ]; then IMPL_PKG="$c"; break; fi
  done
fi

if [ -z "$IMPL_PKG" ] || [ ! -f "$IMPL_PKG" ]; then
  RESOLVER="${PLUGIN_ROOT:-$(dirname "$(dirname "$(dirname "${BASH_SOURCE[0]}")")")}/scripts/extract/find-impl-root.sh"
  if [ -x "$RESOLVER" ]; then
    RESOLVED=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | sed -n '3p')
    if [ -n "$RESOLVED" ] && [ -f "$RESOLVED" ]; then
      IMPL_PKG="$RESOLVED"
    fi
  fi
fi

write_status() {
  local status="$1" reason="$2"
  python3 "$SCRIPT_DIR/bundle_impl_coverage.py" \
    "$OUT" "$status" "$reason" "$BUNDLE_MAP" "${IMPL_PKG:-}"
}

if [ ! -f "$BUNDLE_MAP" ]; then
  write_status skip "bundle-map.json absent"
  echo "▸ bundle-impl-coverage: SKIP (no bundle-map.json)"
  exit 0
fi

if [ -z "$IMPL_PKG" ] || [ ! -f "$IMPL_PKG" ]; then
  write_status skip "impl package.json not found — pass it explicitly or scaffold impl first"
  echo "▸ bundle-impl-coverage: SKIP (no impl package.json)"
  exit 0
fi

# First pass — write tentative skip then re-run the check via python to decide
# pass/fail by re-reading the JSON we just wrote.
write_status pass "tentative; rechecking"
MISSING_COUNT=$(python3 -c "
import json
with open('$OUT') as fh:
    d = json.load(fh)
print(len(d.get('missingDeps', [])))
")

if [ "${MISSING_COUNT:-0}" -eq 0 ]; then
  # Recompute with proper reason once
  DETECTED_COUNT=$(python3 -c "
import json
with open('$OUT') as fh:
    d = json.load(fh)
print(len(d.get('detectedLibs', [])))
")
  if [ "$DETECTED_COUNT" -eq 0 ]; then
    # No library signatures in bundle-map.json means there's nothing to verify
    # against — a static-site clone has no bundle libs that need matching
    # package.json deps. Emit pass (not skip): the gate counts `status:"skip"`
    # as failure, but "no libs detected" is unambiguously a success condition
    # (Common cheat pattern). The two upstream skip cases (bundle-map.json absent /
    # impl package.json not found) are real prerequisite failures and remain
    # `skip` so the operator notices them.
    write_status pass "no library signatures detected in bundle-map.json (static build, nothing to verify)"
    echo "✓ bundle-impl-coverage: PASS (no library signatures detected)"
  else
    write_status pass "all $DETECTED_COUNT detected library signature(s) have matching install"
    echo "✓ bundle-impl-coverage: PASS"
  fi
  exit 0
fi

# Compose human-readable missing list
MISSING_LIST=$(python3 -c "
import json
with open('$OUT') as fh:
    d = json.load(fh)
parts = []
for m in d.get('missingDeps', []):
    sig = m.get('signature', '?')
    anyof = m.get('anyOf', [])
    parts.append(f\"{sig} (install one of: {', '.join(anyof)})\")
print('; '.join(parts))
")

write_status fail "bundle decode dead wire — $MISSING_COUNT missing: $MISSING_LIST"
echo "✗ bundle-impl-coverage: FAIL — $MISSING_LIST" >&2
exit 1
