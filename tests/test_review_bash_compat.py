"""Regression coverage for direct review.sh execution on modern Bash."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "scripts" / "ci" / "review.sh"


def test_direct_review_completes_without_bash_compat() -> None:
    bash = shutil.which("bash")
    assert bash is not None

    env = os.environ.copy()
    env.pop("BASH_COMPAT", None)
    env["UI_CLONE_REVIEW_SKIP_TESTS"] = "1"
    env["UI_CLONE_REVIEW_SKIP_SECURITY"] = "1"

    result = subprocess.run(
        [bash, str(REVIEW), "--quiet"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"Review: \d+ passed, 0 warnings, 0 errors", result.stdout)


def test_review_avoids_inline_heredoc_writes() -> None:
    source = REVIEW.read_text(encoding="utf-8")

    assert "python3 - <<" not in source
    assert '<<< "$SH_FILES"' not in source
    assert 'done < <(printf "%s\\n" "$SH_FILES")' in source
