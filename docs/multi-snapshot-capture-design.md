# Multi-snapshot capture — design

> Status: **IMPLEMENTED** (Phase A: commit e2e5657, Phase B: 509a498,
> Phase C: abc5328, state-coverage gate: this commit). Surfaces the
> largest capture-side architectural gap revealed by the 26-site loop
> (2026-05-24/25). User articulated the fix shape directly: "splash 끝날
> 때까지 capture, splash 끝나면 scroll하면서 capture."
>
> Each phase landed with codex parallel review (memory policy:
> architectural decisions get a second opinion via codex:codex-rescue
> subagent before merge). Phase C review caught a fundamental design
> error — synthetic dispatchEvent does not activate CSS :hover — and
> redirected the implementation to static CSSOM extraction.

## Problem

The current capture pipeline (`scripts/extract/extract-dom.sh`,
`scripts/extract/capture.sh`) takes a **single DOM snapshot** at one
"settled" moment after `agent-browser open`. juanmora ref dir evidence:

```
tmp/ref/juanmora/
├── html/
│   ├── about.json     # section slice from THE one snapshot
│   ├── contact.json
│   ├── footer.json
│   ├── info.json
│   ├── projects.json
│   ├── work.json
│   └── wrapper-hero.json
└── body-state.json    # bodyClass: "body", htmlClass: "lenis w-mod-ix3"
```

`html/*.json` are slices of one snapshot, not per-state captures. What gets missed:

| State variation | Single-snapshot result | What we should capture |
|---|---|---|
| **Splash overlay** (`is-loading` → `is-loaded` body class) | Splash already gone OR splash blocking content | DOM at `is-loading` + DOM at `is-loaded` (both, so impl can diff and replicate the transition) |
| **Splash-gated reveals** (hero entry animations triggered by body class change) | Initial `from` state OR final state, never the bridge | DOM at each class-transition moment |
| **Scroll-driven state** (sticky navbar shrink, scroll-progress fades, parallax position) | Top-of-page state only | DOM at 0% / 25% / 50% / 75% / 100% scroll positions |
| **Lazy-loaded sections** (IntersectionObserver-mounted) | Section absent if it's below the fold | Section present after scroll past its trigger |
| **Time-based state** (after-N-seconds reveals, scheduled animations) | Pre-reveal state | DOM at t=0, t=1s, t=3s, t=5s |

raviklaassens (WebGL UnicornStudio canvas) is the extreme case: the canvas
frame at t=0 is different from t=2s is different from scroll=50%. Single
snapshot captures one frame, agent has to guess the rest.

## Design — 2-phase capture

User-articulated sequence:

### Phase A: splash transition snapshots (time-based)

> **Revision (codex review 2026-05-25)**: original sketch used 50 shell-side
> `agent-browser eval` calls @ 100ms each. Codex flagged this RISKY: CLI
> round-trip overhead, no latency guarantee, and `splash-bypass.sh` already
> uses a single in-page Promise loop pattern. Reworked below.

**Single agent-browser eval invocation** with an in-page state-hash poller:

```bash
agent-browser --session "${SESSION}-states" eval --stdin --json <<'JS'
(async () => {
  const states = [];
  const startedAt = performance.now();
  let lastHash = null;
  let lastChangeAt = startedAt;
  const computeHash = () => {
    const html = document.documentElement;
    const body = document.body || {};
    // State hash inputs (codex item b — class is one signal, not THE signal):
    //   html.className + body.className
    //   scroll lock detection (overflow:hidden on html/body, position:fixed)
    //   full-screen overlay presence (any element ≥ 95% viewport with z-index > 100)
    //   DOM length (document.body.outerHTML.length)
    //   computed-style fingerprint of top-3 above-the-fold elements (color, opacity, transform, visibility)
    const composite = [
      html.className, body.className,
      getComputedStyle(html).overflow, getComputedStyle(body).overflow,
      detectFullScreenOverlay(),
      (document.body.outerHTML || '').length,
      fingerprintTopElements(),
    ].join('|');
    // cheap djb2 hash (SubtleCrypto async would complicate the loop)
    let h = 5381;
    for (let i = 0; i < composite.length; i++) h = ((h << 5) + h) + composite.charCodeAt(i);
    return [h >>> 0, composite];
  };

  while ((performance.now() - startedAt) < 5000) {
    const [hash, composite] = computeHash();
    const now = performance.now();
    if (hash !== lastHash) {
      states.push({
        ts_ms: Math.round(now - startedAt),
        hash,
        bodyClass: document.body.className,
        htmlClass: document.documentElement.className,
        compositeDigest: composite.slice(0, 200),  // truncated for size
      });
      lastHash = hash;
      lastChangeAt = now;
    } else if ((now - lastChangeAt) >= 2000) {
      break;  // 2s of stability → splash done
    }
    await new Promise(r => requestAnimationFrame(() => setTimeout(r, 100)));
  }
  return {
    states,
    durationMs: Math.round(performance.now() - startedAt),
    polls: states.length,
    timedOut: (performance.now() - startedAt) >= 5000,
    reason: states.length === 0 ? 'no-change' :
            (performance.now() - startedAt) >= 5000 ? 'wall-clock-cap' :
            'stable-2s',
  };
})();
JS
```

**Compact-by-default snapshot policy** (codex item c — OVER-ENG to write
full DOM 50×):
- Always: `states/splash/trajectory.json` — array of state transitions
  with hash, class strings, ts_ms, compositeDigest
- Always: `states/splash/0ms.json` + `states/splash/settled.json` — full
  `outerHTML` (the bookend states)
- On structural mutations only (DOM length delta > 20%): full snapshot
  `states/splash/<NNNms>.json`. Otherwise just the trajectory entry.

**Session handling** (codex item d): use `${SESSION}-states` derived
session name. Either:
- Run capture-states.sh from within capture.sh at a deterministic point
  (same parent session, sequential), OR
- Standalone caller passes a unique session name; capture-states.sh
  opens its own page navigation.

**Output metadata** (codex item e): `states/splash/summary.json` with
`{checked: true, durationMs, polls, timedOut, reason}` so downstream
gates distinguish "checked + static" from "capture failed" from "legacy
ref dir without states/".

### Phase B: scroll-progress snapshots (position-based)

After Phase A signals "splash settled":

1. Compute scroll depth: `document.documentElement.scrollHeight - window.innerHeight`
2. For each percentage in [0, 10, 25, 50, 75, 90, 100]:
   - `window.scrollTo(0, depth * pct / 100)` + 500ms settle
   - Snapshot DOM → `html/scroll-<pct>pct.json`
   - Snapshot computed style of viewport-visible sections → `scroll-state-<pct>pct.json`
3. Emit `scroll-state-trajectory.json` linking scroll percentage to
   visible section selectors + computed style deltas

**Why 7 stops not 5**: 0 and 10 differentiate "above the fold" from
"first scroll" (which often triggers IO reveals); 90 and 100 differentiate
"approach footer" from "fully scrolled" (some sites snap-lock at 100%).

### Phase C: hover-state snapshots (grid + element-targeted)

User-articulated addition: hover state is invisible to a static or scroll
capture. Two complementary passes:

**C1: 10×10 viewport grid sweep**

For each of 100 grid cells (viewport divided 10×10):
1. Move agent-browser cursor to cell center
2. 200ms settle
3. Diff DOM against rest-state snapshot (Phase A's `settled.json`)
4. If diff non-empty (any element class/style changed), emit
   `states/hover/grid-<row>-<col>.json` + the diff manifest
5. Skip cells with no diff (most cells produce nothing)

Expected output: 5-30 grid snapshots per site (sites with pervasive
hover effects emit more; portfolio cards typically 3-8 cells matter).

**C2: element-targeted hover** (selector-driven, complements grid)

1. Enumerate elements with detectable hover signal:
   - `:hover` rules in `styles.json`
   - `addEventListener('mouseenter'|'mouseover')` grep in `bundles/`
   - Computed `cursor: pointer` from a quick scroll pass
2. For each, dispatch hover via `agent-browser eval` → DOM snapshot
3. Output `states/hover/elem-<selector-hash>.json`

C1 catches positional hovers (cursor-near interactions like spotlight
follows, hover-text fields with parallax). C2 catches element-discrete
hovers (button hover-fade, card lift, link underline grow).

**Stop conditions**: C1 hard cap at 100 cells (fixed grid); C2 hard cap
at 50 elements (top-50 by computed hover signal score).

### Phase A+B+C output

```
tmp/ref/<c>/
├── html/                       # existing single snapshots stay (back-compat)
│   ├── about.json
│   └── …
├── states/                     # NEW
│   ├── splash/
│   │   ├── 0ms.json
│   │   ├── 100ms.json
│   │   ├── 300ms.json
│   │   ├── settled.json        # final stable state
│   │   └── trajectory.json     # ordered class transitions
│   ├── scroll/
│   │   ├── 0pct.json
│   │   ├── 10pct.json
│   │   ├── 25pct.json
│   │   ├── 50pct.json
│   │   ├── 75pct.json
│   │   ├── 90pct.json
│   │   ├── 100pct.json
│   │   └── trajectory.json     # per-pct visible-section deltas
│   └── hover/
│       ├── grid-3-4.json       # 10×10 cell at row 3, col 4 (user-articulated)
│       ├── grid-5-7.json       # only cells with non-empty diff emitted
│       ├── elem-a1b2c3.json    # selector-targeted (hash of selector)
│       └── manifest.json       # mapping selector → snapshot file
```

Existing `html/`, `body-state.json` stay unchanged — back-compat for
all current consumers (`gate.py`, `verification-plan.json`,
`section-compare.sh`). The new `states/` directory is additive.

## Consumer changes

Few, isolated:

- **`transition-spec.json` extraction** (bundle-analysis.md) — read
  `states/splash/trajectory.json` to find class transitions, generate
  spec entries for splash reveals automatically. Today the agent
  hand-writes these from incomplete signals.
- **`transition-compare.sh`** — when comparing scroll-driven entries,
  use `states/scroll/<pct>pct.json` for ref instead of single snapshot,
  match against impl screenshot at same `pct`.
- **`section-compare.sh`** — optionally compare scroll-state slices
  (`states/scroll/50pct.json` ref-section vs impl-section at 50%)
  for sections marked `scroll-driven: true`.
- **New gate `state-coverage`** — fail post-implement when ref has
  `states/splash/trajectory.json` with N transitions but impl source
  has 0 splash-class hooks (`is-loading`, `is-loaded` selectors absent
  from impl/src/**).

## Edge cases

- **No splash, no scroll content** (single-page hero only): Phase A
  detects 0 class transitions, emits `splash/settled.json` = `splash/0ms.json`.
  Phase B detects scrollHeight ≤ viewportHeight, emits only `scroll/0pct.json`.
  Result is functionally identical to single-snapshot, just with extra
  files marking "yes, we checked and the page is static."

- **Infinite scroll** (twitter/reddit feed): scrollHeight changes
  during scroll. Mitigation: measure once before Phase B starts, use
  that snapshot of scrollHeight for the percent math. Detected
  late-loaded content shows as new sections in scroll/75pct.json
  vs scroll/25pct.json; the diff itself is signal.

- **WebGL canvas** (raviklaassens UnicornStudio): canvas pixel content
  varies frame-to-frame regardless of state. Multi-snapshot captures
  *DOM* state, not canvas frame parity. Canvas-replay mode design
  (`docs/canvas-replay-mode-design.md`) handles that orthogonally.

- **Splash never ends** (broken site): Phase A wall-clock cap stops at
  5s, emits `states/splash/settled.json` with whatever the class was
  at 5s + a warning marker `"splash_timed_out": true`. Downstream gate
  surfaces this as "ref has a stuck splash; treat sections that don't
  appear as out-of-scope."

## Implementation surface (as shipped)

| File | Status | Actual LOC | Estimate |
|---|---|---|---|
| `scripts/extract/capture-states.sh` (Phase A) | shipped e2e5657 | 245 | ~250 |
| `scripts/extract/capture-scroll.sh` (Phase B) | shipped 509a498 | 290 | (split from A+B estimate) |
| `scripts/extract/capture-hover.sh` (Phase C) | shipped abc5328 | 310 | ~300 |
| `ui_clone/gates/state_coverage.py` | this commit | ~250 | ~80 (expanded for 3-phase coverage) |
| `ui_clone/state.py:GATE_ORDER` insertion | this commit | +1 | ~5 |
| `ui_clone/gates/__init__.py` rebind | this commit | +2 | ~3 |
| `ui_clone/gates/base.py` stub | this commit | +2 | (not in original estimate) |
| `tests/test_capture_states.py` (Phase A tests) | shipped e2e5657 | 268 | ~250 |
| `tests/test_capture_scroll.py` (Phase B tests) | shipped 509a498 | 310 | (split) |
| `tests/test_capture_hover.py` (Phase C tests) | shipped abc5328 | 344 | (split) |
| `tests/gates/test_state_coverage.py` | this commit | ~280 | ~150 |
| `docs/gates.md` state-coverage entry | this commit | +1 | (added) |
| `tests/hooks/test_section_gate.py` (fixture bump for new gate) | this commit | +5 | (collateral) |
| `tests/test_state.py::test_gate_order_contains_all_gates` | this commit | +1 | (collateral) |

Total shipped ≈ **2,300 lines** across 4 commits. Largest single feature
since v0.6.0. Justified by:
- Addresses the dominant unresolved fidelity gap (splash + scroll-driven
  state visible across every motion-heavy site in the 26-site loop)
- Backward-compat: existing artifacts unchanged, new gate skips silently
  when `states/` directory is absent (capture-phase v1 pipelines)

## Risks

1. **Phase A timing flakiness**: 100ms polling may miss fast transitions
   on slow machines, or trigger false positives on transitions that
   happen pre-render. Mitigation: each snapshot includes a
   `requestAnimationFrame` settle + computed-style diff threshold.

2. **Phase B scroll triggers may double-fire**: scroll-driven animations
   that fire on entering AND leaving viewport produce ambiguous mid-scroll
   states. Mitigation: scroll-down only, no scroll-up; trajectory.json
   records direction.

3. **Storage cost**: a 5-section ref site goes from ~5 JSON files to
   ~5 + 7 (scroll) + 5 (splash avg) = ~17 files. 3× growth. Mostly text
   JSON, average size 5-50KB each → 100-800KB per ref dir total. Negligible.

4. **Gate addition (`state-coverage`) is structural**: every site's
   GATE_ORDER changes. v0.8.0 minor bump because no public API removed.
   Codex review of GATE_ORDER drift is mandatory before merge.

5. **WebGL/canvas sites still need canvas-replay mode** — multi-snapshot
   captures DOM, not canvas pixels. This design is necessary but not
   sufficient for full visual parity on canvas-driven sites.

## Codex review checklist (pre-implementation)

- [ ] capture-states.sh sequence safe under agent-browser session reuse?
- [ ] state-coverage gate's "splash hook absent in impl" detection sound?
  (similar shape to F1 anti-cheat + F spec-bundle-grounding)
- [ ] GATE_ORDER insertion point — between `pre-generate` and
  `post-implement` correct, or should it be later?
- [ ] back-compat — old ref dirs without `states/` continue passing
  state-coverage gate as skip?

This design defers implementation pending codex review of the above
checklist + operator confirmation that the value warrants 780 lines /
v0.8.0 release.
