#!/usr/bin/env bash
# pre-push-security.sh — Local security gate, equivalent to Snyk/Socket-class checks.
# Runs without external services. Called by pre-push-guard.sh; can also be run manually.
#
# Usage:
#   bash scripts/ci/pre-push-security.sh           # check, exit 1 on blockers
#   bash scripts/ci/pre-push-security.sh --quiet   # only print on failure
#
# Exit codes: 0 = clean, 1 = blockers found, 2 = invocation error

# Note: -e is intentionally NOT set. grep returning "no match" (exit 1) is normal,
# and the script tracks errors explicitly via err()/ok() counters.
set -uo pipefail

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "pre-push-security: not in a git repo" >&2
  exit 2
}
cd "$REPO_ROOT" || { echo "pre-push-security: cd to repo root failed" >&2; exit 2; }

ERRORS=0
WARNINGS=0
PASSED=0

err() { echo "  ❌ $*" >&2; ERRORS=$((ERRORS + 1)); }
warn() { echo "  ⚠️  $*" >&2; WARNINGS=$((WARNINGS + 1)); }
ok() { [ "$QUIET" = "1" ] || echo "  ✓ $*"; PASSED=$((PASSED + 1)); }
section() { [ "$QUIET" = "1" ] || echo ""; [ "$QUIET" = "1" ] || echo "── $* ──"; }

# SELF: this scanner's own regex literals would self-match; exclude its own filename.
SELF="pre-push-security.sh"
# DRIFT_TEST: scripts/ci/test-parity.sh inlines known-bad strings on purpose so it
# can mutate files to those patterns and verify this scanner still catches them.
# Excluded from secret scans for the same reason as $SELF.
DRIFT_TEST="test-parity.sh"

# Grep pattern $1 across git-tracked files matching pathspec glob(s) $2...
#
# Tracked files, not `grep --exclude-dir=NAME`, is what actually scopes these
# scans to the shipped surface: --exclude-dir matches a directory by BASENAME
# anywhere in the tree, so `--exclude-dir=benchmark` used to hide the tracked
# skills/benchmark/ maintainer skill from every scan below — along with the
# untracked top-level ./benchmark/ (captured runtime data) it was meant to
# exclude — because both share that basename. Untracked dirs (tmp/, scratch/,
# benchmark/, .omx/, node_modules/, .venv/, caches) never appear in
# `git ls-files` regardless of name, so this scopes correctly with no explicit
# exclusion list to keep in sync.
_scan_tracked() {
  local pattern="$1" files
  shift
  files="$(git ls-files -- "$@" | grep -vE "(^|/)(${SELF}|${DRIFT_TEST})\$")"
  [ -n "$files" ] || return 0
  # -H: force the `file:line:` prefix even when only one file matches (a glob
  # this narrow, e.g. '*.lock', legitimately CAN match exactly one file) —
  # without it grep drops the prefix for a single-file argument list, and the
  # `grep -vE "^[^:]*:[0-9]+:"`-shaped filters downstream key on that shape.
  printf '%s\n' "$files" | xargs grep -EHn "$pattern" -- 2>/dev/null
}

section "Secrets"
secret_patterns=(
  'AKIA[0-9A-Z]{16}'                    # AWS access key id
  'sk-[a-zA-Z0-9]{20,}'                 # OpenAI-style key
  'sk-ant-[a-zA-Z0-9_-]{20,}'           # Anthropic key (its hyphens break the sk- pattern above)
  'gh[oprsu]_[A-Za-z0-9]{36}'           # GitHub token: classic ghp_/OAuth gho_/server ghs_/user ghu_/refresh ghr_
  'github_pat_[0-9A-Za-z_]{82}'         # GitHub fine-grained PAT
  'glpat-[0-9A-Za-z_-]{20}'             # GitLab personal access token
  '(sk|rk)_live_[0-9a-zA-Z]{24,}'       # Stripe live secret / restricted key (underscore form sk- misses)
  'xox[baprs]-[0-9A-Za-z-]{10,}'        # Slack token (bot/app/user/refresh/legacy)
  'xapp-[0-9]-[A-Za-z0-9-]{10,}'        # Slack app-level token
  'AIza[0-9A-Za-z_-]{35}'               # Google API key
  'npm_[A-Za-z0-9]{36}'                 # npm classic automation/publish token
  '_authToken=[A-Za-z0-9+/=_.-]{16,}'   # npm registry auth token (.npmrc); value charset excludes ${...} env refs
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'  # PEM private key block
)
secret_hits=0
# N2: token-scoped placeholder suppression. The OLD whole-line filter dropped any
# matched line that ALSO contained example/placeholder/YOUR_/TODO in prose, so a real
# key annotated `# TODO rotate` leaked silently. This drops a hit only when the
# MATCHED TOKEN is itself placeholder-shaped. Markers are case-sensitive (lowercase
# example/placeholder so the AWS docs shape AKIA...EXAMPLE stays caught) plus YOUR_;
# TODO/<YOUR are prose-only and dropped from token scope; A-/x-filler is NOT
# suppressed (drift tests inject A-filler tokens that must stay caught). A function,
# not an inline $() while/case, for bash-3.2 (macOS) parser compatibility.
_drop_placeholder_token_lines() {
  local _p="$1" _line _tok
  while IFS= read -r _line; do
    _tok=$(printf '%s' "$_line" | grep -oE "$_p" | head -1)
    case "$_tok" in
      *example*|*placeholder*|*YOUR_*) continue ;;
    esac
    printf '%s\n' "$_line"
  done
}
for p in "${secret_patterns[@]}"; do
  # tmp/, scratch/, benchmark/, and .omx/ hold generated runtime/captured
  # contents. Those can contain third-party public keys, absolute paths, and
  # local agent logs; they are gitignored and never published, so exclude them
  # from shipped-surface scans.
  # Include the rest of the shipped/trackable surface, not just config files:
  # *.py is the bulk of the published package (ui_clone/), and .npmrc/.env* are
  # the canonical homes of the npm/env tokens above (git-trackable — not in
  # .gitignore). bin/ui-clone (extensionless) ships via npm `files`.
  # Git pathspec matching is NOT grep --include's "basename anywhere" —
  # a literal like 'ui-clone' or '.npmrc' matches only that exact path at
  # the repo ROOT, so bin/ui-clone silently dropped out of this scan when
  # _scan_tracked replaced the old grep --include walk (fable review).
  # Name bin/ui-clone's real path explicitly, and add one-level-deep globs
  # for .npmrc/.env* (nothing nested is tracked today, but a bare literal
  # would silently drop it exactly like bin/ui-clone did).
  hits=$(_scan_tracked "$p" \
    '*.sh' '*.md' '*.json' '*.yaml' '*.yml' '*.py' '*.toml' '*.lock' \
    '.npmrc' '*/.npmrc' '.env*' '*/.env*' 'bin/ui-clone' | \
    grep -vE 'evals\.json' | \
    _drop_placeholder_token_lines "$p" || true)
  if [ -n "$hits" ]; then
    err "Potential secret matching /$p/"
    echo "$hits" | head -3 | sed 's/^/      /' >&2
    secret_hits=$((secret_hits + 1))
  fi
done
[ "$secret_hits" -eq 0 ] && ok "no API keys / private keys / tokens"

section "Code injection"
eval_count=$(_scan_tracked '(^|[[:space:];&|])eval[[:space:]"'"'"']' '*.sh' | \
  grep -v 'agent-browser' | \
  grep -v "^[^:]*:[0-9]*:[[:space:]]*echo " | \
  grep -vE "^[^:]*:[0-9]+:[[:space:]]*#" | wc -l | tr -d ' ')
[ "$eval_count" -eq 0 ] && ok "no bash eval()" || err "bash eval() found ($eval_count occurrences)"

# CWE-377: insecure use of fixed temporary file paths (race / symlink attack)
# tmp/, scratch/, and benchmark/ hold captured agent / third-party site scripts that are
# not part of the shipped surface — same exclusion as the secret scan above.
fixed_tmp=$(_scan_tracked '/tmp/[a-zA-Z][a-zA-Z0-9_.-]+\.(txt|log|json|tmp)' '*.sh' | \
  grep -v 'mktemp\|RESULT_FILE\|TEMP_FILE' | wc -l | tr -d ' ')
[ "$fixed_tmp" -eq 0 ] && ok "no fixed /tmp paths (CWE-377)" || err "fixed /tmp paths found ($fixed_tmp)"

backdoor=$(_scan_tracked 'nc -[el]|/dev/tcp/|bash -i.*&|reverse shell|exec [0-9]<>/dev/' '*.sh' | wc -l | tr -d ' ')
[ "$backdoor" -eq 0 ] && ok "no reverse-shell / backdoor patterns" || err "backdoor pattern ($backdoor)"

section "Manifest validity"
if command -v python3 >/dev/null 2>&1; then
  python3 -c "import json; json.load(open('.claude-plugin/plugin.json'))" 2>/dev/null && \
    ok "plugin.json valid JSON" || err "plugin.json invalid JSON"
  python3 -c "import json; json.load(open('.claude-plugin/marketplace.json'))" 2>/dev/null && \
    ok "marketplace.json valid JSON" || err "marketplace.json invalid JSON"
  if [ -f ".codex-plugin/plugin.json" ]; then
    python3 -c "import json; json.load(open('.codex-plugin/plugin.json'))" 2>/dev/null && \
      ok ".codex-plugin/plugin.json valid JSON" || err ".codex-plugin/plugin.json invalid JSON"
  fi
  if [ -f "hooks/hooks.json" ]; then
    python3 -c "import json; json.load(open('hooks/hooks.json'))" 2>/dev/null && \
      ok "hooks/hooks.json valid JSON" || err "hooks/hooks.json invalid JSON"
  fi
  if [ -f "hooks/codex-hooks.json" ]; then
    python3 -c "import json; json.load(open('hooks/codex-hooks.json'))" 2>/dev/null && \
      ok "hooks/codex-hooks.json valid JSON" || err "hooks/codex-hooks.json invalid JSON"
  fi
  if python3 "$REPO_ROOT/scripts/ci/validate_openai_agent_manifests.py"
  then
    ok "skills/*/agents/openai.yaml shape valid"
  else
    err "skills/*/agents/openai.yaml shape invalid"
  fi
else
  warn "python3 not available - skipping JSON validity check"
fi

section "Shell syntax"
# Use bash 4+ explicitly. macOS ships bash 3.2 as /bin/bash, which cannot parse
# a heredoc nested inside `$(...)` command substitution (a 3.2 limitation) and
# would false-flag valid scripts as syntax errors. All target hosts (GitHub
# Actions Ubuntu, Linux installs, macOS users with `brew install bash`) run
# bash 4+, so resolve one here — mirrors scripts/ci/ci-local.sh.
SYNTAX_BASH=$(command -v bash)
if [ "$("$SYNTAX_BASH" -c 'echo ${BASH_VERSION%%.*}' 2>/dev/null)" -lt 4 ] 2>/dev/null; then
  for cand in /opt/homebrew/bin/bash /usr/local/bin/bash; do
    [ -x "$cand" ] && { SYNTAX_BASH="$cand"; break; }
  done
fi
syntax_fail=0
while IFS= read -r f; do
  "$SYNTAX_BASH" -n "$f" 2>/dev/null || { err "syntax error: $f"; syntax_fail=$((syntax_fail + 1)); }
done < <(find scripts hooks -name '*.sh' -type f 2>/dev/null; [ -f install.sh ] && printf '%s\n' install.sh)
[ "$syntax_fail" -eq 0 ] && ok "all shell scripts parse"

if command -v shellcheck >/dev/null 2>&1; then
  section "Shellcheck (error-level)"
  sc_errors=0
  while IFS= read -r f; do
    e=$(shellcheck -S error "$f" 2>&1 | grep -c '^In ' || true)
    sc_errors=$((sc_errors + e))
  done < <(find scripts hooks -name '*.sh' -type f 2>/dev/null; [ -f install.sh ] && printf '%s\n' install.sh)
  [ "$sc_errors" -eq 0 ] && ok "shellcheck error-level clean" || err "shellcheck errors: $sc_errors"
fi

section "Cross-references"
broken_refs=0
for f in skills/*/SKILL.md; do
  [ -f "$f" ] || continue
  skill_dir="$(dirname "$f")"
  while IFS= read -r ref; do
    [ -z "$ref" ] && continue
    target="$skill_dir/$ref"
    if [ ! -e "$target" ]; then
      err "broken ref: $f → $ref"
      broken_refs=$((broken_refs + 1))
    fi
  done < <(grep -oE '\.\./[a-zA-Z_-]+/[a-zA-Z0-9_./-]+\.(md|sh|json)' "$f" 2>/dev/null | sort -u)
done
[ "$broken_refs" -eq 0 ] && ok "all cross-refs resolve"

section "Universality"
# Maintainer-bias drift gate — blocks loop-N attribution, benchmark site
# names, brand leakage, personal paths, Hangul in production source.
# Full pattern list lives in scripts/ci/check-universality.sh header.
if bash "$REPO_ROOT/scripts/ci/check-universality.sh" >/dev/null 2>&1; then
  ok "no maintainer-bias drift"
else
  err "universality violations — run \`bash scripts/ci/check-universality.sh\` for details"
fi

section "Version sync"
if command -v python3 >/dev/null 2>&1; then
  plugin_v=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])" 2>/dev/null || echo "")
  market_v=$(python3 -c "import json; print(json.load(open('.claude-plugin/marketplace.json'))['plugins'][0]['version'])" 2>/dev/null || echo "")
  codex_v=$(python3 -c "import json; print(json.load(open('.codex-plugin/plugin.json'))['version'])" 2>/dev/null || echo "")
  package_v=$(python3 -c "import json; print(json.load(open('package.json'))['version'])" 2>/dev/null || echo "")
  pyproj_v=$(python3 -c "import re; m=re.search(r'^version\s*=\s*\"([^\"]+)\"', open('pyproject.toml').read(), re.M); print(m.group(1) if m else '')" 2>/dev/null || echo "")
  init_v=$(python3 -c "import re; m=re.search(r'__version__\s*=\s*\"([^\"]+)\"', open('ui_clone/__init__.py').read()); print(m.group(1) if m else '')" 2>/dev/null || echo "")
  if [ -n "$plugin_v" ] && [ -n "$market_v" ] && [ -n "$codex_v" ] && [ -n "$package_v" ] && [ -n "$pyproj_v" ] && [ -n "$init_v" ]; then
    [ "$plugin_v" = "$market_v" ] && [ "$plugin_v" = "$codex_v" ] && [ "$plugin_v" = "$package_v" ] && [ "$plugin_v" = "$pyproj_v" ] && [ "$plugin_v" = "$init_v" ] && ok "versions match: $plugin_v" || \
      err "version mismatch: claude-plugin=$plugin_v marketplace=$market_v codex-plugin=$codex_v package.json=$package_v pyproject.toml=$pyproj_v ui_clone/__init__.py=$init_v"
  else
    warn "could not extract versions"
  fi
fi

section "Lockfile freshness"
# Every runtime `uv run` in this repo now passes --frozen (shim.sh,
# bin/ui-clone, auto-verify.sh, run_gate, register-driver-session.sh) — a
# pyproject.toml-vs-uv.lock drift used to just cost a slow re-resolve; now it
# makes `uv run --frozen` error outright, and hooks/shim.sh swallows that
# error silently (fable review). `uv lock --check` is the only thing that
# catches drift before it ships.
if command -v uv >/dev/null 2>&1; then
  if uv lock --check --quiet >/dev/null 2>&1; then
    ok "uv.lock matches pyproject.toml"
  else
    err "uv.lock is stale — run 'uv lock' and commit the result"
  fi
else
  warn "uv not found — skipping lockfile freshness check"
fi

echo ""
echo "════════════════════════════════════════"
echo "  Pre-push security: $PASSED passed, $WARNINGS warnings, $ERRORS blockers"
echo "════════════════════════════════════════"

if [ "$ERRORS" -gt 0 ]; then
  echo "  ⛔ BLOCKERS found - fix before push" >&2
  exit 1
fi

if [ "$QUIET" = "1" ]; then
  echo "  ✓ clean"
fi
exit 0
