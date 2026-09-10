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


def test_tree_diff_reports_raw_subpixel_typography_drift(tmp_path: Path) -> None:
    """A 15.84px-vs-16.0px type diff is within the 0.5px PASS tolerance, so it
    must NOT flip status to fail — but the RAW unrounded delta must still be
    reported (not masked to 0 by norm() rounding). Fidelity rule: never silently
    discard sub-pixel typography drift."""

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

            prefix = base.rstrip("X")
            fd, path = tempfile.mkstemp(prefix=prefix, dir=directory)
            os.close(fd)
            print(path)
            """
        ),
        encoding="utf-8",
    )
    mktemp.chmod(0o755)

    # Impl renders fontSize 15.84px; ref renders 16.0px. round(15.84*2)/2 == 16.0,
    # so norm() collapses both to "16.0px" and the PASS/FAIL decision sees no diff.
    agent_browser = bin_dir / "agent-browser"
    agent_browser.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import sys

            joined = " ".join(sys.argv)
            font_size = "15.84px" if "tree-impl" in joined else "16px"

            style = {
                "fontFamily": "Arial",
                "fontSize": font_size,
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

    # PASS/FAIL is unchanged — the drift is within tolerance.
    status = json.loads((out_dir / "tree-diff-status.json").read_text())
    assert status["status"] == "pass", proc.stdout + proc.stderr

    # ...but the raw sub-pixel delta must be reported, not masked to 0.
    rows = json.loads((out_dir / "tree-diff.json").read_text())
    drift = [d for r in rows for d in (r.get("subpx_drift") or [])]
    assert drift, f"expected reported sub-pixel drift, got none: {rows}"
    fonts = [d for d in drift if d[0] == "fontSize"]
    assert fonts, f"expected fontSize drift entry: {drift}"
    raw_impl = str(fonts[0][1])
    assert "15.84" in raw_impl, f"raw impl value must be unrounded: {fonts[0]}"
    # The reported delta must be the true non-zero sub-pixel difference.
    assert float(fonts[0][3]) > 0.0, f"delta must be non-zero: {fonts[0]}"

    # And the raw value must be human-visible in the markdown report too.
    assert "15.84" in (out_dir / "tree-diff.md").read_text()


def test_tree_diff_renders_advisory_rows_without_crashing(tmp_path: Path) -> None:
    import sys

    script = (Path(__file__).resolve().parents[1] /
              'skills/visual-debug/scripts/tree-diff.sh').read_text()
    marker = 'python3 - "$TMP_IMPL" "$TMP_REF" "$OUT_DIR" "$PAIR_TOLERANCE" <<\'PYEOF\'\n'
    program = script.split(marker, 1)[1].split('\nPYEOF', 1)[0]
    row = dict(i=0, tag='P', cls='copy', txt='Visible text', x=10, y=10,
               top=0, left=0, w=20, h=20, area=400)
    impl = tmp_path / 'impl.json'
    ref = tmp_path / 'ref.json'
    impl.write_text(json.dumps([dict(row, style={'fontFamily': 'Inter'})]))
    ref.write_text(json.dumps([dict(row, style={'fontFamily': 'Die Grotesk'})]))
    (tmp_path / 'asset-substitution.json').write_text(json.dumps({
        'fonts': [{'original': 'Die Grotesk', 'substitute': 'Inter'}],
        'structuralOnlySections': ['*'],
    }))
    result = subprocess.run([sys.executable, '-', str(impl), str(ref), str(tmp_path), '10'],
                            input=program, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'fontFamily' in (tmp_path / 'tree-diff.md').read_text()
    receipt = json.loads((tmp_path / 'tree-diff-status.json').read_text())
    assert receipt['status'] == 'pass'
