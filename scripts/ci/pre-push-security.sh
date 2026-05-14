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
cd "$REPO_ROOT"

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
# Excluded from secret + identity-leak scans for the same reason as $SELF.
DRIFT_TEST="test-parity.sh"

section "Secrets"
secret_patterns=(
  'AKIA[0-9A-Z]{16}'
  'sk-[a-zA-Z0-9]{20,}'
  'ghp_[a-zA-Z0-9]{36}'
  'xox[baprs]-[0-9A-Za-z-]{10,}'
  'AIza[0-9A-Za-z_-]{35}'
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
)
secret_hits=0
for p in "${secret_patterns[@]}"; do
  # tmp/ and benchmark/ hold third-party site contents captured by the
  # pipeline (e.g. realfood.gov head.json). Those contain PUBLIC API keys
  # that ship in the site's own HTML — not maintainer secrets. They're
  # gitignored and never published, so exclude from this scan.
  hits=$(grep -rEn "$p" \
    --include='*.sh' --include='*.md' --include='*.json' --include='*.yaml' --include='*.yml' \
    --exclude="$SELF" --exclude="$DRIFT_TEST" \
    --exclude-dir=.git --exclude-dir=tmp --exclude-dir=benchmark \
    --exclude-dir=.venv --exclude-dir=.mypy_cache --exclude-dir=.sisyphus \
    . 2>/dev/null | \
    grep -vE 'evals\.json|example|placeholder|YOUR_|TODO|<YOUR' || true)
  if [ -n "$hits" ]; then
    err "Potential secret matching /$p/"
    echo "$hits" | head -3 | sed 's/^/      /' >&2
    secret_hits=$((secret_hits + 1))
  fi
done
[ "$secret_hits" -eq 0 ] && ok "no API keys / private keys / tokens"

section "Code injection"
eval_count=$(grep -rEn '(^|[[:space:];&|])eval[[:space:]"'"'"']' \
  --include='*.sh' --exclude="$SELF" --exclude-dir=.git . 2>/dev/null | \
  grep -v 'agent-browser' | \
  grep -v "^[^:]*:[0-9]*:[[:space:]]*echo " | \
  grep -vE "^[^:]*:[0-9]+:[[:space:]]*#" | wc -l | tr -d ' ')
[ "$eval_count" -eq 0 ] && ok "no bash eval()" || err "bash eval() found ($eval_count occurrences)"

# CWE-377: insecure use of fixed temporary file paths (race / symlink attack)
# tmp/ and benchmark/ hold captured agent / third-party site scripts that are
# not part of the shipped surface — same exclusion as the secret scan above.
fixed_tmp=$(grep -rEn '/tmp/[a-zA-Z][a-zA-Z0-9_.-]+\.(txt|log|json|tmp)' \
  --include='*.sh' --exclude="$SELF" \
  --exclude-dir=.git --exclude-dir=tmp --exclude-dir=benchmark . 2>/dev/null | \
  grep -v 'mktemp\|RESULT_FILE\|TEMP_FILE' | wc -l | tr -d ' ')
[ "$fixed_tmp" -eq 0 ] && ok "no fixed /tmp paths (CWE-377)" || err "fixed /tmp paths found ($fixed_tmp)"

backdoor=$(grep -rEn 'nc -[el]|/dev/tcp/|bash -i.*&|reverse shell|exec [0-9]<>/dev/' \
  --include='*.sh' --exclude="$SELF" \
  --exclude-dir=.git --exclude-dir=tmp --exclude-dir=benchmark . 2>/dev/null | wc -l | tr -d ' ')
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
  if python3 - <<'PY'
import pathlib
import re
import sys

expected = {
    pathlib.Path("skills/ui-reverse-engineering/agents/openai.yaml"),
    pathlib.Path("skills/ui-capture/agents/openai.yaml"),
    pathlib.Path("skills/visual-debug/agents/openai.yaml"),
}
paths = set(pathlib.Path("skills").glob("*/agents/openai.yaml"))
errors = []

for missing in sorted(expected - paths):
    errors.append(f"{missing}: missing OpenAI agent manifest")

required = {
    "interface": ("display_name", "short_description", "default_prompt"),
    "policy": ("allow_implicit_invocation",),
}

for path in sorted(paths):
    text = path.read_text(encoding="utf-8")
    if "\t" in text:
        errors.append(f"{path}: tabs are not allowed")

    sections = {}
    current = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            key = line.split(":", 1)[0].strip()
            current = key
            sections.setdefault(current, {})
            continue
        if current is None:
            continue
        match = re.match(r"^  ([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if match:
            sections.setdefault(current, {})[match.group(1)] = match.group(2).strip()

    for section, keys in required.items():
        if section not in sections:
            errors.append(f"{path}: missing {section}: section")
            continue
        for key in keys:
            if key not in sections[section]:
                errors.append(f"{path}: missing {section}.{key}")
            elif sections[section][key] == "":
                errors.append(f"{path}: empty {section}.{key}")

if errors:
    for error in errors:
        print(error, file=sys.stderr)
    sys.exit(1)
PY
  then
    ok "skills/*/agents/openai.yaml shape valid"
  else
    err "skills/*/agents/openai.yaml shape invalid"
  fi
else
  warn "python3 not available - skipping JSON validity check"
fi

section "Shell syntax"
syntax_fail=0
while IFS= read -r f; do
  bash -n "$f" 2>/dev/null || { err "syntax error: $f"; syntax_fail=$((syntax_fail + 1)); }
done < <(find scripts hooks -name '*.sh' -type f 2>/dev/null)
[ "$syntax_fail" -eq 0 ] && ok "all shell scripts parse"

if command -v shellcheck >/dev/null 2>&1; then
  section "Shellcheck (error-level)"
  sc_errors=0
  while IFS= read -r f; do
    e=$(shellcheck -S error "$f" 2>&1 | grep -c '^In ' || true)
    sc_errors=$((sc_errors + e))
  done < <(find scripts hooks -name '*.sh' -type f 2>/dev/null)
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

section "Identity leakage"
# Real company / employer-specific service names must not appear in code, docs,
# or fixtures. The plugin is a public-facing marketplace surface; leakage of
# the maintainer's employer / work-project names is unprofessional and embeds
# NDA-adjacent info. Use generic placeholders: project-a / example.com /
# <cdn-domain-1> / <streaming-cdn-host> / "a partner site". This scanner runs
# on staged content. To add a new denylist entry, append to leak_patterns.
leak_patterns=(
  '\bnavercorp\b'
  '\bkurlynmart\b'
  '\bagencefoudre\b'
  'livecloud-thumb\.akamaized\.net'
  'nng-phinf\.pstatic\.net'
)
leak_hits=0
for p in "${leak_patterns[@]}"; do
  hits=$(grep -rEn "$p" \
    --include='*.sh' --include='*.py' --include='*.md' --include='*.json' \
    --include='*.yaml' --include='*.yml' --include='*.ts' --include='*.tsx' \
    --exclude="$SELF" --exclude="$DRIFT_TEST" --exclude-dir=.git --exclude-dir=tmp --exclude-dir=node_modules \
    . 2>/dev/null || true)
  if [ -n "$hits" ]; then
    err "identity leak ($p):"
    echo "$hits" | head -5 | sed 's/^/      /' >&2
    leak_hits=$((leak_hits + 1))
  fi
done
[ "$leak_hits" -eq 0 ] && ok "no real company/site identifiers leaked"

section "Version sync"
if command -v python3 >/dev/null 2>&1; then
  plugin_v=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])" 2>/dev/null || echo "")
  market_v=$(python3 -c "import json; print(json.load(open('.claude-plugin/marketplace.json'))['plugins'][0]['version'])" 2>/dev/null || echo "")
  codex_v=$(python3 -c "import json; print(json.load(open('.codex-plugin/plugin.json'))['version'])" 2>/dev/null || echo "")
  if [ -n "$plugin_v" ] && [ -n "$market_v" ] && [ -n "$codex_v" ]; then
    [ "$plugin_v" = "$market_v" ] && [ "$plugin_v" = "$codex_v" ] && ok "versions match: $plugin_v" || \
      err "version mismatch: claude-plugin=$plugin_v marketplace=$market_v codex-plugin=$codex_v"
  else
    warn "could not extract versions"
  fi
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
