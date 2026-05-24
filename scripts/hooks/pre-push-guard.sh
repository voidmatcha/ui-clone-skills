#!/bin/bash
# Agent pre-push guard. Blocks git push if:
# 1. skills/ changed without docs/config update,
# 2. Claude Code/Codex plugin manifests and package versions are out of sync, or
# 3. pre-push-security.sh finds blockers (secrets, eval, insecure /tmp, etc).

input=$(cat)
echo "$input" | grep -qE '"command":"[^"]*git[[:space:]]+push' || exit 0

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

# Security gate (Snyk/Socket-class checks - secrets, eval, /tmp, manifests)
if [ -f scripts/ci/pre-push-security.sh ]; then
  if ! bash scripts/ci/pre-push-security.sh --quiet; then
    echo "decision: block" >&2
    echo "Run 'bash scripts/ci/pre-push-security.sh' (no --quiet) to see details." >&2
    exit 2
  fi
fi

# Version sync check: Claude Code plugin, Codex plugin, package metadata, and ui_clone/__init__.py must all match
plugin_v=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])" 2>/dev/null || echo "")
market_v=$(python3 -c "import json; print(json.load(open('.claude-plugin/marketplace.json'))['plugins'][0]['version'])" 2>/dev/null || echo "")
codex_v=$(python3 -c "import json; print(json.load(open('.codex-plugin/plugin.json'))['version'])" 2>/dev/null || echo "")
pyproj_v=$(python3 -c "import re; m=re.search(r'^version\s*=\s*\"([^\"]+)\"', open('pyproject.toml').read(), re.M); print(m.group(1) if m else '')" 2>/dev/null || echo "")
init_v=$(python3 -c "import re; m=re.search(r'__version__\s*=\s*\"([^\"]+)\"', open('ui_clone/__init__.py').read()); print(m.group(1) if m else '')" 2>/dev/null || echo "")
versions="claude-plugin.json=$plugin_v marketplace.json=$market_v codex-plugin.json=$codex_v pyproject.toml=$pyproj_v ui_clone/__init__.py=$init_v"
unique=$(printf '%s\n' "$plugin_v" "$market_v" "$codex_v" "$pyproj_v" "$init_v" | sort -u | grep -v '^$' | wc -l | tr -d ' ')
if [ "$unique" != "1" ]; then
  echo "⚠️ Version mismatch: $versions" >&2
  echo "All versioned package/plugin files must be bumped together. decision: block" >&2
  exit 2
fi

# CI mirror — pytest + mypy + ruff + shell syntax + review.sh.
# Mirrors .github/workflows/ci.yml `test` job so we don't push code that GitHub
# will reject. Slow (~30-60s) so it runs after the fast checks above.
# Bypass: UI_RE_SKIP_CI_LOCAL=1 git push (emergency only — same env var ci-local.sh honors).
if [ -f scripts/ci/ci-local.sh ]; then
  if ! bash scripts/ci/ci-local.sh --quiet; then
    echo "⚠️ CI mirror failed — run 'bash scripts/ci/ci-local.sh' to see details." >&2
    echo "Bypass (emergency only): UI_RE_SKIP_CI_LOCAL=1 git push" >&2
    echo "decision: block" >&2
    exit 2
  fi
fi

upstream=$(git rev-parse --abbrev-ref --symbolic-full-name @{upstream} 2>/dev/null) || upstream=""
if [ -z "$upstream" ]; then
  current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || current_branch=""
  [ -n "$current_branch" ] && upstream="origin/$current_branch"
fi
[ -z "$upstream" ] && upstream="origin/master"
base=$(git rev-parse "$upstream" 2>/dev/null) || {
  echo "⚠️  Cannot resolve upstream ref ($upstream) — skipping push check" >&2
  exit 0
}
[ "$base" = "$(git rev-parse HEAD)" ] && exit 0

changed=$(git diff --name-only "$base" HEAD)
echo "$changed" | grep -q '^skills/' || exit 0

missing=""
for f in CHANGELOG.md .claude-plugin/plugin.json .claude-plugin/marketplace.json .codex-plugin/plugin.json; do
  echo "$changed" | grep -q "^$f$" || missing="$missing $f"
done

if [ -n "$missing" ]; then
  # stderr (not stdout) so Claude Code's PreToolUse hook harness surfaces
  # the reason. The harness shows "No stderr output" and discards stdout,
  # so a stdout-only reject looks like an opaque hook failure and burns
  # iterations debugging. Other reject branches in this script already
  # use >&2; this one was the odd-out.
  echo "⚠️ skills/ changed but missing:$missing" >&2
  echo "Bump CHANGELOG.md and the 3 plugin manifests together, or revert" >&2
  echo "the skills/ change if it was incidental." >&2
  echo "decision: block" >&2
  exit 2
fi
