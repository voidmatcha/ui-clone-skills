# Gates and pipeline step numbering

Reference material extracted from `AGENTS.md` to keep the canonical guide thin (it is re-injected into every agent turn). When editing gate behavior in `ui_clone/gate.py` `VALID_GATES`, update the relevant table below; `AGENTS.md` carries only a pointer.

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
- `spec` (after 5d): `bundle-map.json`, `external-sdks.json`, `transition-spec.json` (validates `transitions[0]` has id/trigger/bundle_branch), `verification-plan.json` (produced by `skills/visual-debug/scripts/verification-plan.sh` — declares the site-specific required-check list including the universal hydration-check, proxy-mirror-check, and conditional Lottie/runtime rows; without it, `gate_post_implement` would silently skip those checks), `verify/` ≥5 frames. Also cross-validates against `paid-features.json`: any paid font marked `decision="substitute"` must have an entry in `asset-substitution.json` `fonts[]` — otherwise font-parity FAILs after generation.
- `pre-generate` (before Step 7): `extracted.json`, `transition-coverage.json`, `section-map.json`, hover timing resolved, `dom-state-diff.json` (if hasPreloader), `webflow-*` (if Webflow), audit artifacts (element-roles, element-groups, layout-decisions, component-map)
- `post-implement` (after each transition impl): `extracted.json`, `transition-spec.json`, `static/ref` ≥5
- `boundary` (after 8-pre-bound): `responsive/boundary-collisions.json` — must be `[]`. Produced by `skills/visual-debug/scripts/breakpoint-collision-check.sh` (REF_DIR env required to write the artifact). Catches Tailwind ↔ project @media inclusive-boundary collisions (Root Cause J in diagnosis.md). The script tracks three signals; only signal 2 (isolated overflow spike) and signal 3 (rootFontSize jitter) become gate-blocking findings. Signal 1 (matchMedia overlap at the boundary) is W3C-spec inevitable — reported on stdout as advisory only and never written to the JSON artifact.
- `font-parity` (after 8b-pre): `font-parity.json` — `parity:"match"` PASSes (with silent-fallback guard via `document.fonts.check()`); `parity:"mismatch"` requires `asset-substitution.json` with at least one `fonts[]` entry. Produced by `skills/visual-debug/scripts/font-parity-check.sh`.
- `section-compare` (Stop hook): `tmp/ref/<c>/sections/result.txt` — 0 ❌ FAIL lines and 0 "⚠️ MISSING impl" lines

If you add an artifact check to a gate, ensure the sub-doc that produces it runs BEFORE that gate.

**Phase 0A note:** `canvas-webgl-detection.json` is produced by the pipeline via `skills/visual-debug/scripts/canvas-webgl-detect.sh` but is *advisory*, not gated — it routes the agent to `canvas-webgl-extraction.md` when needed. No `gate_canvas_*` exists.
