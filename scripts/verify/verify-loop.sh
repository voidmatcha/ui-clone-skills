#!/usr/bin/env bash
# verify-loop.sh — Per-loop fidelity verification under the new SUCCESS criteria.
#
# Usage:
#   verify-loop.sh <N> <impl_url> [ref_url]
#
# Defaults: ref_url=https://realfood.gov
#
# Runs the full post-loop checklist:
#   - probe splash state of ref via scripts/extract/splash-bypass.sh
#   - snapshot ref + impl on desktop (1280x800) and mobile (390x844),
#     dual-frame (splash + post-splash) when ref hasSplash=true
#   - AE compare per viewport
#   - basic transition coverage check (if transition-spec.json exists)
#   - write verify-report.json to scratch/_snapshots/loop-N/
#
# Output dir: scratch/_snapshots/loop-N/

set -euo pipefail

N="${1:?Usage: $0 <N> <impl_url> [ref_url]}"
IMPL_URL="${2:?impl_url required (e.g. http://localhost:5174)}"
REF_URL="${3:-https://realfood.gov}"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOOPDIR="$REPO_ROOT/scratch/loop-${N}"
SNAPDIR="$REPO_ROOT/scratch/_snapshots/loop-${N}"
mkdir -p "$SNAPDIR"

REPORT="$SNAPDIR/verify-report.json"
SPLASH_JSON="$SNAPDIR/splash-state.json"
REF_SESSION="loop${N}-ref"
IMPL_SESSION="loop${N}-impl"

echo "=== verify-loop $N ==="
echo "  ref:  $REF_URL"
echo "  impl: $IMPL_URL"
echo "  out:  $SNAPDIR"

# 1. Probe splash on ref ----------------------------------------------------
echo
echo "[1/5] splash probe (ref)"
bash "$REPO_ROOT/scripts/extract/splash-bypass.sh" "$REF_URL" "$REF_SESSION" "$SNAPDIR"
HAS_SPLASH="$(python3 -c "import json; print(json.load(open('$SPLASH_JSON'))['hasSplash'])")"
echo "  hasSplash=$HAS_SPLASH"

# 2. Snapshot helper --------------------------------------------------------
snap() {
  # snap <session> <viewport-w> <viewport-h> <out-png>
  local session="$1" vw="$2" vh="$3" out="$4"
  agent-browser --session "$session" set viewport "$vw" "$vh" > /dev/null 2>&1
  agent-browser --session "$session" screenshot --full "$out" 2>&1 | grep -E '^✓|^✗' | head -1
}

# 3. Capture matrix ---------------------------------------------------------
echo
echo "[2/5] capture matrix"
# Desktop post-splash (always)
snap "$REF_SESSION"  1280 800 "$SNAPDIR/ref-desktop-post.png"
# Impl desktop
agent-browser --session "$IMPL_SESSION" open "$IMPL_URL" > /dev/null 2>&1 || true
sleep 2
snap "$IMPL_SESSION" 1280 800 "$SNAPDIR/impl-desktop-post.png"

# Splash frame (only if ref has splash) — capture EARLY on a fresh session
if [ "$HAS_SPLASH" = "True" ]; then
  echo "  ref has splash — capturing splash frames"
  agent-browser --session "${REF_SESSION}-splash" open "$REF_URL" > /dev/null 2>&1
  # Take splash screenshot ASAP (no settle wait)
  snap "${REF_SESSION}-splash" 1280 800 "$SNAPDIR/ref-desktop-splash.png"
  agent-browser --session "${IMPL_SESSION}-splash" open "$IMPL_URL" > /dev/null 2>&1
  snap "${IMPL_SESSION}-splash" 1280 800 "$SNAPDIR/impl-desktop-splash.png"
fi

# Mobile post (always)
snap "$REF_SESSION"  390 844 "$SNAPDIR/ref-mobile-post.png"
snap "$IMPL_SESSION" 390 844 "$SNAPDIR/impl-mobile-post.png"

# 4. AE compare per viewport ------------------------------------------------
echo
echo "[3/5] AE compare"
AE_SCRIPT="$REPO_ROOT/skills/visual-debug/scripts/ae-compare.sh"
# ae-compare exits non-zero when STATUS=FAIL — that's a signal not an error.
# `|| true` keeps `set -e` from aborting on legitimate AE failure verdicts.
ae_desktop="$(bash "$AE_SCRIPT" "$SNAPDIR/ref-desktop-post.png" "$SNAPDIR/impl-desktop-post.png" 2>&1 | grep -oE 'AE=[0-9]+' | head -1 || true)"
ae_mobile="$(bash "$AE_SCRIPT"  "$SNAPDIR/ref-mobile-post.png"  "$SNAPDIR/impl-mobile-post.png"  2>&1 | grep -oE 'AE=[0-9]+' | head -1 || true)"
echo "  desktop $ae_desktop"
echo "  mobile  $ae_mobile"

# 5. Transition coverage (best-effort — needs transition-spec.json) --------
echo
echo "[4/5] transition coverage"
SPEC="$(find "$LOOPDIR" -name "transition-spec.json" 2>/dev/null | head -1)"
if [ -n "$SPEC" ]; then
  COV_SCRIPT="$REPO_ROOT/skills/visual-debug/scripts/transition-spec-coverage.sh"
  ts_cov="$(bash "$COV_SCRIPT" "$SPEC" "$LOOPDIR/impl/src" 2>&1 | tail -1)"
  echo "  $ts_cov"
else
  ts_cov="NO_SPEC"
  echo "  transition-spec.json not found — clone did not produce a spec"
fi

# 6. Criteria evaluation + report ------------------------------------------
echo
echo "[5/5] report"
TS_DIR="$HOME/.claude/projects/-Users-yongjae-Documents-ui-skills-scratch-loop-${N}"
TRANSCRIPT="$(ls -t "$TS_DIR"/*.jsonl 2>/dev/null | head -1 || true)"

python3 - "$N" "$REF_URL" "$IMPL_URL" "$SNAPDIR" "$SPLASH_JSON" \
  "${ae_desktop:-AE=NA}" "${ae_mobile:-AE=NA}" "$ts_cov" "${TRANSCRIPT:-}" \
  "$LOOPDIR" "$REPORT" <<'PY'
import json, sys, pathlib, re
N, ref_url, impl_url, snapdir, splash_json, ae_d, ae_m, ts_cov, ts_path, loopdir, report_path = sys.argv[1:12]
splash = json.loads(pathlib.Path(splash_json).read_text())

def grep_count(needle):
    if not ts_path:
        return 0
    return sum(1 for line in pathlib.Path(ts_path).read_text(errors="ignore").splitlines()
               if needle in line)

# Process criteria (cheap text checks)
p1_pipeline_runs = grep_count("python -m ui_clone.pipeline") if ts_path else 0
p1_run_invocations = 0
if ts_path:
    text = pathlib.Path(ts_path).read_text(errors="ignore")
    p1_run_invocations = len(re.findall(r"python -m ui_clone\.pipeline\s+\S+\s+\S+\s+\S+\s+run", text))
regions_json = list(pathlib.Path(loopdir).rglob("regions.json"))
splash_state_in_loop = list(pathlib.Path(loopdir).rglob("splash-state.json"))
p3_leaks = "see leak-baseline diff in poll log"
p4_denies = grep_count('"deny"') + grep_count('"blocked":true') + grep_count("Hook denied")

# Outcome
o1_gate_invocations = 0
if ts_path:
    text = pathlib.Path(ts_path).read_text(errors="ignore")
    o1_gate_invocations = len(re.findall(r"python -m ui_clone\.gate\s+\S+\s+\S+", text))
ae_d_val = int(ae_d.split("=")[1]) if "=" in ae_d else None
ae_m_val = int(ae_m.split("=")[1]) if "=" in ae_m else None
# O3 build artifact — vite produces impl/dist/, Next.js produces impl/.next/.
# Either signals a successful production build; the agent's framework choice
# is allowed to vary (vite vs Next.js).
impl_root = pathlib.Path(loopdir) / "impl"
dist_exists = (impl_root / "dist").is_dir() or (impl_root / ".next").is_dir()

def verdict(ok):
    return "PASS" if ok else "FAIL"

criteria = {
    "P1_pipeline_run":     {"value": p1_run_invocations, "verdict": verdict(p1_run_invocations >= 1)},
    "P2_regions_json":     {"value": [str(p) for p in regions_json], "verdict": verdict(len(regions_json) >= 1)},
    "P3_no_leak":          {"value": p3_leaks, "verdict": "MANUAL"},
    "P4_no_hook_deny":     {"value": p4_denies, "verdict": verdict(p4_denies == 0)},
    "O1_verification_gates": {"value": o1_gate_invocations, "verdict": verdict(o1_gate_invocations >= 1)},
    "O2_ae_desktop":       {"value": ae_d_val, "threshold": 2_000_000, "verdict": verdict(ae_d_val is not None and ae_d_val < 2_000_000)},
    "O2_ae_mobile":        {"value": ae_m_val, "threshold": 2_000_000, "verdict": verdict(ae_m_val is not None and ae_m_val < 2_000_000)},
    "O3_build_dist":       {"value": dist_exists, "verdict": verdict(dist_exists)},
    "O4_transition_cov":   {"value": ts_cov, "verdict": "MANUAL" if "NO_SPEC" in ts_cov else verdict("PASS" in ts_cov or "0 missing" in ts_cov.lower())},
}

verdicts = [c["verdict"] for c in criteria.values()]
overall = "PASS" if all(v == "PASS" for v in verdicts) else ("PARTIAL" if any(v == "PASS" for v in verdicts) else "FAIL")

report = {
    "loop": int(N),
    "refUrl": ref_url,
    "implUrl": impl_url,
    "splash": splash,
    "snapshots": sorted(p.name for p in pathlib.Path(snapdir).glob("*.png")),
    "criteria": criteria,
    "overallVerdict": overall,
}
pathlib.Path(report_path).write_text(json.dumps(report, indent=2) + "\n")
print(f"verdict={overall}")
for k, v in criteria.items():
    print(f"  {k:25s} {v['verdict']}   {v.get('value')}")
print(f"report: {report_path}")
PY

echo
echo "=== verify-loop $N done ==="
