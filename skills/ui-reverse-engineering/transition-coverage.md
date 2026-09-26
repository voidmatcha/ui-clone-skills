# Transition Coverage Audit — Step 6d

**Step 6d runs AFTER `section-audit.md` (Step 6c) and BEFORE `python -m ui_clone.gate <ref-dir> pre-generate`.** It produces `transition-coverage.json` — the canonical inventory of every element that animates and the measured curve of its animation. The pre-generate gate fails unless coverage is non-empty.

> **Why this step exists.** Pixel screenshots are static. DOM inspection at one scroll position is static. Bundle grep finds the *intent* of animation but not whether it actually applied to a given element. The only way to confirm "this element animates" is to **measure its computed style at multiple scroll positions and detect change**.

## When this step is mandatory

Always. Skipping this means the pre-generate gate cannot enforce that every animated element is mapped to an animation hook in the impl. Even sites that *look* mostly static still typically have 5–20 scroll-driven transforms.

## Inputs

- Live `agent-browser --session <s>` open on the original site (post-splash)
- `tmp/ref/<c>/structure.json` (so we know the meaningful selectors to sample)
- `tmp/ref/<c>/webflow-detection.json` (if Webflow site → see `webflow-ix2.md` Step W-2 to extract the hide-rule selector list and seed the candidate list)

## Output

```
tmp/ref/<c>/transition-coverage.json
```

Schema:
```json
{
  "url": "...",
  "viewport": { "w": 1440, "h": 900 },
  "totalScrollHeight": 11440,
  "samplePositions": [0, 1144, 2288, 3432, 4576, 5720, 6864, 8008, 9152, 10296],
  "scrollEngine": "lenis|native|locomotive|other",
  "scrollEngineParams": { "lerp": 0.1, "wheelMultiplier": 1 },
  "animatedElements": [
    {
      "selector": ".pill-scroll",
      "samples": [
        { "scrollY": 0, "transform": "matrix(...)", "opacity": "1", "rect": { "y_view": 800, "y_doc": 800 } },
        { "scrollY": 1144, "transform": "matrix(...)", "opacity": "1", "rect": { "y_view": 0, "y_doc": 1144 } }
      ],
      "decoded": {
        "rotateRange": { "min": 0, "max": 5.27, "atScroll": [0, 1500] },
        "translateYRange": { "min": 0, "max": 288, "atScroll": [900, 3000] },
        "opacityRange": null,
        "scaleRange": null
      },
      "trigger": "scroll-driven",
      "sectionAnchor": ".click-scroll-height",
      "sectionRange": { "start": 900, "end": 2585 }
    }
  ],
  "staticElements": [".scroll-text-static", ".jm-icon"],
  "skipped": [{ "selector": "...", "reason": "size < 30px" }]
}
```

## Procedure

### Step 6d-1: Build candidate selector list

Sources, in priority order:
1. Webflow IX2 hide-rule selectors (`webflow-hide-rule.json` — see `webflow-ix2.md`)
2. `[data-w-id]`, `[data-wf-target]` elements (Webflow)
3. Elements with `position: sticky`, `transform != none`, `will-change != auto`, `animation-name != none` at scroll=0
4. Top 100 elements by viewport-coverage size (largest visible boxes)
5. Elements scraped from `bundle-map.json` (selectors referenced by GSAP/Motion/Lenis)

Deduplicate. Cap at 200. Save to `tmp/ref/<c>/animation-candidates.json`.

### Step 6d-2: Multi-position scroll sampling

```bash
agent-browser --session <s> eval "(() => window.scrollTo(0, 0))()" && sleep 1.5

TOTAL=$(agent-browser --session <s> eval "(() => document.body.scrollHeight)()" | tail -1 | tr -d '"')
STEP=$((TOTAL / 10))

# Use Lenis-aware wheel events when scrollEngine != native
for i in 0 1 2 3 4 5 6 7 8 9; do
  Y=$((STEP * i))
  agent-browser --session <s> eval "
    (async () => {
      let cur = window.scrollY;
      while (Math.abs(cur - $Y) > 50) {
        const delta = $Y - cur;
        window.dispatchEvent(new WheelEvent('wheel', { deltaY: Math.sign(delta) * Math.min(800, Math.abs(delta)), bubbles: true }));
        await new Promise(r => setTimeout(r, 50));
        cur = window.scrollY;
      }
      await new Promise(r => setTimeout(r, 600));
      // Sample candidates
      const sels = $(jq -c '.candidates' tmp/ref/<c>/animation-candidates.json);
      const samples = {};
      for (const s of sels) {
        const el = document.querySelector(s);
        if (!el) { samples[s] = null; continue; }
        const cs = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        samples[s] = {
          transform: cs.transform,
          opacity: cs.opacity,
          y_view: Math.round(r.top),
          y_doc: Math.round(r.top + window.scrollY),
          width: Math.round(r.width),
          height: Math.round(r.height)
        };
      }
      return JSON.stringify({ scrollY: window.scrollY, samples }, null, 2);
    })()
  " > tmp/ref/<c>/coverage-pos-$i.json
done
```

### Step 6d-3: Decode each sample's matrix

For each `transform` value, decode using:

```javascript
function decode(transform) {
  if (transform === 'none') return { rotate: 0, translateY: 0, scale: 1 };
  if (transform.startsWith('matrix3d(')) {
    // 4x4 matrix — extract rotateX (m22, m23), rotateY (m11, m13), rotateZ (m11, m12), translate (m42)
    const v = transform.match(/-?\d+(\.\d+)?/g).map(Number);
    return {
      rotateX: Math.atan2(v[6], v[5]) * 180 / Math.PI,
      rotateY: Math.atan2(-v[2], v[0]) * 180 / Math.PI,
      rotateZ: Math.atan2(v[1], v[0]) * 180 / Math.PI,
      translateY: v[13],
      translateZ: v[14]
    };
  }
  // 2D matrix(a,b,c,d,e,f)
  const v = transform.match(/-?\d+(\.\d+)?/g).map(Number);
  return {
    rotateZ: Math.atan2(v[1], v[0]) * 180 / Math.PI,
    scale: Math.sqrt(v[0] ** 2 + v[1] ** 2),
    translateX: v[4],
    translateY: v[5]
  };
}
```

### Step 6d-4: Classify per element

For each candidate selector, look at the 10 samples:
- If `decoded.rotateZ` varies > 1° across samples → **scroll-driven rotation**
- If `decoded.translateY` varies > 5px → **scroll-driven translation (parallax)**
- If `decoded.scale` varies > 0.02 → **scroll-driven scale**
- If `opacity` varies > 0.05 → **scroll-driven fade**
- If `decoded.rotateX` or `rotateY` varies > 1° → **scroll-driven 3D**
- If width/height visibly changes between samples (line-draw, mask reveal) → **scroll-driven dimensions**
- If transform is constant but element only appears in some samples (`y_view` from offscreen → onscreen) → **enter reveal** (intersection trigger)
- If all samples identical → **static**

Note `sectionRange` (where in the page each animated property is changing) — needed for impl mapping.

### Step 6d-5: Assemble `transition-coverage.json`

For each non-static animated element, write an entry per the schema above. Include `trigger` classification ("scroll-driven", "enter-reveal", or "complex"), the property ranges, and the section anchor.

The skipped/static lists exist for transparency — pre-generate gate inspects `animatedElements.length` against expected count derived from candidate list to detect "you ran the audit but coverage is suspiciously low".

## Pre-generate gate enforcement

`python -m ui_clone.gate tmp/ref/<c> pre-generate` MUST verify:

```bash
test -f tmp/ref/<c>/transition-coverage.json
ANIMATED=$(jq '.animatedElements | length' tmp/ref/<c>/transition-coverage.json)
[ "$ANIMATED" -gt 0 ] || die "No animated elements detected. Either site is genuinely static (rare) or audit was not run correctly."

# For Webflow sites, animated count should be at least 5% of [data-w-id] count
if [ -f tmp/ref/<c>/webflow-detection.json ]; then
  WID=$(jq '.wIdCount' tmp/ref/<c>/webflow-detection.json)
  MIN_EXPECTED=$((WID / 20))
  [ "$ANIMATED" -ge "$MIN_EXPECTED" ] || warn "animated=$ANIMATED but data-w-id=$WID. Expected ≥$MIN_EXPECTED. Audit likely incomplete."
fi
```

## How the coverage drives implementation

When `component-generation.md` builds the briefing for the generation subagent, it MUST include a **per-element transform-curve table** derived from `transition-coverage.json`:

```
| Selector | Trigger | Property | From | To | Section range | @beyond hook |
|---|---|---|---|---|---|---|
| .pill-scroll | scroll-driven | rotateZ | 0° | 0° (peak 5°) | [900, 2585] | useScroll + useTransform |
| .pill-scroll | scroll-driven | translateY | 0px | 288px | [900, 2585] | useScroll + useTransform |
| .front-folder | scroll-driven | rotateX | 20° | -25° | [5215, 6122] | useScroll(target=ref) |
| .h2-step1-1 | enter-reveal | opacity | 0 | 1 | [6500, 7000] | useScrollTrigger |
| .light-jm-img | scroll-driven | opacity | 0 | 1 | [7000, 8500] | useScroll + useTransform |
```

Without this table, the subagent fills in plausible-but-wrong defaults (translateY only, no rotation) and silently ships a less-animated clone. With it, each row maps to one mandatory hook in the impl.

## Post-implementation verification (Step 8c-extended)

After generating, re-sample the **localhost impl** at the same 10 scroll positions and produce `tmp/ref/<c>/transition-coverage-impl.json`. Diff against `transition-coverage.json`:

For each entry in `animatedElements`:
- If impl has matching property variation within 20% → ✓
- If impl property is static → ✗ "missing scroll motion"
- If property direction is wrong (rotates clockwise instead of counter) → ✗

This is the **content-aware verification** that pixel diff misses.

## Failure modes this prevents

| Without coverage audit | With coverage audit |
|---|---|
| Decorative shapes ship as static | Each shape's rotation curve enforced |
| Folder icon stays 2D | rotateX requirement enforced |
| Headlines never reveal | enter-reveal trigger enforced |
| Line-draw stays width:0 | dimension change requirement enforced |
| Light/dark image swap missing | opacity range enforced |
| Section enter animations absent | every element with offscreen→onscreen sample gets reveal hook |

The pre-generate gate is no longer just "files exist" — it's "every animated element in the original has an implementation contract."
