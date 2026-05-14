#!/usr/bin/env bash
# extract-animation-runtime.sh — Dump runtime-only animation parameters.
#
# Bundle-grep (Step 4) catches *literal* values present in source — durations,
# numeric ease coefficients, string ease names. It misses anything computed at
# runtime: ScrollTrigger.start expressions like "top 80%" resolved to pixel
# offsets, custom cubic-bezier functions defined as arrow bodies, Webflow IX2
# timeline IDs only known after the runtime mounts, Lenis instance config
# composed by user code.
#
# This script runs ONCE against the live ref page and dumps whatever animation
# runtimes are present into a single JSON sidecar. The spec gate should consult
# it when authoring transition-spec.json so easing/threshold values aren't
# silently lost between extraction and generation.
#
# Usage:
#   bash extract-animation-runtime.sh <session> <output-dir>
#
# Output: <output-dir>/animation-runtime-dump.json
#         { gsap:{...}, scrollTrigger:[...], webAnimations:[...],
#           lenis:{...}, ix2:{...}, generatedAt:"<ISO8601>" }
#
# Missing-runtime fields are emitted as null (not omitted) so downstream code
# can do a single shape check.

set -euo pipefail

SESSION="${1:?Usage: extract-animation-runtime.sh <session> <output-dir>}"
DIR="${2:?Usage: extract-animation-runtime.sh <session> <output-dir>}"

if ! command -v agent-browser >/dev/null 2>&1; then
  echo "ERROR: agent-browser CLI not on PATH" >&2
  exit 2
fi

mkdir -p "$DIR"

OUT="$DIR/animation-runtime-dump.json"

# The eval IIFE must be defensive: ScrollTrigger / Lenis / IX2 may be absent.
# Each branch returns null when the runtime isn't there; null in JSON means
# "we looked, it wasn't running" — distinguishable from "we didn't look".
#
# Scroll-walk: ScrollTrigger entries for below-fold sections are registered
# LAZY (when the section actually mounts during scroll). A single dump at
# page-load default scroll misses them. We walk N scroll fractions, capture
# at each, dedupe by trigger key, and merge — same idea as section sweep but
# for animation runtime state.
#
# Token discipline: serialize INSIDE the page so the agent-browser bridge
# returns a compact JSON string instead of a giant object graph.
#
# String-quoting discipline: this heredoc body must contain NO ASCII single
# quotes. Bash parses the body for matching apostrophes even when the
# heredoc uses a quoted delimiter (<<"JS") inside a $(...) command
# substitution — quirk we already hit once. Use double quotes for JS strings
# and template literals where needed.
RESULT=$(agent-browser --session "$SESSION" eval "$(cat <<'JS'
(async () => {
  const safe = (fn) => { try { return fn(); } catch (_e) { return null; } };

  // ── Helpers: capture at current scroll position ──
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
        return {
          duration: a.duration?.() ?? null,
          ease: a.vars?.ease?.toString?.() ?? null,
          targets: (a.targets?.() || []).slice(0, 5).map(el =>
            el?.tagName?.toLowerCase() + (el?.id ? "#" + el.id : "")
          ),
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

  // ── Scroll walk: visit N fractions, accumulate uniques ──
  const positions = [0, 0.25, 0.5, 0.75, 1.0];
  const stMap = new Map();
  const waMap = new Map();
  let stEverPresent = false;
  let waEverPresent = false;
  const origScroll = window.scrollY;

  for (const pos of positions) {
    const max = Math.max(document.documentElement.scrollHeight - window.innerHeight, 0);
    window.scrollTo({ top: pos * max, behavior: "instant" });
    await new Promise(r => setTimeout(r, 250));
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
  }

  // Restore original scroll so downstream operations are not stuck at bottom.
  window.scrollTo({ top: origScroll, behavior: "instant" });

  const scrollTrigger = stEverPresent ? [...stMap.values()].slice(0, 50) : null;
  const webAnimations = waEverPresent ? [...waMap.values()].slice(0, 50) : null;

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

  return JSON.stringify({
    gsap,
    scrollTrigger,
    webAnimations,
    lenis,
    ix2,
    scrolledPositions: positions,
    generatedAt: new Date().toISOString(),
  });
})()
JS
)" 2>/dev/null || echo "")

if [ -z "$RESULT" ]; then
  echo "WARN: agent-browser eval returned empty; writing minimal dump" >&2
  printf '%s\n' '{"gsap":null,"scrollTrigger":null,"webAnimations":null,"lenis":null,"ix2":null,"generatedAt":null,"note":"eval returned empty"}' > "$OUT"
  exit 0
fi

# Validate JSON before writing. The eval returns a JSON STRING literal (the
# IIFE called JSON.stringify), so the raw response is `"{...}"`. python -m
# json.tool parses the outer string, and we then re-emit just the inner
# object so the artifact is the dict, not a quoted string.
printf '%s' "$RESULT" | python3 -c "
import json, sys
raw = sys.stdin.read().strip()
try:
    payload = json.loads(raw)
    if isinstance(payload, str):
        # Double-encoded: agent-browser wrapped our stringify result.
        payload = json.loads(payload)
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
except Exception as e:
    sys.stderr.write(f'extract-animation-runtime: JSON parse failed: {e}\n')
    sys.exit(2)
" > "$OUT"

echo "Wrote $OUT"
exit 0
