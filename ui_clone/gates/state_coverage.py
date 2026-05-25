"""state-coverage gate — multi-snapshot capture artifacts vs impl source.

Inserted into GATE_ORDER between `pre-generate` and `post-implement`. Reads
the Phase A/B/C capture artifacts under `<ref_dir>/states/` and verifies
that the impl source has corresponding hooks:

  - states/splash/trajectory.json: if polls > 1 (class transitions
    detected on ref), impl/src must reference at least one of the
    captured body/html class strings (`is-loading`, `is-loaded`, etc.).
  - states/scroll/summary.json: if not static (page is scrollable),
    impl/src must show a scroll-state primitive (IntersectionObserver,
    ScrollTrigger, useScroll, useInView, scroll-snap, data-scroll, etc.).
  - states/hover/manifest.json: if entries non-empty, impl/src must
    contain at least one hover handler (`:hover`, `hover:`, `onMouseEnter`,
    `whileHover`, `onPointerEnter`).

Backward-compat: if `<ref_dir>/states/` is absent entirely, emit a single
pass with "skip" message — legacy ref dirs predate the multi-snapshot
capture pipeline. When the dir exists but one phase is missing, only the
present phases are checked (partial ref dirs from interrupted captures
stay valid).

Extracted from gate.py. Each function takes `self: "Gate"` and is rebound
onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .base import CheckResult

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401


# Recognized scroll-state primitives. Order matters only for the "found"
# message — first match wins. Sourced from the 26-site loop observation
# of which APIs impls actually use across React + Next.js + plain Vite
# stacks.
_SCROLL_PRIMITIVES: tuple[str, ...] = (
    "IntersectionObserver",
    "ScrollTrigger",
    "useScroll",
    "useInView",
    "scroll-snap",
    "data-scroll",
    "useScrollPosition",
    "framer-motion.*useScroll",
    "data-aos",
    "useElementOnScreen",
)


# Recognized hover handlers across React + plain JS + Tailwind. Pattern
# is regex, applied to file contents after light normalization.
_HOVER_HANDLERS: tuple[str, ...] = (
    r":hover\b",                 # CSS hover pseudoclass
    r"\bhover:[a-zA-Z-]",        # Tailwind hover: variant
    r"\bonMouseEnter\b",         # React onMouseEnter
    r"\bonMouseOver\b",          # React onMouseOver
    r"\bonPointerEnter\b",       # React onPointerEnter
    r"\bwhileHover\b",           # framer-motion
    r"\bmouseenter\b",           # raw addEventListener
    r"\bmouseover\b",            # raw addEventListener
    r"\bgroup-hover:",           # Tailwind group-hover
)


_SRC_GLOB_EXTS: tuple[str, ...] = (
    ".tsx", ".ts", ".jsx", ".js", ".css", ".scss", ".sass", ".vue", ".svelte", ".html",
)


def _read_src_text(impl_root: Path) -> str:
    """Concatenate impl/src/** file contents (limited by extension) into
    one searchable blob. Returns empty string when src/ is missing. Keeps
    each file separated by newlines so regex anchors still work."""
    src = impl_root / "src"
    if not src.is_dir():
        return ""
    pieces: list[str] = []
    for ext in _SRC_GLOB_EXTS:
        for path in src.rglob(f"*{ext}"):
            try:
                pieces.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return "\n".join(pieces)


def _extract_class_strings(trajectory: list[dict]) -> list[str]:
    """From a splash trajectory, return the unique non-empty body/html
    class strings — these are the hooks impl must reference."""
    classes: set[str] = set()
    for entry in trajectory:
        for key in ("bodyClass", "htmlClass"):
            raw = entry.get(key, "") if isinstance(entry, dict) else ""
            if not isinstance(raw, str):
                continue
            for token in raw.split():
                token = token.strip()
                # Skip generic Webflow + framework noise that any site has.
                if not token or token in {"body", "html", "no-js"}:
                    continue
                classes.add(token)
    return sorted(classes)


def _check_splash_coverage(ref_dir: Path, src_text: str) -> CheckResult | None:
    """None when not applicable (no splash dir, or no transitions). Otherwise
    one CheckResult (pass or fail)."""
    splash_dir = ref_dir / "states" / "splash"
    summary_path = splash_dir / "summary.json"
    trajectory_path = splash_dir / "trajectory.json"
    if not summary_path.is_file() or not trajectory_path.is_file():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return CheckResult(
            "state-coverage splash",
            "fail",
            "states/splash/{summary,trajectory}.json unreadable",
            fix="Re-run: bash scripts/extract/capture-states.sh <url> <session> <ref_dir>",
        )
    polls = int(summary.get("polls") or 0) if isinstance(summary, dict) else 0
    if polls <= 1:
        # No class transitions detected — splash check is N/A.
        return None
    if not isinstance(trajectory, list):
        return None
    classes = _extract_class_strings(trajectory)
    if not classes:
        # Transitions exist but produced no observable class hooks — N/A.
        return None
    found = [c for c in classes if c in src_text]
    if found:
        return CheckResult(
            "state-coverage splash",
            "pass",
            f"impl references {len(found)}/{len(classes)} splash class hook(s): "
            f"{', '.join(sorted(found)[:5])}",
        )
    return CheckResult(
        "state-coverage splash",
        "fail",
        (
            f"states/splash recorded {polls} transition(s) producing class "
            f"hooks {classes} but impl/src/** references none of them. "
            "Impl is missing the splash-state bridge — content likely jumps "
            "from no-render to settled without the ref's reveal sequence."
        ),
        fix=(
            "Add a loading-state mechanism to App.tsx that mirrors the "
            "ref's class transitions. Pattern: useState(loaded), apply "
            f"`className={{loaded ? '{classes[-1]}' : '{classes[0]}'}}` on "
            "the root element, flip after fonts/data settle."
        ),
    )


def _check_scroll_coverage(ref_dir: Path, src_text: str) -> CheckResult | None:
    scroll_dir = ref_dir / "states" / "scroll"
    summary_path = scroll_dir / "summary.json"
    if not summary_path.is_file():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return CheckResult(
            "state-coverage scroll",
            "fail",
            "states/scroll/summary.json unreadable",
            fix="Re-run: bash scripts/extract/capture-scroll.sh <url> <session> <ref_dir>",
        )
    if not isinstance(summary, dict):
        return None
    if summary.get("static") is True:
        # Page fits in viewport — scroll check is N/A.
        return None
    # Page is scrollable. Require at least one scroll-state primitive.
    matched: list[str] = []
    for primitive in _SCROLL_PRIMITIVES:
        # _SCROLL_PRIMITIVES contains both plain substrings and regexes; try regex first.
        try:
            if re.search(primitive, src_text):
                matched.append(primitive)
        except re.error:
            if primitive in src_text:
                matched.append(primitive)
    if matched:
        return CheckResult(
            "state-coverage scroll",
            "pass",
            f"impl uses scroll-state primitive(s): {', '.join(matched[:3])}",
        )
    return CheckResult(
        "state-coverage scroll",
        "fail",
        (
            "states/scroll shows a scrollable ref page but impl/src has "
            "no scroll-state primitive (IntersectionObserver, ScrollTrigger, "
            "useScroll, useInView, scroll-snap, data-scroll, ...). The impl "
            "renders a flat page — scroll-triggered reveals, sticky shrinks, "
            "and parallax position from the ref will all be absent."
        ),
        fix=(
            "Add IntersectionObserver-driven reveals to sections below the "
            "fold, or use a library: framer-motion `useScroll`, GSAP "
            "ScrollTrigger, or react-intersection-observer. See "
            "skills/ui-reverse-engineering/animation-detection.md."
        ),
    )


def _check_hover_coverage(ref_dir: Path, src_text: str) -> CheckResult | None:
    hover_dir = ref_dir / "states" / "hover"
    manifest_path = hover_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return CheckResult(
            "state-coverage hover",
            "fail",
            "states/hover/manifest.json unreadable",
            fix="Re-run: bash scripts/extract/capture-hover.sh <url> <session> <ref_dir>",
        )
    if not isinstance(manifest, dict):
        return None
    entries = manifest.get("entries", []) or []
    if not entries:
        # Ref has no hover signal — N/A.
        return None
    matched: list[str] = []
    for pattern in _HOVER_HANDLERS:
        if re.search(pattern, src_text):
            matched.append(pattern)
    if matched:
        return CheckResult(
            "state-coverage hover",
            "pass",
            (
                f"impl has {len(matched)} hover-handler pattern(s) "
                f"(ref captured {len(entries)} hover target(s))"
            ),
        )
    return CheckResult(
        "state-coverage hover",
        "fail",
        (
            f"states/hover captured {len(entries)} hover target(s) on ref "
            "but impl/src has no hover handlers (`:hover`, `hover:`, "
            "onMouseEnter, whileHover). The impl will be visually static "
            "where the ref reacts to cursor — common cause of structural-"
            "complete-but-feels-dead clones in the 26-site loop."
        ),
        fix=(
            "Add Tailwind `hover:` variants to buttons/cards/links (e.g. "
            "`hover:scale-105 hover:bg-red-500`), or React onMouseEnter "
            "with state, or framer-motion `whileHover`. The ref's hover "
            "manifest entries point to the exact selectors needing "
            "treatment (read states/hover/manifest.json)."
        ),
    )


def gate_state_coverage(self: Gate) -> list[CheckResult]:
    """Verify multi-snapshot capture artifacts have corresponding impl hooks.

    Reads <ref_dir>/states/{splash,scroll,hover}/ and grep-checks impl/src/**.
    Skips silently when states/ is absent (legacy ref dirs). When the dir
    exists, runs only the checks whose summary.json is present — partial
    captures don't penalize for missing phases.
    """
    states_root = self.ref_dir / "states"
    if not states_root.is_dir():
        return [
            CheckResult(
                "state-coverage",
                "pass",
                "no states/ directory — multi-snapshot capture not run (skip)",
            )
        ]

    impl_root = self._find_impl_root()
    if impl_root is None:
        # No impl yet — gate runs before post-implement; legitimate to skip.
        return [
            CheckResult(
                "state-coverage",
                "pass",
                "no impl/ root resolved — state-coverage check deferred until impl exists",
            )
        ]

    src_text = _read_src_text(impl_root)
    if not src_text:
        return [
            CheckResult(
                "state-coverage",
                "fail",
                f"impl_root={impl_root} has no src/** files matching {_SRC_GLOB_EXTS}",
                fix="Verify impl scaffolding completed before state-coverage runs.",
            )
        ]

    results: list[CheckResult] = []
    for check in (
        _check_splash_coverage(self.ref_dir, src_text),
        _check_scroll_coverage(self.ref_dir, src_text),
        _check_hover_coverage(self.ref_dir, src_text),
    ):
        if check is not None:
            results.append(check)

    if not results:
        # states/ exists but every phase summary reported N/A (static page
        # with no transitions and no hover).
        results.append(
            CheckResult(
                "state-coverage",
                "pass",
                "states/ present but all phases reported no signal to check (static page)",
            )
        )
    return results
