"""Cross-command browser identity must survive caller-owned session reuse."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCH_KEYS = (
    "AGENT_BROWSER_NAMESPACE",
    "AGENT_BROWSER_COLOR_SCHEME",
    "AGENT_BROWSER_DEFAULT_TIMEOUT",
    "AGENT_BROWSER_ARGS",
    "AGENT_BROWSER_INIT_SCRIPTS",
)


@pytest.mark.parametrize("kind", ["hover", "scroll", "states"])
@pytest.mark.parametrize("configured", [False, True])
def test_reuse_preserves_caller_browser_identity(
    tmp_path: Path, kind: str, configured: bool
) -> None:
    fake = tmp_path / "agent-browser"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"keys = {LAUNCH_KEYS!r}\n"
        "with open(os.environ['CALLS'], 'a') as out:\n"
        "    out.write(json.dumps({'args': sys.argv[1:], 'env': {k: os.environ.get(k) for k in keys}}) + '\\n')\n"
        "if 'eval' in sys.argv:\n"
        "    if '--stdin' in sys.argv: sys.stdin.read()\n"
        "    print(os.environ['PAYLOAD'])\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    payload = {
        "results": [],
        "states": [],
        "stops": [
            {
                "pct": 0,
                "scrollY": 0,
                "outerHTML": "<html><body>static</body></html>",
                "visibleSections": [],
                "compositeDigest": "d",
            }
        ],
        "durationMs": 0,
        "polls": 0,
        "reason": "no-change",
        "timedOut": False,
        "candidatesFound": 0,
        "candidatesProcessed": 0,
        "candidatesCappedAt": 20,
        "scrollHeight": 900,
        "viewportHeight": 900,
        "finalScrollHeight": 900,
        "static": True,
        "scrollEngine": "native",
        "scrollTransportProven": True,
        "domMutations": [],
        "alignmentFailures": [],
    }
    if kind == "hover":
        if "success" in payload:
            data = payload["data"]
            assert isinstance(data, dict)
            data["result"] = {"state": "done", "value": data["result"]}
        else:
            payload = {"success": True, "data": {"origin": "https://example.test",
                       "result": {"state": "done", "value": payload}}}
    env = os.environ.copy()
    for key in LAUNCH_KEYS:
        env.pop(key, None)
    if configured:
        env.update(dict(zip(LAUNCH_KEYS, ("caller-space", "dark", "43210", "--disable-dev-shm-usage", "/caller/init.js"), strict=True)))
    expected = {key: env.get(key) for key in LAUNCH_KEYS}
    calls = tmp_path / "calls.jsonl"
    env.update(PATH=f"{tmp_path}:{env['PATH']}", CALLS=str(calls), PAYLOAD=json.dumps(payload))
    proc = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/extract" / f"capture-{kind}.sh"),
            "https://example.test",
            "caller",
            str(tmp_path / "ref"),
            "--reuse-session",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    rows = [json.loads(line) for line in calls.read_text().splitlines()]
    assert rows and all(row["env"] == expected for row in rows), rows
    assert all("open" not in row["args"] and "close" not in row["args"] for row in rows)
    assert all("--init-script" not in row["args"] for row in rows)
    assert rows[0]["args"] == [
        "--session",
        "caller",
        "set",
        "media",
        "dark" if configured else "light",
    ]
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("kind", ["hover", "scroll", "states"])
@pytest.mark.parametrize("wrong_origin", [False, True])
@pytest.mark.parametrize("reuse", [False, True])
def test_capture_validates_recorded_redirect(
    tmp_path: Path, kind: str, wrong_origin: bool, reuse: bool
) -> None:
    """Only the origin returned by this session's open can authorize a redirect."""
    fake = tmp_path / "agent-browser"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['CALLS'], 'a') as out:\n"
        "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if 'open' in sys.argv:\n"
        "    print('harmless browser warning', file=sys.stderr)\n"
        "    assert '--json' in sys.argv\n"
        "    print(json.dumps({'success': True, 'data': {'url': 'https://www.example.test/final'}}))\n"
        "elif 'eval' in sys.argv:\n"
        "    if '--stdin' in sys.argv: sys.stdin.read()\n"
        "    print(os.environ['PAYLOAD'])\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    result = {
        "results": [],
        "states": [],
        "stops": [
            {
                "pct": 0,
                "scrollY": 0,
                "outerHTML": "<html></html>",
                "visibleSections": [],
                "compositeDigest": "d",
            }
        ],
        "durationMs": 0,
        "polls": 0,
        "reason": "no-change",
        "timedOut": False,
        "candidatesFound": 0,
        "candidatesProcessed": 0,
        "candidatesCappedAt": 20,
        "scrollHeight": 900,
        "viewportHeight": 900,
        "finalScrollHeight": 900,
        "static": True,
        "scrollEngine": "native",
        "scrollTransportProven": True,
        "domMutations": [],
        "alignmentFailures": [],
    }
    payload = {
        "success": True,
        "data": {
            "origin": "https://unrelated.test" if wrong_origin else "https://www.example.test",
            "result": result,
        },
    }
    ref_dir = tmp_path / "ref"
    receipt_path = ref_dir / (
        "capture-navigation.json" if reuse else f"capture-{kind}-navigation.json"
    )
    expected_session = "caller" if reuse else f"caller-{kind}"
    if reuse:
        ref_dir.mkdir()
        receipt_path.write_text(
            json.dumps(
                {
                    "requestedUrl": "http://example.test",
                    "finalUrl": "https://www.example.test/final",
                    "session": expected_session,
                    "namespace": "",
                }
            )
        )
    if kind == "hover":
        if "success" in payload:
            data = payload["data"]
            assert isinstance(data, dict)
            data["result"] = {"state": "done", "value": data["result"]}
        else:
            payload = {"success": True, "data": {"origin": "https://example.test",
                       "result": {"state": "done", "value": payload}}}
    env = os.environ.copy()
    for key in LAUNCH_KEYS:
        env.pop(key, None)
    env.update(
        PATH=f"{tmp_path}:{env['PATH']}",
        CALLS=str(tmp_path / "calls.jsonl"),
        PAYLOAD=json.dumps(payload),
        CAPTURE_SCROLL_EVAL_ATTEMPTS="1",
    )
    proc = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/extract" / f"capture-{kind}.sh"),
            "http://example.test",
            "caller",
            str(ref_dir),
            *(["--reuse-session"] if reuse else []),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == (3 if wrong_origin else 0), proc.stderr
    receipt = json.loads(receipt_path.read_text())
    assert receipt["requestedUrl"] == "http://example.test"
    assert receipt["finalUrl"] == "https://www.example.test/final"
    assert receipt["session"] == expected_session
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    if reuse:
        assert all("open" not in call and "close" not in call for call in calls)
    else:
        commands = []
        for call in calls[:4]:
            command = call[2:]
            if command[:1] == ["--init-script"]:
                command = command[2:]
            commands.append(command)
        assert commands == [
            ["close"],
            ["get", "url"],
            ["set", "viewport", "1440", "900"],
            ["set", "media", "light"],
        ]
        assert "open" in calls[4]
    if wrong_origin:
        assert not (
            ref_dir / "states" / ("splash" if kind == "states" else kind) / "summary.json"
        ).exists()
