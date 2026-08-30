# Scroll-scrub and splash defects are one wiring gap, not an extraction gap

Measured on one benchmark component's ref directory (2026-08-22). The run stopped
INCOMPLETE — no `verify-stamp.json` — but the motion gates had already produced
results, and they agree on a single root cause.

## What the gates measured

| artifact | status |
| --- | --- |
| `transition-spec-coverage.json` | pass |
| `scroll-engine-parity.json` | pass |
| `scroll-state-machine.json` | pass |
| `scroll-completion.json` | pass |
| `transition-fires.json` | **fail** — 40 total, 8 fired, 32 failed |
| `spec-implementation-coverage.json` | **fail** — withMotion 19, triggerStatic 20, missingEntirely 1 |
| `splash-lifecycle.json` | **fail** — `impl-overlay-absent`, `impl-duration-ratio-mismatch`, `impl-coverage-too-low` |
| `scroll-coverage.json` | **fail** |
| `transition-proof.json` | **fail** (4 reasons, all downstream of the above) |

All 32 failures collapse into two observed strings:

- 30x `engine-driven re-probe advanced the page (scrollY moved) but transform/opacity stayed flat — scrub is dead, not unmeasurable`
- 2x `style change on hover=False`

Split by kind: 24 `scrub`/`scroll-state-machine`, 6 `scrub`/`scroll`, 2 `hover`.

## Where it breaks

The pipeline has three stages here, and only the third is broken.

1. **Extraction — works.** `transition-spec.json` carries all 40 transitions with
   real values. Example `scroll-n0`: target an opaque-hashed nav class,
   framer-motion `scroll-latched-state`, `latched: true`, and a full
   `keyframesByProgress` table (`translateY(-200px)` at 0.0, `-100px` at 0.0125,
   `-101.866px` at 0.05, `0.189606px` at 0.0875, …) traced to
   a hashed bundle chunk.
2. **Helper emission — works.** `emit-scroll-helpers.sh` deterministically wrote
   `lib/ScrollScrub.tsx`, `lib/ScrollReveal.tsx`, and `lib/scrollScrubSites.ts`
   with the ref's real offset windows and `useTransform` input/output bands.
3. **Wiring — missing entirely.** Of 41 `.tsx` components, **zero** import
   `ScrollScrub`; only 5 use `motion.` at all. `useTransform`/`scrollYProgress`
   appear only inside `lib/` itself, where the helpers reference each other.
   `grep translateY` over the generated source returns nothing, though the spec
   holds a full translateY keyframe table.

`SiteHeader.tsx` is the clearest case. It imports `motion`, renders
`<motion.nav>`, and calls `useScrollSpy()` — but binds no `style` to
`scrollYProgress`, so scroll advances while transform stays flat, exactly as
`transition-fires` reports.

`scrollScrubSites.ts` documents the step that never happened:

```
Each entry is spreadable into <ScrollScrub>.
Wrap the scrubbed element (e.g. a background that scales on scroll):
  <ScrollScrub {...scrollScrubSites[0]}>...</ScrollScrub>
```

The emitter is deterministic and did its job. Wrapping the element is the
generation step's job, and it is the step that is skipped.

## Why this looked like a recurring extraction bug

The generator emits the *presence* of an animation but not its *value change*.
That produces a signature that reads as "the skill was fixed but the issue
persists": every spec-side gate passes because the spec is complete, and only
the runtime gates fail. Splash is the same defect one step earlier — the ref
mounts a 1904 ms overlay (`presentSampleCount` 37) and the impl never mounts one
at all (`presentSampleCount` 0, `mounted: false`), so there is no element to
wire. Scrub (30), hover (2) and splash (1) are three views of one gap.

Fixing extraction cannot move these numbers. The fix belongs in generation:
components must consume the emitted helpers.

## Not established

- Whether `generation-plan.json` carries per-component wiring instructions that
  the generator ignored, or omits them so the generator never had the mapping.
  That distinction decides whether the fix is in the plan or in the prompt.
- Whether any earlier run ever produced a component that imports `ScrollScrub`.
  If none has, the wiring step may never have worked rather than having
  regressed.
- The 2 hover failures were not traced to source; they are grouped here by the
  shared "trigger present, value static" signature, not by inspection.
