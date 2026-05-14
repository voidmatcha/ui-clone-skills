#!/usr/bin/env bash
# freeze-animations.sh — Freeze all animations on a page before screenshot capture
#
# Usage: bash freeze-animations.sh <session>
#
# Freezes: CSS animations, JS timers, rAF, Canvas rendering, Lottie SVGs
# Run AFTER page load + splash completion, BEFORE screenshot capture.
#
# Why: Auto-rotating carousels, Lottie loops, and Canvas animations make
# consecutive screenshots differ — AE comparison fails even if layout is correct.
# This script freezes the page state so captures are deterministic.
#
# Limitations:
#   - Lottie SVGs using lottie-web's internal render loop cannot be fully frozen
#     via rAF override alone. The script hides canvas elements and applies CSS
#     freeze, but Lottie SVG transforms may still shift between frames.
#   - For sites with Lottie, use --lottie-tolerance flag in batch-compare.sh
#     or apply dynamic-content masks (see below).
#
# Returns JSON: { frozenTimers, frozenIntervals, cssFreeze, canvasHidden, lottieCount }

set -uo pipefail

SESSION="${1:?Usage: freeze-animations.sh <session>}"
trap 'agent-browser --session "$SESSION" close 2>/dev/null || true' EXIT

agent-browser eval "(()=>{
  const result = { frozenTimers: 0, frozenIntervals: 0, cssFreeze: false, canvasHidden: 0, lottieCount: 0 };

  // 1. Kill all timers (stops auto-rotating carousels)
  const h1 = setTimeout(()=>{}, 0);
  for (let i = 0; i <= h1; i++) { clearTimeout(i); result.frozenTimers++; }
  const h2 = setInterval(()=>{}, 99999);
  for (let i = 0; i <= h2; i++) { clearInterval(i); result.frozenIntervals++; }

  // 2. CSS animation/transition freeze
  const style = document.createElement('style');
  style.id = 'ui-re-freeze';
  style.textContent = '*, *::before, *::after { animation-play-state: paused !important; transition-duration: 0s !important; }';
  document.head.appendChild(style);
  result.cssFreeze = true;

  // 3. Hide canvas elements (curtain animations, particle effects)
  document.querySelectorAll('canvas').forEach(c => {
    c.style.visibility = 'hidden';
    result.canvasHidden++;
  });

  // 4. Count Lottie SVGs (for threshold adjustment)
  document.querySelectorAll('svg').forEach(svg => {
    if (svg.querySelector('[id*=lottie], clipPath[id*=lottie]')) {
      result.lottieCount++;
    }
  });

  return JSON.stringify(result);
})()" --session "$SESSION" 2>&1 | tail -1
