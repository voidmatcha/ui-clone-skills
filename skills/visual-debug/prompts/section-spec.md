# section-spec — generate grounded per-section implementation spec

You are looking at one section of a web page's reference design. Your job is to
emit a **deterministic spec** that downstream code generation can follow
verbatim, eliminating the agent's tendency to fabricate text / colors /
typography from class names and URLs.

## Inputs

- **REF clip PNG**: visual screenshot of one section, captured at extraction time
- **Section metadata**: tag/className/id/top/height from Phase 2 DOM extraction
- **Direct text** (when present): textContent extracted from this section's DOM
- **Asset list** (when present): downloaded images/fonts/videos visible in this section

## Goal

Produce a JSON spec a Phase-4 generator can paste-translate into Tailwind/JSX
without further guessing. The spec captures **what is visible in the REF** —
verbatim text, observed hex colors, measured typographic scale, layout
pattern, enumerated elements. **No interpretation. No invention.**

## Hard rules

1. **Read the REF PNG with the Read tool.** Look at it. Do not infer from
   filename or class name.
2. **No fabrication.** If you cannot determine a field from the visible
   evidence, set it to `null` (or omit it for optional fields). Inventing
   "plausible" text or colors poisons the convergence loop more than omitting.
3. **JSON only.** No prose outside the JSON object, no code fences, no
   leading/trailing text. Exactly one JSON object matching the schema.
4. **Text verbatim.** If text is visible in the PNG, copy it character-for-
   character (preserve case, punctuation, line breaks as `\n`). Do not
   summarize, paraphrase, or "improve". If text is partially occluded, mark
   the unclear portion with `?` and lower confidence.
5. **Colors as hex.** Read pixel colors directly. Don't say "dark beige" —
   say `#f5ead2`. Don't say "near black" — sample the actual hex.
6. **Typography measured, not guessed.** When estimating heading size, anchor
   to viewport (default ref viewport is 1440×900). If hero h1 fills ~⅔ of the
   width with bold serif, that's roughly `text-[140px] font-black`. State the
   size + weight + family + tracking + leading as observed.
7. **Layout pattern named.** Use a small vocabulary:
   `centered-fullscreen`, `centered-with-pad`, `single-column`,
   `2-col-grid`, `3-col-grid`, `flex-row-spaced`, `flex-col-stack`,
   `absolute-stage`, `sticky-pin`, `scroll-scrub`. Combine when needed
   (`"3-col-grid + flex-col-stack on mobile"`).

## Output schema

```json
{
  "label": "<echoed section name>",
  "purpose": "<≤120 chars: what this section communicates (hero/stats/CTA/footer/...)>",
  "text": {
    "h1": "<verbatim heading text or null>",
    "h2": "<verbatim h2 or null>",
    "subhead": "<verbatim subhead paragraph or null>",
    "body": ["<verbatim paragraph 1>", "<paragraph 2>", "..."],
    "cta_label": "<verbatim CTA button text or null>",
    "captions": ["<short label 1>", "<label 2>", "..."]
  },
  "colors": {
    "bg": "<hex of dominant background or null>",
    "fg": "<hex of primary text or null>",
    "accent": "<hex of accent / highlight or null>",
    "extra": ["<other notable hex>", "..."]
  },
  "typography": {
    "h1": "<observed size + weight + family + spacing or null>",
    "h2": "<observed or null>",
    "body": "<observed or null>",
    "caption": "<observed or null>"
  },
  "layout": "<one of the vocabulary terms, or combined>",
  "key_elements": [
    "<concrete element 1, e.g. 'top gov banner with US flag, mono uppercase ~12px'>",
    "<element 2, e.g. 'centered h1 across 2 lines'>",
    "..."
  ],
  "assets": [
    "<absolute or root-relative path used (e.g. /videos/hero-bg.mp4) or null>"
  ],
  "interactions": [
    "<observed motion / hover / scroll-trigger, or empty array>"
  ],
  "confidence": "high|medium|low",
  "notes": "<≤200 chars: anything unclear that downstream should verify, or null>"
}
```

## Good-output example

```json
{
  "label": "section-0-hero",
  "purpose": "Hero: site title + mission statement, full-screen dark stage",
  "text": {
    "h1": "Real Food\nWins",
    "h2": null,
    "subhead": "America is the greatest country on Earth. And the sickest. Highly processed food has hollowed out our health, driving obesity, diabetes, heart disease, and early death. The truth is simple: real food restores health.",
    "body": [],
    "cta_label": null,
    "captions": ["AN OFFICIAL WEBSITE OF THE UNITED STATES GOVERNMENT"]
  },
  "colors": {
    "bg": "#1a0e08",
    "fg": "#f5ead2",
    "accent": null,
    "extra": ["#ffffff"]
  },
  "typography": {
    "h1": "serif ~140px font-black tracking-tight leading-[0.95], split across 2 lines, centered",
    "h2": null,
    "body": "sans ~22px font-medium centered max-w-2xl",
    "caption": "mono uppercase ~12px tracking-widest"
  },
  "layout": "centered-fullscreen",
  "key_elements": [
    "top gov banner with US flag + mono uppercase text",
    "centered h1 stacked across 2 lines, second line right-shifted",
    "centered subhead paragraph below h1, max ~640px wide",
    "no CTA button in this section"
  ],
  "assets": [],
  "interactions": ["scroll-down indicator at bottom (optional)"],
  "confidence": "high",
  "notes": null
}
```

## Bad-output examples (DO NOT emit)

- `{"text": {"h1": "Eat Real Food"}}` ← fabricated text not visible in PNG
- `{"colors": {"bg": "near black"}}` ← not hex, vague
- `{"typography": {"h1": "large bold heading"}}` ← not measured
- `{"layout": "looks like a hero section"}` ← not in vocabulary
- `{"key_elements": ["some text", "an image"]}` ← not concrete
- Prose anywhere outside the JSON object.

## Calibration

A good spec lets a frontend engineer (or a Phase-4 generator) produce the
section's TSX without looking at the REF again. Test: can the reader produce
exactly this section's HTML + Tailwind from your spec alone? If not, add
more concrete detail.
