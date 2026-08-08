(async () => {
  const safe = (fn) => { try { return fn(); } catch (_e) { return null; } };

  // ── Helpers: capture at current scroll position ──
  //
  // Motion-site review: the original tween capture
  // reported `ease: function () { ... }` (toString of the GSAP ease wrapper)
  // and empty `targets`. CustomEase / SteppedEase / Back / Power eases all
  // collapse to opaque function source; the agent receiving this data
  // could not reproduce eases. Fix: capture (a) the ease NAME via
  // `ease.id || ease.toString()`, (b) the CustomEase data string via
  // `window.CustomEase._map[name].data`, (c) richer target selectors
  // including class fragments, (d) `delay` and full `vars` snapshot.
  const elSelector = (el) => {
    if (!el || !el.tagName) return null;
    const id = el.id ? "#" + el.id : "";
    const cls = (typeof el.className === "string" && el.className)
      ? "." + el.className.trim().split(/\s+/).slice(0, 3).join(".")
      : "";
    return el.tagName.toLowerCase() + id + cls;
  };

  const captureEaseName = (ease) => {
    if (!ease) return null;
    // GSAP CustomEase instances expose .getRatio + .id.
    if (typeof ease.getRatio === "function" && ease.id) return String(ease.id);
    // Built-in eases (Power2.out, Back.inOut etc.) expose .name OR are functions
    // whose toString contains a recognizable pattern.
    if (ease.name) return String(ease.name);
    if (typeof ease === "string") return ease;
    const s = String(ease);
    // Try to extract a GSAP ease key from the function source.
    const m = s.match(/(?:Power[0-4]|Back|Bounce|Circ|Cubic|Elastic|Expo|Linear|Quad|Quart|Quint|Sine|Stepped|SlowMo|RoughEase|CustomEase|none)\.?(?:in|out|inOut)?/);
    return m ? m[0] : (s.length > 80 ? s.slice(0, 80) + "…" : s);
  };

  const captureScrollTrigger = () => {
    const ST = window.ScrollTrigger || window.gsap?.core?.globals?.()?.ScrollTrigger;
    if (!ST || !ST.getAll) return null;
    return ST.getAll().map(t => ({
      // Resolved pixel offsets — what the trigger ACTUALLY fires at, not the
      // "top 80%" expression source. This is the value generation needs.
      start:   typeof t.start === "number" ? Math.round(t.start) : null,
      end:     typeof t.end === "number"   ? Math.round(t.end)   : null,
      scrub:   t.scrub ?? null,
      pin:     !!t.pin,
      trigger: t.trigger?.tagName?.toLowerCase()
               + (t.trigger?.id ? "#" + t.trigger.id : "")
               + (typeof t.trigger?.className === "string" && t.trigger.className
                   ? "." + t.trigger.className.trim().split(/\s+/).slice(0, 2).join(".")
                   : ""),
      tween: safe(() => {
        const a = t.animation;
        if (!a) return null;
        const vars = a.vars || {};
        const easeRef = vars.ease;
        const easeName = captureEaseName(easeRef);
        // Snapshot vars MINUS function/non-serializable members.
        const varsSnap = {};
        for (const k of Object.keys(vars)) {
          const v = vars[k];
          if (typeof v === "function") continue;
          if (k === "ease") continue;  // captured separately as easeName
          if (k === "scrollTrigger") continue;  // captured at the parent level
          if (k === "onUpdate" || k === "onComplete" || k === "onStart") continue;
          // Skip objects with circular refs by attempting json round-trip.
          try { JSON.stringify(v); varsSnap[k] = v; } catch { /* skip */ }
        }
        return {
          duration: a.duration?.() ?? null,
          delay: typeof vars.delay === "number" ? vars.delay : null,
          // Legacy ease field stays for backward-compat — downstream
          // consumers (runtime-spec-coverage.sh) read either ease or easeName.
          ease: easeName,
          easeName,
          targets: (a.targets?.() || []).slice(0, 5).map(elSelector).filter(Boolean),
          vars: varsSnap,
        };
      }),
    }));
  };

  const captureWebAnimations = () => {
    if (!document.getAnimations) return null;
    return document.getAnimations().map(a => {
      const t = a.effect?.getTiming?.() || {};
      const target = a.effect?.target;
      return {
        id: a.id || null,
        playState: a.playState,
        currentTime: typeof a.currentTime === "number" ? Math.round(a.currentTime) : null,
        duration: typeof t.duration === "number" ? Math.round(t.duration) : t.duration ?? null,
        delay: typeof t.delay === "number" ? Math.round(t.delay) : null,
        easing: t.easing ?? null,
        iterations: t.iterations ?? null,
        target: target?.tagName?.toLowerCase()
                + (target?.id ? "#" + target.id : ""),
      };
    });
  };

  // ── Scroll-linked inline-style sampler (framer-motion / any rAF scrub) ──
  //
  // framer-motion (useScroll + useTransform) and any scroll-driven code that
  // writes inline styles on each rAF tick leave NO global registry to query,
  // unlike ScrollTrigger/GSAP. The only runtime-observable truth is the inline
  // style itself changing as the page scrolls. Snapshot every element carrying
  // an inline transform/opacity/size at each scroll fraction, key by a stable
  // DOM index, and downstream keep only the ones whose value VARIES across
  // scroll — that residue IS the scroll-progress-to-value curve (e.g. the eBay
  // grid svg opacity 0->1 and its 3 g-layer scale 0.425->1).
  // Stable per-node identity across scroll ticks. Keying by document order
  // (querySelectorAll index) is WRONG: framer/React sites mount and unmount
  // nodes during the scroll walk, so one element lands at a different index
  // between fractions — splitting a single curve across keys and merging two
  // elements under one key (the classic oscillating-opacity residue, and the
  // dropped mid-scroll fractions). A WeakMap keyed by the live node object
  // stays stable regardless of DOM mutation, so each scroll-progress curve
  // stays intact and every fraction where the node is present is kept.
  const nodeIds = new WeakMap();
  const observedMotionNodes = new WeakSet();
  let nodeCounter = 0;
  const sampleInlineMotion = () => {
    const all = document.querySelectorAll("*");
    const out = {};
    for (let i = 0; i < all.length; i++) {
      const el = all[i];
      const s = el.style;
      if (!s) continue;
      const t = (s.transform !== "" && s.transform != null) ? s.transform : null;
      const o = (s.opacity !== "" && s.opacity != null) ? s.opacity : null;
      const w = s.width || null;
      const h = s.height || null;
      const br = s.borderRadius || null;
      const activeMotion = (t != null && t !== "none") || o != null || w != null || h != null || br != null;
      if (activeMotion) observedMotionNodes.add(el);
      if (!activeMotion && !observedMotionNodes.has(el)) continue;
      let id = nodeIds.get(el);
      if (id === undefined) { id = "n" + (nodeCounter++); nodeIds.set(el, id); }
      out[id] = { sel: elSelector(el), transform: t, opacity: o, width: w, height: h, borderRadius: br };
    }
    return out;
  };

  // ── Scroll walk: visit N fractions, accumulate uniques ──
  // Denser near the top: pinned scroll-scrub sections (eBay grid) complete
  // within the first ~10-20% of page scroll, so [0,.25,.5,.75,1] alone would
  // sample only the settled endpoints and miss the intra-section curve.
  let positions = [0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.45, 0.6, 0.75, 1.0];
  const stMap = new Map();
  const waMap = new Map();
  const framerFrames = {};
  let stEverPresent = false;
  let waEverPresent = false;
  const origScroll = window.scrollY;

  // A spring is still in flight shortly after a scroll jump, so reading once
  // after a fixed delay records a mid-flight value as if it were a keyframe —
  // replayed, that reads as jitter. Poll until the inline-motion snapshot
  // stops changing and record the settled frame instead. Stable frames cost
  // two short polls, less than the fixed wait this replaced.
  const SETTLE_POLL_MS = 120;
  const SETTLE_MAX_POLLS = 8;
  const frameSignature = frame => JSON.stringify(
    Object.entries(frame || {}).map(([id, rec]) => [
      id, rec.sel, rec.transform, rec.opacity, rec.width, rec.height, rec.borderRadius,
    ]),
  );

  const capturePosition = async (pos, sink) => {
    const max = Math.max(document.documentElement.scrollHeight - window.innerHeight, 0);
    window.scrollTo({ top: pos * max, behavior: "instant" });
    let settledFrame = {};
    let previousSignature = null;
    for (let poll = 0; poll < SETTLE_MAX_POLLS; poll++) {
      await new Promise(r => setTimeout(r, SETTLE_POLL_MS));
      settledFrame = safe(sampleInlineMotion) || {};
      const signature = frameSignature(settledFrame);
      if (signature === previousSignature) break;
      previousSignature = signature;
    }
    const st = safe(captureScrollTrigger);
    if (Array.isArray(st)) {
      stEverPresent = true;
      for (const entry of st) {
        const key = (entry.trigger || "?") + "|" + entry.start + "|" + entry.end;
        if (!stMap.has(key)) stMap.set(key, entry);
      }
    }
    const wa = safe(captureWebAnimations);
    if (Array.isArray(wa)) {
      waEverPresent = true;
      for (const entry of wa) {
        const key = JSON.stringify({ t: entry.target, d: entry.duration, e: entry.easing, i: entry.id });
        if (!waMap.has(key)) waMap.set(key, entry);
      }
    }
    (sink || framerFrames)[pos] = settledFrame;
  };

  for (const pos of positions) {
    await capturePosition(pos);
  }

  // Coarse document fractions can cross an entire short scrub section with one
  // interior sample. Subdivide only intervals whose inline-motion snapshot
  // changed, preserving curve shape without paying a full 40-frame page walk.
  const adaptivePositions = [];
  for (let index = 1; index < positions.length; index++) {
    const left = positions[index - 1];
    const right = positions[index];
    if (frameSignature(framerFrames[left]) === frameSignature(framerFrames[right])) {
      continue;
    }
    const span = right - left;
    for (const fraction of [0.25, 0.5, 0.75]) {
      adaptivePositions.push(
        Math.round((left + span * fraction) * 1000000) / 1000000,
      );
    }
  }
  for (const pos of adaptivePositions.slice(0, 24)) {
    await capturePosition(pos);
  }
  positions = [...new Set([...positions, ...adaptivePositions.slice(0, 24)])]
    .sort((a, b) => a - b);

  // Return sweep: revisit every offset on the way back up. A scroll-linked
  // property is a function of offset and reports the same settled value in
  // both directions; a latched one (fires on enter, never reverses) does not.
  // Replaying a latch as an interpolated band is what renders every state
  // half-applied, so the two must be told apart before the plan is built.
  const returnFrames = {};
  for (const pos of [...positions].reverse()) {
    await capturePosition(pos, returnFrames);
  }

  // Restore original scroll so downstream operations are not stuck at bottom.
  window.scrollTo({ top: origScroll, behavior: "instant" });

  const scrollTrigger = stEverPresent ? [...stMap.values()].slice(0, 50) : null;
  const webAnimations = waEverPresent ? [...waMap.values()].slice(0, 50) : null;

  // Reduce the per-fraction inline snapshots to only elements whose value
  // changes across scroll — the scroll-linked animation residue. Static inline
  // styles (set once, never animated) have size-1 value sets and are dropped.
  const scrollLinkedStyles = (() => {
    const keys = new Set();
    for (const pos of positions) {
      const frame = framerFrames[pos] || {};
      for (const k in frame) keys.add(k);
    }
    const rows = [];
    for (const k of keys) {
      const byScroll = {};
      const seen = { transform: new Set(), opacity: new Set(), width: new Set(), height: new Set(), borderRadius: new Set() };
      let sel = null;
      for (const pos of positions) {
        const rec = framerFrames[pos] ? framerFrames[pos][k] : null;
        if (!rec) continue;
        if (!sel) sel = rec.sel;
        byScroll[pos] = { transform: rec.transform, opacity: rec.opacity, width: rec.width, height: rec.height, borderRadius: rec.borderRadius };
        for (const p of ["transform", "opacity", "width", "height", "borderRadius"]) {
          seen[p].add(rec[p] == null ? "__ui_clone_reset__" : String(rec[p]));
        }
      }
      const varies = Object.keys(seen).filter(p => seen[p].size > 1);
      if (!varies.length) continue;
      // Only a value the return sweep disagrees with marks a latch; an offset
      // the element was absent from on either pass proves nothing, and a
      // property that never varied cannot distinguish the two.
      let latched = false;
      for (const pos of positions) {
        const down = framerFrames[pos] ? framerFrames[pos][k] : null;
        const up = returnFrames[pos] ? returnFrames[pos][k] : null;
        if (!down || !up) continue;
        if (varies.some(p => String(down[p]) !== String(up[p]))) { latched = true; break; }
      }
      rows.push({ selector: sel, varies, byScroll, latched });
    }
    return rows.slice(0, 30);
  })();
  const scrollLinkedEverPresent = scrollLinkedStyles.length > 0;

  // ── Globals (scroll-position-independent) ──
  const gsap = safe(() => {
    const g = window.gsap || window.GSAP;
    if (!g) return null;
    return {
      version: g.version || null,
      ticker: g.ticker?.lagSmoothing ? "lagSmoothing-on" : "default",
    };
  });

  const lenis = safe(() => {
    const l = window.lenis || window.__lenis;
    if (!l) return null;
    const opt = l.options || {};
    return {
      duration: opt.duration ?? null,
      // ease is a function — toString gives the source the agent needs to
      // reproduce. Truncate so we do not blow past response budgets.
      easing: opt.easing?.toString?.()?.slice(0, 400) ?? null,
      smoothWheel: opt.smoothWheel ?? null,
      smoothTouch: opt.smoothTouch ?? null,
      direction: opt.direction ?? null,
    };
  });

  const ix2 = safe(() => {
    const ixData = window.Webflow?.require?.("ix2")?.store?.getState?.()?.ixData;
    if (!ixData) return null;
    const tlNames = Object.keys(ixData.timelines || {}).slice(0, 50);
    return {
      timelineCount: Object.keys(ixData.timelines || {}).length,
      timelineKeys: tlNames,
      eventCount: Object.keys(ixData.events || {}).length,
    };
  });

  // Motion-site review: when GSAP CustomEase is loaded,
  // dump the registry data strings (SVG path snippets) so downstream
  // ease replication can use the exact curve instead of cubic-bezier
  // approximation. Without this, site-defined GSAP `CustomEase` declarations
  // could only be reproduced via guessed `cubic-bezier()` — losing the
  // specific motion character of each named curve.
  const customEaseRegistry = safe(() => {
    const CE = window.CustomEase || window.gsap?.core?.globals?.()?.CustomEase;
    if (!CE) return null;
    // GSAP exposes the registry on CustomEase._map (modern) or .registry (older).
    const reg = CE._map || CE.registry || null;
    if (!reg) return null;
    const entries = {};
    let count = 0;
    for (const [key, val] of Object.entries(reg)) {
      if (count >= 50) break;
      const data = val?.data ?? val?._data ?? null;
      if (data) {
        entries[key] = typeof data === "string"
          ? (data.length > 400 ? data.slice(0, 400) + "…" : data)
          : null;
        count++;
      }
    }
    return Object.keys(entries).length > 0 ? entries : null;
  });

  // Capture global timeline children — surfaces tweens that are NOT
  // tied to ScrollTrigger and that document.getAnimations() can miss
  // (GSAP runs its own ticker, not Web Animations API).
  const gsapTimelines = safe(() => {
    const g = window.gsap || window.GSAP;
    if (!g?.globalTimeline?.getChildren) return null;
    const children = g.globalTimeline.getChildren(true, true, true);
    if (!Array.isArray(children) || !children.length) return null;
    return children.slice(0, 100).map(child => {
      const vars = child.vars || {};
      const easeName = captureEaseName(vars.ease);
      return {
        kind: child.constructor?.name || "Animation",
        duration: child.duration?.() ?? null,
        delay: typeof vars.delay === "number" ? vars.delay : null,
        progress: typeof child.progress === "function"
          ? Math.round(child.progress() * 1000) / 1000 : null,
        easeName,
        targets: (child.targets?.() || []).slice(0, 3).map(elSelector).filter(Boolean),
      };
    });
  });

  return JSON.stringify({
    gsap,
    scrollTrigger,
    webAnimations,
    lenis,
    ix2,
    customEaseRegistry,
    gsapTimelines,
    scrollLinkedStyles: scrollLinkedEverPresent ? scrollLinkedStyles : null,
    scrolledPositions: positions,
    viewport: {
      width: typeof window.innerWidth === "number" ? window.innerWidth : null,
      height: typeof window.innerHeight === "number" ? window.innerHeight : null,
    },
    documentScroll: {
      scrollHeight: typeof document.documentElement?.scrollHeight === "number"
        ? document.documentElement.scrollHeight : null,
      maxScroll: typeof document.documentElement?.scrollHeight === "number" && typeof window.innerHeight === "number"
        ? Math.max(document.documentElement.scrollHeight - window.innerHeight, 0) : null,
    },
    generatedAt: new Date().toISOString(),
  });
})()
