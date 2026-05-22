#!/usr/bin/env bash
# ref-js-loader-check.sh — fail when the impl loads the ref site's
# JavaScript bundle at runtime to fake behavior it should have rebuilt.
#
# Usage:
#   ref-js-loader-check.sh <ref-dir> <impl-root> [<impl-url>]
#
#
#   <script src="https://realfood.gov/_next/static/chunks/main-X.js" />
#   import "https://example.com/ref-bundle.js"  // component-side
#   const mod = await import("/public/ref-vendor.js")
#
# Detection logic (static scan of impl source + optional runtime probe):
#   1. Extract every plausible ref host from ref artifacts:
#      - head.json's URL field
#      - bundle-map.json's url / hosts fields
#      - external-sdks.json's signature hosts
#      - any *.json under ref-dir that has "host" or "origin" fields
#   2. Scan impl source tree (src/, app/, public/) for any string match
#      of those hosts in <script>, <link>, fetch(), import(), or
#      url-string contexts.
#   3. If <impl-url> is provided AND agent-browser is available, also
#      open the impl page, scan `performance.getEntriesByType("resource")`
#      for requests whose URL host matches a ref host.
#   4. FAIL if any match: this is a clear cheat — the impl is depending
#      on the ref's compiled output to fake the runtime.
#
# Same-origin assets (./relative paths, /static/, /_next/static/ on the
# IMPL host) are NOT flagged — those are legitimate bundler output. Only
# absolute URLs whose host matches the ref are flagged.
#
# Writes:
#   <ref-dir>/ref-js-loader.json
#
# Exit 0 on pass/skip, 1 on ref-host references found, 2 on setup error.

set -uo pipefail

REF_DIR="${1:?Usage: ref-js-loader-check.sh <ref-dir> <impl-root> [<impl-url>]}"
IMPL_ROOT="${2:?impl-root required}"
IMPL_URL="${3:-}"

[ -d "$REF_DIR" ]   || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }
[ -d "$IMPL_ROOT" ] || { echo "impl-root not found: $IMPL_ROOT" >&2; exit 2; }

OUT="$REF_DIR/ref-js-loader.json"
RUNTIME_SESSION="ref-js-loader-$$"
RUNTIME_RAW=""

cleanup() {
  [ -n "$RUNTIME_RAW" ] && rm -f "$RUNTIME_RAW"
  if [ -n "${RUNTIME_SESSION:-}" ]; then
    agent-browser --session "$RUNTIME_SESSION" close >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# Optional runtime probe
if [ -n "$IMPL_URL" ] && command -v agent-browser >/dev/null 2>&1; then
  RUNTIME_RAW=$(mktemp -t ref-js-runtime.XXXX.json)
  agent-browser --session "$RUNTIME_SESSION" open "$IMPL_URL" --wait 2000 >/dev/null 2>&1 || true
  agent-browser --session "$RUNTIME_SESSION" eval '
(() => {
  const hosts = new Set();
  const collect = (url) => {
    try {
      const u = new URL(url, location.href);
      if (u.host && u.host !== location.host) hosts.add(u.host + "|" + u.name || u.href);
    } catch (_) {}
  };
  (performance.getEntriesByType("resource") || []).forEach((e) => collect(e.name));
  document.querySelectorAll("iframe").forEach((el) => { if (el.src) collect(el.src); });
  document.querySelectorAll("link[rel=stylesheet], link[as=style]").forEach((el) => {
    if (el.href) collect(el.href);
  });
  return JSON.stringify({ resources: [...hosts].slice(0, 200) });
})()
' > "$RUNTIME_RAW" 2>/dev/null || true
fi

python3 - "$REF_DIR" "$IMPL_ROOT" "$OUT" "${RUNTIME_RAW:-}" "$IMPL_URL" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ref_dir, impl_root, out_path, runtime_raw, impl_url = sys.argv[1:6]
ref_dir_p = Path(ref_dir)
impl_root_p = Path(impl_root)
out_p = Path(out_path)

# ── Collect candidate ref hosts ───────────────────────────────────────
ref_hosts: set[str] = set()

def add_host(url_or_host: str | None) -> None:
    if not url_or_host:
        return
    s = url_or_host.strip()
    if not s:
        return
    if "://" in s:
        try:
            p = urlparse(s)
            if p.netloc:
                ref_hosts.add(p.netloc.lower())
                return
        except Exception:
            pass
    # Looks like a bare host
    if "." in s and " " not in s and len(s) < 200:
        ref_hosts.add(s.lower())

# Pull from common artifact shapes
for name in ("head.json", "bundle-map.json", "external-sdks.json", "extracted.json"):
    p = ref_dir_p / name
    if not p.exists():
        continue
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    # Recurse to find any "url" / "host" / "origin" string fields
    stack: list = [data]
    seen = 0
    while stack and seen < 5000:
        node = stack.pop()
        seen += 1
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and k.lower() in ("url", "host", "origin", "src", "href"):
                    add_host(v)
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)

SHARED_CDN_HOSTS = {
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "ajax.googleapis.com",
    "use.typekit.net",
    "use.fontawesome.com",
    "cdn.fontshare.com",
    "rsms.me",
    "ka-f.fontawesome.com",
    "code.jquery.com",
    "cdn.tailwindcss.com",
    "esm.sh",
    "deno.land",
    "vercel.live",
    "vitals.vercel-insights.com",
    "vercel-analytics.com",
    "googletagmanager.com",
    "www.googletagmanager.com",
    "google-analytics.com",
    "www.google-analytics.com",
}

# Drop generic / localhost / impl-side / shared-CDN hosts
def is_ref_like(host: str) -> bool:
    h = host.lower().strip()
    if h.startswith("localhost") or h.startswith("127.") or h.startswith("0."):
        return False
    if h.startswith("192.168.") or h.startswith("10.") or h.startswith("172."):
        return False
    if not h or len(h) < 4:
        return False
    if h in SHARED_CDN_HOSTS:
        return False
    # *.vercel.app preview deploys, *.netlify.app preview deploys, etc.
    # are typically the impl's own staging host or unrelated services —
    # not the ref-OWNED bundle we're targeting.
    suffix_skips = (".vercel.app", ".netlify.app", ".pages.dev", ".workers.dev")
    if any(h.endswith(suf) for suf in suffix_skips):
        return False
    return True

ref_hosts = {h for h in ref_hosts if is_ref_like(h)}

if not ref_hosts:
    out_p.write_text(json.dumps({
        "schemaVersion": 1,
        "status": "skip",
        "refHosts": [],
        "violations": [],
        "reasons": ["no ref host candidates found in ref artifacts — gate skipped"],
        "rule": (
            "When ref artifacts name external hosts, the impl source and runtime must "
            "not load JavaScript from those hosts. Loading ref JS bundles to fake runtime "
            "is a documented cheat (see SKILL.md Tier 5 no-cheat rule)."
        ),
    }, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status": "skip", "out": str(out_p)}))
    sys.exit(0)

# ── Scan impl source tree ─────────────────────────────────────────────
SCAN_DIRS = [impl_root_p / d for d in ("src", "app", "public", "pages", "components")]
SCAN_DIRS = [d for d in SCAN_DIRS if d.exists()]

SOURCE_EXT = {".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs", ".html", ".css", ".scss", ".json"}

ALLOWED_ASSET_EXT = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".webm", ".mp3", ".ogg", ".wav", ".m4a",
)
DENY_MARKERS = (
    "<script", "src=", "import(", "import ", "from '",
    'from "', "<link", "rel=stylesheet", '<iframe',
    "stylesheet", "loadStylesheet",
)
ALLOW_MARKERS = (
    "<img", "<video", "<audio", "<source",
    "background-image", "src-set", "srcSet=",
)

def classify_line(snippet: str) -> tuple[bool, str]:
    """Return (is_cheat, reason). is_cheat=True only when the line
    looks like a script/style/iframe/import — not when it looks like
    an image/font/video asset reference.
    """
    s = snippet.lower()
    # If the URL in the snippet ends in an allowed asset extension, it's
    # almost certainly an asset reference, not a bundle drop.
    for ext in ALLOWED_ASSET_EXT:
        if ext in s:
            # But not if it's inside a script/link/iframe marker
            if any(d in s for d in DENY_MARKERS) and not any(a in s for a in ALLOW_MARKERS):
                return True, "deny-marker overrides asset-ext heuristic"
            return False, "asset reference (image/font/video) — allowed"
    if any(a in s for a in ALLOW_MARKERS):
        return False, "asset markup tag — allowed"
    if any(d in s for d in DENY_MARKERS):
        return True, "script/stylesheet/iframe/import marker found"
    # Ambiguous: bare URL with no marker. Conservative: flag as cheat.
    return True, "ambiguous bare URL — treated as cheat"

violations: list[dict] = []
files_scanned = 0

for d in SCAN_DIRS:
    for path in d.rglob("*"):
        if path.is_dir():
            continue
        if path.suffix.lower() not in SOURCE_EXT:
            continue
        if "node_modules" in path.parts or ".next" in path.parts:
            continue
        files_scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for host in ref_hosts:
            if host in text:
                # Find the line / context for the report
                m = re.search(re.escape(host), text)
                if not m:
                    continue
                start = text.rfind("\n", 0, m.start()) + 1
                end = text.find("\n", m.end())
                if end == -1:
                    end = len(text)
                line_no = text.count("\n", 0, m.start()) + 1
                snippet = text[start:end].strip()[:200]
                is_cheat, reason = classify_line(snippet)
                if not is_cheat:
                    continue  # allowed asset reference, not a cheat
                violations.append({
                    "host": host,
                    "file": str(path.relative_to(impl_root_p)),
                    "line": line_no,
                    "snippet": snippet,
                    "reason": reason,
                })

# ── Runtime probe (optional) ──────────────────────────────────────────
runtime_resources: list[str] = []
if runtime_raw:
    rt = Path(runtime_raw)
    if rt.exists():
        try:
            text = rt.read_text(encoding="utf-8")
            for line in reversed(text.strip().splitlines()):
                s = line.strip()
                if not s.startswith("{"):
                    continue
                try:
                    val = json.loads(s)
                    if isinstance(val, str):
                        val = json.loads(val)
                    if isinstance(val, dict):
                        runtime_resources = val.get("resources") or []
                        break
                except Exception:
                    continue
        except Exception:
            pass

runtime_violations: list[dict] = []
for entry in runtime_resources:
    if "|" not in entry:
        continue
    rhost, url = entry.split("|", 1)
    rhost = rhost.lower()
    if rhost not in ref_hosts:
        continue
    url_lower = url.lower()
    if any(url_lower.endswith(ext) or (ext + "?") in url_lower for ext in ALLOWED_ASSET_EXT):
        continue
    runtime_violations.append({"host": rhost, "url": url[:200]})

# ── Verdict ───────────────────────────────────────────────────────────
total_violations = len(violations) + len(runtime_violations)
if total_violations == 0:
    status = "pass"
    reasons = [
        f"no ref-host references found across {files_scanned} impl source file(s)"
    ]
else:
    status = "fail"
    reasons = []
    if violations:
        sample = violations[:5]
        reasons.append(
            f"{len(violations)} impl source reference(s) to ref host(s) (cheat: "
            "loading ref JS bundle directly). Examples: "
            + "; ".join(f"{v['file']}:{v['line']} → {v['host']}" for v in sample)
        )
    if runtime_violations:
        sample = runtime_violations[:5]
        reasons.append(
            f"{len(runtime_violations)} runtime resource(s) loaded from ref host(s). "
            "Examples: " + "; ".join(v["url"] for v in sample)
        )

payload = {
    "schemaVersion": 1,
    "status": status,
    "refHosts": sorted(ref_hosts),
    "filesScanned": files_scanned,
    "violations": violations[:50],   # cap to keep artifact small
    "runtimeViolations": runtime_violations[:50],
    "runtimeProbeRan": bool(runtime_raw),
    "implUrl": impl_url,
    "reasons": reasons,
    "nextAction": (
        "Remove the impl source's imports / runtime fetches from the ref's "
        "host. Replace ref-bundle behavior by rebuilding it in the impl tree "
        "(use the ref's compiled output as a SPEC, not a runtime dependency). "
        "Loading the ref's compiled JS / CSS / iframing the ref site is the "
        "Tier 5 'outsource fidelity' cheat this gate blocks."
        if (status == "fail") else "no ref-host references — impl rebuilds behavior locally"
    ),
    "rule": (
        "Impl source and runtime must not reference any host listed in ref "
        "artifacts (head.json, bundle-map.json, external-sdks.json, extracted.json). "
        "This catches Tier 5 cheats where impl outsources fidelity by loading the "
        "ref's compiled JS bundle, CSS bundle, or embedding the ref site via iframe. "
        "Same-origin paths (./relative, /static/) are not flagged. Shared CDN hosts "
        "(Google Fonts, jsDelivr, Vercel Analytics, etc.) are allowlisted because "
        "both ref and impl may legitimately use them."
    ),
}

out_p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": status, "violations": total_violations, "out": str(out_p)}, ensure_ascii=False))
sys.exit({"pass": 0, "skip": 0, "fail": 1}.get(status, 2))
PY
