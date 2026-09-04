"""Integration test for scripts/extract/capture-hover.sh (Phase C — hover-state
snapshots).

The fixture `fixtures/hover.html` carries one CSS-only hover rule
(`a.btn:hover { transform: scale(1.1); background-color: #1e40af }`) and one
JS-only mouseenter handler on `#js-card`. We expect:

- `manifest.entries` includes the .btn entry (CSS-rule signal)
- `summary.candidatesWithCssRule >= 1` (a.btn:hover)
- `summary.candidatesWithJsDiff >= 1` (#js-card mouseenter handler — only
  gets probed because the fixture pairs it with a `#js-card:hover` CSSOM
  entry; without that pairing the JS-handler probe is dead code per the
  script's design)
- at least one per-elem snap file (`elem-<id>.json`) exists on disk

Gated by UI_CLONE_INTEGRATION=1 via tests/integration/conftest.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path


def _close_session(session: str) -> None:
    """Best-effort agent-browser session teardown."""
    subprocess.run(
        ["agent-browser", "close", "--session", session],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_hover_rule_and_manifest_emitted(tmp_path: Path, http_server: str, repo_root: Path) -> None:
    """Hover fixture (one .btn:hover CSS rule + one JS mouseenter handler) produces:
    - manifest.json with >= 1 entry referencing the .btn:hover rule
    - summary.candidatesWithCssRule >= 1
    - at least one elem-<id>.json snap file on disk corresponding to a manifest entry
    """
    script = repo_root / "scripts" / "extract" / "capture-hover.sh"
    assert script.is_file(), f"capture-hover.sh missing at {script}"

    url = f"{http_server}hover.html"
    session = f"ithover-{uuid.uuid4().hex[:12]}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    derived = f"{session}-hover"

    proc: subprocess.CompletedProcess[str] | None = None
    try:
        proc = subprocess.run(
            [str(script), url, session, str(ref_dir)],
            capture_output=True,
            text=True,
            # Header documents 22.5s worst-case; 60s gives headroom for Chrome boot.
            timeout=60,
            env={**os.environ},
        )
    finally:
        _close_session(derived)

    assert proc is not None
    assert proc.returncode == 0, (
        f"capture-hover.sh failed (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

    hover_dir = ref_dir / "states" / "hover"
    assert hover_dir.is_dir(), f"hover dir missing: {hover_dir}"

    manifest_path = hover_dir / "manifest.json"
    summary_path = hover_dir / "summary.json"
    assert manifest_path.is_file(), f"manifest.json missing in {hover_dir}"
    assert summary_path.is_file(), f"summary.json missing in {hover_dir}"

    manifest = json.loads(manifest_path.read_text())
    summary = json.loads(summary_path.read_text())

    entries = manifest.get("entries")
    assert isinstance(entries, list), f"manifest.entries not a list: {manifest!r}"
    assert len(entries) >= 1, (
        f"expected >= 1 manifest entry (the .btn:hover rule), got 0.\n"
        f"summary: {summary}"
    )

    btn_entries = [e for e in entries if "btn" in (e.get("selector") or e.get("activation") or "")]
    assert btn_entries, (
        f"no manifest entry mentions .btn in selector/activation — CSSOM sweep missed it.\n"
        f"manifest entries: {json.dumps(entries, indent=2)}"
    )

    # The per-elem snap referenced by each manifest entry must exist with matching id.
    for entry in entries:
        entry_id = entry.get("id")
        fname = entry.get("file")
        assert entry_id and fname, f"manifest entry missing id/file: {entry!r}"
        snap_path = hover_dir / fname
        assert snap_path.is_file(), f"per-elem snap missing: {snap_path} for entry {entry!r}"
        snap = json.loads(snap_path.read_text())
        assert snap.get("id") == entry_id, (
            f"id mismatch between manifest entry {entry_id!r} and snap file {snap!r}"
        )
        assert snap.get("schemaVersion") == 1, f"snap schemaVersion drift: {snap}"

    assert summary.get("checked") is True, f"summary.checked != True: {summary}"
    assert summary.get("candidatesWithCssRule", 0) >= 1, (
        f"expected candidatesWithCssRule >= 1 from .btn:hover rule, got "
        f"{summary.get('candidatesWithCssRule')!r}; full summary: {summary}"
    )
    assert summary.get("candidatesWithJsDiff", 0) >= 1, (
        "pure JS hover handlers must be discovered without a dummy CSS :hover rule; "
        f"full summary: {summary}"
    )
    assert any("js-card" in (entry.get("selector") or "") for entry in entries)
    assert not any("auto-card" in (entry.get("selector") or "") for entry in entries), (
        "passive timer/autoplay mutation must not be promoted to hover evidence"
    )
    assert summary.get("candidatesFound", 0) >= 2, (
        f"candidatesFound={summary.get('candidatesFound')} — expected >= 2 "
        f"(a.btn:hover + #js-card:hover); CSSOM walk missed one"
    )
    # JS-handler probe coverage. The #js-card mouseenter handler sets
    # `style.transform` + `style.boxShadow` synchronously; both are in the
    # script's TRACKED set so getComputedStyle diff registers > 0 changes.
    assert summary.get("candidatesWithJsDiff", 0) >= 1, (
        f"expected candidatesWithJsDiff >= 1 from #js-card mouseenter "
        f"handler, got {summary.get('candidatesWithJsDiff')!r}; full summary: {summary}"
    )
    assert summary.get("schemaVersion") == 1, f"schemaVersion drift: {summary}"
