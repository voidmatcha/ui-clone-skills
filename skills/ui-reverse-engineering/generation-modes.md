# Generation Modes — Step 7 Reference

Conditional Step 7 procedures split out of `component-generation.md`. Read only
the section whose condition holds; the generation contract itself stays in
`component-generation.md`.

| Section | Read when |
|---|---|
| [Forensic preservation mode](#forensic-preservation-mode-css-modules--complex-motion) | `generation-plan.json.forensicPreservation.required=true` |
| [Injecting captured HTML](#injecting-captured-html--preserve-wrapper-depth) | a component renders captured per-section `outerHTML` (`site-detection.md` raw HTML injection approach) |
| [Lazy-load attribute rewrite](#lazy-load-attribute-rewrite-mandatory-for-sites-that-ship-a-runtime-lazy-loader) | captured `html/<section>.json` carries `data-src` / `data-srcset` / `data-bg` / `lazyload` placeholders (the ref ships a runtime lazy-loader) |
| [Parallel section generation](#parallel-section-generation-for-pages-with-4-sections) | `component-map.json` has 4+ sections and forensic preservation is not required |

## Forensic preservation mode (CSS Modules / complex motion)

`generation-plan.json.forensicPreservation` is authoritative.

Use this mode when `required=true`:

0. If `missingCssArtifacts=true` or `blockedUntilCssArtifacts=true`, do not
   generate a freehand fallback. Recover `tmp/ref/<component>/css/*.css`
   from CSS capture or bundle extraction, rerun `generation-plan.sh`, and
   only continue once `cssArtifactStatus="present"`.

1. Copy every `tmp/ref/<component>/css/*.css` file to `impl/src/ref-css/`
   through the sanitizer, not raw `cp`:
   ```bash
   bash "$PLUGIN_ROOT/scripts/extract/sanitize-ref-css.sh" \
     "$(pwd)/tmp/ref/<component>" "<impl-root>"
   ```
   This preserves filenames/provenance while repairing browser-tolerated
   bundle tokens that Vite rejects (for example `background-image: var("/img/x.png")`).
2. Import the copied local CSS chunks before local overrides:
   ```tsx
   import './ref-css/<hash>.css';
   import './overrides.css';
   ```
   If `generation-plan.json.forensicPreservation.requiresRuntimeUnlock=true`
   or `tmp/ref/<component>/ref-css-sanitize-report.json.requiresRuntimeUnlock=true`,
   the copied CSS hides `html`, `body`, or the app root at first paint
   (`opacity:0`, `visibility:hidden`, or `display:none`). Reproduce the
   reference site's local ready/unlock behavior before visual verification:
   remove the loading class, add the ready class, or set the affected
   `body`/root opacity/visibility back to visible after the intro/loader
   completes. Do not leave a plain `body{opacity:0}` scaffold and assume
   later section gates will catch it.
3. Before the first visual/debug iteration, verify the copy/import actually
   happened:
   - `find impl/src/ref-css -maxdepth 1 -name '*.css' | wc -l` must be at
     least the number of `cssFiles[]` entries in `generation-plan.json`.
   - `tmp/ref/<component>/ref-css-sanitize-report.json` must exist.
   - If either report says `requiresRuntimeUnlock=true`, run the impl and
     confirm `getComputedStyle(document.body).opacity !== "0"` and the app
     root is visible before continuing.
   - Source imports must reference `./ref-css/...` before local overrides.
   - A run with `required=true` and zero files under `copyCssTo` is
     `INCOMPLETE` even when JSX contains many preserved CSS-module tokens.
4. Generate ref-derived JSX from `dom-scaffold.json`; preserve the original
   tag hierarchy, direct-child depth, text, media elements, and CSS-module
   `className` tokens.
5. Put fixes in `overrides.css` or small React controllers. Do not rewrite the
   first pass into fresh Tailwind utilities.
6. Add transitions after the static scaffold is visibly close: local mount,
   scroll, hover, and click controllers may toggle classes or inline style, but
   must not load the reference site's JS bundles.

This is different from a proxy/static mirror: the impl owns the React tree and
runtime controllers, while local CSS preserves the source styling contract.
Whole-document HTML paste, screenshot-as-background, upstream proxying, and raw
reference JS loading remain failures.

Forensic CSS is a styling contract, not proof that the settled DOM captured
every pseudo-element, background layer, pinned track, or scroll-height
contribution. If a full scaffold renders blank, collapsed, or missing large
background/media/sticky layers, keep the copied CSS and preserved tokens as the
base, then reconstruct the missing layers from `section-map.json`,
`backgrounds.json` / media artifacts, `sticky-elements.json`, and
`transition-spec.json`. Do not ask the user to approve a pivot from a working
clone to a near-blank forensic scaffold.


## Injecting captured HTML — preserve wrapper depth

When pasting per-section `outerHTML` (from Step 2.6 `html/<section>.json`) into a component, the *captured* HTML already includes the section's outer element (`<section class="...">`, `<footer>`, etc.). Wrapping it in another React element changes the parent → child depth and silently breaks any selector that relied on that depth — `main > .hero` no longer matches because there's now an extra anonymous `<div>` between them. `section-compare`'s structure-diff catches the resulting "ref `<main>` had N children, impl had N+1" mismatch *after* generation; cheaper to avoid it up front.

**Anti-pattern (silently breaks `main > .X` selectors and adds AE-invisible structural drift):**
```tsx
export function HeroSection() {
  return <div dangerouslySetInnerHTML={{ __html: heroHtml }} />  // ← extra <div> wrapper
}
```

**Correct — strip the captured outer element OR render via a sibling-flattening pattern:**
```tsx
// Option A: strip outer element from the captured HTML at extraction time, then re-render the outer in JSX
export function HeroSection() {
  return <section className="hero" dangerouslySetInnerHTML={{ __html: heroInner }} />
}

// Option B: render the captured HTML directly into <main> via a single fragment-style helper
//   (set the outer element from the captured string by extracting `tagName` + `attributes`
//   and injecting innerHTML — keeps depth identical to ref)
```

Verify after generation:
```bash
agent-browser --session <s> eval "(() => document.querySelector('main').children.length)()" \
  --on <ref-url> > /tmp/ref-children.txt
agent-browser --session <s> eval "(() => document.querySelector('main').children.length)()" \
  --on <impl-url> > /tmp/impl-children.txt
diff /tmp/ref-children.txt /tmp/impl-children.txt   # must be identical
```

`section-compare.sh` reports this as `ref children: N, impl children: M` in `sections/result.txt` — read those counts before chasing pixel diffs.


## Parallel section generation (for pages with 4+ sections)

Use the host's delegated subagent mechanism with isolated write scopes for 2-3x speedup (Claude delegated subagents, Codex native subagents, or inline execution if delegation is unavailable).

**Phase 3A — Foundation (sequential):**
Generate shared files first. All section builders depend on these.

1. `globals.css` — design tokens, CSS variables from `variables.txt`, font imports
2. `types.ts` — shared TypeScript types
3. `icons.tsx` — all SVGs from `inline-svgs.json` + `decorative-svgs.json`
4. `layout.tsx` — app shell with scroll provider, fonts, global styles
5. `page.tsx` skeleton — section imports (empty components) defining assembly structure

⛔ Gate: all 5 files exist, `pnpm tsc --noEmit` passes.

**Phase 3B — Section builders (parallel):**
For each section in `component-map.json`:

**Complexity budget rule:** If a builder prompt exceeds ~150 lines of spec content, the section is too complex for one agent. Split it — one agent per distinct sub-component (card variant, nav panel, carousel), plus one agent for the section wrapper that imports them. This is a mechanical check, not a judgment call.

1. Build an INLINE prompt (not file references) containing:
   - Section spec from design audit (relevant slice of `extracted.json`)
   - Relevant `transition-spec.json` entries (filter by section selector)
   - Reference clip path
   - Foundation files content for import consistency
   - Rules from this document (font accuracy, CSS var consistency, transition integration)
   - Relevant slice of `transition-implementation.md`

2. Dispatch one isolated delegated worker/subagent per section. Pass the full inline prompt, use worktree isolation where the host supports it, and label the job `Build <SectionName> component`.

3. Each builder produces `src/components/<SectionName>/<SectionName>.tsx` + local sub-components. Passes `python -m ui_clone.gate <ref-dir> post-implement` independently.

**Fallback:** if delegated workers/subagents are unavailable, generate sequentially with the same spec + rules.

**Phase 3C — Assembly (sequential):**
Collect section components from worktree branches → wire imports in `page.tsx` → add cross-section wiring (scroll context, Lenis wrapper) in `component-map.json` order → `pnpm tsc --noEmit` → `python -m ui_clone.gate <ref-dir> post-implement`.

---

## Lazy-load attribute rewrite (MANDATORY for sites that ship a runtime lazy-loader)

Captured HTML often contains placeholder attributes that the original site's runtime lazy-loader rewrites *after* page load (`data-src`, `data-lazy`, `data-srcset`, `data-bg`, `lazyload` class, `loading="lazy"`). When this HTML is injected verbatim into the impl (e.g. via `dangerouslySetInnerHTML` per Step 7), there is no runtime to rewrite the attributes — `<img>` tags render with no `src` and stay broken. The bug is silent: the page renders with missing images, and `visible-images.json` (Step 2.5) does not catch it because that script collects from `img.src`, not `img.dataset.src`. Two root causes overlap on the same page: an image captured before the lazy-loader fired has only `data-src`; one captured after has both `data-src` and `src`, so the attribute count differs by viewport/scroll position.

**Fix:** before writing any component, scan section HTML for these patterns and rewrite at extraction time:

```bash
# Detect — flags any section file with src-less <img> still carrying a data-src placeholder
grep -lE '<(img|source|video)[^>]*\bdata-(src|srcset|lazy|bg)=' tmp/ref/<component>/html/*.json | while read f; do
  python3 -c "import json,re,sys; d=json.load(open(sys.argv[1])); h=json.dumps(d); print(sys.argv[1], len(re.findall(r'data-(src|srcset|lazy|bg)=', h)))" "$f"
done

# Rewrite (per-section HTML strings) — data-src → src, drop data-lazy / lazyload class.
# Use python (not sed) for portability + correct data-bg handling: BSD sed (`sed -i ''`)
# and GNU sed (`sed -i`) take incompatible -i syntax, and the data-bg rewrite needs
# to capture the URL value and emit a fully-closed `style="background-image:url(<v>)"`
# (a sed one-liner with a pasted-in `style="background-image:url(` produces broken
# output that swallows the trailing quote).
python3 - <<'PY'
import json, re, glob, pathlib
RE_BG  = re.compile(r'\bdata-bg=(["\'])(.*?)\1')
RE_SRC = re.compile(r'\bdata-(src|srcset)=')
RE_LZ  = re.compile(r'\s+data-lazy=(["\']).*?\1')
RE_CLS = re.compile(r'\s+class=(["\'])lazyload\1')
for path in glob.glob("tmp/ref/<component>/html/*.json"):
    text = pathlib.Path(path).read_text()
    text = RE_BG.sub(lambda m: f'style="background-image:url({m.group(2)})"', text)
    text = RE_SRC.sub(lambda m: f'{m.group(1)}=', text)
    text = RE_LZ.sub('', text)
    text = RE_CLS.sub('', text)
    pathlib.Path(path).write_text(text)
PY
```

Run an additional pre-scroll *before* Step 2.6 on lazy-loaded pages so the captured HTML is in its post-lazy-load form everywhere — see `../visual-debug/comparison-fix.md` triage row D for the `scrollTo(0, document.body.scrollHeight)` warmup.

---

After this step, return to `component-generation.md` (Step 7) and continue the generation contract.
