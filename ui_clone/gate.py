"""
Gate validation for ui-clone pipeline.

Usage:
    python -m ui_clone.gate <ref-dir> <gate> [--json]
    gate: reference | extraction | bundle | spec | pre-generate |
          post-implement | section-compare | all
Exit: 0=PASS, 1=BLOCKED, 2=usage error
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, Literal

from ui_clone import dag as _dag
from ui_clone import state as _state
from ui_clone.hooks._common import GREEN as _GREEN
from ui_clone.hooks._common import NC as _NC
from ui_clone.hooks._common import RED as _RED
from ui_clone.hooks._common import YELLOW as _YELLOW
from ui_clone.hooks._common import load_json_safe as _load_json_safe

# Single source of truth: state.GATE_ORDER. Everything else (VALID_GATES,
# dispatch dict, drift validators) is derived. Adding a gate = (1) add to
# state.GATE_ORDER, (2) add a `gate_<name>` method to Gate (with `-` → `_`).
# The import-time validator below catches any miss.
VALID_GATES = list(_state.GATE_ORDER) + ["all"]


def _gate_method_name(gate: str) -> str:
    """Map gate name (kebab-case) to Gate method name (snake_case)."""
    return f"gate_{gate.replace('-', '_')}"


@dataclass
class CheckResult:
    label: str
    status: Literal["pass", "fail", "warn"]
    message: str
    fix: str = ""


# Valid `verifiedBy` values for known-artifacts.json entries. Entries with
# any other value are rejected (the underlying FAIL stays a FAIL).
_VALID_VERIFIED_BY = {
    "readPixels",
    "bundle-grep",
    "manual-frame-cmp",
    "non-deterministic-ref",
}

# Required fields per entry. Missing any → rejected.
_REQUIRED_ARTIFACT_FIELDS = ("name", "verifiedBy", "evidence", "aeThresholdCeiling", "verifiedAt")

# AE growth multiplier — entry rejected if current AE > ceiling × this value.
_AE_GROWTH_MULTIPLIER = 1.5

# Artifacts that are too easy for an agent to hand-write convincingly. These
# must carry an evidence trail before code generation is allowed.
_PROVENANCE_REQUIRED_ARTIFACTS = (
    "extracted.json",
    "transition-spec.json",
    "animation-init-styles.json",
    "section-map.json",
    "svg-text-elements.json",
    "responsive/sizing-expressions.json",
    "interactions-detected.json",
    "transition-coverage.json",
    "component-map.json",
)

_VALID_PROVENANCE_SOURCES = {
    "agent-browser-eval",
    "ui-capture",
    "computed-style",
    "dom-snapshot",
    "bundle-grep",
    "downloaded-bundle",
    "visual-measurement",
    "script",
    "generated-from-artifacts",
}

_DISALLOWED_PROVENANCE_SOURCES = {
    "manual",
    "handwritten",
    "guess",
    "guessed",
    "assumption",
    "vision-only",
    "look-at-only",
}


def _parse_failed_sections(lines: list[str]) -> list[tuple[str, int]]:
    """Extract (section_name, ae) pairs from sections/result.txt ❌ lines.

    result.txt is a markdown table:
        | <name> | <ae> | <ae/mpx> | <severity> | ❌ |
    Returns the failed sections only. Names missing or AE unparseable
    are still returned (with AE=0) so we don't silently drop failures.
    """
    out: list[tuple[str, int]] = []
    for ln in lines:
        if "❌" not in ln or not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) < 2:
            continue
        name = cells[0]
        if not name or name.lower() == "section" or "---" in name:
            continue
        try:
            ae = int(cells[1])
        except (ValueError, IndexError):
            ae = 0
        out.append((name, ae))
    return out


def _parse_all_section_ae(lines: list[str]) -> list[tuple[str, int]]:
    """Extract (section_name, ae) pairs from result.txt — pass AND fail rows.

    Used by the strict-cap check: a `severity=minor ✅ PASS` row can still
    carry AE in the hundreds of thousands, large enough to be visually
    obvious. The classifier's severity is density-based and lenient when
    impl is small; absolute AE caps catch the cases severity misses.
    """
    out: list[tuple[str, int]] = []
    for ln in lines:
        if not ln.startswith("|") or "---" in ln:
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) < 2:
            continue
        name = cells[0]
        if not name or name.lower() == "section":
            continue
        # Skip the **Result:** summary row that has cells[0] = ""
        ae_str = cells[1]
        if ae_str in ("", "—", "-"):
            continue
        try:
            ae = int(ae_str)
        except ValueError:
            continue
        out.append((name, ae))
    return out


def _validate_artifact_entry(
    entry: dict[str, Any], section_ae: dict[str, int]
) -> tuple[bool, str, str]:
    """Validate a known-artifacts.json sections[] entry.

    Returns (is_valid, rejection_reason, section_name). Missing or unknown
    fields → invalid. AE > ceiling × _AE_GROWTH_MULTIPLIER → invalid.
    Section not in the failed set → invalid (no FAIL to downgrade).
    """
    name = str(entry.get("name") or "")
    if not name:
        return False, "missing 'name'", ""

    missing = [f for f in _REQUIRED_ARTIFACT_FIELDS if f not in entry]
    if missing:
        return False, f"missing field(s): {', '.join(missing)}", name

    verified_by = entry.get("verifiedBy")
    if verified_by not in _VALID_VERIFIED_BY:
        return False, f"unknown verifiedBy: {verified_by!r}", name

    if name not in section_ae:
        return False, "no matching FAIL in sections/result.txt", name

    try:
        ceiling = float(entry.get("aeThresholdCeiling") or 0)
    except (TypeError, ValueError):
        return False, "aeThresholdCeiling not a number", name
    if ceiling <= 0:
        return False, "aeThresholdCeiling must be > 0", name

    current_ae = section_ae[name]
    if current_ae > ceiling * _AE_GROWTH_MULTIPLIER:
        return (
            False,
            f"current AE {current_ae} exceeds ceiling {int(ceiling)} × {_AE_GROWTH_MULTIPLIER} "
            f"(={int(ceiling * _AE_GROWTH_MULTIPLIER)}) — bug got worse",
            name,
        )

    return True, "", name


class Gate:
    def __init__(self, ref_dir: Path) -> None:
        self.ref_dir = Path(ref_dir)

    # ── Primitive checks ──

    def check_file(
        self,
        path: Path,
        label: str,
        *,
        allow_empty_array: bool = False,
        fix: str = "",
    ) -> CheckResult:
        """File must exist and have > 10 bytes (or be a valid empty JSON array if allow_empty_array)."""
        if not path.exists():
            return CheckResult(label, "fail", f"{label} — MISSING", fix=fix)
        try:
            size = path.stat().st_size
        except OSError as e:
            return CheckResult(label, "fail", f"{label} — exists but unreadable ({e})", fix=fix)
        if size < 10:
            if allow_empty_array and size >= 2:
                try:
                    if json.loads(path.read_text()) == []:
                        return CheckResult(label, "pass", f"{label} (empty array — no elements found)")
                except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                    pass
            return CheckResult(label, "fail", f"{label} — exists but empty ({size} bytes)", fix=fix)
        return CheckResult(label, "pass", f"{label}")

    def check_dir(
        self,
        path: Path,
        label: str,
        min_files: int = 1,
        fix: str = "",
        pattern: str = "*",
    ) -> CheckResult:
        """Directory must exist with at least min_files files matching pattern."""
        if not path.is_dir():
            return CheckResult(label, "fail", f"{label} — MISSING directory", fix=fix)
        matched = list(islice((p for p in path.rglob(pattern) if p.is_file()), min_files))
        if len(matched) < min_files:
            return CheckResult(
                label,
                "fail",
                f"{label} \u2014 directory exists but only {len(matched)} files (need \u2265{min_files})",
            )
        return CheckResult(label, "pass", f"{label} (\u2265{min_files} files)")

    def check_json_key(self, path: Path, key: str, label: str) -> CheckResult:
        """JSON file must contain a top-level key."""
        if not path.exists():
            # File-level failure already reported by check_file; skip to avoid duplicate fail
            return CheckResult(label, "warn", f"{label} (skipped — file missing)")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return CheckResult(label, "fail", f"{label} — malformed JSON: {str(e)[:80]}")
        if key not in data:
            return CheckResult(label, "fail", f"{label} — JSON missing required key '{key}'")
        return CheckResult(label, "pass", f"{label} (has '{key}' key)")

    def _load_json(self, filename: str) -> dict[str, Any] | None:
        """Load a JSON artifact from ref_dir. Returns None if missing, malformed, or not an object."""
        return _load_json_safe(self.ref_dir / filename)

    def _check_artifact_provenance(self) -> list[CheckResult]:
        """Require evidence-backed provenance for high-risk extraction artifacts."""
        path = self.ref_dir / "artifact-provenance.json"
        results: list[CheckResult] = []
        if not path.exists():
            return [
                CheckResult(
                    "artifact-provenance.json",
                    "fail",
                    "artifact-provenance.json — MISSING (required before pre-generate)",
                    fix="Write artifact-provenance.json with source/evidence for each generated extraction artifact; rerun the extraction step instead of hand-writing JSON.",
                )
            ]

        data = self._load_json("artifact-provenance.json")
        if data is None:
            return [
                CheckResult(
                    "artifact-provenance.json",
                    "fail",
                    "artifact-provenance.json — malformed or not a JSON object",
                )
            ]

        raw_entries = data.get("artifacts")
        if not isinstance(raw_entries, list):
            return [
                CheckResult(
                    "artifact-provenance.json",
                    "fail",
                    "artifact-provenance.json — JSON missing required artifacts[] array",
                )
            ]

        entries: dict[str, dict[str, Any]] = {}
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            artifact = entry.get("path")
            if isinstance(artifact, str):
                entries[artifact] = entry

        for artifact in _PROVENANCE_REQUIRED_ARTIFACTS:
            entry = entries.get(artifact)
            label = f"provenance: {artifact}"
            if entry is None:
                results.append(
                    CheckResult(
                        label,
                        "fail",
                        f"artifact-provenance.json — missing provenance for {artifact}",
                    )
                )
                continue

            source = entry.get("source")
            if not isinstance(source, str) or not source:
                results.append(CheckResult(label, "fail", f"{artifact} provenance — missing source"))
                continue

            source_normalized = source.strip().lower()
            if source_normalized in _DISALLOWED_PROVENANCE_SOURCES:
                results.append(
                    CheckResult(
                        label,
                        "fail",
                        f"{artifact} provenance source '{source}' is disallowed; rerun extraction from browser/script evidence",
                    )
                )
                continue

            if source_normalized not in _VALID_PROVENANCE_SOURCES:
                results.append(
                    CheckResult(
                        label,
                        "fail",
                        f"{artifact} provenance source '{source}' is unknown; use one of {sorted(_VALID_PROVENANCE_SOURCES)}",
                    )
                )
                continue

            evidence = entry.get("evidence")
            if not isinstance(evidence, list) or not evidence or not all(isinstance(item, str) and item for item in evidence):
                results.append(
                    CheckResult(label, "fail", f"{artifact} provenance — missing non-empty evidence[]")
                )
                continue

            missing = [item for item in evidence if not (self.ref_dir / item).exists()]
            if missing:
                results.append(
                    CheckResult(
                        label,
                        "fail",
                        f"{artifact} provenance evidence missing: {', '.join(missing[:3])}",
                    )
                )
                continue

            generated_at = entry.get("generatedAt")
            if not isinstance(generated_at, str) or not generated_at:
                results.append(CheckResult(label, "fail", f"{artifact} provenance — missing generatedAt"))
                continue

            results.append(CheckResult(label, "pass", f"{artifact} provenance ({source_normalized})"))

        return results

    # ── Gate functions ──

    def gate_reference(self) -> list[CheckResult]:
        results = []
        results.append(
            self.check_dir(
                self.ref_dir / "static" / "ref",
                "static/ref screenshots",
                min_files=5,
                fix="Run Phase 1: invoke /ui-capture <url> to capture reference screenshots",
            )
        )
        results.append(
            self.check_dir(
                self.ref_dir / "transitions" / "ref",
                "transitions/ref (transition videos)",
                min_files=1,
                fix="Run Phase 1: invoke /ui-capture <url> to capture transition videos",
            )
        )
        results.append(
            self.check_file(
                self.ref_dir / "regions.json",
                "regions.json (transition regions)",
                fix="Run Phase 1: invoke /ui-capture <url> to generate regions.json",
            )
        )
        return results

    def gate_extraction(self) -> list[CheckResult]:
        results = []
        for filename, label in [
            ("structure.json", "structure.json (DOM hierarchy)"),
            ("head.json", "head.json (metadata)"),
            ("styles.json", "styles.json (computed styles)"),
            ("fonts.json", "fonts.json (font faces)"),
            ("visible-images.json", "visible-images.json"),
            ("inline-svgs.json", "inline-svgs.json"),
            ("body-state.json", "body-state.json"),
            ("design-bundles.json", "design-bundles.json"),
        ]:
            results.append(self.check_file(self.ref_dir / filename, label))

        results.append(
            self.check_file(
                self.ref_dir / "css" / "variables.txt", "css/variables.txt (CSS custom properties)"
            )
        )

        # Viewport-scaled font em-conversion gate
        typo = self._load_json("typography.json")
        if typo:
            scaling = typo.get("scalingSystem", "")
            if scaling and any(k in scaling.lower() for k in ("viewport-scaled", "em-based")):
                results.append(
                    self.check_file(
                        self.ref_dir / "em-conversion.json",
                        f"em-conversion.json (REQUIRED: scalingSystem={scaling})",
                    )
                )

        return results

    def gate_bundle(self) -> list[CheckResult]:
        results = []
        results.append(
            self.check_dir(self.ref_dir / "bundles", "bundles/ (downloaded JS chunks)", min_files=1)
        )

        # Advisory: warn if fewer than 3 JS chunks
        bundles_dir = self.ref_dir / "bundles"
        if bundles_dir.is_dir():
            js_count = sum(1 for f in bundles_dir.rglob("*.js") if f.is_file())
            if 1 <= js_count < 3:
                results.append(
                    CheckResult(
                        "JS chunk count",
                        "warn",
                        f"Only {js_count} JS chunk(s) — typical SPAs have \u22653. "
                        "Verify all chunks via performance.getEntriesByType('resource').",
                    )
                )

        for filename, label in [
            ("interactions-detected.json", "interactions-detected.json"),
            ("scroll-engine.json", "scroll-engine.json"),
        ]:
            results.append(self.check_file(self.ref_dir / filename, label))

        return results

    # Paid-font CDN hostnames — must stay in sync with PAID_FONT_HOSTS in
    # skills/visual-debug/scripts/paid-features-detect.sh. Used both for
    # cross-validation in gate_spec and for the defensive "agent skipped
    # paid-features gate" check below.
    _PAID_FONT_CDN_HOSTS = (
        "use.typekit.net",
        "p.typekit.net",
        "use.edgefonts.net",
        "fast.fonts.net",
        "fast.fonts.com",
        "cloud.typography.com",
        "client.linotype.com",
        "mit.fontplus.jp",
        "webfont.fontplus.jp",
        "typesquare.com",
    )

    def _check_paid_font_substitution(self) -> list[CheckResult]:
        """FAIL early if any paid font is marked decision='substitute' but the
        substitution is not declared in asset-substitution.json.

        Why: 'substitute' is a promise — the agent picked a free family at 5c-c
        and font-parity will verify the swap at runtime. Without an
        asset-substitution.json fonts[] entry, font-parity FAILs much later
        (after Step 7 generation and section-compare have already run). Surfacing
        the missing declaration at spec time saves the wasted generation pass.

        Only paid-features with decision='substitute' are checked. 'use' and
        'skip' do not require asset-substitution.json. Empty findings pass.

        Also defensively flags the "agent skipped the paid-features gate" case:
        when paid-features.json is missing but extraction artifacts (fonts.json,
        head.json) contain known paid CDN hostnames, fail spec gate with a
        pointer to run paid-features-detect.sh.
        """
        results: list[CheckResult] = []
        paid = self._load_json("paid-features.json")
        if not paid:
            # Defensive: if extraction artifacts already prove paid CDNs are
            # in play, the paid-features gate should have run before spec.
            corpus = ""
            for fname in ("fonts.json", "head.json", "external-sdks.json"):
                fp = self.ref_dir / fname
                if fp.is_file():
                    try:
                        corpus += fp.read_text(encoding="utf-8")
                    except OSError:
                        pass
            hits = [h for h in self._PAID_FONT_CDN_HOSTS if h in corpus]
            if hits:
                shown = ", ".join(hits[:3]) + ("..." if len(hits) > 3 else "")
                results.append(
                    CheckResult(
                        "paid-features.json missing",
                        "fail",
                        f"Paid font CDN host(s) detected in extraction artifacts ({shown}) "
                        "but paid-features.json is missing — the `paid-features` gate has not run.",
                        fix=(
                            "Run: bash skills/visual-debug/scripts/paid-features-detect.sh "
                            "$(pwd)/tmp/ref/<component> — then re-run the `paid-features` gate "
                            "before `spec` so substitution decisions are recorded."
                        ),
                    )
                )
            return results

        substitutes = [
            item
            for item in paid.get("paidFonts", [])
            if isinstance(item, dict) and item.get("decision") == "substitute"
        ]
        if not substitutes:
            return results

        sub_path = self.ref_dir / "asset-substitution.json"
        cdns = ", ".join(str(item.get("cdn", "?")) for item in substitutes[:5]) + (
            "..." if len(substitutes) > 5 else ""
        )
        if not sub_path.is_file():
            results.append(
                CheckResult(
                    "paid-font substitution undeclared",
                    "fail",
                    f"{len(substitutes)} paid font(s) marked decision='substitute' "
                    f"({cdns}) but asset-substitution.json is missing.",
                    fix=(
                        "Write tmp/ref/<c>/asset-substitution.json with a fonts[] entry "
                        "for each substituted CDN. See ui-reverse-engineering/asset-substitution.md."
                    ),
                )
            )
            return results

        sub_data = self._load_json("asset-substitution.json")
        fonts = sub_data.get("fonts", []) if sub_data else []
        if not (isinstance(fonts, list) and len(fonts) > 0):
            results.append(
                CheckResult(
                    "paid-font substitution undeclared",
                    "fail",
                    f"asset-substitution.json exists but has no fonts[] entries — "
                    f"{len(substitutes)} paid font(s) marked decision='substitute' "
                    f"({cdns}) need declaration.",
                    fix=(
                        "Add a fonts[] entry to asset-substitution.json for each "
                        "substituted CDN. See ui-reverse-engineering/asset-substitution.md."
                    ),
                )
            )
            return results

        results.append(
            CheckResult(
                "paid-font substitution",
                "pass",
                f"{len(substitutes)} substitute decision(s) declared in asset-substitution.json",
            )
        )
        return results

    def gate_paid_features(self) -> list[CheckResult]:
        """Verify the agent has *consciously decided* what to do about paid fonts.

        Reads tmp/ref/<c>/paid-features.json (produced by paid-features-detect.sh).
        The script greps downloaded bundles + CSS for paid font CDN domains and
        writes findings with `decision: null`.

        For every entry:
          - decision == null  → FAIL (the agent has not made a choice yet)
          - decision == "use"        → PASS (license is in place; agent confirmed)
          - decision == "substitute" → PASS (using a free alternative; downstream
                                        font-parity gate enforces declaration)
          - decision == "skip"       → PASS (intentionally not replicating)

        Why early: catches expensive scope problems BEFORE Step 7 generation.
        Declaring paid-font substitution upfront avoids a section-compare loop
        that can never close (every text-bearing section reports 100% mismatch
        forever when the impl silently falls back to default sans-serif).

        Note: GSAP plugins are no longer checked — GSAP became 100% free
        following the Webflow acquisition. See paid-features-detect.sh header.
        """
        results = []
        path = self.ref_dir / "paid-features.json"
        fix_msg = (
            "Run: bash skills/visual-debug/scripts/paid-features-detect.sh "
            "$(pwd)/tmp/ref/<component>"
        )
        if not path.is_file():
            results.append(
                CheckResult(
                    "paid-features.json",
                    "fail",
                    "paid-features.json — MISSING (paid-features-detect.sh has not been run)",
                    fix=fix_msg,
                )
            )
            return results

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            results.append(
                CheckResult(
                    "paid-features.json",
                    "fail",
                    f"paid-features.json — unreadable ({e})",
                    fix=fix_msg,
                )
            )
            return results

        if not isinstance(data, dict):
            results.append(
                CheckResult(
                    "paid-features.json",
                    "fail",
                    "paid-features.json — must be a JSON object",
                    fix=fix_msg,
                )
            )
            return results

        valid_decisions = {"use", "substitute", "skip"}
        pending: list[str] = []
        invalid: list[str] = []
        total = 0
        items = data.get("paidFonts", [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                total += 1
                name = item.get("family") or item.get("cdn") or "?"
                decision = item.get("decision")
                label = f"paidFont:{name}"
                if decision is None:
                    pending.append(label)
                elif decision not in valid_decisions:
                    invalid.append(f"{label} (decision={decision!r})")

        if total == 0:
            results.append(
                CheckResult(
                    "paid-features",
                    "pass",
                    "No paid fonts detected",
                )
            )
            return results

        if invalid:
            results.append(
                CheckResult(
                    "paid-features decisions",
                    "fail",
                    f"{len(invalid)} item(s) have invalid `decision`: {', '.join(invalid[:5])}"
                    + ("..." if len(invalid) > 5 else ""),
                    fix="Set `decision` to one of: 'use', 'substitute', 'skip'",
                )
            )
            return results

        if pending:
            results.append(
                CheckResult(
                    "paid-features decisions",
                    "fail",
                    f"{len(pending)}/{total} paid item(s) have decision=null: "
                    f"{', '.join(pending[:5])}"
                    + ("..." if len(pending) > 5 else ""),
                    fix=(
                        "Edit paid-features.json — set each `decision` to one of: "
                        "'use' (you have the license), "
                        "'substitute' (using free alternative — must back with asset-substitution.json), "
                        "'skip' (intentionally not replicating). "
                        "Decide BEFORE generation to avoid wasted Step 7 work."
                    ),
                )
            )
            return results

        results.append(
            CheckResult(
                "paid-features",
                "pass",
                f"All {total} paid item(s) have a decision recorded",
            )
        )
        return results

    def gate_spec(self) -> list[CheckResult]:
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
        if spec is not None:
            transitions = spec.get("transitions", [])
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
                missing_keys = [
                    k for k in required_transition_keys if k not in transition
                ]
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

    # ── gate_pre_generate helpers ──

    def _check_webflow(self) -> list[CheckResult]:
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

    def _check_hover_timing(
        self, interactions_data: dict[str, Any]
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

    def _check_transition_coverage(self, spec: dict[str, Any] | None) -> list[CheckResult]:
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

    def _check_section_counts(
        self, section_map: dict[str, Any], component_map: dict[str, Any]
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

    def _check_audit_artifacts(self) -> list[CheckResult]:
        """Check that all 6c audit JSON artifacts are present."""
        results = []
        if (self.ref_dir / "section-map.json").exists():
            for filename, label in [
                ("element-roles.json", "element-roles.json"),
                ("element-groups.json", "element-groups.json"),
                ("layout-decisions.json", "layout-decisions.json"),
                ("component-map.json", "component-map.json"),
            ]:
                results.append(self.check_file(self.ref_dir / filename, label))
        return results

    # ── gate_pre_generate ──

    def gate_pre_generate(self) -> list[CheckResult]:
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

        # Section count cross-check
        section_map = self._load_json("section-map.json")
        component_map = self._load_json("component-map.json")
        if section_map and component_map:
            results.extend(self._check_section_counts(section_map, component_map))

        # Audit artifacts
        results.extend(self._check_audit_artifacts())

        return results

    def gate_post_implement(self) -> list[CheckResult]:
        results = []
        results.append(self.check_file(self.ref_dir / "extracted.json", "extracted.json"))
        results.append(
            self.check_file(self.ref_dir / "transition-spec.json", "transition-spec.json")
        )
        results.append(
            self.check_dir(self.ref_dir / "static" / "ref", "static/ref screenshots", min_files=5)
        )
        results.extend(self._check_verification_plan())
        results.extend(self._check_componentization())
        return results

    def _check_componentization(self) -> list[CheckResult]:
        """Fail when `impl/src/app/page.tsx` > 200 LOC AND `impl/src/components/`
        has < 3 files. The c9b638d benchmark showed the agent could pass every
        other check while shipping a 214-line monolithic page.tsx with no
        per-section components. Splitting first localizes future fixes.

        Skipped silently when:
          - impl/ can't be located (regular tmp/ref/ flow with no co-located impl),
          - page.tsx doesn't exist yet (pre-generation pipelines),
          - page.tsx exists but is small (≤ 200 LOC).
        """
        impl_root = self._find_impl_root()
        if impl_root is None:
            return []
        page = impl_root / "src" / "app" / "page.tsx"
        if not page.is_file():
            return []
        try:
            page_loc = sum(1 for _ in page.open(encoding="utf-8", errors="replace"))
        except OSError:
            return []
        if page_loc <= 200:
            return []
        components_dir = impl_root / "src" / "components"
        if components_dir.is_dir():
            try:
                tsx_files = [
                    p for p in components_dir.rglob("*.tsx")
                    if p.is_file() and not p.name.startswith(".")
                ]
            except OSError:
                tsx_files = []
        else:
            tsx_files = []
        if len(tsx_files) >= 3:
            return []
        return [
            CheckResult(
                "componentization",
                "fail",
                f"impl/src/app/page.tsx is {page_loc} LOC and impl/src/components/ "
                f"has {len(tsx_files)} .tsx file(s) — site is monolithic. The "
                "benchmark surfaced this exact pattern (c9b638d: 214 LOC / 0 "
                "components) producing a wall of inline sections that resists "
                "targeted markup fixes. Split each ref section into its own "
                "file under src/components/ and import them from page.tsx.",
                fix=(
                    "Move each ref section (Hero, StateOfHealth, …) into "
                    "impl/src/components/<Section>.tsx and render via "
                    "`<Section />` in page.tsx. Target ≥ 3 component files and "
                    "page.tsx ≤ 200 lines (mostly imports + layout glue)."
                ),
            )
        ]

    def _find_impl_root(self) -> Path | None:
        """Locate the impl/ root co-located with this ref_dir.

        Convention: `benchmark/work/<sha>/{ref,impl}/` (benchmark flow) or
        `apps/<component>/` (legacy). Returns the impl ROOT (containing src/
        and public/), not impl/public/. None when no candidate exists.
        """
        candidates = [
            self.ref_dir.parent / "impl",                                 # benchmark/work/<sha>/impl
            self.ref_dir.parent.parent / "apps" / self.ref_dir.name,       # apps/<component>/
            self.ref_dir.parent.parent / "apps" / self.ref_dir.name / "app",
        ]
        for c in candidates:
            if c.is_dir() and (c / "src").is_dir():
                return c
        return None

    def _check_verification_plan(self) -> list[CheckResult]:
        """Honor tmp/ref/<c>/verification-plan.json — declared site-specific checks.

        Schema:
          { "schemaVersion": 1,
            "requiredChecks": [{
              "id": "<short-id>",
              "script": "<path/to/script.sh>",
              "produces": "<artifact relative to ref-dir>",
              "reason": "<why required>",
              "severity": "block" | "warn"
            }] }

        Missing file → returns []. Each required check artifact must exist
        and contain `"status": "pass"`. Severity "warn" emits a warning that
        does not block; "block" (default) fails the gate.
        """
        plan_path = self.ref_dir / "verification-plan.json"
        if not plan_path.is_file():
            return []

        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return [
                CheckResult(
                    "verification-plan.json",
                    "warn",
                    f"verification-plan.json — unreadable ({e}); skipping",
                )
            ]

        schema_version = plan.get("schemaVersion")
        vp_fix = "Run: bash skills/visual-debug/scripts/verification-plan.sh <ref-dir>"
        if "schemaVersion" not in plan:
            # Hand-written / hallucinated verification-plan.json (e.g. agent
            # inventing {component, checks} keys instead of running
            # verification-plan.sh) used to slip through as a silent warn —
            # making every declared required check unenforceable. Hard-fail
            # when no version is declared so the agent must actually run the
            # script. (Known future versions still degrade gracefully below.)
            return [
                CheckResult(
                    "verification-plan.json",
                    "fail",
                    "verification-plan.json — missing `schemaVersion`. "
                    "The file is hand-written; declared checks would be silently ignored.",
                    fix=vp_fix,
                )
            ]
        if schema_version != 1:
            return [
                CheckResult(
                    "verification-plan.json",
                    "warn",
                    f"verification-plan.json — schemaVersion {schema_version!r} not supported; ignoring",
                )
            ]

        if "requiredChecks" not in plan:
            return [
                CheckResult(
                    "verification-plan.json",
                    "fail",
                    "verification-plan.json — missing `requiredChecks` key "
                    "(wrong schema; required by verification-plan.sh output).",
                    fix=vp_fix,
                )
            ]
        checks = plan.get("requiredChecks") or []
        if not isinstance(checks, list):
            return [
                CheckResult(
                    "verification-plan.json",
                    "fail",
                    f"verification-plan.json — `requiredChecks` must be a list, "
                    f"got {type(checks).__name__}.",
                    fix=vp_fix,
                )
            ]
        if not checks:
            # Empty list is rare-but-legitimate (static site with no JS/scroll
            # signals). Surface it as a warn so the operator sees that NO
            # site-specific checks fired, rather than silently passing.
            return [
                CheckResult(
                    "verification-plan.json",
                    "warn",
                    "verification-plan.json — `requiredChecks` is empty "
                    "(verification-plan.sh detected no site-specific checks).",
                )
            ]

        out: list[CheckResult] = []
        for entry in checks:
            if not isinstance(entry, dict):
                continue
            check_id = str(entry.get("id") or "?")
            produces = entry.get("produces")
            script = entry.get("script") or ""
            reason = entry.get("reason") or ""
            severity = entry.get("severity") or "block"

            if not produces:
                continue
            artifact = self.ref_dir / produces
            label = f"required: {check_id}"
            fix = f"Run: bash {script}" if script else ""

            if not artifact.is_file():
                msg = f"{check_id} — produces artifact missing ({produces}). Reason: {reason}"
                if severity == "warn":
                    out.append(CheckResult(label, "warn", msg))
                else:
                    out.append(CheckResult(label, "fail", msg, fix=fix))
                continue

            try:
                raw = artifact.read_text(encoding="utf-8")
            except OSError:
                out.append(CheckResult(label, "pass", f"{check_id} (artifact unreadable)"))
                continue

            # If artifact is JSON with a `status` field, enforce status: "pass".
            # Non-JSON artifacts (e.g. transitions/result.txt) are scanned for
            # ❌ FAIL markers — presence-only would let real failures slip past
            # this gate when section-compare's dedicated parser only watches
            # sections/result.txt, not transitions/result.txt.
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                fail_lines = sum(1 for line in raw.splitlines() if "❌" in line)
                if fail_lines > 0:
                    msg = f"{check_id} — {fail_lines} FAIL line(s) in {produces}. Reason: {reason}"
                    if severity == "warn":
                        out.append(CheckResult(label, "warn", msg))
                    else:
                        out.append(CheckResult(label, "fail", msg, fix=fix))
                else:
                    # transition-compare / video-motion-compare / hover-state /
                    # keyframes-diff etc — when transition-spec declares any
                    # transitions, the corresponding result.txt MUST contain
                    # actual measurement rows (✅ or ❌). An empty / near-empty
                    # text artifact is the "transition-compare never actually
                    # ran" gaming pattern (vacuous PASS because no ❌ exists).
                    if check_id in {
                        "transition-compare",
                        "video-motion-compare",
                        "hover-state-compare",
                        "keyframes-diff",
                        "scroll-anim-temporal",
                    }:
                        measurement_rows = sum(
                            1 for line in raw.splitlines()
                            if ("✅" in line or "❌" in line)
                            and "result:" not in line.lower()
                        )
                        spec_has_transitions = self._transition_spec_count() > 0
                        if spec_has_transitions and measurement_rows == 0:
                            msg = (
                                f"{check_id} — {produces} contains 0 measurement rows "
                                f"(no ✅/❌ lines) despite transition-spec.json declaring "
                                f"{self._transition_spec_count()} transition(s). The check "
                                f"didn't actually run."
                            )
                            if severity == "warn":
                                out.append(CheckResult(label, "warn", msg))
                            else:
                                out.append(CheckResult(label, "fail", msg, fix=fix))
                            continue
                    out.append(CheckResult(label, "pass", f"{check_id} (text artifact, no FAIL markers)"))
                continue

            status = data.get("status") if isinstance(data, dict) else None
            # tree-diff floor — a `status=pass` with `elements_walked` below
            # the floor is the 5199dd9 gaming pattern: agent ships a near-
            # empty impl (11 elements walked vs ref's 200) and tree-diff
            # vacuously passes "0 critical mismatches" because there's
            # nothing to mismatch. Floor cross-references section-map.json
            # so a 4-section page doesn't trip the gate.
            if (
                check_id == "tree-diff"
                and status == "pass"
                and isinstance(data, dict)
            ):
                walked = int(data.get("elements_walked") or 0)
                floor = self._tree_diff_floor()
                if walked < floor:
                    msg = (
                        f"tree-diff — only {walked} elements walked (floor: {floor}). "
                        f"This is the 'near-empty impl' gaming pattern: with so few "
                        f"elements to pair, tree-diff vacuously reports 0 mismatches. "
                        f"Generate real impl content."
                    )
                    out.append(CheckResult(label, "fail", msg, fix=fix))
                    continue
            if status == "pass":
                out.append(CheckResult(label, "pass", f"{check_id} (status: pass)"))
            elif status is None:
                out.append(CheckResult(label, "pass", f"{check_id} (artifact present, no status field)"))
            else:
                count = (data.get("errorCount") or data.get("failureCount") or
                         data.get("totalStuck") or "?") if isinstance(data, dict) else "?"
                msg = f"{check_id} — status: {status} ({count} issue(s)). Reason: {reason}"
                if severity == "warn":
                    out.append(CheckResult(label, "warn", msg))
                else:
                    out.append(CheckResult(label, "fail", msg, fix=fix))

        return out

    def _transition_spec_count(self) -> int:
        """Number of declared transitions in transition-spec.json.

        Used to gate "transition-compare must have measurement rows" — only
        applies when the spec actually declared transitions to compare.
        """
        spec = self.ref_dir / "transition-spec.json"
        if not spec.is_file():
            return 0
        try:
            data = json.loads(spec.read_text(encoding="utf-8"))
            transitions = data.get("transitions") if isinstance(data, dict) else None
            if isinstance(transitions, list):
                return len(transitions)
        except (json.JSONDecodeError, OSError):
            pass
        return 0

    def _tree_diff_floor(self) -> int:
        """Minimum elements_walked tree-diff must achieve to be meaningful.

        Cross-reference section-map.json. The floor is max(30, sections * 5):
        a real page averages ≥5 visible elements per section (heading, sub-
        head, paragraph, button, image at minimum). Below 30 absolute, any
        page is too sparse to call tree-diff a real measurement.
        """
        section_map = self.ref_dir / "section-map.json"
        section_count = 0
        if section_map.is_file():
            try:
                data = json.loads(section_map.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    sections = data.get("sections") or []
                    if isinstance(sections, list):
                        section_count = len(sections)
                elif isinstance(data, list):
                    section_count = len(data)
            except (json.JSONDecodeError, OSError):
                section_count = 0
        return max(30, section_count * 5)

    def gate_boundary(self) -> list[CheckResult]:
        """Check that breakpoint-collision-check.sh has been run and reports no collisions.

        Reads tmp/ref/<c>/responsive/boundary-collisions.json — must exist and be `[]`.
        Catches the Tailwind ↔ project @media boundary collision class
        (see diagnosis.md → Root Cause J). The bug only manifests at exactly the
        breakpoint width and Step 4-C2 measurements happen to land on those widths,
        so it never appears as a sweep change — only an isolated overflow spike.
        """
        results = []
        path = self.ref_dir / "responsive" / "boundary-collisions.json"
        fix_msg = (
            "Run: bash skills/visual-debug/scripts/breakpoint-collision-check.sh "
            "<session> <impl-url>"
        )
        if not path.is_file():
            results.append(
                CheckResult(
                    "responsive/boundary-collisions.json",
                    "fail",
                    "responsive/boundary-collisions.json — MISSING (breakpoint-collision-check.sh has not been run)",
                    fix=fix_msg,
                )
            )
            return results

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            results.append(
                CheckResult(
                    "responsive/boundary-collisions.json",
                    "fail",
                    f"responsive/boundary-collisions.json — unreadable ({e})",
                    fix=fix_msg,
                )
            )
            return results

        if not isinstance(data, list):
            results.append(
                CheckResult(
                    "responsive/boundary-collisions.json",
                    "fail",
                    "responsive/boundary-collisions.json — must be a JSON array",
                    fix=fix_msg,
                )
            )
            return results

        if not data:
            results.append(
                CheckResult(
                    "responsive/boundary-collisions.json",
                    "pass",
                    "No breakpoint collisions detected",
                )
            )
            return results

        bp_summary = ", ".join(str(d.get("bp", "?")) for d in data if isinstance(d, dict))
        results.append(
            CheckResult(
                "boundary collisions",
                "fail",
                f"{len(data)} breakpoint collision(s) detected at: {bp_summary}. "
                "See diagnosis.md → Root Cause J for fix patterns.",
                fix=(
                    "Pick ONE side: (A) shift project @media to (max-width: <bp - 0.02>px), "
                    "or (B) bump Tailwind variant up one tier (md: → lg:). "
                    "Re-run breakpoint-collision-check.sh until the array is []."
                ),
            )
        )
        return results

    def gate_font_parity(self) -> list[CheckResult]:
        """Check that the impl loads the same font as the ref, OR that the substitution is declared.

        Reads tmp/ref/<c>/font-parity.json (produced by font-parity-check.sh).
        - parity: "match" → PASS.
        - parity: "mismatch" + asset-substitution.json with at least one font entry → PASS (declared).
        - parity: "mismatch" + no asset-substitution.json → FAIL.

        Catches the class of bug where commercial-font substitution makes section-compare
        report 100% FAIL forever because every section renders the substituted asset.
        See asset-substitution.md.
        """
        results = []
        path = self.ref_dir / "font-parity.json"
        fix_msg = (
            "Run: bash skills/visual-debug/scripts/font-parity-check.sh "
            "<session> <ref-url> <impl-url> $(pwd)/tmp/ref/<component>"
        )
        if not path.is_file():
            results.append(
                CheckResult(
                    "font-parity.json",
                    "fail",
                    "font-parity.json — MISSING (font-parity-check.sh has not been run)",
                    fix=fix_msg,
                )
            )
            return results

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            results.append(
                CheckResult(
                    "font-parity.json",
                    "fail",
                    f"font-parity.json — unreadable ({e})",
                    fix=fix_msg,
                )
            )
            return results

        if not isinstance(data, dict):
            results.append(
                CheckResult(
                    "font-parity.json",
                    "fail",
                    "font-parity.json — must be a JSON object",
                    fix=fix_msg,
                )
            )
            return results

        parity = data.get("parity")
        ref_obj = data.get("ref") or {}
        impl_obj = data.get("impl") or {}
        if parity == "match":
            # Silent-fallback guard: ref and impl declare the same family, but the
            # impl's FontFace failed to load (paid font 404'd, expired Typekit ID,
            # CORS-blocked). computedStyle.fontFamily lies in this case — we use
            # document.fonts.check() result captured by font-parity-check.sh.
            ref_loaded = ref_obj.get("loaded", True)
            impl_loaded = impl_obj.get("loaded", True)
            family = (impl_obj.get("family") or ref_obj.get("family") or "?")
            if not ref_loaded and not impl_loaded:
                # Both sides report the FontFace is not loaded. The parity result
                # is meaningless — neither side is actually rendering the declared
                # family, so any "match" is matching two fallbacks. Could be a
                # transient network issue (re-run) or a real config bug (paid
                # font CDN unreachable from both deploys).
                results.append(
                    CheckResult(
                        "font load failure (both sides)",
                        "fail",
                        f"Both ref and impl declare '{family}' but neither has the "
                        "FontFace actually loaded — both are rendering with a fallback. "
                        "The parity 'match' is between two fallbacks, not the declared font.",
                        fix=(
                            "Re-run font-parity-check.sh with WAIT_MS bumped (slow networks "
                            "may not resolve the FontFace within 2.5s). If the failure persists, "
                            "the declared font CDN is unreachable — fix the source, or substitute "
                            "and declare via asset-substitution.json."
                        ),
                    )
                )
                return results
            if ref_loaded and not impl_loaded:
                results.append(
                    CheckResult(
                        "font load failure",
                        "fail",
                        f"Impl declares '{family}' (matches ref) but the FontFace is NOT actually loaded "
                        "— browser is silently rendering with a fallback. Likely causes: 404, CORS, "
                        "expired Typekit/Adobe Fonts ID, or missing license file in deploy.",
                        fix=(
                            "Open DevTools → Network → filter 'font' on the impl URL. "
                            "Look for failed font requests. Either: (A) fix the loading issue "
                            "(restore @font-face, add CDN auth, refresh Typekit kit ID), "
                            "or (B) intentionally substitute and declare it in asset-substitution.json."
                        ),
                    )
                )
                return results
            results.append(CheckResult("font-parity", "pass", "Ref and impl load the same primary font"))
            return results

        if parity != "mismatch":
            results.append(
                CheckResult(
                    "font-parity.json",
                    "fail",
                    f"font-parity.json — `parity` must be 'match' or 'mismatch' (got {parity!r})",
                    fix=fix_msg,
                )
            )
            return results

        # Mismatch — must be acknowledged via asset-substitution.json
        sub_path = self.ref_dir / "asset-substitution.json"
        ref_family = (data.get("ref") or {}).get("family", "?")
        impl_family = (data.get("impl") or {}).get("family", "?")
        if not sub_path.is_file():
            results.append(
                CheckResult(
                    "font substitution undeclared",
                    "fail",
                    f"Ref loads '{ref_family}' but impl loads '{impl_family}'. "
                    "If this is intentional (e.g. commercial-font replacement), declare it in "
                    "asset-substitution.json. Otherwise fix the impl to load the original font.",
                    fix=(
                        "Either: (A) fix impl to load the same font as ref, "
                        "or (B) write tmp/ref/<c>/asset-substitution.json per asset-substitution.md "
                        "with a fonts[] entry covering the substitution."
                    ),
                )
            )
            return results

        try:
            sub_data = json.loads(sub_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            results.append(
                CheckResult(
                    "asset-substitution.json",
                    "fail",
                    f"asset-substitution.json — unreadable ({e})",
                )
            )
            return results

        fonts = sub_data.get("fonts", []) if isinstance(sub_data, dict) else []
        if not (isinstance(fonts, list) and len(fonts) > 0):
            results.append(
                CheckResult(
                    "font substitution undeclared",
                    "fail",
                    f"asset-substitution.json exists but has no fonts[] entry. "
                    f"Ref loads '{ref_family}', impl loads '{impl_family}'.",
                    fix=(
                        "Add a fonts[] entry to asset-substitution.json describing the substitution, "
                        "or fix the impl to load the original font."
                    ),
                )
            )
            return results

        results.append(
            CheckResult(
                "font-parity",
                "pass",
                f"Font mismatch declared in asset-substitution.json ({ref_family} → {impl_family})",
            )
        )
        return results

    def gate_section_compare(self) -> list[CheckResult]:
        """Check that section-compare.sh has been run and all sections passed.

        Honors tmp/ref/<c>/known-artifacts.json: per-section FAILs whose entries
        validate (required fields present, AE within ceiling × 1.5) are
        downgraded to PASS in the gate's output. result.txt is never modified.
        Emits an advisory warning if more than 30% of sections are marked.
        """
        results = []
        result_file = self.ref_dir / "sections" / "result.txt"
        if not result_file.is_file():
            results.append(
                CheckResult(
                    "sections/result.txt",
                    "fail",
                    "sections/result.txt — MISSING (skills/visual-debug/scripts/section-compare.sh has not been run)",
                    fix=(
                        f"Run: bash skills/visual-debug/scripts/section-compare.sh "
                        f"<orig-url> <impl-url> <session> {self.ref_dir}"
                    ),
                )
            )
            return results

        content = result_file.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        failed_sections = _parse_failed_sections(lines)
        missing_count = sum(1 for ln in lines if "⚠️ MISSING impl" in ln)

        # SECTION_THRESHOLD gaming detector — d19e28d benchmark exposed an
        # agent setting SECTION_THRESHOLD=250000 (vs default 2000) so that
        # AE/Mpx of 88K/228K — nominally `critical` (>20K) — were re-classified
        # as `minor` and ✅ PASSed. result.txt records both severity AND
        # AE/Mpx; the canonical bands are ok≤500, minor≤2000, major≤20000,
        # critical>20000. If we see a row labeled `minor` (or `ok`) whose
        # AE/Mpx exceeds 2000, the threshold was inflated. Flag this as a
        # gaming attempt — operators should either (a) re-run via
        # `python -m ui_clone.measure section-compare` which locks the
        # threshold, or (b) declare asset-substitution for the affected
        # sections rather than tuning the classifier.
        threshold_gaming: list[tuple[str, int, str]] = []
        _CANON_MINOR_CAP = 2000  # AE/Mpx, mirrors section-compare.sh default
        for ln in lines:
            if not ln.startswith("|"):
                continue
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if len(cells) < 5:
                continue
            name, _ae, mpx, sev, _status = cells[0], cells[1], cells[2], cells[3], cells[4]
            if name.lower() == "section" or "---" in name:
                continue
            if mpx in ("", "—", "-"):
                continue
            try:
                mpx_n = int(mpx)
            except ValueError:
                continue
            if sev in ("ok", "minor") and mpx_n > _CANON_MINOR_CAP:
                threshold_gaming.append((name, mpx_n, sev))

        # Apply known-artifacts.json downgrades.
        artifact_path = self.ref_dir / "known-artifacts.json"
        downgraded: list[tuple[str, str]] = []  # (section_name, reason)
        rejected: list[tuple[str, str]] = []    # (section_name, why_rejected)
        coverage_warning = ""

        if artifact_path.is_file():
            try:
                artifact_data = json.loads(artifact_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                results.append(
                    CheckResult(
                        "known-artifacts.json",
                        "warn",
                        f"known-artifacts.json — unreadable ({e}); ignoring",
                    )
                )
                artifact_data = None

            if isinstance(artifact_data, dict):
                schema_version = artifact_data.get("schemaVersion")
                if schema_version != 1:
                    results.append(
                        CheckResult(
                            "known-artifacts.json",
                            "warn",
                            f"known-artifacts.json — schemaVersion {schema_version!r} not supported; ignoring",
                        )
                    )
                else:
                    entries = artifact_data.get("sections") or []
                    section_ae = {name: ae for name, ae in failed_sections}
                    seen_names: set[str] = set()
                    for entry in entries if isinstance(entries, list) else []:
                        if not isinstance(entry, dict):
                            continue
                        ok, why, name = _validate_artifact_entry(entry, section_ae)
                        if name in seen_names:
                            continue
                        seen_names.add(name)
                        if ok:
                            downgraded.append((name, entry.get("verifiedBy", "?")))
                        elif name in section_ae:
                            rejected.append((name, why))

                    # Coverage advisory: >30% of sections marked is suspicious.
                    total_sections = sum(
                        1 for ln in lines
                        if ln.startswith("| ") and "---" not in ln and "Section" not in ln
                    )
                    if total_sections > 0:
                        cov = len(downgraded) / total_sections
                        if cov > 0.30:
                            coverage_warning = (
                                f"known-artifacts.json marks {len(downgraded)}/{total_sections} "
                                f"({cov:.0%}) of sections as artifacts. Above the 30% advisory "
                                "threshold — re-verify manual-frame-cmp entries."
                            )

        downgraded_names = {name for name, _ in downgraded}
        effective_fails = [
            (name, ae) for name, ae in failed_sections if name not in downgraded_names
        ]
        effective_fail_count = len(effective_fails)

        # STRUCTURAL_ONLY override — a section marked STRUCTURAL_ONLY in
        # result.txt (asset-substitution skips AE/SSIM) is still gated on
        # structure-diff.json. Block when EITHER:
        #   (a) severity == "critical" (DISPLAY_MISMATCH, ratio < 0.05 etc.), OR
        #   (b) severity == "major" AND HEIGHT_MISMATCH ratio < 0.5
        # The 077d8c3 benchmark exposed (b) — section-0 ratio=0.35 (impl
        # 6955px vs ref 19954px = 65% of content missing) was classified
        # `major`, slipped past the prior `critical`-only guard, and a stub
        # clone was marked DONE. Anything under half the reference height is
        # not a substitution; it's a regression. The pixel-bypass is for
        # legitimate font/image substitution, not for content disappearance.
        _ratio_re = re.compile(r"ratio=([0-9.]+)")
        critical_structural: list[str] = []
        diff_path = self.ref_dir / "sections" / "structure-diff.json"
        if diff_path.is_file():
            try:
                diff_data = json.loads(diff_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                diff_data = None
            blocked_section_names: set[str] = set()
            if isinstance(diff_data, list):
                for entry in diff_data:
                    if not isinstance(entry, dict):
                        continue
                    severity = entry.get("severity")
                    diff_section = entry.get("section")
                    if not isinstance(diff_section, str):
                        continue
                    if severity == "critical":
                        blocked_section_names.add(diff_section)
                        continue
                    if severity == "major":
                        issues = entry.get("issues") or []
                        if not isinstance(issues, list):
                            continue
                        min_ratio: float | None = None
                        for issue in issues:
                            if not isinstance(issue, str):
                                continue
                            m = _ratio_re.search(issue)
                            if m:
                                try:
                                    r = float(m.group(1))
                                except ValueError:
                                    continue
                                if min_ratio is None or r < min_ratio:
                                    min_ratio = r
                        if min_ratio is not None and min_ratio < 0.5:
                            blocked_section_names.add(diff_section)
            if blocked_section_names:
                for ln in lines:
                    if not ln.startswith("|") or "STRUCTURAL_ONLY" not in ln:
                        continue
                    cells = [p.strip() for p in ln.split("|")]
                    if len(cells) < 3:
                        continue
                    row_name = cells[1]
                    if row_name in blocked_section_names:
                        critical_structural.append(row_name)

        # STRUCTURAL_ONLY ratio cap — `asset-substitution.json` is a legitimate
        # escape hatch for one or two sections that use commercial fonts /
        # licensed imagery. The 5199dd9 benchmark exposed a gaming pattern
        # where the agent marked ALL 9 sections as substituted, getting a
        # "9 PASS, 9 STRUCTURAL_ONLY" verdict with zero pixel measurement.
        # Treat substitution above 50% of sections as an obvious bypass.
        structural_only_count = sum(
            1 for ln in lines
            if ln.startswith("|") and "STRUCTURAL_ONLY" in ln
        )
        total_section_rows = sum(
            1 for ln in lines
            if ln.startswith("| ")
            and "---" not in ln
            and "Section" not in ln
            and ln.strip() != "|"
        )
        structural_only_excess = (
            total_section_rows > 0
            and structural_only_count >= 3
            and (structural_only_count / total_section_rows) > 0.5
        )

        if (
            effective_fail_count == 0
            and missing_count == 0
            and not threshold_gaming
            and not critical_structural
            and not structural_only_excess
        ):
            if downgraded:
                msg = f"All sections PASS ({len(downgraded)} known artifact(s) downgraded)"
            else:
                msg = "All sections PASS"
            results.append(CheckResult("sections/result.txt", "pass", msg))
        else:
            if effective_fail_count > 0:
                # Tiered escalation: cheap auto-diagnose → tree-diff (style) →
                # layout-tree-diff (position) → hover-tree-diff (state). The
                # ad-hoc escalation tools live in skills/visual-debug/scripts/
                # but are not gate-dispatched — naming them in the fail message
                # gives the agent a concrete next-step instead of "fix diffs".
                # See SKILL.md "L3 → L4 escalation" table for the symptom map.
                results.append(
                    CheckResult(
                        "section failures",
                        "fail",
                        f"{effective_fail_count} section(s) FAILED — fix diffs in "
                        f"{self.ref_dir}/sections/diff/ and re-run section-compare",
                        fix=(
                            "Escalation ladder when AE keeps failing:\n"
                            "  1. bash skills/visual-debug/scripts/auto-diagnose.sh "
                            f"<session> <orig> <impl> {self.ref_dir}\n"
                            "     (locates hotspot elements via pixel clustering)\n"
                            "  2. bash skills/visual-debug/scripts/tree-diff.sh "
                            "<session> <orig> <impl>\n"
                            "     (when auto-diagnose finds nothing: walks every "
                            "visible element ≥ MIN_SIZE px, pairs by elementFromPoint, "
                            "diffs computed style)\n"
                            "  3. bash skills/visual-debug/scripts/layout-tree-diff.sh "
                            "<session> <orig> <impl>\n"
                            "     (when tree-diff style matches but element looks "
                            "misplaced: signature-based pairing reports top/left/w/h "
                            "delta regardless of reflow)\n"
                            "  4. bash skills/visual-debug/scripts/hover-tree-diff.sh "
                            "<session> <orig> <impl>\n"
                            "     (when sections look static-correct but feel off: "
                            "every hover-capable pair, idle → CDP :hover → settled)\n"
                            "  5. bash skills/visual-debug/scripts/dssim-compare.sh "
                            f"{self.ref_dir}\n"
                            "     (structural similarity sanity check — catches "
                            "AE/SSIM disagreement = real layout issue vs sampling noise)"
                        ),
                    )
                )
            if structural_only_excess:
                pct = round(100 * structural_only_count / total_section_rows)
                results.append(
                    CheckResult(
                        "structural-only excess",
                        "fail",
                        f"{structural_only_count}/{total_section_rows} sections ({pct}%) "
                        f"are STRUCTURAL_ONLY — asset-substitution.json is being used to "
                        f"bypass section-compare entirely, not for legitimate font/image "
                        f"substitution. Cap is 50%.",
                        fix=(
                            "Trim asset-substitution.json to only the sections that actually "
                            "use commercial fonts / licensed imagery. The rest must pass "
                            "real AE measurement. If the impl genuinely can't match those "
                            "sections, the fix is to implement them — not to declare them "
                            "structurally-only-comparable."
                        ),
                    )
                )
            if threshold_gaming:
                gamed = ", ".join(
                    f"{n} (AE/Mpx={mpx}, labeled {sev})" for n, mpx, sev in threshold_gaming[:5]
                )
                more = f" + {len(threshold_gaming) - 5} more" if len(threshold_gaming) > 5 else ""
                results.append(
                    CheckResult(
                        "section-threshold gaming",
                        "fail",
                        f"{len(threshold_gaming)} section(s) labeled ok/minor with AE/Mpx > 2000 "
                        f"— SECTION_THRESHOLD was inflated to bypass the classifier: {gamed}{more}",
                        fix=(
                            "Re-run section-compare via `python -m ui_clone.measure "
                            "section-compare <ref-dir> ...` which locks SECTION_THRESHOLD=2000, "
                            "OR declare asset-substitution.json for the affected sections "
                            "rather than inflating the threshold."
                        ),
                    )
                )
            if critical_structural:
                results.append(
                    CheckResult(
                        "structural-only critical override",
                        "fail",
                        f"{len(critical_structural)} STRUCTURAL_ONLY section(s) have critical "
                        f"structure-diff severity and cannot be substituted: "
                        f"{', '.join(critical_structural)}",
                        fix=(
                            "Fix impl layout to match ref (display/height) or remove "
                            "these sections from asset-substitution.json — the "
                            "STRUCTURAL_ONLY bypass is for asset/font substitution, "
                            "NOT for layout regressions."
                        ),
                    )
                )
            if missing_count > 0:
                results.append(
                    CheckResult(
                        "missing sections",
                        "fail",
                        f"{missing_count} section(s) MISSING impl — implement them and re-run section-compare",
                    )
                )
            if downgraded:
                results.append(
                    CheckResult(
                        "known-artifacts downgrades",
                        "pass",
                        f"{len(downgraded)} section(s) downgraded via known-artifacts.json: "
                        + ", ".join(name for name, _ in downgraded),
                    )
                )

        for name, reason in rejected:
            results.append(
                CheckResult(
                    f"known-artifact:{name}",
                    "warn",
                    f"{name} — known-artifacts.json entry rejected: {reason}",
                )
            )

        if coverage_warning:
            results.append(CheckResult("known-artifacts coverage", "warn", coverage_warning))

        return results

    # ── Dispatch ──

    def _make_dispatch(self) -> dict[str, Any]:
        """Build {gate_name: bound_method} from state.GATE_ORDER.

        Method names follow the convention `gate_<name>` with `-` → `_`. The
        import-time validator below ensures every gate in GATE_ORDER has a
        matching method, so getattr() here cannot raise at runtime.
        """
        return {gate: getattr(self, _gate_method_name(gate)) for gate in _state.GATE_ORDER}

    def _dispatch(self, gate: str) -> list[CheckResult]:
        dispatch = self._make_dispatch()
        if gate == "all":
            results = []
            for fn in dispatch.values():
                results.extend(fn())
            return results
        if gate not in dispatch:
            return []
        return list(dispatch[gate]())

    def _render_text(self, results: list[CheckResult]) -> None:
        for r in results:
            if r.status == "pass":
                print(f"  {_GREEN}\u2713{_NC} {r.message}")
            elif r.status == "fail":
                print(f"  {_RED}\u2717{_NC} {r.message}")
                if r.fix:
                    print(f"    \u2192 {r.fix}")
            else:  # warn
                print(f"  {_YELLOW}\u26a0{_NC}  {r.message}")

    def _render_json(self, results: list[CheckResult]) -> None:
        failures = [
            {"label": r.label, "reason": r.message, "fix": r.fix}
            for r in results
            if r.status == "fail"
        ]
        output = {
            "passed": len(failures) == 0,
            "fail_count": len(failures),
            "warn_count": sum(1 for r in results if r.status == "warn"),
            "pass_count": sum(1 for r in results if r.status == "pass"),
            "failures": failures,
        }
        print(json.dumps(output, ensure_ascii=False))

    def run(self, gate: str, json_output: bool = False) -> int:
        """Run gate checks. Returns 0=PASS, 1=BLOCKED, 2=usage error."""
        if gate not in VALID_GATES:
            if json_output:
                print(json.dumps({"error": f"Unknown gate: {gate}", "valid": VALID_GATES}))
            else:
                print(f"Unknown gate: {gate}")
                print(f"Valid gates: {' | '.join(VALID_GATES)}")
            return 2

        if not json_output:
            print(f"Gate: {gate}")

        results = self._dispatch(gate)

        if json_output:
            self._render_json(results)
        else:
            self._render_text(results)
            fail_count = sum(1 for r in results if r.status == "fail")
            total = len(results)
            print()
            if fail_count > 0:
                print(
                    f"{_RED}BLOCKED{_NC}: {fail_count}/{total} checks failed. Fix before proceeding."
                )
            else:
                print(f"{_GREEN}PASS{_NC}: {total}/{total} checks passed. May proceed.")

        passed = not any(r.status == "fail" for r in results)

        # Record gate result in pipeline-state.json. Skip "all" (composite run).
        # PASS resets the consecutive-fail counter for this gate inside
        # mark_passed; BLOCKED increments it inside mark_failed when this gate
        # is the active one. The counter is what the goal card uses to surface
        # "STUCK after N — read diagnosis.md" so loop drivers don't grind.
        if gate != "all":
            try:
                ps = _state.PipelineState.load(self.ref_dir)
                if passed:
                    ps.mark_passed(gate, self.ref_dir)
                else:
                    ps.mark_failed(gate, self.ref_dir)
            except OSError:
                pass  # Non-fatal — state tracking is best-effort

        return 0 if passed else 1


# Validate at import: every gate in state.GATE_ORDER has a matching `gate_<name>`
# method on Gate. Catches drift the moment a gate is added to GATE_ORDER without
# a corresponding implementation (or vice versa), with no runtime overhead.
_missing_methods = [
    gate for gate in _state.GATE_ORDER
    if not callable(getattr(Gate, _gate_method_name(gate), None))
]
if _missing_methods:
    raise RuntimeError(
        f"Gate methods missing for state.GATE_ORDER entries: {_missing_methods}. "
        f"Expected method name: gate_<name> with '-' replaced by '_'."
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate ui-clone-skills pipeline gate",
        usage="python -m ui_clone.gate <ref-dir> <gate> [--json]",
    )
    parser.add_argument("ref_dir", type=Path)
    parser.add_argument("gate", choices=VALID_GATES)
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output structured JSON instead of colored text",
    )
    args = parser.parse_args()

    gate = Gate(args.ref_dir)
    sys.exit(gate.run(args.gate, json_output=args.json_output))


if __name__ == "__main__":
    main()
