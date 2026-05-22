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
import os
import re
import subprocess
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
    """Extract (section_name, ae) pairs from sections/result.txt FAIL lines.

    result.txt is a markdown table:
        | <name> | <ae> | <ae/mpx> | <severity> | ❌ |   (critical fail)
        | <name> | <ae> | <ae/mpx> | saturated | 🌑 |   (gradient-dead fail)
    Codex audit review Q1: `🌑 saturated` rows are FAIL_COUNT increments
    in section-compare.sh (AE/Mpx ≥ 800k, "gradient dead, not comparable")
    but `_parse_failed_sections` was only catching `❌`. That let
    known-artifacts.json downgrade saturated rows to a structural pass
    even though section-compare itself treated them as failures —
    a silent bypass that contributed to L11-L13's persistent AE envelope.
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

        log = self._load_json("download-log.json") or {}
        attempts = log.get("attempts") if isinstance(log, dict) else None
        attempted_urls: list[str] = []
        if isinstance(attempts, list):
            attempted_urls = [
                str(a.get("url") or "") for a in attempts if isinstance(a, dict)
            ]
        missing_download: list[str] = []
        for item in substitutes:
            family = str(item.get("cdn") or item.get("family") or "").strip()
            if not family:
                continue
            # Build keyword(s) to match against the URL list.
            # "Die Grotesk" → tokens ["Die", "Grotesk"]; require both AND-match
            # against at least one URL (case-insensitive). This catches the
            # common shapes (foundry CDN + self-hosted) without overreaching.
            tokens = [t for t in re.split(r"\s+", family) if t]
            hit = False
            for url in attempted_urls:
                u = url.lower()
                if all(tok.lower() in u for tok in tokens):
                    hit = True
                    break
            if not hit:
                missing_download.append(family)
        if missing_download:
            sample = ", ".join(missing_download[:3])
            results.append(
                CheckResult(
                    "paid-font substitution — download attempt missing",
                    "fail",
                    f"{len(missing_download)} paid font(s) marked decision='substitute' "
                    f"({sample}) but download-log.json shows zero attempts for the "
                    "family. Research-mode policy: a substitution is only valid AFTER "
                    "an HTTP download attempt has been made and recorded — "
                    "iteration-discipline.md 'Asset substitution policy' section.",
                    fix=(
                        "Identify the woff2/otf/ttf URLs for the commercial family "
                        "(check head.json + bundle-extraction.json for @font-face src), "
                        "add them to the asset-download targets, re-run "
                        "scripts/extract/asset-download.sh, and confirm "
                        "download-log.json records the attempt. Substitution is then "
                        "valid if the attempt returned non-200."
                    ),
                )
            )
            return results

        results.append(
            CheckResult(
                "paid-font substitution",
                "pass",
                f"{len(substitutes)} substitute decision(s) declared and download "
                "attempts recorded in download-log.json",
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
        results.extend(self._check_generation_completeness())
        return results

    def _check_generation_completeness(self) -> list[CheckResult]:
        """Reject components with empty function bodies / no JSX return.

        audit incident (2026-05-19): agent produced 11 component functions in a
        single `App.tsx`; some had real bodies, but the gaming pattern is
        easy — write the signature, skip the body, let section-compare's
        STRUCTURAL_ONLY rows mark it as 'good enough'. This static check
        catches the trivial stub case before visual evidence is consulted.

        Heuristic (simple + low false-positive):
          - For each `function NAME()` or `const NAME = ()` where NAME is
            CapitalizedIdentifier (React component convention), find its
            closing brace.
          - If the body contains zero `<` characters between the opening
            and closing brace AND zero `return ` statement → stub.

        Inline-App layout (audit incident) and separate-component-files layout
        (audit incident) both pass when their components have real bodies.
        """
        results: list[CheckResult] = []
        impl_root = self._find_impl_root()
        if impl_root is None:
            return results
        src_dir = impl_root / "src"
        if not src_dir.is_dir():
            return results
        stubs: list[str] = []
        for tsx in src_dir.rglob("*.tsx"):
            try:
                text = tsx.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # Match either `function NAME(` or `const NAME = (` where NAME starts uppercase.
            for m in re.finditer(
                r"(?:^|\n)(?:export\s+)?(?:function\s+([A-Z][A-Za-z0-9_]*)\s*\([^)]*\)\s*\{|"
                r"const\s+([A-Z][A-Za-z0-9_]*)\s*=\s*\([^)]*\)\s*=>\s*\{)",
                text,
            ):
                name = m.group(1) or m.group(2)
                start = m.end()
                # Walk balanced braces to find the matching closer.
                depth = 1
                i = start
                while i < len(text) and depth > 0:
                    ch = text[i]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                    i += 1
                body = text[start:i]
                # A component body must contain either a JSX tag or `return (` at minimum.
                if "<" not in body and "return " not in body:
                    rel = tsx.relative_to(impl_root)
                    stubs.append(f"{rel}:{name}")
        if stubs:
            preview = ", ".join(stubs[:5])
            more = f" (+{len(stubs) - 5} more)" if len(stubs) > 5 else ""
            results.append(
                CheckResult(
                    "generation-completeness",
                    "fail",
                    f"❌ {len(stubs)} component(s) are stubs (empty body, no "
                    f"JSX, no return): {preview}{more}",
                    fix="Fill bodies or remove unused stubs before re-running post-implement.",
                )
            )
        else:
            results.append(
                CheckResult(
                    "generation-completeness",
                    "pass",
                    "✓ No stub components found in impl/src/**/*.tsx.",
                )
            )
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

    def _check_detection_artifact_integrity(self) -> list[CheckResult]:
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

    def _check_scroll_spec_coverage(self, spec: Any) -> list[CheckResult]:
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

    def _find_impl_root(self) -> Path | None:
        """Locate the impl/ root co-located with this ref_dir.

        Delegates to `scripts/extract/find-impl-root.sh` so this gate and all
        shell-side checks (bundle-impl-coverage, transition-spec-coverage,
        verify-loop) share one resolver. audit incident surfaced a split-brain risk
        where a Python and a shell heuristic could diverge — passing one
        while failing the other was a real escape vector. A single canonical
        implementation closes that gap (Codex audit issue 1).

        Returns the impl ROOT (containing src/ and public/), not impl/public/.
        None when the resolver exits non-zero. Stdout shape from resolver
        (3 lines): impl_root, impl_src, impl_package_json — we use line 1.
        """
        env_root = os.environ.get("PLUGIN_ROOT") or os.environ.get(
            "CLAUDE_PLUGIN_ROOT"
        )
        resolver: Path | None = None
        if env_root:
            cand = Path(env_root) / "scripts" / "extract" / "find-impl-root.sh"
            if cand.is_file():
                resolver = cand
        if resolver is None:
            # Walk up from this file so in-repo tests work without env vars.
            here = Path(__file__).resolve()
            for parent in here.parents:
                cand = parent / "scripts" / "extract" / "find-impl-root.sh"
                if cand.is_file():
                    resolver = cand
                    break
        if resolver is None:
            return None
        try:
            proc = subprocess.run(
                ["bash", str(resolver), str(self.ref_dir)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line:
                p = Path(line)
                if p.is_dir():
                    return p
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
            return [
                CheckResult(
                    "verification-plan.json",
                    "fail",
                    "verification-plan.json — MISSING. post-implement cannot infer required text/DOM/asset/motion checks without it.",
                    fix="Run: bash skills/visual-debug/scripts/verification-plan.sh <ref-dir>",
                )
            ]

        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return [
                CheckResult(
                    "verification-plan.json",
                    "fail",
                    f"verification-plan.json — unreadable ({e}). "
                    "post-implement cannot enforce required checks against "
                    "an unparseable plan. Regenerate before retrying.",
                    fix="Run: bash skills/visual-debug/scripts/verification-plan.sh <ref-dir>",
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

        # Two-phase mode (option A) — when UI_CLONE_PHASE=rapid the
        # agent is in initial visual-iteration mode. Non-anti-cheat
        # block checks are downgraded to warn so the agent can build
        # quickly and iterate visually first. Anti-cheat gates and
        # the must-stay-strict set remain block regardless.
        #
        # Promotion to strict: set UI_CLONE_PHASE=strict (default)
        # before declaring done. The strict run enforces every block.
        import os as _os
        phase = (_os.environ.get("UI_CLONE_PHASE") or "strict").lower()
        # Anti-cheat gates that ALWAYS stay block, even in rapid
        # mode — these catch cheating and must never downgrade.
        #
        STRICT_ALWAYS = {
            # Anti-cheat (block cheating regardless of phase).
            "ref-screenshot-asset", "invalidation", "scaffold-warn",
            "remote-asset-ref", "html-paste", "proxy-mirror-check",
            "hidden-children", "monolithic-impl", "entry-coherence",
            "text-fidelity-check", "dom-mirror-check",
            "required-media-coverage", "css-mirror",
            "runtime-dom-parity", "svg-dom-parity", "motion-coverage",
            "scroll-engine-parity",
            # runtime-image-validity — HTML-fallback-as-image is a
            # fundamental cheat (Vite serving index.html for missing
            # assets). Must stay strict.
            "runtime-image-validity",
            # reveal-trigger — IO+overflow:hidden reveals that never
            # fire are a core completion-blocker (catches "stuck
            # reveal" patterns that visually show empty space where
            # ref has content). Must stay strict.
            "reveal-trigger",
        }

        out: list[CheckResult] = []
        for entry in checks:
            if not isinstance(entry, dict):
                continue
            check_id = str(entry.get("id") or "?")
            produces = entry.get("produces")
            script = entry.get("script") or ""
            reason = entry.get("reason") or ""
            severity = entry.get("severity") or "block"
            # Rapid-mode downgrade: block→warn for non-anti-cheat
            # checks so the agent can iterate visually without
            # consuming the iteration budget on fidelity gates.
            if phase == "rapid" and severity == "block" and check_id not in STRICT_ALWAYS:
                severity = "warn"

            if not produces:
                continue
            artifact = self.ref_dir / produces
            label = f"required: {check_id}"
            fix = f"Run: bash {script}" if script else ""

            if not artifact.is_file():
                msg = (
                    f"MISSING_ARTIFACT {check_id} — produces "
                    f"{produces}. Reason: {reason}. "
                    "Run scripts/verify/run-required-checks.sh "
                    "<session> <ref-url> <impl-url> <ref-dir> to "
                    "produce every missing required-check artifact "
                    "in one shell call."
                )
                if severity == "warn":
                    out.append(CheckResult(label, "warn", msg))
                else:
                    out.append(CheckResult(label, "fail", msg, fix=fix))
                continue

            try:
                raw = artifact.read_text(encoding="utf-8")
            except OSError as e:
                msg = (
                    f"{check_id} — artifact unreadable ({e}). "
                    "Cannot verify; re-run the producing script."
                )
                if severity == "warn":
                    out.append(CheckResult(label, "warn", msg))
                else:
                    out.append(CheckResult(label, "fail", msg, fix=fix))
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
                counts = data.get("counts") or {}
                if isinstance(counts, dict):
                    unpaired = int(counts.get("unpaired") or 0)
                    ok = int(counts.get("ok") or 0)
                    if unpaired >= 3 and unpaired > ok:
                        msg = (
                            f"tree-diff — unpaired majority "
                            f"(unpaired={unpaired}, ok={ok}). "
                            "elementFromPoint pairing failed, so status=pass "
                            "is not convergence evidence. Fix DOM/layout "
                            "structure until most walked elements pair."
                        )
                        out.append(CheckResult(label, "fail", msg, fix=fix))
                        continue
            #
            PATH_CHECK_IDS = {
                "asset-transfer", "asset-utilization", "image-fidelity",
                "proxy-mirror-check", "lottie-runtime",
                "bundle-impl-coverage",
                "ref-screenshot-asset",
                # Common cheat pattern A1/A2/A3 — all emit implRoot.
                "entry-coherence", "scaffold-residue", "html-paste",
                # Diagnosis B — required-media coverage emits implRoot.
                "required-media-coverage",
                # Common cheat pattern A4/A5 + fix #2 — css-mirror emits
                # implRoot, runtime-dom-parity and hidden-children
                # are URL-based (no implRoot path to validate, but
                # listing here makes intent explicit; the PATH_CHECK
                # block is skipped when the recorded path field is
                # absent so this is safe).
                "css-mirror",
                # Signal 1 — scaffold-warn placeholders (impl source scan).
                "scaffold-warn",
                # validation run findings — monolithic-impl + motion-coverage
                # both emit implRoot for cross-loop protection.
                "monolithic-impl", "motion-coverage",
                "scroll-engine-parity",
            }
            if (
                check_id in PATH_CHECK_IDS
                and isinstance(data, dict)
                and status == "pass"
            ):
                def _nz(v: object) -> str | None:
                    if isinstance(v, str) and v.strip():
                        return v
                    return None

                recorded = (
                    _nz(data.get("implPublicDir"))
                    or _nz(data.get("implSrcDir"))
                    or _nz(data.get("implDir"))
                    or _nz(data.get("implRoot"))
                    or _nz(data.get("implPkgJson"))
                )
                impl_root = self._find_impl_root()
                if (
                    impl_root is not None
                    and recorded is None
                ):
                    msg = (
                        f"{check_id} — path-check artifact must emit "
                        "implRoot/implDir/implSrcDir/implPublicDir/"
                        "implPkgJson. None present, so cross-loop "
                        "contamination cannot be ruled out. Re-run the "
                        "check (newer scripts emit the field)."
                    )
                    out.append(CheckResult(label, "fail", msg, fix=fix))
                    continue
                if recorded and impl_root is not None:
                    rec_path = Path(str(recorded)).resolve()
                    impl_resolved = impl_root.resolve()
                    expected_roots = {
                        impl_resolved,
                        (impl_root / "public").resolve(),
                        (impl_root / "src").resolve(),
                    }
                    if rec_path not in expected_roots:
                        try:
                            rec_path.relative_to(impl_resolved)
                            recorded_inside = True
                        except ValueError:
                            recorded_inside = False
                        if not recorded_inside:
                            msg = (
                                f"{check_id} — loop path contamination. "
                                f"artifact recorded {recorded}, but current "
                                f"impl_root is {impl_root}. Run the check "
                                "against the active loop's impl tree."
                            )
                            out.append(CheckResult(label, "fail", msg, fix=fix))
                            continue
                    # Stale-relative check — artifact in-tree but older than
                    try:
                        artifact_path = self.ref_dir / produces
                        if artifact_path.is_file():
                            artifact_mtime = artifact_path.stat().st_mtime
                            newest_impl = 0.0
                            for sub in ("src", "public"):
                                sub_dir = impl_root / sub
                                if sub_dir.is_dir():
                                    for p in sub_dir.rglob("*"):
                                        try:
                                            if p.is_file():
                                                m = p.stat().st_mtime
                                                if m > newest_impl:
                                                    newest_impl = m
                                        except OSError:
                                            continue
                            if newest_impl > artifact_mtime + 1.0:
                                msg = (
                                    f"{check_id} — stale artifact. "
                                    f"{produces} mtime is older than "
                                    "newest impl source/public file by "
                                    f"{newest_impl - artifact_mtime:.0f}s. "
                                    "Re-run the check against the current impl."
                                )
                                out.append(CheckResult(label, "fail", msg, fix=fix))
                                continue
                    except OSError:
                        pass
            STATUS_REQUIRED = {
                "asset-transfer", "asset-utilization", "image-fidelity",
                "font-parity", "dom-mirror-check", "text-fidelity",
                "hydration-check", "transition-spec-coverage",
                "spec-implementation-coverage", "runtime-spec-coverage",
                "tree-diff", "scroll-end-completion", "reveal-trigger",
                "boundary",
                "tailwind-transform-conflict", "proxy-mirror-check",
                "lottie-runtime", "bundle-impl-coverage", "scroll-coverage",
                "runtime-image-validity", "remote-asset-ref",
                "ref-screenshot-asset",
                # Common cheat pattern A1/A2/A3 anti-cheat — entry-coherence
                # (stack/entry consistency), scaffold-residue (orphan
                # components), html-paste (structural/script/CSS theft).
                "entry-coherence", "scaffold-residue", "html-paste",
                # Diagnosis B — required-media (video/Lottie) coverage.
                "required-media-coverage",
                # Common cheat pattern A4/A5 + fix #2 anti-cheat —
                # css-mirror (static), runtime-dom-parity (runtime
                # positive parity), hidden-children (runtime hidden
                # DOM with screenshot background overlay).
                "css-mirror", "runtime-dom-parity", "hidden-children",
                "invalidation",
                # Signal 1 — scaffold-warn placeholders.
                "scaffold-warn",
                "svg-dom-parity",
                # validation run findings — monolithic-impl + motion-coverage.
                "monolithic-impl", "motion-coverage",
                "scroll-engine-parity",
            }
            if status == "pass":
                out.append(CheckResult(label, "pass", f"{check_id} (status: pass)"))
            elif status is None:
                if check_id in STATUS_REQUIRED:
                    msg = (
                        f"{check_id} — artifact present but `status` field "
                        "is absent. Known checks must declare status; missing "
                        "status is the audit incident 'check produced JSON but never "
                        "ran the assertion' gaming pattern."
                    )
                    out.append(CheckResult(label, "fail", msg, fix=fix))
                    continue
                out.append(CheckResult(label, "pass", f"{check_id} (artifact present, no status field)"))
            else:
                count = (data.get("errorCount") or data.get("failureCount") or
                         data.get("totalStuck") or "?") if isinstance(data, dict) else "?"
                msg = f"{check_id} — status: {status} ({count} issue(s)). Reason: {reason}"
                if str(status).lower() == "warn":
                    out.append(CheckResult(label, "warn", msg))
                elif severity == "warn":
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

    def _check_pipeline_state_prerequisites(self, gate: str) -> CheckResult | None:
        """Fail closed when pipeline-state skipped required earlier gates."""
        if gate == "all" or gate not in _state.GATE_ORDER:
            return None
        if not (self.ref_dir / "pipeline-state.json").is_file():
            return None
        ps = _state.PipelineState.load(self.ref_dir)
        missing = ps.missing_prerequisites(gate)
        if not missing:
            return None
        missing_s = ", ".join(missing)
        return CheckResult(
            "pipeline-state prerequisites",
            "fail",
            (
                f"pipeline-state.json is out of order: gate {gate!r} cannot pass "
                f"until earlier gate(s) are completed: {missing_s}."
            ),
            fix=(
                "Resume at the earliest missing gate instead of continuing closeout. "
                f"Run: python -m ui_clone.goal {self.ref_dir}"
            ),
        )

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

        state_prereq = self._check_pipeline_state_prerequisites(gate)
        results = [state_prereq] if state_prereq is not None else self._dispatch(gate)

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
