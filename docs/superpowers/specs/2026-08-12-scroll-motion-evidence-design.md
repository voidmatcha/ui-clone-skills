# Scroll Motion Evidence Hardening

## Problem

The Realfood reference contains substantial scroll-linked motion, but the current extraction pipeline can silently convert a failed runtime capture into a valid-looking empty artifact. The extraction script performs an expensive full-DOM, multi-pass scroll sweep. When `agent-browser` times out or returns no stdout, the shell wrapper suppresses the failure and writes `animation-runtime-dump.json` with null fields and `note: "eval returned empty"`. Downstream planning then treats missing runtime evidence as absence of motion, while hand-authored component wires can still describe ungrounded animation behavior.

This creates three coupled failure modes:

1. Capture failure and a genuinely static page are indistinguishable.
2. Runtime-discovered scroll sites are not required to appear in the transition specification.
3. Motion implementation guidance can be emitted as unstructured prose without a traceable source artifact.

## Goal

Make scroll-motion extraction fail closed and evidence-backed: a motion-rich page cannot advance with a failed runtime capture, every captured scroll-linked site must be accounted for, and every generated motion wire must identify the artifact evidence that justifies it.

## Design

### 1. Bounded, engine-aware runtime capture

`extract-animation-runtime.js` will use a bounded sampler instead of repeatedly scanning every DOM node:

- Detect a supported scroll engine first (`Lenis`, `ScrollSmoother`, then native scrolling).
- Sample a fixed initial set of normalized positions.
- Observe inline-style candidates and previously changed nodes rather than rescanning all elements on every poll.
- Add only a bounded number of adaptive midpoint samples where values changed.
- Perform a bounded reverse sweep to detect direction-dependent behavior.
- Record the requested and observed position for each sample in `scrollAudit`.
- Include `filter` in observed style channels so blur-driven transitions are not discarded.

The artifact receives an explicit contract:

```json
{
  "captureStatus": "ok",
  "captureError": null,
  "scrollAudit": {
    "engine": "lenis",
    "maxScroll": 18573,
    "samples": [
      {"requested": 0.25, "observed": 0.2498, "method": "lenis.scrollTo"}
    ]
  }
}
```

An empty response, invalid JSON, browser command failure, or a long page whose observed scroll position does not move becomes `captureStatus: "error"` with a structured `captureError`. The wrapper exits non-zero after writing the diagnostic artifact, allowing pipeline orchestration to remain nonfatal while preserving the reason for later gates.

### 2. Fail-closed specification coverage

The specification gate will reject an explicit runtime capture error on a motion-rich reference. Legacy artifacts that say `eval returned empty` are also treated as failed captures. Static pages remain valid when their measured maximum scroll is zero.

Successful runtime rows receive a stable `sourceId`. Every captured scroll-linked site must then be represented by either:

- a transition-spec entry grounded to that `sourceId`, or
- a structured skipped entry with a reason.

This distinguishes “looked and found no relevant implementation target” from “never looked.”

### 3. Grounded generation-plan wires

Motion-like `libraryWires` become structured objects:

```json
{
  "kind": "scroll-motion",
  "library": "framer-motion",
  "hooks": ["useScroll", "useTransform"],
  "trigger": "scroll",
  "selector": ".broken-system__image",
  "sourceArtifact": "animation-runtime-dump.json",
  "sourceId": ".broken-system__image::0"
}
```

The pre-generation gate rejects motion-like prose wires and rejects structured wires whose artifact or source identifier cannot be resolved. Non-motion string wires remain supported for backward compatibility.

`animation-runtime-dump.json` becomes a canonical generation-plan provenance input, so a fresh runtime capture automatically stales any plan derived from an older capture.

### 4. Documentation and replay contract

The reverse-engineering skill will explicitly require:

- rerunning a failed runtime capture instead of interpreting it as no motion,
- mapping runtime sites to transitions or structured skipped reasons,
- emitting structured, source-grounded motion wires.

Filter blur values will be retained in the generated scroll scrub data and replayed by the scroll-linked style driver when the runtime series is a supported numeric `blur(px)` sequence or exact stable `blur(px) brightness(number)` sequence. Unsupported order-changing, extra-function, negative, nonfinite, or mixed compound filters remain captured as evidence but are not fabricated into an interpolator.

The runtime dump remains one `sourceId` per observed row. The deterministic planner may collapse repeated non-latched runtime rows that share the same selector, progress keys, and measured curve into a generation-plan site with `replay: "all-matches"` while preserving every contributing `sourceIds[]` value. Mixed repeated rows stay selector-indexed so per-element timing is not flattened.

## Compatibility

- Existing legacy runtime artifacts without `captureStatus` remain readable unless they carry a known failure marker.
- Existing non-motion string wires remain valid.
- Pipeline execution may continue after capture failure, but the specification gate blocks advancement on motion-rich references.
- No new dependency is introduced.

## Verification

Verification proceeds from narrow to live:

1. Unit tests lock the wrapper error artifact, bounded sampler markers, capture integrity gate, runtime-site coverage, grounded motion wires, provenance, and blur replay.
2. Targeted pytest modules run green.
3. Local CI and security checks run after integration.
4. The Realfood reference is recaptured; success requires a bounded runtime capture with observed scroll movement and non-empty accounted scroll sites. Canonical clone completion is claimed only if the normal project gates also pass.

## Stop Condition

Stop when the new contracts are regression-tested, the relevant skill documentation matches them, and a fresh Realfood run either produces valid accounted runtime evidence or leaves an explicit, honest gate failure with its diagnostic cause. Do not represent build success or a preview URL as clone completion.
