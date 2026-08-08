(() => {
  const target = document.querySelector(SELECTOR_PLACEHOLDER);
  if (!target) return JSON.stringify({ error: 'selector not found' });
  const directText = (el) => {
    let t = '';
    for (const n of el.childNodes) {
      // Text nodes: collapse all whitespace (incl. source-format newlines) to
      // a single space so only real <br> elements become line breaks.
      if (n.nodeType === 3) t += n.textContent.replace(/\s+/g, ' ');
      else if (n.nodeType === 1 && n.tagName === 'BR') t += '\n';
    }
    return t.replace(/[ \t]*\n[ \t]*/g, '\n').replace(/[ \t]{2,}/g, ' ').trim().slice(0, 2000);
  };
  const safeClassName = (value) => {
    const tokens = String(value || '').trim().split(/\s+/).filter(Boolean);
    let out = '';
    for (const token of tokens) {
      const next = out ? `${out} ${token}` : token;
      if (next.length > 2000) break;
      out = next;
    }
    return out;
  };
  const LAYOUT_PROPS = [
    'display','position','top','left','right','bottom',
    'width','height','min-width','max-width','min-height','max-height',
    // Fix 93 (B3) — aspect-ratio round-trips losslessly: getComputedStyle returns
    // it as an author ratio (e.g. "16 / 9"), NOT px-resolved, so capturing it
    // preserves intrinsic sizing without any relativity inference.
    'aspect-ratio',
    // box-sizing must be captured: getComputedStyle().height on a border-box
    // element is a border-box px value (padding INSIDE). Re-emitting that height
    // without box-sizing:border-box (content-box default) adds the padding on
    // top, inflating every padded section by its vertical padding (navercorp B1:
    // each section grew by exactly padT+padB). Capturing it lets the transpiler
    // reproduce the reference box model faithfully.
    'box-sizing',
    'padding','margin','border-radius','border',
    'background-color','background-image','background-size','background-position',
    'color','font-family','font-size','font-weight','line-height','letter-spacing',
    'text-align','text-decoration','text-transform','white-space','vertical-align',
    'transform','opacity','overflow',
    'flex','flex-direction','justify-content','align-items','gap',
    'grid-template-columns','grid-template-rows',
    // Grid ITEM placement. The two lines above capture the grid CONTAINER's
    // track sizing, but a child's explicit placement (e.g. a hero carousel
    // spanning grid-row:1/4 down the left column) lives on the child. Without
    // it, grid auto-flow scatters every child into default cells and
    // overlapping siblings collapse onto each other (navercorp hero: the
    // right-rail banner cards fell onto the carousel). getComputedStyle
    // resolves an auto-placed item to "auto" (already filtered by NOISE), so
    // only real placements are captured — no per-node bloat. grid-area/order/
    // grid-auto-* are deliberately excluded: grid-area's serialization
    // (e.g. "1 / auto / 4") conflicts with row+column, and order/grid-auto-flow
    // default to "0"/"row" which NOISE cannot filter without also dropping the
    // meaningful flex-direction:row and zero values on other properties.
    'grid-row','grid-column',
    'z-index','box-shadow',
    // Fix 16 — transition + animation. The transpiler emits each captured
    // value as a property inside style={{ ... }} so the impl renders the
    // same hover/focus/active transitions as the ref. NOISE filters out
    // the user-agent defaults ('none', 'all 0s ease 0s', etc.) so only
    // ref-authored transitions reach the JSX.
    'transition','transition-property','transition-duration',
    'transition-timing-function','transition-delay',
    'animation','animation-name','animation-duration',
    'animation-timing-function','animation-delay',
    'animation-iteration-count','animation-direction',
    'animation-fill-mode','animation-play-state',
    'cursor','pointer-events',
  ];
  const NOISE = new Set([
    '', 'normal', 'none', 'auto', '0px', 'rgba(0, 0, 0, 0)', 'visible', 'start',
    // An unplaced grid item serializes grid-row/grid-column as the two-token
    // 'auto / auto' in some engines (single-token 'auto' above covers the rest).
    // Grid-only string, collides with no other LAYOUT_PROPS value, so a global
    // NOISE entry safely drops the default without a per-property guard.
    'auto / auto',
    // Fix 16 — user-agent defaults for transition/animation. Without these
    // every node would carry a noisy 'all 0s ease 0s' transition value.
    'all 0s ease 0s', 'all', '0s', 'ease', '1', 'running', 'forwards', 'backwards',
  ]);
  // Fix 19 — :hover rule extraction. Walks document.styleSheets, matches each
  // full selector (including variant attributes and ancestor constraints)
  // against the element, and pulls the LAYOUT_PROPS subset from declarations so the
  // transpiler emits matching CSS that lets the captured transition values
  // actually animate something. Without this Fix 16's transition properties
  // exist but have nothing to interpolate to — the impl stays static under
  // hover. Tries each sheet under try/catch since cross-origin stylesheets
  // throw on cssRules access. A first-class-only matcher used to merge every
  // Primer button variant into the same node: an invisible button inherited an
  // inactive button's color and box-shadow. Full selector matching prevents
  // that cross-variant contamination.
  let HOVER_RULES = null;  // lazy initialized
  const splitSelectorList = (selectorText) => {
    const out = [];
    let current = '';
    let depth = 0;
    for (const char of selectorText) {
      if (char === '(' || char === '[') depth += 1;
      else if ((char === ')' || char === ']') && depth > 0) depth -= 1;
      if (char === ',' && depth === 0) {
        if (current.trim()) out.push(current.trim());
        current = '';
      } else {
        current += char;
      }
    }
    if (current.trim()) out.push(current.trim());
    return out;
  };
  const buildHoverRules = () => {
    if (HOVER_RULES !== null) return HOVER_RULES;
    const finalCompound = (selector) => {
      let depth = 0;
      let start = 0;
      for (let i = 0; i < selector.length; i += 1) {
        const char = selector[i];
        if (char === '(' || char === '[') depth += 1;
        else if ((char === ')' || char === ']') && depth > 0) depth -= 1;
        else if (depth === 0 && (char === '>' || char === '+' || char === '~' || /\s/.test(char))) {
          while (i + 1 < selector.length && /\s/.test(selector[i + 1])) i += 1;
          start = i + 1;
        }
      }
      return selector.slice(start).trim();
    };
    const pseudoHoverTarget = (selector) => {
      const normalized = selector
        // CSSOM commonly canonicalizes legacy `:before`/`:after` selectors to
        // the double-colon form. Match either spelling exactly; replacing a
        // bare `:before` inside an existing `::before` creates `:::before` and
        // silently drops every materialized pseudo hover endpoint.
        .replace(/:{1,2}before\b/g, '::before')
        .replace(/:{1,2}after\b/g, '::after');
      const pseudo = normalized.match(/^(.*?)(::before|::after)\s*$/);
      if (!pseudo) return null;
      const baseSelector = pseudo[1].trim();
      const subject = finalCompound(baseSelector);
      // Conservative support: only same-subject hover pseudos are materialized.
      // `.card:hover .icon::after` is intentionally skipped because the pseudo
      // belongs to a descendant while the hover state belongs to an ancestor.
      if (!subject.includes(':hover')) return null;
      return {
        which: pseudo[2] === '::before' ? 'before' : 'after',
        matchSelector: baseSelector.replace(/:hover\b/g, ':is(*)'),
      };
    };
    const out = [];  // [{selector, matchSelector, decls, pseudo?: before|after}]
    const walkRules = (rules) => {
      if (!rules) return;
      for (const rule of rules) {
        // Recurse through active @media/@supports/layer groups. The old
        // top-level-only walk silently missed responsive hover rules.
        if (rule.cssRules && !rule.selectorText) {
          const kind = rule.constructor && rule.constructor.name;
          if (
            kind === 'CSSMediaRule'
            && rule.conditionText
            && !window.matchMedia(rule.conditionText).matches
          ) {
            continue;
          }
          if (
            kind === 'CSSSupportsRule'
            && rule.conditionText
            && typeof CSS !== 'undefined'
            && CSS.supports
          ) {
            try {
              if (!CSS.supports(rule.conditionText)) continue;
            } catch (e) {
              continue;
            }
          }
          walkRules(rule.cssRules);
          continue;
        }
        if (!rule.selectorText || !rule.style) continue;
        if (!rule.selectorText.includes(':hover')) continue;
        for (const selector of splitSelectorList(rule.selectorText)) {
          if (!selector.includes(':hover')) continue;
          // `:not(:hover)` describes the idle state, not a hover target.
          if (/:not\([^)]*:hover[^)]*\)/.test(selector)) continue;
          const pseudoHover = pseudoHoverTarget(selector);
          if (!pseudoHover && /:(?:before|after)\b|::/.test(selector)) continue;
          // Replace the state pseudo with an always-matching simple selector
          // while preserving attributes, :where/:is wrappers, combinators,
          // and descendant target structure for Element.matches().
          const matchSelector = pseudoHover
            ? pseudoHover.matchSelector
            : selector.replace(/:hover\b/g, ':is(*)');
          const decls = {};
          for (const p of LAYOUT_PROPS) {
            const v = rule.style.getPropertyValue(p);
            if (!v || (!pseudoHover && NOISE.has(v))) continue;
            const important = rule.style.getPropertyPriority(p) === 'important';
            decls[p] = (v + (important ? ' !important' : '')).slice(0, 800);
          }
          if (Object.keys(decls).length) {
            out.push({ selector, matchSelector, decls, pseudo: pseudoHover && pseudoHover.which });
          }
        }
      }
    };
    for (const sheet of document.styleSheets) {
      let rules;
      try { rules = sheet.cssRules || sheet.rules; } catch (e) { continue; }
      walkRules(rules);
    }
    HOVER_RULES = out;
    return out;
  };
  const captureHover = (el) => {
    const rules = buildHoverRules();
    const merged = {};
    for (const r of rules) {
      let matches = false;
      try { matches = el.matches(r.matchSelector); } catch (e) { matches = false; }
      if (matches && !r.pseudo) {
        Object.assign(merged, r.decls);
      }
    }
    return Object.keys(merged).length ? merged : null;
  };
  const capturePseudoHover = (el, which) => {
    const rules = buildHoverRules();
    const merged = {};
    for (const r of rules) {
      if (r.pseudo !== which) continue;
      let matches = false;
      try { matches = el.matches(r.matchSelector); } catch (e) { matches = false; }
      if (matches) {
        Object.assign(merged, r.decls);
      }
    }
    return Object.keys(merged).length ? merged : null;
  };
  // Fix 18 — pseudo-element capture. Helper extracts a non-empty subset of
  // LAYOUT_PROPS from a pseudo computed style, plus its `content` so the
  // transpiler can emit a <span data-pseudo="before" /> with matching styles
  // when the ref draws decorations via ::before / ::after (glow rings, icon
  // dots, gradient overlays, divider lines etc.). Without this the impl is
  // missing the entire pseudo-element layer — a dominant cause of the
  // "the impl doesn't capture the overall layout" failure mode.
  const capturePseudo = (el, which) => {
    const ps = getComputedStyle(el, which);
    const content = ps.getPropertyValue('content');
    if (!content || content === 'none' || content === 'normal') return null;
    const out = { content };
    for (const p of LAYOUT_PROPS) {
      const v = ps.getPropertyValue(p);
      if (v && !NOISE.has(v)) out[p] = v.slice(0, 800);
    }
    return out;
  };
  const SVG_TAGS = new Set([
    'svg','g','defs','use','symbol','marker','clippath','clip-path',
    'mask','pattern','filter','feblend','fecolormatrix',
    'fecomposite','fegaussianblur','femerge','femergenode','feoffset',
    'feflood','fetile','feturbulence','fedropshadow','fediffuselighting',
    'fespecularlighting','femorphology','feimage','fedisplacementmap',
    'lineargradient','linear-gradient','radialgradient','radial-gradient',
    'stop',
    'path','rect','circle','ellipse','line','polyline','polygon',
    'text','textpath','tspan','title','desc','foreignobject',
  ]);
  const SVG_ATTR_KEYS = [
    'id','viewBox','xmlns','xmlns:xlink',
    'fill','stroke','stroke-width','stroke-linecap','stroke-linejoin',
    'stroke-miterlimit','stroke-dasharray','stroke-dashoffset',
    'fill-rule','fill-opacity','clip-rule','clip-path','mask','filter',
    'opacity',
    'd','points','x','y','x1','y1','x2','y2','cx','cy','r','rx','ry',
    'width','height','transform','preserveAspectRatio',
    'offset','stop-color','stop-opacity',
    'gradientTransform','gradientUnits','spreadMethod',
    'href','xlink:href','xlink:title',
    'patternUnits','patternContentUnits','patternTransform',
    'markerUnits','refX','refY','orient','overflow',
    'in','in2','result','values','operator','mode','type',
    'stdDeviation','floodColor','floodOpacity',
  ];
  // SVG geometry commonly exceeds the ordinary 2k attribute envelope (GitHub
  // octicons can ship path `d` payloads above 2.3k). Preserve these bounded,
  // source-authored coordinate streams verbatim; ordinary attrs retain the
  // tighter cap and geometry above 20k is rejected rather than truncated.
  const SVG_GEOMETRY_ATTRS = new Set(['d', 'points']);
  const attrWithinLimit = (name, value) => (
    value && value.length < (SVG_GEOMETRY_ATTRS.has(name) ? 20000 : 2000)
  );
  const SVG_DEPTH_CAP = 30;
  const HTML_DEPTH_CAP = 10;

  const isSvgNode = (el) => {
    try {
      if (typeof SVGElement !== 'undefined' && el instanceof SVGElement) return true;
    } catch (e) { /* ignore */ }
    const tag = (el.tagName || '').toLowerCase();
    return SVG_TAGS.has(tag);
  };

  const MEDIA_TAGS = new Set(['img','source','picture','video','audio','track']);
  const TEXT_BREAK_TAGS = new Set(['br','wbr']);
  const DEPTH_CAP_BONUS = 8;  // #6 — bounded extra descent past the cap for content
  const STRUCTURED_DEPTH_CAP_BONUS = 16;
  const SEMANTIC_STRUCTURE_TAGS = new Set([
    'article','section','figure','figcaption',
    'h1','h2','h3','h4','h5','h6',
    'ol','ul','li','dl','dt','dd',
    'table','thead','tbody','tr','th','td',
  ]);
  // #10 — a past-cap wrapper that PAINTS is visually load-bearing even with no
  // text/media descendant: stat-grid bars, card-parallax layers and footer
  // column backers are empty divs whose only content is a fill/border. The
  // text-or-media keepsContent test dropped them, flattening those structures
  // (the 14/15-critical-section wall). Detect a non-transparent background
  // fill, a background-image, or a visible border. Still bounded by the hard
  // cap+BONUS drop below, so this only ADDS reachable painted nodes — it never
  // removes a node the old test kept, and empty transparent wrappers still go.
  const _colorOpaque = (c) => {
    if (!c || c === 'transparent') return false;
    const m = c.match(/^rgba?\(([^)]+)\)/);
    if (!m) return false;
    const parts = m[1].split(',').map((x) => x.trim());
    return parts.length < 4 ? true : parseFloat(parts[3]) > 0;
  };
  const _paintsSomething = (cs) => {
    if (_colorOpaque(cs.backgroundColor)) return true;
    if (cs.backgroundImage && cs.backgroundImage !== 'none') return true;
    return ['borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth']
      .some((p) => parseFloat(cs[p]) > 0);
  };
  const _hasSelector = (el, selector) => {
    try {
      return !!(el.querySelector && el.querySelector(selector));
    } catch (e) {
      return false;
    }
  };
  const _hasStructuredSemanticSubtree = (el) => (
    _hasSelector(el, 'img,picture,video,svg,canvas') &&
    _hasSelector(el, 'article,section,figure,figcaption,h1,h2,h3,h4,h5,h6,ol,ul,li,dl,dt,dd,table,thead,tbody,tr,th,td')
  );
  const extract = (el, depth = 0, insideSvg = false, structuredContext = false) => {
    const elIsSvg = insideSvg || isSvgNode(el);
    const cap = elIsSvg ? SVG_DEPTH_CAP : HTML_DEPTH_CAP;
    // U1 / #6 — past the depth cap keep only media leaves and subtrees that
    // still carry text OR wrap media. Deep React/Tailwind trees push a
    // <picture> to the cap depth (its <source>/<img> sit one level deeper →
    // zero-image clones), and nest real copy + split-text word spans past depth
    // 10 (clone text 6521 vs ref 8202). A commercial SPA also nests
    // IMAGE-ONLY containers deep — e.g. eBay's `dp-item-tile-image-zones`
    // (a product-image wrapper that establishes a CSS container-query context)
    // has no direct text, so the text-only check dropped it AND the <img>
    // inside it, silently breaking @container sizing. Keeping a subtree that
    // contains media (not just IS media) fixes that without unbounding bloat:
    // empty wrapper subtrees are still dropped and the hard bonus bound still
    // caps pathological depth.
    if (depth > cap) {
      const tagLc = (el.tagName || '').toLowerCase();
      const subtreeIsStructured = !elIsSvg && _hasStructuredSemanticSubtree(el);
      const inStructuredAllowance = !elIsSvg &&
        (structuredContext || subtreeIsStructured) &&
        depth <= cap + STRUCTURED_DEPTH_CAP_BONUS;
      const keepsContent = MEDIA_TAGS.has(tagLc) ||
        TEXT_BREAK_TAGS.has(tagLc) ||
        (inStructuredAllowance && SEMANTIC_STRUCTURE_TAGS.has(tagLc)) ||
        ((el.textContent || '').trim().length > 0) ||
        _hasSelector(el, 'img,picture,video,svg,canvas') ||
        _paintsSomething(getComputedStyle(el));  // #10 — painted structural wrapper
      const depthLimit = inStructuredAllowance
        ? cap + STRUCTURED_DEPTH_CAP_BONUS
        : cap + DEPTH_CAP_BONUS;
      if (!keepsContent || depth > depthLimit) return null;
      structuredContext = inStructuredAllowance;
    }
    const s = getComputedStyle(el);
    const text = directText(el);
    const styles = {};
    for (const p of LAYOUT_PROPS) {
      const v = s.getPropertyValue(p);
      // A 0px HEIGHT is a deliberate collapse (a visually-hidden skip-nav ul, a
      // height:0 clip box), not a UA default. Preserve it so the transpiler
      // reproduces the collapsed box; dropping it lets empty line-boxes render
      // at content height and push the whole page down. For every other property
      // 0px is the meaningful default and stays filtered as noise.
      const keepZeroHeight = p === 'height' && v === '0px';
      if (v && (keepZeroHeight || !NOISE.has(v))) styles[p] = v.slice(0, 800);
    }
    if (el.style && el.style.length) {
      for (let i = 0; i < el.style.length; i += 1) {
        const p = el.style[i] || (el.style.item && el.style.item(i));
        if (!p || !p.startsWith('--')) continue;
        const v = el.style.getPropertyValue(p);
        if (!v) continue;
        styles[p] = v.slice(0, 800);
      }
    }
    // Fix 22 — build children while recording inter-element whitespace. A
    // whitespace-only text node between two element siblings renders as a space
    // for inline content (word-split spans: <span>For</span> <span>the</span>).
    // directText collapses that text node into the parent and trims it away, so
    // without this flag the transpiler runs the words together ("Forthe").
    const kids = [];
    for (const c of Array.from(el.children)) {
      const k = extract(c, depth + 1, elIsSvg, structuredContext);
      if (!k) continue;
      let sib = c.nextSibling;
      while (sib && sib.nodeType === 3 && sib.textContent === '') sib = sib.nextSibling;
      const trailingChildSpace =
        c.nextElementSibling && /\s$/.test(c.textContent || '');
      if (
        trailingChildSpace ||
        (sib && sib.nodeType === 3 && /^\s+$/.test(sib.textContent))
      ) {
        k.wsAfter = true;
      }
      kids.push(k);
    }
    const out = {
      tag: el.tagName.toLowerCase(),
      class: safeClassName(
        typeof el.className === 'string' ? el.className : el.className?.baseVal || ''
      ),
      display: s.display,
      position: s.position,
      children: kids,
    };
    if (elIsSvg) out.svg = true;
    if (text) {
      out.text = text;
    } else if (!elIsSvg && el.children.length && !kids.length) {
      // At the hard depth boundary a text-bearing child can be pruned while
      // its parent still survives. Preserve the rendered aggregate on that
      // surviving leaf container instead of emitting an empty heading/link.
      const aggregate = (el.textContent || '')
        .replace(/\s+/g, ' ').trim().slice(0, 2000);
      if (aggregate) out.text = aggregate;
    }
    // Mid-text-span fidelity (loop-e2e-9): `text` joins DIRECT text nodes
    // only, so an inline child between two fragments ("treating <span>chronic
    // disease</span>—much") stores "treating —much" — an order no faithful
    // impl renders. Keep the live-rendered full string alongside the
    // fragments when inline element children carry text.
    if (text && !elIsSvg) {
      const hasInlineTextChild = Array.from(el.children).some(
        (c) => c.tagName !== 'BR' && (c.textContent || '').trim()
      );
      if (hasInlineTextChild) {
        const full = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 2000);
        if (full && full !== text) out.textFull = full;
      }
    }
    // F1 option-B: preserve text-node POSITION when direct text interleaves
    // with element children (navercorp ticker: "(<blind><num> <percent>)" —
    // the merged `text` "()" loses which side of .percent each paren sat on).
    // Strings = whitespace-collapsed text fragments, ints = index into
    // children[]. Stored only when the element-child count matched the
    // captured children 1:1, so the indexes are guaranteed to align; the
    // transpiler falls back to textFull alignment otherwise.
    if (text && kids.length && !elIsSvg) {
      const seq = []; let ei = 0; let sawText = false;
      for (const n of el.childNodes) {
        if (n.nodeType === 3) {
          const t = n.textContent.replace(/\s+/g, ' ');
          if (t.trim()) { seq.push(t); sawText = true; }
          else if (t && seq.length) seq.push(' ');
        } else if (n.nodeType === 1) {
          seq.push(ei); ei += 1;
        }
      }
      if (sawText && ei === kids.length) out.textSeq = seq;
    }
    if (Object.keys(styles).length) out.styles = styles;
    // Fix 18 — pseudo styles attached to the node so the transpiler can
    // synthesize <span data-pseudo> children with matching CSS.
    const before = capturePseudo(el, '::before');
    if (before) out.before_styles = before;
    const after = capturePseudo(el, '::after');
    if (after) out.after_styles = after;
    const beforeHover = capturePseudoHover(el, 'before');
    if (beforeHover) out.before_hover_styles = beforeHover;
    const afterHover = capturePseudoHover(el, 'after');
    if (afterHover) out.after_hover_styles = afterHover;
    // Fix 19 — :hover/:focus rule declarations matching this element's
    // class list, so the transpiler can emit a CSS rule that gives the
    // captured `transition` (Fix 16) something to animate to.
    const hover = captureHover(el);
    if (hover) out.hover_styles = hover;
    // Capture asset/link attrs so the transpiler can emit <img src>, <a href>,
    // <video poster>, etc. Without these the scaffold renders empty
    // placeholder boxes for every media element, which inflates section-compare
    // AE by ~700k per image-heavy section.
    // A-family: allow/allowfullscreen must survive capture — without iframe
    // allow, the Chrome permissions policy blocks autoplay in cross-origin
    // embeds (ebpb loop-1: all 3 Vimeo players stuck on poster+Play).
    const ATTR_KEYS = ['id','src','href','alt','poster','srcset','sizes','media','type','target','rel','aria-label','aria-haspopup','aria-expanded','title','role','allow','allowfullscreen','data-src','data-poster','data-srcset','data-lazy-src','data-original','data-lazy'];
    const keys = elIsSvg ? ATTR_KEYS.concat(SVG_ATTR_KEYS) : ATTR_KEYS;
    for (const k of keys) {
      const v = el.getAttribute ? el.getAttribute(k) : null;
      if (attrWithinLimit(k, v)) out[k] = v;
    }
    // G-family guard: record which un-bake props the REF element declares in
    // its OWN inline style attribute. Its inline beat its own CSS, so the
    // mirrored CSS value is NOT what rendered — the transpiler must never
    // un-bake these (framer/scrub controllers write inline width/height).
    if (el.style && el.style.length) {
      const ip = [];
      for (const p of [
        'top','right','bottom','left',
        'width','min-width','max-width','height','min-height','max-height',
        'padding-left','padding-right','padding-top','padding-bottom',
        'margin-left','margin-right',
        'grid-template-columns','grid-template-rows',
        'font-size','line-height','letter-spacing',
        'margin','padding','gap','row-gap','column-gap',
      ]) {
        if (el.style.getPropertyValue(p)) ip.push(p);
      }
      if (ip.length) out.inlineProps = ip;
    }
    // B-family: generic data-* hooks (animation state machines key on them,
    // e.g. word-reveal data-word-id) — capture them all; the transpiler
    // drops the lazy-loader subset (U1) and emits the rest verbatim.
    if (el.attributes) {
      for (const a of el.attributes) {
        const nm = a.name;
        if (!nm.startsWith('data-')) continue;
        const v = a.value;
        if (v && v.length < 2000 && !(nm in out)) out[nm] = v;
      }
    }
    // Universality audit HIGH FN: SVG attr whitelist drops
    // unfamiliar icon-system attrs silently. For SVG nodes, capture
    // EVERY attribute (subject to the same length cap), then the
    // JSX emitter can apply the kebab→camel rename to whatever it
    // sees. Attrs already in keys[] above are simply overwritten
    // with the same value — idempotent.
    if (elIsSvg && el.attributes) {
      for (const a of el.attributes) {
        const nm = a.name;
        // Skip standard HTML attrs already in keys[] and React-
        // unfriendly attrs starting with `on*` (event handlers).
        if (nm.startsWith('on')) continue;
        const v = a.value;
        if (attrWithinLimit(nm, v) && !(nm in out)) {
          out[nm] = v;
        }
      }
    }
    // Fix 122 — an inline <svg> with no viewBox AND no width/height attribute
    // has no intrinsic size, so a clone renders it at the CSS replaced-element
    // default of 300x150 (eBay: 42/56 icons ballooned to 150px tall, ~+844px
    // of phantom docH). The live page sizes these via a sprite <symbol> viewBox
    // or a CSS class the clone does not fully mirror; neither survives into the
    // scaffold. Bake the REAL rendered box (the ground truth at capture time) as
    // width/height attrs so the transpiler (which passes svg width/height
    // through) constrains them. Only the root <svg> establishes the replaced
    // box — inner <g>/<path> do not — so gate on tag === 'svg'.
    if (elIsSvg && out.tag === 'svg' && !out.viewBox && !out.width && !out.height) {
      try {
        const r = el.getBoundingClientRect();
        const w = Math.round(r.width), h = Math.round(r.height);
        if (w > 0 && h > 0 && w <= 4000 && h <= 4000) {
          out.width = String(w);
          out.height = String(h);
        }
      } catch (e) { /* ignore */ }
    }
    return out;
  };
  return JSON.stringify(extract(target), null, 2);
})()
