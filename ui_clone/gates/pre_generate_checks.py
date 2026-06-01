"""Pre-Generate gate — private check helpers.

These helpers live in their own module so the top-level gate_pre_generate
fits under the per-gate ≤500-line budget. They're imported back into
`pre_generate` and rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from .base import CheckResult

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
    their content cross-references the section-map (Codex audit review:
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
    sec_ids = {s.get("id") for s in section_map.get("sections", []) if s.get("id")}
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
    # juanmora-iter-10 finding (2026-05-28): regions.json schema migrated
    # from bare list to `{"regions": [...]}` dict-wrap (per
    # _capture_artifacts.write_regions_json). The old `isinstance(list)`
    # branch silently never fired against new captures, masking any
    # hover/click upstream signal regions.json carried. Accept both shapes.
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
    detected = (scroll_engine.get("detected") or {}) if isinstance(scroll_engine, dict) else {}
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
    """Detect the audit incident / Codex audit issue 5 escape: upstream artifacts
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
    # sticky-elements.json can be a list OR a wrapper dict — coerce to list[Any].
    raw_sticky = self._load_json_any("sticky-elements.json")
    sticky: list[Any] = []
    if isinstance(raw_sticky, list):
        sticky = raw_sticky
    elif isinstance(raw_sticky, dict):
        entries = raw_sticky.get("elements") or raw_sticky.get("stickyElements")
        if isinstance(entries, list):
            sticky = entries
    if not sticky:
        extracted = self._load_json("extracted.json") or {}
        ext_sticky = extracted.get("stickyElements") if isinstance(extracted, dict) else None
        if isinstance(ext_sticky, list):
            sticky = ext_sticky
    observed = _observed_scroll_motion(self)
    # Drop the sticky-ONLY precondition: any observed scroll-classified element
    # (sticky OR non-sticky parallax/reveal) requires a scroll-trigger spec entry.
    if not sticky and not observed:
        return []
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
    if sticky:
        sample_sticky = ", ".join(
            (e.get("className") or e.get("cls") or e.get("tag") or "?")
            for e in sticky[:3]
            if isinstance(e, dict)
        )
        observed_desc = f"{len(sticky)} sticky element(s) ({sample_sticky})"
    else:
        observed_desc = "observed scroll motion (non-sticky parallax/reveal)"
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


