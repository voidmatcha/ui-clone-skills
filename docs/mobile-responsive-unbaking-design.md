# Mobile-responsive clones & the inline-bake / un-bake coupling — design + handoff

Status: **investigated, not landed.** This is a portable handoff for a future
session. It packages an evidence-backed problem statement, a proven dead-end, and
the exact next step, plus a ready-to-apply hardened patch
(`docs/mobile-responsive-unbaking-forensic.patch`). Nothing here changes pipeline
behavior today.

Origin: navercorp.com fidelity run (loop-nv-1). Defects #4 (mobile
non-responsive) and #5 (nested wrapper flattened) were proven to share one
structural root.

## The coupling law (proven)
The transpiler (`skills/visual-debug/scripts/scaffold-to-jsx.sh`) bakes DESKTOP
computed styles as INLINE JSX and flattens DOM wrappers, then imports the ref's
compiled CSS. Inline styles beat stylesheet rules for the same property, so:

- **#5** Restoring a dropped class-bearing wrapper (`main#content.container`) is
  structurally trivial but re-activates `.container__inner{max-width:1280;
  padding:0 48}` ref CSS that fights the sections' baked 1440-centered inline —
  tech-section dSSIM regressed 0.130 → 0.419. (spike: `git apply` the
  `spike-wrapper.patch` referenced below to reproduce.)
- **#4** navercorp ships 509 `@media` blocks. Baked desktop inline overrides all
  of them at 390px, so mobile renders the desktop layout squished (mobile dSSIM
  0.948).

**Conclusion: un-flattening requires un-baking.** You cannot let ref CSS (scope
rules or `@media`) drive layout while inline styles still override it. #4 and #5
are the same problem in two guises (`@media` vs scope wrapper).

## The dead-end: a standalone `@media`-aware strip does NOT work
Attempt: in the existing gated forensic mode
(`UI_CLONE_FORENSIC_CLASSNAME_ONLY=1` + `generation-plan.json`
`forensicPreservation.strategy = "ref-derived-jsx-with-local-css"`), strip an
inline box-model prop for a node when a screen `@media` rule overrides it, so the
ref CSS drives that viewport. Behind a new `UI_CLONE_FORENSIC_MEDIA_AWARE=1` flag
(default off). Hardened over four adversarial review rounds:

- subject-compound selector matching (a descendant `.page .card` marks only
  `.card`; a compound `.card.featured` requires BOTH classes);
- screen-only media filter (skips `print`/`speech`/`only print`/`not screen`,
  keeps `not print`, bare feature queries, and `print, (max-width)`);
- logical props mapped to one physical side (`padding-inline-start` → left, LTR);
- pseudo-class/attribute stripping (`:not(.x)`, `:hover`, `[attr]`);
- a **base-rule guard**: strip only when a NON-`@media` base rule ALSO sets the
  prop for the node's subject compound;
- a class-token bucket index for O(node-classes) lookup;
- a CSS-comment strip (see Perf below).

**It still regresses desktop.** Deterministic static proof on navercorp: of the
inline widths the strip removed, the matching base-rule value EQUALS the captured
inline in **9** cases and DIFFERS in **93** (e.g. `.masonry-grid-item` inline
`100%` vs base `33.33%`; `.item-cate` `330.625px` vs `100%`; `.item-visual`
`100%` vs `50%`). Root cause: **the captured inline is the FULL-CASCADE computed
value** (more-specific selectors + parent flex/grid context + JS), whereas any
single `.class{width}` base rule is only one contributor. "A base rule exists" ≠
"the base rule produces the desktop value." Stripping those inlines reflows
desktop to the wrong value.

## Perf pathology (fixed in the patch; reusable lesson)
Minified bundle CSS ends with a huge `/*# sourceMappingURL=data:...base64... */`
comment (navercorp's is 1.7 MB). A coarse `([^{}]+)\{([^{}]*)\}` rule scan
re-attempts `[^{}]+` from every offset across that brace-free blob → O(n²) hang
(> 10 min). **Fix: strip CSS comments before parsing**
(`re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)`); this also de-noises the
substring coverage checks. After it, the forensic transpile is 18.9 s vs 20.1 s
for the normal path. Applies to ANY code that regex-scans minified bundle CSS.

## Next step (how to resume)
1. Apply the hardened skeleton:
   `git apply docs/mobile-responsive-unbaking-forensic.patch`
   (adds the flag-gated `@media`-aware strip + 10 unit tests to
   `scaffold-to-jsx.sh` and `tests/test_scaffold_forensic_media_aware.py`).
2. Replace the base-rule guard with a real **cascade resolver**: compute the
   ref CSS's DESKTOP value for each node (specificity + inheritance + `@media`-off
   resolution, like the browser) and strip an inline prop only when that computed
   value EQUALS the captured inline. Then `@media` can safely drive mobile
   without touching props whose desktop truth is not reproduced by the ref
   cascade. (Alternative, larger: emit className-only JSX for ref-covered nodes
   and let the mirrored ref CSS own layout end-to-end — full un-bake.)
3. Re-measure desktop (1440) AND mobile (390) per section vs live. Desktop gate:
   each section ≤ base + 0.02 dSSIM, docHeight not collapsed. Mobile: materially
   below base (the strip already showed hero −0.128 / mid −0.056 before the
   desktop cost was understood — that upside returns once desktop is safe).

## Known limitations of the skeleton patch (do not trust as-is)
The applied patch is a hardened *skeleton*, not a correct feature — step 2 (the
cascade resolver) is what makes it safe, and it will re-architect the selector
matching. Carry these forward when you rework it:

- **Functional pseudo-classes over-strip.** `_subject_compounds` strips the whole
  pseudo, so `.card:not(.featured)` and `.card:is(.featured)` both index as
  `card`. `:not()` is a NEGATIVE constraint (the rule must NOT match a
  `.card.featured` node) and `:is()`/`:where()` are alternations — flattening
  them can strip inline box-model from nodes the rule never matches, the very
  desktop/mobile drift this work is trying to avoid. Fix by SKIPPING selectors
  that carry a functional pseudo (safest) or modelling negative/alternation
  constraints in the index. The cascade resolver (step 2) subsumes this by
  computing actual per-node applicability instead of token heuristics.
- The coarse `([^{}]+)\{([^{}]*)\}` scan does not understand nested at-rules
  (`@supports`, CSS nesting); it is conservative (misses rules) rather than
  wrong, but a real cascade resolver should parse properly.

## Hard ceiling to keep in mind
Live navercorp mobile is a SEPARATE dedicated mobile DOM (distinct `mo-nav`,
restacked content). `@media` reflow of the desktop DOM floors around mobile dSSIM
0.79–1.07; true parity needs mobile-DOM capture — a different workstream, not
this one.

## Companion artifacts
- `docs/mobile-responsive-unbaking-forensic.patch` — the hardened impl + 10
  unit tests (this repo, applies clean on the commit that introduced this doc).
- serena memory `navercorp/loop-nv-1-unbaking-charter` — the full run record.
