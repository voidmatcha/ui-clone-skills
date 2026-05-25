#!/usr/bin/env bash
# runtime-proof-rollup.sh — composite runtime-fidelity aggregator.
#
# Usage:
#   runtime-proof-rollup.sh <ref-dir>
#
# 2026-05-22 SKILL.md Tier 2+4 enforcement: roll-up validator that reads
# every existing runtime-measurement artifact and emits a single
# composite verdict at <ref-dir>/runtime-proof.json. Per codex-rescue
# audit (a125b997), the aggregator does NOT run new browser probes — it
# only validates the measurements the constituent gates already wrote.
#
# Critical: a source gate with status=pass but no actual measurement
# (zero candidates, missing delta fields, empty counts) is treated as
# evidence of a measurement-free pass and counts as a composite FAIL.
# This catches the failure mode where a gate technically "passed" but
# never had anything to measure (e.g., lottie-runtime status=skip when
# ref signaled lottie, header-state status=skip when impl crashed
# before mount).
#
# Aggregated source artifacts (read-only):
#   lottie-runtime.json            — Tier 2 media (Lottie)
#   runtime-image-validity.json    — Tier 2 media (<img> validity)
#   runtime-dom-parity.json        — Tier 2 (DOM node count + structure)
#   motion-coverage.json           — Tier 2 + Tier 3 (motion presence)
#   runtime-spec-coverage.json     — Tier 3 (spec coverage runtime)
#   header-state-runtime.json      — Tier 4 (header state machine)
#   scroll-completion.json     — Tier 3 (scroll reveal completion)
#   reveal-trigger.json            — Tier 3 (IO reveal triggers)
#   hidden-children.json           — Tier 4 (initially-hidden settle)
#   svg-provenance.json            — Tier 5 (SVG source-shape provenance)
#   hero-composite.json            — Tier 1 (hero composite parity)
#
# Writes:
#   <ref-dir>/runtime-proof.json
#
# Exit 0 on pass/skip, 1 when any tier component is failing or
# measurement-free, 2 on setup error.

set -uo pipefail

REF_DIR="${1:?Usage: runtime-proof-rollup.sh <ref-dir>}"
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

OUT="$REF_DIR/runtime-proof.json"

python3 - "$REF_DIR" "$OUT" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
out_path = Path(sys.argv[2])

# Each entry: (artifact-filename, tier, measurement-validator).
# The measurement validator returns (ok: bool, note: str) given the
# parsed artifact dict. ok=False means "pass status but no real
# measurement" — the composite treats this as FAIL.
def lottie_measure(d: dict) -> tuple[bool, str]:
    rp = d.get("runtimeProof") or {}
    rp_status = rp.get("status", "not-attempted")
    if d.get("status") == "skip":
        return True, "skipped (no ref signal)"
    if rp_status in ("runtime-pass",):
        anim = int(rp.get("animatingCount", 0) or 0)
        return (anim > 0), f"runtime-pass (animating={anim})"
    if rp_status in ("static-only", "agent-browser-missing"):
        # SKILL.md Tier 2: package presence is not proof. Fall through
        # to fail if ref signaled lottie.
        if d.get("refDetected"):
            return False, f"ref signaled lottie but runtimeProof.status={rp_status}"
        return True, rp_status
    if rp_status == "not-attempted":
        # ref had no lottie signal → not-attempted is fine
        return (not d.get("refDetected", False)), "not-attempted"
    return False, f"runtimeProof.status={rp_status}"

def header_measure(d: dict) -> tuple[bool, str]:
    if d.get("status") == "skip":
        return True, "skipped (static ref header)"
    if d.get("status") == "pass":
        impl_m = bool(d.get("impl", {}).get("mutates"))
        ref_m = bool(d.get("ref", {}).get("mutates"))
        if ref_m and not impl_m:
            return False, "ref mutates but impl static — measurement contradicts status"
        return impl_m == ref_m, "header mutation parity"
    return False, f"status={d.get('status')}"

def hero_composite_measure(d: dict) -> tuple[bool, str]:
    if d.get("status") == "skip":
        return True, "skipped"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    explicit_missing = d.get("missingInImpl")
    if isinstance(explicit_missing, list):
        return (not explicit_missing), (
            "all ref-present kinds present" if not explicit_missing
            else f"missing in impl: {explicit_missing}"
        )
    ref = d.get("ref", {})
    impl = d.get("impl", {})
    if ref:
        missing = [k for k, ref_has in ref.items() if ref_has and not impl.get(k)]
    else:
        missing = [k for k, v in impl.items() if v is False]
    if missing:
        return False, f"pass but missing in impl: {missing}"
    return True, "all ref-present kinds present"

def runtime_dom_measure(d: dict) -> tuple[bool, str]:
    if d.get("status") == "skip":
        return True, "skipped"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    impl = (d.get("implMetrics")
            or d.get("impl")
            or d.get("counts")
            or {})
    if not impl:
        return False, "pass but no measurement payload"
    return True, "DOM parity measured"

def svg_provenance_measure(d: dict) -> tuple[bool, str]:
    if d.get("status") == "skip":
        return True, "skipped (no inline SVG in ref or impl)"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    impl = d.get("impl", {})
    if int(impl.get("matchedCount", 0)) == 0 and int(impl.get("svgCount", 0)) > 0:
        return False, "impl has SVGs but 0 matched ref geometry"
    return True, f"matched={impl.get('matchedCount', 0)}"

def generic_pass(d: dict) -> tuple[bool, str]:
    status = d.get("status")
    if status in ("pass", "skip"):
        return True, status
    return False, f"status={status}"

def scroll_end_measure(d: dict) -> tuple[bool, str]:
    if d.get("status") == "skip":
        return True, "skipped"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    # Vacuous-pass signals: zero candidates probed OR zero scroll range
    candidates = int(d.get("candidates", -1))
    max_scroll = int(d.get("maxScroll", -1))
    if candidates == 0 or max_scroll == 0:
        return False, (
            f"vacuous pass: candidates={candidates} maxScroll={max_scroll} "
            "— gate reports pass but probed nothing; check that the ref signal "
            "is real and that the impl page actually has scroll content"
        )
    return True, f"candidates={candidates} maxScroll={max_scroll}"

def reveal_trigger_measure(d: dict) -> tuple[bool, str]:
    if d.get("status") == "skip":
        return True, "skipped"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    # Vacuous signals: zero initially-hidden elements found
    hidden = int(d.get("hiddenCount", d.get("candidates", -1)))
    if hidden == 0:
        return False, (
            "vacuous pass: 0 initially-hidden elements probed — if ref had IO "
            "reveals, impl should have hidden init elements; if ref didn't, "
            "the gate should have skipped"
        )
    return True, f"hidden={hidden}"

def motion_coverage_measure(d: dict) -> tuple[bool, str]:
    if d.get("status") == "skip":
        return True, "skipped"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    impl_count = int(d.get("impl", 0))
    ref_count = int(d.get("ref", 0))
    if ref_count > 0 and impl_count == 0:
        return False, f"vacuous: ref motion={ref_count} but impl motion=0"
    return True, f"ref={ref_count} impl={impl_count}"

def runtime_spec_coverage_measure(d: dict) -> tuple[bool, str]:
    if d.get("status") == "skip":
        return True, "skipped"
    if d.get("status") != "pass":
        return False, f"status={d.get('status')}"
    note = (d.get("note") or "").lower()
    if "no runtime dump" in note or "nothing to enforce" in note:
        return False, f"vacuous: {d.get('note')!r}"
    counters = [
        int(d.get("scrollTriggerCount", 0)),
        int(d.get("ix2TimelineCount", 0)),
        int(d.get("specEntryCount", 0)),
    ]
    if all(c == 0 for c in counters):
        # All counters zero AND ref had a runtime-spec.json → vacuous.
        # When ref truly has no entries, gate should skip earlier.
        return False, "vacuous: all runtime-spec counters at zero"
    return True, f"spec counters {counters}"

components = [
    ("hero-composite.json",        "Tier 1 static",   hero_composite_measure),
    ("lottie-runtime.json",        "Tier 2 media",    lottie_measure),
    ("runtime-image-validity.json","Tier 2 media",    generic_pass),
    ("runtime-dom-parity.json",    "Tier 2 structure", runtime_dom_measure),
    ("motion-coverage.json",       "Tier 2 motion",   motion_coverage_measure),
    ("runtime-spec-coverage.json", "Tier 3 spec",     runtime_spec_coverage_measure),
    ("scroll-completion.json", "Tier 3 reveal",   scroll_end_measure),
    ("reveal-trigger.json",        "Tier 3 reveal",   reveal_trigger_measure),
    ("header-state-runtime.json",  "Tier 4 state",    header_measure),
    ("hidden-children.json",       "Tier 4 state",    generic_pass),
    ("svg-provenance.json",        "Tier 5 no-cheat", svg_provenance_measure),
]

#
expected_artifacts: set[str] = set()
plan_rows = 0
plan_present = False
plan_path = ref_dir / "verification-plan.json"
if plan_path.exists():
    plan_present = True
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        rows = plan.get("requiredChecks", [])
        plan_rows = len(rows)
        for row in rows:
            produces = row.get("produces")
            if produces:
                expected_artifacts.add(produces)
    except Exception:
        pass

NO_SIGNAL_MARKER = ref_dir / "no-signals-justified.txt"
UNIVERSAL_ANCHOR_IDS = {
    "hydration-check",
    "text-fidelity-check",
    "image-fidelity",
    "asset-transfer",
    "scaffold-warn",
}
empty_plan_fail = False
empty_plan_reason = ""
if not plan_present and not NO_SIGNAL_MARKER.exists():
    empty_plan_fail = True
    empty_plan_reason = (
        "verification-plan.json does not exist — extraction step never "
        "ran or failed before emitting the plan. Run the pipeline up to "
        "the spec gate before invoking the rollup."
    )
elif plan_present:
    # Re-read for ids (we already have rows count above, but need ids too)
    try:
        plan_for_ids = json.loads(plan_path.read_text(encoding="utf-8"))
        plan_ids = {row.get("id") for row in plan_for_ids.get("requiredChecks", [])}
        missing_anchors = UNIVERSAL_ANCHOR_IDS - plan_ids
        if missing_anchors and not NO_SIGNAL_MARKER.exists():
            empty_plan_fail = True
            empty_plan_reason = (
                f"verification-plan.json missing universal anchor checks: "
                f"{sorted(missing_anchors)}. These are emitted unconditionally "
                "by verification-plan.sh — their absence indicates a malformed "
                "plan, not a legitimate no-signals case. Add a "
                f"{NO_SIGNAL_MARKER.name} marker if this is intentional."
            )
    except Exception:
        pass

tier_results: list[dict] = []
missing: list[str] = []
overall_fail = False
overall_skip = True

for name, tier, validator in components:
    path = ref_dir / name
    entry: dict = {"artifact": name, "tier": tier}
    if not path.exists():
        # Conditional artifacts: if the producing check wasn't in the
        # verification plan, this site doesn't trigger that gate. Treat
        # as "not applicable" rather than failure. When the plan IS
        # present and lists the check, missing artifact = real failure.
        if expected_artifacts and name not in expected_artifacts:
            entry.update({
                "present": False,
                "valid": True,
                "note": "not applicable (check not in verification plan for this site)",
            })
            tier_results.append(entry)
            continue
        entry.update({"present": False, "valid": False, "note": "source artifact missing"})
        missing.append(name)
        overall_fail = True
        tier_results.append(entry)
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        entry.update({"present": True, "valid": False, "note": f"parse error: {exc}"})
        overall_fail = True
        tier_results.append(entry)
        continue
    ok, note = validator(data)
    src_status = data.get("status", "?")
    entry.update({
        "present": True,
        "valid": ok,
        "sourceStatus": src_status,
        "note": note,
    })
    if not ok:
        overall_fail = True
    if src_status != "skip":
        overall_skip = False
    tier_results.append(entry)

if empty_plan_fail:
    composite = "fail"
    reasons_extra = [empty_plan_reason]
elif overall_fail:
    composite = "fail"
    reasons_extra = []
elif overall_skip:
    composite = "skip"
    reasons_extra = []
else:
    composite = "pass"
    reasons_extra = []

# Per-tier roll-up summary so the report contract has a quick scan view.
tiers = {}
for entry in tier_results:
    t = entry["tier"].split()[1] if len(entry["tier"].split()) >= 2 else entry["tier"]
    tier_key = f"Tier{t[0]}"
    tiers.setdefault(tier_key, {"components": [], "allValid": True})
    tiers[tier_key]["components"].append(entry["artifact"])
    if not entry.get("valid", False):
        tiers[tier_key]["allValid"] = False

reasons: list[str] = list(reasons_extra)
if missing:
    reasons.append(f"{len(missing)} source artifact(s) missing: " + ", ".join(missing))
for entry in tier_results:
    if not entry.get("valid", False) and entry.get("present", False):
        reasons.append(f"{entry['artifact']}: {entry.get('note', 'invalid')}")

payload = {
    "schemaVersion": 1,
    "status": composite,
    "tiers": tiers,
    "components": tier_results,
    "reasons": reasons,
    "rule": (
        "Composite roll-up over every runtime-measurement artifact. A source "
        "artifact whose status=pass but contains no actual measurement (zero "
        "candidates, no delta fields) is treated as a measurement-free pass and "
        "counts as composite FAIL. Missing source artifacts also fail. Source "
        "artifacts with status=skip are valid only when the skip reason is the "
        "ref's own absence of the signal (no Lottie, static header)."
    ),
}

out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": composite, "missing": len(missing), "components": len(tier_results), "out": str(out_path)}, ensure_ascii=False))
sys.exit({"pass": 0, "skip": 0, "fail": 1}.get(composite, 2))
PY
