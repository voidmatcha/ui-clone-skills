# Canvas-replay closeout mode (v0.7.0)

> **Status: shipped in v0.7.0**. Operator-driven opt-in path for refs whose
> visual identity is canvas-driven (WebGL UnicornStudio scenes, generative
> scroll-driven plates, custom shader walls). Not automatic — must be
> explicitly enabled per ref dir.

---

## Automatic canvas-replay routing (the AUTO fallback)

> The sections below ("Problem this solves" onward) describe the **manual
> closeout policy** — operator attestation that releases the Stop hook. That
> path still exists for license/permission sign-off. What follows here is the
> **automatic technical path**: detection → capture → generation routing,
> which runs without an operator deciding anything. The only thing that stays
> manual is the license attestation (`canvas-replay-attestation.json`); the
> engineering — deciding a hero is unreproducible, recording the reference's
> own motion, and emitting the replay — is now automatic.

When a hosted WebGL/canvas hero **cannot be re-embedded** in the clone, the
pipeline auto-reproduces it by recording the **reference's own rendered canvas
output** to a short looped video and emitting a `<video>` replay — instead of
shipping a blank hero. Honesty-first: this is the ref's own pixels in motion
(like recording a video background), **declared** as a substituted asset.

### 1. Detection — `scripts/extract/canvas-replay-plan.sh`

Routes a hero/section to canvas-replay when **both** hold:

- The ref is canvas-driven: `canvas-webgl-detection.json` shows
  `primaryRenderType: webgl|canvas` or `canvasCount > 0`.
- A live re-embed is impossible, via either trigger:
  - **`origin-lock`** — the canvas-driving scene src is served from a
    ref-bound CDN (`*.b-cdn.net`, `*.website-files.com`, `*.webflow.io`) or a
    cross-origin fetch of the scene returns a blocking status (403/401/CORS).
  - **`blank`** — the impl renders **0 canvases** where the ref renders a
    WebGL/canvas surface (SDK not bundled / init failed).

The decision is deterministic and unit-tested in
`ui_clone/policies/canvas_replay_auto.py`
(`needs_canvas_replay`, `reembed_blocked_from_status`, `build_replay_plan`).
It writes **`canvas-replay-plan.json`**:

```json
{
  "schemaVersion": 1,
  "decision": "canvas-replay",
  "reason": "origin-lock",
  "url": "https://example.com/",
  "sections": [
    {
      "section": "sec-2",
      "refCanvasSelector": "canvas",
      "region": { "x": 0, "y": 0, "width": 1440, "height": 900 },
      "reason": "origin-lock",
      "replayAsset": "public/canvas-replay/hero.webm",
      "poster": "public/canvas-replay/hero-poster.png"
    }
  ]
}
```

It also merges a `canvasReplay[]` declaration into `asset-substitution.json`
so **anti-cheat understands the asset**: the replay is the ref's OWN recorded
motion, a declared substituted asset — NOT a static screenshot used as a CSS
background. (`ref-screenshot-asset` anti-cheat targets static section
screenshots; a moving, declared hero video is a different, legitimate asset.)

### 1b. Interactive-physics canvas → behavioral-repro, NOT video replay

Video replay is correct for a **decorative** canvas (WebGL shader, Spline
scene, generative plate): its identity is its pixels, and the ref's own
recorded motion reproduces it faithfully. It is **wrong** for an
**interactive-physics** canvas — matter.js / verlet / planck / p2 drop-in
letters, falling bodies, cloth — whose identity is the *running simulation*
that spawns, drops and appends bodies (often on interaction). A recorded loop
cannot respond or append, so it ships dead motion.

`canvas-webgl-detect.sh` positively detects a physics engine (runtime global
`window.Matter`/`planck`/`p2`/`Box2D`, else a bundle-script signature) and
stamps the detection artifact:

```json
{ "renderKind": "interactive-physics", "hasPhysics": true,
  "physicsEngine": { "name": "matter-js", "version": "0.20.0",
                     "source": "runtime-global", "liveEngine": null } }
```

When `hasPhysics` is set, `build_replay_plan` short-circuits to
`decision: "behavioral-repro"` (reason `interactive-physics`) instead of
`canvas-replay`, and `generation-plan.json` → `canvas.physics.required` carries
the engine + constants. Generation MUST then **rebuild the simulation with the
same engine** (bundle the library; use `physicsEngine.liveEngine.gravity` when
captured, else the library defaults plus any bundle-grep constants), producing
a **live canvas that actually runs** — not a `<video>`. Because physics is
non-deterministic (spawn randomness, frame timing), the runtime verdict is an
honest **`unmeasurable`** on exact frames; the enforced bar is that the impl
renders a running, responding canvas. A behavioral-repro plan deliberately does
**not** get the blank-hero video relief (`replay_satisfies_blank_hero`), so a
blank impl still fails `runtime-frame-proof` — the physics has to run.

A decorative shader/Spline canvas has **no** physics engine, so `hasPhysics`
stays false and it keeps the video-replay route below unchanged.

### 2. Capture — `scripts/extract/canvas-replay-capture.sh`

Records the reference's hero canvas region against the **live ref URL** via
`agent-browser record start/stop` (WebM), then crops to the hero region and
re-encodes with `ffmpeg` to a web-friendly looped `hero.webm` (+ `hero.mp4`
fallback) and a `hero-poster.png`. Writes to `<ref-dir>/static/canvas-replay/`
and mirrors to the impl `public/canvas-replay/`.

### 3. Generation routing — emit the `<video>` replay

For each `canvas-replay-plan.json` section with `decision: "canvas-replay"`,
generation emits a replay surface **instead of** a re-embed that 403s:

```jsx
<video
  className="hero-canvas-replay"
  autoPlay loop muted playsInline
  poster="/canvas-replay/hero-poster.png"
>
  <source src="/canvas-replay/hero.webm" type="video/webm" />
  <source src="/canvas-replay/hero.mp4" type="video/mp4" />
</video>
```

`autoPlay loop muted playsInline` is required for the video to play on load
without user interaction (muted is mandatory for autoplay).

### 4. Gate coherence

A `<video>` replay that plays (currentTime advances, non-blank pixels)
**satisfies** the non-blank-hero gates:

- **`runtime-frame-proof-check.sh`** (Fix 39) samples `<video>` currentTime
  before/after. When `canvas-replay-plan.json` declares the section AND a
  `<video>` advanced, the 0-canvas blank-hero fail becomes a **pass**
  (`replay_satisfies_blank_hero`). A bare `<video>` with no declared plan, or
  a stalled video, does NOT silence the gate.
- **`hero-composite-check.sh`** (Fix 2a) treats the declared `<video>` replay
  as substituting the ref's `<canvas>` kind, so the missing-`canvas` kind no
  longer fails the composite (`canvasReplaySubstituted`).

### Scope of what is automated vs. still manual

| Step | Automated? |
| --- | --- |
| Detect origin-lock / blank | ✅ `canvas-replay-plan.sh` + tested policy |
| Record ref canvas → video | ✅ `canvas-replay-capture.sh` |
| Declare substituted asset | ✅ merged into `asset-substitution.json` |
| Emit `<video>` replay in generation | ✅ per-trigger pattern above |
| Gate coherence (frame-proof / hero-composite) | ✅ |
| **License / permission attestation** | ❌ **stays manual** — see below |

The automatic path NEVER applies to static, reproducible sections — it only
fires for genuinely-unreproducible WebGL/canvas heroes where a re-embed is
impossible. Static sections still use real reproduction; no AE/SSIM threshold
is loosened by the auto path.

---

## Problem this solves

The default canonical closeout policy requires `section-compare` to pass —
pixel-exact AE/SSIM diff between ref and impl screenshots. Sites whose
visual identity is driven by imperative `<canvas>` drawing (custom canvas
arcs, exported WebGL scenes, or similar generated plates) fail this gate by
design: CSS approximation of canvas output is approximate, AE/SSIM is
bit-exact. Past iterations confirmed AE/Mpx can stay saturated regardless of
CSS-only fix attempts.

Current default fallback is the 30-min canvas CSS replication hard cap →
`record_unclonable(category="hard-cap-fail")`. The clone is shelved as
"structurally complete, visually unclonable." Honest, but loses production
value when the user genuinely wants the canvas identity preserved AND
the canvas source has a permissive license OR explicit owner permission.

Canvas-replay mode is the escape hatch for that case.

## What canvas-replay mode does

When enabled, the operator loads the reference site's canvas-driving JS
at runtime in their impl (sandboxed iframe + postMessage scroll relay is
the recommended pattern). The Stop hook releases via
`canvas-replay-stamp.json` — written by
`scripts/verify/check-canvas-replay.sh` after validating an
operator-written `canvas-replay-attestation.json`. The canonical
`verify-stamp.json` and structural `structural-convergence-stamp.json`
remain unaffected; canvas-replay is a third, distinct closeout path.

## Scope boundary (READ THIS FIRST)

Canvas-replay is **canvas pixel-fidelity ONLY**. Explicitly OUT of scope:

- **WebAudio output**: audio synthesis / playback / visualizers. WebAudio
  may be an *input dependency* feeding canvas pixels, but audio output
  fidelity is not covered.
- **Video replay**: `<video>` element content. Use standard CDN asset
  embedding or the canonical pipeline.
- **DOM replay**: loading arbitrary ref DOM at runtime. The clone must
  still produce its own DOM tree; canvas-replay only allows loading
  canvas-driving JS for the canvas region.
- **Non-canvas asset bypasses**: text fidelity, font parity, runtime DOM
  parity, transition compare — these gates stay strict. Canvas-replay
  does NOT loosen them.

`check-canvas-replay.sh` actively rejects attestations whose
`ref_canvas_sources[]` contain URLs ending in `.mp3` / `.wav` / `.ogg` /
`.mp4` / `.webm` / `.mov` / `.m3u8` / `.mpd` — surface for accidental
scope creep.

## Operator workflow

### Step 1 — Decide if canvas-replay is the right policy

Check the failure pattern. Canvas-replay is appropriate when:

- `section-compare` fails ONLY on canvas-tagged sections (typically the
  hero or one feature region). Non-canvas sections pass.
- AE/Mpx is high regardless of fix attempts (~60k+) — confirms pixel
  approximation is the structural blocker, not impl bugs.
- The ref's canvas source has a permissive license OR you've obtained
  explicit owner permission.

Canvas-replay is NOT appropriate when:

- The site fails non-canvas gates too (text fidelity, font parity, DOM
  parity, transitions) — fix those first; canvas-replay won't help.
- The ref's canvas source is unlicensed AND no permission obtained.
- The ref's canvas section is decorative-only — use CSS @property +
  `animation-timeline` for scroll-driven motion (GPU-cheap, no canvas).

### Step 2 — Write the attestation

Create `<ref-dir>/canvas-replay-attestation.json` with all five fields:

```json
{
  "license": "<URL or text of the source's license / explicit owner permission>",
  "disclaimer": "Not affiliated with <site-name>. <ref-url> canvas assets loaded for fidelity per opt-in.",
  "attestedBy": "<operator-handle>",
  "attestedAt": "2026-05-25T08:00:00Z",
  "ref_canvas_sources": [
    "https://example.test/assets/canvas-driver.js"
  ]
}
```

- **`license`**: URL of the source's license file OR text quoting the
  permission you obtained (email excerpt, project README license clause,
  etc.). This is honor-system — falsified attestations are an ethical
  issue, no engineering mitigation possible.
- **`disclaimer`**: Operator-decided wording. Recommended pattern is the
  non-affiliation statement above. Where to display it (footer link,
  in-canvas overlay, README) is operator's choice — `check-canvas-replay.sh`
  does NOT enforce placement.
- **`ref_canvas_sources[]`**: Non-empty array of URLs of the canvas-driving
  JS bundles the impl loads at runtime. Each URL is recorded in the
  stamp for audit.

### Step 3 — Set the closeout policy

Edit `<ref-dir>/pipeline-state.json`:

```json
{
  "...": "...",
  "closeoutPolicy": "canvas-replay"
}
```

### Step 4 — Write the stamp

```bash
bash scripts/verify/check-canvas-replay.sh <ref-dir> --write-stamp
```

The script:
1. Validates attestation shape (all 5 required fields, non-empty
   `ref_canvas_sources[]`).
2. Rejects out-of-scope sources (audio/video URLs).
3. Computes `sha256(attestation file)` and writes `canvas-replay-stamp.json`
   carrying the hash + the URLs + the operator handle.

Now the Stop hook accepts this run as complete.

## Tamper detection

After the stamp is written, any of these invalidates it:

- Editing `canvas-replay-attestation.json` (sha256 mismatch → block).
- Editing impl source files newer than the stamp (impl-freshness check,
  mirrors canonical/structural).
- Stamp older than 30 minutes (staleness window, mirrors canonical/structural).
- `canvas-replay-attestation.json` deleted (gate explicitly checks the
  attestation still exists alongside the stamp).

For any of these, re-run `check-canvas-replay.sh --write-stamp`.

## Sandboxing recommendation (NOT enforced, but strongly advised)

The impl should load `ref_canvas_sources[]` JS inside a sandboxed iframe
(`sandbox="allow-scripts"` + `data:` or `blob:` origin) and relay scroll
position via postMessage:

```jsx
function CanvasReplay({ src }) {
  const iframeRef = useRef(null);
  useEffect(() => {
    const onScroll = () => {
      iframeRef.current?.contentWindow?.postMessage(
        { type: "scroll", scrollY: window.scrollY }, "*"
      );
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  return (
    <iframe
      ref={iframeRef}
      sandbox="allow-scripts"
      src={`data:text/html,<script src='${src}'></script>`}
      className="w-full h-full"
    />
  );
}
```

Inline loading (no sandbox) is **technically** allowed by the stamp but
weakens the very boundary canvas-replay claims to preserve. Document
inline loading explicitly in the disclaimer if you must use it (some ref
bundles synchronously read DOM globals — sandbox can break compatibility).

## What canvas-replay does NOT relax (v0.7.0)

`section-compare` AE/SSIM thresholds and `ref-js-loader` / `ref-screenshot-asset`
gates still apply with their canonical strictness. The v0.7.0 release
ships the closeout-policy plumbing + Stop hook routing; gate-side relief
for `kind: "canvas"` sections in `section-map.json` is a follow-up
commit. Until that lands, operators using canvas-replay still need to
either (a) get their section-compare to pass on canvas sections via
sufficient approximation, or (b) accept that the natural pipeline blocks
at section-compare and the canvas-replay stamp is reached via manual
intervention (operator decides the impl is good enough and writes the
attestation knowingly).

## Related design docs

- `docs/canvas-replay-mode-design.md` — original design and review notes
- `docs/multi-snapshot-capture-design.md` — splash/scroll/hover DOM state
  capture that complements canvas-replay (capture phases run before
  closeout policy matters)
