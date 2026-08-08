import json
import os
import subprocess
from pathlib import Path

import pytest


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _stateful_header_probe() -> dict:
    at0 = {
        "tag": "header",
        "cls": "Header",
        "attrs": {},
        "geo": {"height": 80, "scrollY": 0},
        "childTagClasses": [],
    }
    at200 = {
        "tag": "header",
        "cls": "Header color-shadow-small",
        "attrs": {"class": "Header color-shadow-small"},
        "geo": {"height": 80, "scrollY": 200},
        "childTagClasses": [],
    }
    return {
        "found": True,
        "at0": at0,
        "at600": at200,
        "samples": [{"top": 200, "snapshot": at200}],
        "allRoots0": [{"name": "header", "snap": at0}],
        "allRootsDeep": [{"name": "header", "snap": at200}],
        "scrollHeight": 3000,
    }


def test_header_state_runtime_reopens_when_viewport_setup_blanks_page(tmp_path: Path) -> None:
    """agent-browser can reset a valid open to about:blank while setting viewport.

    The checker must not turn that setup reset into a false
    "ref header does not mutate" skip. It should reopen after viewport setup
    and probe the real stateful page.
    """
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log_path = tmp_path / "agent-browser.log"
    ref_probe = tmp_path / "ref-probe.json"
    impl_probe = tmp_path / "impl-probe.json"
    ref_probe.write_text(json.dumps(_stateful_header_probe()), encoding="utf-8")
    impl_probe.write_text(json.dumps(_stateful_header_probe()), encoding="utf-8")

    stub = bindir / "agent-browser"
    stub.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$AB_LOG"
session=""
if [ "${1:-}" = "--session" ]; then
  session="$2"
  shift 2
fi
cmd="${1:-}"
shift || true
href_file="$STATE_DIR/${session}.href"
case "$cmd" in
  open)
    printf '%s\\n' "${1:-}" > "$href_file"
    echo "ok"
    ;;
  set)
    if [ "${1:-}" = "viewport" ]; then
      printf 'about:blank\\n' > "$href_file"
    fi
    echo "ok"
    ;;
  eval)
    js="${1:-}"
    if [[ "$js" == *innerWidth* ]]; then
      echo 1440
    elif [[ "$js" == *location.href* ]]; then
      cat "$href_file"
    else
      href="$(cat "$href_file" 2>/dev/null || true)"
      if [ "$href" = "about:blank" ] || [ -z "$href" ]; then
        printf '%s\\n' '{"found":true,"at0":{"cls":"","attrs":{},"childTagClasses":[]},"at600":{"cls":"","attrs":{},"childTagClasses":[]},"samples":[],"allRoots0":[],"allRootsDeep":[]}'
      elif [[ "$session" == *-hdr-ref ]]; then
        cat "$REF_PROBE"
      else
        cat "$IMPL_PROBE"
      fi
    fi
    ;;
  close)
    ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["AB_LOG"] = str(log_path)
    env["STATE_DIR"] = str(state_dir)
    env["REF_PROBE"] = str(ref_probe)
    env["IMPL_PROBE"] = str(impl_probe)

    script = _project_root() / "skills/visual-debug/scripts/header-state-runtime-check.sh"
    proc = subprocess.run(
        [
            "bash",
            str(script),
            "viewport-reset",
            "https://ref.example.test/docs",
            "https://impl.example.test/docs",
            str(ref_dir),
            "1440",
            "900",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    artifact = json.loads((ref_dir / "header-state-runtime.json").read_text(encoding="utf-8"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert artifact["status"] == "pass"
    assert artifact["ref"]["mutates"] is True
    assert artifact["impl"]["mutates"] is True
    assert artifact["ref"]["classesToggled"] == ["color-shadow-small"]
    log = log_path.read_text(encoding="utf-8")
    assert log.count("open https://ref.example.test/docs") == 2
    assert log.count("open https://impl.example.test/docs") == 2


@pytest.mark.parametrize(
    ("mode", "expected_error", "artifact_status"),
    [
        ("href-eval-failure", "location.href evaluation failed", None),
        ("invalid-href", "refusing to probe non-http(s) browser page", None),
        ("probe-eval-failure", "header probe evaluation failed", None),
        ("malformed-probe", "ref header measurement failed", "fail"),
    ],
)
def test_header_state_runtime_fails_closed_when_reference_is_unmeasured(
    tmp_path: Path,
    mode: str,
    expected_error: str,
    artifact_status: str | None,
) -> None:
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ref_probe = tmp_path / "ref-probe.json"
    ref_probe.write_text(json.dumps(_stateful_header_probe()), encoding="utf-8")

    stub = bindir / "agent-browser"
    stub.write_text(
        """#!/usr/bin/env bash
set -uo pipefail
session=""
if [ "${1:-}" = "--session" ]; then
  session="$2"
  shift 2
fi
cmd="${1:-}"
shift || true
href_file="$STATE_DIR/${session}.href"
case "$cmd" in
  open)
    printf '%s\\n' "${1:-}" > "$href_file"
    echo "ok"
    ;;
  set)
    echo "ok"
    ;;
  eval)
    js="${1:-}"
    if [[ "$js" == *innerWidth* ]]; then
      echo 1440
    elif [[ "$js" == *location.href* ]]; then
      if [[ "$session" == *-hdr-ref && "$MODE" = "href-eval-failure" ]]; then
        exit 9
      elif [[ "$session" == *-hdr-ref && "$MODE" = "invalid-href" ]]; then
        echo "file:///tmp/reference.html"
      else
        cat "$href_file"
      fi
    elif [[ "$session" == *-hdr-ref && "$MODE" = "probe-eval-failure" ]]; then
      exit 9
    elif [[ "$session" == *-hdr-ref && "$MODE" = "malformed-probe" ]]; then
      echo '{}'
    else
      cat "$REF_PROBE"
    fi
    ;;
  close)
    ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["STATE_DIR"] = str(state_dir)
    env["REF_PROBE"] = str(ref_probe)
    env["MODE"] = mode

    script = _project_root() / "skills/visual-debug/scripts/header-state-runtime-check.sh"
    proc = subprocess.run(
        [
            "bash",
            str(script),
            f"fail-closed-{mode}",
            "https://ref.example.test/docs",
            "https://impl.example.test/docs",
            str(ref_dir),
            "1440",
            "900",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    assert proc.returncode != 0
    assert expected_error in f"{proc.stdout}\n{proc.stderr}"
    artifact_path = ref_dir / "header-state-runtime.json"
    if artifact_status is None:
        assert not artifact_path.exists()
    else:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["status"] == artifact_status
        assert artifact["ref"]["measurementComplete"] is False
