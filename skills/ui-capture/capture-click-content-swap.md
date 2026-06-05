# Click-Content-Swap Transition Capture — Step 2C-swap

Read this sub-doc when a click interaction **swaps page content** (e.g., masonry grid → search results, gallery → detail view). Otherwise skip — toggle/cycle clicks are handled by Steps 2C-toggle / 2C-cycle in `capture-transitions.md`.

Unlike click-toggle (show/hide a panel) or click-cycle (switch between tabs), content-swap replaces the main content area entirely with an animated transition.

**Detection signal:** Clicking an element changes the URL (pushState), changes column count, or replaces >50% of visible images.

**Safety rule:** trigger clicks with `agent-browser click <selector>`, not
`document.querySelector(...).click()`, when capturing user-observable behavior.
Use an isolated session per candidate. Skip non-HTTP schemes (`mailto:`,
`tel:`, `javascript:`, `data:`), downloads, and `_blank` targets as declared
navigation; they are not safe same-page state evidence. If a safe click
navigates away (external origin, same-origin route, or hash jump), record it as
navigation and restore with `agent-browser back`; if origin or path restore
fails, reopen the reference URL. Do not claim same-page DOM mutation for
navigation-only clicks.

## Capture sequence

```bash
# 1. Record video of the full transition
agent-browser --session <project> record start $OUT_DIR/transitions/ref/content-swap-<name>.webm
agent-browser --session <project> wait 500
agent-browser --session <project> click "<click-target>"
agent-browser --session <project> wait 5000
agent-browser --session <project> record stop

# 2. Extract transition DOM structure at 100ms after click
#    This is the CRITICAL step — determines implementation architecture
agent-browser --session <project> eval "(() => {
  window.__swapStructure = null;
  window.__captureSwapStructure = () => setTimeout(() => {
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
  return 'ready';
})()"
# Re-do from fresh state (or use a different element), but trigger the user
# action through agent-browser rather than synthetic DOM click.
agent-browser --session <project> eval "(() => { window.__captureSwapStructure?.(); return 'armed'; })()"
agent-browser --session <project> click "<click-target-2>"
agent-browser --session <project> wait 500
agent-browser --session <project> eval "(() => JSON.stringify(window.__swapStructure, null, 2))()"
agent-browser --session <project> get url
# If URL changed, run `agent-browser --session <project> back`; if still off-site, reopen the reference URL.
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
  "artifacts": {
    "video": "transitions/ref/content-swap-search.webm",
    "idle": "transitions/ref/content-swap-search-idle.png",
    "mid": "transitions/ref/content-swap-search-mid.png",
    "active": "transitions/ref/content-swap-search-active.png"
  }
}
```

---

After this step, return to `capture-transitions.md` Step 2D (mousemove / cursor-reactive).
