# Generation plan enrichment

**Audience**: anyone (host-agnostic) performing Phase 6-pre enrichment of `generation-plan.json`.

- **Claude Code path**: invoked via the `generation-planner` sub-agent (`.claude-plugin/agents/generation-planner.md`). The sub-agent reads this file as its operational contract.
- **Codex native path**: invoked via the `generation-planner` native subagent (`.codex/agents/generation-planner.toml`) when Codex/OMX subagent routing is available. The native subagent reads this file as the same operational contract.
- **Inline fallback**: if a host has no delegated-worker surface, perform the same work in the main context and state that fallback explicitly.

## Pre-condition

`scripts/extract/generation-plan.sh` has already produced `tmp/ref/<component>/generation-plan.json` at `schemaVersion: 1`. The plan covers component list, library deps, sticky strategy, hidden state, mobile-swap, architecture booleans, smooth-scroll wrapper, intro animation. Your job is to enrich it to `schemaVersion: 2`.

## Inputs (already exist)

- `tmp/ref/<component>/generation-plan.json` — deterministic base
- `tmp/ref/<component>/structure.json` — full DOM tree
- `tmp/ref/<component>/styles.json` + `tmp/ref/<component>/css/variables.txt` — design tokens
- `tmp/ref/<component>/signature-effects-candidates.json` — deterministic signature-effect candidates
- `tmp/ref/<component>/animations-detected.json` — animation candidates
- `tmp/ref/<component>/animation-runtime-dump.json` — canonical runtime motion provenance for scroll-linked styles and runtime-only animation params
- `tmp/ref/<component>/states/scroll/trajectory.json` — optional/supporting scroll-state trajectory evidence
- `tmp/ref/<component>/transition-spec.json` — transitions
- `tmp/ref/<component>/element-roles.json`, `element-groups.json`, `layout-decisions.json`, `component-map.json` — Step 6c audit
- `tmp/ref/<component>/asset-substitution.json` + `font-parity.json` — substitution declarations
- `tmp/ref/<component>/bundle-extraction.json` — deterministic bundle-parameter extraction (`scripts/extract/bundle-extraction.sh`, produced by the Phase-2 driver; `bundle-analyzer` only merges the `unresolved[]` gaps at Phase 5d); read this for sticky-mechanism decisions

## Work — fill these gaps in the plan

### 1. Component grouping refinement

The deterministic plan lists each section as a separate component. Decide which sections share enough structure to belong inside an ds-components primitive (cards, accordions, list rows, badge groups, motion wrappers). Update `componentList` with a `dsComponent: "<name>"` field on entries that should consume a shared primitive, and add a top-level `dsComponentsRequired` array listing those primitives.

### 2. Token names (anti-hallucination contract)

Inspect `css/variables.txt` and `styles.json`. Extract semantic names for repeated color / spacing / radius / shadow / typography values. Output a top-level `tokens` object:

```json
"tokens": {
  "colors": {"ink": "#110000", "cream": "#fdfbee", ...},
  "spacing": {"section": "144px", "block": "48px", ...},
  "radius": {"card": "24px"},
  "shadows": {...},
  "typography": {"display": "Die Grotesk D 700 / 88px / 0.95", ...}
}
```

**Anti-hallucination contract**: every emitted value MUST literally appear in `css/variables.txt` or `styles.json`. Names are your judgment, values are not. Skip tokens with only one occurrence — they're not shared design system values.

### 3. Library wiring per-component

For each `componentList` entry, add `wires: []` with concrete implementation
wiring the impl should use. Non-motion wire strings remain allowed for ordinary
component wiring, but any motion-like wire must be structured and grounded in
forensic evidence. Do not emit motion-like strings such as `"useScroll"` or
`"gsap.timeline + ScrollTrigger"` because downstream implementation cannot trace
them to a captured row.

```json
{
  "name": "Hero",
  "matchedSection": "hero",
  "selector": "section.hero",
  "path": "components/Hero.tsx",
  "wires": [
    {
      "kind": "motion",
      "library": "framer-motion",
      "hooks": ["useScroll", "useTransform"],
      "trigger": "scroll",
      "selector": ".hero-media",
      "replay": "all-matches",
      "media": "(min-width: 581px)",
      "sourceArtifact": "animation-runtime-dump.json",
      "sourceId": "runtime-scroll-filter-001",
      "sourceIds": ["runtime-scroll-filter-001", "runtime-scroll-filter-002"]
    },
    "useEffect with setInterval"
  ]
}
```

Only attach wires the detected library supports — never invent a hook from a library that wasn't installed.
Runtime wire sourceId+selector must match the same `scrollLinkedStyles[]` row in
`animation-runtime-dump.json`. Motion wires may cite only gate-approved
forensic artifacts such as `animation-runtime-dump.json`,
`transition-spec.json`, `bundle-extraction.json`, `animations-detected.json`,
`scroll-engine.json`, `sticky-elements.json`, `scroll-state-machine.json`,
`signature-effects-candidates.json`, or `states/scroll/trajectory.json`.
Never cite `generation-plan.json`, `extracted.json`, or self-authored notes as
motion evidence.

When the deterministic planner has collapsed identical repeated non-latched
curves, carry `replay: "all-matches"` and the complete `sourceIds` list into
the motion wire. When repeated rows have mixed curves, mixed curves must stay selector-indexed so the implementation can address each matched element separately.

If `transition-spec.json` attaches a CSS-grounded `media` query to a runtime
scroll transition, preserve that exact string on the corresponding generation
plan wire by exact runtime `sourceId`. Do not guess media guards from capture
viewport width. Runtime replay must skip inactive media queries and restore
inline styles it previously wrote so the underlying responsive CSS can apply.
If one runtime `sourceId` has conflicting media guards, omit its replay instead
of converting a viewport-specific curve into an unguarded global curve.

### 4. Signature effects

Read `animations-detected.json` carefully. If you see per-character / staggered / scramble / dissolve / disintegrate / glyph-split patterns, name the effect explicitly in `signatureEffects[]`:

```json
"signatureEffects": [
  {"selector": ".hero_title__abc", "name": "DisintegratingText", "component": "components/ui/DisintegratingText.tsx", "library": "framer-motion"}
]
```

Common signatures:

- Per-letter stagger fade → `DisintegratingText` / `SplitText`
- Random-character cycling before reveal → `ScrambleText`
- Word-by-word reveal → `WordRevealText`
- Image dissolve via mask + blur → `MaskedDissolve`

### 5. Pin / scroll-snap mapping (uses bundle-extraction.json)

For each entry in `stickyStrategy`, decide:

- True CSS sticky → `mechanism: "css-sticky"` (mirror position+top+zIndex)
- GSAP ScrollTrigger.pin → `mechanism: "gsap-pin"` with start/end values copied from `bundle-extraction.json` (NOT just `bundle-map.json` which only reports detection, not parameter extraction)
- Scroll-snap parent → `mechanism: "scroll-snap"` (CSS `scroll-snap-type` on parent)

The deterministic plan defaults to `mirror-as-is`. Upgrade to `gsap-pin` only when `bundle-extraction.json` shows ScrollTrigger with concrete `pin: true` + `start`/`end` values.

## Output

Write the enriched plan back to the SAME path (`tmp/ref/<component>/generation-plan.json`). Preserve all fields from the deterministic base; only add or refine. Set `schemaVersion: 2` to indicate enrichment.

Refresh the top-level `provenance` receipt in the same write:

- Set `source` to exactly `"generation-planner"`.
- Set `generatedAt` to the current timezone-aware ISO-8601 timestamp.
- Keep `hashAlgorithm: "sha256"`.
- Recompute `sourceHashes` for every entry in the canonical `ui_clone.dag.GENERATION_PLAN_SOURCES` set. It covers the inputs above, every deterministic base-plan input, and a manifest hash for `css/*.css`. Record `null` only when an optional source does not exist.

Do not copy the deterministic base receipt unchanged. The `pre-generate` gate rejects a schema-v2 plan whose planner identity is missing, whose receipt is malformed, or whose recorded input bytes no longer match the current artifacts.

End with a one-line summary printed to stdout:

```
✓ enrichment: <N> components | <M> ds-components | <K> tokens | <S> signature effects | mechanisms: [<sticky-types>]
```

## Don'ts

- Don't run the pipeline / generate impl code yourself. Your output is JSON only.
- Don't invent fields not grounded in the detection artifacts. If the artifact is empty/missing, the corresponding plan section stays empty.
- Don't trim the deterministic base — refine, don't replace.
- Don't emit a `tokens.colors.<name>: <value>` where `<value>` cannot be found in `css/variables.txt` or `styles.json`. The `pre-generate` gate validates this.

## Anti-optimization rule (HARD)

Never omit, downgrade, or coarsen a detected feature in the plan to avoid a downstream verification step. Specifically forbidden:
- Removing a sticky / scroll-driven element from the plan because trajectory-compare would otherwise run.
- Re-labeling a `scroll-scrub` or `sticky-pin` mechanism as `css-sticky` to skip motion verification.
- Marking a transition `dynamic: true` (which suppresses AE diff) on entries that DO have a deterministic end frame, just to dodge section-compare.
- Pruning items from `componentList` to shrink the diff surface.

Detection artifacts (`stickyElements`, `scrollEngine`, `interactionsDetected`, `paid_features_detected`) define the verification surface. Omitting from spec/plan is treated as a gate failure equivalent to having no plan at all. If a detected feature genuinely doesn't need a verification (e.g. the sticky element is a footer copyright bar with no inner motion), document the reason in `notes[]` on the plan entry — do NOT silently drop it.

## Asset substitution validation

When the enrichment encounters an existing `asset-substitution.json` with `images[]` entries, validate them:

- Reject `replacement: "emoji-or-gradient"` (or any of `emoji` / `gradient` / `placeholder` / `stub`) — these are banned.
- For each image substitution, the enrichment must check `download-log.json` (if exists) or `asset-transfer.json` to confirm an actual download attempt occurred. If not, mark the entry as `pending-download` in the plan and let the main agent retry download.
- Public-domain sources (`.gov`, `wikimedia.org`, `wikipedia.org`, `commons.wikimedia.org`) must NEVER be substituted on copyright grounds. Agent self-assessed "looks USDA-licensed" is not evidence — `.gov` is by-default public domain.

## Post-condition verification

After writing back, the main agent or delegated worker MUST verify:

1. `jq '.schemaVersion == 2' tmp/ref/<component>/generation-plan.json` returns true
2. `jq '.provenance.source == "generation-planner" and .provenance.hashAlgorithm == "sha256"' tmp/ref/<component>/generation-plan.json` returns true
3. `componentList` covers every captured section exactly once; every entry preserves non-empty `name`, `matchedSection`, `selector`, and `path`, and has a concrete `wires: []` array
4. `tokens` contains the five category objects and `dsComponentsRequired` is an array, even when no reusable value or primitive was found
5. Every `tokens.colors`/`spacing`/etc. value is grep-able in `css/variables.txt` or `styles.json`
6. Every `signatureEffects[].component` path is a valid impl target (e.g. `components/ui/<Name>.tsx`)
7. `python -m ui_clone.gate tmp/ref/<component> pre-generate` reports no `generation-plan provenance` failure
8. Every structured motion wire has a gate-approved `sourceArtifact` and
   non-empty `sourceId`; runtime dump and transition spec wires match the
   same-row selector/sourceId where those schemas support row matching, while
   other gate-approved artifacts require exact `sourceId` presence
9. Current `provenance.sourceHashes` includes the runtime dump hash for
   `animation-runtime-dump.json` when the file exists

If any verification fails, re-run enrichment with the failure as feedback.
