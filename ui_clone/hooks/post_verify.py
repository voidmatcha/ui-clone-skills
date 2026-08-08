"""
Post-verification check hook for ui-reverse-engineering.
Advisory only — prints warnings to stdout but always exits 0.

Usage: python -m ui_clone.hooks.post_verify
Reads PostToolUse JSON from stdin.
Always exits 0 (advisory, not blocking).
"""

from __future__ import annotations

import json
import re
import sys

from ui_clone.hooks._common import extract_tool_command as _extract_tool_command
from ui_clone.hooks._common import find_project_root as _find_project_root
from ui_clone.hooks._common import find_ref_dir as _find_ref_dir
from ui_clone.hooks._common import mark_ref_session as _mark_ref_session
from ui_clone.hooks._common import session_id_from_payload as _session_id_from_payload
from ui_clone.hooks._common import (
    target_ref_dir_for_ui_re_command as _target_ref_dir_for_ui_re_command,
)

# Word-boundary patterns for English terms to avoid false positives like
# "let's commit to this" or "commitment" triggering the hook.
_ALT_STATE_EXTS = {".png", ".webm", ".jpg", ".jpeg"}

_WORD_BOUNDARY_PATTERNS = re.compile(
    r"\b(?:commit|done|complete|finish|merge|push|deploy)\b"
    r"|looks\s+good"
    r"|all\s+pass",
    re.IGNORECASE,
)


def _is_completion_command(cmd: str) -> bool:
    return bool(_WORD_BOUNDARY_PATTERNS.search(cmd))


def main() -> None:
    raw_input = sys.stdin.read() if not sys.stdin.isatty() else ""

    project_root = _find_project_root()
    search_root = project_root / "tmp" / "ref"
    ref_dir = _find_ref_dir(search_root)

    if ref_dir is None:
        sys.exit(0)

    # Only run if inside an active ui-re session (WIP marker present)
    if not (ref_dir / ".ui-re-active").is_file():
        sys.exit(0)

    # Parse tool input to extract bash command
    bash_cmd = ""
    session_id = ""
    if raw_input.strip():
        try:
            data = json.loads(raw_input)
            if isinstance(data, dict):
                bash_cmd = _extract_tool_command(data)
                session_id = _session_id_from_payload(data)
        except json.JSONDecodeError:
            pass

    target_ref_dir = _target_ref_dir_for_ui_re_command(bash_cmd, project_root)
    if target_ref_dir is not None:
        _mark_ref_session(target_ref_dir, session_id, source="post_verify")

    if not _is_completion_command(bash_cmd):
        sys.exit(0)

    # ── Check 1: Verification has been run ──
    diff_dir = ref_dir / "static" / "diff"
    diff_count = sum(1 for f in diff_dir.rglob("*.png") if f.is_file()) if diff_dir.is_dir() else 0
    health_file = ref_dir / "layout-health.json"

    if diff_count < 3 and not health_file.is_file():
        print()
        print("⚠️  UI-RE: Verification has NOT been run.")
        print("    Before declaring done, run:")
        print(f"    bash scripts/verify/auto-verify.sh <session> <orig-url> <impl-url> {ref_dir}")
        print()
        sys.exit(0)

    # ── Check 2: Verification results passing ──
    compare_log = ref_dir / "batch-compare-result.txt"
    if compare_log.is_file():
        lines = compare_log.read_text(encoding="utf-8", errors="replace").splitlines()
        fail_count = sum(1 for ln in lines if "❌" in ln)
        pass_count = sum(1 for ln in lines if "✅" in ln)

        if fail_count > 0:
            print()
            print(
                f"⚠️  UI-RE: Verification ran but {fail_count} positions FAILED (only {pass_count} passed)."
            )
            print(f"    Read diff images in {ref_dir}/static/diff/ to diagnose.")
            print("    DO NOT declare done with failing positions.")
            print()

    # ── Check 3: Multi-state verification ──
    interactions_file = ref_dir / "interactions-detected.json"
    if interactions_file.is_file():
        try:
            interactions_data = json.loads(interactions_file.read_text(encoding="utf-8"))
            state_changing = sum(
                1 for i in interactions_data.get("interactions", []) if i.get("trigger") == "click"
            )
            if state_changing > 0:
                # Check capture directories for alternate-state screenshots
                capture_dirs = [ref_dir / "static", ref_dir / "transitions", ref_dir / "sections"]
                alt_captures = sum(
                    1
                    for d in capture_dirs
                    if d.is_dir()
                    for f in d.rglob("*")
                    if f.is_file()
                    and f.suffix.lower() in _ALT_STATE_EXTS
                    and any(kw in f.name for kw in ("search", "active", "result", "click"))
                )
                if alt_captures == 0:
                    print()
                    print(
                        f"⚠️  UI-RE: {state_changing} click interactions exist but no alternate-state captures found."
                    )
                    print("    Did you verify the search/results/active view as well?")
                    print()
        except (json.JSONDecodeError, OSError):
            pass

    # Always advisory — exit 0
    sys.exit(0)


if __name__ == "__main__":
    main()
