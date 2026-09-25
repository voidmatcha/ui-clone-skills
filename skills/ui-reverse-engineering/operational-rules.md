# Operational rules

Niche execution rules and per-request scope adjustments. Read when your situation matches a heading; these rules don't fire on every run.

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

Section-only and element-only requests are supported, including a
trigger-opened modal or drawer requested on its own. Run them as a scoped clone,
not by trimming a whole-page run.

- **Identify the target first.** Resolve one CSS selector per target from DOM
  evidence, a `section-map.json` entry name, or, for trigger-opened UI, the
  trigger selector plus the opened container's selector. The component name
  alone names `tmp/ref/<component>/`; it does not select a DOM subtree, so
  derive the selector from live DOM evidence or ask for it when several
  candidates match. `--scope=desktop|all` selects responsive layouts, not
  sections. Name artifact directories after the target (for example
  `tmp/ref/hero/`); multiple requested targets get one directory and one
  component each.
- **Scope capture, extraction, and verification to the target.** Record the
  target with `scripts/extract/element-evidence.sh` (→ `element-target.json`),
  capture reference frames with [element-capture.md](element-capture.md) clip
  screenshots at the target's real scroll position, extract its computed styles
  and transitions through the transition sub-pipeline in
  [pipeline-execution.md](pipeline-execution.md#transition-extraction), and
  verify with the element-scope AE and Phase D static-state diffs in
  [comparison-fix.md](../visual-debug/comparison-fix.md#element-scope-verification-transition-extraction).
  Keep the surrounding scroll context (sticky ancestors, scroll triggers) that
  the target's behavior depends on.
- **Trigger-opened UI.** Perform the trigger interaction before reference
  capture; reference frames must show the open state, not the default page.
  Capture both opening and closing: idle → open-state clip, an open/close
  recording, and the timing of each animated layer (for example backdrop fade
  and panel slide). Verify open and close on the implementation the same way.
- **Report scoped completion honestly.** `python -m ui_clone.pipeline ... run`
  and `verify` are page-level; they have no subtree selector and cannot
  certify that a scoped clone stayed inside its boundary. Its completion
  command is `python -m ui_clone.scoped_check <ref-dir>`; report it as scoped
  with that evidence, never as a page-level verified clone. Do not trim `section-map.json`, fabricate components, or bypass gates
  to make a page-level run pass; a silent expansion to a full-page run is also
  out of scope unless the user asks for it.

## Cleaning up `tmp/ref/`

Delete `tmp/ref/<component>` only when the user explicitly asks, as the final
step after every gate passes. Warn first that resume, context recovery, and
re-verification depend on that evidence and become impossible; never touch `impl/`.
