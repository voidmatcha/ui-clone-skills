#!/usr/bin/env bash
# capture-states.sh — Phase A splash transition snapshots
#
# Captures DOM state transitions during page initial-load/splash so the impl
# can replicate the bridge between `is-loading` and `is-loaded`-style states
# instead of guessing from a single post-settled snapshot.
#
# Design decisions (see docs/multi-snapshot-capture-design.md):
#   - Single `agent-browser eval` with in-page Promise loop, not 50 shell
#     evals @ 100ms (CLI round-trip cost + no latency guarantee).
#   - State-hash includes html/body class + scroll lock + full-screen overlay
#     presence + DOM length + computed-style fingerprint — class is one
#     signal, not THE signal.
#   - Compact deltas as default; full DOM only for 0ms / settled / structural
#     mutations above threshold (DOM length delta > 20%).
#   - Derived `${SESSION}-states` session to avoid race with parallel
#     capture.sh mutations on the same page.
#   - summary.json metadata distinguishes "static checked" from "capture
#     failed" from "legacy ref dir without states/".
#
# Usage:
#   capture-states.sh <url> <session> <ref_dir> [--reuse-session]
#
# By default opens its own derived session `${session}-states`. Pass
# `--reuse-session` to use the caller's session directly (only safe when
# capture-states.sh is called sequentially from capture.sh on a quiet
# session).
#
# Output:
#   <ref_dir>/states/splash/trajectory.json   — array of {ts_ms, hash, bodyClass, htmlClass, compositeDigest, full: bool}
#   <ref_dir>/states/splash/summary.json      — {checked, durationMs, polls, timedOut, splashTimedOut, reason}
#   <ref_dir>/states/splash/0ms.json          — full outerHTML at t=0
#   <ref_dir>/states/splash/settled.json      — full outerHTML at end-of-loop
#   <ref_dir>/states/splash/<NNN>ms.json      — full outerHTML when structural mutation > 20%
#   <ref_dir>/states/splash/contract.json     — splash lifecycle verdict + absence certificate (ui_clone.splash_contract)
#
# Exit codes:
#   0  capture completed (transitions may be 0 — that's the "static page" case)
#   1  bad usage
#   2  agent-browser open failed
#   3  agent-browser eval returned unparseable / error response

set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <url> <session> <ref_dir> [--reuse-session]" >&2
  exit 1
fi

URL="$1"
SESSION="$2"
REF_DIR="$3"
CAPTURE_COLOR_SCHEME="${AGENT_BROWSER_COLOR_SCHEME:-light}"
REUSE_SESSION="false"
if [ "${4:-}" = "--reuse-session" ]; then
  REUSE_SESSION="true"
fi

STATES_SESSION="${SESSION}-states"
if [ "$REUSE_SESSION" = "true" ]; then
  STATES_SESSION="$SESSION"
fi
if [ "$REUSE_SESSION" = "false" ] && [ -z "${AGENT_BROWSER_NAMESPACE:-}" ]; then
  CAPTURE_NAMESPACE_ID="$(printf '%s' "$SESSION" | cksum | awk '{print $1}')"
  AGENT_BROWSER_NAMESPACE="ui-clone-${CAPTURE_NAMESPACE_ID}"
  export AGENT_BROWSER_NAMESPACE
fi
CAPTURE_MODE="pre-navigation"
if [ "$REUSE_SESSION" = "true" ]; then
  CAPTURE_MODE="reuse-session"
fi

OUTDIR="${REF_DIR}/${STATES_PREFIX:-states}/splash"
mkdir -p "$OUTDIR"
INIT_SCRIPT=""
RESPONSE_TMP=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/extract/capture-browser-bootstrap.sh
source "$SCRIPT_DIR/capture-browser-bootstrap.sh"
ORIGIN_VALIDATOR="$SCRIPT_DIR/validate-agent-browser-origin.py"
NAVIGATION_RECEIPT="$REF_DIR/capture-states-navigation.json"
if [ "$REUSE_SESSION" = "true" ]; then
  NAVIGATION_RECEIPT="$REF_DIR/capture-navigation.json"
else
  unset AGENT_BROWSER_COLOR_SCHEME
fi

# Native launch hashing includes init-script paths. Keep the owned session's
# launch options identical from its first command until it is closed.
state-agent-browser() {
  if [ -n "$INIT_SCRIPT" ]; then
    agent-browser --session "$STATES_SESSION" --init-script "$INIT_SCRIPT" "$@"
  else
    agent-browser --session "$STATES_SESSION" "$@"
  fi
}

cleanup() {
  if [ "$REUSE_SESSION" = "false" ]; then
    state-agent-browser close >/dev/null 2>&1 || true
  fi
  rm -f "${INIT_SCRIPT:-}" "${RESPONSE_TMP:-}"
}

trap cleanup EXIT

# In-page state-hash poller. Single eval — no CLI round-trip per poll.
# djb2 hash over a composite of: html/body class + scroll lock + full-screen
# overlay presence + DOM length + computed-style fingerprint of top-3
# above-the-fold elements.
# shellcheck disable=SC2016 # JavaScript template literals are intentionally shell-literal.
EVAL_JS='(async () => {
  const states = [];
  while (!document.documentElement || !document.body) {
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  const startedAt = performance.now();
  let lastHash = null;
  let lastChangeAt = startedAt;
  let lastSplashHash = null;
  let lastSplashChangeAt = startedAt;
  let splashSettledAt = null;
  let visibleStructureBaseline = null;
  let materialVisualBaseline = null;
  let visibleStructureEpoch = 0;

  const cssEscape = (value) => {
    const raw = String(value || "");
    if (window.CSS && typeof window.CSS.escape === "function") return window.CSS.escape(raw);
    return raw.replace(/[^a-zA-Z0-9_-]/g, (char) => `\\${char.codePointAt(0).toString(16)} `);
  };

  const cssString = (value) => String(value || "").replace(/\\/g, "\\\\").replace(/"/g, "\\\"");

  const nthOfTypePath = (el) => {
    const parts = [];
    let cur = el;
    while (
      cur &&
      cur.nodeType === Node.ELEMENT_NODE &&
      cur !== document.body &&
      cur !== document.documentElement
    ) {
      if (parts.length >= 8) return null;
      const tag = cur.localName;
      const parent = cur.parentNode;
      if (!tag || !parent || !parent.children) break;
      const siblings = Array.from(parent.children).filter((sibling) => (
        sibling.localName === tag
      ));
      parts.unshift(`${tag}:nth-of-type(${siblings.indexOf(cur) + 1})`);
      if (parent.nodeType === Node.DOCUMENT_FRAGMENT_NODE && parent.host) {
        // Top of an open shadow tree. Anchor the path on the host so a shadow
        // child cannot share an identity with a light-DOM element sitting at
        // the same nth-of-type position.
        const host = identityFor(parent.host);
        return host ? `${host} >>> ${parts.join(" > ")}` : null;
      }
      cur = parent;
    }
    return parts.length ? `body > ${parts.join(" > ")}` : null;
  };

  // Every rendered element, descending into open shadow roots.
  // `document.querySelectorAll("body *")` stops at shadow boundaries, so a
  // splash mounted inside a web component never reached the probe.
  const eachRenderedElement = function* (root) {
    for (const el of root.querySelectorAll("*")) {
      yield el;
      if (el.shadowRoot) yield* eachRenderedElement(el.shadowRoot);
    }
  };

  const selectorFor = (el) => {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;
    const tag = el.localName || "element";
    if (el.id) return `#${cssEscape(el.id)}`;
    const classes = (el.getAttribute("class") || "").trim().split(/\s+/).filter(Boolean);
    if (classes.length) return `${tag}.${classes.slice(0, 3).map(cssEscape).join(".")}`;
    for (const attr of ["data-testid", "data-test", "data-cy", "aria-label", "name", "role"]) {
      const value = el.getAttribute(attr);
      if (value) return `${tag}[${attr}="${cssString(value)}"]`;
    }
    return nthOfTypePath(el);
  };

  // The identity of an element with no id and no identifying attribute is the
  // NODE, not its DOM position. Keyed by nth-of-type path alone, a class-only
  // preloader at body > div:nth-of-type(1) that is removed hands that exact
  // key to the wrapper behind it, the covering map never records an exit, and
  // a page with a loader certifies absence. Each such node is given a serial
  // the first time it is surveyed and keeps it for the life of the page (a
  // WeakMap, so a sibling inserted in front of it later does not rename it);
  // the path is kept as a readable label. A node too deep for nthOfTypePath
  // still gets an identity, so a deeply nested loader cannot fall out of the
  // survey by depth alone. Ids and identifying attributes stay role-keyed: a
  // hydration pass that replaces #app with a fresh #app is not a splash exit.
  //
  // One replacement is not an exit. A framework re-mount (React/Next hydration
  // mismatch, a skeleton swapped for its content) throws an id-less node away
  // and mounts a fresh one in its place; keyed by node alone that reads as the
  // old node leaving, and a page with no splash is refused. A fresh node
  // inherits the identity of the node it replaced only when ALL of these hold,
  // each checked against the immediately preceding survey and nothing older:
  //   - the fresh node did not exist at the previous survey (never walked,
  //     rendered or hidden - a wrapper that was merely visibility: hidden and
  //     is now shown is not fresh, which is the aliasing case above);
  //   - the previous survey recorded a serial-keyed covering node at the same
  //     nth-of-type path, and that node is now detached from the document (a
  //     loader that is still connected but hidden or moved is not replaced);
  //   - both carry the same tag and the same set of classes.
  // A loader replaced in place by content of a different class, a loader
  // removed with its replacement mounted more than one poll later, and the
  // aliasing case all still record an exit. Same tag, same classes, same
  // path, same poll: no DOM instrument can tell that from a re-mount.
  const nodeSerials = new WeakMap();
  let nextNodeSerial = 1;
  const surveyedNodes = new WeakSet();
  let previousCoveringNodes = new Map();
  let currentCoveringNodes = new Map();
  const nodeSignature = (el) => {
    const classes = (el.getAttribute("class") || "").trim().split(/\s+/).filter(Boolean).sort();
    return `${el.localName || "element"}.${classes.join(".")}`;
  };
  const nodeIdentityFor = (el, createdSinceLastSurvey) => {
    let record = nodeSerials.get(el);
    if (!record) {
      const path = nthOfTypePath(el);
      const predecessor = path ? previousCoveringNodes.get(path) : null;
      if (
        createdSinceLastSurvey && predecessor && predecessor.el !== el &&
        predecessor.el.isConnected === false && predecessor.signature === nodeSignature(el)
      ) {
        record = { identity: predecessor.identity, path };
      } else {
        record = { identity: `${path || "deep"} @n${nextNodeSerial}`, path };
        nextNodeSerial += 1;
      }
      nodeSerials.set(el, record);
    }
    return record.identity;
  };
  // Called for every serial-keyed node the current survey records as covering,
  // so the next survey can recognise an in-place replacement of it.
  const noteCoveringNode = (el, identity) => {
    const record = nodeSerials.get(el);
    if (!record || !record.path) return;
    currentCoveringNodes.set(record.path, { el, identity, signature: nodeSignature(el) });
  };

  const identityFor = (el, createdSinceLastSurvey) => {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;
    const tag = el.localName || "element";
    if (el.id) return `#${cssEscape(el.id)}`;
    for (const attr of ["data-testid", "data-test", "data-cy", "aria-label", "name", "role"]) {
      const value = el.getAttribute(attr);
      if (value) return `${tag}[${attr}="${cssString(value)}"]`;
    }
    return nodeIdentityFor(el, createdSinceLastSurvey === true);
  };

  // One pass over every rendered element measures two different things.
  //
  // `overlay` is the splash probe: the largest viewport-covering element that
  // is positioned like an overlay (fixed, or absolute with z-index >= 10). Its
  // lifecycle drives `detected` and exit timing. It cannot classify an in-flow
  // full-viewport loader or a low-z absolute one, because in a single sample
  // those look exactly like a hero section.
  //
  // `covering` is what makes their lifecycle visible: EVERY rendered element
  // covering at least COVERING_RECORD_FLOOR of the viewport, keyed by identity,
  // whatever its position or z-index. A loader leaves; a hero stays. The
  // python writer compares this map across samples (ui_clone.splash_contract):
  // an identity that reached COVERING_ENTER and later fell from its own peak
  // past the writer COVERING_EXIT line, or vanished, is an exit. The floor is
  // deliberately lower than that exit line, so a curtain that settles at 35%
  // arrives at the writer as a measured 35% rather than as an absence, and the
  // 45% mount line stays reachable. Both numbers are substituted below from
  // ui_clone.splash_contract, where they are pinned to the thresholds of the
  // lifecycle probe itself: whatever splash-lifecycle-probe.js would call a
  // candidate, this survey records. (No apostrophes in these comments: EVAL_JS
  // is a single-quoted shell string.)
  const COVERING_ENTER = __UI_CLONE_COVERING_ENTER__;
  const COVERING_RECORD_FLOOR = __UI_CLONE_COVERING_RECORD_FLOOR__;
  const surveyViewportCoverage = () => {
    const vw = window.innerWidth, vh = window.innerHeight;
    let best = null;
    const covering = {};
    for (const el of eachRenderedElement(document.body)) {
      try {
        // Recorded before any filter: a node the previous survey walked, even
        // hidden or too small, is not a fresh node.
        const createdSinceLastSurvey = !surveyedNodes.has(el);
        surveyedNodes.add(el);
        const r = el.getBoundingClientRect();
        const visibleWidth = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
        const visibleHeight = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
        const viewportCoverage = Math.min(1, (visibleWidth * visibleHeight) / Math.max(vw * vh, 1));
        if (viewportCoverage < COVERING_RECORD_FLOOR) continue;
        const cs = getComputedStyle(el);
        const opacity = Number.parseFloat(cs.opacity || "1");
        const rendered = opacity > 0.05 && cs.visibility !== "hidden" && cs.display !== "none";
        if (!rendered) continue;
        const identity = identityFor(el, createdSinceLastSurvey);
        if (identity) {
          covering[identity] = Math.round(viewportCoverage * 100) / 100;
          noteCoveringNode(el, identity);
        }
        const coversViewport = viewportCoverage >= 0.75;
        if (!coversViewport) continue;
        const z = parseInt(cs.zIndex || "0", 10) || 0;
        if (cs.position === "sticky") continue;
        if (cs.position === "fixed" || (cs.position === "absolute" && z >= 10)) {
          const candidate = {
            selector: selectorFor(el),
            identity,
            coverage: Math.round(viewportCoverage * 1000) / 1000,
            visible: true,
            opacity: cs.opacity,
            position: cs.position,
            zIndex: z,
          };
          if (!best || candidate.coverage > best.coverage) best = candidate;
        }
      } catch (e) {}
    }
    // Only the immediately preceding survey can vouch for a replacement.
    previousCoveringNodes = currentCoveringNodes;
    currentCoveringNodes = new Map();
    return {
      overlay: best || { selector: null, identity: null, coverage: 0, visible: false, opacity: "0" },
      covering,
    };
  };

  const animationEvidence = () => {
    const samples = [];
    let runningCount = 0;
    for (const animation of document.getAnimations()) {
      const target = animation.effect && animation.effect.target;
      const timing = animation.effect && animation.effect.getTiming ? animation.effect.getTiming() : {};
      if (animation.playState === "running") runningCount++;
      if (samples.length < 8) {
        samples.push({
          selector: selectorFor(target),
          playState: animation.playState,
          currentTime: Math.round(Number(animation.currentTime || 0)),
          duration: Number.isFinite(Number(timing.duration)) ? Number(timing.duration) : timing.duration,
          delay: Number(timing.delay || 0),
        });
      }
    }
    return { activeCount: document.getAnimations().length, runningCount, samples };
  };

  const mediaFingerprint = () => {
    const videos = Array.from(document.querySelectorAll("video, audio")).slice(0, 6).map((el) => ({
      selector: selectorFor(el),
      src: el.currentSrc || el.src || "",
      currentTime: Math.round(Number(el.currentTime || 0) * 1000) / 1000,
      paused: Boolean(el.paused),
      readyState: Number(el.readyState || 0),
    }));
    const raw = JSON.stringify(videos);
    return { videos, hash: String(cheapHash(raw)) };
  };

  const fingerprintTopElements = () => {
    const top = [];
    const all = document.body ? document.body.querySelectorAll("*") : [];
    let picked = 0;
    for (const el of all) {
      try {
        const r = el.getBoundingClientRect();
        if (r.top < window.innerHeight && r.bottom > 0 && r.width > 100 && r.height > 50) {
          const cs = getComputedStyle(el);
          top.push([cs.color, cs.opacity, cs.transform, cs.visibility, Math.round(r.top), Math.round(r.height)].join(":"));
          picked++;
          if (picked >= 3) break;
        }
      } catch (e) {}
    }
    return top.join("|");
  };

  // Splash settlement must not depend on timer-driven transforms, media time,
  // animation counts, or offscreen hydration pruning. It still needs a
  // structural channel for an in-viewport loading shell that is replaced
  // without an overlay or root-class lifecycle. Record stable DOM identities
  // for rendered elements intersecting the viewport. Layout geometry and
  // transient motion styles are excluded; stable image, poster, background,
  // mask, and SVG geometry identities remain observable.
  const effectivelyRendered = (el, ownStyle) => {
    let opacity = 1;
    let cur = el;
    while (cur && cur.nodeType === Node.ELEMENT_NODE) {
      const cs = cur === el ? ownStyle : getComputedStyle(cur);
      if (cs.display === "none" || cs.visibility === "hidden") return false;
      const ownOpacity = Number.parseFloat(cs.opacity || "1");
      opacity *= Number.isFinite(ownOpacity) ? ownOpacity : 1;
      if (opacity <= 0.05) return false;
      cur = cur.parentElement;
    }
    return true;
  };

  const stableVisualIdentity = (el, cs) => {
    const tag = el.localName || "";
    const parts = [];
    const attr = (name) => String(el.getAttribute(name) || "");
    if (tag === "img") {
      parts.push(`img:${String(el.currentSrc || attr("src"))}:${attr("srcset")}`);
    } else if (tag === "video") {
      parts.push(`video-poster:${attr("poster")}`);
    }
    // Animation ownership does not prove that an asset change is unrelated
    // to loading. Keep visual replacements observable, even on animated nodes.
    for (const [name, value] of [
      ["background", cs.backgroundImage],
      ["mask", cs.maskImage],
      ["webkit-mask", cs.webkitMaskImage],
    ]) {
      const normalized = String(value || "");
      if (normalized && normalized !== "none") parts.push(`${name}:${normalized}`);
    }
    const svgGeometry = new Set([
      "svg", "path", "use", "image", "polygon", "polyline", "circle",
      "ellipse", "line", "rect",
    ]);
    if (svgGeometry.has(tag)) {
      const geometryAttrs = [
        "viewBox", "d", "points", "href", "xlink:href", "pathLength",
        "width", "height", "cx", "cy", "r", "rx", "ry",
      ];
      const geometry = geometryAttrs
        .map((name) => [name, attr(name)])
        .filter((entry) => entry[1])
        .map((entry) => `${entry[0]}=${entry[1]}`)
        .join(",");
      if (geometry) parts.push(`${tag}:${geometry}`);
    }
    return parts.join("|").slice(0, 2048);
  };

  const visibleStructureLabels = () => {
    const labels = [];
    const materialVisuals = new Map();
    for (const el of eachRenderedElement(document.body)) {
      try {
        const r = el.getBoundingClientRect();
        if (r.right <= 0 || r.left >= window.innerWidth || r.bottom <= 0 || r.top >= window.innerHeight) continue;
        const cs = getComputedStyle(el);
        if (!effectivelyRendered(el, cs)) continue;
        const selector = selectorFor(el);
        if (selector) {
          const childNodes = Array.from(el.childNodes || []);
          const directText = childNodes
            .filter((node) => node.nodeType === Node.TEXT_NODE)
            .map((node) => String(node.textContent || ""))
            .join(" ")
            .replace(/\s+/g, " ")
            .trim()
            .slice(0, 120);
          const visualIdentity = stableVisualIdentity(el, cs);
          labels.push(`${selector}|${directText}|${visualIdentity}`);
          const visibleWidth = Math.max(0, Math.min(r.right, window.innerWidth) - Math.max(r.left, 0));
          const visibleHeight = Math.max(0, Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0));
          const coverage = (visibleWidth * visibleHeight) /
            Math.max(window.innerWidth * window.innerHeight, 1);
          if (visualIdentity && coverage >= COVERING_RECORD_FLOOR) {
            materialVisuals.set(selector, visualIdentity);
          }
        }
      } catch (e) {}
    }
    return { labels: Array.from(new Set(labels)).sort(), materialVisuals };
  };

  const materialVisualChanged = (before, after) => {
    if (!before) return false;
    const keys = new Set([...before.keys(), ...after.keys()]);
    for (const key of keys) {
      if ((before.get(key) || "") !== (after.get(key) || "")) return true;
    }
    return false;
  };

  const structureDelta = (before, after) => {
    if (!before) return 0;
    if (before.size === 0) return after.size === 0 ? 0 : 1;
    let changed = 0;
    for (const label of before) if (!after.has(label)) changed++;
    for (const label of after) if (!before.has(label)) changed++;
    return changed / Math.max(before.size, after.size, 1);
  };

  const cheapHash = (str) => {
    let h = 5381;
    for (let i = 0; i < str.length; i++) h = ((h << 5) + h) + str.charCodeAt(i);
    return h >>> 0;
  };

  const computeState = () => {
    const html = document.documentElement;
    const body = document.body || { className: "", outerHTML: "" };
    const survey = surveyViewportCoverage();
    const overlay = survey.overlay;
    // Only the >= COVERING_ENTER set feeds the hash, so an element the
    // certificate would count as covering always earns its own sample when it
    // enters or leaves, while share jitter below that line does not. This can
    // add a poll or two on a page with no splash (a hero fading in past the
    // line); `polls` was already "distinct composite hashes" (animation
    // counts, media readiness), and its readers - the class-hook lookup in
    // state_coverage.py and the 3x ratio on timed-out refs in
    // behavior-parity-check.sh - key off class changes and gross ratios, not
    // exact counts.
    const coveringIdentities = Object.keys(survey.covering)
      .filter((identity) => survey.covering[identity] >= COVERING_ENTER)
      .sort();
    const animations = animationEvidence();
    const media = mediaFingerprint();
    const visibleSnapshot = visibleStructureLabels();
    const visibleLabels = visibleSnapshot.labels;
    const visibleSet = new Set(visibleLabels);
    let visibleStructureChanged = false;
    if (visibleStructureBaseline === null) {
      visibleStructureBaseline = visibleSet;
    } else if (
      structureDelta(visibleStructureBaseline, visibleSet) > 0.2 ||
      materialVisualChanged(materialVisualBaseline, visibleSnapshot.materialVisuals)
    ) {
      visibleStructureChanged = true;
      visibleStructureEpoch++;
      visibleStructureBaseline = visibleSet;
    }
    materialVisualBaseline = visibleSnapshot.materialVisuals;
    const composite = [
      html.className || "",
      body.className || "",
      getComputedStyle(html).overflow,
      body.style ? getComputedStyle(body).overflow : "",
      JSON.stringify(overlay),
      coveringIdentities.join(","),
      animations.activeCount,
      animations.runningCount,
      media.hash,
      (body.outerHTML || "").length,
      fingerprintTopElements(),
    ].join("|");
    const splashComposite = [
      html.className || "",
      body.className || "",
      getComputedStyle(html).overflow,
      body.style ? getComputedStyle(body).overflow : "",
      JSON.stringify(overlay),
      coveringIdentities.join(","),
      visibleStructureEpoch,
    ].join("|");
    return {
      hash: cheapHash(composite),
      compositeDigest: composite.slice(0, 200),
      splashHash: cheapHash(splashComposite),
      splashDigest: splashComposite.slice(0, 200),
      bodyClass: body.className || "",
      htmlClass: html.className || "",
      domLength: (body.outerHTML || "").length,
      overlay,
      covering: survey.covering,
      visibleStructure: {
        hash: String(cheapHash(visibleLabels.join("|"))),
        count: visibleLabels.length,
        changed: visibleStructureChanged,
      },
      animationEvidence: animations,
      motionEvidence: {
        changed: false,
        signals: [
          overlay.visible ? "fullscreen-overlay" : "",
          animations.activeCount > 0 ? "active-animation" : "",
          media.videos.length > 0 ? "media" : "",
        ].filter(Boolean),
      },
      mediaFingerprint: media,
      fullHTML: document.documentElement.outerHTML,
    };
  };

  // Initial state @ t=0
  const initial = computeState();
  states.push({
    ts_ms: 0,
    hash: initial.hash,
    bodyClass: initial.bodyClass,
    htmlClass: initial.htmlClass,
    compositeDigest: initial.compositeDigest,
    splashHash: initial.splashHash,
    splashDigest: initial.splashDigest,
    domLength: initial.domLength,
    overlay: initial.overlay,
    covering: initial.covering,
    visibleStructure: initial.visibleStructure,
    animationEvidence: initial.animationEvidence,
    motionEvidence: initial.motionEvidence,
    mediaFingerprint: initial.mediaFingerprint,
    fullHTML: initial.fullHTML,  // always full for 0ms bookend
    bookend: "0ms",
  });
  lastHash = initial.hash;
  lastSplashHash = initial.splashHash;
  let baselineDomLength = initial.domLength;
  const initialOverlayIdentity = initial.overlay.identity || initial.overlay.selector;
  const awaitingInitialOverlayExit = Boolean(
    initial.overlay.visible && initial.overlay.coverage >= 0.75
  );
  let initialOverlayExitObserved = false;
  // A page that visibly starts behind a fullscreen overlay gets the longest
  // evidence window because real loaders are often gated on media/font
  // readiness and can cross 5s under cold-cache or network variance. Exit as
  // soon as that same overlay disappears.
  //
  // The no-overlay ceiling was 5000ms, which is shorter than a common entry
  // choreography: the loop only exits early on `stable-2s`, so a page whose
  // own entry animations run ~1.6s and whose hero video moves the media
  // fingerprint (readyState 0 -> 4) while it loads cannot go 2s without a
  // change inside 5s. It then hits the cap mid-load: `timedOut: true`,
  // `reason: wall-clock-cap`, and a settled bookend taken before the page
  // actually settled, which behavior-parity-check reads as a continuous ref
  // animation the impl lacks. 10000ms lets that choreography finish and stays
  // well under the overlay-exit window. Measured on one observed
  // site: 5000ms timed out with motionEvidence ["media",
  // "active-animation"]; 10000ms settles at ~6.3s with `stable-2s`.
  //
  // `authoritativeNegative` now rests on a separate splash settle clock. The
  // full evidence hash may keep changing for autoplay media or an infinite
  // animation and run to this ceiling; that no longer makes an otherwise
  // stable splash probe inconclusive. Overlay lifecycle, covering identities,
  // root classes, scroll lock, and material viewport-structure replacement
  // still reset the splash clock and fail closed when they do not settle.
  const captureLimitMs = awaitingInitialOverlayExit ? 15000 : 10000;

  while ((performance.now() - startedAt) < captureLimitMs) {
    await new Promise(r => requestAnimationFrame(() => setTimeout(r, 100)));
    const cur = computeState();
    const now = performance.now();
    const currentOverlayIdentity = cur.overlay.identity || cur.overlay.selector;
    const initialOverlayExited = Boolean(
      awaitingInitialOverlayExit && (
        !cur.overlay.visible || currentOverlayIdentity !== initialOverlayIdentity
      )
    );
    if (initialOverlayExited) initialOverlayExitObserved = true;
    const evidenceChanged = cur.hash !== lastHash;
    const splashChanged = cur.splashHash !== lastSplashHash;
    if (evidenceChanged || splashChanged) {
      const structuralDelta = Math.abs(cur.domLength - baselineDomLength) / Math.max(baselineDomLength, 1);
      const includeFullHTML = structuralDelta > 0.2;  // >20% delta
      states.push({
        ts_ms: Math.round(now - startedAt),
        hash: cur.hash,
        bodyClass: cur.bodyClass,
        htmlClass: cur.htmlClass,
        compositeDigest: cur.compositeDigest,
        splashHash: cur.splashHash,
        splashDigest: cur.splashDigest,
        domLength: cur.domLength,
        overlay: cur.overlay,
        covering: cur.covering,
        visibleStructure: cur.visibleStructure,
        animationEvidence: cur.animationEvidence,
        motionEvidence: {
          changed: true,
          signals: cur.motionEvidence.signals,
        },
        mediaFingerprint: cur.mediaFingerprint,
        fullHTML: includeFullHTML ? cur.fullHTML : null,
        structuralDelta: includeFullHTML,
      });
      lastHash = cur.hash;
      if (evidenceChanged) lastChangeAt = now;
      if (splashChanged) {
        lastSplashHash = cur.splashHash;
        lastSplashChangeAt = now;
        splashSettledAt = null;
      }
      if (includeFullHTML) baselineDomLength = cur.domLength;
    }
    if (
      !awaitingInitialOverlayExit && splashSettledAt === null &&
      (now - lastSplashChangeAt) >= 2000
    ) {
      splashSettledAt = now;
    }
    if (
      !awaitingInitialOverlayExit && splashSettledAt !== null &&
      (now - lastChangeAt) >= 2000
    ) {
      break;
    }
    if (initialOverlayExited) break;
  }

  // Settled state — always full
  const final = computeState();
  const finalOverlayIdentity = final.overlay.identity || final.overlay.selector;
  if (
    awaitingInitialOverlayExit &&
    (!final.overlay.visible || finalOverlayIdentity !== initialOverlayIdentity)
  ) {
    initialOverlayExitObserved = true;
  }
  if (final.splashHash !== lastSplashHash) {
    lastSplashHash = final.splashHash;
    lastSplashChangeAt = performance.now();
    splashSettledAt = null;
  }
  const finalEntry = {
    ts_ms: Math.round(performance.now() - startedAt),
    hash: final.hash,
    bodyClass: final.bodyClass,
    htmlClass: final.htmlClass,
    compositeDigest: final.compositeDigest,
    splashHash: final.splashHash,
    splashDigest: final.splashDigest,
    domLength: final.domLength,
    overlay: final.overlay,
    covering: final.covering,
    visibleStructure: final.visibleStructure,
    animationEvidence: final.animationEvidence,
    motionEvidence: {
      changed: states.length > 1,
      signals: final.motionEvidence.signals,
    },
    mediaFingerprint: final.mediaFingerprint,
    fullHTML: final.fullHTML,  // always full for settled bookend
    bookend: "settled",
  };
  // If settled state hash matches the last recorded state, tag it as the
  // settled bookend AND ensure fullHTML is present so the python writer
  // emits settled.json. The earlier transition pass may have pruned
  // fullHTML when structuralDelta was <= 20% — without this backfill the
  // settled snapshot would silently go missing for CSS-only transitions
  // (display:none swap, opacity fade, class flip without DOM growth).
  if (states[states.length - 1] && states[states.length - 1].hash === final.hash) {
    const last = states[states.length - 1];
    last.bookend = last.bookend || "settled-same";
    if (!last.fullHTML) last.fullHTML = final.fullHTML;
  } else {
    states.push(finalEntry);
  }

  const elapsed = performance.now() - startedAt;
  const splashTimedOut = awaitingInitialOverlayExit
    ? !initialOverlayExitObserved
    : splashSettledAt === null;
  return {
    states,
    durationMs: Math.round(elapsed),
    polls: states.length,
    timedOut: elapsed >= captureLimitMs,
    splashTimedOut,
    splashSettledMs: splashSettledAt === null ? null : Math.round(splashSettledAt - startedAt),
    reason: states.length <= 1 ? "no-change" :
            elapsed >= captureLimitMs ? "wall-clock-cap" :
            "stable-2s",
  };
})();'

# The covering thresholds have exactly one definition, in
# ui_clone.splash_contract, pinned there to the lifecycle probe's own numbers.
# Substitute them into the sampler so the survey, the certificate that reads it
# and the check the certificate can suppress cannot disagree about what covers
# the viewport. A missing module or a non-numeric value is a hard stop: a
# sampler running with a placeholder would throw inside the page and the
# capture would fail closed anyway, but this names the cause.
#
# The repo root is placed AHEAD of sys.path[0] (the working directory, for
# `python3 -c`) rather than appended through PYTHONPATH: a `ui_clone/` package
# in the caller's cwd - an impl tree, a scratch dir - would otherwise shadow
# the real module and hand the sampler whatever thresholds it carries.
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COVERING_THRESHOLDS="$(python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
from ui_clone.splash_contract import COVERING_ENTER, COVERING_RECORD_FLOOR
print(COVERING_ENTER, COVERING_RECORD_FLOOR)
' "$REPO_ROOT")" || {
  echo "capture-states: cannot read covering thresholds from ui_clone.splash_contract" >&2
  exit 3
}
read -r COVERING_ENTER COVERING_RECORD_FLOOR <<<"$COVERING_THRESHOLDS"
for threshold in "$COVERING_ENTER" "$COVERING_RECORD_FLOOR"; do
  if [[ ! "$threshold" =~ ^(0(\.[0-9]+)?|1(\.0+)?)$ ]]; then
    echo "capture-states: covering threshold is not a share in [0, 1]: $threshold" >&2
    exit 3
  fi
done
EVAL_JS="${EVAL_JS//__UI_CLONE_COVERING_ENTER__/$COVERING_ENTER}"
EVAL_JS="${EVAL_JS//__UI_CLONE_COVERING_RECORD_FLOOR__/$COVERING_RECORD_FLOOR}"

RUN_EVAL_JS="$EVAL_JS"

# Open page in the derived session unless reusing the caller's session. The
# default path installs the sampler before navigation so short first-load
# splashes cannot complete during a post-open settle delay.
if [ "$REUSE_SESSION" = "false" ]; then
  INIT_SCRIPT="$(mktemp -t capture-states-init.XXXX.js)"
  printf '%s\n' "window.__UI_CLONE_SPLASH_CAPTURE__ = $EVAL_JS" > "$INIT_SCRIPT"
  # A killed prior run can leave this deterministic derived session alive on
  # about:blank. Reset it before registering the first-navigation init script;
  # otherwise the stale context consumes the script contract and the target
  # eval remains attached to the blank page.
  state-agent-browser close >/dev/null 2>&1 || true
  # Materialize the persistent page before applying emulation settings. The
  # first cold command can otherwise target a transient launch page.
  if ! capture_browser_bootstrap "capture-states" state-agent-browser >/dev/null; then
    echo "capture-states: agent-browser page initialization failed (session=$STATES_SESSION)" >&2
    exit 2
  fi
  # Apply emulation after materializing the page with its final launch options.
  # Adding --init-script only at open would recreate the browser and discard
  # viewport/media; dropping it at eval can recreate the page as about:blank.
  if ! state-agent-browser set viewport 1440 900 >/dev/null; then
    echo "capture-states: agent-browser viewport failed (session=$STATES_SESSION)" >&2
    exit 2
  fi
  if ! state-agent-browser set media "$CAPTURE_COLOR_SCHEME" >/dev/null; then
    echo "capture-states: agent-browser color scheme failed (session=$STATES_SESSION)" >&2
    exit 2
  fi
  if ! OPEN_OUTPUT="$(state-agent-browser open "$URL" --json)"; then
    echo "capture-states: agent-browser open failed for $URL (session=$STATES_SESSION)" >&2
    printf '%s\n' "$OPEN_OUTPUT" >&2
    exit 2
  fi
  if ! printf '%s' "$OPEN_OUTPUT" | python3 "$ORIGIN_VALIDATOR" "$URL" --session "$STATES_SESSION" --navigation "$NAVIGATION_RECEIPT" --record; then
    exit 2
  fi
  RUN_EVAL_JS='(async () => await window.__UI_CLONE_SPLASH_CAPTURE__)()'
else
  if ! state-agent-browser set media "$CAPTURE_COLOR_SCHEME" >/dev/null; then
    echo "capture-states: agent-browser color scheme failed (session=$STATES_SESSION)" >&2
    exit 2
  fi
fi

RESPONSE_RAW="$(state-agent-browser eval --json "$RUN_EVAL_JS" 2>&1)" || {
  echo "capture-states: agent-browser eval failed (session=$STATES_SESSION)" >&2
  echo "$RESPONSE_RAW" >&2
  exit 3
}
if ! printf '%s' "$RESPONSE_RAW" | python3 "$ORIGIN_VALIDATOR" "$URL" --session "$STATES_SESSION" --navigation "$NAVIGATION_RECEIPT"; then
  echo "capture-states: agent-browser eval returned a non-page origin (session=$STATES_SESSION)" >&2
  exit 3
fi

# Validate + split into trajectory / summary / per-state files via python.
# Heredoc + stdin pipe conflict — write response to a temp file the python
# block reads via argv. Also handles multi-MB DOM blobs that would exceed
# env-var size limits.
RESPONSE_TMP="$(mktemp -t capture-states-resp.XXXX)"
printf '%s' "$RESPONSE_RAW" > "$RESPONSE_TMP"
python3 - "$OUTDIR" "$RESPONSE_TMP" "$CAPTURE_MODE" "$SCRIPT_DIR/../.." <<'PY'
import json
import sys
from pathlib import Path

outdir = Path(sys.argv[1])
raw = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")
capture_mode = sys.argv[3]

# The absence certificate has exactly one implementation, shared with every
# consumer of contract.json. A hard ImportError is deliberate: a private copy
# of the rule here is how producer and consumers drifted apart before.
sys.path.insert(0, str(Path(sys.argv[4]).resolve()))
from ui_clone.splash_contract import absence_evidence, certify_absence  # noqa: E402

# agent-browser may wrap the eval result in a JSON envelope; try both.
try:
    parsed = json.loads(raw)
except json.JSONDecodeError as e:
    print(f"capture-states: invalid JSON from agent-browser eval ({e}):\n{raw[:300]}", file=sys.stderr)
    sys.exit(3)

# Peel agent-browser eval envelope: {success, data: {origin, result: <inner>}}.
# Real `agent-browser eval --json` always wraps. Unit-test fake-browser emits
# the inner JSON bare, so this peel is a no-op there.
if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict) and "result" in parsed["data"]:
    parsed = parsed["data"]["result"]
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            pass

# Legacy single-key wrapper {"result": <inner>}. Kept so a future shim that
# pre-strips the envelope on the caller side keeps working without script edits.
if isinstance(parsed, dict) and "result" in parsed and isinstance(parsed["result"], (dict, str)):
    inner = parsed["result"]
    if isinstance(inner, str):
        try:
            parsed = json.loads(inner)
        except json.JSONDecodeError:
            pass
    else:
        parsed = inner

if not isinstance(parsed, dict) or "states" not in parsed:
    print(f"capture-states: unexpected payload shape:\n{json.dumps(parsed)[:300]}", file=sys.stderr)
    sys.exit(3)

states = parsed.get("states", [])
summary = {
    "checked": True,
    "captureMode": capture_mode,
    "durationMs": parsed.get("durationMs", 0),
    "polls": parsed.get("polls", len(states)),
    "timedOut": parsed.get("timedOut", False),
    "splashTimedOut": parsed.get("splashTimedOut", parsed.get("timedOut", False)),
    "splashSettledMs": parsed.get("splashSettledMs"),
    "reason": parsed.get("reason", "unknown"),
    "schemaVersion": 1,
}

# Trajectory entries — drop fullHTML from the trajectory.json (kept separately
# in per-state files so the trajectory stays human-readable).
trajectory = []
for s in states:
    entry = {k: v for k, v in s.items() if k != "fullHTML"}
    trajectory.append(entry)

(outdir / "trajectory.json").write_text(
    json.dumps(trajectory, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
(outdir / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

# Per-state full DOM snapshots: only for entries carrying fullHTML.
for s in states:
    html = s.get("fullHTML")
    if not html:
        continue
    ts = s.get("ts_ms", 0)
    bookend = s.get("bookend")
    if bookend == "0ms":
        filename = "0ms.json"
    elif bookend in ("settled", "settled-same"):
        filename = "settled.json"
    else:
        filename = f"{ts}ms.json"
    (outdir / filename).write_text(
        json.dumps({"ts_ms": ts, "outerHTML": html}, ensure_ascii=False),
        encoding="utf-8",
    )

def _num(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _unique(values):
    seen = set()
    out = []
    for value in values:
        if value in (None, "") or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _overlay_identity(overlay):
    if not isinstance(overlay, dict):
        return None
    return overlay.get("identity") or overlay.get("selector")


def _splash_contract(states, capture_mode, summary):
    first = states[0] if states else {}
    last = states[-1] if states else {}
    overlay_observations = [
        (index, state, state.get("overlay"))
        for index, state in enumerate(states)
        if isinstance(state.get("overlay"), dict)
    ]
    visible_observations = [
        observation
        for observation in overlay_observations
        if observation[2].get("visible") and _num(observation[2].get("coverage")) > 0
    ]
    first_visible = visible_observations[0] if visible_observations else None
    primary_identity = _overlay_identity(first_visible[2]) if first_visible else None
    primary_visible = [
        observation
        for observation in visible_observations
        if _overlay_identity(observation[2]) == primary_identity
    ]
    max_overlay = max(
        (observation[2] for observation in primary_visible),
        key=lambda overlay: _num(overlay.get("coverage")),
        default={},
    )
    exit_observation = None
    if first_visible:
        for observation in overlay_observations:
            index, _, overlay = observation
            if index <= first_visible[0]:
                continue
            if not overlay.get("visible") or _overlay_identity(overlay) != primary_identity:
                exit_observation = observation
                break
    primary_phases = {
        json.dumps(
            {
                "coverage": observation[2].get("coverage"),
                "opacity": observation[2].get("opacity"),
                "position": observation[2].get("position"),
                "zIndex": observation[2].get("zIndex"),
            },
            sort_keys=True,
        )
        for observation in primary_visible
    }
    overlay_phase_changed = len(primary_phases) > 1
    animations = [
        s.get("animationEvidence")
        for s in states
        if isinstance(s.get("animationEvidence"), dict)
    ]
    max_active_count = max((_num(a.get("activeCount")) for a in animations), default=0)
    max_running_count = max((_num(a.get("runningCount")) for a in animations), default=0)
    animation_samples = []
    for evidence in animations:
        samples = evidence.get("samples")
        if isinstance(samples, list):
            animation_samples.extend(sample for sample in samples if isinstance(sample, dict))
    media_entries = [
        s.get("mediaFingerprint")
        for s in states
        if isinstance(s.get("mediaFingerprint"), dict)
    ]
    media_hashes = _unique(m.get("hash") for m in media_entries)
    motion_signals = []
    for state in states:
        evidence = state.get("motionEvidence")
        if isinstance(evidence, dict) and isinstance(evidence.get("signals"), list):
            motion_signals.extend(str(signal) for signal in evidence["signals"] if signal)
    from_ms = first_visible[1].get("ts_ms") if first_visible else None
    to_ms = exit_observation[1].get("ts_ms") if exit_observation else None
    duration_ms = (
        int(to_ms) - int(from_ms)
        if isinstance(from_ms, int) and isinstance(to_ms, int) and to_ms >= from_ms
        else None
    )
    detected = bool(len(states) > 1 and first_visible and exit_observation)
    timed_out = bool(summary.get("splashTimedOut", summary.get("timedOut")))
    evidence_timed_out = bool(summary.get("timedOut"))
    reason = summary.get("reason")
    # "This page has no splash" is certified from what every sample recorded
    # (overlay probe, covering-element lifecycle, html/body class removal,
    # structural DOM shift) plus the run settling on its own. The rule and its
    # rationale live in ui_clone/splash_contract.py; the evidence it read is
    # stamped next to the verdict so a reader can check the verdict against it.
    # Measured on one observed site - 11-13 polled states from
    # entry animations and media readiness, classes empty throughout, DOM +2%,
    # no overlay - this certifies; the old `len(states) == 1` rule refused it.
    evidence = absence_evidence(states, capture_mode=capture_mode, timed_out=timed_out)
    authoritative_negative = certify_absence(evidence)
    return {
        "schemaVersion": 1,
        "captureMode": capture_mode,
        "detected": detected,
        "overlay": {
            "selector": max_overlay.get("selector"),
            "identity": _overlay_identity(max_overlay),
            "maxCoverage": _num(max_overlay.get("coverage")),
            "everVisible": first_visible is not None,
            "exitObserved": exit_observation is not None,
            "phaseChanged": overlay_phase_changed,
            "initial": first.get("overlay") if isinstance(first.get("overlay"), dict) else {},
            "settled": last.get("overlay") if isinstance(last.get("overlay"), dict) else {},
        },
        "capture": {
            "stateCount": len(states),
            "timedOut": timed_out,
            "evidenceTimedOut": evidence_timed_out,
            "splashSettledMs": summary.get("splashSettledMs"),
            "reason": reason,
            "absenceEvidence": evidence.to_contract(),
            "authoritativeNegative": authoritative_negative,
        },
        "activeAnimation": {
            "maxActiveCount": int(max_active_count),
            "maxRunningCount": int(max_running_count),
            "samples": animation_samples[:12],
        },
        "motionEvidence": {
            "changed": bool(detected or (first.get("hash") != last.get("hash"))),
            "signals": _unique(motion_signals),
        },
        "mediaFingerprint": {
            "hashes": media_hashes,
            "initial": first.get("mediaFingerprint") if isinstance(first.get("mediaFingerprint"), dict) else {},
            "settled": last.get("mediaFingerprint") if isinstance(last.get("mediaFingerprint"), dict) else {},
        },
        "exitTiming": {
            "fromMs": from_ms,
            "toMs": to_ms,
            "durationMs": duration_ms,
            "source": "states/splash/trajectory.json",
        },
        "bookends": [
            "states/splash/0ms.json",
            "states/splash/settled.json",
        ],
    }


(outdir / "contract.json").write_text(
    json.dumps(_splash_contract(states, capture_mode, summary), ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print(f"capture-states: wrote {len(trajectory)} transition(s) to {outdir}/", file=sys.stderr)
PY

SPEC_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/state-structure-spec.py"
if [ "${STATE_STRUCTURE_SPEC:-1}" != "0" ] && [ -f "$SPEC_PY" ]; then
  python3 "$SPEC_PY" "$REF_DIR" >/dev/null
fi
