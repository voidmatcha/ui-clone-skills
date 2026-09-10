#!/usr/bin/env bash
# capture-hover.sh — Phase C hover-state snapshots
#
# Captures CSS `:hover` rule signal (declared properties from CSSOM) AND
# JS-handler hover signal (synthetic event-driven computed-style diff) in
# one in-page eval, so the impl can replicate hover transitions without
# guessing from bundle grep alone.
#
# Design: docs/multi-snapshot-capture-design.md § Phase C.
#   [1] RISKY: synthetic dispatchEvent does NOT activate CSS :hover →
#       static CSSOM extraction is the CSS signal, no runtime trigger.
#   [2] RISKY: DOM-attribute hash misses paint-only hover changes →
#       computed-style hash over a fixed property set.
#   [3] OVER-ENG: 10×10 grid sweep dropped — agent-browser has no
#       pixel-coord cursor primitive. C1 deferred to a future C3 mode.
#   [4] RISKY: descendant `:hover` parsing — `.card:hover .title` means
#       activation=".card", affected=".card .title", NOT take-first-match.
#   [5] SAFE: manifest entries carry {id, kind, file, selector, ...} not
#       a selector-keyed map (selectors are ambiguous + non-durable).
#   [6] RISKY: CSS-hover signal is labeled separately from JS-hover; the
#       script never lets one satisfy the other.
#
# Usage:
#   capture-hover.sh <url> <session> <ref_dir> [--reuse-session]
#
# By default opens its own derived session `${session}-hover`. Pass
# `--reuse-session` to use the caller's session directly.
#
# Output:
#   <ref_dir>/states/hover/elem-<id>.json    — per-candidate snapshot
#   <ref_dir>/states/hover/manifest.json     — {entries: [{id,kind,file,selector,activation,changedCount,schemaVersion}]}
#   <ref_dir>/states/hover/summary.json      — {checked, durationMs, candidatesFound, candidatesProcessed,
#                                              candidatesCappedAt, selectorsAbsentFromPage, selectorsInvalid,
#                                              candidatesWithCssRule, candidatesWithJsDiff,
#                                              candidatesWithAnySignal, schemaVersion}
#
# Exit codes:
#   0  capture completed (may be empty — no :hover rules + no JS handlers)
#   1  bad usage
#   2  agent-browser open failed
#   3  agent-browser eval returned unparseable / unexpected-shape response

set -euo pipefail

# W-4 (loop-ebpb-0): pin the light color scheme at CAPTURE time too — a
# dark-evening Phase-0 capture bakes dark styles into the ref corpus
# PERMANENTLY, and every light-pinned verify then honestly-fails against
# poisoned ground truth. Caller override intact (default only when unset).
CAPTURE_COLOR_SCHEME="${AGENT_BROWSER_COLOR_SCHEME:-light}"
HOVER_EVAL_TIMEOUT_MS="${CAPTURE_HOVER_TIMEOUT_MS:-90000}"
if ! [[ "$HOVER_EVAL_TIMEOUT_MS" =~ ^[0-9]+$ ]] || [ "$HOVER_EVAL_TIMEOUT_MS" -lt 10000 ]; then
  HOVER_EVAL_TIMEOUT_MS=90000
fi
HOVER_CANDIDATE_CAP="${CAPTURE_HOVER_CANDIDATE_CAP:-50}"
if ! [[ "$HOVER_CANDIDATE_CAP" =~ ^[0-9]+$ ]] || [ "$HOVER_CANDIDATE_CAP" -lt 1 ] || [ "$HOVER_CANDIDATE_CAP" -gt 50 ]; then
  HOVER_CANDIDATE_CAP=50
fi
# Configure the timeout before the first fresh-session command. Reused sessions
# must keep their caller's launch environment so the daemon retains the page.


if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <url> <session> <ref_dir> [--reuse-session]" >&2
  exit 1
fi

URL="$1"
SESSION="$2"
REF_DIR="$3"
REUSE_SESSION="false"
if [ "${4:-}" = "--reuse-session" ]; then
  REUSE_SESSION="true"
fi

HOVER_SESSION="${SESSION}-hover"
if [ "$REUSE_SESSION" = "true" ]; then
  HOVER_SESSION="$SESSION"
fi
if [ "$REUSE_SESSION" = "false" ] && [ -z "${AGENT_BROWSER_NAMESPACE:-}" ]; then
  CAPTURE_NAMESPACE_ID="$(printf '%s' "$SESSION" | cksum | awk '{print $1}')"
  AGENT_BROWSER_NAMESPACE="ui-clone-${CAPTURE_NAMESPACE_ID}"
  export AGENT_BROWSER_NAMESPACE
fi

OUTDIR="${REF_DIR}/${STATES_PREFIX:-states}/hover"
mkdir -p "$OUTDIR"
RESPONSE_TMP=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORIGIN_VALIDATOR="$SCRIPT_DIR/validate-agent-browser-origin.py"
NAVIGATION_RECEIPT="$REF_DIR/capture-hover-navigation.json"
if [ "$REUSE_SESSION" = "true" ]; then
  NAVIGATION_RECEIPT="$REF_DIR/capture-navigation.json"
else
  unset AGENT_BROWSER_COLOR_SCHEME
  AGENT_BROWSER_DEFAULT_TIMEOUT="$HOVER_EVAL_TIMEOUT_MS"
  export AGENT_BROWSER_DEFAULT_TIMEOUT
fi

derived_ready_wait_ms() {
  local splash_summary="${REF_DIR}/${STATES_PREFIX:-states}/splash/summary.json"
  local fallback="${CAPTURE_DERIVED_READY_WAIT_MS:-3500}"
  local buffer="${CAPTURE_DERIVED_READY_BUFFER_MS:-500}"
  python3 - "$splash_summary" "$fallback" "$buffer" <<'PY'
import json
import sys
from pathlib import Path


def as_nonnegative_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


summary_path = Path(sys.argv[1])
fallback_ms = as_nonnegative_int(sys.argv[2], 3500)
buffer_ms = as_nonnegative_int(sys.argv[3], 500)
wait_ms = fallback_ms

if summary_path.is_file():
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        summary = {}
    if summary.get("checked") is True:
        duration_ms = as_nonnegative_int(summary.get("durationMs"), 0)
        wait_ms = max(wait_ms, duration_ms + buffer_ms)

print(wait_ms)
PY
}

wait_for_derived_readiness() {
  local wait_ms
  wait_ms="$(derived_ready_wait_ms)"
  if ! agent-browser --session "$HOVER_SESSION" wait "$wait_ms" >/dev/null 2>&1; then
    echo "capture-hover: agent-browser wait failed (session=$HOVER_SESSION waitMs=$wait_ms)" >&2
    exit 2
  fi
}

cleanup() {
  rm -f "${RESPONSE_TMP:-}"
  if [ "$REUSE_SESSION" = "false" ]; then
    python3 - "$HOVER_SESSION" <<'PY_CLOSE' >/dev/null 2>&1 || true
import subprocess
import sys

try:
    subprocess.run(
        ["agent-browser", "--session", sys.argv[1], "close"],
        timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
except (OSError, subprocess.TimeoutExpired):
    pass
PY_CLOSE
  fi
}

trap cleanup EXIT

# Open page in the derived session unless reusing the caller's session.
if [ "$REUSE_SESSION" = "false" ]; then
  agent-browser --session "$HOVER_SESSION" close >/dev/null 2>&1 || true
  # Materialize the persistent page before applying emulation settings. The
  # first cold command can otherwise target a transient launch page.
  if ! agent-browser --session "$HOVER_SESSION" get url >/dev/null; then
    echo "capture-hover: agent-browser page initialization failed (session=$HOVER_SESSION)" >&2
    exit 2
  fi
  if ! agent-browser --session "$HOVER_SESSION" set viewport 1440 900 >/dev/null; then
    echo "capture-hover: agent-browser viewport failed (session=$HOVER_SESSION)" >&2
    exit 2
  fi
  if ! agent-browser --session "$HOVER_SESSION" set media "$CAPTURE_COLOR_SCHEME" >/dev/null; then
    echo "capture-hover: agent-browser color scheme failed (session=$HOVER_SESSION)" >&2
    exit 2
  fi
  if ! OPEN_OUTPUT="$(agent-browser --session "$HOVER_SESSION" open "$URL" --json)"; then
    echo "capture-hover: agent-browser open failed for $URL (session=$HOVER_SESSION)" >&2
    exit 2
  fi
  if ! printf '%s' "$OPEN_OUTPUT" | python3 "$ORIGIN_VALIDATOR" "$URL" --session "$HOVER_SESSION" --navigation "$NAVIGATION_RECEIPT" --record; then
    exit 2
  fi
  wait_for_derived_readiness
else
  if ! agent-browser --session "$HOVER_SESSION" set media "$CAPTURE_COLOR_SCHEME" >/dev/null; then
    echo "capture-hover: agent-browser color scheme failed (session=$HOVER_SESSION)" >&2
    exit 2
  fi
fi

# One in-page probe — CSS rule extraction + JS-handler probing in one
# Promise loop, started once and harvested through short eval polls. Each candidate gets a passive control interval before events,
# so timer/autoplay changes are not mislabeled as hover.
# The default top-50 budget bounds CSS and JavaScript hover candidates.
# CAPTURE_HOVER_CANDIDATE_CAP can lower the budget for diagnostic runs.
EVAL_JS='(async () => {
  const CAP = 50;
  const PROBE_TIMEOUT_MS = 90000;
  const SETTLE_MS = 200;
  const RESTORE_MS = 50;
  const startedAt = performance.now();
  const boundedFrameWait = (delayMs) => new Promise((resolve, reject) => {
    if (performance.now() - startedAt >= PROBE_TIMEOUT_MS) {
      reject(new Error("hover capture exceeded total deadline"));
      return;
    }
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      resolve();
    };
    // Background tabs may suspend requestAnimationFrame indefinitely. Keep
    // the preferred rendered-frame boundary, but guarantee forward progress.
    setTimeout(finish, delayMs + 50);
    requestAnimationFrame(() => setTimeout(finish, delayMs));
  });
  const normalizeSelector = (selector) => selector.trim()
    .replace(/\s+/g, " ")
    .replace(/\s*([>+~])\s*/g, "$1");
  const splitSelectorList = (selector) => {
    const parts = [];
    let start = 0, parenDepth = 0, bracketDepth = 0, quote = "";
    for (let i = 0; i < selector.length; i++) {
      const ch = selector[i];
      if (quote) {
        if (ch === quote && selector[i - 1] !== "\\") quote = "";
        continue;
      }
      if (ch === "\"" || ch.charCodeAt(0) === 39) { quote = ch; continue; }
      if (ch === "(") parenDepth++;
      else if (ch === ")") parenDepth = Math.max(0, parenDepth - 1);
      else if (ch === "[") bracketDepth++;
      else if (ch === "]") bracketDepth = Math.max(0, bracketDepth - 1);
      else if (ch === "," && parenDepth === 0 && bracketDepth === 0) {
        parts.push(selector.slice(start, i));
        start = i + 1;
      }
    }
    parts.push(selector.slice(start));
    return parts;
  };

  // Track real pseudo-classes separately from quoted/attribute text. Only
  // top-level predicates belong to the compound currently being normalized.
  const hoverShape = (selector) => {
    const hovers = [], boundaries = [];
    let quote = "", bracket = 0, depth = 0, hasHover = false;
    for (let i = 0; i < selector.length; i++) {
      const ch = selector[i];
      if (ch === "\\") { i++; continue; }
      if (quote) { if (ch === quote) quote = ""; continue; }
      if (ch === "\"" || ch.charCodeAt(0) === 39) { quote = ch; continue; }
      if (ch === "[") { bracket++; continue; }
      if (ch === "]") { bracket--; continue; }
      if (bracket) continue;
      if (/^:hover(?![\w-])/i.test(selector.slice(i))) {
        hasHover = true;
        if (!depth) hovers.push(i);
      }
      if (!depth && (/[\s>+~]/.test(ch)
        || /^::|^:(?:before|after|first-letter|first-line)(?![\w-])/i.test(selector.slice(i)))) {
        boundaries.push(i);
      }
      if (ch === "(") depth++;
      if (ch === ")") depth--;
    }
    return {hovers, boundaries, hasHover};
  };
  const stripTopLevelHovers = (selector) => {
    let result = selector;
    for (const index of hoverShape(selector).hovers.reverse()) {
      result = result.slice(0, index) + result.slice(index + 6);
    }
    return result;
  };
  const normalizeHoverSubject = (selector) => {
    const {hovers, boundaries} = hoverShape(selector);
    if (!hovers.length) return [];
    const end = boundaries.find(index => index > hovers[0]) ?? selector.length;
    // More than one hovered subject needs multiple pointer activations.
    if (hovers.some(index => index >= end)) return [];
    return [stripTopLevelHovers(selector.slice(0, end)) + ":hover" + selector.slice(end)];
  };

  // Recover only positive selector-list functions. Moving a hover predicate
  // outside :is/:where is valid when it belongs to the branch subject. A
  // descendant after that predicate, or :not/:has, requires different semantics
  // and stays unsupported rather than selecting an unrelated element.
  const expandHoverBranches = (selector, level = 0) => {
    if (level > 16) return [];
    let quote = "", bracketDepth = 0;
    for (let i = 0; i < selector.length; i++) {
      const ch = selector[i];
      if (ch === "\\") { i++; continue; }
      if (quote) { if (ch === quote) quote = ""; continue; }
      if (ch === "\"" || ch.charCodeAt(0) === 39) { quote = ch; continue; }
      if (ch === "[") { bracketDepth++; continue; }
      if (ch === "]") { bracketDepth--; continue; }
      if (bracketDepth) continue;
      const call = selector.slice(i).match(/^:([a-z-]+)\(/i);
      if (!call) continue;
      const open = i + call[0].length - 1;
      let end = open + 1, depth = 1, innerQuote = "", innerBracket = 0;
      for (; end < selector.length; end++) {
        const c = selector[end];
        if (c === "\\") { end++; continue; }
        if (innerQuote) { if (c === innerQuote) innerQuote = ""; continue; }
        if (c === "\"" || c.charCodeAt(0) === 39) { innerQuote = c; continue; }
        if (c === "[") { innerBracket++; continue; }
        if (c === "]") { innerBracket--; continue; }
        if (innerBracket) continue;
        if (c === "(") depth++;
        if (c === ")" && --depth === 0) break;
      }
      if (depth !== 0) return [];
      const body = selector.slice(open + 1, end);
      if (!hoverShape(body).hasHover) { i = end; continue; }
      if (!["is", "where"].includes(call[1].toLowerCase())) return [];
      // An earlier hovered subject cannot stand in for this branch subject.
      const prefix = selector.slice(0, i);
      const prefixShape = hoverShape(prefix);
      if (prefixShape.hovers.some(index => prefixShape.boundaries.some(end => end > index))) return [];
      const expanded = [];
      for (const branch of splitSelectorList(body)) {
        if (!hoverShape(branch).hasHover) continue;
        for (const recovered of expandHoverBranches(branch.trim(), level + 1)) {
          const hover = hoverShape(recovered).hovers[0];
          // A combinator after hover changes the branch subject. Do not move
          // that hover onto the final subject; report unsupported syntax.
          if (hoverShape(recovered).boundaries.some(index => index > hover)) continue;
          const subject = stripTopLevelHovers(recovered).trim() || "*";
          // Keep suffixes on the same compound (.enabled, attribute tests,
          // or non-hover pseudo-classes) in the activation selector too.
          const suffix = selector.slice(end + 1);
          let stop = 0, suffixDepth = 0, suffixBracket = 0, suffixQuote = "";
          for (; stop < suffix.length; stop++) {
            const c = suffix[stop];
            if (c === "\\") { stop++; continue; }
            if (suffixQuote) { if (c === suffixQuote) suffixQuote = ""; continue; }
            if (c === "\"" || c.charCodeAt(0) === 39) { suffixQuote = c; continue; }
            if (c === "[") { suffixBracket++; continue; }
            if (c === "]") { suffixBracket--; continue; }
            if (suffixBracket) continue;
            if (!suffixDepth && /^::|^:(?:before|after|first-letter|first-line)(?![\w-])/i.test(suffix.slice(stop))) break;
            if (c === "(") suffixDepth++;
            if (c === ")") suffixDepth--;
            if (!suffixDepth && /[\s>+~]/.test(c)) break;
          }
          // A later subject needs its own hover activation. The capture driver
          // targets one element, so do not erase that additional requirement.
          if (hoverShape(suffix.slice(stop)).hasHover) continue;
          const replacement = selector.slice(0, open + 1) + subject + ")"
            + suffix.slice(0, stop) + ":hover" + suffix.slice(stop);
          expanded.push(...expandHoverBranches(replacement, level + 1));
          if (expanded.length > 50) return [];
        }
      }
      return expanded;
    }
    return normalizeHoverSubject(selector);
  };

  // 1. Parse CSSOM for :hover rules. Skip CORS-blocked sheets silently.
  const cssHoverRules = [];
  let unsupportedHoverSelectors = 0;
  for (const sheet of document.styleSheets) {
    const sourceHref = typeof sheet.href === "string" ? sheet.href : "";
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    if (!rules) continue;
    for (const rule of rules) {
      if (!rule.selectorText || !rule.style) continue;
      const sel = rule.selectorText;
      if (!hoverShape(sel).hasHover) continue;
      // Split only top-level commas. Regex lookaheads mis-split nested
      // :is()/:where() lists and attribute values containing commas.
      const parts = splitSelectorList(sel);
      for (const part of parts) {
        if (!hoverShape(part).hasHover) continue;
        const recoveredSelectors = expandHoverBranches(part.trim());
        if (!recoveredSelectors.length) unsupportedHoverSelectors++;
        for (const trimmed of recoveredSelectors) {
          const idx = hoverShape(trimmed).hovers[0];
          const activation = normalizeSelector(trimmed.slice(0, idx));
          const affected = normalizeSelector(stripTopLevelHovers(trimmed));
          if (!activation) continue;
          const props = {};
          for (let i = 0; i < rule.style.length; i++) {
            const p = rule.style[i];
            props[p] = rule.style.getPropertyValue(p);
          }
          cssHoverRules.push({
            activation,
            affected,
            cssProperties: props,
            sourceHrefs: sourceHref ? [sourceHref] : [],
          });
        }
      }
    }
  }

  // 2. Deduplicate by activation+affected pair. Merge declared properties.
  const candidates = new Map();
  for (const r of cssHoverRules) {
    const key = r.activation + "|" + r.affected;
    if (candidates.has(key)) {
      Object.assign(candidates.get(key).cssProperties, r.cssProperties);
      for (const href of r.sourceHrefs) {
        if (!candidates.get(key).sourceHrefs.includes(href)) {
          candidates.get(key).sourceHrefs.push(href);
        }
      }
    } else {
      candidates.set(key, { activation: r.activation, affected: r.affected,
                            cssProperties: { ...r.cssProperties },
                            sourceHrefs: [...r.sourceHrefs], priority: 1000 });
    }
  }
  // CSSOM cannot enumerate pure JavaScript hover handlers. Seed a second,
  // bounded candidate pool from interactive semantics, pointer cursors, and
  // declared transitions; the runtime diff below keeps only elements whose
  // synthetic pointer/mouse events actually change style or DOM state.
  const selectorSegment = (el) => {
    const tag = el.localName || "element";
    if (el.id) return `#${CSS.escape(el.id)}`;
    const classes = Array.from(el.classList || []).filter(Boolean).slice(0, 3);
    let segment = classes.length
      ? `${tag}.${classes.map((name) => CSS.escape(name)).join(".")}`
      : tag;
    const parent = el.parentElement;
    if (parent) {
      const sameTag = Array.from(parent.children).filter((child) => child.localName === tag);
      if (sameTag.length > 1) segment += `:nth-of-type(${sameTag.indexOf(el) + 1})`;
    }
    return segment;
  };
  const runtimeSelector = (el) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const tag = el.localName || "element";
    for (const attr of ["data-testid", "data-test", "data-cy", "aria-label", "name"]) {
      const value = el.getAttribute(attr);
      if (value) {
        const candidate = `${tag}[${attr}="${String(value).replace(/\\/g, "\\\\").replace(/"/g, "\\\"")}"]`;
        try { if (document.querySelectorAll(candidate).length === 1) return candidate; }
        catch (e) {}
      }
    }
    const path = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && path.length < 6) {
      path.unshift(selectorSegment(node));
      const candidate = path.join(">");
      try { if (document.querySelectorAll(candidate).length === 1) return candidate; }
      catch (e) {}
      node = node.parentElement;
    }
    return path.join(">");
  };
  for (const el of document.querySelectorAll("*")) {
    let cs, rect;
    try {
      cs = getComputedStyle(el);
      rect = el.getBoundingClientRect();
    } catch (e) { continue; }
    if (rect.width < 20 || rect.height < 20 || cs.display === "none" || cs.visibility === "hidden") continue;
    const semantic = el.matches("a,button,input,select,textarea,[role=button],[role=link],[tabindex]");
    const pointer = cs.cursor === "pointer";
    const hasTransition = cs.transitionDuration.split(",").some((value) => parseFloat(value) > 0);
    if (!semantic && !pointer && !hasTransition) continue;
    const activation = runtimeSelector(el);
    const key = activation + "|" + activation;
    if (candidates.has(key)) continue;
    candidates.set(key, {
      activation,
      affected: activation,
      cssProperties: {},
      sourceHrefs: [],
      priority: (el.id ? 200 : 0) + (pointer ? 100 : 0) + (hasTransition ? 50 : 0) + (semantic ? 25 : 0),
    });
  }
  // Validate before applying CAP so unused global stylesheet rules cannot
  // consume the live-page capture budget. Affected selectors only need valid
  // syntax: pseudo-elements and other stateful suffixes may not match until
  // the real pointer state is active.
  let selectorsAbsentFromPage = 0;
  let selectorsInvalid = unsupportedHoverSelectors;
  const presentCandidates = [];
  for (const cand of candidates.values()) {
    let activationEl;
    try {
      activationEl = document.querySelector(cand.activation);
      document.querySelector(cand.affected);
    } catch (e) {
      selectorsInvalid++;
      continue;
    }
    if (!activationEl) {
      selectorsAbsentFromPage++;
      continue;
    }
    presentCandidates.push(cand);
  }
  const candidatesFound = presentCandidates.length;
  const orderedCandidates = presentCandidates
    .sort((a, b) => (b.priority || 0) - (a.priority || 0))
    .slice(0, CAP);

  // Fixed property set for computed-style hash.
  const TRACKED = [
    "transform", "opacity", "color", "backgroundColor",
    "width", "height", "scale", "rotate", "translate",
    "filter", "boxShadow", "borderColor", "borderRadius",
  ];

  const elSelector = (el) => {
    const id = el.id ? "#" + el.id : "";
    const cls = (el.className && typeof el.className === "string")
      ? "." + el.className.trim().split(/\s+/).slice(0, 2)
              .filter(c => c).join(".") : "";
    return el.tagName.toLowerCase() + id + cls;
  };

  const csSnapshot = (el) => {
    const cs = getComputedStyle(el);
    const out = {};
    for (const p of TRACKED) out[p] = cs[p];
    return out;
  };

  const domSnapshot = (el) => ({
    selector: elSelector(el),
    className: typeof el.className === "string" ? el.className : "",
    childElementCount: el.childElementCount,
    textHash: (() => {
      const text = (el.textContent || "").slice(0, 1000);
      let h = 5381;
      for (let i = 0; i < text.length; i++) h = ((h << 5) + h) + text.charCodeAt(i);
      return h >>> 0;
    })(),
    ariaExpanded: el.getAttribute("aria-expanded"),
    dataState: el.getAttribute("data-state"),
  });

  // 3. For each candidate, JS-side hover probe: snapshot affected scope
  //    BEFORE, dispatch hover events, settle, snapshot AFTER, diff, restore.
  const results = [];
  for (const cand of orderedCandidates) {
    let activationEl = null;
    try { activationEl = document.querySelector(cand.activation); }
    catch (e) {
      selectorsInvalid++;
      continue;
    }
    if (!activationEl) {
      selectorsAbsentFromPage++;
      continue;
    }

    const result = {
      activation: cand.activation,
      affected: cand.affected,
      activationValidated: true,
      kind: "css",
      cssProperties: cand.cssProperties,
      sourceHrefs: cand.sourceHrefs,
      jsChanges: [],
      domChanges: [],
      eventDriver: "agent-browser.eval.synthetic-hover",
    };

    // Snapshot affected scope BEFORE (cap 10 elements per candidate).
    const observed = [];
    try {
      for (const el of document.querySelectorAll(cand.affected)) {
        observed.push(el);
        if (observed.length >= 10) break;
      }
    } catch (e) {}

    const controlStart = new Map();
    const controlStartDom = new Map();
    for (const el of observed) controlStart.set(el, csSnapshot(el));
    for (const el of observed) controlStartDom.set(el, domSnapshot(el));

    // Passive A/B control: identify properties changing without any input.
    // Those signals belong to autoplay/timers/page-load, not hover.
    await boundedFrameWait(SETTLE_MS);
    const before = new Map();
    const beforeDom = new Map();
    const passiveStyleChanges = new Map();
    const passiveDomChanges = new Map();
    for (const [el, start] of controlStart) {
      if (!el.isConnected) continue;
      const current = csSnapshot(el);
      before.set(el, current);
      passiveStyleChanges.set(el, new Set(TRACKED.filter((key) => current[key] !== start[key])));
    }
    for (const [el, start] of controlStartDom) {
      if (!el.isConnected) continue;
      const current = domSnapshot(el);
      beforeDom.set(el, current);
      passiveDomChanges.set(el, new Set(
        ["className", "childElementCount", "textHash", "ariaExpanded", "dataState"]
          .filter((key) => current[key] !== start[key])
      ));
    }

    // Dispatch synthetic hover events. Catches JS-attached handlers
    // (GSAP, Framer Motion, vanilla listeners). Does NOT activate CSS
    // :hover — that signal is already captured from CSSOM above.
    const opts = { bubbles: true, cancelable: true, view: window };
    const optsNoBubble = { bubbles: false, cancelable: true, view: window };
    try {
      activationEl.dispatchEvent(new MouseEvent("mouseover", opts));
      activationEl.dispatchEvent(new MouseEvent("mouseenter", optsNoBubble));
      activationEl.dispatchEvent(new MouseEvent("mousemove", opts));
    } catch (e) {}

    await boundedFrameWait(SETTLE_MS);

    // Snapshot AFTER + diff.
    for (const [el, b] of before) {
      if (!el.isConnected) continue;
      const a = csSnapshot(el);
      const passive = passiveStyleChanges.get(el) || new Set();
      const changedProps = TRACKED.filter(k => a[k] !== b[k] && !passive.has(k));
      if (changedProps.length > 0) {
        result.jsChanges.push({
          selector: elSelector(el),
          computedStyleBefore: Object.fromEntries(changedProps.map(k => [k, b[k]])),
          computedStyleAfter: Object.fromEntries(changedProps.map(k => [k, a[k]])),
        });
      }
    }
    for (const [el, b] of beforeDom) {
      if (!el.isConnected) {
        result.domChanges.push({ selector: b.selector, disconnectedAfterHover: true });
        continue;
      }
      const a = domSnapshot(el);
      const changed = {};
      const passive = passiveDomChanges.get(el) || new Set();
      for (const key of ["className", "childElementCount", "textHash", "ariaExpanded", "dataState"]) {
        if (a[key] !== b[key] && !passive.has(key)) {
          changed[key] = { before: b[key], after: a[key] };
        }
      }
      if (Object.keys(changed).length > 0) {
        result.domChanges.push({ selector: b.selector, changes: changed });
      }
    }

    // Restore hover state.
    try {
      activationEl.dispatchEvent(new MouseEvent("mouseout", opts));
      activationEl.dispatchEvent(new MouseEvent("mouseleave", optsNoBubble));
    } catch (e) {}
    await new Promise(r => setTimeout(r, RESTORE_MS));

    // Pure runtime candidates need a causal replay. Passive media/timer state
    // can change once during the event window by coincidence; a real hover
    // response must restore on leave and reproduce on a second enter.
    if (
      Object.keys(cand.cssProperties).length === 0 &&
      (result.jsChanges.length > 0 || result.domChanges.length > 0)
    ) {
      const confirmBefore = new Map();
      const confirmBeforeDom = new Map();
      for (const el of observed) {
        if (!el.isConnected) continue;
        confirmBefore.set(el, csSnapshot(el));
        confirmBeforeDom.set(el, domSnapshot(el));
      }
      try {
        activationEl.dispatchEvent(new MouseEvent("mouseover", opts));
        activationEl.dispatchEvent(new MouseEvent("mouseenter", optsNoBubble));
        activationEl.dispatchEvent(new MouseEvent("mousemove", opts));
      } catch (e) {}
      await boundedFrameWait(SETTLE_MS);
      result.jsChanges = result.jsChanges.filter((change) => {
        const el = observed.find((candidate) => elSelector(candidate) === change.selector);
        if (!el || !el.isConnected || !confirmBefore.has(el)) return false;
        const replayAfter = csSnapshot(el);
        const replayBefore = confirmBefore.get(el);
        return Object.keys(change.computedStyleAfter).some((key) => (
          replayBefore[key] === change.computedStyleBefore[key] &&
          replayAfter[key] === change.computedStyleAfter[key] &&
          replayBefore[key] !== replayAfter[key]
        ));
      });
      result.domChanges = result.domChanges.filter((change) => {
        if (!change.changes) return false;
        const el = observed.find((candidate) => elSelector(candidate) === change.selector);
        if (!el || !el.isConnected || !confirmBeforeDom.has(el)) return false;
        const replayAfter = domSnapshot(el);
        const replayBefore = confirmBeforeDom.get(el);
        return Object.entries(change.changes).some(([key, values]) => (
          replayBefore[key] === values.before &&
          replayAfter[key] === values.after &&
          replayBefore[key] !== replayAfter[key]
        ));
      });
      try {
        activationEl.dispatchEvent(new MouseEvent("mouseout", opts));
        activationEl.dispatchEvent(new MouseEvent("mouseleave", optsNoBubble));
      } catch (e) {}
      await new Promise(r => setTimeout(r, RESTORE_MS));
    }

    if (result.jsChanges.length > 0) result.kind = "css+js";

    results.push(result);
  }

  // Summary metrics — count CSS-rule signal and JS-diff signal separately.
  let cssCount = 0, jsCount = 0, anySignal = 0;
  for (const r of results) {
    const hasCSS = r.kind === "css" || r.kind === "css+js";
    const hasJS = r.kind === "js" || r.kind === "css+js";
    if (hasCSS) cssCount++;
    if (hasJS) jsCount++;
    if (Object.keys(r.cssProperties).length > 0 || r.jsChanges.length > 0 || (r.domChanges || []).length > 0) anySignal++;
  }

  return {
    results,
    durationMs: Math.round(performance.now() - startedAt),
    candidatesFound,
    candidatesProcessed: results.length,
    candidatesCappedAt: CAP,
    selectorsAbsentFromPage,
    selectorsInvalid,
    candidatesWithCssRule: cssCount,
    candidatesWithJsDiff: jsCount,
    candidatesWithAnySignal: anySignal,
  };
})();'

# Keep the browser program static and shell-safe while allowing bounded
# diagnostic/capacity runs. Values are validated integers in [1, 50].
EVAL_JS="${EVAL_JS/const CAP = 50;/const CAP = ${HOVER_CANDIDATE_CAP};}"
EVAL_JS="${EVAL_JS/const PROBE_TIMEOUT_MS = 90000;/const PROBE_TIMEOUT_MS = ${HOVER_EVAL_TIMEOUT_MS};}"

RESPONSE_RAW="$(printf '%s' "$EVAL_JS" | python3 "$SCRIPT_DIR/browser-async-eval.py" --session "$HOVER_SESSION" --timeout-ms "$HOVER_EVAL_TIMEOUT_MS" 2>&1)" || {
  echo "capture-hover: agent-browser eval failed (session=$HOVER_SESSION)" >&2
  echo "$RESPONSE_RAW" >&2
  exit 3
}
if ! printf '%s' "$RESPONSE_RAW" | python3 "$ORIGIN_VALIDATOR" "$URL" --session "$HOVER_SESSION" --navigation "$NAVIGATION_RECEIPT"; then
  echo "capture-hover: agent-browser eval returned a non-page origin (session=$HOVER_SESSION)" >&2
  exit 3
fi

# Validate + split into manifest / summary / per-elem files via python.
RESPONSE_TMP="$(mktemp -t capture-hover-resp.XXXX)"
printf '%s' "$RESPONSE_RAW" > "$RESPONSE_TMP"
python3 - "$OUTDIR" "$RESPONSE_TMP" "$REF_DIR" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

outdir = Path(sys.argv[1])
raw = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")
ref_dir = Path(sys.argv[3])

try:
    parsed = json.loads(raw)
except json.JSONDecodeError as e:
    print(f"capture-hover: invalid JSON from agent-browser eval ({e}):\n{raw[:300]}", file=sys.stderr)
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

if not isinstance(parsed, dict) or "results" not in parsed:
    print(f"capture-hover: unexpected payload shape:\n{json.dumps(parsed)[:300]}", file=sys.stderr)
    sys.exit(3)

def normalize_selector(selector: object) -> str:
    normalized = re.sub(r"\s+", " ", str(selector or "").strip())
    return re.sub(r"\s*([>+~])\s*", r"\1", normalized)


# Keep the writer fail-closed if a stale/fake payload bypasses live discovery,
# and collapse equivalent selector spellings before creating artifacts.
deduped_results = {}
for raw_result in parsed.get("results", []):
    if raw_result.get("activationValidated") is not True:
        continue
    activation = normalize_selector(raw_result.get("activation"))
    affected = normalize_selector(raw_result.get("affected", activation))
    key = (activation, affected)
    if key not in deduped_results:
        result = dict(raw_result)
        result["activation"] = activation
        result["affected"] = affected
        result["cssProperties"] = dict(raw_result.get("cssProperties", {}) or {})
        result["sourceHrefs"] = list(raw_result.get("sourceHrefs", []) or [])
        result["jsChanges"] = list(raw_result.get("jsChanges", []) or [])
        result["domChanges"] = list(raw_result.get("domChanges", []) or [])
        deduped_results[key] = result
        continue
    result = deduped_results[key]
    result["cssProperties"].update(raw_result.get("cssProperties", {}) or {})
    for href in raw_result.get("sourceHrefs", []) or []:
        if href not in result["sourceHrefs"]:
            result["sourceHrefs"].append(href)
    result["jsChanges"].extend(raw_result.get("jsChanges", []) or [])
    result["domChanges"].extend(raw_result.get("domChanges", []) or [])

results = list(deduped_results.values())
for result in results:
    has_css = bool(result["cssProperties"])
    has_runtime = bool(result["jsChanges"] or result["domChanges"])
    result["kind"] = "css+js" if has_css and has_runtime else ("css" if has_css else "js")

# Build manifest with a stable record shape:
# {id, kind, file, selector, activation, changedCount, schemaVersion}.
manifest_entries = []
seen_ids = set()
for r in results:
    activation = r.get("activation", "")
    affected = r.get("affected", activation)
    kind = r.get("kind", "css")
    css_props = r.get("cssProperties", {}) or {}
    js_changes = r.get("jsChanges", []) or []
    dom_changes = r.get("domChanges", []) or []
    if not css_props and not js_changes and not dom_changes:
        # No CSS rule body + no JS diff → no signal worth recording.
        continue

    # Stable id: 8-char hex of "activation|affected|kind".
    raw_key = f"{activation}|{affected}|{kind}".encode("utf-8")
    entry_id = hashlib.sha256(raw_key).hexdigest()[:8]
    # Disambiguate the (rare) hash collision.
    if entry_id in seen_ids:
        suffix = 0
        while f"{entry_id}-{suffix}" in seen_ids:
            suffix += 1
        entry_id = f"{entry_id}-{suffix}"
    seen_ids.add(entry_id)

    fname = f"elem-{entry_id}.json"
    snap = {
        "id": entry_id,
        "activation": activation,
        "affected": affected,
        "kind": kind,
        "cssProperties": css_props,
        "sourceHrefs": r.get("sourceHrefs", []) or [],
        "jsChanges": js_changes,
        "domChanges": dom_changes,
        "eventDriver": r.get("eventDriver", "agent-browser.eval.synthetic-hover"),
        "schemaVersion": 1,
    }
    (outdir / fname).write_text(
        json.dumps(snap, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_entries.append({
        "id": entry_id,
        "kind": kind,
        "file": fname,
        "selector": activation,
        "activation": activation,
        "changedCount": len(js_changes) + len(dom_changes),
        "schemaVersion": 1,
    })

(outdir / "manifest.json").write_text(
    json.dumps({"entries": manifest_entries}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

summary = {
    "checked": True,
    "durationMs": parsed.get("durationMs", 0),
    "candidatesFound": parsed.get("candidatesFound", 0),
    "candidatesProcessed": len(results),
    "candidatesCappedAt": parsed.get("candidatesCappedAt", 50),
    "selectorsAbsentFromPage": parsed.get("selectorsAbsentFromPage", 0),
    "selectorsInvalid": parsed.get("selectorsInvalid", 0),
    "candidatesWithCssRule": len([
        r for r in results
        if r.get("cssProperties", {}) or {}
    ]),
    "candidatesWithJsDiff": len([
        r for r in results
        if (r.get("jsChanges", []) or [])
    ]),
    "candidatesWithDomDiff": len([
        r for r in results
        if (r.get("domChanges", []) or [])
    ]),
    "candidatesWithAnySignal": len([
        r for r in results
        if (
            (r.get("cssProperties", {}) or {})
            or (r.get("jsChanges", []) or [])
            or (r.get("domChanges", []) or [])
        )
    ]),
    "schemaVersion": 1,
}
(outdir / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

hover_css_rules = []
css_files_by_basename = {
    path.name: str(path.relative_to(ref_dir))
    for path in sorted((ref_dir / "css").glob("*.css"))
    if path.is_file()
}


def mapped_css_source(result):
    for href in result.get("sourceHrefs", []) or []:
        basename = Path(unquote(urlparse(str(href)).path)).name
        if basename in css_files_by_basename:
            return css_files_by_basename[basename]
    return None


for r in results:
    css_props = r.get("cssProperties", {}) or {}
    if not css_props:
        continue
    activation = str(r.get("activation", "") or "")
    affected = str(r.get("affected", activation) or activation)
    selector = f"{activation}:hover"
    if activation and affected.startswith(activation):
        selector = f"{activation}:hover{affected[len(activation):]}"
    declarations = "; ".join(f"{name}: {value}" for name, value in css_props.items())
    source_file = mapped_css_source(r)
    hover_rule = {
        "selector": selector,
        "activation": activation,
        "affected": affected,
        "declarations": declarations,
        "cssProperties": css_props,
        "sourceHrefs": r.get("sourceHrefs", []) or [],
        "source": "scripts/extract/capture-hover.sh:live-cssom",
    }
    if source_file:
        hover_rule["sourceFile"] = source_file
    hover_css_rules.append(hover_rule)

(ref_dir / "hover-css-rules.json").write_text(
    json.dumps({
        "schemaVersion": 1,
        "source": "scripts/extract/capture-hover.sh",
        "status": "pass",
        "observation": (
            "hover-css-rules"
            if hover_css_rules
            else "no-hover-css-rules-observed"
        ),
        "count": len(hover_css_rules),
        "rules": hover_css_rules,
        "derivedFrom": ["live-cssom", str(outdir.relative_to(ref_dir))],
    }, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print(f"capture-hover: wrote {len(manifest_entries)} entry/entries to {outdir}/",
      file=sys.stderr)
PY

if [ "${STATE_STRUCTURE_SPEC:-1}" = "0" ]; then
  exit 0
fi

SPEC_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/state-structure-spec.py"
if [ -f "$SPEC_PY" ]; then
  python3 "$SPEC_PY" "$REF_DIR" >/dev/null
fi
