# Gates and pipeline step numbering

Reference material extracted from `AGENTS.md` to keep the canonical guide thin (it is re-injected into every agent turn). When editing gate behavior in `ui_clone/gate.py` `VALID_GATES`, update the relevant table below; `AGENTS.md` carries only a pointer. Round-by-round anti-cheat hardening narratives live in [`gate-hardening-history.md`](gate-hardening-history.md) so this file stays a thin lookup.

## Pipeline step numbering

Sub-docs must match `skills/ui-reverse-engineering/SKILL.md` pipeline numbering:

- Step 5 = `interaction-detection.md`
- Step 5c-a = `bundle-analysis.md` (download + grep; NOT "Step 6")
- Step 5c-b = `bundle-verification.md` (numerical comparison)
- Step 5c-c = `paid-features-detect.sh` (paid font CDN scan; produces `paid-features.json`)
- Step 5d = `transition-spec-rules.md` (produces `external-sdks.json`)
- Step 6 = `animation-detection.md`
- Step 6c = `section-audit.md`
- Step 6d = `transition-coverage.md`
- Step 8-pre-bound = `breakpoint-collision-check.sh` (produces `responsive/boundary-collisions.json`)
- Step 8b-pre = `font-parity-check.sh` (produces `font-parity.json`)

## Gate → artifact mapping

Each gate checks artifacts produced BEFORE that gate fires. Dispatch keys live in `ui_clone/gate.py` `VALID_GATES`:

- `reference` (after Phase 1 / `/ui-capture`): `static/ref/` ≥5 PNGs, `transitions/ref/` ≥1 file, `regions.json`
- `extraction` (after Step 3): `structure.json`, `head.json`, `styles.json`, `fonts.json`, `visible-images.json`, `inline-svgs.json`, `body-state.json`, `design-bundles.json`, `css/variables.txt`, `em-conversion.json` (if `scalingSystem ≠ px-fixed`)
- `bundle` (after 5c-a): `bundles/` (≥1 JS chunk; warns <3), `interactions-detected.json`, `scroll-engine.json`
- `paid-features` (after 5c-c): `paid-features.json` — every paid font CDN hit must have `decision` ∈ {`use`, `substitute`, `skip`}. Empty findings pass. GSAP plugins are not checked (GSAP is now 100% free). See `skills/visual-debug/scripts/paid-features-detect.sh`.
- `spec` (after 5d): `bundle-map.json`, `external-sdks.json`, `transition-spec.json` (validates each transition has id/trigger/source_chunk/bundle_branch/target/animation/reference_frames, and grounds `source_chunk` in captured `bundles/`, `css/`, or `html/` files unless it uses the `"inline init"` sentinel), `verification-plan.json` (produced by `skills/visual-debug/scripts/verification-plan.sh` — declares the site-specific required-check list including the universal hydration-check, proxy-mirror-check, and conditional Lottie/runtime rows; without it, `gate_post_implement` would silently skip those checks), `verify/` ≥5 frames. Also cross-validates against `paid-features.json`: any paid font marked `decision="substitute"` must have an entry in `asset-substitution.json` `fonts[]` — otherwise font-parity FAILs after generation.
- `pre-generate` (before Step 7): `extracted.json`, `transition-coverage.json`, `section-map.json`, hover timing resolved, `dom-state-diff.json` (if hasPreloader), `webflow-*` (if Webflow), audit artifacts (element-roles, element-groups, layout-decisions, component-map)
- `state-coverage` (between `pre-generate` and `post-implement`): multi-snapshot capture artifacts vs impl source. Reads `state-structure-spec.json` as the compact rollup plus `states/splash/trajectory.json`, `states/scroll/summary.json`, `states/hover/manifest.json`, and optional `states/click/manifest.json`, then verifies impl/src/** has matching hooks (class strings from splash transitions, scroll-state primitives like IntersectionObserver/ScrollTrigger/useScroll, hover handlers like `:hover`/`hover:`/`onMouseEnter`/`whileHover`, click state handlers when captured). Skips silently when `states/` directory is absent (legacy ref dirs predate the multi-snapshot capture pipeline). Partial captures check only the present phases. Produced by `scripts/extract/capture-states.sh` (Phase A splash), `scripts/extract/capture-scroll.sh` (Phase B scroll), `scripts/extract/capture-hover.sh` (Phase C hover), `scripts/extract/capture-click.sh` (click state), and `scripts/extract/state-structure-spec.py` (compact rollup).
- `post-implement` (after each transition impl): `extracted.json`, `transition-spec.json`, `static/ref` ≥5, plus every block-severity `verification-plan.json` artifact for the active tier. The per-tier sub-checks are enumerated in the table below; their full anti-cheat hardening rationale (Rounds 3–7, pixel-truth visible-identity, splash distribution calibration) lives in [`gate-hardening-history.md`](gate-hardening-history.md).

### post-implement sub-checks

Compact lookup; the exhaustive per-check reasoning is in [`gate-hardening-history.md`](gate-hardening-history.md). Scrub / per-element-evolution specs are judged inside `transition-fires` (each declared channel must move and the children must move relative to each other).

| Sub-check (min plan tier) | Artifact | Producer | Pass-rule (summary) |
|---|---|---|---|
| impl-url guard (standard) | `impl-url-guard.json` | post-implement census | local impl URL port is served from the canonical `.impl-root` |
| runtime env (standard) | `runtime-env.json` | post-implement census | runtime environment recorded |
| live parity (standard) | `live-parity.json` | paired ref/impl `agent-browser` scroll census | no visible pseudo duplication, broken assets, image-inventory drift, missing fonts, or mask-hidden geometry/count drift |
| capacity report (quick) | `capacity-report.json` | capacity probe | browser-heavy lanes use measured local capacity, not a guess |
| alignment parity (quick) | `alignment-parity.json` | `alignment-parity-check.sh` | ref-relative section-center, contentBox gap-asymmetry, per-container contentGroups asymmetry, and per-child offset distribution within tolerance; off-center unpaired impl group ⇒ `group-leftover` fail; overflow track exempt (`group-overflow`) |
| junk token | `junk-token.json` | `junk-token-check.sh` | no serialization junk (`undefined`/`null`/`NaN`/`[object Object]`) as standalone tokens in source or runtime DOM (homoglyph + zero-width folded); `runtimeScanned` bound to a receipt inside `impl_root` |
| alignment sweep | `alignment-sweep.json` | `alignment-sweep-check.sh` | impl-only DOM-rect sweep at the impl's own @media boundaries ±1px upholds ref-classified centered/fixed-gutter invariants |
| hover fallback | `hover-fallback.json` | `hover-fallback-probe.sh` + `ui_clone/gates/hover_probe.py` | every hoverable entry gets a verdict: event delta (`verified`), impl `:hover` CSS with size proven under forced CDP hover (`static-verified`), or `fail` |
| masked-region motion | `masked-region-motion.json` | `masked-region-motion-proof-check.sh` + `masked_region_motion.py` | `dynamic:true` timer/carousel entries show ≥2 states at the declared cadence over the spec-declared channels |
| masked-region static | `masked-region-static.json` | `masked-region-static-check.sh` + `masked_region_static.py` | un-masked live impl computed styles match `dom-scaffold.json` ref ground truth per rendered-visible element at every fan-out viewport |
| state reveal | `state-reveal.json` | `state-reveal-proof-check.sh` + `ui_clone/gates/state_reveal.py` | declared active-state width reveal expands AND paints visible text under a live scroll-driven state change; off-screen decoys excluded; thresholds clamped |

The remaining gate rows continue the `## Gate → artifact mapping` list:

- `boundary` (after 8-pre-bound): `responsive/boundary-collisions.json` — must be `[]`. Produced by `skills/visual-debug/scripts/breakpoint-collision-check.sh` (REF_DIR env required to write the artifact). Catches Tailwind ↔ project @media inclusive-boundary collisions (Root Cause J in diagnosis.md). The script tracks three signals; only signal 2 (isolated overflow spike) and signal 3 (rootFontSize jitter) become gate-blocking findings. Signal 1 (matchMedia overlap at the boundary) is W3C-spec inevitable — reported on stdout as advisory only and never written to the JSON artifact.
- `font-parity` (after 8b-pre): `font-parity.json` — `parity:"match"` PASSes (with silent-fallback guard via `document.fonts.check()`); `parity:"mismatch"` requires `asset-substitution.json` with at least one `fonts[]` entry. Produced by `skills/visual-debug/scripts/font-parity-check.sh`.
- `section-compare` (Stop hook): `tmp/ref/<c>/sections/result.txt` — 0 ❌ FAIL lines and 0 "⚠️ MISSING impl" lines. `STRUCTURAL_ONLY` rows are allowed only as scoped substitution evidence: broad coverage warns at 30%+ and fails above 50%, because those rows skip pixel AE polishing.

If you add an artifact check to a gate, ensure the sub-doc that produces it runs BEFORE that gate.

## Verification strictness knobs

- `UI_CLONE_STRICT_WARNINGS=1` (or `"strictWarnings": true` in `verification-plan.json`) promotes selected fidelity advisory rows such as `tree-diff`, `scroll-coverage`, and `keyframes-diff` from warning to blocking failure for release/closeout runs.
- `UI_CLONE_RESOURCE_MIRROR_REQUIRED=1` makes the Step 2.5b browser resource mirror a hard extraction requirement. Without it, `resource-manifest.json` remains recommended recovery evidence and mirror failures do not abort deterministic Phase 2.
- `UI_CLONE_LIVE_CURRENT_MODE=pin` is the default for `live-parity-sweep.sh`; it pauses common media/carousel/animation APIs before comparing. Use `snapshot` only when raw live behavior is intentionally under review.

The verification-path hardening narratives (frozen same-frame section-compare, video-motion determinism, the Round-3 … Round-7 anti-cheat / pixel-truth visible-identity rounds, and splash distribution-level SSIM calibration) are preserved in [`gate-hardening-history.md`](gate-hardening-history.md) to keep this lookup thin. They record the anti-cheat rationale behind each gate's current form — read them before relaxing a gate or changing a tolerance.

**Phase 0A note:** `canvas-webgl-detection.json` is produced by the pipeline via `skills/visual-debug/scripts/canvas-webgl-detect.sh` but is *advisory*, not gated — it routes the agent to `canvas-webgl-extraction.md` when needed. No `gate_canvas_*` exists.

## Rejected design notes

**Rejected: fingerprint-cached check reuse.** A proposal to skip re-running a check when its input fingerprint (impl source-tree hash + relevant ref-artifact hashes + check-script hash) exactly matches the fingerprint recorded in the last GREEN artifact was reviewed and **declined** (design review 2026-06-12). The intended saving is only 1–3 min/loop, against an unbounded anti-cheat risk: (1) no per-check input-set contract exists — `verification-plan.json` rows carry no file-input lists and the dispatcher maps args, not inputs, so mapping drift would cause silent stale-GREEN reuse; (2) the "deterministic" candidates are not pure file-IO (junk-token has a live-DOM mode; alignment depends on `UI_CLONE_ALIGN_*` / `UI_CLONE_GENERATED_EVIDENCE_DIRS` env state); (3) consumer-side fingerprint recomputation is intractable today (the canonical verify stamp only hash-pins `sections/`+`result.txt`), and cached old-mtime artifacts break the dispatcher/gate/Stop-hook freshness checks in both directions. The tier system already addresses cost where it matters, and `section-compare` has its own scoped content-hash fast path. No caching is implemented; checks always re-run.

## Ref-vs-ref self-pass invariant (batch-11)

A gate must PASS when run against its own reference (ref-as-impl). This is a THIRD verification axis beyond bypass-resistance ("a cheat must FAIL") and false-positive-resistance ("an honest impl must PASS"): it catches **achievability** bugs — a frozen, adversarially-hardened gate that a CORRECT impl cannot satisfy because the gate consumes an artifact no pipeline step produces, or derives an input from the wrong layer. Six adversarial rounds (batch-5..10) checked the first two axes but never this one (their panels fed gate inputs directly instead of producing them through the pipeline), so the ITEM 1-4 class survived to loop-e2e-12.

- CI enforcement: `tests/gates/test_ref_self_pass.py` — for each gate, the ref-as-impl achievability scenario must PASS, paired with a real-defect NEGATIVE so the self-pass relaxation never blunts detection.
- Full-pipeline proof: `scripts/ci/ref-vs-ref-selfpass.sh` (opt-in, `UI_CLONE_REF_SELFPASS=1`; the frozen ref corpus is gitignored so it never runs on CI) drives each block-severity check SCRIPT with the live reference URL as the impl URL. **batch-12 ITEM 6:** it now runs EVERY block-severity LIVE-PROBE gate (led by section-compare; full list in the script header), pins out-dirs to the ref dir, orders dependencies (section-compare before alignment-parity/-sweep), and treats a REQUIRED gate that produces NO artifact as a FAILURE (the old exit only checked `fail`, so a gate that produced nothing silently SKIPped); a setup error (exit 2) stays a SKIP. STATIC-SOURCE gates (file-IO over an impl source tree) are out of scope for live-ref-as-impl (no "reference source tree") and stay enforced at the verdict layer. Run a subset with `UI_CLONE_SELFPASS_ONLY="<gate> <gate>"`. NOTE the LIVE-vs-FROZEN gates (a fresh live probe vs the captured dom-scaffold/plan) only self-pass against a FRESH corpus — a stale/inherited corpus surfaces real live-render drift (cardinality/style), which is closed by re-capturing the corpus, never by loosening the gate.

When adding or hardening a gate, add a ref-vs-ref self-pass assertion alongside the bypass and false-positive ones.
