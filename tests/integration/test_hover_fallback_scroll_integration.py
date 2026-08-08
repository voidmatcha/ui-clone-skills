"""Integration test for skills/visual-debug/scripts/hover-fallback-probe.sh —
tools-batch-11 ITEM 3 (reach scroll-revealed JS-hover targets).

The fixture `fixtures/hover-scroll-js.html` pushes two hover targets below the
fold behind a 180vh spacer:

- `.js-hover-card` — a framer-style JS pointerenter/mouseenter handler scales it
  (transform), with NO CSS :hover rule. The OLD probe resolved the target only
  at idle scroll-top 0, where it is off-screen → found:false → false-FAIL. The
  fixed probe scroll-sweeps to mount, scrolls each candidate into view, resolves
  with belowFoldOk, and dispatches real pointer events → transform delta →
  status "verified".
- `.no-hover-el` — no handler, no :hover rule → genuinely missing → "fail" (the
  regression guard: making scroll-revealed targets reachable must NOT mint
  coverage for a hover that does not exist).

Gated by UI_CLONE_INTEGRATION=1 via tests/integration/conftest.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path


def _close_session(session: str) -> None:
    subprocess.run(
        ["agent-browser", "close", "--session", session],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_scroll_revealed_js_hover_verified_missing_fails(
    tmp_path: Path, http_server: str, repo_root: Path
) -> None:
    script = repo_root / "skills" / "visual-debug" / "scripts" / "hover-fallback-probe.sh"
    assert script.is_file(), f"hover-fallback-probe.sh missing at {script}"

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    # Declare both hover entries via transition-spec so build_plan emits them.
    (ref_dir / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "js-card-hover",
                        "trigger": "hover",
                        "target": ".js-hover-card",
                        "animation": {"property": "transform"},
                    },
                    {
                        "id": "missing-hover",
                        "trigger": "hover",
                        "target": ".no-hover-el",
                        "animation": {"property": "transform"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    url = f"{http_server}hover-scroll-js.html"
    session = f"ithfb-{uuid.uuid4().hex[:12]}"

    proc: subprocess.CompletedProcess[str] | None = None
    try:
        proc = subprocess.run(
            ["bash", str(script), session, url, str(ref_dir)],
            capture_output=True,
            text=True,
            timeout=90,
            env={**os.environ},
        )
    finally:
        _close_session(session)
        _close_session(f"{session}-rv")

    assert proc is not None
    art_path = ref_dir / "hover-fallback.json"
    assert art_path.is_file(), (
        f"hover-fallback.json not written (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    art = json.loads(art_path.read_text())
    by_id = {e["id"]: e for e in art.get("entries") or []}

    assert "js-card-hover" in by_id, f"js-card-hover entry missing: {art}"
    assert by_id["js-card-hover"]["status"] == "verified", (
        "scroll-revealed JS-hover target must be VERIFIED after the probe scrolls "
        f"it into view and dispatches pointer events; got {by_id['js-card-hover']}\n"
        f"stdout:\n{proc.stdout}"
    )
    assert "missing-hover" in by_id, f"missing-hover entry missing: {art}"
    assert by_id["missing-hover"]["status"] == "fail", (
        "a genuinely-missing hover (no handler, no :hover rule) must still FAIL — "
        f"the fix must not mint coverage for it; got {by_id['missing-hover']}"
    )


def test_transform_displaced_js_hover_verified_missing_fails(
    tmp_path: Path, http_server: str, repo_root: Path
) -> None:
    """tools-batch-12 ITEM 2: the loop-12 residual. A scroll-revealed JS-hover
    card DISPLACED off the viewport by a transform (the e2e-12 ResourcesDeck
    class: a large scroll-tied translate) is RENDERED + LAID OUT but fails the
    probe's isOnScreen gate after scrollIntoView (the x-axis / above-viewport
    branches are not relaxed by belowFoldOk), so findVisible returned null and the
    gate emitted a false "hover behavior does not exist". The fix resolves a
    probe-driven target on layout presence (isRendered && isLaidOut) so the
    EXISTING pointer-event dispatch reaches it; a genuinely-missing hover with no
    handler and no :hover rule still FAILS (no minted coverage).
    """
    script = repo_root / "skills" / "visual-debug" / "scripts" / "hover-fallback-probe.sh"
    assert script.is_file(), f"hover-fallback-probe.sh missing at {script}"

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "transition-spec.json").write_text(
        json.dumps(
            {
                "transitions": [
                    {
                        "id": "transform-card-hover",
                        "trigger": "hover",
                        "target": ".js-hover-card",
                        "animation": {"property": "transform"},
                    },
                    {
                        "id": "missing-hover",
                        "trigger": "hover",
                        "target": ".no-hover-el",
                        "animation": {"property": "transform"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    url = f"{http_server}hover-scroll-transform-offscreen.html"
    session = f"ithft-{uuid.uuid4().hex[:12]}"

    proc: subprocess.CompletedProcess[str] | None = None
    try:
        proc = subprocess.run(
            ["bash", str(script), session, url, str(ref_dir)],
            capture_output=True,
            text=True,
            timeout=90,
            env={**os.environ},
        )
    finally:
        _close_session(session)
        _close_session(f"{session}-rv")

    assert proc is not None
    art_path = ref_dir / "hover-fallback.json"
    assert art_path.is_file(), (
        f"hover-fallback.json not written (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    art = json.loads(art_path.read_text())
    by_id = {e["id"]: e for e in art.get("entries") or []}

    assert "transform-card-hover" in by_id, f"transform-card-hover entry missing: {art}"
    assert by_id["transform-card-hover"]["status"] == "verified", (
        "a JS-hover card displaced OFF-screen by a transform must be VERIFIED once "
        "the probe resolves it on layout presence and dispatches pointer events; "
        f"got {by_id['transform-card-hover']}\nstdout:\n{proc.stdout}"
    )
    assert "missing-hover" in by_id, f"missing-hover entry missing: {art}"
    assert by_id["missing-hover"]["status"] == "fail", (
        "a genuinely-missing hover (no handler, no :hover rule) must still FAIL — "
        f"the relaxed resolution must not mint coverage; got {by_id['missing-hover']}"
    )
