# transition-spec.json — Step 5d

> This file defines how to produce, verify, and maintain the single most important artifact for implementation.

## 1. bundle-map.json format

Map each downloaded chunk to the features it contains:

```json
{
  "chunks": [
    {
      "file": "main.js",
      "size": "305KB",
      "contains": ["GSAP core", "Lenis config", "Page transition", "Intro timeline"],
      "key_selectors": [".introHome", ".siteLoader", ".hero"]
    }
  ]
}
```

## 2. transition-spec.json format

One entry per distinct transition. Each entry is **self-contained**:

```json
{
  "transitions": [
    {
      "id": "intro-logo-stagger",
      "description": "SVG logo parts stagger up from below on page load",
      "trigger": "page load (first visit, delay 0.8s)",
      "source_chunk": "C8xy95f-.js",
      "bundle_branch": "n=true (first visit only)",
      "target": ".hero .gsap:logo > *",
      "animation": {
        "property": "y", "from": "height * 2", "to": 0,
        "duration": 1, "ease": "circ2 → cubic-bezier(0.08, 0.82, 0.17, 1)",
        "stagger": 0.1, "delay": 0.8
      },
      "reference_frames": "verify/intro/f010.png to f030.png",
      "dynamic": false
    }
  ]
}
```

**`dynamic` field (optional, default `false`):** set to `true` for entries whose visual is RAF-driven and never settles to a deterministic frame — auto-timer canvases, looping shaders, `<video>` autoplay, Lottie loops. `EXCLUDE_DYNAMIC=1 bash section-compare.sh ...` reads `transition-spec.json` and auto-augments its mask list with each `"dynamic": true` entry's `target` selector, hiding those regions from AE diff on both ref and impl. Per-frame pixel parity for these is unmatchable; layout-only verification is the right bar. Leave `false` (or omit) for entries with a deterministic end state (page-load reveal, scroll trigger, hover settle).

## 3. Rules

1. **One entry per distinct visual transition.** Don't merge different triggers.
2. **Include `bundle_branch`** — which `if/else` branch and under what condition.
3. **Include `source_chunk`** — which file to re-read if updating.
4. **Include `reference_frames`** — frame paths that show this transition.
5. **Convert GSAP easing to CSS** — write both GSAP name and `cubic-bezier()`.
6. **Include `simultaneous`** — transitions that co-occur with specific delays.
7. **Consult `animation-runtime-dump.json`** when present — easing functions, resolved ScrollTrigger pixel offsets, Lenis/IX2 timings live there even when bundle-grep misses them. Phase 0 of `animation-detection.md` writes this file.

## 4. Gate

```
$ cat tmp/ref/<c>/bundle-map.json
 □ Exists, each chunk mapped to features

$ cat tmp/ref/<c>/transition-spec.json
 □ Exists, ≥1 transition entry
 □ Each entry has: id, trigger, source_chunk, bundle_branch, target, animation
 □ Each entry has: reference_frames (or "none" if not yet captured)
 □ Each RAF-driven entry (auto-timer canvas, looping shader, video) has: "dynamic": true
 □ GSAP easing converted to cubic-bezier
 □ Capture verification passed (Step 5e below)
```

## 5. MANDATORY: Capture Verification (Step 5e)

**Problem:** Bundle analysis alone produces specs with wrong positions, timing, and branches. Code says "center" but the animation starts bottom-right.

**Rule:** Every entry with spatial properties MUST be verified against captured frames.

### Procedure

```bash
# 1. Record original from fresh load
agent-browser --session <name>-verify open "about:blank"
agent-browser --session <name>-verify set viewport 1440 900
agent-browser --session <name>-verify record start tmp/ref/<c>/verify/<id>.webm
agent-browser --session <name>-verify open "<original-url>"
agent-browser --session <name>-verify wait 8000
agent-browser --session <name>-verify record stop

# 2. Extract frames at 4fps
mkdir -p tmp/ref/<c>/verify/<id>
ffmpeg -y -i tmp/ref/<c>/verify/<id>.webm -vf "fps=4" tmp/ref/<c>/verify/<id>/f%03d.png

# 3. Read key frames and compare against spec values
```

### What to verify

| Spec field | Method |
|---|---|
| Start position (x, y) | First frame where element appears |
| End position | Last frame of transition |
| Direction | Compare start/end frames |
| Size | Measure in frame |
| Timing | Count frames ÷ fps |
| Stagger order | Sequential frames |

### When spec and frames disagree

1. **Frames are authoritative.** Update the spec.
2. Add `"verified"` field with correction and frame evidence.
3. Note which conditional branch is active at 1440×900 desktop.

### Must-verify transitions

- Preloader/splash — start position, sizes, spread direction, cutout shape
- Page entrance — element order, directions
- Scroll reveals — threshold, direction
- Hover followers — size, offset
- Slider — direction, active position

## 6. External SDK Detection and Reuse (MANDATORY)

When a site uses a third-party SDK (UnicornStudio, Spline, Rive, Lottie) to render visuals, **reuse the SDK directly** instead of replicating with CSS.

### Detection

Run during bundle download to identify SDKs:

```bash
agent-browser --session <s> eval "(() => {
  const resources = performance.getEntriesByType('resource');
  const patterns = [
    { name: 'UnicornStudio', match: /unicornstudio|unicornStudio/i, type: 'webgl-scene' },
    { name: 'Spline', match: /spline|@splinetool/i, type: 'webgl-scene' },
    { name: 'Rive', match: /rive\.wasm|@rive-app/i, type: 'animation' },
    { name: 'Lottie', match: /lottie|bodymovin/i, type: 'animation' },
    { name: 'Three.js', match: /three\.module|three\.min/i, type: 'webgl' },
  ];
  const sdks = [];
  resources.forEach(r => patterns.forEach(p => {
    if (p.match.test(r.name)) sdks.push({ name: p.name, type: p.type, url: r.name });
  }));
  return JSON.stringify({ sdks });
})()"
```

Save to `tmp/ref/<c>/external-sdks.json`.

### Decision matrix

| SDK | Scene data? | Action |
|---|---|---|
| UnicornStudio | JSON + textures | Reuse SDK + scene data |
| Spline | `.splinecode` | `@splinetool/react-spline` |
| Rive | `.riv` | `@rive-app/react-canvas` |
| Lottie | `.json` | `lottie-react` or `lottie-web` |
| Three.js | GLTF/GLB | Evaluate `@react-three/fiber` |
| GSAP plugin dependency choice | N/A | Project library / OSS / native alternatives (see `transition-implementation.md`) |

### Reuse procedure

1. **Download scene data** from DOM (`data-us-project-src`, `data-spline`, etc.)
2. **Download texture dependencies** referenced inside scene data
3. **Replace CDN URLs** with local paths
4. **Create wrapper component**: lazy load SDK (after preloader, `requestIdleCallback`), CSS fallback during loading
5. **Optimize**: `fps: 30`, `dpi: 1`, gate behind `enabled` prop

## 7. When to load these documents

- **Step 7 (implementation):** Read `transition-spec.json` before writing animation code.
- **Iteration/fixes:** Read the relevant entry — don't re-grep bundles.
- **Re-invocation:** Check if spec exists in `tmp/ref/<c>/`; load immediately.
