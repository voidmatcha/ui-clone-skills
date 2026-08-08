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

# shellcheck source=../../../scripts/lib/viewport.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/scripts/lib/viewport.sh"

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
  const isVisible = (el) => {
    for (let node = el; node instanceof Element; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (
        style.display === "none" ||
        style.visibility === "hidden" ||
        style.visibility === "collapse" ||
        Number.parseFloat(style.opacity || "1") <= 0.01
      ) {
        return false;
      }
    }
    const rect = el.getBoundingClientRect();
    return rect.width >= 2 && rect.height >= 2;
  };
  const interactiveControlSelector =
    ":is(button, [role=button], summary, label)";
  const directMobileNavControlSelectors = [
    ":is(button, [role=button], summary, label)[data-mobile-nav]",
    ":is(button, [role=button], summary, label)[data-testid*=\"mobile-menu\" i]",
    ":is(button, [role=button], summary, label)[class*=\"mobile-nav\"]",
    ":is(button, [role=button], summary, label)[class*=\"hamburger\"]",
    ":is(button, [role=button], summary, label)[class*=\"menu-toggle\"]",
    ":is(button, [role=button], summary, label)[class*=\"nav-toggle\"]",
    ":is(button, [role=button], summary, label)[class*=\"burger\"]",
    ":is(button, [role=button])[aria-label*=\"menu\" i]",
    ":is(button, [role=button])[aria-label*=\"navigation\" i]",
    ":is(button, [role=button])[aria-controls*=\"nav\" i]",
    ":is(button, [role=button])[aria-controls*=\"menu\" i]",
    "details > summary",
    "input[type=checkbox][id*=\"menu\" i] ~ label",
    "input[type=checkbox][id*=\"nav\" i] ~ label",
  ];
  const mobileNavSelectors = [
    "[data-mobile-nav]",
    "[data-testid*=\"mobile-menu\" i]",
    "[class*=\"mobile-nav\"]",
    "[class*=\"hamburger\"]",
    "[class*=\"menu-toggle\"]",
    "[class*=\"nav-toggle\"]",
    "[class*=\"burger\"]",
    ...directMobileNavControlSelectors,
  ];
  const directMobileNavControlSelector =
    directMobileNavControlSelectors.join(",");
  const mobileNavCandidates = new Set(
    mobileNavSelectors.flatMap((selector) => [
      ...document.querySelectorAll(selector),
    ])
  );
  const mobileNavControls = new Set();
  for (const candidate of mobileNavCandidates) {
    if (!isVisible(candidate)) continue;
    if (candidate.matches(interactiveControlSelector)) {
      mobileNavControls.add(candidate);
      continue;
    }
    const explicitDescendants = [
      ...candidate.querySelectorAll(directMobileNavControlSelector),
    ].filter((el) => isVisible(el));
    if (explicitDescendants.length > 0) {
      explicitDescendants.forEach((el) => mobileNavControls.add(el));
      continue;
    }
    const visibleInteractiveDescendants = [
      ...candidate.querySelectorAll(interactiveControlSelector),
    ].filter((el) => isVisible(el));
    if (visibleInteractiveDescendants.length === 1) {
      mobileNavControls.add(visibleInteractiveDescendants[0]);
    }
  }
  const mobileNavCount = mobileNavControls.size;
  const text = body.innerText.slice(0, 1000);
  const landmarkGroups = [
    { category: "banner", selectors: ["header", "[role=banner]"] },
    { category: "navigation", selectors: ["nav", "[role=navigation]"] },
    { category: "main", selectors: ["main", "[role=main]"] },
    { category: "contentinfo", selectors: ["footer", "[role=contentinfo]"] },
    { category: "complementary", selectors: ["aside", "[role=complementary]"] },
    {
      category: "region",
      selectors: ["section", "article", "[role=region]"],
    },
  ];
  const landmarkSelector = landmarkGroups.flatMap((group) => group.selectors).join(",");
  const visibleLandmarks = new Set(
    [...document.querySelectorAll(landmarkSelector)].filter((el) => isVisible(el))
  );
  const categoryByElement = new Map();
  for (const group of landmarkGroups) {
    for (const el of visibleLandmarks) {
      if (
        !categoryByElement.has(el) &&
        group.selectors.some((selector) => el.matches(selector))
      ) {
        categoryByElement.set(el, group.category);
      }
    }
  }
  const stableLandmarks = [...visibleLandmarks].filter((el) => {
    const category = categoryByElement.get(el);
    for (let ancestor = el.parentElement; ancestor; ancestor = ancestor.parentElement) {
      if (visibleLandmarks.has(ancestor) && categoryByElement.get(ancestor) === category) {
        return false;
      }
    }
    return true;
  });
  const landmarkCounts = Object.fromEntries(
    landmarkGroups.map((group) => [
      group.category,
      stableLandmarks.filter((el) => categoryByElement.get(el) === group.category).length,
    ])
  );
  const landmarkCategories = landmarkGroups
    .filter((group) => landmarkCounts[group.category] > 0)
    .map((group) => group.category);
  const sectionCount = stableLandmarks.length;
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
    landmarkCategories,
    landmarkCounts,
    textLen: text.length,
    title: document.title || "",
  });
})()
'

probe() {
  local session="$1" url="$2" out_file="$3"
  if ! ab_open_at_viewport "$session" "$url" "$WIDTH" "$HEIGHT" 2; then
    echo "mobile-viewport-parity-check: cannot probe at declared viewport ${WIDTH}x${HEIGHT}; failing closed" >&2
    exit 1
  fi
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
    # Ref-relative overflow (batch-12 ITEM 6 achievability): the REFERENCE itself
    # may carry inherent horizontal overflow at mobile — e.g. realfood's
    # JS-positioned foods/pyramid strip extends to ~1151px (overflow ~776px),
    # clipped by overflow-x:clip. An ABSOLUTE "overflow>4px => fail" rule fails the
    # reference against its OWN ground truth (a gate so strict it cannot self-pass).
    # Fail only when the impl overflows MORE than the ref does (beyond a small
    # tolerance): a clone that ADDS overflow is a real defect, but faithfully
    # reproducing the ref's own overflow is parity (and gives ref-vs-ref self-pass).
    ref_overflow = int(ref.get("overflowPx", 0))
    impl_overflow = int(impl.get("overflowPx", 0))
    if impl_overflow > 4 and impl_overflow > ref_overflow + 4:
        reasons.append(
            f"impl has horizontal overflow at {width}x{height} beyond the ref: "
            f"body.scrollWidth={impl.get('scrollWidth')}, innerWidth={width}, "
            f"impl overflow={impl_overflow}px vs ref overflow={ref_overflow}px. "
            "Typical cause: a fixed-width element or wide image without "
            "max-width:100% that the ref does not have."
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
    ref_title = " ".join(str(ref.get("title", "")).split())
    impl_title = " ".join(str(impl.get("title", "")).split())
    if ref_title and impl_title != ref_title:
        reasons.append(
            f"impl document title differs from ref: {impl_title!r} vs "
            f"{ref_title!r}. Preserve the rendered page title copy instead of "
            "using a clone/debug label."
        )
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
    # Section/landmark count parity: a
    # severely-broken mobile layout often loses sections entirely while
    # remaining within height tolerance. Compare stable visible landmarks:
    # selector aliases resolve to one DOM element and same-category nesting is
    # collapsed, while sibling landmarks remain independently countable.
    ref_landmark_counts = ref.get("landmarkCounts", {})
    impl_landmark_counts = impl.get("landmarkCounts", {})
    if isinstance(ref_landmark_counts, dict) and isinstance(impl_landmark_counts, dict):
        core_categories = ("banner", "navigation", "main", "contentinfo")
        major_categories = (*core_categories, "complementary")
        ref_major_count = sum(
            int(ref_landmark_counts.get(category, 0))
            for category in major_categories
        )
        impl_major_count = sum(
            int(impl_landmark_counts.get(category, 0))
            for category in major_categories
        )
        if ref_major_count > 0 and impl_major_count < ref_major_count * 0.5:
            reasons.append(
                f"impl has {impl_major_count} stable major landmarks vs ref "
                f"{ref_major_count} — more than half the core page structure "
                "is missing at mobile. Likely responsive-hidden via "
                "display:none or never rendered."
            )
        for category in core_categories:
            ref_count = int(ref_landmark_counts.get(category, 0))
            impl_count = int(impl_landmark_counts.get(category, 0))
            if ref_count > 0 and impl_count == 0:
                reasons.append(
                    f"impl lost the core {category} landmark present in the ref "
                    "at mobile viewport."
                )
        for category in major_categories:
            raw_ref_count = ref_landmark_counts.get(category, 0)
            ref_count = int(raw_ref_count)
            impl_count = int(impl_landmark_counts.get(category, 0))
            if ref_count >= 3 and impl_count < ref_count * 0.5:
                reasons.append(
                    f"impl has {impl_count} stable {category} landmarks vs ref "
                    f"{ref_count} — major per-category landmark loss at mobile."
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
        "vertically at <768px, and preserve the reference document title. "
        "Test the impl at 375x812 in DevTools before re-running the gate."
        if (status == "fail") else "mobile viewport parity verified"
    ),
    "rule": (
        f"At {width}x{height}, the impl must: (1) have no horizontal "
        "overflow (>4px), (2) include a mobile-nav element when ref has one, "
        "(3) render non-empty body content, (4) preserve core landmarks and "
        "document title, (5) total height within the ref-relative tolerance."
    ),
}

Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"status": status, "reasons": len(reasons), "out": out_path}))
sys.exit(0 if status in ("pass", "skip") else 1)
PY
