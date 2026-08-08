from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

VISUAL_AXES = ("layout", "text", "color", "animation")
TRANSITION_CORE_COMPONENTS = (
    "transition-spec-coverage.json",
    "spec-implementation-coverage.json",
    "transition-coverage.json",
    "transition-fires.json",
    "reveal-trigger.json",
    "scroll-completion.json",
    "keyframes-diff.json",
)
TRANSITION_VIDEO_COMPONENT = "transitions/video-motion-result.txt"
TRANSITION_COMPARE_COMPONENT = "transitions/result.txt"
TRANSITION_HOVER_COMPONENT = "transitions/hover-state-result.txt"
TRANSITION_PROOF_COMPONENT = "transition-proof.json"


def reject_json_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON numeric constant: {token}")


def load_strict_json_text(raw: str) -> Any:
    return json.loads(raw, parse_constant=reject_json_constant)


def load_strict_json_file(path: Path) -> Any:
    return load_strict_json_text(path.read_text(encoding="utf-8"))


def bounded_score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):  # noqa: UP038
        return None
    score = float(value)
    if not math.isfinite(score) or not 0 <= score <= 10:
        return None
    return score


def visual_fidelity_semantic_error(data: object) -> str | None:
    if not isinstance(data, dict):
        return "artifact is not an object"
    if data.get("schemaVersion") != 1:
        return "schemaVersion must be 1"
    status = data.get("status")
    if status not in {"pass", "fail"}:
        return "status must be pass or fail"
    static_sections = data.get("staticSections")
    motion = data.get("motion")
    overall = data.get("overall")
    if (
        not isinstance(static_sections, list)
        or not isinstance(motion, dict)
        or not isinstance(overall, dict)
    ):
        return "staticSections, motion, or overall is malformed"
    axes = motion.get("axes")
    if not isinstance(axes, dict):
        return "motion.axes is missing"

    signals: list[float] = []
    for axis in VISUAL_AXES:
        score = bounded_score(axes.get(axis))
        if score is None:
            return f"motion axis {axis!r} must be finite and within [0, 10]"
        signals.append(score)
    for index, section in enumerate(static_sections):
        if not isinstance(section, dict):
            return f"staticSections[{index}] is not an object"
        score = bounded_score(section.get("score"))
        if score is None:
            return (
                f"staticSections[{index}].score must be finite and within [0, 10]"
            )
        signals.append(score)

    overall_score = bounded_score(overall.get("score"))
    overall_min = bounded_score(overall.get("min"))
    if overall_score is None or overall_min is None:
        return "overall score and min must be finite and within [0, 10]"
    expected_score = round(sum(signals) / len(signals), 2)
    expected_min = round(min(signals), 2)
    if not math.isclose(overall_score, expected_score, abs_tol=0.0001):
        return (
            f"overall.score={overall_score} disagrees with recomputed "
            f"mean={expected_score}"
        )
    if not math.isclose(overall_min, expected_min, abs_tol=0.0001):
        return (
            f"overall.min={overall_min} disagrees with recomputed "
            f"minimum={expected_min}"
        )
    expected_status = "pass" if all(score >= 7 for score in signals) else "fail"
    if status != expected_status:
        return f"status={status!r} disagrees with recomputed status={expected_status!r}"
    return None


def _strict_json_object(path: Path) -> dict[str, Any] | None:
    try:
        data = load_strict_json_file(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _transition_plan_outputs(ref_dir: Path) -> set[str]:
    plan = _strict_json_object(ref_dir / "verification-plan.json")
    rows = plan.get("requiredChecks") if isinstance(plan, dict) else None
    if not isinstance(rows, list):
        return set()
    return {
        str(row["produces"])
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("produces"), str)
    }


def _generated_transition_id_aliases(
    value: object,
    known_ids: set[str],
) -> set[str]:
    """Return the bounded aliases emitted by generated transition IDs."""
    transition_id = str(value or "").strip()
    if not transition_id:
        return set()
    without_ordinal = re.sub(r"^\d+-", "", transition_id, count=1)
    aliases = {transition_id, without_ordinal}
    collision_base = re.sub(
        r"-(?:[2-9]|[1-9]\d+)$",
        "",
        transition_id,
        count=1,
    )
    if collision_base != transition_id and collision_base in known_ids:
        aliases.add(re.sub(r"^\d+-", "", collision_base, count=1))
    return aliases


def _partial_hover_companion_error(
    ref_dir: Path,
    unmeasurable_targets: dict[str, str],
) -> str | None:
    plan = _strict_json_object(ref_dir / "verification-plan.json")
    rows = plan.get("requiredChecks") if isinstance(plan, dict) else None
    if not isinstance(rows, list):
        return "partial hover evidence requires a valid verification plan"
    required_checks = {
        "transition-fires": "transition-fires.json",
        "transition-compare": TRANSITION_COMPARE_COMPONENT,
        "transition-proof": TRANSITION_PROOF_COMPONENT,
    }
    invalid_checks: list[str] = []
    for check_id, produces in required_checks.items():
        matching_rows = [
            row
            for row in rows
            if isinstance(row, dict) and row.get("id") == check_id
        ]
        if (
            len(matching_rows) != 1
            or matching_rows[0].get("produces") != produces
            or matching_rows[0].get("severity") != "block"
        ):
            invalid_checks.append(f"{check_id}->{produces}")
    if invalid_checks:
        return (
            "partial hover evidence requires orthogonal checks: exactly one "
            "blocking companion row per check in verification-plan.json; "
            f"invalid checks={invalid_checks}"
        )

    spec = _strict_json_object(ref_dir / "transition-spec.json")
    raw_spec_entries = (
        spec.get("transitions") or spec.get("entries") or []
        if isinstance(spec, dict)
        else []
    )
    if not isinstance(raw_spec_entries, list) or not raw_spec_entries:
        return "partial hover evidence has no transition-spec entries"
    spec_entries = [
        entry for entry in raw_spec_entries if isinstance(entry, dict)
    ]
    if len(spec_entries) != len(raw_spec_entries):
        return "partial hover transition-spec contains a non-object entry"
    spec_ids = [str(entry.get("id", "")).strip() for entry in spec_entries]
    if any(not entry_id for entry_id in spec_ids):
        return "partial hover transition-spec contains a blank identity"
    if len(set(spec_ids)) != len(spec_ids):
        return "partial hover transition-spec contains duplicate identities"
    known_spec_ids = set(spec_ids)
    bound_spec_ids: dict[str, str] = {}
    for target_name, selector in unmeasurable_targets.items():
        matches = [
            entry
            for entry in spec_entries
            if (
                target_name
                in _generated_transition_id_aliases(
                    entry.get("id"),
                    known_spec_ids,
                )
                and str(entry.get("trigger", "")).strip().lower() == "hover"
                and str(entry.get("target", "")).strip() == selector
            )
        ]
        if len(matches) != 1:
            return (
                "partial hover target must bind to exactly one hover spec "
                f"with the same generated identity and selector; "
                f"target={target_name!r}, selector={selector!r}, "
                f"matches={len(matches)}"
            )
        bound_spec_ids[target_name] = str(matches[0]["id"]).strip()

    fires = _strict_json_object(ref_dir / "transition-fires.json")
    if not isinstance(fires, dict) or fires.get("status") != "pass":
        return "partial hover evidence requires transition-fires status=pass"
    tally_keys = ("total", "fired", "known_skip", "failed", "unmeasurable")
    if any(
        not isinstance(fires.get(key), int)
        or isinstance(fires.get(key), bool)
        for key in tally_keys
    ):
        return "partial hover transition-fires tallies are malformed"
    reported = {key: int(fires[key]) for key in tally_keys}
    entries = fires.get("entries")
    if not isinstance(entries, list):
        return "partial hover transition-fires entries are missing"
    recomputed = {
        "total": len(entries),
        "fired": 0,
        "known_skip": 0,
        "failed": 0,
        "unmeasurable": 0,
    }
    fire_ids: list[str] = []
    status_keys = {
        "pass": "fired",
        "degraded": "fired",
        "known-skip": "known_skip",
        "fail": "failed",
        "unmeasurable": "unmeasurable",
    }
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return f"partial hover transition-fires entries[{index}] is malformed"
        entry_id = str(entry.get("id", "")).strip()
        status_key = status_keys.get(str(entry.get("status", "")))
        if not entry_id or status_key is None:
            return (
                f"partial hover transition-fires entries[{index}] has an "
                "invalid identity or status"
            )
        fire_ids.append(entry_id)
        recomputed[status_key] += 1
    if reported != recomputed:
        return (
            "partial hover transition-fires tallies disagree with entries; "
            f"reported={reported}, recomputed={recomputed}"
        )
    if Counter(fire_ids) != Counter(spec_ids):
        return (
            "partial hover transition-fires identities disagree with "
            f"transition-spec; spec={spec_ids}, fires={fire_ids}"
        )
    entries_by_id = {
        entry_id: entry
        for entry_id, entry in zip(fire_ids, entries)
    }
    for target_name, spec_id in bound_spec_ids.items():
        entry = entries_by_id.get(spec_id)
        if (
            entry is None
            or entry.get("status") != "pass"
            or str(entry.get("trigger", "")).strip().lower() != "hover"
            or not any(
                "hover" in str(entry.get(key, "")).strip().lower()
                for key in ("type", "kind")
            )
        ):
            return (
                "partial hover target lacks an exact passing hover fire "
                f"receipt: target={target_name!r}, specId={spec_id!r}"
            )
    if (
        reported["fired"] <= 0
        or reported["failed"] > 0
        or reported["unmeasurable"] > 0
    ):
        return (
            "partial hover evidence requires independently measured transition "
            "fires with fired>0, failed=0, and unmeasurable=0"
        )

    return None


def transition_compare_text_result(
    text: str,
    *,
    allow_empty: bool = False,
) -> tuple[bool, str]:
    """Validate transition-compare's text receipt without promoting abstentions.

    HOVER_UNVERIFIED rows are structurally complete measurements but not hover
    parity proof. They therefore produce a PARTIAL note for warning-level gate
    reporting instead of a full pass. Older receipts may contain row verdicts
    without the aggregate header, or only the legacy ``N PASS, M FAIL``
    aggregate; accept those shapes while keeping the canonical aggregate and
    row counts in lockstep.
    """

    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    canonical_summaries = re.findall(
        r"^Transition compare:\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL\s*$",
        plain,
        flags=re.MULTILINE,
    )
    legacy_summaries = re.findall(
        r"^\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL\s*$",
        plain,
        flags=re.MULTILINE,
    )
    if len(canonical_summaries) > 1 or len(legacy_summaries) > 1:
        return False, (
            "transition compare must contain exactly one PASS/FAIL summary"
        )
    pass_rows = re.findall(
        r"^.*✅\s+PASS(?:\s|$)",
        plain,
        flags=re.MULTILINE,
    )
    fail_rows = re.findall(
        r"^.*❌\s+FAIL(?:\s|$)",
        plain,
        flags=re.MULTILINE,
    )
    has_canonical_summary = len(canonical_summaries) == 1
    has_legacy_summary = len(legacy_summaries) == 1
    if has_canonical_summary and has_legacy_summary:
        return False, (
            "transition compare must contain exactly one PASS/FAIL summary"
        )
    if has_canonical_summary:
        passed, failed = map(int, canonical_summaries[0])
        if len(pass_rows) != passed or len(fail_rows) != failed:
            return False, (
                "transition compare summary disagrees with measured rows; "
                f"summary={passed}/{failed}, "
                f"rows={len(pass_rows)}/{len(fail_rows)}"
            )
    elif has_legacy_summary:
        passed, failed = map(int, legacy_summaries[0])
        if (pass_rows or fail_rows) and (
            len(pass_rows) != passed or len(fail_rows) != failed
        ):
            return False, (
                "transition compare summary disagrees with measured rows; "
                f"summary={passed}/{failed}, "
                f"rows={len(pass_rows)}/{len(fail_rows)}"
            )
    elif pass_rows or fail_rows:
        passed, failed = len(pass_rows), len(fail_rows)
    elif allow_empty:
        return True, "transition compare: no transitions declared"
    else:
        return False, (
            "transition compare contains 0 measurement rows"
        )
    if passed + failed == 0:
        return False, "transition compare reports 0 pass / 0 fail"
    if failed > 0:
        return False, f"transition compare: {passed} pass / {failed} fail"
    hover_unverified = len(
        re.findall(r"^\s*⚠\s+HOVER_UNVERIFIED:", plain, flags=re.MULTILINE)
    )
    if hover_unverified > passed:
        return False, (
            "transition compare reports more hover abstentions than pass rows"
        )
    if hover_unverified > 0:
        return True, (
            f"transition compare: PARTIAL {passed - hover_unverified} verified "
            f"pass row(s), {hover_unverified} hover-unverified abstention(s)"
        )
    return True, f"transition compare: {passed} verified pass row(s) / 0 fail"


def hover_state_partial_result(
    ref_dir: Path,
    text: str,
) -> tuple[bool, str] | None:
    """Validate an honest partial hover-video abstention.

    A partial result is never a standalone pass. It is valid only when every
    measured target has an explicit terminal verdict and the plan also requires
    independently passing transition-fires, transition-compare, and composite
    transition-proof evidence.
    """

    divergence = re.search(
        r"(\d+)/(\d+)\s+hover target-run\(s\)\s+diverged",
        text,
    )
    if divergence:
        return False, (
            "hover-state: "
            f"{divergence.group(1)}/{divergence.group(2)} "
            "target-run(s) diverged"
        )
    coverage_lines = re.findall(
        r"^# coverage:.*$",
        text,
        flags=re.MULTILINE,
    )
    summary_lines = re.findall(
        r"^⚠️\s+\d+/\d+\s+hover target-run\(s\) unmeasurable\s*$",
        text,
        flags=re.MULTILINE,
    )
    if not coverage_lines and not summary_lines:
        return None
    if len(coverage_lines) != 1:
        return False, "hover-state: expected exactly one coverage summary"
    coverage = re.fullmatch(
        r"# coverage:\s*measured=(\d+)\s+failed=(\d+)\s+"
        r"(?:unmeasurable=(\d+)\s+)?fallbackFailed=(\d+)\s*",
        coverage_lines[0],
    )
    if coverage is None:
        return False, "hover-state: malformed coverage summary"
    measured = int(coverage.group(1))
    failed = int(coverage.group(2))
    unmeasurable_group = coverage.group(3)
    unmeasurable = int(unmeasurable_group or 0)
    fallback_failed = int(coverage.group(4))
    if failed > 0:
        return False, f"hover-state: {failed}/{measured} target-run(s) failed"
    if fallback_failed > 0:
        return False, (
            f"hover-state: {fallback_failed} fallback probe(s) failed"
        )
    terminal_unmeasurable = re.findall(
        r"^⚠️\s+.+?\s+unmeasurable-after-retry\s+"
        r"\[[^\]\r\n]+\](?:\s+—.*)?$",
        text,
        flags=re.MULTILINE,
    )
    if unmeasurable_group is None and (
        summary_lines or terminal_unmeasurable
    ):
        return False, (
            "hover-state: coverage summary omits unmeasurable count despite "
            "terminal unmeasurable evidence"
        )

    success_matches = re.findall(
        r"^✅\s+(.+?)\s+(?:clean|pass-after-(?:retry|reference-self-"
        r"calibration|complementary-reference-self-calibration|static-"
        r"discrete-hover-state-calibration))\s+"
        r"\[([^\]\r\n]+)\](?:\s+—.*)?$",
        text,
        flags=re.MULTILINE,
    )
    unmeasurable_matches = re.findall(
        r"^⚠️\s+(.+?)\s+unmeasurable-after-retry\s+"
        r"\[([^\]\r\n]+)\](?:\s+—.*)?$",
        text,
        flags=re.MULTILINE,
    )
    success_targets = set(success_matches)
    unmeasurable_targets = set(unmeasurable_matches)
    if success_targets & unmeasurable_targets:
        return False, (
            "hover-state: one target has conflicting successful and "
            "unmeasurable verdicts"
        )

    section_matches = re.findall(
        r"^##\s+(.+?)\s+\((?:css-)?hover\)\s+\[([^\]\r\n]+)\]\s*$"
        r"\nselector:\s*(\S[^\r\n]*?)\s*$",
        text,
        flags=re.MULTILINE,
    )
    section_selectors: dict[tuple[str, str], str] = {}
    for name, viewport, selector in section_matches:
        key = (name, viewport)
        if key in section_selectors:
            return False, (
                "hover-state: duplicate target section for "
                f"{name!r} [{viewport}]"
            )
        section_selectors[key] = selector

    fallback_lines = re.findall(
        r"^hover-fallback:.*$",
        text,
        flags=re.MULTILINE,
    )
    if len(fallback_lines) != 1:
        return False, (
            "hover-state: coverage requires exactly one fallback tally"
        )
    fallback = re.fullmatch(
        r"hover-fallback:\s*status=([a-z-]+)\s+verified=(\d+)\s+"
        r"static=(\d+)\s+failed=(\d+)\s*",
        fallback_lines[0],
    )
    if (
        fallback is None
        or fallback.group(1) != "pass"
        or int(fallback.group(4)) != 0
    ):
        return False, "hover-state: fallback tally is not a clean pass"

    if unmeasurable == 0:
        if summary_lines or terminal_unmeasurable or unmeasurable_matches:
            return False, (
                "hover-state: unmeasurable summary disagrees with coverage"
            )
        if measured <= 0:
            return None
        if (
            len(success_matches) != measured
            or len(success_targets) != measured
        ):
            return False, (
                "hover-state: complete coverage requires exactly "
                f"{measured} unique explicit successful target verdict(s); "
                f"found {len(success_targets)}"
            )
        missing_sections = success_targets - set(section_selectors)
        if missing_sections:
            return False, (
                "hover-state: successful target lacks an exact selector "
                "section"
            )
        success_summaries = re.findall(
            r"^✅\s+all\s+(\d+)\s+(?:measured\s+)?hover target-run\(s\) "
            r"within SSIM threshold(?:;.*)?$",
            text,
            flags=re.MULTILINE,
        )
        if len(success_summaries) != 1:
            return False, (
                "hover-state: complete coverage requires exactly one "
                "success summary"
            )
        if int(success_summaries[0]) != measured:
            return False, (
                "hover-state: success summary disagrees with coverage"
            )
        return True, (
            f"hover-state: PASS {measured}/{measured} target-run(s) "
            "explicitly successful with clean fallback coverage"
        )
    if len(summary_lines) != 1:
        return False, (
            "hover-state: expected exactly one partial-unmeasurable summary"
        )
    summary = re.fullmatch(
        r"⚠️\s+(\d+)/(\d+)\s+hover target-run\(s\) unmeasurable\s*",
        summary_lines[0],
    )
    assert summary is not None
    summary_unmeasurable, summary_total = map(int, summary.groups())
    if (
        summary_unmeasurable != unmeasurable
        or summary_total != measured
    ):
        return False, (
            "hover-state: partial summary disagrees with coverage "
            f"({summary_unmeasurable}/{summary_total} vs "
            f"{unmeasurable}/{measured})"
        )
    if measured <= 0 or unmeasurable >= measured:
        return False, (
            f"hover-state: all {measured} target-run(s) unmeasurable"
        )

    expected_clean = measured - unmeasurable
    if (
        len(success_matches) != expected_clean
        or len(success_targets) != expected_clean
    ):
        return False, (
            "hover-state: partial coverage requires exactly "
            f"{expected_clean} unique explicit clean target verdict(s); "
            f"found {len(success_targets)}"
        )
    if (
        len(unmeasurable_matches) != unmeasurable
        or len(unmeasurable_targets) != unmeasurable
    ):
        return False, (
            "hover-state: partial coverage requires exactly "
            f"{unmeasurable} unique explicit unmeasurable target verdict(s); "
            f"found {len(unmeasurable_targets)}"
        )
    unmeasurable_target_selectors: dict[str, str] = {}
    for target in unmeasurable_targets:
        selector = section_selectors.get(target)
        if selector is None:
            return False, (
                "hover-state: unmeasurable target lacks an exact selector "
                f"section: {target[0]!r} [{target[1]}]"
            )
        prior = unmeasurable_target_selectors.get(target[0])
        if prior is not None and prior != selector:
            return False, (
                "hover-state: one target identity maps to multiple selectors"
            )
        unmeasurable_target_selectors[target[0]] = selector

    companion_error = _partial_hover_companion_error(
        ref_dir,
        unmeasurable_target_selectors,
    )
    if companion_error is not None:
        return False, f"hover-state: {companion_error}"
    return True, (
        f"hover-state: PARTIAL {expected_clean}/{measured} target-run(s) "
        f"explicitly clean; {unmeasurable} unmeasurable after retry, "
        "with exact target-bound transition fire evidence; hover parity "
        "remains unresolved and is reported as a warning"
    )


def transition_proof_evidence(ref_dir: Path, components: list[dict[str, Any]]) -> dict[str, Any]:
    spec = _strict_json_object(ref_dir / "transition-spec.json")
    raw_spec_entries = (
        spec.get("transitions") or spec.get("entries") or []
        if isinstance(spec, dict)
        else []
    )
    spec_entries = raw_spec_entries if isinstance(raw_spec_entries, list) else []
    spec_ids = [
        str(entry.get("id", "")).strip() if isinstance(entry, dict) else ""
        for entry in spec_entries
    ]

    fires = _strict_json_object(ref_dir / "transition-fires.json")
    raw_fire_entries = fires.get("entries") if isinstance(fires, dict) else None
    fire_entries = raw_fire_entries if isinstance(raw_fire_entries, list) else []
    fire_ids = [
        str(entry.get("id", "")).strip() if isinstance(entry, dict) else ""
        for entry in fire_entries
    ]
    tallies: dict[str, int] | None = None
    if isinstance(fires, dict):
        keys = ("total", "fired", "known_skip", "failed", "unmeasurable")
        if all(
            isinstance(fires.get(key), int) and not isinstance(fires.get(key), bool)
            for key in keys
        ):
            tallies = {key: int(fires[key]) for key in keys}

    return {
        "componentCount": len(components),
        "invalidComponentCount": sum(
            component.get("valid") is not True for component in components
        ),
        "specEntryCount": len(spec_entries),
        "specEntryIds": spec_ids,
        "transitionFireTallies": tallies,
        "transitionFireEntryIds": fire_ids if isinstance(raw_fire_entries, list) else None,
    }


def transition_proof_semantic_error(ref_dir: Path, data: object) -> str | None:
    if not isinstance(data, dict):
        return "artifact is not an object"
    if data.get("schemaVersion") != 1:
        return "schemaVersion must be 1"
    status = data.get("status")
    if status not in {"pass", "fail", "skip"}:
        return "status must be pass, fail, or skip"
    components = data.get("components")
    reasons = data.get("reasons")
    evidence = data.get("evidence")
    rule = data.get("rule")
    if not isinstance(components, list) or not isinstance(reasons, list):
        return "components and reasons must be arrays"
    if not isinstance(evidence, dict):
        return "evidence summary is missing"
    if not isinstance(rule, str) or not rule.strip():
        return "composite rule is missing"

    plan_outputs = _transition_plan_outputs(ref_dir)
    expected_artifacts = list(TRANSITION_CORE_COMPONENTS) + [
        TRANSITION_VIDEO_COMPONENT
    ]
    if (
        (ref_dir / TRANSITION_COMPARE_COMPONENT).exists()
        or TRANSITION_COMPARE_COMPONENT in plan_outputs
    ):
        expected_artifacts.append(TRANSITION_COMPARE_COMPONENT)
    if (
        (ref_dir / TRANSITION_HOVER_COMPONENT).exists()
        or TRANSITION_HOVER_COMPONENT in plan_outputs
    ):
        expected_artifacts.append(TRANSITION_HOVER_COMPONENT)

    component_artifacts: list[str] = []
    invalid_notes: list[str] = []
    active_source_status = False
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            return f"components[{index}] is not an object"
        artifact = component.get("artifact")
        if not isinstance(artifact, str) or not artifact:
            return f"components[{index}].artifact is invalid"
        component_artifacts.append(artifact)
        if not isinstance(component.get("tier"), str):
            return f"components[{index}].tier is invalid"
        if not isinstance(component.get("present"), bool):
            return f"components[{index}].present is invalid"
        if not isinstance(component.get("valid"), bool):
            return f"components[{index}].valid is invalid"
        note = component.get("note")
        if not isinstance(note, str) or not note:
            return f"components[{index}].note is invalid"
        if component["valid"] is False:
            invalid_notes.append(f"{artifact}: {note}")
        if artifact in TRANSITION_CORE_COMPONENTS:
            source_status = component.get("sourceStatus")
            if source_status not in {"pass", "fail", "skip", "error", "n/a"}:
                return f"components[{index}].sourceStatus is invalid"
            source = _strict_json_object(ref_dir / artifact)
            actual_status = source.get("status", "n/a") if source else "n/a"
            if source_status != actual_status:
                return (
                    f"{artifact} sourceStatus={source_status!r} disagrees with "
                    f"source artifact status={actual_status!r}"
                )
            if actual_status not in {"skip", "n/a", None}:
                active_source_status = True

    if component_artifacts != expected_artifacts:
        return (
            "component artifact sequence/cardinality disagrees with required "
            f"components; expected={expected_artifacts}, actual={component_artifacts}"
        )
    if len(set(component_artifacts)) != len(component_artifacts):
        return "component artifacts contain duplicates"

    recomputed_evidence = transition_proof_evidence(
        ref_dir, [component for component in components if isinstance(component, dict)]
    )
    if evidence != recomputed_evidence:
        return (
            "evidence summary disagrees with canonical transition inputs; "
            f"expected={recomputed_evidence}, actual={evidence}"
        )

    spec_ids = recomputed_evidence["specEntryIds"]
    if status == "pass":
        if any(not entry_id for entry_id in spec_ids):
            return "pass proof has blank transition-spec identities"
        duplicates = sorted(
            entry_id for entry_id, count in Counter(spec_ids).items() if count > 1
        )
        if duplicates:
            return f"pass proof has duplicate transition-spec identities: {duplicates}"
        fire_ids = recomputed_evidence["transitionFireEntryIds"]
        tallies = recomputed_evidence["transitionFireTallies"]
        if spec_ids:
            if not isinstance(fire_ids, list):
                return "pass proof is missing transition-fires entry identities"
            if Counter(fire_ids) != Counter(spec_ids):
                return (
                    "transition-fires identities disagree with transition-spec; "
                    f"spec={spec_ids}, fires={fire_ids}"
                )
            if not isinstance(tallies, dict):
                return "pass proof is missing transition-fires summary tallies"
            recomputed_tallies = {
                "total": len(fire_ids),
                "fired": 0,
                "known_skip": 0,
                "failed": 0,
                "unmeasurable": 0,
            }
            fires = _strict_json_object(ref_dir / "transition-fires.json")
            entries = fires.get("entries") if isinstance(fires, dict) else None
            assert isinstance(entries, list)
            mapping = {
                "pass": "fired",
                "degraded": "fired",
                "known-skip": "known_skip",
                "fail": "failed",
                "unmeasurable": "unmeasurable",
            }
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict) or entry.get("status") not in mapping:
                    return f"transition-fires entries[{index}] has invalid status"
                recomputed_tallies[mapping[str(entry["status"])]] += 1
            if tallies != recomputed_tallies:
                return (
                    "transition-fires tallies disagree with entries; "
                    f"reported={tallies}, recomputed={recomputed_tallies}"
                )

    tallies = recomputed_evidence["transitionFireTallies"]
    zero_measured_motion = (
        isinstance(tallies, dict)
        and tallies["total"] > 0
        and tallies["fired"] == 0
        and tallies["failed"] == 0
        and tallies["unmeasurable"] > 0
    )
    expected_status = (
        "fail"
        if invalid_notes or zero_measured_motion
        else ("pass" if active_source_status else "skip")
    )
    if status != expected_status:
        return f"status={status!r} disagrees with components={expected_status!r}"
    if any(not isinstance(reason, str) for reason in reasons):
        return "reasons contains a non-string value"
    if not all(note in reasons for note in invalid_notes):
        return "reasons omits one or more invalid component verdicts"
    if zero_measured_motion and not any(
        "zero measured motion" in reason for reason in reasons
    ):
        return "reasons omits the zero-measured-motion composite failure"
    return None
