"""Regression tests for skills/visual-debug/scripts/reveal-trigger-check.sh.

F3: phase-2 used to be one monolithic async eval that settled 1200ms PER
hidden-init element. On a page with many reveals the eval exceeded
agent-browser's eval budget and returned EMPTY output (stderr is swallowed with
2>/dev/null). The gate then treated empty output identically to an all-clear
"[]" and wrote status:pass / exit 0 — certifying genuinely-stuck IO reveals as
working.

A COMPLETED phase-2 over zero probes returns "[]" (not empty), so an empty RAW is
the unambiguous signature of an eval that never finished. When phase-1 pinned
N>0 candidates, the gate must fail closed instead of claiming a clean pass.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


SCRIPT = _project_root() / "skills" / "visual-debug" / "scripts" / "reveal-trigger-check.sh"
ARTIFACT_HELPER = SCRIPT.parent / "lib" / "reveal_trigger_artifact.py"
REPORT_HELPER = SCRIPT.parent / "lib" / "reveal-trigger-report.js"


def test_shell_wrapper_has_no_heredocs_or_inline_node_program() -> None:
    """Keep helper programs out of Bash 5.1+ pipe-backed heredocs."""
    shell = SCRIPT.read_text(encoding="utf-8")

    assert "<<" not in shell
    assert "python3 -" not in shell
    assert "node -e" not in shell
    assert ARTIFACT_HELPER.is_file()
    assert REPORT_HELPER.is_file()
    assert 'python3 "$SCRIPT_DIR/lib/reveal_trigger_artifact.py"' in shell
    assert 'node "$SCRIPT_DIR/lib/reveal-trigger-report.js" "$DATA"' in shell


def test_legacy_candidates_require_a_relevant_transition_channel() -> None:
    """Static transforms and already-running keyframes are not IO reveals.

    Marker-backed generated reveals take the dedicated path; the legacy style
    heuristic must only pin an opacity/transform pre-state when that same CSS
    channel is transitioned and no keyframe animation is already attached.
    """
    shell = SCRIPT.read_text(encoding="utf-8")

    assert "transitionProperty" in shell
    assert 'transitionProps.includes("opacity")' in shell
    assert 'transitionProps.includes("transform")' in shell
    assert 'if (animName !== "none") continue;' in shell


def test_selector_helper_extracts_only_intersection_or_reveal_entries(tmp_path: Path) -> None:
    spec = {
        "transitions": [
            {
                "trigger": "hover",
                "selector": ".hover-icon",
                "animation": {"type": "css-hover"},
            },
            {
                "trigger": "scroll",
                "selector": ".hero-copy",
                "animation": {"type": "gsap-scroll-scrub-parallax"},
            },
            {
                "trigger": "intersection",
                "selector": ".effect-data:not(.active) .effect-value",
                "animation": {"type": "intersectionobserver-class-toggle"},
            },
            {
                "trigger": "scroll",
                "target": ".plain-reveal",
                "animation": {"type": "stagger-reveal"},
            },
        ]
    }
    spec_path = tmp_path / "transition-spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    proc = subprocess.run(
        ["python3", str(ARTIFACT_HELPER), "selectors", str(spec_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [
        ".effect-data:not(.active) .effect-value",
        ".plain-reveal",
    ]


def _fake_agent_browser(phase2_output: str) -> str:
    """Fake agent-browser: phase-1 (setAttribute data-reveal-probe) reports 3
    candidates; phase-2 (scrollIntoView async sweep) returns the given output."""
    return f"""#!/usr/bin/env bash
set -uo pipefail
if [ "${{1:-}}" = "--session" ]; then shift 2; fi
cmd="${{1:-}}"; shift || true
case "$cmd" in
  set|navigate|close) exit 0 ;;
  eval)
    js="${{1:-}}"
    if [[ "$js" == *scrollIntoView* ]]; then
      printf '%s' {phase2_output!r}
      exit 0
    fi
    # phase-1 candidate enumeration -> 3 hidden-init candidates pinned
    printf '3'
    exit 0
    ;;
esac
exit 0
"""


def _fake_agent_browser_spec_scoped_candidates() -> str:
    """Fake agent-browser for REF_DIR transition-spec scoped legacy probes.

    The fake cannot execute DOM CSS matching, so it checks that phase 1 contains
    the selector-scope branch and then reports one candidate instead of the
    three hover/closed/scrub false positives plus one real reveal candidate that
    an unscoped heuristic would have reported.
    """
    return """#!/usr/bin/env bash
set -uo pipefail
if [ "${1:-}" = "--session" ]; then shift 2; fi
cmd="${1:-}"; shift || true
case "$cmd" in
  set|navigate|close) exit 0 ;;
  eval)
    js="${1:-}"
    if [[ "$js" == *scrollIntoView* ]]; then
      start=$(grep -o 'const START = [0-9]*;' <<< "$js")
      end=$(grep -o 'const END = [0-9]*;' <<< "$js")
      printf '%s | %s\n' "$start" "$end" >> "${FAKE_AGENT_LOG:?}"
      printf '[]'
      exit 0
    fi
    if [[ "$js" == *removeAttribute* ]]; then
      exit 0
    fi
    if [[ "$js" == *hasScopedLegacy* && "$js" == *selectorMatchesSpec* && "$js" == *'!selectorMatchesSpec(el)'* ]]; then
      printf '1'
    else
      printf '4'
    fi
    exit 0
    ;;
esac
exit 0
"""


def _fake_agent_browser_many_candidates() -> str:
    """Fake agent-browser: 25 candidates, every phase-2 batch completes cleanly."""
    return """#!/usr/bin/env bash
set -uo pipefail
if [ "${1:-}" = "--session" ]; then shift 2; fi
cmd="${1:-}"; shift || true
case "$cmd" in
  set|navigate|close) exit 0 ;;
  eval)
    js="${1:-}"
    if [[ "$js" == *scrollIntoView* ]]; then
      start=$(grep -o 'const START = [0-9]*;' <<< "$js")
      end=$(grep -o 'const END = [0-9]*;' <<< "$js")
      printf '%s | %s\n' "$start" "$end" >> "${FAKE_AGENT_LOG:?}"
      printf '[]'
      exit 0
    fi
    if [[ "$js" == *removeAttribute* ]]; then
      exit 0
    fi
    printf '25'
    exit 0
    ;;
esac
exit 0
"""


def _run(tmp_path: Path, phase2_output: str) -> tuple[subprocess.CompletedProcess, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "agent-browser").write_text(_fake_agent_browser(phase2_output), encoding="utf-8")
    (bin_dir / "sleep").chmod(0o755)
    (bin_dir / "agent-browser").chmod(0o755)

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["REF_DIR"] = str(ref_dir)
    env["WAIT_MS"] = "0"
    env.pop("BASH_COMPAT", None)
    bash = shutil.which("bash")
    assert bash is not None

    proc = subprocess.run(
        [bash, str(SCRIPT), "reveal-test", "https://example.test/"],
        check=False, capture_output=True, env=env, text=True, timeout=120,
    )
    return proc, ref_dir / "reveal-trigger.json"


def _run_spec_scoped_candidates(
    tmp_path: Path,
) -> tuple[subprocess.CompletedProcess, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "agent-browser").write_text(
        _fake_agent_browser_spec_scoped_candidates(), encoding="utf-8"
    )
    (bin_dir / "sleep").chmod(0o755)
    (bin_dir / "agent-browser").chmod(0o755)

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "trigger": "hover",
                        "selector": ".search-panel [aria-hidden='true']",
                        "animation": {"type": "css-hover"},
                    },
                    {
                        "trigger": "scroll",
                        "selector": ".hero-copy p",
                        "animation": {"type": "gsap-scroll-scrub-parallax"},
                    },
                    {
                        "trigger": "intersection",
                        "selector": ".effect-data:not(.active) .effect-value",
                        "animation": {"type": "intersectionobserver-class-toggle"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    log_path = tmp_path / "phase2.log"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["REF_DIR"] = str(ref_dir)
    env["WAIT_MS"] = "0"
    env["SETTLE_MS"] = "0"
    env["FAKE_AGENT_LOG"] = str(log_path)
    env.pop("BASH_COMPAT", None)
    bash = shutil.which("bash")
    assert bash is not None

    proc = subprocess.run(
        [bash, str(SCRIPT), "reveal-spec-scope-test", "https://example.test/"],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=120,
    )
    return proc, ref_dir / "reveal-trigger.json", log_path


def _run_many_candidates(tmp_path: Path) -> tuple[subprocess.CompletedProcess, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "agent-browser").write_text(
        _fake_agent_browser_many_candidates(), encoding="utf-8"
    )
    (bin_dir / "sleep").chmod(0o755)
    (bin_dir / "agent-browser").chmod(0o755)

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    log_path = tmp_path / "phase2.log"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["REF_DIR"] = str(ref_dir)
    env["WAIT_MS"] = "0"
    env["SETTLE_MS"] = "0"
    env["FAKE_AGENT_LOG"] = str(log_path)
    env.pop("BASH_COMPAT", None)
    bash = shutil.which("bash")
    assert bash is not None

    proc = subprocess.run(
        [bash, str(SCRIPT), "reveal-batch-test", "https://example.test/"],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=120,
    )
    return proc, ref_dir / "reveal-trigger.json", log_path


def _fake_agent_browser_marker_reveal() -> str:
    """Fake agent-browser for generated boolean state reveals.

    Phase 1 returns one candidate only when the probe enumerator knows about the
    generator marker. Phase 2 passes only when the sweep validates that the
    marked data attr reached its requested terminal value after scroll.
    """
    stuck = json.dumps(
        [
            {
                "idx": "0",
                "tag": "DIV",
                "cls": "card",
                "box": "160x120",
                "init": {"opacity": "1", "transform": "none"},
                "post": {"opacity": "1", "transform": "none"},
                "chain": [],
                "stateReveal": [
                    {"name": "data-in-view", "expected": "true", "actual": "false"}
                ],
            }
        ]
    )
    return f"""#!/usr/bin/env bash
set -uo pipefail
if [ "${{1:-}}" = "--session" ]; then shift 2; fi
cmd="${{1:-}}"; shift || true
case "$cmd" in
  set|navigate|close) exit 0 ;;
  eval)
    js="${{1:-}}"
    if [[ "$js" == *scrollIntoView* ]]; then
      if [[ "$js" == *data-ui-clone-state-reveal* && "$js" == *getAttribute* && "$js" == *actual* ]]; then
        printf '[]'
      else
        printf '%s' {stuck!r}
      fi
      exit 0
    fi
    if [[ "$js" == *data-ui-clone-state-reveal* ]]; then
      printf '1'
    else
      printf '0'
    fi
    exit 0
    ;;
esac
exit 0
"""


def _fake_agent_browser_malformed_marker() -> str:
    malformed = json.dumps(
        [{
            "idx": "0",
            "tag": "DIV",
            "cls": "card",
            "box": "160x120",
            "init": {"opacity": "1", "transform": "none"},
            "post": {"opacity": "1", "transform": "none"},
            "stateReveal": [],
            "invalidStateReveal": ["data-in-view"],
            "chain": [],
        }]
    )
    return f"""#!/usr/bin/env bash
set -uo pipefail
if [ "${{1:-}}" = "--session" ]; then shift 2; fi
cmd="${{1:-}}"; shift || true
case "$cmd" in
  set|navigate|close) exit 0 ;;
  eval)
    js="${{1:-}}"
    if [[ "$js" == *scrollIntoView* ]]; then
      if [[ "$js" == *invalidStateReveal* ]]; then
        printf '%s' {malformed!r}
      else
        printf '[]'
      fi
      exit 0
    fi
    if [[ "$js" == *data-ui-clone-state-reveal* ]]; then printf '1'; else printf '0'; fi
    exit 0
    ;;
esac
exit 0
"""


def _fake_agent_browser_invalid_spec_selector() -> str:
    return """#!/usr/bin/env bash
set -uo pipefail
if [ "${1:-}" = "--session" ]; then shift 2; fi
cmd="${1:-}"; shift || true
case "$cmd" in
  set|navigate|close) exit 0 ;;
  eval)
    js="${1:-}"
    if [[ "$js" == *removeAttribute* ]]; then exit 0; fi
    if [[ "$js" == *'return -1'* ]]; then
      printf '%s' '-1'
    else
      printf '%s' '"ERROR: invalid transition-spec selector: .broken["'
    fi
    exit 0
    ;;
esac
exit 0
"""


def _run_with_fake_agent_browser(
    tmp_path: Path,
    fake_agent_browser: str,
    transition_spec: dict[str, object] | None = None,
) -> tuple[subprocess.CompletedProcess, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "agent-browser").write_text(fake_agent_browser, encoding="utf-8")
    (bin_dir / "sleep").chmod(0o755)
    (bin_dir / "agent-browser").chmod(0o755)

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    if transition_spec is not None:
        (ref_dir / "transition-spec.json").write_text(
            json.dumps(transition_spec), encoding="utf-8"
        )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["REF_DIR"] = str(ref_dir)
    env["WAIT_MS"] = "0"
    env.pop("BASH_COMPAT", None)
    bash = shutil.which("bash")
    assert bash is not None

    proc = subprocess.run(
        [bash, str(SCRIPT), "reveal-marker-test", "https://example.test/"],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=120,
    )
    return proc, ref_dir / "reveal-trigger.json"


def test_empty_phase2_output_with_candidates_fails_closed(tmp_path: Path) -> None:
    """N>0 candidates but phase-2 eval returned nothing (timeout) -> must NOT be
    reported as a clean pass; fail closed so stuck reveals aren't certified."""
    proc, artifact = _run(tmp_path, phase2_output="")
    assert proc.returncode != 0, (
        "empty phase-2 output with candidates present must fail closed, not exit 0; "
        f"stdout={proc.stdout!r}"
    )
    assert "No stuck reveals found" not in proc.stdout
    if artifact.exists():
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        assert payload["status"] != "pass"


def test_explicit_empty_array_is_a_genuine_pass(tmp_path: Path) -> None:
    """A COMPLETED phase-2 that found no stuck reveals returns "[]" -> real pass."""
    proc, artifact = _run(tmp_path, phase2_output="[]")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No stuck reveals found" in proc.stdout
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert payload["candidateCount"] == 3
    assert payload["stuckCount"] == 0


def test_transition_spec_scopes_legacy_reveal_candidates(tmp_path: Path) -> None:
    proc, artifact, log_path = _run_spec_scoped_candidates(tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    phase2_calls = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert phase2_calls == ["const START = 0; | const END = 1;"]
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert payload["candidateCount"] == 1
    assert payload["stuckCount"] == 0


def test_many_candidates_are_split_into_phase2_batches(tmp_path: Path) -> None:
    """Twenty-five candidates must not run as one settle-per-candidate eval."""
    proc, artifact, log_path = _run_many_candidates(tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    phase2_calls = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(phase2_calls) == 4
    assert "const START = 0;" in phase2_calls[0]
    assert "const END = 8;" in phase2_calls[0]
    assert "const START = 24;" in phase2_calls[-1]
    assert "const END = 25;" in phase2_calls[-1]
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert payload["candidateCount"] == 25
    assert payload["stuckCount"] == 0


def test_default_bash_completes_without_compat_state(tmp_path: Path) -> None:
    """The current Bash must not require inherited heredoc compatibility state."""
    proc, artifact = _run(tmp_path, phase2_output="[]")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"


def test_generated_boolean_state_reveal_marker_is_validated_after_scroll(tmp_path: Path) -> None:
    """Generated StateRevealDriver markers are reveal candidates even without
    hidden opacity/transform, and their target attr must be verified after
    scrolling into view."""
    proc, artifact = _run_with_fake_agent_browser(
        tmp_path,
        _fake_agent_browser_marker_reveal(),
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No stuck reveals found" in proc.stdout
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert payload["stuckCount"] == 0


def test_malformed_state_reveal_marker_fails_closed(tmp_path: Path) -> None:
    proc, artifact = _run_with_fake_agent_browser(
        tmp_path,
        _fake_agent_browser_malformed_marker(),
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "fail"
    assert payload["stuckCount"] == 1


def test_invalid_spec_selector_uses_unquoted_fail_closed_sentinel(tmp_path: Path) -> None:
    proc, artifact = _run_with_fake_agent_browser(
        tmp_path,
        _fake_agent_browser_invalid_spec_selector(),
        transition_spec={
            "transitions": [
                {
                    "trigger": "intersection",
                    "selector": ".broken[",
                    "animation": {"type": "intersectionobserver"},
                }
            ]
        },
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "error"
