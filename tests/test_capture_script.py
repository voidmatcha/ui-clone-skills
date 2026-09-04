"""Regression tests for scripts/extract/capture.sh orchestration."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _write_success_fake_browser(bin_dir: Path, calls: Path) -> None:
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "agent-browser").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
CALLS={calls}
printf '%s\\n' "$*" >> "$CALLS"
is_json=0
for arg in "$@"; do
  [ "$arg" = "--json" ] && is_json=1
done
if [ "${{1:-}}" = "--session" ]; then
  shift 2
fi
while [ "${{1:-}}" = "--init-script" ]; do
  shift 2
done
cmd="${{1:-}}"
shift || true
case "$cmd" in
  open|set|wait|close)
    exit 0
    ;;
  eval)
    if [ "$is_json" -eq 1 ]; then
      eval_input="$*"
      if [[ " $* " = *" --stdin "* ]]; then
        eval_input="$(cat)"
      fi
      case "$eval_input" in
        *window.__UI_CLONE_SPLASH_CAPTURE__*)
          echo '{{"states":[{{"ts_ms":0,"hash":1,"bodyClass":"","htmlClass":"","compositeDigest":"","domLength":100,"fullHTML":"<html><body>ready</body></html>","bookend":"0ms"}}],"durationMs":100,"polls":1,"timedOut":false,"reason":"no-change"}}'
          ;;
        *"const PCTS"*)
          echo '{{"stops":[{{"pct":0,"scrollY":0,"outerHTML":"<html><body>ready</body></html>","visibleSections":[]}}],"durationMs":100,"scrollHeight":900,"viewportHeight":900,"finalScrollHeight":900,"scrollHeightDeltaPct":0,"scrollHeightGrew":false,"infiniteScroll":false,"scrollEngine":"native","static":true}}'
          ;;
        *)
          echo '{{"results":[],"durationMs":100,"candidatesFound":0,"candidatesCappedAt":50,"selectorsAbsentFromPage":0,"selectorsInvalid":0}}'
          ;;
      esac
    else
      echo '"5000"'
    fi
    exit 0
    ;;
  screenshot)
    mkdir -p "$(dirname "$1")"
    printf 'png' > "$1"
    exit 0
    ;;
  record)
    op="${{1:-}}"
    shift || true
    case "$op" in
      start)
        printf '%s' "$1" > "{bin_dir / 'current-recording'}"
        exit 0
        ;;
      stop)
        path="$(cat "{bin_dir / 'current-recording'}")"
        mkdir -p "$(dirname "$path")"
        printf 'webm' > "$path"
        exit 0
        ;;
    esac
    ;;
esac
echo "unexpected agent-browser args: $cmd $*" >&2
exit 64
""",
        encoding="utf-8",
    )
    (bin_dir / "sleep").chmod(0o755)
    (bin_dir / "agent-browser").chmod(0o755)


def _run_capture(
    ref_dir: Path,
    bin_dir: Path,
    *,
    reuse_session: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    args = [
        "bash",
        str(_project_root() / "scripts" / "extract" / "capture.sh"),
        "https://example.test/",
        "capture-test",
        str(ref_dir),
    ]
    if reuse_session:
        args.append("--reuse-session")
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=30,
    )


def test_capture_sh_resets_named_session_before_open_by_default(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    _write_success_fake_browser(bin_dir, calls)

    result = _run_capture(tmp_path / "ref", bin_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    logged = calls.read_text(encoding="utf-8").splitlines()
    assert "--session capture-test close" in logged
    assert "--session capture-test open https://example.test/" in logged
    assert logged.index("--session capture-test close") < logged.index(
        "--session capture-test open https://example.test/"
    )
    assert not any("--session capture-test-capture-" in line for line in logged)


def test_capture_sh_collects_pre_generation_state_contracts(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    _write_success_fake_browser(bin_dir, calls)
    ref_dir = tmp_path / "ref"

    result = _run_capture(ref_dir, bin_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    contract = json.loads((ref_dir / "states" / "splash" / "contract.json").read_text())
    assert contract["captureMode"] == "pre-navigation"
    assert (ref_dir / "states" / "scroll" / "summary.json").is_file()
    assert (ref_dir / "states" / "hover" / "summary.json").is_file()
    logged = calls.read_text(encoding="utf-8")
    assert "--session capture-test-states --init-script" in logged
    assert "--session capture-test-states close" in logged
    assert "--session capture-test-scroll close" in logged
    assert "--session capture-test-hover close" in logged


def test_capture_sh_uses_splash_derived_wait_for_settled_pass(tmp_path: Path) -> None:
    """The canonical screenshot pass must respect the measured splash lifecycle."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    _write_success_fake_browser(bin_dir, calls)

    result = _run_capture(tmp_path / "ref", bin_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    logged = calls.read_text(encoding="utf-8").splitlines()
    open_index = logged.index("--session capture-test open https://example.test/")
    viewport_index = logged.index("--session capture-test set viewport 1440 900")
    assert logged[open_index + 1] == "--session capture-test set viewport 1440 900"
    assert logged[viewport_index + 1] == "--session capture-test wait 3500"


def test_capture_sh_does_not_fabricate_transition_placeholder(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    _write_success_fake_browser(bin_dir, calls)
    ref_dir = tmp_path / "ref"

    result = _run_capture(ref_dir, bin_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (ref_dir / "transitions" / "ref" / "placeholder.webm").exists()
    assert "transition-placeholder" not in calls.read_text(encoding="utf-8")


def test_capture_sh_reuse_session_opt_out_keeps_callers_session(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    _write_success_fake_browser(bin_dir, calls)

    result = _run_capture(tmp_path / "ref", bin_dir, reuse_session=True)

    assert result.returncode == 0, result.stdout + result.stderr
    logged = calls.read_text(encoding="utf-8").splitlines()
    assert "--session capture-test open https://example.test/" in logged
    assert "--session capture-test close" not in logged


def test_capture_sh_writes_error_when_record_stop_has_no_recording(tmp_path: Path) -> None:
    """A recorder stop lifecycle failure leaves structured diagnostics."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "agent-browser").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
STATE_DIR={state_dir}
is_json=0
for arg in "$@"; do
  [ "$arg" = "--json" ] && is_json=1
done
if [ "${{1:-}}" = "--session" ]; then
  shift 2
fi
while [ "${{1:-}}" = "--init-script" ]; do
  shift 2
done
cmd="${{1:-}}"
shift || true
case "$cmd" in
  open|set|wait|close)
    exit 0
    ;;
  eval)
    if [ "$is_json" -eq 1 ]; then
      eval_input="$*"
      if [[ " $* " = *" --stdin "* ]]; then
        eval_input="$(cat)"
      fi
      case "$eval_input" in
        *window.__UI_CLONE_SPLASH_CAPTURE__*)
          echo '{{"states":[{{"ts_ms":0,"hash":1,"bodyClass":"","htmlClass":"","compositeDigest":"","domLength":100,"fullHTML":"<html><body>ready</body></html>","bookend":"0ms"}}],"durationMs":100,"polls":1,"timedOut":false,"reason":"no-change"}}'
          ;;
        *"const PCTS"*)
          echo '{{"stops":[{{"pct":0,"scrollY":0,"outerHTML":"<html><body>ready</body></html>","visibleSections":[]}}],"durationMs":100,"scrollHeight":900,"viewportHeight":900,"finalScrollHeight":900,"scrollHeightDeltaPct":0,"scrollHeightGrew":false,"infiniteScroll":false,"scrollEngine":"native","static":true}}'
          ;;
        *)
          echo '{{"results":[],"durationMs":100,"candidatesFound":0,"candidatesCappedAt":50,"selectorsAbsentFromPage":0,"selectorsInvalid":0}}'
          ;;
      esac
    else
      echo '"5000"'
    fi
    exit 0
    ;;
  screenshot)
    mkdir -p "$(dirname "$1")"
    printf 'png' > "$1"
    exit 0
    ;;
  record)
    op="${{1:-}}"
    shift || true
    case "$op" in
      start)
        printf '%s' "$1" > "$STATE_DIR/current-recording"
        exit 0
        ;;
      stop)
        echo "✗ No recording in progress" >&2
        exit 1
        ;;
    esac
    ;;
esac
echo "unexpected agent-browser args: $cmd $*" >&2
exit 64
""",
        encoding="utf-8",
    )
    (bin_dir / "sleep").chmod(0o755)
    (bin_dir / "agent-browser").chmod(0o755)

    ref_dir = tmp_path / "ref"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "bash",
            str(_project_root() / "scripts" / "extract" / "capture.sh"),
            "https://example.test/",
            "capture-test",
            str(ref_dir),
        ],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert "No recording in progress" in result.stderr
    payload = json.loads((ref_dir / "capture-error.json").read_text(encoding="utf-8"))
    assert payload["stage"] == "scroll-video:record-stop"
    assert payload["artifact"] == "scroll-video/ref/full-scroll.webm"
    assert "No recording in progress" in payload["message"]
    assert payload["summary"]["static_ref_screenshots"] == 5
    assert payload["summary"]["scroll_video_ref_videos"] == 0
