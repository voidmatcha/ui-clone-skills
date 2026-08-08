"""Deterministic artifact-handling helpers for `scripts/extract/capture.sh`.

Extracted so the JSON/parsing/timing layer is unit-testable
while agent-browser orchestration stays in shell. Shell wrapper calls
these via:
    python3 _capture_artifacts.py parse-height '"5400"'
    python3 _capture_artifacts.py write-regions <ref_dir> <h> <w>
    python3 _capture_artifacts.py summarize <ref_dir>

Public API:
    parse_page_height(raw: str, *, fallback: int = 5000) -> int
    write_regions_json(ref_dir: Path, page_height: int, viewport_width: int = 1440) -> None
    derive_regions_json(transition_spec, section_map, *, viewport_width=1440) -> dict | None
    produce_regions_json(ref_dir: Path, *, viewport_width: int = 1440) -> dict | None
    summarize_artifacts(ref_dir: Path) -> dict
    write_capture_error(...) -> dict
    main(argv: list[str]) -> int
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_FALLBACK_HEIGHT = 5000


def parse_page_height(raw: str, *, fallback: int = _FALLBACK_HEIGHT) -> int:
    """Parse the JSON-encoded value returned by `agent-browser eval`.

    `agent-browser eval` double-encodes its return value (a number comes
    back as `"5400"` — string containing a number). We unwrap once to
    get the int. Fallback when:
      - raw is empty or whitespace
      - raw isn't valid JSON
      - decoded value isn't numeric
      - decoded value is <= 0 (would cause divide-by-zero downstream)
    """
    if not raw or not raw.strip():
        return fallback
    text = raw.strip()
    # Try direct int parse first (raw int output from some browsers).
    try:
        n = int(text)
        return n if n > 0 else fallback
    except ValueError:
        pass
    # Try JSON unwrap (string-wrapped number is the agent-browser case).
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return fallback
    # Decoded could be a number or a string containing a number.
    if isinstance(decoded, int | float):
        n = int(decoded)
        return n if n > 0 else fallback
    if isinstance(decoded, str):
        try:
            n = int(decoded.strip())
            return n if n > 0 else fallback
        except ValueError:
            return fallback
    return fallback


def write_regions_json(
    ref_dir: Path,
    page_height: int,
    viewport_width: int = 1440,
) -> None:
    """Write the minimal `regions.json` containing a single full-page region.

    Proper region segmentation belongs to the `ui-capture` skill's
    detection pipeline. This minimal shape unblocks the `reference`
    gate row that requires `regions.json` to exist.
    """
    ref_dir = Path(ref_dir)
    ref_dir.mkdir(parents=True, exist_ok=True)
    # Resume-safe: never downgrade a real regions.json back to the placeholder.
    # A partial re-run of the early capture phases (e.g. `run --phases 0A,1,2`)
    # calls write-regions again, but the later enrichment phase that upgraded
    # regions.json (transition-categorize -> derive-regions) does not re-run in
    # that same invocation. Clobbering here would regress a previously-passing
    # `reference` gate to the placeholder. Mirror produce_regions_json's
    # "never downgrade a real detection" contract: preserve any existing
    # non-placeholder regions.json.
    existing = _read_json(ref_dir / "regions.json")
    if (
        isinstance(existing, dict)
        and not existing.get("placeholder", False)
        and existing.get("regions")
    ):
        return
    payload: dict[str, Any] = {
        # Self-incriminating: this file only unblocks the existence row.
        # Real Phase-2 detection must replace it — the reference gate fails
        # placeholder regions when the site shows motion signals.
        "placeholder": True,
        "detectionRan": False,
        "regions": [
            {
                "name": "full-page",
                "x": 0,
                "y": 0,
                "width": int(viewport_width),
                "height": int(page_height),
            }
        ]
    }
    (ref_dir / "regions.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


# ── sound regions derivation (Fix 5 redesign) ──────────────────────────────
#
# regions.json is a deterministic PROJECTION of transition-spec.json's real
# transitions into the regions schema (name / triggerType / selector /
# artifacts, plus optional in-bounds geometry resolved from section-map.json).
# This is NOT pixel-diffing — the reverted detector diffed non-overlapping
# scroll-position slices and fabricated bands. Here a static page (no real
# transitions) yields None so the caller keeps the honest placeholder, and a
# re-run with the spec transiently absent never downgrades a real regions.json.


def _read_json(path: Path) -> Any:
    """Load JSON, returning None on missing/unreadable/malformed input."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _is_valid_css_selector(selector: object) -> bool:
    """True when `selector` looks like a CSS selector, not a declaration
    fragment. Mirrors ui_clone.extraction_artifacts._is_valid_selector so the
    producer and the reference gate reject the same garbage (e.g. the
    auto-minted spec's 'transform .2s ease;&')."""
    s = " ".join(str(selector or "").split())
    if not s or len(s) > 400:
        return False
    if any(c in s for c in "{};&"):
        return False
    return bool(re.search(r"[.#A-Za-z]", s))


def _normalize_trigger_type(trigger: object) -> str | None:
    """Map a transition `trigger` to a canonical region triggerType.

    Real specs carry descriptive sentences ('scroll: GSAP ScrollTrigger scrub
    on .page-hero', 'page load (first paint, scroll-guard)'). The region
    triggerType must be the canonical leading class so consumers' prefix
    matching works and benchmark-harvest's distinct-triggerType count is not
    polluted. Classification is by LEADING keyword (page-load is checked before
    scroll so a 'scroll-guard' substring cannot misclassify a load trigger).
    A bare 'click' becomes 'click-action'; an explicit 'click-*' subtype is
    preserved for the click-state-compare consumer."""
    t = " ".join(str(trigger or "").split()).lower()
    if not t:
        return None
    if t.startswith("page load") or t.startswith("load") or "autoplay" in t:
        return "load"
    if t.startswith("hover"):
        return "hover"
    if t.startswith("click"):
        head = t.split()[0]
        return head if head.startswith("click-") else "click-action"
    if t.startswith("scroll") or "scroll" in t:
        return "scroll"
    if "intersection" in t or t.startswith("io") or "reveal" in t:
        return "intersection"
    if t.startswith("focus"):
        return "focus"
    if t.startswith("drag"):
        return "drag"
    # Unknown free-text trigger -> None. The previous `t.split()[0]` fallback
    # minted a placeholder:false region for ANY junk trigger, fabricating
    # detection (multi-agent review M3). Fail safe: caller drops this entry.
    return None


def _selector_terminal_tokens(selector: str) -> tuple[set[str], set[str]]:
    """Class and id tokens of the selector's terminal simple-selector.

    Only the rightmost compound selector matters for which element animates
    (`.wrap .hero` animates `.hero`). Returns (class_tokens, id_tokens)."""
    segment = re.split(r"[ >+~]+", selector.strip())[-1] if selector.strip() else ""
    classes = set(re.findall(r"\.([A-Za-z0-9_-]+)", segment))
    ids = set(re.findall(r"#([A-Za-z0-9_-]+)", segment))
    return classes, ids


def _resolve_section_geometry(
    selector: str,
    section_map: Any,
    viewport_width: int,
) -> dict[str, int] | None:
    """Resolve a transition selector to a section's measured geometry.

    Returns an in-bounds {x,y,width,height} when the selector's terminal class
    or id matches a section, or None otherwise (→ selector-only region; we do
    NOT fabricate geometry). Off-canvas/fixed sections (top<0) or zero-height
    sections return None so no negative, out-of-bounds band is emitted."""
    if not isinstance(section_map, dict):
        return None
    classes, ids = _selector_terminal_tokens(selector)
    if not classes and not ids:
        return None
    for s in section_map.get("sections") or []:
        if not isinstance(s, dict):
            continue
        class_tokens = set(str(s.get("className") or "").split())
        section_id = s.get("id")
        matched = bool(classes & class_tokens) or bool(
            section_id and section_id in ids
        )
        if not matched:
            continue
        try:
            top = float(s.get("top"))
            height = float(s.get("height"))
        except (TypeError, ValueError):
            return None
        if top < 0 or height <= 0:
            return None
        return {
            "x": 0,
            "y": int(top),
            "width": int(viewport_width),
            "height": int(height),
        }
    return None


def derive_regions_json(
    transition_spec: Any,
    section_map: Any,
    *,
    viewport_width: int = 1440,
) -> dict[str, Any] | None:
    """Project real transition-spec transitions into the regions schema.

    Returns None when there is no real transition to project (static page) so
    the caller keeps the honest placeholder. `skipped[]` is never read."""
    if not isinstance(transition_spec, dict):
        return None
    transitions = transition_spec.get("transitions")
    if not isinstance(transitions, list):
        return None

    regions: list[dict[str, Any]] = []
    for t in transitions:
        if not isinstance(t, dict):
            continue
        selector = t.get("selector") or t.get("target")
        if not _is_valid_css_selector(selector):
            continue
        trigger_type = _normalize_trigger_type(t.get("trigger"))
        if not trigger_type:
            continue
        selector_s = " ".join(str(selector).split())
        region: dict[str, Any] = {
            "name": str(t.get("id") or selector_s),
            "triggerType": trigger_type,
            "selector": selector_s,
            # This region is a deterministic projection of transition-spec, NOT
            # independent capture proof. The marker tells the capture-artifact
            # inventory check to treat it as dispatch-only (no per-state capture
            # manifest is expected) instead of flagging a missing manifest.
            "dispatchOnly": True,
        }
        frames = t.get("reference_frames")
        if isinstance(frames, list):
            # Spec provenance only — these are whole-page static/ref frames the
            # spec cited, NOT a per-state {idle,hover,...} capture manifest, so
            # they live under referenceFrames (not the `artifacts` dict key the
            # inventory check validates).
            reference_frames = [f for f in frames if isinstance(f, str) and f]
            if reference_frames:
                region["referenceFrames"] = reference_frames
        geometry = _resolve_section_geometry(selector_s, section_map, viewport_width)
        if geometry:
            region.update(geometry)
        regions.append(region)

    if not regions:
        return None
    return {
        "placeholder": False,
        "detectionRan": True,
        "source": "derive-from-transition-spec",
        "derivedFrom": ["transition-spec.json", "section-map.json"],
        "regions": regions,
    }


def produce_regions_json(
    ref_dir: Path,
    *,
    viewport_width: int = 1440,
) -> dict[str, Any] | None:
    """Read ground truth from `ref_dir` and upgrade regions.json in place.

    Best-effort + idempotent: when derivation yields None (no real
    transitions, or the spec is transiently absent) the existing regions.json
    is left untouched — a real detection is never downgraded to placeholder,
    and the honest placeholder is never overwritten with a fabricated band."""
    ref_dir = Path(ref_dir)
    derived = derive_regions_json(
        _read_json(ref_dir / "transition-spec.json"),
        _read_json(ref_dir / "section-map.json"),
        viewport_width=viewport_width,
    )
    if derived is None:
        return None
    (ref_dir / "regions.json").write_text(
        json.dumps(derived, indent=2) + "\n",
        encoding="utf-8",
    )
    return derived


def summarize_artifacts(ref_dir: Path) -> dict[str, Any]:
    """Return the count summary capture.sh prints after capture finishes.

    Mirrors the shell `find ... | wc -l` calls (lines 92-95) so the
    same totals are available to Python callers and unit tests without
    a subprocess. Keys match the shell labels for readability.
    """
    ref_dir = Path(ref_dir)

    def _count(subpath: str, pattern: str = "*") -> int:
        d = ref_dir / subpath
        if not d.is_dir():
            return 0
        return sum(1 for p in d.glob(pattern) if p.is_file() and not p.name.startswith("."))

    return {
        "static_ref_screenshots": _count("static/ref"),
        "scroll_video_ref_videos": _count("scroll-video/ref"),
        "transitions_ref_videos": _count("transitions/ref"),
        "regions_json_present": (ref_dir / "regions.json").is_file(),
    }


def _relative_artifact_path(ref_dir: Path, artifact: str) -> str | None:
    """Return `artifact` relative to `ref_dir` when possible."""
    if not artifact:
        return None
    path = Path(artifact)
    if not path.is_absolute():
        return artifact
    try:
        return str(path.resolve().relative_to(ref_dir.resolve()))
    except ValueError:
        return artifact


def write_capture_error(
    ref_dir: Path,
    *,
    stage: str,
    exit_code: int,
    command: str = "",
    artifact: str = "",
    message: str = "",
) -> dict[str, Any]:
    """Write a structured Phase 1 capture failure diagnostic.

    `capture.sh` can fail after producing partial evidence. Persisting the
    failing stage and current artifact counts makes the next gate blocker
    actionable without requiring agents to recover shell logs.
    """
    ref_dir = Path(ref_dir)
    ref_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = _relative_artifact_path(ref_dir, artifact)
    payload: dict[str, Any] = {
        "error": "capture-step-failed",
        "phase": "reference",
        "stage": stage,
        "exitCode": int(exit_code),
        "command": command,
        "artifact": artifact_path,
        "message": message.strip(),
        "summary": summarize_artifacts(ref_dir),
        "nextAction": (
            "Inspect the named stage, command, and artifact path; rerun Phase 1 "
            "after fixing recorder/session lifecycle or browser capture setup."
        ),
    }
    (ref_dir / "capture-error.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def _print_summary(summary: dict[str, Any], ref_dir: Path) -> None:
    """Reproduce capture.sh's textual summary block."""
    print(f"capture.sh: Phase 1 artifacts written to {ref_dir}")
    print(f"  static/ref/: {summary['static_ref_screenshots']} screenshots")
    print(f"  scroll-video/ref/: {summary['scroll_video_ref_videos']} videos")
    print(f"  transitions/ref/: {summary['transitions_ref_videos']} videos")
    print(f"  regions.json: {'ok' if summary['regions_json_present'] else 'MISSING'}")


def main(argv: list[str]) -> int:
    """CLI entry point — dispatches to subcommand."""
    if not argv:
        print(
            "usage: _capture_artifacts.py "
            "{parse-height|write-regions|derive-regions|summarize|write-error} ...",
            file=sys.stderr,
        )
        return 2
    cmd = argv[0]
    rest = argv[1:]

    if cmd == "parse-height":
        if not rest:
            print("usage: parse-height <raw>", file=sys.stderr)
            return 2
        # Concatenate remaining args (shell may split JSON whitespace).
        raw = " ".join(rest)
        print(parse_page_height(raw))
        return 0

    if cmd == "write-regions":
        if len(rest) < 2:
            print(
                "usage: write-regions <ref_dir> <page_height> [viewport_width]",
                file=sys.stderr,
            )
            return 2
        ref_dir = Path(rest[0])
        try:
            page_height = int(rest[1])
        except ValueError:
            print(f"page_height must be integer, got: {rest[1]!r}", file=sys.stderr)
            return 2
        viewport_width = int(rest[2]) if len(rest) >= 3 else 1440
        write_regions_json(ref_dir, page_height, viewport_width)
        return 0

    if cmd == "derive-regions":
        if not rest:
            print(
                "usage: derive-regions <ref_dir> [viewport_width]",
                file=sys.stderr,
            )
            return 2
        ref_dir = Path(rest[0])
        viewport_width = 1440
        if len(rest) >= 2:
            try:
                viewport_width = int(rest[1])
            except ValueError:
                print(
                    f"viewport_width must be integer, got: {rest[1]!r}",
                    file=sys.stderr,
                )
                return 2
        result = produce_regions_json(ref_dir, viewport_width=viewport_width)
        if result is None:
            print(
                "derive-regions: no real transitions in transition-spec.json — "
                "left regions.json unchanged (honest placeholder preserved)"
            )
        else:
            print(
                f"derive-regions: wrote {len(result['regions'])} region(s) "
                f"to {ref_dir / 'regions.json'}"
            )
        return 0

    if cmd == "summarize":
        if not rest:
            print("usage: summarize <ref_dir>", file=sys.stderr)
            return 2
        ref_dir = Path(rest[0])
        summary = summarize_artifacts(ref_dir)
        _print_summary(summary, ref_dir)
        return 0

    if cmd == "write-error":
        if len(rest) < 3:
            print(
                "usage: write-error <ref_dir> <stage> <exit_code> [artifact] [command] [message]",
                file=sys.stderr,
            )
            return 2
        ref_dir = Path(rest[0])
        stage = rest[1]
        try:
            exit_code = int(rest[2])
        except ValueError:
            print(f"exit_code must be integer, got: {rest[2]!r}", file=sys.stderr)
            return 2
        artifact = rest[3] if len(rest) >= 4 else ""
        command = rest[4] if len(rest) >= 5 else ""
        message = rest[5] if len(rest) >= 6 else ""
        write_capture_error(
            ref_dir,
            stage=stage,
            exit_code=exit_code,
            artifact=artifact,
            command=command,
            message=message,
        )
        print(ref_dir / "capture-error.json")
        return 0

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
