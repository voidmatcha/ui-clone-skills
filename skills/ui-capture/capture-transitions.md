# Transition Capture — Phase 2B–2E

Capture each region from `regions.json`. Apply trigger type to choose the correct activation method.

## Critical: `record start` behavior

**`agent-browser --session <project> record start` always creates a fresh browser context** — it navigates to the URL from scratch and resets scroll position to 0, regardless of where the current session is. This means:

- Pre-scrolling before `record start` has NO effect on the recording
- Any `eval` scroll commands issued AFTER `record start` DO work, but only appear in the recording after a delay while the page reloads (~3-5s)
- **The recording is always captured at 1280×720 regardless of `set viewport`** — this is a known bug in agent-browser ([#1031](https://github.com/vercel-labs/agent-browser/issues/1031)). Chrome is launched with `--window-size=1280,720` hardcoded, and the new recording context does not inherit the session viewport. `set viewport` still controls CSS layout (so the page renders at the correct width), but the captured frames are cropped to 1280×720. Upscale after recording as a workaround (see below).

**Correct pattern for deep-page elements:**
```bash
agent-browser --session <project> record start <path>.webm   # fresh context, page at y=0
agent-browser --session <project> set viewport 1440 900
agent-browser --session <project> wait 3000                  # wait for page to load in recording context
agent-browser --session <project> eval "(() => { document.querySelector('<selector>').scrollIntoView({block:'start'}); return window.scrollY; })()"
agent-browser --session <project> wait 1000                  # wait for scroll to settle in recording
# verify with screenshot before proceeding
agent-browser --session <project> screenshot /tmp/verify.png
# NOW the recording shows the correct position
```

**Crop to target section, not just blank removal:**

The `stdev > 8` method only removes blank frames — but for deep-page sections (scroll grid, flip cards, playground, etc.), the recording starts at y=0 (hero) which also has stdev > 8. This means hero footage appears at the start of every clip even after cropping.

**Correct approach: record a timestamp when the scroll reaches the target y, then use that as the crop point.**

```bash
# Step 1: Before scrolling, note the wall-clock offset from record start.
# record start happens at t=0; page loads in ~3s; scroll command runs after wait 3000.
# Measure actual scroll arrival time:
agent-browser --session <project> record start <path>.webm
agent-browser --session <project> set viewport 1440 900
agent-browser --session <project> wait 3000
# Save timestamp before scroll (recording elapsed ≈ 3s so far)
SCROLL_T=3.5   # conservative: 3s load + 0.5s margin
agent-browser --session <project> eval "(() => { window.scrollTo(0, <target_y>); return window.scrollY; })()"
agent-browser --session <project> wait 1000
# At this point recording is at ~4.5s — use 4.5 as crop point
```

```bash
# Step 2: Crop using the scroll arrival time as start point
# Formula: crop_t = 3.0 (load) + 0.5 (margin) + 1.0 (scroll settle) = 4.5s minimum
# For sections below y=5000 (playground, flip cards, scale), add +0.5s → 5.0s
ffmpeg -y -ss <crop_t> -i <file>.webm -c:v libx264 -preset fast -crf 23 -an <file>.mp4
```

**Crop point rules by section depth (empirically measured on 1440px viewport, Next.js SSR pages):**

| Target y | Crop point | Notes |
|----------|-----------|-------|
| y < 2000 (header, nav, hero) | 4.0s | Page loads at y=0, no scroll needed |
| y 2000–6000 (scroll grid ~y=1200, passion ~y=5000) | 5.0–10.0s | Scroll grid: 8.5s; Passion: 10.0s — wide variance, always verify |
| y 6000–10000 (evolve ~y=7500, flip cards ~y=9000) | 6.0–7.0s | Evolve: 6.0s; Flip cards: 7.0s |
| y > 10000 (playground ~y=10200, scale ~y=14000) | 5.0s | Instant scroll jump, settles fast |

> **Warning:** "scroll grid" (y~1200) takes 8.5s despite being shallow — it's a slow-scroll range covering 2400px, so the page traversal itself takes extra time. Do NOT assume shallow y = fast arrival.

**Verify the crop worked:** Always read the first frame of the output mp4:
```bash
ffmpeg -y -ss 0 -i <file>.mp4 -vframes 1 -update 1 /tmp/verify-crop.png 2>/dev/null
# Read /tmp/verify-crop.png — must show target section, NOT hero/y=0
```
If hero is still visible, increase crop_t by 1.0s and reconvert. Repeat until target section is visible.

---

## Before every recording: Fresh state protocol

**Always** ensure clean state before hitting record:
1. Start recording first: `agent-browser --session <project> record start <path>.webm`
2. Set viewport: `agent-browser --session <project> set viewport 1440 900`
3. Wait for page load: `agent-browser --session <project> wait 3000`
4. Scroll to target section via eval
5. Wait for scroll to settle: `agent-browser --session <project> wait 1000`
6. Take a screenshot to verify the recording context shows correct content
7. Proceed with interaction/sweep
8. After stopping: crop blank start with ffmpeg (see above)

---

## Step 2B: Scroll transitions

Two phases: **exploration (video)** → **verification (clip screenshot)**

### Step 2B-1: Exploration — identify transition range via video

```bash
agent-browser --session <project> record start $OUT_DIR/transitions/ref/<name>.webm
agent-browser --session <project> set viewport 1440 900
agent-browser --session <project> wait 3000

# Scroll to just above the transition range
agent-browser --session <project> eval "(() => window.scrollTo(0, <from - 300>))()"
agent-browser --session <project> wait 800

# Slow smooth scroll through the full transition range
agent-browser --session <project> eval "(() => {
  let pos = <from - 300>;
  const target = <to + 300>;
  const step = () => {
    pos += 30;          // slow: 30px per tick
    window.scrollTo(0, pos);
    if (pos < target) setTimeout(step, 50);  // ~600px/s
  };
  step();
})()"

# Wait for scroll to finish: ((to - from + 600) / 30) * 50ms
agent-browser --session <project> wait <duration_ms>
agent-browser --session <project> wait 500
agent-browser --session <project> record stop
```

**Validate:** `ffprobe -v quiet -show_entries format=duration -of csv=p=0 <file>` — must be > 2s and < 30s.

Open the video with the Read tool to confirm:
- Scroll y value where change begins (`trigger_y`)
- Scroll y value where change fully ends (`settled_y`)
- Midpoint y value (`mid_y = (trigger_y + settled_y) / 2`)

### Step 2B-2: Verification — precise comparison via clip screenshots

Capture 3 states as clip screenshots at y values identified during exploration.

```bash
# before: just before change begins
agent-browser --session <project> eval "(() => window.scrollTo(0, <trigger_y - 50>))()"
agent-browser --session <project> wait 500
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  const r = el.getBoundingClientRect();
  return JSON.stringify({ x: r.x, y: r.y, width: r.width, height: r.height });
})()"
agent-browser --session <project> screenshot --clip <x>,<y>,<w>,<h> \
  $OUT_DIR/clip/ref/<name>-before.png

# mid: midpoint of change (re-measure rect — transform may change size depending on scroll position)
agent-browser --session <project> eval "(() => window.scrollTo(0, <mid_y>))()"
agent-browser --session <project> wait 500
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  const r = el.getBoundingClientRect();
  return JSON.stringify({ x: r.x, y: r.y, width: r.width, height: r.height });
})()"
agent-browser --session <project> screenshot --clip <x>,<y>,<w>,<h> \
  $OUT_DIR/clip/ref/<name>-mid.png

# after: after change completes
agent-browser --session <project> eval "(() => window.scrollTo(0, <settled_y + 50>))()"
agent-browser --session <project> wait 500
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  const r = el.getBoundingClientRect();
  return JSON.stringify({ x: r.x, y: r.y, width: r.width, height: r.height });
})()"
agent-browser --session <project> screenshot --clip <x>,<y>,<w>,<h> \
  $OUT_DIR/clip/ref/<name>-after.png
```

> **Role of mid state:** Comparing only before/after cannot verify easing curves. Checking whether transform/opacity values are exactly 50% at mid catches easing differences like linear vs ease-in-out.

**Repeat identically for impl** (change ref → impl in paths, use the same y values).

Update that region in `regions.json` with:

```json
"artifacts": {
  "before": "clip/ref/<name>-before.png",
  "mid": "clip/ref/<name>-mid.png",
  "after": "clip/ref/<name>-after.png"
}
```

---

## Step 2C: Hover / interactive transitions

**Capture idle/active two states via eval + clip screenshot instead of video.**
Use video only when mid-transition frames matter — most hover/class/intersection comparisons need only two states.

**Choose activation method based on `triggerType`:**

### css-hover

```bash
# Confirm element position
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  el.scrollIntoView({ block: 'center' });
  const r = el.getBoundingClientRect();
  return JSON.stringify({ x: r.x, y: r.y, width: r.width, height: r.height });
})()"
agent-browser --session <project> wait 500

# Capture idle state
agent-browser --session <project> screenshot \
  --clip <x>,<y>,<width>,<height> \
  $OUT_DIR/clip/ref/<name>-idle.png

# Force hover state
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
  el.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
})()"
agent-browser --session <project> wait <transitionDuration + 100>

# Capture hover state
agent-browser --session <project> screenshot \
  --clip <x>,<y>,<width>,<height> \
  $OUT_DIR/clip/ref/<name>-active.png

# Release hover
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  el.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }));
  el.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true }));
})()"
```

> **CSS `:hover` pseudo-class may not be triggered by JS events.** In that case, use `agent-browser --session <project> hover <selector>` for CDP-level hover, then screenshot immediately:
> ```bash
> agent-browser --session <project> hover <unique-selector>
> agent-browser --session <project> wait <transitionDuration + 100>
> agent-browser --session <project> screenshot --clip <x>,<y>,<w>,<h> $OUT_DIR/clip/ref/<name>-active.png
> agent-browser --session <project> hover body
> ```

### js-class (e.g. flip card toggled by JS class)

```bash
# Confirm element position
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  el.scrollIntoView({ block: 'center' });
  const r = el.getBoundingClientRect();
  return JSON.stringify({ x: r.x, y: r.y, width: r.width, height: r.height });
})()"
agent-browser --session <project> wait 500

# Capture idle state
agent-browser --session <project> screenshot \
  --clip <x>,<y>,<width>,<height> \
  $OUT_DIR/clip/ref/<name>-idle.png

# Force active state
agent-browser --session <project> eval "(() => {
  document.querySelector('<selector>').classList.add('<triggerClass>');
})()"
agent-browser --session <project> wait <transitionDuration + 100>

# Capture active state
agent-browser --session <project> screenshot \
  --clip <x>,<y>,<width>,<height> \
  $OUT_DIR/clip/ref/<name>-active.png

# Restore original state
agent-browser --session <project> eval "(() => {
  document.querySelector('<selector>').classList.remove('<triggerClass>');
})()"
```

### intersection (scroll-triggered entry animation)

```bash
# reset: remove in-view state
agent-browser --session <project> eval "(() => {
  document.querySelectorAll('[data-in-view]').forEach(el => el.dataset.inView = 'false');
  document.querySelectorAll('.in-view, .is-visible, .animate').forEach(el => {
    el.classList.remove('in-view', 'is-visible', 'animate');
  });
})()"

# Confirm element position (after reset)
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  el.scrollIntoView({ block: 'center' });
  const r = el.getBoundingClientRect();
  return JSON.stringify({ x: r.x, y: r.y, width: r.width, height: r.height });
})()"
agent-browser --session <project> wait 300

# Capture before-animate state (without class)
agent-browser --session <project> screenshot \
  --clip <x>,<y>,<width>,<height> \
  $OUT_DIR/clip/ref/<name>-before.png

# Force in-view state
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  el.classList.add('in-view');
  el.classList.add('is-visible');
  if (el.dataset.inView !== undefined) el.dataset.inView = 'true';
})()"
agent-browser --session <project> wait <transitionDuration + 100>

# Capture after-animate state
agent-browser --session <project> screenshot \
  --clip <x>,<y>,<width>,<height> \
  $OUT_DIR/clip/ref/<name>-after.png
```

> **If IntersectionObserver adds its own class:** Check the class name first:
> ```bash
> agent-browser --session <project> eval "document.querySelector('<selector>').className"
> # Scroll to trigger in-view, then check again
> ```
> Adjust the eval above with the confirmed class name.

Update each `css-hover` / `js-class` region with `artifacts.idle` and
`artifacts.active`, and each `intersection` region with `artifacts.before` and
`artifacts.after`. Run `capture-artifact-inventory-check.sh` before handoff;
do not leave bare trigger metadata for generation to interpret.

---

## Step 2C-click: Click-Toggle / Click-Cycle Capture

For each region with `triggerType: "click-toggle"` or `"click-cycle"`.

### click-toggle (binary state: accordion, dropdown, single toggle)

```bash
# 1. Scroll element into view
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  el.scrollIntoView({ block: 'center' });
  const r = el.getBoundingClientRect();
  return JSON.stringify({ x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) });
})()"
agent-browser --session <project> wait 500

# 2. Capture idle state — clip the CONTENT area, not just the button
# The content area is the sibling/child that changes (panel, dropdown, accordion body)
agent-browser --session <project> screenshot --clip <content_x>,<content_y>,<content_w>,<content_h> \
  $OUT_DIR/transitions/ref/<name>-idle.png

# 3. Click to activate
agent-browser --session <project> click <selector>
agent-browser --session <project> wait 500

# 4. Verify state changed
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  return JSON.stringify({
    ariaExpanded: el.getAttribute('aria-expanded'),
    dataState: el.getAttribute('data-state'),
  });
})()"

# 5. Capture active state
agent-browser --session <project> screenshot --clip <content_x>,<content_y>,<content_w>,<content_h> \
  $OUT_DIR/transitions/ref/<name>-active.png

# 6. Restore: click again to toggle back (or reload if one-way)
agent-browser --session <project> click <selector>
agent-browser --session <project> wait 300
```

### click-cycle (N states, e.g., tabs)

```bash
# For each tab/pill in the group:
# Click tab 0 (should already be active, but ensure consistent state)
agent-browser --session <project> click <tab-selector-0>
agent-browser --session <project> wait 500
agent-browser --session <project> screenshot --clip <content_x>,<content_y>,<content_w>,<content_h> \
  $OUT_DIR/transitions/ref/<name>-state-0.png

# Click tab 1
agent-browser --session <project> click <tab-selector-1>
agent-browser --session <project> wait 500
agent-browser --session <project> screenshot --clip <content_x>,<content_y>,<content_w>,<content_h> \
  $OUT_DIR/transitions/ref/<name>-state-1.png

# ... repeat for all tabs

# Restore to first state
agent-browser --session <project> click <tab-selector-0>
agent-browser --session <project> wait 300
```

### Validation

- Each screenshot must be > 10KB
- idle vs active must differ (if identical, the click had no effect — remove from `regions.json`)
- For click-cycle: at least 2 states must differ from each other

### regions.json schema

**click-toggle:**
```json
{
  "triggerType": "click-toggle",
  "selector": "button[data-tab='pricing']",
  "bounds": { "x": 100, "y": 500, "w": 200, "h": 40 },
  "stateCount": 1,
  "artifacts": {
    "idle": "transitions/ref/pricing-idle.png",
    "active": "transitions/ref/pricing-active.png"
  }
}
```

**click-cycle:**
```json
{
  "triggerType": "click-cycle",
  "selector": ".tab-group",
  "bounds": { "x": 100, "y": 500, "w": 800, "h": 400 },
  "stateCount": 3,
  "artifacts": {
    "state-0": "transitions/ref/tabs-state-0.png",
    "state-1": "transitions/ref/tabs-state-1.png",
    "state-2": "transitions/ref/tabs-state-2.png"
  }
}
```

---

## Step 2C-swap: Click-Content-Swap Transition Capture

> **If clicking an element swaps page content** (changes URL via pushState, changes column count, or replaces >50% of visible images) — read `capture-click-content-swap.md` for the video + 100ms-DOM-structure capture procedure and the `content-swap-<name>-structure.json` output. Otherwise skip — toggle/cycle clicks are covered by Steps 2C-toggle / 2C-cycle above.

---

## Step 2D: Mousemove / cursor-reactive

**One video only** — no matrix screenshots. Record a continuous path that covers the whole element:

```bash
agent-browser --session <project> record start $OUT_DIR/transitions/ref/mousemove-<name>.webm
agent-browser --session <project> set viewport 1440 900
agent-browser --session <project> wait 3000

# Scroll to element — use scrollIntoView, check rect.top confirms it's in viewport
agent-browser --session <project> eval "(() => { document.querySelector('<selector>').scrollIntoView({block:'start'}); return window.scrollY; })()"
agent-browser --session <project> wait 1000

# Verify recording shows the element (not blank/wrong page)
agent-browser --session <project> screenshot /tmp/mousemove-verify.png
# Read the screenshot — if it shows the wrong content, the recording context hasn't caught up.
# In that case: agent-browser --session <project> wait 2000 and re-verify.

agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  const r = el.getBoundingClientRect();

  // 10×10 grid raster path: top-left zigzag covering entire element
  const points = [];
  for (let row = 0; row < 10; row++) {
    for (let col = 0; col < 10; col++) {
      // Alternate row direction for raster scan
      const c = row % 2 === 0 ? col : 9 - col;
      points.push({
        x: r.left + r.width * (c + 0.5) / 10,
        y: r.top + r.height * (row + 0.5) / 10
      });
    }
  }

  // Also dispatch to document in case listener is on document/window
  let i = 0;
  const step = () => {
    const p = points[i];
    const evt = new MouseEvent('mousemove', { clientX: p.x, clientY: p.y, bubbles: true });
    el.dispatchEvent(evt);
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: p.x, clientY: p.y, bubbles: true }));
    i++;
    if (i < points.length) setTimeout(step, 120);  // ~8s total for 100 points
  };
  step();
})()"

# 100 points × 120ms = ~12s
agent-browser --session <project> wait 13000
agent-browser --session <project> record stop
```

This single video shows the cursor sweeping the full element in a raster pattern — every region covered, movement visible throughout.

Update the region with:

```json
"artifacts": { "video": "transitions/ref/mousemove-<name>.webm" }
```

---

## Step 2E: Auto-timer transitions

Only record if the element visually changes on its own (no user interaction needed):

```bash
agent-browser --session <project> record start $OUT_DIR/transitions/ref/timer-<name>.webm
agent-browser --session <project> set viewport 1440 900
agent-browser --session <project> wait 3000

# Scroll to element (AFTER record start — record creates fresh context)
agent-browser --session <project> eval "(() => { document.querySelector('<selector>').scrollIntoView({block:'center'}); return window.scrollY; })()"
agent-browser --session <project> wait 1000

# Wait for 2-3 full cycles
agent-browser --session <project> wait <interval_ms * 3>
agent-browser --session <project> record stop
```

Update the region with:

```json
"artifacts": { "video": "transitions/ref/timer-<name>.webm" }
```

---

## Validation checklist after each recording

```
□ ffprobe duration > 1s
□ File size > 50KB
□ Read first frame: not blank/white
□ Duration matches expected: hover ~5s, scroll ~6s, mousemove ~13s, timer = interval×3
□ If duration is 0 or > 60s → re-record (recording got stuck or never started)
```

---

## Auto-crop: remove blank intro from every transition video

**Run this after EVERY recording.** `record start` always adds 1–4s of blank/wrong-state frames at the beginning. Crop them out before saving the final file.

### Step 1: Detect blank end point via frame file sizes

```bash
# Extract frames at 2fps, measure file sizes
# Blank frames compress tiny (<10KB); content frames are large (>50KB)
AUTO_CROP() {
  local INPUT="$1"
  local OUTPUT="$2"

  local TMPDIR=$(mktemp -d)
  ffmpeg -y -i "$INPUT" -vf "fps=2" "$TMPDIR/f%03d.png" 2>/dev/null

  # Find first frame > 10KB (= first content frame)
  local FIRST_CONTENT=0
  local IDX=0
  for f in $(ls "$TMPDIR"/f*.png | sort); do
    local SZ=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f")
    IDX=$((IDX + 1))
    if [ "$SZ" -gt 10000 ]; then
      # Convert frame index to timestamp: (IDX - 1) / 2fps
      FIRST_CONTENT=$(echo "scale=2; ($IDX - 1) / 2" | bc)
      break
    fi
  done

  rm -rf "$TMPDIR"

  # Add 0.1s margin before first content frame (never go negative)
  local CROP_T=$(echo "scale=2; $FIRST_CONTENT - 0.1" | bc)
  if (( $(echo "$CROP_T < 0" | bc -l) )); then CROP_T=0; fi

  echo "Cropping from ${CROP_T}s → $OUTPUT"
  ffmpeg -y -ss "$CROP_T" -i "$INPUT" "$OUTPUT" 2>/dev/null
}
```

### Step 2: Verify first frame shows target content

```bash
ffmpeg -y -ss 0 -i <output>.webm -vframes 1 -update 1 /tmp/verify-crop.png 2>/dev/null
# Read /tmp/verify-crop.png
# ✓ Shows the target element (button, card, text, etc.)
# ✗ Still shows blank or hero/top-of-page → increase crop by 0.5s and retry
```

### Step 3: Apply in-place (replace original)

```bash
AUTO_CROP input.webm input-cropped.webm
# Verify first frame, then replace:
mv input-cropped.webm input.webm
```

### Quick one-liner (when blank duration is known)

```bash
# If you know the blank is ~N seconds:
ffmpeg -y -ss N -i input.webm output.webm 2>/dev/null
```

### Typical blank durations by video type

| Video type | Typical blank | Reason |
|---|---|---|
| Hover (element already in view) | 1–2s | `record start` context reload |
| Scroll transition (from near y=0) | 2–3s | Context reload + scroll travel |
| Scroll transition (deep page y>5000) | 4–6s | Context reload + longer scroll travel |
| Auto-timer (carousel) | 2–3s | Context reload |
| Mousemove | 2–3s | Context reload + scroll to element |

Convert to mp4 for browser compatibility:
```bash
ffmpeg -y -i <file>.webm -c:v libx264 -preset fast -crf 23 -an <file>.mp4
```

**Workaround for 1280×720 recording bug:** If the target viewport is larger (e.g. 1440×900), upscale during conversion:
```bash
ffmpeg -y -i <file>.webm -vf scale=<target_width>:<target_height> -c:v libx264 -preset fast -crf 23 -an <file>.mp4
```
Note: this is pixel upscaling — sharpness will be slightly reduced. The correct fix requires agent-browser to call `Emulation.setDeviceMetricsOverride` on the recording context ([#1031](https://github.com/vercel-labs/agent-browser/issues/1031)).
