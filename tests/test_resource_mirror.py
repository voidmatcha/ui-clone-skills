from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import ClassVar

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


def test_mirror_resources_downloads_browser_observed_assets(tmp_path: Path) -> None:
    mod = _load_module()
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


def test_payload_source_url_drives_manifest_and_same_origin_policy(tmp_path: Path) -> None:
    mod = _load_module()
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
