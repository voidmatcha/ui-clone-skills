'use strict';
// Fake-DOM harness for the click-to-open opener walk shared library
// (TF_OPENER_LIB_JS in skills/visual-debug/scripts/transition-fires-check.sh).
//
// Usage: node opener_walk_harness.js <path-to-extracted-lib.js>
// Prints one JSON object: { scenarioName: result }. Driven by
// tests/measure/test_transition_fires_opener_walk.py, which extracts the
// library heredoc from the script so the code under test is the shipped one.
//
// The library only asks elements for tagName / getAttribute / hasAttribute /
// parentElement / children / contains / click / dispatchEvent, plus
// getComputedStyle + getBoundingClientRect for rendering and one
// document.querySelectorAll for the overlay count, so a small element class
// is enough to exercise the candidate predicate, the ranking, the fingerprint
// and the restore ladder without a browser.

const fs = require('fs');

const lib = fs.readFileSync(process.argv[2], 'utf8');

class El {
  constructor(tag, attrs, children) {
    this.tagName = tag.toUpperCase();
    this.attrs = Object.assign({}, attrs || {});
    this.rendered = this.attrs.rendered === undefined ? true : !!this.attrs.rendered;
    delete this.attrs.rendered;
    this.children = [];
    this.parentElement = null;
    this.clicks = 0;
    this.onClick = null;
    this.listeners = {};
    for (const c of children || []) this.append(c);
  }
  append(c) { c.parentElement = this; this.children.push(c); return c; }
  getAttribute(n) { return Object.prototype.hasOwnProperty.call(this.attrs, n) ? this.attrs[n] : null; }
  hasAttribute(n) { return Object.prototype.hasOwnProperty.call(this.attrs, n); }
  setAttribute(n, v) { this.attrs[n] = String(v); }
  removeAttribute(n) { delete this.attrs[n]; }
  contains(o) { for (let c = o; c; c = c.parentElement) if (c === this) return true; return false; }
  isRendered() { for (let c = this; c; c = c.parentElement) if (!c.rendered) return false; return true; }
  getBoundingClientRect() {
    const on = this.isRendered();
    return { width: on ? 10 : 0, height: on ? 10 : 0, top: 0, left: 0 };
  }
  click() { this.clicks += 1; if (this.onClick) this.onClick(this); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter((g) => g !== f); }
  dispatchEvent(ev) {
    if (!ev.target) ev.target = this;
    for (const f of (this.listeners[ev.type] || []).slice()) f(ev);
    if (ev.bubbles && this.parentElement) this.parentElement.dispatchEvent(ev);
    return true;
  }
  all() { const out = [this]; for (const c of this.children) out.push(...c.all()); return out; }
  // <details>.open, as the ladder's toggle rung drives it.
  get open() { return this.hasAttribute('open'); }
  set open(v) {
    if (v) this.setAttribute('open', ''); else this.removeAttribute('open');
    if (this.onOpenChange) this.onOpenChange(!!v);
  }
}

const h = (tag, attrs, ...children) => new El(tag, attrs, children);

class Ev {
  constructor(type, init) {
    this.type = type;
    Object.assign(this, init || {});
    this.bubbles = !!(init && init.bubbles);
    this.defaultPrevented = false;
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.stopped = true; }
}

globalThis.window = globalThis;
globalThis.KeyboardEvent = Ev;
globalThis.MouseEvent = Ev;
globalThis.PointerEvent = Ev;
globalThis.history = { pushState() {}, replaceState() {} };
globalThis.HTMLFormElement = function HTMLFormElement() {};
globalThis.HTMLFormElement.prototype.submit = function () {};
// The ladder waits between rungs; collapse the waits so scenarios run fast.
globalThis.setTimeout = (fn) => { fn(); return 0; };

const OVERLAY = /modal|overlay|drawer|backdrop|dimmed|popup/;

function install(html) {
  const body = html.children.find((c) => c.tagName === 'BODY');
  globalThis.document = {
    documentElement: html,
    body,
    activeElement: null,
    querySelectorAll: () => html.all().filter((e) => (
      e.attrs.role === 'dialog' || e.hasAttribute('aria-modal')
      || (e.tagName === 'DIALOG' && e.hasAttribute('open')) || OVERLAY.test(e.attrs.class || '')
    )),
    addEventListener: (t, f) => html.addEventListener(t, f),
    removeEventListener: (t, f) => html.removeEventListener(t, f),
  };
  globalThis.getComputedStyle = (el) => ({
    display: el.isRendered() ? 'block' : 'none', visibility: 'visible', opacity: '1',
  });
  delete globalThis.__tfOpener;
  return { html, body };
}

// The body is the checked-in library source handed over by the test (not
// user input); evaluating it is the point of the harness.
const api = new Function(lib + '\nreturn { tfRefusal, tfPickOpener, tfOpenerWalk, tfOpenerClose, tfFingerprint, tfSettled };')();

const toggler = (panel, container) => () => {
  panel.rendered = !panel.rendered;
  panel.attrs.style = panel.rendered ? 'display: block;' : 'display: none;';
  if (container) container.attrs.class = panel.rendered ? container.attrs.class + ' is-open' : container.attrs.class.replace(' is-open', '');
};

// The navercorp.com header shape that motivated the walk: a language list
// behind a plain <button type=button class=btn-selected> and a search box
// behind the header's btn-search. Class names follow the captured markup.
function navercorpHeader(opts) {
  const o = opts || {};
  const seedKo = h('button', { type: 'button', class: 'btn-ko' });
  const seedEn = h('button', { type: 'button', class: 'btn-en' });
  const list = h('ul', { class: 'btn-lang__list', style: 'display: none;', rendered: false },
    h('li', { 'data-lang': 'ko' }, seedKo), h('li', { 'data-lang': 'en' }, seedEn));
  const selected = h('button', { type: 'button', class: 'nclick-target btn-selected', 'data-nclick': 'gnb.lng' });
  const lang = h('div', { class: 'btn-lang' }, selected, list);
  const search = h('button', { type: 'button', class: o.searchClass || 'nclick-target btn-search', 'data-nclick': o.searchNclick || 'gnb.search' });
  const moNav = h('button', { type: 'button', class: 'btn-mo-nav', rendered: false });
  const utils = h('div', { class: 'header__utils' }, lang, search, moNav);
  const nav = h('nav', { class: 'nav' }, h('ul', {}, h('li', {}, h('a', { href: '/about', class: 'nav__link' }))));
  const searchBox = h('div', { class: 'search-tab__box' },
    h('input', { type: 'text' }), h('button', { type: 'button', class: 'btn-search' }), h('button', { type: 'button', class: 'btn-delete' }));
  const searchTab = h('div', { class: 'search-tab', rendered: false }, h('div', { class: 'search-tab__inner' }, h('fieldset', {}, searchBox)));
  const header = h('header', { class: 'header thema-black' }, h('div', { class: 'header__inner' }, nav, utils), searchTab);
  const body = h('body', {}, header);
  const { html } = install(h('html', {}, body));
  return { html, body, header, seedKo, seedEn, list, selected, lang, search, searchTab, searchBox };
}

const scenarios = {};

scenarios.refusals = async () => {
  const seed = h('span', { class: 'target', rendered: false });
  const form = h('form', {}, h('button', { class: 'bare-in-form' }), h('button', { type: 'button', class: 'typed-in-form' }));
  const link = h('a', { href: '/x' }, h('button', { type: 'button', class: 'in-link' }));
  const tablist = h('div', { role: 'tablist' },
    h('button', { type: 'button', role: 'tab', class: 'tab' }), h('button', { type: 'button', class: 'plain-in-tablist' }));
  const els = {
    submit: h('button', { type: 'submit' }),
    bareInForm: form.children[0],
    typedInForm: form.children[1],
    inLink: link.children[0],
    tab: tablist.children[0],
    plainInTablist: tablist.children[1],
    pressed: h('button', { type: 'button', 'aria-pressed': 'false' }),
    switchRole: h('div', { role: 'switch' }),
    dialogOpener: h('button', { type: 'button', 'aria-haspopup': 'dialog' }),
    alreadyOpen: h('button', { type: 'button', 'aria-expanded': 'true' }),
    swiperNext: h('button', { type: 'button', class: 'swiper-button-next' }),
    loadMore: h('button', { type: 'button', class: 'btn-more' }),
    modalTrigger: h('button', { type: 'button', 'data-bs-toggle': 'modal' }),
    hidden: h('button', { type: 'button', rendered: false }),
    plain: h('button', { type: 'button', class: 'nclick-target btn-selected' }),
    expanded: h('button', { type: 'button', 'aria-expanded': 'false' }),
    menuButton: h('button', { type: 'button', 'aria-haspopup': 'menu' }),
    summary: h('summary', {}),
    roleButton: h('div', { role: 'button', class: 'lang-toggle' }),
  };
  const loose = Object.values(els).filter((e) => !e.parentElement);
  const body = h('body', {}, seed, form, link, tablist, ...loose);
  install(h('html', {}, body));
  const out = {};
  for (const [k, el] of Object.entries(els)) out[k] = api.tfRefusal(el, seed);
  return out;
};

scenarios.lang_toggle = async () => {
  const p = navercorpHeader();
  p.selected.onClick = toggler(p.list, p.lang);
  const walk = await api.tfOpenerWalk([p.seedKo, p.seedEn], 2);
  const revealedDuring = p.seedKo.isRendered();
  const close = await api.tfOpenerClose();
  return {
    walk, close, revealedDuring,
    listRenderedAfterClose: p.list.isRendered(),
    langClassAfterClose: p.lang.attrs.class,
    selectedClicks: p.selected.clicks, searchClicks: p.search.clicks,
  };
};

scenarios.search_affinity = async () => {
  const p = navercorpHeader();
  p.search.onClick = toggler(p.searchTab, null);
  p.selected.onClick = toggler(p.list, p.lang);
  const walk = await api.tfOpenerWalk([p.searchBox], 2);
  const close = await api.tfOpenerClose();
  return { walk, close, searchClicks: p.search.clicks, selectedClicks: p.selected.clicks, tabRenderedAfterClose: p.searchTab.isRendered() };
};

scenarios.ambiguous_not_clicked = async () => {
  // Two generic controls at the shared level and no name token in common
  // with the hidden branch: nothing is clicked.
  const p = navercorpHeader({ searchClass: 'nclick-target btn-util', searchNclick: 'gnb.util' });
  p.search.onClick = toggler(p.searchTab, null);
  p.selected.onClick = toggler(p.list, p.lang);
  const walk = await api.tfOpenerWalk([p.searchBox], 2);
  return { walk, searchClicks: p.search.clicks, selectedClicks: p.selected.clicks };
};

scenarios.aria_controls_wins = async () => {
  const seed = h('a', { href: '/x', class: 'sub-link' });
  const panel = h('div', { id: 'lang-panel', rendered: false }, seed);
  const toggle = h('button', { type: 'button', 'aria-expanded': 'false', class: 'other-toggle' });
  const named = h('button', { type: 'button', class: 'plain', 'aria-controls': 'lang-panel' });
  const box = h('div', { class: 'box' }, toggle, named, panel);
  const body = h('body', {}, box);
  install(h('html', {}, body));
  named.onClick = toggler(panel, null);
  const walk = await api.tfOpenerWalk([seed], 2);
  const close = await api.tfOpenerClose();
  return { walk, close, toggleClicks: toggle.clicks, namedClicks: named.clicks };
};

scenarios.open_only_modal = async () => {
  // Case (a): the opener only opens; a second click re-invokes open. Escape
  // is what closes it.
  const seed = h('span', { class: 'modal-text' });
  const modal = h('div', { class: 'lang-modal', rendered: false }, seed);
  const opener = h('button', { type: 'button', class: 'btn-selected' });
  const box = h('div', { class: 'box' }, opener, modal);
  const body = h('body', {}, box);
  const { html } = install(h('html', {}, body));
  opener.onClick = () => { modal.rendered = true; };
  html.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') modal.rendered = false; });
  const walk = await api.tfOpenerWalk([seed], 2);
  const close = await api.tfOpenerClose();
  return { walk, close, openerClicks: opener.clicks, modalRenderedAfterClose: modal.isRendered() };
};

scenarios.outside_click_closes = async () => {
  const seed = h('span', { class: 'panel-text' });
  const panel = h('div', { class: 'lang-panel', rendered: false }, seed);
  const opener = h('button', { type: 'button', class: 'btn-selected' });
  const box = h('div', { class: 'box' }, opener, panel);
  const body = h('body', {}, box);
  const { html } = install(h('html', {}, body));
  opener.onClick = () => { panel.rendered = true; };
  html.addEventListener('mousedown', (ev) => { if (!panel.contains(ev.target)) panel.rendered = false; });
  const walk = await api.tfOpenerWalk([seed], 2);
  const close = await api.tfOpenerClose();
  return { walk, close, openerClicks: opener.clicks, panelRenderedAfterClose: panel.isRendered() };
};

scenarios.step_advancing_stops_walk = async () => {
  // Case (b): a stepper whose label escapes the never-click list. It never
  // reveals the target and every click advances; the walk must stop after
  // one attempt and report the page as not handed back.
  const seed = h('span', { class: 'hidden-note', rendered: false });
  const stepper = h('div', { class: 'stepper step-0' });
  const go = h('button', { type: 'button', class: 'stepper-go' });
  stepper.append(go);
  stepper.append(seed);
  const other = h('button', { type: 'button', class: 'other-toggle' });
  const outer = h('div', { class: 'outer' }, stepper, other);
  const body = h('body', {}, outer);
  install(h('html', {}, body));
  let step = 0;
  go.onClick = () => { step += 1; stepper.attrs.class = 'stepper step-' + step; };
  const walk = await api.tfOpenerWalk([seed], 2);
  return { walk, goClicks: go.clicks, otherClicks: other.clicks, step };
};

scenarios.radio_tab_never_clicked = async () => {
  // Case (c): tabs are selection controls; re-click does not restore tab A.
  const seed = h('span', { class: 'tab-b-text' });
  const panelB = h('div', { role: 'tabpanel', id: 'panel-b', rendered: false }, seed);
  const tabA = h('button', { type: 'button', role: 'tab', 'aria-selected': 'true', class: 'tab' });
  const tabB = h('button', { type: 'button', role: 'tab', 'aria-selected': 'false', class: 'tab', 'aria-controls': 'panel-b' });
  const tablist = h('div', { role: 'tablist' }, tabA, tabB);
  const tabs = h('div', { class: 'tabs' }, tablist, h('div', { role: 'tabpanel', id: 'panel-a' }), panelB);
  const body = h('body', {}, tabs);
  install(h('html', {}, body));
  tabB.onClick = () => { panelB.rendered = true; };
  const walk = await api.tfOpenerWalk([seed], 2);
  return { walk, tabAClicks: tabA.clicks, tabBClicks: tabB.clicks, refusal: api.tfRefusal(tabB, seed) };
};

scenarios.no_reclick_across_levels = async () => {
  // The same failing control is in scope at every level above it; it must be
  // clicked for one attempt only (open + toggle back), never again.
  const seed = h('span', { class: 'note', rendered: false });
  const side = h('div', { class: 'side-panel', rendered: false });
  const x = h('button', { type: 'button', class: 'side-toggle' });
  const inner = h('div', { class: 'inner' }, x, side, seed);
  const mid = h('div', { class: 'mid' }, inner);
  const outer = h('div', { class: 'outer' }, mid);
  const body = h('body', {}, outer);
  install(h('html', {}, body));
  x.onClick = toggler(side, null);
  const walk = await api.tfOpenerWalk([seed], 2);
  return { walk, xClicks: x.clicks, sideRendered: side.isRendered() };
};

scenarios.visible_then_blind_restore = async () => {
  // Two failing openers. The inner one toggles a class on an ancestor of the
  // seed (the fingerprint sees it move and come back); the outer one shows a
  // sibling through an inline style only (invisible to the fingerprint, whose
  // "restore" is therefore vacuous). Both report restored:'toggle'; only the
  // first may be trusted, and the walk says which through fpChanged.
  const seed = h('span', { class: 'note', rendered: false });
  const pVisible = h('div', { class: 'p-visible', rendered: false });
  const bVisible = h('button', { type: 'button', class: 'visible-toggle' });
  const inner = h('div', { class: 'inner' }, bVisible, pVisible, seed);
  const pBlind = h('div', { class: 'p-blind', rendered: false });
  const bBlind = h('button', { type: 'button', class: 'blind-toggle' });
  const outer = h('div', { class: 'outer' }, bBlind, pBlind, inner);
  const body = h('body', {}, outer);
  install(h('html', {}, body));
  bVisible.onClick = toggler(pVisible, inner);
  bBlind.onClick = toggler(pBlind, null);
  const walk = await api.tfOpenerWalk([seed], 2);
  // Nothing was revealed, so the close eval has nothing to undo: the shell
  // relies on this when it hands every unrevealed attempt back by navigate.
  const close = await api.tfOpenerClose();
  return { walk, close, innerClass: inner.attrs.class, blindRendered: pBlind.isRendered() };
};

scenarios.settled_reading = async () => {
  // tfSettled accepts a reading only once two consecutive samples agree.
  const slow = [{ height: 10 }, { height: 20 }, { height: 30 }, { height: 40 }, { height: 40 }, { height: 40 }];
  let i = 0;
  const opening = await api.tfSettled(() => slow[Math.min(i++, slow.length - 1)], 250, 2000);
  let j = 0;
  const marquee = await api.tfSettled(() => ({ transform: 'translateX(' + (j++) + 'px)' }), 250, 2000);
  let k = 0;
  const still = await api.tfSettled(() => { k += 1; return { height: 40 }; }, 250, 2000);
  return { opening, marquee, still, stillPolls: k };
};

scenarios.attempt_cap = async () => {
  const seed = h('span', { class: 'note', rendered: false });
  const p1 = h('div', { class: 'p1', rendered: false });
  const b1 = h('button', { type: 'button', class: 'first-toggle' });
  const l1 = h('div', { class: 'l1' }, b1, p1, seed);
  const p2 = h('div', { class: 'p2', rendered: false });
  const b2 = h('button', { type: 'button', class: 'second-toggle' });
  const l2 = h('div', { class: 'l2' }, b2, p2, l1);
  const p3 = h('div', { class: 'p3', rendered: false });
  const b3 = h('button', { type: 'button', class: 'third-toggle' });
  const l3 = h('div', { class: 'l3' }, b3, p3, l2);
  const body = h('body', {}, l3);
  install(h('html', {}, body));
  b1.onClick = toggler(p1, null);
  b2.onClick = toggler(p2, null);
  b3.onClick = toggler(p3, null);
  const walk = await api.tfOpenerWalk([seed], 2);
  return { walk, clicks: [b1.clicks, b2.clicks, b3.clicks] };
};

scenarios.scroll_lock_residue = async () => {
  // Toggling back hides the panel but leaves a body scroll-lock class behind;
  // the fingerprint must reject that hand-back and escalate.
  const seed = h('span', { class: 'drawer-text' });
  const drawer = h('div', { class: 'lang-drawer', rendered: false }, seed);
  const opener = h('button', { type: 'button', class: 'btn-selected' });
  const box = h('div', { class: 'box' }, opener, drawer);
  const body = h('body', { class: 'page' }, box);
  const { html } = install(h('html', {}, body));
  opener.onClick = () => { drawer.rendered = !drawer.rendered; body.attrs.class = 'page no-scroll'; };
  html.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') { drawer.rendered = false; body.attrs.class = 'page'; } });
  const walk = await api.tfOpenerWalk([seed], 2);
  const close = await api.tfOpenerClose();
  return { walk, close, bodyClassAfterClose: body.attrs.class };
};

scenarios.details_summary = async () => {
  const seed = h('a', { href: '/x', class: 'faq-link' });
  const content = h('div', { class: 'faq-body', rendered: false }, seed);
  const summary = h('summary', { class: 'faq-q' });
  const details = h('details', {}, summary, content);
  const body = h('body', {}, details);
  install(h('html', {}, body));
  summary.onClick = () => { details.open = !details.open; };
  details.onOpenChange = (v) => { content.rendered = v; };
  const walk = await api.tfOpenerWalk([seed], 2);
  const close = await api.tfOpenerClose();
  return { walk, close, openAfterClose: details.open, summaryClicks: summary.clicks };
};

scenarios.already_rendered_noop = async () => {
  const p = navercorpHeader();
  p.list.rendered = true;
  const walk = await api.tfOpenerWalk([p.seedKo, p.seedEn], 2);
  return { walk, selectedClicks: p.selected.clicks };
};

(async () => {
  const out = {};
  for (const [name, fn] of Object.entries(scenarios)) {
    try {
      out[name] = await fn();
    } catch (err) {
      out[name] = { error: String(err && err.stack || err) };
    }
  }
  process.stdout.write(JSON.stringify(out));
})();
