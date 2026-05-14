# Dual-Snapshot DOM Extraction — Step 2.6-pre

Read this sub-doc when the site has a splash/preloader (detected in Step 5c-a bundle analysis or by `hasPreloader` in interactions). Otherwise skip — dual-snapshot is wasted work on sites whose pre-/post-load DOM state is identical.

Sites with splash/preloader animations have **two distinct DOM states**:

| State | When | What's different |
|---|---|---|
| **Pre-splash** (loading) | Page load, before intro animation completes | GSAP bakes `visibility:hidden`, `opacity:0`, `transform:translate(-500px)` as inline styles. Runtime transitions NOT yet injected. |
| **Post-splash** (idle) | After intro animation completes (~5-8s) | Inline styles cleared/updated. Webflow interactions inject `transition` properties at runtime. Classes toggled (e.g., `html.rk-preloading` → removed). |

**If you only extract at ONE timepoint, you miss half the data:**
- Extract during splash → get GSAP-baked init styles but miss runtime transitions
- Extract after splash → get final state but miss which properties were animated (can't distinguish "always visible" from "revealed by animation")

## Procedure

```bash
# 1. IMMEDIATELY after page load (within 1s, before splash finishes)
agent-browser open <url> --session <s>
agent-browser wait 500 --session <s>

# Extract pre-splash state
agent-browser eval --session <s> "
(() => {
  const snapshot = {};
  document.querySelectorAll('*').forEach(el => {
    const s = getComputedStyle(el);
    const inline = el.style.cssText;
    if (!inline && s.transition === 'all 0s ease 0s') return;
    const cn = typeof el.className === 'string' ? el.className : '';
    const key = el.tagName + '.' + cn.trim().split(/\s+/)[0];
    if (snapshot[key]) return;
    snapshot[key] = {
      inlineStyle: inline || null,
      transition: s.transition !== 'all 0s ease 0s' ? s.transition : null,
      visibility: s.visibility,
      opacity: s.opacity,
      transform: s.transform !== 'none' ? s.transform : null,
      display: s.display,
    };
  });
  return JSON.stringify({
    timestamp: 'pre-splash',
    htmlClass: document.documentElement.className,
    bodyClass: document.body.className,
    elements: snapshot,
  }, null, 2);
})()
"
# Save to tmp/ref/<component>/dom-state-pre-splash.json

# 2. AFTER splash completes (wait for full duration + 1s buffer)
agent-browser wait 8000 --session <s>

# Extract post-splash state (same eval)
# Save to tmp/ref/<component>/dom-state-post-splash.json
```

## Diff analysis

```bash
node -e "
const pre = JSON.parse(require('fs').readFileSync('./tmp/ref/<component>/dom-state-pre-splash.json'));
const post = JSON.parse(require('fs').readFileSync('./tmp/ref/<component>/dom-state-post-splash.json'));

const diffs = {};
for (const key of new Set([...Object.keys(pre.elements), ...Object.keys(post.elements)])) {
  const a = pre.elements[key] || {};
  const b = post.elements[key] || {};
  const changes = {};
  for (const prop of ['inlineStyle', 'transition', 'visibility', 'opacity', 'transform', 'display']) {
    if (a[prop] !== b[prop]) changes[prop] = { pre: a[prop], post: b[prop] };
  }
  if (Object.keys(changes).length > 0) diffs[key] = changes;
}

console.log(JSON.stringify({
  htmlClassChanged: pre.htmlClass !== post.htmlClass,
  bodyClassChanged: pre.bodyClass !== post.bodyClass,
  preHtmlClass: pre.htmlClass,
  postHtmlClass: post.htmlClass,
  elementDiffs: diffs,
}, null, 2));
" > tmp/ref/<component>/dom-state-diff.json
```

**Save to** `tmp/ref/<component>/dom-state-diff.json`

## What this reveals

- **`transition` appeared in post but not pre** → Webflow runtime injection. Must add to globals.css manually (these transitions are NOT in the downloaded CSS files).
- **`inlineStyle` cleared in post** → GSAP animation completed and cleaned up. These are the "init states" to reset.
- **`visibility` or `opacity` changed** → Element was revealed by splash animation.
- **`htmlClass` changed** → Preloader class removed (e.g., `rk-preloading`). Body-level state transition.

⛔ **Gate:** If site has a splash/preloader (detected in Step 5c-a bundle analysis), `dom-state-diff.json` MUST exist before proceeding to Step 3 (style extraction). Without it, runtime-injected transitions will be silently missed.

---

After this step, return to `dom-extraction.md` Step 2.6a (catalog GSAP-baked inline styles).
