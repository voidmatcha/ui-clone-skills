"""Pre-Generate gate.

Extracted from ui_clone/gate.py. The orchestrator `gate_pre_generate`
lives here; the individual `_check_*` helpers it dispatches to live in
`pre_generate_checks.py` (kept separate so each module stays under the
≤500-line per-gate budget). All of these are rebound onto the Gate class
in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from typing import TYPE_CHECKING, Any

from ui_clone import dag as _dag

from .base import CheckResult
from .pre_generate_checks import (
    _check_audit_artifacts,  # noqa: F401  (re-exported for __init__ rebinding)
    _check_detection_artifact_integrity,  # noqa: F401
    _check_hover_timing,  # noqa: F401
    _check_regions_generation_readiness,  # noqa: F401
    _check_scroll_spec_coverage,  # noqa: F401
    _check_section_counts,  # noqa: F401
    _check_transition_coverage,  # noqa: F401
    _check_webflow,  # noqa: F401
    _scroll_motion_signals,  # noqa: F401
)
from .pre_generate_media import check_media_inventory_receipts
from .state_coverage import (
    _is_motion_rich_ref,
    _required_motion_state_artifact_gaps,
)

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401


def gate_pre_generate(self: Gate) -> list[CheckResult]:
    results = []
    try:
        from ui_clone.extraction_artifacts import refresh_extracted_artifact

        refresh_extracted_artifact(self.ref_dir)
    except Exception as exc:  # pragma: no cover - defensive gate hardening
        results.append(
            CheckResult(
                "pre-generate artifact finalizer",
                "warn",
                f"pre-generate artifact finalizer skipped: {exc}",
            )
        )

    results.append(
        self.check_file(self.ref_dir / "extracted.json", "extracted.json (assembled extraction)")
    )
    results.append(
        self.check_json_key(
            self.ref_dir / "extracted.json", "sections", "extracted.json content validation"
        )
    )
    results.append(self.check_file(self.ref_dir / "transition-spec.json", "transition-spec.json"))
    runtime_media_path = self.ref_dir / "runtime-media.json"
    required_media_path = self.ref_dir / "required-media.json"
    results.append(
        self.check_file(
            runtime_media_path,
            "runtime-media.json (hydrated media inventory)",
            fix="Run Step 6b-bis runtime-media.sh against the live reference URL.",
        )
    )
    results.append(
        self.check_json_key(
            runtime_media_path,
            "videos",
            "runtime-media.json content validation",
        )
    )
    results.append(
        self.check_file(
            required_media_path,
            "required-media.json (required media inventory)",
            fix=f'bash $PLUGIN_ROOT/scripts/extract/required-media.sh "{self.ref_dir}"',
        )
    )
    results.append(
        self.check_json_key(
            required_media_path,
            "videos",
            "required-media.json video inventory",
        )
    )
    results.append(
        self.check_json_key(
            required_media_path,
            "lottie",
            "required-media.json Lottie inventory",
        )
    )
    results.extend(check_media_inventory_receipts(self.ref_dir))
    # Research1 finding: agent ran asset-download.sh but skipped Phase 7-pre
    # (generation-plan.sh). Without the plan, transition wiring + library
    # installs + ds-components groupings get dropped entirely. Require the
    # plan exist + have a valid schemaVersion before generation starts.
    plan_path = self.ref_dir / "generation-plan.json"
    if not plan_path.exists():
        results.append(
            CheckResult(
                "generation-plan.json",
                "fail",
                "generation-plan.json — MISSING. Run scripts/extract/generation-plan.sh "
                "before Phase 6. The plan is the Phase 6 SSOT for componentList, "
                "library installs, sticky strategy, signature effects.",
                fix=f'bash $PLUGIN_ROOT/scripts/extract/generation-plan.sh "{self.ref_dir}"',
            )
        )
    else:
        results.append(
            self.check_json_key(
                plan_path, "componentList", "generation-plan.json content validation"
            )
        )
        # Reject emoji / gradient / placeholder substitutions. generation-plan.sh
        # writes BANNED_REPLACEMENTS violations to assetSubstitution.violations[];
        # without this gate the array is recorded but never blocks generation,
        try:
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError):
            plan_data = {}
        if not isinstance(plan_data, dict):
            plan_data = {}
        if plan_data.get("schemaVersion") != 2:
            results.append(
                CheckResult(
                    "generation-plan schema",
                    "fail",
                    "generation-plan.json must be enriched to schemaVersion 2 "
                    "by the generation-planner before implementation. The "
                    "schemaVersion 1 deterministic base omits semantic tokens, "
                    "component wiring, and signature-effect decisions.",
                    fix="Dispatch the generation-planner role with "
                    "skills/ui-reverse-engineering/enrichment.md, then rerun "
                    "python -m ui_clone.gate <ref-dir> pre-generate.",
                )
            )
        else:
            # Bind enrichment to its section, style, token, and signature sources.
            invalid_receipt, stale_sources = _dag.generation_plan_provenance_issues(
                self.ref_dir, plan_data.get("provenance")
            )
            if invalid_receipt or stale_sources:
                receipt_detail = "; ".join(
                    [*invalid_receipt, *(f"stale source hash: {path}" for path in stale_sources)]
                )
                results.append(CheckResult(
                    "generation-plan provenance", "fail",
                    f"generation-plan.json provenance is not trustworthy: {receipt_detail}.",
                    fix="Re-run generation-plan.sh, then generation-planner enrichment.",
                    stale=bool(stale_sources) and not invalid_receipt,
                ))
            enrichment_gaps: list[str] = []
            components = plan_data.get("componentList")
            if not isinstance(components, list):
                enrichment_gaps.append("componentList must be an array")
                components = []

            token_categories = ("colors", "spacing", "typography", "radius", "shadows")
            tokens = plan_data.get("tokens")
            if not isinstance(tokens, dict) or any(
                not isinstance(tokens.get(category), dict)
                for category in token_categories
            ):
                enrichment_gaps.append(
                    "tokens must contain colors/spacing/typography/radius/shadows objects"
                )

            if not isinstance(plan_data.get("dsComponentsRequired"), list):
                enrichment_gaps.append("dsComponentsRequired must be an array")

            missing_wires = [
                index
                for index, component in enumerate(components)
                if not isinstance(component, dict)
                or not isinstance(component.get("wires"), list)
            ]
            if missing_wires:
                enrichment_gaps.append(
                    "componentList entries missing wires[] at indexes "
                    + ", ".join(str(index) for index in missing_wires[:8])
                )

            section_map = self._load_json("section-map.json")
            section_rows = (
                section_map.get("sections")
                if isinstance(section_map, dict)
                else section_map
            )
            expected_sections = (
                [row for row in section_rows if isinstance(row, dict)]
                if isinstance(section_rows, list)
                else []
            )
            required_component_fields = ("name", "matchedSection", "selector", "path")
            invalid_identity = [
                index
                for index, component in enumerate(components)
                if not isinstance(component, dict)
                or any(
                    not isinstance(component.get(field), str)
                    or not component.get(field, "").strip()
                    for field in required_component_fields
                )
            ]
            if invalid_identity:
                enrichment_gaps.append(
                    "componentList entries missing name/matchedSection/selector/path "
                    "at indexes "
                    + ", ".join(str(index) for index in invalid_identity[:8])
                )
            for unique_field in ("name", "path"):
                values_by_portable_key: dict[str, list[str]] = {}
                for component in components:
                    if (
                        not isinstance(component, dict)
                        or not isinstance(component.get(unique_field), str)
                    ):
                        continue
                    value = component[unique_field].strip()
                    if not value:
                        continue
                    portable_key = unicodedata.normalize("NFC", value).casefold()
                    values_by_portable_key.setdefault(portable_key, []).append(value)
                duplicate_groups = [
                    values
                    for values in values_by_portable_key.values()
                    if len(values) > 1
                ]
                if duplicate_groups:
                    enrichment_gaps.append(
                        f"duplicate component {unique_field} value(s): "
                        + "; ".join(
                            " / ".join(values)
                            for values in duplicate_groups[:8]
                        )
                    )

            expected_section_ids = [
                str(
                    section.get("id")
                    or section.get("name")
                    or section.get("selector")
                    or f"section-{index}"
                )
                for index, section in enumerate(expected_sections)
            ]
            matched_section_ids = [
                component["matchedSection"].strip()
                for component in components
                if isinstance(component, dict)
                and isinstance(component.get("matchedSection"), str)
                and component["matchedSection"].strip()
            ]
            expected_counts = Counter(expected_section_ids)
            matched_counts = Counter(matched_section_ids)
            if expected_counts != matched_counts:
                missing = list((expected_counts - matched_counts).elements())
                unexpected = list((matched_counts - expected_counts).elements())
                detail = []
                if missing:
                    detail.append("missing " + ", ".join(missing[:8]))
                if unexpected:
                    detail.append("duplicate/unexpected " + ", ".join(unexpected[:8]))
                enrichment_gaps.append(
                    "componentList matchedSection coverage mismatch"
                    + (": " + "; ".join(detail) if detail else "")
                )

            if enrichment_gaps:
                results.append(
                    CheckResult(
                        "generation-plan enrichment",
                        "fail",
                        "generation-plan.json claims schemaVersion 2 but is not "
                        "implementation-ready: "
                        + "; ".join(enrichment_gaps)
                        + ". Re-run generation-planner enrichment instead of "
                        "editing only schemaVersion.",
                        fix="Dispatch the generation-planner role with "
                        "skills/ui-reverse-engineering/enrichment.md.",
                    )
                )
        plan_asset_sub = plan_data.get("assetSubstitution")
        violations = (
            (plan_asset_sub.get("violations") or [])
            if isinstance(plan_asset_sub, dict)
            else []
        )
        if violations:
            sample = ", ".join(
                f"{v.get('asset', '?')}→{v.get('replacement', '?')}" for v in violations[:3]
            )
            results.append(
                CheckResult(
                    "assetSubstitution.violations",
                    "fail",
                    f"{len(violations)} banned substitution(s) detected "
                    f"(emoji / gradient / placeholder / stub): {sample}. "
                    "Research-mode policy: download the real asset via "
                    "asset-download.sh; never substitute with placeholder strings.",
                    fix="bash $PLUGIN_ROOT/scripts/extract/asset-download.sh "
                    f'"{self.ref_dir}" <impl-public-dir> && '
                    "bash $PLUGIN_ROOT/scripts/extract/generation-plan.sh "
                    f'"{self.ref_dir}"',
                )
            )
        asset_sub = self._load_json("asset-substitution.json") or {}
        banned_terms = ("emoji", "gradient", "placeholder", "stub", "emoji-or-gradient")
        upstream_banned: list[dict[str, Any]] = []
        for img in asset_sub.get("images") or []:
            if not isinstance(img, dict):
                continue
            repl = (img.get("replacement") or "").strip().lower()
            if any(term in repl for term in banned_terms):
                upstream_banned.append(img)
        if upstream_banned and len(violations) < len(upstream_banned):
            sample_up = ", ".join(
                f"{i.get('asset', '?')}→{i.get('replacement', '?')}" for i in upstream_banned[:3]
            )
            results.append(
                CheckResult(
                    "assetSubstitution.violations.cross-ref",
                    "fail",
                    f"{len(upstream_banned)} banned substitution(s) in "
                    f"asset-substitution.json ({sample_up}) but "
                    f"generation-plan.json.assetSubstitution.violations "
                    f"reports {len(violations)} — the plan understates "
                    "the upstream source. Plan appears hand-rewritten to "
                    "dodge the violations check.",
                    fix="bash $PLUGIN_ROOT/scripts/extract/generation-plan.sh "
                    f'"{self.ref_dir}"  # regenerate plan from sources',
                )
            )
        # Ordering-hole backstop (design fix #9): verification-plan.json is minted
        # at Step 5d, before generation-plan.json exists, so plan-derived rows
        # (signature-effects-coverage) never register unless the plan is amended
        # after Step 7-pre. If the plan exists but lacks a row its generation-plan
        # now warrants, fail with a re-run-amend remediation. Mirrors the
        # verification-plan.sh signature-effects dispatch condition exactly.
        vplan_path = self.ref_dir / "verification-plan.json"
        if vplan_path.is_file():
            try:
                gp = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                gp = {}
            se = gp.get("signatureEffects")
            ss = gp.get("scrollScrub") if isinstance(gp.get("scrollScrub"), dict) else {}
            has_se = isinstance(se, list) and bool(se)
            scrub_scale = bool(ss.get("required")) and any(
                str(t.get("property") or "").startswith("scale")
                for s in (ss.get("sites") or [])
                if isinstance(s, dict)
                for t in (s.get("transforms") or [])
                if isinstance(t, dict)
            )
            if has_se or scrub_scale:
                try:
                    vplan = json.loads(vplan_path.read_text(encoding="utf-8"))
                    row_ids = {
                        c.get("id")
                        for c in (vplan.get("requiredChecks") or [])
                        if isinstance(c, dict)
                    }
                except (OSError, ValueError):
                    row_ids = set()
                if "signature-effects-coverage" not in row_ids:
                    results.append(
                        CheckResult(
                            "verification-plan amend (signature-effects-coverage)",
                            "fail",
                            "generation-plan.json declares signatureEffects / a "
                            "scrollScrub scale band, but verification-plan.json is "
                            "missing signature-effects-coverage — the plan was minted "
                            "before generation-plan.json existed and was never "
                            "amended, leaving the block-severity motion gate "
                            "unregistered.",
                            fix="bash $PLUGIN_ROOT/skills/visual-debug/scripts/"
                            f'verification-plan.sh "{self.ref_dir}" --amend',
                        )
                    )
    results.extend(self._check_artifact_provenance())
    results.extend(_check_regions_generation_readiness(self))

    # Load once — reused across helpers below
    spec = self._load_json("transition-spec.json")

    # DAG staleness — transitive dependency check
    stale_issues = _dag.check_staleness(self.ref_dir)
    for issue in stale_issues:
        results.append(
            CheckResult(
                f"staleness: {issue.stale}",
                "fail" if issue.severity == "block" else "warn",
                f"{issue.stale} — STALE (re-extracted after {issue.because_of})",
                fix=issue.fix,
            )
        )

    if _is_motion_rich_ref(self.ref_dir):
        missing_state_artifacts = _required_motion_state_artifact_gaps(self.ref_dir)
        if missing_state_artifacts:
            results.append(
                CheckResult(
                    "state-capture prerequisites",
                    "fail",
                    (
                        "motion-rich ref is missing required Phase A/B/C "
                        "state capture artifact(s) before generation: "
                        f"{', '.join(missing_state_artifacts)}. "
                        "Implementation must not start from a settled-only "
                        "snapshot because splash, scroll, and hover transition "
                        "ground truth would be incomplete."
                    ),
                    fix=(
                        "bash scripts/extract/capture-states.sh <url> <session> <ref_dir> && "
                        "bash scripts/extract/capture-scroll.sh <url> <session> <ref_dir> && "
                        "bash scripts/extract/extract-hover-css-rules.sh <session> <ref_dir> <url> && "
                        "bash scripts/extract/capture-hover.sh <url> <session> <ref_dir> && "
                        "python -m ui_clone.gate <ref_dir> pre-generate"
                    ),
                )
            )

    for filename, label, allow_empty in [
        ("animation-init-styles.json", "animation-init-styles.json (Step 2.6)", False),
        ("section-map.json", "section-map.json (semantic section enumeration)", False),
        ("svg-text-elements.json", "svg-text-elements.json (SVG-as-text detection)", True),
        # Fix 9 — dom-scaffold.json (Phase 2.7) is the Fix 8 source-of-truth
        # for Phase-4 generation. V5 showed agents skipping Phase 2.7 when
        # it lived as SKILL.md guidance only; making it a pre-generate gate
        # artifact enforces it before any component is written. The
        # scaffold's anti-fabrication value is lost if Phase 4 starts
        # without it.
        (
            "dom-scaffold.json",
            "dom-scaffold.json (Phase 2.7 — Fix 8 generation source-of-truth)",
            False,
        ),
    ]:
        results.append(
            self.check_file(self.ref_dir / filename, label, allow_empty_array=allow_empty)
        )

    results.append(
        self.check_file(
            self.ref_dir / "responsive" / "sizing-expressions.json",
            "sizing-expressions.json (multi-viewport element sizing)",
        )
    )
    # Content validation: an existence-only check passes the deterministic
    # single-viewport sentinel (_finalize_responsive) even when the Step 4-C2
    # sweep was skipped. Fail when that sentinel coexists with real responsive
    # signals so generation cannot proceed with an empty sizing lookup table.
    from ui_clone.extraction_artifacts import responsive_sweep_remediation

    sizing_remediation = responsive_sweep_remediation(self.ref_dir)
    if sizing_remediation:
        results.append(
            CheckResult(
                "sizing-expressions.json content validation",
                "fail",
                sizing_remediation,
                fix=(
                    "Read responsive-detection.md Step 4-C2 → re-run the "
                    "multi-viewport element sizing sweep."
                ),
            )
        )

    # Viewport-scaled em check
    typo = self._load_json("typography.json")
    if typo:
        scaling = typo.get("scalingSystem", "")
        if scaling and any(k in scaling.lower() for k in ("viewport-scaled", "em-based")):
            results.append(
                self.check_file(
                    self.ref_dir / "em-conversion.json",
                    f"em-conversion.json (REQUIRED for {scaling} sites)",
                )
            )

    # Hover timing + preloader
    interactions_data = self._load_json("interactions-detected.json")
    has_hover = False
    if interactions_data:
        hover_results, has_hover = self._check_hover_timing(interactions_data)
        results.extend(hover_results)

    if has_hover:
        results.append(
            self.check_file(
                self.ref_dir / "hover-css-rules.json",
                "hover-css-rules.json (ALL :hover rules from live stylesheets)",
            )
        )
    else:
        results.append(
            CheckResult(
                "hover-css-rules.json",
                "pass",
                "hover-css-rules.json (skipped — no hover interactions detected)",
            )
        )

    # Webflow IX2
    results.extend(self._check_webflow())

    # Transition coverage
    results.extend(self._check_transition_coverage(spec))

    results.extend(self._check_scroll_spec_coverage(spec))

    # Detection-artifact integrity (Common cheat pattern). Sub-agent reported
    # "Emptied interactions-detected.json after observing the impl uses
    # native CSS" — a classic gate-game where hand-clearing a detection
    # artifact silences downstream dispatchers. Cross-check the artifact
    # against upstream evidence; fail if the artifact has been zeroed
    # while sibling detection sources still indicate the feature exists.
    results.extend(self._check_detection_artifact_integrity())

    # Section count cross-check
    section_map = self._load_json("section-map.json")
    component_map = self._load_json("component-map.json")
    if section_map and component_map:
        results.extend(self._check_section_counts(section_map, component_map))

    # Audit artifacts
    results.extend(self._check_audit_artifacts())

    return results
