# Bundle-Based Verification — Step 5c-b (Numerical Comparison)

**For auto-rotating, scroll-driven, and timer-based animations, screenshot comparison is unreliable because the reference point (T=0) cannot be synchronized between ref and impl.** Use bundle-extracted values as the source of truth instead.

## When to use

| Animation type | Screenshot verification | Bundle verification |
|---|---|---|
| Static layout (colors, spacing, fonts) | ✅ Primary | Not needed |
| CSS hover/click transitions | ✅ Primary (triggerable) | Backup |
| Page-load sequence | ⚠️ Timing-sensitive | ✅ Primary |
| Auto-rotating carousel | ❌ Unreliable | ✅ Primary |
| Scroll-driven parallax/flip | ⚠️ GSAP scroll state varies | ✅ Primary |
| Timer-based animations (setInterval, rAF) | ❌ Unreliable | ✅ Primary |

## Pipeline

```
Step 1: Identify animated elements     → DOM scan for animated selectors
Step 2: Download & grep bundles        → Extract animation parameters
Step 3: Build verification spec        → extracted.json with from/to/duration/ease per selector
Step 4: Compare spec vs impl code      → Numerical diff
Step 5: Resting-state screenshot (1x)  → Sanity check only (idle state, no animation running)
```

## Step 1: Identify animated elements

Scan the DOM for elements that have animation-related properties set:

```bash
agent-browser --session <s> eval "(() => {
  const animated = [];
  document.querySelectorAll('*').forEach(el => {
    const s = getComputedStyle(el);
    const hasTransition = s.transitionDuration !== '0s';
    const hasAnimation = s.animationName !== 'none';
    const hasWillChange = s.willChange !== 'auto';
    const hasTransform = s.transform !== 'none';
    if (hasTransition || hasAnimation || hasWillChange || hasTransform) {
      const cls = el.getAttribute('class') || '';
      const id = el.id || '';
      // Extract meaningful class names (skip utility classes under 4 chars)
      const meaningful = cls.split(/\s+/).filter(c => c.length > 3 && !c.match(/^(flex|grid|text|bg-|p-|m-|w-|h-)/));
      if (meaningful.length > 0 || id) {
        animated.push({
          selector: id ? '#' + id : '.' + meaningful[0],
          classes: meaningful.slice(0, 3),
          willChange: s.willChange,
          transition: s.transitionProperty + ' ' + s.transitionDuration,
          animation: s.animationName,
          transform: s.transform.slice(0, 60),
        });
      }
    }
  });
  // Deduplicate by selector
  const seen = new Set();
  return JSON.stringify(animated.filter(a => {
    if (seen.has(a.selector)) return false;
    seen.add(a.selector); return true;
  }).slice(0, 30));
})()"
```

Save output as `tmp/ref/<effect-name>/animated-elements.json`.

## Step 2: Bundle grep with class anchors

Use the class names from Step 1 as grep anchors to find animation code in JS bundles:

```bash
SCRIPTS="$(dirname "$0")/../scripts"

# Get all script URLs
agent-browser --session <s> eval "(() => {
  return JSON.stringify([...document.querySelectorAll('script[src]')].map(s => s.src));
})()"

# Download chunks (filters analytics/tracking automatically)
echo '<urls-json>' | bash "$SCRIPTS/download-chunks.sh" tmp/ref/<effect-name> -

# Grep for animation patterns using class anchors from Step 1
CLASSES="carousel program-service overlay card-flip"  # from animated-elements.json
for cls in $CLASSES; do
  echo "=== $cls ==="
  grep -n "$cls" tmp/ref/<effect-name>/bundles/*.js | head -5
  # Extract 500-char context around each match
  grep -oP ".{0,200}${cls}.{0,300}" tmp/ref/<effect-name>/bundles/*.js | head -3
done
```

### What to extract per animated element

| Property | Where to find | Example |
|---|---|---|
| `from` / `to` values | `gsap.fromTo(el, {opacity:0}, {opacity:1})` | `{opacity: 0} → {opacity: 1}` |
| `duration` | `duration: 1` or 3rd arg | `1000ms` |
| `ease` | `ease: "power3.out"` | Use `gsap-to-css.sh` to convert |
| `delay` | `delay: 0.5` | `500ms` |
| `trigger` | `ScrollTrigger.create({trigger: el})` | scroll position |
| `repeat` / `yoyo` | `repeat: -1, yoyo: true` | carousel auto-rotation |
| `stagger` (number) | `stagger: 0.1` | `100ms` PER-target step (last child = base + (N-1)×100ms) |
| `stagger.amount` | `stagger: { amount: 0.1 }` | `100ms` TOTAL spread distributed across N targets → per-target step = `amount / (N-1)`. Last child completes at `base + amount`, NOT `base + N×amount`. **Mistaking total-spread for per-target pushes late targets past timeline end → they never reveal.** |
| `stagger.each` | `stagger: { each: 0.1 }` | Same as number shorthand — per-target step |
| IX3 ease values | `ease: 0` / `ease: 1` | `0` = LINEAR (NOT easeIn). `1` = easeIn (t²). Check `easeMode` field too |

### Auto-rotation detection patterns

```bash
# Carousel/slider auto-rotation
grep -E '(repeat:\s*-1|setInterval|autoplay|auto.?rotate|auto.?slide)' tmp/ref/<effect-name>/bundles/*.js | head -10

# Timer-based with class swapping
grep -E '(classList\.(add|remove|toggle).*program|classList\.(add|remove|toggle).*slide)' tmp/ref/<effect-name>/bundles/*.js | head -10

# GSAP timeline with repeat
grep -E 'timeline.*repeat|\.repeat\(\s*-1\s*\)' tmp/ref/<effect-name>/bundles/*.js | head -10
```

## Step 3: Build verification spec

Save extracted values as `tmp/ref/<effect-name>/transition-spec.json`:

```json
{
  "animations": [
    {
      "selector": ".carousel",
      "type": "auto-rotation",
      "mechanism": "setInterval + classList swap",
      "interval": 4000,
      "variants": ["program1", "program2", "program3", "program4"],
      "note": "Cannot screenshot-verify — use bundle comparison only"
    },
    {
      "selector": "#program-services .card",
      "type": "scroll-driven-flip",
      "mechanism": "GSAP ScrollTrigger",
      "from": { "rotateY": -180 },
      "to": { "rotateY": 0 },
      "trigger": "top top-=100vh",
      "duration": 1,
      "ease": "back.inOut(1.5)"
    },
    {
      "selector": ".program-service_face",
      "type": "scroll-driven-slide",
      "mechanism": "GSAP ScrollTrigger scrub",
      "from": { "y": "0%" },
      "to": { "y": "-100%" },
      "scrub": true
    }
  ]
}
```

## Step 4: Compare spec vs implementation

For each entry in `transition-spec.json`, verify the impl matches:

### 4a. Carousel / auto-rotation

```
Spec says: setInterval(fn, 4000) + classList swap between 4 variants
Impl check:
  ✅ Has setInterval with ~4000ms? (or intentionally removed for static clone)
  ✅ Has same variant class names?
  ✅ Initial variant matches ref's default?
```

### 4b. Scroll-driven (GSAP ScrollTrigger / Motion useTransform)

```
Spec says: rotateY -180→0, trigger "top top-=100vh", back.inOut(1.5), duration 1s
Impl check:
  ✅ Same from/to values?
  ✅ Same trigger point?
  ✅ Same easing? (use gsap-to-css.sh to verify cubic-bezier equivalence)
  ✅ Same duration?
```

### 4c. Numerical diff output

```json
{
  "selector": "#program-services .card",
  "checks": [
    { "property": "from.rotateY", "spec": -180, "impl": -180, "match": true },
    { "property": "to.rotateY", "spec": 0, "impl": 0, "match": true },
    { "property": "ease", "spec": "back.inOut(1.5)", "impl": "backInOut(1.5)", "match": true },
    { "property": "duration", "spec": 1000, "impl": 1000, "match": true },
    { "property": "trigger", "spec": "-1vh", "impl": "-1vh", "match": true }
  ],
  "status": "pass"
}
```

Save as `tmp/ref/<effect-name>/bundle-verification.json`.

## Step 5: Resting-state screenshot (sanity check only)

After bundle verification passes, take ONE screenshot of the idle/resting state:

```bash
# Freeze all auto-rotation before screenshot
agent-browser --session <s> eval "(() => {
  // Block future carousel intervals
  const orig = window.setInterval;
  window.setInterval = function(fn, ms) {
    if (typeof ms === 'number' && ms >= 2000) return -1;
    return orig.apply(window, arguments);
  };
  // Freeze carousel classList mutations
  document.querySelectorAll('section[class*=carousel], [class*=slider], [class*=swiper]').forEach(el => {
    el.classList.add = function() {};
    el.classList.remove = function() {};
  });
  return 'frozen for screenshot';
})()"

agent-browser --session <s> screenshot tmp/ref/<effect-name>/resting-state-impl.png
```

Compare against a single ref resting-state screenshot. This is a **sanity check** — it verifies layout/colors but does NOT validate animation timing or transitions.

## Gate

```
✅ transition-spec.json exists with all animated elements documented
✅ bundle-verification.json exists with all checks "match": true
✅ Resting-state screenshot taken (sanity check — not a pass/fail gate)
```

**If bundle-verification fails** → fix impl code to match spec values → re-run Step 4.
**If resting-state screenshot looks wrong** → re-download bundles (site may have updated) → re-run from Step 2.
