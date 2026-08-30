from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_module() -> ModuleType:
    key = "_resource_mirror_test_module"
    if key in sys.modules:
        return sys.modules[key]
    path = ROOT / "scripts" / "extract" / "_resource_mirror.py"
    spec = importlib.util.spec_from_file_location(key, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


class _ResourceHandler(BaseHTTPRequestHandler):
    routes: ClassVar[dict[str, tuple[str, bytes]]] = {
        "/assets/app.css": ("text/css", b":root { --brand: #00c73c; }"),
        "/img/hero": ("image/webp", b"RIFF-webp-bytes"),
        "/anim/data.json": ("application/json", b'{"v":"5.7.0","layers":[]}'),
        "/ignored/page": ("text/html", b"<html>not an asset</html>"),
    }

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?", 1)[0]
        if path not in self.routes:
            self.send_error(404)
            return
        content_type, body = self.routes[path]
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _server_base_url() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ResourceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_mirror_resources_downloads_browser_observed_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    monkeypatch.setenv("UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE", "1")
    server, base = _server_base_url()
    try:
        payload = {
            "resources": [
                {"url": f"{base}/assets/app.css?v=1", "initiatorType": "link"},
                {"url": f"{base}/assets/app.css?v=2", "initiatorType": "link"},
                {"url": f"{base}/img/hero", "initiatorType": "img"},
                {"url": f"{base}/anim/data.json", "initiatorType": "fetch"},
                {"url": f"{base}/ignored/page", "initiatorType": "fetch"},
            ]
        }

        manifest = mod.mirror_resources(
            payload,
            tmp_path,
            source_url=f"{base}/",
            include_external=True,
            max_resources=20,
            max_bytes=1024 * 1024,
            timeout=5,
            captured_at="2026-06-08T00:00:00Z",
        )
    finally:
        server.shutdown()

    assert manifest["summary"]["candidates"] == 5
    assert manifest["summary"]["downloaded"] == 4
    assert manifest["summary"]["skipped"] == 1
    rows = manifest["resources"]
    downloaded = [row for row in rows if row["status"] == "downloaded"]
    paths = [row["path"] for row in downloaded]
    assert len(paths) == len(set(paths)), "query variants must not overwrite each other"
    assert any(path.endswith(".css") for path in paths)
    assert any(path.endswith("hero.webp") for path in paths)
    assert any(path.endswith("data.json") for path in paths)
    for path in paths:
        assert (tmp_path / path).is_file()

    written = json.loads((tmp_path / "resource-manifest.json").read_text(encoding="utf-8"))
    assert written["summary"] == manifest["summary"]
    assert "Extraction evidence only" in written["note"]


def test_mirror_resources_can_skip_external_origins(tmp_path: Path) -> None:
    mod = _load_module()
    manifest = mod.mirror_resources(
        {"resources": [{"url": "https://cdn.example/app.css", "initiatorType": "link"}]},
        tmp_path,
        source_url="https://source.example/",
        include_external=False,
        timeout=0.1,
    )

    assert manifest["summary"]["attempted"] == 0
    assert manifest["summary"]["downloaded"] == 0
    assert manifest["resources"][0]["status"] == "skipped"
    assert manifest["resources"][0]["reason"] == "external-origin"


def test_payload_source_url_drives_manifest_and_same_origin_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    monkeypatch.setenv("UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE", "1")
    server, base = _server_base_url()
    try:
        manifest = mod.mirror_resources(
            {
                "sourceUrl": f"{base}/page",
                "resources": [
                    {"url": "/assets/app.css", "initiatorType": "link"},
                    {"url": "https://cdn.example/app.css", "initiatorType": "link"},
                ],
            },
            tmp_path,
            include_external=False,
            timeout=5,
        )
    finally:
        server.shutdown()

    assert manifest["sourceUrl"] == f"{base}/page"
    assert manifest["resources"][0]["url"] == f"{base}/assets/app.css"
    assert manifest["resources"][1]["status"] == "skipped"
    assert manifest["resources"][1]["reason"] == "external-origin"


def test_required_resource_mirror_returns_nonzero_on_warn_manifest(tmp_path: Path) -> None:
    mod = _load_module()
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"sourceUrl": "https://example.com/", "resources": []}))

    rc = mod.main([str(tmp_path), str(payload), "--required"])

    assert rc == 1
    manifest = json.loads((tmp_path / "resource-manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "warn"
    assert manifest["policy"]["defaultSeverity"] == "advisory"


def test_unwrap_agent_browser_result_envelope() -> None:
    mod = _load_module()
    raw_payload = json.dumps({"resources": [{"url": "https://example.com/a.js"}]})
    envelope = json.dumps({"success": True, "data": {"result": raw_payload}})

    payload = mod.unwrap_agent_browser(envelope)
    payload = mod.unwrap_agent_browser(payload)

    assert payload["resources"][0]["url"] == "https://example.com/a.js"


def _dl(mod: ModuleType, url: str, tmp_path: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        mod.download_candidate(
            {"url": url, "initiatorType": "img"}, tmp_path, set(), timeout=2
        ),
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/secret",
        "http://[::1]/secret",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/router",
        "http://0.0.0.0/x",
    ],
)
def test_download_candidate_blocks_non_public_hosts(
    url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    monkeypatch.delenv("UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE", raising=False)
    row = _dl(mod, url, tmp_path)
    assert row["status"] == "blocked", row
    assert row["reason"] == "ssrf-blocked"


def test_download_candidate_skips_disallowed_scheme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    monkeypatch.delenv("UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE", raising=False)
    row = _dl(mod, "file:///etc/passwd", tmp_path)
    assert row["status"] == "skipped"
    assert row["reason"] == "disallowed-scheme"


def test_allow_private_env_reopens_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    monkeypatch.setenv("UI_CLONE_RESOURCE_MIRROR_ALLOW_PRIVATE", "1")
    row = _dl(mod, "http://127.0.0.1:1/x", tmp_path)
    assert row["status"] != "blocked"


def test_ip_block_reason_classification() -> None:
    mod = _load_module()
    assert mod._ip_block_reason("8.8.8.8") is None
    assert mod._ip_block_reason("1.1.1.1") is None
    assert mod._ip_block_reason("127.0.0.1") is not None
    assert mod._ip_block_reason("169.254.169.254") is not None
    assert mod._ip_block_reason("10.1.2.3") is not None
    assert mod._ip_block_reason("172.16.0.1") is not None
    assert mod._ip_block_reason("::1") is not None
    assert mod._ip_block_reason("::ffff:10.0.0.1") is not None
    assert mod._ip_block_reason("not-an-ip") is not None


def test_guarded_opener_revalidates_redirect_hops() -> None:
    mod = _load_module()
    handler = mod._GuardedRedirectHandler()
    with pytest.raises(mod.SsrfBlocked):
        handler.redirect_request(
            req=None,
            fp=None,
            code=302,
            msg="Found",
            headers={},
            newurl="http://169.254.169.254/latest/meta-data/",
        )


def test_guarded_connection_uses_the_validated_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_module()
    resolved_ip = "93.184.216.34"
    connected: list[tuple[str, int]] = []

    monkeypatch.setattr(
        mod,
        "_validate_host_addrs",
        lambda host, port: [(mod.socket.AF_INET, (resolved_ip, port))],
    )

    class _Socket:
        pass

    def fake_create_connection(address: tuple[str, int], *args: object) -> _Socket:
        connected.append(address)
        return _Socket()

    monkeypatch.setattr(mod.socket, "create_connection", fake_create_connection)
    connection = mod._GuardedHTTPConnection("example.com", 80, timeout=1)
    connection.connect()

    assert connected == [(resolved_ip, 80)]
