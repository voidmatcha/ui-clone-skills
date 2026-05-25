from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path


def test_tree_diff_writes_status_with_bsd_mktemp_semantics(tmp_path: Path) -> None:
    """tree-diff must not rely on GNU-only mktemp templates with suffixes."""

    repo = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    mktemp = bin_dir / "mktemp"
    mktemp.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import os
            import sys
            import tempfile

            args = sys.argv[1:]
            if not args:
                sys.exit(1)

            if args[0] == "-t":
                if len(args) < 2:
                    sys.exit(1)
                directory = os.environ.get("TMPDIR") or tempfile.gettempdir()
                base = args[1]
            else:
                template = args[0]
                base = os.path.basename(template)
                directory = os.path.dirname(template) or tempfile.gettempdir()
                if not base.endswith("X"):
                    print(f"mktemp: invalid template suffix: {template}", file=sys.stderr)
                    sys.exit(1)

            prefix = base.rstrip("X")
            fd, path = tempfile.mkstemp(prefix=prefix, dir=directory)
            os.close(fd)
            print(path)
            """
        ),
        encoding="utf-8",
    )
    mktemp.chmod(0o755)

    agent_browser = bin_dir / "agent-browser"
    agent_browser.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import sys

            style = {
                "fontFamily": "Arial",
                "fontSize": "16px",
                "fontWeight": "400",
                "fontStyle": "normal",
                "letterSpacing": "0px",
                "lineHeight": "20px",
                "textTransform": "none",
                "textAlign": "left",
                "color": "rgb(0, 0, 0)",
                "backgroundColor": "rgba(0, 0, 0, 0)",
                "display": "block",
                "position": "static",
                "padding": "0px",
                "margin": "0px",
                "borderRadius": "0px",
                "borderTopWidth": "0px",
                "borderTopColor": "rgb(0, 0, 0)",
                "opacity": "1",
            }

            if "eval" in sys.argv:
                js = sys.argv[-1]
                if "elementFromPoint" in js:
                    payload = [{
                        "i": 0,
                        "tag": "DIV",
                        "cls": "hero",
                        "txt": "Hello",
                        "x": 20,
                        "y": 20,
                        "top": 10,
                        "left": 10,
                        "w": 20,
                        "h": 20,
                        "style": style,
                    }]
                else:
                    payload = [{
                        "tag": "DIV",
                        "cls": "hero",
                        "txt": "Hello",
                        "x": 20,
                        "y": 20,
                        "top": 10,
                        "left": 10,
                        "w": 20,
                        "h": 20,
                        "area": 400,
                        "style": style,
                    }]
                print(json.dumps(payload))
            sys.exit(0)
            """
        ),
        encoding="utf-8",
    )
    agent_browser.chmod(0o755)

    out_dir = tmp_path / "out"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "WAIT_MS": "0",
        "MIN_SIZE": "1",
        "MAX_ELEMENTS": "1",
    }
    proc = subprocess.run(
        [
            "bash",
            str(repo / "skills" / "visual-debug" / "scripts" / "tree-diff.sh"),
            "tree-diff-test",
            "https://ref.example",
            "https://impl.example",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    status = json.loads((out_dir / "tree-diff-status.json").read_text())
    assert status["status"] == "pass"
    assert status["elements_walked"] == 1
