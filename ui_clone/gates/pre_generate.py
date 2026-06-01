"""Pre-Generate gate.

Extracted from ui_clone/gate.py. The orchestrator `gate_pre_generate`
lives here; the individual `_check_*` helpers it dispatches to live in
`pre_generate_checks.py` (kept separate so each module stays under the
≤500-line per-gate budget). All of these are rebound onto the Gate class
in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ui_clone import dag as _dag

from .base import CheckResult
from .pre_generate_checks import (
    _check_audit_artifacts,  # noqa: F401  (re-exported for __init__ rebinding)
    _check_detection_artifact_integrity,  # noqa: F401
    _check_hover_timing,  # noqa: F401
    _check_scroll_spec_coverage,  # noqa: F401
    _check_section_counts,  # noqa: F401
    _check_transition_coverage,  # noqa: F401
    _check_webflow,  # noqa: F401
    _scroll_motion_signals,  # noqa: F401
)

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401


def gate_pre_generate(self: Gate) -> list[CheckResult]:
    results = []
    results.append(
        self.check_file(
            self.ref_dir / "extracted.json", "extracted.json (assembled extraction)"
        )
    )
    results.append(
        self.check_json_key(
            self.ref_dir / "extracted.json", "sections", "extracted.json content validation"
        )
    )
    results.append(
        self.check_file(self.ref_dir / "transition-spec.json", "transition-spec.json")
    )
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
                fix="bash $PLUGIN_ROOT/scripts/extract/generation-plan.sh "
                    f'"{self.ref_dir}"',
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
            violations = (
                (plan_data.get("assetSubstitution") or {}).get("violations") or []
            )
        except (OSError, ValueError, AttributeError):
            violations = []
        if violations:
            sample = ", ".join(
                f"{v.get('asset','?')}→{v.get('replacement','?')}"
                for v in violations[:3]
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
        banned_terms = (
            "emoji", "gradient", "placeholder", "stub", "emoji-or-gradient"
        )
        upstream_banned: list[dict[str, Any]] = []
        for img in (asset_sub.get("images") or []):
            if not isinstance(img, dict):
                continue
            repl = (img.get("replacement") or "").strip().lower()
            if any(term in repl for term in banned_terms):
                upstream_banned.append(img)
        if upstream_banned and len(violations) < len(upstream_banned):
            sample_up = ", ".join(
                f"{i.get('asset','?')}→{i.get('replacement','?')}"
                for i in upstream_banned[:3]
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
    results.extend(self._check_artifact_provenance())

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
        ("dom-scaffold.json", "dom-scaffold.json (Phase 2.7 — Fix 8 generation source-of-truth)", False),
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

