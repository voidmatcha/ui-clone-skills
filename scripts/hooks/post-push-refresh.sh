#!/bin/bash
# Agent post-push refresh hook: after a successful `git push`, wipe the local
# install at INSTALL_DIR and re-run the canonical curl-pipe install. This
# dogfoods install.sh end-to-end against the just-pushed state — if the
# installer breaks, the maintainer learns immediately rather than after a real
# user files an issue.
#
# Behavior:
#   - Triggers only when the tool input was a successful `git push`.
#   - INSTALL_DIR defaults to ~/.local/share/ui-clone-skills (matches install.sh).
#     The maintainer's working repo is NEVER touched — INSTALL_DIR is the install
#     location, not the dev clone.
#   - --no-deps is passed so brew/uv don't re-resolve every push (system deps
#     don't change between commits in any meaningful way).
#   - Default install registers BOTH Claude and Codex marketplaces; each is a
#     no-op when that host's CLI is absent.
#   - Then runs review.sh to catch lint/doc regressions.
#
# Override:
#   UI_CLONE_SKIP_POST_PUSH_REFRESH=1  — skip the wipe+reinstall entirely
#   INSTALL_DIR=<path>                  — install elsewhere (e.g. for testing)

input=$(cat)
command=$(echo "$input" | grep -o '"command":"[^"]*"' | head -1 | sed 's/"command":"//;s/"$//')

if ! echo "$command" | grep -qE 'git\s+push'; then
  exit 0
fi

if ! echo "$input" | grep -q '"exit_code":0'; then
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

if [ "${UI_CLONE_SKIP_POST_PUSH_REFRESH:-0}" != "1" ]; then
  INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/share/ui-clone-skills}"
  # Defensive: never wipe the maintainer's working repo even if INSTALL_DIR was
  # mis-set to it. Compare resolved paths to be safe against symlinks.
  RESOLVED_INSTALL=$(cd "$INSTALL_DIR" 2>/dev/null && pwd -P || echo "$INSTALL_DIR")
  RESOLVED_REPO=$(cd "$REPO_ROOT" 2>/dev/null && pwd -P || echo "$REPO_ROOT")
  if [ "$RESOLVED_INSTALL" = "$RESOLVED_REPO" ]; then
    echo "⚠️ post-push-refresh: INSTALL_DIR=$INSTALL_DIR is the working repo — skipping wipe" >&2
  elif [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
  fi

  # Wait briefly for the remote to settle so the curl fetch sees the pushed sha.
  # GitHub raw cache TTL is short but non-zero; 2s avoids occasional stale reads.
  sleep 2

  curl -LsSf https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh \
    | INSTALL_DIR="$INSTALL_DIR" bash -s -- --no-deps 2>&1 \
    | sed 's/^/[post-push-refresh] /' || \
    echo "⚠️ post-push-refresh: curl install failed — check network / GitHub" >&2
fi

# Run automated review regardless of refresh outcome — catches lint/doc regressions
# in the just-pushed working tree.
bash "$REPO_ROOT/scripts/ci/review.sh" --quiet 2>/dev/null || \
  echo "⚠️ review.sh found issues — run 'bash scripts/ci/review.sh' for details" >&2

exit 0
