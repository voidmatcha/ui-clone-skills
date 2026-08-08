"""Pre-Generate gate — private check helpers.

These helpers live in their own module so the top-level gate_pre_generate
fits under the per-gate ≤500-line budget. They're imported back into
`pre_generate` and rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import CheckResult
from .reference import _has_real_detection_provenance

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401


def _check_webflow(self: Gate) -> list[CheckResult]:
    """Check Webflow IX2 artifacts when site is Webflow."""
    results = []
    webflow = self._load_json("webflow-detection.json")
    if webflow and webflow.get("isWebflow"):
        results.append(
            self.check_file(
                self.ref_dir / "webflow-hide-rule.json",
                "webflow-hide-rule.json (IX2 selector inventory — Step W-2)",
            )
        )
        results.append(
            self.check_file(
                self.ref_dir / "webflow-ix2.json",
                "webflow-ix2.json (IX2 timeline data — Step W-3)",
            )
        )
    return results


def _safe_ref_json(ref_dir: Path, relative: Any) -> dict[str, Any] | None:
    if not isinstance(relative, str) or not relative.strip():
        return None
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    try:
        path = (ref_dir / rel).resolve()
        path.relative_to(ref_dir.resolve())
        if not path.is_file() or path.stat().st_size <= 0:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _is_static_no_signal_evidence(data: dict[str, Any]) -> bool:
    verdict = str(data.get("verdict") or data.get("result") or "").strip().lower()
    observation = str(data.get("observation") or data.get("reason") or "").strip().lower()
    probe_completed = (
        data.get("checked") is True
        or data.get("probeCompleted") is True
        or data.get("runtimeScanned") is True
    )
    explicit_static = (
        data.get("static") is True
        or data.get("hasMotion") is False
        or data.get("motionDetected") is False
        or verdict in {"static", "no-signal", "no_signal", "no motion", "no-motion"}
        or observation
        in {"no-signal", "no_signal", "no motion", "no-motion", "static"}
    )
    return probe_completed and explicit_static


def _positive_motion_evidence(self: Gate) -> bool:
    plan = self._load_json("verification-plan.json")
    signals = plan.get("signals") if isinstance(plan, dict) else None
    if isinstance(signals, dict) and any(
        signals.get(key)
        for key in ("hasScrollScrub", "hasScrollStateMachine", "hasIOReveal", "hasHover")
    ):
        return True

    interactions = self._load_json("interactions-detected.json")
    rows = interactions.get("interactions") if isinstance(interactions, dict) else None
    if isinstance(rows, list) and any(isinstance(row, dict) for row in rows):
        return True

    hover = self._load_json("hover-css-rules.json")
    rules = hover.get("rules") if isinstance(hover, dict) else hover
    if isinstance(rules, list) and any(isinstance(rule, dict) for rule in rules):
        return True

    runtime = self._load_json("animation-runtime-dump.json")
    if isinstance(runtime, dict):
        if runtime.get("scrollLinkedStyles") or runtime.get("animations"):
            return True
        timelines = runtime.get("timelines")
        if isinstance(timelines, list) and timelines:
            return True
    return False


def _has_typed_static_no_motion_classification(self: Gate) -> bool:
    """Accept a future explicit static/no-motion classifier, not placeholders."""
    data = self._load_json("motion-classification.json")
    if not isinstance(data, dict):
        return False
    classification = str(
        data.get("classification") or data.get("motion") or ""
    ).strip().lower()
    source = str(data.get("source") or "").strip().lower()
    evidence = data.get("evidence")
    if _positive_motion_evidence(self):
        return False
    evidence_items = evidence if isinstance(evidence, list) else []
    evidence_payloads = [_safe_ref_json(self.ref_dir, item) for item in evidence_items]
    return (
        data.get("status") == "pass"
        and classification in {"static", "no-motion", "no_motion"}
        and source in {"browser-capture", "agent-browser", "runtime-probe"}
        and bool(evidence_payloads)
        and all(
            payload is not None and _is_static_no_signal_evidence(payload)
            for payload in evidence_payloads
        )
    )


def _check_regions_generation_readiness(self: Gate) -> list[CheckResult]:
    """Block generation unless regions are real or explicitly static-classified."""
    regions = self._load_json("regions.json")
    if not isinstance(regions, dict):
        return []
    is_placeholder = bool(regions.get("placeholder")) or (
        regions.get("detectionRan") is False
    )
    if is_placeholder:
        if _has_typed_static_no_motion_classification(self):
            return [
                CheckResult(
                    "regions.json generation readiness",
                    "pass",
                    "regions.json is placeholder-only, but a typed static/no-motion "
                    "classification exists; generation may proceed without motion regions.",
                )
            ]
        return [
            CheckResult(
                "regions.json generation readiness",
                "fail",
                "regions.json is an auto placeholder (placeholder=true or "
                "detectionRan=false). This is enough for provisional reference "
                "capture, but not enough to generate a transition-faithful clone. "
                "A typed static/no-motion classification is required and must not "
                "be missing, forged, or contradicted by positive motion evidence.",
                fix="Run Phase 2 transition detection / capture-region-artifacts so "
                "regions.json is browser-measured or transition-spec-derived. For a "
                "genuinely static page, write a typed motion-classification.json "
                "from browser/runtime evidence.",
            )
        ]

    entries = regions.get("regions")
    entries = entries if isinstance(entries, list) else []
    has_trigger_types = any(
        isinstance(region, dict) and region.get("triggerType") for region in entries
    )
    if has_trigger_types and not _has_real_detection_provenance(self, regions):
        return [
            CheckResult(
                "regions.json generation readiness",
                "fail",
                "regions.json claims real transition regions, but its "
                "transition-spec derivation or live-capture evidence is missing, "
                "failed, partial, or does not match the declared region artifacts.",
                fix="Re-derive regions.json from transition-spec.json + section-map.json, "
                "or rerun capture-region-artifacts.py until "
                "capture-region-artifacts-summary.json has status=pass and every "
                "active region is captured.",
            )
        ]
    return []


def _check_hover_timing(self: Gate, interactions_data: dict[str, Any]
) -> tuple[list[CheckResult], bool]:
    """Check hover interaction timing and preloader. Returns (results, has_hover)."""
    results = []
    has_hover = any(
        i.get("trigger") == "hover" for i in interactions_data.get("interactions", [])
    )
    unknown_timing = [
        i
        for i in interactions_data.get("interactions", [])
        if i.get("timingSource") == "unknown"
    ]
    if unknown_timing:
        results.append(
            CheckResult(
                "hover timing",
                "fail",
                f"{len(unknown_timing)} hover interactions have timingSource='unknown' "
                "— bundle analysis must resolve",
            )
        )
    else:
        results.append(
            CheckResult("hover timing", "pass", "All hover interactions have known timing")
        )

    if interactions_data.get("hasPreloader"):
        results.append(
            self.check_file(
                self.ref_dir / "dom-state-diff.json",
                "dom-state-diff.json (REQUIRED: site has preloader — dual-snapshot needed)",
            )
        )
    return results, has_hover


def _check_transition_coverage(self: Gate, spec: dict[str, Any] | None) -> list[CheckResult]:
    """Check transition-coverage.json completeness."""
    results = []
    results.append(
        self.check_file(
            self.ref_dir / "transition-coverage.json",
            "transition-coverage.json (Step 6d multi-position scroll measurement)",
        )
    )
    cov = self._load_json("transition-coverage.json")
    if cov is not None:
        animated_count = len(cov.get("animatedElements", []))
        is_static = spec is not None and len(spec.get("transitions", [])) == 0
        if animated_count > 0:
            results.append(
                CheckResult(
                    "transition-coverage animated",
                    "pass",
                    f"transition-coverage: {animated_count} animated elements",
                )
            )
        elif is_static:
            results.append(
                CheckResult(
                    "transition-coverage animated",
                    "pass",
                    "transition-coverage: 0 animated elements (static site)",
                )
            )
        else:
            results.append(
                CheckResult(
                    "transition-coverage animated",
                    "fail",
                    "transition-coverage.json animatedElements is empty — audit incomplete",
                )
            )
    return results


def _check_section_counts(self: Gate, section_map: dict[str, Any], component_map: dict[str, Any]
) -> list[CheckResult]:
    """Cross-check section counts between section-map and component-map."""
    results = []
    sc = section_map.get("totalCount", len(section_map.get("sections", [])))
    cc = component_map.get("sectionCount", len(component_map.get("sections", [])))
    if sc is not None and cc is not None and sc != cc:
        results.append(
            CheckResult(
                "section count",
                "warn",
                f"Section count: section-map={sc}, component-map={cc} (advisory — "
                "OK if sections were intentionally merged/omitted)",
            )
        )
    elif sc is not None and cc is not None:
        results.append(
            CheckResult("section count", "pass", f"Section count matches ({sc} sections)")
        )

    if section_map.get("hasFooter"):
        comp_sections = component_map.get("sections", [])
        has_footer_in_map = any(
            "footer" in s.get("sourceTag", "").lower()
            or "footer" in s.get("componentName", "").lower()
            or "footer" in s.get("sourceClass", "").lower()
            for s in comp_sections
        )
        if not has_footer_in_map:
            results.append(
                CheckResult(
                    "footer in component-map",
                    "fail",
                    "section-map.json has a <footer> but component-map.json does not include it. "
                    "Add a Footer component before generating code.",
                )
            )
    return results


def _check_audit_artifacts(self: Gate) -> list[CheckResult]:
    """Check that all 6c audit JSON artifacts are present AND that
    their content cross-references the section-map (artifact cross-reference review:
    agent had been satisfying gates by writing canonical filenames
    with low-content or fabricated bodies — e.g. interactions-detected
    with 0 entries while the ref clearly has FAQ accordions + hover
    + scroll reveals; component-map sectionIds invented to satisfy
    filename presence). Cross-validation refuses both fabrication
    modes.
    """
    results: list[CheckResult] = []
    if not (self.ref_dir / "section-map.json").exists():
        return results
    for filename, label in [
        ("element-roles.json", "element-roles.json"),
        ("element-groups.json", "element-groups.json"),
        ("layout-decisions.json", "layout-decisions.json"),
        ("component-map.json", "component-map.json"),
    ]:
        results.append(self.check_file(self.ref_dir / filename, label))

    # Cross-reference checks: sectionIds in audit artifacts must
    # appear in section-map; component count must roughly match
    # section count.
    section_map = self._load_json("section-map.json")
    if not section_map:
        return results
    sec_ids: set[str] = set()
    for index, section in enumerate(section_map.get("sections", [])):
        if not isinstance(section, dict):
            continue
        raw = section.get("id") or section.get("sectionId")
        if isinstance(raw, str) and raw.strip():
            sec_ids.add(raw.strip())
            continue
        tag = str(section.get("tag") or "section").lower()
        cls = re.sub(
            r"[^a-zA-Z0-9]+",
            "-",
            str(section.get("className") or "").strip(),
        ).strip("-")
        sec_ids.add(f"section-{index}-{cls or tag}")
    if not sec_ids:
        return results

    def _cross_check(filename: str, list_key: str, id_field: str = "sectionId") -> None:
        data = self._load_json(filename)
        if not data:
            return
        entries = data.get(list_key, [])
        if not isinstance(entries, list):
            return
        fabricated = [
            str(e.get(id_field, ""))
            for e in entries
            if isinstance(e, dict)
            and e.get(id_field)
            and str(e.get(id_field)) not in sec_ids
        ]
        if fabricated:
            results.append(
                CheckResult(
                    f"{filename} sectionId cross-ref",
                    "warn",
                    f"{filename} references sectionIds not in section-map.json: "
                    f"{sorted(set(fabricated))[:5]}. Either fix the IDs or extend "
                    f"section-map.json so the audit and the map agree.",
                )
            )

    _cross_check("component-map.json", "components")
    _cross_check("layout-decisions.json", "decisions")

    # Component-count parity: |components| should track |sections|.
    component_map = self._load_json("component-map.json")
    if component_map:
        n_components = len(component_map.get("components", []))
        n_sections = len(sec_ids)
        if n_components and n_sections and abs(n_components - n_sections) > 2:
            results.append(
                CheckResult(
                    "component-count parity",
                    "warn",
                    f"component-map has {n_components} components vs section-map's "
                    f"{n_sections} sections — gap of {abs(n_components - n_sections)} "
                    f"exceeds advisory tolerance ±2. Likely a monolith page.tsx "
                    f"(under-count) or fabricated components (over-count).",
                )
            )
    return results


def _check_detection_artifact_integrity(self: Gate) -> list[CheckResult]:
    """Common cheat pattern: sub-agent emptied interactions-detected.json to
    silence the hover/click dispatcher even though hover-css-rules.json
    and regions.json (the upstream signal sources) still indicated
    interactions existed. Cross-check the artifact against sibling
    evidence and fail when the artifact is hand-zeroed while the
    upstream still proves the feature exists.

    Returns no failures when everything agrees (true zero-interaction
    sites still pass). Single failure when interactions-detected.json
    is empty but ≥1 upstream source still shows interaction evidence.
    """
    results: list[CheckResult] = []
    raw = self._load_json("interactions-detected.json")
    # Wrapper shape: {"interactions":[...]} OR bare list.
    interactions: list[Any] = []
    if isinstance(raw, list):
        interactions = raw
    elif isinstance(raw, dict):
        wrapped = raw.get("interactions")
        if isinstance(wrapped, list):
            interactions = wrapped
    if interactions:
        return results  # non-empty — fine
    # Upstream evidence sources.
    upstream_signals: list[str] = []
    hover_rules = self._load_json("hover-css-rules.json")
    if isinstance(hover_rules, list) and hover_rules:
        upstream_signals.append(f"hover-css-rules.json[{len(hover_rules)}]")
    elif isinstance(hover_rules, dict):
        rules = hover_rules.get("rules") or hover_rules.get("entries")
        if isinstance(rules, list) and rules:
            upstream_signals.append(f"hover-css-rules.json.rules[{len(rules)}]")
    # Backward compatibility: regions.json migrated from a bare list to the
    # `{"regions": [...]}` wrapper emitted by _capture_artifacts.write_regions_json.
    # Accept both shapes so older and newer captures both surface hover/click
    # upstream signals.
    regions_raw = self._load_json("regions.json")
    region_list: list = []
    if isinstance(regions_raw, list):
        region_list = regions_raw
    elif isinstance(regions_raw, dict):
        nested = regions_raw.get("regions")
        if isinstance(nested, list):
            region_list = nested
    if region_list:
        hover_click = [
            r for r in region_list
            if isinstance(r, dict)
            and str(r.get("triggerType") or "").startswith(("hover", "click-"))
        ]
        if hover_click:
            upstream_signals.append(
                f"regions.json hover/click triggers[{len(hover_click)}]"
            )
    if not upstream_signals:
        return results  # no upstream evidence — empty artifact is valid
    sample = "; ".join(upstream_signals[:3])
    return [
        CheckResult(
            "interactions-detected.json — hand-emptied",
            "fail",
            f"interactions-detected.json is empty but upstream sources "
            f"({sample}) prove interactions exist. Hand-clearing detection "
            "artifacts to silence dispatchers is a gate-game; the artifact "
            "must reflect the upstream evidence.",
            fix=(
                "Re-run interaction detection (ui-reverse-engineering Step 5b) "
                "to regenerate interactions-detected.json from regions.json + "
                "hover-css-rules.json. Do NOT hand-edit to empty."
            ),
        )
    ]


# Strong scroll-motion tokens. scroll-engine.json can be EMPTY on a GSAP
# ScrollTrigger site — the real evidence then lives in the JS bundles / plan /
# sdk artifacts, so we scan those too (heuristic substring grep, not a parser).
_SCROLL_MOTION_TOKENS = (
    "ScrollTrigger",
    "gsap-scrolltrigger",
    "scrollYProgress",
    "useScroll",
    "scroll-scrub",
    "scroll-pin",
    "scrub:",
    "pin:",
)


_TRACK_PROPS = ("transform", "opacity", "scale", "clipPath", "top")


def _observed_scroll_motion(self: Gate) -> bool:
    """Library-agnostic: True when motion was OBSERVED under scroll during
    extraction, independent of any token allowlist. Catches unknown / hand-rolled
    motion and non-sticky parallax/reveal that no token grep sees, via
    transition-coverage scroll-classified elements (trigger ~ /scroll/ or sticky
    decoded.position) or element-tracking cross-position property change.
    """
    cov = self._load_json("transition-coverage.json")
    # Provenance guard: transition-coverage.json minted by the Phase-2
    # finalizer is DERIVED FROM transition-spec.json — spec-derived artifacts
    # may never serve as observation evidence about the spec (a 1-entry
    # placeholder spec manufactured the evidence of its own adequacy on
    # realfood-e2e-1).
    if isinstance(cov, dict) and cov.get("source") == "ui_clone.extraction_artifacts":
        cov = None
    if isinstance(cov, dict) and any(
        "transition-spec" in str(d) for d in (cov.get("derivedFrom") or [])
    ):
        cov = None
    if isinstance(cov, dict):
        for el in cov.get("animatedElements") or []:
            if not isinstance(el, dict):
                continue
            if "scroll" in str(el.get("trigger", "")).lower():
                return True
            dec = el.get("decoded") or {}
            if isinstance(dec, dict) and str(dec.get("position", "")).lower() == "sticky":
                return True
    track = self._load_json_any("element-tracking.json")
    if isinstance(track, list) and len(track) >= 2:
        seen: dict[str, dict[str, set[str]]] = {}
        for frame in track:
            elems = frame.get("elements") if isinstance(frame, dict) else None
            for el in elems or []:
                if not isinstance(el, dict):
                    continue
                sel = el.get("selector")
                if sel is None:
                    continue
                bucket = seen.setdefault(sel, {p: set() for p in _TRACK_PROPS})
                for p in _TRACK_PROPS:
                    bucket[p].add(json.dumps(el.get(p), sort_keys=True))
        for props in seen.values():
            if any(len(props[p]) >= 2 for p in _TRACK_PROPS):
                return True
    return False


def _scroll_motion_signals(self: Gate) -> bool:
    """True when STRONG scroll-motion evidence appears in ANY upstream artifact.

    Sources (broadened beyond scroll-engine.json so GSAP ScrollTrigger sites,
    whose scroll-engine.json is often empty, are not missed):
      - scroll-engine.json detected.<x>.matches > 0 (framer-motion / IO path)
      - _SCROLL_MOTION_TOKENS in bundle-map.json / external-sdks.json /
        generation-plan.json
      - _SCROLL_MOTION_TOKENS in the first ~30 JS bundles under bundles/
        (each read up to ~2MB — a bounded heuristic, not a full parse)
      - observed motion (transition-coverage scroll-classified / element-tracking
        cross-position change) — library-agnostic, see _observed_scroll_motion
    """
    # 1) framer-motion / IntersectionObserver path via scroll-engine.json.
    scroll_engine = self._load_json("scroll-engine.json") or {}
    detected_raw = scroll_engine.get("detected") if isinstance(scroll_engine, dict) else None
    detected = detected_raw if isinstance(detected_raw, dict) else {}
    for key in ("motion", "useScroll", "scrollYProgress", "IntersectionObserver"):
        entry = detected.get(key) or {}
        if isinstance(entry, dict) and (entry.get("matches") or 0) > 0:
            return True
    # 2) GSAP / scroll tokens in JSON artifacts (list-or-dict safe).
    for fname in ("bundle-map.json", "external-sdks.json", "generation-plan.json"):
        blob = json.dumps(self._load_json_any(fname) or "")
        if any(tok in blob for tok in _SCROLL_MOTION_TOKENS):
            return True
    # 3) bounded scan of the first ~30 JS bundles (≤2MB each).
    bundles_dir = self.ref_dir / "bundles"
    if bundles_dir.is_dir():
        for path in sorted(bundles_dir.glob("*.js"))[:30]:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")[: 2 * 1024 * 1024]
            except OSError:
                continue
            if any(tok in text for tok in _SCROLL_MOTION_TOKENS):
                return True
    # 4) observed motion (library-agnostic) — fires regardless of which library
    #    (or none) drove it, if pixels actually moved under scroll.
    if _observed_scroll_motion(self):
        return True
    return False


def _check_scroll_spec_coverage(self: Gate, spec: Any) -> list[CheckResult]:
    """Detect a prior audit escape: upstream artifacts
    show sticky elements + scroll-motion evidence (framer-motion,
    IntersectionObserver, scrollYProgress, or GSAP ScrollTrigger / scroll-scrub /
    pin tokens in the bundles / plan / sdk) but transition-spec.json has zero
    scroll-triggered entries, so motion verification never fires.

    Fires whenever scroll motion was observed/evidenced — sticky OR non-sticky
    parallax/reveal (the sticky-ONLY precondition is dropped so unknown-library
    and non-sticky cases are caught). Fails when ALL of the following hold:
      - sticky-elements.json is non-empty OR a scroll-classified element was
        observed (transition-coverage / element-tracking)
      - _scroll_motion_signals() is True (allowlist tokens OR observed motion)
      - transition-spec.json has zero entries whose trigger / type / mechanism
        contains scroll | intersection | inview | viewport | scrub
    """
    # Trust order inverted deliberately: STRONG independent evidence
    # (_scroll_motion_signals: bundle tokens, generation-plan markers) ALONE
    # satisfies the precondition. The old order gated on sticky/observed
    # first — and `observed` read transition-coverage.json, an artifact
    # DERIVED from the spec under audit, so a 1-entry placeholder spec
    # manufactured the evidence of its own adequacy and the check returned
    # [] before ever consulting the independent signals (realfood-e2e-1).
    # sticky-elements / _observed_scroll_motion remain available as
    # corroboration for diagnostics but no longer gate this check.
    if not self._scroll_motion_signals():
        return []
    spec_entries: list[Any] = []
    if isinstance(spec, list):
        spec_entries = spec
    elif isinstance(spec, dict):
        spec_entries = spec.get("transitions") or []
    scroll_pattern = re.compile(r"scroll|intersection|inview|viewport|scrub", re.I)
    has_scroll_entry = False
    for entry in spec_entries:
        if not isinstance(entry, dict):
            continue
        blob = f"{entry.get('trigger', '')} {entry.get('type', '')} {entry.get('mechanism', '')}"
        if scroll_pattern.search(blob):
            has_scroll_entry = True
            break
    observed_desc = "scroll-motion evidence (bundle tokens / plan signals)"
    if has_scroll_entry:
        return [
            CheckResult(
                "scroll-spec-coverage",
                "pass",
                f"✓ {observed_desc} + scroll-motion evidence — "
                "transition-spec has scroll-trigger entries.",
            )
        ]
    return [
        CheckResult(
            "scroll-spec-coverage",
            "fail",
            f"❌ {observed_desc} detected "
            "+ scroll-motion evidence (scroll-engine / bundles / sdk / plan / "
            "observed motion), but transition-spec.json "
            "has ZERO scroll-trigger entries. Pin / scroll-scrub / parallax / "
            "reveal motion "
            "will be unverified. Add transition-spec entries with "
            '`"trigger": "scroll"` or `"mechanism": "scroll-scrub"` for '
            "each animated region.",
            fix="Re-run scripts/extract/generation-plan.sh then enrich "
            "transition-spec.json with scroll-triggered entries per "
            "sticky-elements.json; consult animation-detection.md Phase B.",
        )
    ]
