"""Section-Compare gate.

Extracted from ui_clone/gate.py. Each function takes `self: "Gate"` and is
rebound onto the Gate class in `ui_clone.gates.__init__`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..policies import canvas_replay as _canvas_replay
from .base import (
    CheckResult,
    _parse_failed_sections,
    _validate_artifact_entry,
)

if TYPE_CHECKING:
    from .base import Gate  # noqa: F401

def _required_viewports(ref_dir: Path) -> list[str]:
    """Plan-declared viewport set, required when the site is responsive.

    Returns WxH strings (e.g. ["375x812", ...]) when detected-breakpoints.json
    exists AND verification-plan.json declares >1 viewports; [] otherwise.
    """
    if not (ref_dir / "detected-breakpoints.json").is_file():
        return []
    try:
        plan = json.loads(
            (ref_dir / "verification-plan.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return []
    out: list[str] = []
    for vp in plan.get("viewports") or []:
        try:
            out.append(f"{int(vp['w'])}x{int(vp['h'])}")
        except (KeyError, TypeError, ValueError):
            continue
    return out if len(out) > 1 else []


def _stats_signal_rich(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    raw_unique = raw.get("unique")
    raw_dominant = raw.get("dominant")
    if raw_unique is None or raw_dominant is None:
        return False
    try:
        unique = float(raw_unique)
        dominant = float(raw_dominant)
    except (TypeError, ValueError):
        return False
    return unique > 8 and dominant < 0.6


def _stats_sparse_detail(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    raw_unique = raw.get("unique")
    raw_dominant = raw.get("dominant")
    if raw_unique is None or raw_dominant is None:
        return False
    try:
        unique = float(raw_unique)
        dominant = float(raw_dominant)
    except (TypeError, ValueError):
        return False
    return unique >= 32 and dominant < 0.985


def _low_contrast_media_signal_rich(info: dict[str, object]) -> bool:
    return (
        info.get("mediaBearing") is True
        and _stats_signal_rich(info.get("ref"))
        and _stats_signal_rich(info.get("impl"))
    )


def _low_variance_sparse_detail(info: dict[str, object]) -> bool:
    return _stats_sparse_detail(info.get("ref"))


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

    # Multi-viewport enforcement: a responsive site verified at one viewport
    # hides every breakpoint-specific defect (mobile-swap sections never
    # render, vw layouts only reflow elsewhere). When detected-breakpoints
    # evidence exists and the verification plan declares a viewport set,
    # result.txt must be the VIEWPORTS fan-out covering every plan viewport.
    required_vps = _required_viewports(self.ref_dir)
    if required_vps:
        missing_vps = [vp for vp in required_vps if f"viewport: {vp}" not in content]
        if missing_vps:
            vp_csv = ",".join(required_vps)
            results.append(
                CheckResult(
                    "sections/viewport-coverage",
                    "fail",
                    (
                        "sections/result.txt covers a single viewport but the site is "
                        f"responsive (detected-breakpoints.json) and verification-plan.json "
                        f"declares {len(required_vps)} viewports — missing: "
                        f"{', '.join(missing_vps)}"
                    ),
                    fix=(
                        f"Run: VIEWPORTS={vp_csv} bash skills/visual-debug/scripts/"
                        f"section-compare.sh <orig-url> <impl-url> <session> {self.ref_dir}"
                    ),
                )
            )

    failed_sections = _parse_failed_sections(lines)
    missing_count = sum(1 for ln in lines if "⚠️ MISSING impl" in ln)
    # A crop-evidence guard converts a section to UNMEASURED when the REFERENCE
    # crop carries no signal (blank / symmetric-blank / mostly-masked / colour-
    # flattened). The producer folded these into SKIP_COUNT, where a 0-FAIL
    # summary made the run read as a clean pass. They are absence of evidence,
    # and the sections that lose evidence this way are disproportionately the
    # animated ones (a mid-reveal section captures blank), so this is exactly
    # where a motion-cloning tool must not certify.
    unmeasured_count = sum(1 for ln in lines if ln.startswith("|") and "UNMEASURED" in ln)

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
    _CANON_REF_MIN_STD = 0.05  # mirrors ui_clone/section_guards.py REF_MIN_STD default
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

    # SECTION_REF_MIN_STD gaming detector — the same shape as the threshold
    # detector above, for the knob that arms blank-ref detection. Now that an
    # UNMEASURED row blocks the gate, driving REF_MIN_STD to 0 makes
    # `ref["std"] < REF_MIN_STD` unsatisfiable, so no section is ever guarded and
    # the closure reverts silently. crop-guards.json records every section's
    # measured std whether or not it was guarded, which is what makes a lowered
    # floor visible after the run.
    std_gaming: list[str] = []
    guards_path = self.ref_dir / "sections" / "crop-guards.json"
    if guards_path.is_file():
        try:
            guards = json.loads(guards_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            guards = None
        if isinstance(guards, dict):
            declared = (guards.get("thresholds") or {}).get("refMinStd")
            try:
                if declared is not None and float(declared) < _CANON_REF_MIN_STD:
                    std_gaming.append(
                        f"declared refMinStd={declared} below canonical {_CANON_REF_MIN_STD}"
                    )
            except (TypeError, ValueError):
                pass
            for name, info in (guards.get("sections") or {}).items():
                if not isinstance(info, dict) or info.get("reason"):
                    continue
                # Only producer-attested non-content rows may be low variance
                # without a blank-reference guard. Legacy/malformed artifacts
                # omit this field and remain fail-closed.
                if info.get("contentBearing") is False:
                    continue
                raw_std = (info.get("ref") or {}).get("std")
                if raw_std is None:
                    continue
                try:
                    std = float(raw_std)
                except (TypeError, ValueError):
                    continue
                if (
                    std < _CANON_REF_MIN_STD
                    and not _low_contrast_media_signal_rich(info)
                    and not _low_variance_sparse_detail(info)
                ):
                    std_gaming.append(f"{name} (ref std {std}) produced no blank-ref guard")

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

    # Canvas-replay AE relief (v0.7.0 closeoutPolicy="canvas-replay").
    # When the operator has opted into the policy AND signed the attestation
    # AND a failing section is tagged kind="canvas" in section-map.json, the
    # critical AE/Mpx ceiling widens from 20000 to 40000. Rows whose AE/Mpx
    # is within the widened band downgrade to PASS; rows above it stay
    # critical (relief widens the band — it does NOT bypass). Scope is
    # strictly the FAIL → PASS reclassification; STRUCTURAL_ONLY guards,
    # threshold-gaming detection, and missing-impl checks are unaffected.
    relief_section_names = _canvas_replay.relief_active_sections(self.ref_dir)
    canvas_relieved: list[tuple[str, int]] = []  # (name, ae_per_mpx)
    if relief_section_names and failed_sections:
        # Build {name: ae_per_mpx} from FAIL rows so we can re-check the
        # widened band. result.txt cell order: name | ae | ae/mpx | sev | status.
        ae_per_mpx_by_name: dict[str, int] = {}
        for ln in lines:
            if not ln.startswith("|"):
                continue
            if "❌" not in ln and "🌑" not in ln:
                continue
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if len(cells) < 3:
                continue
            row_name = cells[0]
            if not row_name or row_name.lower() == "section" or "---" in row_name:
                continue
            try:
                ae_per_mpx_by_name[row_name] = int(cells[2])
            except (ValueError, IndexError):
                continue
        ceiling = _canvas_replay.critical_ae_ceiling()
        for fail_name, _fail_ae in failed_sections:
            if fail_name in downgraded_names:
                continue
            if fail_name not in relief_section_names:
                continue
            row_mpx = ae_per_mpx_by_name.get(fail_name)
            if row_mpx is None:
                continue
            if row_mpx <= ceiling:
                canvas_relieved.append((fail_name, row_mpx))

    canvas_relieved_names = {name for name, _ in canvas_relieved}
    effective_fails = [
        (name, ae)
        for name, ae in failed_sections
        if name not in downgraded_names and name not in canvas_relieved_names
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
    # escape hatch for isolated sections that use commercial fonts / licensed
    # imagery. Broad coverage explains a common operator symptom: "pixel
    # polishing isn't running" because those rows skip AE entirely. Warn at
    # 30%+ so the cause is visible before it crosses the hard 50% bypass cap.
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
    structural_only_broad = (
        total_section_rows > 0
        and structural_only_count >= 3
        and not structural_only_excess
        and (structural_only_count / total_section_rows) >= 0.30
    )

    # Completion enforcement — a truncated/empty/crashed result.txt carries no
    # ❌ rows, so the PASS branch below (which decides on ABSENCE of negative
    # evidence) used to certify it as "All sections PASS". section-compare.sh
    # writes the aggregate incrementally: it emits each `viewport: WxH` header
    # BEFORE running the inner compare and appends `[WxH] exit: N` after, so a
    # crash between the two leaves a header with no rows/exit. Confirmed as a
    # false-pass blocker by codex + multi-agent review. Require: (A) at least one
    # measured section row; (B) every present viewport ran to completion with
    # exit 0. "no negative evidence" is NOT "verified".
    incompleteness: list[str] = []
    if total_section_rows == 0:
        incompleteness.append(
            "no section rows were measured — result.txt is empty or truncated"
        )
    viewport_headers = [
        (idx, ln.strip()[len("viewport:"):].strip())
        for idx, ln in enumerate(lines)
        if ln.strip().startswith("viewport:")
    ]
    for pos, (line_no, vp) in enumerate(viewport_headers):
        end = viewport_headers[pos + 1][0] if pos + 1 < len(viewport_headers) else len(lines)
        block = lines[line_no:end]
        exit_codes = [
            int(m.group(1))
            for ln in block
            if (m := re.search(rf"\[{re.escape(vp)}\]\s*exit:\s*(-?\d+)", ln))
        ]
        # Completion is PER-VIEWPORT, not global: total_section_rows is summed
        # across the whole file, so a viewport block with exit:0 and ZERO rows
        # would pass on a SIBLING viewport's rows. Require >=1 measured row in
        # THIS viewport's own block.
        vp_rows = sum(
            1 for ln in block
            if ln.startswith("| ")
            and "---" not in ln
            and "Section" not in ln
            and ln.strip() != "|"
        )
        if not exit_codes:
            incompleteness.append(
                f"viewport {vp} is incomplete/truncated (no exit line — "
                "section-compare did not finish it)"
            )
        elif any(code != 0 for code in exit_codes):
            incompleteness.append(
                f"viewport {vp} reported a nonzero exit ({exit_codes}) — "
                "the compare crashed or failed"
            )
        elif vp_rows == 0:
            incompleteness.append(
                f"viewport {vp} ran to exit 0 but measured ZERO section rows — "
                "an empty viewport block certifies that viewport on no evidence"
            )

    if (
        effective_fail_count == 0
        and missing_count == 0
        and unmeasured_count == 0
        and not threshold_gaming
        and not std_gaming
        and not critical_structural
        and not structural_only_excess
        and not incompleteness
    ):
        parts: list[str] = []
        if downgraded:
            parts.append(f"{len(downgraded)} known artifact(s) downgraded")
        if canvas_relieved:
            parts.append(f"{len(canvas_relieved)} canvas-replay relief")
        if parts:
            msg = f"All sections PASS ({', '.join(parts)})"
        else:
            msg = "All sections PASS"
        results.append(CheckResult("sections/result.txt", "pass", msg))
    else:
        if incompleteness:
            results.append(
                CheckResult(
                    "sections/result.txt completeness",
                    "fail",
                    "sections/result.txt is incomplete — "
                    + "; ".join(incompleteness[:6]),
                    fix=(
                        "Re-run section-compare.sh to completion. The gate cannot "
                        "certify PASS on an empty/truncated/crashed result.txt — "
                        "absence of ❌ rows is not the same as a verified clone."
                    ),
                )
            )
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
                        f"<session> <orig> <impl> {self.ref_dir}/sections/diff/<failing-section>.png\n"
                        "     (arg 4 must be a diff IMAGE from sections/diff/ — "
                        "locates hotspot elements via pixel clustering)\n"
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
        if std_gaming:
            results.append(
                CheckResult(
                    "blank-ref threshold gaming",
                    "fail",
                    "SECTION_REF_MIN_STD was lowered below the canonical "
                    f"{_CANON_REF_MIN_STD}, disarming blank-ref detection: "
                    + "; ".join(std_gaming[:5]),
                    fix=(
                        "Do not lower SECTION_REF_MIN_STD to clear an UNMEASURED row — "
                        "that removes the detector, not the defect. Re-capture the "
                        "reference with a longer settle (WAIT_SCROLL_SETTLE=<seconds>). "
                        "If a section is genuinely flat by design, record it in "
                        "known-artifacts.json with capture evidence rather than moving "
                        "the global floor."
                    ),
                )
            )
        if unmeasured_count > 0:
            results.append(
                CheckResult(
                    "unmeasured sections",
                    "fail",
                    f"{unmeasured_count} section(s) UNMEASURED — the reference crop carried no "
                    "signal, so these sections have no pixel evidence either way",
                    fix=(
                        "Fix the CAPTURE, not the impl: a blank reference crop means the ref "
                        "screenshot was taken before the section revealed/animated. Re-capture "
                        "with a longer settle — WAIT_SCROLL_SETTLE=<seconds>, normally derived "
                        "from the longest transition in transition-spec.json — then re-run "
                        "section-compare. Iterating on impl/src for these rows tunes against "
                        "an artifact that measured nothing."
                    ),
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
        if canvas_relieved:
            results.append(
                CheckResult(
                    "canvas-replay AE relief",
                    "pass",
                    f"{len(canvas_relieved)} section(s) relieved by canvas-replay "
                    f"policy (AE/Mpx ≤ {int(_canvas_replay.critical_ae_ceiling())}): "
                    + ", ".join(
                        f"{name} (AE/Mpx={mpx})" for name, mpx in canvas_relieved
                    ),
                )
            )

    if structural_only_broad:
        pct = round(100 * structural_only_count / total_section_rows)
        results.append(
            CheckResult(
                "structural-only broad coverage",
                "warn",
                f"{structural_only_count}/{total_section_rows} sections ({pct}%) "
                "are STRUCTURAL_ONLY — pixel AE polishing skipped for those "
                "sections. This is below the hard 50% cap, but broad "
                "substitution makes visual polish low-signal.",
                fix=(
                    "Narrow asset-substitution.json structuralOnlySections to "
                    "only sections with documented font/image/video "
                    "substitutions, then re-run section-compare so the rest "
                    "produce real AE measurements."
                ),
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
