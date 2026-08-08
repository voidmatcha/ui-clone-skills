# Webflow IX2 — Step 2-W (Detection & Extraction)

Webflow Interactions 2.0 (IX2) is Webflow's proprietary scroll/hover/click animation engine. It is NOT GSAP. It does NOT use `ScrollTrigger`. Standard scroll-trigger detection from `bundle-analysis.md` will silently miss every IX2-driven effect.

If you skip this file on a Webflow site, you will lose: every scroll-driven reveal, every parallax, every rotate-on-scroll, every fade-on-scroll, every section enter animation. The DOM will look static even though the original is densely animated.

## Step W-1: Detect IX2

Run this eval immediately after `agent-browser --session <s> open` and splash completion. **MUST be Step 0.5 — before any other extraction** when Webflow generator is present.

```bash
agent-browser --session <s> eval "(() => {
  const meta = document.querySelector('meta[name=generator]')?.content || '';
  const isWebflow = meta.includes('Webflow') || !!document.querySelector('html[data-wf-page], html[data-wf-site]');
  if (!isWebflow) return JSON.stringify({ isWebflow: false });

  // Match BOTH markers: classic IX2 stamps `w-mod-ix2`, newer builds `w-mod-ix3`.
  // Matching only ix3 silently missed every classic-IX2 site's hide-rule
  // inventory (initial opacity:0 / translate states), so candidates fell back to
  // bare [data-w-id] without their captured initial states.
  const ix2Active =
    document.documentElement.classList.contains('w-mod-ix2') ||
    document.documentElement.classList.contains('w-mod-ix3');
  const wIdElements = document.querySelectorAll('[data-w-id]');
  const wTargetElements = document.querySelectorAll('[data-wf-target]');

  // Hide rule pattern: <style>html.w-mod-js:not(.w-mod-ix[23]) :is(...selectors...) { display:none/opacity:0; visibility:hidden; ... }</style>
  // This is the giant inline <style> in the <head> that hides IX-targeted elements
  // until the IX engine adds 'w-mod-ix2'/'w-mod-ix3' to <html>.
  const hideRuleStyle = [...document.querySelectorAll('style')].find(s =>
    /w-mod-js:not\(\.w-mod-ix[23]\)/.test(s.textContent || '')
  );
  const hideRuleLength = hideRuleStyle?.textContent.length || 0;

  // Webflow IX2 stores its compiled timeline JSON either:
  //   1) Inline in webflow.js (string blob)
  //   2) On the html element via data-wf-page → fetched from /pages/<id>.json
  //   3) As Webflow.require('ix2') runtime config
  const wfPageId = document.documentElement.getAttribute('data-wf-page');
  const wfSiteId = document.documentElement.getAttribute('data-wf-site');

  return JSON.stringify({
    isWebflow: true,
    ix2Active,
    wfPageId, wfSiteId,
    wIdCount: wIdElements.length,
    wTargetCount: wTargetElements.length,
    hideRuleLength,
    hasHideRule: !!hideRuleStyle,
  });
})()" > tmp/ref/<c>/webflow-detection.json
```

`isWebflow: true` → run the full Step W pipeline. `false` → skip this file.

`hideRuleLength` typically exceeds 5,000 characters on IX2-heavy pages (observed ~12,000 on portfolio refs in the field). Every element selector listed in this rule is hidden until IX2 reveals it. Treat this rule as the **inventory of scroll-revealed elements**.

## Step W-2: Extract the hide-rule selector list

```bash
agent-browser --session <s> eval "(() => {
  const style = [...document.querySelectorAll('style')].find(s =>
    /w-mod-js:not\(\.w-mod-ix[23]\)/.test(s.textContent || '')
  );
  if (!style) return JSON.stringify({ error: 'no hide rule' });
  const text = style.textContent;
  // Match the :is(...) selector list
  const isMatch = text.match(/:is\\(([^)]+)\\)/);
  if (!isMatch) return JSON.stringify({ error: 'no :is selector' });
  const selectorList = isMatch[1].split(',').map(s => s.trim()).filter(Boolean);
  // Match the declaration block (display:none / opacity:0 / visibility:hidden / transform:...)
  const blockMatch = text.match(/\\}\\s*([^{}]+)\\{([^}]+)\\}/);
  return JSON.stringify({
    selectorCount: selectorList.length,
    selectors: selectorList,
    initialDeclarations: blockMatch ? blockMatch[2].trim() : null,
  });
})()" > tmp/ref/<c>/webflow-hide-rule.json
```

The selector list reveals every element IX2 will animate. Many use `[data-wf-target*='[...id...]']` — the ID matches a `data-w-id` somewhere in the DOM.

**Save:** `tmp/ref/<c>/webflow-hide-rule.json` — list of ALL IX2 targets and their initial state (display:none, opacity:0, transform:translate3d(0,2em,0), visibility:hidden, etc.).

## Step W-3: Extract IX2 page timeline JSON

Webflow IX2 fetches a per-page JSON containing every interaction definition. The fetch is automatic on page load — capture it via the performance API or refetch directly.

```bash
agent-browser --session <s> eval "(() => {
  const wfSite = document.documentElement.getAttribute('data-wf-site');
  const wfPage = document.documentElement.getAttribute('data-wf-page');
  if (!wfSite || !wfPage) return JSON.stringify({ error: 'missing wf ids' });

  // Method A: scrape from performance entries (most reliable)
  const entries = performance.getEntriesByType('resource')
    .filter(e => e.name.includes('/pages/') && e.name.endsWith('.json'))
    .map(e => e.name);

  return JSON.stringify({ wfSite, wfPage, candidateUrls: entries });
})()" > tmp/ref/<c>/webflow-ix2-urls.json
```

If the candidate URL exists, download it:

```bash
URL=$(jq -r '.candidateUrls[0]' tmp/ref/<c>/webflow-ix2-urls.json)
[ -n "$URL" ] && [ "$URL" != "null" ] && curl -sL "$URL" -o tmp/ref/<c>/webflow-ix2.json
```

If no `pages/<id>.json` URL exists in network entries, the IX2 timeline is **inline** in `webflow.js` or as a `Webflow.require('ix2').actionLists` global. Try:

```bash
agent-browser --session <s> eval "(() => {
  const ix2 = window.Webflow?.require?.('ix2');
  if (!ix2) return JSON.stringify({ error: 'ix2 not exposed' });
  const data = ix2.store?.getState?.() || ix2.actionLists || null;
  if (!data) return JSON.stringify({ error: 'no state/actionLists' });
  return JSON.stringify(data, null, 2);
})()" > tmp/ref/<c>/webflow-ix2.json
```

The output (whether from `.json` or runtime) contains:
- `actionLists[id]` — array of timeline keyframes
- `eventsState[id]` — trigger config (scroll/click/hover/intersection)
- Each event has a `target.selector` (matches a `data-w-id` element) and `actionItems` with `actionTypeId` ('TRANSFORM_MOVE', 'TRANSFORM_ROTATE', 'STYLE_OPACITY', 'GENERAL_DISPLAY', etc.) plus `value` and `easing`.

## Step W-4: Multi-position scroll-driven measurement

For IX2 sites, the bundle JSON tells you what *should* happen but the easiest way to extract exact transform curves is multi-position measurement of the live page. Use this when bundle extraction fails or for verification.

```bash
# Reset to top
agent-browser --session <s> eval "window.scrollTo(0, 0)"
sleep 1.5

# Build candidate selector list from webflow-hide-rule.json or animated-detected.json
SELECTORS='.pill-scroll,.circle-left-scroll,.hex-scroll,.circle-center-scroll,.circle-plus-scroll,.front-folder,.jm-siluete-img,.dark-jm-img,.light-jm-img'

# Sample at N scroll positions across page
TOTAL=$(agent-browser --session <s> eval "document.body.scrollHeight" | tail -1 | tr -d '"')
STEP=$((TOTAL / 10))

for i in 0 1 2 3 4 5 6 7 8 9; do
  Y=$((STEP * i))
  agent-browser --session <s> eval "
    (async () => {
      // Lenis-aware scroll: use mouse wheel events, not scrollTo
      let cur = window.scrollY;
      while (Math.abs(cur - $Y) > 50) {
        const delta = $Y - cur;
        const step = Math.sign(delta) * Math.min(Math.abs(delta), 800);
        window.dispatchEvent(new WheelEvent('wheel', { deltaY: step, bubbles: true }));
        await new Promise(r => setTimeout(r, 50));
        cur = window.scrollY;
      }
      await new Promise(r => setTimeout(r, 600));
      const sels = '$SELECTORS'.split(',');
      const out = {};
      for (const s of sels) {
        const el = document.querySelector(s);
        if (!el) { out[s] = null; continue; }
        const cs = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        out[s] = { transform: cs.transform, opacity: cs.opacity, y_view: r.top, y_doc: r.top + window.scrollY };
      }
      return JSON.stringify({ scrollY: window.scrollY, items: out }, null, 2);
    })()
  " > tmp/ref/<c>/scroll-pos-$i.json
done
```

Now you have 10 transform snapshots across the scroll range. Decode each `matrix(...)` to derive rotation, translation, scale per scroll position. Plot to find each element's scroll-progress curve.

`matrix(a, b, c, d, e, f)` decodes as:
- rotation: `Math.atan2(b, a) * 180 / Math.PI`
- scaleX: `sqrt(a*a + b*b)`
- scaleY: `sqrt(c*c + d*d)`
- translateX: `e`, translateY: `f`

For `matrix3d(...)`, the 16 values include 3D rotation/translation. Pre-multiply the 4x4 to extract Euler angles when needed.

## Step W-5: Map IX2 effects → @beyond / framework

Standard mappings:

| IX2 actionTypeId | What | Replacement |
|---|---|---|
| TRANSFORM_MOVE | translate X/Y/Z over scroll/click | `useScroll` + `useTransform` (scroll), `useAnimate` (click) |
| TRANSFORM_ROTATE | rotate X/Y/Z over scroll/click | same — `transform: rotate(${useTransform(scroll, ...)}deg)` |
| TRANSFORM_SCALE | scale over scroll | `useScroll` + `useTransform` |
| STYLE_OPACITY | opacity fade | `useScrollTrigger` (intersection) OR `useScroll` (scrubbed) |
| STYLE_BACKGROUND | bg color shift | `useScrollTrigger` + class toggle |
| GENERAL_DISPLAY | display none → block | `useScrollTrigger` + state toggle |
| PLUGIN_LOTTIE | scrub lottie via scroll | `lottie-react` with progress prop, or skip |

**Trigger mapping:**

| IX2 trigger | When | Equivalent |
|---|---|---|
| `SCROLL_INTO_VIEW` | element enters viewport (one-shot) | `useScrollTrigger({ once: true })` |
| `SCROLL_OUT_VIEW` | element leaves viewport | `useScrollTrigger({ once: false })` + state |
| `SCROLLING_IN_VIEW` | continuous scroll-driven (scrubbed) | `useScroll` + `useTransform`, NOT useScrollTrigger |
| `MOUSE_OVER` / `MOUSE_OUT` | hover | CSS `:hover` or `useState` |
| `MOUSE_CLICK` | click | `onClick` handler |
| `MOUSE_MOVE` | mousemove (parallax/tilt) | RAF loop on mouse position |

**Critical:** `SCROLLING_IN_VIEW` is the IX2 equivalent of GSAP `scrub: true`. It runs every scroll frame and interpolates progress 0→1 from element entering to leaving the trigger zone. **Do NOT confuse with intersection-based reveals.** Most decorative shapes, parallax, and continuous transforms use `SCROLLING_IN_VIEW`.

## Step W-6: Generate component code

For each IX2 target found:
1. Find its `data-w-id` in the HTML
2. Look up the action list in `webflow-ix2.json`
3. Determine trigger type
4. Pick the @beyond hook from Step W-5 mapping
5. For continuous scroll effects: use `useScroll` + `useTransform`, NEVER one-shot `useScrollTrigger`
6. Match the easing — IX2 default is `outQuart` ≈ cubic-bezier(0.25, 1, 0.5, 1). Custom eases live in the action item's `easing` field.

## Anti-skip checklist for Webflow sites

- [ ] `webflow-detection.json` exists and `isWebflow: true`
- [ ] `webflow-hide-rule.json` lists ALL IX2 targets (selector count > 0)
- [ ] `webflow-ix2.json` saved (from `pages/<id>.json` OR `Webflow.require('ix2')`)
- [ ] At least 10 scroll-position snapshots in `scroll-pos-*.json`
- [ ] `transition-spec.json` includes a `webflowIx2` block listing each target's selector + actionTypeId + trigger + value range
- [ ] **Action count parity**: for each timeline, `transition-spec.json` impl-action count === `actionItems.length` from the timeline JSON. Off-by-one = a target that never animates.
- [ ] Component generation imports the IX2 mapping table (Step W-5) and uses `useScroll` + `useTransform` for continuous effects, NOT `useScrollTrigger`

### Action enumeration completeness check

IX3 timelines bundle multiple targets per timeline. Visually-obvious targets (image translate, line draw) are easy to spot; per-line text reveals via `splitText.mask='lines'` look identical to static text in a screenshot. **Enumerate every entry in `actionItems[]` from the timeline JSON, not just what looks animated.**

```bash
# After Step W-3, verify every action has a target in your impl
jq '.actionItems[] | { actionTypeId, target: .config.target.selector, splitText: .config.splitText }' \
  tmp/ref/<c>/timeline-t-<id>.json
```

Common missed actions:
- `actionTypeId: "STYLE_OPACITY"` with `splitText: { type: "lines", mask: "lines" }` — wraps each line in a mask + animates per-line. Looks identical to static text in a frame.
- `actionTypeId: "TRANSFORM_MOVE"` on `* within .grid-cell` — `*` selector targets every descendant; easy to miss when scanning by class name.
- Any action whose `config.target.selector` is `*` or contains `within` — these are wildcard/scope selectors, not single elements.

## Common Webflow IX2 misses (real failures observed across portfolio refs)

| Class | What was missed | Why |
|---|---|---|
| `.pill-scroll`, `.circle-*-scroll`, `.hex-scroll` | Scroll-driven rotation 0°→60°+ | DOM inspection only saw current static transform; no scroll progress sampled |
| `.front-folder` | matrix3d rotateX on scroll | `matrix3d` is invisible to 2D `transform: rotate()` extraction |
| `.jm-siluete-img`, `.dark-jm-img` | translateY on scroll within section | IX2 `SCROLLING_IN_VIEW` not classified as scroll-driven |
| `.light-jm-img` | opacity 0→1 on scroll progress | Treated as static (default opacity) without scroll sampling |
| `.line.step1`, `.line-step2` | width 0→full draw animation | width transitions on scroll progress not captured by transition-only detection |
| Section enter reveals (h2-step1-1, h2-step1-2, h2-step1-3) | Translate + opacity reveal as section enters | Pre-render hide rule missed → element hidden by default → wrongly assumed static |

If your clone has any of the above patterns invisible/static while the original animates them, you skipped Step W. Go back and run it.
