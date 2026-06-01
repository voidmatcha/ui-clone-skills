# Agent Environment Rules — load on first session, before Phase 0

These rules cover post-mortem failure modes that have surfaced in prior cloning sessions. They are environment-level (shell, CLI, paths) rather than pipeline-level. Read once at session start; the SKILL.md core rules cover token / I/O hygiene, this file covers the environment surface around them.

## Viewport ordering rule

Always run `agent-browser --session <s> open <url>` **before** `set viewport <w> <h>`, and add `wait` after. Calling `set viewport` *before* the page is open is silently dropped — the browser opens at the default 1280 wide and your width is lost. Correct order:

```
open → set viewport → wait
```

This matters most for mobile sweeps (375, 414, 768) where a wrong viewport silently passes desktop checks. The script `font-parity-check.sh`, `breakpoint-collision-check.sh`, and `section-compare.sh` all follow this order; reuse the pattern when writing new scripts.

**Caveat — bundles with init-time viewport branching require the REVERSE order on the ref baseline.** Some bundles read `window.innerWidth` exactly once at load (`this.isMobile = window.innerWidth < 1024`, `matchMedia(...).matches` cached, GSAP `ScrollTrigger.matchMedia` with no resize handler) and apply mobile-only inline styles only on that one read. The default `open → set viewport 375` order opens at 1280 (desktop branch fires, mobile inline styles skipped), then resizes to 375 — the bundle never re-runs init, so the ref renders a half-mobile state — the desktop-branch inline styles are present, but the mobile-branch inline styles that should override or supplement them are missing (e.g. a multi-axis `transform` ends up with only some of the expected component values). The IMPL clones it correctly but section-compare's REF baseline is now a stale-init artifact, dominating AE.

Symptoms that this caveat applies:
- Mobile section-compare shows large AE on a section where the IMPL visually matches a real-mobile-load ref
- The "missing" mobile inline style appears in the bundle's source but is absent from the captured REF DOM
- Rendering at 375 in a real browser (not via `set viewport` after open) shows the IMPL is correct

Fix in this case — set the viewport BEFORE `open` for the mobile run:

```
set viewport 375 812 → open <url> → wait
```

The originally-documented "silently dropped" behavior was observed under different agent-browser versions / page types; this caveat does NOT apply universally — verify per project. Rule of thumb: `open → set viewport` for general use; flip the order only when the symptom above appears.

## Shell-loop rule (zsh)

zsh does NOT word-split unquoted variables by default, so `set -- $vp; W=$1; H=$2` leaves `W` and `H` empty when iterating `"375 812"`-style strings.

Either:

```bash
# Option A: split with awk
W=$(echo "$vp" | awk '{print $1}')
H=$(echo "$vp" | awk '{print $2}')

# Option B: heredoc-style read
read -r W H <<< "$vp"

# Option C: run the loop in bash explicitly
#!/usr/bin/env bash
```

Silent empty-variable bugs are common when reusing scripts written in bash. Verify with `echo "W=$W H=$H"` once before relying on the values inside a long loop.

## Monorepo path resolution rule

When the user names a project with a space — *"X showcase"*, *"Y dashboard"*, *"Z app"* — do NOT treat that as a literal directory `~/Documents/X showcase/`.

Always check monorepo layouts first:

```bash
ls ~/Documents/<X>/apps/<showcase|dashboard|app>
ls ~/Documents/<X>/packages/
```

Only fall back to the literal-path interpretation if no monorepo match exists. Confirm with `pwd` after `cd`. Acting on the literal path silently puts every artifact in the wrong place and the pipeline gates pass against an empty repo — the worst failure mode because every check looks green.

## agent-browser CLI rule

Run `agent-browser --help` once at session start to confirm verb syntax.

Common mistakes from prior sessions:

- `agent-browser scrollTo 0 500` — **no such verb.** Use `agent-browser --session <s> eval "window.scrollTo(0, 500)"` or `agent-browser scroll down 500`.
- The valid scroll verbs are `scroll <dir> [px]`, `scrollintoview <sel>`, `mouse wheel <dy> [dx]` — there is no `scrollTo`.

Unknown verbs typically exit with an error rather than silently no-op, but the error is easy to miss in long Bash chains; check exit codes when in doubt.

## Pipeline directory layout rule

`tmp/ref/<component>/` is **flat**. Sub-dirs live directly under it:

```
tmp/ref/<component>/
├── static/ref/         ← screenshots
├── scroll-video/ref/   ← scroll videos
├── transitions/ref/    ← transition recordings
├── frames/             ← extracted frames
├── bundles/            ← downloaded JS chunks
├── css/                ← downloaded CSS
├── sections/           ← section-compare output
├── verify/             ← post-implement verification frames
└── responsive/         ← boundary-collisions.json, sizing-expressions.json
```

Gate validators look for `tmp/ref/<c>/static/ref/`, NOT `tmp/ref/<c>/capture/static/ref/`. The `capture/` parent only exists in standalone `ui-capture` runs without a `<component>` arg.

**Always invoke `ui-capture` with the 3rd `[component]` arg** so it writes to `tmp/ref/<c>/` directly — `ui-capture <url> "" <component>` (Claude slash command: `/ui-capture <url> "" <component>`). Pass `""` for the local-url slot when you only need ref capture.

`tmp/ref/` lives **inside the project root** that owns the component, not under `~/`. If you've `cd`'d to `~/Documents/<repo>/apps/<app>/`, that's where `tmp/ref/` should be. Verify with `pwd && ls tmp/ref/<c>/` before each gate run — gates have no opinion about which directory is "the right one"; they validate the path you pass in.

## Stale dev-server rule (agent-vs-user reproducibility delta)

When the user reports a behavior the `agent-browser` session can NOT reproduce — splash frozen, animation jittery, click handler dead — and the impl source looks correct, the most expensive failure mode is debugging the source code instead of the runtime serving it.

`agent-browser --session <s> open <url>` cache-busts on each open; the user's tab does not. Long-running dev servers (Next.js + Turbopack / Vite / Webpack) accumulate state — HMR worker wedges, RSS climbs into multi-GB territory, module graph diverges from disk. After 1+ days uptime, the user's tab can be served stale chunks while the agent's fresh-open session sees the current code. The behaviors look like impl bugs but are environment ghosts.

**Triage before opening source code:**
```bash
# Find the dev-server PID for the project port
lsof -i :<port> -sTCP:LISTEN -t | head -1
# Inspect uptime + memory
ps -o etime,rss,command -p <pid>
```

**Decision rule:**
- `etime` ≥ 1 day OR `rss` ≥ 2 GB → restart the dev server FIRST, then ask the user to re-test before any code investigation
- The restart is cheap (≤10s); a wrong-direction debug session is not
- If the bug reproduces after a fresh server, it is a real bug — proceed to diagnosis

This rule applies to *any* user/agent reproducibility delta, not just splash issues. The asymmetry is structural: `agent-browser --session <s> open` always gets fresh chunks, the user's tab depends on whatever the dev server's HMR worker last decided to serve.
