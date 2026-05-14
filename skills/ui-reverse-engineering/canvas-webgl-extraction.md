# Canvas/WebGL Extraction — Step T2c (also Step 0A / Step 6 fallback)

## Identify 3D Engine First

```bash
agent-browser --session <s> eval "
(() => {
  var resources = performance.getEntriesByType('resource').map(function(e) { return e.name; });
  return JSON.stringify({
    spline: resources.filter(function(u) { return u.indexOf('spline') > -1 || u.indexOf('.splinecode') > -1; }),
    rive: resources.filter(function(u) { return u.indexOf('.riv') > -1 || u.indexOf('rive') > -1; }),
    lottie: resources.filter(function(u) { return u.indexOf('lottie') > -1 || u.indexOf('bodymovin') > -1; }),
    draco: resources.filter(function(u) { return u.indexOf('draco') > -1 || u.indexOf('.wasm') > -1; }),
    glb: resources.filter(function(u) { return u.indexOf('.glb') > -1 || u.indexOf('.gltf') > -1; })
  }, null, 2);
})()
"
```

| Engine | Network signals | Bundle patterns |
|--------|----------------|-----------------|
| **Spline** | `.splinecode`, `draco_decoder.wasm` | `SplineRuntime`, OGL (`vertex:`, `fragment:`) |
| **Three.js** | `.glb/.gltf` files | `THREE.`, `PerspectiveCamera`, `WebGLRenderer` |
| **Rive** | `.riv` file | `RiveCanvas`, `rive-wasm` |
| **Lottie** | `.json` animation data | `lottie-web`, `bodymovin` |
| **Custom WebGL** | Canvas only | `getContext('webgl')`, raw GLSL strings |

If Spline/Rive/Lottie: **data-driven** (scene file controls the animation, not application code). Extract the scene URL, then investigate interactions.

## Rive / Spline / Lottie — Interactive Extraction

These engines use scene files that embed state machines, event listeners, and animation segments. The JS bundle wires them to DOM events. Extract both the **scene URL** and the **interaction wiring**.

### Step 1: Extract scene URL

```bash
# Rive — find .riv file URL
agent-browser --session <s> eval "
(() => {
  const resources = performance.getEntriesByType('resource');
  const riv = resources.filter(r => r.name.includes('.riv'));
  return JSON.stringify(riv.map(r => r.name));
})()"

# Spline — find .splinecode or spline CDN URL
agent-browser --session <s> eval "
(() => {
  const resources = performance.getEntriesByType('resource');
  const spline = resources.filter(r => r.name.includes('.splinecode') || r.name.includes('prod.spline'));
  return JSON.stringify(spline.map(r => r.name));
})()"

# Lottie — find animation JSON URL
agent-browser --session <s> eval "
(() => {
  const resources = performance.getEntriesByType('resource');
  const lottie = resources.filter(r => r.name.includes('lottie') || r.name.includes('bodymovin') || r.name.endsWith('.json'));
  return JSON.stringify(lottie.map(r => r.name));
})()"
```

### Step 2: Extract interaction wiring from JS bundle

Download bundles (see "Find and download JS bundles" below), then grep for interaction patterns:

```bash
# Rive state machine inputs
grep -E 'stateMachineInputs|SMIInput|SMIBool|SMITrigger|SMINumber|onStateChange|rive.*addEventListener' \
  tmp/ref/<effect-name>/bundles/*.js | head -30

# Spline interaction events
grep -E 'splineMouseDown|splineMouseUp|splineMouseHover|SplineEventName|onSplineEvent' \
  tmp/ref/<effect-name>/bundles/*.js | head -20

# Lottie playback config
grep -E 'lottie\.loadAnimation|autoplay|loop|setSpeed|playSegments|goToAndStop|addEventListener.*complete' \
  tmp/ref/<effect-name>/bundles/*.js | head -20
```

### Step 3: Capture interactive states

For each interaction found, capture reference frames of each state:

```bash
# Example: Rive hover state
agent-browser --session <s> screenshot tmp/ref/<effect-name>/frames/ref/rive-default.png
agent-browser --session <s> hover <canvas-or-wrapper-selector>
agent-browser --session <s> wait 800
agent-browser --session <s> screenshot tmp/ref/<effect-name>/frames/ref/rive-hover.png

# Example: Lottie click trigger
agent-browser --session <s> screenshot tmp/ref/<effect-name>/frames/ref/lottie-before.png
agent-browser --session <s> click <trigger-selector>
agent-browser --session <s> wait 2000
agent-browser --session <s> screenshot tmp/ref/<effect-name>/frames/ref/lottie-after.png
```

### Step 4: Document in extracted.json

```json
{
  "engine": "rive | spline | lottie",
  "sceneUrl": "https://cdn.example.com/animation.riv",
  "interactions": [
    {
      "type": "hover",
      "trigger": "mouseenter on canvas wrapper",
      "stateMachine": {
        "name": "State Machine 1",
        "inputs": [
          { "name": "isHovered", "type": "boolean", "defaultValue": false }
        ]
      },
      "states": ["default", "hover"],
      "transitions": "default → hover on mouseenter, hover → default on mouseleave"
    }
  ],
  "playback": {
    "autoplay": true,
    "loop": true,
    "speed": 1
  }
}
```

> **For non-interactive Rive/Spline/Lottie** (autoplay loop, no state machine): skip Steps 2-3 and document only the scene URL and playback config.

## Find and download JS bundles

```bash
# Find chunk URLs and download them to tmp/ref/<effect-name>/bundles/
URLS=$(agent-browser --session <s> eval "
(() => {
  var scripts = Array.from(document.querySelectorAll('script[src]'))
    .map(function(s) { return s.src; })
    .filter(function(u) {
      return u.indexOf('/_next/') > -1 ||
             u.indexOf('/chunks/') > -1 ||
             u.indexOf('/_nuxt/') > -1 ||
             u.indexOf('/assets/') > -1 ||
             u.indexOf('/build/') > -1 ||
             /\.[a-f0-9]{8,}\.js/.test(u);
    });
  return JSON.stringify(scripts);
})()" | jq -r '.[]')
mkdir -p tmp/ref/<effect-name>/bundles
failed=0
while IFS= read -r url; do
  [ -z "$url" ] && continue
  # Validate URL scheme — only allow https
  if ! [[ "$url" =~ ^https:// ]]; then
    echo "Skipping non-HTTPS URL: $url" >&2
    continue
  fi
  name=$(echo "$url" | grep -oE '[a-f0-9]{16}' | head -1)
  if [ -z "$name" ]; then
    # md5sum (Linux) or md5 (macOS)
    name=$(echo "$url" | (md5sum 2>/dev/null || md5) | awk '{print $1}' | cut -c1-16)
  fi
  if [ -z "$name" ]; then
    echo "Error: could not generate filename for $url" >&2
    failed=$(( failed + 1 ))
    continue
  fi
  if curl -s --max-time 30 --max-filesize 10485760 --fail --location \
    -o "tmp/ref/<effect-name>/bundles/${name}.js" \
    -- "$url"; then
    echo "Downloaded: $(basename "$url")" >&2
  else
    echo "Failed to download: $url" >&2
    failed=$(( failed + 1 ))
  fi
done <<< "$URLS"
[ "$failed" -gt 0 ] && echo "Warning: $failed downloads failed" >&2

# Sanitization check — scan downloaded bundles for suspicious patterns
for f in tmp/ref/<effect-name>/bundles/*.js; do
  [ -f "$f" ] || continue
  if grep -qiE 'eval\(atob|document\.cookie|\.cookie\s*=|fetch\(.*/exfil' "$f" 2>/dev/null; then
    echo "⚠️  Suspicious pattern in $(basename "$f") — review before analysis" >&2
  fi
done

# Find canvas-related chunks (read-only grep — never execute bundles locally)
for f in tmp/ref/<effect-name>/bundles/*.js; do
  [ -f "$f" ] || continue
  count=$(grep -cE 'getContext|createRadialGradient|requestAnimationFrame|Float32Array' "$f" 2>/dev/null)
  [ "$count" -gt 1 ] && echo "$(basename -- "$f"): $count matches"
done
```

> **Security reminder:** Bundle analysis is **read-only**. Never run downloaded bundles via `node`, `eval`, or any other execution method. Only use `grep` to extract patterns.

## Three.js / WebGL extraction checklist

All grep commands below use the saved bundle file. **Extract exact values.**

| Category | grep pattern | What to extract |
|----------|-------------|-----------------|
| Camera | `PerspectiveCamera\|fov\|camera\.position` | FOV, position xyz, near/far |
| Renderer | `WebGLRenderer\|antialias\|alpha\|pixelRatio` | antialias, alpha, dpr cap |
| Particles | `Float32Array\|BufferGeometry\|Points` | count, buffer attributes |
| Distribution | `Math\.cos\|Math\.sin\|Math\.random` | position formula (sphere/disk/cube) |
| Colors | `Color\|0x[0-9a-fA-F]{6}` | exact hex values |
| Material | `PointsMaterial\|ShaderMaterial\|depthWrite` | size, opacity, blending |
| Shaders | `vertexShader\|fragmentShader\|gl_Position` | full GLSL source |
| Animation | `requestAnimationFrame\|rotation\.\|time\s*\+=` | time increment, rotation speed |
| Mouse | `mousemove\|clientX\|mouse\.x` | mouse→uniform mapping |
| Responsive | `innerWidth\|matchMedia\|isMobile` | breakpoints, per-breakpoint values |
| Textures | `TextureLoader\|\.png\|\.webp` | download referenced textures |

### Common position distributions

| Shape | Formula pattern |
|-------|----------------|
| Sphere shell | `r * sin(phi) * cos(theta)` |
| Disk with depth | `R * cos(angle)`, `R * sin(angle)`, `randFloat(-d, d)` |
| Card shape | `(random - 0.5) * width`, `(random - 0.5) * height` |
| Fibonacci sphere | `acos(2 * random - 1)` for phi |

### Three.js common defaults

| Setting | Common values | Impact |
|---------|--------------|--------|
| `devicePixelRatio` | `Math.min(dpr, 2)` | Perf cap on retina |
| `sizeAttenuation` | true | Depth effect |
| `depthWrite` | false | Transparent blending |
| Point `size` | 0.015–0.02 | Depends on camera distance |
| Time increment | 0.003 | ~40s full rotation at 0.15 speed |
