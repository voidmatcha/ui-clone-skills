#!/usr/bin/env bash
# build-decode-receipt.sh — emit a single-file standalone HTML receipt
# summarising one decode/clone/verify trial against a live URL.
#
# Purpose:
#   Educational + shareable artifact. Captures, in one self-contained
#   HTML page (no external assets, no relative paths):
#     - target URL + extraction timestamp
#     - chosen metaphor (motion forensics) header
#     - detected motion engine + libraries (from external-sdks.json)
#     - gate verdicts (bundle-paste, html-paste, ref-screenshot-asset,
#       proxy-mirror, font-parity, transition-spec, etc.)
#     - AE/SSIM section scores if present
#     - unclonable_reasons + fallback_suggestions if present (Step G)
#     - mandatory disclaimer: "Not affiliated with <site>. This is a
#       motion-forensics study artifact, not a substitute for the
#       original."
#
# Single file, embeds CSS + selected JSON inline (no external <script>
# / <link>). Inline images / video are NOT embedded — the receipt links
# to them by relative path (impl-side or ref-side) but stays standalone-
# usable when shared as a pastebin / static-host artifact.
#
# Usage:
#   bash build-decode-receipt.sh <ref-dir> [<output-html-path>]
#
# Default output: <repo>/outbox/<YYYY-MM-DD>/<ref-dir basename>/receipt.html
# Exit: 0 wrote receipt, 2 setup error.

set -uo pipefail

REF_DIR="${1:-}"
OUT_ARG="${2:-}"

if [ -z "$REF_DIR" ] || [ ! -d "$REF_DIR" ]; then
  echo "Usage: build-decode-receipt.sh <ref-dir> [<output-html-path>]" >&2
  exit 2
fi

# Resolve repo root for default outbox path.
REPO_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}}"
if [ -z "$REPO_ROOT" ] || [ ! -d "$REPO_ROOT" ]; then
  REPO_ROOT=$(cd "$(dirname "$0")/../../.." 2>/dev/null && pwd)
fi
if [ -z "$REPO_ROOT" ] || [ ! -d "$REPO_ROOT" ]; then
  echo "build-decode-receipt: cannot resolve repo root" >&2
  exit 2
fi

# Default output path.
COMPONENT=$(basename "$REF_DIR")
TODAY=$(date -u +"%Y-%m-%d")
if [ -z "$OUT_ARG" ]; then
  OUT_DIR="$REPO_ROOT/outbox/$TODAY/$COMPONENT"
  mkdir -p "$OUT_DIR"
  OUT_PATH="$OUT_DIR/receipt.html"
else
  OUT_PATH="$OUT_ARG"
  mkdir -p "$(dirname "$OUT_PATH")"
fi

python3 - "$REF_DIR" "$OUT_PATH" "$COMPONENT" <<'PY'
from __future__ import annotations

import datetime
import html as _html
import json
import sys
from pathlib import Path

ref_dir = Path(sys.argv[1])
out_path = Path(sys.argv[2])
component = sys.argv[3]


def load_json(name: str) -> dict | None:
    p = ref_dir / name
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_text(name: str) -> str | None:
    p = ref_dir / name
    if not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def esc(s) -> str:
    return _html.escape(str(s), quote=True)


# ── Inputs ──
state = load_json("pipeline-state.json") or {}
sdks = load_json("external-sdks.json") or {}
transition_spec = load_json("transition-spec.json") or {}
bundle_paste = load_json("bundle-paste-check.json") or {}
html_paste = load_json("html-paste.json") or {}
ref_screenshot = load_json("ref-screenshot-asset.json") or {}
proxy_mirror = load_json("proxy-mirror-check.json") or {}
verify_stamp = load_json("verify-stamp.json") or {}
# Task #11: section-staged plans satisfy Stop via structural-convergence-stamp.json
# (closeoutPolicy=structural in pipeline-state.json). Surface it alongside the
# canonical verify stamp so the receipt accurately reflects either closeout mode.
structural_stamp = load_json("structural-convergence-stamp.json") or {}
visual_debug = load_json("visual-debug-stamp.json") or {}
font_parity = load_json("font-parity.json") or {}

sections_result = read_text("sections/result.txt") or ""

# Site identity
target_url = (
    state.get("targetUrl")
    or state.get("ref_url")
    or state.get("orig_url")
    or "(URL not recorded in pipeline-state.json)"
)
try:
    from urllib.parse import urlparse
    site_label = urlparse(target_url).netloc or target_url
except Exception:
    site_label = target_url

# Motion library detection
sdk_libs: list[str] = []
if isinstance(sdks, dict):
    for k, v in sdks.items():
        if isinstance(v, dict) and v.get("matches", 0):
            sdk_libs.append(f"{k} ({v.get('matches')}×)")
        elif isinstance(v, list) and v:
            sdk_libs.append(f"{k} ({len(v)} hit)")

transition_count = 0
if isinstance(transition_spec, dict):
    arr = transition_spec.get("transitions")
    if isinstance(arr, list):
        transition_count = len(arr)

# Gate verdicts roll-up
def verdict_row(label: str, payload: dict | None) -> tuple[str, str, str]:
    """Return (label, status badge text, detail text)."""
    if not payload:
        return (label, "n/a", "artifact missing")
    status = payload.get("status", "?")
    reason = payload.get("reason", "")
    return (label, str(status), reason)


def structural_stamp_row(payload: dict | None) -> tuple[str, str, str]:
    """The structural stamp has different fields than canonical gate JSONs
    (closeoutKind, stage, sectionResult instead of status/reason). Render its
    own row so the receipt distinguishes "this run satisfied Stop via the
    convergence detector" from a missing canonical verify."""
    label = "structural-convergence-stamp"
    if not payload:
        return (label, "n/a", "artifact missing")
    kind = payload.get("closeoutKind", "?")
    stage = payload.get("stage")
    section_result = payload.get("sectionResult", "")
    stage_label = f"stage={stage}" if stage else "stage=?"
    detail = f"{stage_label} {section_result}".strip()
    return (label, str(kind), detail)


verdicts: list[tuple[str, str, str]] = [
    verdict_row("bundle-paste", bundle_paste),
    verdict_row("html-paste", html_paste),
    verdict_row("ref-screenshot-asset", ref_screenshot),
    verdict_row("proxy-mirror", proxy_mirror),
    verdict_row("font-parity", font_parity),
    verdict_row("visual-debug-stamp", visual_debug),
    verdict_row("verify-stamp (canonical)", verify_stamp),
    structural_stamp_row(structural_stamp),
]

# Unclonable + fallback suggestions (Step G payload, optional)
unclonable_rows: list[tuple[str, str, str, list[str]]] = []
reasons = state.get("unclonable_reasons", []) if isinstance(state, dict) else []
if isinstance(reasons, list):
    for r in reasons:
        if not isinstance(r, dict):
            continue
        gate = r.get("gate", "?")
        category = r.get("category", "?")
        reason = r.get("reason", "")
        fallbacks = r.get("fallback_suggestions", [])
        if not isinstance(fallbacks, list):
            fallbacks = []
        unclonable_rows.append((gate, category, reason, [str(x) for x in fallbacks]))

# ── HTML emit ──
now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

verdict_rows_html = "\n".join(
    f"  <tr><td>{esc(name)}</td><td class='v v-{esc(status)}'>{esc(status)}</td><td>{esc(detail)}</td></tr>"
    for name, status, detail in verdicts
)

unclonable_html = ""
if unclonable_rows:
    rows_html = []
    for gate, category, reason, fallbacks in unclonable_rows:
        fb_html = ""
        if fallbacks:
            fb_html = "<ul class='fallbacks'>" + "".join(
                f"<li>{esc(f)}</li>" for f in fallbacks
            ) + "</ul>"
        rows_html.append(
            f"<tr><td>{esc(gate)}</td><td>{esc(category)}</td>"
            f"<td>{esc(reason)}{fb_html}</td></tr>"
        )
    unclonable_html = (
        "<section><h2>Unclonable reasons + fallback suggestions</h2>"
        "<table class='unclonable'><thead><tr><th>Gate</th><th>Category</th>"
        "<th>Reason &amp; fallback</th></tr></thead><tbody>"
        + "".join(rows_html)
        + "</tbody></table></section>"
    )

sections_summary_html = ""
if sections_result.strip():
    # Show last 30 lines, escape, monospace.
    tail = "\n".join(sections_result.splitlines()[-30:])
    sections_summary_html = (
        "<section><h2>Section compare (tail)</h2>"
        f"<pre class='block'>{esc(tail)}</pre></section>"
    )

sdks_html = ", ".join(esc(s) for s in sdk_libs) if sdk_libs else "(none detected)"

html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Motion forensics receipt — {esc(site_label)}</title>
<style>
  :root {{
    color-scheme: light dark;
    --fg: #111;
    --muted: #555;
    --bg: #fafafa;
    --border: #ddd;
    --pass: #1f7a3f;
    --fail: #b00020;
    --skip: #888;
    --na:   #999;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg: #eee; --muted: #aaa; --bg: #111; --border: #333; }}
  }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--fg);
    font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    padding: 32px 24px;
    max-width: 980px;
    margin-inline: auto;
  }}
  header {{
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
    margin-bottom: 24px;
  }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .subtitle {{ color: var(--muted); font-size: 13px; }}
  h2 {{ font-size: 16px; margin: 28px 0 10px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{
    text-align: left; padding: 8px 10px;
    border-bottom: 1px solid var(--border); vertical-align: top;
  }}
  th {{ background: rgba(0,0,0,0.04); font-weight: 600; }}
  .v {{ font-weight: 600; text-transform: lowercase; }}
  .v-pass {{ color: var(--pass); }}
  .v-fail {{ color: var(--fail); }}
  .v-skip, .v-n\\/a {{ color: var(--skip); }}
  .summary {{
    background: rgba(0,0,0,0.03); padding: 12px 16px;
    border: 1px solid var(--border); border-radius: 6px;
    margin-bottom: 24px;
  }}
  .block {{
    background: rgba(0,0,0,0.05); padding: 12px 16px;
    border: 1px solid var(--border); border-radius: 6px;
    overflow: auto; white-space: pre; font-family: ui-monospace, monospace;
    font-size: 12px;
  }}
  .fallbacks {{ margin: 6px 0 0 16px; padding: 0; }}
  .disclaimer {{
    margin-top: 40px; padding: 16px; border: 1px dashed var(--border);
    border-radius: 6px; font-size: 12px; color: var(--muted);
  }}
  .meta {{ font-family: ui-monospace, monospace; font-size: 12px; color: var(--muted); }}
</style>
</head>
<body>
<header>
  <h1>Motion forensics receipt — {esc(site_label)}</h1>
  <div class="subtitle">
    Generated {esc(now_iso)} by ui-clone-skills.
    Component <code>{esc(component)}</code>.
  </div>
</header>

<section class="summary">
  <div><strong>Target URL:</strong> <code>{esc(target_url)}</code></div>
  <div><strong>Motion engines detected:</strong> {sdks_html}</div>
  <div><strong>Transition spec entries:</strong> {esc(transition_count)}</div>
</section>

<section>
  <h2>Gate verdicts</h2>
  <table>
    <thead><tr><th>Gate</th><th>Status</th><th>Reason</th></tr></thead>
    <tbody>
{verdict_rows_html}
    </tbody>
  </table>
</section>

{unclonable_html}

{sections_summary_html}

<div class="disclaimer">
  <strong>Not affiliated with {esc(site_label)}.</strong>
  This receipt is a motion-forensics study artifact produced by
  <a href="https://github.com/voidmatcha/ui-clone-skills">ui-clone-skills</a>.
  It documents the reverse-engineering analysis of publicly-accessible
  HTML/CSS/JS only — no proprietary content is bundled. The receipt is
  not a substitute for the original site and confers no endorsement by
  it.
</div>
</body>
</html>
"""

out_path.write_text(html_doc, encoding="utf-8")
print(f"build-decode-receipt: wrote {out_path}")
PY
EXIT=$?
exit $EXIT
