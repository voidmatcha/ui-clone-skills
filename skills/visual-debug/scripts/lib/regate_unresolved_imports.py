#!/usr/bin/env python3
"""Post-emit re-gate: reconcile emitted imports against files that exist.

WHY THIS EXISTS. Every driver is decided by TWO independent predicates — one in
scaffold_to_jsx.py deciding whether App.tsx imports it, one in
emit_scroll_helpers.py deciding whether the file is written. When a pair
disagrees the generated project does not compile. Observed on a reference whose
extraction was fully green: the scaffold emitted "./lib/ScrollLatchDriver" and
the build could not resolve it, because the plan declared `scrollLatch.required`
with a non-empty `sites` list whose every entry was an observer description
carrying neither endState nor progress — so each site was dropped by the
emitter's per-site validation and no file was ever written.

WHY NOT MIRROR THE PREDICATES. There are ~12 driver imports across three
emitters (Next `@/lib/...`, vite `./lib/...`, and a third alias form). Mirroring
means keeping every pair in sync forever, and a new driver silently reopens the
hole. This pass runs AFTER emission and asks ground truth instead: does the
imported file exist on disk? Same reasoning as Fix 115's "mount implies emit".

WHAT IT DOES.
  1. A dangling import of a KNOWN DRIVER is removed, along with its <Driver />
     mount. The driver was never written, so it was never going to run —
     removing the mount is the honest outcome. Emitting a stub instead would
     fabricate a capability the generation plan does not specify, which is the
     failure mode this campaign exists to prevent. Every removal is reported on
     stderr; this pass is never silent.
  2. Any OTHER dangling relative/alias import is a real fault (a missing
     component, a bad path) and fails the run. Stripping it would hide an
     emitter bug. This is the missing build gate — nothing in the pipeline
     previously checked that the generated tree could resolve its own imports.

Usage:
  regate_unresolved_imports.py <impl-dir> [--check] [--report <path>]

  --check reports without mutating (the verification-plan row). Exit 0 clean,
  3 when unresolved imports remain (or, under --check, when any were found).
  --report writes the evidence artifact the plan row declares in `produces`;
  every other check writes one, and staleness tracking reads it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Default-imported helper modules the scaffold mounts. A dangling import of one
# of these is a gate-pair disagreement, not a missing hand-written file.
DRIVER_NAMES = frozenset(
    {
        "ScrollReveal",
        "ScrollScrub",
        "SmoothScroll",
        "ScrollStateDriver",
        "ScrollClassToggleDriver",
        "HoverClassToggleDriver",
        "ScrollLatchDriver",
        "ScrollLinkedStyleDriver",
        "SwiperActivator",
        "VideoAutoplayKick",
        "StateRevealDriver",
        "IOClassRevealDriver",
        "WordRevealDriver",
    }
)

_SCAN_SUFFIXES = (".tsx", ".ts", ".jsx", ".js")
# Resolution order mirrors the bundlers': exact file, then extensions, then
# directory index.
_TRY_SUFFIXES = (".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs", ".json", ".css")

_IMPORT_RE = re.compile(
    r"""^\s*import\s+(?P<clause>[^'"]*?)\s*from\s*['"](?P<spec>[^'"]+)['"]\s*;?\s*$""",
    re.MULTILINE,
)
# Side-effect import: `import './x.css';`
_BARE_IMPORT_RE = re.compile(r"""^\s*import\s*['"](?P<spec>[^'"]+)['"]\s*;?\s*$""", re.MULTILINE)
# Every OTHER way a module specifier reaches the bundler. A re-export or a
# dynamic import breaks the build exactly like a static import; scanning only
# `import ... from` left four of five forms unchecked. These are reported, never
# rewritten — a driver is only ever stripped from a real `import ... from`.
_OTHER_SPEC_RES = (
    # export { x } from '...'   /   export * from '...'   /   export * as ns from '...'
    re.compile(r"""^\s*export\s+(?:\*(?:\s+as\s+[A-Za-z_$][\w$]*)?|\{[^}]*\})\s*from\s*['"](?P<spec>[^'"]+)['"]""", re.MULTILINE),
    # import('...') — dynamic / lazy
    re.compile(r"""\bimport\s*\(\s*['"](?P<spec>[^'"]+)['"]\s*\)"""),
    # require('...')
    re.compile(r"""\brequire\s*\(\s*['"](?P<spec>[^'"]+)['"]\s*\)"""),
)


def _default_name(clause: str) -> str | None:
    """The default-imported identifier, or None for named/namespace-only."""
    head = clause.split(",", 1)[0].strip()
    if not head or head.startswith("{") or head.startswith("*"):
        return None
    return head if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", head) else None


def _resolve_path(spec: str, from_file: Path, src_root: Path) -> Path | None:
    """Concrete file a local specifier resolves to, or None when it dangles.

    Returns None for bare package specifiers too — node_modules is not our
    business, and callers distinguish via `_resolve`.
    """
    if spec.startswith("@/"):
        base = src_root / spec[2:]
    elif spec.startswith("./") or spec.startswith("../"):
        base = (from_file.parent / spec).resolve()
    else:
        return None
    if base.is_file():
        return base
    for suffix in _TRY_SUFFIXES:
        cand = base.with_name(base.name + suffix)
        if cand.is_file():
            return cand
    if base.is_dir():
        for suffix in _TRY_SUFFIXES:
            cand = base / f"index{suffix}"
            if cand.is_file():
                return cand
    return None


def _resolve(spec: str, from_file: Path, src_root: Path) -> bool:
    if not (spec.startswith("@/") or spec.startswith("./") or spec.startswith("../")):
        return True  # bare package specifier — node_modules is not our business
    return _resolve_path(spec, from_file, src_root) is not None


# ── Named-export reconciliation ──────────────────────────────────────────────
# A module that RESOLVES can still be missing the symbols an importer asks for.
# A development compiler may emit the page with import warnings and still serve
# HTTP 200, leaving the requested binding `undefined`; a production TypeScript
# build catches it later. Observed on realfood-v4: MotionController imported ten
# scroll constants from `@/generated/motion-skeletons`, a file
# emit-motion-skeletons.sh regenerates as `use*` HOOKS and has never exported a
# constant. The first deref (`NAV_REVEAL_OUTPUT[0]`) threw inside the effect, and
# every channel that controller owned — nav state machine, hero-video width
# scrub, broken-system reveal, disintegrating chars, word reveal, stats bars,
# foods parallax, FAQ accordion — was dead on the served development page. This
# re-gate makes the same fault fail before the later production build.
_NAMED_CLAUSE_RE = re.compile(r"\{(?P<names>[^}]*)\}")
_EXPORT_DECL_RE = re.compile(
    r"^\s*export\s+(?!default\b)(?:declare\s+)?(?:abstract\s+)?"
    r"(?:async\s+)?(?:function\*?|class|type|interface|const\s+enum|enum)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_EXPORT_VAR_START_RE = re.compile(
    r"^\s*export\s+(?!default\b)(?:declare\s+)?"
    r"(?:const(?!\s+enum\b)|let|var)\s+",
    re.MULTILINE,
)
_EXPORT_VAR_DECL_RE = re.compile(
    r"^\s*export\s+(?!default\b)(?:declare\s+)?"
    r"(?:const(?!\s+enum\b)|let|var)\s+(?P<body>[^;\n]+);",
    re.MULTILINE,
)
_EXPORT_LIST_RE = re.compile(r"^\s*export\s*\{(?P<names>[^}]*)\}", re.MULTILINE)
_EXPORT_STAR_RE = re.compile(r"^\s*export\s*\*", re.MULTILINE)


def _imported_names(clause: str) -> list[str]:
    """Named bindings requested by an import clause, by their SOURCE name."""
    if clause.strip().startswith("type "):
        return []
    match = _NAMED_CLAUSE_RE.search(clause)
    if not match:
        return []
    names = []
    for part in match.group("names").split(","):
        part = part.strip()
        if not part or part.startswith("type "):
            continue  # `import { type X }` is erased before the bundler runs
        source = part.split(" as ", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_$][\w$]*", source):
            names.append(source)
    return names


def _exported_variable_names(text: str) -> set[str] | None:
    """Named bindings from simple, semicolon-terminated variable exports.

    A conservative parser is deliberate: arrays, objects, calls, and strings
    may all contain commas, while destructuring and multiline/ASI declarations
    need a real TypeScript parser. If a declaration is outside the subset we
    can prove, return None so the gate stays silent instead of rejecting a
    valid generated tree.
    """
    starts = list(_EXPORT_VAR_START_RE.finditer(text))
    matches = list(_EXPORT_VAR_DECL_RE.finditer(text))
    if len(starts) != len(matches):
        return None

    names: set[str] = set()
    for match in matches:
        body = match.group("body")
        parts: list[str] = []
        start = 0
        depth = 0
        quote = ""
        escaped = False
        for index, char in enumerate(body):
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
                continue
            if char in "'\"`":
                quote = char
            elif char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
                if depth < 0:
                    return None
            elif char == "," and depth == 0:
                parts.append(body[start:index])
                start = index + 1
        if quote or depth:
            return None
        parts.append(body[start:])

        for part in parts:
            declared = re.match(r"\s*(?P<name>[A-Za-z_$][\w$]*)\b", part)
            if not declared:
                return None  # destructuring or syntax outside the safe subset
            names.add(declared.group("name"))
    return names


def _exported_names(target: Path) -> set[str] | None:
    """Names a module exports, or None when it cannot be reasoned about."""
    if target.suffix not in _SCAN_SUFFIXES:
        return None
    text = target.read_text(encoding="utf-8", errors="replace")
    if _EXPORT_STAR_RE.search(text):
        return None  # re-export barrel — resolving it needs a full module graph
    names = {m.group("name") for m in _EXPORT_DECL_RE.finditer(text)}
    variable_names = _exported_variable_names(text)
    if variable_names is None:
        return None
    names.update(variable_names)
    for match in _EXPORT_LIST_RE.finditer(text):
        for part in match.group("names").split(","):
            part = part.strip()
            if not part:
                continue
            alias = part.split(" as ")[-1].strip()
            if re.fullmatch(r"[A-Za-z_$][\w$]*", alias):
                names.add(alias)
    return names


def _strip_driver(text: str, name: str, spec: str) -> str:
    """Remove the import line and every self-closing mount of `name`."""
    text = re.sub(
        rf"""^\s*import\s+{re.escape(name)}\s*from\s*['"]{re.escape(spec)}['"]\s*;?\s*\n""",
        "",
        text,
        flags=re.MULTILINE,
    )
    # `<Driver />` / `<Driver/>` — mounts are always self-closing and prop-less
    # in the emitted App shells; anything else is hand-written and left alone.
    text = re.sub(rf"""^[ \t]*<{re.escape(name)}\s*/>[ \t]*\n""", "", text, flags=re.MULTILINE)
    return text


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: regate_unresolved_imports.py <impl-dir> [--check]", file=sys.stderr)
        return 2
    impl = Path(argv[1])
    rest = argv[2:]
    check_only = "--check" in rest
    report_path = None
    if "--report" in rest:
        idx = rest.index("--report")
        if idx + 1 < len(rest):
            report_path = Path(rest[idx + 1])

    def _write_report(status: str, stripped: list[str], fatal: list[str]) -> None:
        if report_path is None:
            return
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "status": status,
                        "checkOnly": check_only,
                        "strippedDriverMounts": stripped,
                        "unresolvedImports": fatal,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    src_root = impl / "src"
    if not src_root.is_dir():
        _write_report("skip", [], [])
        return 0

    files = [p for p in src_root.rglob("*") if p.suffix in _SCAN_SUFFIXES and p.is_file()]
    stripped: list[str] = []
    fatal: list[str] = []

    for path in sorted(files):
        text = path.read_text(encoding="utf-8", errors="replace")
        original = text
        for match in list(_IMPORT_RE.finditer(original)) + list(
            _BARE_IMPORT_RE.finditer(original)
        ):
            spec = match.group("spec")
            if _resolve(spec, path, src_root):
                # Resolves — but does it actually export what was asked for?
                clause = match.groupdict().get("clause") or ""
                wanted = _imported_names(clause)
                if wanted:
                    target = _resolve_path(spec, path, src_root)
                    exported = _exported_names(target) if target else None
                    if exported is not None:
                        missing = [n for n in wanted if n not in exported]
                        if missing:
                            fatal.append(
                                f"{path.relative_to(impl)}: {spec!r} does not export "
                                + ", ".join(sorted(missing))
                                + " — the requested binding is unavailable"
                            )
                continue
            clause = match.groupdict().get("clause") or ""
            name = _default_name(clause)
            rel = path.relative_to(impl)
            if name and name in DRIVER_NAMES and spec.rstrip("/").endswith(name):
                if check_only:
                    fatal.append(f"{rel}: unwritten driver {name} imported from {spec}")
                    continue
                candidate = _strip_driver(text, name, spec)
                # Removing the import is only safe when NOTHING still references
                # the identifier. A wrapper usage (`<ScrollReveal>…</ScrollReveal>`)
                # or a mount carrying props survives the self-closing strip, and
                # dropping the import out from under it leaves an undefined
                # identifier — a WORSE tree than the one we started with. Those
                # cases are a real emitter fault and must fail loudly.
                if re.search(rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])", candidate):
                    fatal.append(
                        f"{rel}: driver {name} imported from {spec} was never written, "
                        "and it is still referenced (wrapper or propful usage) — "
                        "cannot be removed safely"
                    )
                    continue
                text = candidate
                stripped.append(f"{rel}: {name} ({spec})")
            else:
                fatal.append(f"{rel}: unresolved import {spec!r}")
        # Re-exports, dynamic imports and require() reach the bundler too. They
        # are never rewritten — only reported — because there is no import
        # statement to remove and no safe edit for a live call site.
        for pattern in _OTHER_SPEC_RES:
            for match in pattern.finditer(original):
                spec = match.group("spec")
                if _resolve(spec, path, src_root):
                    continue
                fatal.append(f"{path.relative_to(impl)}: unresolved import {spec!r}")
        if text != original and not check_only:
            path.write_text(text, encoding="utf-8")

    for entry in stripped:
        print(
            "scaffold-to-jsx: re-gate removed unwritten driver mount — " + entry,
            file=sys.stderr,
        )
    if stripped:
        print(
            f"scaffold-to-jsx: re-gate reconciled {len(stripped)} emitted import(s) "
            "against files the helper emitter actually wrote.",
            file=sys.stderr,
        )

    if fatal:
        _write_report("fail", stripped, fatal)
        print(
            "scaffold-to-jsx: UNRESOLVED IMPORTS — the generated project cannot build:",
            file=sys.stderr,
        )
        for entry in fatal:
            print(f"  {entry}", file=sys.stderr)
        return 3
    _write_report("pass", stripped, fatal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
