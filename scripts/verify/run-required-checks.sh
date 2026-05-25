#!/usr/bin/env bash
# run-required-checks.sh — single-pass dispatcher that reads
# verification-plan.json and runs every requiredCheck whose artifact
# is missing (or stale).
#
# Why this exists:
#   validation run hit the agent's "10 consecutive Bash failures →
#   hard ABORT" circuit breaker because the comprehensive
#   verification plan declared 31 checks, auto-verify.sh only ran a
#   handful (layout-health, batch-compare, gate post-implement), and
#   the agent was left to invoke the remaining 25+ scripts one at a
#   time. Each missing artifact registers as a separate Bash failure
#   in the agent's iteration loop, and the 10-failures threshold is
#   reached before half the checks have produced their output.
#
#   This script collapses that into ONE invocation: read the plan,
#   dispatch each requiredCheck with the correct args based on a
#   known-signatures table, skip scripts whose artifact already
#   passes. The agent runs this once at Step 8 and gets every static
#   + runtime gate artifact materialized in a single Bash call.
#
# Usage:
#   run-required-checks.sh <session> <ref-url> <impl-url> <ref-dir>
#
# Exit:
#   0 — every dispatched check exited 0 (pass) OR was skipped
#   1 — at least one check exited non-zero (fail) — gate.py will
#       enforce the actual pass/fail verdict via the artifacts
#   2 — setup error (missing plan, unreachable URL, etc.)
#
# What it does NOT do:
#   - Replace gate.py enforcement. The agent still runs
#     `uv run python -m ui_clone.gate <ref-dir> post-implement` to
#     get the canonical verdict.
#   - Iterate / fix failures. This is one shot per call. The agent
#     reads the resulting status JSONs and applies targeted fixes
#     (or invokes visual-debug-iterator).
#   - Run extract-phase scripts (extract-dom, extract-assets, etc).
#     Only post-implement / spec verification checks listed in
#     verification-plan.json's requiredChecks.

set -uo pipefail

# Codex review (2026-05-24/25): per-check timeout via Python wrapper.
# The first attempt used bash timeout-shim.sh's pure-bash fallback, which
# only SIGTERM's the immediate child PID and leaves the spawned tree
# (bash → node → chromium) alive inside the `if cmd | tail | sed; then`
# pipeline. run_with_timeout.py uses subprocess.Popen(start_new_session=True)
# + os.killpg() so the whole process group is terminated on timeout.
# Default 3 min; override via RUN_REQUIRED_CHECK_TIMEOUT_SEC.
: "${RUN_REQUIRED_CHECK_TIMEOUT_SEC:=180}"
_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
_RUN_WITH_TIMEOUT="python3 ${_SCRIPT_DIR}/../lib/run_with_timeout.py"

SESSION="${1:-}"
REF_URL="${2:-}"
IMPL_URL="${3:-}"
REF_DIR="${4:-}"

if [ -z "$SESSION" ] || [ -z "$REF_URL" ] || [ -z "$IMPL_URL" ] || [ -z "$REF_DIR" ]; then
  echo "Usage: run-required-checks.sh <session> <ref-url> <impl-url> <ref-dir>" >&2
  exit 2
fi

# Codex universality audit MEDIUM: prior version derived deterministic
# session suffixes (`{session}-hyd`, `{session}-rdp`, etc) from the
# caller's session name. Repeated invocations against the same parent
# session reused stale browser state and leaked across loops. Append
# a per-run UUID to the session prefix so each dispatch gets fresh
# agent-browser sessions, and trap-close all derived sessions on exit.
RUN_UUID=$(date +%s%N | tail -c 8)
SESSION="${SESSION}-${RUN_UUID}"
DERIVED_SESSIONS=()

cleanup_browser_sessions() {
  for s in "${DERIVED_SESSIONS[@]}"; do
    agent-browser --session "$s" close >/dev/null 2>&1 || true
    # Also close the -ref / -impl child variants spawned by scripts
    # that open ref+impl pairs (svg-dom-parity, runtime-dom-parity,
    # font-parity).
    agent-browser --session "${s}-ref" close >/dev/null 2>&1 || true
    agent-browser --session "${s}-impl" close >/dev/null 2>&1 || true
  done
}
trap cleanup_browser_sessions EXIT INT TERM

if [ ! -d "$REF_DIR" ]; then
  echo "ref-dir not found: $REF_DIR" >&2
  exit 2
fi

PLAN="$REF_DIR/verification-plan.json"
if [ ! -f "$PLAN" ]; then
  echo "verification-plan.json missing — run verification-plan.sh first" >&2
  exit 2
fi

REPO_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}}}"

# Determine impl_root by walking up from REF_DIR's parent (typical:
# tmp/ref/<c> → repo/impl) and falling back to the canonical resolver.
IMPL_ROOT=""
RESOLVER="$REPO_ROOT/scripts/extract/find-impl-root.sh"
if [ -f "$RESOLVER" ]; then
  IMPL_ROOT=$(bash "$RESOLVER" "$REF_DIR" 2>/dev/null | head -1)
fi
if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  # Fall back to the conventional <ref-dir>/../../../impl path.
  CAND="$(cd "$REF_DIR/../../.." && pwd)/impl"
  if [ -d "$CAND" ]; then
    IMPL_ROOT="$CAND"
  fi
fi

if [ -z "$IMPL_ROOT" ] || [ ! -d "$IMPL_ROOT" ]; then
  echo "run-required-checks: ERROR — could not resolve impl_root for $REF_DIR." >&2
  echo "  Tried: $RESOLVER (returned empty/non-dir)" >&2
  echo "  Tried: $(cd "$REF_DIR/../../.." 2>/dev/null && pwd)/impl (not found)" >&2
  echo "  Without a valid impl_root, dispatchers would compose '/src' = filesystem root." >&2
  echo "  Verify <ref-dir>/../../../impl exists, OR symlink it from your impl location." >&2
  exit 2
fi
IMPL_SRC="${IMPL_ROOT}/src"
IMPL_PUBLIC="${IMPL_ROOT}/public"
# Belt-and-suspenders: also reject paths that resolve to the
# filesystem root or to /src (which only exists on some systems but
# is never an impl source dir for us).
case "$IMPL_ROOT" in
  /|/src|/usr|/opt|/etc|/var|/tmp|/Users|/home)
    echo "run-required-checks: ERROR — impl_root '$IMPL_ROOT' is a filesystem root or system dir." >&2
    exit 2
    ;;
esac
# validation run lesson follow-up: even a valid-looking path could be a
# typo'd directory that lacks impl markers. Require at least one of
# package.json + (src/ or app/ or pages/) before treating IMPL_ROOT
# as a usable impl tree. This catches the case where the resolver
# stumbles into an unrelated directory that happens to exist.
if [ ! -f "$IMPL_ROOT/package.json" ]; then
  echo "run-required-checks: ERROR — impl_root '$IMPL_ROOT' lacks package.json." >&2
  echo "  Not a usable impl tree; refusing to dispatch checks against it." >&2
  exit 2
fi
if [ ! -d "$IMPL_ROOT/src" ] && [ ! -d "$IMPL_ROOT/app" ] && [ ! -d "$IMPL_ROOT/pages" ]; then
  echo "run-required-checks: ERROR — impl_root '$IMPL_ROOT' has no src/, app/, or pages/ directory." >&2
  echo "  Not a usable impl tree; refusing to dispatch checks against it." >&2
  exit 2
fi

GREEN="\033[0;32m"; RED="\033[0;31m"; YELLOW="\033[1;33m"; NC="\033[0m"
TOTAL=0; PASS=0; FAIL=0; SKIP=0; STALE=0

# Build the list of (id, script, produces, args-mode) tuples from the plan.
# args-mode is determined by the script basename — kept small and
# explicit so adding a new gate means updating this table.
python3 - "$PLAN" "$REF_DIR" "$REPO_ROOT" "$IMPL_ROOT" "$IMPL_SRC" "$IMPL_PUBLIC" "$REF_URL" "$IMPL_URL" "$SESSION" <<'PY' > "$REF_DIR/.run-required-checks-dispatch.txt"
import json
import sys
from pathlib import Path

(plan_path, ref_dir, repo_root, impl_root, impl_src, impl_public,
 ref_url, impl_url, session) = sys.argv[1:10]
plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))

# Known dispatch signatures. Add new scripts here as they are wired in.
# Each entry maps the SCRIPT FILENAME to an ARGS RECIPE string with
# placeholders {ref_dir}, {impl_root}, {impl_src}, {ref_url}, {impl_url},
# {session}.
SIGNATURES = {
    # ── static / quick tier (no browser) ──
    "ref-screenshot-asset-check.sh": "{ref_dir} {impl_root}",
    "entry-coherence-check.sh": "{ref_dir} {impl_root}",
    "scaffold-residue-check.sh": "{ref_dir} {impl_root}",
    "html-paste-check.sh": "{ref_dir} {impl_root}",
    "monolithic-impl-check.sh": "{ref_dir} {impl_root}",
    "motion-coverage-check.sh": "{ref_dir} {impl_root}",
    "scroll-engine-parity-check.sh": "{ref_dir} {impl_root}",
    "css-mirror-check.sh": "{ref_dir} {impl_root}",
    "scaffold-warn-check.sh": "{ref_dir} {impl_root}",
    "invalidation-check.sh": "{ref_dir}",
    "required-media-coverage-check.sh": "{ref_dir} {impl_root}",
    "remote-asset-ref-check.sh": "{ref_dir}",
    "capture-artifact-inventory-check.sh": "{ref_dir}",
    "asset-transfer-check.sh": "{ref_dir} {impl_public}",
    "asset-utilization-check.sh": "{ref_dir} {impl_src}",
    "asset-placement-check.sh": "{ref_dir} {impl_root}",
    "image-fidelity-check.sh": "{ref_dir} {impl_src}",
    "proxy-mirror-check.sh": "{ref_dir}",
    "class-signature-preservation-check.sh": "{ref_dir} {impl_root}",
    "bundle-paste-check.sh": "{ref_dir} {impl_root}",
    "class-signature-css-coverage-check.sh": "{ref_dir} {impl_root}",
    "transition-spec-coverage.sh": "{ref_dir} {impl_src}",
    "spec-implementation-coverage.sh": "{ref_dir} {impl_src}",
    "runtime-spec-coverage.sh": "{ref_dir} {impl_src}",
    "bundle-impl-coverage-check.sh": "{ref_dir} {impl_pkg}",
    # 2026-05-22: add {impl_url} third arg so the runtime-proof block in
    # lottie-runtime-check.sh fires (it opens impl_url, waits 1.5s, and
    # asserts at least one Lottie container painted svg/canvas). Without
    # impl_url the script falls back to the legacy static-only check
    # which can pass when imports exist but loadAnimation never runs.
    "lottie-runtime-check.sh": "{ref_dir} {impl_root} {impl_url}",
    # ── browser-needed / standard tier ──
    "tailwind-transform-conflict-check.sh":
        "ENV:REF_DIR={ref_dir} -- {session}-twc {impl_url}",
    "hydration-check.sh": "{session}-hyd {impl_url} {ref_dir}",
    "runtime-image-validity-check.sh":
        "ENV:REF_DIR={ref_dir} -- {session}-rim {impl_url}",
    "hidden-children-check.sh": "{session}-hidden {impl_url} {ref_dir}",
    # NOTE: reveal-trigger-check.sh script writes via REF_DIR env;
    # but it also doesn't currently emit reveal-trigger.json — script
    # bug deferred to a follow-up commit. SIGNATURE here is what the
    # script EXPECTS once the writer is added.
    "reveal-trigger-check.sh":
        "ENV:REF_DIR={ref_dir} -- {session}-reveal {impl_url}",
    # 2026-05-22: header-state-runtime gate fires unconditionally — proves
    # the impl header is a runtime state machine (mutates className on
    # scroll) when the ref's header is stateful. Args: session ref-url
    # impl-url ref-dir [w] [h]. self-skips when ref header is static.
    "header-state-runtime-check.sh":
        "{session}-hsr {ref_url} {impl_url} {ref_dir}",
    # 2026-05-22 (codex-rescue a125b997): svg-provenance closes the
    # IconMark.tsx hand-roll loophole. svg-dom-parity only checks count
    # + section presence; this gate asserts impl SVG geometry traces
    # back to ref geometry. Args: session ref-url impl-url ref-dir.
    "svg-provenance-check.sh":
        "{session}-svgp {ref_url} {impl_url} {ref_dir}",
    # 2026-05-22: runtime-proof rollup is a file-IO aggregator —
    # only ref-dir needed. Must run AFTER all source artifacts are
    # produced; dispatcher already orders rows by add_check insertion
    # order (this row is inserted near the end of standard tier so
    # source artifacts exist by the time it dispatches).
    "runtime-proof-rollup.sh":
        "{ref_dir}",
    # 2026-05-22: transition-proof rollup — same file-IO contract as
    # runtime-proof; ref-dir only.
    "transition-proof-rollup.sh":
        "{ref_dir}",
    # 2026-05-22: ref-js-loader gate — static scan of impl source for
    # ref-host references, plus optional runtime probe when impl_url
    # is passed.
    "ref-js-loader-check.sh":
        "{ref_dir} {impl_root} {impl_url}",
    # runtime-env gate — catches Vite preamble traps, hydration
    # mismatches, port-routing collisions from orphan dev servers.
    # Observed failure modes: NODE_ENV=production trap and orphan-port
    # interception. Needs ref-dir + impl-root + impl-url.
    "runtime-env-check.sh":
        "{ref_dir} {impl_root} {impl_url}",
    # 2026-05-22: video-play-proof — currentTime advancement check.
    "video-play-proof-check.sh":
        "{session}-vpp {impl_url} {ref_dir}",
    # 2026-05-22: impl-scope guard — diff git HEAD against baseline,
    # fail if iteration touched plugin tooling.
    "impl-scope-check.sh":
        "{ref_dir} {impl_root}",
    # 2026-05-22 grounding: color-token gate is pure file-scan;
    # ref-dir + impl-root only.
    "color-token-grounding-check.sh":
        "{ref_dir} {impl_root}",
    # 2026-05-22: duration/easing grounding — scan impl for guessed
    # transition timings; static, no browser.
    "duration-easing-grounding-check.sh":
        "{ref_dir} {impl_root}",
    # 2026-05-22: mobile viewport parity at 375x812.
    "mobile-viewport-parity-check.sh":
        "{session}-mvp {ref_url} {impl_url} {ref_dir}",
    # 2026-05-22: stronger frame-delta proof (Lottie currentFrame +
    # canvas paint + WebGL drawbuffer).
    "runtime-frame-proof-check.sh":
        "{session}-rfp {impl_url} {ref_dir}",
    "scroll-end-completion-check.sh": "{session}-sec {impl_url} {ref_dir}",
    "font-parity-check.sh": "{session}-fp {ref_url} {impl_url} {ref_dir}",
    "breakpoint-collision-check.sh":
        "ENV:REF_DIR={ref_dir} -- {session}-bound {impl_url}",
    # ── ref+impl browser pairs ──
    "runtime-dom-parity-check.sh":
        "{session}-rdp {ref_url} {impl_url} {ref_dir}",
    "svg-dom-parity-check.sh":
        "{session}-svg {ref_url} {impl_url} {ref_dir}",
    "transition-compare.sh":
        "{ref_url} {impl_url} {session}-tc {ref_dir}",
    "hover-state-compare.sh":
        "{ref_url} {impl_url} {session}-hsc {ref_dir}",
    "video-motion-compare.sh":
        "{ref_url} {impl_url} {session}-vmc {ref_dir}",
    "click-state-compare.sh":
        "{ref_url} {impl_url} {session}-clk {ref_dir}",
    "scroll-coverage-check.sh":
        "{ref_dir} {ref_url} {impl_url} {session}-scov",
    "tree-diff.sh": "{session}-td {ref_url} {impl_url} {ref_dir}",
    "keyframes-diff.sh": "{session}-kf {ref_url} {impl_url} {ref_dir}",
    # ── static text/dom fidelity ──
    "text-fidelity-check.sh": "{ref_dir} {impl_root}",
    "dom-mirror-check.sh": "{ref_dir} {impl_root}",
    # 2026-05-22: hero-composite-check pairs with the dom-mirror advisory
    # downgrade — same {ref_dir} {impl_root} contract; default artifact path
    # is $REF_DIR/hero-composite.json (matches verification-plan row).
    "hero-composite-check.sh": "{ref_dir} {impl_root}",
    "scroll-anim-temporal-diff.sh": "MANUAL",
}

ctx = {
    "ref_dir": ref_dir,
    "impl_root": impl_root,
    "impl_src": impl_src,
    "impl_public": impl_public,
    "impl_pkg": str(Path(impl_root) / "package.json"),
    "ref_url": ref_url,
    "impl_url": impl_url,
    "session": session,
}

for check in plan.get("requiredChecks", []):
    cid = check.get("id", "?")
    script_rel = check.get("script") or ""
    produces = check.get("produces") or ""
    if not script_rel or not produces:
        print(f"SKIP\t{cid}\t\t\tno-script-or-produces", flush=True)
        continue
    # Resolve script path against repo root.
    script_path = Path(repo_root) / script_rel
    if not script_path.is_file():
        # Try relative basename match (for scripts referenced by short
        # name only).
        alt = list(Path(repo_root).rglob(Path(script_rel).name))
        if alt:
            script_path = alt[0]
        else:
            print(f"NOSCRIPT\t{cid}\t{script_rel}\t\tscript not found", flush=True)
            continue
    sig = SIGNATURES.get(script_path.name)
    if not sig:
        print(f"NOSIG\t{cid}\t{script_path}\t\tunknown signature", flush=True)
        continue
    args = sig.format(**ctx)
    deps = " ".join(check.get("dependsOn", []) or [])
    print(f"DISPATCH\t{cid}\t{script_path}\t{args}\t{produces}\t{deps}", flush=True)
PY


SETUP_FAILURE=0
FAILED_IDS=""
mark_failed() {
  local id="$1"
  if ! echo " $FAILED_IDS " | grep -q " $id "; then
    FAILED_IDS="$FAILED_IDS $id"
  fi
}
dep_failed() {
  local deps="$1"
  [ -z "$deps" ] && return 1
  for dep in $deps; do
    if echo " $FAILED_IDS " | grep -q " $dep "; then
      echo "$dep"
      return 0
    fi
  done
  return 1
}
while IFS=$'\t' read -r kind cid script_path args produces deps; do
  TOTAL=$((TOTAL + 1))
  case "$kind" in
    SKIP)
      echo -e "${YELLOW}~${NC} $cid: $kind"
      SKIP=$((SKIP + 1))
      continue
      ;;
    NOSCRIPT|NOSIG)
      echo -e "${RED}!${NC} $cid: $kind — wire the script into run-required-checks.sh SIGNATURES table"
      SKIP=$((SKIP + 1))
      SETUP_FAILURE=1
      continue
      ;;
  esac
  # MANUAL recipes are advisory scripts that need agent-provided
  # args (e.g. scroll-anim-temporal-diff needs a selector). Log
  # but skip dispatch — these are not SETUP failures.
  if [ "$args" = "MANUAL" ]; then
    echo -e "${YELLOW}~${NC} $cid: MANUAL (agent invokes when applicable)"
    SKIP=$((SKIP + 1))
    continue
  fi
  if [ -n "${deps:-}" ]; then
    failing_dep=$(dep_failed "$deps") || failing_dep=""
    if [ -n "$failing_dep" ]; then
      echo -e "${YELLOW}~${NC} $cid: SKIPPED_DEP (depends on failed: $failing_dep)"
      SKIP=$((SKIP + 1))
      continue
    fi
  fi
  # Skip when artifact already exists with status=pass (idempotency).
  art="$REF_DIR/$produces"
  if [ -f "$art" ]; then
    cur_status=$(python3 -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(d.get('status') if isinstance(d, dict) else 'unknown')
except Exception:
    print('parse-error')
" "$art" 2>/dev/null)
    if [ "$cur_status" = "pass" ]; then
      # Stale-check: artifact older than newest source/asset file.
      stale_seen=0
      if [ -n "$IMPL_ROOT" ]; then
        for sub in src app pages components public lib hooks contexts; do
          d="$IMPL_ROOT/$sub"
          [ -d "$d" ] || continue
          if find "$d" -type f -newer "$art" 2>/dev/null | head -1 | grep -q .; then
            stale_seen=1
            break
          fi
        done
      fi
      if [ "$stale_seen" = "1" ]; then
        STALE=$((STALE + 1))
        # Fall through to re-dispatch.
      else
        PASS=$((PASS + 1))
        continue
      fi
    fi
  fi
  # Dispatch the check.
  env_vars=""
  positional="$args"
  if [[ "$args" == ENV:* ]]; then
    env_spec="${args#ENV:}"
    env_vars="${env_spec%% -- *}"
    positional="${env_spec##* -- }"
  fi
  echo -e "▶ $cid"
  # Track every agent-browser session name that appears in the args
  # so cleanup_browser_sessions can close them on exit.
  for tok in $positional; do
    case "$tok" in
      "${SESSION}"-*) DERIVED_SESSIONS+=("$tok") ;;
    esac
  done
  # shellcheck disable=SC2086 # intentional word-split on positional
  if [ -n "$env_vars" ]; then
    # shellcheck disable=SC2086 # intentional word-split on env_vars
    if $_RUN_WITH_TIMEOUT "$RUN_REQUIRED_CHECK_TIMEOUT_SEC" env $env_vars bash "$script_path" $positional 2>&1 | tail -3 | sed 's/^/  /'; then
      rc=0
    else
      rc=$?
    fi
  else
    if $_RUN_WITH_TIMEOUT "$RUN_REQUIRED_CHECK_TIMEOUT_SEC" bash "$script_path" $positional 2>&1 | tail -3 | sed 's/^/  /'; then
      rc=0
    else
      rc=$?
    fi
  fi
  if [ "$rc" -eq 124 ]; then
    echo "  → check timed out after ${RUN_REQUIRED_CHECK_TIMEOUT_SEC}s (RUN_REQUIRED_CHECK_TIMEOUT_SEC)" >&2
  fi
  if [ "$rc" -eq 0 ]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    mark_failed "$cid"
  fi
done < "$REF_DIR/.run-required-checks-dispatch.txt"

rm -f "$REF_DIR/.run-required-checks-dispatch.txt"

echo
echo "═══ run-required-checks summary ═══"
echo "  dispatched: $TOTAL"
echo "  pass:       $PASS"
echo "  fail:       $FAIL"
echo "  skipped:    $SKIP (unknown signature or missing script — wire into SIGNATURES table)"
echo "  stale:      $STALE (re-dispatched because impl source moved)"
echo
if [ "$SETUP_FAILURE" = "1" ]; then
  echo -e "${RED}DISPATCHER_SETUP_FAILED — at least one required-check has no SIGNATURES entry or its script is missing. Wire it into run-required-checks.sh before re-running.${NC}"
  exit 2
fi

if [ "$FAIL" -gt 0 ]; then
  echo -e "${RED}CHECKS_FAILED count=$FAIL — this is NOT a dispatcher break.${NC}"
  echo -e "${RED}Run \`uv run python -m ui_clone.gate $REF_DIR post-implement\` for the canonical verdict and per-check fix commands.${NC}"
  exit 1
fi
echo -e "${GREEN}CHECKS_PASSED count=$PASS dispatched=$TOTAL.${NC}"
exit 0
