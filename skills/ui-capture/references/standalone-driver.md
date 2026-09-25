# Standalone capture driver

Read this file for a fresh baseline or standalone capture. Do not combine this
route with the manual runbook unless the driver reports a structured failure.

## Dependencies — preflight (run once per session)

`npx skills add` installs the SKILL files but skips system tooling. Run this check at session start; if anything is missing, halt and surface the bootstrap one-liner to the user (do **not** auto-execute a remote installer on their behalf).

```bash
miss=""
for c in agent-browser ffmpeg; do command -v "$c" >/dev/null 2>&1 || miss+=" $c"; done
if [ -n "$miss" ]; then
  printf 'Missing system deps:%s\n\nFastest fix:\n  tmp=$(mktemp) && curl -LsSf -o "$tmp" https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh && bash "$tmp" && rm -f "$tmp"\n\nOr install manually:\n  brew install ffmpeg   # macOS  (Linux: apt install ffmpeg)\n  npm i -g agent-browser\n' "$miss"
  exit 1
fi
```

## Driver inputs and result

Run the deterministic command in `SKILL.md`. Use `COMPONENT=capture` for the
minimal standalone prompt.

When the driver exits 0 without `<local-url>`, report the canonical output
directory and stop. A compact read-only status or inventory check is allowed.
Do not open another browser session or recapture artifacts the driver owns.

Before a named manual retry, restore browser identity using `docs/agent-cli.md`
section "Browser identity for manual retries". Driver child-process exports do
not persist in the calling shell. Resolve later helpers from `$PLUGIN_ROOT`, not
from the caller's cwd.
