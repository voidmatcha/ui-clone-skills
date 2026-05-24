# raycast-extension — Motion forensics from Raycast

Run ui-clone-skills jobs from Raycast's command palette. Type a URL → pick `Decode` / `Clone` / `Verify` / `Extract` → receipt opens in the browser.

## Status

**Scaffold — Step B of the positioning rollout.** `package.json` is in place with the four commands declared. Each command currently `exec`s the local mcp_server via `uv run python -m mcp_server` — see `src/<command>.tsx` for the wire-up template.

## Install (dev)

```bash
cd raycast-extension
npm install
npm run dev   # builds, registers with Raycast in dev mode
```

The extension assumes the engine is installed at `~/dev/ui-clone-skills` (override via Raycast preferences).

## Roadmap

- [ ] Each src/<command>.tsx wires to mcp_server tools
- [ ] Receipt HTML preview inside Raycast (uses build-decode-receipt.sh output)
- [ ] Progress streaming via Raycast Detail.metadata.tag
