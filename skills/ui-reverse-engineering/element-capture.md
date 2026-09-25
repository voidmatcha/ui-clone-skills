# Element-Scope Capture — Step T0

> Element-scope captures — isolated target element effects (hover, scroll-driven, page-load). For `fullpage` scope, use the main ui-capture pipeline (Phase 1+2) instead.

This is also the reference-capture path for a supported section-only,
element-only, or trigger-opened modal/drawer clone: resolve `<target-selector>`
first (see [scoped runs](operational-rules.md#scope-adjustments-by-request-shape))
and record it with `"$PLUGIN_ROOT/scripts/extract/element-evidence.sh"`, writing
`tmp/ref/<target>/element-target.json` (hooks recognize a scoped run only by that
file, produced by the script; never hand-write it). The script refuses a
selector that matches 0 or 2+ elements, a box under 8×8 CSS px, or an element
hidden by `display:none` / `visibility:hidden` / `opacity:0` (own or ancestor),
so open a modal/drawer before probing its container. Name the target directory
after the component you will write (`tmp/ref/pricing-modal/` for
`PricingModal.tsx`).

## Setup

```bash
mkdir -p tmp/ref/<effect-name>/frames/{ref,impl}

agent-browser --session <project> open https://target-site.com
agent-browser --session <project> set viewport 1440 900
```

## Capture command (both sides)

Every frame is written by `"$PLUGIN_ROOT/scripts/extract/element-state-capture.sh"`, which
measures the element in the session, records its computed styles and those of
its element subtree (`frames/<side>/<state>.computed.json`; descendants in
document order up to 40 nodes, each keyed by a structural path such as
`div[0]/span[1]` — index among element siblings, text nodes never count),
inventories the resources the page has loaded, crops the clip from a
full-viewport screenshot, and stamps `frames/<side>/capture-manifest.json`
with the page origin, loaded-resource origins, the script/stylesheet URLs the
page loaded (`codeResources`), match count and visibility per clip, session,
timestamp, sha256 of each frame, and its producer record (module and driver
hashes of the shipped release manifest). Clip mode applies the same target
sanity as `element-evidence.sh`. An `impl` capture is refused when the page is
on the reference origin, has loaded anything but images/video/fonts from the
reference host or its subdomains, or has loaded any script/stylesheet the
reference captures inventoried — from any host, including a CDN, classified
by initiator kind, code extension, or the response content type resource
timing reports, matched by normalized URL — or anything but media from a
non-first-party origin that served reference code (an extensionless chunk
fetched from that CDN counts; a proxy, an iframe, or the reference bundles
are not an implementation; the preserved media and font URLs are). Capture
`ref` before `impl`: the impl check needs the reference inventory.
`scoped_check` re-hashes every frame against that manifest and re-checks
those rules, so never write frames, the manifest, or the computed records by
hand, never copy reference frames into `frames/impl/`, and never drive
`ui_clone.element_capture` or import it from a shell program — the hooks
deny both. Run each producer (`element-evidence.sh`,
`element-state-capture.sh`, `ui-clone scoped-diff`) as its own Bash
command in the form shown here, not chained with `&&`/`;`/`|`/newline, piped,
redirected, or wrapped in a script of your own: the PostToolUse hook records
the hash of what the producer wrote into `.scoped-evidence-ledger.json` only
for a command that is exactly that invocation, and `scoped_check` rejects
evidence without that record (`evidence-unledgered`) or without the ledger
(`evidence-ledger-missing`). The hook expands only `$PLUGIN_ROOT` /
`$CLAUDE_PLUGIN_ROOT` / `$CODEX_PLUGIN_ROOT` (to its own plugin root) and
`$(pwd)` / `$PWD`; any other variable or `$(...)` leaves the run unrecorded.

```bash
bash "$PLUGIN_ROOT/scripts/extract/element-evidence.sh" <session> <page-url> \
  "<target-selector>" "$(pwd)/tmp/ref/<effect-name>/element-target.json"
```

```bash
# resting-state clip (put the page into the state first)
bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" clip <session> <page-url> \
  "<target-selector>" "$(pwd)/tmp/ref/<effect-name>" <ref|impl> <state>
# 60fps frames from a recording made in that session
bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" video <session> <page-url> \
  "$(pwd)/tmp/ref/<effect-name>" <ref|impl> tmp/ref/<effect-name>/<clip>.webm <prefix>
```

`<page-url>` is the reference URL for `ref` and the local implementation URL
for `impl`; the capture aborts when the session is on another origin. Capture
the implementation under the same states and prefixes as the reference.

## CSS hover / click effects — idle + active clip

Re-capture after activation because `transform: scale` and geometry-changing transitions move the bounding box; the script re-measures on every call.

```bash
agent-browser --session <project> eval "(() => {
  document.querySelector('<target-selector>').scrollIntoView({ block: 'center' });
})()"
agent-browser --session <project> wait 300
bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" clip <project> <page-url> \
  "<target-selector>" "$(pwd)/tmp/ref/<effect-name>" ref idle

# Active state — CDP hover reliably triggers CSS :hover
agent-browser --session <project> hover <target-selector>
agent-browser --session <project> wait <transitionDuration + 100>
bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" clip <project> <page-url> \
  "<target-selector>" "$(pwd)/tmp/ref/<effect-name>" ref active
```

## Trigger-opened UI (modal / drawer) — open and close

Perform the trigger before any reference capture, including the
`element-evidence.sh` probe of `<opened-selector>`; the default page state is
not evidence for the opened UI, and a closed (hidden or zero-size) container
fails target sanity.

```bash
# Open through the real trigger while recording the opening animation
agent-browser --session <project> record start tmp/ref/<effect-name>/open.webm
agent-browser --session <project> click <trigger-selector>
agent-browser --session <project> wait <openDuration + 300>
agent-browser --session <project> record stop

# Settled open state of the opened container
bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" clip <project> <page-url> \
  "<opened-selector>" "$(pwd)/tmp/ref/<effect-name>" ref open

# Record the closing animation through the page's own close control
agent-browser --session <project> record start tmp/ref/<effect-name>/close.webm
agent-browser --session <project> click <close-selector>
agent-browser --session <project> wait <closeDuration + 300>
agent-browser --session <project> record stop

bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" video <project> <page-url> \
  "$(pwd)/tmp/ref/<effect-name>" ref tmp/ref/<effect-name>/open.webm open
bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" video <project> <page-url> \
  "$(pwd)/tmp/ref/<effect-name>" ref tmp/ref/<effect-name>/close.webm close
```

This yields `frames/ref/open-%04d.png` and `frames/ref/close-%04d.png`; record
each animated layer's timing (for example backdrop fade and panel slide)
separately. Capture the implementation under the same names in `frames/impl/`.

## Page-load / splash animations — video + frame extraction

```bash
agent-browser --session <project> record start tmp/ref/<effect-name>/ref.webm
agent-browser --session <project> wait 3000
agent-browser --session <project> record stop

bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" video <project> <page-url> \
  "$(pwd)/tmp/ref/<effect-name>" ref tmp/ref/<effect-name>/ref.webm frame
```

The script extracts at 60fps (`frame-%04d.png`); lower rates lose easing curve shape.

## Scroll-driven animations — two phases

**Phase 1: exploration video** — identifies `trigger_y` (transition starts), `mid_y` (midpoint), `settled_y` (completes).

```bash
agent-browser --session <project> eval "(() => window.scrollTo(0, 0))()"
agent-browser --session <project> wait 500
agent-browser --session <project> record start \
  tmp/ref/<effect-name>/frames/ref/scroll-explore.webm

agent-browser --session <project> eval "(() => {
  const h = document.body.scrollHeight;
  let pos = 0;
  const step = () => {
    pos += 120;
    window.scrollTo(0, pos);
    if (pos < h) setTimeout(step, 80);
  };
  step();
})()"
agent-browser --session <project> wait 4000
agent-browser --session <project> record stop
# Watch video → record trigger_y, mid_y, settled_y
```

**Phase 2: clip screenshot verification at each y.** For each position (before/mid/after) run:

```bash
agent-browser --session <project> eval "(() => window.scrollTo(0, <y>))()"
agent-browser --session <project> wait 500
bash "$PLUGIN_ROOT/scripts/extract/element-state-capture.sh" clip <project> <page-url> \
  "<target-selector>" "$(pwd)/tmp/ref/<effect-name>" ref <state>
```

y values:
- `before` → `trigger_y - 50`
- `mid`    → `mid_y`
- `after`  → `settled_y + 50`

Save as `before.png`, `mid.png`, `after.png`.

## Gate

`tmp/ref/<effect-name>/frames/ref/` must contain the appropriate frames for your classification, each with a `capture-manifest.json` entry, before proceeding.

- CSS hover/click → `idle.png`, `active.png`
- Trigger-opened UI → `open.png`, `open-NNNN.png`, `close-NNNN.png`, plus `open.webm` and `close.webm` in the target directory
- Page-load → `frame-0001.png`, `frame-0002.png`, ... (≥10 frames)
- Scroll-driven → `before.png`, `mid.png`, `after.png`
