# Canvas-replay closeout mode — design

> Status: **DESIGN** (not implemented). Captures the architectural choice surfaced by the kayiseisagu / juanmora / 26-site loop (2026-05-24/25). Implementation is opt-in and deferred until an operator explicitly requests it.

## Problem

ui-clone-skills' section-compare gate measures pixel AE/SSIM between ref and impl screenshots. Sites whose visual identity is driven by imperative `<canvas>` drawing (kayiseisagu's `.bg-canvas` concentric arcs + `.music-sphere`, real-time WebAudio visualizers, generative scroll-driven plates) fail this gate by design — CSS replication of canvas output is *approximate*, and pixel-diff is bit-perfect. Two iterations on kayiseisagu (claude 4 iter, codex 4 iter) confirmed AE/Mpx stays 60k–440k regardless of fix attempts. The clone reaches every structural and motion gate but the canvas region drives the visual gate red.

Currently the only escape is the **30-min canvas CSS replication cap** (`canvas-webgl-extraction.md`). After 30 min the agent declares unclonable via `record_unclonable(category="hard-cap-fail")`. The clone is shelved as "structurally complete, visually unclonable." This is honest but loses production value when the user genuinely wants the canvas identity preserved.

## Goal

Add a **canvas-replay closeout mode** that lets an operator opt into loading the reference site's canvas-driving JS at runtime (sandboxed) instead of approximating it in CSS. Maintain license + attribution discipline as a hard requirement. Surface the policy choice in the same `closeout_policy` field that already exists for `canonical` vs `structural`.

## Non-goals

- Automatic canvas-replay (operator must opt in explicitly per ref dir)
- Full browser-engine emulation or canvas-frame-by-frame replication
- Bypass for non-canvas section-compare failures (`canvas-replay` does not loosen `text-fidelity`, `font-parity`, `runtime-dom-parity`, `transition-compare`, etc.)
- Replacement for the canonical path — `canonical` stays the default; `canvas-replay` is a third option alongside `structural`

## Activation contract

Opt-in is two-step (deliberate friction):

1. Operator sets `closeout_policy: "canvas-replay"` in `pipeline-state.json` (manual edit or future `pipeline ... opt-in` subcommand)
2. Operator creates `<ref-dir>/canvas-replay-attestation.json`:
   ```json
   {
     "license": "<URL or text of the source's license / explicit owner permission>",
     "disclaimer": "Not affiliated with <site-name>. <ref-url> assets loaded for canvas-fidelity per opt-in.",
     "attestedBy": "<operator-handle>",
     "attestedAt": "2026-05-25T08:00:00Z",
     "ref_canvas_sources": [
       "<URL of the JS bundle being loaded — must match ref's actual canvas driver>"
     ]
   }
   ```

Without both, the existing `ref-js-loader` gate and `ref-screenshot-asset` gate continue to FAIL canvas-replay attempts — same protection production users have today.

## Gate behavior changes

When `closeout_policy == "canvas-replay"` AND `canvas-replay-attestation.json` is present AND non-empty:

- **`ref-js-loader` gate**: PASS for the URLs declared in `ref_canvas_sources[]` only. Other ref bundle imports still FAIL.
- **`ref-screenshot-asset` gate**: PASS for canvas-only sections. Non-canvas section-compare runs against rendered output as before.
- **`section-compare` thresholds**: critical AE/Mpx threshold raised by `_CANVAS_REPLAY_AE_RELIEF` (TBD, suggest 2×) for sections tagged `kind: "canvas"` in `section-map.json`. Non-canvas sections unchanged.
- **New gate `canvas-replay-attestation`**: validates attestation file shape + that declared `ref_canvas_sources[]` are actually loaded by the impl (HTTP request inspection via agent-browser).
- **Stop hook**: `_enforce_canvas_replay_stamp(ref_dir)` mirrors `_enforce_structural_convergence_stamp`. New stamp writer: `scripts/verify/check-canvas-replay.sh --write-stamp` produces `canvas-replay-stamp.json` with sha256 of attestation + ref_canvas_sources list at stamp time. Tamper-detection identical to the other two stamps.

## Schema additions

`state.py`:
```python
# closeout_policy enum extended:
closeout_policy: str = "canonical"  # was: literal "canonical" | "structural"
# Now: "canonical" | "structural" | "canvas-replay"
```

New artifact:
```
<ref-dir>/canvas-replay-attestation.json   # operator-written
<ref-dir>/canvas-replay-stamp.json         # check-canvas-replay.sh-written
```

New gate name added to `GATE_ORDER`? **No** — `canvas-replay-attestation` is a *modifier* gate that runs inside `post-implement` health checks, not a pipeline phase. Keeps GATE_ORDER stable; opt-in mode doesn't add phases for canonical/structural users.

## Failure cases this mode unlocks vs leaves untouched

**Unlocks** (would have been unclonable):
- kayiseisagu (`.bg-canvas` + `.music-sphere`, 4 iter AE 60k–440k)
- juanmora GSAP+ScrollTrigger canvas overlays
- any portfolio whose visual identity is `<canvas>` + WebAudio + RAF

**Still fails** (canvas-replay does not fix):
- mersi-style Webflow 1M-px horizontal scroll (architectural, not canvas)
- ordrhealth (CDN asset misses, not canvas)
- DRM-protected canvas (license unobtainable → attestation impossible)
- Auth-gated sites (canvas behind login)
- Paid commercial fonts (still need substitution decision)

Roughly: opt-in helps the ~5–10% of refs whose ONLY remaining gap is canvas pixel-fidelity AND whose canvas source has a permissive license OR explicit owner permission.

## Implementation surface (estimate)

| File | Change | Lines |
|---|---|---|
| `ui_clone/state.py` | extend `closeout_policy` enum + `_to_disk_payload` | ~10 |
| `ui_clone/hooks/section_gate.py` | `_enforce_canvas_replay_stamp` + closeout routing | ~80 |
| `ui_clone/gates/post_implement.py` | `canvas-replay-attestation` sub-check + AE relief for canvas sections | ~40 |
| `scripts/verify/check-canvas-replay.sh` | stamp writer (mirrors `check-converged.sh`) | ~60 |
| `skills/ui-reverse-engineering/canvas-replay-mode.md` | operator-facing doc | ~100 |
| `tests/test_state.py` + `tests/gates/test_post_implement.py` + `tests/hooks/test_section_gate.py` | contract tests | ~120 |
| `CHANGELOG.md` + version bump (v0.7.0 — minor; adds opt-in mode) | metadata | ~20 |

**Total ~430 lines**. Single-PR feasible but non-trivial; ~2-3h focused work.

## Risks

1. **License laundering**: operator falsifies attestation. Mitigation: `attestedBy` field + the attestation file is text and visible; this is honor-system anyway, no engineering mitigation possible for malicious operators.
2. **CORS / CSP**: ref's canvas JS may refuse to load cross-origin. Mitigation: out of scope; document as known limitation and let operator handle (CSP relax in their own dev server config).
3. **Mode confusion**: existing `closeout_policy` already has 2 values; adding a 3rd increases surface. Mitigation: explicit gate (`canvas-replay-attestation`) fails closed; absent attestation = mode disabled even if policy field is set.
4. **Visual fidelity isn't "complete"**: even with canvas-replay, the impl still relies on ref's bundle. If ref disappears, impl breaks. Document as architectural trade-off; not a hidden cost.

## Decision point

This doc is design-only. Implementation requires:
- Operator confirmation that this opt-in path matches their needs (vs alternatives: keep 30-min cap as-is, or build approximate canvas tooling per common patterns)
- Codex architectural review on the gate routing (closeout policy is the most-touched state field; adding a third value is structural)

No code changes shipped with this commit.
