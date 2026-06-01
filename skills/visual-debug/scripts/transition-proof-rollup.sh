#!/usr/bin/env bash
# transition-proof-rollup.sh — composite transition-fidelity aggregator.
#
# Usage:
#   transition-proof-rollup.sh <ref-dir>
#
# 2026-05-22 SKILL.md Tier 3 + codex-rescue audit (a125b997): roll-up
# validator that confirms every transition-spec entry has BOTH static
# coverage (impl file references the spec id / selector / type) AND
# runtime evidence (browser actually triggered the transition).
#
# Aggregated source artifacts (read-only):
#   transition-spec-coverage.json     — every spec entry has ≥1 impl file
#   spec-implementation-coverage.json — every covered entry has motion declaration
#   transition-coverage.json          — runtime per-element scroll samples
#   reveal-trigger.json               — IO-driven reveals advance after IO fires
#   scroll-completion.json            — scroll-scrub reveals settle by maxScroll
#   keyframes-diff.json               — @keyframes match between ref and impl
#   transitions/result.txt            — hover/click compare verdicts (if present)
#   transitions/video-motion-result.txt — 60fps SSIM verdict (if present)
#
# Failure modes the rollup catches that individual gates miss:
#   - spec-coverage status=pass with covered<total (silent partial
#     coverage that the static gate didn't itself fail)
#   - spec-implementation withMotion < total (entries matched a file
#     but the file has no motion declaration)
#   - transition-coverage with empty animatedElements (probe ran but
#     found nothing — likely impl URL was wrong or page didn't load)
#   - keyframes-diff with "only-on-ref" or "different-steps" entries
#     present (impl missed an entrance animation)
#   - video-motion-result with non-zero FAIL count
#
# Writes:
#   <ref-dir>/transition-proof.json
#
# Exit 0 on pass/skip, 1 on any transition tier failure, 2 on setup error.

set -uo pipefail

REF_DIR="${1:?Usage: transition-proof-rollup.sh <ref-dir>}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

OUT="$REF_DIR/transition-proof.json"

python3 - "$REF_DIR" "$OUT" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

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
spec_has_entries = False
if spec_path.exists():
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec_has_entries = bool(spec.get("transitions") or spec.get("entries") or [])
    except Exception:
        pass

def read_json_safe(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

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
        if isinstance(el, (str, dict))
    ]
    if not elements:
        return False, "probe ran but found 0 animated elements (URL or hydration issue)"
    # transition-coverage.json may be produced by Phase 6d as ref-side
    # extraction (no samples or one baseline sample per element) OR by a
    # post-implement runtime probe (two or more samples per element). Treat
    # declaration-only artifacts as inventory and let runtime proof artifacts
    # carry the firing evidence instead.
    has_runtime_samples = any(len(el.get("samples") or []) >= 2 for el in elements)
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
    for el in elements:
        samples = el.get("samples") or []
        if len(samples) >= 2:
            # Look for any property that changes across samples
            keys = set()
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
        return True, "skipped"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    total = int(d.get("total", 0) or 0)
    failed = int(d.get("failed", 0) or 0)
    fired = int(d.get("fired", 0) or 0)
    known_skip = int(d.get("known_skip", 0) or 0)
    unmeasurable = int(d.get("unmeasurable", 0) or 0)
    if failed > 0:
        return False, f"{failed}/{total} transition(s) did not fire"
    if total > 0 and fired + known_skip + unmeasurable < total:
        return False, (
            f"only {fired}+{known_skip} known-skip+{unmeasurable} "
            f"unmeasurable out of {total} transition(s)"
        )
    return True, f"{fired}/{total} fired ({unmeasurable} unmeasurable)"

def measure_video_motion(path: Path) -> tuple[bool, str]:
    if not path.exists():
        if VIDEO_MOTION_PRODUCES in expected:
            return False, "video-motion expected by verification-plan but artifact missing"
        return True, "not produced (no scroll/splash signal or comprehensive tier skipped)"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False, "video-motion-result.txt unreadable"
    # Look for clear PASS / FAIL markers. ✅ on its own line = success
    # in the trajectory pre-filter; "Pass: N Fail: M" is the SSIM tally
    # in the comprehensive tier.
    m = re.search(r"Pass:\s*(\d+).*Fail:\s*(\d+)", text)
    if m:
        passed = int(m.group(1))
        failed = int(m.group(2))
        if passed + failed == 0:
            return False, "vacuous: video-motion reports 0 pass / 0 fail (probe didn't run)"
        if failed > 0:
            return False, f"video-motion: {passed} pass / {failed} fail"
        return True, f"video-motion: {passed} pass / 0 fail"
    if "trajectory pre-filter passed" in text:
        return True, "trajectory pre-filter passed"
    if "trajectory pre-filter FAILED" in text or "early-exit on trajectory fail" in text:
        return False, "trajectory pre-filter failed"
    return False, "no PASS/FAIL marker in video-motion-result.txt"


def measure_transition_compare(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "transition compare result missing"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False, "transition compare result unreadable"
    m = re.search(r"(\d+)\s+PASS,\s*(\d+)\s+FAIL", text)
    if not m:
        return False, "transition compare result has no PASS/FAIL tally"
    passed = int(m.group(1))
    failed = int(m.group(2))
    if passed + failed == 0:
        return False, "transition compare reports 0 pass / 0 fail"
    if failed > 0:
        return False, f"transition compare: {passed} pass / {failed} fail"
    return True, f"transition compare: {passed} pass / 0 fail"

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
    if ok and fires and note.startswith(tuple(str(i) for i in range(10))):
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

if overall_fail:
    composite = "fail"
elif overall_skip:
    composite = "skip"
else:
    composite = "pass"

reasons = [
    f"{c['artifact']}: {c['note']}"
    for c in components if not c.get("valid", False)
]

payload = {
    "schemaVersion": 1,
    "status": composite,
    "components": components,
    "reasons": reasons,
    "rule": (
        "Composite transition fidelity roll-up. Every transition-spec entry must "
        "be covered by an impl file AND that file must have a motion declaration "
        "AND the runtime probe must observe the transition firing. Partial "
        "coverage (covered<total) and measurement-free passes (empty "
        "animatedElements) compose to FAIL even when the individual gate's "
        "status field reads pass."
    ),
}

out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": composite, "components": len(components), "out": str(out_path)}, ensure_ascii=False))
sys.exit({"pass": 0, "skip": 0, "fail": 1}.get(composite, 2))
PY
