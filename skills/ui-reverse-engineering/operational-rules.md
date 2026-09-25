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

- **Diagnose before declaring a stall:** identify the exact session, latest turn/tool timestamp, owned check process, and newest result. Terminal banners and pending-shell counts may be stale. A preview server staying alive does not prove the agent is active. If evidence conflicts, report the conflict rather than assuming a stop or progress.
- **Possible causes:** a live long-running check, pending user input, a terminated process, or a lost completion notification. Quiet output alone cannot distinguish them or prove artifacts are intact.
- **Recovery:** if the check is alive, wait on its existing handle. If a question is pending, inspect its scope and existing authorization before asking again. If no owned job is running and continuation is authorized, inspect pipeline state and artifact freshness, then resume the specific unfinished action. Never launch a duplicate dispatcher solely to wake the agent. A status-only request does not itself authorize restarting stopped work.
- **Why exposure is bounded:** verification invocations that would exceed ~8 min are split into <8-min, idempotent chunks with persisted intermediate state, so a lost wake-up loses at most one in-flight chunk. The video-motion scroll sweep is the primary case: each captured position is checkpointed to `<ref-dir>/transitions/.../scroll-chunk-manifest.json` and `UI_CLONE_VMC_SCROLL_CHUNK` bounds positions per invocation. A resumed run skips already-captured positions (frames on disk + manifest) and the dispatcher aggregates the chunked frames into a verdict identical to a monolithic run.

## Scope adjustments by request shape

Section/element-only cloning is not supported end-to-end. The component name
names artifacts, not a DOM subtree; `--scope=desktop|all` selects responsive
layouts, not sections. Modal verification also needs explicit open/close evidence.

Explain this limitation before capture or scheduling. Preserve the requested URL,
selector, and existing evidence; report a scope-support blocker. Do not start a
full-page run, trim `section-map.json`, fabricate components, or bypass gates to
make a partial clone pass. Support requires one persisted selection contract
shared by capture, generation, and verification, retaining source scroll context.
