'use strict';
// Fake-DOM harness for the Phase A splash sampler (EVAL_JS in
// scripts/extract/capture-states.sh), run against the init script the shell
// actually installs — thresholds substituted, shadow-root walk and all.
//
// Usage: node splash_sampler_harness.js <init-script.js> <scenario>
// Prints the sampler's return value ({states, durationMs, polls, timedOut,
// reason}) as one JSON document, exactly what `agent-browser eval` would
// hand back. tests/test_capture_states.py wires this as the fake browser's
// eval so capture-states.sh -> sampler -> python writer runs end to end.
//
// The clock is virtual: requestAnimationFrame and setTimeout advance it and
// resolve on the microtask queue, so the sampler's 2s quiet window costs no
// wall time. Scenario events fire when the clock passes their time.

const fs = require('fs');
const vm = require('vm');

const initScript = fs.readFileSync(process.argv[2], 'utf8');
const scenarioName = process.argv[3];

const VW = 1440;
const VH = 900;

class El {
  constructor(tag, attrs, children) {
    this.localName = tag;
    this.nodeType = 1;
    this.attrs = Object.assign({}, attrs || {});
    // rect: [left, top, width, height] in CSS px; style: computed overrides.
    this.rect = this.attrs.rect || [0, 0, VW, VH];
    delete this.attrs.rect;
    this.style = Object.assign(
      { position: 'static', zIndex: 'auto', opacity: '1', visibility: 'visible', display: 'block',
        overflow: 'visible', color: 'rgb(0, 0, 0)', transform: 'none' },
      this.attrs.style || {}
    );
    delete this.attrs.style;
    this.text = this.attrs.text || '';
    delete this.attrs.text;
    this.children = [];
    this.parentNode = null;
    this.shadowRoot = null;
    for (const c of children || []) this.append(c);
  }
  get id() { return this.attrs.id || ''; }
  get className() { return this.attrs.class || ''; }
  set className(v) { this.attrs.class = v; }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  append(c) { c.parentNode = this; this.children.push(c); return c; }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
    this.parentNode = null;
  }
  // In-place replacement, as a framework re-mount does it: the new node takes
  // the old node's position among its siblings, so its nth-of-type path is
  // the same, and the old node is detached.
  replaceWith(next) {
    const parent = this.parentNode;
    if (!parent) return;
    const index = parent.children.indexOf(this);
    if (next.parentNode) next.remove();
    next.parentNode = parent;
    parent.children.splice(index, 1, next);
    this.parentNode = null;
  }
  get isConnected() {
    let cur = this;
    while (cur.parentNode) cur = cur.parentNode;
    return cur === documentRoot;
  }
  getAttribute(n) { return Object.prototype.hasOwnProperty.call(this.attrs, n) ? this.attrs[n] : null; }
  getBoundingClientRect() {
    const [left, top, width, height] = this.rect;
    return { left, top, width, height, right: left + width, bottom: top + height, x: left, y: top };
  }
  querySelectorAll(selector) {
    const out = [];
    const walk = (node) => { for (const c of node.children) { out.push(c); walk(c); } };
    walk(this);
    if (selector === 'video, audio') return out.filter((e) => e.localName === 'video' || e.localName === 'audio');
    return out;
  }
  get outerHTML() {
    const attrs = Object.entries(this.attrs).map(([k, v]) => ` ${k}="${v}"`).join('');
    return `<${this.localName}${attrs}>${this.text}${this.children.map((c) => c.outerHTML).join('')}</${this.localName}>`;
  }
}

const h = (tag, attrs, ...children) => new El(tag, attrs, children);
const cover = (share) => [0, 0, VW, Math.round(VH * share)];

// ── scenarios ───────────────────────────────────────────────────────────────
// Each returns {html, body, events: [{at, run}]}. Elements are laid out so
// their viewport share is exact: full-width blocks of height VH * share.
function scenario(name) {
  const html = h('html', {});
  const body = h('body', {});
  html.append(body);
  const events = [];
  switch (name) {
    case 'half-viewport-loader-exits': {
      // An in-flow loader covering 55% of the viewport sits over the hero
      // until 600ms, then is removed. No root class, no fixed position, DOM
      // shrinks by well under 20%.
      const app = h('div', { id: 'app', text: 'x'.repeat(2000) });
      const loader = h('div', { id: 'loader', rect: cover(0.55), text: 'loading' });
      const hero = h('section', { id: 'hero', rect: cover(1.0), text: 'y'.repeat(600) });
      app.append(loader);
      app.append(hero);
      body.append(app);
      events.push({ at: 600, run: () => loader.remove() });
      break;
    }
    case 'inflow-intro-shrinks-to-35':
    case 'inflow-intro-shrinks-to-10': {
      // An id-less in-flow `div.intro` covers the whole viewport at load and
      // shrinks to a strip at 600ms, then persists. Nothing else moves: the
      // root classes never change, the DOM never grows, nothing is fixed or
      // high-z, so `covering` is the only channel that can see it leave.
      // `-35` settles at 35% and `-10` at 10%; both are an intro curtain
      // leaving, so both must be refused.
      const settled = name === 'inflow-intro-shrinks-to-35' ? 0.35 : 0.10;
      const app = h('div', { class: 'app', text: 'x'.repeat(4000) });
      const intro = h('div', { class: 'intro', rect: cover(1.0), text: 'intro' });
      app.append(intro);
      body.append(app);
      events.push({ at: 600, run: () => { intro.rect = cover(settled); } });
      break;
    }
    case 'class-only-preloader-aliased':
    case 'id-preloader-control': {
      // A class-only full-viewport preloader in flow (no fixed position, so
      // the overlay channel is blind to it) sits in front of an id-less
      // wrapper that is `visibility: hidden` until the page is ready (the
      // computed value is inherited, so its hero is hidden too). At 600ms the
      // preloader is removed and the wrapper is shown. Keyed by DOM position
      // the wrapper becomes `body > div:nth-of-type(1)`, the key the preloader
      // held, so the map never records an exit. The control gives the
      // preloader an id, which is the only difference between the two.
      const preloaderAttrs = { class: 'preloader', rect: cover(1.0), text: 'loading' };
      if (name === 'id-preloader-control') preloaderAttrs.id = 'preloader';
      const preloader = h('div', preloaderAttrs);
      const wrapper = h('div', { class: 'wrapper', rect: cover(1.0), text: 'x'.repeat(2000), style: { visibility: 'hidden' } });
      const hero = h('section', { class: 'hero', rect: cover(1.0), text: 'hero', style: { visibility: 'hidden' } });
      wrapper.append(hero);
      body.append(preloader);
      body.append(wrapper);
      events.push({ at: 600, run: () => {
        preloader.remove();
        wrapper.style.visibility = 'visible';
        hero.style.visibility = 'visible';
      } });
      break;
    }
    case 'deep-class-only-preloader': {
      // The class-only preloader sits nine containers deep, past the eight
      // levels nthOfTypePath will walk. The containers persist; only the
      // preloader is removed at 600ms.
      let cur = body;
      for (let depth = 0; depth < 9; depth += 1) {
        const next = h('div', { class: `shell-${depth}`, rect: cover(1.0) });
        cur.append(next);
        cur = next;
      }
      const preloader = h('div', { class: 'preloader', rect: cover(1.0), text: 'loading' });
      cur.append(preloader);
      events.push({ at: 600, run: () => preloader.remove() });
      break;
    }
    case 'deferred-loading-class': {
      // `` -> `is-loading` (300ms) -> `loaded` (900ms); nothing else changes.
      const app = h('div', { id: 'app', text: 'x'.repeat(2000) });
      body.append(app);
      events.push({ at: 300, run: () => { body.className = 'is-loading'; } });
      events.push({ at: 900, run: () => { body.className = 'loaded'; } });
      break;
    }
    case 'entry-choreography-no-splash': {
      // No splash: wrappers persist, the hero reflows from 76% to 72% once
      // fonts land, a smooth-scroll marker is added late, the DOM grows 2%.
      const app = h('div', { id: 'app', text: 'x'.repeat(5000) });
      const hero = h('section', { id: 'hero', rect: cover(0.76), text: 'hero' });
      const nav = h('nav', { id: 'nav', rect: cover(0.22), text: 'nav' });
      app.append(nav);
      app.append(hero);
      body.append(app);
      events.push({ at: 400, run: () => { hero.rect = cover(0.72); } });
      events.push({ at: 700, run: () => { html.className = 'lenis lenis-smooth'; app.text += 'z'.repeat(100); } });
      break;
    }
    case 'hydration-remount-same-class':
    case 'skeleton-replaced-in-place':
    case 'loader-replaced-by-content-different-class':
    case 'remount-two-polls-apart': {
      // Four in-place replacements of an id-less element at the same path.
      //
      // hydration-remount-same-class: React/Next hydration mismatch. The
      // server-rendered `div.app` (full viewport) is thrown away and a fresh
      // `div.app` node with the client tree is mounted in its place within
      // one poll. No splash: the certificate must stand.
      //
      // skeleton-replaced-in-place: a `div.page` skeleton covering 50% is
      // replaced within one poll by a fresh `div.page` holding the content.
      // No splash: the certificate must stand.
      //
      // loader-replaced-by-content-different-class: a full-viewport
      // `div.loader` is replaced in place by `div.content`. Same path, same
      // tag, different class: a loading shell leaving, refused.
      //
      // remount-two-polls-apart: `div.app` is removed at 500ms and a fresh
      // `div.app` is appended at 800ms (several polls later). The viewport was
      // uncovered in between, which is what a loader leaving looks like:
      // refused.
      const shell = h('div', { class: 'shell', rect: cover(1.0) });
      body.append(shell);
      // Enough persistent text that removing the 2000-char block is a DOM
      // shift well under the 20% structural line: refusals below isolate to
      // the covering identity.
      const nav = h('nav', { class: 'nav', rect: cover(0.1), text: 'n'.repeat(12000) });
      shell.append(nav);
      const skeleton = name === 'skeleton-replaced-in-place';
      const oldClass = name === 'loader-replaced-by-content-different-class' ? 'loader' : skeleton ? 'page' : 'app';
      const newClass = name === 'loader-replaced-by-content-different-class' ? 'content' : oldClass;
      const before = h('div', { class: oldClass, rect: cover(skeleton ? 0.5 : 1.0), text: 'x'.repeat(2000) });
      shell.append(before);
      const after = h('div', { class: newClass, rect: cover(1.0), text: 'y'.repeat(2200) });
      if (name === 'remount-two-polls-apart') {
        events.push({ at: 500, run: () => before.remove() });
        events.push({ at: 800, run: () => shell.append(after) });
      } else {
        events.push({ at: 500, run: () => before.replaceWith(after) });
      }
      break;
    }
    default:
      throw new Error(`unknown scenario: ${name}`);
  }
  return { html, body, events };
}

const { html, body, events } = scenario(scenarioName);
const documentRoot = html;

// ── virtual clock + globals the sampler touches ────────────────────────────
let clock = 0;
function advance(ms) {
  clock += ms;
  for (const event of events) {
    if (!event.done && clock >= event.at) { event.done = true; event.run(); }
  }
}

const g = globalThis;
g.window = g;
g.innerWidth = VW;
g.innerHeight = VH;
g.Node = { ELEMENT_NODE: 1, DOCUMENT_FRAGMENT_NODE: 11 };
g.performance = { now: () => clock };
g.requestAnimationFrame = (cb) => { Promise.resolve().then(() => { advance(16); cb(clock); }); return 1; };
g.setTimeout = (fn, ms) => { Promise.resolve().then(() => { advance(Number(ms) || 0); fn(); }); return 1; };
g.getComputedStyle = (el) => el.style;
g.document = {
  documentElement: html,
  body,
  getAnimations: () => [],
  querySelectorAll: (selector) => html.querySelectorAll(selector),
};

vm.runInThisContext(initScript, { filename: 'capture-states-init.js' });

(async () => {
  const result = await g.window.__UI_CLONE_SPLASH_CAPTURE__;
  process.stdout.write(JSON.stringify(result));
})().catch((error) => {
  process.stderr.write(String(error && error.stack || error) + '\n');
  process.exit(1);
});
