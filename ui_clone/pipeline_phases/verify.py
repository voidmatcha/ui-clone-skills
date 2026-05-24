"""execute_verify — drive post-generation gates.

The gates themselves live in ui_clone.gate; we shell out so the gate
module's argparse + exit codes stay the source of truth.
"""

from __future__ import annotations

import datetime
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ui_clone.hooks._common import BOLD as _BOLD
from ui_clone.hooks._common import GREEN as _GREEN
from ui_clone.hooks._common import NC as _NC
from ui_clone.hooks._common import RED as _RED

if TYPE_CHECKING:
    from ui_clone.pipeline import Pipeline


def execute_verify(pipeline: Pipeline) -> int:
    gates_post_impl = (
        "spec",
        "post-implement",
        "boundary",
        "font-parity",
        "section-compare",
    )
    impl_dir = Path.cwd() / "impl"
    if not impl_dir.is_dir():
        print(
            f"{_RED}verify: impl/ not found at {impl_dir}. "
            f"Generate components first, then re-run verify.{_NC}"
        )
        return 1

    failures: list[str] = []
    for gate_name in gates_post_impl:
        print(f"\n{_BOLD}== verify: gate {gate_name}{_NC}")
        result = subprocess.run(
            [sys.executable, "-m", "ui_clone.gate", str(pipeline.ref_dir), gate_name],
            capture_output=False,  # stream gate output to operator
        )
        if result.returncode != 0:
            failures.append(gate_name)
            print(
                f"  {_RED}✗{_NC} {gate_name} exit {result.returncode} "
                f"— continuing to surface every failure rather than short-circuit"
            )
    if failures:
        print(
            f"\n{_RED}{_BOLD}verify: {len(failures)} gate(s) failed: "
            f"{', '.join(failures)}{_NC}"
        )
        return 1
    # Stop-hook stamp. On success, write a current-run-fresh marker that
    # the Stop hook checks before allowing the agent to claim completion.
    # Without this stamp, the Stop hook will block — closing the bypass
    # where an agent runs individual verification scripts directly and
    # never reaches the canonical entry.
    stamp = {
        "verifiedAt": datetime.datetime.now(datetime.UTC)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gatesPassed": list(gates_post_impl),
        "stampedBy": "pipeline.execute_verify",
        "implDir": str(impl_dir),
        "refDir": str(pipeline.ref_dir),
    }
    stamp_path = pipeline.ref_dir / "verify-stamp.json"
    stamp_path.write_text(json.dumps(stamp, indent=2) + "\n")
    print(f"\n{_GREEN}{_BOLD}verify: all post-impl gates passed{_NC}")
    print(f"  stamp: {stamp_path}")
    return 0
