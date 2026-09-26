# Animation Detection — Step 6

Detect ALL motion on the page: splash/intro, auto-timers, scroll-driven, parallax. Uses **high-framerate video** + **per-element tracking** + **multi-snapshot DOM state**.

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

All 4 phases are required for **completion**, but not all are a prerequisite for the **first generation**. Phase 0 is cheap (one eval) and recovers runtime-only params that bundle-grep and video never see (`"top 80%"` → resolved pixel start, arrow-function eases, IX2 timeline keys) — run it **before** generation by default, with a tight timeout.

**Generate-first / time-box discipline.** Phases A/B/C (60fps idle/scroll/per-element video) are expensive and *refine* motion on an existing impl — they do **not** *gate* the scaffold. Reach a `generation-plan.json` and a base scaffold from Steps 1–5 + Phase 0 **first**, then run A/B/C to add exact motion timings and satisfy the post-implement / runtime-proof gates. Frames are *verification* of the bundle spec, not a precondition for it; a full A/B/C pass before any impl exists can consume the entire budget and produce no deliverable.

**Exception — capture motion before generation when it is load-bearing for STRUCTURE, not just polish. Decide this from CHEAP pre-Step-6 artifacts only (Phase-0 runtime dump + Steps 0A/2.6-pre/5/5c-a outputs). Never gate this decision on `state-structure-spec.json`, which is itself written by the A/B/C capture scripts you are deciding whether to run:**
- **Canvas/WebGL** — `hasCanvas` in `canvas-webgl-detection.json` (Step 0A); if true, read `canvas-webgl-extraction.md` before Phase 2.
- **Timed splash/preloader** overlays — preloader / class-flip / >20% DOM mutation already flagged in `dom-state-diff.json` (Step 2.6-pre).
- A **custom scroll engine** — non-native wrapper/API in `scroll-engine.json` (Step 5c-a) whose wrapper choice dictates layout coupling.
- A **structure-altering interaction** — an interaction already flagged in `interactions-detected.json` (Step 5) that changes component NESTING.

Everything else (exact transform/opacity/scale per scroll %, hover deltas, per-element timings) is polish: generate the scaffold first, then run A/B/C.

## Multi-snapshot DOM state inputs (v0.7.0+)

Phases 0/A/B/C capture **motion** (pixels over time); the multi-snapshot scripts (`scripts/extract/capture-{states,scroll,hover,click}.sh`) capture **DOM state** (HTML + computed style at distinct moments) — video shows WHAT moves, multi-snapshot shows WHICH class hooks / scroll positions / hover targets gate it. The scripts drive a live `agent-browser` page, never static source: splash is observed during a fresh navigation, scroll uses the detected scroll engine, hover uses runtime probes plus visual checks, and click uses real `agent-browser click` in an isolated session per safe candidate.

| Artifact | Produced by | Consumes |
|---|---|---|
| `tmp/ref/<c>/states/splash/trajectory.json` + `0ms.json`, `settled.json`, `<NNN>ms.json` | `capture-states.sh` (Phase A) | Splash class transitions (`is-loading` → `is-loaded`, body-class flips) and DOM bookends at t=0 / end-of-splash; `<NNN>ms.json` files capture structural mutations >20% DOM delta. Feeds `splash-extraction.md` together with Phase A pixels. |
| `tmp/ref/<c>/states/scroll/<pct>pct.json` + `trajectory.json` + `summary.json` | `capture-scroll.sh` (Phase B) | DOM at 7 scroll percentages [0, 10, 25, 50, 75, 90, 100] plus `visibleSections` per stop. `summary.json` records the proven native/Lenis/Locomotive transport and its detection reason (hidden instances need a navigation-time root wheel-listener proof; marker-only detection fails closed). `scrollHeightDeltaPct` exposes growth during the sweep; an incomplete traversal is not published as evidence. |
| `tmp/ref/<c>/states/hover/elem-<id>.json` + `manifest.json` + `summary.json` | `capture-hover.sh` (Phase C) | Per-candidate hover signal: `kind: "css"` (CSSOM `:hover` rules), `kind: "js"` (runtime event probe + computed-style diff), `domChanges` (class/text/aria/data-state mutation), or `kind: "css+js"`. Manifest `id` = SHA-256[:8] of activation\|affected\|kind for cross-referencing. |
| `tmp/ref/<c>/states/click/click-<id>.json` + `manifest.json` + `summary.json` | `capture-click.sh` (Phase C-click) | Real click per safe candidate in an isolated throwaway session (not a crawler). Navigation is guarded with `back`/reopen and recorded as `navigationOnly`; non-HTTP schemes, downloads, and `_blank` targets are recorded as declared navigation and skipped; only same-page clicks can claim DOM mutation. |
| `tmp/ref/<c>/state-structure-spec.json` | `state-structure-spec.py` (post-pass) | Compact derived index across splash/scroll/hover/click events: triggers, event drivers, class/DOM mutation summaries, guard status, artifact references — never full `outerHTML`. Read `state-structure-spec.md` only when consuming this file to refine the scaffold. |

**State-coverage gate** (`gate_state_coverage`, between `pre-generate` and `post-implement` in GATE_ORDER) consumes these artifacts and fails when impl source lacks corresponding hooks: class strings from the splash trajectory, scroll-state primitives (IntersectionObserver / ScrollTrigger / useScroll / `use:inView` / `v-intersection-observer` / ...), hover handlers (`:hover` / Tailwind `hover:` / `onMouseEnter` / `whileHover` / `@mouseenter` / `on:mouseenter`), and same-page click handlers (`onClick` / `addEventListener("click")` / `@click` / `on:click` / `aria-expanded` / `data-state`). Legacy ref dirs without `states/` pass as skip.

## Phase 0 — Runtime instrumentation (zero-video)

**Purpose:** capture animation parameters that exist ONLY at runtime — ScrollTrigger `start: "top 80%"` resolved to a pixel offset, an `ease` defined as `t => t*t*(3-2*t)`, a user-composed Lenis `easing`, Webflow IX2 timeline IDs minted post-mount.

```bash
PLUGIN_ROOT="${PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-${UI_CLONE_ROOT:-}}}}"
bash "$PLUGIN_ROOT/scripts/extract/extract-animation-runtime.sh" <session> tmp/ref/<component>
```

Writes `tmp/ref/<component>/animation-runtime-dump.json`:

```json
{
  "captureStatus": "ok",
  "captureError": null,
  "scrollAudit": {
    "engine": "native",
    "maxScroll": 2560,
    "samples": [
      { "requested": 0, "observed": 0, "method": "native" },
      { "requested": 0.25, "observed": 0.25, "method": "native" },
      { "requested": 0.5, "observed": 0.5, "method": "native" },
      { "requested": 0.75, "observed": 0.75, "method": "native" },
      { "requested": 1, "observed": 1, "method": "native" }
    ]
  },
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
  "scrollLinkedStyles": [
    {
      "sourceId": "runtime-scroll-filter-001",
      "selector": ".hero-media",
      "filter": ["filter"],
      "varies": ["filter"],
      "byScroll": {
        "0": {
          "transform": "none",
          "opacity": "1",
          "width": "640px",
          "height": "480px",
          "borderRadius": "0px",
          "filter": "blur(12px) brightness(0.8)"
        },
        "0.5": {
          "transform": "none",
          "opacity": "1",
          "width": "640px",
          "height": "480px",
          "borderRadius": "0px",
          "filter": "blur(4px) brightness(1.5)"
        },
        "1": {
          "transform": "none",
          "opacity": "1",
          "width": "640px",
          "height": "480px",
          "borderRadius": "0px",
          "filter": "blur(0px) brightness(2)"
        }
      },
      "latched": false
    }
  ],
  "lenis": { "duration": 1.2, "easing": "t => Math.min(1, 1.001 - Math.pow(2, -10 * t))", "smoothWheel": true },
  "ix2": { "timelineCount": 12, "timelineKeys": ["e-1","e-2"], "eventCount": 24 },
  "generatedAt": "2026-05-14T20:00:00.000Z"
}
```

Only `captureStatus: "ok"` with a trustworthy `scrollAudit` is usable motion
evidence. `captureStatus: "error"`, a nonzero wrapper exit, an empty/invalid
browser response, or a scrollable page without >=3 distinct observed positions
means rerun or recover the browser session; never interpret as no motion.

Missing runtime families may be `null` only on successful capture. That explicit
success shape distinguishes "not observed" from "browser capture failed";
`captureError` records the failure case instead of letting downstream agents
guess absence.

`scrollLinkedStyles[]` rows must carry stable `sourceId` values so Step 5d and
Step 7 can cite the same row. A single `blur(px)` runtime curve is replayable,
and a stable `blur(px) brightness(number)` runtime curve is replayable by the
selector-scoped linked driver. The driver treats order-changing, extra-function, negative, nonfinite, or mixed compound filters as captured evidence only; they need explicit implementation and verification rather than invented interpolation.

Runtime dump rows stay one `sourceId` per observed site. The planner may collapse identical repeated non-latched runtime curves for the same selector, scroll progress keys, and scope into a generation-plan scrub site with `replay: "all-matches"` plus `sourceIds[]`. Mixed repeated curves stay selector-indexed so element-specific offsets are not smeared across all matches.
Consult this file when authoring `transition-spec.json`
(`transition-spec-rules.md`) — easing/threshold values that bundle-grep misses
live here.

## Phase A — Idle capture (splash + auto-timers)

**Purpose:** find everything that moves WITHOUT user interaction. **Recording must start DURING page load** — waiting for load first means the splash has already played.

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

> **Do NOT Read every frame with the LLM** (104 frames ≈ 260K tokens). Automation first; LLM only for what automation can't classify.

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

> **Use `requestAnimationFrame`, NOT `setInterval`.** 200ms polling (5fps) gives ~4 samples of a 0.8s ease-out instead of ~48 — it tells you THAT something changed, not HOW.

**If Tier 1 shows AE spikes in the first 1–3 seconds, a splash exists** — exactly what plain `eval` misses, since capture attaches after the splash already fired.

> **→ If a splash exists, read `splash-extraction.md`** for the throttled splash capture protocol, video↔bundle cross-reference, GSAP timeline parsing, conditional branch detection, overlay preservation, and splash end-state verification.

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

60fps is what resolves the clipPath axis (`inset(0% 0% 0.03%)` → `0.06%` — clearly the 3rd value), the easing curve shape (48 samples vs 4), the transform trajectory (`scale(0.85)` → `scale(0.63)` deceleration), and property coupling (opacity + transform both at t=1260).

#### Tier 3 — LLM Read: transition boundaries only (minimal tokens)

After Tier 1+2 you know WHEN and WHAT. Read images ONLY when: a large AE spike has no Tier 2 property change (image content, not CSS — read 1 frame); a transition boundary needs classifying ("is splash fully gone by frame 35?" — read that frame); or as a final sanity check (first significant-change frame + last stable frame). Expected usage: 2–4 frame reads.

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

**Boundary detection:** start = first frame with AE > threshold (e.g., 5000); end = last AE > threshold before returning to 0; duration = (end - start) / fps. `high → 0 → high` = hold between transitions. **A/B timing (impl vs original):** the first AE spike in each is the alignment anchor; each subsequent spike must occur at the same relative time, hold durations must match, and peak AE indicates magnitude.

### Bundle code is the spec, frames are verification

> **Never derive animation parameters from frame analysis alone.** Frames show THAT something moves; the bundle shows HOW (easing, duration, delay, position). When they disagree, the bundle is correct — you may be on the wrong conditional branch or the capture missed the start.

Workflow: parse bundle → exact timeline structure → implement from it → capture ref + impl at 60fps → AE diff both → if curves don't match, re-read the bundle (likely a misinterpreted position parameter). If a splash exists, **read `splash-extraction.md`** for splash-specific bundle cross-referencing, conditional branches, and GSAP timeline parsing.

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

**Scroll method selection** — check `scroll-engine.json` first:

| `scroll-engine.json` type | Method | Why |
|---|---|---|
| `native` or missing | `window.scrollTo(0, pos)` | Standard scroll |
| `lenis-native` / `@beyond/react` | `agent-browser --session <project> scroll down N` (wheel events) | Lenis intercepts `scrollTo` but responds to wheel |
| `lenis-wrapper` / `locomotive` | `lenis.scrollTo(pos)` or `container.scrollTop = pos` | Transform-based scroll, native scroll disabled |
| `gsap-scrollsmoother` | `ScrollSmoother.scrollTo(pos)` | GSAP wrapper |

⛔ **`window.scrollTo` does NOT work on many modern sites.** With any custom scroll library, use `agent-browser --session <project> scroll down <px>` (wheel simulation) — it triggers the actual engine regardless of implementation.

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
# NOTE: do NOT infer the scroll container from [class*=scroll] — it matches
# decorative nodes (.scroll-indicator, .pill-scroll) and writes scrollTop to a
# non-scrollable div, so the page never moves and every sample is identical
# (all-static -> scroll animations silently dropped). Consult scroll-engine.json
# (from B-1) for the real engine; on a custom engine (Lenis/Locomotive)
# scrollTop/scrollTo is intercepted — drive wheel events (see B-1) instead.
agent-browser --session <project> eval "(() => {
  const engine = document.querySelector('[class*=lenis], [data-scroll-container], [class*=locomotive]');
  const container = document.scrollingElement || document.documentElement;
  const maxScroll = Math.max(container.scrollHeight, document.body.scrollHeight) - window.innerHeight;

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
    window.scrollTo(0, scrollPos);
    if (engine && engine !== document.documentElement) engine.scrollTop = scrollPos;
    requestAnimationFrame(() => {
      // Record the REAL scrolled position, never the intended scrollPos — on an
      // intercepted engine the page may not have moved, and a snapshot that
      // lies about scrollY makes an all-static sweep look like a valid one.
      const realY = Math.round(window.scrollY || document.documentElement.scrollTop || (engine ? engine.scrollTop : 0));
      const snapshot = { scrollY: realY, scrollPct: maxScroll > 0 ? Math.round(realY / maxScroll * 100) : 0, elements: [] };
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

Combine findings from A + B + C into `animations-detected.json`, consumed by Step 6b (the gates check `extracted.json`, not this file).

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
