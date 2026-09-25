"""Tests for scripts/extract/capture-scroll.sh — Phase B scroll-progress
snapshots. Mirrors the fake-`agent-browser`-on-PATH pattern from
test_capture_states.py (codex review item f). No live browser invocation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "extract" / "capture-scroll.sh"
EVAL_SCRIPT = REPO_ROOT / "scripts" / "extract" / "capture-scroll-eval.js"


def _make_fake_agent_browser(
    tmp_path: Path, eval_payload: str, open_returncode: int = 0,
    eval_returncode: int = 0,
) -> Path:
    """Build a fake `agent-browser` shell wrapper that records its argv to
    `<tmp_path>/calls.log` and returns the given eval payload (stdout) on
    `eval`-subcommand invocations. `open` returns the configured rc with no
    output.
    """
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> '{tmp_path / 'calls.log'}'\n"
        "# Find the subcommand position — after --session NAME comes 'open' or 'eval'.\n"
        "shift 2  # consume --session NAME\n"
        'while [ "$1" = "--init-script" ]; do shift 2; done\n'
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


def _run_capture_scroll(
    ref_dir: Path, bin_dir: Path, *, reuse_session: bool = False
) -> subprocess.CompletedProcess[str]:
    """Invoke capture-scroll.sh with the fake bin dir prepended to PATH."""
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    # Keep the fake bash-backed agent-browser from emitting host locale
    # warnings into stdout/stderr that the capture script parses as JSON.
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    args = [str(SCRIPT), "https://example.test", "sess1", str(ref_dir)]
    if reuse_session:
        args.append("--reuse-session")
    return subprocess.run(
        args, capture_output=True, text=True, env=env, timeout=20
    )


def _stop(pct: int, scroll_y: int, outer_html: str | None = None,
          sections: list[dict] | None = None, digest: str = "d") -> dict:
    """Build one per-pct stop entry matching the eval-result shape."""
    return {
        "pct": pct,
        "scrollY": scroll_y,
        "outerHTML": outer_html if outer_html is not None
                     else f"<html><body data-pct='{pct}'></body></html>",
        "visibleSections": sections if sections is not None else [
            {"selector": "section.hero", "top": 0, "height": 600},
        ],
        "compositeDigest": digest,
    }


def _eval_payload(stops: list[dict], duration_ms: int = 3500,
                  scroll_height: int = 8000, viewport_height: int = 1080,
                  final_scroll_height: int | None = None,
                  infinite_scroll: bool = False,
                  scroll_engine: str = "native",
                  scroll_engine_reason: str = "native window scrolling",
                  scroll_transport_proven: bool = True,
                  static: bool = False,
                  dom_mutations: list[dict] | None = None,
                  scan_step_px: int = 120,
                  alignment_failures: list[dict] | None = None,
                  capture_complete: bool = True,
                  incomplete_reason: str | None = None,
                  end_traversal: list[dict] | None = None,
                  input_listeners: list[dict[str, object]] | None = None) -> str:
    """Return a JSON-as-stdout payload matching the script's expected shape."""
    final = final_scroll_height if final_scroll_height is not None else scroll_height
    delta_pct = (
        round(((final - scroll_height) / scroll_height) * 100) if scroll_height > 0 else 0
    )
    payload = {
        "stops": stops,
        "domMutations": dom_mutations or [],
        "domMutationTraceTruncated": False,
        "scanStepPx": scan_step_px,
        "alignmentFailures": alignment_failures or [],
        "captureComplete": capture_complete,
        "incompleteReason": incomplete_reason,
        "endTraversal": end_traversal or [],
        "inputListeners": input_listeners or [],
        "durationMs": duration_ms,
        "scrollHeight": scroll_height,
        "viewportHeight": viewport_height,
        "finalScrollHeight": final,
        "scrollHeightDeltaPct": delta_pct,
        "scrollHeightGrew": (final > scroll_height) and not static,
        "infiniteScroll": infinite_scroll,
        "scrollEngine": scroll_engine,
        "scrollEngineReason": scroll_engine_reason,
        "scrollTransportProven": scroll_transport_proven,
        "scrollControlMethod": "engine-api" if scroll_engine != "native" else "native-window-scroll",
        "static": static,
    }
    return json.dumps(payload, ensure_ascii=False).replace("'", "'\\''")


# ── tests ────────────────────────────────────────────────────────────


def test_static_page_emits_single_zero_pct_snapshot(tmp_path: Path) -> None:
    """A page that fits in the viewport (scrollHeight <= viewportHeight) →
    only 0pct.json emitted, summary.static=true."""
    ref_dir = tmp_path / "ref"
    stops = [_stop(0, 0, outer_html="<html><body>fits</body></html>")]
    bin_dir = _make_fake_agent_browser(
        tmp_path,
        _eval_payload(stops, duration_ms=400, scroll_height=800,
                      viewport_height=900, static=True),
    )
    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    scroll_dir = ref_dir / "states" / "scroll"
    summary = json.loads((scroll_dir / "summary.json").read_text())
    assert summary["checked"] is True
    assert summary["static"] is True
    assert summary["scrollHeight"] == 800
    assert summary["viewportHeight"] == 900

    trajectory = json.loads((scroll_dir / "trajectory.json").read_text())
    assert len(trajectory) == 1
    assert trajectory[0]["pct"] == 0
    assert "outerHTML" not in trajectory[0], (
        "trajectory entries must not carry outerHTML — kept in per-pct files"
    )

    snap_0 = json.loads((scroll_dir / "0pct.json").read_text())
    assert "fits" in snap_0["outerHTML"]
    # No other per-pct files
    for pct in (10, 25, 50, 75, 90, 100):
        assert not (scroll_dir / f"{pct}pct.json").is_file()


def test_normal_scroll_emits_seven_snapshots(tmp_path: Path) -> None:
    """Page taller than viewport → all 7 pct files + trajectory + summary,
    each per-pct file carries outerHTML + visibleSections, trajectory is
    compact (no outerHTML)."""
    ref_dir = tmp_path / "ref"
    stops = [
        _stop(0, 0, digest="d0"),
        _stop(10, 700, digest="d10"),
        _stop(25, 1750, digest="d25"),
        _stop(50, 3500, digest="d50"),
        _stop(75, 5250, digest="d75"),
        _stop(90, 6300, digest="d90"),
        _stop(100, 6920, digest="d100"),
    ]
    bin_dir = _make_fake_agent_browser(
        tmp_path,
        _eval_payload(stops, duration_ms=3700, scroll_height=8000,
                      viewport_height=1080),
    )
    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    scroll_dir = ref_dir / "states" / "scroll"
    summary = json.loads((scroll_dir / "summary.json").read_text())
    assert summary["checked"] is True
    assert summary["static"] is False
    assert summary["infiniteScroll"] is False
    assert summary["scrollHeight"] == 8000

    trajectory = json.loads((scroll_dir / "trajectory.json").read_text())
    assert [e["pct"] for e in trajectory] == [0, 10, 25, 50, 75, 90, 100]
    for entry in trajectory:
        assert "outerHTML" not in entry
        assert "compositeDigest" in entry
        assert "visibleSections" in entry

    # All 7 per-pct files exist with outerHTML
    for pct in (0, 10, 25, 50, 75, 90, 100):
        snap = json.loads((scroll_dir / f"{pct}pct.json").read_text())
        assert snap["pct"] == pct
        assert f"data-pct='{pct}'" in snap["outerHTML"]


def test_scroll_dom_mutations_are_preserved_with_scroll_range(tmp_path: Path) -> None:
    """Transient DOM state changes must survive even when bookend HTML matches."""
    ref_dir = tmp_path / "ref"
    stops = [_stop(pct, pct * 70) for pct in (0, 10, 25, 50, 75, 90, 100)]
    mutations = [{
        "fromPct": 10,
        "toPct": 25,
        "firstScrollY": 1200,
        "lastScrollY": 1200,
        "selector": "header.site-header",
        "type": "attributes",
        "attribute": "class",
        "oldValue": "site-header",
        "newValue": "site-header is-scrolled",
        "count": 1,
    }]
    bin_dir = _make_fake_agent_browser(
        tmp_path,
        _eval_payload(stops, dom_mutations=mutations),
    )

    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 0, proc.stderr

    scroll_dir = ref_dir / "states" / "scroll"
    assert json.loads((scroll_dir / "dom-mutations.json").read_text()) == mutations
    summary = json.loads((scroll_dir / "summary.json").read_text())
    assert summary["schemaVersion"] == 2
    assert summary["domMutationCount"] == 1
    assert summary["scanStepPx"] == 120


def test_scroll_input_listener_evidence_is_preserved(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    listeners: list[dict[str, object]] = [
        {"target": "window", "type": "keydown", "declaredPassive": None},
        {"target": "document", "type": "touchmove", "declaredPassive": False},
    ]
    bin_dir = _make_fake_agent_browser(
        tmp_path,
        _eval_payload([_stop(0, 0)], input_listeners=listeners),
    )
    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(
        (ref_dir / "states" / "scroll" / "summary.json").read_text()
    )
    assert summary["inputListeners"] == listeners


def test_scroll_eval_uses_bounded_fine_grained_sweep() -> None:
    """Mutation thresholds need intermediate scroll frames, not seven teleports."""
    script = EVAL_SCRIPT.read_text(encoding="utf-8")
    assert "MAX_SCAN_STEPS = 90" in script
    assert "MIN_SCAN_STEP_PX = 120" in script
    assert "await sweepTo(targetY)" in script
    assert "await alignToTarget(targetY)" in script
    assert "settleAtDocumentEnd" in script
    assert "REQUIRED_STABLE_END_PROBES = 2" in script
    assert "new MutationObserver" in script
    assert "renderedFrame" in script
    wrapper = SCRIPT.read_text(encoding="utf-8")
    assert 'CAPTURE_SCROLL_TIMEOUT_MS:-25000' in wrapper
    assert "AGENT_BROWSER_NAMESPACE" in wrapper


def _run_scroll_eval_fixture(mode: str) -> dict[str, Any]:
    """Execute the browser program against a small, site-neutral DOM model."""
    node = shutil.which("node")
    if node is None:
        import pytest
        pytest.skip("node is required to execute capture-scroll-eval.js")
    assert node is not None
    source = EVAL_SCRIPT.read_text(encoding="utf-8")
    harness = f"""
const mode = {json.dumps(mode)};
let height = mode === "delayed-growth" ? 5000
  : mode === "static-delayed-growth" ? 1000 : 8000;
let scrollY = 0;
const root = {{
  get scrollHeight() {{ return height; }},
  outerHTML: "<html><body><main></main></body></html>",
  classList: {{ contains: () => false }},
  querySelectorAll: () => [],
}};
const footer = {{
  nodeType: 1, tagName: "FOOTER", id: "", className: "", dataset: {{}},
  parentElement: null, matches: (selector) => selector.includes("footer"),
  getBoundingClientRect: () => ({{ top: 2000, bottom: 2200, width: 800, height: 200 }}),
}};
const main = {{
  nodeType: 1, tagName: "MAIN", id: "", className: "", dataset: {{}},
  parentElement: null, matches: () => false,
  getBoundingClientRect: () => mode === "footerless-unreachable"
    ? ({{ top: 2000, bottom: 2200, width: 800, height: 200 }})
    : ({{ top: -scrollY, bottom: height - scrollY, width: 800, height }}),
}};
globalThis.performance = {{ now: (() => {{ let n = 0; return () => ++n; }})() }};
globalThis.document = {{
  documentElement: root,
  body: {{ querySelectorAll: () => [] }},
  scrollingElement: root,
  querySelectorAll: (selector) => mode === "sentinel-unreachable"
    && selector.includes("footer") ? [footer]
    : (mode === "footerless-unreachable" || mode === "valid-long-main")
      && selector.includes("main") ? [main] : [],
}};
globalThis.window = {{
  innerHeight: 1000,
  get scrollY() {{ return scrollY; }},
  scrollTo(arg1, arg2) {{
    const target = typeof arg1 === "object" ? arg1.top : arg2;
    if (mode === "delayed-growth" && target >= 4000) height = 8000;
    const browserCap = mode === "unreachable-end" ? 3000 : height - 1000;
    scrollY = Math.max(0, Math.min(target, browserCap));
  }},
}};
globalThis.MutationObserver = class {{ observe() {{}} disconnect() {{}} }};
globalThis.CSS = {{ escape: (value) => String(value) }};
globalThis.getComputedStyle = () => ({{
  color: "", opacity: "1", transform: "none", visibility: "visible",
}});
globalThis.requestAnimationFrame = (callback) => callback();
globalThis.setTimeout = (callback, delay) => {{
  if (mode === "static-delayed-growth" && delay >= 1200) height = 5000;
  callback();
  return 1;
}};
(async () => {{
  let result = await ({source});
  for (let continuation = 0; result.continuationRequired && continuation < 32; continuation += 1) {{
    result = await ({source});
  }}
  process.stdout.write(JSON.stringify(result));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
    proc = subprocess.run(
        [node, "-e", harness], capture_output=True, text=True, timeout=10
    )
    assert proc.returncode == 0, proc.stderr
    parsed = json.loads(proc.stdout)
    assert isinstance(parsed, dict)
    return parsed


def test_scroll_eval_recaptures_percent_stops_after_delayed_height_growth() -> None:
    result = _run_scroll_eval_fixture("delayed-growth")
    assert result["captureComplete"] is True
    assert result["recaptureCount"] == 0
    assert result["finalScrollHeight"] == 8000
    assert result["infiniteScroll"] is False
    assert result["potentialInfiniteScroll"] is False
    assert [stop["pct"] for stop in result["stops"]] == [0, 10, 25, 50, 75, 90, 100]
    assert result["stops"][-1]["scrollY"] == 7000
    assert result["stops"][-1]["maxScrollableAtCapture"] == 7000
    assert result["captureTraversal"] == "forward"
    assert result["stops"][0]["traversalDirection"] == "forward"
    assert result["stops"][1]["traversalDirection"] == "forward"
    assert result["stops"][-1]["traversalDirection"] == "forward"
    assert len(result["endTraversal"]) >= 2


def test_scroll_eval_does_not_misclassify_delayed_growth_as_static() -> None:
    result = _run_scroll_eval_fixture("static-delayed-growth")
    assert result["captureComplete"] is True
    assert result["static"] is False
    assert result["finalScrollHeight"] == 5000
    assert [stop["pct"] for stop in result["stops"]] == [0, 10, 25, 50, 75, 90, 100]
    assert result["stops"][-1]["scrollY"] == 4000


def test_scroll_eval_marks_permanently_unreachable_end_incomplete() -> None:
    result = _run_scroll_eval_fixture("unreachable-end")
    assert result["captureComplete"] is False
    assert result["incompleteReason"] == "document-end-unreachable"
    assert result["alignmentFailures"]


def test_scroll_eval_requires_semantic_end_sentinel_when_present() -> None:
    result = _run_scroll_eval_fixture("sentinel-unreachable")
    assert result["captureComplete"] is False
    assert result["incompleteReason"] == "document-end-sentinel-not-reached"
    assert result["endTraversal"][-1]["sentinel"]["selector"] == "footer"


def test_scroll_eval_rejects_footerless_clipped_semantic_landmark() -> None:
    result = _run_scroll_eval_fixture("footerless-unreachable")
    assert result["captureComplete"] is False
    assert result["incompleteReason"] == "document-end-sentinel-not-reached"
    assert result["endTraversal"][-1]["sentinel"]["clippedLandmarks"] == ["main"]


def test_scroll_eval_accepts_valid_footerless_long_main() -> None:
    result = _run_scroll_eval_fixture("valid-long-main")
    assert result["captureComplete"] is True
    assert result["endTraversal"][-1]["sentinel"]["selector"] == "main"
    assert result["endTraversal"][-1]["sentinel"]["clippedLandmarks"] == []


def test_wrapper_rejects_incomplete_adaptive_traversal(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    stale_scroll_dir = ref_dir / "states" / "scroll"
    stale_scroll_dir.mkdir(parents=True)
    (stale_scroll_dir / "summary.json").write_text('{"checked": true}')
    payload = _eval_payload(
        [_stop(0, 0)],
        capture_complete=False,
        incomplete_reason="document-end-did-not-stabilize",
        end_traversal=[{"probe": 10, "targetY": 9000, "observedY": 9000}],
    )
    bin_dir = _make_fake_agent_browser(tmp_path, payload)
    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 3
    assert "adaptive traversal incomplete" in proc.stderr
    assert "document-end-did-not-stabilize" in proc.stderr
    assert not (ref_dir / "states" / "scroll").exists()
    error = json.loads((ref_dir / "capture-scroll-error.json").read_text())
    assert error["reason"] == "document-end-did-not-stabilize"
    assert error["summary"]["endTraversal"] == [
        {"probe": 10, "targetY": 9000, "observedY": 9000}
    ]


def test_wrapper_completes_bounded_continuation_in_same_session(tmp_path: Path) -> None:
    ref_dir = tmp_path / "ref"
    first = json.loads(_eval_payload([]).replace("'\\''", "'"))
    first["continuationRequired"] = True
    first["captureEpoch"] = "test-epoch"
    first["continuationSequence"] = 1
    final = _eval_payload([_stop(0, 0)], static=True)
    first_payload = json.dumps(first).replace("'", "'\\''")
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    counter = tmp_path / "eval-count"
    counter.write_text("0")
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> '{tmp_path / 'calls.log'}'\n"
        "shift 2\n"
        'while [ "$1" = "--init-script" ]; do shift 2; done\n'
        'if [ "$1" = "eval" ]; then\n'
        f"  count=$(cat '{counter}')\n"
        '  if [ "$count" = "0" ]; then\n'
        f"    echo 1 > '{counter}'\n"
        f"    echo '{first_payload}'\n"
        "  else\n"
        f"    echo '{final}'\n"
        "  fi\n"
        "fi\n"
        "exit 0\n"
    )
    fake.chmod(0o755)

    proc = _run_capture_scroll(ref_dir, bin_dir)

    assert proc.returncode == 0, proc.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert sum(" eval " in f" {line} " for line in calls) == 2
    assert (ref_dir / "states" / "scroll" / "summary.json").is_file()


def test_alignment_failure_fails_closed(tmp_path: Path) -> None:
    """A mislabeled percentage snapshot must not be accepted as evidence."""
    ref_dir = tmp_path / "ref"
    stops = [_stop(0, 4300)]
    bin_dir = _make_fake_agent_browser(
        tmp_path,
        _eval_payload(
            stops,
            alignment_failures=[{"pct": 0, "targetY": 0, "actualY": 4300}],
        ),
    )
    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 3
    assert "failed to align" in proc.stderr
    assert not (ref_dir / "states" / "scroll").exists(), (
        "failed captures must not publish partially checked artifacts"
    )


def test_unproven_smooth_scroll_transport_fails_closed(tmp_path: Path) -> None:
    """A CSS/class marker alone must not certify Lenis transport fidelity."""
    ref_dir = tmp_path / "ref"
    bin_dir = _make_fake_agent_browser(
        tmp_path,
        _eval_payload(
            [_stop(0, 0)],
            scroll_engine="lenis-unproven",
            scroll_engine_reason="lenis marker without callable instance",
            scroll_transport_proven=False,
        ),
    )

    proc = _run_capture_scroll(ref_dir, bin_dir)

    assert proc.returncode == 3
    assert "scroll transport is unproven" in proc.stderr
    assert not (ref_dir / "states" / "scroll").exists()


def test_infinite_scroll_marked_in_summary(tmp_path: Path) -> None:
    """When finalScrollHeight > initial * 1.5 → summary.infiniteScroll=true.
    Codex item (d): threshold raised from 1.1 because lazy-loaded sections
    routinely add 10-15% without being infinite feeds."""
    ref_dir = tmp_path / "ref"
    stops = [_stop(pct, pct * 80) for pct in (0, 10, 25, 50, 75, 90, 100)]
    bin_dir = _make_fake_agent_browser(
        tmp_path,
        _eval_payload(stops, scroll_height=8000, final_scroll_height=16000,
                      infinite_scroll=True),
    )
    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 0

    summary = json.loads(
        (ref_dir / "states" / "scroll" / "summary.json").read_text()
    )
    assert summary["infiniteScroll"] is True
    assert summary["scrollHeightGrew"] is True
    assert summary["scrollHeightDeltaPct"] == 100  # 8000 → 16000
    assert summary["finalScrollHeight"] == 16000
    assert summary["scrollHeight"] == 8000


def test_modest_growth_records_delta_without_infinite_flag(tmp_path: Path) -> None:
    """Codex item (d): 15% growth from lazy-loaded sections must show
    `scrollHeightGrew=True` but NOT `infiniteScroll=True` — the looser
    threshold (>1.5x) prevents false positives on normal lazy-load sites."""
    ref_dir = tmp_path / "ref"
    stops = [_stop(pct, pct * 80) for pct in (0, 10, 25, 50, 75, 90, 100)]
    bin_dir = _make_fake_agent_browser(
        tmp_path,
        _eval_payload(stops, scroll_height=8000, final_scroll_height=9200,
                      infinite_scroll=False),
    )
    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 0
    summary = json.loads(
        (ref_dir / "states" / "scroll" / "summary.json").read_text()
    )
    assert summary["scrollHeightGrew"] is True
    assert summary["infiniteScroll"] is False
    assert summary["scrollHeightDeltaPct"] == 15


def test_lenis_scroll_engine_recorded_in_summary(tmp_path: Path) -> None:
    """Codex item (a): when in-page detects Lenis wrapper scroll,
    summary.scrollEngine='lenis' so downstream consumers know which API
    was used to set scroll position."""
    ref_dir = tmp_path / "ref"
    stops = [_stop(pct, pct * 80) for pct in (0, 10, 25, 50, 75, 90, 100)]
    bin_dir = _make_fake_agent_browser(
        tmp_path,
        _eval_payload(stops, scroll_engine="lenis"),
    )
    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 0
    summary = json.loads(
        (ref_dir / "states" / "scroll" / "summary.json").read_text()
    )
    assert summary["scrollEngine"] == "lenis"
    assert summary["scrollTransportProven"] is True


def test_agent_browser_open_failure_exit_2(tmp_path: Path) -> None:
    """Phase 1: open returncode != 0 → script exits 2."""
    ref_dir = tmp_path / "ref"
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload([]), open_returncode=1
    )
    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 2
    assert "open failed" in proc.stderr


def test_invalid_eval_response_exit_3(tmp_path: Path) -> None:
    """eval returns non-JSON → script exits 3."""
    ref_dir = tmp_path / "ref"
    bin_dir = _make_fake_agent_browser(tmp_path, "not json{{{")
    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 3


def test_eval_channel_failure_reopens_and_retries(tmp_path: Path) -> None:
    """A transient CDP response-channel failure should not invalidate the
    whole scroll capture. The derived session is re-opened and the full eval
    is retried; only a complete retry payload is accepted."""
    ref_dir = tmp_path / "ref"
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    counter = tmp_path / "eval-count"
    counter.write_text("0")
    payload = _eval_payload([_stop(0, 0, outer_html="<html><body>ok</body></html>")])
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> '{tmp_path / 'calls.log'}'\n"
        "shift 2\n"
        'while [ "$1" = "--init-script" ]; do shift 2; done\n'
        'if [ "$1" = "open" ]; then\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "eval" ]; then\n'
        f"  count=$(cat '{counter}')\n"
        '  if [ "$count" = "0" ]; then\n'
        f"    echo 1 > '{counter}'\n"
        '    echo \'{"success":false,"data":null,"error":"CDP response channel closed"}\'\n'
        "    exit 7\n"
        "  fi\n"
        f"  echo '{payload}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    fake.chmod(0o755)

    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 0, proc.stderr
    assert "attempt=1/3" in proc.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert sum(" open " in f" {line} " for line in calls) == 2
    assert not any("open about:blank" in line for line in calls)
    assert sum(line.endswith(" set viewport 1440 900") for line in calls) == 2
    assert sum(" eval " in f" {line} " for line in calls) == 2
    snap_0 = json.loads((ref_dir / "states" / "scroll" / "0pct.json").read_text())
    assert "ok" in snap_0["outerHTML"]


def test_derived_session_used_by_default(tmp_path: Path) -> None:
    """Default behavior uses ${SESSION}-scroll derived session, not the
    caller's session directly. Prevents race with parallel splash capture
    (which uses ${SESSION}-states)."""
    ref_dir = tmp_path / "ref"
    stops = [_stop(0, 0), _stop(50, 3500), _stop(100, 6920)]
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload(stops))
    proc = _run_capture_scroll(ref_dir, bin_dir, reuse_session=False)
    assert proc.returncode == 0
    calls = (tmp_path / "calls.log").read_text()
    assert "sess1-scroll" in calls
    assert any(line.endswith(" close") for line in calls.splitlines())
    init_flag = f"--init-script {REPO_ROOT / 'scripts/extract/capture-scroll-init.js'}"
    assert all(init_flag in line for line in calls.splitlines())
    # Caller's bare session "sess1" should NOT appear on its own (only
    # embedded as a prefix of "sess1-scroll").
    bare_session_lines = [
        line for line in calls.splitlines()
        if "--session sess1 " in (line + " ") and "sess1-scroll" not in line
    ]
    assert not bare_session_lines, f"derived session must be used: {calls}"


def test_derived_session_wait_uses_splash_summary_duration(tmp_path: Path) -> None:
    """Scroll capture must not sample a fresh derived session before a measured
    splash has had time to settle.
    """
    ref_dir = tmp_path / "ref"
    summary_dir = ref_dir / "states" / "splash"
    summary_dir.mkdir(parents=True)
    (summary_dir / "summary.json").write_text(
        json.dumps({"checked": True, "durationMs": 2700}),
        encoding="utf-8",
    )
    stops = [_stop(0, 0)]
    bin_dir = _make_fake_agent_browser(tmp_path, _eval_payload(stops))

    proc = _run_capture_scroll(ref_dir, bin_dir, reuse_session=False)

    assert proc.returncode == 0, proc.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    wait_call = next(line for line in calls if line.endswith(" wait 3500"))
    assert wait_call
    close_index = next(i for i, line in enumerate(calls) if line.endswith(" close"))
    viewport_index = next(
        i for i, line in enumerate(calls) if line.endswith(" set viewport 1440 900")
    )
    open_index = next(
        i for i, line in enumerate(calls)
        if line.endswith(" open https://example.test --json")
        and "--session sess1-scroll " in line
    )
    wait_index = calls.index(wait_call)
    eval_index = next(
        i for i, line in enumerate(calls)
        if "--session sess1-scroll " in line and " eval " in f" {line} "
    )
    assert close_index < viewport_index < open_index < wait_index < eval_index


def test_reuse_session_flag_uses_callers_session(tmp_path: Path) -> None:
    """--reuse-session → caller's session directly (no -scroll suffix). For
    when capture-scroll.sh runs sequentially after capture-states.sh on a
    shared session orchestrated by capture.sh."""
    ref_dir = tmp_path / "ref"
    stops = [_stop(0, 0)]
    bin_dir = _make_fake_agent_browser(
        tmp_path, _eval_payload(stops, static=True, scroll_height=800,
                                viewport_height=900),
    )
    proc = _run_capture_scroll(ref_dir, bin_dir, reuse_session=True)
    assert proc.returncode == 0
    calls = (tmp_path / "calls.log").read_text()
    assert "--session sess1 " in (calls + " "), (
        f"reuse-session must invoke caller's session: {calls}"
    )
    assert "sess1-scroll" not in calls
    assert "--session sess1 close" not in calls


def test_unexpected_payload_shape_exit_3(tmp_path: Path) -> None:
    """eval returns valid JSON but missing 'stops' key → script exits 3
    (matches Phase A's `states` key check)."""
    ref_dir = tmp_path / "ref"
    # Missing 'stops' — wrong shape entirely
    bad_payload = json.dumps({"durationMs": 100}).replace("'", "'\\''")
    bin_dir = _make_fake_agent_browser(tmp_path, bad_payload)
    proc = _run_capture_scroll(ref_dir, bin_dir)
    assert proc.returncode == 3


def test_about_blank_eval_envelope_retries_then_fails(tmp_path: Path) -> None:
    """A daemon restart must not turn about:blank into a static-page success."""
    ref_dir = tmp_path / "ref"
    payload = json.dumps({"success": True, "data": {"origin": "about:blank", "result": {}}})
    bin_dir = _make_fake_agent_browser(tmp_path, payload)

    proc = _run_capture_scroll(ref_dir, bin_dir)

    assert proc.returncode == 3
    assert "lost the page target" in proc.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert sum(" eval " in f" {line} " for line in calls) == 3
