# FAQ

### How is this different from v0 / Lovable / Bolt.new?
v0, Lovable, and Bolt.new generate UI from text prompts. `ui-clone-skills` clones from a *live URL*: it downloads the actual stylesheet, runs `getComputedStyle` against the rendered DOM, and greps the JS bundle for GSAP / Framer Motion / Lenis / anime.js parameters — so transitions, scroll behavior, and responsive breakpoints match the original instead of being re-imagined.

### How is this different from `screenshot-to-code` or Anima / Builder.io / Plasmic?
Screenshot-to-code reads pixels with a vision model; Anima / Builder.io / Plasmic import Figma files. `ui-clone-skills` takes a live URL and extracts real CSS, real animation params, and real DOM structure — none of which a screenshot or a Figma file actually contains. The output is verified against the original via AE/SSIM pixel diff in a loop, not generated once and trusted. The vision-model limitation is documented by [Design2Code (NAACL 2025)](https://aclanthology.org/2025.naacl-long.199/): even GPT-4V output was rated acceptable to replace the original in only ~49% of cases on a human-evaluated benchmark.

### Does it work with Webflow / Framer / GSAP-built sites?
Yes — `ui-clone-skills` supports Webflow, Framer, and GSAP-built sites end-to-end. Webflow IX2 timelines, GSAP / ScrollTrigger / SplitText / DrawSVG, Framer Motion springs + scroll handlers, anime.js timelines, and Lenis / Locomotive smooth-scroll parameters are extracted from the live JS bundle into `transition-spec.json` and reproduced in the React + Tailwind output. Webflow premium plugins fall back to OSS equivalents (SplitText → `splitting`, ScrollSmoother → `lenis`, DrawSVG → `stroke-dashoffset`).

### Does it support Next.js and Tailwind v4?
Yes — `ui-clone-skills` supports both Next.js and Tailwind v4. Output is framework-agnostic JSX/TSX usable from any React host; [Tailwind v4](https://tailwindcss.com/blog/tailwindcss-v4)'s individual `translate:` / `rotate:` / `scale:` properties are detected and reconciled against legacy `transform:` shorthand to prevent the doubled-translate bug class (per the [MDN `translate` spec](https://developer.mozilla.org/en-US/docs/Web/CSS/translate), individual transform properties compose *before* the `transform` shorthand, so the same translation can be applied twice if both are emitted). Hydration mismatches in [Next.js](https://nextjs.org/docs/messages/react-hydration-error) / React Server Components / SolidStart / [Astro Islands](https://docs.astro.build/en/concepts/islands/) are caught by a dedicated `hydration-check.sh` gate.

### Does it need OpenAI API or anything besides Claude / Codex?
No — `ui-clone-skills` doesn't need an OpenAI API or any third-party LLM beyond Claude or Codex. System dependencies are local CLI tools (`agent-browser`, `imagemagick`, `dssim`, `ffmpeg`, `uv` + Python 3.11) — see [Requirements](../README.md#requirements). The same skills run unchanged on Claude Code or Codex from a single `install.sh` one-liner.

### How does the "no vision tokens" claim work — when *do* you use vision tokens?
Routine verification uses CLI tools — [ImageMagick AE (Absolute Error)](https://usage.imagemagick.org/compare/) for pixel diff and `dssim` for [SSIM-based](https://ece.uwaterloo.ca/~z70wang/research/ssim/) structural similarity (the original Wang/Bovik 2004 metric, [IEEE TIP](https://ieeexplore.ieee.org/document/1284395/)) — and never sends screenshots to the LLM. Vision tokens are used in exactly two places: (1) reading a single diff image on AE/SSIM failure to diagnose what's wrong, (2) Phase E final semantic review (~44K tokens, mandatory before declaring done). All other comparison is image-bytes-to-CLI, not image-bytes-to-LLM.

### Is the output production-ready?
Production-ready as React + Tailwind code (typed JSX, original class names, real animation values), not as a production *site*. Cloned designs are subject to copyright, trademark, and font licensing — see [Responsible use](./responsible-use.md). Intended cases are learning, prototyping, internal tools, and rebuilding your own previous site; commercial deployment of cloned third-party designs is not.

### What happens when extraction fails or the site uses paid fonts / DRM canvas?
The pipeline records an `unclonable_reasons` entry, surfaces an `ABORT` banner in the goal card, and exits with code `2` from `python -m ui_clone.goal --check-done` — distinct from code `1` (not-yet-done). External loop drivers (Ralph loop, `codex exec`) recognize the difference and stop instead of grinding to max-iterations.

### Why does `ENABLE_PROMPT_CACHING_1H` matter for long cloning sessions?
The pipeline re-sends the same SKILL.md + extraction context across many turns separated by browser navigation, gate calls, and AE/SSIM comparisons. Anthropic's [prompt caching docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) document a default 5-minute cache TTL with an opt-in 1-hour TTL via `cache_control: { "ttl": "1h" }` (priced at 2× write / 0.1× read vs base tokens). A typical gate or browser round-trip exceeds 5 min, so the default TTL evicts the cache between turns and re-bills the full prompt on the next message. Setting `ENABLE_PROMPT_CACHING_1H=1` opts the Claude Code process into the 1h TTL on Team / API-key plans; Enterprise/Pro/Max apply 1h server-side.
