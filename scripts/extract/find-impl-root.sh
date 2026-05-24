#!/usr/bin/env bash
# find-impl-root.sh — shared resolver for impl directory location.
# Single source of truth so gate.py, bundle-impl-coverage-check.sh,
# transition-spec-coverage.sh, verify-loop.sh, measure.py, and future
# impl-source checks all locate the same target — even when the agent
# renamed `impl/` to `<component>-clone/` etc.
#
#
# Usage: find-impl-root.sh <ref-dir>
#   ref-dir   tmp/ref/<component>/ — used to anchor the loop / benchmark root
#
# Output: one line per resolved path (or empty lines for unresolved):
#   <impl_root>
#   <impl_src>
#   <impl_package_json>
#
# Exit 0 if impl found, 2 if not found. Stderr carries diagnostic info.
set -euo pipefail

REF_DIR="${1:-}"
if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: $0 <ref-dir>" >&2
  exit 2
fi
REF_DIR="$(cd "$REF_DIR" && pwd)"

python3 - "$REF_DIR" <<'PY'
# Python 3.9 compat for PEP 604 unions used below — defer
# annotation evaluation so `X | Y` is parsed as a string.
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])

candidates: list[Path] = []

# UNIVERSAL OVERRIDE (highest priority): when the caller knows where
# impl lives, they say so via env var or a marker file. This frees
# the resolver from depending on any directory-naming convention.
#
# Lookup order:
#   1. $UI_CLONE_IMPL_ROOT env var (set by pipeline driver / harness
#      / launch script — works in any directory layout)
#   2. <ref-dir>/.impl-root marker file containing an absolute path
#      (written once at extraction start by pipeline.execute_extract)
#   3. pipeline-state.json `implRoot` field (read from existing
#      extraction state)
#   4. Convention-based candidates below (legacy / convenience)
override = os.environ.get("UI_CLONE_IMPL_ROOT", "").strip()
if override:
    p = Path(override).expanduser().resolve()
    if p.is_dir():
        candidates.append(p)

marker = ref_dir / ".impl-root"
if marker.is_file():
    try:
        marker_path = marker.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if marker_path:
            p = Path(marker_path).expanduser().resolve()
            if p.is_dir():
                candidates.append(p)
    except OSError:
        pass

state = ref_dir / "pipeline-state.json"
if state.is_file():
    try:
        sd = json.loads(state.read_text(encoding="utf-8"))
        recorded = sd.get("implRoot") if isinstance(sd, dict) else None
        if isinstance(recorded, str) and recorded.strip():
            p = Path(recorded.strip()).expanduser().resolve()
            if p.is_dir():
                candidates.append(p)
    except (OSError, ValueError):
        pass

# Convention paths (preferred when present — exact names).
candidates.append(ref_dir.parent / "impl")  # benchmark/work/<sha>/impl
candidates.append(ref_dir.parent.parent / "apps" / ref_dir.name)
candidates.append(ref_dir.parent.parent / "apps" / ref_dir.name / "app")
if len(ref_dir.parents) >= 3:
    candidates.append(ref_dir.parents[2] / "impl")  # nested workspace impl (3-up sibling)

# Structural heuristic — find ANY directory that looks like an impl
# tree (package.json + src/ or app/ or pages/ with .tsx/.jsx/.vue/
# .svelte/.astro), regardless of its name. This makes the resolver
# work for arbitrary project layouts:
#   - <repo>/impl/                           (canonical)
#   - <repo>/apps/<component>/               (monorepo)
#   - <repo>/<workspace>/impl/               (nested per-workspace layout)
#   - <repo>/clones/<arbitrary>/             (no convention)
#   - <repo>/experiments/<sha>/              (no convention)
#
# Depth-bounded walk from the common parent of ref-and-candidate-impls.
# For ref at <repo>/tmp/ref/<c>/ the common parent is <repo>; for
# ref at <repo>/<workspace>/tmp/ref/<c>/ the common parent is
# <repo>/<workspace>/. Walk depth ≤ 3 to keep it bounded but
# reachable for nested layouts.
#
# Skip set is INTENTIONALLY EMPTY for impl candidates — we don't
# know what naming convention the user picked. Instead, reject by
# STRUCTURE: must have package.json + a source root + at least one
# component file. Build outputs (dist/, .next/, node_modules) are
# rejected because they lack package.json OR have no real source.
NON_RECURSE_DIRS = {
    "node_modules", ".git", ".next", ".nuxt", ".svelte-kit",
    ".vite", ".turbo", ".cache", "dist", "build", "tmp",
}
SOURCE_SUFFIXES = (".tsx", ".jsx", ".vue", ".svelte", ".astro")

heuristic_candidates: list[Path] = []


def has_impl_shape(p: Path) -> bool:
    """Structural impl detection — name-agnostic."""
    if not p.is_dir() or not (p / "package.json").is_file():
        return False
    for sub in ("src", "app", "pages"):
        s = p / sub
        if s.is_dir():
            try:
                # Cheap shape probe: any source file directly under sub
                # OR within one level.
                for child in s.iterdir():
                    if child.is_file() and child.suffix in SOURCE_SUFFIXES:
                        return True
                    if child.is_dir():
                        for grand in child.iterdir():
                            if grand.is_file() and grand.suffix in SOURCE_SUFFIXES:
                                return True
            except OSError:
                continue
    return False


def walk_for_impls(root: Path, depth: int) -> None:
    """Depth-bounded walk collecting impl-shape directories."""
    if depth < 0 or not root.is_dir():
        return
    try:
        if has_impl_shape(root):
            heuristic_candidates.append(root)
            # If this dir is itself an impl, don't recurse — its
            # node_modules/src would just generate noise.
            return
        for child in root.iterdir():
            if not child.is_dir() or child.name in NON_RECURSE_DIRS:
                continue
            walk_for_impls(child, depth - 1)
    except OSError:
        pass


# Search common parents at bounded depth. Try the loop-root first
# (preserves prior behavior), then walk repo root for cross-located
# layouts (ref at <repo>/tmp/ref/ + impl at <repo>/scratch/.../impl/).
search_roots: list[Path] = []
if len(ref_dir.parents) >= 3:
    search_roots.append(ref_dir.parents[2])  # loop_root (if nested)
# Repo root candidate: walk up from ref until we hit a dir with a
# .git or package.json (typical repo markers); cap at 6 hops.
cur = ref_dir
for _ in range(6):
    if cur in search_roots:
        break
    if (cur / ".git").exists() or (cur / "package.json").is_file():
        search_roots.append(cur)
        break
    if cur.parent == cur:
        break
    cur = cur.parent

seen_roots: set[Path] = set()
for sr in search_roots:
    try:
        sr_resolved = sr.resolve()
    except OSError:
        continue
    if sr_resolved in seen_roots:
        continue
    seen_roots.add(sr_resolved)
    walk_for_impls(sr_resolved, depth=3)


def is_valid_convention(p: Path) -> bool:
    """Convention candidates (impl/, apps/<comp>/, <workspace>/impl/) are
    canonical locations — accept on src/ or app/ presence alone. Requiring
    package.json here would break legacy back-compat where the impl scaffold
    is checked in piecemeal.
    """
    if not p.is_dir():
        return False
    return (p / "src").is_dir() or (p / "app").is_dir()


# Prefer convention candidates first.
resolved: Path | None = None
for c in candidates:
    if is_valid_convention(c):
        resolved = c
        break

# Heuristic fallback — disambiguate by framework config marker.
if resolved is None and heuristic_candidates:
    if len(heuristic_candidates) == 1:
        resolved = heuristic_candidates[0]
    else:
        markers = ("next.config.ts", "next.config.js", "next.config.mjs",
                   "vite.config.ts", "vite.config.js", "vite.config.mjs")
        with_markers = [
            c for c in heuristic_candidates
            if any((c / m).exists() for m in markers)
        ]
        if len(with_markers) == 1:
            resolved = with_markers[0]
        elif len(with_markers) > 1 or len(heuristic_candidates) > 1:
            print(
                f"AMBIGUOUS: multiple impl-like directories found near {ref_dir}:",
                file=sys.stderr,
            )
            for c in heuristic_candidates:
                print(f"  - {c}", file=sys.stderr)
            print("  Resolve by renaming one to `impl/` or removing the others.", file=sys.stderr)
            sys.exit(2)

if resolved is None:
    print("NOT_FOUND: no impl directory located near", ref_dir, file=sys.stderr)
    print("")  # impl_root
    print("")  # impl_src
    print("")  # impl_package_json
    sys.exit(2)

# Resolve impl_src: prefer src/, fall back to app/
impl_src = resolved / "src" if (resolved / "src").is_dir() else resolved / "app"
impl_pkg = resolved / "package.json"

print(str(resolved))
print(str(impl_src))
print(str(impl_pkg))
PY
