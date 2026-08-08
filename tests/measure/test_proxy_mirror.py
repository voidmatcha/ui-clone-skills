from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ._helpers import (
    _project_root,
)


def test_proxy_mirror_dispatcher_passes_impl_root() -> None:
    """The required-check dispatcher must use the resolved implementation."""
    dispatcher = _project_root() / "scripts" / "verify" / "build_required_dispatch.py"
    text = dispatcher.read_text(encoding="utf-8")
    match = re.search(r'"proxy-mirror-check\.sh":\s*"([^"]+)"', text)

    assert match, "proxy-mirror-check.sh signature missing from dispatcher"
    recipe = match.group(1)
    assert "{ref_dir}" in recipe
    assert "{impl_root}" in recipe, (
        "proxy-mirror-check.sh must receive the resolved impl root; otherwise "
        "external scratch implementations are incorrectly skipped"
    )


def test_proxy_mirror_check_fails_proxy_backed_original_runtime(tmp_path: Path) -> None:
    """A proxy-backed original runtime is a mirror, not a generated clone."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    public = impl / "public"
    ref.mkdir()
    public.mkdir(parents=True)
    (impl / "server.js").write_text(
        'const upstream = "https://target-site.example";\n'
        "async function proxyAndCache(parsed, targetPath, res) {\n"
        "  const response = await fetch(upstream + parsed.pathname);\n"
        "}\n",
        encoding="utf-8",
    )
    (public / "index.html").write_text(
        '<!DOCTYPE html><html><head><script src="/_next/static/chunks/app/page.js"></script></head>'
        '<body><script>self.__next_f=self.__next_f||[]</script></body></html>',
        encoding="utf-8",
    )

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "proxy-mirror-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 1, f"proxy-backed mirror must fail: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "proxy-mirror-check.json").read_text())
    assert artifact["status"] == "fail"
    assert any("proxy" in finding["kind"] for finding in artifact["findings"])
    assert any("next-ssr" in finding["kind"] for finding in artifact["findings"])



def test_proxy_mirror_check_passes_source_component_impl(tmp_path: Path) -> None:
    """Normal source components with public assets are allowed."""
    ref = tmp_path / "ref"
    impl = tmp_path / "impl"
    src = impl / "src"
    public = impl / "public"
    ref.mkdir()
    src.mkdir(parents=True)
    public.mkdir(parents=True)
    (impl / "package.json").write_text(json.dumps({"dependencies": {"react": "19"}}))
    (src / "App.tsx").write_text("export default function App(){ return <main />; }\n")
    (public / "index.html").write_text('<div id="root"></div>\n')

    script = _project_root() / "skills" / "visual-debug" / "scripts" / "proxy-mirror-check.sh"
    proc = subprocess.run(
        ["bash", str(script), str(ref), str(impl)],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert proc.returncode == 0, f"source component impl should pass: {proc.stdout}\n{proc.stderr}"
    artifact = json.loads((ref / "proxy-mirror-check.json").read_text())
    assert artifact["status"] == "pass"
