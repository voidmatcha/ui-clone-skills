# Comparison Page — Phase 4B

Generate `$OUT_DIR/compare.html` for side-by-side human review of original vs clone.

## Phase 4A: Pixel-Perfect Visual Gate (MANDATORY — run BEFORE generating compare.html)

> **Read and execute `visual-debug/verification.md` Phase D1 (Visual Gate) for each major section of the page.**
> Visual Gate (clip screenshot diff) is the objective pass/fail criterion. If it fails, run Phase D2 (Numerical Diagnosis) to find and fix the CSS mismatch, then re-run Phase D1.

For each section in `regions.json` plus any static-only sections (header, footer, hero):
1. Follow `visual-debug/verification.md` Phase D1 to capture clip screenshots and run pixel diff
2. Produce `$OUT_DIR/pixel-perfect-diff.json` with `"result": "pass"`

The diff JSON must be embedded in the compare.html output (see HTML structure below).

Gate before generating compare.html:
```
□ pixel-perfect-diff.json exists at $OUT_DIR/pixel-perfect-diff.json
□ All elements status = "pass" (Visual Gate criterion)
□ mismatches = 0 (Numerical Diagnosis criterion)
```

Both must pass. If Visual Gate fails or mismatches > 0 → fix CSS → re-run Phase 1 + Phase 2 → THEN generate compare.html.

---

## HTML structure

```html
<!DOCTYPE html>
<html>
<head>
  <title>UI Capture — Original vs Clone</title>
  <style>
    body { background: #0a0a0a; color: #fff; font-family: system-ui; padding: 20px; }
    .pair { display: flex; gap: 8px; margin-bottom: 32px; }
    .side { flex: 1; position: relative; }
    .side img, .side video { width: 100%; border: 1px solid #333; border-radius: 4px; }
    .tag { position: absolute; top: 8px; left: 8px; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
    .tag.original { background: rgba(0,180,0,0.8); }
    .tag.clone { background: rgba(0,100,255,0.8); }
    /* Pixel-perfect diff table */
    .diff-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 32px; }
    .diff-table th, .diff-table td { padding: 6px 12px; border: 1px solid #333; text-align: left; }
    .diff-table th { background: #1a1a1a; }
    .diff-pass td { color: #4caf50; }
    .diff-fail td { color: #f44336; background: rgba(244,67,54,0.1); }
    .diff-summary { font-size: 18px; font-weight: bold; margin-bottom: 12px; }
    .diff-summary.pass { color: #4caf50; }
    .diff-summary.fail { color: #f44336; }
  </style>
</head>
<body>
  <h1>Original vs Clone</h1>

  <h2>Pixel-Perfect Visual Gate</h2>
  <!-- Embed pixel-perfect-diff.json results as table -->
  <!-- Show: element, state (idle/active), ae, ssim, status -->
  <!-- Summary line: "N elements failed" in red if any, "All pass — pixel perfect" in green if none -->

  <button id="play-all">▶ Play All</button>

  <h2>Full Page (visual baseline)</h2>
  <!-- Side-by-side full page screenshots -->

  <h2>Scroll Transitions</h2>
  <!-- Primary comparison: paired clip screenshots (before/mid/after) — see Scroll-Driven States section below -->
  <!-- Exploration videos (optional reference): paired webm videos with sync, one per scroll region in regions.json -->

  <h2>Hover / Interactive States</h2>
  <!-- Paired screenshots (idle + active) — one pair per css-hover, js-class, intersection region -->
  <!-- Each region shows: idle side-by-side, then active side-by-side -->

  <h2>Cursor-Reactive (mousemove)</h2>
  <!-- Paired raster-path videos — one per mousemove region -->

  <h2>Auto-Timer</h2>
  <!-- Paired videos — one per timer region -->

  <script>
  // Sync paired videos
  // Rules:
  // 1. play/seek sync: when one plays or seeks, the other follows
  // 2. pause sync: only sync user-initiated pauses (not buffering/ended)
  // 3. different durations: when the shorter video ends, let the longer one finish
  // 4. busy flag: prevents recursive sync loops
  document.querySelectorAll('.pair').forEach(pair => {
    const videos = pair.querySelectorAll('video');
    if (videos.length !== 2) return;
    const [a, b] = videos;
    let busy = false;

    a.addEventListener('play',   () => { if (busy) return; busy = true; b.currentTime = a.currentTime; b.play().catch(()=>{}).finally(()=>{ busy = false; }); });
    b.addEventListener('play',   () => { if (busy) return; busy = true; a.currentTime = b.currentTime; a.play().catch(()=>{}).finally(()=>{ busy = false; }); });
    a.addEventListener('pause',  () => { if (!busy && !a.ended) b.pause(); });
    b.addEventListener('pause',  () => { if (!busy && !b.ended) a.pause(); });
    a.addEventListener('seeked', () => { if (!busy) b.currentTime = a.currentTime; });
    b.addEventListener('seeked', () => { if (!busy) a.currentTime = b.currentTime; });
    // ended: do NOT pause the other — let it finish
  });

  document.getElementById('play-all')?.addEventListener('click', () => {
    document.querySelectorAll('video').forEach(v => { v.currentTime = 0; v.play(); });
  });
  </script>
</body>
</html>
```

## Hover / Interactive States section

For each `css-hover`, `js-class` region — show idle and active states as paired screenshots. For `intersection` — show before and after states (same layout, different file names):

```html
<!-- idle state -->
<h3><name> — idle</h3>
<div class="pair">
  <div class="side">
    <span class="tag original">Original</span>
    <img src="clip/ref/<name>-idle.png">
  </div>
  <div class="side">
    <span class="tag clone">Clone</span>
    <img src="clip/impl/<name>-idle.png">
  </div>
</div>

<!-- active state -->
<h3><name> — active</h3>
<div class="pair">
  <div class="side">
    <span class="tag original">Original</span>
    <img src="clip/ref/<name>-active.png">
  </div>
  <div class="side">
    <span class="tag clone">Clone</span>
    <img src="clip/impl/<name>-active.png">
  </div>
</div>
```

> Both idle and active must pass the Visual Gate (clip screenshot diff). It's common for idle to pass while active fails — hover color, transform, shadow errors.

## Scroll-Driven States section

For each `scroll-driven` region — show before / mid / after states as paired screenshots (from Phase 2B-2 clip verification):

```html
<!-- before state (above trigger point) -->
<h3><name> — before</h3>
<div class="pair">
  <div class="side">
    <span class="tag original">Original</span>
    <img src="clip/ref/<name>-before.png">
  </div>
  <div class="side">
    <span class="tag clone">Clone</span>
    <img src="clip/impl/<name>-before.png">
  </div>
</div>

<!-- mid state (midpoint of transition) -->
<h3><name> — mid</h3>
<div class="pair">
  <div class="side">
    <span class="tag original">Original</span>
    <img src="clip/ref/<name>-mid.png">
  </div>
  <div class="side">
    <span class="tag clone">Clone</span>
    <img src="clip/impl/<name>-mid.png">
  </div>
</div>

<!-- after state (settled) -->
<h3><name> — after</h3>
<div class="pair">
  <div class="side">
    <span class="tag original">Original</span>
    <img src="clip/ref/<name>-after.png">
  </div>
  <div class="side">
    <span class="tag clone">Clone</span>
    <img src="clip/impl/<name>-after.png">
  </div>
</div>
```

> `mid` state is the easing-curve canary: if before/after both pass but mid fails, the easing function is wrong (e.g., `linear` vs `ease-in-out`). The exploration video (Phase 2B-1) is what tells you the correct `mid_y`.

---

## Cursor-reactive (mousemove) section

For each cursor-reactive element, two raster-path videos side by side (same as other paired sections):

```html
<div class="pair">
  <div class="side">
    <span class="tag original">Original</span>
    <video src="transitions/ref/mousemove-<name>.mp4" controls loop></video>
  </div>
  <div class="side">
    <span class="tag clone">Clone</span>
    <video src="transitions/impl/mousemove-<name>.mp4" controls loop></video>
  </div>
</div>
```

The raster-path video sweeps all 100 grid points in sequence — no separate PNG grid needed. The video shows movement across the full element, making parallax/tilt response visible throughout.

## Serve

```bash
# Copy to project public dir if available
cp -r "$OUT_DIR" public/ui-capture-compare

# Or standalone
npx serve "$OUT_DIR" -p 3002
```

Present URL to user and wait for feedback (interactive mode) or check pixel-perfect-diff.json (autonomous mode). See SKILL.md Phase 5.

---

## Report Mode (standalone — no local-url)

> **Read `report-page.md` before executing this phase.**

When no implementation exists yet, generate `$OUT_DIR/report.html` instead of `compare.html`. In standalone mode with no component, `$OUT_DIR` is `tmp/ref/capture`. Shows the fullpage screenshot with interactive transition overlays pinned at exact page coordinates.

- User invokes `ui-capture <url>` without a local-url (Claude slash command: `/ui-capture <url>`) → generate `report.html`
- User invokes `ui-capture <url> vs <local-url>` (Claude slash command: `/ui-capture <url> vs <local-url>`) → generate `compare.html` (default comparison mode)
