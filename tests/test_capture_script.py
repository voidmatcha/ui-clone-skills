"""Regression tests for scripts/extract/capture.sh orchestration."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _write_success_fake_browser(
    bin_dir: Path,
    calls: Path,
    final_url: str = "https://example.test/",
) -> None:
    parsed_url = urlsplit(final_url)
    final_origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "agent-browser").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
CALLS={calls}
printf '%s|%s|%s\\n' "$*" "${{AGENT_BROWSER_COLOR_SCHEME+set}}:${{AGENT_BROWSER_COLOR_SCHEME-}}" "${{AGENT_BROWSER_NAMESPACE+set}}:${{AGENT_BROWSER_NAMESPACE-}}" >> "$CALLS.identity"
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
  open)
    printf '{{"success":true,"data":{{"url":"%s"}}}}\\n' "{final_url}"
    exit 0
    ;;
  state)
    if [ "$1" = "save" ]; then
      python3 - "$2" <<'PY_STATE'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
assert path.stat().st_mode & 0o077 == 0
path.write_text("private-test-state")
PY_STATE
    else
      test -s "$2"
    fi
    exit 0
    ;;
  get|set|wait|close)
    exit 0
    ;;
  eval)
    if [ "$is_json" -eq 1 ]; then
      eval_input="$*"
      if [[ " $* " = *" --stdin "* ]]; then
        eval_input="$(cat)"
      fi
      case "$eval_input" in
        *"Promise.resolve().then"*)
          echo '{{"success":true,"data":{{"origin":"{final_origin}","result":{{"state":"pending"}}}}}}'
          ;;
        *"const record = globalThis[key]"*)
          echo '{{"success":true,"data":{{"origin":"{final_origin}","result":{{"state":"done","value":{{"results":[],"durationMs":100,"candidatesFound":0,"candidatesCappedAt":50,"selectorsAbsentFromPage":0,"selectorsInvalid":0}}}}}}}}'
          ;;
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
        printf '%s' "$1" > "{bin_dir / "current-recording"}"
        exit 0
        ;;
      stop)
        path="$(cat "{bin_dir / "current-recording"}")"
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
    assert "--session capture-test open https://example.test/ --json" in logged
    assert logged.index("--session capture-test close") < logged.index(
        "--session capture-test open https://example.test/ --json"
    )
    assert not any("--session capture-test-capture-" in line for line in logged)


def test_capture_sh_records_redirect_from_navigation_response(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    final_url = "https://www.example.test/landing"
    _write_success_fake_browser(bin_dir, tmp_path / "calls.log", final_url)
    ref_dir = tmp_path / "ref"
    result = _run_capture(ref_dir, bin_dir)
    assert result.returncode == 0, result.stderr
    receipt = json.loads((ref_dir / "capture-navigation.json").read_text())
    assert receipt["requestedUrl"] == "https://example.test/"
    assert receipt["finalUrl"] == final_url
    assert receipt["session"] == "capture-test"
    assert receipt["namespace"]


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
    state_calls = [
        shlex.split(line)
        for line in logged.splitlines()
        if line.startswith("--session capture-test-states ")
    ]
    assert any(args[-1] == "close" and args[2] == "--init-script" for args in state_calls)
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
    open_index = logged.index("--session capture-test open https://example.test/ --json")
    viewport_index = logged.index("--session capture-test set viewport 1440 900", open_index)
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
    assert "--session capture-test open https://example.test/ --json" in logged
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
  open)
    printf '{{"success":true,"data":{{"url":"%s"}}}}\\n' "$1"
    exit 0
    ;;
  state)
    if [ "$1" = "save" ]; then
      python3 - "$2" <<'PY_STATE'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
assert path.stat().st_mode & 0o077 == 0
path.write_text("private-test-state")
PY_STATE
    else
      test -s "$2"
    fi
    exit 0
    ;;
  get|set|wait|close)
    exit 0
    ;;
  eval)
    if [ "$is_json" -eq 1 ]; then
      eval_input="$*"
      if [[ " $* " = *" --stdin "* ]]; then
        eval_input="$(cat)"
      fi
      case "$eval_input" in
        *"Promise.resolve().then"*)
          echo '{{"success":true,"data":{{"origin":"https://example.test","result":{{"state":"pending"}}}}}}'
          ;;
        *"const record = globalThis[key]"*)
          echo '{{"success":true,"data":{{"origin":"https://example.test","result":{{"state":"done","value":{{"results":[],"durationMs":100,"candidatesFound":0,"candidatesCappedAt":50,"selectorsAbsentFromPage":0,"selectorsInvalid":0}}}}}}}}'
          ;;
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


def test_ui_capture_skill_uses_pipeline_as_external_cwd_default() -> None:
    skill = (_project_root() / "skills" / "ui-capture" / "SKILL.md").read_text(encoding="utf-8")
    deterministic = skill.index("## Deterministic default")
    manual = skill.index("## Phase 1")
    assert deterministic < manual
    assert 'uv run --project "$UI_CLONE_ROOT" python -m ui_clone.pipeline' in skill
    assert '"$URL" "$COMPONENT" "$SESSION" run --phases 0A,1,2' in skill
    assert "never `uv run --directory`" in skill
    assert "$HOME/.config/ui-clone-skills/root" in skill
    assert '"$PLUGIN_ROOT/scripts/..."' in skill
    assert "Standalone success is terminal" in skill
    success_contract = skill.index("**Standalone success is terminal.**")
    assert success_contract < manual
    assert "Do not run\nthe Phase 1/2 commands below" in skill
    assert "open another agent-browser session" in skill
    assert "Do not append `| tail`, `| tee`, or\nanother pipeline" in skill
    assert "turn a failed capture into exit 0" in skill
    assert "CAPTURE_STATUS=$?" in skill
    assert 'exit "$CAPTURE_STATUS"' in skill


@pytest.mark.parametrize("color", [None, "", "dark"])
@pytest.mark.parametrize("reuse", [False, True])
def test_primary_capture_preserves_browser_identity_for_downstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    color: str | None,
    reuse: bool,
) -> None:
    if color is None:
        monkeypatch.delenv("AGENT_BROWSER_COLOR_SCHEME", raising=False)
    else:
        monkeypatch.setenv("AGENT_BROWSER_COLOR_SCHEME", color)
    monkeypatch.setenv("AGENT_BROWSER_NAMESPACE", "driver-namespace")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    _write_success_fake_browser(bin_dir, calls)
    result = _run_capture(tmp_path / "ref", bin_dir, reuse_session=reuse)
    assert result.returncode == 0, result.stderr
    downstream_env = os.environ.copy()
    # The pipeline driver pins the same launch default for every producer.
    downstream_env["AGENT_BROWSER_COLOR_SCHEME"] = color or "light"
    downstream = subprocess.run(
        [str(bin_dir / "agent-browser"), "--session", "capture-test", "get", "url"],
        env=downstream_env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert downstream.returncode == 0
    rows = [row.split("|") for row in Path(str(calls) + ".identity").read_text().splitlines()]
    primary = [row for row in rows if row[0].startswith("--session capture-test ")]
    assert primary and all(row[1:] == primary[-1][1:] for row in primary)
    commands = [row[0] for row in primary]
    media = f"--session capture-test set media {color or 'light'}"
    assert commands.index("--session capture-test get url") < commands.index(media)
    assert commands.index(media) < commands.index(
        "--session capture-test open https://example.test/ --json"
    )


@pytest.mark.parametrize("fail_media", [False, True])
def test_recording_restores_media_without_navigating_active_recorder(
    tmp_path: Path,
    fail_media: bool,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    _write_success_fake_browser(bin_dir, calls)
    fake = bin_dir / "agent-browser"
    source = fake.read_text()
    source = source.replace(
        "      start)\n",
        """      start)
        [ "$#" -eq 1 ] || exit 71
        [ "$(cat "${CALLS}.page")" = "https://example.test/" ] || exit 71
        touch "${CALLS}.recording"
        printf dark > "${CALLS}.media"
        printf default > "${CALLS}.viewport"
""",
        1,
    )
    source = source.replace("      stop)\n", '      stop)\n        rm -f "${CALLS}.recording"\n', 1)
    source = source.replace(
        "  get|set|wait|close)\n",
        f"""  set)
    if [ "$1" = "media" ]; then
      if [ -f "${{CALLS}}.recording" ]; then {"exit 73" if fail_media else ":"}; fi
      printf '%s' "$2" > "${{CALLS}}.media"
    fi
    if [ "$1" = "viewport" ]; then printf '%s %s' "$2" "$3" > "${{CALLS}}.viewport"; fi
    exit 0
    ;;
  get|wait|close)
""",
        1,
    )
    source = source.replace(
        "  open)\n",
        """  open)
    [ ! -f "${CALLS}.recording" ] || exit 74
    printf '%s' "$1" > "${CALLS}.page"
""",
        1,
    )
    source = source.replace(
        "  eval)\n",
        """  eval)
    if [ -f "${CALLS}.recording" ]; then
      [ "$(cat "${CALLS}.media")" = "${AGENT_BROWSER_COLOR_SCHEME:-light}" ] || exit 72
      [ "$(cat "${CALLS}.viewport")" = "1440 900" ] || exit 72
    fi
""",
        1,
    )
    fake.write_text(source)
    ref_dir = tmp_path / "ref"
    result = _run_capture(ref_dir, bin_dir)
    assert result.returncode == (73 if fail_media else 0), result.stderr
    logged = calls.read_text().splitlines()
    start = next(index for index, line in enumerate(logged) if " record start " in line)
    assert logged[start].endswith("full-scroll.webm")
    saved_state = next(
        command.split(" state save ", 1)[1] for command in logged if " state save " in command
    )
    assert not Path(saved_state).exists(), "Private state must be deleted on every exit"
    assert not Path(saved_state).is_relative_to(ref_dir)
    assert logged[start - 1] == f"--session capture-test state save {saved_state}"
    assert logged[start + 1] == "--session capture-test set viewport 1440 900"
    assert logged[start + 2].startswith("--session capture-test set media ")
    if fail_media:
        error = json.loads((ref_dir / "capture-error.json").read_text())
        assert error["stage"] == "scroll-video:color-scheme"
        assert logged[-1] == "--session capture-test record stop"
    else:
        stop = logged.index("--session capture-test record stop", start)
        assert not any(" open " in command for command in logged[start:stop])
        assert logged[stop + 1] == f"--session capture-test state load {saved_state}"
        assert logged[stop + 2] == "--session capture-test open https://example.test/ --json"
        assert logged[stop + 3] == "--session capture-test wait 3500"
        assert (ref_dir / "scroll-video/ref/full-scroll.webm").is_file()
