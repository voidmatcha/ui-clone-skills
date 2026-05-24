"""Section-Compare gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from .base import (
    CheckResult,
    _parse_failed_sections,
    _validate_artifact_entry,
)

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401

def gate_section_compare(self: Gate) -> list[CheckResult]:
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

