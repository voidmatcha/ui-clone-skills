"""Tests for scripts/extract/capture-hover.sh — Phase C hover-state
snapshots. Mirrors the fake-`agent-browser`-on-PATH pattern from
test_capture_states.py / test_capture_scroll.py.

## Phase C design (codex review applied, 2026-05-25)

Codex review surfaced that synthetic `dispatchEvent(MouseEvent)` does NOT
activate CSS `:hover` (item [1]) — pseudo-class state is set by UA
hit-testing, not by JS events. agent-browser has CDP `hover <selector>`
but it's a shell command, not callable from inside an in-page eval.

**Phase C therefore does TWO things in one in-page eval** (no shell-loop
round-trips):
  - **CSS hover signal (kind="css")**: static extraction from CSSOM
    `:hover` rules. For each rule (e.g. `.btn:hover { transform: ... }`),
    parse the rule into activation target + affected scope (e.g.
    `.card:hover .title` → activation=".card", affected=".card .title").
    Record declared properties (transform, opacity, color, ...). No
    runtime trigger required — the rule body IS the signal.
  - **JS hover signal (kind="js")**: synthetic `dispatchEvent(mouseenter)`
    on candidate elements. Captures DOM/computed-style mutations produced
    by JS hover handlers (GSAP, Framer Motion, vanilla listeners). Diff
    uses computed-style hash (codex [2] — DOM-attr-only hashes miss
    paint-only changes), restricted to candidate's affected-scope subtree.

A candidate may report both signals (kind="css+js") when a `:hover` rule
AND a JS handler exist on the same target.

## Limitations (documented)

  - **C1 grid sweep dropped**: agent-browser exposes no pixel-coord cursor
    primitive. Real cursor-following effects (spotlights, magnetic
    regions) need a future C3 mode driven by CDP `Input.dispatchMouseEvent`
    at the shell level. Codex [3] flagged the original 10×10 design as
    OVER-ENG (20s wall floor for cells most of which produce no diff).
  - **Real-pointer pure-CSS-hover paint changes** that depend on hover-driven
    descendant transforms ARE detected via CSSOM extraction (no real
    activation needed — declared properties are the signal). For visual
    verification of the actual paint, visual-debug's hover-state-compare
    gate uses real `agent-browser hover` round-trips at a coarser level.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "extract" / "capture-hover.sh"


def _make_fake_agent_browser(
    tmp_path: Path, eval_payload: str, open_returncode: int = 0,
    eval_returncode: int = 0,
) -> Path:
    """Build a fake `agent-browser` shell wrapper. Same shape as Phase A/B."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> '{tmp_path / "calls.log"}'\n"
        "shift 2  # consume --session NAME\n"
        'if [ "$1" = "open" ]; then\n'
        f"  exit {open_returncode}\n"
        'elif [ "$1" = "eval" ]; then\n'
        f"  echo '{eval_payload}'\n"
        f"  exit {eval_returncode}\n"
        "fi\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    return bin_dir


def _run_capture_hover(
    ref_dir: Path, bin_dir: Path, *, reuse_session: bool = False
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    args = [str(SCRIPT), "https://example.test", "sess1", str(ref_dir)]
    if reuse_session:
        args.append("--reuse-session")
    return subprocess.run(
        args, capture_output=True, text=True, env=env, timeout=20
    )


def _result(activation: str, *, affected: str | None = None,
            kind: str = "css+js",
            css_props: dict | None = None,
            js_changes: list[dict] | None = None) -> dict:
    """Build one per-candidate hover result entry.

    kind: "css" | "js" | "css+js" — which signal class produced this entry.
    css_props: declared properties from CSSOM :hover rule (kind in css|css+js).
    js_changes: runtime computed-style changes from synthetic mouseenter
                dispatch (kind in js|css+js).
    """
    if css_props is None and kind in ("css", "css+js"):
        css_props = {"transform": "scale(1.05)", "opacity": "0.9"}
    if js_changes is None and kind in ("js", "css+js"):
        js_changes = [
            {"selector": affected or activation,
             "computedStyleBefore": {"transform": "none"},
             "computedStyleAfter": {"transform": "scale(1.05)"}},
        ]
    return {
        "activation": activation,
        "affected": affected if affected else activation,
        "kind": kind,
        "cssProperties": css_props or {},
        "jsChanges": js_changes or [],
    }


def _eval_payload(
    results: list[dict], duration_ms: int = 5000,
    candidates_found: int | None = None,
    candidates_processed: int | None = None,
    candidates_capped_at: int = 50,
) -> str:
    css_count = len([r for r in results if r.get("kind") in ("css", "css+js")])
    js_count = len([r for r in results if r.get("kind") in ("js", "css+js")])
    any_signal = len([
        r for r in results
        if r.get("cssProperties") or r.get("jsChanges")
    ])
    payload = {
        "results": results,
        "durationMs": duration_ms,
        "candidatesFound": candidates_found if candidates_found is not None
                           else len(results),
        "candidatesProcessed": candidates_processed if candidates_processed is not None
                              else len(results),
        "candidatesCappedAt": candidates_capped_at,
        "candidatesWithCssRule": css_count,
        "candidatesWithJsDiff": js_count,
        "candidatesWithAnySignal": any_signal,
    }
    return json.dumps(payload, ensure_ascii=False).replace("'", "'\\''")


# ── tests ────────────────────────────────────────────────────────────


def test_no_hover_candidates_emits_empty_manifest(tmp_path: Path) -> None:
    """Site with no :hover rules + no JS hover handlers → results empty,
    manifest.entries empty, summary records 0 found / 0 with signal."""
    ref_dir = tmp_path / "ref"
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload([], duration_ms=500)
    )
    proc = _run_capture_hover(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    hover_dir = ref_dir / "states" / "hover"
    summary = json.loads((hover_dir / "summary.json").read_text())
    assert summary["checked"] is True
    assert summary["candidatesFound"] == 0
    assert summary["candidatesWithCssRule"] == 0
    assert summary["candidatesWithJsDiff"] == 0

    manifest = json.loads((hover_dir / "manifest.json").read_text())
    assert manifest == {"entries": []}


def test_css_only_candidate_emits_entry_with_declared_properties(
    tmp_path: Path,
) -> None:
    """A `.btn:hover { transform: ... }` rule with no JS handler →
    one entry kind="css", file contains the declared properties, manifest
    records it. CSSOM extraction is the signal — no runtime trigger needed
    (codex [1] [6]: synthetic events cannot activate CSS :hover)."""
    ref_dir = tmp_path / "ref"
    results = [
        _result(".btn", kind="css",
                css_props={"transform": "scale(1.05)", "opacity": "0.9"},
                js_changes=[]),
    ]
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload(results))
    proc = _run_capture_hover(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    hover_dir = ref_dir / "states" / "hover"
    manifest = json.loads((hover_dir / "manifest.json").read_text())
    assert len(manifest["entries"]) == 1
    entry = manifest["entries"][0]
    assert entry["kind"] == "css"
    assert entry["activation"] == ".btn"
    assert entry["id"], "manifest entries must carry stable id (codex [5])"
    assert entry["file"].startswith("elem-")
    assert entry["file"].endswith(".json")
    assert entry["changedCount"] == 0  # no JS changes for CSS-only

    snap = json.loads((hover_dir / entry["file"]).read_text())
    assert snap["kind"] == "css"
    assert snap["cssProperties"]["transform"] == "scale(1.05)"
    assert snap["cssProperties"]["opacity"] == "0.9"


def test_js_only_candidate_emits_entry_with_runtime_changes(
    tmp_path: Path,
) -> None:
    """A button with vanilla `addEventListener('mouseenter')` mutating
    style but no CSS :hover rule → entry kind="js", jsChanges populated
    with computed-style before/after."""
    ref_dir = tmp_path / "ref"
    results = [
        _result("button.cta", kind="js",
                css_props={}, js_changes=[
                    {"selector": "button.cta",
                     "computedStyleBefore": {"backgroundColor": "rgb(255, 0, 0)"},
                     "computedStyleAfter": {"backgroundColor": "rgb(200, 0, 0)"}},
                ]),
    ]
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload(results))
    proc = _run_capture_hover(ref_dir, bin_dir)
    assert proc.returncode == 0

    hover_dir = ref_dir / "states" / "hover"
    manifest = json.loads((hover_dir / "manifest.json").read_text())
    entry = manifest["entries"][0]
    assert entry["kind"] == "js"
    assert entry["changedCount"] == 1
    snap = json.loads((hover_dir / entry["file"]).read_text())
    assert snap["jsChanges"][0]["computedStyleAfter"]["backgroundColor"] == "rgb(200, 0, 0)"


def test_descendant_hover_records_affected_scope_distinct_from_activation(
    tmp_path: Path,
) -> None:
    """Codex item [4]: `.card:hover .title` means hover `.card`, observe
    `.title` — activation and affected are NOT the same selector. Entry
    must record both so downstream gate can resolve correctly."""
    ref_dir = tmp_path / "ref"
    results = [
        _result(".card", affected=".card .title", kind="css",
                css_props={"color": "rgb(255, 100, 0)"}),
    ]
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload(results))
    proc = _run_capture_hover(ref_dir, bin_dir)
    assert proc.returncode == 0

    hover_dir = ref_dir / "states" / "hover"
    manifest = json.loads((hover_dir / "manifest.json").read_text())
    entry = manifest["entries"][0]
    assert entry["activation"] == ".card"
    snap = json.loads((hover_dir / entry["file"]).read_text())
    assert snap["affected"] == ".card .title"
    assert snap["activation"] == ".card"


def test_combined_css_and_js_signal(tmp_path: Path) -> None:
    """Element with BOTH a :hover rule AND a JS mouseenter handler →
    one entry kind="css+js", both cssProperties + jsChanges populated."""
    ref_dir = tmp_path / "ref"
    results = [
        _result("a.combined", kind="css+js"),
    ]
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload(results))
    proc = _run_capture_hover(ref_dir, bin_dir)
    assert proc.returncode == 0

    hover_dir = ref_dir / "states" / "hover"
    manifest = json.loads((hover_dir / "manifest.json").read_text())
    entry = manifest["entries"][0]
    assert entry["kind"] == "css+js"
    snap = json.loads((hover_dir / entry["file"]).read_text())
    assert snap["cssProperties"]
    assert snap["jsChanges"]


def test_agent_browser_open_failure_exit_2(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload([]), open_returncode=1,
    )
    proc = _run_capture_hover(ref_dir, bin_dir)
    assert proc.returncode == 2
    assert "open failed" in proc.stderr


def test_invalid_eval_response_exit_3(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    bin_dir = _make_fake_agent_browser(tmp_path, "not json{{{")
    proc = _run_capture_hover(ref_dir, bin_dir)
    assert proc.returncode == 3


def test_unexpected_payload_shape_exit_3(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    bad = json.dumps({"durationMs": 100}).replace("'", "'\\''")
    bin_dir = _make_fake_agent_browser(tmp_path, bad)
    proc = _run_capture_hover(ref_dir, bin_dir)
    assert proc.returncode == 3


def test_derived_session_used_by_default(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload([]))
    proc = _run_capture_hover(ref_dir, bin_dir, reuse_session=False)
    assert proc.returncode == 0
    calls = (tmp_path / "calls.log").read_text()
    assert "sess1-hover" in calls
    bare_lines = [
        line for line in calls.splitlines()
        if "--session sess1 " in (line + " ") and "sess1-hover" not in line
    ]
    assert not bare_lines, f"derived session must be used: {calls}"


def test_reuse_session_flag_uses_callers_session(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload([]))
    proc = _run_capture_hover(ref_dir, bin_dir, reuse_session=True)
    assert proc.returncode == 0
    calls = (tmp_path / "calls.log").read_text()
    assert "--session sess1 " in (calls + " ")
    assert "sess1-hover" not in calls


def test_summary_carries_cap_metadata(tmp_path: Path) -> None:
    """Summary records candidatesFound (total enumerated) vs Processed
    (post-cap) vs CappedAt (the cap itself). State-coverage gate uses
    this to detect "this site has 200 hover targets but we only sampled
    50" provenance."""
    ref_dir = tmp_path / "ref"
    results = [_result(f"a.item-{i}", kind="css") for i in range(50)]
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(results, candidates_found=150,
                                candidates_processed=50,
                                candidates_capped_at=50),
    )
    proc = _run_capture_hover(ref_dir, bin_dir)
    assert proc.returncode == 0
    summary = json.loads(
        (ref_dir / "states" / "hover" / "summary.json").read_text()
    )
    assert summary["candidatesFound"] == 150
    assert summary["candidatesProcessed"] == 50
    assert summary["candidatesCappedAt"] == 50
