#!/usr/bin/env bash
# bench-verification.sh — Micro-bench for the verification-dispatch surface.
#
# Why this exists:
#   The 0.4.10 cycle added a tier system (quick/standard/comprehensive) and two
#   new gates (spec-implementation-coverage, runtime-spec-coverage) on the
#   verification-plan dispatch path. Both knobs trade cost for catch-rate; both
#   were validated by unit tests for *correctness* but not measured for *cost*.
#   Without a number, the next person to add a check has no idea whether the
#   tier filter is buying back 10ms or 10s, and no signal when an additive
#   check regresses the quick-tier latency past the ~10s budget AGENTS.md
#   advertises to iteration-loop callers.
#
# What it measures:
#   1. verification-plan.sh wall time per (fixture × tier) — empty / hover-only
#      / all-signals × quick / standard / comprehensive. Nine cells total.
#   2. spec-implementation-coverage.sh wall time + exit-code accuracy on a
#      pass-fixture (motion declared) and a fail-fixture (presence-only).
#   3. runtime-spec-coverage.sh wall time + exit-code accuracy on a pass-fixture
#      (scroll entry in spec when scrollTrigger non-empty) and a fail-fixture
#      (scroll entry missing).
#
# Not measured:
#   Anything that requires agent-browser / network / video recording (the
#   comprehensive-tier scripts themselves — hover-state-compare, video-motion-
#   compare, etc.). Those are dominated by RECORD_DURATION (deterministic) and
#   browser launch cost (system-dependent); a local micro-bench wouldn't add
#   signal over the documented 5min+ ceiling. This script focuses on the cheap
#   surfaces where regressions would otherwise be invisible.
#
# Usage:
#   bash scripts/ci/bench-verification.sh [--json] [--repeat=N]
#
#   --json     emit a JSON object instead of a markdown table (CI consumption)
#   --repeat=N take the median of N runs per cell (default 3). N must be odd.
#
# Target runtime: <30s with default N=3 (9 cells × 3 tiers × ~150ms verification-
# plan + 4 coverage runs ≈ 4s of inner work + bash overhead). Real-world ~3-5s.
#
# Exit:
#   0 — bench completed, all accuracy checks passed.
#   1 — bench completed but an accuracy check disagreed with its fixture intent
#       (e.g. pass-fixture returned non-zero, or fail-fixture returned 0). This
#       is a regression signal, not a setup error.
#   2 — setup error (missing dependency, fixture write failed, etc.).

set -uo pipefail

REPEAT=3
EMIT_JSON=0
for arg in "$@"; do
  case "$arg" in
    --json) EMIT_JSON=1 ;;
    --repeat=*) REPEAT="${arg#--repeat=}" ;;
    -h|--help)
      sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "ERROR: unexpected argument: $arg" >&2
      exit 2
      ;;
  esac
done

if ! [[ "$REPEAT" =~ ^[0-9]+$ ]] || [ "$REPEAT" -lt 1 ] || [ $((REPEAT % 2)) -eq 0 ]; then
  echo "ERROR: --repeat must be a positive odd integer (got '$REPEAT')" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)" || { echo "bench-verification.sh: cannot resolve repo root" >&2; exit 2; }
cd "$REPO_ROOT" || exit 2

VP="$REPO_ROOT/skills/visual-debug/scripts/verification-plan.sh"
SIC="$REPO_ROOT/skills/visual-debug/scripts/spec-implementation-coverage.sh"
RSC="$REPO_ROOT/skills/visual-debug/scripts/runtime-spec-coverage.sh"

for s in "$VP" "$SIC" "$RSC"; do
  [ -f "$s" ] || { echo "ERROR: missing $s" >&2; exit 2; }
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 required for fixture writes + median calc" >&2
  exit 2
fi

BENCH_ROOT="$(mktemp -d)"
trap 'rm -rf "$BENCH_ROOT"' EXIT

# ── fixture writers ──────────────────────────────────────────────────────────
write_empty_fixture() {
  local dir="$1"
  mkdir -p "$dir"
  # No artifacts: only the two universal rows fire.
}

write_hover_only_fixture() {
  local dir="$1"
  mkdir -p "$dir"
  printf '%s' '{"interactions":[{"trigger":"hover","target":".btn"}]}' > "$dir/interactions-detected.json"
  printf '%s' '{"hover":[{"name":"btn","triggerType":"hover","selector":".btn"}]}' > "$dir/regions.json"
}

write_all_signals_fixture() {
  local dir="$1"
  mkdir -p "$dir"
  printf '%s' '{"detected":["useScroll","scrollYProgress"]}' > "$dir/external-sdks.json"
  printf '%s' '{"interactions":[{"trigger":"hover","target":".btn"}]}' > "$dir/interactions-detected.json"
  printf '%s' '{"click":[{"name":"tabs","triggerType":"click-cycle","selector":".tab"}]}' > "$dir/regions.json"
  printf '%s' '{"transitions":[{"id":"x","trigger":"hover","selector":".btn"}]}' > "$dir/transition-spec.json"
  printf '%s' '{"scrollTrigger":[{"start":0,"end":1000}]}' > "$dir/animation-runtime-dump.json"
  printf '%s' '{"paidFonts":[{"family":"Foo","cdn":"use.typekit.net","decision":null}]}' > "$dir/paid-features.json"
}

write_sic_pass_fixture() {
  local comp="$1" impl="$2"
  mkdir -p "$comp" "$impl/src"
  printf '%s' '{"transitions":[{"id":"hero","trigger":"scroll","type":"scroll-driven","selector":".hero"}]}' > "$comp/transition-spec.json"
  cat > "$impl/src/Hero.tsx" <<'TSX'
import { useScroll, useTransform } from "framer-motion";
export function Hero() {
  const { scrollYProgress } = useScroll();
  const opacity = useTransform(scrollYProgress, [0, 1], [0, 1]);
  return <section className="hero">animated</section>;
}
TSX
}

write_sic_fail_fixture() {
  local comp="$1" impl="$2"
  mkdir -p "$comp" "$impl/src"
  printf '%s' '{"transitions":[{"id":"hero","trigger":"scroll","type":"scroll-driven","selector":".hero"}]}' > "$comp/transition-spec.json"
  cat > "$impl/src/Hero.tsx" <<'TSX'
export function Hero() { return <section className="hero">static</section>; }
TSX
}

write_rsc_pass_fixture() {
  local dir="$1"
  mkdir -p "$dir"
  printf '%s' '{"scrollTrigger":[{"start":"top 80%","end":"bottom 20%"}]}' > "$dir/animation-runtime-dump.json"
  printf '%s' '{"transitions":[{"id":"hero","trigger":"scroll","type":"scroll-driven","selector":".hero"}]}' > "$dir/transition-spec.json"
}

write_rsc_fail_fixture() {
  local dir="$1"
  mkdir -p "$dir"
  printf '%s' '{"scrollTrigger":[{"start":"top 80%","end":"bottom 20%"}]}' > "$dir/animation-runtime-dump.json"
  printf '%s' '{"transitions":[{"id":"hover-only","trigger":"hover","type":"hover","selector":".btn"}]}' > "$dir/transition-spec.json"
}

# ── timing primitive ─────────────────────────────────────────────────────────
# Returns wall-time in milliseconds (int). Uses python time.monotonic_ns so we
# don't depend on GNU time / coreutils flags that differ across darwin/linux.
time_ms() {
  local out
  out=$(python3 - "$@" <<'PY'
import os, subprocess, sys, time
cmd = sys.argv[1:]
t0 = time.monotonic_ns()
proc = subprocess.run(cmd, capture_output=True)
t1 = time.monotonic_ns()
ms = (t1 - t0) // 1_000_000
print(f"{ms} {proc.returncode}")
PY
)
  echo "$out"
}

median_ms() {
  python3 - "$@" <<'PY'
import sys
vals = sorted(int(x) for x in sys.argv[1:])
print(vals[len(vals)//2])
PY
}

run_vp_bench() {
  # $1=fixture-dir $2=tier  → prints "<median_ms>\t<check_count>"
  local dir="$1" tier="$2"
  local samples=()
  local last_rc=0
  for ((i=0; i<REPEAT; i++)); do
    local result rc ms
    result=$(time_ms bash "$VP" "$dir" "--tier=$tier")
    ms=$(echo "$result" | awk '{print $1}')
    rc=$(echo "$result" | awk '{print $2}')
    samples+=("$ms")
    last_rc=$rc
  done
  if [ "$last_rc" -ne 0 ]; then
    echo "ERROR: verification-plan.sh failed on $dir tier=$tier (exit $last_rc)" >&2
    exit 2
  fi
  local med
  med=$(median_ms "${samples[@]}")
  local count
  count=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["requiredChecks"]))' "$dir/verification-plan.json")
  printf '%s\t%s\n' "$med" "$count"
}

run_sic_bench() {
  # $1=comp $2=impl $3=expected-exit
  local comp="$1" impl="$2" expected="$3"
  local samples=()
  local last_rc=0
  for ((i=0; i<REPEAT; i++)); do
    local result rc ms
    result=$(time_ms bash "$SIC" "$comp" "$impl")
    ms=$(echo "$result" | awk '{print $1}')
    rc=$(echo "$result" | awk '{print $2}')
    samples+=("$ms")
    last_rc=$rc
  done
  local med
  med=$(median_ms "${samples[@]}")
  local accuracy="ok"
  if [ "$last_rc" -ne "$expected" ]; then
    accuracy="MISMATCH (got $last_rc, expected $expected)"
  fi
  printf '%s\t%s\n' "$med" "$accuracy"
}

run_rsc_bench() {
  # $1=fixture-dir $2=expected-exit
  local dir="$1" expected="$2"
  local samples=()
  local last_rc=0
  for ((i=0; i<REPEAT; i++)); do
    local result rc ms
    result=$(time_ms bash "$RSC" "$dir")
    ms=$(echo "$result" | awk '{print $1}')
    rc=$(echo "$result" | awk '{print $2}')
    samples+=("$ms")
    last_rc=$rc
  done
  local med
  med=$(median_ms "${samples[@]}")
  local accuracy="ok"
  if [ "$last_rc" -ne "$expected" ]; then
    accuracy="MISMATCH (got $last_rc, expected $expected)"
  fi
  printf '%s\t%s\n' "$med" "$accuracy"
}

# ── fixture build ────────────────────────────────────────────────────────────
EMPTY_DIR="$BENCH_ROOT/empty"
HOVER_DIR="$BENCH_ROOT/hover-only"
ALL_DIR="$BENCH_ROOT/all-signals"
SIC_PASS_COMP="$BENCH_ROOT/sic-pass/comp"; SIC_PASS_IMPL="$BENCH_ROOT/sic-pass/impl"
SIC_FAIL_COMP="$BENCH_ROOT/sic-fail/comp"; SIC_FAIL_IMPL="$BENCH_ROOT/sic-fail/impl"
RSC_PASS_DIR="$BENCH_ROOT/rsc-pass"
RSC_FAIL_DIR="$BENCH_ROOT/rsc-fail"

write_empty_fixture "$EMPTY_DIR"
write_hover_only_fixture "$HOVER_DIR"
write_all_signals_fixture "$ALL_DIR"
write_sic_pass_fixture "$SIC_PASS_COMP" "$SIC_PASS_IMPL"
write_sic_fail_fixture "$SIC_FAIL_COMP" "$SIC_FAIL_IMPL"
write_rsc_pass_fixture "$RSC_PASS_DIR"
write_rsc_fail_fixture "$RSC_FAIL_DIR"

# ── bench runs ───────────────────────────────────────────────────────────────
# bash 3.2 (macOS default) has no associative arrays — use parallel indexed
# arrays with index = fixture_idx * 3 + tier_idx (fixtures: 0=empty 1=hover
# 2=all; tiers: 0=quick 1=standard 2=comprehensive).
VP_MS=()
VP_COUNT=()
FIXTURES=("empty" "hover" "all")
FIXTURE_DIRS=("$EMPTY_DIR" "$HOVER_DIR" "$ALL_DIR")
TIERS=("quick" "standard" "comprehensive")

for fi in 0 1 2; do
  dir="${FIXTURE_DIRS[$fi]}"
  for ti in 0 1 2; do
    tier="${TIERS[$ti]}"
    idx=$(( fi * 3 + ti ))
    out=$(run_vp_bench "$dir" "$tier")
    VP_MS[$idx]=$(echo "$out" | awk '{print $1}')
    VP_COUNT[$idx]=$(echo "$out" | awk '{print $2}')
  done
done

vp_ms()    { echo "${VP_MS[$(( $1 * 3 + $2 ))]}";    }
vp_count() { echo "${VP_COUNT[$(( $1 * 3 + $2 ))]}"; }

SIC_PASS=$(run_sic_bench "$SIC_PASS_COMP" "$SIC_PASS_IMPL" 0)
SIC_FAIL=$(run_sic_bench "$SIC_FAIL_COMP" "$SIC_FAIL_IMPL" 1)
RSC_PASS=$(run_rsc_bench "$RSC_PASS_DIR" 0)
RSC_FAIL=$(run_rsc_bench "$RSC_FAIL_DIR" 1)

# Detect accuracy mismatches before formatting.
exit_code=0
for line in "$SIC_PASS" "$SIC_FAIL" "$RSC_PASS" "$RSC_FAIL"; do
  if echo "$line" | grep -q MISMATCH; then exit_code=1; fi
done

# ── output ───────────────────────────────────────────────────────────────────
if [ "$EMIT_JSON" = "1" ]; then
  # Build JSON via python3 with all values passed as env vars to avoid quoting hell.
  REPEAT="$REPEAT" \
  EMPTY_Q_MS=$(vp_ms 0 0) EMPTY_Q_CK=$(vp_count 0 0) \
  EMPTY_S_MS=$(vp_ms 0 1) EMPTY_S_CK=$(vp_count 0 1) \
  EMPTY_C_MS=$(vp_ms 0 2) EMPTY_C_CK=$(vp_count 0 2) \
  HOVER_Q_MS=$(vp_ms 1 0) HOVER_Q_CK=$(vp_count 1 0) \
  HOVER_S_MS=$(vp_ms 1 1) HOVER_S_CK=$(vp_count 1 1) \
  HOVER_C_MS=$(vp_ms 1 2) HOVER_C_CK=$(vp_count 1 2) \
  ALL_Q_MS=$(vp_ms 2 0) ALL_Q_CK=$(vp_count 2 0) \
  ALL_S_MS=$(vp_ms 2 1) ALL_S_CK=$(vp_count 2 1) \
  ALL_C_MS=$(vp_ms 2 2) ALL_C_CK=$(vp_count 2 2) \
  SIC_PASS_MS=$(echo "$SIC_PASS" | awk '{print $1}') \
  SIC_FAIL_MS=$(echo "$SIC_FAIL" | awk '{print $1}') \
  RSC_PASS_MS=$(echo "$RSC_PASS" | awk '{print $1}') \
  RSC_FAIL_MS=$(echo "$RSC_FAIL" | awk '{print $1}') \
  ACCURACY=$( [ $exit_code -eq 0 ] && echo ok || echo regression ) \
  python3 - <<'PY'
import json, os
def cell(prefix, tier):
    pf = {"q":"Q","s":"S","c":"C"}[tier]
    return {
      "medianMs": int(os.environ[f"{prefix}_{pf}_MS"]),
      "checkCount": int(os.environ[f"{prefix}_{pf}_CK"]),
    }
out = {
  "repeat": int(os.environ["REPEAT"]),
  "verificationPlan": {
    "empty": {"quick": cell("EMPTY","q"), "standard": cell("EMPTY","s"), "comprehensive": cell("EMPTY","c")},
    "hoverOnly": {"quick": cell("HOVER","q"), "standard": cell("HOVER","s"), "comprehensive": cell("HOVER","c")},
    "allSignals": {"quick": cell("ALL","q"), "standard": cell("ALL","s"), "comprehensive": cell("ALL","c")},
  },
  "specImplementationCoverage": {
    "passMs": int(os.environ["SIC_PASS_MS"]),
    "failMs": int(os.environ["SIC_FAIL_MS"]),
    "accuracy": os.environ["ACCURACY"],
  },
  "runtimeSpecCoverage": {
    "passMs": int(os.environ["RSC_PASS_MS"]),
    "failMs": int(os.environ["RSC_FAIL_MS"]),
    "accuracy": os.environ["ACCURACY"],
  },
}
print(json.dumps(out, indent=2))
PY
  exit $exit_code
fi

echo "# verification dispatch bench (median of $REPEAT runs)"
echo
echo "## verification-plan.sh (ms / check-count)"
echo
printf "| fixture        | quick           | standard        | comprehensive   |\n"
printf "|----------------|-----------------|-----------------|-----------------|\n"
for fi in 0 1 2; do
  case "$fi" in
    0) row_label="empty" ;;
    1) row_label="hover-only" ;;
    2) row_label="all-signals" ;;
  esac
  printf "| %-14s | %4sms / %2d ck | %4sms / %2d ck | %4sms / %2d ck |\n" \
    "$row_label" \
    "$(vp_ms $fi 0)" "$(vp_count $fi 0)" \
    "$(vp_ms $fi 1)" "$(vp_count $fi 1)" \
    "$(vp_ms $fi 2)" "$(vp_count $fi 2)"
done
echo
echo "## per-gate accuracy (median ms / expected vs actual exit)"
echo
sic_pass_ms=$(echo "$SIC_PASS" | awk '{print $1}')
sic_pass_acc=$(echo "$SIC_PASS" | cut -f2)
sic_fail_ms=$(echo "$SIC_FAIL" | awk '{print $1}')
sic_fail_acc=$(echo "$SIC_FAIL" | cut -f2)
rsc_pass_ms=$(echo "$RSC_PASS" | awk '{print $1}')
rsc_pass_acc=$(echo "$RSC_PASS" | cut -f2)
rsc_fail_ms=$(echo "$RSC_FAIL" | awk '{print $1}')
rsc_fail_acc=$(echo "$RSC_FAIL" | cut -f2)
printf "| gate                          | pass-fixture          | fail-fixture          |\n"
printf "|-------------------------------|-----------------------|-----------------------|\n"
printf "| spec-implementation-coverage  | %5sms (%s) | %5sms (%s) |\n" \
  "$sic_pass_ms" "$sic_pass_acc" "$sic_fail_ms" "$sic_fail_acc"
printf "| runtime-spec-coverage         | %5sms (%s) | %5sms (%s) |\n" \
  "$rsc_pass_ms" "$rsc_pass_acc" "$rsc_fail_ms" "$rsc_fail_acc"
echo

if [ $exit_code -ne 0 ]; then
  echo "❌ one or more accuracy checks disagreed with their fixture intent — see MISMATCH rows above"
fi

exit $exit_code
