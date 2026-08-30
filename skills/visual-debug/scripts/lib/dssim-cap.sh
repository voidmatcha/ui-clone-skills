#!/usr/bin/env bash
# dssim-cap.sh — AE ceiling for the dssim/perceptual leniency paths.
#
# pass-by-dssim and pass-by-perceptual exist to absorb font anti-aliasing,
# idle drift, and codec noise — divergences that score high on AE but read
# identical to a human. They were NEVER meant to wave through sections whose
# AE/Mpx is an order of magnitude past the threshold (observed: a section at
# AE/Mpx 83593 = ~42x threshold passed pass-by-perceptual). Above
# threshold x SECTION_DSSIM_AE_CAP_MULT (default 10), the leniency paths
# close unless an explicit visual-judge confirmation exists:
#
#   <dir>/sections/<name>-judge.json
#     {
#       "verdict": "PASS",
#       "implSha256": "<sha256 of the impl crop bytes reviewed>",
#       "refSha256":  "<sha256 of the ref crop bytes reviewed>",   # optional
#       "rationale":  "<non-empty why-this-passes-despite-high-AE>"
#     }
#
# written by the visual-debug-reviewer subagent (Phase E protocol) AFTER
# looking at the actual ref/impl pair.
#
# P0·2 anti-cheat: the verdict is BOUND to the exact crop bytes reviewed. The
# old guard accepted a self-issued one-line {"verdict":"PASS"} as long as the
# file's mtime was newer than the crop — but mtime is trivially forgeable
# (`touch`) and the same agent driving the clone writes the file. So a section
# at ~42x threshold became PASS on one line. Now the verdict must embed
# implSha256 (the same sha256-over-file-bytes the visual-judge dispatcher
# computes for its cache key), re-verified HERE against the live crop bytes at
# read time. A re-rendered crop changes its hash and the stale verdict no longer
# applies — independent of mtime. A non-empty rationale is also required.

# _dssim_sha256_file <path> — prints lowercase hex sha256 of file bytes; empty
# (return 1) if missing or no hasher available. Matches visual_judge_dispatcher
# ._short_sha256_file (sha256 over raw bytes; the dispatcher truncates to 12,
# verified as a prefix below).
_dssim_sha256_file() {
  local p="${1:-}"
  [ -n "$p" ] && [ -f "$p" ] || return 1
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$p" 2>/dev/null | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$p" 2>/dev/null | awk '{print $1}'
  else
    return 1
  fi
}

# dssim_cap_allows <ae_per_mpx> <threshold> <cap_mult> <judge_file> <impl_crop> [ref_crop]
# returns 0 when a dssim/perceptual pass is permitted at this AE level.
# Sets DSSIM_LAST_CAP_OVERRIDE=1 when the pass was granted via an above-cap
# visual-judge override (so callers can count overrides), else 0.
dssim_cap_allows() {
  local ae="${1:?ae_per_mpx}" thr="${2:?threshold}" mult="${3:?cap_mult}"
  local judge="${4:?judge_file}" crop="${5:?impl_crop}" refcrop="${6:-}"
  local cap=$((thr * mult))
  DSSIM_LAST_CAP_OVERRIDE=0
  if [ "$ae" -le "$cap" ]; then
    return 0
  fi
  # Above the cap: only a fresh PASS visual-judge confirmation bound to the
  # exact crop bytes re-opens the leniency path.
  [ -f "$judge" ] || return 1
  local impl_sha ref_sha
  impl_sha="$(_dssim_sha256_file "$crop" 2>/dev/null || true)"
  # No hashable crop → cannot bind the verdict to anything → deny.
  [ -n "$impl_sha" ] || return 1
  ref_sha=""
  if [ -n "$refcrop" ]; then
    ref_sha="$(_dssim_sha256_file "$refcrop" 2>/dev/null || true)"
  fi
  if JUDGE_IMPL_SHA="$impl_sha" JUDGE_REF_SHA="$ref_sha" python3 - "$judge" <<'PY'
import json, os, sys

try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
if not isinstance(d, dict):
    sys.exit(1)

# 1) explicit PASS verdict
if str(d.get("verdict", "")).strip().upper() != "PASS":
    sys.exit(1)

# 2) non-empty rationale (a bare verdict line is not evidence)
rationale = d.get("rationale")
if not (isinstance(rationale, str) and rationale.strip()):
    sys.exit(1)


def matches(claimed: object, actual: str) -> bool:
    """True when the claimed hash equals the actual sha256, allowing the
    dispatcher's 12-char prefix form. Requires >=12 chars so a trivially short
    claim can't match."""
    c = str(claimed or "").strip().lower()
    a = str(actual or "").strip().lower()
    if not c or not a:
        return False
    n = min(len(c), len(a))
    return n >= 12 and c[:n] == a[:n]


# 3) impl crop hash bound + re-verified against the live crop bytes
impl_claim = d.get("implSha256") or d.get("cropSha256")
if not matches(impl_claim, os.environ.get("JUDGE_IMPL_SHA", "")):
    sys.exit(1)

# 4) ref crop hash verified too when both a claim and a live ref hash exist
ref_claim = d.get("refSha256")
ref_actual = os.environ.get("JUDGE_REF_SHA", "")
if ref_claim and ref_actual and not matches(ref_claim, ref_actual):
    sys.exit(1)

sys.exit(0)
PY
  then
    DSSIM_LAST_CAP_OVERRIDE=1
    return 0
  fi
  return 1
}
