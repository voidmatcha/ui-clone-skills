# Compare & Fix — Phase C/E/H

> Split from `verification.md` for readability. This phase runs after Phase A (reference capture) and Phase B (implementation capture).
> **After this phase:** proceed to Phase D (Pixel-Perfect Diff) in `verification.md`.


> **Use the `visual-debug` skill for automated comparison.** Instead of manually running `compare` commands below, invoke `/visual-debug` which provides `batch-scroll.sh` (captures both sites at identical scroll positions) and `batch-compare.sh` (outputs a markdown pass/fail table). This is faster and uses zero vision tokens.

**Token budget rule:** All image comparisons use AE/SSIM (zero tokens). Only read images with the Read tool for: (1) one-time spot-checks (Phase A gate), (2) diagnosing AE/SSIM failures via diff images, (3) the final VLM sanity check (1 pair). Never read ref+impl image pairs side-by-side for comparison — use pixel diff instead.

## Triage — artifact vs real mismatch (BEFORE any code fix)

When AE on a section comes back **unusually large** (≥ ~50K, or any number that "feels too big to be one CSS bug"), the most expensive failure mode is implementing a fix for a defect that doesn't exist. Reference + impl can both be correct while the *capture* is wrong. Rule out the four common artifact causes below before touching code:

| Suspect | How to confirm in seconds | Fix at the harness, not in impl |
|---|---|---|
| **A. Capture-timing artifact** (snapshot taken mid-animation on one side, settled on the other) | Re-run with a longer settle: `WAIT_SCROLL_SETTLE=1.5 WAIT_REF=12000 WAIT_IMPL=12000 bash section-compare.sh ...`. AE drops by an order of magnitude → it was timing. | Raise the relevant `WAIT_*` env var permanently for this project. Do NOT add `setTimeout`s in impl to "wait for ref". |
| **B. Programmatic scroll didn't fire custom-scroll handlers** (Lenis / Locomotive / smooth-scroll sites — `window.scrollTo()` skips their scroll-event pipeline, leaving section-state classes such as `is-loaded`, `is-active`, project-scoped `-postX` flags stale on one side and current on the other) | On the failing scroll position, eval `document.body.className` and `document.documentElement.className` on both ref and impl. If the state classes differ, the scroll never propagated. | Switch the harness to native scroll (`agent-browser --session <s> mouse wheel`) for that target, or accept the desync and add the affected section to a substitute/skip list. |
| **C. Per-viewport init lock** (bundle reads `window.innerWidth` once at init; resizing the session after `open` does not re-evaluate. Common with GSAP `ScrollTrigger`, `matchMedia` once-and-cache, container queries baked at boot) | Open a fresh session at the *target* viewport (not 1440 → resize 375). If AE collapses, init was the issue. | Always recreate the session per viewport in multi-viewport sweeps — never `set viewport` after `open` for the run that's compared. |
| **D. Lazy-load not yet triggered on one side** (lazy `<img>` / `loading="lazy"` / IntersectionObserver-gated content was never scrolled past on one side, leaving height differences and missing pixels) | Force a full-page traverse (`window.scrollTo(0, document.body.scrollHeight); await wait(800); window.scrollTo(0, 0)`) before the section measurement. If heights now match, it was lazy load. | Add the traverse to `layout-health-check.sh` / pre-section-compare warmup; never measure section heights at `scroll=0` on lazy-load-heavy sites. |

**Decision rule:** if any one of A–D explains the diff, fix the harness and re-run before reading the diff image. Reading a diff image first (or worse, editing impl CSS) wastes tokens and can introduce real bugs while chasing an artifact.

If A–D all check out clean and AE is still large → it's a real mismatch; proceed to `auto-diagnose.sh` and the rest of the fix protocol below.

### Forbidden fixes (don't game the metric)

Once a real mismatch is confirmed, the temptation is to make the *number* go down by editing layout values until it does. Don't. The following "fixes" satisfy the metric while leaving the actual bug in place — they cost a re-discovery cycle later:

- **Force impl total page height to match ref** by hardcoding `min-height` / `max-height` on a footer/spacer to close a 2px gap. `batch-scroll`'s percentage-based capture only makes sense when both sides have the same content at the same % offset; if heights diverge, the right answer is `section-compare.sh` (pairs by section identity), not `min-height: 38rem` on an `<img>`.
- **Match a section by adding fixed pixel padding** to a container whose ref uses `dvh` / `clamp()` / a sticky pin. The pixel hack passes at one viewport and breaks at every other.
- **Hardcode a section translate offset** to align scroll positions when the real mismatch is a missing pinned section above. The pin is doing the layout work; faking the offset hides the missing pin.
- **Tighten AE thresholds** (raise the per-image cap from 500 to 5000) to make a section that "looks close enough" pass. The threshold is a contract — bump it only with explicit user approval and a one-line note in `pixel-perfect-diff.json` saying why.
- **Crop the diff** to exclude a failing region. If a region is excluded from the gate, it is not verified — the gate hasn't passed, it's been narrowed.

Rule: every layout / metric edit must answer "what does the **ref** do here?" with a measurement. If the answer is "I don't know, but this number makes the diff smaller," revert the edit and run `auto-diagnose.sh` on the failing region instead.

**Three comparison tables — one per capture type.** All three must pass.

### C1: Static screenshot comparison (AE diff)

Run pixel diff for each position — do NOT read images with the Read tool for comparison (wastes tokens).

```bash
for POS in top 25pct 50pct 75pct bottom; do
  compare -metric AE \
    tmp/ref/<component>/static/ref/${POS}.png \
    tmp/ref/<component>/static/impl/${POS}.png \
    tmp/ref/<component>/static/diff/${POS}.png 2>&1
done
# → 0 = pass. Non-zero = fail (diff image shows mismatched pixels)
```

**On AE FAIL — use `auto-diagnose.sh` instead of reading diff images:**

```bash
SCRIPTS="${VISUAL_DEBUG_SCRIPTS_DIR:-${PLUGIN_ROOT:+$PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS="${SCRIPTS:-${CODEX_PLUGIN_ROOT:+$CODEX_PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS="${SCRIPTS:-${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/visual-debug/scripts}}"
[ -n "$SCRIPTS" ] || { echo "Set VISUAL_DEBUG_SCRIPTS_DIR or PLUGIN_ROOT" >&2; exit 1; }
bash "$SCRIPTS/auto-diagnose.sh" <session> <orig-url> <impl-url> \
  tmp/ref/<component>/static/diff/<pos>.png
```

This extracts mismatch coordinates from the diff image, identifies DOM elements via `elementFromPoint`, and runs `computed-diff` on those elements — all in text, **zero vision tokens**. Only fall back to reading the diff image with `Read` if `auto-diagnose.sh` finds no elements (e.g., background-only difference).

| Position | AE | Status |
|----------|----|--------|
| Top      |    | ✅/❌  |
| 25%      |    | ✅/❌  |
| 50%      |    | ✅/❌  |
| 75%      |    | ✅/❌  |
| Bottom   |    | ✅/❌  |

**Responsive comparison:** see `responsive-detection.md` C-R table (covers all detected breakpoints).

### C2: Scroll video frame comparison (SSIM batch)

Compare all extracted 60fps frames using ffmpeg SSIM — no LLM image reading. Only inspect frames that fail SSIM threshold.

```bash
mkdir -p tmp/ref/<component>/frames/diff

# Batch SSIM comparison of all frame pairs
FRAME_COUNT=$(ls tmp/ref/<component>/frames/ref/scroll-*.png | wc -l)
for i in $(seq -f "%06g" 1 $FRAME_COUNT); do
  SSIM=$(ffmpeg -i tmp/ref/<component>/frames/ref/scroll-${i}.png \
                -i tmp/ref/<component>/frames/impl/scroll-${i}.png \
                -lavfi "ssim" -f null - 2>&1 | grep -oP 'All:\K[0-9.]+')
  if (( $(echo "$SSIM < 0.995" | bc -l) )); then
    echo "FAIL frame ${i}: SSIM=${SSIM}"
    compare -metric AE \
      tmp/ref/<component>/frames/ref/scroll-${i}.png \
      tmp/ref/<component>/frames/impl/scroll-${i}.png \
      tmp/ref/<component>/frames/diff/scroll-${i}.png 2>/dev/null
  fi
done
# → Only read diff images for FAIL frames when diagnosing root cause
```

If no frames fail → C2 passes. If frames fail, read **only the diff images** of failing frames to diagnose the mismatch region.

### C3: Transition comparison

**For css-hover / js-class / intersection — clip screenshot diff:**

```
| Region   | State  | Ref                                   | Impl                                   | Match? | Issue |
|----------|--------|---------------------------------------|----------------------------------------|--------|-------|
| <name>   | idle   | transitions/ref/<name>-idle.png       | transitions/impl/<name>-idle.png       | ✅/❌  |       |
| <name>   | active | transitions/ref/<name>-active.png     | transitions/impl/<name>-active.png     | ✅/❌  |       |
```

Run pixel diff for each pair:
```bash
compare -metric AE tmp/ref/<component>/transitions/ref/<name>-idle.png tmp/ref/<component>/transitions/impl/<name>-idle.png /dev/null 2>&1
compare -metric AE tmp/ref/<component>/transitions/ref/<name>-active.png tmp/ref/<component>/transitions/impl/<name>-active.png /dev/null 2>&1
# → 0 = pass
```

**For scroll-driven / mousemove / auto-timer — frame comparison (SSIM batch):**

Same SSIM batch approach as C2 — compare all extracted frames automatically, only inspect failures.

```bash
# Per-transition SSIM batch (example: carousel)
FRAME_COUNT=$(ls tmp/ref/<component>/transitions/ref/carousel-*.png | wc -l)
for i in $(seq -f "%06g" 1 $FRAME_COUNT); do
  SSIM=$(ffmpeg -i tmp/ref/<component>/transitions/ref/carousel-${i}.png \
                -i tmp/ref/<component>/transitions/impl/carousel-${i}.png \
                -lavfi "ssim" -f null - 2>&1 | grep -oP 'All:\K[0-9.]+')
  if (( $(echo "$SSIM < 0.995" | bc -l) )); then
    echo "FAIL frame ${i}: SSIM=${SSIM}"
  fi
done
# → Only read diff images for FAIL frames when diagnosing root cause
```

### Fix priority (severity-based)

`section-compare.sh` classifies each defect by severity. **Always fix in this order:**

| Severity | Icon | Meaning | Fix first? |
|---|---|---|---|
| **critical** | 🔴 | Section missing, layout broken, SVG text lost, height ratio >3x | Yes — blocks all other work |
| **major** | 🟠 | Visible diff (color, font, spacing), AE > threshold | After critical |
| **minor** | 🟡 | Sub-pixel diff, anti-aliasing, AE 500–2000 | Last, or skip if Phase E approves |

Do NOT fix minor issues while critical ones exist — critical fixes often resolve major/minor issues as side effects (e.g., fixing a missing section also fixes its child element diffs).

### Fix protocol

**For each ❌ (in severity order — critical first):**
1. **Run 10-point score first** (see `../ui-reverse-engineering/style-audit.md` scoring section). This tells you what category to fix.
2. Write one sentence naming the root cause before touching any code: _"The gap exists because X"_
3. Check if the property belongs to a design bundle (`design-bundles.json`). If yes, verify all sibling properties in the bundle (see `component-generation.md` covariance rules).
4. If you cannot name the cause, run `agent-browser --session <s> eval` to inspect computed styles at that moment
5. Targeted fix → re-run scoring → re-run the specific capture that failed → compare
6. **Score regression → rollback:** If the 10-point score drops after a fix, `git checkout` the component and try a different approach
7. **Repeated FAIL detection:** If the same scroll position or element FAILs twice consecutively with the same AE range (±200), the diagnosis is wrong. Do NOT try the same approach a third time. Instead:
   - Switch from pixel diagnosis to `computed-diff.sh` (or vice versa)
   - Check a different property category (layout → typography → color)
   - Inspect the DOM structure, not just CSS values
   - If 3 consecutive iterations fail on the same position, escalate to the user with the specific property/value diff

### Phase D0: Layout Health Check (MANDATORY before Phase D)

Before pixel-level comparison, verify the implementation's layout structure matches the original:

```bash
SCRIPTS="${VISUAL_DEBUG_SCRIPTS_DIR:-${PLUGIN_ROOT:+$PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS="${SCRIPTS:-${CODEX_PLUGIN_ROOT:+$CODEX_PLUGIN_ROOT/skills/visual-debug/scripts}}"
SCRIPTS="${SCRIPTS:-${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/skills/visual-debug/scripts}}"
[ -n "$SCRIPTS" ] || { echo "Set VISUAL_DEBUG_SCRIPTS_DIR or PLUGIN_ROOT" >&2; exit 1; }
bash "$SCRIPTS/layout-health-check.sh" <session> <orig-url> <impl-url> tmp/ref/<component>
```

**Gate:** Exit code 0 (no critical issues). If exit 1:
- Fix height mismatches first (wrong padding, missing sections, collapsed elements)
- Re-run layout health check
- Only proceed to Phase D when D0 passes

**Why D0 before D1/D2:** Pixel comparison on structurally different pages produces noise, not signal. A page with 3000px of blank space will FAIL every position comparison, but the root cause is a layout bug, not a style bug. D0 catches this in 2 seconds.

### Phase D: Pixel-Perfect Visual Gate (MANDATORY)

> **Read and execute `verification.md` Phase D1 (Visual Gate) AND Phase D2 (Numerical Diagnosis) before declaring any section done — both always run.**

C1–C3 use pixel-level diff (AE/SSIM) to catch visual mismatches. Phase D goes deeper with per-element clip screenshots and getComputedStyle to catch sub-pixel numerical differences that full-page AE/SSIM may miss:
- `font-size: 15px` vs `16px` (passes full-page SSIM, caught by per-element clip + getComputedStyle)
- `letter-spacing` micro-differences
- `font-weight: 400` vs `600` at small sizes

Phase D runs **in parallel with C1** (both use the static loaded page). Phase D1 and Phase D2 always both run.

**For each major section of the component:**

1. Follow `verification.md` Phase D1 — clip screenshot per element per state (idle / active / before / mid / after — by triggerType; for click-toggle: idle + active; for click-cycle: state-0, state-1, ..., state-N), AE/SSIM diff
2. Follow `verification.md` Phase D2 — getComputedStyle all properties, build diff table
3. Produce `tmp/ref/<component>/pixel-perfect-diff.json`

Phase D gate:
```
□ pixel-perfect-diff.json exists for this component
□ all elements Visual Gate status = "pass" (idle / active / before / mid / after — by triggerType)
□ mismatches = 0
```

### Phase H: Self-Healing Loop

> Runs automatically when Phase D fails. Classifies defects, applies targeted fixes, re-verifies. Max 3 iterations.

**Prerequisite:** `section-compare.sh` must have been run (Step 8b). The machine-readable defect list is `<dir>/sections/result.json` (emitted alongside the human table in `result.txt`).

#### H1: Defect Classification

Parse `sections/result.json` (per-section `{name, ae, aePerMpx, severity, status, diffCrop}`):

```bash
python3 -c "
import json
data = json.load(open('<dir>/sections/result.json'))
order = {'missing': 0, 'saturated': 1, 'fail': 2, 'structural-only': 3}
rows = [s for s in data['sections'] if s['status'] not in ('pass',)]
rows.sort(key=lambda s: (order.get(s['status'], 4), -(s.get('aePerMpx') or 0)))
for s in rows:
    print(f\"{s['status']:16s} {s.get('severity') or '?':10s} aePerMpx={s.get('aePerMpx')} {s['name']} diff={s.get('diffCrop')}\")
"
```

**Priority order:** `missing` → `saturated` → `fail` (by descending AE/Mpx) → `structural-only`.

#### H2: Routed Fix (route by status, not per-property tweaks)

- **`missing` or `severity=critical` or `saturated`:** the section's DOM is wrong or absent — per-property LAYOUT/COLOR edits cannot converge. Route to the **per-section DOM rebuild** procedure: rebuild the component's inner structure from the ground-truth `html/<name>.json` (and `section-map.json` entry), preserving real class names, then re-run section-compare for that section. This was the decisive fix path in prior loops (hero AE 218k → 0 came from structure rebuild, not style tweaks).
- **`fail` with moderate AE/Mpx:** targeted property fixes:
  1. **Locate the file.** `grep -rn "<section-class>" src/components/`
  2. **Diagnose textually first:** `auto-diagnose.sh <session> <orig> <impl> <dir>/sections/diff/<name>.png` (hotspot selectors + computed-style diff), then `tree-diff.sh` / `computed-diff.sh` if needed. Do NOT read the PNGs.
  3. **Apply minimal Edit** per finding — exact computed values, not Tailwind approximations; for ANIMATION findings re-read `transition-spec.json`.
  4. **If the defect is in a design bundle** (`design-bundles.json`): fix all co-varying sibling properties together.
- **Capture-suspect sections:** if `sections/capture-confidence.json` lists the section in `suspectSections`, the AE failure may be the capture harness freezing mid-animation — re-run section-compare once before editing code.

#### H3: Re-verify

After fixing all defects in the current batch:

1. Re-capture implementation screenshots (Phase B)
2. Re-run `section-compare.sh <orig-url> <impl-url> <session> "$(pwd)/tmp/ref/<component>"` for fresh `sections/result.txt`
3. Re-run Phase D (Visual Gate + Numerical Diagnosis)

**Outcomes:**
- All pass → exit healing loop, proceed to completion gate
- New defects found → loop back to H1 (iteration count += 1)
- Same defects persist after fix → diagnosis was wrong. Re-instrument:
  ```bash
  agent-browser --session <s> eval "(() => {
    const el = document.querySelector('<selector>');
    const s = getComputedStyle(el);
    return JSON.stringify({ /* all relevant properties */ });
  })()"
  ```
  Compare with extracted values to find the actual discrepancy.

**Max iterations: 3.** After 3 healing iterations, escalate to user with:
- Remaining defect list (category + severity + selector + property + expected vs actual)
- What was attempted in each iteration
- Suggested manual investigation areas

#### Healing Loop Integration

The completion gate (below) is unchanged. Phase H runs BEFORE the gate check:

```
Phase D fails → Phase H (up to 3 iterations) → Phase D passes → completion gate
Phase H exhausted → escalate with defect report
```

---

### Completion gate

> **Phase H runs first.** If Phase D fails, the healing loop (Phase H above) runs automatically before checking the completion gate. The gate below is checked only after Phase H passes or exhausts its iterations.

```
COMPLETION = 10-point score ≥ 9
             AND C1 all ✅ AND C2 all ✅ AND C3 all ✅
             AND Phase D Visual Gate all pass
             AND Phase D mismatches = 0

Fix iteration loop:
  1. Run 10-point score (style-audit.md)
  2. Score < previous iteration? → rollback, retry differently
  3. Score < 9? → fix lowest category → re-run from 1
  4. Score ≥ 9 → run Phase D pixel-perfect-diff
  5. Phase D fail? → fix specific element → re-run from 1
  6. Phase D pass → run VLM sanity check
  7. VLM flags issue? → fix → re-run from 1
  8. VLM clean → DONE

Any single ❌ = NOT DONE.
Max 3 full iterations before escalating to user with score breakdown.
```

### Phase E: LLM Structural Review (MANDATORY, ALL positions)

After AE + DSSIM complete, the LLM reads **every position's** ref+impl pair. This is NOT a sanity check — it is a **mandatory verification axis** that catches what AE and DSSIM cannot.

**Why this changed from "1 pair only":** We discovered that AE can report PASS on completely wrong content (scientific notation parsing bug: `1.27e+06` → `1`), and DSSIM can report PASS when content is missing on a same-color background (`empty yellow bg` vs `yellow bg + card` → DSSIM=0.19). Neither automated metric reliably answers "is this the same page?"

#### MANDATORY: delegate to a subagent

Phase E reads ~22 PNG pairs (~44K vision tokens). **Run it in a delegated subagent context** so the vision tokens never enter the main context, and only the verdict markdown table returns.

Claude Code-style example (Codex should use native subagents or an equivalent delegated worker when available):

```
Agent({
  description: "Phase E LLM review — N positions",
  subagent_type: "ui-clone-skills:visual-debug-reviewer",
  prompt: "<paste the Procedure block below verbatim, with absolute paths substituted>"
})
```

`visual-debug-reviewer` is a plugin sub-agent pinned to `model: opus` on Claude Code (see `.claude-plugin/agents/visual-debug-reviewer.md`) and a Codex native subagent backed by `.codex/agents/visual-debug-reviewer.toml` on Codex/OMX. Use the host-native role first so the vision verdict stays high-quality regardless of the parent agent's model. Falling back to `subagent_type: "general-purpose"` or an inline fallback is acceptable only on hosts that do not expose role-specific agents; on Claude Code the explicit form is preferred because `general-purpose` inherits the parent model and silently degrades when a sonnet-tier parent dispatches Phase E.

The subagent has its own context, reads every pair, returns the table. Main context cost: ~500 tokens (the table) instead of ~44K. Do **not** run Phase E inline, because that defeats the entire visual-debug "near-zero vision tokens" guarantee for any session that reaches this step.

If you skip the subagent and read pairs inline, mark the choice explicitly with the reason ("session is already terminating, no compaction risk") — otherwise default to subagent.

#### Procedure

For each scroll position (0%, 10%, ..., 100%):

```
Read tmp/ref/<component>/static/ref/<pos>pct.png
Read tmp/ref/<component>/static/impl/<pos>pct.png
```

Judge each pair:

| Verdict | Meaning | Action |
|---------|---------|--------|
| **PASS** | Same sections, same content, same visual weight | None |
| **PARTIAL** | Same structure, minor differences (Lottie frame, icon style, animation state) | Document the difference, acceptable if known |
| **FAIL** | Different content, missing sections, wrong layout | Must fix before declaring done |

#### Output format

```markdown
| Position | AE | DSSIM | LLM | Verdict | Issue |
|----------|-----|-------|-----|---------|-------|
| 0% | 418K ❌ | 0.38 ✅ | ✅ | PARTIAL | Lottie frame diff |
| 10% | 1.2M ❌ | 0.19 ✅ | ❌ | FAIL | ServiceCards not visible — scroll trigger not fired |
| ... | | | | | |
```

**Final gate:** Every position must be PASS or PARTIAL (with documented reason). Any FAIL blocks completion.

#### Advisory deductions (optional detail, never gating)

For each PARTIAL or FAIL position, also record the concrete defects as deduction entries in `phase-e-review.json` (see the reviewer agent contract). Each deduction is `{location, reason, penalty, label}`:

- `label` — one of three semantic categories that pixel metrics cannot classify:
  - `completeness` — element missing/extra, clipped, squashed, broken image, duplicated
  - `visual-effect` — shadow / border-radius / opacity / gradient presence or strength differs
  - `icon-variant` — same icon category but different variant (outline vs filled), weight, or asset
- `penalty` — anchored severity bands: large defect −25 to −40, medium −10 to −20, small −3 to −8
- `location` — region/element the defect sits in (e.g. "hero CTA row, right icon")
- `reason` — one line, observable fact only

Deductions are **advisory forensic detail**: they refine the `observations` field into a structured fix-list and let the main agent prioritize fixes by penalty. They do NOT change the PASS/PARTIAL/FAIL verdict, do not feed any gate, and a PASS position simply has `deductions: []`. Severity language stays anchored to the bands above so penalties are comparable across runs.

#### What LLM catches that metrics miss

- Empty background where content should be (DSSIM=0.19 on same-color bg)
- Wrong program/carousel state (AE=1 between two captures of the same wrong page)
- Missing sections that don't affect pixel count (small element on large background)
- Structural ordering errors (sections in wrong sequence but similar colors)

> **Token budget:** ~4000 tokens per pair × 11 positions = ~44K tokens. This is expensive but mandatory. The cost of shipping a wrong implementation and re-doing the entire pipeline is higher.

**Before declaring done — entry point check:**
```bash
# Confirm global CSS is actually imported in the app entry file
grep -r "import.*\.css\|import.*global" src/main.tsx src/index.tsx src/pages/_app.tsx src/app/layout.tsx 2>/dev/null \
  || grep -r "stylesheet" index.html 2>/dev/null \
  || echo "WARNING: no CSS entry point import found — check your framework's entry file"
```
Missing this is a silent failure: styles exist but have no effect.

---

## Element-Scope Verification (transition extraction)

> For single-element animation verification. Runs after ui-reverse-engineering Step T3 (implement). Frames live in `tmp/ref/<effect-name>/frames/{ref,impl}/`.

### Frame Comparison — element scope (AE)

Cropped to element bounds. Compare using AE diff — do not read image pairs with the LLM.

```bash
FRAME_COUNT=$(ls tmp/ref/<effect-name>/frames/ref/frame-*.png | wc -l)
for i in $(seq -f "%02g" 1 $FRAME_COUNT); do
  AE=$(compare -metric AE \
    tmp/ref/<effect-name>/frames/ref/frame-${i}.png \
    tmp/ref/<effect-name>/frames/impl/frame-${i}.png \
    tmp/ref/<effect-name>/frames/diff/frame-${i}.png 2>&1)
  if [ "$AE" -gt 0 ]; then
    echo "FAIL frame ${i}: AE=${AE}"
  fi
done
```

For each FAIL frame: read the diff image to identify which region differs → targeted fix → re-capture impl only → compare.

### Frame Comparison — fullpage scope (SSIM batch)

Full-page screenshot comparison across the entire transition window.

```bash
FRAME_COUNT=$(ls tmp/ref/<effect-name>/frames/ref/frame-*.png | wc -l)
for i in $(seq -f "%02g" 1 $FRAME_COUNT); do
  SSIM=$(ffmpeg -i tmp/ref/<effect-name>/frames/ref/frame-${i}.png \
                -i tmp/ref/<effect-name>/frames/impl/frame-${i}.png \
                -lavfi "ssim" -f null - 2>&1 | grep -oP 'All:\K[0-9.]+')
  if (( $(echo "$SSIM < 0.995" | bc -l) )); then
    echo "FAIL frame ${i}: SSIM=${SSIM}"
  fi
done
```

**Automated failure detection:**
- SSIM < 0.5 on any frame where ref has content → likely blank/loading/white impl frame
- SSIM drop > 0.3 between consecutive impl frames where ref is smooth → likely layout jump

### Bug Diagnosis Protocol

When a visual bug is reported (white flash, wrong timing, layout jump):

**Before writing any fix:**
1. Name the root cause in one sentence: _"The white flash happens because X"_
2. If you cannot name it, instrument:
   ```bash
   agent-browser --session <s> eval "
   (() => {
     const panes = document.querySelectorAll('[class*=pane], [class*=slot]');
     return JSON.stringify([...panes].map(el => {
       const s = getComputedStyle(el);
       return { cls: (typeof el.className === 'string' ? el.className : el.className?.baseVal || ''), opacity: s.opacity, visibility: s.visibility, zIndex: s.zIndex, position: s.position, height: el.offsetHeight };
     }));
   })()"
   ```
3. Only after root cause is confirmed → write the fix
4. After fix → re-capture impl frames → verify the specific bug frame is gone

**Do not iterate on the same approach more than twice.** If two fixes in the same direction don't work, the diagnosis was wrong — re-instrument and re-diagnose.

### Pixel-Perfect Static State Diff (element scope)

Frame comparison verifies timing and motion but CANNOT verify numerical correctness of resting states (font-size, weight, color, spacing, border-radius).

Run Phase D (above) for the element's resting states. States by triggerType:

| triggerType | States |
|---|---|
| css-hover / js-class | `idle`, `active` |
| intersection | `before`, `after` |
| scroll-driven | `before` (trigger_y − 50), `mid` (mid_y), `after` (settled_y + 50) |

Save clips under `tmp/ref/<effect-name>/frames/{ref,impl}/<state>.png`.

Gate: `pixel-perfect-diff.json` exists with all elements `"status": "pass"` AND `mismatches = 0`.

### Bundle-Based Verification (untriggerable animations)

Use `bundle-verification.md` (in ui-reverse-engineering) when:
- Animation auto-starts (carousel, page-load, timer-based)
- T=0 synchronization between ref and impl is impossible

Gate: `bundle-verification.json` all checks `"match": true`.

### Transition-RE "Is This Done?" Checklist

- [ ] `measurements.json` saved (11-point multi-property measurement)
- [ ] Non-linear curves / phase boundaries identified
- [ ] `extracted.json` saved
- [ ] Implementation uses measured values (NOT guessed)
- [ ] **Triggerable:** impl frames captured, comparison all ✅, no white flash/blank
- [ ] **Untriggerable:** `transition-spec.json` + `bundle-verification.json` all match + resting screenshot OK
- [ ] **`pixel-perfect-diff.json`** all pass AND mismatches = 0
- [ ] Entry points verified (CSS imports loaded)
- [ ] **Scroll transitions:** reverse direction verified
- [ ] **Post-implementation full-page capture** (top → bottom → top, SSIM batch)
