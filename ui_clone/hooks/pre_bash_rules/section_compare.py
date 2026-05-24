"""Section-compare precondition gate.

section-compare is expensive and easy to misread as the "real" verdict.
If verification-plan declares dom-mirror-check or proxy-mirror-check, a
missing/failing artifact means the implementation already diverged
structurally or is a mirror; running pixel crops first lets agents chase
section ids while ignoring the root failure.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_SECTION_COMPARE_COMMAND_PATTERNS = re.compile(
    r"skills/visual-debug/scripts/section-compare\.sh\b"
    r"|python(?:3)?\s+-m\s+ui_clone\.measure\s+section-compare\b",
    re.IGNORECASE,
)


def _section_compare_precondition_reason(ref_dir: Path, cmd: str) -> str | None:
    """Block section-compare while earlier block-severity static gates are missing."""
    if not _SECTION_COMPARE_COMMAND_PATTERNS.search(cmd):
        return None

    plan_path = ref_dir / "verification-plan.json"
    if not plan_path.is_file():
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    rows = plan.get("requiredChecks") if isinstance(plan, dict) else None
    if not isinstance(rows, list):
        return None
    preconditions = {
        "dom-mirror-check": (
            "dom-mirror-check.json",
            f"bash $SCRIPTS_DIR/dom-mirror-check.sh {ref_dir} <impl-dir>",
            "DOM mirror",
        ),
        "proxy-mirror-check": (
            "proxy-mirror-check.json",
            f"bash $SCRIPTS_DIR/proxy-mirror-check.sh {ref_dir} <impl-dir>",
            "proxy/static mirror",
        ),
    }

    for check_id, (default_artifact, command, label) in preconditions.items():
        row = next(
            (
                row for row in rows
                if isinstance(row, dict)
                and row.get("id") == check_id
                and row.get("severity") == "block"
            ),
            None,
        )
        if not isinstance(row, dict):
            continue

        artifact_name = str(row.get("produces") or default_artifact)
        artifact = ref_dir / artifact_name
        status = "missing"
        if artifact.is_file():
            try:
                payload = json.loads(artifact.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    status = str(payload.get("status") or "unknown")
            except (json.JSONDecodeError, OSError):
                status = "malformed"

        if status == "pass":
            continue

        return (
            f"⛔ UI-RE: section-compare blocked because {check_id} is {status}. "
            f"Run/fix the block-severity {label} gate first:\n"
            f"  {command}\n"
            f"Then re-run section-compare after {artifact_name} reports status=pass."
        )

    return None
