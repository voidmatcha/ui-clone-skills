# Whole-page dSSIM — capture the impl at the ref proxy's width

Reusable methodology gotcha for whole-page structural comparison. Kept in-repo
(not machine-local agent memory) so it survives on any checkout/machine.

## The trap

`skills/visual-debug/scripts/dssim-compare.sh` compares `<dir>/static/ref/*.png`
against `<dir>/static/impl/*.png` by basename. When the two images differ in
size it force-resizes the impl to the ref dims:

```sh
convert "$IMPL_FILE" -resize "${W}x${H}!" "$RESIZED"   # note the "!": non-uniform
```

The `!` forces exact dims, so a **width** mismatch is corrected by a horizontal
stretch. On a layout-sensitive page (product grids, multi-column footers) a
horizontal stretch shifts every column boundary, fabricating a
column-registration penalty that has nothing to do with real fidelity.

If the impl is captured at a viewport **narrower** than the ref proxy (e.g. impl
at 1280 vs a 1440-wide proxy), the stretch to proxy width distorts the impl's
grid packing. Whichever build happens to stretch into closer accidental
alignment "wins" the dSSIM, independent of actual per-element correctness.

## The rule

Capture the impl at the **same pixel width as the ref proxy** before running the
whole-page dSSIM. Only the vertical axis (doc height) should ever be normalized
by the resize, because impl and ref doc heights legitimately differ.

With `agent-browser` the viewport command is:

```sh
agent-browser set viewport <W> <H> --session <name>   # NOT `resize` / `viewport`
```

`resize` and a bare `viewport` both return "Unknown command"; the working form
is `set viewport`. Verify it took with
`agent-browser eval '(() => window.innerWidth)()'` — headless sessions silently
default to 1280 otherwise (the loop-145 viewport confound).

## Worked evidence (eBay F-1 vs F-2, 2026-07-05)

F-2's nested run reported whole-page dSSIM **worsening** 0.688 (F-1) -> 0.786
(F-2) after the grid-tile sizing + image-fidelity reader fixes, implying the
fixes hurt. They did not. Both impls had been captured at 1280 then stretched to
the 1440-wide proxy.

Re-measuring both impls captured at **native 1440** (`set viewport 1440 900`)
against the same fixed 1440x4500 proxy:

| impl | 1280-capture -> 1440 stretch | native 1440 capture |
|------|------------------------------|---------------------|
| F-1  | 0.688 | 0.747 |
| F-2  | 0.786 | 0.765 |
| gap  | +0.098 | **+0.018** |

~82% of the apparent F-1 -> F-2 regression was the horizontal-stretch artifact.
The tile-sizing fixes did not meaningfully change whole-page structure. Do not
chase this phantom regression.

### The ~0.75 residual was ALSO a proxy artifact — the reused proxy is stale

An initial reading blamed the shared ~0.75 residual on a real ~400px footer
deficit (`DpGlobalFooter` compression). That was WRONG, and the correction is the
more important lesson: **the reused fixed 1440x4500 proxy does not match the ref
being cloned.**

Verified 2026-07-09 against live eBay at 1440 (`agent-browser eval`
`document.documentElement.scrollHeight`), cross-checked with this run's own
`component-map.json`:

- Live eBay real docH @1440 = **3449** (footer `top=3027, bottom=3449`) — matching
  `component-map.json` (DpGlobalFooter `top 3027, height 422, bottom 3449`)
  exactly. The capture is accurate and the footer is fully present.
- Impl docH @1440 = **3607** — i.e. ~158px TALLER than the real ref, not shorter.
  Footer heights match (ref 422 ≈ impl 437). There is NO footer deficit.
- The reused proxy is **4500** — 1051px taller than the real ref (3449). Its
  bottom ~1050px (the "missing content" + "blank padding" seen earlier) has no
  counterpart in the current ref DOM. It is a stale artifact from an earlier loop
  (E-1/D-1) when eBay was taller / captured differently.

**Consequence:** every whole-page dSSIM in this series (E-1 0.573, F-1 0.688,
F-2 0.786, and the native-1440 recomputes above) was measured against a ground
truth ~30% too tall AND width-mismatched. They are NOT a meaningful fidelity
series. The apparent ceiling was a measurement artifact top to bottom.

**The real methodology fix:** never reuse a fixed whole-page proxy across runs
when the ref can change. Regenerate the ref proxy from each run's OWN capture, or
assert the proxy height ≈ that run's `component-map.json` ref docH and fail on a
large mismatch. Until a proxy is regenerated at the correct height (~3449, native
1440), there is no valid whole-page dSSIM number for this clone — and no evidence
of any real footer/height gap.
