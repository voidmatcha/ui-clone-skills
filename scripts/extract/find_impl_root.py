#!/usr/bin/env python3
# Python 3.9 compat for PEP 604 unions used below — defer
# annotation evaluation so `X | Y` is parsed as a string.
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])

candidates: list[Path] = []

is_pipeline_ref = (
    len(ref_dir.parents) >= 3
    and ref_dir.parent.name == "ref"
    and ref_dir.parent.parent.name == "tmp"
)


def is_cross_scratch_impl(p: Path) -> bool:
    """Reject stale state/marker/heuristic paths into another scratch run.

    Env overrides are still trusted. This guard only protects repo-local
    resolver state such as:
      tmp/ref/project-a-main/.impl-root -> scratch/project-a-sustainability-04
    which otherwise makes verification gates reason about the wrong clone.
    """
    if not is_pipeline_ref:
        return False
    scratch_root = ref_dir.parents[2] / "scratch"
    try:
        rel = p.resolve().relative_to(scratch_root.resolve())
    except (OSError, ValueError):
        return False
    if not rel.parts:
        return False
    slot = rel.parts[0]
    ref_name = ref_dir.name
    return not (
        slot == ref_name
        or slot.startswith(f"{ref_name}-")
        or slot.startswith(f"{ref_name}_")
        or slot.startswith(f"{ref_name}.")
    )


def has_ref_backlink(p: Path) -> bool:
    """Mutual-handshake check: the impl dir vouches for this ref dir.

    A cross-scratch `.impl-root` / pipeline-state path is trusted when the
    impl dir contains a `.ref-dir` file whose first line resolves to THIS
    ref dir. Stale markers (copied/renamed ref dirs) fail because the old
    impl has no backlink or one pointing at its own ref dir.
    """
    backlink = p / ".ref-dir"
    if not backlink.is_file():
        return False
    try:
        first = backlink.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return False
    if not first:
        return False
    try:
        return Path(first).expanduser().resolve() == ref_dir.resolve()
    except OSError:
        return False


def backlink_mismatch(p: Path) -> bool:
    """D4 (loop-nvti-0): an impl whose `.ref-dir` backlink resolves to a
    DIFFERENT ref dir belongs to another site's run — a convention/heuristic
    candidate carrying a foreign backlink must never resolve for this ref
    (false state-coverage PASS against stale leftovers + clobbering the other
    run's tree). No backlink = legacy tree, keep legacy resolution.
    """
    backlink = p / ".ref-dir"
    if not backlink.is_file():
        return False
    try:
        first = backlink.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return False
    if not first:
        return False
    try:
        return Path(first).expanduser().resolve() != ref_dir.resolve()
    except OSError:
        return False


# Candidates exempt from the cross-scratch guard: explicit env override
# (caller-knows-best, mirrors ui_clone/pipeline_phases/execute.py) and
# marker/state paths confirmed by a `.ref-dir` backlink handshake.
trusted_candidates: set = set()

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
        # Env override is trusted unconditionally ("caller knows best"),
        # matching the documented contract above and the Python resolver
        # in ui_clone/pipeline_phases/execute.py.
        candidates.append(p)
        trusted_candidates.add(p)

marker = ref_dir / ".impl-root"
if marker.is_file():
    try:
        marker_path = marker.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if marker_path:
            p = Path(marker_path).expanduser().resolve()
            if p.is_dir():
                if not is_cross_scratch_impl(p):
                    candidates.append(p)
                elif has_ref_backlink(p):
                    candidates.append(p)
                    trusted_candidates.add(p)
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
                if not is_cross_scratch_impl(p):
                    candidates.append(p)
                elif has_ref_backlink(p):
                    candidates.append(p)
                    trusted_candidates.add(p)
    except (OSError, ValueError):
        pass

# Convention paths (preferred when present — exact names).
candidates.append(ref_dir.parent / "impl")  # benchmark/work/<sha>/impl
candidates.append(ref_dir.parent.parent / "apps" / ref_dir.name)
candidates.append(ref_dir.parent.parent / "apps" / ref_dir.name / "app")
if len(ref_dir.parents) >= 3:
    candidates.append(ref_dir.parents[2] / "impl")  # nested workspace impl (3-up sibling)


def is_valid_convention(p: Path) -> bool:
    """Convention candidates (impl/, apps/<comp>/, <workspace>/impl/) are
    canonical locations — accept on src/ or app/ presence alone. Requiring
    package.json here would break legacy back-compat where the impl scaffold
    is checked in piecemeal.
    """
    if not p.is_dir():
        return False
    return (p / "src").is_dir() or (p / "app").is_dir()


# Fast path: if an explicit/convention location exists, do not run the
# structural heuristic. In pytest temp dirs, walking the grandparent can scan a
# huge shared /private/var/... tree and turn a resolver call into a multi-second
# operation even though the sibling impl/ is already present.
for c in candidates:
    if (
        is_valid_convention(c)
        and (c in trusted_candidates or not is_cross_scratch_impl(c))
        and (c in trusted_candidates or not backlink_mismatch(c))
    ):
        impl_src = c / "src" if (c / "src").is_dir() else c / "app"
        impl_pkg = c / "package.json"
        print(str(c))
        print(str(impl_src))
        print(str(impl_pkg))
        sys.exit(0)

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
    if is_cross_scratch_impl(root):
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
is_pytest_tmp = any(
    p.name.startswith("pytest-") or p.name.startswith("pytest-of-")
    for p in ref_dir.parents
)
if is_pipeline_ref:
    search_roots.append(ref_dir.parents[2])  # loop_root for tmp/ref/<component>
elif is_pytest_tmp:
    # Pytest creates many sibling temp directories under one shared
    # /private/var/.../pytest-N root. Walking that grandparent makes each
    # resolver call scan unrelated tests. A test fixture's impl, when present,
    # lives under the fixture directory itself, so keep the heuristic local.
    search_roots.append(ref_dir.parent)
elif len(ref_dir.parents) >= 3:
    search_roots.append(ref_dir.parents[2])  # loop_root (if nested)
# Repo root candidate: walk up from ref until we hit a dir with a
# .git or package.json (typical repo markers); cap at 6 hops.
cur = ref_dir
if not is_pytest_tmp:
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


# Prefer convention candidates first.
resolved: Path | None = None
for c in candidates:
    if (
        is_valid_convention(c)
        and (c in trusted_candidates or not is_cross_scratch_impl(c))
        and (c in trusted_candidates or not backlink_mismatch(c))
    ):
        resolved = c
        break

# Heuristic fallback — disambiguate by framework config marker.
# Foreign-backlink trees are other runs' impls (D4) — drop them before
# disambiguation so they neither win nor force an AMBIGUOUS exit.
heuristic_candidates = [c for c in heuristic_candidates if not backlink_mismatch(c)]
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
