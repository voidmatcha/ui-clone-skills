(() => {
  const semanticTags = new Set(["section", "footer", "header", "nav", "main"]);
  const containers = [];
  // Deep React/Next layouts can place <main> at depth 5 and its real sections
  // below another wrapper at depth 7. A shallower generated clone would still
  // enumerate those sections, making the ref/impl evidence asymmetric.
  const MAX_COLLECT_DEPTH = 10;

  // Mask-aware geometry. section-compare's dynamic mask sets
  // `visibility: hidden` on the dynamic:true selectors to absorb timer-phase
  // MOTION from the screenshot, and applies it BEFORE this enumeration. Because
  // the contentBox/contentGroups filters below drop visibility:hidden /
  // opacity:0 nodes, a STATIC geometry defect under a mask (loop-11 footer cards
  // baked left:426px / ±192 transform, off-center at non-extraction viewports)
  // silently vanished from alignment-parity — exemption-without-compensation.
  // visibility:hidden PRESERVES layout, so for elements under a masked selector
  // we still MEASURE geometry; only display:none / zero-size stay filtered. The
  // mask is injected identically into ref + impl, so the comparison is
  // ref-relative. Selector list is passed quote-free by the caller.
  const __maskSel = (typeof window !== "undefined" && window.__UI_RE_DYNAMIC_SELECTORS__) || "";
  let __maskRoots = [];
  if (__maskSel) {
    try { __maskRoots = Array.from(document.querySelectorAll(__maskSel)); } catch (e) { __maskRoots = []; }
  }
  const isMaskHidden = (node) =>
    __maskRoots.some(r => r === node || (r.contains && r.contains(node)));

  // Browser capture helpers may inject anonymous, absolutely-positioned
  // backdrops made of viewport-height chunks. They are explicitly hidden from
  // accessibility, ignore pointer input, and carry no page identity, but their
  // large child divs otherwise look like real sections. Exclude only that
  // narrow instrumentation shape; named/interactive aria-hidden visuals remain
  // eligible for geometry measurement through their owning section.
  const isAnonymousCaptureOverlay = (node) => {
    if (!node || node.getAttribute("aria-hidden") !== "true") return false;
    if (node.id || (typeof node.className === "string" && node.className.trim())) return false;
    const style = getComputedStyle(node);
    return style.pointerEvents === "none"
      && (style.position === "absolute" || style.position === "fixed");
  };

  // paintsNothing — a LEAF element that renders no visible paint contributes
  // nothing to a content union. A 4px transparent-background spacer (opacity:1,
  // nonzero size) passes the display/visibility/opacity/size filters yet is
  // invisible to a human; counting it as content lets a decoy group register
  // (alignment-parity Attack 4 — the same paint-blindness class). Wrappers
  // (elements with children) are kept — their descendants carry the content.
  const __REPLACED = { img: 1, svg: 1, canvas: 1, video: 1, picture: 1, iframe: 1, object: 1, embed: 1 };
  const paintsNothing = (node, s) => {
    if (node.children && node.children.length > 0) return false;
    const t = node.tagName ? node.tagName.toLowerCase() : "";
    if (__REPLACED[t] === 1) return false;
    if ((node.innerText || node.textContent || "").trim()) return false;
    const bgImg = s.backgroundImage;
    if (bgImg && bgImg !== "none") return false;
    const bg = (s.backgroundColor || "").trim().toLowerCase();
    if (bg && bg !== "transparent") {
      const open = bg.indexOf("("), close = bg.indexOf(")");
      if (open < 0 || close < 0) return false; // named/hex -> opaque
      const parts = bg.substring(open + 1, close).split(",");
      if (parts.length < 4) return false;      // rgb()/hsl() -> opaque
      if (parseFloat(parts[3]) > 0) return false;
    }
    return true; // no text, no bg paint, no image, not replaced, leaf
  };
  const isVisuallyClipped = (rect, style) => {
    // Screen-reader-only copy commonly uses a 1px clipped absolute box. Its
    // off-screen coordinate is not painted geometry and must not expand a
    // section/group alignment union.
    const legacyClip = (style.clip || "auto") !== "auto";
    const clippedPath = (style.clipPath || "none") !== "none";
    return rect.width <= 1 && rect.height <= 1
      && style.overflow === "hidden"
      && (legacyClip || clippedPath);
  };

  function collect(parent, depth) {
    if (depth > MAX_COLLECT_DEPTH) return;
    const children = Array.from(parent.children);

    children.forEach(el => {
      const tag = el.tagName.toLowerCase();
      if (tag === "script" || tag === "style" || tag === "link" || tag === "noscript") return;
      if (isAnonymousCaptureOverlay(el)) return;
      // Transpiler-emitted uncovered fragments (scaffold-to-jsx.sh
      // _UncoveredHead / _UncoveredAfter*) are stamped data-uncovered and are
      // NOT section-map sections — they carry ref content section-map.json never
      // claimed (capture-state fragmentation, e.g. a hero_video block split from
      // its hero section). Enumerating them inflates the impl section count vs
      // the ref and cascades into section MISPAIRS (wrong ref<->impl crops).
      // Skip the element and its subtree. Completes wall-fix-1 (nested-section
      // <div> demotion), which the large-div promotion below otherwise defeats.
      // The ref has no data-uncovered nodes, so ref-vs-ref self-pass is
      // unaffected.
      if (el.getAttribute && el.getAttribute("data-uncovered") !== null) return;
      const rect = el.getBoundingClientRect();
      const h = rect.height;
      if (h < 50 || rect.width < 100) {
        // h === 0 indicates a layout-only wrapper that does not size to its
        // descendants (typical of abs-positioned-widget DOMs like Readymag
        // exports — body children such as #root, #mags are 0-height because
        // content lives in nested abs-positioned widgets). Descend so we
        // can find the real visible sections inside.
        if (h === 0 && el.children.length > 0) {
          collect(el, depth + 1);
        }
        return;
      }

      const isSemantic = semanticTags.has(tag);
      const isLargeDiv = tag === "div" && h > window.innerHeight * 0.2;
      const isPageWrapper = h > document.documentElement.scrollHeight * 0.8;

      if (isSemantic) {
        // Descend only when this element directly wraps other layout-level sections
        // (e.g., <main> with <section> children, or <section> wrapping nested <section>s).
        // <header>/<footer>/<nav>/<aside> are internal *content* roles when they appear
        // inside a section (page header, section heading row, table-of-contents nav,
        // sidebar aside) — descending on them loses the wrapping section. Only true
        // layout-level wrappers (section/main) trigger descent here.
        const hasStructuralChild = Array.from(el.children).some(c => {
          const t = c.tagName.toLowerCase();
          return t === "section" || t === "main";
        });
        const structuralDescendantCount = Array.from(el.querySelectorAll("section, main"))
          .filter(c => c !== el).length;
        const hasWrappedStructuralDescendants = tag === "main"
          && structuralDescendantCount >= 2
          && h > window.innerHeight * 1.5;
        // Webflow / single-main pages collapse 17+ visible sub-sections into one giant
        const isJumboMain = tag === "main"
          && el.children.length > 3
          && h > window.innerHeight * 1.5;
        // Webflow CMS patterns can wrap multiple semantic sub-sections in a
        // generic outer <section>. The outer <section> looks like a single
        // container, so a shallow detector can add it and never descend,
        // missing named sub-sections. Detect
        // this shape: a tall <section> whose direct children include ≥2
        // distinctly-named-class divs each large enough to be a section.
        const hasMultipleNamedSubsections = (() => {
          if (tag !== "section" || h <= window.innerHeight * 1.5) return false;
          const namedDivKids = Array.from(el.children).filter(c => {
            if (c.tagName.toLowerCase() !== "div") return false;
            if (!c.className || typeof c.className !== "string") return false;
            const r = c.getBoundingClientRect();
            return r.height > window.innerHeight * 0.25;
          });
          if (namedDivKids.length < 2) return false;
          const distinctClasses = new Set(
            namedDivKids.map(d => d.className.trim().split(/\s+/)[0]).filter(Boolean)
          );
          return distinctClasses.size >= 2;
        })();
        if (hasStructuralChild || isJumboMain || hasWrappedStructuralDescendants
            || hasMultipleNamedSubsections) {
          collect(el, depth + 1);
        } else {
          containers.push({ el, tag, rect });
        }
      } else if (isLargeDiv) {
        // If this div wraps most of the page, descend into it instead
        if (isPageWrapper) {
          collect(el, depth + 1);
        } else {
          // Check if this div has semantic children — if so, descend
          const hasSemanticChildren = Array.from(el.children).some(c =>
            semanticTags.has(c.tagName.toLowerCase())
          );
          if (hasSemanticChildren) {
            collect(el, depth + 1);
          } else {
            containers.push({ el, tag, rect });
          }
        }
      } else if (tag === "div" && h > 100) {
        collect(el, depth + 1);
      }
    });
  }

  collect(document.body, 0);

  // Deduplicate: remove parents that contain other found sections
  const filtered = containers.filter((c, i) =>
    !containers.some((other, j) => j !== i && c.el.contains(other.el) && c.el !== other.el)
  );

  filtered.sort((a, b) => a.rect.top - b.rect.top);

  return filtered.map((c, i) => {
    const el = c.el;
    const rect = el.getBoundingClientRect();
    const scrollY = window.scrollY;

    // Extract text fingerprint (first 100 chars of visible text, normalized)
    const text = el.innerText || "";
    const words = text.replace(/\\s+/g, " ").trim().substring(0, 200);
    const fingerprint = words.substring(0, 100).toLowerCase().replace(/[^a-z0-9 ]/g, "");
    // Full visible-text word string (not truncated to 100 chars) — drives the
    // text-content pairing signal so sections match by what they SAY, not by
    // class name. Capped at 800 chars to bound JSON size.
    const textWords = text.replace(/\\s+/g, " ").trim().toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\\s+/g, " ").trim().substring(0, 800);

    // Check for SVGs
    const svgs = el.querySelectorAll("svg");
    const hasSvgText = [...svgs].some(svg => {
      const paths = svg.querySelectorAll("path");
      if (paths.length < 3) return false;
      const totalD = [...paths].reduce((sum, p) => sum + (p.getAttribute("d")?.length || 0), 0);
      return totalD > 500;
    });
    const visibleMedia = Array.from(
      el.querySelectorAll("img,video,canvas,iframe,picture,object,embed")
    ).filter(node => {
      const r = node.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return false;
      const s = getComputedStyle(node);
      return s.display !== "none"
        && (s.visibility !== "hidden" || isMaskHidden(node))
        && parseFloat(s.opacity || "1") > 0;
    });

    // Get rendering info
    const cs = getComputedStyle(el);

    // contentBox — union bbox of visible descendant boxes (>=2 levels deep:
    // grandchildren preferred, child fallback when a child has no visible
    // element children). Feeds the alignment-parity gate: a full-bleed
    // section can have an IDENTICAL rect on both sides while its inner
    // content column is horizontally mis-placed (pixel constants baked for
    // one design width), so the section rect alone is blind to the defect.
    const contentBox = (() => {
      const boxes = [];
      const vpW = document.documentElement.clientWidth;
      const visibleRect = (node) => {
        const t = node.tagName ? node.tagName.toLowerCase() : "";
        if (t === "script" || t === "style" || t === "link" || t === "noscript") return null;
        const r = node.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return null;
        if (r.right <= 0 || r.left >= vpW) return null;
        // Only boxes intersecting the section's own vertical crop contribute
        // to its content envelope. Closed header drawers/search panels are
        // commonly translated above the viewport while retaining layout
        // geometry; counting them makes an invisible overlay look like a
        // horizontal section-alignment defect.
        if (r.bottom <= rect.top || r.top >= rect.bottom) return null;
        const s = getComputedStyle(node);
        if (s.display === "none") return null;
        if (!isMaskHidden(node) && (s.visibility === "hidden" || parseFloat(s.opacity || "1") === 0)) return null;
        if (isVisuallyClipped(r, s)) return null;
        if (paintsNothing(node, s)) return null;
        return r;
      };
      Array.from(el.children).forEach(c1 => {
        const kids = Array.from(c1.children);
        let pushed = 0;
        kids.forEach(c2 => {
          const r2 = visibleRect(c2);
          if (r2) { boxes.push(r2); pushed++; }
        });
        if (pushed === 0) {
          const r1 = visibleRect(c1);
          if (r1) boxes.push(r1);
        }
      });
      if (!boxes.length) return null;
      const left = Math.min(...boxes.map(b => b.left));
      const right = Math.max(...boxes.map(b => b.right));
      return { left: Math.round(left), width: Math.round(right - left), boxCount: boxes.length };
    })();

    // contentGroups — per-container child-union gaps (depth <= 4). The
    // whole-section contentBox union is diluted by full-width centered
    // siblings (loop-9 eatReal: the h2 spans the content column while the
    // carousel cards group sits +64px off-center), so each multi-child
    // container also records the union bbox of ITS children. The
    // alignment-parity gate pairs groups ref-vs-impl by normalized class
    // token and compares gap asymmetry ref-relative.
    const contentGroups = (() => {
      const groups = [];
      const skipTags = new Set(["script", "style", "link", "noscript", "svg"]);
      const collectGroups = (node, depth) => {
        if (depth > 4 || groups.length >= 24) return;
        Array.from(node.children).forEach(child => {
          if (groups.length >= 24) return;
          const t = child.tagName ? child.tagName.toLowerCase() : "";
          if (skipTags.has(t)) return;
          const cr = child.getBoundingClientRect();
          if (cr.width >= 200 && cr.height > 0 && child.children.length >= 2) {
            const st = getComputedStyle(child);
            if (st.display !== "none" && (st.visibility !== "hidden" || isMaskHidden(child))) {
              const kidBoxes = [];
              Array.from(child.children).forEach(k => {
                const kt = k.tagName ? k.tagName.toLowerCase() : "";
                if (kt === "script" || kt === "style" || kt === "link" || kt === "noscript") return;
                const kr = k.getBoundingClientRect();
                if (kr.width <= 0 || kr.height <= 0) return;
                const ks = getComputedStyle(k);
                if (ks.display === "none") return;
                if (!isMaskHidden(k) && (ks.visibility === "hidden" || parseFloat(ks.opacity || "1") === 0)) return;
                if (isVisuallyClipped(kr, ks)) return;
                if (paintsNothing(k, ks)) return;
                kidBoxes.push(kr);
              });
              if (kidBoxes.length >= 2) {
                const uLeft = Math.min(...kidBoxes.map(b => b.left));
                const uRight = Math.max(...kidBoxes.map(b => b.right));
                const cls = (child.className?.toString?.() || "").trim().split(" ")[0] || "";
                const rawName = cls || t;
                // strip CSS-module hash suffix (cards__aB3xY -> cards)
                // so differently-hashed ref/impl builds still pair.
                const name = rawName.includes("__")
                  ? rawName.substring(0, rawName.lastIndexOf("__"))
                  : rawName;
                groups.push({
                  name: name.substring(0, 40),
                  containerLeft: Math.round(cr.left),
                  containerWidth: Math.round(cr.width),
                  unionLeft: Math.round(uLeft),
                  unionWidth: Math.round(uRight - uLeft),
                  childCount: kidBoxes.length,
                  // Per-child centres (batch-7 ITEM 3): a SYMMETRIC union can
                  // hide a systematic shift when one painting sibling sits
                  // off-centre the opposite way. Emit each child's centre so the
                  // gate can detect per-child opposite-offset cancellation that
                  // the union envelope alone misses. Capped to bound JSON size.
                  childCenters: kidBoxes.slice(0, 24).map(b => Math.round(b.left + b.width / 2)),
                });
              }
            }
          }
          // LONE masked content element (alignment-parity Attack B): a single
          // masked heading forms no multi-child group, so a parent transform
          // that de-centers it (correct text-align, shifted ancestor) escapes
          // both the static gate and group asymmetry. Emit its rect anchored to
          // the SECTION so its horizontal placement is measured ref-relative.
          if (isMaskHidden(child) && cr.height > 0 && cr.width >= 50
              && !(cr.width >= 200 && child.children.length >= 2)
              && (child.innerText || "").trim()) {
            const mst = getComputedStyle(child);
            if (mst.display !== "none") {
              const mcls = (child.className?.toString?.() || "").trim().split(" ")[0] || "";
              const mraw = mcls || t;
              const mname = mraw.includes("__")
                ? mraw.substring(0, mraw.lastIndexOf("__"))
                : mraw;
              groups.push({
                name: ("masked:" + mname).substring(0, 40),
                containerLeft: Math.round(rect.left),
                containerWidth: Math.round(rect.width),
                unionLeft: Math.round(cr.left),
                unionWidth: Math.round(cr.width),
                childCount: 1,
              });
            }
          }
          collectGroups(child, depth + 1);
        });
      };
      collectGroups(el, 1);
      return groups;
    })();

    return {
      index: i,
      tag: c.tag,
      id: el.id || null,
      className: (el.className?.toString?.() || "").substring(0, 80),
      fingerprint,
      textWords,
      hasSvgText,
      hasVisibleMedia: visibleMedia.length > 0,
      visibleMediaCount: visibleMedia.length,
      rect: {
        top: Math.round(rect.top + scrollY),
        left: Math.round(rect.left),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
      display: cs.display,
      gridCols: cs.gridTemplateColumns !== "none" ? cs.gridTemplateColumns : null,
      childCount: el.children.length,
      clientWidth: document.documentElement.clientWidth,
      contentBox,
      contentGroups,
      leftGap: contentBox ? Math.round(contentBox.left - rect.left) : null,
      rightGap: contentBox ? Math.round((rect.left + rect.width) - (contentBox.left + contentBox.width)) : null,
    };
  });
})()
