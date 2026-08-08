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
#   - dump.gsapTimelines targets → spec must mention at least one captured
#     target selector/class/id, so non-ScrollTrigger GSAP motion is not lost.
#   - dump.customEaseRegistry keys used by runtime timelines → spec must carry
#     the exact ease key or curve data, not a guessed generic ease.
#
#   Bijective matching (every runtime tween → spec entry) is intentionally NOT
#   required. The spec is the impl plan, not a mirror — it may model ref's GSAP
#   timeline as a CSS keyframe, collapse multiple trigger entries into one, etc.
#   What is NEVER acceptable is missing the entire class.
#
# Usage:
#   bash runtime-spec-coverage.sh <component-dir> [impl-src]
#
# Output: <component-dir>/runtime-spec-coverage.json
#   { schemaVersion: 1, status: "pass" | "fail",
#     scrollTriggerCount, ix2TimelineCount, gsapTimelineCount,
#     customEaseCount, customEaseUsedCount, specEntryCount, missing: [...] }
#
# Exit: 0 = pass, 1 = coverage gap, 2 = setup error.

set -uo pipefail

COMP_DIR="${1:?Usage: runtime-spec-coverage.sh <component-dir>}"
DUMP="$COMP_DIR/animation-runtime-dump.json"
SPEC="$COMP_DIR/transition-spec.json"
OUT="$COMP_DIR/runtime-spec-coverage.json"
IMPL_SRC="${2:-}"

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
const path = require("path");
let dump, spec, generationPlan = {};
try { dump = JSON.parse(fs.readFileSync(process.argv[1], "utf8")); }
catch (e) { console.error("animation-runtime-dump.json parse error: " + e.message); process.exit(2); }
try { spec = JSON.parse(fs.readFileSync(process.argv[2], "utf8")); }
catch (e) { console.error("transition-spec.json parse error: " + e.message); process.exit(2); }
const generationPlanPath = path.join(path.dirname(process.argv[1]), "generation-plan.json");
if (fs.existsSync(generationPlanPath)) {
  try { generationPlan = JSON.parse(fs.readFileSync(generationPlanPath, "utf8")); }
  catch (_) { generationPlan = {}; }
}

const entries = Array.isArray(spec) ? spec
  : (Array.isArray(spec.transitions) ? spec.transitions
    : (Array.isArray(spec.entries) ? spec.entries : []));
const specText = JSON.stringify(spec);

const missing = [];
const warnings = [];

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

// Denominator reconciliation (loop-nvti-2 / fable review): every runtime
// motion check iterates SPEC entries, so the spec author controls the
// denominator of every downstream "N/N" claim. The class-level check above
// let 4 scroll entries immunize 22 uncovered div.page-stack triggers (85%
// of the census) — the bulk of the page scroll choreography shipped dead
// while every gate passed. Reconcile trigger GROUPS: every runtime trigger
// selector group must be referenced by a spec entry PLAN FIELD (target /
// trigger / id — prose notes do not count) or by a named skipped[] row.
// One state-machine entry may cover all of a group trip lines — the spec
// stays a plan, not a mirror — but silent omission becomes impossible.
const skippedRows = Array.isArray(spec.skipped) ? spec.skipped : [];
const planCorpus = [];
for (const e of entries) {
  for (const k of ["target", "selector", "trigger", "id"]) {
    if (typeof e[k] === "string" && e[k].trim()) planCorpus.push(e[k]);
  }
}
for (const s of skippedRows) {
  if (typeof s === "string") { planCorpus.push(s); continue; }
  if (!s || typeof s !== "object") continue;
  for (const k of ["selector", "target", "sourceId", "id", "reason"]) {
    if (typeof s[k] === "string" && s[k].trim()) planCorpus.push(s[k]);
  }
}
const planText = planCorpus.join("\n");
const loadImplementationSource = (root) => {
  if (!root || !fs.existsSync(root)) return "";
  const chunks = [];
  const visit = (path) => {
    let stat;
    try { stat = fs.statSync(path); } catch (_) { return; }
    if (stat.isDirectory()) {
      const name = path.split(/[\\/]/).pop();
      if ([".git", "node_modules", "dist", "build", "coverage"].includes(name)) return;
      for (const child of fs.readdirSync(path)) visit(path + "/" + child);
      return;
    }
    if (!/\.(?:[cm]?[jt]sx?|html?)$/i.test(path) || stat.size > 2_000_000) return;
    try { chunks.push(fs.readFileSync(path, "utf8")); } catch (_) {}
  };
  visit(root);
  return chunks.join("\n");
};
const implSourceText = loadImplementationSource(process.argv[4]);
const consumesSwiperProgress = /dataset\s*(?:\.\s*swiperProgress|\[\s*["\x27]swiperProgress["\x27]\s*\])/.test(implSourceText);
const swiperProgressTargets = new Set();
if (consumesSwiperProgress) {
  const attrPattern = /data-swiper-progress\s*=\s*(?:\{\s*)?(["\x27])([^"\x27]+)\1(?:\s*\})?/g;
  for (const match of implSourceText.matchAll(attrPattern)) {
    if (match[2].trim()) swiperProgressTargets.add(match[2].trim().replace(/\s+/g, " "));
  }
}
const hasSwiperParentPlan = entries.some((entry) => {
  if (!entry || typeof entry !== "object") return false;
  return ["target", "selector"].some((key) => {
    const value = String(entry[key] || "");
    return /(^|[\s>+~,(])\.swiper(?=$|[\s>+~.[#,:])/.test(value);
  });
});
const isSwiperRuntimeChild = (selector) => {
  const normalized = String(selector || "").trim().replace(/\s+/g, " ");
  return /^(?:[A-Za-z][\w-]*)?\.swiper-slide(?:\.swiper-slide-[\w-]+)*$/.test(normalized);
};
const isScrollScrubEntry = (entry) => {
  if (!entry || typeof entry !== "object") return false;
  const trigger = String(entry.trigger || "").toLowerCase();
  const type = String(entry.type || (entry.animation && entry.animation.type) || "").toLowerCase();
  const progress = String(entry.animation && entry.animation.progress || "").toLowerCase();
  const duration = String(entry.animation && entry.animation.duration || "").toLowerCase();
  return trigger.includes("scroll")
    && (/scrub|scroll-bound|use-scroll|framer-motion-scroll/.test(type)
      || progress.includes("scrollyprogress")
      || duration === "scroll-bound");
};
const scrollScrubPlanTargets = new Set();
for (const entry of entries) {
  if (!isScrollScrubEntry(entry)) continue;
  for (const key of ["target", "selector"]) {
    const value = String(entry[key] || "").trim().replace(/\s+/g, " ");
    if (value) scrollScrubPlanTargets.add(value);
  }
}
const scrollScrubSites = (
  generationPlan
  && generationPlan.scrollScrub
  && Array.isArray(generationPlan.scrollScrub.sites)
) ? generationPlan.scrollScrub.sites : [];
const scrollScrubSiteCovered = (selector) => {
  const runtime = String(selector || "").trim().replace(/\s+/g, " ");
  if (!runtime || scrollScrubPlanTargets.size === 0) return false;
  return scrollScrubSites.some((site) => {
    if (!site || typeof site !== "object") return false;
    if (String(site.source || "") !== "animation-runtime-dump.json:scrollLinkedStyles") return false;
    if (String(site.selector || "").trim().replace(/\s+/g, " ") !== runtime) return false;
    const parentCandidates = [site.target, site.scope]
      .map((value) => String(value || "").trim().replace(/\s+/g, " "))
      .filter(Boolean);
    return parentCandidates.some((parent) => scrollScrubPlanTargets.has(parent));
  });
};
const planCovered = (selector) => {
  if (!selector) return true;
  if (planText.includes(selector)) return true;
  if (hasSwiperParentPlan && isSwiperRuntimeChild(selector)) return true;
  if (scrollScrubSiteCovered(selector)) return true;
  if (
    hasSwiperParentPlan
    && consumesSwiperProgress
    && swiperProgressTargets.has(String(selector).trim().replace(/\s+/g, " "))
  ) return true;
  const tokens = selector.match(/[#.][A-Za-z0-9_-]+/g) || [];
  return tokens.some(tok => planText.includes(tok));
};
const triggerGroups = new Map();
for (const st of (Array.isArray(dump.scrollTrigger) ? dump.scrollTrigger : [])) {
  const sel = (st && typeof st === "object")
    ? String(st.trigger || st.triggerSelector || st.selector || st.target || "").trim()
    : "";
  if (!sel) continue; // legacy dumps without selectors: class-level check only
  triggerGroups.set(sel, (triggerGroups.get(sel) || 0) + 1);
}
const slsRows = Array.isArray(dump.scrollLinkedStyles) ? dump.scrollLinkedStyles : [];
for (const row of slsRows) {
  const sel = (row && typeof row === "object")
    ? String(row.selector || row.target || "").trim()
    : "";
  if (!sel) continue;
  if (!triggerGroups.has(sel)) triggerGroups.set(sel, 0);
}
const uncoveredGroups = [...triggerGroups.entries()].filter(([sel]) => !planCovered(sel));
if (uncoveredGroups.length > 0) {
  missing.push(
    uncoveredGroups.length + " runtime motion trigger group(s) with no spec entry and no named skipped[] row: "
    + uncoveredGroups.slice(0, 8).map(([sel, n]) => sel + (n ? " x" + n : "")).join(", ")
    + " — every trigger group needs a plan (entry target/trigger/id) or a named skip with a reason (denominator reconciliation)"
  );
}

const ixCount = (dump.ix2 && typeof dump.ix2.timelineCount === "number") ? dump.ix2.timelineCount : 0;
if (ixCount > 0 && entries.length === 0) {
  missing.push(ixCount + " Webflow IX2 timeline(s) detected at runtime but transition-spec is empty — see animation-runtime-dump.json ix2");
}

const timelines = Array.isArray(dump.gsapTimelines) ? dump.gsapTimelines : [];
const timelineTargets = [];
const timelineEaseNames = [];
for (const tl of timelines) {
  if (!tl || typeof tl !== "object") continue;
  if (Array.isArray(tl.targets)) {
    for (const target of tl.targets) {
      if (typeof target === "string" && target.trim()) {
        timelineTargets.push(target.trim());
      }
    }
  }
  if (typeof tl.easeName === "string" && tl.easeName.trim()) {
    timelineEaseNames.push(tl.easeName.trim());
  }
}

const plannedTimelineSelectors = [];
for (const row of [...entries, ...skippedRows]) {
  if (!row || typeof row !== "object") continue;
  for (const key of ["target", "selector"]) {
    if (typeof row[key] !== "string") continue;
    for (const selector of row[key].split(",")) {
      if (selector.trim()) plannedTimelineSelectors.push(selector.trim());
    }
  }
}
const normalizeSelector = (value) => String(value || "").trim().replace(/\s+/g, " ");
const explicitGroupCovers = (planned, runtime) => {
  const match = normalizeSelector(planned).match(/^(.*?)\s*>\s*\*$/);
  if (!match) return false;
  const parent = normalizeSelector(match[1]);
  if (!parent || !runtime.startsWith(parent)) return false;
  const remainder = runtime.slice(parent.length);
  const childMatch = remainder.match(/^\s*>\s*(\S.*)$/)
    || remainder.match(/^\s+(\S.*)$/);
  const child = childMatch ? childMatch[1].trim() : "";
  // `.hero > *` covers one child selector segment only. Treating every
  // descendant prefix as covered lets `.hero .card .title` pass even though
  // `.title` is not a direct child represented by the plan.
  return Boolean(child) && !/[\s>+~]/.test(child);
};
const selectorCovered = (selector) => {
  const runtime = normalizeSelector(selector);
  const runtimeTokens = runtime.match(/[#.][A-Za-z0-9_-]+/g) || [];
  const runtimeLeaf = runtimeTokens[runtimeTokens.length - 1];
  return plannedTimelineSelectors.some((plannedValue) => {
    const planned = normalizeSelector(plannedValue);
    if (planned === runtime || explicitGroupCovers(planned, runtime)) return true;
    return /^[#.][A-Za-z0-9_-]+$/.test(planned) && planned === runtimeLeaf;
  });
};
const uniqueTimelineTargets = [...new Set(timelineTargets)];
const uncoveredTargets = uniqueTimelineTargets.filter(t => !selectorCovered(t));
const coveredTargets = uniqueTimelineTargets.filter(t => selectorCovered(t));
if (uniqueTimelineTargets.length > 0 && uncoveredTargets.length === uniqueTimelineTargets.length) {
  missing.push(
    timelines.length + " GSAP global timeline child(ren) detected at runtime but transition-spec mentions none of their targets: "
    + uncoveredTargets.slice(0, 5).join(", ")
    + " — see animation-runtime-dump.json gsapTimelines[]"
  );
}
if (
  uniqueTimelineTargets.length >= 3
  && coveredTargets.length > 0
  && coveredTargets.length / uniqueTimelineTargets.length < 0.5
) {
  missing.push(
    "GSAP timeline target coverage low: "
    + coveredTargets.length + "/" + uniqueTimelineTargets.length
    + " unique runtime target(s) mentioned in transition-spec. Uncovered examples: "
    + uncoveredTargets.slice(0, 5).join(", ")
    + " — cover the runtime target groups in transitions[] or document each "
    + "intentional omission in skipped[]"
  );
}

const customEaseRegistry = dump.customEaseRegistry && typeof dump.customEaseRegistry === "object"
  ? dump.customEaseRegistry : {};
const customEaseKeys = Object.keys(customEaseRegistry);
const scrollEaseNames = Array.isArray(dump.scrollTrigger)
  ? dump.scrollTrigger.flatMap(st => {
      const tween = st && typeof st === "object" ? st.tween : null;
      if (!tween || typeof tween !== "object") return [];
      return [tween.easeName, tween.ease].filter(v => typeof v === "string" && v.trim());
    })
  : [];
const usedCustomEaseKeys = customEaseKeys.filter(key => {
  return [...timelineEaseNames, ...scrollEaseNames].some(name => name === key || name.includes(key));
});
const missingEaseKeys = usedCustomEaseKeys.filter(key => {
  if (specText.includes(key)) return false;
  const data = customEaseRegistry[key];
  if (typeof data !== "string" || !data) return true;
  return !specText.includes(data) && !specText.includes(data.slice(0, Math.min(40, data.length)));
});
if (missingEaseKeys.length > 0) {
  missing.push(
    missingEaseKeys.length + " GSAP CustomEase key(s) used at runtime but absent from transition-spec: "
    + missingEaseKeys.slice(0, 5).join(", ")
    + " — copy the exact key or curve data from animation-runtime-dump.json customEaseRegistry"
  );
}

const status = missing.length === 0 ? "pass" : "fail";
const out = {
  schemaVersion: 1,
  status,
  scrollTriggerCount: stCount,
  ix2TimelineCount: ixCount,
  gsapTimelineCount: timelines.length,
  gsapTimelineTargetCount: timelineTargets.length,
  gsapTimelineUniqueTargetCount: uniqueTimelineTargets.length,
  gsapTimelineTargetCoveredCount: coveredTargets.length,
  customEaseCount: customEaseKeys.length,
  customEaseUsedCount: usedCustomEaseKeys.length,
  specEntryCount: entries.length,
  missing,
  warnings
};
fs.writeFileSync(process.argv[3], JSON.stringify(out, null, 2));
console.log("Wrote " + process.argv[3]);
if (missing.length === 0) {
  console.log("✅ runtime-spec coverage clean (scrollTrigger=" + stCount + " ix2=" + ixCount + " gsapTimelines=" + timelines.length + " customEaseUsed=" + usedCustomEaseKeys.length + " spec=" + entries.length + ")");
} else {
  console.log("❌ runtime-spec coverage gaps:");
  for (const m of missing) console.log("  - " + m);
}
process.exit(missing.length === 0 ? 0 : 1);
' "$DUMP" "$SPEC" "$OUT" "$IMPL_SRC"
