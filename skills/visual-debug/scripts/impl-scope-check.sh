#!/usr/bin/env bash
# impl-scope-check.sh — block iteration-time modifications of plugin
# tooling.
#
# Usage:
#   impl-scope-check.sh <ref-dir> <impl-root>
#
#
# This gate runs in two modes:
#   1. If <ref-dir>/iteration-baseline-sha.txt exists, diff HEAD
#      against the baseline and fail if any file outside the allowed
#      iteration scope changed.
#   2. If no baseline file exists, write the current HEAD SHA as the
#      baseline and pass with status=initialized. The next invocation
#      will diff against it.
#
# Allowed iteration scope (default):
#   - <impl-root>/**            (resolved by find-impl-root.sh)
#   - tmp/ref/<component>/**    (artifact writes)
#   - tmp/<component>-*.log
#
# Explicit exception list (override via env IMPL_SCOPE_ALLOWED):
#   - macOS-specific timestamp hotfixes (one-line edits to single shell
#     helpers; agent must add `# scope-allow: macos-timestamp` comment
#     justifying the edit)
#
# Writes:
#   <ref-dir>/impl-scope.json
#   <ref-dir>/iteration-baseline-sha.txt (first invocation)
#
# Exit 0 on pass/initialized, 1 on tooling-scope violation, 2 on
# setup error.

set -uo pipefail

REF_DIR="${1:?Usage: impl-scope-check.sh <ref-dir> <impl-root>}"
IMPL_ROOT="${2:?impl-root required}"

[ -d "$REF_DIR" ]   || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }
[ -d "$IMPL_ROOT" ] || { echo "impl-root not found: $IMPL_ROOT" >&2; exit 2; }

OUT="$REF_DIR/impl-scope.json"
BASELINE_FILE="$REF_DIR/iteration-baseline-sha.txt"

# Repo root (canonical, walk up from impl-root)
REPO_ROOT=$(cd "$IMPL_ROOT" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$REPO_ROOT" ]; then
  echo "impl-scope: cannot find git repo root from impl-root" >&2
  exit 2
fi

cd "$REPO_ROOT"

# Initialize baseline on first call.
if [ ! -f "$BASELINE_FILE" ]; then
  CURRENT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")
  if [ -z "$CURRENT_SHA" ]; then
    echo "impl-scope: cannot resolve git HEAD" >&2
    exit 2
  fi
  printf '%s\n' "$CURRENT_SHA" > "$BASELINE_FILE"
  python3 - "$OUT" "$CURRENT_SHA" <<'PY'
import json, sys
from pathlib import Path
out_path, sha = sys.argv[1:3]
payload = {
    "schemaVersion": 1,
    "status": "initialized",
    "baselineSha": sha,
    "violations": [],
    "reasons": [
        f"baseline SHA written to iteration-baseline-sha.txt ({sha}). "
        "Subsequent invocations will diff HEAD against this SHA."
    ],
    "rule": (
        "Impl iteration must only modify files under the impl root. "
        "Plugin tooling (skills/, scripts/verify/, ui_clone/, tests/, hooks/) "
        "MUST NOT be edited during a clone iteration — that's the cheat "
        "pattern of editing the gates themselves to make them pass."
    ),
}
Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": "initialized", "baselineSha": sha, "out": out_path}))
PY
  exit 0
fi

BASELINE=$(head -n 1 "$BASELINE_FILE")
if [ -z "$BASELINE" ]; then
  echo "impl-scope: baseline file empty" >&2
  exit 2
fi

# Resolve impl-root relative path for the allowlist
IMPL_REL=$(python3 -c "
import os, sys
print(os.path.relpath(sys.argv[1], sys.argv[2]))
" "$IMPL_ROOT" "$REPO_ROOT" 2>/dev/null)

# Diff committed changes between baseline and HEAD plus uncommitted
# working-tree changes. Both must be inside scope.
COMMITTED=$(git diff --name-only "$BASELINE" HEAD 2>/dev/null | sort -u)
UNCOMMITTED=$(git diff --name-only HEAD 2>/dev/null | sort -u)
UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null | sort -u)
ALL_CHANGES=$(printf '%s\n%s\n%s\n' "$COMMITTED" "$UNCOMMITTED" "$UNTRACKED" | grep -v '^$' | sort -u)

python3 - "$OUT" "$BASELINE" "$IMPL_REL" <<PY
import json
import os
import sys
from pathlib import Path

out_path, baseline, impl_rel = sys.argv[1:4]
all_changes = """$ALL_CHANGES""".strip().splitlines()
all_changes = [p for p in all_changes if p]

allowed_prefixes = (impl_rel + "/", "tmp/")

# Files that are commonly touched as legit-but-not-impl. The allowlist
# is conservative: each entry needs justification when seen.
allowed_literal: set = {
    ".gitignore",       # likely impl-side adjustments propagated up
    ".gitattributes",
}

violations: list = []
allowed: list = []

for path in all_changes:
    if path in allowed_literal:
        allowed.append({"path": path, "reason": "literal-allowlist"})
        continue
    if path.startswith(allowed_prefixes):
        allowed.append({"path": path, "reason": "scope-prefix"})
        continue
    # Reject everything else — including skills/, scripts/, ui_clone/,
    # tests/, hooks/, pyproject.toml, etc.
    violations.append({"path": path, "reason": "outside-iteration-scope"})

status = "fail" if violations else "pass"
reasons: list = []
if violations:
    samples = violations[:10]
    reasons.append(
        f"{len(violations)} file(s) modified outside iteration scope. "
        "Iteration should only touch the impl tree under "
        f"{impl_rel}/. Examples: "
        + ", ".join(v["path"] for v in samples)
    )
    reasons.append(
        "If the change is a legitimate plugin fix (not a gate-cheat), "
        "land it in a separate commit BEFORE the iteration starts. The "
        "iteration baseline can then be reset by deleting "
        "iteration-baseline-sha.txt."
    )

payload = {
    "schemaVersion": 1,
    "status": status,
    "baselineSha": baseline,
    "implRel": impl_rel,
    "filesChecked": len(all_changes),
    "violations": violations[:50],
    "allowed": allowed[:50],
    "reasons": reasons,
    "nextAction": (
        "Revert all changes outside the impl tree. Use `git checkout " + baseline + " -- "
        "<violating-path>` per file, OR if the change is a legitimate plugin "
        "fix, commit it BEFORE starting the iteration and delete "
        "iteration-baseline-sha.txt to reset the baseline."
        if violations else "iteration scope clean"
    ),
    "rule": (
        "Impl iteration must only modify files under the impl root and "
        "tmp/** files. Plugin tooling (skills/, scripts/verify/, "
        "ui_clone/, tests/, hooks/) MUST NOT be edited during a clone "
        "iteration — editing the gates themselves to make them pass is "
        "the cheat this gate blocks."
    ),
}
Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": status, "violations": len(violations), "out": out_path}))
sys.exit(0 if status == "pass" else 1)
PY
