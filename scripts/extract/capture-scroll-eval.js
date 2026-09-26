(async () => {
  const resumeRunner = async (runner) => {
    runner.beginChunk();
    const next = await runner.iterator.next();
    runner.sequence += 1;
    if (!next.done) {
      return {
        continuationRequired: true,
        captureEpoch: runner.epoch,
        continuationSequence: runner.sequence,
        phase: next.value && next.value.phase ? next.value.phase : "capture",
      };
    }
    delete window.__uiCloneScrollCaptureRunner;
    return next.value;
  };
  if (window.__uiCloneScrollCaptureRunner) {
    return resumeRunner(window.__uiCloneScrollCaptureRunner);
  }

  const PCTS = [0, 10, 25, 50, 75, 90, 100];
  // Keep the full sweep below agent-browser's 30-second IPC ceiling. Ninety
  // rendered steps remain finer than half a viewport on a 17k-pixel page;
  // the MutationObserver records changes between the seven persisted stops.
  const MAX_SCAN_STEPS = 90;
  const MAX_TOTAL_SCAN_STEPS = MAX_SCAN_STEPS * 4;
  const MAX_END_PROBES = 10;
  const MAX_SHRINK_REPROBES = 3;
  const REQUIRED_STABLE_END_PROBES = 2;
  const END_PROBE_DWELL_MS = 1200;
  const CAPTURE_DEADLINE_MS = 22000;
  const MIN_SCAN_STEP_PX = 120;
  const startedAt = performance.now();
  let chunkStartedAt = startedAt;
  const root = document.documentElement;
  const initialScrollHeight = root.scrollHeight;
  let maxObservedScrollHeight = initialScrollHeight;
  const viewportHeight = window.innerHeight;
  const initialMaxScrollable = Math.max(0, initialScrollHeight - viewportHeight);

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
    Math.ceil(Math.max(1, initialMaxScrollable) / MAX_SCAN_STEPS),
  );
  let scanStepsUsed = 0;
  const deadlineExceeded = () => performance.now() - chunkStartedAt >= CAPTURE_DEADLINE_MS;
  const sweepTo = async (targetY) => {
    const startY = readScrollY();
    const distance = targetY - startY;
    const steps = Math.max(1, Math.ceil(Math.abs(distance) / scanStepPx));
    for (let step = 1; step <= steps; step += 1) {
      if (scanStepsUsed >= MAX_TOTAL_SCAN_STEPS || deadlineExceeded()) return false;
      performScroll(Math.round(startY + (distance * step) / steps));
      scanStepsUsed += 1;
      await renderedFrame();
    }
    return true;
  };

  const alignToTarget = async (targetY) => {
    const tolerance = Math.max(8, Math.min(40, Math.round(scanStepPx / 3)));
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (deadlineExceeded()) return false;
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

  const endSentinel = () => {
    const candidates = [];
    for (const element of document.querySelectorAll(
      "main, [role='main'], footer, [role='contentinfo'], article, section",
    )) {
      const isFooter = typeof element.matches === "function"
        && element.matches("footer, [role='contentinfo']");
      const excludedSelector = isFooter
        ? "article, section, aside, nav, dialog, [role='dialog'], [role='complementary'], [role='navigation'], [aria-modal='true'], [aria-hidden='true']"
        : "aside, nav, dialog, [role='dialog'], [role='complementary'], [role='navigation'], [aria-modal='true'], [aria-hidden='true']";
      if (element.parentElement && typeof element.parentElement.closest === "function"
          && element.parentElement.closest(excludedSelector)) continue;
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      let excludedByAncestor = false;
      for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
        const style = getComputedStyle(ancestor);
        if (style.display === "none" || style.position === "fixed") {
          excludedByAncestor = true;
          break;
        }
        const isRootScroller = ancestor === document.scrollingElement
          || ancestor === document.documentElement || ancestor === document.body;
        if (!isRootScroller && ancestor !== element && /^(auto|scroll)$/.test(style.overflowY)
            && ancestor.scrollHeight > ancestor.clientHeight + 2) {
          excludedByAncestor = true;
          break;
        }
      }
      if (excludedByAncestor) continue;
      candidates.push({
        element,
        rect,
        absoluteBottom: rect.bottom + readScrollY(),
      });
    }
    if (!candidates.length) return { required: false, reached: true, selector: null };
    candidates.sort((left, right) => right.absoluteBottom - left.absoluteBottom);
    const { element, rect, absoluteBottom } = candidates[0];
    const clippedLandmarks = candidates
      .filter((candidate) => candidate.absoluteBottom > root.scrollHeight + 2)
      .map((candidate) => targetSelector(candidate.element));
    const withinDocumentExtent = clippedLandmarks.length === 0;
    return {
      required: true,
      // A valid site may place trailing content after its footer. Reaching or
      // passing the footer is sufficient; requiring it to remain onscreen at
      // the final bottom would reject that layout. A transformed/clipped
      // footer beyond the document extent still fails closed.
      reached: rect.top < viewportHeight && withinDocumentExtent,
      selector: targetSelector(element),
      top: Math.round(rect.top),
      bottom: Math.round(rect.bottom),
      absoluteBottom: Math.round(absoluteBottom),
      withinDocumentExtent,
      clippedLandmarks,
    };
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

  const stableWait = async (initialWaitMs = 500) => {
    await renderedFrame();
    await new Promise((resolve) => setTimeout(resolve, initialWaitMs));
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

  let stops = [];
  let initialZeroStop = null;
  const alignmentFailures = [];
  const endTraversal = [];
  let isStatic = initialMaxScrollable <= 0;
  let captureComplete = true;
  let incompleteReason = null;
  let settledEndScrollHeight = null;
  let captureSequence = 0;

  const snapshotAt = (pct, maxScrollable, traversalDirection = "forward") => {
    captureSequence += 1;
    return {
      pct,
      scrollY: readScrollY(),
      targetY: Math.round(maxScrollable * pct / 100),
      maxScrollableAtCapture: maxScrollable,
      observedScrollHeight: root.scrollHeight,
      observedMaxScrollable: Math.max(0, root.scrollHeight - viewportHeight),
      traversalDirection,
      captureSequence,
      outerHTML: root.outerHTML,
      visibleSections: visibleSections(),
      compositeDigest: visualDigest(),
    };
  };

  const ensureTargetAvailable = async function* (targetY, pct) {
    const tolerance = Math.max(8, Math.min(40, Math.round(scanStepPx / 3)));
    let unchangedProbes = 0;
    for (let probe = 1; probe <= MAX_END_PROBES; probe += 1) {
      const availableMax = Math.max(0, root.scrollHeight - viewportHeight);
      if (availableMax + tolerance >= targetY) return { complete: true, reason: null };
      activeScrollLeg = { fromPct: pct, toPct: pct };
      const swept = await sweepTo(availableMax);
      const aligned = swept && await alignToTarget(availableMax);
      await stableWait(END_PROBE_DWELL_MS);
      const grownMax = Math.max(0, root.scrollHeight - viewportHeight);
      maxObservedScrollHeight = Math.max(maxObservedScrollHeight, root.scrollHeight);
      yield { phase: "target-availability", pct, probe };
      if (!swept || !aligned) {
        return { complete: false, reason: "document-end-unreachable" };
      }
      unchangedProbes = grownMax > availableMax + tolerance ? 0 : unchangedProbes + 1;
      if (unchangedProbes >= REQUIRED_STABLE_END_PROBES) {
        return { complete: false, reason: "scroll-target-unavailable" };
      }
    }
    return { complete: false, reason: "scroll-target-availability-did-not-stabilize" };
  };

  const captureStops = async function* (maxScrollable, percentages = PCTS) {
    const captured = [];
    let previousPct = null;
    for (const pct of percentages) {
      activeScrollLeg = previousPct === null ? null : { fromPct: previousPct, toPct: pct };
      const targetY = Math.round(maxScrollable * pct / 100);
      const available = yield* ensureTargetAvailable(targetY, pct);
      if (!available.complete) {
        activeScrollLeg = null;
        return { captured, complete: false, reason: available.reason };
      }
      const swept = await sweepTo(targetY);
      if (!swept) {
        activeScrollLeg = null;
        return { captured, complete: false, reason: "capture-budget-exhausted" };
      }
      if (!(await alignToTarget(targetY))) {
        alignmentFailures.push({ pct, targetY, actualY: readScrollY() });
        activeScrollLeg = null;
        return { captured, complete: false, reason: "scroll-target-unreachable" };
      }
      await stableWait();
      maxObservedScrollHeight = Math.max(maxObservedScrollHeight, root.scrollHeight);
      const direction = previousPct !== null && pct < previousPct ? "reverse" : "forward";
      captured.push(snapshotAt(pct, maxScrollable, direction));
      activeScrollLeg = null;
      previousPct = pct;
      yield { phase: "forward-snapshot", pct };
    }
    return { captured, complete: true, reason: null };
  };

  let totalEndProbeCount = 0;
  const settleAtDocumentEnd = async function* () {
    let stableProbes = 0;
    let probesThisPass = 0;
    let shrinkReprobes = 0;
    let sentinel = { required: false, reached: true, selector: null };
    while (probesThisPass < MAX_END_PROBES) {
      if (deadlineExceeded()) {
        return { complete: false, reason: "capture-deadline-exceeded", maxScrollable: 0 };
      }
      const targetY = Math.max(0, root.scrollHeight - viewportHeight);
      activeScrollLeg = { fromPct: 100, toPct: 100 };
      const swept = await sweepTo(targetY);
      const aligned = swept && await alignToTarget(targetY);
      await stableWait(END_PROBE_DWELL_MS);
      activeScrollLeg = null;
      const observedY = readScrollY();
      const observedHeight = root.scrollHeight;
      maxObservedScrollHeight = Math.max(maxObservedScrollHeight, observedHeight);
      const observedMax = Math.max(0, observedHeight - viewportHeight);
      sentinel = endSentinel();
      const tolerance = Math.max(8, Math.min(40, Math.round(scanStepPx / 3)));
      probesThisPass += 1;
      totalEndProbeCount += 1;
      endTraversal.push({
        probe: totalEndProbeCount,
        targetY,
        observedY,
        scrollHeight: observedHeight,
        maxScrollable: observedMax,
        aligned,
        sentinel,
      });
      yield { phase: "end-probe", probe: totalEndProbeCount };
      if (!swept) {
        return { complete: false, reason: "capture-budget-exhausted", maxScrollable: observedMax };
      }
      // The document shrank while sweeping; the browser clamped scroll to the new
      // end, so the end was reached. Only a small shrink relative to the largest
      // extent observed so far is a legitimate settle (lazy placeholder/footer
      // resize). A large drop (scroll-lock modal, content torn down, route swap)
      // means earlier stops no longer describe this document, so fail closed.
      // Limit: 5% of the peak scroll range, capped at half a viewport, never
      // below 2x the alignment tolerance. Re-probes are also capped so a page
      // that keeps shrinking cannot consume the whole probe budget silently.
      const shrankToEnd = observedMax + tolerance < targetY
        && Math.abs(observedY - observedMax) <= tolerance;
      if (shrankToEnd) {
        const peakMax = Math.max(0, maxObservedScrollHeight - viewportHeight);
        const shrinkPx = peakMax - observedMax;
        const shrinkLimit = Math.max(
          tolerance * 2,
          Math.min(Math.round(viewportHeight / 2), Math.round(peakMax * 0.05)),
        );
        shrinkReprobes += 1;
        if (shrinkPx > shrinkLimit || shrinkReprobes > MAX_SHRINK_REPROBES) {
          alignmentFailures.push({
            pct: 100,
            targetY,
            actualY: observedY,
            phase: "end-probe-collapse",
            peakMaxScrollable: peakMax,
            shrinkPx,
            shrinkLimit,
          });
          return { complete: false, reason: "document-collapsed", maxScrollable: observedMax };
        }
        stableProbes = 0;
        continue;
      }
      if (!aligned || Math.abs(observedY - targetY) > tolerance) {
        alignmentFailures.push({ pct: 100, targetY, actualY: observedY, phase: "end-probe" });
        return { complete: false, reason: "document-end-unreachable", maxScrollable: observedMax };
      }
      if (Math.abs(observedMax - targetY) <= tolerance && sentinel.reached) stableProbes += 1;
      else stableProbes = 0;
      if (stableProbes >= REQUIRED_STABLE_END_PROBES) {
        settledEndScrollHeight = observedHeight;
        return {
          complete: true,
          reason: null,
          maxScrollable: observedMax,
          endStop: snapshotAt(100, observedMax, "forward-end"),
        };
      }
    }
    return {
      complete: false,
      reason: sentinel.required && !sentinel.reached
        ? "document-end-sentinel-not-reached" : "document-end-did-not-stabilize",
      maxScrollable: Math.max(0, root.scrollHeight - viewportHeight),
    };
  };

  const resultPayload = () => {
    observer.disconnect();
    const endingScrollHeight = root.scrollHeight;
    const finalScrollHeight = settledEndScrollHeight || endingScrollHeight;
    const scrollHeightDeltaPct = initialScrollHeight > 0
      ? Math.round(((finalScrollHeight - initialScrollHeight) / initialScrollHeight) * 100)
      : 0;
    return {
      stops,
      initialZeroStop,
      domMutations: mutationTrace,
      domMutationTraceTruncated: mutationTraceTruncated,
      scanStepPx,
      scanStepsUsed,
      alignmentFailures,
      captureComplete,
      incompleteReason,
      continuationRequired: false,
      endTraversal,
      recaptureCount: 0,
      captureTraversal: "forward",
      durationMs: Math.round(performance.now() - startedAt),
      scrollHeight: initialScrollHeight,
      viewportHeight,
      finalScrollHeight,
      endingScrollHeight,
      settledEndScrollHeight,
      maxObservedScrollHeight,
      scrollHeightDeltaPct,
      scrollHeightGrew: !isStatic && finalScrollHeight > initialScrollHeight,
      potentialInfiniteScroll: !isStatic && !captureComplete
        && maxObservedScrollHeight > initialScrollHeight * 1.5,
      infiniteScroll: !isStatic && !captureComplete
        && maxObservedScrollHeight > initialScrollHeight * 1.5,
      scrollEngine,
      scrollEngineReason,
      scrollTransportProven,
      scrollControlMethod,
      inputListeners: Array.isArray(window.__uiCloneScrollInputListeners)
        ? window.__uiCloneScrollInputListeners : [],
      static: isStatic,
    };
  };

  const runCapture = async function* () {
    initialZeroStop = snapshotAt(0, initialMaxScrollable, "initial");
    const end = yield* settleAtDocumentEnd();
    if (!end.complete) {
      captureComplete = false;
      incompleteReason = end.reason;
      stops = [initialZeroStop];
      return resultPayload();
    }
    if (end.maxScrollable <= 0) {
      stops = [initialZeroStop];
      return resultPayload();
    }
    isStatic = false;
    const finalMaxScrollable = end.maxScrollable;

    activeScrollLeg = { fromPct: 100, toPct: 0 };
    const resetSwept = await sweepTo(0);
    const resetAligned = resetSwept && await alignToTarget(0);
    await stableWait();
    activeScrollLeg = null;
    yield { phase: "reverse-reset" };
    if (!resetSwept || !resetAligned) {
      captureComplete = false;
      incompleteReason = "reverse-reset-unreachable";
      stops = [initialZeroStop, end.endStop];
      return resultPayload();
    }

    const sequence = yield* captureStops(finalMaxScrollable);
    stops = sequence.captured;
    if (!sequence.complete) {
      captureComplete = false;
      incompleteReason = sequence.reason;
      return resultPayload();
    }

    const tolerance = Math.max(8, Math.min(40, Math.round(scanStepPx / 3)));
    const terminalSentinel = endSentinel();
    const endingMax = Math.max(0, root.scrollHeight - viewportHeight);
    if (Math.abs(endingMax - finalMaxScrollable) > tolerance
        || Math.abs(readScrollY() - finalMaxScrollable) > tolerance
        || !terminalSentinel.reached) {
      captureComplete = false;
      incompleteReason = "final-range-or-sentinel-drift";
    }
    return resultPayload();
  };

  const runner = {
    iterator: runCapture(),
    epoch: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    sequence: 0,
    beginChunk: () => { chunkStartedAt = performance.now(); },
  };
  window.__uiCloneScrollCaptureRunner = runner;
  return resumeRunner(runner);
})()
