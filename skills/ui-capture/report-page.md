# Report Page — Phase R (Overlay Mode)

Generate `$OUT_DIR/report.html` — fullpage screenshot with interactive transition overlays. In standalone mode with no component, `$OUT_DIR` is `tmp/ref/capture`.

## Concept

The fullpage screenshot (`static/ref/fullpage.png`) serves as the base layer. Each detected region from `regions.json` gets an overlay pinned at its exact page coordinates (`y`, `bounds`). Overlays contain videos (mousemove, timer, scroll) or toggle-able images (hover, intersection) that play in-place, showing transitions in spatial context.

## Coordinate mapping

The fullpage screenshot is rendered at its natural pixel width (1440px as captured). `regions.json` stores absolute page coordinates:
- `y` — top offset from page top (pixels)
- `bounds.width`, `bounds.height` — element dimensions

The overlay container matches the screenshot dimensions. Each overlay is positioned with:
```css
position: absolute;
top: <y / totalHeight * 100>%;
left: <bounds.x / viewportWidth * 100>%;
width: <bounds.width / viewportWidth * 100>%;
height: <bounds.height / totalHeight * 100>%;
```

For `scroll` regions that span a Y range (`from`/`to`), use `top: <from>`, `height: <to - from>`.

`bounds.x` is always available in `regions.json` — the detection script captures `rect.left + window.scrollX` for every region type.

## report.html structure

```html
<!DOCTYPE html>
<html>
<head>
  <title>UI Capture — Site Analysis Report</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { background: #0a0a0a; color: #fff; font-family: system-ui; }

    /* Header bar */
    .report-header {
      position: sticky; top: 0; z-index: 100;
      background: rgba(10,10,10,0.95); backdrop-filter: blur(8px);
      border-bottom: 1px solid #333; padding: 12px 24px;
      display: flex; align-items: center; justify-content: space-between;
    }
    .report-header h1 { font-size: 16px; font-weight: 600; }
    .report-header .stats { font-size: 12px; color: #888; }

    /* Region index (sidebar) */
    .layout { display: flex; }
    .sidebar {
      position: sticky; top: 52px; align-self: flex-start;
      width: 280px; min-width: 280px; height: calc(100vh - 52px);
      overflow-y: auto; border-right: 1px solid #222; padding: 12px 0;
      background: #0a0a0a;
    }
    .sidebar h2 { font-size: 12px; color: #666; padding: 8px 16px; text-transform: uppercase; letter-spacing: 0.05em; }
    .region-link {
      display: flex; align-items: center; gap: 8px;
      padding: 8px 16px; cursor: pointer; font-size: 13px;
      border-left: 2px solid transparent; transition: all 0.15s;
    }
    .region-link:hover { background: #1a1a1a; }
    .region-link.active { border-left-color: #fff; background: #1a1a1a; }
    .region-link code { font-size: 11px; color: #aaa; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

    /* Trigger badges */
    .trigger-badge { padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; white-space: nowrap; }
    .trigger-badge.scroll { background: rgba(255,152,0,0.3); color: #ffb74d; }
    .trigger-badge.hover { background: rgba(33,150,243,0.3); color: #64b5f6; }
    .trigger-badge.mousemove { background: rgba(156,39,176,0.3); color: #ce93d8; }
    .trigger-badge.timer { background: rgba(76,175,80,0.3); color: #81c784; }

    /* Main canvas area */
    .canvas { flex: 1; padding: 24px; overflow-x: auto; }
    .screenshot-container {
      position: relative; display: inline-block;
      max-width: 100%;
    }
    .screenshot-container img.fullpage {
      display: block; width: 100%; height: auto;
      border: 1px solid #333; border-radius: 4px;
    }

    /* Overlays */
    .overlay {
      position: absolute; border: 2px solid transparent; border-radius: 4px;
      cursor: pointer; transition: border-color 0.2s;
      z-index: 10;
    }
    .overlay:hover, .overlay.highlight { border-color: rgba(255,255,255,0.6); }
    .overlay .overlay-label {
      position: absolute; top: -24px; left: 0;
      background: rgba(0,0,0,0.85); padding: 2px 8px; border-radius: 3px;
      font-size: 11px; white-space: nowrap; opacity: 0;
      transition: opacity 0.2s; pointer-events: none;
    }
    .overlay:hover .overlay-label, .overlay.highlight .overlay-label { opacity: 1; }

    /* Video overlays */
    .overlay video {
      width: 100%; height: 100%; object-fit: cover;
      border-radius: 2px; opacity: 0; transition: opacity 0.3s;
    }
    .overlay.playing video { opacity: 1; }

    /* Image toggle overlays (hover/intersection) */
    .overlay .toggle-img {
      width: 100%; height: 100%; object-fit: cover;
      border-radius: 2px; opacity: 0; transition: opacity 0.3s;
      position: absolute; top: 0; left: 0;
    }
    .overlay.showing-active .toggle-img { opacity: 1; }

    /* Play hint */
    .overlay .play-hint {
      position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
      width: 40px; height: 40px; border-radius: 50%;
      background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center;
      opacity: 0; transition: opacity 0.2s; pointer-events: none;
    }
    .overlay:hover .play-hint { opacity: 1; }
    .overlay.playing .play-hint { opacity: 0; }

    /* CTA */
    .cta {
      background: #1a1a2e; border: 1px solid #333; border-radius: 8px;
      padding: 16px 24px; margin: 32px 24px;
    }
    .cta code { background: #333; padding: 2px 8px; border-radius: 4px; }
  </style>
</head>
<body>

<div class="report-header">
  <h1>Site Analysis — <reference-url></h1>
  <span class="stats">N regions detected · <reference-url></span>
</div>

<div class="layout">
  <!-- Sidebar: region index -->
  <nav class="sidebar">
    <h2>Interactive Regions</h2>
    <!-- One link per region from regions.json -->
    <!-- clicking scrolls the canvas so the overlay is centered in viewport -->
    <div class="region-link" data-region="<name>">
      <span class="trigger-badge <type>"><triggerType></span>
      <code><selector></code>
    </div>
    <!-- ... repeat for each region -->
  </nav>

  <!-- Main canvas: fullpage screenshot + overlays -->
  <div class="canvas">
    <div class="screenshot-container" id="screenshot-container">
      <img class="fullpage" src="static/ref/fullpage.png" alt="Full page screenshot">

      <!-- === OVERLAY PER REGION === -->

      <!-- For mousemove / auto-timer / scroll-driven: VIDEO overlay -->
      <!-- Video file: transitions/ref/mousemove-<name>.mp4, transitions/ref/scroll-<name>.webm, etc. -->
      <div class="overlay" data-region="<name>"
           style="top: <topPct>%; left: <leftPct>%; width: <widthPct>%; height: <heightPct>%;">
        <span class="overlay-label"><name> · <triggerType></span>
        <video src="transitions/ref/<type>-<name>.webm" loop muted playsinline preload="none"></video>
        <div class="play-hint">▶</div>
      </div>

      <!-- For css-hover / js-class / intersection: IMAGE TOGGLE overlay -->
      <!-- Shows the active/after state on hover/click over the idle screenshot area -->
      <div class="overlay" data-region="<name>"
           style="top: <topPct>%; left: <leftPct>%; width: <widthPct>%; height: <heightPct>%;">
        <span class="overlay-label"><name> · <triggerType></span>
        <img class="toggle-img" src="clip/ref/<name>-active.png" alt="active state">
      </div>

    </div>

    <!-- CTA -->
    <div class="cta">
      <p>To clone this site into a React + Tailwind component:</p>
      <p><code>/ui-reverse-engineering &lt;reference-url&gt;</code></p>
    </div>
  </div>
</div>

<script>
// --- Sidebar click → scroll to overlay ---
document.querySelectorAll('.region-link').forEach(link => {
  link.addEventListener('click', () => {
    const name = link.dataset.region;
    const overlay = document.querySelector(`.overlay[data-region="${name}"]`);
    if (!overlay) return;
    overlay.scrollIntoView({ behavior: 'smooth', block: 'center' });
    // Highlight briefly
    overlay.classList.add('highlight');
    setTimeout(() => overlay.classList.remove('highlight'), 2000);
    // Update sidebar active state
    document.querySelectorAll('.region-link').forEach(l => l.classList.remove('active'));
    link.classList.add('active');
  });
});

// --- Video overlays: IntersectionObserver auto-play ---
const videoOverlays = document.querySelectorAll('.overlay video');
const videoObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    const overlay = entry.target.closest('.overlay');
    if (entry.isIntersecting) {
      entry.target.play().then(() => overlay.classList.add('playing')).catch(() => {});
    } else {
      entry.target.pause();
      overlay.classList.remove('playing');
    }
  });
}, { threshold: 0.3 });
videoOverlays.forEach(v => videoObserver.observe(v));

// --- Video overlays: click to pause/resume ---
document.querySelectorAll('.overlay video').forEach(video => {
  video.closest('.overlay').addEventListener('click', () => {
    if (video.paused) {
      video.play().then(() => video.closest('.overlay').classList.add('playing')).catch(() => {});
    } else {
      video.pause();
      video.closest('.overlay').classList.remove('playing');
    }
  });
});

// --- Image toggle overlays: hover to show active state ---
document.querySelectorAll('.overlay .toggle-img').forEach(img => {
  const overlay = img.closest('.overlay');
  overlay.addEventListener('mouseenter', () => overlay.classList.add('showing-active'));
  overlay.addEventListener('mouseleave', () => overlay.classList.remove('showing-active'));
});
</script>

</body>
</html>
```

## Overlay positioning rules

When generating report.html, compute overlay positions from `regions.json` + `page` metadata:

```
For each region:
  topPct    = (region.y / page.totalHeight) * 100
  leftPct   = (region.bounds.x / page.viewportWidth) * 100
  widthPct  = (region.bounds.width / page.viewportWidth) * 100
  heightPct = (region.bounds.height / page.totalHeight) * 100

For scroll regions with from/to:
  topPct    = (region.from / page.totalHeight) * 100
  leftPct   = (region.bounds.x / page.viewportWidth) * 100
  widthPct  = (region.bounds.width / page.viewportWidth) * 100
  heightPct = ((region.to - region.from) / page.totalHeight) * 100
```

## Overlay content by trigger type

| Trigger Type | Overlay Element | Source File |
|---|---|---|
| `scroll-driven` | `<video>` loop muted | `transitions/ref/scroll-<name>.webm` |
| `mousemove` | `<video>` loop muted | `transitions/ref/mousemove-<name>.mp4` |
| `auto-timer` | `<video>` loop muted | `transitions/ref/timer-<name>.webm` |
| `css-hover` | `<img>` toggle (active state on hover) | `clip/ref/<name>-active.png` |
| `js-class` | `<img>` toggle (active state on hover) | `clip/ref/<name>-active.png` |
| `intersection` | `<img>` toggle (after state on hover) | `clip/ref/<name>-after.png` |
