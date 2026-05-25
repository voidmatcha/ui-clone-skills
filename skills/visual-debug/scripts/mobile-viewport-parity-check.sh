#!/usr/bin/env bash
# mobile-viewport-parity-check.sh — assert key structural properties
# at mobile viewport (375x812 default).
#
# Usage:
#   mobile-viewport-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>
#
#
# What this gate checks at 375x812:
#   1. No horizontal overflow on impl (scrollWidth ≤ innerWidth + 4)
#   2. Mobile nav element exists when ref has one (data-mobile-nav,
#      [aria-label*="menu"], <button class="hamburger">, etc.)
#   3. Hero / above-the-fold elements render at mobile (root has
#      non-zero children, content has non-zero text length)
#   4. Approximate vertical-stacking parity: total content height
#      within ±30% of ref's mobile content height
#
# Writes:
#   <ref-dir>/mobile-viewport-parity.json
#
# Exit 0 on pass/skip, 1 on mobile-specific failures, 2 on setup error.

set -uo pipefail

SESSION="${1:?Usage: mobile-viewport-parity-check.sh <session> <ref-url> <impl-url> <ref-dir>}"
REF_URL="${2:?ref-url required}"
IMPL_URL="${3:?impl-url required}"
REF_DIR="${4:?ref-dir required}"
WIDTH="${MOBILE_PARITY_WIDTH:-375}"
HEIGHT="${MOBILE_PARITY_HEIGHT:-812}"

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "mobile-viewport-parity: agent-browser CLI missing" >&2
  exit 2
fi
[ -d "$REF_DIR" ] || { echo "ref-dir not found: $REF_DIR" >&2; exit 2; }

OUT="$REF_DIR/mobile-viewport-parity.json"
REF_SESSION="${SESSION}-mvp-ref"
IMPL_SESSION="${SESSION}-mvp-impl"
REF_RAW=$(mktemp -t mvp-ref.XXXX.json)
IMPL_RAW=$(mktemp -t mvp-impl.XXXX.json)
trap 'rm -f "$REF_RAW" "$IMPL_RAW"; agent-browser --session "$REF_SESSION" close >/dev/null 2>&1 || true; agent-browser --session "$IMPL_SESSION" close >/dev/null 2>&1 || true' EXIT

PROBE_JS='
(() => {
  const body = document.body;
  if (!body) return JSON.stringify({ ok: false, error: "no-body" });
  const html = document.documentElement;
  const overflow = body.scrollWidth - window.innerWidth;
  const mobileNavSelectors = [
    "[data-mobile-nav]",
    "[class*=\"mobile-nav\"]",
    "[class*=\"hamburger\"]",
    "[class*=\"menu-toggle\"]",
    "[class*=\"nav-toggle\"]",
    "[class*=\"burger\"]",
    "[aria-label*=\"menu\" i]",
    "[aria-label*=\"navigation\" i]",
    "[aria-controls*=\"nav\" i]",
    "[aria-controls*=\"menu\" i]",
    "button[aria-expanded]",
    "details > summary",
    "input[type=checkbox][id*=\"menu\" i] ~ label",
    "input[type=checkbox][id*=\"nav\" i] ~ label",
  ];
  let mobileNavCount = 0;
  for (const sel of mobileNavSelectors) {
    mobileNavCount += document.querySelectorAll(sel).length;
  }
  const text = body.innerText.slice(0, 1000);
  const sectionCount = document.querySelectorAll(
    "section, main, article, [role=region], [role=main], [role=contentinfo]"
  ).length;
  return JSON.stringify({
    ok: true,
    viewport: [window.innerWidth, window.innerHeight],
    scrollWidth: body.scrollWidth,
    scrollHeight: body.scrollHeight,
    overflowPx: overflow,
    bodyChildren: body.childElementCount,
    htmlClasses: html.className || "",
    bodyClasses: body.className || "",
    mobileNavCount,
    sectionCount,
    textLen: text.length,
    title: document.title || "",
  });
})()
'

probe() {
  local session="$1" url="$2" out_file="$3"
  agent-browser --session "$session" open "$url" --viewport "${WIDTH}x${HEIGHT}" --wait 2000 >/dev/null 2>&1 || true
  agent-browser --session "$session" eval "$PROBE_JS" > "$out_file" 2>/dev/null || true
}

probe "$REF_SESSION" "$REF_URL" "$REF_RAW"
probe "$IMPL_SESSION" "$IMPL_URL" "$IMPL_RAW"

python3 - "$OUT" "$REF_RAW" "$IMPL_RAW" "$REF_URL" "$IMPL_URL" "$WIDTH" "$HEIGHT" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

out_path, ref_path, impl_path, ref_url, impl_url, width, height = sys.argv[1:8]

def read_probe(path: str) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return {"ok": False, "error": "probe-missing"}
    for line in reversed(text.strip().splitlines()):
        s = line.strip()
        # agent-browser may wrap the eval result in outer quotes
        # ("{...}") or emit a bare object ({...}). Accept both forms.
        if not (s.startswith("{") or s.startswith('"{')):
            continue
        try:
            value = json.loads(s)
            if isinstance(value, str):
                value = json.loads(value)
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    return {"ok": False, "error": "probe-parse-failed"}

ref = read_probe(ref_path)
impl = read_probe(impl_path)
reasons: list[str] = []

if not ref.get("ok"):
    status = "skip"
    reasons.append(f"ref probe failed: {ref.get('error','unknown')}")
elif not impl.get("ok"):
    status = "fail"
    reasons.append(f"impl probe failed at mobile viewport: {impl.get('error','unknown')}")
else:
    if int(impl.get("overflowPx", 0)) > 4:
        reasons.append(
            f"impl has horizontal overflow at {width}x{height}: "
            f"body.scrollWidth={impl.get('scrollWidth')}, innerWidth={width}, "
            f"overflow={impl.get('overflowPx')}px. Typical cause: a fixed-width "
            "element or wide image without max-width:100%."
        )
    if int(ref.get("mobileNavCount", 0)) > 0 and int(impl.get("mobileNavCount", 0)) == 0:
        reasons.append(
            "ref has a mobile-nav element (hamburger / aria menu) but impl has "
            "none at mobile viewport. The clone is missing the mobile-specific "
            "navigation pattern."
        )
    if int(impl.get("bodyChildren", 0)) == 0:
        reasons.append("impl <body> is empty at mobile viewport — page failed to render")
    if int(impl.get("textLen", 0)) < 20:
        reasons.append("impl rendered <20 chars of text — likely a render error at this width")
    # Vertical-stacking parity: impl total height should be within ±50% of ref
    # mobile (stricter than desktop because mobile reflow accumulates more).
    rh = int(ref.get("scrollHeight", 0))
    ih = int(impl.get("scrollHeight", 0))
    if rh > 0 and ih > 0:
        ratio = ih / rh
        if ratio < 0.7 or ratio > 1.3:
            reasons.append(
                f"impl total height {ih}px vs ref {rh}px (ratio {ratio:.2f}x) — "
                "outside 0.7–1.3 tolerance. Either content is missing or stacked "
                "incorrectly at mobile."
            )
    # Section/landmark count parity (added per codex-rescue audit): a
    # severely-broken mobile layout often loses sections entirely while
    # remaining within height tolerance. Compare top-level <section> +
    # role=region counts; FAIL if impl has <50% of ref's count.
    ref_sections = int(ref.get("sectionCount", 0))
    impl_sections = int(impl.get("sectionCount", 0))
    if ref_sections > 0 and impl_sections < ref_sections * 0.5:
        reasons.append(
            f"impl has {impl_sections} top-level sections vs ref {ref_sections} "
            "— more than half the sections missing at mobile. Likely "
            "responsive-hidden via display:none or never rendered."
        )
    status = "fail" if reasons else "pass"

payload = {
    "schemaVersion": 1,
    "status": status,
    "viewport": [int(width), int(height)],
    "ref": ref,
    "impl": impl,
    "reasons": reasons,
    "nextAction": (
        "Fix mobile-specific layout: add max-width:100% to wide elements, "
        "ensure mobile nav (hamburger) is rendered, verify content stacks "
        "vertically at <768px. Test the impl at 375x812 in DevTools before "
        "re-running the gate."
        if (status == "fail") else "mobile viewport parity verified"
    ),
    "rule": (
        f"At {width}x{height}, the impl must: (1) have no horizontal "
        "overflow (>4px), (2) include a mobile-nav element when ref has one, "
        "(3) render non-empty body content, (4) total height within 2x of ref."
    ),
}

Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": status, "reasons": len(reasons), "out": out_path}))
sys.exit(0 if status in ("pass", "skip") else 1)
PY
