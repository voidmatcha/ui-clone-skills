"""Pre-Generate gate — private check helpers.

These helpers live in their own module so the top-level gate_pre_generate
fits under the per-gate ≤500-line budget. They're imported back into
`pre_generate` and rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

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
    regions = self._load_json("regions.json")
    if isinstance(regions, list):
        hover_click = [
            r for r in regions
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


def _check_scroll_spec_coverage(self: Gate, spec: Any) -> list[CheckResult]:
    """Detect the audit incident / Codex audit issue 5 escape: upstream artifacts
    show sticky elements + non-GSAP scroll engine signals (framer-motion,
    IntersectionObserver, scrollYProgress) but transition-spec.json has
    zero scroll-triggered entries, so motion verification never fires.

    Fails when ALL of the following hold:
      - sticky-elements.json (or extracted.json.stickyElements) is non-empty
      - scroll-engine.json shows at least one detected.<x>.matches > 0
        among (motion / useScroll / scrollYProgress / IntersectionObserver)
      - transition-spec.json has zero entries whose trigger / type contains
        scroll | intersection | inview | viewport | scrub
    """
    # sticky-elements.json can be a list OR a wrapper dict — coerce to list[Any].
    raw_sticky = self._load_json("sticky-elements.json")
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
    if not sticky:
        return []
    scroll_engine = self._load_json("scroll-engine.json") or {}
    detected = (scroll_engine.get("detected") or {}) if isinstance(scroll_engine, dict) else {}
    non_gsap_signal = False
    for key in ("motion", "useScroll", "scrollYProgress", "IntersectionObserver"):
        entry = detected.get(key) or {}
        if isinstance(entry, dict) and (entry.get("matches") or 0) > 0:
            non_gsap_signal = True
            break
    if not non_gsap_signal:
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
    if has_scroll_entry:
        return [
            CheckResult(
                "scroll-spec-coverage",
                "pass",
                f"✓ {len(sticky)} sticky element(s) + scroll-engine signal — "
                "transition-spec has scroll-trigger entries.",
            )
        ]
    sample_sticky = ", ".join(
        (e.get("className") or e.get("cls") or e.get("tag") or "?")
        for e in sticky[:3]
        if isinstance(e, dict)
    )
    signals = ", ".join(
        f"{k}({(detected[k] or {}).get('matches')})"
        for k in ("motion", "useScroll", "scrollYProgress", "IntersectionObserver")
        if isinstance(detected.get(k), dict)
        and (detected[k].get("matches") or 0) > 0
    )
    return [
        CheckResult(
            "scroll-spec-coverage",
            "fail",
            f"❌ {len(sticky)} sticky element(s) detected ({sample_sticky}) "
            f"+ scroll engine signals ({signals}), but transition-spec.json "
            "has ZERO scroll-trigger entries. Pin / scroll-scrub motion "
            "will be unverified. Add transition-spec entries with "
            '`"trigger": "scroll"` or `"mechanism": "scroll-scrub"` for '
            "each animated sticky region.",
            fix="Re-run scripts/extract/generation-plan.sh then enrich "
            "transition-spec.json with scroll-triggered entries per "
            "sticky-elements.json; consult animation-detection.md Phase B.",
        )
    ]


