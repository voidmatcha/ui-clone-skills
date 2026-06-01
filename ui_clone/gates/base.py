"""Gate base class + shared module-level constants, parsers, and validators.

The per-gate modules (reference.py, extraction.py, …) define bare
functions taking `self: "Gate"`. `ui_clone.gates.__init__` rebinds them
onto the Gate class so callers can keep using `Gate(ref_dir).gate_*()`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, Literal

from ui_clone.hooks._common import load_json_safe as _load_json_safe


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
    """Extract (section_name, ae) pairs from sections/result.txt FAIL lines.

    result.txt is a markdown table:
        | <name> | <ae> | <ae/mpx> | <severity> | ❌ |   (critical fail)
        | <name> | <ae> | <ae/mpx> | saturated | 🌑 |   (gradient-dead fail)
    Saturated-row accounting fix: `🌑 saturated` rows are FAIL_COUNT
    increments in section-compare.sh (AE/Mpx ≥ 800k, "gradient dead, not
    comparable") but `_parse_failed_sections` was only catching `❌`. That
    let known-artifacts.json downgrade saturated rows to a structural pass
    even though section-compare itself treated them as failures —
    a silent bypass that produced a persistent AE envelope across loops.
    Counting `🌑` lines as fail aligns the gate's accounting with the
    producer's. Names missing or AE unparseable are still returned
    (with AE=0) so we don't silently drop failures.
    """
    out: list[tuple[str, int]] = []
    for ln in lines:
        if not ln.startswith("|"):
            continue
        if "❌" not in ln and "🌑" not in ln:
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
    # Paid-font CDN hostnames — must stay in sync with PAID_FONT_HOSTS in
    # skills/visual-debug/scripts/paid-features-detect.sh. Used both for
    # cross-validation in gate_spec and for the defensive "agent skipped
    # paid-features gate" check.
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
                f"{label} — directory exists but only {len(matched)} files (need ≥{min_files})",
            )
        return CheckResult(label, "pass", f"{label} (≥{min_files} files)")

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

    def _load_json_any(self, filename: str) -> Any:
        """Load a JSON artifact, preserving a top-level list OR dict.

        Unlike `_load_json` (which coerces non-dict roots to None),
        sticky-elements.json and several bundle/sdk artifacts are top-level
        lists. Returns None when missing or malformed."""
        path = self.ref_dir / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

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

    # ── Methods rebound at import-time by ui_clone.gates.__init__ ──
    # Declared here as stubs so mypy can resolve cross-method references like
    # `self._dispatch(...)` inside dispatch.py or `self._check_paid_font_substitution()`
    # inside spec.py. The rebinding happens once at package import; calling
    # the stubs would mean __init__ never ran (which raises at import time
    # via the drift validator, so callers never reach these stubs).

    def _make_dispatch(self) -> dict[str, Any]: ...  # type: ignore[empty-body]

    def _dispatch(self, gate: str) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def _check_pipeline_state_prerequisites(
        self, gate: str
    ) -> CheckResult | None: ...

    def _render_text(self, results: list[CheckResult]) -> None: ...

    def _render_json(self, results: list[CheckResult]) -> None: ...

    def run(self, gate: str, json_output: bool = False) -> int: ...  # type: ignore[empty-body]

    def gate_reference(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def gate_extraction(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def gate_bundle(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def gate_paid_features(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def gate_spec(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def gate_pre_generate(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def gate_state_coverage(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def gate_post_implement(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def gate_boundary(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def gate_font_parity(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def gate_section_compare(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def _check_paid_font_substitution(  # type: ignore[empty-body]
        self,
    ) -> list[CheckResult]: ...

    def _check_webflow(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def _check_hover_timing(  # type: ignore[empty-body]
        self, interactions_data: dict[str, Any]
    ) -> tuple[list[CheckResult], bool]: ...

    def _check_transition_coverage(  # type: ignore[empty-body]
        self, spec: dict[str, Any] | None
    ) -> list[CheckResult]: ...

    def _check_section_counts(  # type: ignore[empty-body]
        self, section_map: dict[str, Any], component_map: dict[str, Any]
    ) -> list[CheckResult]: ...

    def _check_audit_artifacts(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def _check_detection_artifact_integrity(  # type: ignore[empty-body]
        self,
    ) -> list[CheckResult]: ...

    def _scroll_motion_signals(self) -> bool: ...  # type: ignore[empty-body]

    def _check_scroll_spec_coverage(  # type: ignore[empty-body]
        self, spec: Any
    ) -> list[CheckResult]: ...

    def _check_generation_completeness(  # type: ignore[empty-body]
        self,
    ) -> list[CheckResult]: ...

    def _check_componentization(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def _find_impl_root(self) -> Path | None: ...

    def _check_verification_plan(self) -> list[CheckResult]: ...  # type: ignore[empty-body]

    def _transition_spec_count(self) -> int: ...  # type: ignore[empty-body]

    def _tree_diff_floor(self) -> int: ...  # type: ignore[empty-body]
