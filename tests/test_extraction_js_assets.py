from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_large_extraction_programs_are_standalone_assets() -> None:
    dom_wrapper = (
        ROOT / "skills" / "visual-debug" / "scripts" / "extract-dom.sh"
    ).read_text(encoding="utf-8")
    runtime_wrapper = (
        ROOT / "scripts" / "extract" / "extract-animation-runtime.sh"
    ).read_text(encoding="utf-8")

    assert "EXTRACT_JS=$(cat <<" not in dom_wrapper
    assert 'eval --stdin < "$EVAL_JS"' in dom_wrapper
    assert 'lib/extract-dom.js' in dom_wrapper
    assert 'eval "$(cat <<' not in runtime_wrapper
    assert 'eval --stdin < "$EVAL_JS"' in runtime_wrapper
    assert 'extract-animation-runtime.js' in runtime_wrapper


def test_extraction_assets_keep_iife_contract() -> None:
    assets = (
        ROOT / "skills" / "visual-debug" / "scripts" / "lib" / "extract-dom.js",
        ROOT / "scripts" / "extract" / "extract-animation-runtime.js",
    )
    for asset in assets:
        source = asset.read_text(encoding="utf-8").strip()
        assert source.startswith(("(() => {", "(async () => {"))
        assert source.endswith("})()")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_runtime_scroll_sampler_keeps_reset_frames() -> None:
    asset = ROOT / "scripts" / "extract" / "extract-animation-runtime.js"
    harness = f"""
const fs = require("fs");
const asset = {json.dumps(str(asset))};
const source = fs.readFileSync(asset, "utf8");
const style = {{
  transform: "scale(0.5)", opacity: "0.4", width: "80px",
  height: "120px", borderRadius: "",
}};
const el = {{ tagName: "DIV", id: "hero", className: "card", style }};
const scrollHeight = 1500;
const innerHeight = 500;
global.setTimeout = (fn) => {{ fn(); return 0; }};
global.document = {{
  documentElement: {{ scrollHeight }},
  querySelectorAll: () => [el],
}};
global.window = {{
  innerWidth: 1440,
  innerHeight,
  scrollY: 0,
  scrollTo: (opts) => {{
    const top = typeof opts === "object" ? opts.top : opts;
    global.window.scrollY = top;
    const progress = top / Math.max(scrollHeight - innerHeight, 1);
    if (progress >= 0.15) {{
      style.transform = "none";
      style.opacity = "";
      style.width = "";
      style.height = "";
    }} else if (progress >= 0.05) {{
      style.transform = "scale(0.8)";
      style.opacity = "0.8";
      style.width = "90px";
      style.height = "135px";
    }} else {{
      style.transform = "scale(0.5)";
      style.opacity = "0.4";
      style.width = "80px";
      style.height = "120px";
    }}
  }},
}};
(async () => {{
  const raw = await eval(source);
  console.log(raw);
}})().catch((err) => {{
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
}});
"""
    proc = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    artifact = json.loads(proc.stdout)
    rows = artifact["scrollLinkedStyles"]
    assert rows and len(rows) == 1
    row = rows[0]
    assert set(row["varies"]) >= {"transform", "opacity", "width", "height"}
    assert row["byScroll"]["0.05"]["transform"] == "scale(0.8)"
    assert row["byScroll"]["0.15"]["transform"] == "none"
    assert row["byScroll"]["0.15"]["opacity"] is None
    assert row["byScroll"]["0.15"]["width"] is None
    assert row["byScroll"]["0.15"]["height"] is None
    assert 0.125 in artifact["scrolledPositions"]
    assert row["byScroll"]["0.125"]["transform"] == "scale(0.8)"
    assert artifact["viewport"] == {"width": 1440, "height": 500}
    assert artifact["documentScroll"] == {"scrollHeight": 1500, "maxScroll": 1000}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_runtime_scroll_sampler_records_settled_not_midflight_values() -> None:
    """A spring is still in flight shortly after a scroll jump. Reading once
    after a fixed delay records that mid-flight value as if it were a
    keyframe, which replays as jitter. The sampler must wait for the frame to
    stop changing and record the settled value."""
    asset = ROOT / "scripts" / "extract" / "extract-animation-runtime.js"
    harness = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(asset))}, "utf8");
const scrollHeight = 1500;
const innerHeight = 500;

// ticks advance only when the sampler waits; a spring needs more than one.
let ticks = 0;
let settledOpacity = "0.4";
let midflightOpacity = "0.31";
global.setTimeout = (fn) => {{ ticks += 1; fn(); return 0; }};

const style = {{
  transform: "none", width: "", height: "", borderRadius: "",
  get opacity() {{ return ticks >= 2 ? settledOpacity : midflightOpacity; }},
}};
const el = {{ tagName: "DIV", id: "hero", className: "card", style }};
global.document = {{
  documentElement: {{ scrollHeight }},
  querySelectorAll: () => [el],
}};
global.window = {{
  innerWidth: 1440,
  innerHeight,
  scrollY: 0,
  scrollTo: (opts) => {{
    const top = typeof opts === "object" ? opts.top : opts;
    global.window.scrollY = top;
    ticks = 0;
    const progress = top / Math.max(scrollHeight - innerHeight, 1);
    settledOpacity = progress >= 0.1 ? "1" : progress >= 0.05 ? "0.8" : "0.4";
    // a spring passes through a different value at every stop
    midflightOpacity = progress >= 0.1 ? "0.93" : progress >= 0.05 ? "0.62" : "0.31";
  }},
}};
(async () => {{
  // eval of a repo-owned asset read from disk: the asset is an IIFE meant to
  // run inside a page, so this is how the suite exercises it under node.
  console.log(await eval(source));
}})().catch((err) => {{
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
}});
"""
    proc = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    rows = json.loads(proc.stdout)["scrollLinkedStyles"]
    assert rows and len(rows) == 1
    recorded = {
        _frac: _rec.get("opacity") for _frac, _rec in rows[0]["byScroll"].items()
    }
    midflight = {"0.31", "0.62", "0.93"}
    assert not (midflight & set(recorded.values())), (
        f"sampler recorded mid-flight spring values as keyframes: {recorded}"
    )
    assert recorded["0"] == "0.4"
    assert recorded["0.05"] == "0.8"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_runtime_scroll_sampler_flags_latched_rows() -> None:
    """A latched motion (fires once on enter, never reverses) reads the same
    as a scroll-linked one on a single downward sweep, so it gets replayed as
    an interpolated curve — which renders every state half-applied. Revisiting
    each position on the way back up separates them: a scroll-linked property
    returns the same value at the same offset, a latched one does not."""
    asset = ROOT / "scripts" / "extract" / "extract-animation-runtime.js"
    harness = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(asset))}, "utf8");
const scrollHeight = 1500;
const innerHeight = 500;

let ticks = 0;
let progress = 0;
let fired = false;
global.setTimeout = (fn) => {{ ticks += 1; fn(); return 0; }};

const settledScrub = () => (progress >= 0.1 ? "1" : progress >= 0.05 ? "0.8" : "0.4");
// latched: flips on first entry past the threshold and never comes back
const settledLatch = () => (fired ? "1" : "0");

const mk = (id, settled) => ({{
  tagName: "DIV", id, className: "card",
  style: {{
    transform: "none", width: "", height: "", borderRadius: "",
    get opacity() {{ return ticks >= 2 ? settled() : "0.5"; }},
  }},
}});
const scrubEl = mk("scrub", settledScrub);
const latchEl = mk("latch", settledLatch);

global.document = {{
  documentElement: {{ scrollHeight }},
  querySelectorAll: () => [scrubEl, latchEl],
}};
global.window = {{
  innerWidth: 1440,
  innerHeight,
  scrollY: 0,
  scrollTo: (opts) => {{
    const top = typeof opts === "object" ? opts.top : opts;
    global.window.scrollY = top;
    ticks = 0;
    progress = top / Math.max(scrollHeight - innerHeight, 1);
    if (progress >= 0.1) fired = true;
  }},
}};
(async () => {{
  // eval of a repo-owned asset read from disk (see note above).
  console.log(await eval(source));
}})().catch((err) => {{
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
}});
"""
    proc = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    rows = {r["selector"]: r for r in json.loads(proc.stdout)["scrollLinkedStyles"]}
    latch = rows["div#latch.card"]
    scrub = rows["div#scrub.card"]
    assert latch["latched"] is True, "latched motion not flagged"
    assert scrub["latched"] is False, "scroll-linked motion wrongly flagged as latched"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_runtime_scroll_sampler_refines_late_changed_intervals() -> None:
    """Adaptive sampling must not spend the whole refinement budget on the
    earliest changing intervals. Scroll-scrubbed sections later in the page
    need at least one interior midpoint too."""
    asset = ROOT / "scripts" / "extract" / "extract-animation-runtime.js"
    harness = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(asset))}, "utf8");
const scrollHeight = 1500;
const innerHeight = 500;
global.setTimeout = (fn) => {{ fn(); return 0; }};

let progress = 0;
const style = {{
  transform: "none", width: "", height: "", borderRadius: "",
  get opacity() {{ return progress.toFixed(3); }},
}};
const el = {{ tagName: "DIV", id: "scrub", className: "card", style }};
global.document = {{
  documentElement: {{ scrollHeight }},
  querySelectorAll: () => [el],
}};
global.window = {{
  innerWidth: 1440,
  innerHeight,
  scrollY: 0,
  scrollTo: (opts) => {{
    const top = typeof opts === "object" ? opts.top : opts;
    global.window.scrollY = top;
    progress = top / Math.max(scrollHeight - innerHeight, 1);
  }},
}};
(async () => {{
  console.log(await eval(source));
}})().catch((err) => {{
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
}});
"""
    proc = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    positions = json.loads(proc.stdout)["scrolledPositions"]
    assert len(positions) <= 32
    assert 0.45 in positions


def test_runtime_capture_contract_is_bounded_and_explicit() -> None:
    source = (
        ROOT / "scripts" / "extract" / "extract-animation-runtime.js"
    ).read_text(encoding="utf-8")

    assert "captureStatus" in source
    assert "captureError" in source
    assert "scrollAudit" in source
    assert "[0, 0.05, 0.1, 0.2, 0.35, 0.55, 0.75, 1]" in source
    assert "SETTLE_MAX_POLLS = 3" in source
    assert "MAX_ADAPTIVE_POSITIONS = 8" in source
    assert "30-second IPC read ceiling" in source
    assert "querySelectorAll(\"[style]\")" in source
    assert 'querySelectorAll("*")' not in source
    assert "requested" in source
    assert "observed" in source
    assert "sourceId" in source
    assert "ScrollSmoother" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_runtime_capture_outputs_audit_filter_and_source_ids() -> None:
    asset = ROOT / "scripts" / "extract" / "extract-animation-runtime.js"
    harness = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(asset))}, "utf8");
const scrollHeight = 1500;
const innerHeight = 500;
const calls = [];
let ticks = 0;
global.setTimeout = (fn) => {{ ticks += 1; fn(); return 0; }};
const style = {{ transform: "scale(0.5)", opacity: "0.4", width: "", height: "", borderRadius: "" }};
const el = {{ tagName: "DIV", id: "hero", className: "card", style }};
global.document = {{
  documentElement: {{ scrollHeight }},
  querySelectorAll: (selector) => {{
    calls.push(selector);
    if (selector !== "[style]") throw new Error("unexpected selector: " + selector);
    return [el];
  }},
}};
global.window = {{
  innerWidth: 1440,
  innerHeight,
  scrollY: 0,
  scrollTo: (opts) => {{
    const top = typeof opts === "object" ? opts.top : opts;
    global.window.scrollY = top;
    const progress = top / Math.max(scrollHeight - innerHeight, 1);
    style.transform = progress >= 0.2 ? "scale(1)" : progress >= 0.05 ? "scale(0.8)" : "scale(0.5)";
    style.opacity = progress >= 0.2 ? "1" : progress >= 0.05 ? "0.8" : "0.4";
  }},
}};
(async () => {{
  const raw = await eval(source);
  const artifact = JSON.parse(raw);
  console.log(JSON.stringify({{ artifact, calls }}));
}})().catch((err) => {{
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
}});
"""
    proc = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    artifact = result["artifact"]
    assert set(result["calls"]) == {"[style]"}
    assert artifact["captureStatus"] == "ok"
    assert artifact["captureError"] is None
    assert artifact["scrollAudit"]["engine"] == "native"
    assert artifact["scrollAudit"]["maxScroll"] == 1000
    samples = artifact["scrollAudit"]["samples"]
    assert samples[0]["requested"] == 0
    assert 1 in {sample["requested"] for sample in samples}
    assert all("observed" in sample and "method" in sample for sample in samples)
    assert len(samples) <= 32
    rows = artifact["scrollLinkedStyles"]
    assert rows and rows[0]["sourceId"].startswith("n")
    assert rows[0]["filter"] == ["transform", "opacity"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_runtime_capture_samples_css_filter_values() -> None:
    asset = ROOT / "scripts" / "extract" / "extract-animation-runtime.js"
    harness = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(asset))}, "utf8");
const scrollHeight = 1500;
const innerHeight = 500;
let ticks = 0;
global.setTimeout = (fn) => {{ ticks += 1; fn(); return 0; }};
const style = {{
  transform: "translateX(0px)",
  opacity: "",
  width: "",
  height: "",
  borderRadius: "",
  filter: "blur(0px)",
}};
const el = {{ tagName: "DIV", id: "hero", className: "card", style }};
global.document = {{
  documentElement: {{ scrollHeight }},
  querySelectorAll: (selector) => {{
    if (selector !== "[style]") throw new Error("unexpected selector: " + selector);
    return [el];
  }},
}};
global.window = {{
  innerWidth: 1440,
  innerHeight,
  scrollY: 0,
  scrollTo: (opts) => {{
    const top = typeof opts === "object" ? opts.top : opts;
    global.window.scrollY = top;
    const progress = top / Math.max(scrollHeight - innerHeight, 1);
    style.filter = progress >= 0.2 ? "blur(12px)" : progress >= 0.05 ? "blur(4px)" : "blur(0px)";
  }},
}};
(async () => {{
  const raw = await eval(source);
  console.log(raw);
}})().catch((err) => {{
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
}});
"""
    proc = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    artifact = json.loads(proc.stdout)
    assert artifact["captureStatus"] == "ok"
    rows = artifact["scrollLinkedStyles"]
    assert rows and len(rows) == 1
    row = rows[0]
    assert row["filter"] == ["filter"]
    assert row["varies"] == ["filter"]
    assert row["byScroll"]["0"]["filter"] == "blur(0px)"
    assert row["byScroll"]["0.05"]["filter"] == "blur(4px)"
    assert row["byScroll"]["0.2"]["filter"] == "blur(12px)"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_runtime_capture_fails_closed_when_lenis_drive_throws() -> None:
    asset = ROOT / "scripts" / "extract" / "extract-animation-runtime.js"
    harness = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(asset))}, "utf8");
const scrollHeight = 1500;
const innerHeight = 500;
global.setTimeout = (fn) => {{ fn(); return 0; }};
const style = {{ transform: "translateY(0px)", opacity: "", width: "", height: "", borderRadius: "", filter: "" }};
const el = {{ tagName: "DIV", id: "hero", className: "card", style }};
global.document = {{
  documentElement: {{ scrollHeight }},
  querySelectorAll: () => [el],
}};
global.window = {{
  innerWidth: 1440,
  innerHeight,
  scrollY: 0,
  lenis: {{
    options: {{ duration: 1.2, smoothWheel: true }},
    scrollTo: () => {{ throw new Error("lenis drive failed"); }},
  }},
  scrollTo: (opts) => {{
    const top = typeof opts === "object" ? opts.top : opts;
    global.window.scrollY = top;
  }},
}};
(async () => {{
  const raw = await eval(source);
  console.log(raw);
}})().catch((err) => {{
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
}});
"""
    proc = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    artifact = json.loads(proc.stdout)
    assert artifact["scrollAudit"]["engine"] == "lenis"
    assert artifact["captureStatus"] == "error"
    assert artifact["captureError"]["kind"] == "scroll-engine-drive-failed"
    assert "lenis drive failed" in artifact["captureError"]["message"]


@pytest.mark.parametrize(
    ("agent_body", "expected_kind"),
    (
        ("exit 0\n", "empty-output"),
        ("echo browser exploded >&2\nexit 9\n", "agent-browser-failed"),
        ("printf not-json\nexit 0\n", "invalid-json"),
        (
            "printf '%s' '{\"captureStatus\":\"error\",\"captureError\":{\"kind\":\"scroll-not-moving\",\"message\":\"stuck\"},\"scrollAudit\":{\"engine\":\"native\",\"samples\":[]}}'\nexit 0\n",
            "scroll-not-moving",
        ),
    ),
)
def test_runtime_wrapper_writes_structured_error_artifact_for_failed_eval(
    tmp_path: Path,
    agent_body: str,
    expected_kind: str,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_agent = fake_bin / "agent-browser"
    fake_agent.write_text(f"#!/usr/bin/env bash\n{agent_body}", encoding="utf-8")
    fake_agent.chmod(fake_agent.stat().st_mode | stat.S_IXUSR)

    out_dir = tmp_path / "out"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "extract" / "extract-animation-runtime.sh"), "fake", str(out_dir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert proc.returncode == 3
    assert proc.stdout == ""
    artifact = json.loads((out_dir / "animation-runtime-dump.json").read_text(encoding="utf-8"))
    assert artifact["gsap"] is None
    assert artifact["scrollTrigger"] is None
    assert artifact["webAnimations"] is None
    assert artifact["lenis"] is None
    assert artifact["ix2"] is None
    assert artifact["scrollLinkedStyles"] is None
    assert artifact["captureStatus"] == "error"
    assert artifact["captureError"]["kind"] == expected_kind
    assert artifact["captureError"]["message"]
    if expected_kind == "scroll-not-moving":
        assert artifact["scrollAudit"] == {"engine": "native", "samples": []}
    else:
        assert artifact["scrollAudit"] is None
    assert artifact["generatedAt"]
