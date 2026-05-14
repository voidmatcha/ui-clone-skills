# Operational rules

Niche execution rules and per-request scope adjustments. Read when your situation matches a heading — these rules don't fire on every run, so they live outside the main SKILL.md pipeline.

## Adding pages to an existing project

1. Find the running dev server port: `ps aux | grep next`
2. Verify every target URL actually 404s: `curl -s <url> -o /dev/null -w "%{http_code}"`
3. Read ALL existing components before writing new ones
4. Check if site's JS is loaded: compare `layout.tsx` `<script>` tags vs `document.querySelectorAll('script[src]')` on live ref
5. Grep CSS for page-specific hero class — do NOT assume it matches existing pages
6. **If `layout.tsx` loads a `*.min.js` bundle:** grep the bundle for class selectors it queries. Never rename those classes — add a parallel override class instead. See `diagnosis.md` Root Cause F.

## Tailwind class name collides with legacy bundle selector

- Do NOT rename the original class to avoid Tailwind conflict
- Add a new override class *alongside*: `className="nc-container container"`
- Override only the conflicting property in globals.css: `.nc-container { max-width: none !important }`

## Where extraction / implementation / verification rules live

| Concern | Read |
|---|---|
| Extraction discipline (measurement vs assumption) | `no-judgment.md` |
| Generation pitfalls + output validation | `component-generation.md`, `post-gen-verification.md` |

## Scope adjustments by request shape

| Request | Scope | Adjustments |
|---|---|---|
| "clone the hero" | single-section | Phase R scoped; Step 8 compares section viewport only |
| "replicate this card" | single-element | C1 = cropped; skip C2; skip viewport sweep |
| "clone the modal" | hidden-element | Trigger first, then capture. Step 9 verifies open + close |
