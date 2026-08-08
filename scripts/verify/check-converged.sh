#!/usr/bin/env bash
# check-converged.sh — canonical convergence detector for staged clone loops.
#
# Exit codes:
#   0  converged   : last `**Result: ...**` line shows 0 FAIL AND >=1 genuine PASS
#   1  not yet    : Result line found with FAIL > 0, OR 0 genuine PASS (pure
#                   STRUCTURAL_ONLY / empty — the gaming vector)
#   2  setup error: missing args, missing ref dir, missing result.txt, no Result line,
#                   or invalid --stage value
#
# Phase 2 (genuine-fidelity convergence): convergence requires >=1 genuine pixel
# PASS, not just 0 FAIL. Since commit 33a7f8f the footer PASS field counts real
# ✅ pixel passes only (STRUCTURAL_ONLY is a separate field). STRUCTURAL_ONLY rows
# remain allowed *alongside* genuine passes but cannot by themselves make a ref
# "converged". SKIP doesn't gate.
#
# Usage:
#   bash scripts/verify/check-converged.sh <ref-dir> [--write-stamp] [--stage A|B|C|D]
#
# --write-stamp (review, Task #11):
#   On convergence (exit 0), additionally write structural-convergence-stamp.json
#   into <ref-dir>/. The Stop hook consumes this stamp as proof-of-closeout when
#   pipeline-state.json's closeoutPolicy=="structural" (default canonical path
#   keeps requiring verify-stamp.json from pipeline.execute_verify).
#
# --stage A|B|C|D (optional, requires --write-stamp):
#   Records the stage label inside the stamp so the receipt builder can attribute
#   convergence to the right pipeline stage. Stages outside {A,B,C,D} → exit 2.
#
# Used by per-stage loop prompts as the canonical STOP signal. Read-only check
# of sections/result.txt; --write-stamp adds one stamp file. Not a gate
# (briefing §4: don't add new gates).

set -u

# macOS host shells may inherit Linux-only C.UTF-8 settings; Perl-backed
# shasum can panic under that locale and return an empty checksum. Use the
# portable C locale for subprocesses while keeping output stable.
export LC_ALL=C
export LANG=C

write_stamp=0
stage=""
positional=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --write-stamp)
      write_stamp=1
      shift
      ;;
    --stage)
      if [[ $# -lt 2 ]]; then
        printf 'check-converged: --stage requires a value (A|B|C|D)\n' >&2
        exit 2
      fi
      stage="$2"
      shift 2
      ;;
    --stage=*)
      stage="${1#--stage=}"
      shift
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do positional+=("$1"); shift; done
      ;;
    -*)
      printf 'check-converged: unknown flag %q\n' "$1" >&2
      exit 2
      ;;
    *)
      positional+=("$1")
      shift
      ;;
  esac
done

if [[ ${#positional[@]} -ne 1 ]]; then
  printf 'usage: %s <ref-dir> [--write-stamp] [--stage A|B|C|D]\n' "$0" >&2
  exit 2
fi

if [[ -n "$stage" ]]; then
  case "$stage" in
    A|B|C|D) ;;
    *)
      printf 'check-converged: invalid --stage %q (must be A, B, C, or D)\n' "$stage" >&2
      exit 2
      ;;
  esac
fi

ref="${positional[0]}"

if [[ ! -d "$ref" ]]; then
  printf 'check-converged: ref dir not found: %s\n' "$ref" >&2
  exit 2
fi

result_file="$ref/sections/result.txt"
if [[ ! -f "$result_file" ]]; then
  printf 'check-converged: missing sections/result.txt under %s\n' "$ref" >&2
  exit 2
fi

# Match the canonical line that section-compare emits. Two formats observed:
#   4-field (normal):     **Result: 14 PASS, 0 FAIL, 0 SKIP, 14 STRUCTURAL_ONLY**
#   3-field (early exit): **Result: 0 PASS, 1 FAIL, 0 SKIP**
# The 3-field variant is written when 0 sections match (fingerprint extraction
# failed) — see section-compare.sh line ~1160. Last matching line wins
# (handles append-to-existing and trailing notes).
last_result="$(grep -E '^\*\*Result: [0-9]+ PASS, [0-9]+ FAIL, [0-9]+ SKIP(, [0-9]+ STRUCTURAL_ONLY(, [0-9]+ UNMEASURED)?)?\*\*$' "$result_file" | tail -1)"

if [[ -z "$last_result" ]]; then
  printf 'check-converged: no `**Result: ...**` line in %s\n' "$result_file" >&2
  exit 2
fi

# Extract the FAIL count: ", N FAIL,"
fail_count="$(printf '%s\n' "$last_result" | sed -E 's/^.*, ([0-9]+) FAIL,.*$/\1/')"
# Extract the genuine PASS count: "Result: N PASS,". Since commit 33a7f8f the
# footer PASS field counts real pixel passes only (STRUCTURAL_ONLY is its own
# field), so pass_count is the genuine-fidelity signal.
pass_count="$(printf '%s\n' "$last_result" | sed -E 's/^.*Result: ([0-9]+) PASS,.*$/\1/')"

if [[ "$fail_count" -ne 0 ]]; then
  printf 'not converged: %s\n' "$last_result"
  exit 1
fi

# An UNMEASURED section has no pixel evidence either way — the reference crop
# carried no signal, so nothing was compared. It is deliberately excluded from
# FAIL (a blank REF crop is a capture defect, not an impl defect), which is
# exactly why it has to gate here: 0 FAIL alone does not mean the run measured
# anything. Read the canonical field when present, and fall back to the table
# rows so artifacts written before the field existed still gate.
unmeasured_count=0
if [[ "$last_result" == *UNMEASURED* ]]; then
  unmeasured_count="$(printf '%s\n' "$last_result" | sed -E 's/^.*, ([0-9]+) UNMEASURED\*\*$/\1/')"
elif grep -qE '^\|.*\| *unmeasured *\|' "$result_file" 2>/dev/null; then
  unmeasured_count="$(grep -cE '^\|.*\| *unmeasured *\|' "$result_file")"
fi
if [[ "$unmeasured_count" -ne 0 ]]; then
  printf 'not converged (%s section(s) UNMEASURED — no pixel evidence; re-capture the reference): %s\n' \
    "$unmeasured_count" "$last_result"
  exit 1
fi

# Phase 2 — require at least one GENUINE pixel PASS. The footer PASS field is
# only trustworthy for result.txt written since commit 33a7f8f; legacy files
# inflated it by folding STRUCTURAL_ONLY into PASS. So when a real status table
# is present, count genuine ✅ ROWS; fall back to the footer PASS count only when
# no table exists (early-exit footers / minimal callers). A 0-FAIL result with
# 0 genuine passes (pure STRUCTURAL_ONLY, all-FAIL, or empty) is the gaming
# vector and must NOT count as converged.
genuine_rows="$(grep -cE '\| ✅ \|' "$result_file" 2>/dev/null || true)"
table_rows="$(grep -cE '✅|❌|🌑|🔁|⚠️' "$result_file" 2>/dev/null || true)"
if [[ "$table_rows" -gt 0 ]]; then
  if [[ "$genuine_rows" -lt 1 ]]; then
    printf 'not converged (0 genuine ✅ rows — STRUCTURAL_ONLY/all-FAIL table): %s\n' "$last_result"
    exit 1
  fi
elif [[ "$pass_count" -lt 1 ]]; then
  printf 'not converged (0 genuine PASS — empty/early-exit footer): %s\n' "$last_result"
  exit 1
fi

printf 'converged: %s\n' "$last_result"

if [[ "$write_stamp" -eq 1 ]]; then
  # Stamp schema — kept stable for Stop hook reads (ui_clone/hooks/section_gate.py).
  # Bump schemaVersion on any incompatible field rename/removal.
  verified_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  # SHA256 of sections/result.txt — Stop hook uses this to detect post-stamp
  # tampering of the convergence evidence (analogue of the impl-freshness
  # check on verify-stamp.json).
  if command -v shasum >/dev/null 2>&1; then
    sha="$(shasum -a 256 "$result_file" | awk '{print $1}')"
  elif command -v sha256sum >/dev/null 2>&1; then
    sha="$(sha256sum "$result_file" | awk '{print $1}')"
  else
    printf 'check-converged: neither shasum nor sha256sum available; cannot stamp\n' >&2
    exit 2
  fi

  stamp_path="$ref/structural-convergence-stamp.json"
  stamp_tmp="$stamp_path.tmp"
  # JSON-escape sectionResult (only " and \ are realistic in the canonical Result
  # line; printf %s + sed handles them cleanly).
  esc_result="$(printf '%s' "$last_result" | sed 's/\\/\\\\/g; s/"/\\"/g')"

  if [[ -n "$stage" ]]; then
    stage_field="\"stage\": \"$stage\""
  else
    stage_field="\"stage\": null"
  fi

  cat > "$stamp_tmp" <<EOF
{
  "schemaVersion": 1,
  "closeoutKind": "structural",
  "stampedBy": "scripts/verify/check-converged.sh",
  "verifiedAt": "$verified_at",
  $stage_field,
  "sectionResult": "$esc_result",
  "sectionsResultSha256": "$sha"
}
EOF
  mv "$stamp_tmp" "$stamp_path"
  printf 'stamp: %s\n' "$stamp_path"
fi

exit 0
