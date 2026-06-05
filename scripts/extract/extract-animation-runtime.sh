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
