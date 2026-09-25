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

# Routed section and transition verification
bash "$SCRIPTS_DIR/section-compare.sh" <orig> <impl> <session> "$(pwd)/tmp/ref/<component>"
bash "$SCRIPTS_DIR/transition-compare.sh" <orig> <impl> <session> "$(pwd)/tmp/ref/<component>"
```

Before every compare, scan the implementation for fixed dev banners, badges, overlays, widgets, or labels absent from the reference. Use the implementation's documented hide flag in every command; a fixed chrome hotspot is an invalid comparison input, not a product fix.

## Evidence reuse

Freeze a completed dynamic reference with `RECATCH_REF=0` during repair. Re-capture it only when the evidence is demonstrably stale. Recapture the implementation after source changes.

`ONLY_IF_CHANGED=1` is allowed only on the second or later section comparison when the reference remains pinned, implementation source is unchanged, and the prior `sections/result.txt` is a complete measured pass:

```bash
RECATCH_REF=0 ONLY_IF_CHANGED=1 IMPL_SRC_DIR=<impl-src-root> \
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

## Dynamic content masking

Canvas and video clocks can make raw pixels nondeterministic. Mask only regions already identified as dynamic, on both sides, while preserving layout:

```bash
EXCLUDE_DYNAMIC=1 bash "$SCRIPTS_DIR/section-compare.sh" \
  <orig> <impl> <session> "$(pwd)/tmp/ref/<component>"
```

`DYNAMIC_SELECTORS` defaults to `canvas, video`. Prefer `"dynamic": true` on the corresponding `transition-spec.json` entry so targets are evidence backed. Selectors containing single or double quotes are rejected; use class, id, or bare attribute selectors. Masking does not waive runtime, trajectory, media, or semantic verification, and must never hide an unexplained static mismatch.

## Output and cleanup

Read summary artifacts before raw JSON. Keep browser eval output in files. Close each owned session by name before returning, including on errors; never use `close --all`.
