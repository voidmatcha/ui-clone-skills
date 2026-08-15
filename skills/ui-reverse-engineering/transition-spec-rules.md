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

**`dynamic` field (optional, default `false`):** set to `true` for entries whose visual cannot settle to the same frame across fresh loads — auto-timer canvases, looping shaders, `<video>` autoplay, Lottie loops, and randomized initialization such as `Math.random()`, `gsap.utils.random()`, or `randomScaleRange`. `EXCLUDE_DYNAMIC=1 bash section-compare.sh ...` reads `transition-spec.json` and auto-augments its mask list with each `"dynamic": true` entry's narrow `target` selector, hiding those regions from AE diff on both ref and impl. Per-frame pixel parity for these is unmatchable; masked-region static and runtime checks remain the verification bar. Leave `false` (or omit) for entries with a deterministic end state and deterministic initialization (page-load reveal, scroll trigger, hover settle).

**`scroll-state-machine` pixel channel:** use this structured shape when the
bundle declares raw `scrollY` pixel thresholds for a fixed/sticky element:

```json
{
  "type": "scroll-state-machine",
  "target": ".fixed-nav",
  "animation": {
    "channels": [
      {
        "property": "top",
        "inputDomain": "scroll-y-px",
        "inputRange": [0, 100],
        "outputRange": [56, 20],
        "unit": "px"
      }
    ]
  }
}
```

These bundle-literal `scrollY` pixels are document-height independent. Never
normalize them by the capture session's `maxScroll`, and never merge them into
`scroll-scrub` or other `scrollYProgress`/progress channels. Generate
selector-scoped direct style updates for the real target element
(`ScrollLinkedStyleDriver`), not a wrapper `ScrollScrub`. If the reference uses
distinct desktop and mobile nav elements, record separate breakpoint-specific
targets/channels. Do not fabricate this channel when no source evidence or spec
entry exists.

## 3. Rules

1. **One entry per distinct visual transition.** Don't merge different triggers.
2. **Include `bundle_branch`** — which `if/else` branch and under what condition.
3. **Include `source_chunk`** — which file to re-read if updating.
4. **Include `reference_frames`** — paths to existing, non-empty local image or
   video evidence under the reference directory. A string, a list of paths, or
   range text such as `verify/intro/f010.png to f030.png` is accepted only when
   every named media file exists. Placeholder values such as `"none"` do not
   satisfy the spec gate.
5. **Convert GSAP easing to CSS** — write both GSAP name and `cubic-bezier()`.
6. **Include `simultaneous`** — transitions that co-occur with specific delays.
7. **Consult `animation-runtime-dump.json`** when present — Check `animation-runtime-dump.json` `captureStatus` and `scrollAudit` first. Only successful captures with a trustworthy audit can ground runtime motion rows. Easing functions, resolved ScrollTrigger pixel offsets, Lenis/IX2 timings, and scroll-linked computed styles live there even when bundle-grep misses them. Phase 0 of `animation-detection.md` writes this file.
   When a runtime row was captured at one viewport but captured CSS proves the
   animated property is scoped by a breakpoint, add an explicit transition-level
   `"media": "(min-width: ...)"` or `"media": "(max-width: ...)"`. This must be
   CSS-grounded evidence, not an inference from capture viewport width. The
   planner must preserve the media guard by exact runtime `sourceId`, and the
   runtime replay must restore styles it wrote when that media query becomes
   inactive. Conflicting media guards for one `sourceId` are invalid evidence;
   omit that runtime replay instead of falling back to an unguarded global curve.
8. **Consult `state-structure-spec.json`** when a transition depends on DOM/class/content state — page-load splash swaps, sticky threshold class flips, hover class/data-state mutations, accordions, tabs, modals, or click navigation. This file is the compact browser-observed state index; raw `states/**` HTML dumps are fallback evidence for `source-forensics`, not first-pass generation context.
9. **Run `verification-plan.sh` before finalizing the spec, then run it again after the spec changes.** Plan signals decide which runtime checks must execute; they are not transition proof. In particular, `hasIOReveal` can come from a conservative boolean CSS classifier over `structure.json` plus captured CSS. Map that signal to an evidence-backed `transitions[]` entry only when captured source/frames prove the reveal, otherwise add a structured `skipped[]` reason. Never promote a dispatch hint into a fabricated transition.

## 4. Gate

```
$ cat tmp/ref/<c>/bundle-map.json
 □ Exists, each chunk mapped to features

$ cat tmp/ref/<c>/transition-spec.json
 □ Exists, ≥1 transition entry
 □ NOT the Phase-2 placeholder: `source` must not be `ui_clone.extraction_artifacts`
   and `placeholder` must not be true — the auto-minted floor never satisfies
   the gate on a motion site; you must draft the real spec
 □ COMPLETENESS (enforced by `gate spec` spec-inventory-coverage): every row in
   `interactions-detected.json`, every `scroll-transitions.json` entry, every
   motion construction site in `bundle-extraction.json`, and every successful
   `scrollLinkedStyles[]` runtime row in `animation-runtime-dump.json` maps to a
   `transitions[]` id OR a `skipped[]` entry `{sourceArtifact, sourceId, reason}`;
   every true `verification-plan.json` signal class (scroll-scrub,
   scroll-state-machine, IO-reveal, hover, click) has ≥1 matching entry. An
   unmapped detection = FAIL. Successful `scrollLinkedStyles[]` rows are mapped or skipped explicitly.
 □ For each successful `scrollLinkedStyles[]` row with `sourceId` and `selector`,
   a `transitions[]` entry must include `"sourceArtifact": "animation-runtime-dump.json"` plus the exact `sourceId`, or a
   `skipped[]` entry with the same `sourceArtifact` and `sourceId` plus a
   non-empty `reason`. Selector fallback is legacy-only and must be unambiguous.
   A planner-collapsed all-match site does not rewrite the spec inventory:
   every original runtime `sourceId` must still be accounted for here. For
   identical repeated rows, one transition plus structured skips for the remaining original `sourceId` rows is valid when all skipped rows point to the covered equivalent transition. Mixed repeated rows remain indexed per matched element; do not collapse them by selector alone.
 □ A capture error on a motion-rich reference blocks `gate spec`; rerun or
   recover the browser session instead of writing a skip for unknown runtime
   motion.
 □ `verification-plan.sh` was run once before the final inventory pass and once
   after editing the spec; a classifier signal alone never counts as spec proof
 □ Every `target` parses as a CSS selector (querySelector-able — never a
   declaration fragment)
 □ Every non-hover `target` names the element whose animated property the
   runtime probe measures. When a state class is toggled on an ancestor but CSS
   animates a descendant, target the animated descendant (optionally constrained
   by the ancestor's pre-trigger state) and record the class owner in the
   animation metadata. For hover, keep the pointer hit area in `target` and put
   the animated descendant selector in `affectedTarget`; the affected match must
   be contained by the exact activated element. Dispatch and hit testing use
   `target`, while style, timing, and tight-ROI measurement use `affectedTarget`.
   Legacy `animation.measurement: target-and-descendants` remains readable, but
   new evidence should use the explicit selector split. An ancestor without an
   affected measurement scope is not motion evidence
 □ Each entry has: id, trigger, source_chunk, bundle_branch, target, animation
 □ Each entry has: reference_frames naming existing, non-empty local image/video
   evidence; missing files, empty values, and placeholders such as "none" FAIL
 □ Each non-deterministic entry (auto-timer canvas, looping shader, video, or randomized initialization) has top-level `"dynamic": true`; stochastic animation fields without it FAIL the spec gate
 □ Entries that depend on DOM structure/class/content swaps reference the matching `state-structure-spec.json` event in notes or `state_structure_ref`
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
