"""
PreToolUse Bash hook — blocks declaration-of-done commands when verification incomplete.

Why this hook exists
────────────────────
The Stop hook (section_gate) catches the case where Claude finishes a turn while
`current_gate != "done"`. But agents frequently declare done by *running a bash
command* — `git commit`, `git push`, `gh pr create`, `gh pr merge`. Those commands
fire *before* the next Stop event. The PostToolUse advisory (post_verify) prints
warnings but doesn't block — and per the v0.4.5 JSONL analysis, advisories alone
don't change behavior.

This hook fires on PreToolUse Bash. When:
  - a WIP marker `tmp/ref/<c>/.ui-re-active` exists, AND
  - the bash command matches a declaration-of-done pattern, AND
  - section-compare hasn't passed (or pipeline-state isn't "done")

…it denies the tool with a permission decision pointing the agent at the gate.

Bypass:
  - UI_RE_SKIP_BASH_GATE=1 in env disables the hook (escape hatch for emergencies)

Rule modules:
  Each rule family lives in `ui_clone.hooks.pre_bash_rules.<family>`. The
  orchestration (stdin parse + guard cascade) lives in `.dispatcher`. This
  file is the importable entry point and re-exports the legacy private names
  so any out-of-tree code that imported `ui_clone.hooks.pre_bash._foo` keeps
  working without modification.

Usage:
    python -m ui_clone.hooks.pre_bash

Input:  PreToolUse JSON on stdin with tool_input.command
Output: deny payload to stdout when blocking, exit 0 (silent) otherwise
"""

from __future__ import annotations

from ui_clone.hooks.pre_bash_rules.bash_write import (
    _BASH_WRITE_PATTERNS,
    _bash_adhoc_ref_target,
    _bash_scratch_nested_ref_target,
    _bash_write_target,
)
from ui_clone.hooks.pre_bash_rules.declaration import (
    _BLOCK_PATTERNS,
    _is_declaration_command,
)
from ui_clone.hooks.pre_bash_rules.dispatcher import _emit_block, main
from ui_clone.hooks.pre_bash_rules.impl_scaffold import (
    _IMPL_SCAFFOLD_PATTERNS,
    _impl_scaffold_violation,
)
from ui_clone.hooks.pre_bash_rules.ref_state import (
    _FRESH_FOLDER_ALLOW_PATTERNS,
    _FRESH_FOLDER_DENY_PATHS,
    _FRESH_FOLDER_DENY_TOOLS,
    _candidate_ref_roots,
    _command_path_tokens,
    _find_active_ref,
    _find_ref_dir_with_pipeline_state,
    _fresh_state_violation,
    _has_no_populated_component,
    _is_fresh_state,
    _ref_dir_for_static_guard,
    _state_before_gate,
)
from ui_clone.hooks.pre_bash_rules.repo_identity import (
    _canonical_repo_root,
    _is_scratch_nested,
)
from ui_clone.hooks.pre_bash_rules.section_compare import (
    _SECTION_COMPARE_COMMAND_PATTERNS,
    _section_compare_precondition_reason,
)
from ui_clone.hooks.pre_bash_rules.static_mirror import (
    _HTML_WRITE_PATTERNS,
    _STATIC_HTML_MIRROR_SOURCE_PATTERNS,
    _STATIC_MIRROR_DOWNLOAD_PATTERNS,
    _STATIC_SERVER_PATTERNS,
    _WHOLE_DOCUMENT_HTML_PATTERNS,
    _bash_html_write_targets,
    _is_impl_index_html_path,
    _static_html_mirror_write_target,
    _static_mirror_download_violation,
    _static_server_violation,
    _whole_document_html_snapshot_violation,
)

__all__ = [
    "_BASH_WRITE_PATTERNS",
    "_BLOCK_PATTERNS",
    "_FRESH_FOLDER_ALLOW_PATTERNS",
    "_FRESH_FOLDER_DENY_PATHS",
    "_FRESH_FOLDER_DENY_TOOLS",
    "_HTML_WRITE_PATTERNS",
    "_IMPL_SCAFFOLD_PATTERNS",
    "_SECTION_COMPARE_COMMAND_PATTERNS",
    "_STATIC_HTML_MIRROR_SOURCE_PATTERNS",
    "_STATIC_MIRROR_DOWNLOAD_PATTERNS",
    "_STATIC_SERVER_PATTERNS",
    "_WHOLE_DOCUMENT_HTML_PATTERNS",
    "_bash_adhoc_ref_target",
    "_bash_html_write_targets",
    "_bash_scratch_nested_ref_target",
    "_bash_write_target",
    "_candidate_ref_roots",
    "_canonical_repo_root",
    "_command_path_tokens",
    "_emit_block",
    "_find_active_ref",
    "_find_ref_dir_with_pipeline_state",
    "_fresh_state_violation",
    "_has_no_populated_component",
    "_impl_scaffold_violation",
    "_is_declaration_command",
    "_is_fresh_state",
    "_is_impl_index_html_path",
    "_is_scratch_nested",
    "_ref_dir_for_static_guard",
    "_section_compare_precondition_reason",
    "_state_before_gate",
    "_static_html_mirror_write_target",
    "_static_mirror_download_violation",
    "_static_server_violation",
    "_whole_document_html_snapshot_violation",
    "main",
]


if __name__ == "__main__":
    main()
