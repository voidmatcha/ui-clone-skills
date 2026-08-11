from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "visual-debug" / "scripts" / "preview-runtime-health-check.sh"
PLAN = ROOT / "skills" / "visual-debug" / "scripts" / "verification-plan.sh"
DISPATCH = ROOT / "scripts" / "verify" / "run-required-checks.sh"


def test_preview_runtime_health_script_is_registered_in_verification_plan() -> None:
    text = PLAN.read_text(encoding="utf-8")

    assert "preview-runtime-health" in text
    assert "skills/visual-debug/scripts/preview-runtime-health-check.sh" in text
    assert "preview-runtime-health.json" in text
    assert "same-origin" in text
    assert "horizontal overflow" in text
    assert "scroll-state" in text


def test_verification_plan_emits_preview_runtime_health_row(tmp_path: Path) -> None:
    (tmp_path / "extracted.json").write_text('{"sections":[]}', encoding="utf-8")
    (tmp_path / "transition-spec.json").write_text('{"transitions":[]}', encoding="utf-8")

    subprocess.run(["bash", str(PLAN), str(tmp_path)], check=True, cwd=ROOT)

    payload = json.loads((tmp_path / "verification-plan.json").read_text(encoding="utf-8"))
    rows = [
        row
        for value in payload.values()
        if isinstance(value, list)
        for row in value
        if isinstance(row, dict) and row.get("id") == "preview-runtime-health"
    ]

    assert len(rows) == 1
    assert rows[0]["produces"] == "preview-runtime-health.json"
    assert rows[0]["severity"] == "block"
    assert rows[0].get("dependsOn") == ["runtime-env"]


def test_run_required_checks_can_dispatch_preview_runtime_health() -> None:
    text = (ROOT / "scripts" / "verify" / "build_required_dispatch.py").read_text(encoding="utf-8")

    assert '"preview-runtime-health-check.sh":' in text
    assert "{session}-prh {ref_url} {impl_url} {ref_dir}" in text


def test_preview_runtime_health_script_contract() -> None:
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)

    text = SCRIPT.read_text(encoding="utf-8")
    assert "preview-runtime-health.json" in text
    assert "document.documentElement.scrollWidth" in text
    assert "headAssetOnReferenceOrigin" in text
    assert "scrollTransitionParity" in text
    assert "agent-browser" in text
    assert "run_with_timeout.py" in text


def _run_scroll_probe(tmp_path: Path, nodes: list[dict[str, object]], scroll_target: int = 120) -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node is required to exercise the embedded preview-runtime probe")

    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("  const visible = (el) => {")
    end = source.index("  const maxScroll =")
    probe_functions = source[start:end]
    fixture_path = tmp_path / "nodes.json"
    fixture_path.write_text(json.dumps(nodes), encoding="utf-8")

    harness = f"""
const fs = require('fs');
const specs = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const scrollTarget = Number(process.argv[2]);
let activeY = 0;
const styleAt = (spec) => {{
  const byY = spec.styleByY || {{}};
  const yStyle = byY[String(activeY)] || {{}};
  return Object.assign({{
    display: 'block',
    visibility: 'visible',
    opacity: '1',
    position: 'static',
    transform: 'none',
    top: 'auto',
    backgroundColor: 'rgba(0, 0, 0, 0)',
    boxShadow: 'none',
    color: 'rgb(0, 0, 0)',
  }}, spec.style || {{}}, yStyle);
}};
const rectAt = (spec) => {{
  const byY = spec.rectByY || {{}};
  const r = byY[String(activeY)] || spec.rect || {{}};
  return Object.assign({{ width: 0, height: 0 }}, r);
}};
const elements = specs.map((spec) => ({{
  _spec: spec,
  tagName: String(spec.tag || 'div').toUpperCase(),
  id: spec.id || '',
  className: spec.className || '',
  getAttribute(name) {{
    if (name === 'role') return spec.role || null;
    return spec.attrs && Object.prototype.hasOwnProperty.call(spec.attrs, name) ? spec.attrs[name] : null;
  }},
  getBoundingClientRect() {{
    return rectAt(spec);
  }},
}}));
for (let i = 0; i < elements.length; i += 1) {{
  const parentIndex = specs[i].parentIndex;
  elements[i].parentElement = Number.isInteger(parentIndex) ? elements[parentIndex] : null;
}}
global.Element = function Element() {{}};
global.Event = function Event(type) {{ this.type = type; }};
global.getComputedStyle = (el) => styleAt(el._spec);
const matchesProbeSelector = (el, selector) => {{
  const spec = el._spec;
  const tag = el.tagName.toLowerCase();
  const id = String(el.id || '').toLowerCase();
  const cls = String(el.className || '').toLowerCase();
  const role = String(spec.role || '').toLowerCase();
  return (
    tag === 'header' ||
    (selector.includes('nav') && tag === 'nav') ||
    (selector.includes('[role="banner"]') && role === 'banner') ||
    (selector.includes('[role="navigation"]') && role === 'navigation') ||
    (selector.includes('header" i') && (id.includes('header') || cls.includes('header'))) ||
    (selector.includes('nav" i') && (id.includes('nav') || cls.includes('nav')))
  );
}};
global.window = {{
  scrollY: 0,
  scrollTo(_x, y) {{
    activeY = Math.round(y);
    this.scrollY = activeY;
  }},
  dispatchEvent() {{}},
}};
global.document = {{
  documentElement: {{ className: '' }},
  body: {{ className: '', firstElementChild: elements[0] || null }},
  querySelectorAll(selector) {{
    return elements.filter((el) => matchesProbeSelector(el, selector));
  }},
}};
const sleep = async () => {{}};
{probe_functions}
(async () => {{
  const atTop = await captureScrollState(0);
  const atScroll = await captureScrollState(scrollTarget);
  const signature = (state) => JSON.stringify({{ root: state.root, headers: state.headers }});
  console.log(JSON.stringify({{
    atTop,
    atScroll,
    mutates: scrollTarget > 0 && signature(atTop) !== signature(atScroll),
  }}));
}})().catch((err) => {{
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
}});
"""
    proc = subprocess.run(
        ["node", "-e", harness, str(fixture_path), str(scroll_target)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return cast("dict[str, Any]", json.loads(proc.stdout))


def test_probe_serializes_computed_top_so_top_only_header_motion_mutates(tmp_path: Path) -> None:
    result = _run_scroll_probe(
        tmp_path,
        [
            {
                "tag": "header",
                "rect": {"width": 390, "height": 64},
                "style": {"position": "fixed"},
                "styleByY": {
                    "0": {"top": "56px"},
                    "120": {"top": "20px"},
                },
            }
        ],
    )

    assert result["atTop"]["headers"][0]["top"] == "56px"
    assert result["atScroll"]["headers"][0]["top"] == "20px"
    assert result["mutates"] is True


def test_probe_samples_fixed_or_sticky_chrome_roots_despite_stale_generic_visibility(
    tmp_path: Path,
) -> None:
    result = _run_scroll_probe(
        tmp_path,
        [
            {
                "tag": "header",
                "rect": {"width": 0, "height": 0},
                "style": {"display": "none", "position": "fixed", "top": "64px"},
            },
            {
                "tag": "div",
                "role": "navigation",
                "id": "site-navigation",
                "rect": {"width": 0, "height": 0},
                "style": {"display": "none", "position": "sticky", "top": "20px"},
            },
            {
                "tag": "nav",
                "className": "site-nav",
                "rect": {"width": 0, "height": 0},
                "style": {"display": "none", "position": "fixed", "top": "32px"},
            },
        ],
    )

    headers = result["atTop"]["headers"]
    assert [item["tag"] for item in headers] == ["header", "div", "nav"]
    assert [item["top"] for item in headers] == ["64px", "20px", "32px"]


def test_probe_does_not_promote_hidden_arbitrary_navish_descendants(tmp_path: Path) -> None:
    result = _run_scroll_probe(
        tmp_path,
        [
            {
                "tag": "main",
                "rect": {"width": 390, "height": 700},
            },
            {
                "tag": "div",
                "className": "footer-nav-item",
                "parentIndex": 0,
                "rect": {"width": 0, "height": 0},
                "style": {"display": "none", "position": "fixed", "top": "0px"},
            },
        ],
    )

    assert result["atTop"]["headers"] == []
    assert result["mutates"] is False


def test_probe_does_not_promote_hidden_class_named_nav_or_header_divs(tmp_path: Path) -> None:
    result = _run_scroll_probe(
        tmp_path,
        [
            {
                "tag": "div",
                "className": "primary-nav",
                "rect": {"width": 0, "height": 0},
                "style": {"display": "none", "position": "fixed", "top": "0px"},
            },
            {
                "tag": "div",
                "className": "global-nav",
                "rect": {"width": 0, "height": 0},
                "style": {"display": "none", "position": "sticky", "top": "0px"},
            },
            {
                "tag": "div",
                "className": "main-header",
                "rect": {"width": 0, "height": 0},
                "style": {"display": "none", "position": "fixed", "top": "0px"},
            },
        ],
    )

    assert result["atTop"]["headers"] == []
    assert result["mutates"] is False


def test_probe_does_not_promote_hidden_semantic_descendant_nav_roots(tmp_path: Path) -> None:
    result = _run_scroll_probe(
        tmp_path,
        [
            {
                "tag": "nav",
                "className": "footer-nav-item",
                "rect": {"width": 0, "height": 0},
                "style": {"display": "none", "position": "fixed", "top": "0px"},
            },
            {
                "tag": "div",
                "role": "navigation",
                "className": "subnav descendant",
                "rect": {"width": 0, "height": 0},
                "style": {"display": "none", "position": "sticky", "top": "0px"},
            },
        ],
    )

    assert result["atTop"]["headers"] == []
    assert result["mutates"] is False


def test_probe_keeps_motionless_fixed_header_mutates_false(tmp_path: Path) -> None:
    result = _run_scroll_probe(
        tmp_path,
        [
            {
                "tag": "header",
                "rect": {"width": 390, "height": 64},
                "style": {"position": "fixed", "top": "56px"},
            }
        ],
    )

    assert result["atTop"]["headers"][0]["top"] == "56px"
    assert result["atScroll"]["headers"][0]["top"] == "56px"
    assert result["mutates"] is False


def test_preview_runtime_probe_heredoc_survives_shell_boundary(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture_path = tmp_path / "probe.js"
    fake_agent_browser = fake_bin / "agent-browser"
    fake_agent_browser.write_text(
        f"""#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

capture_path = Path({str(capture_path)!r})
args = sys.argv[1:]
if "eval" in args:
    capture_path.write_text(sys.stdin.read(), encoding="utf-8")
    print(json.dumps({{
        "url": "http://preview.test/",
        "viewport": {{"width": 390, "height": 844}},
        "origins": {{"page": "http://preview.test", "reference": "http://ref.test"}},
        "headAssets": [],
        "suspectHeadAssets": [],
        "layout": {{
            "scrollWidth": 390,
            "scrollHeight": 1200,
            "viewportWidth": 390,
            "viewportHeight": 844,
            "overflowPx": 0,
            "overflowElements": [],
        }},
        "scrollTransition": {{
            "scrollTarget": 120,
            "mutates": False,
            "atTop": {{"headers": []}},
            "atScroll": {{"headers": []}},
        }},
    }}))
sys.exit(0)
""",
        encoding="utf-8",
    )
    fake_agent_browser.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "PREVIEW_RUNTIME_HEALTH_VIEWPORTS": "390x844",
        "PREVIEW_RUNTIME_HEALTH_WAIT_MS": "0",
        "PREVIEW_RUNTIME_HEALTH_AGENT_BROWSER_TIMEOUT_SEC": "5",
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT), "shell-boundary", "http://ref.test/", "http://impl.test/", str(tmp_path / "ref")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "bad substitution" not in proc.stderr.lower()
    probe = capture_path.read_text(encoding="utf-8")
    assert "scrollTransition" in probe
    assert "top: cs.top" in probe
