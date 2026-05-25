from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from ui_clone import measure

from ._helpers import (
    _project_root,
)


def test_section_compare_locks_exclude_dynamic_and_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    """`measure section-compare` MUST invoke bash with EXCLUDE_DYNAMIC=1
    and SECTION_THRESHOLD=2000, even when the parent shell sets them to
    permissive values. Locks down the d19e28d gaming pattern where the
    agent set SECTION_THRESHOLD=250000 to re-classify critical→minor.
    """
    captured_env: dict[str, str] = {}

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        captured_env.update(env)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir="/tmp/fake-ref",
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
    )
    with mock.patch.dict(os.environ, {
        "EXCLUDE_DYNAMIC": "0",         # caller's permissive default
        "SECTION_THRESHOLD": "250000",  # caller's gaming attempt
    }, clear=False), mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_section_compare(args)

    assert captured_env["EXCLUDE_DYNAMIC"] == "1", (
        f"EXCLUDE_DYNAMIC must be locked to 1, got: {captured_env.get('EXCLUDE_DYNAMIC')!r}"
    )
    assert captured_env["SECTION_THRESHOLD"] == "2000", (
        f"SECTION_THRESHOLD must be locked to 2000, got: {captured_env.get('SECTION_THRESHOLD')!r}"
    )
    # Status JSON on stdout
    out = capsys.readouterr().out.strip().splitlines()
    status = json.loads(out[-1])
    assert status["step"] == "section-compare"
    assert status["locked_env"] == {"EXCLUDE_DYNAMIC": "1", "SECTION_THRESHOLD": "2000"}



def test_all_runs_section_compare_first(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """The `all` subcommand orders section-compare BEFORE transition-compare —
    static fidelity is measured first so motion noise doesn't contaminate
    the structural verdict.
    """
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    # Make transition-spec.json exist so transition-compare is included
    (ref_dir / "transition-spec.json").write_text(json.dumps({"transitions": []}))

    call_order: list[str] = []

    def fake_run(cmd: list[str], env: dict[str, str], **kw: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        # Extract script name from the bash invocation
        for c in cmd:
            if "section-compare.sh" in c:
                call_order.append("section-compare")
            elif "transition-compare.sh" in c:
                call_order.append("transition-compare")
            elif "asset-utilization-check.sh" in c:
                call_order.append("asset-utilization")
            elif "bundle-impl-coverage-check.sh" in c:
                call_order.append("bundle-impl-coverage")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    args = mock.Mock(
        ref_dir=str(ref_dir),
        orig_url="https://ref.example",
        impl_url="http://localhost:3000",
        session="test",
        impl_src=None,
        impl_pkg=None,
    )
    with mock.patch.object(subprocess, "run", side_effect=fake_run):
        measure.cmd_all(args)

    assert call_order[0] == "section-compare", (
        f"section-compare must run FIRST in the canonical sequence: {call_order}"
    )
    assert "transition-compare" in call_order
    assert call_order.index("section-compare") < call_order.index("transition-compare")



def test_section_compare_synthesis_uses_correct_section_map_keys() -> None:
    """Regression: section-compare.sh synthesizes ref-sections from
    section-map.json when ENUMERATE_SECTIONS comes back too lean. The
    synthesis code MUST read `top`/`cls` keys (the actual schema written by
    extraction) — not just `y`/`class` (older fallback). The 3-round benchmark
    hit `gate_fail_counts[post-implement] == 632` because the synthesis only
    read `y`, collapsed every section's rect.top to 0, and produced phantom-ref
    coords that triggered uniform AE/Mpx ~950k across all sections. This is
    the data-key bug that made the prior three benchmark rounds' AE numbers
    meaningless.

    Locks the key-name reads in section-compare.sh as a guard. If a future
    refactor drops `s.get("top")` or `s.get("cls")` from the synthesis block,
    this test fires before the script is re-deployed.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    # The synthesis block must read both schemas (top/y, cls/class) so the
    # script tolerates whichever the upstream produced. Lock the canonical
    # patterns; either form fires test failure if dropped.
    assert 's.get("top") or s.get("y")' in text, (
        "section-compare.sh synthesis must read s.get('top') with fallback to "
        "s.get('y') — the 632-retry bug came from reading only 'y'"
    )
    assert 's.get("cls") or s.get("className") or s.get("class")' in text, (
        "section-compare.sh synthesis must read s.get('cls') with fallback to "
        "s.get('className') / s.get('class') — section-map.json writes 'cls', "
        "not just 'class'"
    )



def test_section_compare_descends_main_wrappers_with_section_descendants() -> None:
    """Loop-56 regression: a `<main>` with only a few color-band wrapper
    `<div>` children must still be treated as a layout wrapper when those
    children contain real section descendants. Otherwise section-compare pairs
    one giant main element and agents can add invisible sentinel children to
    game enumeration.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    assert "structuralDescendantCount" in text, (
        "section-compare.sh must count nested section/main descendants, not just "
        "direct structural children"
    )
    assert "hasWrappedStructuralDescendants" in text, (
        "section-compare.sh must descend <main> wrapper divs that contain real "
        "section/main descendants"
    )
    assert "structuralDescendantCount >= 2" in text, (
        "the wrapper descent must require multiple nested structural sections so "
        "ordinary one-section mains are not over-split"
    )


def test_section_compare_script_has_viewport_fanout_wrapper() -> None:
    """Static guard for the opt-in multi-viewport wrapper.

    VIEWPORTS must be additive; the single-viewport body remains the inner
    runner, while the wrapper calls it once per viewport and aggregates into
    the canonical sections/result.txt.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    assert "SECTION_COMPARE_INNER_CMD" in text
    assert "SECTION_COMPARE_INNER" in text
    assert "sections/viewports" in text


def test_section_compare_fans_out_per_viewport_with_stub_inner(tmp_path: Path) -> None:
    """VIEWPORTS runs section-compare once per viewport and aggregates result.txt."""
    ref = tmp_path / "ref"
    ref.mkdir()
    stub = tmp_path / "stub-section-compare.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "out=\"${4:?out}\"\n"
        "mkdir -p \"$out/sections/ref\" \"$out/sections/impl\" \"$out/sections/diff\"\n"
        "printf '%sx%s\\n' \"$VIEW_W\" \"$VIEW_H\" > \"$out/viewport.txt\"\n"
        "cat > \"$out/sections/result.txt\" <<'EOF'\n"
        "| Section | AE | AE/Mpx | Severity | Status |\n"
        "|---------|-----|--------|----------|--------|\n"
        "| Hero Section | 0 | 0 | ok | ✅ |\n"
        "\n"
        "**Result: 1 PASS, 0 FAIL, 0 SKIP, 0 STRUCTURAL_ONLY**\n"
        "EOF\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    env = {
        **os.environ,
        "VIEWPORTS": "375x812,1280x800",
        "SECTION_COMPARE_INNER_CMD": str(stub),
    }
    proc = subprocess.run(
        ["bash", str(script), "https://ref.example", "https://impl.example", "mv-session", str(ref)],
        capture_output=True, text=True, timeout=20, env=env,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = (ref / "sections" / "result.txt").read_text(encoding="utf-8")
    assert "viewports: 375x812,1280x800" in result
    assert "viewport: 375x812" in result
    assert "viewport: 1280x800" in result
    assert "| [375x812] Hero Section |" in result
    assert "| [1280x800] Hero Section |" in result
    assert (ref / "sections" / "viewports" / "375x812" / "viewport.txt").read_text().strip() == "375x812"
    assert (ref / "sections" / "viewports" / "1280x800" / "viewport.txt").read_text().strip() == "1280x800"


def test_section_compare_failure_guidance_avoids_sigpipe_prone_head_pipelines() -> None:
    """Regression: the failure-report path runs with `set -o pipefail`.

    Piping long markdown excerpts through `head` makes the upstream `awk`
    receive SIGPIPE once `head` has enough lines, so section-compare exits 141
    instead of its documented visual-failure status.
    """
    script = _project_root() / "skills" / "visual-debug" / "scripts" / "section-compare.sh"
    text = script.read_text(encoding="utf-8")

    failure_block = text.split('if [ "$FAIL_COUNT" -gt 0 ]; then', 1)[1].split(
        "exit 1", 1,
    )[0]

    assert "| head -" not in failure_block
