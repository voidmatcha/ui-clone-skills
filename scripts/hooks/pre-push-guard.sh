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
#    - Version sync: 5 versioned files (.claude-plugin/plugin.json,
#      .claude-plugin/marketplace.json, .codex-plugin/plugin.json,
#      pyproject.toml, ui_clone/__init__.py) must all match.
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

input=$(cat)
echo "$input" | grep -qE '"command":"[^"]*git[[:space:]]+push' || exit 0

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

# Version sync: Claude Code plugin, Codex plugin, package metadata, and
# ui_clone/__init__.py must all match.
plugin_v=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])" 2>/dev/null || echo "")
market_v=$(python3 -c "import json; print(json.load(open('.claude-plugin/marketplace.json'))['plugins'][0]['version'])" 2>/dev/null || echo "")
codex_v=$(python3 -c "import json; print(json.load(open('.codex-plugin/plugin.json'))['version'])" 2>/dev/null || echo "")
pyproj_v=$(python3 -c "import re; m=re.search(r'^version\s*=\s*\"([^\"]+)\"', open('pyproject.toml').read(), re.M); print(m.group(1) if m else '')" 2>/dev/null || echo "")
init_v=$(python3 -c "import re; m=re.search(r'__version__\s*=\s*\"([^\"]+)\"', open('ui_clone/__init__.py').read()); print(m.group(1) if m else '')" 2>/dev/null || echo "")
versions="claude-plugin.json=$plugin_v marketplace.json=$market_v codex-plugin.json=$codex_v pyproject.toml=$pyproj_v ui_clone/__init__.py=$init_v"
unique=$(printf '%s\n' "$plugin_v" "$market_v" "$codex_v" "$pyproj_v" "$init_v" | sort -u | grep -v '^$' | wc -l | tr -d ' ')
if [ "$unique" != "1" ]; then
  echo "⚠️ Version mismatch on release push (target=$target_branch): $versions" >&2
  echo "All versioned package/plugin files must be bumped together." >&2
  echo "decision: block" >&2
  exit 2
fi

# skills/ + CHANGELOG/manifest coupling
upstream=$(git rev-parse --abbrev-ref --symbolic-full-name @{upstream} 2>/dev/null) || upstream=""
if [ -z "$upstream" ]; then
  current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || current_branch=""
  [ -n "$current_branch" ] && upstream="origin/$current_branch"
fi
[ -z "$upstream" ] && upstream="origin/master"
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
