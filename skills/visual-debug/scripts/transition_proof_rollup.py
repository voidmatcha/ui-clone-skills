#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ui_clone.evidence_validation import (  # noqa: E402
    hover_state_partial_result,
    transition_compare_text_result,
    transition_proof_evidence,
    transition_proof_semantic_error,
)

ref_dir = Path(sys.argv[1])
out_path = Path(sys.argv[2])

expected: set[str] = set()
spec_path = ref_dir / "transition-spec.json"
plan_path = ref_dir / "verification-plan.json"
VIDEO_MOTION_PRODUCES = "transitions/video-motion-result.txt"
if plan_path.exists():
    try:
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        for row in plan_data.get("requiredChecks", []):
            produces = row.get("produces")
            if produces:
                expected.add(produces)
    except Exception:
        pass

# Spec file existence is the canonical "this site has transitions"
# signal — verification-plan only adds rows when the spec is present
# with at least one entry.
spec_entry_count = 0
spec_entry_ids: list[str] = []
if spec_path.exists():
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec_entries = spec.get("transitions") or spec.get("entries") or []
        if isinstance(spec_entries, list):
            spec_entry_count = len(spec_entries)
            spec_entry_ids = [
                str(entry.get("id", "")).strip()
                if isinstance(entry, dict)
                else ""
                for entry in spec_entries
            ]
    except Exception:
        pass
spec_has_entries = spec_entry_count > 0

def read_json_safe(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _int_field(d: dict | None, key: str) -> int:
    if not isinstance(d, dict):
        return 0
    try:
        return int(d.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def only_reset_skipped_transitions() -> bool:
    """True when every transition-spec entry was a no-op reset known-skip."""

    fires = read_json_safe(ref_dir / "transition-fires.json")
    if not isinstance(fires, dict) or fires.get("status") != "pass":
        return False
    total = _int_field(fires, "total")
    if total <= 0:
        return False
    if _int_field(fires, "failed") or _int_field(fires, "unmeasurable"):
        return False
    if _int_field(fires, "fired") != 0:
        return False
    if _int_field(fires, "known_skip") != total:
        return False

    spec_impl = read_json_safe(ref_dir / "spec-implementation-coverage.json")
    if isinstance(spec_impl, dict) and _int_field(spec_impl, "total") != 0:
        return False

    # F9: an all-known-skip tally is NOT sufficient — an entry can be known-skip
    # because it is absent from the ref page (cross-page selector) or listed in
    # asset-substitution.json skips[], NOT because it is a reset-only hover. If any
    # spec entry is a SCROLL/scrub/video/carousel/splash trigger, video-motion is a
    # relevant probe and its verdict must NOT be masked. Only when EVERY spec entry
    # is a hover trigger is there genuinely no scroll/splash motion to measure, so
    # a video-motion / hover-state "failure" on such a page is measurement noise.
    spec = read_json_safe(ref_dir / "transition-spec.json")
    transitions = spec.get("transitions") if isinstance(spec, dict) else None
    if not isinstance(transitions, list) or not transitions:
        return False
    for t in transitions:
        if not isinstance(t, dict):
            return False
        trigger = str(t.get("trigger", "")).lower()
        if "hover" not in trigger:
            return False
    return True


def measure_spec_coverage(d: dict | None) -> tuple[bool, str]:
    if d is None:
        return False, "missing"
    if d.get("status") == "skip":
        return True, "skipped"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    total = int(d.get("total", 0))
    covered = int(d.get("covered", 0))
    if total > 0 and covered < total:
        return False, f"partial coverage {covered}/{total} despite pass"
    return True, f"{covered}/{total} covered"

def measure_spec_impl(d: dict | None) -> tuple[bool, str]:
    if d is None:
        return False, "missing"
    if d.get("status") == "skip":
        return True, "skipped"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    total = int(d.get("total", 0))
    with_motion = int(d.get("withMotion", 0))
    if total > 0 and with_motion < total:
        return False, f"only {with_motion}/{total} entries have motion declaration"
    return True, f"{with_motion}/{total} with motion"

def measure_transition_coverage(d: dict | None) -> tuple[bool, str]:
    if d is None:
        return True, "not produced (no transition-spec entries to probe)"
    elements = d.get("animatedElements") or []
    # animatedElements may be a list of selector strings (Phase 6d ref-side
    # extraction) or a list of per-element dicts (runtime probe). Normalize
    # bare strings to {"selector": s} so the .get() calls below never hit a
    # str. Robustness fix only — string entries carry no samples, so they fall
    # through to the same runtime-proof requirement as dict entries without
    # samples; pass/fail semantics are unchanged.
    elements = [
        {"selector": el} if isinstance(el, str) else el
        for el in elements
        if isinstance(el, (str, dict))  # noqa: UP038 - macOS /usr/bin/python3 is still 3.9.
    ]
    if not elements:
        return False, "probe ran but found 0 animated elements (URL or hydration issue)"
    # transition-coverage.json may be produced by Phase 6d as ref-side
    # extraction (no samples or one baseline sample per element) OR by a
    # post-implement runtime probe (two or more samples per element). Treat
    # declaration-only artifacts as inventory and let runtime proof artifacts
    # carry the firing evidence instead.
    validated_samples: list[list[dict[str, Any]]] = []
    for element_index, el in enumerate(elements):
        raw_samples = el.get("samples")
        if raw_samples is None:
            raw_samples = []
        if not isinstance(raw_samples, list):
            return False, (
                f"animated element {element_index} samples must be an array, "
                f"got {type(raw_samples).__name__}"
            )
        samples: list[dict[str, Any]] = []
        for sample_index, sample in enumerate(raw_samples):
            if not isinstance(sample, dict):
                return False, (
                    f"animated element {element_index} sample {sample_index} "
                    f"must be an object, got {type(sample).__name__}"
                )
            samples.append(sample)
        validated_samples.append(samples)

    has_runtime_samples = any(len(samples) >= 2 for samples in validated_samples)
    if not has_runtime_samples:
        runtime_sources = runtime_proof_sources()
        if not runtime_sources:
            return False, (
                f"{len(elements)} ref-side animated element(s) declared "
                "(Phase 6d schema, no multi-sample runtime probe) but no runtime proof "
                "artifact passed"
            )
        return True, (
            f"{len(elements)} ref-side animated element(s) declared "
            "(Phase 6d schema, no multi-sample runtime probe — runtime proof carried by "
            f"{', '.join(runtime_sources)})"
        )
    # Each element should have ≥2 samples and at least one non-default value
    settled = 0
    for samples in validated_samples:
        if len(samples) >= 2:
            # Look for any property that changes across samples
            keys: set[str] = set()
            for s in samples:
                keys.update(s.keys())
            keys.discard("scrollY")
            for k in keys:
                values = {str(s.get(k)) for s in samples if k in s}
                if len(values) > 1:
                    settled += 1
                    break
    if settled == 0 and len(elements) > 0:
        return False, f"{len(elements)} elements probed, none showed value change across scroll samples"
    return True, f"{settled}/{len(elements)} elements showed runtime mutation"

def measure_reveal(d: dict | None) -> tuple[bool, str]:
    if d is None:
        return True, "not produced (no IO-reveal signal)"
    if d.get("status") == "skip":
        return True, "skipped"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    return True, "IO reveals advanced"

def measure_scroll_end(d: dict | None) -> tuple[bool, str]:
    if d is None:
        return True, "not produced (no scroll-scrub signal)"
    if d.get("status") == "skip":
        return True, "skipped"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    return True, "scroll-scrub settles"

def measure_keyframes(d: dict | None) -> tuple[bool, str]:
    if d is None:
        return True, "not produced (advisory; no enforcement when missing)"
    only_ref = d.get("onlyOnRef") or d.get("ref_only") or []
    diff_steps = d.get("differentSteps") or d.get("different_steps") or []
    if only_ref or diff_steps:
        return False, f"{len(only_ref)} ref-only keyframes, {len(diff_steps)} step diffs"
    return True, "keyframes parity"

def measure_transition_fires(d: dict | None) -> tuple[bool, str]:
    if d is None:
        if "transition-fires.json" in expected:
            return False, "transition-fires expected by verification-plan but artifact missing"
        return True, "not produced (runtime fire check not required)"
    if d.get("status") == "skip":
        if spec_has_entries:
            return False, (
                f"status=skip with {spec_entry_count} transition-spec "
                "entry(ies) requiring a measured or known-skip denominator"
            )
        return True, "skipped"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    total = _int_field(d, "total")
    failed = _int_field(d, "failed")
    fired = _int_field(d, "fired")
    known_skip = _int_field(d, "known_skip")
    unmeasurable = _int_field(d, "unmeasurable")
    if spec_has_entries and total <= 0:
        return False, "0 transition(s) probed despite transition-spec entries"
    if spec_has_entries and total != spec_entry_count:
        return False, (
            f"transition fire probe denominator {total}/{spec_entry_count}; "
            "every transition-spec entry, including known skips, must be counted"
        )
    raw_entries = d.get("entries")
    if spec_has_entries:
        if any(not entry_id for entry_id in spec_entry_ids):
            return False, "transition-spec contains a missing/invalid transition identity"
        duplicate_spec_ids = sorted(
            entry_id
            for entry_id, count in Counter(spec_entry_ids).items()
            if count > 1
        )
        if duplicate_spec_ids:
            return False, (
                "transition-spec contains duplicate transition identities: "
                f"{duplicate_spec_ids}"
            )
        if not isinstance(raw_entries, list):
            return False, (
                "transition-fires entries missing; exact transition identities "
                "cannot be reconciled with transition-spec"
            )

    if isinstance(raw_entries, list):
        artifact_ids: list[str] = []
        recomputed = {
            "total": len(raw_entries),
            "fired": 0,
            "known_skip": 0,
            "failed": 0,
            "unmeasurable": 0,
        }
        for index, entry in enumerate(raw_entries):
            if not isinstance(entry, dict):
                return False, f"transition-fires entries[{index}] is not an object"
            entry_id = str(entry.get("id", "")).strip()
            if not entry_id:
                return False, (
                    f"transition-fires entries[{index}] has no transition identity"
                )
            artifact_ids.append(entry_id)
            entry_status = entry.get("status")
            if entry_status in {"pass", "degraded"}:
                recomputed["fired"] += 1
            elif entry_status == "known-skip":
                recomputed["known_skip"] += 1
            elif entry_status == "fail":
                recomputed["failed"] += 1
            elif entry_status == "unmeasurable":
                recomputed["unmeasurable"] += 1
            else:
                return False, (
                    f"transition-fires entries[{index}] has unknown status="
                    f"{entry_status!r}"
                )

        if spec_has_entries:
            duplicate_artifact_ids = sorted(
                entry_id
                for entry_id, count in Counter(artifact_ids).items()
                if count > 1
            )
            if duplicate_artifact_ids:
                return False, (
                    "transition-fires contains duplicate transition identities: "
                    f"{duplicate_artifact_ids}"
                )
            if Counter(artifact_ids) != Counter(spec_entry_ids):
                missing = list((Counter(spec_entry_ids) - Counter(artifact_ids)).elements())
                unexpected = list(
                    (Counter(artifact_ids) - Counter(spec_entry_ids)).elements()
                )
                return False, (
                    "transition-fires transition identities do not match "
                    f"transition-spec; missing={sorted(missing)}, "
                    f"unexpected={sorted(unexpected)}"
                )

        reported = {
            "total": total,
            "fired": fired,
            "known_skip": known_skip,
            "failed": failed,
            "unmeasurable": unmeasurable,
        }
        if reported != recomputed:
            return False, (
                "transition-fires summary tallies disagree with entries; "
                f"reported={reported}, recomputed={recomputed}"
            )
    if failed > 0:
        return False, f"{failed}/{total} transition(s) did not fire"
    if total > 0 and fired + known_skip + unmeasurable < total:
        return False, (
            f"only {fired}+{known_skip} known-skip+{unmeasurable} "
            f"unmeasurable out of {total} transition(s)"
        )
    return True, f"{fired}/{total} fired ({unmeasurable} unmeasurable)"

def measure_video_motion(path: Path) -> tuple[bool, str]:
    if only_reset_skipped_transitions():
        return True, "skipped (only reset-only hover specs; no motion expected)"
    if not path.exists():
        if VIDEO_MOTION_PRODUCES in expected:
            return False, "video-motion expected by verification-plan but artifact missing"
        return True, "not produced (no scroll/splash signal or comprehensive tier skipped)"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False, "video-motion-result.txt unreadable"
    # Loop-10 fix (c): every complete run stamps a completion sentinel as
    # its last line. A result file without it was truncated mid-run
    # (dispatcher timeout/kill) — whatever markers survived describe an
    # unfinished measurement, never a verdict.
    if "# video-motion-compare: COMPLETE" not in text:
        return False, (
            "video-motion-result.txt missing completion sentinel — the run "
            "was truncated (dispatcher timeout/kill); re-run video-motion-compare"
        )
    # Look for clear PASS / FAIL markers. ✅ on its own line = success in the
    # trajectory pre-filter; "Pass: N Fail: M" is the SSIM tally.
    #
    # video-transition-compare prints the tally COLORED ("Pass: <GREEN>N</>,
    # Fail: <RED>M</>") with no TTY guard, so on a multi-mode run the colored
    # splash tally is invisible to a \s*\d+ regex and a single re.search matched
    # only a later PLAIN tally — masking splash failures (proven on
    # realfood-e2e-11: 126 failing splash frames rolled up as pass). Strip ANSI,
    # trust the multi-mode summary line, then sum EVERY per-mode tally.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    if re.search(r"❌\s*\d+/\d+\s*mode\(s\)\s*diverged", plain):
        return False, "video-motion: one or more modes diverged"
    tallies = re.findall(r"Pass:\s*(\d+).*?Fail:\s*(\d+)", plain)
    if tallies:
        passed = sum(int(p) for p, _ in tallies)
        failed = sum(int(f) for _, f in tallies)
        if passed + failed == 0:
            return False, "vacuous: video-motion reports 0 pass / 0 fail (probe didn't run)"
        if failed > 0:
            return False, f"video-motion: {passed} pass / {failed} fail"
        return True, f"video-motion: {passed} pass / 0 fail"
    if "mode(s) within SSIM threshold" in plain:
        return True, "video-motion: all modes within SSIM threshold"
    if "trajectory pre-filter passed" in plain:
        return True, "trajectory pre-filter passed"
    if "trajectory pre-filter FAILED" in plain or "early-exit on trajectory fail" in plain:
        return False, "trajectory pre-filter failed"
    return False, "no PASS/FAIL marker in video-motion-result.txt"


def measure_transition_compare(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "transition compare result missing"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False, "transition compare result unreadable"
    return transition_compare_text_result(
        text,
        allow_empty=not spec_has_entries,
    )

HOVER_STATE_PRODUCES = "transitions/hover-state-result.txt"


def measure_hover_state(path: Path) -> tuple[bool, str]:
    if only_reset_skipped_transitions():
        return True, "skipped (only reset-only hover specs; no motion expected)"
    # Only enforced when the verification-plan demanded the hover row.
    plan_required = HOVER_STATE_PRODUCES in expected
    if not path.exists():
        if plan_required:
            return False, "hover-state expected by verification-plan but artifact missing"
        return True, "not produced (no hover signal)"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False, "hover-state-result.txt unreadable"
    # The "no hover regions" / "no regions.json" / "nothing to compare" exit is a
    # SILENT SKIP. It is only valid when the plan did NOT require hover. When the
    # plan required hover (hover row present) but the gate found nothing to
    # compare, the hover signal sources disagree (the plan's hover signal came
    # from interactions/hoverDelta/states-hover-manifest, but regions.json
    # triggerType carried no hover entries) and the motion-arc check never ran —
    # invalid.
    if (
        "no hover regions found" in text
        or "no regions.json" in text
        or "nothing to compare" in text
        or "hover-state compare skipped" in text
    ):
        if plan_required:
            return False, (
                "plan required hover-state-compare but hover-state-compare found "
                "0 hover regions in regions.json — hover motion-arc check silently "
                "skipped (regions.json triggerType lacks the hover entries the "
                "plan's hover signal implied)"
            )
        return True, "no hover regions (plan did not require hover)"
    # Real run: parse the summary tally lines hover-state-compare.sh writes.
    m = re.search(r"(\d+)/(\d+)\s+hover target-run\(s\)\s+diverged", text)
    if m:
        return False, f"hover-state: {m.group(1)}/{m.group(2)} target-run(s) diverged"
    partial = hover_state_partial_result(ref_dir, text)
    if partial is not None:
        return partial
    # hover-state-compare.sh writes "all N measured hover target-run(s)" since
    # the review-2 per-entry-accounting hardening; accept both wordings.
    m = re.search(r"all\s+(\d+)\s+(?:measured\s+)?hover target-run\(s\)\s+within SSIM threshold", text)
    if m:
        if int(m.group(1)) == 0:
            # 0 executed runs is valid ONLY when every selected target carries
            # a documented `known-skip:` reason (mount-gated overlay UI —
            # asset-substitution skips[] or absence parity verified live).
            # Sites whose every :hover rule targets conditionally-mounted UI
            # legitimately execute nothing; hover coverage is then carried by
            # transition-compare's hover rows. A 0-run tally WITHOUT skip
            # lines means target selection silently produced nothing — vacuous.
            skips = re.findall(r"^##\s+\S.*known-skip:", text, flags=re.MULTILINE)
            if skips:
                return True, (
                    f"hover-state: 0 target-runs, all {len(skips)} target(s) "
                    "known-skip (documented mount-gated UI; coverage carried "
                    "by transition-compare hover rows)"
                )
            return False, "vacuous: 0 hover target-runs executed"
        return True, f"hover-state: {m.group(1)} target-run(s) clean"
    return False, "no PASS/FAIL summary in hover-state-result.txt"


def _text_has_hover(value: object) -> bool:
    if isinstance(value, dict):
        return any(_text_has_hover(v) for v in value.values())
    if isinstance(value, list):
        return any(_text_has_hover(v) for v in value)
    return "hover" in str(value).lower()

def transition_compare_can_prove_runtime() -> bool:
    """transition-compare is valid runtime proof only for hover-like specs."""
    spec = read_json_safe(spec_path)
    if spec:
        for row in spec.get("transitions") or spec.get("entries") or []:
            if isinstance(row, dict) and _text_has_hover(
                {
                    "id": row.get("id"),
                    "trigger": row.get("trigger"),
                    "type": row.get("type"),
                    "animation": row.get("animation"),
                }
            ):
                return True
    coverage = read_json_safe(ref_dir / "transition-coverage.json")
    if coverage:
        for row in coverage.get("animatedElements") or []:
            if isinstance(row, dict) and _text_has_hover(
                {
                    "id": row.get("id"),
                    "trigger": row.get("trigger"),
                    "transition": row.get("transition"),
                    "selector": row.get("selector"),
                }
            ):
                return True
    return False


def runtime_proof_sources() -> list[str]:
    sources: list[str] = []
    fires = read_json_safe(ref_dir / "transition-fires.json")
    ok, note = measure_transition_fires(fires)
    # VERIFY-M1: require motion to have ACTUALLY fired. The note starts with a
    # digit even for "0/5 fired (5 unmeasurable)", so a prefix check counted a
    # run where nothing fired as runtime proof — declaration-only Phase-6d
    # coverage then got certified "runtime proof carried by transition-fires"
    # with zero measured motion. Read fired from the dict, not the note prefix.
    if ok and fires and int(fires.get("fired", 0) or 0) > 0:
        sources.append("transition-fires")
    reveal = read_json_safe(ref_dir / "reveal-trigger.json")
    if reveal and reveal.get("status") == "pass":
        sources.append("reveal-trigger")
    scroll_end = read_json_safe(ref_dir / "scroll-completion.json")
    if scroll_end and scroll_end.get("status") == "pass":
        sources.append("scroll-end-completion")
    vm_path = ref_dir / "transitions" / "video-motion-result.txt"
    if vm_path.exists():
        ok, note = measure_video_motion(vm_path)
        if ok and (
            note.startswith("video-motion:")
            or note == "trajectory pre-filter passed"
        ):
            sources.append("video-motion")
    transition_compare_path = ref_dir / "transitions" / "result.txt"
    ok, note = measure_transition_compare(transition_compare_path)
    if (
        ok
        and note.startswith("transition compare:")
        and "PARTIAL" not in note
        and transition_compare_can_prove_runtime()
    ):
        sources.append("transition-compare")
    return sources


components: list[dict] = []

specs = [
    ("transition-spec-coverage.json", "Tier 3 static", measure_spec_coverage),
    ("spec-implementation-coverage.json", "Tier 3 static", measure_spec_impl),
    ("transition-coverage.json", "Tier 3 runtime", measure_transition_coverage),
    ("transition-fires.json", "Tier 3 runtime", measure_transition_fires),
    ("reveal-trigger.json", "Tier 3 runtime", measure_reveal),
    ("scroll-completion.json", "Tier 3 runtime", measure_scroll_end),
    ("keyframes-diff.json", "Tier 3 keyframes", measure_keyframes),
]

overall_fail = False
overall_skip = True
composite_reasons: list[str] = []

for name, tier, validator in specs:
    path = ref_dir / name
    if not path.exists() and expected and name not in expected:
        components.append({
            "artifact": name,
            "tier": tier,
            "present": False,
            "valid": True,
            "sourceStatus": "n/a",
            "note": "not applicable (check not in verification plan)",
        })
        continue
    if not path.exists() and not spec_has_entries and name in (
        "transition-spec-coverage.json",
        "spec-implementation-coverage.json",
        "transition-coverage.json",
    ):
        components.append({
            "artifact": name,
            "tier": tier,
            "present": False,
            "valid": True,
            "sourceStatus": "n/a",
            "note": "not applicable (no transition-spec.json entries for this site)",
        })
        continue
    data = read_json_safe(path) if path.exists() else None
    ok, note = validator(data)
    entry = {
        "artifact": name,
        "tier": tier,
        "present": path.exists(),
        "valid": ok,
        "sourceStatus": (data or {}).get("status", "n/a"),
        "note": note,
    }
    if not ok:
        overall_fail = True
    if (data or {}).get("status") not in ("skip", None):
        overall_skip = False
    components.append(entry)

# video-motion is plain text, not JSON
vm_path = ref_dir / "transitions" / "video-motion-result.txt"
ok, note = measure_video_motion(vm_path)
entry = {
    "artifact": "transitions/video-motion-result.txt",
    "tier": "Tier 3 video",
    "present": vm_path.exists(),
    "valid": ok,
    "note": note,
}
if not ok:
    overall_fail = True
components.append(entry)

# transition-compare is also plain text. If verification-plan required it
# (standard tier) OR the artifact exists, transition-proof must compose its
# verdict instead of allowing static/spec runtime probes to mask a hover/timing
# mismatch (.btn-arrow/card-image/swiper-wrapper failures).
tc_path = ref_dir / "transitions" / "result.txt"
if tc_path.exists() or "transitions/result.txt" in expected:
    ok, note = measure_transition_compare(tc_path)
    entry = {
        "artifact": "transitions/result.txt",
        "tier": "Tier 3 transition compare",
        "present": tc_path.exists(),
        "valid": ok,
        "note": note,
    }
    if not ok:
        overall_fail = True
    components.append(entry)

# hover-state-compare result is plain text. When the verification-plan required
# the hover row (comprehensive tier) OR the artifact exists, transition-proof
# must validate it — a `no hover regions found ... nothing to compare` silent
# skip while the plan demanded hover means the hover motion-arc check never ran
# and must NOT compose to PASS.
hs_path = ref_dir / "transitions" / "hover-state-result.txt"
if hs_path.exists() or HOVER_STATE_PRODUCES in expected:
    ok, note = measure_hover_state(hs_path)
    entry = {
        "artifact": HOVER_STATE_PRODUCES,
        "tier": "Tier 3 hover-state",
        "present": hs_path.exists(),
        "valid": ok,
        "note": note,
    }
    if not ok:
        overall_fail = True
    components.append(entry)

# transition-fires treats unmeasurable entries as an honest low-level
# abstention: the probe ran and did not observe a contradiction. The composite
# has a stricter proof obligation, though, and cannot promote an all-abstention
# result to PASS when zero transitions were actually measured. An all-known-skip
# reset-only hover tally has unmeasurable=0, so that legitimate skip remains
# unaffected.
fires = read_json_safe(ref_dir / "transition-fires.json")
if (
    isinstance(fires, dict)
    and fires.get("status") == "pass"
    and _int_field(fires, "total") > 0
    and _int_field(fires, "fired") == 0
    and _int_field(fires, "failed") == 0
    and _int_field(fires, "unmeasurable") > 0
):
    overall_fail = True
    composite_reasons.append(
        "transition-fires.json: zero measured motion "
        f"(0/{_int_field(fires, 'total')} fired; "
        f"{_int_field(fires, 'unmeasurable')} unmeasurable)"
    )

if overall_fail:
    composite = "fail"
elif overall_skip:
    composite = "skip"
else:
    composite = "pass"

reasons = composite_reasons + [
    f"{c['artifact']}: {c['note']}"
    for c in components if not c.get("valid", False)
]

payload = {
    "schemaVersion": 1,
    "status": composite,
    "components": components,
    "reasons": reasons,
    "evidence": transition_proof_evidence(ref_dir, components),
    "rule": (
        "Composite transition fidelity roll-up. Every transition-spec entry must "
        "be covered by an impl file AND that file must have a motion declaration "
        "AND the runtime probe must observe the transition firing. Partial "
        "coverage (covered<total) and measurement-free passes (empty "
        "animatedElements or zero fired with unmeasurable entries) compose to "
        "FAIL even when the individual gate's status field reads pass."
    ),
}

semantic_error = transition_proof_semantic_error(ref_dir, payload)
if semantic_error is not None:
    payload["status"] = "fail"
    reasons.append(f"semantic validation: {semantic_error}")

out_path.write_text(
    json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
)
final_status = str(payload["status"])
print(
    json.dumps(
        {
            "status": final_status,
            "components": len(components),
            "out": str(out_path),
        },
        ensure_ascii=False,
        allow_nan=False,
    )
)
sys.exit({"pass": 0, "skip": 0, "fail": 1}.get(final_status, 2))
