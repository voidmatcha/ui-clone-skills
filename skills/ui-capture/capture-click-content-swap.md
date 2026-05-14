# Click-Content-Swap Transition Capture — Step 2C-swap

Read this sub-doc when a click interaction **swaps page content** (e.g., masonry grid → search results, gallery → detail view). Otherwise skip — toggle/cycle clicks are handled by Steps 2C-toggle / 2C-cycle in `capture-transitions.md`.

Unlike click-toggle (show/hide a panel) or click-cycle (switch between tabs), content-swap replaces the main content area entirely with an animated transition.

**Detection signal:** Clicking an element changes the URL (pushState), changes column count, or replaces >50% of visible images.

## Capture sequence

```bash
# 1. Record video of the full transition
agent-browser --session <project> record start $OUT_DIR/transitions/ref/content-swap-<name>.webm
agent-browser --session <project> wait 500
agent-browser --session <project> eval "(() => {
  document.querySelector('<click-target>').click();
  return 'clicked';
})()"
agent-browser --session <project> wait 5000
agent-browser --session <project> record stop

# 2. Extract transition DOM structure at 100ms after click
#    This is the CRITICAL step — determines implementation architecture
agent-browser --session <project> eval "(() => {
  // Re-do: click again from fresh state (or use a different element)
  // Set up structure capture BEFORE clicking
  window.__swapStructure = null;
  document.querySelector('<click-target-2>').click();
  setTimeout(() => {
    const panes = document.querySelectorAll('[class*=pane]');
    window.__swapStructure = {
      paneCount: panes.length,
      panes: Array.from(panes).map((p, i) => {
        const cs = getComputedStyle(p);
        return {
          domIndex: i,
          classes: p.className,
          zIndex: cs.zIndex,
          position: cs.position,
          background: cs.backgroundColor,
          animationName: cs.animationName,
          animationDuration: cs.animationDuration,
          animationDelay: cs.animationDelay,
          childImages: p.querySelectorAll('img').length,
        };
      }),
    };
  }, 100);
  return 'capturing';
})()"
agent-browser --session <project> wait 500
agent-browser --session <project> eval "(() => JSON.stringify(window.__swapStructure, null, 2))()"
```

**Save to:** `$OUT_DIR/transitions/ref/content-swap-<name>-structure.json`

## Why this matters

Without this data, you will guess the pane architecture. Every guess leads to one of these failures:
- Old pane on top → fadeout applied to new content too
- New pane on top without transparent bg → old pane's fadegray invisible
- Same images in both panes → color returns during fadeout
- White flash between states → old pane removed before images load

## regions.json schema

```json
{
  "triggerType": "click-content-swap",
  "selector": "[class*=image_container]",
  "bounds": { "x": 0, "y": 0, "w": 1440, "h": 900 },
  "structure": "content-swap-search-structure.json",
  "captures": {
    "video": "transitions/ref/content-swap-search.webm",
    "idle": "transitions/ref/content-swap-search-idle.png",
    "mid-transition": "transitions/ref/content-swap-search-mid.png",
    "active": "transitions/ref/content-swap-search-active.png"
  }
}
```

---

After this step, return to `capture-transitions.md` Step 2D (mousemove / cursor-reactive).
