# Asset Substitution — Declaring Deliberate Differences

> 🚨 **Most common mistake — `structuralOnlySections` MUST be included.**
> Declaring `fonts` / `images` alone does NOT activate structural-only mode.
> `section-compare.sh` reads `structuralOnlySections` patterns *separately*;
> without it, every section still runs strict pixel AE diff and you get
> 1M+ AE failures even though you declared the substitution. The simplest
> safe value is `"structuralOnlySections": ["*"]` (wildcard, all sections).
> The script's forgiving fallback auto-defaults to `["*"]` when fonts/images/videos
> are declared but no patterns are given — but a warning prints every run.
> Be explicit.

Some clones must substitute assets the original site uses:

| Original | Why substituted | Replacement |
|---|---|---|
| Paid commercial font (Exat, Söhne, Suisse, Druk) | License — can't ship without paying per-deployment | Variable Google font with similar axis (Roboto Flex, Inter Tight, Bricolage Grotesque) |
| Stock photography from a paid provider | Per-image licensing; impossible to bulk-clone | Free Unsplash/Pexels equivalents with the same composition |
| Long-form licensed video | Hosting + license cost | Generic loop or static poster |
| Logo / brand mark of a third party | Trademark | Placeholder rectangle |

**The problem:** when you substitute deliberately, AE pixel comparison is **by design** going to fail for every section that renders the substituted asset. A 12-section site with a substituted hero font will report "12 sections FAILED" forever — the gate never clears, every iteration looks broken even when the layout matches perfectly.

The fix isn't to lower the AE threshold (that masks real regressions). The fix is to *declare* the substitution and have the gate switch comparison mode for the affected sections: **structural diff only, no pixel diff**.

## Artifact: `tmp/ref/<component>/asset-substitution.json`

Optional. When absent, section-compare runs full AE pixel comparison on every section (current behavior). When present, sections matching `structuralOnlySections` get layout-only comparison.

**Schema:**

```json
{
  "fonts": [
    {
      "original": "Exat",
      "replacement": "Roboto Flex",
      "reason": "Commercial font — per-deployment license required",
      "axesMatched": ["wdth", "wght", "opsz"]
    }
  ],
  "images": [
    {
      "originalSrc": "https://example.com/assets/hero.jpg",
      "replacementSrc": "/images/hero-stock.jpg",
      "reason": "Source image not available under permissive license"
    }
  ],
  "videos": [],
  "structuralOnlySections": ["main-hero", "*"]
}
```

**Field rules:**

- `fonts` / `images` / `videos`: arrays of `{ original, replacement, reason }`. Free-form `reason` field — describe *why*, not *what*. Future-you reading this in 6 months needs to know whether the substitution is still required or was a temporary workaround that's now removable.
- `structuralOnlySections`: array of section name patterns. Each is checked against the section name produced by `section-compare.sh` using **substring match** (so `"hero"` matches `"main-hero"`, `"hero-section"`, etc.). The literal `"*"` is a wildcard meaning "every section uses substituted assets" — common when the substitution is the project's primary font.

## What changes

When the file exists, `section-compare.sh`:

1. Echoes the active patterns at the top of its run (`▸ Asset substitution mode active: pixel diff skipped for [...]`).
2. In the AE loop, sections matching any pattern get marked `🔁 STRUCTURAL_ONLY` instead of `✅`/`❌` and increment a separate `SUBSTITUTED_COUNT`.
3. The result line in `sections/result.txt` reports `N PASS, N FAIL, N SKIP, N STRUCTURAL_ONLY` so the breakdown is visible in the Stop gate output.
4. Step 5 (structure diff — heights, child counts, SVG-text presence, layout system) **still runs** on substituted sections. Layout regressions are caught even when pixel diff is bypassed.

The Stop gate only counts `❌ FAIL` lines — `🔁 STRUCTURAL_ONLY` is not a failure, so substituted sections pass without further intervention.

## Detection — when to write this file

Write the file as soon as you decide to substitute. Don't wait for section-compare to fail 12 times.

**During Phase 2.5 / Step 3 asset extraction:**
- Compare `fonts.json` (extracted from ref) with the fonts your impl actually loads. If any commercial font name appears in ref but a free font is loaded in impl → emit a `fonts[]` entry.
- Same for `visible-images.json` if you're swapping any CDN-hosted asset for a local replacement.

**Heuristic check the impl** can run after generation:

```bash
agent-browser --session <s> open <impl-url>
agent-browser --session <s> wait 2000
agent-browser --session <s> eval "(() => {
  // Compare loaded font-family vs the ones extracted to fonts.json
  const families = new Set();
  document.querySelectorAll('*').forEach(el => {
    families.add(getComputedStyle(el).fontFamily.split(',')[0].replace(/['\"]/g, '').trim());
  });
  return JSON.stringify([...families]);
})()"
```

If the result diverges from `fonts.json`'s `family` field, you have a substitution and need to declare it.

## When NOT to use this

- **Bug masking.** If a section legitimately renders wrong (broken layout, missing element, wrong color), do not add it to `structuralOnlySections` to make the gate green. The gate will lie to you for the next 6 months.
- **Unintended substitution.** If the impl loaded a *fallback* font because the intended one 404'd, fix the font load — don't declare it substituted.
- **Color-only differences.** AE with `-fuzz 8%` already tolerates color anti-aliasing. Don't reach for substitution mode just to suppress 200-AE noise; raise the threshold instead.

The artifact says: *"this difference is deliberate and acknowledged"*. Use it for that and only that.

## Example — Plan B font substitution

A clone of a typography-heavy site whose hero font is a paid commercial release:

```json
{
  "fonts": [
    {
      "original": "Exat",
      "replacement": "Roboto Flex",
      "reason": "commercial license; clone is showcase-only, no resale",
      "axesMatched": ["wdth", "wght", "opsz"]
    }
  ],
  "images": [],
  "videos": [],
  "structuralOnlySections": ["*"]
}
```

After this lands, section-compare's first run on the same impl flips from "12 FAIL" to "12 STRUCTURAL_ONLY", the Stop gate clears, and you can iterate on layout regressions in isolation.
