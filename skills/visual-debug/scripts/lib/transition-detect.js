(() => {
  const results = [];
  const seen = new Set();
  const allEls = document.querySelectorAll("a, button, [role=button], img, .product-card, [class*=card], [class*=link], [class*=hover], [class*=btn], nav a, footer a, h1, h2, h3");
  const EXCLUDE = __EXCLUDE_SELECTORS_JSON__;

  allEls.forEach(el => {
    if (EXCLUDE && el.closest(EXCLUDE)) return;
    const rect = el.getBoundingClientRect();
    const offscreenVertical = rect.bottom < 0 || rect.top > window.innerHeight;
    const cs = getComputedStyle(el);
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const ns = getComputedStyle(n);
      if (ns.display === "none" || ns.visibility === "hidden") return;
      // Offscreen reveal wrappers often start at opacity:0 until scrolled into
      // view. transition-compare scrolls each candidate before hover capture,
      // so dropping those candidates here hides exactly the scroll-triggered
      // CTA/tab controls this gate must verify.
      if (Number.parseFloat(ns.opacity || "1") < 0.05 && !offscreenVertical) return;
    }
    const hasTrans = cs.transitionDuration !== "0s" && cs.transitionProperty !== "none";
    const hasAnim = cs.animationName !== "none";

    if (!hasTrans && !hasAnim) return;

    if (rect.width < 10 || rect.height < 10) return;
    if (rect.top + window.scrollY > document.documentElement.scrollHeight) return;

    // Build a selector that identifies THIS semantic element, not just its
    // first shared utility/tracking class. Sites commonly stamp the same
    // tracking class on logos, nav links, footer icons, and buttons. Collapsing
    // those to one selector makes the verifier compare unrelated controls and
    // lets real hover bugs slip through. Prefer a unique id/class; otherwise
    // emit a bounded structural path with nth-of-type.
    const GENERIC_CLASSES = new Set([
      "nclick-target", "swiper-slide", "swiper-wrapper", "active", "is-active",
      "is-show", "is-hide", "is-hidden", "hidden", "show", "on",
      "h_0", "h_1", "h_2", "h_3", "track-animation",
    ]);
    const esc = (v) => (window.CSS && CSS.escape) ? CSS.escape(v) : String(v).replace(/[^A-Za-z0-9_-]/g, "\\$&");
    const usefulClasses = (node) => (
      node.className && typeof node.className === "string"
        ? node.className.split(/\s+/).filter(c =>
            c && c.length < 40 && !c.includes("hover")
              && !GENERIC_CLASSES.has(c) && !/^h_\d+$/.test(c)
          )
        : []
    );
    const firstUniqueClassSelector = (node) => {
      for (const cls of usefulClasses(node)) {
        const sel = "." + esc(cls);
        try {
          if (document.querySelectorAll(sel).length === 1) return sel;
        } catch {}
      }
      return "";
    };
    const pathSelector = (node) => {
      const parts = [];
      let cur = node;
      while (cur && cur.nodeType === 1 && cur !== document.documentElement && parts.length < 6) {
        let part = cur.tagName.toLowerCase();
        if (cur.id) {
          part += "#" + esc(cur.id);
          parts.unshift(part);
          break;
        }
        const cls = usefulClasses(cur).slice(0, 2);
        if (cls.length) part += "." + cls.map(esc).join(".");
        if (cur.parentElement) {
          const siblings = [...cur.parentElement.children].filter(c => c.tagName === cur.tagName);
          if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(cur) + 1})`;
        }
        parts.unshift(part);
        cur = cur.parentElement;
      }
      return parts.join(" > ");
    };

    const tagName = el.tagName.toLowerCase();
    const textKey = (el.textContent || "").trim().replace(/\s+/g, " ").substring(0, 80);
    const hrefKey = el.getAttribute("href") || el.getAttribute("src") || el.getAttribute("aria-label") || el.getAttribute("title") || "";
    // Image motion is verified by image/runtime/live-parity gates. Including
    // every blank carousel/content <img> here turns transition-compare into a
    // dynamic duplicate-slide matcher instead of a user-visible control/state
    // checker.
    if (tagName === "img" && !textKey) return;
    // Content-card carousel anchors are high-volume content tiles. Their hover
    // transforms are covered by card/image/runtime/live-parity checks; letting
    // them consume the first MAX_TRANSITIONS slots hides the smaller global
    // controls that transition-compare is meant to prove (section CTAs, tabs,
    // nav, footer/header links).
    if (
      tagName === "a"
      && el.closest(".masonry-list, .swiper-wrapper, [class*=carousel], [class*=gallery], [class*=news-list], [class*=card-list], [class*=tile-list], [class*=slide]")
      && !el.matches("a[class*=btn], a[class*=button], a[class*=cta], a[class*=nav], a[class*=tab], a[class*=logo], a[class*=social], a[aria-label], nav a, header a, footer a")
    ) return;
    if (rect.left + rect.width < 0 || rect.left > window.innerWidth) return;
    let pointerReachable = null;
    if (!offscreenVertical) {
      const x = Math.min(
        Math.max(rect.left + rect.width / 2, 0),
        Math.max(window.innerWidth - 1, 0),
      );
      const y = Math.min(
        Math.max(rect.top + rect.height / 2, 0),
        Math.max(window.innerHeight - 1, 0),
      );
      const hit = document.elementFromPoint(x, y);
      pointerReachable = Boolean(hit && (hit === el || el.contains(hit)));
    }
    const stateHiddenAncestor = Boolean(
      el.closest(".is-hide, .is-hidden, .hidden, [aria-hidden=true]")
    );
    let selector = "";
    if (el.id && document.querySelectorAll("#" + esc(el.id)).length === 1) {
      selector = "#" + esc(el.id);
    }
    selector = selector || firstUniqueClassSelector(el);
    if (!selector) {
      const hasOwnText = [...el.childNodes].some((node) =>
        node.nodeType === Node.TEXT_NODE && (node.textContent || "").trim().length > 0
      );
      const actionable = ["a", "button", "img"].includes(tagName)
        || ["button", "link", "tab"].includes(el.getAttribute("role") || "")
        || Boolean(hasOwnText || hrefKey);
      if (!actionable) return;
      selector = pathSelector(el);
    }
    if (!selector) selector = el.tagName.toLowerCase();

    if (seen.has(selector)) return;
    seen.add(selector);

    // Get transition properties
    const transProps = cs.transitionProperty.split(",").map(p => p.trim());
    const transDurs = cs.transitionDuration.split(",").map(d => d.trim());
    const transEase = cs.transitionTimingFunction.split(",").map(e => e.trim());

    results.push({
      selector,
      tag: tagName,
      text: textKey.substring(0, 40),
      matchKey: {
        tag: tagName,
        text: textKey,
        href: hrefKey,
        role: el.getAttribute("role") || "",
        aria: el.getAttribute("aria-label") || "",
      },
      rect: {
        top: Math.round(rect.top + window.scrollY),
        left: Math.round(rect.left),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
      transition: {
        properties: transProps,
        durations: transDurs,
        easings: transEase,
      },
      idleStyle: {
        opacity: cs.opacity,
        transform: cs.transform,
        backgroundColor: cs.backgroundColor,
        color: cs.color,
        scale: cs.scale || "none",
        filter: cs.filter,
        boxShadow: cs.boxShadow,
      },
      pointerReachable,
      stateHiddenAncestor,
    });
  });

  const priority = (item) => {
    const haystack = [
      item.selector || "",
      item.text || "",
      item.matchKey?.role || "",
      item.matchKey?.aria || "",
    ].join(" ").toLowerCase();
    let score = 0;
    if (/(^|[-_\s#.])(btn|button|cta|tab)([-_\s#.]|$)/.test(haystack)) score += 1200;
    if (/(^|[-_\s#.])(nav|logo|social|search|more)([-_\s#.]|$)/.test(haystack)) score += 700;
    if (item.tag === "button" || item.matchKey?.role === "button" || item.matchKey?.role === "tab") score += 300;
    if (item.matchKey?.href && item.matchKey.href !== "#") score += 120;
    if (item.text && item.text.length > 0 && item.text.length <= 60) score += 100;
    // Footer mega-menu links are often numerous duplicates of primary page
    // targets. Keep them comparable, but prefer the primary tab/CTA
    // controls when MAX_TRANSITIONS caps the set.
    if (/(^|[-_\s#.])(menu|sitemap|secondary)([-_\s#.]|$)/.test(haystack)) score -= 200;
    return score;
  };

  // Hydrated/sticky headers often keep an old nav tree directly underneath the
  // visible tree. Both copies can have identical text, href, geometry, opacity,
  // and transition metadata, but only the topmost copy can receive a real
  // pointer. De-duplicate only exact semantic+geometry clones and prefer a
  // stable tree over a state-hidden clone, then the element that wins hit
  // testing. The stable-tree tie-break also covers offscreen clones, where hit
  // testing is intentionally deferred and both pointerReachable values are
  // null. This keeps distinct footer/header links (different geometry) while
  // preventing a hidden underlay from consuming a capped comparison slot and
  // producing HOVER_UNVERIFIED noise.
  const semanticGeometryKey = (item) => JSON.stringify([
    item.matchKey?.tag || "",
    item.matchKey?.text || "",
    item.matchKey?.href || "",
    item.matchKey?.role || "",
    item.matchKey?.aria || "",
    item.rect?.top,
    item.rect?.left,
    item.rect?.width,
    item.rect?.height,
  ]);
  const deduped = new Map();
  results.forEach((item) => {
    const key = semanticGeometryKey(item);
    const current = deduped.get(key);
    const candidateRank = (candidate) => {
      const stableTree = candidate?.stateHiddenAncestor ? 0 : 10;
      const reachability = candidate?.pointerReachable === true ? 2 : (
        candidate?.pointerReachable === null ? 1 : 0
      );
      return stableTree + reachability;
    };
    if (!current || candidateRank(item) > candidateRank(current)) {
      deduped.set(key, item);
    }
  });

  return [...deduped.values()]
    .sort((a, b) => priority(b) - priority(a) || a.rect.top - b.rect.top || a.rect.left - b.rect.left)
    .slice(0, __MAX_TRANSITIONS__);
})()
