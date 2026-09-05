"""Spec gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import CheckResult
from .post_implement import _check_spec_bundle_grounding

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401


_REFERENCE_MEDIA_EXTENSIONS = frozenset(
    {
        ".gif",
        ".jpeg",
        ".jpg",
        ".m4v",
        ".mov",
        ".mp4",
        ".png",
        ".webm",
        ".webp",
    }
)
_REFERENCE_IMAGE_EXTENSIONS = frozenset(
    {
        ".gif",
        ".jpeg",
        ".jpg",
        ".png",
        ".webp",
    }
)
_REFERENCE_MEDIA_PATH_RE = re.compile(
    r"""(?<![\w])([^"'<>\s]+?\.(?:gif|jpe?g|m4v|mov|mp4|png|webm|webp))"""
    r"""(?=$|[\s,;)\]}])""",
    re.IGNORECASE,
)


def _reference_media_tokens(value: Any) -> list[str]:
    entries = [value] if isinstance(value, str) else value
    if not isinstance(entries, list):
        return []
    tokens: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            tokens.extend(match.group(1) for match in _REFERENCE_MEDIA_PATH_RE.finditer(entry))
    return tokens


def _resolve_reference_media(
    ref_dir: Path,
    token: str,
    sibling_dir: Path | None,
) -> Path | None:
    raw_path = Path(token)
    candidates = [raw_path if raw_path.is_absolute() else ref_dir / raw_path]
    if not raw_path.is_absolute() and raw_path.parent == Path(".") and sibling_dir is not None:
        candidates.append(sibling_dir / raw_path.name)

    ref_root = ref_dir.resolve()
    for candidate in candidates:
        resolved = candidate.resolve()
        if (
            resolved.is_relative_to(ref_root)
            and resolved.is_file()
            and resolved.suffix.lower() in _REFERENCE_MEDIA_EXTENSIONS
            and resolved.stat().st_size > 0
        ):
            return resolved

    if not raw_path.is_absolute() and raw_path.parent == Path("."):
        basename_matches = [
            candidate.resolve()
            for candidate in ref_dir.rglob(raw_path.name)
            if candidate.is_file()
            and candidate.suffix.lower() in _REFERENCE_MEDIA_EXTENSIONS
            and candidate.stat().st_size > 0
        ]
        if len(basename_matches) == 1:
            return basename_matches[0]
    return None


def _reference_media_is_decodable(path: Path) -> tuple[bool, str]:
    if path.suffix.lower() in _REFERENCE_IMAGE_EXTENSIONS:
        try:
            from PIL import Image

            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
        except ImportError as exc:
            return False, f"image decode unavailable: Pillow is not installed: {exc}"
        except (OSError, ValueError) as exc:
            return False, f"image decode failed: {exc}"
        return True, ""

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False, "video decode unavailable: ffmpeg is not installed"
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"video decode failed: {exc}"
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        return False, ("video decode failed" + (f": {detail[-1]}" if detail else ""))
    return True, ""


def _check_transition_reference_evidence(
    self: Gate,
    transition: dict[str, Any],
    index: int,
) -> CheckResult | None:
    value = transition.get("reference_frames")
    tokens = _reference_media_tokens(value)
    if not tokens:
        return CheckResult(
            f"transitions[{index}] reference frame evidence",
            "fail",
            f"transitions[{index}].reference_frames must name at least one "
            "existing local image/video file; placeholders such as `none` and "
            "empty values do not prove the declared transition.",
        )

    missing: list[str] = []
    undecodable: list[str] = []
    sibling_dir: Path | None = None
    for token in tokens:
        resolved = _resolve_reference_media(self.ref_dir, token, sibling_dir)
        if resolved is None:
            missing.append(token)
        else:
            sibling_dir = resolved.parent
            decodable, reason = _reference_media_is_decodable(resolved)
            if not decodable:
                undecodable.append(f"{token} ({reason})")
    if missing:
        return CheckResult(
            f"transitions[{index}] reference frame evidence",
            "fail",
            f"transitions[{index}].reference_frames must name existing local "
            f"image/video evidence under the reference directory; missing: {missing}",
        )
    if undecodable:
        return CheckResult(
            f"transitions[{index}] reference frame evidence",
            "fail",
            f"transitions[{index}].reference_frames must name decodable local "
            f"image/video evidence; invalid: {undecodable}",
        )
    return None


def _non_empty(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, list | tuple | set | dict | str):
        return len(value) > 0
    if isinstance(value, int | float):
        return value > 0
    return True


def _stochastic_transition_paths(value: Any, prefix: str = "transition") -> list[str]:
    """Return transition evidence that makes fresh-load pixels non-deterministic."""
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}"
            if "random" in str(key).lower() and _non_empty(child):
                paths.append(path)
            paths.extend(_stochastic_transition_paths(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_stochastic_transition_paths(child, f"{prefix}[{index}]"))
    elif isinstance(value, str) and re.search(
        r"(?:Math\.)?random\s*\(|utils\.random\s*\(", value, re.IGNORECASE
    ):
        paths.append(prefix)
    return list(dict.fromkeys(paths))


def _runtime_motion_signals(dump: dict[str, Any]) -> list[str]:
    signals: list[str] = []

    scroll_trigger = dump.get("scrollTrigger") or dump.get("scrollTriggers")
    if isinstance(scroll_trigger, list) and scroll_trigger:
        signals.append(f"ScrollTrigger[{len(scroll_trigger)}]")
    elif isinstance(scroll_trigger, dict) and _non_empty(scroll_trigger):
        signals.append("ScrollTrigger")

    gsap = dump.get("gsap")
    if isinstance(gsap, dict) and _non_empty(gsap):
        signals.append("GSAP")
    elif isinstance(gsap, list) and gsap:
        signals.append(f"GSAP[{len(gsap)}]")

    for key in ("framer", "framerMotion", "motion"):
        val = dump.get(key)
        if _non_empty(val):
            signals.append("Framer")
            break

    ix2 = dump.get("ix2") or dump.get("webflowIx2") or dump.get("webflow")
    if isinstance(ix2, dict):
        timeline_count = ix2.get("timelineCount") or ix2.get("timelinesCount") or 0
        event_count = ix2.get("eventCount") or ix2.get("eventsCount") or 0
        timelines = ix2.get("timelines") or ix2.get("timelineKeys")
        if _non_empty(timeline_count) or _non_empty(event_count) or _non_empty(timelines):
            signals.append("Webflow IX2")
    elif _non_empty(ix2):
        signals.append("Webflow IX2")

    return signals


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _observed_normalized_positions(scroll_audit: Any) -> list[float]:
    if not isinstance(scroll_audit, dict):
        return []
    samples = scroll_audit.get("samples")
    observed: list[Any] = []
    if isinstance(samples, list):
        observed = [sample.get("observed") for sample in samples if isinstance(sample, dict)]
    elif isinstance(samples, dict):
        value = samples.get("observed")
        if isinstance(value, list):
            observed = value
    else:
        value = scroll_audit.get("observed")
        if isinstance(value, list):
            observed = value

    positions: list[float] = []
    for sample in observed:
        value = sample
        if isinstance(sample, dict):
            for key in ("normalized", "normalizedY", "progress", "scrollProgress", "yNormalized"):
                if key in sample:
                    value = sample[key]
                    break
        number = _finite_number(value)
        if number is not None:
            positions.append(number)
    return positions


def _has_meaningful_scroll_movement(scroll_audit: Any) -> bool:
    positions = _observed_normalized_positions(scroll_audit)
    distinct: list[float] = []
    tolerance = 0.001
    for position in positions:
        if not any(abs(position - existing) <= tolerance for existing in distinct):
            distinct.append(position)
    return len(distinct) >= 3


def _format_capture_error(capture_error: Any) -> str:
    if isinstance(capture_error, dict):
        parts = [
            f"{key}={value}"
            for key, value in capture_error.items()
            if value is not None and str(value).strip()
        ]
        return ", ".join(parts) if parts else "{}"
    return str(capture_error or "").strip() or "unknown"


def _check_runtime_capture_integrity(self: Gate) -> CheckResult | None:
    dump = self._load_json("animation-runtime-dump.json")
    if not isinstance(dump, dict):
        return None

    from ui_clone.gates.state_coverage import _is_motion_rich_ref

    is_motion_rich = _is_motion_rich_ref(self.ref_dir)
    note = str(dump.get("note") or "").strip()
    if is_motion_rich and note.lower() == "eval returned empty":
        return CheckResult(
            "runtime capture integrity",
            "fail",
            "animation-runtime-dump.json carries legacy runtime capture failure "
            "`note: eval returned empty` on a motion-rich reference. Re-run "
            "runtime extraction before drafting transition-spec.json.",
        )

    capture_status = dump.get("captureStatus")
    if capture_status != "ok":
        if is_motion_rich and capture_status == "error":
            detail = _format_capture_error(dump.get("captureError"))
            return CheckResult(
                "runtime capture integrity",
                "fail",
                "animation-runtime-dump.json has `captureStatus: error` on a "
                f"motion-rich reference; captureError: {detail}. Runtime evidence "
                "must be captured successfully before the spec gate can trust "
                "transition coverage.",
            )
        return None

    scroll_audit = dump.get("scrollAudit")
    max_scroll = scroll_audit.get("maxScroll") if isinstance(scroll_audit, dict) else None
    max_scroll_value = 0.0
    if isinstance(scroll_audit, dict) and "maxScroll" in scroll_audit:
        finite_max_scroll = _finite_number(max_scroll)
        if finite_max_scroll is None:
            return CheckResult(
                "runtime capture integrity",
                "fail",
                "animation-runtime-dump.json has `captureStatus: ok` but "
                f"scrollAudit.maxScroll is not a finite numeric value: {max_scroll!r}. "
                "Re-run runtime capture so scroll extent is measured honestly.",
            )
        max_scroll_value = finite_max_scroll
    if is_motion_rich and not isinstance(scroll_audit, dict):
        return CheckResult(
            "runtime capture integrity",
            "fail",
            "animation-runtime-dump.json has `captureStatus: ok` on a "
            "motion-rich reference but no scrollAudit. Re-run runtime capture "
            "so scroll movement is measured before transition-spec coverage "
            "is trusted.",
        )
    if max_scroll_value <= 0:
        return None
    if _has_meaningful_scroll_movement(scroll_audit):
        return None
    observed = _observed_normalized_positions(scroll_audit)
    return CheckResult(
        "runtime capture integrity",
        "fail",
        "animation-runtime-dump.json has `captureStatus: ok` and scrollAudit.maxScroll "
        f"{max_scroll_value:g}, but fewer than three "
        f"distinct observed normalized positions were recorded: {observed}. "
        "Re-run runtime capture so scroll-linked evidence is measured rather "
        "than inferred.",
    )


def _runtime_scroll_sites(dump: dict[str, Any]) -> list[dict[str, str]]:
    rows = dump.get("scrollLinkedStyles")
    if not isinstance(rows, list):
        return []
    sites: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("sourceId") or "").strip()
        selector = str(row.get("selector") or "").strip()
        if not source_id or not selector:
            continue
        key = (source_id, selector)
        if key in seen:
            continue
        seen.add(key)
        sites.append({"sourceId": source_id, "selector": selector})
    return sites


def _entry_source_matches(entry: Any, source_id: str) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("sourceArtifact") == "animation-runtime-dump.json"
        and entry.get("sourceId") == source_id
    )


def _entry_selector(entry: dict[str, Any]) -> str:
    for key in ("target", "selector"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _runtime_site_covered_by_transition(
    site: dict[str, str],
    transitions: list[Any],
    selector_counts: dict[str, int],
) -> bool:
    source_id = site["sourceId"]
    selector = site["selector"]
    if any(_entry_source_matches(entry, source_id) for entry in transitions):
        return True
    if selector_counts.get(selector) != 1:
        return False
    matches = [
        entry
        for entry in transitions
        if isinstance(entry, dict) and _entry_selector(entry) == selector
    ]
    return len(matches) == 1


def _runtime_site_skipped(site: dict[str, str], skipped: list[Any]) -> bool:
    source_id = site["sourceId"]
    return any(
        _entry_source_matches(entry, source_id)
        and isinstance(entry, dict)
        and str(entry.get("reason") or "").strip()
        for entry in skipped
    )


def _check_runtime_site_spec_coverage(
    self: Gate,
    spec: dict[str, Any] | None,
) -> CheckResult | None:
    dump = self._load_json("animation-runtime-dump.json")
    if not isinstance(dump, dict) or dump.get("captureStatus") != "ok":
        return None
    sites = _runtime_scroll_sites(dump)
    if not sites:
        return None

    transitions = spec.get("transitions") if isinstance(spec, dict) else None
    transitions = transitions if isinstance(transitions, list) else []
    skipped = spec.get("skipped") if isinstance(spec, dict) else None
    skipped = skipped if isinstance(skipped, list) else []
    selector_counts: dict[str, int] = {}
    for site in sites:
        selector_counts[site["selector"]] = selector_counts.get(site["selector"], 0) + 1

    uncovered = [
        site
        for site in sites
        if not _runtime_site_covered_by_transition(site, transitions, selector_counts)
        and not _runtime_site_skipped(site, skipped)
    ]
    if not uncovered:
        return None
    details = ", ".join(
        f"{site['sourceId']} ({site['selector']})" for site in uncovered[:8]
    )
    return CheckResult(
        "spec-runtime-site-coverage",
        "fail",
        "transition-spec.json does not cover runtime scroll-linked sites from "
        f"animation-runtime-dump.json: {details}. Each scrollLinkedStyles row "
        "with sourceId and selector needs a transition with "
        "`sourceArtifact: animation-runtime-dump.json` plus exact sourceId, or "
        "a structured skipped[] entry with the same sourceArtifact/sourceId and "
        "a nonempty reason. Exact selector fallback is accepted only when a "
        "single runtime site and a single transition use that selector.",
    )


def _bundle_scroll_sites(extraction: dict[str, Any]) -> list[dict[str, str]]:
    """Scroll-linked motion construction sites from bundle-extraction.json.

    Only scroll-linked rows are required to appear in the spec. A bundle also
    carries the vendored library's own internal tweens, and demanding a spec
    entry for those would force noise rather than coverage.
    """
    extractions = extraction.get("extractions")
    if not isinstance(extractions, dict):
        return []
    sites: list[dict[str, str]] = []
    seen: set[str] = set()

    def collect(rows: Any) -> None:
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict) or not row.get("scrollLinked"):
                continue
            source_id = str(row.get("sourceId") or "").strip()
            if not source_id or source_id in seen:
                continue
            seen.add(source_id)
            kind = row.get("kind") or row.get("eventType") or ""
            sites.append({"sourceId": source_id, "kind": str(kind)})

    for value in extractions.values():
        if isinstance(value, list):
            collect(value)
        elif isinstance(value, dict):
            # Webflow IX2 reports a summary object whose rows live under keys
            # such as `events`, so a list-only scan would drop them silently.
            for nested in value.values():
                collect(nested)
    return sites


def _bundle_site_cited(entry: Any, source_id: str) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("sourceArtifact") == "bundle-extraction.json"
        and entry.get("sourceId") == source_id
    )


def _check_bundle_scroll_site_coverage(
    self: Gate, spec: dict[str, Any] | None
) -> CheckResult | None:
    """Every scroll-linked bundle construction site must reach the spec.

    Signal-class coverage alone lets one entry that name-checks "scroll" certify
    a site whose bundles declare many separate scroll-driven timelines, which is
    exactly the under-populated spec this gate exists to reject.
    """
    extraction = self._load_json("bundle-extraction.json")
    if not isinstance(extraction, dict):
        return None
    sites = _bundle_scroll_sites(extraction)
    if not sites:
        return None

    transitions = spec.get("transitions") if isinstance(spec, dict) else None
    transitions = transitions if isinstance(transitions, list) else []
    transitions = [t for t in transitions if not _is_stub_entry(t)]
    skipped = spec.get("skipped") if isinstance(spec, dict) else None
    skipped = skipped if isinstance(skipped, list) else []

    uncovered = [
        site
        for site in sites
        if not any(_bundle_site_cited(t, site["sourceId"]) for t in transitions)
        and not any(
            _bundle_site_cited(entry, site["sourceId"])
            and str(entry.get("reason", "")).strip()
            for entry in skipped
        )
    ]
    if not uncovered:
        return None
    details = ", ".join(f"{site['sourceId']} ({site['kind']})" for site in uncovered[:8])
    more = "" if len(uncovered) <= 8 else f" (+{len(uncovered) - 8} more)"
    return CheckResult(
        "spec-bundle-site-coverage",
        "fail",
        f"transition-spec.json does not cover {len(uncovered)} of {len(sites)} "
        f"scroll-linked construction site(s) in bundle-extraction.json: "
        f"{details}{more}. Each needs a transitions[] entry with "
        "`sourceArtifact: bundle-extraction.json` plus the exact sourceId, or a "
        "structured skipped[] entry with the same sourceArtifact/sourceId and a "
        "nonempty reason (use that for a vendored library's own internals or a "
        "site already covered by another entry). Mine the parameters from "
        "bundle-extraction.json rather than inventing them.",
    )


def _check_runtime_motion_spec_coverage(self: Gate) -> CheckResult | None:
    dump = self._load_json("animation-runtime-dump.json")
    if not dump:
        return None
    signals = _runtime_motion_signals(dump)
    if not signals:
        return None

    spec = self._load_json("transition-spec.json")
    transitions = spec.get("transitions") if isinstance(spec, dict) else None
    if isinstance(transitions, list) and transitions:
        return None

    if spec is None:
        spec_state = "missing"
    elif isinstance(transitions, list):
        spec_state = "empty"
    else:
        spec_state = "missing valid `transitions` list"

    return CheckResult(
        "runtime motion transition-spec coverage",
        "fail",
        "animation-runtime-dump.json shows runtime motion "
        f"({', '.join(signals)}) but transition-spec.json is {spec_state}. "
        "Re-run Step 5d from bundle-analysis.md and transition-spec-rules.md, "
        "using animation-runtime-dump.json as the evidence source for runtime "
        "motion that bundle grep missed.",
        fix="bash $PLUGIN_ROOT/scripts/extract/extract-animation-runtime.sh "
        "<session> <ref-dir> && python -m ui_clone.gate <ref-dir> spec",
    )


def _spec_is_placeholder(spec: dict[str, Any] | None) -> bool:
    """True for the Phase-2 auto-minted floor spec (never agent-drafted).

    finalize_full_extraction_artifacts mints a gate-shaped spec BEFORE the
    agent reaches Step 5d. On a motion-rich site that placeholder must never
    satisfy this gate — it neutralized every downstream motion check on a
    12-motion reference (live forensics, realfood-e2e-1)."""
    if not isinstance(spec, dict):
        return False
    return bool(spec.get("placeholder")) or spec.get("source") == "ui_clone.extraction_artifacts"


def _is_stub_entry(t: Any) -> bool:
    """Content-based stub detection (Codex review: metadata-only placeholder
    detection is bypassable by editing `source`/`placeholder` while keeping
    the auto-minted stub transitions). A stub is recognized by its SHAPE:
    the finalizer's boilerplate bundle_branch, an unresolved mechanism, or
    the auto-<trigger>-<n> id pattern with no measured animation params."""
    if not isinstance(t, dict):
        return True
    anim = t.get("animation")
    anim_text = (
        " ".join(str(v) for v in anim.values()).lower()
        if isinstance(anim, dict)
        else str(anim or "").lower()
    )
    if "unresolved" in anim_text:
        return True
    import re as _re

    auto_id = bool(_re.match(r"^auto-[a-z-]+-\d+$", str(t.get("id", ""))))
    boilerplate_branch = str(t.get("bundle_branch", "")) == "settled branch observed during capture"
    return auto_id and boilerplate_branch


def _check_spec_inventory_coverage(self: Gate, spec: dict[str, Any] | None) -> list[CheckResult]:
    """Deterministic detection→spec cross-count (pure JSON, no browser).

    The only numeric floor before this was `len(transitions) > 0`, so a
    1-entry spec certified a site whose own verification-plan signals said
    scrub+state-machine+hover. Every true motion signal class must map to
    at least one matching spec entry."""
    results: list[CheckResult] = []
    plan = self._load_json("verification-plan.json")
    signals = plan.get("signals") if isinstance(plan, dict) else {}
    signals = signals if isinstance(signals, dict) else {}
    transitions = spec.get("transitions") if isinstance(spec, dict) else None
    transitions = transitions if isinstance(transitions, list) else []
    # Stub-shaped entries never count as coverage — regardless of the spec's
    # placeholder/source metadata (which an agent could edit away).
    transitions = [t for t in transitions if not _is_stub_entry(t)]
    skipped = spec.get("skipped") if isinstance(spec, dict) else None
    skipped = skipped if isinstance(skipped, list) else []
    # Only STRUCTURED skips count: {sourceArtifact/sourceId, reason}. A bare
    # string like "scroll" must not satisfy inventory coverage.
    skipped = [
        s
        for s in skipped
        if isinstance(s, dict)
        and str(s.get("reason", "")).strip()
        and (s.get("sourceId") or s.get("sourceArtifact") or s.get("id"))
    ]

    def _blob(t: Any) -> str:
        if not isinstance(t, dict):
            return ""
        anim = t.get("animation")
        anim_text = (
            " ".join(str(v) for v in anim.values()) if isinstance(anim, dict) else str(anim or "")
        )
        return f"{t.get('trigger', '')} {t.get('type', '')} {anim_text}".lower()

    import re as _re

    requirement_rows = (
        ("hasScrollScrub", r"scroll|scrub", "scroll-scrub"),
        ("hasScrollStateMachine", r"state", "scroll state machine"),
        ("hasIOReveal", r"reveal|intersection|inview|viewport|io-", "IO reveal"),
        ("hasHover", r"hover", "hover"),
        ("hasClickStateTransition", r"click|toggle|accordion|tab|modal", "click state"),
    )
    missing: list[str] = []
    for key, pattern, label in requirement_rows:
        if not signals.get(key):
            continue
        rx = _re.compile(pattern)
        covered = any(rx.search(_blob(t)) for t in transitions) or any(
            rx.search(str(s).lower()) for s in skipped
        )
        if not covered:
            missing.append(
                f"{label} (signals.{key}=true, no matching transitions[] or skipped[] entry)"
            )
    if missing:
        results.append(
            CheckResult(
                "spec-inventory-coverage",
                "fail",
                "transition-spec.json does not cover the site's detected motion "
                "inventory: " + "; ".join(missing) + ". Every true verification-plan "
                "signal class needs >=1 spec entry whose trigger/animation matches "
                "it, or an explicit skipped[] entry with a reason. Re-run Step 5d "
                "(transition-spec-rules.md) mining bundle-extraction.json and "
                "animation-init-styles.json. For IO reveal signals, also inspect "
                "structure.json plus captured CSS for boolean data-attribute states. "
                "The verification-plan signal is a dispatch hint, not transition "
                "proof: add a transition only with captured frame/source evidence, "
                "otherwise record a structured skipped[] reason.",
            )
        )

    invalid_targets = [
        str(t.get("id", f"#{i}"))
        for i, t in enumerate(transitions)
        if isinstance(t, dict) and not _is_valid_selector_for_spec(t.get("target"))
    ]
    if invalid_targets:
        results.append(
            CheckResult(
                "spec target selectors parse",
                "fail",
                "transition-spec.json entries have targets that are not CSS "
                f"selectors (declaration fragments?): {', '.join(invalid_targets[:6])}. "
                "Each target must be a querySelector-able selector.",
            )
        )
    return results


def _is_valid_selector_for_spec(selector: Any) -> bool:
    from ui_clone.extraction_artifacts import _is_valid_selector

    return _is_valid_selector(selector)


def _collect_dom_tokens(node: Any, classes: set[str], ids: set[str], tags: set[str]) -> None:
    """Walk structure.json collecting class/id/tag tokens actually present in
    the captured homepage DOM. Only class/id/tag are reliably captured (the
    extractor's ATTR_KEYS is a fixed subset, so arbitrary data-* attrs are NOT
    in structure.json — callers must not validate attribute selectors here)."""
    if not isinstance(node, dict):
        return
    tag = node.get("tag")
    if isinstance(tag, str) and tag:
        tags.add(tag.lower())
    cls = node.get("class")
    if isinstance(cls, str):
        for tok in cls.split():
            if tok:
                classes.add(tok)
    nid = node.get("id")
    if isinstance(nid, str) and nid:
        ids.add(nid)
    for child in node.get("children") or []:
        _collect_dom_tokens(child, classes, ids, tags)


def _token_present(token: str, captured: set[str]) -> bool:
    """True when a spec class/id token resolves against a captured token.
    Exact match, plus the CSS-modules `[name]__[hash]` convention (double
    underscore) so `.text_line` matches captured `text_line__MVXuV`.
    Single `-`/`_` suffixes are deliberately NOT matched — they collide with
    BEM modifiers (`.btn` vs `.btn-primary`) and would hide real absences."""
    return any(c == token or c.startswith(token + "__") for c in captured)


def _check_spec_selectors_present_in_dom(
    self: Gate, spec: dict[str, Any] | None
) -> list[CheckResult]:
    """Draft-time guard: a transition-spec target whose CLASS/ID tokens are
    absent from the captured homepage DOM is almost always a bundle-derived
    selector that targets a SUBPAGE (mined from minified JS), not this capture.
    Such a target survives the syntax check, then fails far downstream at
    transition-fires ("element not found") after a full generate. structure.json
    is already on disk at spec time, so we surface it here for ~0 cost.

    Conservative by construction: only class/id identifiers are checked (they are
    reliably captured); tag-only and attribute-only targets are skipped; and
    runtime-injected selectors (swiper/lottie/canvas) are exempt because they are
    created after the static capture. Any target that survives those exemptions
    and is still absent BLOCKS: at spec time the fix is cheap (re-capture the
    revealing state, or move the entry to skipped[] with a reason), whereas the
    same absence otherwise surfaces only after a full generate as a
    transition-fires 'element not found' — after the iteration budget is spent."""
    import re as _re

    if not isinstance(spec, dict):
        return []
    structure = self._load_json("structure.json")
    if not isinstance(structure, dict):
        return []  # no captured DOM to validate against — never block
    classes: set[str] = set()
    ids: set[str] = set()
    tags: set[str] = set()
    _collect_dom_tokens(structure, classes, ids, tags)
    if not classes and not ids:
        return []  # degenerate/empty capture — never block

    # First char must be a letter/_/-/escape (rejects `.5`); subsequent chars may
    # include CSS escapes (`\:`, `\/`) so Tailwind-style `.md\:block` / `.w-1\/2`
    # extract their full token, which is then unescaped to match the DOM's
    # unescaped class attribute value (`md:block`).
    class_re = _re.compile(r"\.((?:\\.|[A-Za-z_-])(?:\\.|[A-Za-z0-9_-])*)")
    id_re = _re.compile(r"#((?:\\.|[A-Za-z_-])(?:\\.|[A-Za-z0-9_-])*)")

    def _toks(rx: _re.Pattern[str], group: str) -> list[str]:
        return [m.replace("\\", "") for m in rx.findall(group)]

    # Strip attribute selectors ([data-ratio=".5"]) and pseudo-classes/elements
    # (:hover, ::before(...)) BEFORE extracting class/id tokens — otherwise
    # class-like text inside an attribute value (".5") is mis-read as a required
    # `.5` token. Leading-letter anchoring on class_re/id_re also rejects `.5`.
    # The pseudo branch uses a negative lookbehind so an ESCAPED colon inside a
    # class (Tailwind `.md\:flex`) is not mistaken for a `:flex` pseudo-class.
    noise_re = _re.compile(r"\[[^\]]*\]|(?<!\\)::?[A-Za-z][A-Za-z0-9-]*(?:\([^)]*\))?")
    # Selectors materialized at runtime by libraries/controllers are legitimately
    # absent from the static structure.json capture — never flag them. Anchored
    # with \b so a substring inside an unrelated class (e.g. `.calenis`) is not
    # falsely exempted.
    runtime_re = _re.compile(
        r"\b(?:swiper|splide|slick|flickity|embla|keen-slider|glide"
        r"|lottie|bodymovin|canvas"
        r"|lenis|locomotive|data-scroll|data-lottie|data-pseudo|data-lenis|data-smooth)"
        r"(?![A-Za-z0-9])",  # trailing boundary: `.swiperless`/`.lenislike` are NOT exempted
        _re.IGNORECASE,
    )

    def _selector_present(cleaned: str) -> bool:
        """A CSS selector list matches when ANY comma-group matches; a group
        matches when ALL its class/id tokens are present (so a compound or
        descendant selector whose target leaf is absent is correctly flagged,
        not waved through because some ancestor token happens to exist).
        Expects attribute/pseudo noise already stripped."""
        for group in cleaned.split(","):
            group = group.strip()
            if not group:
                continue
            g_classes = _toks(class_re, group)
            g_ids = _toks(id_re, group)
            if not g_classes and not g_ids:
                # tag-only / attribute-only group: not reliably checkable against
                # structure.json — treat as present so the whole target is not flagged.
                return True
            if all(_token_present(c, classes) for c in g_classes) and all(
                _token_present(d, ids) for d in g_ids
            ):
                return True
        return False

    transitions = spec.get("transitions")
    transitions = transitions if isinstance(transitions, list) else []
    absent: list[tuple[str, str]] = []
    for i, t in enumerate(transitions):
        if not isinstance(t, dict) or _is_stub_entry(t):
            continue
        target = t.get("target")
        if not isinstance(target, str) or not target.strip():
            continue
        if runtime_re.search(target):
            continue
        cleaned = noise_re.sub(" ", target)
        if not class_re.search(cleaned) and not id_re.search(cleaned):
            continue  # tag-only / attr-only — not reliably checkable
        if not _selector_present(cleaned):
            absent.append((str(t.get("id", f"#{i}")), target))

    if not absent:
        return []
    sample = "; ".join(f"{eid} -> {tgt}" for eid, tgt in absent[:6])
    extra = f" (+{len(absent) - 6} more)" if len(absent) > 6 else ""
    return [
        CheckResult(
            "spec-selectors-present-in-dom",
            "fail",
            f"{len(absent)} transition-spec target(s) reference class/id selectors absent "
            f"from the captured homepage DOM (structure.json): {sample}{extra}. Each will "
            "fail downstream at transition-fires ('element not found') after a full generate, "
            "so it must be resolved now, at spec time. For each: (1) if the target is a "
            "same-page node that mounts only after an interaction (hover/tab/scroll), re-capture "
            "the revealing state with stimulation so it lands in the DOM snapshot; (2) otherwise, "
            "if it is a bundle-derived selector that genuinely targets a SUBPAGE, move the entry "
            "to skipped[] with a reason. Do not leave it in transitions[] unresolved. "
            "(swiper/lottie/canvas runtime-injected selectors are already exempt.)",
        )
    ]


def gate_spec(self: Gate) -> list[CheckResult]:
    results = []
    results.append(
        self.check_file(
            self.ref_dir / "bundle-map.json",
            "bundle-map.json (Step 5d input — {} for static sites)",
        )
    )
    results.append(
        self.check_file(
            self.ref_dir / "external-sdks.json",
            "external-sdks.json (GSAP/Lenis/Framer detection — {} for no SDKs)",
        )
    )
    results.append(
        self.check_file(
            self.ref_dir / "transition-spec.json",
            "transition-spec.json (single source of truth)",
        )
    )
    # verification-plan.json declares site-specific required checks
    # (hydration, scroll-end-completion, reveal-trigger, etc.) derived from
    # the signals in extraction artifacts. It must exist by spec time so
    # gate_post_implement can enforce each declared check; otherwise the
    # universal `hydration-check` row is silently skipped.
    plan = self.ref_dir / "verification-plan.json"
    results.append(
        self.check_file(
            plan,
            "verification-plan.json (run skills/visual-debug/scripts/verification-plan.sh)",
        )
    )

    # Validate transition-spec structure
    spec = self._load_json("transition-spec.json")
    runtime_capture_result = _check_runtime_capture_integrity(self)
    if runtime_capture_result is not None:
        results.append(runtime_capture_result)
    runtime_motion_result = _check_runtime_motion_spec_coverage(self)
    if runtime_motion_result is not None:
        results.append(runtime_motion_result)
    if _spec_is_placeholder(spec):
        from ui_clone.gates.state_coverage import _is_motion_rich_ref

        if _is_motion_rich_ref(self.ref_dir):
            results.append(
                CheckResult(
                    "spec drafted by agent (not placeholder)",
                    "fail",
                    "transition-spec.json is the Phase-2 auto-minted placeholder "
                    "(source=ui_clone.extraction_artifacts) but this reference is "
                    "motion-rich (bundle/SDK evidence). The placeholder is a draft "
                    "floor, not a spec — draft the real spec per Step 5d "
                    "(transition-spec-rules.md): every detected interaction, "
                    "scroll transition, and bundle motion construction site must "
                    "map to a transitions[] entry or an explicit skipped[] reason.",
                )
            )
    results.extend(_check_spec_inventory_coverage(self, spec))
    runtime_site_result = _check_runtime_site_spec_coverage(self, spec)
    if runtime_site_result is not None:
        results.append(runtime_site_result)
    bundle_site_result = _check_bundle_scroll_site_coverage(self, spec)
    if bundle_site_result is not None:
        results.append(bundle_site_result)
    results.extend(_check_spec_selectors_present_in_dom(self, spec))
    if spec is not None:
        transitions = spec.get("transitions")
        if not isinstance(transitions, list):
            results.append(
                CheckResult(
                    "transitions list",
                    "fail",
                    "transition-spec.json: `transitions` must be a list (got "
                    f"{type(transitions).__name__}). Re-run Step 5d so the "
                    f"spec captures the observed interactions.",
                )
            )
            transitions = []
        elif len(transitions) == 0:
            results.append(
                CheckResult(
                    "transitions non-empty",
                    "fail",
                    "transition-spec.json: `transitions` is empty. Every site "
                    "the cloner targets has at least page-load / hover / scroll "
                    "/ click handlers — re-run Step 5/6 (animation-detection.md "
                    "Phase A-C) and Step 5d to record them. Empty spec = the "
                    "downstream coverage check has nothing to enforce.",
                )
            )
        required_transition_keys = (
            "id",
            "trigger",
            "source_chunk",
            "bundle_branch",
            "target",
            "animation",
            "reference_frames",
        )
        for index, transition in enumerate(transitions):
            missing_keys = [k for k in required_transition_keys if k not in transition]
            if missing_keys:
                results.append(
                    CheckResult(
                        f"transitions[{index}] keys",
                        "fail",
                        f"transitions[{index}] missing required keys: {missing_keys}",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        f"transitions[{index}] keys",
                        "pass",
                        f"transitions[{index}] has required keys ({len(transitions)} total)",
                    )
                )
                reference_evidence = _check_transition_reference_evidence(self, transition, index)
                if reference_evidence is not None:
                    results.append(reference_evidence)
            stochastic_paths = _stochastic_transition_paths(
                transition if isinstance(transition, dict) else None,
                f"transitions[{index}]",
            )
            if stochastic_paths and transition.get("dynamic") is not True:
                results.append(
                    CheckResult(
                        "stochastic transition dynamic mask",
                        "fail",
                        f"transitions[{index}] contains stochastic animation fields "
                        f"{stochastic_paths} but does not declare top-level "
                        "`dynamic: true`. Fresh reference loads cannot reproduce "
                        "the same random visual state, so pixel gates must mask the "
                        "narrow transition target while runtime/static masked-region "
                        "checks verify its behavior and layout.",
                    )
                )
        source_chunk_grounding = _check_spec_bundle_grounding(self)
        if source_chunk_grounding is not None:
            results.append(source_chunk_grounding)

    # Cross-validate against paid-features decisions: any font marked
    # decision='substitute' at 5c-c MUST be declared in asset-substitution.json
    # by spec time, otherwise font-parity will FAIL after generation.
    results.extend(self._check_paid_font_substitution())

    # Capture verification frames
    verify_frames = (
        sum(1 for f in (self.ref_dir / "verify").rglob("*.png") if f.is_file())
        if (self.ref_dir / "verify").is_dir()
        else 0
    )
    if verify_frames >= 5:
        results.append(
            CheckResult(
                "capture verification",
                "pass",
                f"capture verification frames ({verify_frames} frames in verify/)",
            )
        )
    else:
        results.append(
            CheckResult(
                "capture verification",
                "warn",
                f"capture verification missing ({verify_frames} frames — need \u22655). "
                "See interaction-detection.md 'MANDATORY: Capture Verification'.",
            )
        )

    return results
