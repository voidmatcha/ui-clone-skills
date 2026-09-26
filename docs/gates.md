# Gates and pipeline step numbering

Reference material extracted from `AGENTS.md` to keep the canonical guide thin (it is re-injected into every agent turn). When editing gate behavior or the gate order (`ui_clone/state.py` `GATE_ORDER`, from which `VALID_GATES` derives), update the relevant table below; `AGENTS.md` carries only a pointer. Round-by-round anti-cheat hardening narratives live in [`gate-hardening-history.md`](gate-hardening-history.md) so this file stays a thin lookup.

## Pipeline step numbering

Sub-docs must match `skills/ui-reverse-engineering/SKILL.md` pipeline numbering:

- Step 5 = `interaction-detection.md`
- Step 5c-a = `bundle-analysis.md` (download + grep; NOT "Step 6")
- Step 5c-b = `bundle-verification.md` (numerical comparison)
- Step 5c-c = `paid-features-detect.sh` (paid font CDN scan; produces `paid-features.json`)
- Step 5c-d = `python -m ui_clone.clonability` (clonability risk report; produces `clonability-report.json`, relayed to the user; re-run right before `pre-generate`; stops only on a `blocker`). Blocker decisions come only from the user: a Claude prompt line `ui-clone decide <risk-id> proceed|stop <note>` (non-empty, non-placeholder note; a `>`-quoted line or one prompt answering both ways records nothing; UserPromptSubmit hook, `user-prompt` provenance) or `--decide` run in the user's own terminal (`user-terminal`; the CLI needs a TTY and the pre-bash hook denies it from agent Bash). An undecided paid font is a caution (the agent sets its `decision` at 5c-c). While a current report has an open blocker and the run is before generation (pre-generate not passed, no impl source), the Stop hook releases the turn with a `systemMessage` naming the exact line to send.
- Step 5d = `transition-spec-rules.md` (produces `external-sdks.json`)
- Step 6 = `animation-detection.md`
- Step 6c = `section-audit.md`
- Step 6d = `transition-coverage.md`
- Step 8-pre-bound = `breakpoint-collision-check.sh` (produces `responsive/boundary-collisions.json`)
- Step 8b-pre = `font-parity-check.sh` (produces `font-parity.json`)

## Gate → artifact mapping

Each gate checks artifacts produced BEFORE that gate fires. Dispatch keys are `ui_clone/state.py` `GATE_ORDER` (re-exported as `ui_clone.gate.VALID_GATES`):

- `reference` (after Phase 1 / `/ui-capture`, or after a later Phase 2 repairs deferred provisional/failed evidence): `static/ref/` ≥5 PNGs, `regions.json`, and either ≥1 WebM in `transitions/ref/`, transition artifacts with matching live-capture/inventory provenance, or a complete measured-absence receipt for an auto-generated hover inventory. A measured absence is accepted only when the bridge passed, attempted at least one candidate, retired every attempted key with `absence-measured`, recorded no capture/unsupported/not-instantiated work, retained an empty auto-generated spec, and its receipt fingerprint still matches the exact CSS, structure, and hover-state producer inputs. Backed artifacts must include a WebM/MP4 `video` or distinct PNG state paths (`idle`/`active`, `before`/`after`, or `state-N`). Deferral requires Phase 2 later in the same invocation; it must repair the evidence and pass this gate. A Phase-2-only resume rechecks current reference evidence when the five-screenshot baseline exists or reference completion was previously recorded; a recorded completion with a missing baseline fails. Phase 1 status separately requires the full-scroll video; transition PNG pairs do not replace it.
- `extraction` (after Step 3): `structure.json`, `head.json`, `styles.json`, `fonts.json`, `visible-images.json`, `inline-svgs.json`, `body-state.json`, `design-bundles.json`, `css/variables.txt`; `typography.json` is read when present and its `scalingSystem` decides whether `em-conversion.json` is required (when it contains `viewport-scaled` or `em-based`)
- `bundle` (after 5c-a): `bundles/` (≥1 JS chunk; warns <3), `interactions-detected.json`, `scroll-engine.json`. Non-empty hover CSS with an empty interaction inventory fails unless the same fresh, complete measured-absence receipt accepted by the `reference` gate proves every auto candidate inert.
- `paid-features` (after 5c-c): `paid-features.json` — every paid font CDN hit must have `decision` ∈ {`use`, `substitute`, `skip`}. Empty findings pass. GSAP plugins are not checked (GSAP is now 100% free). See `skills/visual-debug/scripts/paid-features-detect.sh`.
- `spec` (after 5d): `bundle-map.json`, `external-sdks.json`, `transition-spec.json` (validates each transition has id/trigger/source_chunk/bundle_branch/target/animation/reference_frames, and grounds `source_chunk` in captured `bundles/`, `css/`, or `html/` files unless it uses the `"inline init"` sentinel), `verification-plan.json` (produced by `skills/visual-debug/scripts/verification-plan.sh` — declares the site-specific required-check list including the universal hydration-check, proxy-mirror-check, and conditional Lottie/runtime rows; without it, `gate_post_implement` would silently skip those checks), `verify/` ≥5 frames. Also cross-validates against `paid-features.json`: any paid font marked `decision="substitute"` must have an entry in `asset-substitution.json` `fonts[]` — otherwise font-parity FAILs after generation.
- `pre-generate` (before Step 7): `clonability-report.json` (written by `ui_clone.clonability`, `provenance.sourceHashes` match its current inputs, every `blocker` has a decision with user provenance, a user `stop` fails first, state `unclonable_reasons` count as an input and are lifted with `python -m ui_clone.state recover`; only a MISSING report warns, and only when `pre-generate` is already in `completed_steps` and state has no `clonabilityReportAt` (set when a report was first written/checked); Bash writes/deletes of the report, glob forms included, are denied as enforcement state), `extracted.json`, `transition-coverage.json`, `section-map.json`, hover timing resolved, `dom-state-diff.json` (if hasPreloader), `webflow-*` (if Webflow), audit artifacts (element-roles, element-groups, layout-decisions, component-map)
- `state-coverage` (between `pre-generate` and `post-implement`): multi-snapshot capture artifacts vs impl source. Reads `state-structure-spec.json` as the compact rollup plus `states/splash/trajectory.json`, `states/scroll/summary.json`, `states/hover/manifest.json`, and optional `states/click/manifest.json`, then verifies impl/src/** has matching hooks (class strings from splash transitions, scroll-state primitives like IntersectionObserver/ScrollTrigger/useScroll, hover handlers like `:hover`/`hover:`/`onMouseEnter`/`whileHover`, click state handlers when captured). Skips silently when `states/` directory is absent (legacy ref dirs predate the multi-snapshot capture pipeline). Partial captures check only the present phases. Produced by `scripts/extract/capture-states.sh` (Phase A splash), `scripts/extract/capture-scroll.sh` (Phase B scroll), `scripts/extract/capture-hover.sh` (Phase C hover), `scripts/extract/capture-click.sh` (click state), and `scripts/extract/state-structure-spec.py` (compact rollup).
- `post-implement` (after each transition impl): `extracted.json`, `transition-spec.json`, `static/ref` ≥5, plus every block-severity `verification-plan.json` artifact for the active tier. The per-tier sub-checks are enumerated in the table below; their full anti-cheat hardening rationale (Rounds 3–7, pixel-truth visible-identity, splash distribution calibration) lives in [`gate-hardening-history.md`](gate-hardening-history.md).

The `spec` selector census also reads recorded splash DOM snapshots under
`states/splash/`, backed by a checked summary and matching trajectory timestamps.
An initial loading element need not remain in the settled `structure.json`.
Spec declarations and reconciliation reports alone do not prove its presence;
class/id tokens must occur in one captured DOM snapshot, not across different
moments or inside script strings or inert templates.

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
| state reveal | `state-reveal.json` | `state-reveal-proof-check.sh` + `ui_clone/gates/state_reveal.py` | declared active-state width reveal expands AND paints visible text under a live scroll-driven state change; off-screen decoys excluded; thresholds clamped. A measured failing row is `fail` (blocks); a declared selector with no passing observation (state never engaged, or selector absent from the impl) is listed in `unmeasured` and yields `warn` (non-blocking by design, not a clean pass) |

The remaining gate rows continue the `## Gate → artifact mapping` list:

- `boundary` (after 8-pre-bound): `responsive/boundary-collisions.json` — must be `[]`. Produced by `skills/visual-debug/scripts/breakpoint-collision-check.sh` (REF_DIR env required to write the artifact). Catches Tailwind ↔ project @media inclusive-boundary collisions (Root Cause J in diagnosis.md). The script tracks three signals; only signal 2 (isolated overflow spike) and signal 3 (rootFontSize jitter) become gate-blocking findings. Signal 1 (matchMedia overlap at the boundary) is W3C-spec inevitable — reported on stdout as advisory only and never written to the JSON artifact.
- `font-parity` (after 8b-pre): `font-parity.json` — `parity:"match"` PASSes (with silent-fallback guard via `document.fonts.check()`); `parity:"mismatch"` requires `asset-substitution.json` with at least one `fonts[]` entry. Produced by `skills/visual-debug/scripts/font-parity-check.sh`.
- `section-compare` (Stop hook): `tmp/ref/<c>/sections/result.txt` — 0 ❌ FAIL lines and 0 "⚠️ MISSING impl" lines. `STRUCTURAL_ONLY` rows are allowed only as scoped substitution evidence: broad coverage warns at 30%+ and fails above 50%, because those rows skip pixel AE polishing.

If you add an artifact check to a gate, ensure the sub-doc that produces it runs BEFORE that gate.

## Verification strictness knobs

- `UI_CLONE_STRICT_WARNINGS=1` (or `"strictWarnings": true` in `verification-plan.json`) promotes selected fidelity advisory rows such as `tree-diff`, `scroll-coverage`, and `keyframes-diff` from warning to blocking failure for release/closeout runs.
- `UI_CLONE_RESOURCE_MIRROR_REQUIRED=1` makes the Step 2.5 browser resource mirror a hard extraction requirement. Without it, `resource-manifest.json` remains recommended recovery evidence and mirror failures do not abort deterministic Phase 2.
- `UI_CLONE_LIVE_CURRENT_MODE=pin` is the default for `live-parity-sweep.sh`; it pauses common media/carousel/animation APIs before comparing. Use `snapshot` only when raw live behavior is intentionally under review.

The verification-path hardening narratives (frozen same-frame section-compare, video-motion determinism, the Round-3 … Round-7 anti-cheat / pixel-truth visible-identity rounds, and splash distribution-level SSIM calibration) are preserved in [`gate-hardening-history.md`](gate-hardening-history.md) to keep this lookup thin. They record the anti-cheat rationale behind each gate's current form — read them before relaxing a gate or changing a tolerance.

**Phase 0A note:** `canvas-webgl-detection.json` is produced by the pipeline via `skills/visual-debug/scripts/canvas-webgl-detect.sh` but is *advisory*, not gated — it routes the agent to `canvas-webgl-extraction.md` when needed. No `gate_canvas_*` exists.

## Cache and completion scope

Reference-only caching reuses validated baseline/calibration captures with matching
reference content, source code, URL, viewport/settings, and bounded TTL. It never
reuses implementation measurements. Missing or unknown check inputs remain
conservative; partial iteration receipts cannot satisfy either closeout path.
Canonical completion binds the selected scope and representative viewports.
Desktop completion covers the representative viewport plus mandatory live boundary
probes, and must be reported as desktop-only. Scope expansion requires verification.
A section-only, element-only, or trigger-opened modal/drawer clone uses the
element-scope evidence described in `skills/visual-debug/comparison-fix.md`:
`element-target.json` from `element-evidence.sh` (schemaVersion 2: exactly one
match, box ≥ 8×8 px, visible; the pass output names selector, match count,
and bbox), `frames/{ref,impl}/` captured by `scripts/extract/element-state-capture.sh`
(per-side `capture-manifest.json` with page origin, loaded-resource origins,
the script/stylesheet inventory `codeResources`, per-clip target sanity,
producer record, and sha256 per frame; an impl page that loads non-media
resources from the reference host, or any reference script/stylesheet from
any host, is refused — media and font hotlinks are allowed), producer hashes
equal to the shipped `ui_clone/scoped_producers.sha256.json` and the installed
files, resting clips at AE 0, motion sequences under the
`video-transition-compare.sh` criteria (first-change alignment, arc ≤ 18
frames, SSIM ≥ 0.90 with ±1 frame jitter), and `pixel-perfect-diff.json`
produced by the `python -m ui_clone.scoped_diff` CLI (target + subtree style
rows, page-level `proxy-mirror-check` / `bundle-paste-check` verdicts, a
reference-load source scan, producer record, self checksum).
It completes on `python -m ui_clone.scoped_check <ref-dir>` (see
`docs/agent-cli.md`), which recomputes every verdict from those artifacts;
the gates above are page-level and do not certify a subtree boundary, so a
scoped result is reported as scoped, not as page-level canonical completion.
The page pipeline has no selector option, so the element-capture path is the
supported scoped route.

`scroll-completion.json` records an `endpoint` measurement per viewport. The
probe traverses delayed scroll gates before sampling and requires a stable
document height, actual bottom position, and no document footer clipped beyond
the scrollable extent. If those conditions cannot be established within its
bounded traversal, the result is inconclusive (`status: error`), not a settled
PASS at an intermediate scroll cap. Image decoding and downloaded-asset usage
checks do not independently prove reference asset completeness.

## Ref-vs-ref self-pass invariant (batch-11)

A gate must PASS when run against its own reference (ref-as-impl). This is a THIRD verification axis beyond bypass-resistance ("a cheat must FAIL") and false-positive-resistance ("an honest impl must PASS"): it catches **achievability** bugs — a frozen, adversarially-hardened gate that a CORRECT impl cannot satisfy because the gate consumes an artifact no pipeline step produces, or derives an input from the wrong layer. Six adversarial rounds (batch-5..10) checked the first two axes but never this one (their panels fed gate inputs directly instead of producing them through the pipeline), so the ITEM 1-4 class survived into a live end-to-end run.

- CI enforcement: `tests/gates/test_ref_self_pass.py` — for each gate, the ref-as-impl achievability scenario must PASS, paired with a real-defect NEGATIVE so the self-pass relaxation never blunts detection.
- Full-pipeline proof: `scripts/ci/ref-vs-ref-selfpass.sh` (opt-in, `UI_CLONE_REF_SELFPASS=1`; the frozen ref corpus is gitignored so it never runs on CI) drives each block-severity check SCRIPT with the live reference URL as the impl URL. **batch-12 ITEM 6:** it now runs EVERY block-severity LIVE-PROBE gate (led by section-compare; full list in the script header), pins out-dirs to the ref dir, orders dependencies (section-compare before alignment-parity/-sweep), and treats a REQUIRED gate that produces NO artifact as a FAILURE (the old exit only checked `fail`, so a gate that produced nothing silently SKIPped); a setup error (exit 2) stays a SKIP. STATIC-SOURCE gates (file-IO over an impl source tree) are out of scope for live-ref-as-impl (no "reference source tree") and stay enforced at the verdict layer. Run a subset with `UI_CLONE_SELFPASS_ONLY="<gate> <gate>"`. NOTE the LIVE-vs-FROZEN gates (a fresh live probe vs the captured dom-scaffold/plan) only self-pass against a FRESH corpus — a stale/inherited corpus surfaces real live-render drift (cardinality/style), which is closed by re-capturing the corpus, never by loosening the gate.

When adding or hardening a gate, add a ref-vs-ref self-pass assertion alongside the bypass and false-positive ones.
