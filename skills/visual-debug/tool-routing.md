# Visual-debug tool routing — Step 8

Read this reference only when the entrypoint's summary-first route does not provide the exact command or when you need iteration reuse, dynamic masking, or an escalation choice.

## Core commands

```bash
# Structural diagnosis before pixel comparison
bash "$SCRIPTS_DIR/stray-absolute-check.sh" <session>-stray <impl> 375 812
bash "$SCRIPTS_DIR/stray-absolute-check.sh" <session>-stray <impl> 1280 800
bash "$SCRIPTS_DIR/reveal-trigger-check.sh" <session>-reveal <impl> 1280 800
bash "$SCRIPTS_DIR/breakpoint-collision-check.sh" <session>-bp <impl>
bash "$SCRIPTS_DIR/transition-spec-coverage.sh" tmp/ref/<component> <impl-src-dir>
bash "$SCRIPTS_DIR/computed-diff.sh" <session> <orig> <impl> \
  "h1" "h2" "h3" "img" "button" "a" "body" "header" "main" "footer"

# Standalone broad sweep
bash "$SCRIPTS_DIR/batch-scroll.sh" <orig> <impl> <session> <dir>
bash "$SCRIPTS_DIR/batch-compare.sh" <dir>
bash "$SCRIPTS_DIR/dssim-compare.sh" <dir>

# Routed section and transition verification (quiet: progress -> sections/section-compare.log)
SECTION_COMPARE_QUIET=1 bash "$SCRIPTS_DIR/section-compare.sh" <orig> <impl> <session> "$(pwd)/tmp/ref/<component>"
bash "$SCRIPTS_DIR/transition-compare.sh" <orig> <impl> <session> "$(pwd)/tmp/ref/<component>"
```

Before every compare, scan the implementation for fixed dev banners, badges, overlays, widgets, or labels absent from the reference. Use the implementation's documented hide flag in every command; a fixed chrome hotspot is an invalid comparison input, not a product fix.

## Evidence reuse

Freeze a completed dynamic reference with `RECATCH_REF=0` during repair. Re-capture it only when the evidence is demonstrably stale. Recapture the implementation after source changes.

`ONLY_IF_CHANGED=1` is allowed only on the second or later section comparison when the reference remains pinned, implementation source is unchanged, and the prior `sections/result.txt` is a complete measured pass:

```bash
RECATCH_REF=0 ONLY_IF_CHANGED=1 IMPL_SRC_DIR=<impl-src-root> SECTION_COMPARE_QUIET=1 \
  bash "$SCRIPTS_DIR/section-compare.sh" <orig> <impl> <session> "$(pwd)/tmp/ref/<component>"
```

The source hash covers sorted implementation paths and contents. Never reuse a prior `FAIL`, `INCOMPLETE`, or `UNMEASURED` result as success. Delete `sections/.last-impl-hash` to force fresh implementation capture when provenance is uncertain.

## Diagnosis routing

| Evidence or symptom | First tool | Escalation |
|---|---|---|
| Existing run | Read `sections/result.txt` or the named summary | Open detail only for failing rows |
| CSS/reset/layout suspicion | `computed-diff.sh` | `tree-diff.sh` |
| AE failure with a diff image | `auto-diagnose.sh` | Read the diff only if it finds nothing |
| Correct style, wrong position | `layout-tree-diff.sh` | Inspect containing block and pin lifecycle |
| Hover mismatch after transition pass | `hover-tree-diff.sh` | Compare semantic pair identity and timing |
| Entrance/scroll curve mismatch | `keyframes-diff.sh` | Targeted trajectory or temporal diff |
| Scroll-stop, pin, scrub, or velocity evidence | `scroll-state-machine-check.sh` | Require `initial → active/expanded → settled/returned` |
| Repeating scroll motion feels wrong | `scroll-anim-temporal-diff.sh` | Advisory targeted selector only |

Use `tree-diff.sh`, `layout-tree-diff.sh`, `hover-tree-diff.sh`, and `keyframes-diff.sh` as targeted diagnostics; do not run the whole family by default. Reports are severity sorted. Fix critical rows first, then major rows, and defer minor pixel noise until Phase E.

The executable gate catalog is `$SCRIPTS_DIR/verification-plan.sh` (`skills/visual-debug/scripts/verification-plan.sh`). Prefer its tier and dispatch rules over manually recreating a broad list: `quick` for static inner-loop signals, `standard` for one-shot browser checks, and `comprehensive` for final frame-by-frame verification.

## Checker catalog

Scripts are the source of truth for thresholds; values below mirror the code.

| Script | Catches | Fails when |
|---|---|---|
| `layout-diff.sh` / `layout-health-check.sh` | Section box or total-height drift | Structural drift before pixel diff |
| `stray-absolute-check.sh` | "Footer disappeared" (`diagnosis.md` H) | `position:absolute` with no positioned ancestor |
| `tailwind-transform-conflict-check.sh` | Double transform (`diagnosis.md` I) | Non-identity `transform` plus non-`none` `translate`/`rotate`/`scale` |
| `breakpoint-collision-check.sh` | Broken at exact breakpoint (`diagnosis.md` J) | min/max media both match, isolated overflow, or root font jitter at boundary ±1 |
| `font-parity-check.sh` / `paid-features-detect.sh` | Silent font substitution | Mismatch not declared in `asset-substitution.json`; paid entry without `decision` |
| `reveal-trigger-check.sh` | Reveal wired but never fires | Initially hidden element never advances after scroll-in |
| `hidden-children-check.sh` | Ref screenshot painted while DOM stays hidden | Section area >20000 with ≥2 non-trivial children, all hidden (`display:none`, `visibility:hidden`, opacity ≤0.01, rect <2x2) |
| `blank-viewport-check.sh` | DOM exists but page blank | `html`/`body`/root stays hidden or all text invisible |
| `runtime-dom-parity-check.sh` | Screenshot overlay or single-canvas paint | Node ratio outside 0.70–1.30, visible text nodes < max(10, sections×2), one media element >90% viewport, or Lottie missing |
| `ref-screenshot-asset-check.sh` | Ref captures reused as impl assets | Capture-dir path substring or sha256-identical copy in impl |
| `entry-coherence-check.sh` | Mixed stacks or pasted markup | Coexisting entries, mixed Vite+Next deps, or entry HTML with ≥5 content tags (or ≥3 plus ≥800 body chars) |
| `scaffold-residue-check.sh` | Orphan scaffold components | ≥3 orphans, or ≥1 orphan with ratio ≥40% |
| `scaffold-warn-check.sh` | Unresolved scaffold placeholders | Any `data-scaffold-warn` in impl |
| `html-paste-check.sh` | Ref HTML/JS pasted into entry | Tag-multiset similarity ≥70% (≥90% for component files; skipped under 20 tags), ref bundle `<script src>`, or inline style quick_ratio ≥70% |
| `css-mirror-check.sh` | Ref CSS mirrored | `@import` of a ref bundle host/file, byte-identical copy, or quick_ratio ≥70% (snippets under `impl/src/styles/from-ref/` allowed) |
| `monolithic-impl-check.sh` | Whole UI in one entry file | Entry ≥8000 bytes and components < max(3, sections // 3) |
| `required-media-coverage-check.sh` | Missing video/Lottie | `required-media.json` absent, media not in `impl/public/` and source, or Lottie runtime missing |
| `svg-dom-parity-check.sh` | Dropped CSS/inline SVG icons | Ref SVG total ≥2 and impl <50%, empty `<svg>` stubs, or per-section loss |
| `motion-coverage-check.sh` | Ref motion library, impl static | Ref score ≥3 with impl 0, or ref ≥5 with impl <2 |
| `scroll-engine-parity-check.sh` | Wrong motion engine class | Ref class (pin, scrub, Lenis, ScrollTrigger) lacks an impl equivalent |
| `forced-state-class-check.sh` | Hardcoded final states | Forced active/visible classes or final-state patches when ref has state classes |
| `invalidation-check.sh` | Retired known-bad run | `.invalidated` stamp present; post-implement blocks until removed and fixed |
| `impl-url-guard.sh` | Stale dev server on port | Listener cwd differs from canonical impl root |

## Dynamic content masking

Canvas and video clocks can make raw pixels nondeterministic. Mask only regions already identified as dynamic, on both sides, while preserving layout:

```bash
EXCLUDE_DYNAMIC=1 SECTION_COMPARE_QUIET=1 bash "$SCRIPTS_DIR/section-compare.sh" \
  <orig> <impl> <session> "$(pwd)/tmp/ref/<component>"
```

`DYNAMIC_SELECTORS` defaults to `canvas, video`. Prefer `"dynamic": true` on the corresponding `transition-spec.json` entry so targets are evidence backed. Selectors containing single or double quotes are rejected; use class, id, or bare attribute selectors. Masking does not waive runtime, trajectory, media, or semantic verification, and must never hide an unexplained static mismatch.

## Output and cleanup

Read summary artifacts before raw JSON. Keep browser eval output in files. Close each owned session by name before returning, including on errors; never use `close --all`.
