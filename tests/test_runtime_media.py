from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract" / "runtime-media.sh"


def test_runtime_media_writes_live_video_inventory(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    ref = tmp_path / "ref"
    bin_dir.mkdir()
    ref.mkdir()
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> '{tmp_path / 'calls.log'}'\n"
        "if [ \"$1\" = \"--session\" ]; then shift 2; fi\n"
        "cmd=${1:-}; shift || true\n"
        "case \"$cmd\" in\n"
        "  open|set|wait|close) exit 0 ;;\n"
        "  eval)\n"
        "    cat <<'JSON'\n"
        "{\"schemaVersion\":1,\"url\":\"https://ref.test/\",\"videos\":[{\"section\":\"hero\",\"src\":\"https://ref.test/media/hero.mp4\",\"currentSrc\":\"https://ref.test/media/hero.mp4\",\"sources\":[],\"poster\":\"\",\"autoplay\":true,\"loop\":true,\"muted\":true,\"rect\":{\"x\":0,\"y\":0,\"w\":1440,\"h\":900}}],\"totals\":{\"video\":1},\"sources\":{\"extractor\":\"runtime-media.sh\",\"scrollSamples\":5}}\n"
        "JSON\n"
        "    exit 0 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    proc = subprocess.run(
        ["bash", str(SCRIPT), "https://ref.test/", "sess", str(ref)],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads((ref / "runtime-media.json").read_text(encoding="utf-8"))
    assert payload["videos"][0]["src"] == "https://ref.test/media/hero.mp4"
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "--session sess-runtime-media open https://ref.test/" in calls
    assert "--session sess-runtime-media close" in calls
