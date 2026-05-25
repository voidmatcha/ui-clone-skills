# Canvas-replay closeout mode (v0.7.0)

> **Status: shipped in v0.7.0**. Operator-driven opt-in path for refs whose
> visual identity is canvas-driven (WebGL UnicornStudio scenes, generative
> scroll-driven plates, custom shader walls). Not automatic — must be
> explicitly enabled per ref dir.

## Problem this solves

The default canonical closeout policy requires `section-compare` to pass —
pixel-exact AE/SSIM diff between ref and impl screenshots. Sites whose
visual identity is driven by imperative `<canvas>` drawing (kayiseisagu's
`.bg-canvas` arcs, raviklaassens UnicornStudio scenes) fail this gate by
design: CSS approximation of canvas output is approximate, AE/SSIM is
bit-exact. Past iterations confirmed AE/Mpx stays 60k–440k regardless of
fix attempts.

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

- `docs/canvas-replay-mode-design.md` — original design + codex review notes
- `docs/multi-snapshot-capture-design.md` — splash/scroll/hover DOM state
  capture that complements canvas-replay (capture phases run before
  closeout policy matters)
