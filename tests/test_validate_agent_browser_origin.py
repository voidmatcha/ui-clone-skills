from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "extract" / "validate-agent-browser-origin.py"
)


def _run(expected_url: str, payload: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), expected_url],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_rejects_a_different_http_origin() -> None:
    proc = _run(
        "https://realfood.gov/path",
        {"success": True, "data": {"origin": "https://wrong.example", "result": {}}},
    )

    assert proc.returncode == 1
    assert "expected origin" in proc.stderr


def test_accepts_equivalent_default_port_and_host_case() -> None:
    proc = _run(
        "https://REALFOOD.gov/path",
        {"success": True, "data": {"origin": "https://realfood.gov:443", "result": {}}},
    )

    assert proc.returncode == 0, proc.stderr


def test_rejects_a_wrong_origin_from_a_bare_result() -> None:
    proc = _run("https://realfood.gov/", {"url": "https://wrong.example/", "states": []})

    assert proc.returncode == 1
    assert "expected origin" in proc.stderr


@pytest.mark.parametrize(
    "origin", ["https://example.test:0", "https://example.test:invalid", "about:blank"]
)
def test_rejects_invalid_or_different_port_origins(origin: str) -> None:
    proc = _run("https://example.test", {"success": True, "data": {"origin": origin}})
    assert proc.returncode == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False, "error": "navigation failed"},
        {"success": True, "data": {"url": "about:blank"}},
    ],
)
def test_failed_navigation_cannot_create_receipt(tmp_path: Path, payload: object) -> None:
    receipt = tmp_path / "navigation.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "https://example.test",
            "--session",
            "capture",
            "--navigation",
            str(receipt),
            "--record",
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 1
    assert not receipt.exists()


@pytest.mark.parametrize(
    "final_url",
    ["https://example.test/", "https://www.example.test/", "https://locale.example.test/en"],
)
def test_accepts_only_the_origin_recorded_by_navigation(tmp_path: Path, final_url: str) -> None:
    receipt = tmp_path / "navigation.json"
    args = [
        sys.executable,
        str(SCRIPT),
        "http://example.test/",
        "--session",
        "capture",
        "--navigation",
        str(receipt),
    ]
    record = subprocess.run(
        [*args, "--record"],
        input=json.dumps({"success": True, "data": {"url": final_url}}),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert record.returncode == 0, record.stderr
    for origin, expected in [(final_url, 0), ("https://unrelated.test/", 1), ("about:blank", 1)]:
        proc = subprocess.run(
            args,
            input=json.dumps({"success": True, "data": {"origin": origin}}),
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert proc.returncode == expected, proc.stderr


@pytest.mark.parametrize(
    "field,value",
    [
        ("requestedUrl", "https://other.test/"),
        ("session", "other-session"),
        ("namespace", "other-namespace"),
        ("finalUrl", "about:blank"),
    ],
)
def test_navigation_receipt_cannot_authorize_another_capture(
    tmp_path: Path, field: str, value: str
) -> None:
    receipt = tmp_path / "navigation.json"
    data = {
        "requestedUrl": "http://example.test/",
        "finalUrl": "https://example.test/",
        "session": "capture",
        "namespace": os.environ.get("AGENT_BROWSER_NAMESPACE", ""),
    }
    data[field] = value
    receipt.write_text(json.dumps(data))
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "http://example.test/",
            "--session",
            "capture",
            "--navigation",
            str(receipt),
        ],
        input=json.dumps({"success": True, "data": {"origin": "https://example.test"}}),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 1, proc.stderr
