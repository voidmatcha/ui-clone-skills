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
# fable-20260911 round 5 follow-up: also resolve which LOCAL ref is actually
# being pushed (the refspec's SOURCE side), not just the remote target. A
# bare `git push origin main` pushes the LOCAL branch named "main" — which
# is NOT necessarily HEAD/the checked-out branch. Every check below used to
# diff/read against hardcoded HEAD, so `git push origin main` (or
# `origin main:main`) run from a DIFFERENT checked-out branch silently
# compared and read the WRONG branch's content and diffed nothing.
_push_parsed=$(GUARD_INPUT="$input" python3 -c '
import json, os, re, sys
try:
    cmd = json.loads(os.environ.get("GUARD_INPUT", "{}")).get("tool_input", {}).get("command", "")
except Exception:
    cmd = ""

# --all / --mirror touches every branch — treat as a release push. No single
# well-defined source branch either; the bash side falls back to the
# checked-out branch for this case (see _resolve_release_refs).
if re.search(r"\bgit\s+push\b.*(?:--all|--mirror)\b", cmd):
    print("ALL")
    print("")
    print("")
    sys.exit(0)

# Match: git push <flags...> <remote> [<refspec>]
m = re.search(r"\bgit\s+push\s+((?:-\S+\s+)*)(\S+)(?:\s+(\S+))?", cmd)
if not m or m.group(2).startswith("-"):
    sys.exit(0)
refspec = (m.group(3) or "").strip()
if not refspec or refspec.startswith("-"):
    sys.exit(0)
parts = refspec.split(":", 1)
src = parts[0].lstrip("+")
dst = (parts[1] if len(parts) > 1 else parts[0]).lstrip("+")
if dst.startswith("refs/heads/"):
    dst = dst[len("refs/heads/"):]
print(dst)
print(src)
# Printed so bash can sanity-check it is a REAL configured remote — a flag
# taking its own separate argument (e.g. `-o ci.skip origin main`,
# `--push-option`, `--repo`, `--receive-pack`) is not matched by the
# `(?:-\S+\s+)*` flag-skipping group above and gets misread as "the remote"
# followed by "the refspec" one token early. Round 5 follow-up review, MINOR.
print(m.group(2))
' 2>/dev/null)

target_branch=$(printf '%s\n' "$_push_parsed" | sed -n '1p')
push_source=$(printf '%s\n' "$_push_parsed" | sed -n '2p')
_push_remote=$(printf '%s\n' "$_push_parsed" | sed -n '3p')

# If a "remote" was parsed (non-ALL case) but it's neither an actually
# configured remote NOR a direct URL/SCP-style destination (`git push
# git@host:repo.git main`, `git push https://... main` are both legitimate
# and common — a configured-remote-name check alone rejected them, a round-6
# follow-up review regression: those pushes used to parse correctly and now
# silently fell through to the fallback below), the regex almost certainly
# misread a flag-with-its-own-argument (e.g. `-o ci.skip origin main`) as the
# remote/refspec pair one token early.
_push_remote_looks_like_dest=0
case "$_push_remote" in
  *://*|*@*:*) _push_remote_looks_like_dest=1 ;;
esac
if [ -n "$_push_remote" ] && [ "$target_branch" != "ALL" ] \
   && [ "$_push_remote_looks_like_dest" != "1" ] \
   && ! git remote 2>/dev/null | grep -qxF "$_push_remote"; then
  # Genuinely can't tell what's being pushed — this file's own rule is
  # "never silently downgrade to loose" (see the comment block above), so
  # default to the STRICT choice (main, always release-tier) rather than
  # whatever happens to be checked out, which could easily be a non-release
  # branch that would skip the tier entirely.
  target_branch="main"
  push_source="HEAD"
fi

if [ -z "$target_branch" ]; then
  target_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || target_branch="main"
fi
# Bare `git push` (no refspec) and the unparseable fallback both push the
# checked-out branch; an explicit "HEAD:main"-style refspec also names HEAD
# directly. Only a bare local branch NAME (e.g. "main" with no colon) can
# differ from HEAD, and that case already set push_source above.
[ -z "$push_source" ] && push_source="HEAD"

# fable-20260911 round 6 follow-up ("also re-check the non-generic parts"):
# use the ACTUAL parsed remote name instead of hardcoding "origin" wherever
# one is needed — this repo only ever pushes to "origin", but the parser
# already knows better when it doesn't. Only a plain configured remote NAME
# has a meaningful "<remote>/<branch>" remote-tracking namespace to compare
# against; a direct URL/SCP destination has none (git never fetches/tracks
# it under a name), so that case and the --all/--mirror case (no single
# remote captured at all) keep the "origin" default, matching this repo's
# only configured remote.
_release_remote="origin"
if [ -n "$_push_remote" ] && [ "$_push_remote_looks_like_dest" != "1" ] \
   && git remote 2>/dev/null | grep -qxF "$_push_remote"; then
  _release_remote="$_push_remote"
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

# fable-20260910/11 follow-up reviews: the comparison base for "what changed
# on this release push" must be the REMOTE branch actually being pushed to
# ($target_branch, resolved above from the refspec), not @{upstream} of
# whatever branch happens to be checked out locally — and the comparison
# SOURCE (the "new" content about to land) must be the LOCAL ref actually
# named by the refspec ($push_source, resolved above), not hardcoded HEAD.
# `git push origin main` run while some OTHER branch is checked out pushes
# the LOCAL branch named "main" — reading/diffing against HEAD (a different
# branch's working tree) silently checked the wrong content entirely, a
# round-5 regression discovered in the round-4 refspec-base fix itself.
# Resolves the BASE ref (remote "old" state to diff/read FROM) for a single
# local SOURCE ref (the "new" state about to be pushed): prefer the real
# remote-tracking ref for that branch name over its own @{upstream} —
# @{upstream} can point anywhere (or nowhere) when the branch being pushed
# isn't the one checked out (fable-20260911 round 4/5 follow-up reviews).
# $1 = branch name (e.g. "main"), $2 = local source ref (e.g. "refs/heads/main").
_resolve_base_for() {
  local branch="$1" source_ref="$2" source_for_upstream
  if git rev-parse -q --verify "refs/remotes/${_release_remote}/$branch" >/dev/null 2>&1; then
    echo "${_release_remote}/$branch"
    return
  fi
  local up
  # round-6 follow-up: this must be $source_ref's own upstream, not bare
  # @{upstream} (which always means HEAD's upstream regardless of what's
  # actually being pushed). git's `<ref>@{upstream}` suffix only accepts a
  # short branch name or HEAD, not a full "refs/heads/..." path (verified:
  # `git rev-parse refs/heads/main@{upstream}` errors "no such branch") —
  # strip the prefix so this degrades to a real lookup instead of always
  # silently falling through to the naive guess below.
  source_for_upstream="${source_ref#refs/heads/}"
  up=$(git rev-parse --abbrev-ref --symbolic-full-name "${source_for_upstream}@{upstream}" 2>/dev/null) || up=""
  if [ -n "$up" ]; then
    echo "$up"
  else
    echo "${_release_remote}/$branch"
  fi
}

# Runs the full release-discipline check (version sync, version-bump
# enforcement, skills/CHANGELOG coupling) for one base/source pair. `return`s
# on "nothing to report for this pair" so a --all/--mirror caller can keep
# checking additional pairs (round-6 follow-up: checking only the first of
# main/master let an unbumped, real change on the SECOND one through); a
# genuine block still `exit 2`s immediately, same as before.
_run_release_checks_for() {
  local _version_bump_base="$1" _release_push_src="$2" _branch_label="$3"
  local _version_bump_base_sha _release_push_src_sha
  _version_bump_base_sha=$(git rev-parse "$_version_bump_base" 2>/dev/null) || _version_bump_base_sha=""
  _release_push_src_sha=$(git rev-parse "$_release_push_src" 2>/dev/null) || _release_push_src_sha=""

  if [ -z "$_release_push_src_sha" ]; then
    echo "⚠️  Cannot resolve the local ref actually being pushed ($_release_push_src) — skipping release-discipline checks for $_branch_label" >&2
    return 0
  fi

  # Read the 6 versioned files from the COMMIT actually being pushed
  # ($_release_push_src_sha), never the working tree — the working tree
  # reflects whatever is checked out right now, which is not necessarily
  # what `git push` will send (see the refspec-source comment above), and
  # even when it is, an uncommitted-but-staged version edit would otherwise
  # be read as if it were already part of the push.
  _read_pushed_file() {
    git show "${_release_push_src_sha}:$1" 2>/dev/null
  }

  # Version sync: Claude Code plugin, Codex plugin, package metadata, and
  # ui_clone/__init__.py must all match.
  local plugin_v market_v codex_v package_v pyproj_v init_v versions unique
  plugin_v=$(_read_pushed_file .claude-plugin/plugin.json | python3 -c "import json,sys
try:
    print(json.load(sys.stdin)['version'])
except Exception:
    print('')
" 2>/dev/null || echo "")
  market_v=$(_read_pushed_file .claude-plugin/marketplace.json | python3 -c "import json,sys
try:
    print(json.load(sys.stdin)['plugins'][0]['version'])
except Exception:
    print('')
" 2>/dev/null || echo "")
  codex_v=$(_read_pushed_file .codex-plugin/plugin.json | python3 -c "import json,sys
try:
    print(json.load(sys.stdin)['version'])
except Exception:
    print('')
" 2>/dev/null || echo "")
  package_v=$(_read_pushed_file package.json | python3 -c "import json,sys
try:
    print(json.load(sys.stdin)['version'])
except Exception:
    print('')
" 2>/dev/null || echo "")
  pyproj_v=$(_read_pushed_file pyproject.toml | python3 -c "import re,sys
m=re.search(r'^version\s*=\s*\"([^\"]+)\"', sys.stdin.read(), re.M)
print(m.group(1) if m else '')
" 2>/dev/null || echo "")
  init_v=$(_read_pushed_file ui_clone/__init__.py | python3 -c "import re,sys
m=re.search(r'__version__\s*=\s*\"([^\"]+)\"', sys.stdin.read())
print(m.group(1) if m else '')
" 2>/dev/null || echo "")
  versions="claude-plugin.json=$plugin_v marketplace.json=$market_v codex-plugin.json=$codex_v package.json=$package_v pyproject.toml=$pyproj_v ui_clone/__init__.py=$init_v"
  unique=$(printf '%s\n' "$plugin_v" "$market_v" "$codex_v" "$package_v" "$pyproj_v" "$init_v" | sort -u | grep -v '^$' | wc -l | tr -d ' ')
  if [ "$unique" != "1" ]; then
    if [ "$unique" = "0" ]; then
      # fable-20260911 round 6 follow-up review: all six reads came back
      # empty — the source commit doesn't have these files at all (e.g. a
      # push source predating them), not an actual bump mismatch. Say so
      # plainly instead of printing a confusing all-blank "Version mismatch"
      # list.
      echo "⚠️  Cannot read any of the 6 version files from $_release_push_src ($_release_push_src_sha) — skipping version-sync/bump checks for $_branch_label" >&2
      return 0
    fi
    echo "⚠️ Version mismatch on release push ($_branch_label, source=$_release_push_src): $versions" >&2
    echo "All versioned package/plugin files must be bumped together." >&2
    echo "decision: block" >&2
    exit 2
  fi

  # Version-bump enforcement: both Claude Code's and Codex's plugin caches
  # are VERSION-KEYED (~/.claude/plugins/cache/<owner>/<plugin>/<version>,
  # ~/.codex/plugins/cache/<owner>/<plugin>/<version>). post-push-refresh.sh
  # re-runs install.sh after every push, but `claude plugin update` / `codex
  # plugin add` are no-ops when the manifest version matches a version
  # already recorded as installed on this machine — the live cache silently
  # stays stale even though the marketplace source updated.
  #
  # Two independent version sources are checked, either one blocking:
  #   1. $_version_bump_base's CURRENT (pre-push) manifest version — the
  #      deterministic ground truth for "is this push actually a new
  #      release": no local machine state involved, so it can't go stale.
  #      This is the PRIMARY check (fable-20260911 follow-up review, MAJOR:
  #      the local-only check below silently stopped enforcing anything on
  #      this very machine once installed_plugins.json fell behind — e.g.
  #      post-push-refresh.sh's wipe+reinstall is a no-op whenever
  #      INSTALL_DIR is a symlink INTO this checkout, per its own header
  #      comment — and there would be no way to tell from the local machine
  #      alone that the guard had gone silent).
  #   2. This machine's installed_plugins.json — a best-effort LOCAL nudge,
  #      kept as a secondary signal for the common case where the local
  #      cache IS being kept current; skipped silently if unavailable/
  #      unparseable (never something CI machines need).
  local current_version _version_bump_has_diff
  current_version="$plugin_v"
  _version_bump_has_diff=0
  if [ -n "$_version_bump_base_sha" ] && [ "$_version_bump_base_sha" != "$_release_push_src_sha" ] \
     && [ -n "$(git diff --name-only "$_version_bump_base_sha" "$_release_push_src_sha")" ]; then
    _version_bump_has_diff=1
  fi

  _block_unbumped_version() {
    echo "⚠️ Version unchanged ($current_version) but content differs from $_version_bump_base ($_branch_label), and $1." >&2
    echo "Claude/Codex plugin caches are version-keyed — pushing without a bump leaves the live install stale." >&2
    echo "Bump the 6 version files together (see AGENTS.md 'Version sync') before pushing." >&2
    echo "Bypass (emergency only): UI_RE_SKIP_RELEASE_CHECKS=1 git push" >&2
    echo "decision: block" >&2
    exit 2
  }

  if [ "$_version_bump_has_diff" = "1" ]; then
    local origin_version deployed_version _installed_plugins_json
    origin_version=$(git show "$_version_bump_base:.claude-plugin/plugin.json" 2>/dev/null \
      | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('version', ''))
except Exception:
    pass
" 2>/dev/null)
    if [ -n "$origin_version" ] && [ "$origin_version" = "$current_version" ]; then
      _block_unbumped_version "$current_version is already the version live on $_version_bump_base"
    fi

    _installed_plugins_json="${UI_CLONE_INSTALLED_PLUGINS_JSON:-$HOME/.claude/plugins/installed_plugins.json}"
    # Path passed as argv, not interpolated into the python source text — a
    # path containing a single quote would otherwise break out of a `'...'`
    # string literal (fable-20260911 follow-up review).
    deployed_version=$(python3 -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for entry in d.get('plugins', {}).get('ui-clone-skills@voidmatcha') or []:
    v = entry.get('version')
    if v:
        print(v)
        break
" "$_installed_plugins_json" 2>/dev/null)
    if [ -n "$deployed_version" ] && [ "$deployed_version" = "$current_version" ]; then
      _block_unbumped_version "$current_version is already installed on this machine"
    fi
  fi

  # skills/ + CHANGELOG/manifest coupling (same push-target base/source as
  # the version-bump check above — reuse them instead of re-resolving).
  local base changed missing f
  base="$_version_bump_base_sha"
  if [ -z "$base" ]; then
    echo "⚠️  Cannot resolve upstream ref ($_version_bump_base) — skipping skills/ coupling check for $_branch_label" >&2
    return 0
  fi
  [ "$base" = "$_release_push_src_sha" ] && return 0

  changed=$(git diff --name-only "$base" "$_release_push_src_sha")
  echo "$changed" | grep -q '^skills/' || return 0

  missing=""
  for f in CHANGELOG.md .claude-plugin/plugin.json .claude-plugin/marketplace.json .codex-plugin/plugin.json; do
    echo "$changed" | grep -q "^$f$" || missing="$missing $f"
  done

  if [ -n "$missing" ]; then
    # stderr (not stdout) so Claude Code's PreToolUse hook harness surfaces
    # the reason. The harness shows "No stderr output" and discards stdout,
    # so a stdout-only reject looks like an opaque hook failure and burns
    # iterations debugging.
    echo "⚠️ skills/ changed on release push ($_branch_label) but missing:$missing" >&2
    echo "Bump CHANGELOG.md and the 3 plugin manifests together, or revert" >&2
    echo "the skills/ change if it was incidental." >&2
    echo "decision: block" >&2
    exit 2
  fi
  return 0
}

if [ "$target_branch" = "ALL" ]; then
  # No single well-defined push target for --all/--mirror, but it always
  # includes local main/master when they exist — check EVERY branch name
  # that has BOTH a local branch and a remote-tracking ref (fable-20260911
  # round 6 follow-up: checking only the first match let a real, unbumped
  # change on the SECOND one through unchecked), rather than guessing a
  # single one from whatever happens to be checked out.
  _all_checked=0
  for _b in main master; do
    if git rev-parse -q --verify "refs/remotes/${_release_remote}/$_b" >/dev/null 2>&1 \
       && git rev-parse -q --verify "refs/heads/$_b" >/dev/null 2>&1; then
      _all_checked=1
      _run_release_checks_for "$(_resolve_base_for "$_b" "refs/heads/$_b")" "refs/heads/$_b" "target=$_b"
    fi
  done
  if [ "$_all_checked" = "0" ]; then
    # No local main/master paired with a remote-tracking ref at all — fall
    # back to whatever is checked out, same as the pre-round-4 behavior.
    _current=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || _current=""
    if [ -n "$_current" ] && [ "$_current" != "HEAD" ]; then
      _run_release_checks_for "$(_resolve_base_for "$_current" "HEAD")" "HEAD" "target=$_current (fallback)"
    else
      _run_release_checks_for "${_release_remote}/main" "HEAD" "target=main (fallback)"
    fi
  fi
else
  _run_release_checks_for "$(_resolve_base_for "$target_branch" "$push_source")" "$push_source" "target=$target_branch"
fi
exit 0
