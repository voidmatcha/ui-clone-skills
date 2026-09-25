# Boundary Collision Sweep — Step 4-C2b Reference

Manual boundary-collision sweep split out of `responsive-detection.md`. The
deterministic producer is `skills/visual-debug/scripts/breakpoint-collision-check.sh`
(pipeline Step 8-pre-bound, `boundary` gate); read this file only when the impl mixes
Tailwind responsive utilities with project-scoped `@media (max-width: <bp>px)` rules
and you need to debug or extend the manual sweep.

⛔ **Step 4-C2 measures at 768/1280/1440 — and 768 is exactly the point where the worst collision bug hides.** A site using `@media (max-width: 768px)` for any project rule (root font-size, container padding, mobile nav, mobile-only stack) AND Tailwind responsive utilities (`md:`, anything keyed off `min-width: 768px`) hits a single-pixel-wide collision at exactly 768 — both rules match, both apply, layout overflows. See `diagnosis.md` → Root Cause J.

**When to skip:** If the impl uses only Tailwind (no project-scoped `@media (max-width: <bp>px)` rules) OR only project CSS (no Tailwind responsive utilities), the collision class doesn't exist. Otherwise this is mandatory — at every viewport boundary the project actually uses.

**Inputs you already have:** the `@media` rules from Step 4-A (`tmp/ref/<c>/responsive/media-rules.json`) and the Tailwind breakpoints the impl JSX uses (grep `md:`, `lg:`, `sm:`, `xl:` in `apps/<app>/src/projects/<c>/`). Take the **union of project @media boundaries and Tailwind breakpoints** — that's your collision-test set.

**Eval — at boundary ±1 for each candidate width:**

```bash
# Collect all candidate boundary widths (CSS @media + Tailwind breakpoints used in JSX).
# Default Tailwind breakpoints: 640, 768, 1024, 1280, 1536. Trim to the set the project uses.
BPS="640 768 1024 1280 1536"

for BP in $BPS; do
  for OFF in -1 0 1; do
    W=$((BP + OFF))
    agent-browser --session <s>-bp set viewport $W 900
    agent-browser --session <s>-bp open <impl-url>
    agent-browser --session <s>-bp wait 600
    agent-browser --session <s>-bp eval "(() => JSON.stringify({
      width: innerWidth,
      bodyScrollWidth: document.body.scrollWidth,
      htmlScrollWidth: document.documentElement.scrollWidth,
      overflowing: document.body.scrollWidth > innerWidth + 0.5,
      rootFontSize: parseFloat(getComputedStyle(document.documentElement).fontSize),
      mqMaxBp: matchMedia('(max-width: ' + ${BP} + 'px)').matches,
      mqMinBp: matchMedia('(min-width: ' + ${BP} + 'px)').matches,
      collision: matchMedia('(max-width: ' + ${BP} + 'px)').matches && matchMedia('(min-width: ' + ${BP} + 'px)').matches,
    }))()" > tmp/ref/<c>/responsive/boundary-${BP}-${W}.json
  done
done
```

**Analysis — flag any width where overflow appears at exactly the boundary but not on either side:**

```bash
node -e "
const fs = require('fs');
const path = require('path');
const dir = './tmp/ref/<c>/responsive';
const files = fs.readdirSync(dir).filter(f => f.startsWith('boundary-'));
const byBp = {};
for (const f of files) {
  const m = f.match(/^boundary-(\d+)-(\d+)\.json$/);
  if (!m) continue;
  const bp = +m[1], w = +m[2];
  byBp[bp] = byBp[bp] || {};
  byBp[bp][w] = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
}
const findings = [];
for (const [bp, samples] of Object.entries(byBp)) {
  const at = samples[+bp];
  const before = samples[+bp - 1];
  const after = samples[+bp + 1];
  if (!at) continue;
  const isolatedSpike = at.overflowing && !(before && before.overflowing) && !(after && after.overflowing);
  const fontJump = before && after && Math.abs(at.rootFontSize - before.rootFontSize) > 4 && Math.abs(at.rootFontSize - after.rootFontSize) > 4;
  if (at.collision || isolatedSpike || fontJump) {
    findings.push({ breakpoint: +bp, atBoundary: at, before, after, why: { collision: at.collision, isolatedSpike, fontJump } });
  }
}
fs.writeFileSync(path.join(dir, 'boundary-collisions.json'), JSON.stringify(findings, null, 2));
console.log(findings.length ? 'COLLISIONS:' : 'no collisions');
console.log(JSON.stringify(findings, null, 2));
"
```

**Fix:** apply `diagnosis.md` Root Cause J — either shift the project CSS rule to `(max-width: <bp - 0.02>px)` or move the Tailwind variant up one tier. Re-run the sweep until `boundary-collisions.json` is `[]`.

**Output to** `tmp/ref/<component>/responsive/boundary-collisions.json` — empty array means clean.

---

After this step, return to `responsive-detection.md` (Step 4) and continue with Step 4-D.
