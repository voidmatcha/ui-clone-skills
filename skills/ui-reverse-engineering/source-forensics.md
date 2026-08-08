# Source Forensics

**Audience**: host-neutral delegated workers that inspect large reference HTML, CSS, and JavaScript sources when distilled extraction artifacts are not enough.

- **Claude Code path**: invoked via the `source-forensics` sub-agent (`.claude-plugin/agents/source-forensics.md`).
- **Codex native path**: invoked via the `source-forensics` native subagent (`.codex/agents/source-forensics.toml`) when Codex/OMX subagent routing is available.
- **Inline fallback**: allowed only when the host has no delegated-worker surface. State the fallback explicitly and keep reads bounded by line ranges or grep anchors.

## Purpose

Keep large raw sources out of the main clone context. The main agent should work from compact artifacts first (`generation-plan.json`, `transition-spec.json`, `state-structure-spec.json`, `bundle-map.json`, `scroll-engine.json`, `section-map.json`, `dom-scaffold.json`, `styles.json`, and the evidence pack). When those artifacts cannot explain a persistent visual or motion failure, this worker reads the raw sources in an isolated context and writes a compact `source-forensics.json` report for downstream fixes.

## Trigger conditions

Invoke this worker before the main agent reads raw `bundles/*.js`, large `css/*.css`, or captured HTML dumps when any of the following are true:

- two scoped visual-fix iterations show no meaningful AE reduction on the same section;
- `transition-spec-coverage` passes but `transition-fires`, `transition-proof`, scroll-scrub, sticky/pin, hover, or timer behavior still fails at runtime;
- `forensicPreservation` / CSS token coverage reports missing source-backed selectors, variables, pseudo-elements, backgrounds, masks, clip paths, or keyframes;
- section diff remains high after text, visible assets, and DOM order have been corrected;
- a distilled artifact says `unknown`, `empty`, or `not detected`, but gates or screenshots prove motion, sticky behavior, media runtime, or source-specific styling exists;
- the fix requires knowing whether the source used a CSS rule, pseudo-element, inline style, data attribute, script state machine, or third-party runtime.

## Inputs, in order

Read compact artifacts before raw sources:

1. `tmp/ref/<component>/brief/WORKER_BRIEF.md` or `tmp/ref/<component>/evidence-pack.json`, when present.
2. Failure sidecars: `sections/result.txt`, `sections/matches.json`, `tree-diff.json`, `tree-diff-status.json`, `transitions/**`, `transition-proof.json`, `transition-fires.json`, `svg-dom-parity.json`, `runtime-dom-parity.json`, and related gate output.
3. Distilled artifacts: `generation-plan.json`, `transition-spec.json`, `state-structure-spec.json`, `bundle-map.json`, `bundle-extraction.json`, `scroll-engine.json`, `section-map.json`, `dom-scaffold.json`, `styles.json`, `external-sdks.json`, `asset-manifest.json`.
4. Raw fallback files, only inside this delegated worker: `tmp/ref/<component>/bundles/*.js`, `tmp/ref/<component>/css/*.css`, captured HTML/DOM dumps, and other downloaded source snapshots.

## Method

1. Scope the investigation to one failing section, selector cluster, or transition question. Do not summarize the entire site.
2. Build a search plan from the compact artifacts: class names, text snippets, asset basenames, data attributes, CSS variables, animation names, and library identifiers.
3. Use `grep`, `ripgrep`, or line-bounded reads. Never paste whole bundles, full CSS files, full DOM/style JSON, or screenshot-derived blobs into the main context.
4. Cite every fact with file path plus line number, byte offset, or stable grep anchor.
5. Prefer source facts over guesses: if evidence is ambiguous, record the competing interpretations and the next artifact that would disambiguate them.
6. Do not edit implementation source. This worker produces evidence and implementation guidance only.
7. Do not execute untrusted reference JavaScript. Static reads and safe text extraction are enough.

## Output

Write `tmp/ref/<component>/source-forensics.json` with this schema:

```json
{
  "schemaVersion": 1,
  "topic": "section-or-transition-name",
  "triggeringFailure": "short reason this worker was invoked",
  "facts": [
    {
      "claim": "source-backed fact",
      "source": "tmp/ref/<component>/css/app.css:123",
      "confidence": "high"
    }
  ],
  "implementationGuidance": [
    {
      "target": "impl file / component / selector if known",
      "change": "source-backed guidance, not a patch diff",
      "reason": "why this should reduce AE or fix runtime proof"
    }
  ],
  "unresolved": [
    {
      "question": "remaining ambiguity",
      "neededEvidence": "specific artifact or gate output"
    }
  ],
  "sourceFilesRead": [
    "tmp/ref/<component>/bundles/app.js",
    "tmp/ref/<component>/css/app.css"
  ]
}
```

Optionally also write `tmp/ref/<component>/brief/source-forensics-<slug>.md` for human-readable handoff. The main agent should read only `source-forensics.json` and the optional brief, then apply a scoped implementation fix and re-run the failing gate.

## Non-goals

- Do not replace `bundle-analyzer`. `bundle-extraction.json` is produced deterministically by the Phase-2 driver (`scripts/extract/bundle-extraction.sh`); dispatch that worker only for the gaps the parser flags in `bundle-extraction.json` `unresolved[]` (Swiper/Splide/Lottie, multi-file state machines, symbolic transforms), merging into the artifact rather than re-deriving the deterministic extractions.
- Do not replace `generation-planner`; use that worker when the source facts need to update `generation-plan.json` semantics.
- Do not replace `visual-debug-iterator`; use that worker for scoped implementation edits after source facts are already compact.
- Do not copy source JS/CSS wholesale into the implementation. Preserve behavior and visible values, not bundled code structure.
