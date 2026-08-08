from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract" / "extract-hover-css-rules.sh"


def _write_fake_agent_browser(bin_dir: Path, body: str) -> None:
    fake = bin_dir / "agent-browser"
    fake.write_text(body, encoding="utf-8")
    fake.chmod(0o755)


def test_extract_hover_css_rules_timeout_falls_back_to_downloaded_css(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    css_dir = ref / "css"
    css_dir.mkdir(parents=True)
    (css_dir / "app.css").write_text(
        """
        @media (min-width: 800px) {
          .card:hover .title, .cta:hover { transform: translateY(-4px); opacity: .9; }
        }
        .field:-webkit-autofill:hover { color: red; }
        """,
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_agent_browser(
        bin_dir,
        '#!/usr/bin/env bash\nif [ "$3" = "eval" ]; then sleep 2; fi\nexit 0\n',
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["UI_CLONE_HOVER_CSS_TIMEOUT"] = "0.1"
    env["UI_CLONE_HOVER_CSS_OPEN"] = "0"

    proc = subprocess.run(
        ["bash", str(SCRIPT), "sess", str(ref)],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads((ref / "hover-css-rules.json").read_text())
    assert payload["status"] == "pass"
    assert payload["diagnostics"]["liveCssom"]["eval"]["status"] == "timeout"
    assert payload["count"] == 2
    selectors = {rule["selector"] for rule in payload["rules"]}
    assert selectors == {".card:hover .title", ".cta:hover"}
    assert all(rule["source"] == "downloaded-css-fallback" for rule in payload["rules"])


def test_extract_hover_css_rules_accepts_live_cssom_envelope(tmp_path: Path) -> None:
    ref = tmp_path / "ref"
    ref.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    live_payload = {
        "data": {
            "result": {
                "rules": [
                    {
                        "selector": ".nav:hover",
                        "css": ".nav:hover { opacity: .8; }",
                        "declarations": "opacity: .8",
                        "source": "live-cssom",
                    }
                ]
            }
        }
    }
    _write_fake_agent_browser(
        bin_dir,
        "#!/usr/bin/env bash\n"
        'if [ "$3" = "eval" ]; then\n'
        f"  printf '%s\\n' '{json.dumps(live_payload)}'\n"
        "fi\n",
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["UI_CLONE_HOVER_CSS_OPEN"] = "0"

    proc = subprocess.run(
        ["bash", str(SCRIPT), "sess", str(ref)],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads((ref / "hover-css-rules.json").read_text())
    assert payload["status"] == "pass"
    assert payload["count"] == 1
    assert payload["rules"][0]["selector"] == ".nav:hover"
    assert payload["derivedFrom"] == ["live-cssom"]
