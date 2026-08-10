"""Per-check input fingerprinting — the single source of truth for B1 staleness.

Friction #2: editing ONE impl file marked ~25 verification artifacts stale,
because both staleness locations used a *global* newest-mtime over the whole
impl tree:
  * ui_clone/gates/verification_plan.py (Python gate, path-checked artifacts)
  * scripts/verify/run-required-checks.sh (bash dispatcher, every satisfied row)

This module replaces that with a PER-CHECK input fingerprint: each check declares
the impl globs and ref-artifact globs that actually affect its verdict, and a
check is stale iff its OWN declared inputs changed. A style-only CSS edit then
re-hashes only the checks whose inputs include CSS; JS-only runtime checks stay
valid (and rollups still pick up the CSS change transitively, because their
constituent artifacts get regenerated and the rollup declares those constituents
as ref inputs).

LOCKSTEP (why the two locations cannot diverge): both consult THIS module for the
input map, the hashing algorithm, AND the sidecar path. The Python gate imports
the functions; the bash dispatcher shells out to the `hash` / `sidecar` CLI. One
map + one hasher + one path-deriver, all keyed by ``check_id`` (never by the
artifact filename or the script basename — those are different namespaces).

POLICY (kept SEPARATE per location, by design — they have different scopes):
  * ``get_check_inputs(check_id)`` returns ``None`` for an UNREGISTERED check.
    Callers must treat ``None`` CONSERVATIVELY — fall back to the legacy
    newest-mtime / find-newer staleness, never "fresh". A new gate wired without
    registering its inputs therefore still gets (coarse) staleness, and the
    registry-completeness test forces it to be registered.
  * A registered check with NO declared inputs (``impl == () and ref == ()``) is
    an explicit opt-out: genuinely input-independent (host capacity probe, ref
    stamp). The hash CLI prints ``EMPTY`` and such a check is never stale.

The hash is over ``(side/relpath, content)`` pairs (NOT content-only), so a pure
rename busts the cache for placement/identity-sensitive checks. Large text,
source, and catalog files are streamed into the hash in full. Only known binary
media/font/archive formats larger than ``MAX_HASH_BYTES`` use the bounded
``(relpath, size)`` policy, so locale catalogs cannot collide merely because a
same-size edit crossed the threshold.
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path
from typing import NamedTuple

# Directories never relevant to a check's inputs (build output / deps).
PRUNE_DIRS = frozenset({"node_modules", ".next", ".turbo", "dist", "build", ".git"})

# Known binary media above this size are fingerprinted by (relpath, size)
# instead of content, so a public/ tree of large videos/images does not make
# hashing expensive. Text/code/catalog files are always content-hashed.
MAX_HASH_BYTES = 1_048_576  # 1 MiB
HASH_CHUNK_BYTES = 128 * 1024
_BINARY_MEDIA_SUFFIXES = frozenset(
    {
        ".3gp",
        ".7z",
        ".aac",
        ".avi",
        ".avif",
        ".bin",
        ".bmp",
        ".br",
        ".eot",
        ".flac",
        ".gif",
        ".gz",
        ".ico",
        ".jpeg",
        ".jpg",
        ".lottie",
        ".m4a",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".oga",
        ".ogg",
        ".ogv",
        ".otf",
        ".pdf",
        ".png",
        ".rar",
        ".tar",
        ".tif",
        ".tiff",
        ".ttf",
        ".wav",
        ".wasm",
        ".webm",
        ".webp",
        ".woff",
        ".woff2",
        ".xz",
        ".zip",
    }
)

# The eight impl roots the legacy bash dispatcher scanned, braced so a Next.js
# app/ or pages/ route component is covered exactly like a Vite src/ tree.
_ROOTS = "{src,app,pages,components,lib,hooks,contexts}"

# ── Composable impl-input profiles (globs relative to impl_root) ──────────────
# Build each check's input set by composing these; per-check extras are appended
# as raw tuples. JS vs CSS vs SRC is the distinction that delivers the friction
# fix: a CSS-only edit re-hashes CSS/SRC checks but not JS-only runtime checks.
_JS_EXTS = "{ts,tsx,js,jsx,mjs,cjs}"
JS: tuple[str, ...] = (f"{_ROOTS}/**/*.{_JS_EXTS}",)
CSS: tuple[str, ...] = (
    f"{_ROOTS}/**/*.{{css,scss,sass}}",
    "public/**/*.css",
    "tailwind.config.*",
    "postcss.config.*",
)
SRC: tuple[str, ...] = JS + CSS  # style + behavior source
PUBLIC: tuple[str, ...] = ("public/**/*",)
PKG: tuple[str, ...] = ("package.json",)
ENTRY: tuple[str, ...] = (
    "index.html",
    "{src,app,pages}/main.*",
    "app/page.*",
    "app/layout.*",
    "next.config.*",
    "vite.config.*",
)
# Whole-impl checks (identity / git-scope) — broad but bounded (no node_modules).
BROAD: tuple[str, ...] = SRC + PUBLIC + ENTRY + PKG

# ── Ref-artifact input profiles (globs relative to ref_dir) ───────────────────
# Regenerating any of these (re-extract / re-decode / re-capture) must bust the
# verdict of the checks that read it — invisible to impl-only globs otherwise.
REF_DOM: tuple[str, ...] = ("dom-scaffold.json",)
REF_IMAGES: tuple[str, ...] = ("visible-images.json",)
REF_SECTIONS: tuple[str, ...] = (
    "sections/matches.json",
    "sections/viewports/*/sections/matches.json",
)
REF_REGIONS: tuple[str, ...] = ("regions.json", "section-map.json", "component-map.json")
REF_SPEC: tuple[str, ...] = ("transition-spec.json", "animation-runtime-dump.json")
REF_BUNDLE: tuple[str, ...] = (
    "bundle-map.json",
    "external-sdks.json",
    "bundle-extraction.json",
)
REF_GROUNDING: tuple[str, ...] = (
    "css/*.css",
    "section-html/_colors.json",
    "transition-coverage.json",
)
# Asset-substitution declarations switch a check's expectation for a given image
# (use / substitute / skip), so every asset/transfer/compare check that honours
# them must re-run when they change.
REF_ASSET_SUB: tuple[str, ...] = ("asset-substitution.json",)
# Rollup constituents: the per-measurement artifacts each rollup aggregates
# (from runtime-proof-rollup.sh / transition-proof-rollup.sh), plus the plan
# that defines which missing artifacts are required. Excludes each rollup's own
# output.
RUNTIME_CONSTITUENTS: tuple[str, ...] = (
    "verification-plan.json",
    "blank-viewport.json",
    "header-state-runtime.json",
    "hero-composite.json",
    "hidden-children.json",
    "lottie-runtime.json",
    "motion-coverage.json",
    "reveal-trigger.json",
    "runtime-dom-parity.json",
    "runtime-frame-proof.json",
    "runtime-image-validity.json",
    "runtime-spec-coverage.json",
    "runtime-spec.json",
    "scroll-completion.json",
    "svg-provenance.json",
)
TRANSITION_CONSTITUENTS: tuple[str, ...] = (
    "verification-plan.json",
    "keyframes-diff.json",
    "regions.json",
    "reveal-trigger.json",
    "scroll-completion.json",
    "spec-implementation-coverage.json",
    "transitions/hover-state-result.txt",
    "transitions/result.txt",
    "transitions/video-motion-result.txt",
    "transition-coverage.json",
    "transition-fires.json",
    "transition-spec-coverage.json",
    "transition-spec.json",
)


class CheckInputs(NamedTuple):
    """Declared inputs for one check. impl globs resolve under impl_root,
    ref globs under ref_dir. Empty impl+ref == explicit input-independent."""

    impl: tuple[str, ...]
    ref: tuple[str, ...]


def _ci(impl: tuple[str, ...] = (), ref: tuple[str, ...] = ()) -> CheckInputs:
    return CheckInputs(impl, ref)


# ── The registry: check_id -> declared inputs ────────────────────────────────
# Conservative bias: when a check could read more than is provable from a static
# grep (env-driven reads, browser probes, JSON-derived selectors), widen toward
# SRC/PUBLIC rather than narrow — a too-narrow set is a SILENT stale-reuse bug,
# while over-inclusion only costs an extra re-run. Keyed by check_id; keep this
# in sync with verification-plan.sh add_check rows (registry-completeness test).
CHECK_INPUTS: dict[str, CheckInputs] = {
    # ── input-independent (explicit opt-out: never stale) ──
    "capacity-probe": _ci(),
    "invalidation": _ci(),
    # ── whole-impl identity / scope ──
    "impl-url-guard": _ci(BROAD),
    "impl-scope": _ci(BROAD),
    # ── entry / config ──
    "entry-coherence": _ci(ENTRY + PKG),
    "runtime-env": _ci(JS + ENTRY + PKG),
    "html-paste": _ci(SRC + PUBLIC + ENTRY, ("bundle-map.json", "dom-scaffold.json")),
    "proxy-mirror-check": _ci(SRC + PUBLIC + ENTRY),
    "ref-js-loader": _ci(
        SRC + PUBLIC + ENTRY,
        ("bundle-map.json", "external-sdks.json", "extracted.json", "head.json"),
    ),
    "bundle-paste": _ci(SRC + PUBLIC),
    # ── pure CSS / style ──
    "css-mirror": _ci(CSS, ("bundle-map.json", "bundles/*.css", "css/*.css")),
    "keyframes-diff": _ci(CSS),
    "tailwind-transform-conflict": _ci(CSS),
    # ── package-only ──
    "bundle-impl-coverage": _ci(PKG, REF_BUNDLE),
    # library-usage reads impl JS imports + package.json against ref bundle/SDK
    # evidence, so its verdict is stale when either the impl source or the ref
    # library detection changes.
    "library-usage": _ci(JS + PKG, REF_BUNDLE),
    # ── JS / behavior (style-independent) ──
    # Reads only the emitted JS/TSX import graph — whether every relative or
    # aliased specifier has a file behind it. No ref artifact participates, so
    # its verdict goes stale purely on impl source change.
    "unresolved-imports": _ci(JS),
    "hydration-check": _ci(JS + PKG),
    "scaffold-residue": _ci(JS),
    "scaffold-warn": _ci(JS),
    "monolithic-impl": _ci(JS, ("section-map.json",)),
    "header-state-runtime": _ci(JS, REF_SPEC),
    "scroll-state-machine": _ci(JS, REF_SPEC + ("scroll-engine.json",)),
    "text-fidelity-check": _ci(JS, REF_DOM),
    "dom-mirror-check": _ci(JS, REF_DOM),
    "content-cardinality": _ci(JS, REF_DOM + ("head.json",)),
    "hero-composite-check": _ci(JS, REF_DOM + ("structure.json",)),
    "signature-effects-coverage": _ci(JS, REF_SPEC + ("generation-plan.json",)),
    "transition-spec-coverage": _ci(JS, REF_SPEC),
    "spec-implementation-coverage": _ci(JS, REF_SPEC),
    "masked-region-motion": _ci(JS, REF_SPEC),
    "scroll-engine-parity": _ci(JS + PKG, REF_BUNDLE + REF_SPEC + ("scroll-engine.json",)),
    "motion-coverage": _ci(JS + PKG, REF_BUNDLE + ("transition-spec.json",)),
    "swiper-runtime": _ci(JS + PKG, REF_BUNDLE),
    "lottie-runtime": _ci(
        JS + PKG + PUBLIC,
        REF_SPEC
        + (
            "assets.json",
            "bundle-map.json",
            "canvas-webgl-detection.json",
            "external-sdks.json",
            "extracted.json",
            "interactions-detected.json",
            "required-media.json",
        ),
    ),
    "lottie-scroll-scrub": _ci(JS + PUBLIC + PKG, REF_SPEC),
    # Static parse of impl loadAnimation()/mount sites vs the spec slot->asset map.
    "lottie-slot-identity": _ci(JS, REF_SPEC),
    "runtime-frame-proof": _ci(
        JS + PUBLIC, REF_SPEC + ("required-media.json", "canvas-webgl-detection.json")
    ),
    "runtime-dom-parity": _ci(JS + PUBLIC, ("external-sdks.json", "required-media.json")),
    "runtime-text-sequence": _ci(
        SRC + PUBLIC + ENTRY + PKG,
        REF_DOM + ("runtime-text.json",),
    ),
    "runtime-image-validity": _ci(JS + PUBLIC, REF_IMAGES),
    "video-play-proof": _ci(JS + PUBLIC, ("required-media.json", "transition-spec.json")),
    "svg-provenance": _ci(JS + PUBLIC, ("head.json", "extracted.json")),
    "svg-dom-parity": _ci(JS + PUBLIC),
    "asset-placement": _ci(JS, REF_IMAGES + REF_REGIONS + REF_ASSET_SUB),
    # ── rollups (depend on constituent artifacts, not impl directly) ──
    "runtime-proof": _ci(JS + PUBLIC, RUNTIME_CONSTITUENTS),
    "transition-proof": _ci(JS, TRANSITION_CONSTITUENTS),
    # ── SRC (style + behavior) ──
    "forced-state-class": _ci(SRC),
    "junk-token": _ci(SRC),
    "body-opacity-unlock": _ci(SRC, REF_SPEC),
    "hidden-children": _ci(SRC),
    "typography-parity": _ci(SRC),
    "alignment-sweep": _ci(SRC, ("detected-breakpoints.json",) + REF_SECTIONS),
    # Live boundary sweep: rendered overflow/root-font behavior can change with
    # implementation source, entry wiring, public assets, or the captured and
    # implementation-derived breakpoint sets it merges into the probe widths.
    "breakpoint-collision": _ci(
        SRC + PUBLIC + ENTRY,
        ("detected-breakpoints.json", "impl-detected-breakpoints.json"),
    ),
    "mobile-viewport-parity": _ci(SRC),
    "mobile-responsive-coverage": _ci(SRC, ("detected-breakpoints.json",)),
    # Live resize probe: reads impl JS behavior (resize handlers) + package.json
    # against the ref's responsive breakpoints / sizing expressions.
    "resize-behavior": _ci(JS + PKG, ("detected-breakpoints.json", "sizing-expressions.json")),
    # Live reflow-parity probe across desktop-band widths: impl layout comes
    # from source + styles; the row is gated on detected-breakpoints.json.
    "desktop-band-fluidity": _ci(SRC, ("detected-breakpoints.json",)),
    # Unpinned time-lapse probe of declared dynamic regions: impl behavior
    # from source; region discovery reads the optional curated
    # dynamic-regions.json plus the transition spec.
    "dynamic-behavior-parity": _ci(
        SRC, REF_SPEC + ("dynamic-regions.json", "regions.json")
    ),
    "live-parity-sweep": _ci(SRC + PUBLIC),
    "ref-screenshot-asset": _ci(SRC + PUBLIC, ("section-map.json",)),
    "blank-viewport": _ci(SRC + PUBLIC + ENTRY),
    # Live browser probe of the served preview (ref side probes REF_URL, reads
    # no ref-dir artifacts): head assets from ENTRY, overflow from styles,
    # scroll-state/header mutations from behavior source.
    "preview-runtime-health": _ci(SRC + PUBLIC + ENTRY),
    "tree-diff": _ci(SRC, REF_ASSET_SUB),
    "geometry-sanity": _ci(SRC + PUBLIC, REF_REGIONS),
    "scroll-coverage": _ci(SRC + PUBLIC, REF_REGIONS),
    "font-parity": _ci(SRC + PUBLIC),
    # Verifies root-relative font binaries actually landed in impl/public against
    # the optional transfer report. extracted.json is the required extraction
    # anchor: it keeps the no-report PASS fingerprintable, while a report that
    # later appears still joins the glob set and invalidates the cached verdict.
    "font-binaries-present": _ci(
        PUBLIC + CSS, ("extracted.json", "font-transfer.json")
    ),
    "required-media-coverage": _ci(SRC + PUBLIC + PKG, REF_SPEC + ("required-media.json",)),
    "color-token-grounding": _ci(SRC, REF_GROUNDING + ("extracted.json",)),
    "duration-easing-grounding": _ci(SRC, REF_SPEC + ("transition-coverage.json",)),
    "scroll-end-completion": _ci(SRC, REF_SPEC),
    "reveal-trigger": _ci(SRC, REF_SPEC),
    "state-reveal": _ci(SRC, REF_SPEC),
    "scroll-anim-temporal": _ci(SRC, REF_SPEC),
    "transition-fires": _ci(SRC, REF_SPEC + REF_ASSET_SUB),
    "transition-compare": _ci(SRC, REF_SPEC),
    "transition-trajectory": _ci(SRC, REF_SPEC + REF_ASSET_SUB),
    "hover-state-compare": _ci(
        SRC, REF_SPEC + REF_ASSET_SUB + ("regions.json", "hover-css-rules.json")
    ),
    "hover-tree-diff": _ci(SRC, REF_SPEC),
    "click-state-compare": _ci(SRC, REF_SPEC + ("regions.json",)),
    "video-motion-compare": _ci(SRC + PUBLIC, REF_SPEC + REF_ASSET_SUB),
    # VLM "automated eyeball": judges live static crops + a scroll-motion sweep.
    # Impl behavior/style come from SRC; the crop pairing is driven off the ref
    # section map (crops themselves live under sections/, regenerated with it).
    "visual-fidelity-judge": _ci(
        # Judges the LIVE rendered impl (public assets/fonts included) and
        # reads the section crop pairs (codex P2: SRC-only hashing reused a
        # stale verdict across public-asset or crop regeneration).
        SRC + PUBLIC,
        # transition-spec.json drives the derived post-scroll settle window.
        # codex C2: also hash per-viewport crops — the judge scores whichever set
        # is freshest (crop_sets globs sections/viewports/<WxH>/), so hashing only
        # the top-level crops let a viewport-crop regeneration reuse a stale
        # visual-fidelity-judge.json.
        ("section-map.json", "sections/ref/*.png", "sections/impl/*.png",
         "sections/viewports/*/ref/*.png", "sections/viewports/*/impl/*.png",
         "transition-spec.json"),
    ),
    "masked-region-static": _ci(SRC),
    # ── ref-artifact-only (no impl input) ──
    "alignment-parity": _ci((), REF_SECTIONS + ("transition-spec.json",)),
    "runtime-spec-coverage": _ci((), REF_SPEC + ("generation-plan.json",)),
    "capture-artifact-inventory": _ci(
        (), ("regions.json", "section-map.json", "transition-spec.json")
    ),
    # ── asset checks ──
    "asset-transfer": _ci(PUBLIC, REF_IMAGES + REF_ASSET_SUB),
    "asset-utilization": _ci(SRC, REF_IMAGES + REF_ASSET_SUB),
    "image-fidelity": _ci(SRC, REF_IMAGES),
    "remote-asset-ref": _ci(SRC, REF_IMAGES),
}


def get_check_inputs(check_id: str) -> CheckInputs | None:
    """Declared inputs for a check, or None if the check_id is UNREGISTERED.

    None is the conservative signal: callers must fall back to the legacy
    newest-mtime staleness rather than treat the check as fresh."""
    return CHECK_INPUTS.get(check_id)


def all_known_check_ids() -> frozenset[str]:
    return frozenset(CHECK_INPUTS)


def sidecar_path(ref_dir: str | Path, check_id: str) -> Path:
    """The ONE sidecar-path deriver. Keyed by check_id, slash-sanitised to a flat
    filename so a slash-bearing id never escapes into a subdir, and identical
    whether called from the Python gate or the bash CLI."""
    token = check_id.replace("/", "__").replace("\\", "__")
    return Path(ref_dir) / f".checkhash__{token}.inputhash"


def _expand_braces(pattern: str) -> list[str]:
    """Expand {a,b}{c,d} brace alternations (pathlib does not). Returns the
    cartesian product of all brace groups in left-to-right order."""
    start = pattern.find("{")
    if start == -1:
        return [pattern]
    depth = 0
    end = -1
    for i in range(start, len(pattern)):
        if pattern[i] == "{":
            depth += 1
        elif pattern[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return [pattern]  # unbalanced; treat literally
    prefix, body, suffix = pattern[:start], pattern[start + 1 : end], pattern[end + 1 :]
    out: list[str] = []
    for alt in body.split(","):
        for rest in _expand_braces(suffix):
            out.append(prefix + alt + rest)
    return out


class InputFingerprintUnavailable(OSError):
    """A declared input side cannot be proven complete and readable."""


def _iter_files(root: Path, globs: tuple[str, ...]) -> list[tuple[str, Path]]:
    """Resolve globs under root to sorted, deduped ``(relpath, path)`` pairs.

    A missing/non-directory root and a readable tree with no matches both
    return ``[]``. Traversal/stat failures raise
    :class:`InputFingerprintUnavailable`, so callers never collapse
    unreadable evidence into the same state as a legitimately empty match.
    """
    try:
        root_mode = root.stat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as exc:
        raise InputFingerprintUnavailable(
            f"cannot inspect declared input root {root}: {exc}"
        ) from exc
    if not stat.S_ISDIR(root_mode):
        return []

    traversal_error: OSError | None = None

    def _on_walk_error(exc: OSError) -> None:
        nonlocal traversal_error
        traversal_error = exc

    # pathlib.glob() suppresses some scandir failures on newer Python
    # versions. Audit the readable tree separately so permission errors cannot
    # silently look like "no files matched". Prune dependency/build dirs before
    # descending.
    for _current, dirs, _files in os.walk(
        root, topdown=True, onerror=_on_walk_error, followlinks=False
    ):
        dirs[:] = [name for name in dirs if name not in PRUNE_DIRS]
        if traversal_error is not None:
            raise InputFingerprintUnavailable(
                f"cannot traverse declared input root {root}: {traversal_error}"
            ) from traversal_error
    if traversal_error is not None:
        raise InputFingerprintUnavailable(
            f"cannot traverse declared input root {root}: {traversal_error}"
        ) from traversal_error

    seen: dict[str, Path] = {}
    for raw in globs:
        for pat in _expand_braces(raw):
            try:
                matches = list(root.glob(pat))
            except (ValueError, OSError) as exc:
                raise InputFingerprintUnavailable(
                    f"cannot resolve declared input glob {pat!r} under {root}: {exc}"
                ) from exc
            for p in matches:
                try:
                    mode = p.stat().st_mode
                    if not stat.S_ISREG(mode):
                        continue
                    rel = p.relative_to(root)
                except OSError as exc:
                    raise InputFingerprintUnavailable(
                        f"cannot stat declared input {p}: {exc}"
                    ) from exc
                except ValueError as exc:
                    raise InputFingerprintUnavailable(
                        f"declared input escaped root {root}: {p}"
                    ) from exc
                if PRUNE_DIRS.intersection(rel.parts):
                    continue
                seen[rel.as_posix()] = p
    return sorted(seen.items())


def _declared_side_files(
    root: str | Path | None,
    globs: tuple[str, ...],
    side: str,
) -> list[tuple[str, Path]]:
    """Return a fully provable declared side or raise unavailable."""
    if root is None:
        raise InputFingerprintUnavailable(f"{side} input root is unavailable")
    root_path = Path(root)
    files = _iter_files(root_path, globs)
    if not files:
        raise InputFingerprintUnavailable(
            f"{side} declared inputs matched no files under {root_path}"
        )
    return files


def _hash_file_content(hasher: object, path: Path, size: int) -> None:
    """Hash one file using full streamed content unless it is large binary media."""
    if size > MAX_HASH_BYTES and path.suffix.lower() in _BINARY_MEDIA_SUFFIXES:
        hasher.update(f"size={size}".encode())  # type: ignore[attr-defined]
        return
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            hasher.update(chunk)  # type: ignore[attr-defined]


def compute_check_input_hash(
    impl_root: str | Path | None, ref_dir: str | Path | None, check_id: str
) -> str | None:
    """Fingerprint a check's declared inputs.

    Returns:
      * None  — check_id is UNREGISTERED (caller falls back to legacy staleness).
      * ""    — registered but input-independent (never stale).
      * hex   — sha256 over sorted ``(side/relpath, content-or-size)`` pairs.
    """
    spec = get_check_inputs(check_id)
    if spec is None:
        return None
    if not spec.impl and not spec.ref:
        # Explicitly input-independent (host capacity probe, ref stamp).
        return ""
    # Every declared side must be provable. Hashing an empty impl side together
    # with matching ref artifacts would otherwise let ref-only evidence certify
    # a missing clone.
    entries: list[tuple[str, Path]] = []
    if spec.impl:
        for rel, p in _declared_side_files(impl_root, spec.impl, "implementation"):
            entries.append((f"impl/{rel}", p))
    if spec.ref:
        for rel, p in _declared_side_files(ref_dir, spec.ref, "reference"):
            entries.append((f"ref/{rel}", p))
    entries.sort(key=lambda kv: kv[0])
    h = hashlib.sha256()
    for key, p in entries:
        h.update(key.encode("utf-8"))
        h.update(b"\0")
        try:
            size = p.stat().st_size
            _hash_file_content(h, p, size)
        except OSError as exc:
            raise InputFingerprintUnavailable(
                f"cannot fingerprint declared input {p}: {exc}"
            ) from exc
        h.update(b"\0")
    return h.hexdigest()


def newest_input_mtime(
    impl_root: str | Path | None, ref_dir: str | Path | None, check_id: str
) -> float | None:
    """Newest mtime across a check's DECLARED input files (same impl+ref glob set
    the hash uses). The no-sidecar migration fallback consults this instead of a
    fixed src+public sweep, so the fallback scans exactly the declared inputs
    (no under-scan of app/pages/components/package.json) and the Python gate and
    bash dispatcher cannot diverge on which files count.

    Returns None for an UNREGISTERED check or when any declared side is
    unavailable, empty, unreadable, or cannot be traversed. Registered
    input-independent checks return 0.0.
    """
    spec = get_check_inputs(check_id)
    if spec is None:
        return None
    if not spec.impl and not spec.ref:
        return 0.0
    newest = 0.0
    try:
        impl_files = (
            _declared_side_files(impl_root, spec.impl, "implementation")
            if spec.impl
            else []
        )
        ref_files = (
            _declared_side_files(ref_dir, spec.ref, "reference")
            if spec.ref
            else []
        )
        for _rel, p in (*impl_files, *ref_files):
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError as exc:
                raise InputFingerprintUnavailable(
                    f"cannot stat declared input {p}: {exc}"
                ) from exc
    except InputFingerprintUnavailable:
        return None
    return newest


def _main(argv: list[str]) -> int:
    """CLI for the bash dispatcher. Subcommands:
    hash    <impl_root> <ref_dir> <check_id>  -> prints UNREGISTERED | EMPTY |
                                                  UNAVAILABLE | <hex>
    mtime   <impl_root> <ref_dir> <check_id>  -> prints UNREGISTERED |
                                                  UNAVAILABLE | <float mtime>
    sidecar <ref_dir> <check_id>              -> prints the sidecar path
    """
    if len(argv) < 1:
        print("usage: check_inputs.py hash|sidecar ...", file=sys.stderr)
        return 2
    cmd = argv[0]
    if cmd == "sidecar":
        if len(argv) != 3:
            print("usage: check_inputs.py sidecar <ref_dir> <check_id>", file=sys.stderr)
            return 2
        print(sidecar_path(argv[1], argv[2]))
        return 0
    if cmd == "hash":
        if len(argv) != 4:
            print(
                "usage: check_inputs.py hash <impl_root> <ref_dir> <check_id>",
                file=sys.stderr,
            )
            return 2
        impl_root = argv[1] or None
        ref_dir = argv[2] or None
        try:
            result = compute_check_input_hash(impl_root, ref_dir, argv[3])
        except InputFingerprintUnavailable:
            print("UNAVAILABLE")
            return 0
        if result is None:
            print("UNREGISTERED")
        elif result == "":
            print("EMPTY")
        else:
            print(result)
        return 0
    if cmd == "mtime":
        if len(argv) != 4:
            print(
                "usage: check_inputs.py mtime <impl_root> <ref_dir> <check_id>",
                file=sys.stderr,
            )
            return 2
        check_id = argv[3]
        mt = newest_input_mtime(argv[1] or None, argv[2] or None, check_id)
        if get_check_inputs(check_id) is None:
            print("UNREGISTERED")
        elif mt is None:
            print("UNAVAILABLE")
        else:
            print(repr(mt))
        return 0
    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
