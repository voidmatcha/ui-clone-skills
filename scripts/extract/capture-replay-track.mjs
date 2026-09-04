#!/usr/bin/env node
"use strict";

import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { chromium } from "playwright-core";

const require = createRequire(import.meta.url);
const { version: playwrightVersion } = require("playwright-core/package.json");

const SAMPLE_COUNT = 21;
const SAMPLE_DENOMINATOR = SAMPLE_COUNT - 1;
const MIN_OBSERVATION_FRAMES = 4;
const SETTLE_FRAME_CAP = 120;
const SETTLE_TIMEOUT_MS = 3000;
const ACTION_DETECTION_FRAMES = 10;
const MIN_ACTION_DENOMINATOR_MS = 50;
const MAX_ACTION_DENOMINATOR_MS = 5000;
const MIN_VIRTUAL_DENOMINATOR_MS = 320;
const MAX_VIRTUAL_DENOMINATOR_MS = 4800;
const VIRTUAL_DENOMINATOR_STEP_MS = 320;
const VIRTUAL_EPOCH_MS = 1700000000000;
const VIRTUAL_ANCHOR_MS = 1700000060000;
const VIRTUAL_TICK_MS = 16;
const VIRTUAL_REAL_GAP_MS = 100;
const VIRTUAL_SETTLE_MAX_MS = 4800;
const VIRTUAL_SETTLE_CONSECUTIVE_TICKS = 4;
const LENIS_WHEEL_ALIGNMENT_EPSILON_PX = 0.5;
const WHEEL_ACK_TIMEOUT_MS = 1000;
const MAX_READY_WAIT_MS = 30000;
const TRACK_SHA_RE = /^[0-9a-f]{64}$/;

function fail(message) {
  throw new Error(message);
}

function parseArgs(argv) {
  const args = {
    mode: "scroll-progress",
    driver: "animation-pause",
    transport: "native",
    readyWaitMs: 0,
    viewportWidth: 1440,
    viewportHeight: 900,
    channel: "chrome",
    anchorMs: VIRTUAL_ANCHOR_MS,
  };
  const stringOptions = new Map([
    ["--mode", "mode"],
    ["--driver", "driver"],
    ["--transport", "transport"],
    ["--ready-wait-ms", "readyWaitMs"],
    ["--url", "url"],
    ["--selector", "selector"],
    ["--out", "out"],
    ["--baseline-sha", "baselineSha"],
    ["--viewport-width", "viewportWidth"],
    ["--viewport-height", "viewportHeight"],
    ["--executable-path", "executablePath"],
    ["--channel", "channel"],
    ["--start-px", "startPx"],
    ["--end-px", "endPx"],
    ["--track-id", "trackId"],
    ["--denominator-ms", "denominatorMs"],
    ["--anchor-ms", "anchorMs"],
  ]);

  for (let index = 0; index < argv.length; index += 1) {
    const name = argv[index];
    const key = stringOptions.get(name);
    if (!key) {
      fail(`unknown argument: ${name}`);
    }
    const value = argv[index + 1];
    if (value === undefined || value.startsWith("--")) {
      fail(`${name} requires a value`);
    }
    args[key] = value;
    index += 1;
  }

  for (const [name, key] of [
    ["--url", "url"],
    ["--selector", "selector"],
    ["--out", "out"],
    ["--start-px", "startPx"],
    ["--end-px", "endPx"],
  ]) {
    if (!args[key]) {
      fail(`${name} is required`);
    }
  }

  let parsedUrl;
  try {
    parsedUrl = new URL(args.url);
  } catch {
    fail("--url must be a valid http/https URL");
  }
  if (!["http:", "https:"].includes(parsedUrl.protocol)) {
    fail("--url must use http or https");
  }

  args.startPx = parseFiniteNumber("--start-px", args.startPx);
  args.endPx = parseFiniteNumber("--end-px", args.endPx);
  if (args.endPx <= args.startPx) {
    fail("--end-px must be greater than --start-px");
  }
  args.viewportWidth = parsePositiveInteger("--viewport-width", args.viewportWidth);
  args.viewportHeight = parsePositiveInteger("--viewport-height", args.viewportHeight);
  if (args.baselineSha && !TRACK_SHA_RE.test(args.baselineSha)) {
    fail("--baseline-sha must be 64 lowercase hex characters");
  }
  if (!String(args.selector).trim()) {
    fail("--selector must be nonempty");
  }
  if (!String(args.out).trim()) {
    fail("--out must be nonempty");
  }
  if (!String(args.channel).trim()) {
    fail("--channel must be nonempty");
  }
  if (!["scroll-progress", "scroll-action"].includes(args.mode)) {
    fail("--mode must be scroll-progress or scroll-action");
  }
  if (!["animation-pause", "virtual-clock"].includes(args.driver)) {
    fail("--driver must be animation-pause or virtual-clock");
  }
  if (!["native", "lenis-wheel"].includes(args.transport)) {
    fail("--transport must be native or lenis-wheel");
  }
  args.readyWaitMs = parseInteger("--ready-wait-ms", args.readyWaitMs);
  if (args.readyWaitMs < 0 || args.readyWaitMs > MAX_READY_WAIT_MS) {
    fail(`--ready-wait-ms must be 0..${MAX_READY_WAIT_MS}`);
  }
  args.anchorMs = parseInteger("--anchor-ms", args.anchorMs);
  if (args.anchorMs % VIRTUAL_TICK_MS !== 0) {
    fail(`--anchor-ms must be divisible by ${VIRTUAL_TICK_MS}`);
  }
  if (args.mode === "scroll-action") {
    if (!Number.isInteger(args.startPx) || !Number.isInteger(args.endPx)) {
      fail("--start-px and --end-px must be integers in scroll-action mode");
    }
    if (args.driver === "virtual-clock") {
      if (args.denominatorMs === undefined) {
        fail("--denominator-ms is required with --driver virtual-clock");
      }
      args.denominatorMs = parseInteger("--denominator-ms", args.denominatorMs);
      if (
        args.denominatorMs < MIN_VIRTUAL_DENOMINATOR_MS ||
        args.denominatorMs > MAX_VIRTUAL_DENOMINATOR_MS ||
        args.denominatorMs % VIRTUAL_DENOMINATOR_STEP_MS !== 0
      ) {
        fail(
          `--denominator-ms must be ${MIN_VIRTUAL_DENOMINATOR_MS}..${MAX_VIRTUAL_DENOMINATOR_MS} and divisible by ${VIRTUAL_DENOMINATOR_STEP_MS}`,
        );
      }
    } else if (args.denominatorMs !== undefined) {
      fail("--denominator-ms is only supported with --driver virtual-clock");
    }
  } else {
    if (args.driver !== "animation-pause") {
      fail("--driver virtual-clock requires --mode scroll-action");
    }
    if (args.transport === "lenis-wheel" && (!Number.isInteger(args.startPx) || !Number.isInteger(args.endPx))) {
      fail("--start-px and --end-px must be integers with --transport lenis-wheel");
    }
    if (args.denominatorMs !== undefined) {
      fail("--denominator-ms requires --mode scroll-action --driver virtual-clock");
    }
  }
  if (args.mode !== "scroll-progress" && args.transport !== "native") {
    fail("--transport lenis-wheel is only supported with --mode scroll-progress");
  }

  return args;
}

function parseFiniteNumber(name, value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    fail(`${name} must be finite`);
  }
  return number;
}

function parsePositiveInteger(name, value) {
  const number = Number(value);
  if (!Number.isInteger(number) || number <= 0) {
    fail(`${name} must be a positive integer`);
  }
  return number;
}

function parseInteger(name, value) {
  const number = Number(value);
  if (!Number.isInteger(number)) {
    fail(`${name} must be an integer`);
  }
  return number;
}

function canonicalJson(value) {
  return JSON.stringify(sortJson(value));
}

function sortJson(value) {
  if (Array.isArray(value)) {
    return value.map(sortJson);
  }
  if (value && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value).sort()) {
      out[key] = sortJson(value[key]);
    }
    return out;
  }
  return value;
}

function withoutTrackSha(track) {
  const clone = structuredClone(track);
  if (clone.baseline && typeof clone.baseline === "object") {
    delete clone.baseline.trackSha256;
  }
  return clone;
}

function trackSha256(track) {
  return crypto
    .createHash("sha256")
    .update(canonicalJson(withoutTrackSha(track)), "utf8")
    .digest("hex");
}

function defaultTrackId(selector) {
  return `scroll-${crypto.createHash("sha256").update(selector).digest("hex").slice(0, 16)}`;
}

async function assertReady(page) {
  await page.waitForFunction(() => document.readyState === "complete", null, {
    timeout: 15000,
  });
  await page.evaluate(async () => {
    if (document.fonts && typeof document.fonts.ready?.then === "function") {
      await document.fonts.ready;
    }
  });
}

async function waitAfterReady(page, readyWaitMs) {
  if (readyWaitMs > 0) {
    await page.waitForTimeout(readyWaitMs);
  }
}

async function installScrollTransportProbe(page) {
  await page.addInitScript(() => {
    const listeners = [];
    const originalAddEventListener = EventTarget.prototype.addEventListener;
    const rootTargetName = (target) => {
      if (target === window) return "window";
      if (target === document) return "document";
      if (target === document.documentElement) return "documentElement";
      if (target === document.body) return "body";
      return null;
    };
    const isPassive = (options) => {
      if (options === true || options === false || options == null) return false;
      if (typeof options === "object" && options.passive === true) return true;
      return false;
    };

    Object.defineProperty(window, "__uiCloneReplayTrackWheelListeners", {
      configurable: false,
      enumerable: false,
      value: listeners,
      writable: false,
    });
    Object.defineProperty(window, "__uiCloneReplayTrackWheelEventCount", {
      configurable: false,
      enumerable: false,
      value: 0,
      writable: true,
    });

    originalAddEventListener.call(
      window,
      "wheel",
      () => {
        window.__uiCloneReplayTrackWheelEventCount += 1;
      },
      { capture: true, passive: true },
    );

    EventTarget.prototype.addEventListener = function patchedAddEventListener(type, listener, options) {
      const targetName = rootTargetName(this);
      if (type === "wheel" && targetName && !isPassive(options)) {
        listeners.push({
          target: targetName,
          passive: false,
        });
      }
      return originalAddEventListener.call(this, type, listener, options);
    };
  });
}

async function resolveTarget(page, selector) {
  let count;
  try {
    count = await page.locator(selector).count();
  } catch (error) {
    fail(`--selector is not a valid CSS selector: ${error.message}`);
  }
  if (count !== 1) {
    fail(`--selector must resolve exactly one element, got ${count}`);
  }
  const locator = page.locator(selector);
  if (!(await locator.isVisible())) {
    fail("--selector resolved element is not visible");
  }
  return locator;
}

async function detectScrollTransport(page) {
  const detected = await page.evaluate(() => {
    const html = document.documentElement;
    const body = document.body;
    const hasLenisMarker = (element) => {
      if (!element) return false;
      const classList = Array.from(element.classList ?? []);
      return (
        classList.some((className) => /^lenis(?:-|$)/.test(className)) ||
        element.hasAttribute("data-lenis") ||
        element.getAttribute("data-scroll-engine") === "lenis"
      );
    };
    const hasLocomotiveMarker = (element) => {
      if (!element) return false;
      const classList = Array.from(element.classList ?? []);
      return (
        classList.some((className) =>
          className === "has-scroll-init" ||
          className === "has-scroll-smooth" ||
          className === "locomotive-scroll" ||
          className.startsWith("locomotive-scroll-"),
        ) ||
        element.hasAttribute("data-scroll-container") ||
        element.hasAttribute("data-scroll-section") ||
        element.getAttribute("data-scroll-engine") === "locomotive"
      );
    };

    const lenis = window.lenis || window.__lenis || null;
    const wheelListeners = Array.isArray(window.__uiCloneReplayTrackWheelListeners)
      ? window.__uiCloneReplayTrackWheelListeners
      : [];
    if (lenis && typeof lenis.scrollTo === "function") {
      return { name: "lenis", reason: "window lenis scrollTo instance" };
    }
    if (hasLenisMarker(html) || hasLenisMarker(body)) {
      if (wheelListeners.length > 0) {
        const targets = Array.from(new Set(wheelListeners.map((listener) => listener.target))).sort();
        return { name: "lenis", reason: `html/body lenis marker plus root non-passive wheel listener on ${targets.join(", ")}` };
      }
      return { name: "lenis-unproven", reason: "html/body lenis marker without callable instance or root wheel proof" };
    }

    const locomotive =
      window.locomotive ||
      window.locomotiveScroll ||
      window.__locomotiveScroll ||
      window.__locomotive ||
      (window.scroll && typeof window.scroll.scrollTo === "function" ? window.scroll : null);
    if (locomotive && typeof locomotive.scrollTo === "function") {
      return { name: "locomotive", reason: "Locomotive global scrollTo instance" };
    }
    if (
      hasLocomotiveMarker(html) ||
      hasLocomotiveMarker(body) ||
      document.querySelector("[data-scroll-container], [data-scroll-section]")
    ) {
      return { name: "locomotive", reason: "Locomotive DOM marker" };
    }

    const smootherFactory = window.ScrollSmoother || window.gsap?.core?.globals?.()?.ScrollSmoother;
    const smoother = smootherFactory?.get?.() || window.ScrollSmoother?.get?.() || window.__scrollSmoother || null;
    if (smoother && (typeof smoother.scrollTo === "function" || typeof smoother.scrollTop === "function")) {
      return { name: "ScrollSmoother", reason: "GSAP ScrollSmoother global instance" };
    }

    if (wheelListeners.length > 0) {
      const targets = Array.from(new Set(wheelListeners.map((listener) => listener.target))).sort();
      return { name: "custom-wheel", reason: `root non-passive wheel listener on ${targets.join(", ")}` };
    }
    return null;
  });
  return detected ?? { name: "native", reason: "window scroll" };
}

function validateRequestedScrollTransport(detected, requestedTransport) {
  if (requestedTransport === "lenis-wheel") {
    if (detected.name !== "lenis") {
      fail(`--transport lenis-wheel requires detected Lenis, got ${detected.name}`);
    }
    return;
  }
  if (detected.name !== "native") {
    fail(`custom-scroll-transport-unsupported: ${detected.name} detected via ${detected.reason}`);
  }
}

function scrollPositions(startPx, endPx) {
  return Array.from({ length: SAMPLE_COUNT }, (_, index) => {
    const progress = index / SAMPLE_DENOMINATOR;
    return {
      index,
      progress,
      scrollY: roundNumber(startPx + (endPx - startPx) * progress),
    };
  });
}

function roundNumber(value) {
  if (!Number.isFinite(value)) {
    return value;
  }
  const rounded = Math.round(value * 10000) / 10000;
  return Object.is(rounded, -0) ? 0 : rounded;
}

async function sampleSettled(page, selector, sampleMeta, options = {}) {
  const driveNativeScroll = options.driveNativeScroll !== false;
  const recordActualScrollY = options.recordActualScrollY === true;
  const startedAt = Date.now();
  let previous = null;
  let frames = 0;

  while (frames < SETTLE_FRAME_CAP && Date.now() - startedAt <= SETTLE_TIMEOUT_MS) {
    const observation = await page.evaluate(
      async ({ targetSelector, targetScrollY, shouldDriveNativeScroll }) => {
        const round = (value) => {
          if (!Number.isFinite(value)) return value;
          const rounded = Math.round(value * 10000) / 10000;
          return Object.is(rounded, -0) ? 0 : rounded;
        };
        const collapse = (value) => String(value ?? "").trim().replace(/\s+/g, " ");
        const parseColor = (value) => {
          const match = String(value).match(
            /^rgba?\(\s*([0-9.]+)(?:\s*,\s*|\s+)([0-9.]+)(?:\s*,\s*|\s+)([0-9.]+)(?:\s*(?:,|\/)\s*([0-9.]+%?))?\s*\)$/i,
          );
          if (!match) {
            throw new Error(`unsupported background-color: ${value}`);
          }
          const alphaRaw = match[4] ?? "1";
          const alpha = alphaRaw.endsWith("%") ? Number(alphaRaw.slice(0, -1)) / 100 : Number(alphaRaw);
          return [Number(match[1]), Number(match[2]), Number(match[3]), round(alpha)];
        };
        const transform = (value) => {
          if (!value || value === "none") {
            return { translateX: 0, translateY: 0 };
          }
          const matrix = new DOMMatrixReadOnly(value);
          const epsilon = 0.0001;
          const pureTranslation =
            Math.abs(matrix.m11 - 1) <= epsilon &&
            Math.abs(matrix.m12) <= epsilon &&
            Math.abs(matrix.m13) <= epsilon &&
            Math.abs(matrix.m14) <= epsilon &&
            Math.abs(matrix.m21) <= epsilon &&
            Math.abs(matrix.m22 - 1) <= epsilon &&
            Math.abs(matrix.m23) <= epsilon &&
            Math.abs(matrix.m24) <= epsilon &&
            Math.abs(matrix.m31) <= epsilon &&
            Math.abs(matrix.m32) <= epsilon &&
            Math.abs(matrix.m33 - 1) <= epsilon &&
            Math.abs(matrix.m34) <= epsilon &&
            Math.abs(matrix.m43) <= epsilon &&
            Math.abs(matrix.m44 - 1) <= epsilon;
          if (!pureTranslation) {
            throw new Error(`unsupported transform; only translateX/translateY are allowed: ${value}`);
          }
          return {
            translateX: round(matrix.m41),
            translateY: round(matrix.m42),
          };
        };
        if (shouldDriveNativeScroll) {
          window.scrollTo(0, targetScrollY);
        }
        await new Promise((resolve) => requestAnimationFrame(() => resolve()));

        const element = document.querySelector(targetSelector);
        if (!element) {
          throw new Error("target element disappeared");
        }
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return {
          properties: {
            transform: transform(style.transform),
            opacity: round(Number(style.opacity)),
            clipPath: collapse(style.clipPath || "none"),
            backgroundColor: parseColor(style.backgroundColor),
            height: round(Number.parseFloat(style.height)),
            position: style.position,
          },
          box: {
            x: round(rect.x),
            y: round(rect.y),
            width: round(rect.width),
            height: round(rect.height),
          },
          scrollY: round(window.scrollY),
          timedAnimations: element
            .getAnimations({ subtree: true })
            .filter((animation) => animation.playState === "running")
            .map((animation) => animation.timeline?.constructor?.name ?? "")
            .filter((timelineName) => timelineName !== "ScrollTimeline" && timelineName !== "ViewTimeline"),
        };
      },
      { targetSelector: selector, targetScrollY: sampleMeta.scrollY, shouldDriveNativeScroll: driveNativeScroll },
    );

    frames += 1;
    const { timedAnimations, ...measured } = observation;
    if (timedAnimations.length > 0) {
      fail(
        `scroll-progress mode does not support timed animations on the target subtree at sample ${sampleMeta.index}: ${timedAnimations.join(", ")}; use --mode scroll-action`,
      );
    }
    const serialized = JSON.stringify(measured);
    if (
      frames >= MIN_OBSERVATION_FRAMES &&
      timedAnimations.length === 0 &&
      serialized === previous
    ) {
      return {
        index: sampleMeta.index,
        progress: sampleMeta.progress,
        scrollY: recordActualScrollY ? measured.scrollY : sampleMeta.scrollY,
        properties: measured.properties,
        box: measured.box,
        settle: { status: "settled", frames: 2 },
      };
    }
    previous = serialized;
  }

  fail(
    `sample ${sampleMeta.index} did not settle within ${SETTLE_FRAME_CAP} requestAnimationFrame ticks and ${SETTLE_TIMEOUT_MS}ms`,
  );
}

async function driveLenisWheelToPosition(page, targetScrollY, sampleIndex) {
  const currentScrollY = await page.evaluate(() => window.scrollY);
  const deltaY = targetScrollY - currentScrollY;
  const previousWheelCount = await page.evaluate(() => window.__uiCloneReplayTrackWheelEventCount || 0);
  await page.mouse.wheel(0, deltaY);
  await page.waitForFunction(
    (previous) => (window.__uiCloneReplayTrackWheelEventCount || 0) > previous,
    previousWheelCount,
    { timeout: WHEEL_ACK_TIMEOUT_MS },
  );
  await page.waitForFunction(
    ({ target, epsilon }) => Math.abs(window.scrollY - target) <= epsilon,
    { target: targetScrollY, epsilon: LENIS_WHEEL_ALIGNMENT_EPSILON_PX },
    { timeout: SETTLE_TIMEOUT_MS },
  );
  const actualScrollY = await page.evaluate(() => window.scrollY);
  if (Math.abs(actualScrollY - targetScrollY) > LENIS_WHEEL_ALIGNMENT_EPSILON_PX) {
    fail(
      `lenis-wheel sample ${sampleIndex} scrollY alignment exceeded ${LENIS_WHEEL_ALIGNMENT_EPSILON_PX}px: expected ${targetScrollY}, got ${roundNumber(actualScrollY)}`,
    );
  }
}

async function sampleLenisWheelSettled(page, selector, sampleMeta) {
  await driveLenisWheelToPosition(page, sampleMeta.scrollY, sampleMeta.index);
  return sampleSettled(page, selector, sampleMeta, {
    driveNativeScroll: false,
    recordActualScrollY: true,
  });
}

async function captureScrollAction(page, selector, startPx, endPx) {
  return page.evaluate(
    async ({
      targetSelector,
      targetStartPx,
      targetEndPx,
      sampleDenominator,
      detectionFrames,
      minDenominatorMs,
      maxDenominatorMs,
    }) => {
      const failInPage = (message) => {
        throw new Error(message);
      };
      const raf = () => new Promise((resolve) => requestAnimationFrame(() => resolve()));
      const round = (value) => {
        if (!Number.isFinite(value)) return value;
        const rounded = Math.round(value * 10000) / 10000;
        return Object.is(rounded, -0) ? 0 : rounded;
      };
      const collapse = (value) => String(value ?? "").trim().replace(/\s+/g, " ");
      const parseColor = (value) => {
        const match = String(value).match(
          /^rgba?\(\s*([0-9.]+)(?:\s*,\s*|\s+)([0-9.]+)(?:\s*,\s*|\s+)([0-9.]+)(?:\s*(?:,|\/)\s*([0-9.]+%?))?\s*\)$/i,
        );
        if (!match) {
          failInPage(`unsupported background-color: ${value}`);
        }
        const alphaRaw = match[4] ?? "1";
        const alpha = alphaRaw.endsWith("%") ? Number(alphaRaw.slice(0, -1)) / 100 : Number(alphaRaw);
        return [Number(match[1]), Number(match[2]), Number(match[3]), round(alpha)];
      };
      const transform = (value) => {
        if (!value || value === "none") {
          return { translateX: 0, translateY: 0 };
        }
        const matrix = new DOMMatrixReadOnly(value);
        const epsilon = 0.0001;
        const pureTranslation =
          Math.abs(matrix.m11 - 1) <= epsilon &&
          Math.abs(matrix.m12) <= epsilon &&
          Math.abs(matrix.m13) <= epsilon &&
          Math.abs(matrix.m14) <= epsilon &&
          Math.abs(matrix.m21) <= epsilon &&
          Math.abs(matrix.m22 - 1) <= epsilon &&
          Math.abs(matrix.m23) <= epsilon &&
          Math.abs(matrix.m24) <= epsilon &&
          Math.abs(matrix.m31) <= epsilon &&
          Math.abs(matrix.m32) <= epsilon &&
          Math.abs(matrix.m33 - 1) <= epsilon &&
          Math.abs(matrix.m34) <= epsilon &&
          Math.abs(matrix.m43) <= epsilon &&
          Math.abs(matrix.m44 - 1) <= epsilon;
        if (!pureTranslation) {
          failInPage(`unsupported transform; only translateX/translateY are allowed: ${value}`);
        }
        return {
          translateX: round(matrix.m41),
          translateY: round(matrix.m42),
        };
      };
      const observe = (element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return {
          properties: {
            transform: transform(style.transform),
            opacity: round(Number(style.opacity)),
            clipPath: collapse(style.clipPath || "none"),
            backgroundColor: parseColor(style.backgroundColor),
            height: round(Number.parseFloat(style.height)),
            position: style.position,
          },
          box: {
            x: round(rect.x),
            y: round(rect.y),
            width: round(rect.width),
            height: round(rect.height),
          },
        };
      };
      const stableString = (value) => JSON.stringify(value);
      const timelineName = (animation) => animation.timeline?.constructor?.name ?? "";
      const animationDuration = (animation) => {
        const timing = animation.effect?.getComputedTiming?.();
        const endTime = Number(timing?.endTime);
        if (Number.isFinite(endTime) && endTime > 0) return endTime;
        const duration = Number(timing?.duration);
        if (Number.isFinite(duration) && duration > 0) return duration;
        return NaN;
      };

      const target = document.querySelector(targetSelector);
      if (!target) {
        failInPage("target element disappeared before scroll-action capture");
      }

      window.scrollTo({ top: targetStartPx, left: 0, behavior: "instant" });
      await raf();
      await raf();
      if (Math.round(scrollY) !== targetStartPx) {
        failInPage(`scroll-action start scrollY mismatch: expected ${targetStartPx}, got ${Math.round(scrollY)}`);
      }
      const beforeAnimations = new Set(target.getAnimations({ subtree: true }));
      for (const animation of beforeAnimations) {
        const timeline = timelineName(animation);
        if (timeline === "ScrollTimeline" || timeline === "ViewTimeline") {
          failInPage(`scroll-action unsupported: ${timeline} is scroll-position scrubbed, not a timed action`);
        }
      }
      const beforeObservation = observe(target);

      window.scrollTo({ top: targetEndPx, left: 0, behavior: "instant" });
      let freshAnimations = [];
      for (let frame = 0; frame < detectionFrames; frame += 1) {
        await raf();
        freshAnimations = target
          .getAnimations({ subtree: true })
          .filter((animation) => !beforeAnimations.has(animation));
        if (freshAnimations.length > 0) break;
      }
      if (freshAnimations.length === 0) {
        failInPage(
          `scroll-action unsupported: no fresh CSS Animation objects appeared within ${detectionFrames} rAF; JS timed mutations are not supported`,
        );
      }
      if (Math.round(scrollY) !== targetEndPx) {
        failInPage(`scroll-action target scrollY mismatch: expected ${targetEndPx}, got ${Math.round(scrollY)}`);
      }

      for (const animation of freshAnimations) {
        const animatedTarget = animation.effect?.target;
        if (animatedTarget !== target) {
          failInPage("scroll-action ambiguous: fresh animation target must be the selected node exactly");
        }
        const timeline = timelineName(animation);
        if (timeline === "ScrollTimeline" || timeline === "ViewTimeline") {
          failInPage(`scroll-action unsupported: ${timeline} is scroll-position scrubbed, not a timed action`);
        }
      }

      await Promise.all(freshAnimations.map((animation) => animation.ready.catch(() => null)));
      const startTimes = freshAnimations.map((animation) => Number(animation.startTime));
      if (startTimes.some((value) => !Number.isFinite(value))) {
        failInPage("scroll-action ambiguous: fresh animation startTime is not finite");
      }
      if (Math.max(...startTimes) - Math.min(...startTimes) > 1000 / 60) {
        failInPage("scroll-action ambiguous: fresh animation startTime skew exceeds one frame");
      }
      for (const animation of freshAnimations) {
        animation.pause();
      }

      const rawDenominatorMs = Math.max(...freshAnimations.map(animationDuration));
      if (!Number.isFinite(rawDenominatorMs)) {
        failInPage("scroll-action ambiguous: could not derive a finite animation denominator");
      }
      const denominatorMs = Math.round(rawDenominatorMs);
      if (Math.abs(rawDenominatorMs - denominatorMs) > 0.001) {
        failInPage(`scroll-action denominator ${round(rawDenominatorMs)}ms is not an integer`);
      }
      if (denominatorMs < minDenominatorMs || denominatorMs > maxDenominatorMs) {
        failInPage(
          `scroll-action denominator ${denominatorMs}ms is outside ${minDenominatorMs}..${maxDenominatorMs}ms`,
        );
      }

      const samples = [{
        index: 0,
        elapsedMs: 0,
        scrollY: targetStartPx,
        properties: beforeObservation.properties,
        box: beforeObservation.box,
        settle: { status: "settled", frames: 2 },
      }];
      for (let index = 1; index < sampleDenominator; index += 1) {
        const elapsedMs = round((denominatorMs * index) / sampleDenominator);
        for (const animation of freshAnimations) {
          animation.currentTime = elapsedMs;
          animation.pause();
        }
        const first = observe(target);
        await raf();
        const second = observe(target);
        if (stableString(first) !== stableString(second)) {
          failInPage(`scroll-action unstable: paused read drift at sample ${index}`);
        }
        if (Math.round(scrollY) !== targetEndPx) {
          failInPage(`scroll-action sample ${index} scrollY mismatch: expected ${targetEndPx}, got ${Math.round(scrollY)}`);
        }
        samples.push({
          index,
          elapsedMs,
          scrollY: targetEndPx,
          properties: first.properties,
          box: first.box,
          settle: { status: "paused", frames: 2 },
        });
      }

      for (const animation of freshAnimations) {
        animation.finish();
        animation.pause();
      }
      const terminalFirst = observe(target);
      await raf();
      const terminalSecond = observe(target);
      if (stableString(terminalFirst) !== stableString(terminalSecond)) {
        failInPage("scroll-action unstable: terminal finished read drifted");
      }
      if (Math.round(scrollY) !== targetEndPx) {
        failInPage(`scroll-action terminal scrollY mismatch: expected ${targetEndPx}, got ${Math.round(scrollY)}`);
      }
      samples.push({
        index: sampleDenominator,
        elapsedMs: denominatorMs,
        scrollY: targetEndPx,
        properties: terminalFirst.properties,
        box: terminalFirst.box,
        settle: { status: "settled", frames: 2 },
      });

      return {
        denominatorMs,
        animationCount: freshAnimations.length,
        animationConstructors: Array.from(new Set(freshAnimations.map((animation) => animation.constructor?.name ?? ""))).sort(),
        beforeObservation,
        samples,
      };
    },
    {
      targetSelector: selector,
      targetStartPx: startPx,
      targetEndPx: endPx,
      sampleDenominator: SAMPLE_DENOMINATOR,
      detectionFrames: ACTION_DETECTION_FRAMES,
      minDenominatorMs: MIN_ACTION_DENOMINATOR_MS,
      maxDenominatorMs: MAX_ACTION_DENOMINATOR_MS,
    },
  );
}

function realGap(ms = VIRTUAL_REAL_GAP_MS) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function closeNumber(left, right, epsilon = 0.0001) {
  return Math.abs(Number(left) - Number(right)) <= epsilon;
}

function observationsMatch(left, right) {
  return (
    closeNumber(left.properties.transform.translateX, right.properties.transform.translateX) &&
    closeNumber(left.properties.transform.translateY, right.properties.transform.translateY) &&
    closeNumber(left.properties.opacity, right.properties.opacity) &&
    left.properties.clipPath === right.properties.clipPath &&
    left.properties.position === right.properties.position &&
    left.properties.backgroundColor.length === right.properties.backgroundColor.length &&
    left.properties.backgroundColor.every((value, index) =>
      closeNumber(value, right.properties.backgroundColor[index]),
    ) &&
    closeNumber(left.properties.height, right.properties.height) &&
    closeNumber(left.box.x, right.box.x) &&
    closeNumber(left.box.y, right.box.y) &&
    closeNumber(left.box.width, right.box.width) &&
    closeNumber(left.box.height, right.box.height)
  );
}

async function observeTarget(page, selector) {
  return page.evaluate((targetSelector) => {
    const failInPage = (message) => {
      throw new Error(message);
    };
    const round = (value) => {
      if (!Number.isFinite(value)) return value;
      const rounded = Math.round(value * 10000) / 10000;
      return Object.is(rounded, -0) ? 0 : rounded;
    };
    const collapse = (value) => String(value ?? "").trim().replace(/\s+/g, " ");
    const parseColor = (value) => {
      const match = String(value).match(
        /^rgba?\(\s*([0-9.]+)(?:\s*,\s*|\s+)([0-9.]+)(?:\s*,\s*|\s+)([0-9.]+)(?:\s*(?:,|\/)\s*([0-9.]+%?))?\s*\)$/i,
      );
      if (!match) {
        failInPage(`unsupported background-color: ${value}`);
      }
      const alphaRaw = match[4] ?? "1";
      const alpha = alphaRaw.endsWith("%") ? Number(alphaRaw.slice(0, -1)) / 100 : Number(alphaRaw);
      return [Number(match[1]), Number(match[2]), Number(match[3]), round(alpha)];
    };
    const transform = (value) => {
      if (!value || value === "none") {
        return { translateX: 0, translateY: 0 };
      }
      const matrix = new DOMMatrixReadOnly(value);
      const epsilon = 0.0001;
      const pureTranslation =
        Math.abs(matrix.m11 - 1) <= epsilon &&
        Math.abs(matrix.m12) <= epsilon &&
        Math.abs(matrix.m13) <= epsilon &&
        Math.abs(matrix.m14) <= epsilon &&
        Math.abs(matrix.m21) <= epsilon &&
        Math.abs(matrix.m22 - 1) <= epsilon &&
        Math.abs(matrix.m23) <= epsilon &&
        Math.abs(matrix.m24) <= epsilon &&
        Math.abs(matrix.m31) <= epsilon &&
        Math.abs(matrix.m32) <= epsilon &&
        Math.abs(matrix.m33 - 1) <= epsilon &&
        Math.abs(matrix.m34) <= epsilon &&
        Math.abs(matrix.m43) <= epsilon &&
        Math.abs(matrix.m44 - 1) <= epsilon;
      if (!pureTranslation) {
        failInPage(`unsupported transform; only translateX/translateY are allowed: ${value}`);
      }
      return {
        translateX: round(matrix.m41),
        translateY: round(matrix.m42),
      };
    };

    const target = document.querySelector(targetSelector);
    if (!target) {
      failInPage("target element disappeared before virtual-clock observation");
    }
    const style = getComputedStyle(target);
    const rect = target.getBoundingClientRect();
    return {
      scrollY: Math.round(window.scrollY),
      properties: {
        transform: transform(style.transform),
        opacity: round(Number(style.opacity)),
        clipPath: collapse(style.clipPath || "none"),
        backgroundColor: parseColor(style.backgroundColor),
        height: round(Number.parseFloat(style.height)),
        position: style.position,
      },
      box: {
        x: round(rect.x),
        y: round(rect.y),
        width: round(rect.width),
        height: round(rect.height),
      },
    };
  }, selector);
}

async function assertNoTargetOwnAnimations(page, selector, phase) {
  const animations = await page.evaluate((targetSelector) => {
    const target = document.querySelector(targetSelector);
    if (!target) {
      throw new Error("target element disappeared before virtual-clock animation check");
    }
    return target.getAnimations({ subtree: false }).map((animation) => ({
      constructorName: animation.constructor?.name ?? "",
      timelineName: animation.timeline?.constructor?.name ?? "",
      playState: animation.playState,
    }));
  }, selector);
  if (animations.length > 0) {
    fail(`virtual-clock unsupported: target owns Animation objects during ${phase}`);
  }
}

async function stableVirtualRead(page, selector, sampleIndex) {
  await assertNoTargetOwnAnimations(page, selector, `sample ${sampleIndex} first read`);
  const first = await observeTarget(page, selector);
  await realGap();
  await assertNoTargetOwnAnimations(page, selector, `sample ${sampleIndex} second read`);
  const second = await observeTarget(page, selector);
  if (!observationsMatch(first, second)) {
    fail(`virtual-clock unstable: real-gap observation drift at sample ${sampleIndex}`);
  }
  return first;
}

async function settleVirtualState(page, selector) {
  let previous = await stableVirtualRead(page, selector, "settle-0");
  let consecutiveStableTicks = 0;
  const maxTicks = VIRTUAL_SETTLE_MAX_MS / VIRTUAL_TICK_MS;
  for (let frame = 1; frame <= maxTicks; frame += 1) {
    await page.clock.runFor(VIRTUAL_TICK_MS);
    const current = await stableVirtualRead(page, selector, `settle-${frame}`);
    if (observationsMatch(previous, current)) {
      consecutiveStableTicks += 1;
      if (consecutiveStableTicks >= VIRTUAL_SETTLE_CONSECUTIVE_TICKS) {
        return current;
      }
    } else {
      consecutiveStableTicks = 0;
    }
    previous = current;
  }
  fail(`virtual-clock unstable: initial state did not settle within ${VIRTUAL_SETTLE_MAX_MS}ms`);
}

async function captureScrollActionVirtualClock(page, selector, startPx, endPx, denominatorMs) {
  const stepMs = denominatorMs / SAMPLE_DENOMINATOR;
  if (!Number.isInteger(stepMs) || stepMs % VIRTUAL_TICK_MS !== 0) {
    fail(`virtual-clock denominator step must be an integer multiple of ${VIRTUAL_TICK_MS}ms`);
  }

  await page.evaluate((targetStartPx) => window.scrollTo({ top: targetStartPx, left: 0, behavior: "instant" }), startPx);
  await page.clock.runFor(VIRTUAL_TICK_MS);
  let currentScrollY = Math.round(await page.evaluate(() => window.scrollY));
  if (currentScrollY !== startPx) {
    fail(`virtual-clock start scrollY mismatch: expected ${startPx}, got ${currentScrollY}`);
  }
  const settledStart = await settleVirtualState(page, selector);
  if (settledStart.scrollY !== startPx) {
    fail(`virtual-clock settled start scrollY mismatch: expected ${startPx}, got ${settledStart.scrollY}`);
  }

  const sample0 = await stableVirtualRead(page, selector, 0);
  if (sample0.scrollY !== startPx) {
    fail(`virtual-clock sample 0 scrollY mismatch: expected ${startPx}, got ${sample0.scrollY}`);
  }
  const samples = [{
    index: 0,
    elapsedMs: 0,
    scrollY: sample0.scrollY,
    properties: sample0.properties,
    box: sample0.box,
    settle: { status: "settled", frames: 2 },
  }];

  await page.evaluate((targetEndPx) => window.scrollTo({ top: targetEndPx, left: 0, behavior: "instant" }), endPx);
  await realGap();
  currentScrollY = Math.round(await page.evaluate(() => window.scrollY));
  if (currentScrollY !== endPx) {
    fail(`virtual-clock target scrollY mismatch: expected ${endPx}, got ${currentScrollY}`);
  }
  await assertNoTargetOwnAnimations(page, selector, "post-scroll");

  for (let index = 1; index <= SAMPLE_DENOMINATOR; index += 1) {
    await page.clock.runFor(stepMs);
    const observation = await stableVirtualRead(page, selector, index);
    samples.push({
      index,
      elapsedMs: stepMs * index,
      scrollY: observation.scrollY,
      properties: observation.properties,
      box: observation.box,
      settle: {
        status: index === SAMPLE_DENOMINATOR ? "settled" : "paused",
        frames: 2,
      },
    });
  }

  await page.clock.runFor(VIRTUAL_TICK_MS);
  await page.clock.runFor(VIRTUAL_TICK_MS);
  const terminal = await stableVirtualRead(page, selector, "terminal");
  if (!observationsMatch(samples[SAMPLE_DENOMINATOR], terminal)) {
    fail("virtual-clock unstable: terminal state drifted after two virtual ticks");
  }

  return {
    denominatorMs,
    animationCount: 0,
    animationConstructors: [],
    samples,
  };
}

async function fingerprint(page, selector) {
  return page.evaluate((targetSelector) => {
    const element = document.querySelector(targetSelector);
    if (!element) {
      throw new Error("target element disappeared before fingerprinting");
    }
    const tagPath = (node) => {
      const parts = [];
      let current = node;
      while (current && current.nodeType === Node.ELEMENT_NODE) {
        const tag = current.tagName.toLowerCase();
        if (tag === "html") {
          parts.unshift("html");
          break;
        }
        const siblings = Array.from(current.parentElement?.children ?? []).filter(
          (sibling) => sibling.tagName === current.tagName,
        );
        const ordinal = siblings.length > 1 ? `:nth-of-type(${siblings.indexOf(current) + 1})` : "";
        parts.unshift(`${tag}${ordinal}`);
        current = current.parentElement;
      }
      return parts.join(">");
    };
    return {
      selector: targetSelector,
      role: element.getAttribute("role") || "",
      text: String(element.innerText ?? "").trim().replace(/\s+/g, " "),
      path: tagPath(element),
    };
  }, selector);
}

async function atomicWriteJson(outPath, data) {
  const parent = path.dirname(outPath);
  const basename = path.basename(outPath);
  await fs.mkdir(parent, { recursive: true });
  const tempPath = path.join(parent, `.${basename}.${process.pid}.${Date.now()}.tmp`);
  try {
    await fs.writeFile(tempPath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
    await fs.rename(tempPath, outPath);
  } catch (error) {
    await fs.rm(tempPath, { force: true });
    throw error;
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  let browser;
  try {
    const launchOptions = {
      headless: true,
      args: ["--disable-smooth-scrolling"],
    };
    if (args.executablePath) {
      launchOptions.executablePath = args.executablePath;
    } else {
      launchOptions.channel = args.channel;
    }
    browser = await chromium.launch(launchOptions);
    const context = await browser.newContext({
      viewport: { width: args.viewportWidth, height: args.viewportHeight },
      colorScheme: "light",
      reducedMotion: "no-preference",
    });
    const page = await context.newPage();
    await installScrollTransportProbe(page);
    if (args.mode === "scroll-action" && args.driver === "virtual-clock") {
      await page.clock.install({ time: new Date(VIRTUAL_EPOCH_MS) });
    }
    await page.goto(args.url, { waitUntil: "domcontentloaded" });
    await assertReady(page);
    await waitAfterReady(page, args.readyWaitMs);
    const scrollTransport = await detectScrollTransport(page);
    validateRequestedScrollTransport(scrollTransport, args.transport);
    if (args.mode === "scroll-action" && args.driver === "virtual-clock") {
      const now = await page.evaluate(() => Date.now());
      if (now > args.anchorMs) {
        fail(`virtual-clock anchor already passed: Date.now()=${now}, anchor=${args.anchorMs}`);
      }
      await page.clock.pauseAt(new Date(args.anchorMs));
      const pausedNow = await page.evaluate(() => Date.now());
      if (pausedNow !== args.anchorMs) {
        fail(`virtual-clock failed to pause at anchor: expected ${args.anchorMs}, got ${pausedNow}`);
      }
    }
    await resolveTarget(page, args.selector);

    let samples;
    let actionMeta = null;
    if (args.mode === "scroll-action") {
      actionMeta =
        args.driver === "virtual-clock"
          ? await captureScrollActionVirtualClock(
              page,
              args.selector,
              args.startPx,
              args.endPx,
              args.denominatorMs,
            )
          : await captureScrollAction(page, args.selector, args.startPx, args.endPx);
      samples = actionMeta.samples;
    } else {
      samples = [];
      if (args.transport === "lenis-wheel") {
        await page.mouse.move(10, 10);
      }
      for (const position of scrollPositions(args.startPx, args.endPx)) {
        samples.push(
          args.transport === "lenis-wheel"
            ? await sampleLenisWheelSettled(page, args.selector, position)
            : await sampleSettled(page, args.selector, position),
        );
      }
    }

    const track = {
      schemaVersion: 1,
      trackId: args.trackId || defaultTrackId(args.selector),
      trigger: actionMeta
        ? {
            type: "scroll-action",
            action: "scrollTo",
            driver: args.driver,
            fromScrollY: args.startPx,
            toScrollY: args.endPx,
            denominatorMs: actionMeta.denominatorMs,
            ...(args.readyWaitMs > 0 ? { readyWaitMs: args.readyWaitMs } : {}),
            ...(args.driver === "virtual-clock"
              ? { clock: { epochMs: VIRTUAL_EPOCH_MS, anchorMs: args.anchorMs } }
              : {}),
          }
        : {
            type: "scroll-progress",
            startPx: args.startPx,
            endPx: args.endPx,
            sampleDenominator: SAMPLE_DENOMINATOR,
            ...(args.readyWaitMs > 0 ? { readyWaitMs: args.readyWaitMs } : {}),
            ...(args.transport === "lenis-wheel" ? { transport: "lenis-wheel" } : {}),
          },
      node: {
        selector: args.selector,
        fingerprint: await fingerprint(page, args.selector),
      },
      samples,
      baseline: {
        recording: 1,
        trackSha256: args.baselineSha || "",
      },
    };
    if (!args.baselineSha) {
      track.baseline.trackSha256 = trackSha256(track);
    }

    await atomicWriteJson(args.out, track);
    process.stdout.write(
      `${JSON.stringify({
        out: args.out,
        browserVersion: browser.version(),
        playwrightVersion,
        samples: samples.length,
        mode: args.mode,
        driver: args.mode === "scroll-action" ? args.driver : undefined,
        transport: args.transport,
        scrollTransport: scrollTransport.name,
        scrollTransportReason: scrollTransport.reason,
      })}\n`,
    );
  } finally {
    if (browser) {
      await browser.close();
    }
  }
}

main().catch((error) => {
  process.stderr.write(`capture-replay-track: ${error.message}\n`);
  process.exitCode = 1;
});
