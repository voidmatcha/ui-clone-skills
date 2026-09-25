# Manual capture and implementation comparison

Read this file only for a targeted retry named by a structured driver failure,
or when `<local-url>` requires matching reference and implementation captures.

## Routing

- Static page or section evidence: use the canonical capture helpers and flat
  `$OUT_DIR/static/{ref,impl}` layout.
- Trigger detection and region schema: read [../detection.md](../detection.md).
- Scroll, hover, click, mousemove, or timer capture: read
  [../capture-transitions.md](../capture-transitions.md).
- Click-driven content replacement: follow the conditional link from that file
  to [../capture-click-content-swap.md](../capture-click-content-swap.md).
- Local implementation comparison: use `../../visual-debug/verification.md` Phase
  D only, then [../comparison-page.md](../comparison-page.md).
- Reference-only evidence page requested by the user: read
  [../report-page.md](../report-page.md).

## Fixed-viewport capture

Use the same viewport, wait, scroll sequence, hover duration, and pointer path on
reference and implementation. Navigation must succeed before evidence capture.
Keep the viewport at 1440x900 unless the active capture plan specifies another
fixed viewport. Set it after opening the page, then calibrate readiness from the
actual splash or delayed gate; do not use a large arbitrary wait.

Splash calibration, once per project: read `html`/`body` classes at t=0 and after
`wait 15000`. If t=0 has `is-loading|loading|preloading|locked` and the later read
does not, wait = splash visible duration + 500ms (not framework init time). For
splashes over 5s, also set `NEXT_PUBLIC_SPLASH_TEST=true` (or the impl's
equivalent) so iterations skip the loader. Reuse the value as `WAIT_REF`/`WAIT_IMPL`
for `section-compare.sh`; never bump to `wait 30000` "to be safe". Bare sites keep
`wait 3000`.

Scroll rules: take `scrollType`/`scrollSelector` from the `detection.md` eval.
Screenshots use instant `scrollTo(0, Y)` on `window` or `scrollSelector`; videos
use a native `scrollTo` loop, or `mouse wheel <deltaY>` for custom scrollers.

For a section, scroll to its planned location, wait for the named state, read the
actual clamped scroll position, save a viewport screenshot to an absolute path,
and crop from that image. Use `section-compare.sh` or `section_capture.py` for
tall sections and bottom clamps. Never resize the viewport to section height.

An element selector screenshot is valid only for a visible, stable element.
Hover evidence uses two viewport screenshots followed by identical post-crops;
pairing `hover <selector>` with `screenshot <selector>` can move the element away
from the pointer and create a false zero-diff result.

## Transition and comparison contract

Classify the trigger before recording. Every region with `triggerType` must list
the exact evidence paths in `artifacts`. After capture, run:

```bash
bash "$PLUGIN_ROOT/skills/visual-debug/scripts/capture-artifact-inventory-check.sh" "$OUT_DIR"
```

With `<local-url>`, capture identical sequences, run verification Phase D only,
and build `compare.html`. `result: pass` plus zero mismatches returns to the
caller pipeline. Otherwise return `pixel-perfect-diff.json`, clips, and the
comparison page to `visual-debug`; do not change the implementation here.

Viewport screenshots must decode, be nonblank, exceed 10KB, and show expected
content. Element crops must be nonempty. Eval output must be valid JSON. Videos
must exceed 50KB and one second. A zero-difference state pair is a capture defect
until disproven. Retry the named capture once; do not rebuild the full corpus.

## Targeted troubleshooting

- A blank pinned or sticky section captured with a tall viewport is a capture
  defect; recapture at its scroll position in a fixed viewport.
- For a CAPTCHA, retry headed. Remove cookie, banner, or modal overlays before
  capture when they are not part of the requested state.
- If `scrollHeight` equals the viewport, use the detected custom scroll
  container. Use trusted wheel input for animated custom scrolling.
- Start scrolling only after recording begins; trim leading and trailing dead
  time from the saved video.
