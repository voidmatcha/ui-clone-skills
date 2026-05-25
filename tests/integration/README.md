# tests/integration/ — opt-in capture-script integration tests

End-to-end tests that exercise `scripts/extract/capture-{states,scroll,hover}.sh`
against a real Chrome session via `agent-browser`. Each script gets one
test file, backed by a local HTML fixture served over `http.server`.

The unit tests in `tests/test_capture_{states,scroll,hover}.py` use a fake
`agent-browser` on PATH that emits pre-canned JSON. They prove the bash
parsing + python splitter handle every payload shape we care about, but
they never exercise:

- the real `agent-browser eval --json` envelope (`{success, data: {origin, result}}`)
- a real Chrome browser running the in-page Promise loop
- timing-sensitive transitions (the 500ms splash flip in Phase A,
  the per-stop stability loop in Phase B, the synthetic-event dispatch
  in Phase C)

The integration suite covers all three.

## Running

```sh
# Default (regular CI / local pytest) — every test in this dir is skipped:
uv run pytest tests/integration/

# Opt-in — runs all three scripts against real Chrome via agent-browser:
UI_CLONE_INTEGRATION=1 uv run pytest tests/integration/

# One script only:
UI_CLONE_INTEGRATION=1 uv run pytest tests/integration/test_capture_states_integration.py
```

Each test takes 5–20 s (Chrome startup dominates). Budget ~1 min for the
full suite when nothing else is competing for the browser.

## Requirements

- `agent-browser` on `$PATH` (installed via `npm i -g @vercel-labs/agent-browser`
  or via this repo's `scripts/install.sh`)
- A working Chrome / Chromium that `agent-browser` can drive
- No network access required — fixtures are served from `tests/integration/fixtures/`
  via Python's stdlib `http.server`

## Architecture

- `conftest.py` owns the `http_server` session-scoped fixture
  (`ThreadingHTTPServer` on port=0; yields base URL like `http://127.0.0.1:54321/`).
- Each test module generates a fresh UUID-suffixed session name per test
  to avoid collisions with other agent-browser users on the machine.
- `try / finally` always closes the derived `<sess>-{states,scroll,hover}`
  session even on assertion failure.

## Adding a new capture-script test

1. Drop a self-contained HTML fixture in `tests/integration/fixtures/<name>.html`
   (no CDN refs — the test runs offline).
2. Add a new `test_capture_<name>_integration.py` following the pattern of
   the existing three (each is ~80 LOC).
3. Re-run `UI_CLONE_INTEGRATION=1 uv run pytest tests/integration/` to verify.
