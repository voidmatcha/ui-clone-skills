(function (root) {
  "use strict";

  var SAMPLE_INTERVAL_MS = 50;
  var SAMPLE_WINDOW_MS = 4000;
  var MIN_AREA_RATIO = 0.20;
  var MIN_INITIAL_COVERAGE_RATIO = 0.45;
  var MIN_DURATION_RATIO = 0.5;
  var MAX_DURATION_RATIO = 2.0;
  var DURATION_SAMPLE_ALLOWANCE_MS = SAMPLE_INTERVAL_MS * 2;
  var MIN_DURATION_COMPARE_MS = SAMPLE_INTERVAL_MS * 2;
  var EPSILON = 0.5;
  var nodeIds = typeof WeakMap !== "undefined" ? new WeakMap() : null;
  var nextNodeId = 1;

  function number(value, fallback) {
    var n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function rounded(value) {
    return Math.round(number(value, 0) * 10) / 10;
  }

  function overlayKey(overlay) {
    if (!overlay) return "";
    var rect = overlay.rect || {};
    return [
      overlay.signature || "",
      rounded(rect.x),
      rounded(rect.y),
      rounded(rect.width),
      rounded(rect.height),
      rounded(overlay.opacity),
      overlay.transform || "",
    ].join("|");
  }

  function identityKey(overlay) {
    if (!overlay) return "";
    if (overlay.nodeIdentity) return "node:" + overlay.nodeIdentity;
    if (overlay.stableAnchor) return "anchor:" + overlay.stableAnchor;
    if (overlay.mediaSrc) return "media:" + overlay.mediaSrc;
    if (overlay.selector && /^#[^\s>+~]+$/.test(overlay.selector)) {
      return "selector:" + overlay.selector;
    }
    if (overlay.domPath) return "path:" + overlay.domPath;
    if (overlay.selector && !/^[a-z][a-z0-9-]*$/i.test(overlay.selector)) {
      return "selector:" + overlay.selector;
    }
    if (overlay.selector) return "selector:" + overlay.selector;
    return "signature:" + (overlay.signature || "").slice(0, 80);
  }

  function sameIdentity(a, b) {
    if (!a || !b) return false;
    var ak = identityKey(a);
    var bk = identityKey(b);
    return !!ak && ak === bk;
  }

  function analyzeTimeline(samples) {
    samples = Array.isArray(samples) ? samples : [];
    var firstOverlay = null;
    var exited = false;
    var reappeared = false;
    var present = [];
    samples.forEach(function (sample) {
      if (!sample) return;
      if (!firstOverlay && sample.overlay) {
        firstOverlay = sample.overlay;
      }
      if (!firstOverlay) return;
      if (exited) {
        if (sameIdentity(firstOverlay, sample.overlay)) {
          reappeared = true;
        }
        return;
      }
      if (sameIdentity(firstOverlay, sample.overlay)) {
        present.push(sample);
      } else {
        exited = true;
      }
    });

    var keys = {};
    present.forEach(function (sample) {
      keys[overlayKey(sample.overlay)] = true;
    });
    var uniqueKeys = Object.keys(keys);
    var backgroundMotion = samples.some(function (sample) {
      return number(sample && sample.viewportMotion, 0) > EPSILON;
    });
    var durationMs = present.length > 1 ? number(present[present.length - 1].t, 0) - number(present[0].t, 0) : 0;
    var initialCoverageRatio = present.length ? overlayCoverage(present[0].overlay) : 0;
    var minCoverageRatio = present.reduce(function (lowest, sample) {
      return Math.min(lowest, overlayCoverage(sample.overlay));
    }, present.length ? 1 : 0);

    return {
      sampleCount: samples.length,
      presentSampleCount: present.length,
      mounted: present.length > 0,
      phaseChanged: uniqueKeys.length > 1,
      exited: exited,
      reappeared: reappeared,
      backgroundMotion: backgroundMotion,
      identity: firstOverlay ? identityKey(firstOverlay) : null,
      firstPresentMs: present.length ? present[0].t : null,
      lastPresentMs: present.length ? present[present.length - 1].t : null,
      durationMs: Math.max(0, durationMs),
      initialCoverageRatio: rounded(initialCoverageRatio),
      minCoverageRatio: rounded(minCoverageRatio),
    };
  }

  function sideViolations(prefix, side) {
    var violations = [];
    if (!side.mounted) {
      violations.push(prefix + "-overlay-absent");
    }
    if (side.mounted && !side.exited) {
      violations.push(prefix + "-overlay-never-exited");
    }
    if (side.reappeared) {
      violations.push(prefix + "-overlay-reappeared");
    }
    if (side.mounted && side.initialCoverageRatio < MIN_INITIAL_COVERAGE_RATIO) {
      violations.push(prefix + "-coverage-too-low");
    }
    if (!side.mounted && side.backgroundMotion) {
      violations.push("background-motion-is-not-splash-proof");
    }
    return violations;
  }

  function compareLifecycles(refSamples, implSamples) {
    var ref = analyzeTimeline(refSamples);
    var impl = analyzeTimeline(implSamples);
    var violations = sideViolations("ref", ref).concat(sideViolations("impl", impl));
    if (ref.phaseChanged && impl.mounted && !impl.phaseChanged) {
      violations.push("impl-overlay-static");
    }
    if (ref.durationMs >= MIN_DURATION_COMPARE_MS) {
      var minDurationMs = ref.durationMs * MIN_DURATION_RATIO;
      var maxDurationMs = ref.durationMs * MAX_DURATION_RATIO;
      if (
        impl.durationMs + DURATION_SAMPLE_ALLOWANCE_MS < minDurationMs ||
        impl.durationMs - DURATION_SAMPLE_ALLOWANCE_MS > maxDurationMs
      ) {
        violations.push("impl-duration-ratio-mismatch");
      }
    }
    if (
      ref.initialCoverageRatio >= MIN_INITIAL_COVERAGE_RATIO &&
      impl.initialCoverageRatio < Math.max(MIN_INITIAL_COVERAGE_RATIO, ref.initialCoverageRatio * 0.5)
    ) {
      violations.push("impl-coverage-too-low");
    }
    return {
      schemaVersion: 1,
      status: violations.length ? "fail" : "pass",
      ref: ref,
      impl: impl,
      violations: Array.from(new Set(violations)),
    };
  }

  function selectorFor(el) {
    if (!el || el.nodeType !== 1) return "";
    if (el.id) return "#" + el.id;
    var rawClass = el.className && typeof el.className === "object" && "baseVal" in el.className
      ? el.className.baseVal
      : el.className;
    var cls = String(rawClass || "")
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 3)
      .join(".");
    if (cls) return el.tagName.toLowerCase() + "." + cls;
    return domPathFor(el);
  }

  function domPathFor(el) {
    var parts = [];
    var cur = el;
    var depth = 0;
    while (cur && cur.nodeType === 1 && depth < 5 && cur !== document.documentElement) {
      var tag = cur.tagName.toLowerCase();
      if (cur.id) {
        parts.unshift("#" + cur.id);
        break;
      }
      var index = 1;
      var sib = cur;
      while ((sib = sib.previousElementSibling)) {
        if (sib.tagName === cur.tagName) index += 1;
      }
      parts.unshift(tag + ":nth-of-type(" + index + ")");
      cur = cur.parentElement;
      depth += 1;
      if (cur === document.body) {
        parts.unshift("body");
        break;
      }
    }
    return parts.join(">");
  }

  function overlayCoverage(overlay) {
    if (!overlay) return 0;
    var explicit = number(overlay.coverageRatio, NaN);
    if (Number.isFinite(explicit)) return explicit;
    var area = number(overlay.areaRatio, NaN);
    if (Number.isFinite(area)) return area;
    return 1;
  }

  function clippedCoverage(candidate, viewport) {
    var rect = (candidate && candidate.rect) || {};
    var width = Math.max(0, number(viewport && viewport.width, 0));
    var height = Math.max(0, number(viewport && viewport.height, 0));
    var x1 = Math.max(0, number(rect.x, 0));
    var y1 = Math.max(0, number(rect.y, 0));
    var x2 = Math.min(width, number(rect.x, 0) + Math.max(0, number(rect.width, 0)));
    var y2 = Math.min(height, number(rect.y, 0) + Math.max(0, number(rect.height, 0)));
    var clippedArea = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
    return clippedArea / Math.max(1, width * height);
  }

  function chooseBestOverlay(candidates, viewport) {
    candidates = Array.isArray(candidates) ? candidates : [];
    viewport = viewport || { width: 0, height: 0 };
    var best = null;
    candidates.forEach(function (candidate) {
      if (!candidate) return;
      if (candidate.position && candidate.position !== "fixed" && candidate.position !== "absolute") return;
      var coverageRatio = clippedCoverage(candidate, viewport);
      if (coverageRatio < MIN_AREA_RATIO) return;
      var z = number(candidate.zIndex, 0);
      var enriched = Object.assign({}, candidate, {
        coverageRatio: rounded(coverageRatio),
        areaRatio: rounded(coverageRatio),
        zIndex: z,
      });
      if (
        !best ||
        coverageRatio > best._coverageRatio ||
        (coverageRatio === best._coverageRatio && z > best.zIndex)
      ) {
        best = Object.assign({_coverageRatio: coverageRatio}, enriched);
      }
    });
    if (best) delete best._coverageRatio;
    return best;
  }

  function visibleOverlayCandidate(el) {
    if (!el || !el.getBoundingClientRect || el === document.documentElement || el === document.body) {
      return null;
    }
    var style = getComputedStyle(el);
    if (!style || style.display === "none" || style.visibility === "hidden") return null;
    var opacity = number(style.opacity, 1);
    if (opacity <= 0.05) return null;
    var rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    var position = style.position;
    var z = parseInt(style.zIndex || "0", 10);
    var overlayish =
      position === "fixed" ||
      (position === "absolute" && (Number.isFinite(z) ? z : 0) >= 10);
    if (!overlayish) return null;
    return {
      selector: selectorFor(el),
      domPath: domPathFor(el),
      nodeIdentity: nodeIdentityFor(el),
      signature: String(el.textContent || "").trim().slice(0, 120),
      rect: {
        x: rounded(rect.x),
        y: rounded(rect.y),
        width: rounded(rect.width),
        height: rounded(rect.height),
      },
      opacity: rounded(opacity),
      transform: style.transform === "none" ? "" : style.transform,
      zIndex: Number.isFinite(z) ? z : 0,
      position: position,
    };
  }

  function nodeIdentityFor(el) {
    if (!nodeIds || !el) return "";
    if (!nodeIds.has(el)) {
      nodeIds.set(el, "n" + nextNodeId);
      nextNodeId += 1;
    }
    return nodeIds.get(el);
  }

  function detectOverlay() {
    if (!root.document || !document.documentElement) return null;
    var nodes = Array.prototype.slice.call(document.querySelectorAll("body *"));
    var candidates = [];
    nodes.forEach(function (el) {
      var candidate = visibleOverlayCandidate(el);
      if (!candidate) return;
      candidates.push(candidate);
    });
    return chooseBestOverlay(candidates, { width: root.innerWidth || 0, height: root.innerHeight || 0 });
  }

  function installSampler() {
    var targetRoot = arguments[0] || root;
    if (!targetRoot || targetRoot.__uiCloneSplashLifecycleInstalled) return;
    targetRoot.__uiCloneSplashLifecycleInstalled = true;
    var sampleWindowMs = number(targetRoot.__UI_CLONE_SPLASH_LIFECYCLE_WINDOW_MS__, SAMPLE_WINDOW_MS);
    var started = targetRoot.performance && targetRoot.performance.now ? targetRoot.performance.now() : Date.now();
    var samples = [];
    var lastViewportSig = "";
    function now() {
      var current = targetRoot.performance && targetRoot.performance.now ? targetRoot.performance.now() : Date.now();
      return Math.max(0, Math.round(current - started));
    }
    function viewportSignature() {
      if (!targetRoot.document || !targetRoot.document.body) return "";
      return [
        targetRoot.document.body.innerText ? targetRoot.document.body.innerText.length : 0,
        targetRoot.document.images ? targetRoot.document.images.length : 0,
        targetRoot.document.querySelectorAll ? targetRoot.document.querySelectorAll("video,canvas").length : 0,
        targetRoot.scrollY || 0,
      ].join("|");
    }
    function sample() {
      var sig = viewportSignature();
      var viewportMotion = lastViewportSig && sig !== lastViewportSig ? 1 : 0;
      lastViewportSig = sig || lastViewportSig;
      samples.push({
        t: now(),
        overlay: detectOverlay(),
        viewportMotion: viewportMotion,
        readyState: targetRoot.document ? targetRoot.document.readyState : "unknown",
      });
    }
    sample();
    var timer = targetRoot.setInterval(function () {
      sample();
      if (now() >= sampleWindowMs) targetRoot.clearInterval(timer);
    }, SAMPLE_INTERVAL_MS);
    targetRoot.__uiCloneSplashLifecycleSamples = samples;
    targetRoot.__uiCloneSplashLifecycleResult = function () {
      sample();
      return {
        schemaVersion: 1,
        sampleIntervalMs: SAMPLE_INTERVAL_MS,
        sampleWindowMs: sampleWindowMs,
        samples: samples.slice(),
        analysis: analyzeTimeline(samples),
      };
    };
  }

  var api = {
    analyzeTimeline: analyzeTimeline,
    compareLifecycles: compareLifecycles,
    chooseBestOverlay: chooseBestOverlay,
    selectorFor: selectorFor,
    installSampler: installSampler,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root && root.document) {
    root.__uiCloneSplashLifecycleProbe = api;
    installSampler();
  }
})(typeof window !== "undefined" ? window : globalThis);
