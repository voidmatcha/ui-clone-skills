# Un-bake v2 — media-condition evaluation (handoff spec)

> **Status: LANDED** (commit `fc6361d`, branch `tmp`). This document was the
> forward handoff spec; v2 is now implemented and accepted. Kept as the design
> + acceptance record. Verified 2026-07-18: 15 new RED→GREEN tests, ci-local
> green, fable-critic review (one CRITICAL empty-comma-branch false-credit +
> one MAJOR `only screen`, both fixed; em/rem support added). Live A/B on
> ebpb + nvti: thumbnails grid oracle reproduced, zero-regression superset of
> v1 (147→152 props), docH toward ref @1440 on both sites (ebpb +9.7% vs
> +14.3%, nvti +6.3% vs +11.5%). Element-level felt fluidity confirmed at 1100
> (un-baked `1fr` track reflows 972px vs baked-frozen 1006px); the 1280 A≈B
> coincidence is a content-pinned-plateau sampling artifact, not a var wall.

Self-contained implementation spec. A fresh session can implement v2 from this
document alone. Prerequisite state: commit `861a947` (v1 un-bake, default-on)
on branch `tmp`.

## Context (why v2 exists)

v1 (`skills/visual-debug/scripts/scaffold-to-jsx.sh`, G-family block near
`_UNBAKE_PROPS`) drops a baked inline px only when a BASE (non-@media) ref CSS
rule declares that property for the node's single-class subject. Declarations
living ONLY inside `@media` keep the bake — v1 cannot tell whether the media
condition applies at capture width, and clearing a bake whose only source is a
non-applying block would compute `auto` (the shelved-forensic regression mode).

Cost of that conservatism, measured in the v1 acceptance run (2026-07-18):

- ebpb thumbnails family (`@media (min-width:1024px){grid-template-columns:…}`)
  — one of the campaign's own manual removals — is NOT reproduced.
- @1280 docH unmoved on both fixture sites: v1 delivers ownership + zero
  regression at capture width, not felt fluidity. v2 is the felt-fluidity step
  (user pain #2).

## v2 rule

Extend `_build_unbake_index` so a rule inside `@media`/`@container` credits the
subject class IFF the condition **applies at the capture width** (1440 unless
the capture protocol says otherwise):

| Condition | At 1440 | v2 verdict |
|---|---|---|
| `(min-width: W)` with W <= 1440 | applies | credit (un-bake allowed) |
| `(max-width: W)` with W >= 1440 | applies | credit |
| `(max-width: W)` with W < 1440 (mobile blocks) | not applying | keep bake (v1 behavior) |
| `(min-width: W)` with W > 1440 | not applying | keep bake |
| non-width conditions (prefers-*, orientation, hover, resolution) | unknown | keep bake (conservative) |
| compound `and`/`or`/`not` with any unknown term | unknown | keep bake |

Rationale (fable design review, v1 round): a min-width block applying at
capture width is ACTIVELY sizing the element at 1440 — clearing the bake lets
the mirrored cascade drive at every width, and below the block's floor the ref
itself computes base/auto too, so removal matches ref behavior on both sides of
the breakpoint.

## Implementation notes (anchors as of 861a947)

1. `_iter_css_rules(css, in_media=False)` — change the boolean `in_media` to
   carry the media CONDITION text (e.g. the header string after `@media`).
   Nested media blocks: combine conditions; if either is unknown/non-applying,
   the whole chain is.
2. New helper `_media_applies(header, capture_w)` implementing the table above.
   Parse only `(min-width: Npx)` / `(max-width: Npx)` terms (em/rem: multiply
   by 16, or treat as unknown — decide and document). `,` in a media query is
   OR: credit if ANY comma-branch fully applies.
3. `_build_unbake_index`: replace `if in_media: continue` with
   `if in_media and not _media_applies(...): continue`.
4. Capture width source: default 1440; read the actual capture viewport if an
   artifact provides it (structure capture is done at the session viewport —
   check whether ref-dir metadata records it; otherwise add an env
   `UI_CLONE_UNBAKE_CAPTURE_W` with default 1440 and document).
5. Everything else is REUSED unchanged: single-bare-class subject rule
   (review MAJOR — do not weaken), px-only value gate incl. track lists,
   inlineProps guard, pre-synthesis ordering (hook stays the FIRST styles
   transform in `render()`), kill-switch, stderr summary + UNBAKE_DEBUG.
6. Update the summary line: it currently says "@media-only-declared props stay
   baked" — v2 must say what is still kept (non-applying + non-width + unknown
   conditions, var-indirection).

## Tests (extend tests/test_scaffold_unbake_ref_covered.py)

- RED: `@media (min-width: 1024px) { .hero-box { width: 50% } }` with baked
  width 1280px → dropped (v1 keeps it — this is the flip).
- `@media (max-width: 768px) { … }` → still kept (regression lock).
- `@media (min-width: 1600px)` → kept (above capture width).
- `@media (prefers-reduced-motion: reduce)` → kept (non-width).
- `@media (min-width: 768px) and (max-width: 1200px)` → kept at 1440 (range
  excludes capture width).
- Comma OR: `@media (min-width: 2000px), (min-width: 1000px)` → credited.
- Existing 12 tests must stay green (base-rule path untouched).

## Acceptance protocol (repeat the v1 A/B — the recipe that worked)

1. Scaffold A (default) and B (`UI_CLONE_UNBAKE_REF_COVERED=0`) from
   `tmp/ref/ebpb` and `tmp/ref/nvti` into /tmp dirs (each pre-made with `src/`).
2. Wire vite shells from the loop impls: copy `index.html package.json
   package-lock.json tsconfig.json vite.config.ts` + `src/main.tsx
   src/index.css src/ref-css/ src/styles/` from
   `scratch/loop-ebpb-0/impl` / `scratch/loop-nvti-0/impl`; clone node_modules
   with `cp -Rc` (APFS; a symlink breaks .bin resolution); build with
   `node node_modules/vite/dist/node/cli.js build` (the .bin shim breaks after
   copy); serve with `… preview --port 530N --strictPort`.
3. Probe with agent-browser (`AGENT_BROWSER_COLOR_SCHEME=light`, double-read
   until stable, assert in-page innerWidth): docH + section tops at
   1440/1280/1100.
4. Gates: ebpb thumbnails grid-template family must now appear in the
   UNBAKE_DEBUG site list (the v1-missed oracle rows); @1440 docH/tops must
   not move AWAY from ref (ebpb ref docH source: regenerate, do NOT reuse
   stale proxies; nvti ref docH @1440 = 23220 per
   `tmp/ref/nvti/section-map.json`); @1280 should now MOVE toward ref on
   ebpb (the fluidity claim v1 explicitly did not make — if it does not,
   report honestly and investigate var-indirection dominance before claiming).
5. Oracle comparison script pattern: see the v1 session — parse
   `scratch/loop-ebpb-3/unbake_batch{1,2}.py` RULES into (class, kebab-prop)
   pairs; normalize min-height↔height via the pre-synthesis path.

## Process requirements

- fable-critic design+implementation review (the v1 reviews found 5 real
  MAJORs across this series; do not skip).
- RED→GREEN per change; `bash scripts/ci/ci-local.sh` + 
  `bash scripts/ci/pre-push-security.sh` green before each commit; trailer
  block per the commit protocol; NEVER push (user pushes).
- Do not edit the repo while ci-local runs (drift-test mutates AGENTS.md).
- bash syntax checks need bash 4+ (`/opt/homebrew/bin/bash -n`); /bin/bash 3.2
  false-fails the `$(cat <<'EOF')` heredocs.

## Adjacent backlog (same area, separate work items)

- var-indirection detection (gap #2): `--carousel-slide-width` /
  `aspect-ratio: var(--width)/var(--height)` sizing — needs custom-property
  graph resolution, NOT part of v2.
- wrapper-flattening wall: unchanged, out of scope.
