#!/bin/bash
# Agent pre-push guard. Two-tier policy:
#
# 1. Always-on (every push, regardless of target branch):
#    - Security gate (pre-push-security.sh): secrets, eval, insecure /tmp,
#      manifest leaks. Things that must never reach origin under any
#      circumstance.
#    - CI mirror (ci-local.sh): pytest + mypy + ruff + shell syntax +
#      review + universality + drift smoke. Catches anything GitHub
#      Actions would reject so we don't push code that won't build.
#
# 2. Release-discipline (gated on push to main / master, or --all/--mirror
#    which touches every branch including main):
#    - Version sync: 6 versioned files (.claude-plugin/plugin.json,
#      .claude-plugin/marketplace.json, .codex-plugin/plugin.json,
#      package.json, pyproject.toml, ui_clone/__init__.py) must all match.
#    - Version-bump enforcement: the matching version must also differ from
#      whatever version is recorded as installed on THIS machine (Claude/
#      Codex plugin caches are version-keyed; see the check itself, below).
#    - skills/ + CHANGELOG/manifest coupling: if skills/ changed,
#      CHANGELOG.md and the 3 plugin manifests must be bumped together.
#
# Why the split: version-sync and the skills-coupling rules are RELEASE
# discipline. Enforcing them on every WIP push to a feature branch (e.g.
# `tmp`) penalized normal iteration — a single commit rarely justifies
# a version bump — and pushed users to bypass the hook by pushing from
# terminals outside Claude Code. That made enforcement asymmetric and
# let policy violations accumulate silently on work branches. Gating
# release checks on main/master push means the same checks fire at the
# merge boundary (when work actually lands on the release branch)
# without taxing iteration on feature branches.
#
# Bypasses (emergency only):
#   UI_RE_SKIP_CI_LOCAL=1 git push       # skip ci-local
#   UI_RE_SKIP_RELEASE_CHECKS=1 git push  # skip release-discipline tier
#                                          (still useful when patching
#                                          the release flow itself)
#
# Testing: UI_CLONE_INSTALLED_PLUGINS_JSON=<path> overrides the
# installed_plugins.json path the version-bump-enforcement check reads
# (default ~/.claude/plugins/installed_plugins.json).

input=$(cat)
# Extract tool_input.command via JSON parsing rather than a raw-text grep
# expecting compact `"command":"..."` (no space after the colon) — Claude
# Code's PreToolUse payload happens to serialize that way, but Codex's
# PreToolUse/exec_command payload shape is not guaranteed to match byte-for-
# byte (fable-20260910, Codex dev-hook parity design). Falls back to the
# original compact-JSON grep if python3 or JSON parsing is unavailable, so
# this never regresses the Claude-only path it replaces.
hook_command=$(GUARD_INPUT="$input" python3 -c '
import json, os, sys
try:
    print(json.loads(os.environ.get("GUARD_INPUT", "{}")).get("tool_input", {}).get("command", ""))
except Exception:
    sys.exit(1)
' 2>/dev/null) || hook_command=""
if [ -n "$hook_command" ]; then
  echo "$hook_command" | grep -qE '\bgit[[:space:]]+push\b' || exit 0
else
  echo "$input" | grep -qE '"command":\s*"[^"]*git[[:space:]]+push' || exit 0
fi

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

# ── Determine target branch ──
# Common forms:
#   git push                       → current branch (implicit)
#   git push origin tmp            → tmp
#   git push origin HEAD:main      → main (remote side of refspec)
#   git push origin refs/heads/main → main (strip refs/heads/ prefix)
#   git push origin +tmp           → tmp (strip force-push prefix)
#   git push --all / --mirror      → ALL (touches every branch incl main)
# Unparseable / unexpected → fall back to current branch, then to "main"
# (safe default: strict, never silently downgrade to loose).
target_branch=$(GUARD_INPUT="$input" python3 -c '
import json, os, re, sys
try:
    cmd = json.loads(os.environ.get("GUARD_INPUT", "{}")).get("tool_input", {}).get("command", "")
except Exception:
    cmd = ""

# --all / --mirror touches every branch — treat as a release push
if re.search(r"\bgit\s+push\b.*(?:--all|--mirror)\b", cmd):
    print("ALL"); sys.exit(0)

# Match: git push <flags...> <remote> [<refspec>]
m = re.search(r"\bgit\s+push\s+((?:-\S+\s+)*)(\S+)(?:\s+(\S+))?", cmd)
if not m or m.group(2).startswith("-"):
    sys.exit(0)
refspec = (m.group(3) or "").strip()
if not refspec or refspec.startswith("-"):
    sys.exit(0)
target = refspec.split(":")[-1].lstrip("+")
if target.startswith("refs/heads/"):
    target = target[len("refs/heads/"):]
print(target)
' 2>/dev/null)

if [ -z "$target_branch" ]; then
  target_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || target_branch="main"
fi

is_release_target=0
case "$target_branch" in
  main|master|ALL) is_release_target=1 ;;
esac

# ── Tier 1: always-on ──

# Security gate (Snyk/Socket-class checks: secrets, eval, /tmp, manifests)
if [ -f scripts/ci/pre-push-security.sh ]; then
  if ! bash scripts/ci/pre-push-security.sh --quiet; then
    echo "decision: block" >&2
    echo "Run 'bash scripts/ci/pre-push-security.sh' (no --quiet) to see details." >&2
    exit 2
  fi
fi

# CI mirror — pytest + mypy + ruff + shell syntax + review.sh.
# Mirrors .github/workflows/ci.yml `test` job so we don't push code that
# GitHub will reject. Slow (~30-60s) so it runs after the fast checks above.
#
# Export UI_CLONE_REVIEW_SKIP_SECURITY=1 — pre-push-security.sh already ran
# above (line ~88), and ci-local's nested review.sh call would otherwise
# re-run it. Eliminates duplicate ~5s scan during `git push`. ci-local
# additionally sets UI_CLONE_REVIEW_SKIP_TESTS=1 inline so pytest runs
# exactly once (in ci-local step 1, not again inside review.sh).
if [ -f scripts/ci/ci-local.sh ]; then
  if ! UI_CLONE_REVIEW_SKIP_SECURITY=1 bash scripts/ci/ci-local.sh --quiet; then
    echo "⚠️ CI mirror failed — run 'bash scripts/ci/ci-local.sh' to see details." >&2
    echo "Bypass (emergency only): UI_RE_SKIP_CI_LOCAL=1 git push" >&2
    echo "decision: block" >&2
    exit 2
  fi
fi

# ── Tier 2: release-discipline (main/master push only) ──

if [ "$is_release_target" != "1" ] || [ "${UI_RE_SKIP_RELEASE_CHECKS:-}" = "1" ]; then
  # Feature-branch push, or release tier explicitly skipped — done.
  exit 0
fi

# fable-20260910 (Codex dev-hook parity review, finding 4): the comparison
# base for "what changed on this release push" must be the REMOTE branch
# actually being pushed to ($target_branch, already resolved above from the
# refspec), not @{upstream} of whatever branch happens to be checked out
# locally. `git push origin HEAD:main` or `git push origin feature:main`
# from a feature branch resolves @{upstream} to origin/feature (or nothing)
# — comparing against that silently let both the version-bump-enforcement
# check below AND the skills/CHANGELOG coupling check pass a push that puts
# real, unbumped content on origin/main. Only fall back to @{upstream} when
# the push target IS the checked-out branch (the common `git push` /
# `git push origin <branch>` case, where @{upstream} is the right answer and
# a detached-HEAD or first-push branch may have no origin/$target_branch yet).
_release_push_base() {
  local current
  current=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || current=""
  if [ -n "$current" ] && [ "$current" != "HEAD" ] && [ "$current" != "$target_branch" ]; then
    echo "origin/$target_branch"
    return
  fi
  local up
  up=$(git rev-parse --abbrev-ref --symbolic-full-name @{upstream} 2>/dev/null) || up=""
  if [ -n "$up" ]; then
    echo "$up"
  else
    echo "origin/$target_branch"
  fi
}

# Version sync: Claude Code plugin, Codex plugin, package metadata, and
# ui_clone/__init__.py must all match.
plugin_v=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])" 2>/dev/null || echo "")
market_v=$(python3 -c "import json; print(json.load(open('.claude-plugin/marketplace.json'))['plugins'][0]['version'])" 2>/dev/null || echo "")
codex_v=$(python3 -c "import json; print(json.load(open('.codex-plugin/plugin.json'))['version'])" 2>/dev/null || echo "")
package_v=$(python3 -c "import json; print(json.load(open('package.json'))['version'])" 2>/dev/null || echo "")
pyproj_v=$(python3 -c "import re; m=re.search(r'^version\s*=\s*\"([^\"]+)\"', open('pyproject.toml').read(), re.M); print(m.group(1) if m else '')" 2>/dev/null || echo "")
init_v=$(python3 -c "import re; m=re.search(r'__version__\s*=\s*\"([^\"]+)\"', open('ui_clone/__init__.py').read()); print(m.group(1) if m else '')" 2>/dev/null || echo "")
versions="claude-plugin.json=$plugin_v marketplace.json=$market_v codex-plugin.json=$codex_v package.json=$package_v pyproject.toml=$pyproj_v ui_clone/__init__.py=$init_v"
unique=$(printf '%s\n' "$plugin_v" "$market_v" "$codex_v" "$package_v" "$pyproj_v" "$init_v" | sort -u | grep -v '^$' | wc -l | tr -d ' ')
if [ "$unique" != "1" ]; then
  echo "⚠️ Version mismatch on release push (target=$target_branch): $versions" >&2
  echo "All versioned package/plugin files must be bumped together." >&2
  echo "decision: block" >&2
  exit 2
fi

# Version-bump enforcement: both Claude Code's and Codex's plugin caches are
# VERSION-KEYED (~/.claude/plugins/cache/<owner>/<plugin>/<version>,
# ~/.codex/plugins/cache/<owner>/<plugin>/<version>). post-push-refresh.sh
# re-runs install.sh after every push, but `claude plugin update` / `codex
# plugin add` are no-ops when the manifest version matches a version already
# recorded as installed on this machine — the live cache silently stays
# stale even though the marketplace source updated. Verified 2026-09-10:
# install.sh's cache-population steps only copy fresh bytes into a NEW
# version-named directory; an unchanged version reuses the existing one.
# Block a release push that reuses a version already installed HERE when
# there is real content to push, so post-push-refresh can always deliver a
# genuinely new cache dir. Best-effort: skip silently if this machine has
# never installed the plugin (installed_plugins.json absent/unparseable) —
# this is a local-dev convenience check, not something CI machines need.
current_version="$plugin_v"
_installed_plugins_json="${UI_CLONE_INSTALLED_PLUGINS_JSON:-$HOME/.claude/plugins/installed_plugins.json}"
deployed_version=$(python3 -c "
import json, sys
try:
    d = json.load(open('$_installed_plugins_json'))
except Exception:
    sys.exit(0)
for entry in d.get('plugins', {}).get('ui-clone-skills@voidmatcha') or []:
    v = entry.get('version')
    if v:
        print(v)
        break
" 2>/dev/null)
if [ -n "$deployed_version" ] && [ "$deployed_version" = "$current_version" ]; then
  _bump_upstream=$(_release_push_base)
  _bump_base=$(git rev-parse "$_bump_upstream" 2>/dev/null) || _bump_base=""
  if [ -n "$_bump_base" ] && [ "$_bump_base" != "$(git rev-parse HEAD)" ] \
     && [ -n "$(git diff --name-only "$_bump_base" HEAD)" ]; then
    echo "⚠️ Version unchanged ($current_version) but content differs from $_bump_upstream, and $current_version is already installed on this machine." >&2
    echo "Claude/Codex plugin caches are version-keyed — pushing without a bump leaves the live local install stale." >&2
    echo "Bump the 6 version files together (see AGENTS.md 'Version sync') before pushing to $target_branch." >&2
    echo "Bypass (emergency only): UI_RE_SKIP_RELEASE_CHECKS=1 git push" >&2
    echo "decision: block" >&2
    exit 2
  fi
fi

# skills/ + CHANGELOG/manifest coupling
upstream=$(_release_push_base)
base=$(git rev-parse "$upstream" 2>/dev/null) || {
  echo "⚠️  Cannot resolve upstream ref ($upstream) — skipping skills/ coupling check" >&2
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
  # iterations debugging.
  echo "⚠️ skills/ changed on release push to $target_branch but missing:$missing" >&2
  echo "Bump CHANGELOG.md and the 3 plugin manifests together, or revert" >&2
  echo "the skills/ change if it was incidental." >&2
  echo "decision: block" >&2
  exit 2
fi
