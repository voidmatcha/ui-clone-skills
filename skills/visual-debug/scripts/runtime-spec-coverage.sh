#!/usr/bin/env bash
# runtime-spec-coverage.sh — Enforce that transition-spec.json reflects the
# signal classes detected in animation-runtime-dump.json.
#
# Why this exists:
#   extract-animation-runtime.sh (Phase 0) captures runtime animation state
#   that bundle-grep and video phases cannot see — GSAP ScrollTrigger pixel
#   offsets, document.getAnimations() easings, Lenis config, Webflow IX2
#   timelines. transition-spec-rules.md Rule 7 says "consult this dump when
#   authoring transition-spec.json", but the advisory was unenforceable: an
#   agent could write a spec with zero scroll entries while the live page
#   runs 30 ScrollTrigger animations, and nothing in the pipeline would catch
#   it before code generation. This script makes the gap checkable.
#
# Coverage logic (class-level, not bijective):
#   - dump.scrollTrigger has ≥1 entry → spec must have ≥1 entry whose trigger
#     matches scroll/intersection/inview/enter-viewport/viewport/scrub, OR
#     whose type begins with scroll/reveal/intersection.
#   - dump.ix2.timelineCount > 0 → spec must have ≥1 entry (Webflow IX2 sites
#     drive everything off named timelines; an empty spec is a hard miss).
#
#   Bijective matching (every runtime tween → spec entry) is intentionally NOT
#   required. The spec is the impl plan, not a mirror — it may model ref's GSAP
#   timeline as a CSS keyframe, collapse multiple trigger entries into one, etc.
#   What is NEVER acceptable is missing the entire class.
#
# Usage:
#   bash runtime-spec-coverage.sh <component-dir>
#
# Output: <component-dir>/runtime-spec-coverage.json
#   { schemaVersion: 1, status: "pass" | "fail",
#     scrollTriggerCount, ix2TimelineCount, specEntryCount, missing: [...] }
#
# Exit: 0 = pass, 1 = coverage gap, 2 = setup error.

set -uo pipefail

COMP_DIR="${1:?Usage: runtime-spec-coverage.sh <component-dir>}"
DUMP="$COMP_DIR/animation-runtime-dump.json"
SPEC="$COMP_DIR/transition-spec.json"
OUT="$COMP_DIR/runtime-spec-coverage.json"

if [ ! -f "$DUMP" ]; then
  printf '%s\n' '{"schemaVersion":1,"status":"pass","note":"no runtime dump — nothing to enforce"}' > "$OUT"
  echo "Wrote $OUT (no animation-runtime-dump.json — skipped)"
  exit 0
fi

if [ ! -f "$SPEC" ]; then
  printf '%s\n' '{"schemaVersion":1,"status":"fail","missing":["transition-spec.json absent — cannot verify runtime coverage"]}' > "$OUT"
  echo "❌ transition-spec.json absent — cannot verify runtime coverage"
  exit 1
fi

if ! command -v node &>/dev/null; then
  echo "ERROR: node not found" >&2
  exit 2
fi

node -e '
const fs = require("fs");
let dump, spec;
try { dump = JSON.parse(fs.readFileSync(process.argv[1], "utf8")); }
catch (e) { console.error("animation-runtime-dump.json parse error: " + e.message); process.exit(2); }
try { spec = JSON.parse(fs.readFileSync(process.argv[2], "utf8")); }
catch (e) { console.error("transition-spec.json parse error: " + e.message); process.exit(2); }

const entries = Array.isArray(spec) ? spec
  : (Array.isArray(spec.transitions) ? spec.transitions
    : (Array.isArray(spec.entries) ? spec.entries : []));

const missing = [];

const stCount = Array.isArray(dump.scrollTrigger) ? dump.scrollTrigger.length : 0;
if (stCount > 0) {
  const hasScroll = entries.some(e => {
    const trig = String(e.trigger || "").toLowerCase();
    const type = String(e.type || (e.animation && e.animation.type) || "").toLowerCase();
    return /scroll|intersection|inview|enter-viewport|viewport|scrub/.test(trig)
        || /^(scroll|reveal|intersection)/.test(type);
  });
  if (!hasScroll) {
    missing.push(stCount + " ScrollTrigger entry(ies) detected at runtime but transition-spec has zero scroll/intersection entries — see animation-runtime-dump.json scrollTrigger[]");
  }
}

const ixCount = (dump.ix2 && typeof dump.ix2.timelineCount === "number") ? dump.ix2.timelineCount : 0;
if (ixCount > 0 && entries.length === 0) {
  missing.push(ixCount + " Webflow IX2 timeline(s) detected at runtime but transition-spec is empty — see animation-runtime-dump.json ix2");
}

const status = missing.length === 0 ? "pass" : "fail";
const out = {
  schemaVersion: 1,
  status,
  scrollTriggerCount: stCount,
  ix2TimelineCount: ixCount,
  specEntryCount: entries.length,
  missing
};
fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 2));
console.log("Wrote " + process.argv[3]);
if (missing.length === 0) {
  console.log("✅ runtime-spec coverage clean (scrollTrigger=" + stCount + " ix2=" + ixCount + " spec=" + entries.length + ")");
} else {
  console.log("❌ runtime-spec coverage gaps:");
  for (const m of missing) console.log("  - " + m);
}
process.exit(missing.length === 0 ? 0 : 1);
' "$DUMP" "$SPEC" "$OUT"
