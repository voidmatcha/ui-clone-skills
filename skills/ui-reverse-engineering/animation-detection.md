# Animation Detection — Step 6

Detect ALL motion on the page: splash/intro, auto-timers, scroll-driven, parallax. Uses **high-framerate video** + **per-element tracking**.

> Runs AFTER `interaction-detection.md` (Step 5) and supplements it.
> Step 5 catches hover/click/intersection transitions. Step 6 catches everything that MOVES — with or without input.
> **If Canvas/Lottie/video/auto-timer detected:** read `dynamic-content-protocol.md` for non-deterministic capture handling.

## 4-Phase strategy

| Phase | Input | Detects |
|---|---|---|
| **0 — Runtime dump** | One-shot `eval` against the live page. No video. | Resolved ScrollTrigger pixel offsets, GSAP tween ease functions, `document.getAnimations()` timings, Lenis options, Webflow IX2 timeline IDs |
| **A — Idle capture** | 8–10s video at page load. No scroll, no mouse. | Splash/intro, auto-timers, CSS animations, video autoplay |
| **B — Scroll capture** | Full scroll video at 60fps, consistent speed. | Parallax, scale transitions, sticky, clip-path reveals, opacity fades, position changes |
| **C — Per-element tracking** | Targeted video per section/element from A or B. | Exact transform/opacity/scale at each scroll % |

All 4 phases are MANDATORY. Phase 0 is cheap (one eval) and recovers runtime-only params that bundle-grep (Step 4) and video phases never see — `"top 80%"` → resolved pixel start, custom cubic-bezier arrow-function ease, IX2 timeline keys.

## Phase 0 — Runtime instrumentation (zero-video)

**Purpose:** capture animation parameters that exist ONLY at runtime — never present as literals in the bundle and not visually recoverable from frames. Examples: ScrollTrigger `start: "top 80%"` resolved to a pixel offset, an `ease` defined as `t => t*t*(3-2*t)`, a Lenis `easing` function composed by user code, Webflow IX2 timeline IDs minted post-mount.

```bash
PLUGIN_ROOT="${PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${UI_CLONE_ROOT:-}}}}"
bash "$PLUGIN_ROOT/scripts/extract/extract-animation-runtime.sh" <session> tmp/ref/<component>
```

Writes `tmp/ref/<component>/animation-runtime-dump.json`:

```json
{
  "gsap": { "version": "3.12.5", "ticker": "lagSmoothing-on" },
  "scrollTrigger": [
    { "start": 1200, "end": 2400, "scrub": 1, "pin": true,
      "trigger": "section#hero.full-bleed",
      "tween": { "duration": 1.2, "ease": "power2.out", "targets": ["h1#title"] } }
  ],
  "webAnimations": [
    { "id": null, "playState": "running", "duration": 800,
      "delay": 0, "easing": "cubic-bezier(0.4, 0, 0.2, 1)", "target": "div#logo" }
  ],
  "lenis": { "duration": 1.2, "easing": "t => Math.min(1, 1.001 - Math.pow(2, -10 * t))", "smoothWheel": true },
  "ix2": { "timelineCount": 12, "timelineKeys": ["e-1","e-2"], "eventCount": 24 },
  "generatedAt": "2026-05-14T20:00:00.000Z"
}
```

Missing runtimes are emitted as `null` (NOT omitted) so downstream code can do a single shape check. Consult this file when authoring `transition-spec.json` (`transition-spec-rules.md`) — easing/threshold values that bundle-grep misses live here.

## Phase A — Idle capture (splash + auto-timers)

**Purpose:** find everything that moves WITHOUT user interaction.

**The #1 failure mode:** waiting for the page to load before starting recording. By then the splash has already played. **Recording must start DURING page load.**

### Capture protocol — execute as ONE sequential block

```bash
# 1. Close session (clean slate)
agent-browser --session <project> close

# 2. Open page — triggers splash/intro
agent-browser --session <project> open <url>
agent-browser --session <project> set viewport 1440 900

# 3. START RECORDING IMMEDIATELY — before any wait
agent-browser --session <project> record start tmp/ref/<component>/idle-capture.webm

# 4. Wait 10s — capture full splash + settle
agent-browser --session <project> wait 10000

# 5. Stop
agent-browser --session <project> record stop

# 6. Extract frames
mkdir -p tmp/ref/<component>/idle-frames
ffmpeg -i tmp/ref/<component>/idle-capture.webm -vf fps=10 tmp/ref/<component>/idle-frames/frame-%04d.png -y

# 7. CHECKPOINT — video > 50KB, frames > 50
ls -la tmp/ref/<component>/idle-capture.webm
ls tmp/ref/<component>/idle-frames/ | wc -l
```

Cookie banners can be dismissed AFTER recording — they don't interfere with splash detection since they overlay on top.

### Analyze idle frames — 3-tier approach

> **Do NOT Read every frame with the LLM.** 104 frames × ~2500 tokens = 260K tokens wasted. Use automation first; LLM only for what automation can't classify.

#### Tier 1 — AE diff: WHEN changes happen (zero tokens)

```bash
cd tmp/ref/<component>/idle-frames
echo "frame,ae_diff" > ../idle-frame-diffs.csv
PREV=""
for f in $(ls frame-*.png | sort); do
  if [ -n "$PREV" ]; then
    AE=$(compare -metric AE "$PREV" "$f" /dev/null 2>&1 | awk '{print $1}')
    echo "$f,$AE" >> ../idle-frame-diffs.csv
  fi
  PREV="$f"
done

# Find significant changes (AE > 5000 = visual change, not just noise)
awk -F',' '$2 > 5000 { print $1 " — AE=" $2 }' ../idle-frame-diffs.csv
```

Tells you exact frame numbers of transitions — zero image reading.

#### Tier 2 — DOM polling at 60fps: WHAT changed (zero tokens)

> **Use `requestAnimationFrame`, NOT `setInterval`.** 200ms polling (5fps) misses most CSS transition frames. A 0.8s ease-out has ~48 values at 60fps but only ~4 at 200ms — you lose easing curve shape entirely. 5fps tells you THAT something changed, not HOW.

**If Tier 1 shows AE spikes in the first 1–3 seconds, a splash exists.** Splash is what you most need 60fps values for, and it's exactly what plain `eval` misses (capture attaches after the splash already fired).

> **→ Read `splash-extraction.md`** for the throttled splash capture protocol, video↔bundle cross-reference, GSAP timeline parsing, conditional branch detection, fixed overlay cleanup, and splash end-state verification.

**Standard Tier 2 (no splash, or post-splash):**

```bash
agent-browser --session <project> close
agent-browser --session <project> open <url>
agent-browser --session <project> set viewport 1440 900

# Inject 60fps rAF capture immediately
agent-browser --session <project> eval "(() => {
  window.__frames = [];
  const start = performance.now();
  const targets = [
    '[class*=load]', '[class*=intro]', '[class*=splash]', '[class*=overlay]',
    '[class*=logo]', '[class*=text]', '[class*=image]', '[class*=bg]',
    'section', 'header', 'nav', 'main'
  ];
  const capture = () => {
    const t = performance.now() - start;
    if (t > 8000) return;
    const els = [];
    for (const sel of targets) {
      document.querySelectorAll(sel).forEach(el => {
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        if (r.width < 10 && r.height < 10) return;
        els.push({
          sel: (el.className?.toString?.() || el.tagName).slice(0, 40),
          op: +s.opacity,
          tf: s.transform !== 'none' ? s.transform.slice(0, 50) : null,
          cp: s.clipPath !== 'none' ? s.clipPath.slice(0, 50) : null,
          y: Math.round(r.top), h: Math.round(r.height),
        });
      });
    }
    window.__frames.push({ t: Math.round(t), els });
    requestAnimationFrame(capture);
  };
  requestAnimationFrame(capture);
  return 'capturing 60fps for 8s...';
})()"

agent-browser --session <project> wait 9000
agent-browser --session <project> eval "(() => JSON.stringify(window.__frames || []))()"
```

Save to `tmp/ref/<component>/idle-dom-60fps.json`.

**Why 60fps matters:**

| Property | 200ms (5fps) | 16ms (60fps) |
|---|---|---|
| clipPath direction | `inset(0% 0% 0%)` → `inset(0% 0% 3.7%)` — can't tell axis | `inset(0% 0% 0.03%)` → `inset(0% 0% 0.06%)` — clearly 3rd value (bottom) |
| easing curve | 4 samples — looks linear | 48 samples — distinguishes ease-in / ease-out / power5 |
| transform axis | `scale(1)` → `scale(0.5)` — no intermediate trajectory | `scale(0.85)` → `scale(0.63)` shows deceleration curve |
| simultaneous props | "Both changed between frames" | opacity + transform both at t=1260 → confirms coupling |

#### Tier 3 — LLM Read: transition boundaries only (minimal tokens)

After Tier 1+2 you know WHEN and WHAT. Read images ONLY when:

1. Large AE spike but Tier 2 DOM log shows no property changes → visual change is from image content, not CSS. Read 1 frame to identify.
2. Classify visual state at a transition boundary (e.g., "is splash fully gone by frame 35?"). Read that 1 frame.
3. Final sanity check: 1st significant-change frame + last-stable frame (2 frames).

Expected usage: 2–4 frame reads × ~2500 tokens = ~10K total (vs 260K for all frames).

### AE diff curve analysis — easing, hold, timing (zero tokens)

AE diff over consecutive 60fps frames reveals animation characteristics without reading images.

```bash
ffmpeg -i capture.webm -vf fps=60 frames/frame-%04d.png -y
cd frames
echo "frame,ae" > ../ae.csv
PREV=""
for f in $(ls frame-*.png | sort); do
  if [ -n "$PREV" ]; then
    AE=$(compare -metric AE "$PREV" "$f" /dev/null 2>&1 | awk '{print $1}')
    echo "$f,$AE" >> ../ae.csv
  fi
  PREV="$f"
done
```

**Reading the curve:**

| Pattern | Meaning |
|---|---|
| `0, 0, 0, 0` | **Hold** — stationary. Consecutive zeros × frame interval = hold duration |
| `0, 5K, 20K, 50K, 80K, 90K, 95K` | **Ease-in** — slow start, accelerating |
| `95K, 90K, 80K, 50K, 20K, 5K, 0` | **Ease-out** — fast start, decelerating |
| `5K, 50K, 90K, 90K, 50K, 5K` | **Ease-in-out** — slow → fast → slow |
| `90K, 85K, 80K, 75K, 70K` | **Linear** — consistent rate |
| `0, 0, 80K, 0, 0` | **Instant/step** — single-frame change |

**Boundary detection:**
- Start: first frame with AE > threshold (e.g., 5000)
- End: last AE > threshold before returning to 0
- Duration: (end - start) / fps

**Multi-transition separation:** `high → 0 → high` — zero gap = hold between transitions.

**A/B timing (impl vs original):** find first AE spike in each = alignment anchor. Each subsequent spike should occur at the same relative time. Hold durations must match. Peak AE indicates transition magnitude.

### Bundle code is the spec, frames are verification

> **Never derive animation parameters from frame analysis alone.** Frames show THAT something moves. The bundle shows HOW (easing, duration, delay, position). When they disagree, the bundle is correct — you may be looking at the wrong conditional branch or the frame capture missed the start.

**Workflow:**
1. Parse bundle → exact timeline structure (position params, durations, easings)
2. Implement from bundle spec
3. Capture ref + impl at 60fps
4. AE diff both → compare curves
5. If curves don't match → re-read bundle (likely misinterpreted position parameter)

For splash-specific bundle cross-referencing, conditional branches, and GSAP timeline parsing, **Read `splash-extraction.md`**.

### Build the idle timeline

Combine all 3 tiers:

```json
{
  "phase": "idle",
  "type": "splash | auto-timer | css-animation | video-autoplay",
  "frameRange": [1, 30],
  "description": "Tier 1 AE spike frames 8-25, Tier 2 DOM .introHome opacity 0→1 at t=800ms, .gsap:text yPercent 100→0 at t=1200ms",
  "duration_ms": 3000,
  "domChanges": [
    { "selector": ".introHome", "property": "opacity", "from": "0", "to": "1", "at_ms": 800 },
    { "selector": ".gsap\\:text lines", "property": "transform", "from": "translateY(100%)", "to": "translateY(0%)", "at_ms": 1200 }
  ]
}
```

## Phase B — Scroll capture

**Purpose:** find everything that moves DURING scroll.

### B-1: Record full scroll video

**Scroll method selection:** Different scroll engines require different scroll commands. Check `scroll-engine.json` first.

| `scroll-engine.json` type | Method | Why |
|---|---|---|
| `native` or missing | `window.scrollTo(0, pos)` | Standard scroll |
| `lenis-native` / `@beyond/react` | `agent-browser scroll down N` (wheel events) | Lenis intercepts `scrollTo` but responds to wheel |
| `lenis-wrapper` / `locomotive` | `lenis.scrollTo(pos)` or `container.scrollTop = pos` | Transform-based scroll, native scroll disabled |
| `gsap-scrollsmoother` | `ScrollSmoother.scrollTo(pos)` | GSAP wrapper |

⛔ **`window.scrollTo` does NOT work on many modern sites.** If `scroll-engine.json` shows any custom scroll library, use `agent-browser scroll down` (mouse wheel simulation) instead. This triggers the actual scroll engine regardless of implementation.

```bash
# Scroll to top — use wheel events, not scrollTo
agent-browser --session <project> eval "(() => { window.scrollTo(0, 0); return 'ok'; })()"
agent-browser --session <project> wait 2000

agent-browser --session <project> record start tmp/ref/<component>/scroll-capture.webm
agent-browser --session <project> wait 500

# Method 1 (preferred): Use agent-browser scroll (wheel events)
# This works with ALL scroll engines including Lenis, Locomotive, ScrollSmoother
for i in $(seq 1 30); do
  agent-browser --session <project> scroll down 200
  agent-browser --session <project> wait 200
done

# Method 2 (fallback): JS scroll for native-scroll sites
# agent-browser --session <project> eval "(() => {
#   const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
#   let pos = 0;
#   const step = () => {
#     pos = Math.min(pos + 80, maxScroll);
#     window.scrollTo(0, pos);
#     if (pos < maxScroll) setTimeout(step, 50);
#   };
#   step();
# })()"
# agent-browser --session <project> wait 12000

agent-browser --session <project> wait 1000
agent-browser --session <project> record stop

ffmpeg -i tmp/ref/<component>/scroll-capture.webm -vf fps=60 \
  tmp/ref/<component>/scroll-frames/frame-%06d.png -y
```

### B-2: Track elements across scroll positions

> 60fps extraction above is for AE/SSIM automated comparison (Phase 4 verification), NOT for LLM reading. Do NOT Read scroll frames visually — use the DOM tracking eval below for exact property values at each scroll position.

```bash
agent-browser --session <project> eval "(() => {
  const lenis = document.querySelector('[class*=lenis], [class*=scroll]');
  const container = lenis || document.documentElement;
  const maxScroll = container.scrollHeight - window.innerHeight;

  const targets = [];
  document.querySelectorAll('section, [class*=banner], [class*=hero], [class*=asset], [class*=project], [class*=review], footer').forEach(el => {
    const cn = typeof el.className === 'string' ? el.className : '';
    targets.push({ el, selector: el.tagName.toLowerCase() + '.' + cn.trim().split(/\s+/)[0] });
  });
  document.querySelectorAll('section img, section video, [class*=background]').forEach(el => {
    const cn = typeof el.className === 'string' ? el.className : '';
    targets.push({ el, selector: el.tagName.toLowerCase() + '.' + cn.trim().split(/\s+/)[0] });
  });

  const samples = [];
  const positions = Array.from({ length: 11 }, (_, i) => Math.round(maxScroll * i / 10));
  let posIndex = 0;
  const capture = () => {
    if (posIndex >= positions.length) { window.__elementTracking = samples; return; }
    const scrollPos = positions[posIndex];
    if (lenis) lenis.scrollTop = scrollPos; else window.scrollTo(0, scrollPos);
    requestAnimationFrame(() => {
      const snapshot = { scrollY: scrollPos, scrollPct: Math.round(scrollPos / maxScroll * 100), elements: [] };
      targets.forEach(({ el, selector }) => {
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        snapshot.elements.push({
          selector,
          inViewport: r.bottom > 0 && r.top < window.innerHeight,
          top: Math.round(r.top),
          transform: s.transform !== 'none' ? s.transform.slice(0, 80) : null,
          opacity: s.opacity !== '1' ? s.opacity : null,
          scale: s.scale !== 'none' ? s.scale : null,
          clipPath: s.clipPath !== 'none' ? s.clipPath : null,
          position: s.position === 'sticky' || s.position === 'fixed' ? s.position : null,
        });
      });
      samples.push(snapshot);
      posIndex++;
      setTimeout(capture, 200);
    });
  };
  capture();
  return 'tracking ' + targets.length + ' elements across ' + positions.length + ' positions';
})()"

agent-browser --session <project> wait 5000
agent-browser --session <project> eval "(() => JSON.stringify(window.__elementTracking || [], null, 2))()"
```

Save to `tmp/ref/<component>/element-tracking.json`.

### B-3: Classify detected animations

| Pattern | Classification |
|---|---|
| `transform` changes with scroll | **parallax** — translateY changes at different rate than scrollY |
| `transform: scale()` changes | **scroll-zoom** — scale increases as element enters viewport |
| `opacity` goes 0→1 | **scroll-reveal** — fades in entering viewport |
| `clipPath` changes | **clip-reveal** — clipping area expands on scroll |
| `position: sticky` at some positions | **sticky/pinned** — stays fixed during scroll range |
| `top` constant while scroll moves | **fixed-in-section** — image fixed while container scrolls past |
| Visible but `transform` has large translateY | **parallax offset** — image moves slower than container |

## Phase C — Per-element deep capture

For each animation detected in Phase B, capture a targeted video:

```bash
# Scroll to just before the element triggers
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  const rect = el.getBoundingClientRect();
  const scrollPos = rect.top + window.scrollY - window.innerHeight;
  window.scrollTo(0, Math.max(0, scrollPos));
})()"
agent-browser --session <project> wait 500

# Record while scrolling through the element
agent-browser --session <project> record start tmp/ref/<component>/element-<name>.webm
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  const rect = el.getBoundingClientRect();
  const startPos = window.scrollY;
  const endPos = startPos + rect.height + window.innerHeight;
  let pos = startPos;
  const step = () => { pos += 40; window.scrollTo(0, pos); if (pos < endPos) setTimeout(step, 50); };
  step();
})()"
agent-browser --session <project> wait 5000
agent-browser --session <project> record stop

ffmpeg -i tmp/ref/<component>/element-<name>.webm -vf fps=60 \
  tmp/ref/<component>/element-<name>-frames/frame-%06d.png -y
```

### Extract values at key frames

For each element video: before trigger, 25%, 50%, 75%, after.

```bash
agent-browser --session <project> eval "(() => {
  const el = document.querySelector('<selector>');
  const s = getComputedStyle(el);
  return JSON.stringify({
    transform: s.transform, opacity: s.opacity, scale: s.scale,
    clipPath: s.clipPath, position: s.position, top: s.top,
    width: s.width, height: s.height,
  });
})()"
```

## Save: `animations-detected.json` → merge into `extracted.json` at Step 6b

Combine findings from A + B + C into `animations-detected.json`. This file is consumed by Step 6b (assemble `extracted.json`) — the pipeline gates check `extracted.json`, not this file directly.

```json
{
  "splash": {
    "type": "splash",
    "phases": [
      { "name": "loading", "duration_ms": 600, "description": "White screen + spinner" },
      { "name": "logo",    "duration_ms": 1400, "description": "Brand logo reveal" },
      { "name": "reveal",  "duration_ms": 800,  "description": "Overlay fades out" }
    ],
    "totalDuration_ms": 2800
  },
  "autoTimers": [
    { "selector": ".carousel", "interval_ms": 4000, "type": "slideshow" }
  ],
  "scrollAnimations": [
    {
      "selector": "section.product-collection .block",
      "type": "parallax",
      "scrollRange": { "start": 1200, "end": 2800 },
      "properties": { "translateY": { "from": 200, "to": 0 } },
      "ease": "linear (scroll-driven)"
    },
    {
      "selector": "section.banner-showroom .background",
      "type": "scroll-zoom",
      "scrollRange": { "start": 3000, "end": 4200 },
      "properties": { "scale": { "from": 0.55, "to": 1.0 } }
    }
  ],
  "textReveals": [
    {
      "selector": ".text-cta p",
      "type": "word-stagger",
      "triggerType": "intersection",
      "params": { "stagger_ms": 30, "duration_ms": 800, "ease": "power3.inOut", "translateY": "110%" }
    }
  ]
}
```

## Integration with Step 7 (Generation)

| Animation type | Implementation |
|---|---|
| `splash` | `IntroOverlay` component with phased `setTimeout` |
| `auto-timer` | `setInterval` + state cycling |
| `parallax` | `useScroll` + `useTransform` |
| `scroll-zoom` | `useScroll` + `useTransform` for scale |
| `scroll-reveal` | `ScrollReveal` / `LineReveal` component |
| `scroll-converge` | `useScroll` + `useTransform` for translateX |
| `word-stagger` | `WordReveal` component |
| `sticky/pinned` | CSS `position: sticky` with appropriate `top` |
| `clip-reveal` | `useScroll` + `useTransform` for clipPath |
