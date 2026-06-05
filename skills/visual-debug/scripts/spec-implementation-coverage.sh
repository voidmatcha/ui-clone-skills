#!/usr/bin/env bash
# spec-implementation-coverage.sh — Post-implement gate: every transition-spec
# entry whose selector/id is matched in the impl source must also have a motion
# declaration in that source. Closes the silent-killer failure class where:
#   1. The agent produced a clean transition-spec.json,
#   2. transition-spec-coverage passed (selector hits ≥1 per entry),
#   3. but the generated component renders the selector with zero animation
#      hooks — fade-in is missing, easing wrong, or scroll-driven entry never
#      wired to IntersectionObserver / useScroll.
#
# Usage:
#   bash spec-implementation-coverage.sh <component-dir> <impl-src-dir>
#
# Exit: 0 = every covered entry has a motion declaration in its matched files,
#       1 = entries with selector hit but no motion declaration,
#       2 = setup error / missing files

set -uo pipefail

COMP_DIR="${1:?Usage: spec-implementation-coverage.sh <component-dir> <impl-src-dir>}"
IMPL_DIR="${2:?Missing impl-src-dir}"
SPEC="$COMP_DIR/transition-spec.json"

if [ ! -f "$SPEC" ]; then
  echo "ERROR: transition-spec.json not found at $SPEC"
  exit 2
fi
if [ ! -d "$IMPL_DIR" ]; then
  echo "ERROR: impl source dir not found at $IMPL_DIR"
  exit 2
fi
if ! command -v node &>/dev/null; then
  echo "ERROR: node not found"
  exit 2
fi

# Single-process implementation. The original shell version spawned grep for
# every (entry × needle × file) probe; on macOS with version-manager shims this
# pushed small fixtures past pytest's 30s timeout. Keep the public table and
# JSON contract, but do all scanning from one Node process.
node - "$COMP_DIR" "$IMPL_DIR" "$SPEC" <<'NODE'
const fs = require('fs');
const path = require('path');

const [compDir, implDir, specPath] = process.argv.slice(2);

function failSetup(message) {
  console.log(`ERROR: ${message}`);
  process.exit(2);
}

function loadSpec(file) {
  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    failSetup(`cannot parse transition-spec.json: ${error.message}`);
  }
  const list = Array.isArray(parsed)
    ? parsed
    : (Array.isArray(parsed.transitions)
      ? parsed.transitions
      : (Array.isArray(parsed.entries) ? parsed.entries : []));
  return list
    .map((entry) => ({
      id: String(entry.id || entry.name || ''),
      type: String(entry.type || (entry.animation && entry.animation.type) || ''),
      trigger: String(entry.trigger || ''),
      selector: String(entry.selector || entry.target || ''),
    }))
    .filter((entry) => entry.id);
}

const entries = loadSpec(specPath);
if (entries.length === 0) {
  console.log('ERROR: spec has no entries (or schema not recognised).');
  process.exit(2);
}

const skipDirs = new Set(['.git', 'node_modules', 'dist', 'build', '.next', 'coverage']);
function walk(dir, out = []) {
  for (const name of fs.readdirSync(dir)) {
    if (skipDirs.has(name)) continue;
    const full = path.join(dir, name);
    let stat;
    try {
      stat = fs.statSync(full);
    } catch (_) {
      continue;
    }
    if (stat.isDirectory()) walk(full, out);
    else if (stat.isFile()) out.push(full);
  }
  return out;
}

const files = walk(implDir).map((file) => {
  let text = '';
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (_) {
    text = '';
  }
  return { file, text };
});

const motionNeedles = [
  'transition:', 'transition-property', 'scroll-behavior', 'animation:', '@keyframes',
  'transition-', 'animate-', 'duration-', 'ease-', 'hover:', 'group-hover:', 'focus:',
  'framer-motion', "from 'motion", 'from "motion', '<motion.', 'useMotionValue',
  'useTransform', 'useScroll', 'useSpring', 'AnimatePresence',
  'gsap.to', 'gsap.from', 'gsap.timeline', 'ScrollTrigger', 'Lenis', 'ReactLenis',
  'IntersectionObserver', 'useInView', 'useIntersection', 'useScrollTrigger',
  'react-spring', 'useSprings', 'useChain', 'data-w-id', 'w-mod',
];

const markerScaffoldRe = /data-transition-hooks|data-transition=|data-scroll-hook|data-hover-hook|data-click-hook|data-motion-hook|hidden\s+[^>]*data-/;
const markerHookFileRe = /data-transition-hooks/;
const markerLineRe = /.*(?:data-transition-hooks|data-transition=|data-scroll-hook|data-hover-hook|data-click-hook|data-motion-hook|hidden\s+[^>]*data-).*\n?/g;

function camelize(id, pascal = false) {
  return id.split('-').filter(Boolean).map((part, index) => {
    if (!pascal && index === 0) return part;
    return part.charAt(0).toUpperCase() + part.slice(1);
  }).join('');
}

function selectorNeedles(selector) {
  const out = [];
  for (const piece of selector.split(/\s+/)) {
    for (const token of piece.split('.')) {
      let raw = token.replace(/^[.#]/, '');
      if (!raw || ['>', '+', '~', '*'].includes(raw) || raw.startsWith(':')) continue;
      if (raw.length < 3) continue;
      out.push(raw);
      const base = raw.replace(/__[A-Za-z0-9_-]{3,}$/, '');
      if (base && base !== raw && base.length >= 3) {
        out.push(base);
        const localName = base.replace(/^[a-z]*_/, '');
        if (localName && localName !== base && localName.length >= 3) out.push(localName);
      }
    }
  }
  return out;
}

function entryNeedles(entry) {
  const needles = [entry.id];
  const camel = camelize(entry.id, false);
  const pascal = camelize(entry.id, true);
  if (camel && camel !== entry.id) needles.push(camel);
  if (pascal && pascal !== entry.id && pascal !== camel) needles.push(pascal);
  needles.push(...selectorNeedles(entry.selector));
  return [...new Set(needles.filter(Boolean))];
}

function matchedFiles(entry) {
  const needles = entryNeedles(entry);
  return files.filter(({ text }) => needles.some((needle) => text.includes(needle)));
}

function sanitizedContent(matched) {
  return matched
    .filter(({ text }) => !markerHookFileRe.test(text))
    .map(({ text }) => text.replace(markerLineRe, ''))
    .join('\n');
}

function triggerStaticReason(entry, sanitized) {
  const key = `${entry.id} ${entry.type} ${entry.trigger}`;
  if (/hover|mouseenter|mouseover|pointerenter/i.test(key)) {
    const ok = /(^|[^A-Za-z0-9_-])(:hover|hover:|group-hover:|onMouseEnter|onMouseLeave|onPointerEnter|onPointerLeave|whileHover|useHover|addEventListener\s*\(\s*["'](?:mouseenter|mouseover|pointerenter))/m.test(sanitized);
    if (!ok) return 'hover trigger missing handler/css';
  }
  if (/click|accordion|toggle|expanded/i.test(key)) {
    const ok = /(onClick|addEventListener\s*\(\s*["']click|aria-expanded|useState|useReducer|<details|<summary|\sopen[=}]|data-state=|set[A-Z][A-Za-z0-9_]*)/m.test(sanitized);
    if (!ok) return 'click/accordion trigger missing handler/state';
  }
  if (/smooth-scroll|smooth[\s_-]*scroll|lenis/i.test(key)) {
    const ok = /(new\s+Lenis|ReactLenis|from\s+["']lenis["']|Lenis\s*\(|scroll-behavior\s*:\s*smooth|scrollBehavior\s*:\s*["']?smooth)/m.test(sanitized);
    if (!ok) return 'smooth scroll missing Lenis/native smooth-scroll wiring';
  } else if (/(^|[\s_-])scroll([\s_-]|$)|scroll-driven|scrolltrigger/i.test(key)) {
    const ok = /(useScroll|scrollYProgress|useTransform|ScrollTrigger|scrollTrigger|addEventListener\s*\(\s*["']scroll|onscroll|requestAnimationFrame|getBoundingClientRect|ScrollTimeline|animationTimeline)/m.test(sanitized);
    if (!ok) return 'scroll trigger missing scroll progress/listener wiring';
  }
  if (/page-load|(^|[\s_-])load([\s_-]|$)|mount-reveal|load-reveal/i.test(key)) {
    const ok = /(@keyframes|animation:|animate-|<motion\.|initial=|animate=|useEffect|requestAnimationFrame|setTimeout|onLoad|data-loaded|isLoaded|loaded)/m.test(sanitized);
    if (!ok) return 'load reveal missing mount/load animation wiring';
  }
  return '';
}

let uncovered = 0;
let presenceOnly = 0;
let scrollScrubStatic = 0;
let intersectionStatic = 0;
let triggerStatic = 0;
let markerOnly = 0;
let missingEntirely = 0;
let total = 0;
let row = 0;

console.log('═══ Spec Implementation Coverage ═══');
console.log(`Spec:        ${specPath}`);
console.log(`Impl source: ${implDir}`);
console.log('');
console.log('| # | id | trigger | type | matched file(s) | motion |');
console.log('|---|----|---------|------|-----------------|--------|');

for (const entry of entries) {
  total += 1;
  const matched = matchedFiles(entry);
  if (matched.length === 0) {
    console.log(`| ${row} | ❌ ${entry.id} | ${entry.trigger} | ${entry.type} | (none — entry not implemented) | — |`);
    missingEntirely += 1;
    uncovered += 1;
    row += 1;
    continue;
  }

  const rawMatched = matched.map(({ text }) => text).join('\n');
  const sanitized = sanitizedContent(matched);
  if (matched.some(({ text }) => markerScaffoldRe.test(text))) markerOnly += 1;

  let motionHit = '';
  for (const needle of motionNeedles) {
    if (sanitized.includes(needle)) {
      motionHit = `\`${needle}\``;
      break;
    }
  }

  const key = `${entry.id} ${entry.type} ${entry.trigger}`;
  if (/scroll-scrub|scroll-driven.*pin|pin.*scroll|sticky-pin/i.test(key)) {
    const hasProgress = /(useScroll|scrollYProgress|useTransform|ScrollTrigger|scrollTrigger|requestAnimationFrame|getBoundingClientRect|ScrollTimeline|animationTimeline)/.test(rawMatched) ? 1 : 0;
    const hasPin = /position:\s*['"]?sticky|position:\s*sticky|className=.*sticky|pin:\s*true|pin:\s*[^,}]+|ScrollTrigger/.test(rawMatched) ? 1 : 0;
    if (!hasProgress || !hasPin) {
      console.log(`| ${row} | ❌ ${entry.id} | ${entry.trigger} | ${entry.type} | ${matched.length} file(s) | scroll-scrub missing progress=${hasProgress} pin=${hasPin} |`);
      scrollScrubStatic += 1;
      uncovered += 1;
      row += 1;
      continue;
    }
    if (!motionHit) motionHit = '`scroll-scrub progress+pin`';
  }

  if (/intersection|in-view|inview|viewport|while-in-view|whileInView/i.test(key)) {
    const hasObserver = /(IntersectionObserver|useInView|whileInView|viewport\s*=|viewport:|onViewportEnter|onViewportLeave|useIntersection|react-intersection-observer)/.test(rawMatched) ? 1 : 0;
    if (!hasObserver) {
      console.log(`| ${row} | ❌ ${entry.id} | ${entry.trigger} | ${entry.type} | ${matched.length} file(s) | intersection reveal missing observer |`);
      intersectionStatic += 1;
      uncovered += 1;
      row += 1;
      continue;
    }
    if (!motionHit) motionHit = '`intersection observer`';
  }

  if (!motionHit) {
    console.log(`| ${row} | ❌ ${entry.id} | ${entry.trigger} | ${entry.type} | ${matched.length} file(s) | — |`);
    presenceOnly += 1;
    uncovered += 1;
    row += 1;
    continue;
  }

  const triggerReason = triggerStaticReason(entry, sanitized);
  if (triggerReason) {
    console.log(`| ${row} | ❌ ${entry.id} | ${entry.trigger} | ${entry.type} | ${matched.length} file(s) | ${triggerReason} |`);
    triggerStatic += 1;
    uncovered += 1;
    row += 1;
    continue;
  }

  console.log(`| ${row} | ✅ ${entry.id} | ${entry.trigger} | ${entry.type} | ${matched.length} file(s) | ${motionHit} |`);
  row += 1;
}

console.log('');
console.log(`Coverage: ${total - uncovered} / ${total} with motion declared`);
console.log('');

const status = uncovered > 0 ? 'fail' : 'pass';
fs.writeFileSync(path.join(compDir, 'spec-implementation-coverage.json'), JSON.stringify({
  schemaVersion: 1,
  status,
  total,
  withMotion: total - uncovered,
  presenceOnly,
  scrollScrubStatic,
  intersectionStatic,
  triggerStatic,
  markerOnly,
  missingEntirely,
}, null, 2) + '\n');

if (uncovered > 0) {
  console.log(`⛔ ${uncovered} spec entr${uncovered === 1 ? 'y' : 'ies'} matched in impl source but have no motion declaration.`);
  console.log('');
  console.log('   This is the bug class where the generated component renders the');
  console.log('   selector but never animates it — same end markup, missing motion.');
  console.log('   For scroll-scrub / pinned sections, CSS transition alone is not enough:');
  console.log('   matched source must include a scroll progress source and sticky/pin');
  console.log('   structure.');
  console.log('   For intersection / in-view reveals, CSS transition alone is not enough:');
  console.log('   matched source must include viewport observer wiring such as');
  console.log('   IntersectionObserver, useInView, whileInView, or onViewportEnter.');
  console.log('   For trigger-specific entries, marker strings are not enough:');
  console.log('   matched source must include non-marker hover/click/load/scroll wiring');
  console.log('   appropriate to the spec trigger.');
  console.log("   Fix: open each entry's matched file and wire the declared trigger /");
  console.log('   easing / duration. Do NOT mark verification PASS until this table');
  console.log('   is all ✅.');
  process.exit(1);
}

console.log('✅ Every covered spec entry has a motion declaration in its matched files.');
process.exit(0);
NODE
