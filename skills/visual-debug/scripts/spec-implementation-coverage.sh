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

# Navigation watchdog: shadows `agent-browser` so any open/goto/navigate this
# gate (or a future probe added to it) issues fails fast on a dead/unreachable
# URL (UI_CLONE_AB_OPEN_TIMEOUT, default 30s) instead of deadlocking. This gate
# is currently a static node scan with no browser calls, so the source is a
# no-cost guard. See lib/ab-timeout.sh header.
_SIC_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/ab-timeout.sh
[ -f "$_SIC_SCRIPT_DIR/lib/ab-timeout.sh" ] && . "$_SIC_SCRIPT_DIR/lib/ab-timeout.sh" || true

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
      raw: entry,
      id: String(entry.id || entry.name || ''),
      type: String(entry.type || (entry.animation && entry.animation.type) || ''),
      trigger: String(entry.trigger || ''),
      selector: String(entry.selector || entry.target || ''),
      animation: entry.animation || null,
    }))
    .filter((entry) => entry.id);
}

const entries = loadSpec(specPath);
if (entries.length === 0) {
  console.log('ERROR: spec has no entries (or schema not recognised).');
  process.exit(2);
}

const generatedEvidenceDirs = (process.env.UI_CLONE_GENERATED_EVIDENCE_DIRS || 'ref-css')
  .split(/[,:]/)
  .map((name) => name.trim())
  .filter(Boolean);
const skipDirs = new Set(['.git', 'node_modules', 'dist', 'build', '.next', 'coverage']);
for (const name of generatedEvidenceDirs) skipDirs.add(name);
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

function readText(file) {
  let text = '';
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (_) {
    text = '';
  }
  return text;
}

function fileRecord(file, source = 'source') {
  return { file, text: readText(file), source };
}

function isStyleFile(file) {
  return /\.(?:css|scss|sass|less|styl)$/i.test(file);
}

const files = walk(implDir).map((file) => fileRecord(file));
const fileByPath = new Map(files.map((record) => [path.resolve(record.file), record]));
const sourceRoots = new Set(files.map((record) => path.resolve(record.file)));
const scriptExtensions = ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs'];
const styleExtensions = ['.css', '.scss', '.sass', '.less', '.styl'];
const importExtensions = [...scriptExtensions, ...styleExtensions];

function isWithinImplDir(file) {
  const rel = path.relative(path.resolve(implDir), path.resolve(file));
  return rel && !rel.startsWith('..') && !path.isAbsolute(rel);
}

function resolveLocalImport(fromFile, specifier) {
  if (!specifier || /^(?:[a-z]+:)?\/\//i.test(specifier)) return null;
  if (!specifier.startsWith('.') && !specifier.startsWith('/')) return null;
  const base = specifier.startsWith('/')
    ? path.join(implDir, specifier.slice(1))
    : path.resolve(path.dirname(fromFile), specifier);
  if (!isWithinImplDir(base)) return null;
  const candidates = [base];
  for (const ext of importExtensions) candidates.push(`${base}${ext}`);
  for (const ext of importExtensions) candidates.push(path.join(base, `index${ext}`));
  for (const candidate of candidates) {
    if (!isWithinImplDir(candidate)) continue;
    try {
      if (fs.statSync(candidate).isFile()) return path.resolve(candidate);
    } catch (_) {
      // Keep probing common local module extensions.
    }
  }
  return null;
}

function importSpecifiers(text) {
  const out = [];
  const importRe = /\bimport\s+(?:[^'"]*?\s+from\s+)?["']([^"']+)["']|\bexport\s+[^'"]*?\s+from\s+["']([^"']+)["']|\bimport\s*\(\s*["']([^"']+)["']\s*\)/g;
  let match;
  while ((match = importRe.exec(text)) !== null) {
    out.push(match[1] || match[2] || match[3]);
  }
  return out;
}

function recordForFile(file, source = 'imported') {
  const resolved = path.resolve(file);
  const existing = fileByPath.get(resolved);
  if (existing) return existing;
  const record = fileRecord(resolved, source);
  fileByPath.set(resolved, record);
  return record;
}

function localImportTargets(record) {
  if (isStyleFile(record.file)) return [];
  const targets = [];
  for (const specifier of importSpecifiers(record.text)) {
    const resolved = resolveLocalImport(record.file, specifier);
    if (resolved) targets.push(recordForFile(resolved, 'imported'));
  }
  return targets;
}

const importsByFile = new Map();
const importersByFile = new Map();

function collectImportedRecords(startRecords) {
  const collected = new Map();
  const queue = [...startRecords];
  while (queue.length > 0 && collected.size < 512) {
    const record = queue.shift();
    const resolved = path.resolve(record.file);
    if (collected.has(resolved)) continue;
    collected.set(resolved, record);
    const targets = localImportTargets(record);
    importsByFile.set(resolved, targets);
    for (const target of targets) {
      const targetPath = path.resolve(target.file);
      if (!importersByFile.has(targetPath)) importersByFile.set(targetPath, []);
      importersByFile.get(targetPath).push(record);
      queue.push(target);
    }
  }
  return [...collected.values()];
}

collectImportedRecords(files);

function relatedImportRecords(seedRecords) {
  const related = new Map();
  const queue = [...seedRecords];
  while (queue.length > 0 && related.size < 128) {
    const record = queue.shift();
    const resolved = path.resolve(record.file);
    if (related.has(resolved)) continue;
    related.set(resolved, record);
    // Follow the modules that consume selector-bearing source/data. Walking
    // forward again from a shared entry module would pull every sibling
    // component into the evidence set, allowing an unrelated driver to satisfy
    // this transition.
    for (const next of importersByFile.get(resolved) || []) {
      if (!isStyleFile(next.file)) queue.push(next);
    }
  }
  return [...related.values()];
}

function importedStyleClosure(records) {
  const imported = new Map();
  const queue = [...records];
  const seen = new Set();
  while (queue.length > 0 && seen.size < 256) {
    const record = queue.shift();
    const resolved = path.resolve(record.file);
    if (seen.has(resolved)) continue;
    seen.add(resolved);
    for (const target of importsByFile.get(resolved) || []) {
      if (isStyleFile(target.file)) {
        imported.set(path.resolve(target.file), target);
      } else {
        queue.push(target);
      }
    }
  }
  return [...imported.values()];
}

function sourceLooksLikeDom(text) {
  if (text.includes('className=') || text.includes('className =')) return true;
  if (text.includes('class=') || text.includes('class =')) return true;
  return text.includes('return <') || text.includes('(<') || text.includes('= <');
}

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

function stripFunctionalPseudos(selector) {
  let stable = '';
  let index = 0;

  while (index < selector.length) {
    const functional = selector.slice(index).match(/^:{1,2}[A-Za-z-]+\(/);
    if (!functional) {
      stable += selector[index];
      index += 1;
      continue;
    }

    index += functional[0].length;
    let depth = 1;
    let quote = '';
    while (index < selector.length && depth > 0) {
      const char = selector[index];
      if (quote) {
        if (char === '\\') index += 1;
        else if (char === quote) quote = '';
      } else if (char === '"' || char === "'") {
        quote = char;
      } else if (char === '(') {
        depth += 1;
      } else if (char === ')') {
        depth -= 1;
      }
      index += 1;
    }
  }

  return stable;
}

function selectorNeedles(selector) {
  const out = [];

  function addNeedle(raw) {
    if (!raw || raw.length < 3) return;
    out.push(raw);
    const base = raw.replace(/__[A-Za-z0-9_-]{3,}$/, '');
    if (base && base !== raw && base.length >= 3) {
      out.push(base);
      const localName = base.replace(/^[a-z]*_/, '');
      if (localName && localName !== base && localName.length >= 3) out.push(localName);
    }
  }

  const stableSelector = stripFunctionalPseudos(selector);
  for (const piece of stableSelector.split(/[\s>+~,]+/).filter(Boolean)) {
    const withoutAttributes = piece.replace(/\[[^\]]*\]/g, '');
    const identifiers = [
      ...withoutAttributes.matchAll(/[.#]([A-Za-z_][A-Za-z0-9_-]*)/g),
    ];
    for (const match of identifiers) {
      addNeedle(match[1]);
    }
    if (identifiers.length > 0) continue;

    const attributes = [
      ...piece.matchAll(/\[\s*([A-Za-z_][A-Za-z0-9_-]*)/g),
    ];
    for (const match of attributes) addNeedle(match[1]);
    if (attributes.length > 0) continue;

    const tag = withoutAttributes.match(/^([A-Za-z][A-Za-z0-9-]*)/);
    if (tag) addNeedle(tag[1]);
  }
  return [...new Set(out)];
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
  const matchesNeedle = ({ text }) => needles.some((needle) => text.includes(needle));
  const direct = files.filter(matchesNeedle);
  const domMatches = direct.filter(({ file, text }) => !isStyleFile(file) && sourceLooksLikeDom(text));
  const key = `${entry.id} ${entry.type} ${entry.trigger}`;
  if (domMatches.length === 0 && /hover|mouseenter|mouseover|pointerenter/i.test(key)) return [];
  const related = relatedImportRecords(direct).filter(({ file }) => !isStyleFile(file));
  const styleSeeds = [...domMatches, ...related];
  const importedStyleMatches = importedStyleClosure(styleSeeds).filter(matchesNeedle);
  const all = new Map();
  // A stylesheet merely existing under src is dead evidence until a matched
  // DOM/importer path reaches it. Keep direct script evidence, but admit style
  // records only through importedStyleClosure.
  for (const record of direct) {
    if (!isStyleFile(record.file)) all.set(path.resolve(record.file), record);
  }
  for (const record of related) all.set(path.resolve(record.file), record);
  for (const record of importedStyleMatches) all.set(path.resolve(record.file), record);
  return [...all.values()];
}

function sanitizedContent(matched) {
  return matched
    .filter(({ text }) => !markerHookFileRe.test(text))
    // Marker attributes only exist in DOM-producing source. Running the
    // line-oriented marker regex over minified one-line CSS can backtrack
    // quadratically, turning a 1 MB imported stylesheet into a minute-long
    // gate. Imported styles are already executable-only evidence here.
    .map(({ file, text }) => isStyleFile(file) ? text : text.replace(markerLineRe, ''))
    .join('\n');
}

function isPureCssSticky(entry) {
  const animation = entry.animation;
  const animationType = typeof animation === 'object' && animation !== null
    ? animation.type
    : '';
  const type = String(animationType || entry.type || '').trim().toLowerCase();
  return ['css-sticky', 'sticky', 'position-sticky'].includes(type);
}

function hasStickyDeclaration(content) {
  return /position\s*:\s*["']?sticky|position-sticky|className\s*=\s*["'][^"']*\bsticky\b/.test(content);
}

function rawEntryText(entry) {
  try {
    return JSON.stringify(entry.raw || {});
  } catch (_) {
    return '';
  }
}

function scrollScrubRequiresPin(entry) {
  const key = `${entry.id} ${entry.type} ${entry.trigger}`.toLowerCase();
  if (/scroll-driven.*pin|pin.*scroll|sticky-pin|sticky/.test(key)) return true;
  const raw = rawEntryText(entry).toLowerCase();
  if (/"pin"\s*:\s*true|"pinned"\s*:\s*true|"sticky"\s*:\s*true/.test(raw)) return true;
  if (/position\s*:\s*["']?sticky/.test(raw)) return true;
  return false;
}

function triggerStaticReason(entry, sanitized) {
  const key = `${entry.id} ${entry.type} ${entry.trigger}`;
  if (/hover|mouseenter|mouseover|pointerenter/i.test(key)) {
    const ok = /(^|[{},]\s*)[^\n{}"'<>=]*:hover\b[^{]*\{/m.test(sanitized)
      || /(^|[^A-Za-z0-9_-])(:hover|hover:|group-hover:|onMouseEnter|onMouseLeave|onPointerEnter|onPointerLeave|whileHover|useHover|addEventListener\s*\(\s*["'](?:mouseenter|mouseover|pointerenter))/m.test(sanitized);
    if (!ok) return 'hover trigger missing handler/css';
  }
  const explicitClickTrigger = /click|tap/i.test(entry.trigger);
  const clickInteractionType = /click|accordion|expanded/i.test(`${entry.id} ${entry.type}`);
  if (explicitClickTrigger || clickInteractionType) {
    const ok = /(onClick|addEventListener\s*\(\s*["']click|aria-expanded|useState|useReducer|<details|<summary|\sopen[=}]|data-state=|set[A-Z][A-Za-z0-9_]*)/m.test(sanitized);
    if (!ok) return 'click/accordion trigger missing handler/state';
  }
  if (isPureCssSticky(entry)) {
    if (!hasStickyDeclaration(sanitized)) {
      return 'css-sticky trigger missing position: sticky declaration';
    }
    return '';
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


function isResetOnlyHover(entry) {
  const anim = entry.animation;
  const blob = typeof anim === 'object' && anim !== null
    ? `${anim.type || ''} ${anim.cssText || ''} ${anim.css || ''}`
    : String(anim || '');
  const text = blob.toLowerCase();
  if (!`${entry.trigger || ''} ${text}`.toLowerCase().includes('hover')) return false;
  if (!/text-decoration(?:-line)?\s*:\s*none\b/.test(text)) return false;
  return !/(?:transition|animation|transform|opacity|filter|box-shadow|background(?:-color)?|color|fill|stroke|width|height|top|right|bottom|left)\s*:/.test(text);
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
  if (isResetOnlyHover(entry)) {
    console.log(`| ${row} | ○ ${entry.id} | ${entry.trigger} | ${entry.type} | reset-only hover rule (no motion expected) | known-skip |`);
    row += 1;
    continue;
  }

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
  if (isPureCssSticky(entry) && hasStickyDeclaration(sanitized)) {
    motionHit = '`position: sticky`';
  }

  const key = `${entry.id} ${entry.type} ${entry.trigger}`;
  if (/scroll-scrub|scroll-driven.*pin|pin.*scroll|sticky-pin/i.test(key)) {
    const hasProgress = /(useScroll|scrollYProgress|useTransform|ScrollTrigger|scrollTrigger|requestAnimationFrame|getBoundingClientRect|ScrollTimeline|animationTimeline)/.test(rawMatched) ? 1 : 0;
    const hasPin = /position:\s*['"]?sticky|position:\s*sticky|className=.*sticky|pin:\s*true|pin:\s*[^,}]+|ScrollTrigger/.test(rawMatched) ? 1 : 0;
    const needsPin = scrollScrubRequiresPin(entry) ? 1 : 0;
    if (!hasProgress || (needsPin && !hasPin)) {
      console.log(`| ${row} | ❌ ${entry.id} | ${entry.trigger} | ${entry.type} | ${matched.length} file(s) | scroll-scrub missing progress=${hasProgress} pin=${needsPin ? hasPin : 'not-required'} |`);
      scrollScrubStatic += 1;
      uncovered += 1;
      row += 1;
      continue;
    }
    if (!motionHit) motionHit = needsPin ? '`scroll-scrub progress+pin`' : '`scroll-scrub progress`';
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
  console.log('   For scroll-scrub sections, CSS transition alone is not enough:');
  console.log('   matched source must include a scroll progress source. Pinned specs');
  console.log('   must also include sticky/pin structure.');
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
