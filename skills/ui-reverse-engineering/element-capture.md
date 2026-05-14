# Element-Scope Capture — Step T0

> Element-scope captures — isolated target element effects (hover, scroll-driven, page-load). For `fullpage` scope, use the main ui-capture pipeline (Phase 1+2) instead.

## Setup

```bash
mkdir -p tmp/ref/<effect-name>/frames/{ref,impl}

agent-browser --session <project> open https://target-site.com
agent-browser --session <project> set viewport 1440 900
```

## CSS hover / click effects — idle + active clip

Use `--clip` screenshots for pixel-perfect element-level comparison. Re-measure the rect after activation because `transform: scale` and geometry-changing transitions move the bounding box.

```bash
# Measure idle rect + scroll into view
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<target-selector>');
  el.scrollIntoView({ block: 'center' });
  const r = el.getBoundingClientRect();
  return JSON.stringify({ x: r.x, y: r.y, width: r.width, height: r.height });
})()"
agent-browser --session <project> wait 300

# Idle state
agent-browser --session <project> screenshot --clip <x>,<y>,<w>,<h> \
  tmp/ref/<effect-name>/frames/ref/idle.png

# Active state — CDP hover reliably triggers CSS :hover
agent-browser --session <project> hover <target-selector>
agent-browser --session <project> wait <transitionDuration + 100>

# Re-measure rect (transform may have changed bounds)
agent-browser --session <project> eval "(() => {
  const r = document.querySelector('<target-selector>').getBoundingClientRect();
  return JSON.stringify({ x: r.x, y: r.y, width: r.width, height: r.height });
})()"
agent-browser --session <project> screenshot --clip <x>,<y>,<w>,<h> \
  tmp/ref/<effect-name>/frames/ref/active.png
```

## Page-load / splash animations — video + frame extraction

```bash
agent-browser --session <project> record start tmp/ref/<effect-name>/ref.webm
agent-browser --session <project> wait 3000
agent-browser --session <project> record stop

ffmpeg -i tmp/ref/<effect-name>/ref.webm -vf fps=60 \
  tmp/ref/<effect-name>/frames/ref/frame-%04d.png -y
```

60fps extraction is mandatory — lower rates lose easing curve shape.

## Scroll-driven animations — two phases

**Phase 1: exploration video** — identifies `trigger_y` (transition starts), `mid_y` (midpoint), `settled_y` (completes).

```bash
agent-browser --session <project> eval "(() => window.scrollTo(0, 0))()"
agent-browser --session <project> wait 500
agent-browser --session <project> record start \
  tmp/ref/<effect-name>/frames/ref/scroll-explore.webm

agent-browser --session <project> eval "(() => {
  const h = document.body.scrollHeight;
  let pos = 0;
  const step = () => {
    pos += 120;
    window.scrollTo(0, pos);
    if (pos < h) setTimeout(step, 80);
  };
  step();
})()"
agent-browser --session <project> wait 4000
agent-browser --session <project> record stop
# Watch video → record trigger_y, mid_y, settled_y
```

**Phase 2: clip screenshot verification at each y.** For each position (before/mid/after) run:

```bash
agent-browser --session <project> eval "(() => window.scrollTo(0, <y>))()"
agent-browser --session <project> wait 500

# Re-measure rect — scroll transforms change element bounds
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<target-selector>');
  const r = el.getBoundingClientRect();
  return JSON.stringify({ x: r.x, y: r.y, width: r.width, height: r.height });
})()"
agent-browser --session <project> screenshot --clip <x>,<y>,<w>,<h> \
  tmp/ref/<effect-name>/frames/ref/<state>.png
```

y values:
- `before` → `trigger_y - 50`
- `mid`    → `mid_y`
- `after`  → `settled_y + 50`

Save as `before.png`, `mid.png`, `after.png`.

## Gate

`tmp/ref/<effect-name>/frames/ref/` must contain the appropriate frames for your classification before proceeding.

- CSS hover/click → `idle.png`, `active.png`
- Page-load → `frame-0001.png`, `frame-0002.png`, ... (≥10 frames)
- Scroll-driven → `before.png`, `mid.png`, `after.png`
