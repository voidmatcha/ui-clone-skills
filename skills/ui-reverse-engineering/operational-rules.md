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

## Recovering a stalled / frozen run

- **Symptom:** the session has been quiet for >15 min, the UI shows "N shells still running", but `ps`/`pgrep`/`lsof` find no live processes for those shells.
- **Root cause (runtime-level, not the pipeline):** a background-shell completion wake-up was lost — the completion event failed to re-invoke the agent and was not retried. No artifact is corrupted; the run simply has no live driver.
- **Recovery:** send any message. The agent re-enters at the before-starting state inspection step and resumes losslessly from `pipeline-state.json`, `current_gate`, and the on-disk artifacts — re-run the same `python -m ui_clone.pipeline <url> <component> <session> <action>` and it continues where it stopped.
- **Why exposure is bounded (batch-4 item 2):** verification invocations that would exceed ~8 min are split into <8-min, idempotent chunks with persisted intermediate state, so a lost wake-up loses at most one in-flight chunk. The video-motion scroll sweep is the primary case: each captured position is checkpointed to `<ref-dir>/transitions/.../scroll-chunk-manifest.json` and `UI_CLONE_VMC_SCROLL_CHUNK` bounds positions per invocation. A resumed run skips already-captured positions (frames on disk + manifest) and the dispatcher aggregates the chunked frames into a verdict identical to a monolithic run.

## Scope adjustments by request shape

| Request | Scope | Adjustments |
|---|---|---|
| "clone the hero" | single-section | Phase R scoped; Step 8 compares section viewport only |
| "replicate this card" | single-element | C1 = cropped; skip C2; skip viewport sweep |
| "clone the modal" | hidden-element | Trigger first, then capture. Step 9 verifies open + close |
