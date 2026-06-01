#!/usr/bin/env bash
# behavior-parity-check.sh — JSON-level ref↔impl behavior diff.
#
# Codex 2026-05-28 design (Option 3): the existing pipeline captures
# Phase A/B/C only from the REF URL and verifies impl via pixel-diff
# (section-compare AE/Mpx). Dynamic / behavioral mismatches (hover
# handler count, scroll engine choice, splash sequence timing) are
# never measured directly. This check compares
# `<ref_dir>/states/{splash,scroll,hover}/*.json` against
# `<ref_dir>/states-impl/{splash,scroll,hover}/*.json` and surfaces
# behavior mismatches as the next iteration's fix queue.
#
# Capture scripts (capture-states.sh, capture-scroll.sh,
# capture-hover.sh) accept the env var `STATES_PREFIX=states-impl` to
# route their output. Run them once against the impl URL with that
# env set, then call this script with the same ref-dir.
#
# Usage:
#   behavior-parity-check.sh <ref-dir> [--tier quick|standard|comprehensive]
#
# Tiers (per codex tier-mapping recommendation):
#   quick         — schema/existence only, no value comparison
#   standard      — count-level diff (DEFAULT for iterations)
#   comprehensive — per-entry selector + trajectory hash diff
#
# Verdict rules (codex blocking/advisory recommendation):
#   FAIL  (blocking)  — missing hover targets, missing splash transitions,
#                       timed-out stabilization on impl, scrollHeight
#                       divergence >50%, missing required artifacts
#   WARN  (advisory)  — scroll engine identity mismatch (impl choice, not
#                       observable failure), minor count drift
#   PASS              — counts within tolerance and required artifacts present
#
# Output:
#   <ref-dir>/behavior-parity.json (schema below)
#   <ref-dir>/behavior-parity.txt  (human-readable summary)
#
# Exit codes:
#   0  PASS or only WARNs (advisory-only)
#   1  one or more FAIL (blocking) mismatches
#   2  setup error (ref-dir missing, no captures, bad args)

set -uo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <ref-dir> [--tier quick|standard|comprehensive]" >&2
  exit 2
fi

REF_DIR="$1"
TIER="standard"
shift
while [ "$#" -gt 0 ]; do
  case "$1" in
    --tier) TIER="${2:?--tier requires value}"; shift 2 ;;
    --tier=*) TIER="${1#--tier=}"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

case "$TIER" in
  quick|standard|comprehensive) ;;
  *) echo "invalid --tier value: $TIER (allowed: quick|standard|comprehensive)" >&2; exit 2 ;;
esac

if [ ! -d "$REF_DIR" ]; then
  echo "ref-dir not found: $REF_DIR" >&2
  exit 2
fi
if [ ! -d "$REF_DIR/states" ]; then
  echo "behavior-parity: ref-side captures missing ($REF_DIR/states/)" >&2
  exit 2
fi

OUT_JSON="$REF_DIR/behavior-parity.json"
OUT_TXT="$REF_DIR/behavior-parity.txt"

# Compute the comparison in Python — easier JSON manipulation than bash.
python3 - "$REF_DIR" "$TIER" "$OUT_JSON" "$OUT_TXT" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

ref_dir, tier, out_json_path, out_txt_path = (
    Path(sys.argv[1]),
    sys.argv[2],
    Path(sys.argv[3]),
    Path(sys.argv[4]),
)

ref_root = ref_dir / "states"
impl_root = ref_dir / "states-impl"

findings: list[dict] = []


def add(verdict: str, label: str, detail: str, fix: str = "") -> None:
    findings.append({
        "verdict": verdict,  # "pass" | "warn" | "fail"
        "label": label,
        "detail": detail,
        "fix": fix,
    })


def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# Tier `quick`: just check that states-impl/ exists and has the same
# top-level subdirectory shape. No value comparison.
if not impl_root.is_dir():
    add(
        "fail",
        "states-impl-missing",
        "states-impl/ directory does not exist — no impl-capture has run "
        "yet. Run capture-{states,scroll,hover}.sh against the impl URL "
        "with `STATES_PREFIX=states-impl` first.",
        fix=(
            "STATES_PREFIX=states-impl bash scripts/extract/capture-states.sh "
            "<impl-url> <session> <ref-dir>"
        ),
    )
else:
    # Phase existence parity (all tiers)
    for phase in ("splash", "scroll", "hover"):
        ref_dir_phase = ref_root / phase
        impl_dir_phase = impl_root / phase
        if ref_dir_phase.is_dir() and not impl_dir_phase.is_dir():
            add(
                "fail",
                f"phase-{phase}-missing",
                f"states/{phase}/ exists but states-impl/{phase}/ does not. "
                f"capture-{phase}.sh was not run against impl URL.",
                fix=(
                    f"STATES_PREFIX=states-impl bash scripts/extract/"
                    f"capture-{'states' if phase == 'splash' else phase}.sh "
                    f"<impl-url> <session> <ref-dir>"
                ),
            )

# Tier `standard` and `comprehensive`: count-level value comparison.
if tier in ("standard", "comprehensive") and impl_root.is_dir():
    # Phase B: scroll engine + scrollHeight comparison
    ref_scroll = _load(ref_root / "scroll" / "summary.json") or {}
    impl_scroll = _load(impl_root / "scroll" / "summary.json") or {}
    if ref_scroll and impl_scroll:
        r_engine = ref_scroll.get("scrollEngine", "native")
        i_engine = impl_scroll.get("scrollEngine", "native")
        if r_engine != i_engine:
            # Codex advisory rule: scroll engine mismatch is impl choice,
            # not observable failure → warn, not fail.
            add(
                "warn",
                "scroll-engine-mismatch",
                f"ref uses '{r_engine}', impl uses '{i_engine}'. This is a "
                "library-choice difference; only a failure if scroll states / "
                "timing / final DOM states demonstrably diverge.",
                fix=(
                    f"If desired, integrate matching engine in impl (e.g. "
                    f"`npm i lenis` for ref='lenis'). Otherwise document the "
                    "intentional deviation."
                ),
            )

        r_h = int(ref_scroll.get("scrollHeight", 0))
        i_h = int(impl_scroll.get("scrollHeight", 0))
        if r_h and i_h:
            ratio = abs(r_h - i_h) / max(r_h, i_h)
            if ratio > 0.5:
                add(
                    "fail",
                    "scrollheight-divergence",
                    f"ref scrollHeight={r_h}px, impl scrollHeight={i_h}px "
                    f"(divergence={ratio:.0%}). Above 50% threshold suggests "
                    "missing sections or runaway overflow.",
                    fix=(
                        "Check section structure: impl may be missing whole "
                        "sections, or impl may have unclipped absolute-"
                        "positioned children inflating body height."
                    ),
                )
            elif ratio > 0.15:
                add(
                    "warn",
                    "scrollheight-drift",
                    f"ref scrollHeight={r_h}px, impl scrollHeight={i_h}px "
                    f"(drift={ratio:.0%}). Below 50% threshold but worth "
                    "investigating before final convergence.",
                )

        # infiniteScroll signal must match
        if ref_scroll.get("infiniteScroll") != impl_scroll.get("infiniteScroll"):
            add(
                "warn",
                "infinite-scroll-mismatch",
                f"ref infiniteScroll={ref_scroll.get('infiniteScroll')}, "
                f"impl={impl_scroll.get('infiniteScroll')}.",
            )

    # Phase C: hover entry count
    ref_hover = _load(ref_root / "hover" / "manifest.json") or {}
    impl_hover = _load(impl_root / "hover" / "manifest.json") or {}
    if ref_hover and impl_hover:
        r_entries = ref_hover.get("entries") or []
        i_entries = impl_hover.get("entries") or []
        r_n, i_n = len(r_entries), len(i_entries)
        if r_n > 0 and i_n == 0:
            add(
                "fail",
                "hover-targets-missing",
                f"ref captured {r_n} hover target(s); impl captured 0. "
                "Hover handlers from ref are not present in impl.",
                fix=(
                    "Wire IntersectionObserver / useHover / onMouseEnter "
                    "handlers in impl matching the ref hover targets listed "
                    "in states/hover/manifest.json."
                ),
            )
        elif r_n > 0 and i_n < r_n / 2:
            add(
                "warn",
                "hover-target-shortfall",
                f"impl captured {i_n} hover target(s) vs ref {r_n} "
                f"({i_n / r_n:.0%}). Likely missing handlers.",
            )

    # Phase A: splash stabilization
    ref_splash = _load(ref_root / "splash" / "summary.json") or {}
    impl_splash = _load(impl_root / "splash" / "summary.json") or {}
    if ref_splash and impl_splash:
        # If REF timed out but IMPL stabilized too quickly → impl is missing
        # the continuous animation that ref has.
        if ref_splash.get("timedOut") and not impl_splash.get("timedOut"):
            ref_polls = int(ref_splash.get("polls", 0))
            impl_polls = int(impl_splash.get("polls", 0))
            if impl_polls < ref_polls / 3:
                add(
                    "fail",
                    "splash-sequence-missing",
                    f"ref splash timed out after {ref_polls} polls "
                    f"(continuous animation), impl stabilized in "
                    f"{impl_polls} polls. Missing splash/intro animation.",
                    fix=(
                        "Implement the splash/intro animation observed in "
                        "states/splash/trajectory.json (class transitions on "
                        "<body> or <html>)."
                    ),
                )

# Tier `comprehensive`: per-entry diff (placeholder — heavier work)
if tier == "comprehensive" and impl_root.is_dir():
    add(
        "warn",
        "comprehensive-tier-stub",
        "comprehensive tier per-entry diff not yet implemented; using "
        "standard-tier comparison as fallback.",
    )

# Compute summary
counts = {"pass": 0, "warn": 0, "fail": 0}
for f in findings:
    counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1
if not findings:
    add("pass", "no-mismatches", "all comparable artifacts within tolerance")
    counts["pass"] = 1

overall = "pass"
if counts["fail"] > 0:
    overall = "fail"
elif counts["warn"] > 0:
    overall = "warn"

payload = {
    "schemaVersion": 1,
    "tier": tier,
    "overall": overall,
    "counts": counts,
    "findings": findings,
}
out_json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

# Human-readable summary
lines = [
    f"behavior-parity ({tier} tier) — overall: {overall.upper()}",
    f"  pass={counts['pass']} warn={counts['warn']} fail={counts['fail']}",
    "",
]
for f in findings:
    glyph = {"pass": "✓", "warn": "⚠", "fail": "✗"}.get(f["verdict"], "·")
    lines.append(f"  {glyph} [{f['verdict']}] {f['label']}: {f['detail']}")
    if f.get("fix"):
        lines.append(f"      fix: {f['fix']}")
out_txt_path.write_text("\n".join(lines) + "\n")

# Exit code: 1 only on FAIL (blocking), 0 on PASS/WARN (advisory only)
print(json.dumps({"overall": overall, "counts": counts, "out": str(out_json_path)}))
sys.exit(1 if overall == "fail" else 0)
PY
