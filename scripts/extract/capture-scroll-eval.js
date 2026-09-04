(async () => {
  const PCTS = [0, 10, 25, 50, 75, 90, 100];
  // Keep the full sweep below agent-browser's 30-second IPC ceiling. Ninety
  // rendered steps remain finer than half a viewport on a 17k-pixel page;
  // the MutationObserver records changes between the seven persisted stops.
  const MAX_SCAN_STEPS = 90;
  const MIN_SCAN_STEP_PX = 120;
  const startedAt = performance.now();
  const root = document.documentElement;
  const initialScrollHeight = root.scrollHeight;
  const viewportHeight = window.innerHeight;
  const maxScrollable = Math.max(0, initialScrollHeight - viewportHeight);

  const lenisInstance = window.lenis
    || (typeof window.Lenis === "object" ? window.Lenis : null);
  const locomotiveInstance = window.locomotive || window.locomotiveScroll || null;
  const wheelListeners = Array.isArray(window.__uiCloneScrollWheelListeners)
    ? window.__uiCloneScrollWheelListeners
    : [];
  const provenWheelTargets = Array.from(new Set(wheelListeners.map((item) => item.target))).sort();
  const hasRootWheelProof = provenWheelTargets.length > 0;
  let scrollEngine = "native";
  let scrollEngineReason = "native window scrolling";
  let scrollTransportProven = true;
  let scrollControlMethod = "native-window-scroll";
  let scrollInstance = null;
  if (lenisInstance && typeof lenisInstance.scrollTo === "function") {
    scrollEngine = "lenis";
    scrollEngineReason = "callable Lenis scrollTo instance";
    scrollControlMethod = "engine-api";
    scrollInstance = lenisInstance;
  } else if (
    (root.classList.contains("lenis") || typeof window.Lenis === "function")
    && hasRootWheelProof
  ) {
    scrollEngine = "lenis";
    scrollEngineReason = `Lenis marker plus root non-passive wheel listener on ${provenWheelTargets.join(", ")}`;
    scrollControlMethod = "proven-wheel-engine-with-native-positioning";
  } else if (root.classList.contains("lenis") || typeof window.Lenis === "function") {
    scrollEngine = "lenis-unproven";
    scrollEngineReason = "Lenis marker or constructor without callable instance";
    scrollTransportProven = false;
  } else if (locomotiveInstance && typeof locomotiveInstance.scrollTo === "function") {
    scrollEngine = "locomotive";
    scrollEngineReason = "callable Locomotive scrollTo instance";
    scrollControlMethod = "engine-api";
    scrollInstance = locomotiveInstance;
  } else if (root.classList.contains("has-scroll-init") && hasRootWheelProof) {
    scrollEngine = "locomotive";
    scrollEngineReason = `Locomotive marker plus root non-passive wheel listener on ${provenWheelTargets.join(", ")}`;
    scrollControlMethod = "proven-wheel-engine-with-native-positioning";
  } else if (root.classList.contains("has-scroll-init")) {
    scrollEngine = "locomotive-unproven";
    scrollEngineReason = "Locomotive marker without callable instance";
    scrollTransportProven = false;
  }

  const readScrollY = () => {
    if (scrollEngine === "lenis" && scrollInstance
        && typeof scrollInstance.scroll === "number") {
      return Math.round(scrollInstance.scroll);
    }
    const locomotiveScroll = scrollInstance && scrollInstance.scroll
      && scrollInstance.scroll.instance && scrollInstance.scroll.instance.scroll;
    if (scrollEngine === "locomotive" && locomotiveScroll
        && typeof locomotiveScroll.y === "number") {
      return Math.round(locomotiveScroll.y);
    }
    return Math.round(window.scrollY);
  };

  const performScroll = (targetY) => {
    if (scrollEngine === "lenis" && scrollInstance) {
      try {
        scrollInstance.scrollTo(targetY, { immediate: true, force: true });
        return;
      } catch (error) {}
    }
    if (scrollEngine === "locomotive" && scrollInstance) {
      try {
        scrollInstance.scrollTo(targetY, { duration: 0, disableLerp: true });
        return;
      } catch (error) {}
    }
    try {
      window.scrollTo({ top: targetY, behavior: "instant" });
    } catch (error) {
      window.scrollTo(0, targetY);
    }
  };

  const renderedFrame = (timeoutMs = 50) => new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      resolve();
    };
    setTimeout(finish, timeoutMs);
    requestAnimationFrame(() => requestAnimationFrame(finish));
  });

  const scanStepPx = Math.max(
    MIN_SCAN_STEP_PX,
    Math.ceil(Math.max(1, maxScrollable) / MAX_SCAN_STEPS),
  );
  const sweepTo = async (targetY) => {
    const startY = readScrollY();
    const distance = targetY - startY;
    const steps = Math.max(1, Math.ceil(Math.abs(distance) / scanStepPx));
    for (let step = 1; step <= steps; step += 1) {
      performScroll(Math.round(startY + (distance * step) / steps));
      await renderedFrame();
    }
  };

  const alignToTarget = async (targetY) => {
    const tolerance = Math.max(8, Math.min(40, Math.round(scanStepPx / 3)));
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      if (Math.abs(readScrollY() - targetY) <= tolerance) return true;
      if (attempt === 9) performScroll(targetY);
    }
    return Math.abs(readScrollY() - targetY) <= tolerance;
  };

  const visibleSections = () => {
    const selectors = [
      "section", "[data-section]", "main > *", "[class*=section]", "header", "footer", "nav",
    ];
    const sections = [];
    const seen = new Set();
    for (const selector of selectors) {
      let elements = [];
      try {
        elements = document.querySelectorAll(selector);
      } catch (error) {
        continue;
      }
      for (const element of elements) {
        if (seen.has(element)) continue;
        seen.add(element);
        const rect = element.getBoundingClientRect();
        if (rect.bottom <= 0 || rect.top >= viewportHeight
            || rect.width <= 50 || rect.height <= 50) continue;
        const id = element.id ? `#${element.id}` : "";
        const className = typeof element.className === "string"
          ? element.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).join(".")
          : "";
        const classes = className ? `.${className}` : "";
        const dataSection = element.dataset && element.dataset.section
          ? `[data-section="${element.dataset.section}"]` : "";
        sections.push({
          selector: `${element.tagName.toLowerCase()}${id}${classes}${dataSection}`,
          top: Math.round(rect.top),
          height: Math.round(rect.height),
        });
        if (sections.length >= 30) return sections;
      }
    }
    return sections;
  };

  const visualDigest = () => {
    const signals = [];
    for (const element of document.body ? document.body.querySelectorAll("*") : []) {
      const rect = element.getBoundingClientRect();
      if (rect.top >= viewportHeight || rect.bottom <= 0
          || rect.width <= 100 || rect.height <= 50) continue;
      const style = getComputedStyle(element);
      signals.push([
        style.color, style.opacity, style.transform, style.visibility,
        Math.round(rect.top), Math.round(rect.height),
      ].join(":"));
      if (signals.length >= 3) break;
    }
    return signals.join("|").slice(0, 200);
  };

  const stableWait = async () => {
    await renderedFrame();
    await new Promise((resolve) => setTimeout(resolve, 500));
    let previous = visualDigest();
    for (let poll = 0; poll < 3; poll += 1) {
      await new Promise((resolve) => setTimeout(resolve, 200));
      const current = visualDigest();
      if (current === previous) return;
      previous = current;
    }
  };

  const clipped = (value) => String(value == null ? "" : value).slice(0, 240);
  const targetSelector = (node) => {
    const element = node && node.nodeType === 1 ? node : node && node.parentElement;
    if (!element || !element.tagName) return "unknown";
    if (element.id) return `${element.tagName.toLowerCase()}#${CSS.escape(element.id)}`;
    const classes = typeof element.className === "string"
      ? element.className.trim().split(/\s+/).filter(Boolean).slice(0, 3)
      : [];
    return element.tagName.toLowerCase()
      + (classes.length ? `.${classes.map((name) => CSS.escape(name)).join(".")}` : "");
  };

  const mutationTrace = [];
  const mutationIndex = new Map();
  let activeScrollLeg = null;
  let mutationTraceTruncated = false;
  const recordMutation = (key, value) => {
    const existing = mutationIndex.get(key);
    if (existing) {
      existing.lastScrollY = value.lastScrollY;
      existing.count += 1;
      if (Object.hasOwn(value, "newValue")) existing.newValue = value.newValue;
      return;
    }
    if (mutationTrace.length >= 300) {
      mutationTraceTruncated = true;
      return;
    }
    mutationIndex.set(key, value);
    mutationTrace.push(value);
  };
  const observer = new MutationObserver((records) => {
    if (!activeScrollLeg) return;
    const scrollY = readScrollY();
    for (const record of records) {
      const selector = targetSelector(record.target);
      const base = {
        fromPct: activeScrollLeg.fromPct,
        toPct: activeScrollLeg.toPct,
        firstScrollY: scrollY,
        lastScrollY: scrollY,
        selector,
        type: record.type,
        count: 1,
      };
      if (record.type === "attributes") {
        const attribute = record.attributeName || "";
        recordMutation(`${activeScrollLeg.fromPct}|${activeScrollLeg.toPct}|${selector}|${attribute}`, {
          ...base,
          attribute,
          oldValue: clipped(record.oldValue),
          newValue: clipped(record.target.getAttribute(attribute)),
        });
      } else if (record.type === "childList") {
        const added = Array.from(record.addedNodes || []).slice(0, 12).map(targetSelector);
        const removed = Array.from(record.removedNodes || []).slice(0, 12).map(targetSelector);
        if (!added.length && !removed.length) continue;
        recordMutation(`${activeScrollLeg.fromPct}|${activeScrollLeg.toPct}|${selector}|children`, {
          ...base, added, removed,
        });
      } else if (record.type === "characterData") {
        recordMutation(`${activeScrollLeg.fromPct}|${activeScrollLeg.toPct}|${selector}|text`, {
          ...base,
          oldValue: clipped(record.oldValue),
          newValue: clipped(record.target.textContent),
        });
      }
    }
  });
  observer.observe(root, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: [
      "class", "hidden", "open", "aria-expanded", "aria-hidden", "aria-current",
      "aria-selected", "aria-pressed", "data-state", "data-active", "data-scroll",
      "data-section", "data-visible", "data-inview", "data-in-view",
    ],
    attributeOldValue: true,
    characterData: true,
    characterDataOldValue: true,
  });

  const stops = [];
  const alignmentFailures = [];
  const isStatic = maxScrollable <= 0;
  if (isStatic) {
    stops.push({
      pct: 0,
      scrollY: readScrollY(),
      outerHTML: root.outerHTML,
      visibleSections: visibleSections(),
      compositeDigest: visualDigest(),
    });
  } else {
    let previousPct = null;
    for (const pct of PCTS) {
      activeScrollLeg = previousPct === null ? null : { fromPct: previousPct, toPct: pct };
      const targetY = Math.round(maxScrollable * pct / 100);
      if (activeScrollLeg) await sweepTo(targetY);
      else performScroll(targetY);
      if (!(await alignToTarget(targetY))) {
        alignmentFailures.push({ pct, targetY, actualY: readScrollY() });
      }
      await stableWait();
      stops.push({
        pct,
        scrollY: readScrollY(),
        outerHTML: root.outerHTML,
        visibleSections: visibleSections(),
        compositeDigest: visualDigest(),
      });
      activeScrollLeg = null;
      previousPct = pct;
    }
  }
  observer.disconnect();

  const finalScrollHeight = root.scrollHeight;
  const scrollHeightDeltaPct = initialScrollHeight > 0
    ? Math.round(((finalScrollHeight - initialScrollHeight) / initialScrollHeight) * 100)
    : 0;
  return {
    stops,
    domMutations: mutationTrace,
    domMutationTraceTruncated: mutationTraceTruncated,
    scanStepPx,
    alignmentFailures,
    durationMs: Math.round(performance.now() - startedAt),
    scrollHeight: initialScrollHeight,
    viewportHeight,
    finalScrollHeight,
    scrollHeightDeltaPct,
    scrollHeightGrew: !isStatic && finalScrollHeight > initialScrollHeight,
    infiniteScroll: !isStatic && finalScrollHeight > initialScrollHeight * 1.5,
    scrollEngine,
    scrollEngineReason,
    scrollTransportProven,
    scrollControlMethod,
    static: isStatic,
  };
})()
