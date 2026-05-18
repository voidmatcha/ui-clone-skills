#!/usr/bin/env bash
# verify-loop.sh — Per-loop fidelity verification under the new SUCCESS criteria.
#
# Usage:
#   verify-loop.sh <N> <impl_url> <ref_url>
#
# Both impl_url and ref_url are required — no built-in defaults, since the
# script is target-agnostic (the original validation target is canonicalised
# in skills/benchmark/, not here).
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

N="${1:?Usage: $0 <N> <impl_url> <ref_url>}"
IMPL_URL="${2:?impl_url required (e.g. http://localhost:5174)}"
REF_URL="${3:?ref_url required (the original site being cloned)}"

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
  # transition-spec-coverage.sh wants <component-dir>, not the json file —
  # it appends `/transition-spec.json` internally. Pass the parent.
  SPEC_DIR="$(dirname "$SPEC")"
  ts_cov="$(bash "$COV_SCRIPT" "$SPEC_DIR" "$LOOPDIR/impl/src" 2>&1 | tail -1 || true)"
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

# O2 mode switch (Codex Q2 — loop-7 post-mortem). When the clone has
# declared an approved font / asset substitution via asset-substitution.json,
# raw full-page AE is no longer the canonical pass signal — it permanently
# fails on font-rendered text it can't help. Fall back to the agent's own
# section-compare result.txt, which already classifies sections as PASS /
# STRUCTURAL_ONLY (substituted) / FAIL.
asset_sub_files = list(pathlib.Path(loopdir).rglob("asset-substitution.json"))
section_result_files = list(pathlib.Path(loopdir).rglob("sections/result.txt"))
structural_mode = bool(asset_sub_files) and bool(section_result_files)
if structural_mode:
    result_text = pathlib.Path(section_result_files[0]).read_text(errors="ignore")
    # Lines look like: "| hero | — | — | substituted | 🔁 STRUCTURAL_ONLY |"
    # or              "| pyramid | 1114010 | 910139 | saturated | ❌ |"
    rows = [r for r in result_text.splitlines() if r.startswith("|") and "Section" not in r and "---" not in r]
    fail_rows = [r for r in rows if "❌" in r]
    structural_rows = [r for r in rows if "STRUCTURAL_ONLY" in r]
    pass_rows = [r for r in rows if "✅" in r or " PASS " in r]
    # Pass when at least half of sections are structural+pass (substituted
    # font sections accepted as structural pass when declared in
    # asset-substitution.json), and no more than a third are hard fails.
    total = len(rows) or 1
    structural_pass_ratio = (len(pass_rows) + len(structural_rows)) / total
    hard_fail_ratio = len(fail_rows) / total
    o2_structural_pass = structural_pass_ratio >= 0.5 and hard_fail_ratio <= 0.34

criteria = {
    "P1_pipeline_run":     {"value": p1_run_invocations, "verdict": verdict(p1_run_invocations >= 1)},
    "P2_regions_json":     {"value": [str(p) for p in regions_json], "verdict": verdict(len(regions_json) >= 1)},
    "P3_no_leak":          {"value": p3_leaks, "verdict": "MANUAL"},
    "P4_no_hook_deny":     {"value": p4_denies, "verdict": verdict(p4_denies == 0)},
    "O1_verification_gates": {"value": o1_gate_invocations, "verdict": verdict(o1_gate_invocations >= 1)},
}
if structural_mode:
    criteria["O2_structural"] = {
        "value": f"sections={len(rows)} pass+structural={len(pass_rows) + len(structural_rows)} fail={len(fail_rows)}",
        "mode": "structural (asset-substitution.json present)",
        "verdict": verdict(o2_structural_pass),
    }
    # Keep raw AE for visibility but mark advisory only.
    criteria["O2_ae_desktop_advisory"] = {"value": ae_d_val, "verdict": "ADVISORY"}
    criteria["O2_ae_mobile_advisory"] = {"value": ae_m_val, "verdict": "ADVISORY"}
else:
    criteria["O2_ae_desktop"] = {"value": ae_d_val, "threshold": 2_000_000, "verdict": verdict(ae_d_val is not None and ae_d_val < 2_000_000)}
    criteria["O2_ae_mobile"] = {"value": ae_m_val, "threshold": 2_000_000, "verdict": verdict(ae_m_val is not None and ae_m_val < 2_000_000)}
criteria["O3_build_dist"] = {"value": dist_exists, "verdict": verdict(dist_exists)}
criteria["O4_transition_cov"] = {
    "value": ts_cov,
    # transition-spec-coverage.sh exit-0 lines we accept as PASS:
    #   "✅ Every spec entry has at least one matching impl artifact."
    #   "PASS" / "0 missing"
    "verdict": "MANUAL" if "NO_SPEC" in ts_cov else verdict(
        "✅" in ts_cov
        or "matching impl artifact" in ts_cov
        or "PASS" in ts_cov
        or "0 missing" in ts_cov.lower()
    ),
}

# ADVISORY rows participate in overall verdict the same way MANUAL does —
# present but not gating. PASS = every gating row is PASS.
gating_verdicts = [v["verdict"] for v in criteria.values() if v["verdict"] not in ("ADVISORY", "MANUAL")]
overall = "PASS" if gating_verdicts and all(v == "PASS" for v in gating_verdicts) else (
    "PARTIAL" if any(v == "PASS" for v in gating_verdicts) else "FAIL"
)

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
